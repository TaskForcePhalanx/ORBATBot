"""Ops traces: a phase's layer of its AO map, drawn as a picture for the packs.

The map is the same SVG the site shows (`tacmap.render()`), cropped to the
phase by `campaign.trace_view()` and turned into a PNG by **resvg** — a
renderer that ships as one wheel and needs nothing from the operating system,
which matters because the bot is built by Nixpacks and cairo is not a given.
It fetches nothing itself, so every terrain tile the crop touches is read
first (from this bot's database for an uploaded terrain, over HTTP for an OCAP
one) and handed in as a data URI. The labels use Bitstream Vera, the font that
ships inside reportlab and that the PDFs already use.

Nothing here decides anything: which layers a trace shows, where it looks and
how deep the terrain goes are `utils/campaign.py`'s.
"""

import asyncio
import base64
import os

from utils import campaign as rules
from utils import database, tacmap

TILE_TIMEOUT = 10
MAX_TILE_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 12 * 1024 * 1024
TILE_CONCURRENCY = 8

# A transparent pixel, for a tile that could not be had: the dark sheet under
# it then shows, as it does on the site when a tile 404s.
_EMPTY = ('data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk'
          'YAAAAAYAAjCB0C8AAAAASUVORK5CYII=')


def available() -> bool:
    """Whether traces can be drawn here at all — resvg is a declared
    dependency, but a deployment without it should still make its packs."""
    try:
        import resvg_py  # noqa: F401
    except ImportError:
        return False
    return True


def _font_dir() -> str:
    import reportlab
    return os.path.join(os.path.dirname(reportlab.__file__), 'fonts')


def _data_uri(data: bytes | None) -> str:
    if not data:
        return _EMPTY
    kind = rules.image_type(data) or 'image/png'
    return f'data:{kind};base64,{base64.b64encode(data).decode()}'


async def _fetch(session, url: str, limit: int) -> bytes | None:
    try:
        async with session.get(url) as response:
            if response.status != 200:
                return None
            body = await response.content.read(limit + 1)
            return body if len(body) <= limit else None
    except Exception:
        return None


async def _background_uris(doc: dict, zoom: int, view: tuple) -> dict:
    """(z, x, y) → data URI for every tile the trace draws, or {None: uri} for
    a single-picture background."""
    background = doc['background']
    url = background['url']
    if not url:
        return {}
    needed = []
    if background['kind'] == 'tiles':
        if zoom > tacmap.BACKDROP_ZOOM:
            needed.append((tacmap.BACKDROP_ZOOM, 0, 0))
        needed += [(zoom, c, r) for c, r in tacmap.visible_tiles(doc, zoom, view)]
    local = tacmap.LOCAL_TILES.match(url)
    if local:
        terrain = int(url.rsplit('/', 1)[1])
        out = {}
        for key in needed:
            data = await database.get_tac_terrain_tile(terrain, *key)
            out[key] = _data_uri(bytes(data) if data else None)
        return out

    import aiohttp
    timeout = aiohttp.ClientTimeout(total=TILE_TIMEOUT * 3, sock_read=TILE_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        if background['kind'] != 'tiles':
            return {None: _data_uri(await _fetch(session, url, MAX_IMAGE_BYTES))}
        gate = asyncio.Semaphore(TILE_CONCURRENCY)

        async def one(key):
            async with gate:
                return key, await _fetch(session, tacmap.tile_url(background, *key),
                                         MAX_TILE_BYTES)
        pairs = await asyncio.gather(*(one(k) for k in needed))
    return {key: _data_uri(data) for key, data in pairs}


def _rasterise(svg: str) -> bytes:
    import resvg_py
    return resvg_py.svg_to_bytes(
        svg_string=svg, font_dirs=[_font_dir()], skip_system_fonts=True,
        font_family='Bitstream Vera Sans', sans_serif_family='Bitstream Vera Sans',
        background='#20262e')


async def draw(guild_id: str, doc: dict, layer_id: str) -> bytes | None:
    """One phase's trace as PNG bytes, or None when nothing is drawn on its
    layer (or traces cannot be drawn on this deployment)."""
    if not available():
        return None
    view = rules.trace_view(doc, layer_id)
    if view is None:
        return None
    zoom = rules.trace_zoom(doc, view)
    uris = await _background_uris(doc, zoom, view)

    def href(z, x, y):
        return uris.get((z, x, y) if z is not None else None, _EMPTY)

    from web.tacmap import places_for
    places = await places_for(guild_id, doc)
    width = rules.TRACE_WIDTH
    svg = tacmap.render(rules.trace_doc(doc, layer_id), standalone=True, places=places,
                        view=view, tile_zoom=zoom, tile_href=href,
                        size=(width, round(width / rules.TRACE_ASPECT)))
    return await asyncio.to_thread(_rasterise, svg)


async def phase_traces(guild_id: str, campaign_id: int, phases: list) -> list[dict]:
    """The traces for a mission's phases, in order: [{key, caption, png}] for
    each phase fought in an AO that has a map with something drawn on that
    phase's layer. A trace that cannot be drawn is left out, never fatal —
    the pack matters more than its pictures."""
    aos = {a['id']: a for a in await database.list_cp_aos(campaign_id)}
    maps: dict = {}
    out = []
    for number, phase in enumerate(phases, 1):
        if not phase.get('id') or not phase.get('ao_id'):
            continue
        ao = aos.get(int(phase['ao_id']))
        if not ao or not ao.get('map_id'):
            continue
        if ao['map_id'] not in maps:
            record = await database.get_tac_map(ao['map_id'])
            maps[ao['map_id']] = (tacmap.parse(record['doc']).doc
                                  if record and record['guild_id'] == str(guild_id) else None)
        doc = maps[ao['map_id']]
        if doc is None:
            continue
        try:
            png = await draw(str(guild_id), doc, rules.phase_layer_id(phase['id']))
        except Exception as e:
            print(f"⚠️ maptrace: phase {phase['id']} could not be drawn: {e!r}")
            png = None
        if png:
            title = (phase.get('title') or '').strip()
            out.append({'key': f"trace:{phase['id']}", 'png': png,
                        'caption': f"Phase {number}{' — ' + title if title else ''} · "
                                   f"{ao['name']}"})
    return out
