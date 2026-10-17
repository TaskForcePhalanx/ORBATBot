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
* `/kt/api/players.json` — missions played per player, linked names counted as
  one (`killtracker.player_missions()`). **No Discord identity in it**: which
  member a player is stays on the admin page, because this answer is public.
* `/stats` and `/stats/{player}` — the dashboard itself, the same files as on
  GitHub Pages, so a player has an address that survives being embedded.
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

import asyncio
import hashlib
import html
import json
import logging
import os
import pathlib
import time
from urllib.parse import quote

from utils import database, killtracker, sitenav
from web.config import LOGO_NAMES

log = logging.getLogger(__name__)

_CORS = {'Access-Control-Allow-Origin': '*'}

# The dashboard is served from the repository's own copy, so the page on GitHub
# Pages and the page on the bot can never be two different versions.
_DASHBOARD = pathlib.Path(__file__).resolve().parent.parent / 'dashboard'
_STATIC = pathlib.Path(__file__).resolve().parent / 'static'
_ASSETS = {'tracker.js': 'text/javascript; charset=utf-8', 'favicon.svg': 'image/svg+xml'}

# A minute is as stale as the numbers can be after a mission is counted that
# anybody would notice, and it keeps a busy dashboard to one query a minute.
_CACHE_SECONDS = 60

_cache = {'generation': None, 'csv': '', 'etag': '', 'campaigns': None,
          'aliases': None, 'units': None, 'players': None}


def _public(record) -> dict:
    """One mission's bookkeeping as the API shows it.

    A failed read says why, shortened: it is the first thing anyone looks for when a
    mission is missing, and what it names is the OCAP server's public address and an
    HTTP status — nothing that is not already public.
    """
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
        'error': (record['error'] or '')[:200] or None,
        'attempts': record['attempts'],
        'retry_at': record['retry_at'].isoformat() + 'Z' if record['retry_at'] else None,
        'imported_at': record['imported_at'].isoformat() + 'Z' if record['imported_at'] else None,
    }


async def stats_csv() -> tuple:
    """`(csv, etag)`, rebuilt only when something it shows has changed."""
    generation = await database.kt_generation()
    if _cache['generation'] != generation:
        text = killtracker.csv_from_stored(await database.kt_rows())
        _cache.update(generation=generation, csv=text, campaigns=None, aliases=None, units=None,
                      players=None,
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


async def players_document() -> dict:
    """Missions per player, as `players.json`. Cached with the CSV, under the same key."""
    await stats_csv()
    if _cache['players'] is None:
        players = killtracker.player_missions(
            await database.kt_rows(), await database.kt_aliases())
        _cache['players'] = {'players': players}
    return _cache['players']


def logo_url() -> str:
    """The site logo for the tab icon, versioned by its mtime like the rest of the site; '' if none."""
    for name in LOGO_NAMES:
        path = _STATIC / name
        if path.exists():
            return f'/static/{name}?v={int(path.stat().st_mtime)}'
    return ''


# -- the unit's website menu, for the stats page ---------------------------------
#
# Read off the homepage (utils/sitenav.py) and shown above the stats page so it
# reads as part of the website. An hour old at most; a failed read keeps the
# last menu that worked, and a page that no longer parses is logged rather than
# shown half-read. `SITE_NAV_URL=` (empty) turns it off.

SITE_NAV_URL = os.environ.get('SITE_NAV_URL', 'https://www.taskforcephalanx.com/')
NAV_TTL = 3600
NAV_RETRY = 300

_nav = {'doc': None, 'fetched': 0.0, 'tried': 0.0}
_nav_lock = asyncio.Lock()


async def _fetch_page(url: str) -> str:
    import aiohttp
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10),
                                     headers={'User-Agent': 'ORBATBot menu sync'}) as http:
        async with http.get(url) as response:
            response.raise_for_status()
            return await response.text()


async def site_nav() -> dict:
    """The parsed menu, refreshed when it is older than `NAV_TTL`; `{}` if never read."""
    if not SITE_NAV_URL:
        return {}
    now = time.monotonic()
    stale = _nav['doc'] is None or now - _nav['fetched'] > NAV_TTL
    if stale and now - _nav['tried'] > NAV_RETRY and not _nav_lock.locked():
        async with _nav_lock:
            _nav['tried'] = time.monotonic()
            try:
                doc = sitenav.parse_nav(await _fetch_page(SITE_NAV_URL), SITE_NAV_URL)
            except Exception:
                log.exception('Could not read the website menu from %s', SITE_NAV_URL)
            else:
                if doc['items']:
                    _nav.update(doc=doc, fetched=time.monotonic())
                else:
                    log.warning('No menu found on %s; the page may have changed. '
                                'Keeping the last one.', SITE_NAV_URL)
    return _nav['doc'] or {}


