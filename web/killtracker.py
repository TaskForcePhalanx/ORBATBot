"""The Kill Tracker's public, read-only numbers.

**No login, on purpose.** The Kill Tracker is the unit's public scoreboard and
the dashboard that reads it is a static page on another origin, so these routes
carry no session and send `Access-Control-Allow-Origin: *`. They are `GET` only,
they hold nothing a Discord account would add (player names and in-game
statistics, as on the OCAP server they come from), and what they cost is one
query and a cached string.

* `/kt/api/stats.csv` — every counted mission as CSV, with the header and the
  columns the Google Sheet had, so the dashboard reads it exactly as it read the
  sheet (`utils/killtracker.HEADER`).
* `/kt/api/missions` — which recordings are counted, under which rules, and how
  the count compares with the OCAP server's own.

Counting happens in `cogs/killtracker.py`; this only serves it.
"""

import hashlib

from utils import database, killtracker

_CORS = {'Access-Control-Allow-Origin': '*'}

# A minute is as stale as the numbers can be after a mission is counted that
# anybody would notice, and it keeps a busy dashboard to one query a minute.
_CACHE_SECONDS = 60

_cache = {'generation': None, 'csv': '', 'etag': ''}


def _public(record) -> dict:
    """One mission's bookkeeping as the API shows it. The error text stays in the
    log: it names URLs and paths, and "failed" is all anyone outside needs."""
    return {
        'id': record['ocap_id'],
        'filename': record['filename'],
        'mission': record['mission'],
        'label': record['label'],
        'world': record['world'],
        'tag': record['tag'],
        'played_on': record['played_on'],
        'duration_s': record['duration'],
        'kills_counted': record['counted_kills'],
        'kills_per_ocap': record['ocap_player_kills'],
        'rules_version': record['rules_version'],
        'status': record['status'],
        'imported_at': record['imported_at'].isoformat() + 'Z' if record['imported_at'] else None,
    }


async def stats_csv() -> tuple:
    """`(csv, etag)`, rebuilt only when a stored count has changed."""
    generation = await database.kt_generation()
    if _cache['generation'] != generation:
        text = killtracker.csv_from_stored(await database.kt_rows())
        _cache.update(generation=generation, csv=text,
                      etag='"kt-' + hashlib.sha1(generation.encode()).hexdigest()[:16] + '"')
    return _cache['csv'], _cache['etag']


def install(app) -> None:
    from fastapi import Request
    from fastapi.responses import JSONResponse, Response

    cache_control = f'public, max-age={_CACHE_SECONDS}'

    @app.get('/kt/api/stats.csv')
    async def kt_stats_csv(request: Request):
        text, etag = await stats_csv()
        headers = {**_CORS, 'Cache-Control': cache_control, 'ETag': etag}
        if request.headers.get('if-none-match') == etag:
            return Response(status_code=304, headers=headers)
        return Response(content=text, media_type='text/csv; charset=utf-8', headers=headers)

    @app.get('/kt/api/missions')
    async def kt_missions():
        missions = [_public(r) for r in await database.kt_missions()]
        return JSONResponse(
            {'rules_version': killtracker.RULES_VERSION, 'missions': missions},
            headers={**_CORS, 'Cache-Control': cache_control},
        )
