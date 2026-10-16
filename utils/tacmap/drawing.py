"""The map as an SVG.

Server-rendered in full, so a map reads with no JavaScript at all and the
editor has real markup to take over rather than an empty box. Every unit and
marker is drawn with Arma's own picture, tinted the way the game tints it.

Part of `utils/tacmap`, split out of one 3,257-line module. The package's
`__init__` re-exports every name, so `from utils import tacmap` and
`tacmap.anything` work exactly as they did.
"""
import hashlib
import math
import re
from xml.sax.saxutils import escape, quoteattr
from .base import (
    ARMA_FONT,
    BACKDROP_ZOOM,
    DEFAULT_INK,
    DEFAULT_MASK,
    DEFAULT_SIDE,
    DEFAULT_TEXT_SCALE,
    DEFAULT_WEIGHT,
    ECHELONS,
    HATCH_GAP,
    MARKERS,
    MASK_COLOUR,
    MIN_LABEL,
    MISSING_PICTURE,
    POINT_BOX,
    POINT_LABEL_GAP,
    UNIT_BOX,
    UNIT_LABEL_GAP,
    _INK_DARK,
    _INK_LIGHT,
    _rgb,
    is_ao,
    item_colour,
    line_size,
    marker_key,
    picture_key,
    unit_class,
)
from .doc import (ordered_items)
from .places import (places_svg)


def _attrs(**values) -> str:
    return ''.join(
        f' {name.replace("_", "-")}={quoteattr(str(value))}'
        for name, value in values.items() if value is not None and value != ''
    )


def icon_mask_id(key: str) -> str:
    return f'tmk-{key}'


def _paints(key: str) -> bool:
    return bool(MARKERS.get(key, {}).get('paint'))


def icon_masks(images: dict | None) -> str:
    """The game's own pictures this guild uploaded, once each, for a `<use>`.

    `images` is key → href: a URL on a page, a data URI in an ops trace,
    which resvg draws without fetching anything."""
    parts = []
    for key, href in sorted((images or {}).items()):
        if not re.fullmatch(r'[a-z0-9_]+', key):
            continue
        parts.append(f'<image id="{icon_mask_id(key)}" href={quoteattr(href)} x="0" y="0" '
                     'width="100" height="100" preserveAspectRatio="xMidYMid meet"/>')
    return ''.join(parts)


def tint_id(colour: str) -> str:
    return 'tmt-' + (colour or '').lstrip('#').lower()


def tint_filter(colour: str) -> str:
    """Arma tints a marker picture by multiplying it with the marker colour:
    white becomes the colour, black stays black, the transparency is the
    picture's. A colour matrix is exactly that multiplication."""
    rgb = _rgb(colour) or (255, 255, 255)
    r, g, b = (round(v / 255, 4) for v in rgb)
    return (f'<filter id="{tint_id(colour)}" color-interpolation-filters="sRGB">'
            f'<feColorMatrix type="matrix" values="{r} 0 0 0 0 0 {g} 0 0 0 '
            f'0 0 {b} 0 0 0 0 0 1 0"/></filter>')


def defs(images: dict | None = None, tints=()) -> str:
    """The pictures and the colours they are tinted in, once each, for both
    renderers to place."""
    filters = ''.join(tint_filter(colour) for colour in sorted(set(tints))
                      if _rgb(colour))
    return f'<defs>{icon_masks(images)}{filters}</defs>'


def _picture_svg(key: str, colour: str, icons) -> str:
    """One of the game's pictures in the item's 100 × 100 box — tinted, or as
    it is for a flag — or a placeholder when this server has none for it."""
    if key in icons:
        if _paints(key):
            return f'<use href="#{icon_mask_id(key)}"/>'
        return f'<use href="#{icon_mask_id(key)}" filter="url(#{tint_id(colour)})"/>'
    return MISSING_PICTURE