# -- who the visitor is, for the stats page ------------------------------------
#
# The page is public and stays that way; signing in only adds a strip at the
# top. The strip is built here, on the server, and the page's script never sees
# it — which is also why it is missing when the page sits in somebody else's
# iframe: the session cookie is SameSite=Lax and is not sent from there.

def _names_of(shown: str, aliases: dict) -> set:
    """Every recorded name that is the player `shown`."""
    return {n for n, c in aliases.items() if c == shown} | {shown}


async def profile_of(guild, user_id) -> dict:
    """`{'player': the name the member's profile is shown under or None,
    'pending': the name they have claimed and an admin has not decided or None}`."""
    mapped = await database.kt_player_members(guild.id)
    aliases = await database.kt_aliases()
    mine = sorted((aliases.get(p, p) for p, uid in mapped.items() if uid == str(user_id)),
                  key=str.lower)
    claim = next((c['player'] for c in await database.kt_claims(guild.id)
                  if c['user_id'] == str(user_id)), None)
    return {'player': mine[0] if mine else None, 'pending': claim}


async def submit_claim(guild, user_id, text) -> str:
    """A member says which player they are. Raises `ValueError` with a sentence.

    This only opens a claim: the mapping the promotion rules read is written by
    an admin approving it (`decide_claim()`), never by the claimant.
    """
    known = killtracker.known_players(await database.kt_rows())
    aliases = await database.kt_aliases()
    player = killtracker.valid_claim(text, killtracker.claimable_players(known, aliases))
    mapped = await database.kt_player_members(guild.id)
    owner = next((mapped[n] for n in _names_of(player, aliases) if n in mapped), None)
    if owner == str(user_id):
        raise ValueError(f'“{player}” is already your profile.')
    if owner:
        raise ValueError(f'“{player}” is already linked to another member. '
                         'If that is wrong, ask a server admin.')
    await database.kt_set_claim(guild.id, user_id, player)
    return f'Your claim for “{player}” was sent. A server admin has to confirm it.'


async def decide_claim(guild, user_id: str, approve: bool) -> str:
    """An admin's answer to one claim. Raises `ValueError`."""
    claim = next((c for c in await database.kt_claims(guild.id) if c['user_id'] == str(user_id)), None)
    if claim is None:
        raise ValueError('That claim is gone already.')
    if approve:
        note = await map_member(guild, claim['player'], str(user_id))
    else:
        note = f'Rejected the claim for “{claim["player"]}”.'
    await database.kt_delete_claim(guild.id, user_id)
    return note


async def claims_view(guild) -> list:
    """The open claims with who made them, for the Players page."""
    out = []
    for c in await database.kt_claims(guild.id):
        member = guild.get_member(int(c['user_id']))
        out.append({**c, 'member': member.display_name if member else c['user_id']})
    return out


_ACCOUNT_CSS = """
.sitenav .snacct{display:flex;align-items:center;gap:10px;margin-left:8px;padding:6px 0 6px 14px;
  border-left:1px solid #ddd;font-size:13px}
.snacct img{width:26px;height:26px;border-radius:50%}
.snacct .snname{color:#666;max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sitenav a.snbtn,.snacct button.snbtn{display:inline-block;padding:7px 14px;border-radius:6px;font:600 13px 'Open Sans',sans-serif;
  background:#424242;color:#fff;border:1px solid #424242;cursor:pointer;white-space:nowrap}
.sitenav a.snbtn:hover,.snacct button.snbtn:hover{background:#5a5a5a}
.sitenav a.snbtn.ghost,.snacct button.snbtn.ghost{background:transparent;color:#424242}
.sitenav a.snbtn.ghost:hover,.snacct button.snbtn.ghost:hover{background:#f0f0f0}
.snacct form{margin:0}
.sitenav.snsolo{justify-content:flex-end;border-top-width:0}
"""


