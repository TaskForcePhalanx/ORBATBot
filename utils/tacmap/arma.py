"""The plan into a running mission, and back out of one.

`to_sqf()` writes the `createMarker` calls somebody pastes into the debug
console — the one channel vanilla Arma leaves open — and
`import_arma_markers()` reads `allMapMarkers` back the other way.
`export_report()` says what will not survive the trip.

Part of `utils/tacmap`, split out of one 3,257-line module. The package's
`__init__` re-exports every name, so `from utils import tacmap` and
`tacmap.anything` work exactly as they did.
"""
import html as _html
import json
import math
import re
from urllib.parse import unquote
from xml.sax.saxutils import escape, quoteattr
from .base import (
    AFFILIATIONS,
    ARMA_COLOURS,
    DEFAULT_EXTENT,
    DEFAULT_MARKER,
    DEFAULT_SIZE,
    DEFAULT_TILE_ZOOM,
    DEFAULT_WEIGHT,
    ECHELONS,
    EXPORT_DASH,
    LINE_COLOURS,
    LINE_LABEL_MARKER,
    LINE_LABEL_SIZE,
    metres_per_unit,
    MARKERS,
    MAX_DASHES,
    MAX_LABEL,
    MAX_SIZE,
    MAX_TILE_ZOOM,
    MIN_SIZE,
    SHAPES,
    SHAPE_FILLS,
    SYMBOLS,
    TILE_SIZE,
    UNIT_CLASSES,
    _INDEX_NAME,
    _rgb,
    arma_colour,
    marker_key,
    picture_key,
    unit_class,
)
from .doc import (_clamp, _number, _text, ordered_items, summarise)
from .places import (from_world)
from .drawing import (_centroid, shape_corners, unit_picture)


# How many terrains one directory listing may offer. A unit's OCAP folder
# holds a few dozen; a listing far longer than that is not a map directory
# and there is no reason to render it into a dropdown.
MAX_INDEX_ENTRIES = 300


_HREF = re.compile(r'<a\s[^>]*?href=["\']([^"\'>]+)["\']', re.I)


def parse_map_index(body) -> list:
    """The terrain folders a directory listing offers, as plain names.

    Typing out an OCAP map URL by hand means knowing the world name of a
    terrain, which is exactly the thing nobody remembers — so the folder is
    read and its contents offered as a list. What comes back differs per web
    server, and none of them is worth requiring:

    * nginx with `autoindex_format json`, a list of `{"name", "type"}`
    * a plain JSON list of names, which is what a hand-written index gives
    * nginx or Apache's HTML autoindex, which is a page of `<a href>`

    All three are read the same way, because the answer wanted from each is
    the same: the subdirectory names. Anything that is not a directory name
    is dropped rather than guessed at — a listing is somebody else's page
    and its links are not a place this bot should follow blindly.
    """
    text = body.decode('utf-8', 'replace') if isinstance(body, bytes) else str(body or '')
    names = _index_from_json(text)
    if names is None:
        names = _index_from_html(text)
    keep = []
    for name in names:
        name = unquote(name).strip().strip('/')
        # A listing links back to where it came from and sideways to itself;
        # neither is a terrain.
        if not name or name in ('.', '..') or not _INDEX_NAME.match(name):
            continue
        if name not in keep:
            keep.append(name)
        if len(keep) >= MAX_INDEX_ENTRIES:
            break
    keep.sort(key=str.lower)
    return keep


def _index_from_json(text: str):
    """The names a JSON listing carries, or None when it is not JSON."""
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, list):
        return None
    names = []
    for entry in payload:
        if isinstance(entry, str):
            names.append(entry)
        elif isinstance(entry, dict):
            # nginx says `"type": "directory"`; a listing that says nothing
            # about type is taken at its word, since a file would not have
            # passed the name test anyway.
            if entry.get('type') not in (None, 'directory'):
                continue
            name = entry.get('name') or entry.get('path') or ''
            if isinstance(name, str):
                names.append(name)
    return names


def _index_from_html(text: str) -> list:
    """The hrefs an HTML autoindex carries, unescaped and made relative."""
    names = []
    for href in _HREF.findall(text):
        href = _html.unescape(href)
        # Only a link into this folder is a terrain in it: an absolute URL,
        # a scheme, a query or a fragment all point somewhere else.
        if href.startswith(('http://', 'https://', '//', '/', '?', '#', 'mailto:')):
            continue
        names.append(href.split('?')[0].split('#')[0])
    # nginx and Apache both write a directory with a trailing slash, which is
    # the only thing on the page that tells a terrain from the readme sitting
    # next to it. A hand-written index may link without one, so the rule is
    # "prefer the folders when the page marks any" rather than "require it".
    folders = [name for name in names if name.endswith('/')]
    return folders or names


