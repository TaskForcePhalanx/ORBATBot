"""Reading the unit's OCAP server: which recordings exist, and fetching one.

The server publishes its recordings without a login, which is what makes the
Kill Tracker's import automatic — nobody has to upload a file:

* `GET {base}/api/v1/operations` lists every recording, each with an `id` that
  only grows, the `filename`, a `conversionStatus` (`completed` once the
  recording is finished and exported), the `tag` (Weekly OP, JointOP) and the
  server's own kill counts;
* `GET {base}/data/{filename}.json.gz` is the recording itself.

`normalise_operation()` and `recording_url()` are pure and tested. The two
network functions import `aiohttp` where they use it, so this module stays
importable without it — the same arrangement as `utils/reddit.py`.
"""

import hashlib
import os
from urllib.parse import quote

DEFAULT_BASE = 'https://ocap.taskforcephalanx.com'

# What may be downloaded for one recording, counted in bytes *as sent*. A big
# mission is 10 MB zipped, and 200+ MB as plain JSON; this is a ceiling against a
# runaway response, not an expectation.
MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024

USER_AGENT = 'ORBATBot-killtracker (+https://github.com/armatfp/ORBATBot)'


class OcapError(Exception):
    """The server could not be read. The message is meant for a log or a person."""


def base_url() -> str:
    """The OCAP server's origin, from `OCAP_BASE_URL`, without a trailing slash."""
    return (os.getenv('OCAP_BASE_URL') or DEFAULT_BASE).strip().rstrip('/')


def recording_url(base: str, filename: str) -> str:
    return f"{base.rstrip('/')}/data/{quote(filename, safe='')}.json.gz"


def _int(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalise_operation(raw) -> dict:
    """One entry of the list, in the shape the importer uses — or None if it is not one.

    Entries without an integer `id` or without a `filename` cannot be fetched or
    told apart, so they are dropped rather than guessed at.
    """
    if not isinstance(raw, dict):
        return None
    filename = raw.get('filename')
    try:
        op_id = int(raw.get('id'))
    except (TypeError, ValueError):
        return None
    if not isinstance(filename, str) or not filename.strip():
        return None
    return {
        'id': op_id,
        'filename': filename.strip(),
        'mission': str(raw.get('mission_name') or ''),
        'world': str(raw.get('world_name') or ''),
        'tag': str(raw.get('tag') or ''),
        'date': str(raw.get('date') or '')[:10],
        'duration': _int(raw.get('mission_duration')),
        'kills': _int(raw.get('kill_count')),
        'player_kills': _int(raw.get('player_kill_count')),
        'completed': raw.get('conversionStatus') == 'completed',
    }


def normalise_operations(raw) -> list:
    """The list, oldest first, malformed entries dropped."""
    if not isinstance(raw, list):
        raise OcapError('The OCAP server did not answer with a list of recordings.')
    ops = [op for op in map(normalise_operation, raw) if op]
    ops.sort(key=lambda op: op['id'])
    return ops


async def fetch_operations(base: str, *, timeout: int = 30) -> list:
    import aiohttp

    url = f"{base.rstrip('/')}/api/v1/operations"
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout),
            headers={'User-Agent': USER_AGENT},
        ) as session:
            async with session.get(url) as response:
                if response.status != 200:
                    raise OcapError(f'{url} answered HTTP {response.status}.')
                return normalise_operations(await response.json(content_type=None))
    except aiohttp.ClientError as e:
        raise OcapError(f'Could not reach {url}: {e}') from e


async def download_recording(base: str, filename: str, dest: str, *,
                             timeout: int = 900, max_bytes: int = MAX_DOWNLOAD_BYTES) -> str:
    """Stream one recording to `dest` and return its SHA-256.

    To disk and not to memory: the file is read back as a stream by
    `utils/killtracker.open_recording()`, so nothing ever holds it whole.

    **The bytes are stored as the server sent them.** The server answers a
    `.json.gz` with `Content-Encoding: gzip`, and `aiohttp` would quietly unzip
    that — storing plain JSON under a `.json.gz` name and counting the size of
    the *unzipped* file against `max_bytes`. `auto_decompress=False` keeps the
    file as it is and the limit honest; `open_recording()` copes with either.
    Only gzip is asked for, because it is the only encoding that is undone here.
    """
    import aiohttp

    url = recording_url(base, filename)
    digest = hashlib.sha256()
    size = 0
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout),
            headers={'User-Agent': USER_AGENT, 'Accept-Encoding': 'gzip'},
            auto_decompress=False,
        ) as session:
            async with session.get(url) as response:
                if response.status != 200:
                    raise OcapError(f'{url} answered HTTP {response.status}.')
                with open(dest, 'wb') as out:
                    async for chunk in response.content.iter_chunked(1 << 16):
                        size += len(chunk)
                        if size > max_bytes:
                            raise OcapError(f'{url} is larger than {max_bytes // (1 << 20)} MB.')
                        digest.update(chunk)
                        out.write(chunk)
    except aiohttp.ClientError as e:
        raise OcapError(f'Could not fetch {url}: {e}') from e
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# What to import
# ---------------------------------------------------------------------------

# How long a recording that would not read is left alone: 10 minutes, doubling
# with each failure, never more than a day. A file that is corrupt on the server
# stays corrupt, and fetching it every poll would be the only thing the loop did.
RETRY_BASE_MINUTES = 10
RETRY_MAX_MINUTES = 24 * 60


def retry_delay_minutes(attempts: int) -> int:
    """Minutes to wait after the `attempts`-th failed read (1 = the first)."""
    return min(RETRY_BASE_MINUTES * 2 ** max(attempts - 1, 0), RETRY_MAX_MINUTES)


def plan(ops: list, known: dict, now, rules_version: int, limit: int) -> list:
    """The recordings to read this time, at most `limit`: new ones first, oldest first,
    then the ones to try again.

    `known` maps a recording's id to what is stored for it (anything with
    `rules_version`, `status` and `retry_at`). A recording is due when

    * it is finished (`conversionStatus: completed`) — an unfinished one is
      still being written, and counting it would store half a mission;
    * and it has never been seen, **or** it was counted under older rules, **or**
      it failed and its back-off has run out.

    A recording that failed is not retried before its `retry_at`, and one that
    was counted under older rules but fails now keeps its old rows (see
    `database.kt_save_failure`) and is retried on the same back-off.
    """
    fresh, again = [], []
    for op in ops:
        if not op['completed']:
            continue
        row = known.get(op['id'])
        if row is None:
            fresh.append(op)
            continue
        retry_at = row['retry_at']
        if row['status'] == 'failed' or row['rules_version'] < rules_version:
            if retry_at is None or retry_at <= now:
                again.append(op)
    # Recordings never tried go first. A recording that keeps failing comes due
    # again and again, and oldest-first it would take the places of the new ones
    # every time its back-off ran out — eight stuck ones would hold up everything.
    return (fresh + again)[:limit]