async def account_chip(session, bot) -> str:
    """The sign-in end of the header: a button, or who you are and where your profile is."""
    from web.guilds import user_guilds
    from web.auth import avatar_url
    esc = html.escape
    if session is None:
        return ('<div class="snacct"><a class="snbtn" href="/login?next=/stats">'
                'Sign in with Discord</a></div>')
    found = None
    for item in await user_guilds(bot, session['id']):
        profile = await profile_of(item['guild'], session['id'])
        if profile['player']:
            found = profile
            break
    if found:
        action = (f'<a class="snbtn" href="/stats/{quote(found["player"], safe="")}">My profile</a>')
    else:
        action = '<a class="snbtn" href="/stats/@me">Claim your profile</a>'
    out = ('<form method="post" action="/logout">'
           f'<input type="hidden" name="csrf" value="{esc(session.get("csrf", ""), quote=True)}">'
           '<button class="snbtn ghost" type="submit">Sign out</button></form>')
    return (f'<div class="snacct"><img src="{esc(avatar_url(session), quote=True)}" alt="">'
            f'<span class="snname">{esc(session["name"])}</span>{action}{out}</div>')


async def stats_header(request, session, bot, logo: str, framed: bool) -> str:
    """The whole top row of the stats page: the website menu with the account at its end.

    Without a menu (switched off, not read yet, or the page is in the Sites
    iframe, which has its own) the account stands alone on a plain bar.
    """
    chip = await account_chip(session, bot)
    nav = {} if framed or not SITE_NAV_URL else await site_nav()
    if nav:
        home = sitenav.urlparse(SITE_NAV_URL)
        body = sitenav.render_nav(nav, here=(request.url.netloc, request.url.path), logo=logo,
                                  home=f'{home.scheme}://{home.netloc}/', extra=chip)
    else:
        body = f'<nav class="sitenav snsolo">{chip}</nav>'
    # render_nav carries the menu's stylesheet; a bar without a menu needs it too.
    css = '' if nav else f'<style>{sitenav.CSS}</style>'
    # The header sticks to the top, and so does the page's own section bar beneath
    # it: that bar's `top` is this header's height, which wraps on a phone, so it is measured.
    fit = ('<script>(function(){var n=document.querySelector(".sitenav");if(!n)return;'
           'function f(){document.documentElement.style.setProperty("--sn-h",n.offsetHeight+"px")}'
           'f();addEventListener("resize",f);addEventListener("load",f)})()</script>')
    return css + f'<style>{_ACCOUNT_CSS}</style>' + body + fit


def install(app, header=None) -> None:
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

    @app.get('/kt/api/players.json')
    async def kt_players():
        return JSONResponse(await players_document(),
                            headers={**_CORS, 'Cache-Control': cache_control})

    @app.get('/kt/api/unit_overrides.json')
    async def kt_unit_overrides():
        await stats_csv()
        if _cache['units'] is None:
            _cache['units'] = killtracker.unit_overrides_document(await database.kt_unit_overrides())
        return JSONResponse(_cache['units'], headers={**_CORS, 'Cache-Control': cache_control})

    async def _page(request: Request, player: str = ''):
        from fastapi.responses import HTMLResponse
        template = (_DASHBOARD / 'index.html').read_text(encoding='utf-8')
        # In an iframe (the Sites embed) the website's own menu is already above it.
        framed = request.headers.get('sec-fetch-dest') == 'iframe'
        strip = await header(request, framed) if header else ''
        # A page with somebody's name in it must not be kept for the next visitor.
        cache = 'private, no-store' if strip else 'public, max-age=60'
        return HTMLResponse(killtracker.stats_page(template, player, logo_url(), strip),
                            headers={'Cache-Control': cache})

    @app.get('/stats')
    async def kt_stats_page(request: Request):
        return await _page(request)

    @app.get('/stats/assets/{name}')
    async def kt_stats_asset(name: str):
        if name not in _ASSETS:
            return Response(status_code=404)
        return Response(content=(_DASHBOARD / name).read_bytes(), media_type=_ASSETS[name],
                        headers={'Cache-Control': 'public, max-age=60'})

    @app.get('/stats/{player}')
    async def kt_stats_player(request: Request, player: str):
        return await _page(request, player)

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