def ocap_settings(payload: dict, base_url: str) -> dict:
    """An OCAP `map.json` turned into a background and an Arma extent.

    OCAP already renders every terrain its users play on, and its `map.json`
    carries the one number the export could not work out for itself:
    `worldSize`, the terrain's edge in metres. So pointing at an OCAP map
    folder settles the background **and** the calibration in one step — which
    is the whole reason this import exists rather than a tile-URL field.

        {"name": "Cham", "worldName": "tem_cham", "worldSize": 8192,
         "imageSize": 16384, "multiplier": 2, "maxZoom": 6, ...}

    `imageSize` is the pyramid's edge in pixels; the bottom level therefore
    holds `imageSize / 256` tiles per side, and `maxZoom` is its level. Both are
    read, and the smaller is believed: asking for a level the folder does not
    have is a screenful of missing tiles.
    """
    world = _number(payload.get('worldSize')) if isinstance(payload, dict) else None
    if not world or world <= 0:
        raise ValueError(
            "That map.json has no usable worldSize — it may not be an OCAP map folder."
        )

    image = _number(payload.get('imageSize'), 0) or 0
    from_image = int(math.log2(image / TILE_SIZE)) if image >= TILE_SIZE else MAX_TILE_ZOOM
    stated = _number(payload.get('maxZoom'))
    max_zoom = min(int(stated) if stated and stated > 0 else from_image, from_image)

    name = (_text(payload.get('name'), 80) or _text(payload.get('worldName'), 80))
    return {
        'background': {
            'kind': 'tiles',
            'url': base_url.rstrip('/'),
            'opacity': 1.0,
            'zoom': min(DEFAULT_TILE_ZOOM, max_zoom),
            'max_zoom': max_zoom,
            'name': name,
        },
        # The pyramid covers the whole terrain, so the sheet's corners are the
        # world's corners — which is exactly what the Arma export needs.
        'arma': {'terrain': name, 'left': 0.0, 'bottom': 0.0,
                 'right': round(world, 2), 'top': round(world, 2)},
        'name': name,
    }


def terrain_settings(terrain_id: int, name: str, world_size: float,
                     max_zoom: int) -> dict:
    """The same as `ocap_settings()`, for a terrain this bot holds itself.

    Which is the point of holding it: an uploaded archive carries the same
    `map.json` an OCAP folder does, so a map set up from a file knows exactly
    what a map set up from a URL knows — including where its corners are in
    Arma's world.
    """
    return {
        'background': {
            'kind': 'tiles',
            'url': f'/t/{int(terrain_id)}',
            'opacity': 1.0,
            'zoom': min(DEFAULT_TILE_ZOOM, max_zoom),
            'max_zoom': max_zoom,
            'name': name,
        },
        'arma': {'terrain': name, 'left': 0.0, 'bottom': 0.0,
                 'right': round(float(world_size), 2),
                 'top': round(float(world_size), 2)},
        'name': name,
    }


def to_world(doc: dict, x: float, y: float) -> tuple:
    """A point on the sheet, in Arma's world metres.

    The sheet's y grows downward and Arma's grows north, so the vertical axis
    is flipped here. Everything else is a straight stretch between the corners
    the person gave, which is what makes a cropped map work as well as a whole
    one.
    """
    arma = doc.get('arma') or DEFAULT_EXTENT
    world_x = arma['left'] + (x / doc['width']) * (arma['right'] - arma['left'])
    world_y = arma['top'] - (y / doc['height']) * (arma['top'] - arma['bottom'])
    return (round(world_x, 2), round(world_y, 2))


def length_m(doc: dict, points: list, closed: bool = False) -> float:
    """A line's length on the ground, in metres. The editor mirrors this."""
    across, down = metres_per_unit(doc)
    path = list(points) + ([points[0]] if closed and points else [])
    return round(sum(math.hypot((b[0] - a[0]) * across, (b[1] - a[1]) * down)
                     for a, b in zip(path, path[1:])), 1)


def grid_ref(doc: dict, x: float, y: float, digits: int = 6) -> str:
    """The Arma grid reference of a point on the sheet — `045112` for six
    digits, 100 m squares. Counted from the terrain's south-west corner, as
    most terrains' map grids are."""
    world_x, world_y = to_world(doc, x, y)
    half = digits // 2
    size = 10 ** (5 - half)
    width = half
    return (f'{max(0, int(world_x // size)) % 10 ** width:0{width}d}'
            f'{max(0, int(world_y // size)) % 10 ** width:0{width}d}')


