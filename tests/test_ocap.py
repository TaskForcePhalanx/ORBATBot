"""`utils/ocap.py` — which recordings to read, and when.

The network functions are not tested (they are `aiohttp` and nothing else); what
decides whether a mission is imported once, twice, or never is, and it is pure.
"""

from datetime import datetime, timedelta

import pytest

from utils import ocap

NOW = datetime(2026, 10, 11, 12, 0, 0)


def op(i, completed=True, filename=None):
    return {'id': i, 'filename': filename or f'm{i}', 'completed': completed}


def stored(version=1, status='ok', retry_at=None, attempts=0):
    return {'rules_version': version, 'status': status, 'retry_at': retry_at, 'attempts': attempts}


# -- the list -----------------------------------------------------------------

def test_an_entry_of_the_list_is_normalised():
    raw = {'id': 66, 'world_name': 'stubbhult', 'mission_name': 'Baltic Showdown',
           'mission_duration': 10077, 'filename': 'Baltic_Showdown_20261010_203523',
           'date': '2026-10-10T00:00:00.0002', 'tag': 'Weekly OP',
           'storageFormat': 'protobuf', 'conversionStatus': 'completed',
           'player_count': 9, 'kill_count': 331, 'player_kill_count': 204}
    assert ocap.normalise_operation(raw) == {
        'id': 66, 'filename': 'Baltic_Showdown_20261010_203523',
        'mission': 'Baltic Showdown', 'world': 'stubbhult', 'tag': 'Weekly OP',
        'date': '2026-10-10', 'duration': 10077, 'kills': 331, 'player_kills': 204,
        'completed': True,
    }


def test_only_completed_conversions_count_as_finished():
    base = {'id': 1, 'filename': 'x'}
    assert ocap.normalise_operation({**base, 'conversionStatus': 'completed'})['completed']
    for status in ('pending', 'failed', None, ''):
        assert not ocap.normalise_operation({**base, 'conversionStatus': status})['completed']


@pytest.mark.parametrize('raw', [
    None, 'x', 5, [], {}, {'id': 1}, {'filename': 'x'}, {'id': 'a', 'filename': 'x'},
    {'id': 1, 'filename': ''}, {'id': 1, 'filename': '  '}, {'id': 1, 'filename': 7},
])
def test_an_entry_that_cannot_be_fetched_is_dropped(raw):
    assert ocap.normalise_operation(raw) is None


def test_the_list_is_sorted_by_id_and_junk_is_dropped():
    raw = [{'id': 3, 'filename': 'c'}, 'junk', {'id': 1, 'filename': 'a'}, {'id': 2}]
    assert [o['id'] for o in ocap.normalise_operations(raw)] == [1, 3]


def test_something_that_is_not_a_list_is_an_error():
    with pytest.raises(ocap.OcapError):
        ocap.normalise_operations({'error': 'nope'})


def test_the_recording_url_quotes_the_file_name():
    assert ocap.recording_url('https://o.example/', 'Baltic_Showdown_20261010_203523') == (
        'https://o.example/data/Baltic_Showdown_20261010_203523.json.gz')
    assert ocap.recording_url('https://o.example', 'a b/c#d') == (
        'https://o.example/data/a%20b%2Fc%23d.json.gz')


def test_the_base_url_comes_from_the_environment(monkeypatch):
    monkeypatch.delenv('OCAP_BASE_URL', raising=False)
    assert ocap.base_url() == ocap.DEFAULT_BASE
    monkeypatch.setenv('OCAP_BASE_URL', ' https://ocap.example.com/ ')
    assert ocap.base_url() == 'https://ocap.example.com'


# -- what is due --------------------------------------------------------------

def ids(due):
    return [o['id'] for o in due]


def test_a_new_finished_recording_is_due_and_an_unfinished_one_is_not():
    assert ids(ocap.plan([op(1), op(2, completed=False)], {}, NOW, 1, 10)) == [1]


def test_a_counted_recording_is_left_alone():
    assert ocap.plan([op(1)], {1: stored()}, NOW, 1, 10) == []


def test_recordings_counted_under_older_rules_are_counted_again():
    known = {1: stored(version=1), 2: stored(version=2)}
    assert ids(ocap.plan([op(1), op(2)], known, NOW, 2, 10)) == [1]


def test_a_failed_recording_waits_for_its_back_off():
    wait = NOW + timedelta(minutes=5)
    assert ocap.plan([op(1)], {1: stored(version=0, status='failed', retry_at=wait)}, NOW, 1, 10) == []
    due = NOW - timedelta(minutes=1)
    assert ids(ocap.plan([op(1)], {1: stored(version=0, status='failed', retry_at=due)}, NOW, 1, 10)) == [1]


def test_a_failed_recount_of_a_good_mission_waits_too():
    wait = NOW + timedelta(hours=1)
    known = {1: stored(version=1, status='ok', retry_at=wait)}
    assert ocap.plan([op(1)], known, NOW, 2, 10) == []


