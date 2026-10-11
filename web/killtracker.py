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
* `/kt/api/campaigns.json`, `/kt/api/player_aliases.json`,
  `/kt/api/unit_overrides.json` — the campaigns, the names that belong together
  and the units set by hand, each in the shape of the dashboard's own JSON file
  of that name, so the dashboard reads them as it read those.

Counting happens in `cogs/killtracker.py`; this only serves it.

The **admin pages** (`/g/{guild}/killtracker…`, server admins only, in `app.py`)
change what these answers say — a mission's shown name and the campaigns — and
the functions they call are here, so that what they do can be read in one place.
"""

import hashlib

from utils import database, killtracker

_CORS = {'Access-Control-Allow-Origin': '*'}

# A minute is as stale as the numbers can be after a mission is counted that
# anybody would notice, and it keeps a busy dashboard to one query a minute.
_CACHE_SECONDS = 60

_cache = {'generation': None, 'csv': '', 'etag': '', 'campaigns': None,
          'aliases': None, 'units': None}


def _public(record) -> dict:
    """One mission's bookkeeping as the API shows it. The error text stays in the
    log: it names URLs and paths, and "failed" is all anyone outside needs."""
    return {
        'id': record['ocap_id'],
        'filename': record['filename'],
        'mission': record['mission'],
        'label': record['label'],
        'shown_as': killtracker.effective_label(record['label'], record['name_override'])
                    if record['label'] else None,
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
    """`(csv, etag)`, rebuilt only when something it shows has changed."""
    generation = await database.kt_generation()
    if _cache['generation'] != generation:
        text = killtracker.csv_from_stored(await database.kt_rows())
        _cache.update(generation=generation, csv=text, campaigns=None, aliases=None, units=None,
                      etag='"kt-' + hashlib.sha1(generation.encode()).hexdigest()[:16] + '"')
    return _cache['csv'], _cache['etag']


def shown_labels(missions) -> dict:
    """`{recording id: the name it is shown under}`, date included.

    A recording not counted yet has no label of its own, so the OCAP list's name
    and date stand in — which is only ever a guess at what the counted rows will
    say, and is used for nothing but picking missions in a form.
    """
    out = {}
    for m in missions:
        label = m['label'] or (f"{m['mission']} ({m['played_on']})" if m['played_on'] else m['mission'])
        out[m['ocap_id']] = killtracker.effective_label(label, m['name_override'])
    return out


async def campaigns_document() -> dict:
    """The campaigns, as `campaigns.json`. Cached with the CSV, under the same key."""
    await stats_csv()
    if _cache['campaigns'] is None:
        missions = await database.kt_missions()
        _cache['campaigns'] = killtracker.campaigns_document(
            await database.kt_campaigns(), shown_labels(missions))
    return _cache['campaigns']


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

    @app.get('/kt/api/campaigns.json')
    async def kt_campaigns():
        return JSONResponse(await campaigns_document(),
                            headers={**_CORS, 'Cache-Control': cache_control})

    @app.get('/kt/api/player_aliases.json')
    async def kt_aliases():
        await stats_csv()
        if _cache['aliases'] is None:
            _cache['aliases'] = await database.kt_aliases()
        return JSONResponse(_cache['aliases'], headers={**_CORS, 'Cache-Control': cache_control})

    @app.get('/kt/api/unit_overrides.json')
    async def kt_unit_overrides():
        await stats_csv()
        if _cache['units'] is None:
            _cache['units'] = killtracker.unit_overrides_document(await database.kt_unit_overrides())
        return JSONResponse(_cache['units'], headers={**_CORS, 'Cache-Control': cache_control})

    @app.get('/kt/api/missions')
    async def kt_missions():
        missions = [_public(r) for r in await database.kt_missions()]
        return JSONResponse(
            {'rules_version': killtracker.RULES_VERSION, 'missions': missions},
            headers={**_CORS, 'Cache-Control': cache_control},
        )


# ---------------------------------------------------------------------------
# What the admin pages do
# ---------------------------------------------------------------------------

async def missions_view() -> list:
    """The mission table: each recording, the name it is shown under, and the
    campaigns it belongs to."""
    missions = await database.kt_missions()
    campaigns = await database.kt_campaigns()
    belongs = {}
    for c in campaigns:
        for ocap_id in c['ocap_ids']:
            belongs.setdefault(ocap_id, []).append(c)
    return [{
        'm': m,
        'own': killtracker.split_label(m['label'] or m['mission'] or '')[0],
        'campaigns': belongs.get(m['ocap_id'], []),
    } for m in missions]


async def rename(ocap_id: int, text) -> str:
    """Give a mission a name, or — empty — take it back to OCAP's. Raises `ValueError`."""
    name = killtracker.clean_name(text)
    if not await database.kt_set_name_override(ocap_id, name):
        raise ValueError('No such recording.')
    return f'Now shown as “{name}”.' if name else 'Back to the name OCAP has.'