def _sqf_string(value: str) -> str:
    """SQF quotes a quote by doubling it, and has no escape for a newline."""
    flat = ' '.join((value or '').split())
    return '"' + flat.replace('"', '""') + '"'


def _arma_type(item: dict) -> str:
    if item['kind'] == 'point':
        return MARKERS[marker_key(item)]['arma']
    if item['kind'] == 'text':
        return 'Empty'
    return UNIT_CLASSES[unit_picture(item)]['arma']


def _marker_text(item: dict) -> str:
    if item['kind'] == 'point' and item.get('glyph'):
        return f"{item['glyph']} {item['label']}".strip()
    return item['label']


def _bearing(start: tuple, end: tuple) -> float:
    """Compass degrees from one world point to the next — 0 is north."""
    return round(math.degrees(math.atan2(end[0] - start[0], end[1] - start[1])) % 360, 1)


# The name Arma gives a marker somebody placed themselves, and the only shape
# it lets that person move or delete: `_USER_DEFINED #owner/index/channel`.
# The owner is the machine's own `clientOwner`, which is why an editable export
# is run once, locally, rather than broadcast — see `to_sqf()`.
USER_MARKER = '_USER_DEFINED #'


# Which channel an editable marker belongs to. Arma's own ids, and the reason
# this is a choice: markers placed in Global are seen by everybody in the
# server, Side by one side, Group by one group.
ARMA_CHANNELS = (
    (0, 'Global'),
    (1, 'Side'),
    (2, 'Command'),
    (3, 'Group'),
    (4, 'Vehicle'),
    (5, 'Direct'),
)
DEFAULT_CHANNEL = 0


def _map_number(prefix: str) -> str:
    """A number of this map's own, to keep two maps' markers apart.

    An editable marker's index has to be a number — the engine parses the
    name — so the prefix cannot ride along in it as text. The map's own digits
    do the same job, and a prefix carrying none falls back to something stable
    rather than colliding with every other such map.
    """
    digits = ''.join(character for character in prefix if character.isdigit())
    return digits or str(sum(ord(character) for character in prefix) % 900 + 100)


def dash_runs(points: list, style: str, closed: bool = False) -> list:
    """A dashed or dotted line as the pieces Arma will draw, in sheet units:
    a list of polylines, one per dash. Arma has no dashed line marker, so a
    dashed line goes across as many short solid ones. A solid line is one run,
    itself.

    Past `MAX_DASHES` the dashes are lengthened rather than multiplied, so a
    long dashed boundary costs at most that many markers."""
    path = [tuple(p) for p in points] + ([tuple(points[0])] if closed and points else [])
    if style not in EXPORT_DASH or len(path) < 2:
        return [path]
    total = sum(math.dist(path[n], path[n + 1]) for n in range(len(path) - 1))
    on, off = EXPORT_DASH[style]
    if total / (on + off) > MAX_DASHES:
        stretch = total / (on + off) / MAX_DASHES
        on, off = on * stretch, off * stretch
    runs, run = [], [path[0]]
    drawing, left = True, on
    for a, b in zip(path, path[1:]):
        length = math.dist(a, b)
        done = 0.0
        while length - done > left:
            done += left
            t = done / length
            cut = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            if drawing:
                run.append(cut)
                runs.append(run)
            else:
                run = [cut]
            drawing = not drawing
            left = on if drawing else off
        left -= length - done
        if drawing:
            run.append(b)
    if drawing and len(run) > 1:
        runs.append(run)
    return [[(round(x, 2), round(y, 2)) for x, y in r] for r in runs if len(r) > 1]


def shape_outline(item: dict, steps: int = 48) -> list:
    """A rectangle's corners or an ellipse as a polygon, turned into place —
    for an outline Arma has to draw as line markers (a dashed one)."""
    if item['shape'] != 'ellipse':
        return shape_corners(item)
    a, b = item['w'] / 2, item['h'] / 2
    turn = math.radians(item.get('rotation') or 0)
    cos, sin = math.cos(turn), math.sin(turn)
    out = []
    for n in range(steps):
        t = 2 * math.pi * n / steps
        dx, dy = a * math.cos(t), b * math.sin(t)
        out.append((round(item['x'] + dx * cos - dy * sin, 2),
                    round(item['y'] + dx * sin + dy * cos, 2)))
    return out


