"""What an item is, and what a saved map may contain.

`parse()` is the gate: it runs on the way **out** of the database as well as
in, so nothing downstream has to trust the column. `ordered_items()` decides
what is drawn over what, and `catalog()` is the vocabulary the editor offers.

Part of `utils/tacmap`, split out of one 3,257-line module. The package's
`__init__` re-exports every name, so `from utils import tacmap` and
`tacmap.anything` work exactly as they did.
"""
import json
import math
import re
from xml.sax.saxutils import escape, quoteattr
from .base import (
    AFFILIATIONS,
    AO_COLOURS,
    ARMA_FONT,
    ARMA_POINTS,
    ARMA_TERRAINS,
    ARROWS,
    BACKDROP_ZOOM,
    BACKGROUND_KINDS,
    DEFAULT_EXTENT,
    DEFAULT_FILL,
    DEFAULT_HEIGHT,
    DEFAULT_INK,
    DEFAULT_LAYER,
    DEFAULT_MARKER,
    DEFAULT_MASK,
    DEFAULT_PLACES,
    DEFAULT_PLACE_KIND,
    DEFAULT_SHAPE,
    DEFAULT_SIDE,
    DEFAULT_SIZE,
    DEFAULT_SYMBOL,
    DEFAULT_TEXT_SCALE,
    DEFAULT_TILE_ZOOM,
    DEFAULT_WEIGHT,
    DEFAULT_WIDTH,
    ECHELONS,
    HATCH_GAP,
    KINDS,
    KIND_ORDER,
    LABEL_PLACES,
    LEGACY_MARKERS,
    LEGACY_SYMBOLS,
    LINE_COLOURS,
    LINE_STYLES,
    LINE_STYLE_LABELS,
    LINE_WEIGHTS,
    MARKERS,
    MASK_COLOUR,
    MAX_GLYPH,
    MAX_ITEMS,
    MAX_LABEL,
    MAX_LAYERS,
    MAX_LAYER_NAME,
    MAX_MASK_OPACITY,
    MAX_NOTE,
    MAX_POINTS,
    MAX_SIZE,
    MAX_TEXT_SCALE,
    MAX_TILE_ZOOM,
    MAX_WEIGHT,
    MIN_HIT,
    MIN_LABEL,
    MIN_LINE_SIZE,
    MIN_MASK_OPACITY,
    MIN_SHAPE,
    MIN_SIZE,
    MIN_TEXT_SCALE,
    MISSING_PICTURE,
    PLACE_GROUPS,
    PLACE_KINDS,
    PLACE_SIZE,
    POINT_BOX,
    POINT_LABEL_GAP,
    POINT_PRESETS,
    SHAPES,
    SHAPE_FILLS,
    SYMBOLS,
    UNIT_BOX,
    UNIT_LABEL_DROP,
    UNIT_LABEL_GAP,
    _CIVILIAN,
    _INK_DARK,
    _INK_LIGHT,
    line_size,
    picture_key,
    unit_class,
)


def blank_doc(width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT) -> dict:
    """An empty map — what a freshly created one opens with."""
    return {
        'width': width,
        'height': height,
        'background': {'kind': 'image', 'url': '', 'opacity': 1.0,
                       'zoom': DEFAULT_TILE_ZOOM, 'max_zoom': MAX_TILE_ZOOM,
                       'name': ''},
        'grid': {'show': False, 'cols': 10, 'rows': 10},
        # Not the names themselves — those belong to the terrain and are
        # passed to the renderer. This is only how much of them to draw.
        'places': dict(DEFAULT_PLACES),
        # Where the sheet's corners are in Arma's world, in metres. Only the
        # export reads it, and only the person exporting can know it — which
        # image of which terrain this is, and whether it is the whole map.
        'arma': dict(DEFAULT_EXTENT),
        'mask': dict(DEFAULT_MASK),
        'text_scale': DEFAULT_TEXT_SCALE,
        'layers': [dict(DEFAULT_LAYER)],
        'items': [],
    }


class ParseResult:
    """A document, and what was wrong with the thing it was parsed from.

    Mirrors `orbat.ParseResult`: errors mean the document is not saveable,
    warnings mean something was silently straightened out and the person should
    know. An item this version does not understand is a warning rather than an
    error — an older deployment reading a newer map should lose that item, not
    refuse the whole plan.
    """

    def __init__(self, doc: dict):
        self.doc = doc
        self.errors = []
        self.warnings = []

    @property
    def ok(self) -> bool:
        return not self.errors


