"""The terrain's own town names.

They hang off a terrain rather than a map, are read out of an OCAP export
where that terrain was built in a way that carries them, and are pasted in
from the game where it was not.

Part of `utils/tacmap`, split out of one 3,257-line module. The package's
`__init__` re-exports every name, so `from utils import tacmap` and
`tacmap.anything` work exactly as they did.
"""
import gzip
import json
import re
from urllib.parse import unquote
from xml.sax.saxutils import escape, quoteattr
from .base import (
    DEFAULT_EXTENT,
    DEFAULT_PLACES,
    DEFAULT_PLACE_KIND,
    MIN_LABEL,
    PLACE_GROUPS,
    PLACE_KINDS,
    PLACE_SIZE,
    _INDEX_NAME,
)
from .doc import (_clamp, _number, _text)


#
# An Arma terrain knows what its towns are called; the tiles do not. OCAP's
# renders come out of the game's own map export as pure topography, and the
# game draws the names over that afterwards from `CfgWorlds >> Names` — so a
# tile pyramid arrives with roads, buildings and contours and not one label on
# it. Nothing here was being dropped: the names never came.
#
# They belong to the **terrain**, not to one map, because every plan drawn on
# Tanoa wants the same ones. `tac_terrains.places` holds them and a map only
# decides how much of them to show — which is what the groups below are for.
# A terrain that knows six hundred places would otherwise bury the plan it is
# supposed to be underneath.

MAX_PLACES = 1500
MAX_PLACE_NAME = 60


def place_kind(raw) -> str:
    """One of `PLACE_KINDS`, from whatever the game or the archive called it."""
    key = re.sub(r'[^a-z]', '', str(raw or '').lower())
    return key if key in PLACE_KINDS else DEFAULT_PLACE_KIND


def place_group(kind: str) -> str:
    return PLACE_KINDS.get(kind, PLACE_KINDS[DEFAULT_PLACE_KIND])['group']


def parse_places(raw) -> tuple:
    """Read a place list, from either source, and say what was wrong with it.

    Returns `(places, warnings)`. Two shapes are accepted because there are two
    ways in and neither is ours to dictate:

    * `{"name": "Air Station Mike-26", "pos": [4278.8, 3855.6, -217.8]}` — the
      grad_meh / Gruppe Adler metadata an archive may carry, where a third
      component is an elevation nobody here needs.
    * `{"name": "Katkoula", "kind": "NameVillage", "x": 4278, "y": 3855}` —
      what `places_sqf()` dumps out of a running mission.

    Both are lists of objects; a bare `{"locations": [...]}` wrapper is
    unwrapped, because that is the file an archive actually holds. Anything
    nameless is dropped in silence — Arma's terrains are full of `FlatArea` and
    `Invisible` helpers that exist to position things and have nothing to say.
    """
    warnings = []
    # Nothing offered is not the same as something malformed: an upload that
    # left the paste box empty has no place names and no complaint either.
    if raw is None:
        return [], warnings
    if isinstance(raw, (str, bytes)):
        text = raw.strip() if isinstance(raw, str) else raw.decode('utf-8', 'replace').strip()
        if not text:
            return [], warnings
        try:
            raw = json.loads(text)
        except (ValueError, TypeError) as e:
            return [], [f'That is not valid JSON ({e}).']

    if isinstance(raw, dict):
        # `map.json` itself, or the wrapper the dump script writes.
        for key in ('locations', 'places', 'names'):
            if isinstance(raw.get(key), list):
                raw = raw[key]
                break
        else:
            return [], ['That JSON has no "locations" list in it.']

    if not isinstance(raw, list):
        return [], ['The place list has to be a JSON array.']

    places = []
    nameless = 0
    for entry in raw:
        if len(places) >= MAX_PLACES:
            warnings.append(
                f'Only the first {MAX_PLACES} places were kept — that terrain '
                f'lists {len(raw)}.'
            )
            break
        if not isinstance(entry, dict):
            continue
        name = _text(entry.get('name'), MAX_PLACE_NAME)
        if not name:
            nameless += 1
            continue

        pos = entry.get('pos') or entry.get('position')
        if isinstance(pos, (list, tuple)) and len(pos) >= 2:
            x, y = _number(pos[0]), _number(pos[1])
        else:
            x, y = _number(entry.get('x')), _number(entry.get('y'))
        if x is None or y is None:
            continue

        places.append({
            'name': name,
            'kind': place_kind(entry.get('kind') or entry.get('type')),
            'x': round(float(x), 1),
            'y': round(float(y), 1),
        })

    if nameless:
        warnings.append(
            f'{nameless} unnamed location{" was" if nameless == 1 else "s were"} '
            f'skipped — those are the terrain’s own positioning helpers.'
        )
    return places, warnings