def _tints(items) -> set:
    """The colours the pictured items on a map are tinted in."""
    return {item_colour(item) for item in items if item['kind'] in ('unit', 'point')}


def label_size(size: float) -> float:
    """How big a label is drawn at that item size — never below MIN_LABEL.

    A symbol shrunk to nothing is still a symbol; its name shrunk to nothing is
    a smudge, and the name is what the plan is read for.
    """
    return round(max(MIN_LABEL, 14 * size), 1)


def _label_svg(text: str, x: float, y: float, size: float, anchor: str = 'middle',
               fill: str = '', ts: float = 1.0) -> str:
    """A name, written the way Arma writes marker text: a narrow face in the
    marker's own colour with a thin shadow in the opposite tone, rather than
    white over a heavy halo. `ts` is the map's text scale."""
    if not text:
        return ''
    font = round(label_size(size) * ts, 1)
    colour = fill or DEFAULT_INK
    return (
        f'<text{_attrs(x=round(x, 2), y=round(y, 2), text_anchor=anchor)} '
        f'class="tm-label" font-size="{font}" font-family="{ARMA_FONT}" '
        f'font-weight="600" fill="{colour}" stroke="{ink_backing(colour)}" '
        f'stroke-opacity="0.6" stroke-width="{round(font * 0.1, 2)}" '
        f'paint-order="stroke" stroke-linejoin="round">{escape(text)}</text>'
    )