async def import_names(text: str, *, overwrite: bool) -> dict:
    """Take the old sheet's mission names. Returns what was and was not done.

    A name already set by hand is left alone unless `overwrite`: the point of
    importing is to carry the old work over, not to undo new work.
    """
    sheet = killtracker.parse_sheet_names(text)
    missions = await database.kt_missions()
    changes = killtracker.plan_name_import(sheet, missions)
    apply, kept = [], []
    for change in changes:
        if change['current'] and not overwrite:
            kept.append(change)
        else:
            apply.append(change)
    await database.kt_set_name_overrides([(c['ocap_id'], c['sheet']) for c in apply])
    unknown = len(set(sheet) - {killtracker.source_key(m['filename']) for m in missions})
    return {'applied': apply, 'kept': kept, 'in_sheet': len(sheet), 'unknown': unknown}


async def import_campaigns(text: str, member_name: str) -> dict:
    """Take the old `campaigns.json`. Existing campaigns of the same name are
    left alone; the rest are created, with each mission label matched to a
    recording where there is one."""
    parsed = killtracker.parse_campaigns_json(text)
    shown = shown_labels(await database.kt_missions())
    have = {c['name'].lower() for c in await database.kt_campaigns()}
    created, skipped, unmatched = [], [], 0
    for item in parsed:
        if item['name'].lower() in have:
            skipped.append(item['name'])
            continue
        ids, leftover = killtracker.match_missions(item['missions'], shown)
        await database.kt_save_campaign(
            None, name=item['name'], patterns=item['patterns'], extra=leftover,
            ocap_ids=ids, updated_by_name=member_name)
        created.append({'name': item['name'], 'matched': len(ids), 'unmatched': leftover})
        unmatched += len(leftover)
    return {'created': created, 'skipped': skipped, 'unmatched': unmatched}


def read_campaign_form(form) -> dict:
    """A submitted campaign form → what `database.kt_save_campaign()` takes.

    Raises `ValueError` with a sentence for the page.
    """
    name = killtracker.clean_name(form.get('name'), what='campaign name')
    if not name:
        raise ValueError('The campaign needs a name.')
    try:
        ids = sorted({int(x) for x in form.getlist('mission')})
    except ValueError:
        raise ValueError('That selection is not valid.')
    return {
        'name': name,
        'patterns': killtracker.clean_patterns(form.get('patterns')),
        'ocap_ids': ids,
        'extra': [x for x in form.getlist('extra') if x],
    }


# -- players -----------------------------------------------------------------

def _matches(text: str, query: str) -> bool:
    return not query or query.lower() in (text or '').lower()


async def players_view(query: str = '') -> dict:
    """The names in the statistics, and the people already linked."""
    known = killtracker.known_players(await database.kt_rows())
    aliases = await database.kt_aliases()
    groups = killtracker.alias_groups(aliases)
    shown_as = {name: aliases.get(name) for name in (k['name'] for k in known)}
    return {
        'groups': [(shown, names) for shown, names in groups.items()
                   if _matches(shown, query) or any(_matches(n, query) for n in names)],
        'names': [dict(k, shown_as=shown_as[k['name']]) for k in known
                  if _matches(k['name'], query)],
        'total': len(known),
        'query': query,
    }