async def save_names(form) -> str:
    """Apply the missions form: every row's name, saved together.

    Only the rows whose name changed are written, and none are if any name is not
    valid, so a typo in one box does not leave the form half saved.
    """
    current = {m['ocap_id']: m['name_override'] for m in await database.kt_missions()}
    submitted = []
    for key in form.keys():
        if not key.startswith('mission_'):
            continue
        index = key[len('mission_'):]
        try:
            ocap_id = int(form.get(key))
        except (TypeError, ValueError):
            continue
        submitted.append((ocap_id, form.get(f'name_{index}')))
    changes = killtracker.plan_name_edits(current, submitted)
    await database.kt_set_name_overrides(list(changes.items()))
    if not changes:
        return 'Nothing changed.'
    cleared = sum(1 for name in changes.values() if name is None)
    text = f'Saved {len(changes)} name(s).'
    return text + (f' {cleared} went back to the name OCAP has.' if cleared else '')


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


async def players_view(guild, query: str = '', selected=(), roles=None, unmapped=False) -> dict:
    """The names in the statistics, the people already linked, and who is who on Discord."""
    known = killtracker.known_players(await database.kt_rows())
    aliases = await database.kt_aliases()
    groups = killtracker.alias_groups(aliases)
    shown_as = {name: aliases.get(name) for name in (k['name'] for k in known)}
    people = killtracker.player_missions(await database.kt_rows(), aliases)
    mapped = await database.kt_player_members(guild.id)
    # Humans only, from what the bot has cached: a server larger than the cache
    # simply offers fewer suggestions, and the form still takes any id.
    roster = [m for m in guild.members if not m.bot]
    labels = {str(m.id): m for m in roster}
    candidates = [(str(m.id), [m.display_name, m.name]) for m in roster]
    taken = set(mapped.values())
    for person in people:
        user_id = next((mapped[n] for n in person['names'] if n in mapped), None)
        member = guild.get_member(int(user_id)) if user_id else None
        person['user_id'] = user_id
        person['member'] = member.display_name if member else None
        person['suggestions'] = [] if user_id else [
            {'id': uid, 'name': labels[uid].display_name}
            for uid, _ in killtracker.suggest_members(person['names'], candidates, taken=taken)]
    # The role filter. `roles` is what was asked for; None means the default four.
    server_roles = [r for r in reversed(guild.roles) if not r.is_default() and not r.managed]
    chosen = (killtracker.default_roles([(r.id, r.name) for r in server_roles])
              if roles is None else [int(r) for r in roles if str(r).isdigit()])
    everyone = roles == 'all'
    if everyone:
        visible = people
    else:
        visible = [p for p in people if killtracker.passes_role_filter(
            [r.id for r in guild.get_member(int(p['user_id'])).roles] if p['member'] else [],
            chosen, bool(p['member']), unmapped)]
    # The ticked names stay on the page whatever the search now says, or a
    # search would quietly drop them from the selection.
    selected = [n for n in dict.fromkeys(killtracker.clean_player(n) for n in selected)
                if n in shown_as]
    return {
        'groups': [(shown, names) for shown, names in groups.items()
                   if _matches(shown, query) or any(_matches(n, query) for n in names)],
        'names': [dict(k, shown_as=shown_as[k['name']]) for k in known
                  if _matches(k['name'], query) or k['name'] in selected],
        'members': [{'id': str(m.id), 'name': f'{m.display_name} (@{m.name})'}
                    for m in sorted(roster, key=lambda m: m.display_name.lower())],
        'selected': selected,
        'selected_default': killtracker.default_display(selected, known),
        'existing': [shown for shown in groups if shown not in selected],
        'people': [p for p in visible if _matches(p['name'], query)
                   or any(_matches(n, query) for n in p['names'])],
        'server_roles': [{'id': r.id, 'name': r.name} for r in server_roles],
        'chosen_roles': chosen,
        'include_unmapped': unmapped,
        'showing_all': everyone,
        'total': len(known),
        'mapped': sum(1 for p in people if p['user_id']),
        'query': query,
        'claims': await claims_view(guild),
    }


async def map_member(guild, player: str, text: str) -> str:
    """Say which Discord member `player` is (an id, a mention, or an exact name).
    An empty `text` clears it. Raises `ValueError`."""
    player = killtracker.clean_player(player)
    aliases = await database.kt_aliases()
    shown = aliases.get(player, player)
    names = {n for n, c in aliases.items() if c == shown} | {shown, player}
    text = (text or '').strip()
    if not text:
        await database.kt_set_player_member(guild.id, names, None)
        return f'“{shown}” is no longer mapped to a Discord member.'
    try:
        user_id = killtracker.clean_discord_id(text)
    except ValueError:
        member = guild.get_member_named(text)
        if member is None:
            raise ValueError('No member of this server has that name; use their Discord id.')
        user_id = str(member.id)
    member = guild.get_member(int(user_id))
    if member is None:
        try:
            member = await guild.fetch_member(int(user_id))
        except Exception:
            raise ValueError('That user is not a member of this server.')
    await database.kt_set_player_member(guild.id, names, user_id)
    return f'“{shown}” is now {member.display_name}.'