def _number(value, fallback=None):
    """A finite float, or None. NaN and infinity are not coordinates."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if math.isnan(number) or math.isinf(number):
        return fallback
    return number


def _clamp(value, low, high):
    return low if value < low else (high if value > high else value)


# Control characters a document has no use for, and one of them it cannot
# survive: PostgreSQL's `jsonb` refuses `\u0000` outright ("unsupported Unicode
# escape sequence") where `text` and Python's `json` both accept it happily. A
# label is typed by anybody with an edit link, so without this a single NUL in
# one would fail every later save of that map — and `tac_maps.doc` is `jsonb`
# (migration 3), so it would have failed the conversion too. `utils/reddit.py`
# drops the characters XML forbids for the same class of reason.
#
# Newline and tab are kept: `_label_svg()` and `_sqf_string()` both handle a
# label with newlines in it, so they are expected rather than junk.
_CONTROLS = re.compile(r'[^\S\n\t]*[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+')


def _text(value, limit: int) -> str:
    if not isinstance(value, str):
        return ''
    return _CONTROLS.sub('', value or '').strip()[:limit]


_HEX_COLOUR = re.compile(r'^#[0-9a-fA-F]{6}$')
_SLUG = re.compile(r'[^a-z0-9_-]+')


def _colour(raw) -> str:
    """A colour somebody picked, or '' meaning "use the side's".

    Only `#rrggbb`: it goes into a `stroke` attribute, and a colour value is
    one of the places CSS lets a string be more than a colour.
    """
    value = (raw or '').strip() if isinstance(raw, str) else ''
    return value.lower() if _HEX_COLOUR.match(value) else ''


def _slug(raw) -> str:
    return _SLUG.sub('', (raw or '').strip().lower() if isinstance(raw, str) else '')[:24]


# A terrain this bot serves itself, from an uploaded archive. It is a path
# rather than an address so that moving the site — a new domain, a local run —
# does not leave every map pointing at the old one.
LOCAL_TILES = re.compile(r'^/t/\d+$')


def _safe_url(raw) -> str:
    """A background URL we are willing to put in an `href`.

    Only http(s) or one of our own terrain paths, because `javascript:` and
    `data:` in an image href are how an editable map becomes a way to run
    script in somebody else's session.
    """
    url = (raw or '').strip() if isinstance(raw, str) else ''
    if not url:
        return ''
    if LOCAL_TILES.match(url):
        return url
    return url if url.lower().startswith(('http://', 'https://')) else ''


def parse(raw) -> ParseResult:
    """Read a document from JSON text or a plain dict, and check every field.

    Nothing that comes out of here can be unsafe to render: the coordinates are
    finite and bounded, the strings are cut to length, the enumerations are
    known values, and the background URL is http(s) or empty.
    """
    if isinstance(raw, (str, bytes)):
        try:
            raw = json.loads(raw or '{}')
        except (ValueError, TypeError) as e:
            result = ParseResult(blank_doc())
            result.errors.append(f'That is not a map document ({e}).')
            return result
    if not isinstance(raw, dict):
        result = ParseResult(blank_doc())
        result.errors.append('That is not a map document.')
        return result

    width = int(_clamp(_number(raw.get('width'), DEFAULT_WIDTH), 100, 20000))
    height = int(_clamp(_number(raw.get('height'), DEFAULT_HEIGHT), 100, 20000))
    doc = blank_doc(width, height)
    result = ParseResult(doc)

    background = raw.get('background')
    if isinstance(background, dict):
        url = _safe_url(background.get('url'))
        if background.get('url') and not url:
            result.warnings.append(
                'The background has to be an http:// or https:// link — '
                'that one was dropped.'
            )
        kind = (background.get('kind') if background.get('kind') in BACKGROUND_KINDS
                else 'image')
        # What the tile set actually has, which bounds what may be asked for:
        # a zoom above it is a folder of 404s where the map should be.
        max_zoom = int(_clamp(_number(background.get('max_zoom'), MAX_TILE_ZOOM),
                              0, MAX_TILE_ZOOM))
        doc['background'] = {
            'kind': kind,
            'url': url.rstrip('/') if kind == 'tiles' else url,
            'opacity': round(_clamp(_number(background.get('opacity'), 1.0), 0.1, 1.0), 2),
            'zoom': int(_clamp(_number(background.get('zoom'), DEFAULT_TILE_ZOOM),
                               0, max_zoom)),
            'max_zoom': max_zoom,
            'name': _text(background.get('name'), 80),
        }

    grid = raw.get('grid')
    if isinstance(grid, dict):
        doc['grid'] = {
            'show': bool(grid.get('show')),
            'cols': int(_clamp(_number(grid.get('cols'), 10), 1, 100)),
            'rows': int(_clamp(_number(grid.get('rows'), 10), 1, 100)),
        }

    places = raw.get('places')
    if isinstance(places, dict):
        groups = places.get('groups')
        known = {key for key, _ in PLACE_GROUPS}
        doc['places'] = {
            'show': bool(places.get('show', True)),
            'groups': [key for key in groups if key in known]
            if isinstance(groups, list) else [],
            'scale': round(_clamp(_number(places.get('scale'), 1.0), 0.4, 3.0), 2),
        }

    arma = raw.get('arma')
    if isinstance(arma, dict):
        corners = {name: _number(arma.get(name), DEFAULT_EXTENT[name])
                   for name in ('left', 'bottom', 'right', 'top')}
        if corners['right'] <= corners['left'] or corners['top'] <= corners['bottom']:
            # A zero-width or inside-out sheet would divide by zero on export
            # and put every marker in the same spot; the default is at least a
            # map somebody can correct.
            result.warnings.append(
                'The Arma corners have to go left → right and bottom → top — '
                'they were put back to the default.'
            )
            corners = {name: DEFAULT_EXTENT[name]
                       for name in ('left', 'bottom', 'right', 'top')}
        doc['arma'] = {'terrain': _text(arma.get('terrain'), 60), **corners}

    mask = raw.get('mask')
    if isinstance(mask, dict):
        doc['mask'] = {
            'on': bool(mask.get('on')),
            'opacity': round(_clamp(_number(mask.get('opacity'), DEFAULT_MASK['opacity']),
                                    MIN_MASK_OPACITY, MAX_MASK_OPACITY), 2),
        }

    doc['text_scale'] = round(_clamp(_number(raw.get('text_scale'), DEFAULT_TEXT_SCALE),
                                     MIN_TEXT_SCALE, MAX_TEXT_SCALE), 2)

    doc['layers'] = _parse_layers(raw.get('layers'))

    items = raw.get('items')
    if items is None:
        items = []
    if not isinstance(items, list):
        result.errors.append('The items are not a list.')
        return result
    if len(items) > MAX_ITEMS:
        result.errors.append(
            f'{len(items)} items — a map holds {MAX_ITEMS} at most.'
        )
        return result

    for index, raw_item in enumerate(items, start=1):
        item = _parse_item(raw_item, doc, result, index)
        if item is not None:
            doc['items'].append(item)
    return result


def _parse_item(raw, doc: dict, result: ParseResult, index: int):
    if not isinstance(raw, dict):
        result.warnings.append(f'Item {index} is not an item and was dropped.')
        return None

    kind = raw.get('kind') if raw.get('kind') in KINDS else None
    if kind is None:
        result.warnings.append(
            f'Item {index} is a "{raw.get("kind")}", which this version does not '
            'know — it was dropped.'
        )
        return None

    side = raw.get('side') if raw.get('side') in AFFILIATIONS else DEFAULT_SIDE
    # An item on a layer this document does not have would be invisible and
    # unreachable, so it lands on the first one rather than nowhere.
    layers = {layer['id'] for layer in doc['layers']}
    layer = raw.get('layer') if raw.get('layer') in layers else doc['layers'][0]['id']
    item = {
        'kind': kind,
        'side': side,
        'layer': layer,
        'label': _text(raw.get('label'), MAX_LABEL),
        'note': _text(raw.get('note'), MAX_NOTE),
        'size': round(_clamp(_number(raw.get('size'), 1.0), MIN_SIZE, MAX_SIZE), 2),
    }

    if kind in ('unit', 'point', 'text', 'shape'):
        point = _parse_point(raw.get('x'), raw.get('y'), doc)
        if point is None:
            result.warnings.append(f'Item {index} has no position and was dropped.')
            return None
        item['x'], item['y'] = point

    if kind == 'unit':
        item['symbol'] = _unit_symbol(raw, side, result, index)
        item['rotation'] = round(_clamp(_number(raw.get('rotation'), 0.0), -360, 360), 1)
        echelon = raw.get('echelon')
        item['echelon'] = echelon if echelon in ECHELONS else ''
    elif kind == 'point':
        item['glyph'] = _text(raw.get('glyph'), MAX_GLYPH).upper()
        item['marker'] = _marker(raw.get('marker'), item['glyph'])
        # Arma turns any marker (setMarkerDir), so a marker here turns too.
        item['rotation'] = round(_clamp(_number(raw.get('rotation'), 0.0), -360, 360), 1)

    if kind in ('line', 'area', 'text', 'point', 'shape'):
        # The side says whose a symbol is and must keep saying it, but a line
        # is a route or a boundary — those are told apart by colour on every
        # paper map there has ever been. A task marker is the same kind of
        # thing: an objective is the plan's, not one side's.
        item['color'] = _colour(raw.get('color'))

    if kind in ('line', 'area'):
        points = _parse_points(raw.get('points'), doc)
        least = 2 if kind == 'line' else 3
        if len(points) < least:
            result.warnings.append(
                f'Item {index} is a {kind} with fewer than {least} points and was '
                'dropped.'
            )
            return None
        item['points'] = points
        item['style'] = raw.get('style') if raw.get('style') in LINE_STYLES else 'solid'
        if kind == 'line':
            item['arrow'] = _arrow(raw.get('arrow'))
            item['label_at'] = (raw.get('label_at') if raw.get('label_at')
                                in dict(LABEL_PLACES) else 'middle')

    if kind in ('line', 'area', 'shape'):
        item['weight'] = _weight(raw.get('weight'), item['size'])

    if kind in ('area', 'shape'):
        # An AO: clear in the mask, drawn as a border with no fill.
        item['ao'] = bool(raw.get('ao'))

    if kind == 'shape':
        item['shape'] = raw.get('shape') if raw.get('shape') in SHAPES else DEFAULT_SHAPE
        item['fill'] = raw.get('fill') if raw.get('fill') in SHAPE_FILLS else DEFAULT_FILL
        item['style'] = raw.get('style') if raw.get('style') in LINE_STYLES else 'solid'
        # Never bigger than half as much again as the sheet: a broken client
        # cannot cover the map in one shape nobody can find the edge of.
        item['w'] = round(_clamp(_number(raw.get('w'), 80.0), MIN_SHAPE,
                                 doc['width'] * 1.5), 2)
        item['h'] = round(_clamp(_number(raw.get('h'), 60.0), MIN_SHAPE,
                                 doc['height'] * 1.5), 2)
        item['rotation'] = round(_clamp(_number(raw.get('rotation'), 0.0), -360, 360), 1)
    return item


def _marker(raw, glyph: str = '') -> str:
    """Which marker a point is, by Arma class in lower case.

    An old map named the military ones without their `mil_` prefix, and one
    older still carried no marker at all — its glyph is then the only thing
    that says what the point was (`OBJ` is the objective)."""
    key = raw.lower() if isinstance(raw, str) else ''
    key = LEGACY_MARKERS.get(key, key)
    if key in MARKERS:
        return key
    return ARMA_POINTS.get(glyph, DEFAULT_MARKER)


def _unit_symbol(raw: dict, side: str, result=None, index: int = 0) -> str:
    """A unit's symbol in Arma's set, reading the APP-6 symbols an older map
    was drawn with as the marker each of them always exported as."""
    symbol = raw.get('symbol')
    if symbol in SYMBOLS and 'dimension' not in raw and not raw.get('hq'):
        return symbol
    if raw.get('hq'):
        return 'hq'
    old = symbol
    symbol = LEGACY_SYMBOLS.get(symbol, symbol)
    if raw.get('dimension') == 'air' and old not in ('air', 'heli', 'uav'):
        symbol = 'heli'
    if symbol not in SYMBOLS:
        if old and result is not None:
            result.warnings.append(
                f'Item {index} uses the unknown symbol "{old}" — drawn as unknown.')
        return 'unknown' if old else DEFAULT_SYMBOL
    if side == 'civ' and symbol != 'installation':
        # A civilian has only its own five markers; name the one it is.
        symbol = {'car': 'car', 'ship': 'ship', 'air': 'heli',
                  'plane': 'plane'}.get(_CIVILIAN.get(symbol, 'unknown'), 'unknown')
    return symbol


def _arrow(raw) -> str:
    """Where a line's arrow heads go. A line saved before there was a choice
    stored a plain true or false, which meant one at the end."""
    if raw is True:
        return 'end'
    return raw if raw in dict(ARROWS) else ''


def _weight(raw, size: float) -> int:
    """A line's thickness in screen pixels. A line from before there was one
    was as thick as its size made it, so it keeps about that."""
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and math.isfinite(raw):
        return int(_clamp(round(raw), 1, MAX_WEIGHT))
    return int(_clamp(round(4 * line_size(size)), 1, LINE_WEIGHTS[-1]))


def _parse_layers(raw) -> list:
    """The document's layers, always at least one.

    A layer is only a name and a switch — items point at it by id. Nothing
    hangs off a layer, so a document that arrives without them, or with
    nonsense in them, gets the default one rather than an error: losing the
    plan because its layer list is broken would be the worse failure.
    """
    if not isinstance(raw, list):
        return [dict(DEFAULT_LAYER)]
    layers, seen = [], set()
    for entry in raw[:MAX_LAYERS]:
        if not isinstance(entry, dict):
            continue
        key = _slug(entry.get('id'))
        if not key or key in seen:
            continue
        seen.add(key)
        layer = {
            'id': key,
            'name': _text(entry.get('name'), MAX_LAYER_NAME) or key,
            'visible': bool(entry.get('visible', True)),
        }
        # Locked: drawn, but nothing on it can be picked up in the editor —
        # the terrain overlay or the enemy picture nobody should nudge while
        # drawing the plan over it. Only written when set.
        if entry.get('locked'):
            layer['locked'] = True
        layers.append(layer)
    return layers or [dict(DEFAULT_LAYER)]


def ordered_items(doc: dict, *, include_hidden: bool = False) -> list:
    """Every item that should be drawn, in the order it should be drawn.

    Layer by layer, and inside a layer by kind — areas, then lines, then
    labels, then markers, then symbols. Leaving the order to whatever was
    added last is what buried a platoon under a boundary somebody drew after
    it; within one kind the document's own order still decides, which is what
    **Bring to front** moves.
    """
    layers = doc.get('layers') or [dict(DEFAULT_LAYER)]
    rank = {layer['id']: index for index, layer in enumerate(layers)}
    hidden = {layer['id'] for layer in layers if not layer.get('visible', True)}
    items = [item for item in (doc.get('items') or [])
             if include_hidden or item.get('layer') not in hidden]
    return sorted(items, key=lambda item: (rank.get(item.get('layer'), 0),
                                           KIND_ORDER.get(item['kind'], 0)))


def _parse_point(raw_x, raw_y, doc: dict):
    x, y = _number(raw_x), _number(raw_y)
    if x is None or y is None:
        return None
    # A quarter of the sheet outside it: far enough that dragging something off
    # the edge keeps it, near enough that a broken client cannot put an item
    # a million units away and make the whole map unreadable.
    return (round(_clamp(x, -doc['width'] / 4, doc['width'] * 1.25), 2),
            round(_clamp(y, -doc['height'] / 4, doc['height'] * 1.25), 2))


def _parse_points(raw, doc: dict) -> list:
    if not isinstance(raw, list):
        return []
    points = []
    for pair in raw[:MAX_POINTS]:
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            point = _parse_point(pair[0], pair[1], doc)
        elif isinstance(pair, dict):
            point = _parse_point(pair.get('x'), pair.get('y'), doc)
        else:
            point = None
        if point is not None:
            points.append(list(point))
    return points


def dumps(doc: dict) -> str:
    """The document as it is stored — compact, and with the keys in one order."""
    return json.dumps(doc, separators=(',', ':'), sort_keys=True)


def json_payload(value) -> str:
    """JSON safe to drop inside a `<script>` element.

    The content of a script tag is raw text, so a label containing `</script>`
    would otherwise end the element and everything after it would be markup —
    which, with labels being text anybody with the share link can type, is the
    one way this page could be made to run somebody else's script. `<`, `>` and
    `&` go out as escapes, which JSON readers accept and a parser never ends a
    tag on.
    """
    return (json.dumps(value, separators=(',', ':'))
            .replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026'))


def summarise(doc: dict) -> str:
    """"12 units · 3 lines · 1 area" — the list page and the Discord post."""
    counts = {}
    for item in doc.get('items') or []:
        counts[item['kind']] = counts.get(item['kind'], 0) + 1
    names = {'unit': 'unit', 'point': 'marker', 'line': 'line', 'area': 'area',
             'shape': 'shape', 'text': 'label'}
    parts = [
        f"{counts[kind]} {names[kind]}{'' if counts[kind] == 1 else 's'}"
        for kind in KINDS if counts.get(kind)
    ]
    return ' · '.join(parts) if parts else 'empty'


def catalog() -> dict:
    """What the browser editor is allowed to draw, as one JSON-able structure.

    The palette, the colours and the limits all come from here rather than being
    written out again in the JavaScript, so the two sides cannot end up offering
    different symbols or disagreeing about what a valid map is.
    """
    return {
        'sides': [
            {'key': key, 'label': value['label'], 'fill': value['fill']}
            for key, value in AFFILIATIONS.items()
        ],
        # Each symbol with the marker it is on every side, so the editor can
        # offer only those this server has a picture for.
        'symbols': [
            {'key': key, 'label': value['label'], 'civ': bool(value.get('civ')),
             'classes': {side: picture_key(unit_class(side, key)) for side in AFFILIATIONS}}
            for key, value in SYMBOLS.items()
        ],
        'echelons': [
            {'key': key, 'label': value['label'], 'picture': picture_key(value['arma'])}
            for key, value in ECHELONS.items()
        ],
        'markers': [
            {'key': key, 'label': value['label'], 'group': value['group'],
             'paint': value['paint']}
            for key, value in MARKERS.items()
        ],
        'points': [{'glyph': glyph, 'label': label} for glyph, label in POINT_PRESETS],
        'terrains': [{'name': name, 'size': size} for name, size in ARMA_TERRAINS],
        'lineStyles': list(LINE_STYLES),
        'lineStyleLabels': dict(LINE_STYLE_LABELS),
        'lineWeights': list(LINE_WEIGHTS),
        'defaultWeight': DEFAULT_WEIGHT,
        'maxWeight': MAX_WEIGHT,
        'arrows': [{'key': key, 'label': label} for key, label in ARROWS],
        'labelPlaces': [{'key': key, 'label': label} for key, label in LABEL_PLACES],
        'shapes': [{'key': key, 'label': value['label']} for key, value in SHAPES.items()],
        'fills': [{'key': key, 'label': value['label']}
                  for key, value in SHAPE_FILLS.items()],
        'defaultFill': DEFAULT_FILL,
        'minShape': MIN_SHAPE,
        'maskColour': MASK_COLOUR,
        'aoColours': list(AO_COLOURS),
        'hatchGap': HATCH_GAP,
        'lineColours': [{'value': value, 'label': label}
                        for value, label in LINE_COLOURS],
        'defaultInk': DEFAULT_INK,
        'inkLight': _INK_LIGHT,
        'inkDark': _INK_DARK,
        'unitBox': UNIT_BOX,
        'pointBox': POINT_BOX,
        'backdropZoom': BACKDROP_ZOOM,
        'maxTileZoom': MAX_TILE_ZOOM,
        'limits': {
            'items': MAX_ITEMS, 'points': MAX_POINTS, 'label': MAX_LABEL,
            'note': MAX_NOTE, 'glyph': MAX_GLYPH,
            'minSize': MIN_SIZE, 'maxSize': MAX_SIZE,
            'layers': MAX_LAYERS, 'layerName': MAX_LAYER_NAME,
        },
        'kindOrder': dict(KIND_ORDER),
        # The place-name tables, so the browser draws a village the same size
        # this module does rather than keeping its own copy of the ranks.
        'placeKinds': {key: {'group': value['group'], 'rank': value['rank']}
                       for key, value in PLACE_KINDS.items()},
        'placeSizes': {str(rank): size for rank, size in PLACE_SIZE.items()},
        'placeGroups': [{'key': key, 'label': label} for key, label in PLACE_GROUPS],
        'defaultPlaceKind': DEFAULT_PLACE_KIND,
        'minLabel': MIN_LABEL,
        'minLineSize': MIN_LINE_SIZE,
        'minHit': MIN_HIT,
        'defaultSize': DEFAULT_SIZE,
        'defaultMarker': DEFAULT_MARKER,
        'armaFont': ARMA_FONT,
        'textScale': {'min': MIN_TEXT_SCALE, 'max': MAX_TEXT_SCALE,
                      'default': DEFAULT_TEXT_SCALE},
        'unitLabelGap': UNIT_LABEL_GAP,
        'pointLabelGap': POINT_LABEL_GAP,
        'unitLabelDrop': UNIT_LABEL_DROP,
        'missingPicture': MISSING_PICTURE,
    }