def test_a_pass_reads_at_most_the_limit_oldest_first():
    assert ids(ocap.plan([op(i) for i in range(1, 8)], {}, NOW, 1, 3)) == [1, 2, 3]


def test_back_off_doubles_up_to_a_day():
    assert [ocap.retry_delay_minutes(n) for n in (1, 2, 3, 4)] == [10, 20, 40, 80]
    assert ocap.retry_delay_minutes(50) == 24 * 60
    assert ocap.retry_delay_minutes(0) == 10


# -- the download ---------------------------------------------------------------
#
# The OCAP server answers a `.json.gz` with `Content-Encoding: gzip`, and an HTTP
# client that honours that stores plain JSON under a `.json.gz` name — which is
# exactly what the first deploy did to 6 of its first 8 recordings ("Not a
# gzipped file"). These run a real server on a free port.

import asyncio
import gzip
import hashlib
import json

from utils import killtracker

_DOC = {
    'missionName': 'Op', 'worldName': 'altis', 'captureDelay': 1, 'endFrame': 10,
    'entities': [
        {'id': 1, 'name': 'Ann', 'side': 'WEST', 'isPlayer': 1, 'type': 'unit',
         'group': 'A', 'role': 'R', 'startFrameNum': 0,
         'positions': [[[0, 0, 0], 0, 1, 0, 'Ann', 1, 'R', 'A', 'WEST']] * 5,
         'framesFired': []},
        {'id': 2, 'name': 'Bob', 'side': 'EAST', 'isPlayer': 0, 'type': 'unit',
         'startFrameNum': 0, 'positions': []},
    ],
    'events': [[3, 'killed', 2, [1, 'M4'], 120]],
}
_RAW = json.dumps(_DOC).encode()
_GZ = gzip.compress(_RAW)


def _serve(handler_body, then):
    """Run `then(base_url)` against a one-route server whose body is `handler_body`."""
    from aiohttp import web

    async def main():
        app = web.Application()
        app.router.add_get('/data/{name}.json.gz', handler_body)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            return await then(f'http://127.0.0.1:{port}')
        finally:
            await runner.cleanup()

    return asyncio.run(main())


def _rows(path):
    return killtracker.compute(killtracker.open_recording(path), 'x.json.gz')['rows']


@pytest.mark.parametrize('headers,body', [
    ({'Content-Encoding': 'gzip', 'Content-Type': 'application/json'}, _GZ),  # what the server does
    ({'Content-Type': 'application/gzip'}, _GZ),                              # zipped, not declared
    ({'Content-Type': 'application/json'}, _RAW),                             # not zipped at all
], ids=['declared-gzip', 'undeclared-gzip', 'plain'])
def test_every_way_a_server_can_send_a_recording_ends_up_countable(tmp_path, headers, body):
    from aiohttp import web

    async def handler(request):
        return web.Response(body=body, headers=headers)

    dest = tmp_path / 'r.json.gz'

    async def then(base):
        return await ocap.download_recording(base, 'Op', str(dest))

    sha = _serve(handler, then)
    assert sha == hashlib.sha256(dest.read_bytes()).hexdigest()
    rows = _rows(str(dest))
    assert [r[3] for r in rows] == ['Ann'] and rows[0][7] == 1


def test_the_file_is_kept_as_the_server_sent_it(tmp_path):
    """Not unzipped on the way in: the size limit is on what crossed the wire."""
    from aiohttp import web

    async def handler(request):
        return web.Response(body=_GZ, headers={'Content-Encoding': 'gzip'})

    dest = tmp_path / 'r.json.gz'
    _serve(handler, lambda base: ocap.download_recording(base, 'Op', str(dest)))
    assert dest.read_bytes() == _GZ


def test_the_size_limit_counts_bytes_as_sent_not_as_unzipped(tmp_path):
    from aiohttp import web

    big = gzip.compress(b'{"x":"' + b'a' * 5_000_000 + b'"}')   # ~5 KB zipped, 5 MB unzipped
    assert len(big) < 100_000

    async def handler(request):
        return web.Response(body=big, headers={'Content-Encoding': 'gzip'})

    dest = tmp_path / 'r.json.gz'
    # a limit far below the unzipped size but above the zipped size is not hit
    _serve(handler, lambda base: ocap.download_recording(base, 'Op', str(dest), max_bytes=1_000_000))
    # …while one below the zipped size is
    with pytest.raises(ocap.OcapError, match='larger than'):
        _serve(handler, lambda base: ocap.download_recording(base, 'Op', str(dest), max_bytes=1000))


def test_a_missing_recording_is_an_error_with_the_status(tmp_path):
    from aiohttp import web

    async def handler(request):
        return web.Response(status=404)

    with pytest.raises(ocap.OcapError, match='404'):
        _serve(handler, lambda base: ocap.download_recording(base, 'Op', str(tmp_path / 'x')))