def place_counts(places: list) -> dict:
    """How many places each group holds, for the filter to say so."""
    counts = {key: 0 for key, _ in PLACE_GROUPS}
    for place in places or ():
        counts[place_group(place['kind'])] = counts.get(place_group(place['kind']), 0) + 1
    return counts


def from_world(doc: dict, world_x: float, world_y: float) -> tuple:
    """Arma's world metres, as a point on the sheet — the inverse of `to_world`.

    This is what puts a place where it belongs: the names arrive in world
    coordinates, and the sheet may be a crop of the terrain rather than all of
    it, so the map's own corners are the only thing that can place them. The
    vertical axis flips back, for the same reason it flips going the other way.
    """
    arma = doc.get('arma') or DEFAULT_EXTENT
    span_x = (arma['right'] - arma['left']) or 1.0
    span_y = (arma['top'] - arma['bottom']) or 1.0
    x = (world_x - arma['left']) / span_x * doc['width']
    y = (arma['top'] - world_y) / span_y * doc['height']
    return (x, y)


def visible_places(doc: dict, places: list) -> list:
    """The places this map shows, in drawing order — biggest last.

    Biggest last because a capital's name matters more than a rock's, and where
    two collide the one that survives the overlap should be the one people are
    navigating by.
    """
    settings = doc.get('places') or DEFAULT_PLACES
    if not settings.get('show') or not places:
        return []

    groups = settings.get('groups') or ()
    out = []
    for place in places:
        kind = place.get('kind', DEFAULT_PLACE_KIND)
        # An empty group list means all of them: a terrain whose names have
        # just been imported should show them, not nothing.
        if groups and place_group(kind) not in groups:
            continue
        x, y = from_world(doc, place['x'], place['y'])
        # Off the sheet entirely — a crop of the terrain keeps only what it
        # covers, and a name in the margin is a name over somebody's legend.
        if not (0 <= x <= doc['width'] and 0 <= y <= doc['height']):
            continue
        out.append({**place, 'sheet_x': x, 'sheet_y': y,
                    'rank': PLACE_KINDS.get(kind, PLACE_KINDS[DEFAULT_PLACE_KIND])['rank']})
    out.sort(key=lambda place: place['rank'])
    return out


def places_svg(doc: dict, places: list) -> str:
    """The place names as one `<g>`, drawn under the plan.

    Under the plan deliberately: these are the terrain talking, and a symbol
    somebody placed must never end up behind a village name. They are drawn the
    way everything outside a frame is drawn here — dark over a pale outline —
    because they sit on satellite imagery that is bright sand in one corner and
    dark jungle in the other.
    """
    shown = visible_places(doc, places)
    if not shown:
        return ''

    scale = _clamp(_number((doc.get('places') or {}).get('scale'), 1.0), 0.4, 3.0)
    parts = []
    for place in shown:
        size = max(PLACE_SIZE.get(place['rank'], PLACE_SIZE[1]) * scale, MIN_LABEL * 0.6)
        x = round(place['sheet_x'], 2)
        y = round(place['sheet_y'], 2)
        name = escape(place['name'])
        # A capital is set wider as well as larger, which is what tells it from
        # a village at a glance when both are on screen at once.
        tracking = ' letter-spacing="1.2"' if place['rank'] >= 4 else ''
        common = (f'x="{x}" y="{y}" text-anchor="middle" '
                  f'font-size="{round(size, 2)}" font-family="inherit"{tracking}')
        parts.append(
            f'<text {common} stroke="#ffffff" stroke-width="{round(size / 4, 2)}" '
            f'stroke-linejoin="round" fill="none" opacity="0.85">{name}</text>'
            f'<text {common} fill="#1b2027">{name}</text>'
        )
    return f'<g class="tm-places" pointer-events="none">{"".join(parts)}</g>'