def ink_backing(colour: str) -> str:
    """The pass drawn under `colour` so it holds up over any terrain."""
    rgb = _rgb(colour)
    if rgb is None:
        return _INK_LIGHT
    # Rec. 601 luma, which is close enough to decide light from dark.
    luma = (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255
    return _INK_DARK if luma > 0.5 else _INK_LIGHT


def item_svg(item: dict, px: float = 1.0, icons=frozenset(), ts: float = 1.0) -> str:
    """One item, drawn. The browser composes the same three or four elements.
    `px` is sheet units per pixel of the picture: 1 on a page, where line
    widths are screen pixels, and the crop over the picture's width for an
    ops trace."""
    kind = item['kind']
    if kind == 'unit':
        return _unit_svg(item, icons, ts)
    if kind == 'point':
        return _point_svg(item, icons, ts)
    if kind == 'text':
        return _text_svg(item, ts)
    if kind == 'shape':
        return _figure_svg(item, px, ts)
    return _shape_svg(item, px, ts)


def ao_corner(item: dict) -> tuple:
    """Where an AO's name tab hangs: the corner nearest the sheet's top left
    (smallest x + y) — of the box for a shape, of the outline for an area.
    Two AOs that overlap have different top-left corners, so their names stop
    landing on one another in the ground they share.

    An ellipse has no corner, and the corner of its box is off the ellipse
    altogether, so it is the point on the ellipse itself furthest towards the
    top left: the one where x + y is smallest."""
    if item['kind'] == 'shape' and item['shape'] == 'ellipse':
        a, b = item['w'] / 2, item['h'] / 2
        turn = math.radians(item.get('rotation') or 0)
        cos, sin = math.cos(turn), math.sin(turn)
        # The direction (1, 1) seen from inside the ellipse's own frame …
        dx, dy = cos + sin, cos - sin
        reach = math.sqrt(a * a * dx * dx + b * b * dy * dy) or 1
        # … and the point on x²/a² + y²/b² = 1 furthest against it, turned back.
        px, py = -a * a * dx / reach, -b * b * dy / reach
        return (round(item['x'] + px * cos - py * sin, 2),
                round(item['y'] + px * sin + py * cos, 2))
    points = shape_corners(item) if item['kind'] == 'shape' else item['points']
    x, y = min(points, key=lambda p: (p[0] + p[1], p[1]))
    return round(x, 2), round(y, 2)


def ao_tab(item: dict, ts: float = 1.0) -> dict | None:
    """The name tab's box and text position, or None without a name. Its width
    is estimated from the character count rather than measured, so the browser
    and this module — which cannot measure text — draw the same box."""
    text = item.get('label') or ''
    if not text:
        return None
    font = round(label_size(item['size']) * ts, 1)
    pad = round(font * 0.4, 2)
    x, y = ao_corner(item)
    w = round(len(text) * font * 0.6 + 2 * pad, 2)
    h = round(font * 1.45, 2)
    return {'x': x, 'y': y, 'w': w, 'h': h, 'font': font,
            'tx': round(x + pad, 2), 'ty': round(y + h * 0.72, 2)}


def _ao_tab_svg(item: dict, ts: float = 1.0) -> str:
    """An AO's name in a small tab filled with its border colour, hung inside
    its top-left corner: the tab ties the name to the border it belongs to.
    The text takes the backing colour, so it reads on a yellow tab and a
    blue one alike."""
    tab = ao_tab(item, ts)
    if tab is None:
        return ''
    colour = item_colour(item)
    contrast = ink_backing(colour)
    return (
        f'<g class="tm-aotab"><rect x="{tab["x"]}" y="{tab["y"]}" width="{tab["w"]}" '
        f'height="{tab["h"]}" rx="{round(tab["font"] * 0.2, 2)}" fill="{colour}" '
        f'stroke="{contrast}" stroke-width="{round(tab["font"] * 0.12, 2)}"/>'
        f'<text x="{tab["tx"]}" y="{tab["ty"]}" text-anchor="start" '
        f'font-size="{tab["font"]}" font-weight="700" font-family="{ARMA_FONT}" '
        f'fill="{contrast}">{escape(item["label"])}</text></g>'
    )


def _hole_svg(item: dict) -> str:
    """An AO's outline as a hole in the mask: filled black in the mask, so
    whatever is under it shows through. Overlapping AOs merge, because a mask
    adds holes rather than toggling them the way an even-odd path would."""
    if item['kind'] == 'shape':
        a, b = round(item['w'] / 2, 2), round(item['h'] / 2, 2)
        inner = (f'<ellipse cx="0" cy="0" rx="{a}" ry="{b}"/>' if item['shape'] == 'ellipse'
                 else f'<rect x="{-a}" y="{-b}" width="{round(2 * a, 2)}" '
                      f'height="{round(2 * b, 2)}"/>')
        return (f'<g transform="translate({item["x"]} {item["y"]}) '
                f'rotate({item.get("rotation") or 0})">{inner}</g>')
    points = ' '.join(f'{x},{y}' for x, y in item['points'])
    return f'<polygon points="{points}"/>'


def mask_svg(doc: dict) -> str:
    """The grey cover with every visible AO cut out of it, or nothing.

    Drawn over the terrain and under the plan, so symbols stay readable over
    the grey. The mask id is taken from what is in it, so two maps on one page
    — or a stale one in the browser — cannot borrow each other's holes."""
    mask = doc.get('mask') or DEFAULT_MASK
    if not mask.get('on'):
        return ''
    holes = ''.join(_hole_svg(item) for item in ordered_items(doc) if is_ao(item))
    pad = max(doc['width'], doc['height'])
    box = (f'x="{-pad}" y="{-pad}" width="{doc["width"] + 2 * pad}" '
           f'height="{doc["height"] + 2 * pad}"')
    key = 'tm-aomask-' + hashlib.sha1((holes + str(mask)).encode()).hexdigest()[:10]
    return (f'<defs><mask id="{key}" maskUnits="userSpaceOnUse" {box}>'
            f'<rect {box} fill="#fff"/><g fill="#000">{holes}</g></mask></defs>'
            f'<rect {box} fill="{MASK_COLOUR}" fill-opacity="{mask["opacity"]}" '
            f'mask="url(#{key})" pointer-events="none"/>')


def _unit_svg(item: dict, icons=frozenset(), ts: float = 1.0) -> str:
    """One unit: Arma's NATO marker in its side's colour, Arma's size marker
    over it when it has one, and its name underneath."""
    colour = item_colour(item)
    scale = UNIT_BOX * item['size'] / 100
    # Drawn in its own 100 × 100 box and moved onto the map, so a rotation
    # turns the symbol about its own centre rather than the sheet's.
    transform = (f"translate({round(item['x'], 2)},{round(item['y'], 2)}) "
                 f"rotate({item.get('rotation', 0)}) scale({round(scale, 4)}) "
                 f"translate(-50,-50)")
    parts = [_picture_svg(unit_picture(item), colour, icons)]
    echelon = ECHELONS.get(item.get('echelon'))
    if echelon:
        size_key = picture_key(echelon['arma'])
        if size_key in icons:
            # Arma's size pictures are drawn to sit over the unit marker in
            # the same box, which is how a mission maker stacks the two.
            parts.append(_picture_svg(size_key, colour, icons))
    # Beside the marker, as Arma writes it, in the marker's colour.
    font = label_size(item['size']) * ts
    label = _label_svg(item['label'], item['x'] + UNIT_BOX * item['size'] * UNIT_LABEL_GAP,
                       item['y'] + font * 0.35, item['size'], 'start', colour, ts)
    return f'<g transform="{transform}">{"".join(parts)}</g>{label}'


def unit_picture(item: dict) -> str:
    """The picture key of the Arma marker a unit is drawn as."""
    return picture_key(unit_class(item.get('side', DEFAULT_SIDE), item.get('symbol')))


def _point_svg(item: dict, icons=frozenset(), ts: float = 1.0) -> str:
    """One marker: the game's picture, in the marker's colour."""
    ink = item_colour(item)
    scale = POINT_BOX * item['size'] / 100 * 1.55
    transform = (f"translate({round(item['x'], 2)},{round(item['y'], 2)}) "
                 f"rotate({item.get('rotation', 0)}) "
                 f"scale({round(scale, 4)}) translate(-50,-50)")
    shape = f'<g transform="{transform}">{_picture_svg(marker_key(item), ink, icons)}</g>'
    # Arma writes a marker's text beside it, in the marker's colour; the
    # short text goes in front of the name.
    label = f"{item.get('glyph', '')} {item['label']}".strip()
    font = label_size(item['size']) * ts
    return shape + _label_svg(label, item['x'] + POINT_BOX * item['size'] * POINT_LABEL_GAP,
                              item['y'] + font * 0.35, item['size'], 'start', ink, ts)


def _text_svg(item: dict, ts: float = 1.0) -> str:
    return _label_svg(item['label'] or ' ', item['x'], item['y'], item['size'],
                      fill=item_colour(item), ts=ts)


def _centroid(points: list) -> tuple:
    return (sum(point[0] for point in points) / len(points),
            sum(point[1] for point in points) / len(points))


def arrow_head(points: list, weight: int = DEFAULT_WEIGHT, px: float = 1.0,
               start: bool = False) -> list:
    """The three corners of the head on the end of a line (or its start), or [].

    Sized from the line's thickness rather than the map's zoom-dependent
    units, so a 1px line gets a fine head and a thick one a bold head. `px` is
    sheet units per pixel — 1 on a page, more for an ops trace drawn into a
    briefing pack.

    Drawn as a polygon rather than an SVG `marker`, because a marker cannot
    take its colour from the line it sits on — `context-stroke` is not
    everywhere — and a line with a colour of its own would have kept the
    side's arrow.
    """
    (x1, y1), (x2, y2) = (points[1], points[0]) if start else (points[-2], points[-1])
    length = math.hypot(x2 - x1, y2 - y1)
    if not length:
        return []
    along = ((x2 - x1) / length, (y2 - y1) / length)
    back, wide, tip = (8 + 3 * weight) * px, (3 + 1.5 * weight) * px, weight * px / 2
    return [
        (round(x2 + along[0] * tip, 2), round(y2 + along[1] * tip, 2)),
        (round(x2 - along[0] * back - along[1] * wide, 2),
         round(y2 - along[1] * back + along[0] * wide, 2)),
        (round(x2 - along[0] * back + along[1] * wide, 2),
         round(y2 - along[1] * back - along[0] * wide, 2)),
    ]


def dash_pattern(style: str, weight: int) -> tuple | None:
    """A dashed or dotted line's on/off lengths, in screen pixels. A dot is a
    dash with no length and a round cap, so it is as wide as the line."""
    if style == 'dashed':
        return (3 * weight + 6, 2 * weight + 4)
    if style == 'dotted':
        return (0.01, 2 * weight + 3)
    return None


def _stroke(colour: str, weight: int, style: str, px: float) -> str:
    """The stroke of a line, an area's edge or a shape's outline.

    `vector-effect: non-scaling-stroke` keeps it the same number of screen
    pixels at every zoom, as Arma's map lines are. A renderer that ignores
    that (resvg, which draws the ops traces) reads the width in sheet units,
    which is why `px` — sheet units per pixel of the picture — scales it: the
    trace comes out with the line as thick as it is on screen."""
    attrs = (f'stroke="{colour}" stroke-width="{round(weight * px, 3):g}" '
             'vector-effect="non-scaling-stroke" stroke-linecap="round" '
             'stroke-linejoin="round"')
    dash = dash_pattern(style, weight)
    if dash:
        attrs += f' stroke-dasharray="{round(dash[0] * px, 3):g} {round(dash[1] * px, 3):g}"'
    return attrs


def _shape_svg(item: dict, px: float = 1.0, ts: float = 1.0) -> str:
    # No backing pass any more: a line is drawn once, in its own colour, as
    # thin as asked. The halo under it made the thinnest line three times as
    # wide as it said, which is the "outline" lines should not have.
    colour = item_colour(item)
    path = ' '.join(f'{round(x, 2)},{round(y, 2)}' for x, y in item['points'])
    stroke = _stroke(colour, item.get('weight', DEFAULT_WEIGHT), item['style'], px)
    if item['kind'] == 'area':
        fill = 'fill="none"' if item.get('ao') else f'fill="{colour}" fill-opacity="0.22"'
        shape = f'<polygon points="{path}" {fill} {stroke}/>'
        if item.get('ao'):
            return shape + _ao_tab_svg(item, ts)
        centre = _centroid(item['points'])
        return shape + _label_svg(item['label'], centre[0], centre[1], item['size'],
                                  fill=colour, ts=ts)

    parts = [f'<polyline points="{path}" fill="none" {stroke}/>']
    weight = item.get('weight', DEFAULT_WEIGHT)
    ends = {'end': (False,), 'start': (True,), 'both': (True, False)}.get(item.get('arrow'), ())
    for at_start in ends:
        head = arrow_head(item['points'], weight, px, start=at_start)
        if head:
            corners = ' '.join(f'{x},{y}' for x, y in head)
            parts.append(f'<polygon points="{corners}" fill="{colour}"/>')
    for x, y, anchor in line_label_spots(item, ts):
        parts.append(_label_svg(item['label'], x, y, item['size'], anchor=anchor,
                                fill=colour, ts=ts))
    return ''.join(parts)


def line_label_spots(item: dict, ts: float = 1.0) -> list:
    """Where a line's name is written, as (x, y, text-anchor) on the sheet:
    above its middle corner, or just beyond an end, reading away from the
    line — a phase line's name sits off both ends. The editor mirrors this."""
    if not item.get('label'):
        return []
    points = item['points']
    place = item.get('label_at') or 'middle'
    if place == 'middle':
        middle = points[len(points) // 2]
        return [(middle[0], middle[1] - 10 * item['size'], 'middle')]
    font = label_size(item['size']) * ts
    gap = font * 0.6
    spots = []
    ends = {'ends': ('start', 'end'), 'end': ('end',), 'start': ('start',)}[place]
    for end in ends:
        tip, back = (points[0], points[1]) if end == 'start' else (points[-1], points[-2])
        dx, dy = tip[0] - back[0], tip[1] - back[1]
        length = math.hypot(dx, dy) or 1
        x = tip[0] + dx / length * gap
        y = tip[1] + dy / length * gap + font * 0.35
        anchor = 'start' if dx > abs(dy) * 0.3 else 'end' if -dx > abs(dy) * 0.3 else 'middle'
        if anchor == 'middle':
            y += font * (0.6 if dy > 0 else -0.6)
        spots.append((round(x, 2), round(y, 2), anchor))
    return spots


def hatch_segments(shape: str, w: float, h: float, gap: float) -> list:
    """The hatch lines of a shape, in its own unrotated frame centred on 0,0.

    Worked out as geometry rather than with an SVG `<pattern>`: a pattern
    cannot take the colour of the shape it fills, and a clip path needs an id
    per item, which the browser and this module would have to agree on. These
    are plain segments, cut to the rectangle or the ellipse, so the two
    renderers draw exactly the same lines. Diagonal, at 45°.
    """
    a, b = w / 2, h / 2
    if a <= 0 or b <= 0 or gap <= 0:
        return []
    segments = []
    reach = a + b
    # Lines y = x + c, from one corner of the bounding box to the other,
    # spaced `gap` apart measured across the lines rather than along an axis.
    step = gap * math.sqrt(2)
    count = int(reach // step)
    for k in range(-count, count + 1):
        c = k * step
        if shape == 'ellipse':
            # x²/a² + (x + c)²/b² = 1
            qa = 1 / (a * a) + 1 / (b * b)
            qb = 2 * c / (b * b)
            qc = c * c / (b * b) - 1
            disc = qb * qb - 4 * qa * qc
            if disc <= 0:
                continue
            root = math.sqrt(disc)
            x1, x2 = (-qb - root) / (2 * qa), (-qb + root) / (2 * qa)
        else:
            x1, x2 = max(-a, -b - c), min(a, b - c)
            if x2 <= x1:
                continue
        segments.append((round(x1, 2), round(x1 + c, 2), round(x2, 2), round(x2 + c, 2)))
    return segments


def shape_corners(item: dict) -> list:
    """The four corners of a shape's box, turned by its rotation, on the
    sheet. An ellipse fits inside them — what anything needing its extent
    (the ops trace's crop) should use."""
    a, b = item['w'] / 2, item['h'] / 2
    turn = math.radians(item.get('rotation') or 0)
    cos, sin = math.cos(turn), math.sin(turn)
    return [(round(item['x'] + dx * cos - dy * sin, 2),
             round(item['y'] + dx * sin + dy * cos, 2))
            for dx, dy in ((-a, -b), (a, -b), (a, b), (-a, b))]


def _figure_svg(item: dict, px: float = 1.0, ts: float = 1.0) -> str:
    """A rectangle or an ellipse: drawn in its own frame, centred on 0,0, and
    turned into place — so the hatching and the outline turn with it. Drawn
    once, in its own colour, with no backing pass, the same as a line."""
    colour = item_colour(item)
    drawn = line_size(item['size'])
    weight = item.get('weight', DEFAULT_WEIGHT)
    stroke = _stroke(colour, weight, item['style'], px)
    a, b = round(item['w'] / 2, 2), round(item['h'] / 2, 2)
    ao = bool(item.get('ao'))
    if ao:
        item = {**item, 'fill': 'none'}     # an AO is the clear ground itself
    if item['shape'] == 'ellipse':
        outline = f'<ellipse cx="0" cy="0" rx="{a}" ry="{b}"'
    else:
        outline = f'<rect x="{-a}" y="{-b}" width="{round(2 * a, 2)}" height="{round(2 * b, 2)}"'
    fill = (f'fill="{colour}" fill-opacity="0.22"' if item['fill'] == 'light'
            else 'fill="none"')
    parts = []
    if item['fill'] == 'hatch':
        lines = ''.join(f'M{x1},{y1}L{x2},{y2}' for x1, y1, x2, y2
                        in hatch_segments(item['shape'], 2 * a, 2 * b, HATCH_GAP * drawn))
        if lines:
            hatch = max(1, weight / 2)
            parts.append(f'<path d="{lines}" stroke="{colour}" '
                         f'stroke-width="{round(hatch * px, 3):g}" '
                         'vector-effect="non-scaling-stroke"/>')
    parts.append(f'{outline} {fill} {stroke}/>')
    turn = item.get('rotation') or 0
    return (f'<g transform="translate({item["x"]} {item["y"]}) rotate({turn})">'
            + ''.join(parts) + '</g>'
            + (_ao_tab_svg(item, ts) if ao
               else _label_svg(item['label'], item['x'], item['y'], item['size'],
                               fill=colour, ts=ts)))


def tile_url(background: dict, zoom: int, column: int, row: int) -> str:
    """One tile of the pyramid. Row counts from the top — see BACKDROP_ZOOM above."""
    return f"{background['url']}/{zoom}/{column}/{row}.png"


def _empty_sheet(doc: dict) -> str:
    return (f'<rect x="0" y="0" width="{doc["width"]}" height="{doc["height"]}" '
            f'fill="#20262e"/>')


def _tiles_svg(doc: dict, *, zoom: int = None, view: tuple = None, href=None) -> str:
    """The tile pyramid as `<image>` elements.

    `zoom` overrides the document's level, `view` (x, y, w, h in sheet units)
    leaves out the tiles that fall wholly outside it, and `href(z, x, y)`
    replaces the tile's address — the ops traces in a briefing pack pass the
    tile's bytes in as a data URI, because the renderer that turns them into a
    picture fetches nothing.
    """
    background = doc['background']
    zoom = background['zoom'] if zoom is None else zoom
    address = href or (lambda z, column, row: tile_url(background, z, column, row))
    per_side = 2 ** zoom
    width = doc['width'] / per_side
    height = doc['height'] / per_side
    parts = []
    if zoom > BACKDROP_ZOOM:
        parts.append(
            f'<image href='
            f'{quoteattr(address(BACKDROP_ZOOM, 0, 0))} '
            f'x="0" y="0" width="{doc["width"]}" height="{doc["height"]}" '
            f'preserveAspectRatio="none"/>'
        )
    for column, row in visible_tiles(doc, zoom, view):
        parts.append(
            f'<image href={quoteattr(address(zoom, column, row))} '
            f'x="{round(column * width, 3)}" y="{round(row * height, 3)}" '
            f'width="{round(width, 3)}" height="{round(height, 3)}" '
            f'preserveAspectRatio="none"/>'
        )
    # The dark sheet stays under both: a tile that 404s draws nothing at all in
    # SVG, and a terrain whose backdrop is missing too should read as terrain
    # we have not got rather than as an empty plan.
    return (f'{_empty_sheet(doc)}<g opacity="{background["opacity"]}">'
            f'{"".join(parts)}</g>')


def visible_tiles(doc: dict, zoom: int, view: tuple = None) -> list:
    """(column, row) of every tile at `zoom` that touches `view` — all of them
    with no view. Row counts from the top, as everywhere here."""
    per_side = 2 ** zoom
    if view is None:
        return [(c, r) for c in range(per_side) for r in range(per_side)]
    x, y, w, h = view
    tile_w, tile_h = doc['width'] / per_side, doc['height'] / per_side
    first_c = max(0, int(math.floor(x / tile_w)))
    last_c = min(per_side - 1, int(math.ceil((x + w) / tile_w)) - 1)
    first_r = max(0, int(math.floor(y / tile_h)))
    last_r = min(per_side - 1, int(math.ceil((y + h) / tile_h)) - 1)
    return [(c, r) for c in range(first_c, last_c + 1) for r in range(first_r, last_r + 1)]


def _background_svg(doc: dict, *, zoom: int = None, view: tuple = None, href=None) -> str:
    background = doc['background']
    if not background['url']:
        return _empty_sheet(doc)
    if background['kind'] == 'tiles':
        return _tiles_svg(doc, zoom=zoom, view=view, href=href)
    url = href(None, None, None) if href else background['url']
    # preserveAspectRatio="none" because the document's own proportions are the
    # authority: the person sized the sheet to the image, not the other way round.
    return (
        f'<image href={quoteattr(url)} x="0" y="0" '
        f'width="{doc["width"]}" height="{doc["height"]}" '
        f'opacity="{background["opacity"]}" preserveAspectRatio="none"/>'
    )


def _grid_svg(doc: dict) -> str:
    grid = doc['grid']
    if not grid['show']:
        return ''
    lines = []
    for column in range(1, grid['cols']):
        x = round(doc['width'] * column / grid['cols'], 2)
        lines.append(f'<line x1="{x}" y1="0" x2="{x}" y2="{doc["height"]}"/>')
    for row in range(1, grid['rows']):
        y = round(doc['height'] * row / grid['rows'], 2)
        lines.append(f'<line x1="0" y1="{y}" x2="{doc["width"]}" y2="{y}"/>')
    return (f'<g stroke="#ffffff" stroke-opacity="0.28" stroke-width="1.5">'
            f'{"".join(lines)}</g>')


def render(doc: dict, *, standalone: bool = False, extra_class: str = '',
           places: list = None, view: tuple = None, tile_zoom: int = None,
           tile_href=None, size: tuple = None, marker_images: dict = None) -> str:
    """The whole map as one `<svg>` element.

    This is what the read-only page, the share link and the no-JavaScript
    fallback show — the editor's canvas is the same markup, kept up to date in
    the browser instead of re-rendered here.

    The rest is for an ops trace drawn into a briefing pack: `view` crops to
    (x, y, w, h) of the sheet, `tile_zoom` picks the terrain's level and
    `tile_href` where each tile comes from (see `_tiles_svg()`), and `size`
    gives the picture's width and height in pixels.

    `marker_images` (picture key → href) are the game's own pictures, which a
    guild uploads; a marker or unit with none is drawn as a placeholder.
    """
    classes = ('tacmap ' + extra_class).strip()
    namespace = ' xmlns="http://www.w3.org/2000/svg"' if standalone else ''
    box = view or (0, 0, doc['width'], doc['height'])
    # A line is so many screen pixels thick. A page's browser does that by
    # itself; a picture of a known size is drawn by resvg, which does not, so
    # the widths are given in sheet units per pixel of the picture instead.
    px = box[2] / size[0] if size and size[0] else 1.0
    icons = frozenset(marker_images or ())
    shown = ordered_items(doc)
    ts = doc.get('text_scale', DEFAULT_TEXT_SCALE)
    body = ''.join(item_svg(item, px, icons, ts) for item in shown)
    names = places_svg(doc, places or [])
    box_text = ' '.join(f'{round(v, 2):g}' for v in box)
    dims = f' width="{int(size[0])}" height="{int(size[1])}"' if size else ''
    # The sheet and the plan are separate groups so the editor can redraw either
    # on its own — changing the background must not touch what is drawn on it.
    return (
        f'<svg{namespace} viewBox="{box_text}"{dims} '
        f'class={quoteattr(classes)} preserveAspectRatio="xMidYMid meet">'
        f'{defs(marker_images, _tints(shown))}<g id="tm-back">'
        f'{_background_svg(doc, zoom=tile_zoom, view=view, href=tile_href)}'
        f'{_grid_svg(doc)}{names}</g>'
        f'<g id="tm-mask">{mask_svg(doc)}</g>'
        f'<g class="tm-items">{body}</g></svg>'
    )