async def link(names: list, display: str) -> str:
    """Make the ticked names one player, shown as `display`. Raises `ValueError`."""
    names = [killtracker.clean_player(n) for n in names if killtracker.clean_player(n)]
    display = killtracker.clean_player(display)
    if len(names) < 2:
        raise ValueError('Tick at least two names to link.')
    if display not in names:
        raise ValueError('Pick which of the ticked names to show.')
    await database.kt_set_aliases(killtracker.link_names(await database.kt_aliases(), names, display))
    return f'{len(names)} names are now one player, shown as “{display}”.'


async def show_instead(name: str) -> str:
    """Show `name` for the player it is linked to."""
    name = killtracker.clean_player(name)
    aliases = await database.kt_aliases()
    if name not in aliases and name not in aliases.values():
        raise ValueError('That name is not linked to anyone.')
    await database.kt_set_aliases(killtracker.link_names(aliases, [name], name))
    return f'Now shown as “{name}”.'


async def unlink(name: str) -> str:
    name = killtracker.clean_player(name)
    aliases = await database.kt_aliases()
    if name not in aliases:
        raise ValueError('That name is not linked as an alias.')
    await database.kt_set_aliases(killtracker.unlink_name(aliases, name))
    return f'“{name}” is a player of their own again.'


# -- units -------------------------------------------------------------------

async def units_view(query: str = '') -> dict:
    """Each player as the dashboard shows them, and the unit set for them by hand."""
    known = killtracker.known_players(await database.kt_rows())
    aliases = await database.kt_aliases()
    overrides = await database.kt_unit_overrides()
    names = {aliases.get(k['name'], k['name']) for k in known} | set(overrides)
    stats = {}
    for k in known:
        shown = aliases.get(k['name'], k['name'])
        entry = stats.setdefault(shown, {'missions': 0, 'last': ''})
        entry['missions'] += k['missions']
        entry['last'] = k['last']
    rows = []
    for name in sorted(names, key=str.lower):
        if not _matches(name, query):
            continue
        current = overrides.get(name)
        rows.append({
            'name': name, 'missions': stats.get(name, {}).get('missions', 0),
            'value': 'auto' if current is None else ('unknown' if current == '' else current),
        })
    return {'rows': rows, 'units': killtracker.UNITS, 'query': query, 'total': len(names)}


async def save_units(form) -> str:
    """Apply the units form: only the rows whose choice changed are written."""
    current = await database.kt_unit_overrides()
    changes = {}
    for key in form.keys():
        if not key.startswith('player_'):
            continue
        index = key[len('player_'):]
        player = killtracker.clean_player(form.get(key))
        if not player:
            continue
        wanted = killtracker.clean_unit(form.get(f'unit_{index}'))
        if wanted != current.get(player):
            changes[player] = wanted
    await database.kt_set_unit_overrides(changes)
    return f'Saved {len(changes)} change(s).' if changes else 'Nothing changed.'


# -- the old files -----------------------------------------------------------

async def import_aliases(text: str) -> dict:
    """Take the old `player_aliases.json`; names already linked are left as they are."""
    parsed = killtracker.parse_aliases_json(text)
    have = await database.kt_aliases()
    merged = dict(have)
    added, kept = [], []
    for alias, canonical in parsed.items():
        if alias in have or canonical in have:
            kept.append(alias)
            continue
        merged = killtracker.link_names(merged, [alias, canonical], canonical)
        added.append(alias)
    await database.kt_set_aliases(merged)
    return {'added': added, 'kept': kept}


async def import_units(text: str) -> dict:
    """Take the old `unit_overrides.json`; a player with a unit already is left alone."""
    parsed = killtracker.parse_unit_overrides_json(text)
    have = await database.kt_unit_overrides()
    changes = {p: u for p, u in parsed.items() if p not in have}
    await database.kt_set_unit_overrides(changes)
    return {'added': sorted(changes), 'kept': sorted(set(parsed) - set(changes))}