def places_sqf() -> str:
    """The script that copies a terrain's own place names out of a mission.

    The mirror of `to_sqf()`, and the same door: vanilla Arma cannot send
    anything out, so the clipboard is how data leaves it, exactly as the debug
    console is how the plan gets in. It reads `CfgWorlds >> worldName >> Names`
    rather than `nearestLocations`, because the config is the terrain's whole
    list — including the types a radius search would miss — and it is what the
    game itself draws the map labels from.

    The name is stripped of quotes and backslashes on the way out so that what
    lands on the clipboard is always valid JSON. Arma's place names do not
    contain either, but the one that does must not cost the whole terrain.
    """
    return '\n'.join((
        '// Copy this terrain\'s place names — run it in a mission on that terrain.',
        '// Open the debug console as a logged-in admin, paste, press LOCAL EXEC.',
        '// The names go to your clipboard as JSON; paste them into the',
        '// "Place names" box on the terrain\'s page.',
        'private _out = [];',
        'private _names = configFile >> "CfgWorlds" >> worldName >> "Names";',
        'for "_i" from 0 to (count _names - 1) do {',
        '    private _entry = _names select _i;',
        '    if (isClass _entry) then {',
        '        private _name = getText (_entry >> "name");',
        '        // SQF writes a literal quote as "" - one in a name',
        '        // would otherwise make the clipboard invalid JSON.',
        '        _name = _name splitString """" joinString "\'";',
        '        private _pos = getArray (_entry >> "position");',
        '        if (_name != "" && {count _pos >= 2}) then {',
        '            _out pushBack format [',
        '                "{""name"":""%1"",""kind"":""%2"",""x"":%3,""y"":%4}",',
        '                _name, getText (_entry >> "type"),',
        '                _pos select 0, _pos select 1',
        '            ];',
        '        };',
        '    };',
        '};',
        'copyToClipboard format [',
        '    "{""terrain"":""%1"",""locations"":[%2]}",',
        '    worldName, _out joinString ","',
        '];',
        'hint format ["%1 place names copied to your clipboard.", count _out];',
    ))


# The address an uploaded terrain is served from — `/t/7`, the other half of
# `database.tac_terrain_usage()`, which finds the maps by searching for exactly
# this string in their documents. Keeping the pattern here means the two
# directions of the same fact are written once each and next to their own code.
_TERRAIN_URL = re.compile(r'^/t/(\d+)$')


def terrain_id(doc: dict):
    """The stored terrain this map is drawn on, or None.

    None covers both a map on no background at all and one pointing at an OCAP
    server, which is somebody else's origin and has no row here to hang place
    names off.
    """
    background = doc.get('background') or {}
    if background.get('kind') != 'tiles':
        return None
    match = _TERRAIN_URL.match((background.get('url') or '').strip())
    return int(match.group(1)) if match else None


#
# The names are in the OCAP data after all — just not in `map.json`. OCAP's
# maptool builds a terrain from a **grad_meh** export, and grad_meh writes the
# locations as vector data beside the tiles:
#
#     geojson/locations/<locationtype>.geojson.gz
#
# one gzipped GeoJSON file per Arma location type, Point geometry, coordinates
# already in Arma's own CRS, and the name in `properties.name`. The file names
# are lowercase location types — `namecity`, `namevillage`, `hill` — which is
# exactly what `PLACE_KINDS` is keyed on, so the file a place came from is its
# kind and nothing has to be guessed.
#
# What a given server actually serves is the one thing this cannot know, so
# `places_from_geojson()` takes one file at a time and the caller reports which
# addresses answered. Probing beats assuming: an OCAP instance may serve the
# whole grad_meh export, only the tiles, or the tiles under a different root.

# Where a map folder may keep them, relative to the folder itself. Ordered by
# how likely they are; the caller stops at the first that answers.
GEOJSON_DIRS = ('geojson/locations', 'locations', 'geojson')


# The location types worth asking for by name when a directory cannot be
# listed. Every key of PLACE_KINDS would be 21 requests for a handful of files
# that exist, so this is the settlement and landmark set people actually plan
# by; a listing that works gets everything regardless.
PROBE_KINDS = ('namecitycapital', 'namecity', 'namevillage', 'namelocal',
               'airport', 'hill', 'mount', 'namemarine', 'ruin')