def to_sqf(doc: dict, *, prefix: str, title: str = '',
           editable: bool = False, channel: int = DEFAULT_CHANNEL) -> str:
    """The map as a script that puts these markers into a running mission.

    A script somebody pastes, rather than something this bot sends: vanilla
    Arma has no way to fetch anything from outside, so the way in without a mod
    is the debug console. `createMarker` is global by nature — one machine
    running it draws the plan for everybody — so LOCAL EXEC is enough.

    Running it a second time **replaces** these markers instead of doubling
    them. The read-only export does that by name: every marker is named after
    this map and the script deletes that set before it draws, which is why the
    prefix has to be this map's alone.

    **`editable` decides whether the plan can be touched in game**, and it
    changes how the markers are named. Arma only lets somebody move or delete
    a marker they own, and it works out who owns one by parsing the name it
    gives its own: `_USER_DEFINED #owner/index/channel`, all three numeric.
    Anything else — including our prefix with `_USER_DEFINED ` merely stuck in
    front of it, which is what this first tried — is read-only on the map.

    Two things follow from that name, and both are load-bearing:

    - **The owner is `clientOwner`, so the script is run once, locally.**
      GLOBAL EXEC runs the code on every machine, and each would fill its own
      id into the name and create its own set — the same plan three times over
      on a three-player server. With a read-only export the names match and
      the duplicates collapse; here they do not.
    - **The map's prefix cannot ride in the name**, since the index is
      numeric. So an editable export remembers what it drew in a public
      mission variable and deletes that on the next run, which is the same
      promise by a different route.

    `channel` is the last part of that name — see `ARMA_CHANNELS`.
    """
    prefix = ''.join(character for character in (prefix or 'tacmap')
                     if character.isalnum() or character == '_')
    prefix = (prefix or 'tacmap') + '_'
    channel = channel if channel in dict(ARMA_CHANNELS) else DEFAULT_CHANNEL
    number = _map_number(prefix)
    store = f'tacmap_{prefix}'.rstrip('_')

    # Only what is on a visible layer: a marker somebody switched off is not
    # part of the plan they are handing over, and Arma has no way to switch it
    # off again once it is drawn.
    items = ordered_items(doc)
    lines = [f'// {title or "Tactical map"} — {summarise(doc)}']
    if (doc.get('mask') or {}).get('on'):
        # A grey cover with holes cut in it is not something a marker can be,
        # and Arma's own cover-map effect takes one rectangle. The AO borders
        # come across; the grey stays on the website.
        lines.append('// The grey mask outside the AOs is not exported — the AO borders are.')
    if editable:
        channel_name = dict(ARMA_CHANNELS)[channel]
        lines += [
            '// Paste into the Arma 3 debug console and press LOCAL EXEC as a',
            '// logged-in admin — once, on one machine. The markers are global',
            '// either way, and GLOBAL EXEC would draw one set per machine.',
            '// Click a marker and press DEL to remove it, or drag it to move',
            '// it. Lines and areas are polyline markers, which the map may',
            '// not let you pick up. Running this again replaces the set.',
            f'private _chan = {channel};  '
            f'// {" · ".join(f"{key} {name}" for key, name in ARMA_CHANNELS)}',
            'private _own = format ["' + USER_MARKER + '%1/", clientOwner];',
            "private _c = '/' + str _chan;",
            f'{{ deleteMarker _x }} forEach (missionNamespace getVariable '
            f'[{_sqf_string(store)}, []]);',
            'private _n = [];',
            'private _m = "";',
        ]
    else:
        lines += [
            '// Paste into the Arma 3 debug console and press LOCAL EXEC as a',
            '// logged-in admin. The markers are global, so one machine',
            '// running this draws them for everybody. Running it again',
            '// replaces these markers.',
            f'private _p = {_sqf_string(prefix)};',
            '{ if (_x select [0, count _p] == _p) then { deleteMarker _x } } '
            'forEach allMapMarkers;',
            'private _m = "";',
        ]

    drawn = [0]

    def marker_name(index: int, suffix: str = '') -> str:
        """What goes in the `createMarker` call, as SQF.

        The two exports name a marker differently on purpose — see `to_sqf`.
        An editable one is numbered per marker rather than per item, because
        the engine parses that part of the name as a number and an arrow's
        "2a" is not one.
        """
        if not editable:
            return f'_p + "{index}{suffix}"'
        drawn[0] += 1
        return f'_own + "{number}{drawn[0]:03d}" + _c'

    for index, item in enumerate(items, start=1):
        colour = arma_colour(item)
        name = marker_name(index)
        text = _marker_text(item)

        def polylines(runs: list, suffix: str):
            """Line markers for each run — one for a solid line, one per dash
            for a dashed or dotted one."""
            for number, run in enumerate(runs):
                world = [to_world(doc, x, y) for x, y in run]
                flat = ', '.join(f'{point[0]}, {point[1]}' for point in world)
                piece = name if number == 0 and not suffix else marker_name(
                    index, f'{suffix}{number}')
                lines.append(f'_m = createMarker [{piece}, [{world[0][0]}, {world[0][1]}]];')
                lines.append('_m setMarkerShape "POLYLINE";')
                lines.append(f'_m setMarkerColor "{colour}";')
                lines.append(f'_m setMarkerPolyline [{flat}];')
                if editable:
                    lines.append('_n pushBack _m;')

        if item['kind'] == 'shape' and item['style'] != 'solid' and not item.get('ao'):
            # Arma's rectangle and ellipse markers have no dashed border, so
            # a dashed outline goes across as dashes of line markers — and the
            # fill, if there is one, as the shape with no border of its own.
            polylines(dash_runs(shape_outline(item), item['style'], closed=True), 'd')
            if item['fill'] != 'none':
                item = {**item, 'style': 'solid'}
                name = marker_name(index, 'f')
            else:
                continue

        if item['kind'] == 'shape':
            centre = to_world(doc, item['x'], item['y'])
            # setMarkerSize takes the half-width and half-height, in metres.
            arma = doc.get('arma') or DEFAULT_EXTENT
            across = abs(arma['right'] - arma['left']) / doc['width']
            down = abs(arma['top'] - arma['bottom']) / doc['height']
            half_w = round(item['w'] / 2 * across, 2)
            half_h = round(item['h'] / 2 * down, 2)
            lines.append(f'_m = createMarker [{name}, [{centre[0]}, {centre[1]}]];')
            lines.append(f'_m setMarkerShape "{SHAPES[item["shape"]]["arma"]}";')
            lines.append(f'_m setMarkerSize [{half_w}, {half_h}];')
            # Both turn clockwise: the sheet's y grows downward, Arma's
            # bearing is clockwise from north.
            lines.append(f'_m setMarkerDir {round((item.get("rotation") or 0) % 360, 1)};')
            brush = 'Border' if item.get('ao') else SHAPE_FILLS[item['fill']]['brush']
            lines.append(f'_m setMarkerBrush "{brush}";')
            lines.append(f'_m setMarkerColor "{colour}";')
            if text:
                lines.append(f'_m setMarkerText {_sqf_string(text)};')
            if editable:
                lines.append('_n pushBack _m;')
            continue

        if item['kind'] in ('line', 'area'):
            # Arma has no closed polyline, so an area repeats its first point;
            # and no dashed one, so a dashed line is cut into its dashes.
            runs = dash_runs(item['points'], item['style'], closed=item['kind'] == 'area')
            polylines(runs, '' if len(runs) == 1 else 'd')
            points = [to_world(doc, x, y) for x, y in item['points']]
            if text:
                # A line marker shows no text, so the name rides on a small
                # marker at each place it is written on the map: the middle
                # corner, or the ends of a phase line.
                if item['kind'] == 'area':
                    spots = [_centroid(item['points'])]
                elif item.get('label_at', 'middle') == 'middle':
                    spots = [item['points'][len(item['points']) // 2]]
                else:
                    want = {'ends': (0, -1), 'end': (-1,), 'start': (0,)}[item['label_at']]
                    spots = [item['points'][n] for n in want]
                for count, (sx, sy) in enumerate(spots):
                    world = to_world(doc, sx, sy)
                    lines.append(f'_m = createMarker [{marker_name(index, "t" + str(count))}, '
                                 f'[{world[0]}, {world[1]}]];')
                    lines.append(f'_m setMarkerType "{LINE_LABEL_MARKER}";')
                    lines.append(f'_m setMarkerColor "{colour}";')
                    lines.append(f'_m setMarkerSize [{LINE_LABEL_SIZE}, {LINE_LABEL_SIZE}];')
                    lines.append(f'_m setMarkerText {_sqf_string(text)};')
                    if editable:
                        lines.append('_n pushBack _m;')
            ends = {'end': ((-2, -1),), 'start': ((1, 0),),
                    'both': ((1, 0), (-2, -1))}.get(item.get('arrow') or '', ())
            if item['kind'] != 'line' or len(points) < 2:
                ends = ()
            for count, (back, tip) in enumerate(ends):
                lines.append(f'_m = createMarker [{marker_name(index, "a" + str(count))}, '
                             f'[{points[tip][0]}, {points[tip][1]}]];')
                lines.append('_m setMarkerType "mil_arrow";')
                lines.append(f'_m setMarkerColor "{colour}";')
                lines.append(f'_m setMarkerDir {_bearing(points[back], points[tip])};')
                if editable:
                    lines.append('_n pushBack _m;')
            continue

        world = to_world(doc, item['x'], item['y'])
        lines.append(f'_m = createMarker [{name}, [{world[0]}, {world[1]}]];')
        lines.append(f'_m setMarkerType "{_arma_type(item)}";')
        lines.append(f'_m setMarkerColor "{colour}";')
        if text:
            lines.append(f'_m setMarkerText {_sqf_string(text)};')
        if item['size'] != 1:
            size = round(item['size'], 2)
            lines.append(f'_m setMarkerSize [{size}, {size}];')
        if item['kind'] in ('unit', 'point') and item.get('rotation'):
            lines.append(f"_m setMarkerDir {round(item['rotation'] % 360, 1)};")
        if editable:
            lines.append('_n pushBack _m;')
        if item['kind'] == 'unit' and item.get('echelon') in ECHELONS:
            # The size is a marker of its own in Arma, stacked on the unit.
            lines.append(f'_m = createMarker [{marker_name(index, "s")}, '
                         f'[{world[0]}, {world[1]}]];')
            lines.append(f'_m setMarkerType "{ECHELONS[item["echelon"]]["arma"]}";')
            lines.append(f'_m setMarkerColor "{colour}";')
            if item['size'] != 1:
                lines.append(f'_m setMarkerSize [{size}, {size}];')
            if editable:
                lines.append('_n pushBack _m;')

    if editable:
        # What was drawn, so the next run can take exactly it away again —
        # public, so it is the same list on every machine and for whoever
        # pastes the corrected plan next.
        lines.append(f'missionNamespace setVariable [{_sqf_string(store)}, _n, true];')
        lines.append('hint format ["%1 markers drawn", count _n];')

    if not items:
        lines.append('// Nothing is drawn on this map yet.')
    return '\n'.join(lines) + '\n'


#
# The mirror of `to_sqf()`. Vanilla Arma can send nothing out except through
# the clipboard, so a script run in the debug console copies every marker to
# it and the person pastes that here. A plan finished at the map table in game
# can then be kept, briefed and exported again.

ARMA_DUMP_SQF = '''// Copies every map marker in this mission to the clipboard, for the
// tactical map's "Import from Arma". Debug console, LOCAL EXEC.
private _out = allMapMarkers apply {
  [_x, markerShape _x, markerType _x, (markerPos _x) select [0, 2], markerText _x,
   markerColor _x, markerSize _x, markerDir _x, markerBrush _x, markerPolyline _x]
};
copyToClipboard str _out;
hint format ["%1 markers copied - paste them into the map", count _out];
'''


MAX_DUMP = 2_000_000


class SQFError(ValueError):
    pass


def parse_sqf_value(text: str):
    """What `str` makes of an SQF array, read back: nested arrays, strings
    (where a quote is written twice), numbers and true/false. Paths with
    backslashes come through as they are — SQF does not escape them."""
    text = text.strip()
    at = 0

    def skip():
        nonlocal at
        while at < len(text) and text[at] in ' \t\r\n,':
            at += 1

    def value():
        nonlocal at
        skip()
        if at >= len(text):
            raise SQFError('That ended in the middle.')
        ch = text[at]
        if ch == '[':
            at += 1
            items = []
            while True:
                skip()
                if at >= len(text):
                    raise SQFError('An array was never closed.')
                if text[at] == ']':
                    at += 1
                    return items
                items.append(value())
        if ch == '"':
            at += 1
            out = []
            while True:
                end = text.find('"', at)
                if end < 0:
                    raise SQFError('A piece of text was never closed.')
                out.append(text[at:end])
                at = end + 1
                if at < len(text) and text[at] == '"':
                    out.append('"')
                    at += 1
                    continue
                return ''.join(out)
        match = re.match(r'-?\d+(?:\.\d+)?(?:e[-+]?\d+)?|true|false|any|<null>',
                         text[at:], re.I)
        if not match:
            raise SQFError(f'Unexpected "{text[at:at + 12]}".')
        at += match.end()
        word = match.group(0).lower()
        if word in ('true', 'false'):
            return word == 'true'
        if word in ('any', '<null>'):
            return None
        return float(word)

    result = value()
    skip()
    if at != len(text):
        raise SQFError('There is more after the end.')
    return result


# Arma's side prefixes, and unit marker suffixes, back onto our symbols.
_ARMA_SIDE_FROM = {'b': 'friend', 'o': 'hostile', 'n': 'neutral', 'c': 'civ', 'u': 'unknown'}


def _unit_from_class(mtype: str):
    """(side, symbol) of an Arma NATO marker, or None when it is not one."""
    wanted = picture_key(mtype)
    for side in AFFILIATIONS:
        for symbol in SYMBOLS:
            if picture_key(unit_class(side, symbol)) == wanted:
                return side, symbol
    return None


_SIDE_COLOURS = {
    'ColorWEST': '#3d8ee8', 'ColorBLUFOR': '#3d8ee8', 'ColorEAST': '#e03b3b',
    'ColorOPFOR': '#e03b3b', 'ColorGUER': '#3fb950', 'ColorIndependent': '#3fb950',
    'ColorCIV': '#a06ee8', 'ColorCivilian': '#a06ee8', 'ColorUNKNOWN': '#f2c832',
}
_BRUSH_FILL = {'border': 'none', 'solid': 'light', 'solidfull': 'light',
               'solidborder': 'light'}


def _colour_from_arma(name: str) -> str:
    """A `CfgMarkerColors` name as one of the palette's colours, or '' (the
    default ink) for Default and anything unknown."""
    if name in _SIDE_COLOURS:
        return _SIDE_COLOURS[name]
    rgb = ARMA_COLOURS.get(name)
    if rgb is None or name == 'ColorBlack':
        return ''
    return min((value for value, _ in LINE_COLOURS),
               key=lambda value: sum((a - b) ** 2 for a, b in zip(_rgb(value), rgb)))


def import_arma_markers(doc: dict, text: str, *, players_only: bool = False) -> tuple:
    """The markers of a pasted dump as items for this map, and what to tell
    the person: how many came in, and what was left out and why.

    Positions go through `from_world()`, so the map's Arma corners have to be
    the mission's terrain. Markers off the sheet are left out rather than
    piled on its edge. `players_only` keeps only what players drew on the map
    themselves (`_USER_DEFINED` markers), leaving out the mission's own."""
    if len(text or '') > MAX_DUMP:
        raise ValueError('That is far more than a marker list — paste only what the script copied.')
    try:
        rows = parse_sqf_value(text or '')
    except SQFError as error:
        raise ValueError(f'That is not what the script copies: {error}')
    if not isinstance(rows, list):
        raise ValueError('That is not what the script copies.')
    across, down = metres_per_unit(doc)
    points_by_marker = {value['arma']: key for key, value in MARKERS.items()}
    by_lower = {name.lower(): key for name, key in points_by_marker.items()}
    items, skipped, unknown, outside = [], 0, {}, 0

    def on_sheet(x, y):
        return -doc['width'] * 0.01 <= x <= doc['width'] * 1.01 and \
            -doc['height'] * 0.01 <= y <= doc['height'] * 1.01

    for row in rows:
        if not isinstance(row, list) or len(row) < 9:
            skipped += 1
            continue
        name, shape, mtype, pos, label, colour, size, direction, brush = row[:9]
        polyline = row[9] if len(row) > 9 else []
        if players_only and not str(name).startswith(USER_MARKER.strip()):
            continue
        label = str(label or '')[:MAX_LABEL]
        tint = _colour_from_arma(str(colour or ''))
        shape = str(shape or '').upper()
        if shape == 'POLYLINE' and isinstance(polyline, list) and len(polyline) >= 4:
            flat = [float(v) for v in polyline if isinstance(v, float)]
            points = [from_world(doc, flat[n], flat[n + 1]) for n in range(0, len(flat) - 1, 2)]
            points = [(round(x, 2), round(y, 2)) for x, y in points]
            if not all(on_sheet(x, y) for x, y in points):
                outside += 1
                continue
            closed = len(points) >= 4 and math.dist(points[0], points[-1]) < 0.5
            item = {'kind': 'area' if closed else 'line',
                    'points': [list(p) for p in (points[:-1] if closed else points)],
                    'label': label, 'color': tint, 'style': 'solid',
                    'weight': DEFAULT_WEIGHT, 'size': DEFAULT_SIZE}
            if not closed:
                item['label_at'] = 'middle'
            items.append(item)
            continue
        if not isinstance(pos, list) or len(pos) < 2:
            skipped += 1
            continue
        x, y = from_world(doc, float(pos[0]), float(pos[1]))
        if not on_sheet(x, y):
            outside += 1
            continue
        x, y = round(x, 2), round(y, 2)
        half = size if isinstance(size, list) and len(size) >= 2 else [1.0, 1.0]
        if shape in ('RECTANGLE', 'ELLIPSE'):
            fill = _BRUSH_FILL.get(str(brush or '').lower(), 'hatch')
            items.append({'kind': 'shape', 'shape': 'rect' if shape == 'RECTANGLE' else 'ellipse',
                          'x': x, 'y': y, 'w': round(2 * float(half[0]) / (across or 1), 2),
                          'h': round(2 * float(half[1]) / (down or 1), 2),
                          'rotation': round(float(direction or 0) % 360, 1),
                          'fill': fill, 'label': label, 'color': tint,
                          'weight': DEFAULT_WEIGHT, 'size': DEFAULT_SIZE})
            continue
        mtype = str(mtype or '')
        # An Arma marker at size 1 is the size this editor draws a new one.
        scale = round(_clamp(float(half[0] or 1) * DEFAULT_SIZE, MIN_SIZE, MAX_SIZE), 2)
        key = points_by_marker.get(mtype) or by_lower.get(mtype.lower())
        if key:
            items.append({'kind': 'point', 'marker': key, 'x': x, 'y': y, 'label': label,
                          'color': '' if MARKERS[key].get('paint') else tint, 'size': scale,
                          'rotation': round(float(direction or 0) % 360, 1)})
            continue
        found = _unit_from_class(mtype)
        if found:
            items.append({'kind': 'unit', 'side': found[0], 'symbol': found[1],
                          'x': x, 'y': y, 'label': label, 'size': scale,
                          'rotation': round(float(direction or 0) % 360, 1)})
            continue
        if mtype in ('Empty', 'EmptyIcon', '') and label:
            items.append({'kind': 'text', 'x': x, 'y': y, 'label': label, 'color': tint,
                          'size': DEFAULT_SIZE * 4})
            continue
        if mtype in ('Empty', 'EmptyIcon', ''):
            skipped += 1
            continue
        # A marker this map has no drawing for yet: a dot, named after what
        # it was, so nothing is silently lost.
        unknown[mtype] = unknown.get(mtype, 0) + 1
        items.append({'kind': 'point', 'marker': DEFAULT_MARKER, 'x': x, 'y': y,
                      'label': (label or mtype)[:MAX_LABEL], 'color': tint, 'size': scale})
    items = _stack_sizes(items)
    notes = [f'{len(items)} marker{"s" if len(items) != 1 else ""} brought in.']
    if outside:
        notes.append(f'{outside} lay outside this map and were left out — check the map\'s '
                     'Arma 3 coordinates match the mission\'s terrain.')
    if unknown:
        listed = ', '.join(f'{name} ×{count}' if count > 1 else name
                           for name, count in sorted(unknown.items())[:8])
        notes.append(f'Drawn as dots, named after their type: {listed}.')
    if skipped:
        notes.append(f'{skipped} had nothing to draw and were left out.')
    return items, notes


def _stack_sizes(items: list) -> list:
    """A size marker (`group_3`) sitting on a unit is that unit's size, the
    way the export writes it; one on its own stays a marker."""
    sizes = {value['arma']: key for key, value in ECHELONS.items()}
    sizes = {picture_key(arma): key for arma, key in sizes.items()}
    units = [item for item in items if item['kind'] == 'unit']
    kept = []
    for item in items:
        echelon = sizes.get(item.get('marker')) if item['kind'] == 'point' else None
        home = next((unit for unit in units if echelon and not unit.get('echelon')
                     and math.dist((unit['x'], unit['y']), (item['x'], item['y'])) < 1),
                    None)
        if home is not None:
            home['echelon'] = echelon
            continue
        kept.append(item)
    return kept


def export_report(doc: dict, sqf: str) -> dict:
    """What happens to this map on its way into Arma, beside the script: how
    many markers it makes, and everything that does not survive the trip."""
    notes = []
    items = doc['items']
    hidden = {layer['id'] for layer in doc['layers'] if not layer['visible']}
    off = sum(1 for item in items if item['layer'] in hidden)
    if off:
        notes.append(f'{off} item{"s" if off != 1 else ""} on hidden layers stay behind.')
    shown = [item for item in items if item['layer'] not in hidden]
    dashed = [item for item in shown if item.get('style') in EXPORT_DASH]
    if dashed:
        notes.append(f'{len(dashed)} dashed or dotted line{"s" if len(dashed) != 1 else ""} '
                     'arrive as their dashes — one marker each.')
    if any(item.get('weight', DEFAULT_WEIGHT) != DEFAULT_WEIGHT for item in shown
           if item['kind'] in ('line', 'area', 'shape')):
        notes.append('Line thickness stays on this map: Arma draws every line at one width.')
    if any(item['kind'] == 'area' and not item.get('ao') for item in shown):
        notes.append('Freehand areas arrive as outlines: Arma cannot fill a drawn shape. '
                     'Use a rectangle or an ellipse for a filled area.')
    if any(item['kind'] == 'shape' and item.get('label') and not item.get('ao') for item in shown):
        notes.append('Rectangle and ellipse labels stay on this map: Arma shows no text on '
                     'an area marker.')
    if (doc.get('mask') or {}).get('on'):
        notes.append('The grey mask stays on this map; the AO borders go across.')
    return {'markers': sqf.count('createMarker'), 'notes': notes}
