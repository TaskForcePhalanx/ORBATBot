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