def places_from_geojson(raw, kind: str = None) -> tuple:
    """One grad_meh locations file, as places.

    Accepts the gzipped bytes, the plain bytes or the decoded text — a server
    may or may not decompress `.gz` for us on the way out, and which it did is
    not worth a branch at the call site.

    `kind` is the location type the file is named after; it is what the file
    says every place in it is. An unknown one falls back the same way
    `parse_places()` does, so a modded terrain's own type still lands.
    """
    warnings = []
    if isinstance(raw, (bytes, bytearray)):
        # gzip magic. A server that already decompressed leaves plain JSON.
        if raw[:2] == b'\x1f\x8b':
            try:
                raw = gzip.decompress(bytes(raw))
            except (OSError, EOFError) as e:
                # A truncated stream raises EOFError rather than OSError, and a
                # half-delivered file is exactly what a flaky server gives.
                return [], [f'That file is not readable gzip ({e!r}).']
        try:
            raw = raw.decode('utf-8-sig')
        except UnicodeDecodeError as e:
            return [], [f'That file is not UTF-8 ({e}).']

    if isinstance(raw, str):
        try:
            raw = json.loads(raw or '{}')
        except (ValueError, TypeError) as e:
            return [], [f'That is not valid GeoJSON ({e}).']

    # A FeatureCollection, or the bare array of features grad_meh's spec
    # describes. Both are in the wild, so both are read.
    if isinstance(raw, dict):
        features = raw.get('features')
        if not isinstance(features, list):
            return [], ['That GeoJSON has no features in it.']
    elif isinstance(raw, list):
        features = raw
    else:
        return [], ['That is not GeoJSON.']

    resolved = place_kind(kind) if kind else DEFAULT_PLACE_KIND
    places = []
    nameless = 0
    for feature in features:
        if not isinstance(feature, dict):
            continue
        properties = feature.get('properties')
        properties = properties if isinstance(properties, dict) else {}
        name = _text(properties.get('name'), MAX_PLACE_NAME)
        if not name:
            # Arma's terrains are full of unnamed positioning helpers, and
            # grad_meh exports them like any other location.
            nameless += 1
            continue

        geometry = feature.get('geometry')
        geometry = geometry if isinstance(geometry, dict) else {}
        coordinates = geometry.get('coordinates')
        # Point only. A location with an area is still placed by its centre in
        # the export, and anything else is not a thing to write a name at.
        if not (isinstance(coordinates, (list, tuple)) and len(coordinates) >= 2):
            continue
        x, y = _number(coordinates[0]), _number(coordinates[1])
        if x is None or y is None:
            continue

        places.append({'name': name, 'kind': resolved,
                       'x': round(float(x), 1), 'y': round(float(y), 1)})

    if nameless:
        warnings.append(
            f'{nameless} unnamed location{" was" if nameless == 1 else "s were"} '
            f'skipped.'
        )
    return places, warnings


def geojson_kind(filename: str) -> str:
    """The location type a grad_meh file name stands for.

    `namecity.geojson.gz` → `namecity`. The name is the type, lowercased, which
    is what makes the file its own label.
    """
    base = (filename or '').rsplit('/', 1)[-1]
    for suffix in ('.geojson.gz', '.json.gz', '.geojson', '.json', '.gz'):
        if base.lower().endswith(suffix):
            base = base[:-len(suffix)]
            break
    return place_kind(base)


def is_location_file(filename: str) -> bool:
    """Whether a listed file is one of Arma's location types.

    This is the guard on the fetching side, and it is not the same question
    `geojson_kind()` answers. A grad_meh export's `geojson/` folder also holds
    `roads`, `houses` and `objects` — the whole vector map, tens of megabytes —
    and those carry no `name` anyway. Fetching them to discover that is the one
    way this import could cost somebody real bandwidth, so a listed file is
    only fetched when its name *is* a type we know.
    """
    base = (filename or '').rsplit('/', 1)[-1]
    for suffix in ('.geojson.gz', '.json.gz', '.geojson', '.json', '.gz'):
        if base.lower().endswith(suffix):
            base = base[:-len(suffix)]
            break
    return re.sub(r'[^a-z]', '', base.lower()) in PLACE_KINDS


def merge_places(groups) -> tuple:
    """Several files' places as one list, capped and de-duplicated.

    Two files can name the same place — a village that is also a flat area —
    and a name at the same spot twice is a name drawn twice, slightly bolder
    and no more informative.
    """
    seen = set()
    places = []
    dropped = 0
    for group in groups:
        for place in group:
            if len(places) >= MAX_PLACES:
                dropped += 1
                continue
            key = (place['name'].casefold(), round(place['x']), round(place['y']))
            if key in seen:
                continue
            seen.add(key)
            places.append(place)
    warnings = []
    if dropped:
        warnings.append(f'{dropped} places past the {MAX_PLACES} cap were left out.')
    return places, warnings


def place_scope(doc: dict) -> str:
    """What this map's place names are filed under, or '' when nothing fits.

    Place names belong to a *terrain*, not to a map — every plan drawn on Tanoa
    wants the same ones. But a terrain here is one of two things: a row we
    store (`/t/7`) or somebody else's OCAP folder (`…/maps/tem_cham`), and only
    the first has a row to hang anything off. So the scope is a string both can
    produce:

        /t/7                        -> 't:7'
        https://ocap…/maps/tem_cham -> 'tem_cham'

    The OCAP form is the terrain's own world name, which is what the folder is
    named after — so two guilds' maps on the same OCAP terrain share a scope,
    and a map moved between OCAP servers keeps one.
    """
    background = doc.get('background') or {}
    if background.get('kind') != 'tiles':
        return ''
    url = (background.get('url') or '').strip().rstrip('/')
    if not url:
        return ''

    stored = terrain_id(doc)
    if stored is not None:
        return f't:{stored}'

    # The last path segment of the map folder. unquote first: a folder with a
    # space in it arrives percent-encoded and would otherwise scope apart from
    # itself.
    segment = unquote(url.rsplit('/', 1)[-1]).strip().lower()
    return segment[:80] if _INDEX_NAME.match(segment) else ''