async def link(names: list, display: str) -> str:
    """Make the ticked names one player, shown as `display`. Raises `ValueError`."""
    names = [killtracker.clean_player(n) for n in names if killtracker.clean_player(n)]
    display = killtracker.clean_player(display)
    if len(names) < 2:
        raise ValueError('Tick at least two names to link, or pick a player to add them to.')
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
    stored = await database.kt_rows()
    known = killtracker.known_players(stored)
    guessed = killtracker.guess_units(stored)
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
            'guess': guessed.get(name, ''),
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


# -- the farthest kills -------------------------------------------------------

SHOWN_KILLS = 100


async def _far_entries() -> list:
    """Every kill on offer, farthest first. Vehicle kills are not on offer: a
    vehicle's weapons really do reach that far, and entries stored before that was
    decided are skipped here as well."""
    missions = []
    struck = await database.kt_exclusions()
    for r in await database.kt_far_kills():
        try:
            far = json.loads(r['far_kills'] or '[]')
        except ValueError:
            far = []
        far = [k for k in far if isinstance(k, dict) and not k.get('veh')] if isinstance(far, list) else []
        # What is struck *now*, not what the last count saw: a kill struck a minute
        # ago leaves the list at once, though its mission is counted again later.
        gone = struck.get(r['ocap_id'], set())
        for k in far:
            try:
                k['x'] = killtracker.kill_key(k['f'], k['ki'], k['vi']) in gone
            except KeyError:
                pass
        missions.append((r['ocap_id'], killtracker.effective_label(r['label'], r['name_override']), far))
    return killtracker.far_listing(missions)


async def kills_view(mission: str = '', query: str = '') -> dict:
    """The kills to check: the farthest of all missions, or one mission's, or one
    player's. Without a filter the list is cut at `SHOWN_KILLS`; with one it is
    not, because the point of a filter is to find a particular kill."""
    entries = await _far_entries()
    records = await database.kt_missions()
    missions = sorted({(e['ocap_id'], e['mission']) for e in entries}, key=lambda m: m[1].lower())
    chosen = int(mission) if str(mission).isdigit() else None
    shown = [e for e in entries if not e['x']
             and (chosen is None or e['ocap_id'] == chosen) and _matches(e['killer'], query)]
    struck = [e for e in entries if e['x']
              and (chosen is None or e['ocap_id'] == chosen) and _matches(e['killer'], query)]
    filtered = chosen is not None or bool(query)
    waiting = sum(1 for m in records
                  if m['status'] == 'ok' and m['rules_version'] < killtracker.RULES_VERSION)
    return {'kills': shown if filtered else shown[:SHOWN_KILLS], 'cut': (not filtered) and len(shown) > SHOWN_KILLS,
            'struck': struck, 'total': sum(1 for e in entries if not e['x']), 'waiting': waiting, 'limit': SHOWN_KILLS, 'missions': missions,
            'mission': chosen, 'query': query, 'struck_total': sum(1 for e in entries if e['x'])}


async def save_strikes(form, by_name: str) -> str:
    """Apply the strike form: ticked kills leave the distance statistics, unticked
    ones come back. Only what differs from what is stored is written, and every
    mission touched is counted again by the next pass of the import loop."""
    changes = killtracker.plan_strikes(form.getlist('listed'), form.getlist('struck'),
                                       await _far_entries())
    if not changes:
        return 'Nothing changed.'
    missions = await database.kt_set_exclusions(
        [(ocap_id, key, struck, entry) for ocap_id, key, struck, entry in changes], by_name)
    struck = sum(1 for c in changes if c[2])
    restored = len(changes) - struck
    parts = ([f'{struck} kill(s) struck'] if struck else []) + ([f'{restored} put back'] if restored else [])
    return (f"{', '.join(parts)}. {missions} mission(s) will be counted again within a few "
            f"minutes; the public numbers change when that is done.")


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
