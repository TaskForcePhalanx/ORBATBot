"""One tactical map: what can be drawn on it, and how it draws.

A map is a **document** — a background image, a grid and a list of items — and
this module is the only thing that knows what an item is. `web/tacmap.py`
translates forms and HTTP into it, `web/app.py` serves it, and the browser
editor in `web/static/tacmap.js` manipulates the same document client-side.

**It imports nothing but the standard library**, for the same reason
`utils/orbat.py` does: the symbol geometry and the validation are the two places
where a bug either loses somebody's planning or puts unescaped text into a page,
and both need to be testable without a database, a Discord connection or a web
server (`tests/test_tacmap.py`).

### The geometry lives here, not in the JavaScript

`defs()` emits every frame, icon and arrowhead once, as `<g>` elements in a
`<defs>` block. Both renderers then only ever place a `<use href="#tmf-friend">`
— so the browser and the server draw the *same* infantry symbol, and adding one
means adding it in one place. Only the composition (which `<use>`, at which
coordinates) is written twice, and it is three attributes long on purpose.

Referenced content is `<g>` rather than `<symbol>` deliberately: a `<symbol>`
establishes a viewport and clips, which would cut the staff off a headquarters
and the arrowhead off a line.

### Everything is in document units

An item's coordinates are in the document's own space (`width` × `height`,
1000 × 1000 by default) and the SVG carries that as its `viewBox`, so the map
scales to whatever it is drawn into without a single number changing. The
background image is stretched across the same box: the document *is* the map
sheet, and nothing here knows about pixels or zoom levels.
"""

import gzip
import hashlib
import html as _html
import json
import math
import re
from urllib.parse import unquote
from xml.sax.saxutils import escape, quoteattr

DEFAULT_WIDTH = 1000
DEFAULT_HEIGHT = 1000

# Limits. Generous enough for a company plan and small enough that one document
# stays a reasonable row, a reasonable POST body and a page a browser can draw.
MAX_ITEMS = 400
MAX_POINTS = 120
MAX_LABEL = 48
MAX_NOTE = 240
MAX_GLYPH = 4
# Named layers, each switchable on its own, so one map holds phase 1, phase 2
# and the enemy picture instead of being copied three times.
MAX_LAYERS = 12
MAX_LAYER_NAME = 40
DEFAULT_LAYER = {'id': 'plan', 'name': 'Plan', 'visible': True}

# How big a save may be before it is refused unread. The caps above bound what
# `parse()` will keep, but the body is read into the bot's own process first —
# and the website runs inside the bot, so a large enough POST takes the Discord
# connection down with the page. The number is measured rather than guessed:
# the largest document `parse()` accepts is 400 freehand lines at the point cap
# with a full-length label each, which serialises to **1.39 MB**, so this is
# about three times the real ceiling and thirty times a working map.
# `tests/test_tacmap.py` builds that document and fails if it ever outgrows
# this — the first figure written here was a third of the true ceiling and
# would have refused legitimate saves.
MAX_DOC_BYTES = 4 * 1024 * 1024
MIN_SIZE = 0.15
MAX_SIZE = 4.0
# What a freshly placed symbol, marker, line, area or label starts at. The
# smallest the slider goes, on purpose: every plan drawn here came out too big
# and was shrunk by hand, item by item, which is the wrong way round. Growing
# the two that need to stand out is one drag; shrinking twenty is twenty.
# This is the *new item* default and deliberately not `parse()`'s fallback,
# which stays at 1.0 so a stored item that somehow carries no size keeps
# drawing the size it always did.
DEFAULT_SIZE = MIN_SIZE

# The box a size-1 symbol occupies, in document units. Small on purpose: a
# platoon plan puts twenty of these on one sheet, and a symbol that reads as
# comfortable with three on screen is a wall of colour with twenty.
UNIT_BOX = 40
# How far under a unit's centre its name sits, as a share of the symbol box.
UNIT_LABEL_DROP = 0.6
# How far right of its centre a unit's or a marker's name starts, as a share of
# its box — beside the picture, where Arma writes marker text.
UNIT_LABEL_GAP = 0.5
POINT_LABEL_GAP = 0.62
POINT_BOX = 26

# However small a symbol is dragged, its name has to stay readable — the label
# is what the plan is for.
MIN_LABEL = 9

# The same argument for a line: one shrunk to nothing is not a line. So its own
# geometry — width, dashes and arrow head — stops shrinking here, whatever size
# the item carries. Without this a line at DEFAULT_SIZE draws 0.6 units wide on
# a 1000-unit sheet, which is under a device pixel and reads as nothing at all.
MIN_LINE_SIZE = 0.4

# The smallest pointer target, across its full width, in document units. A
# marker and a line already carry an invisible fat copy of themselves to be
# grabbed by, and both were sized from the item — so at the small end the thing
# you can see became a thing you cannot click. Only the browser draws these;
# the number lives here with the rest of the geometry.
MIN_HIT = 14

KINDS = ('unit', 'point', 'line', 'area', 'shape', 'text')

# What gets drawn over what. A symbol under an area was the one thing people
# lost on a busy sheet, so the order is fixed by kind rather than left to
# whatever was added last; `sort_order` only breaks ties within a kind.
KIND_ORDER = {'area': 0, 'shape': 0, 'line': 1, 'text': 2, 'point': 3, 'unit': 4}

# Whose a unit is. Every unit and every marker is drawn from Arma's own
# pictures (`utils/arma_icons.py`), tinted the way the game tints them, so a
# unit takes its side's marker colour — `ColorWEST`, `ColorEAST` and the rest,
# the RGB the game itself uses — and the side's marker prefix picks the frame.
AFFILIATIONS = {
    'friend': {'label': 'BLUFOR', 'fill': '#004d99'},
    'hostile': {'label': 'OPFOR', 'fill': '#800000'},
    'neutral': {'label': 'Independent', 'fill': '#008000'},
    'civ': {'label': 'Civilian', 'fill': '#660080'},
    'unknown': {'label': 'Unknown', 'fill': '#b29900'},
}
DEFAULT_SIDE = 'friend'

# What a line, an area or a task marker is drawn in when nobody picked a
# colour: black, which is what Arma's own markers default to.
DEFAULT_INK = '#1b1f24'

# Line art sits on terrain that is bright in one corner and dark in the other,
# so an arrow head or an AO tab is drawn over a backing pass in whichever
# direction the ink is not.
_INK_LIGHT = '#f2f4f6'
_INK_DARK = '#11161c'

try:
    from . import arma_icons as _icons
except ImportError:                     # imported as a top-level module
    import arma_icons as _icons


def picture_key(arma_class: str) -> str:
    """The key a picture is stored and referred to by: the Arma class, in
    lower case, so `loc_Church` and an upload named `loc_church.png` meet."""
    return (arma_class or '').lower()


# Every marker Arma ships, keyed by its class in lower case. `arma` is the
# class itself, which is what the export writes; `icon` is where the game keeps
# the picture, which is what an upload is matched by. A flag is `paint`: a
# picture in its own colours, drawn as it is rather than tinted.
MARKERS = {
    picture_key(cls): {'label': label, 'arma': cls, 'group': group, 'icon': path,
                       'paint': group == 'Flags'}
    for cls, label, group, path in _icons.MARKER_ICONS
}
MARKER_GROUPS = ('Military', 'Hand-drawn', 'Map locations', 'Unit sizes', 'Respawn',
                 'Zeus', 'Flags')
DEFAULT_MARKER = 'mil_dot'

# A map drawn before the markers were keyed by Arma class named the military
# ones without their prefix — `dot`, `objective`. Every other old key already
# was the class in lower case.
LEGACY_MARKERS = {
    key: f'mil_{key}' for key in (
        'dot', 'circle', 'box', 'triangle', 'objective', 'destroy', 'warning',
        'unknown', 'flag', 'arrow', 'arrow2', 'ambush', 'start', 'end', 'pickup',
        'join', 'marker')
}

# Arma's NATO unit markers, by class in lower case.
UNIT_CLASSES = {picture_key(cls): {'arma': cls, 'label': label, 'icon': path}
                for cls, label, path in _icons.UNIT_ICONS}

# A unit is one of Arma's NATO markers: the side picks the prefix and the
# symbol the rest. `arma` is the marker's type without its prefix. Civilians
# have only five markers of their own (`civ`), so the car and the ship are
# offered for them and stand in for the nearest military type elsewhere.
SYMBOLS = {
    'inf': {'label': 'Infantry', 'arma': 'inf'},
    'motor_inf': {'label': 'Motorized infantry', 'arma': 'motor_inf'},
    'mech_inf': {'label': 'Mechanized infantry', 'arma': 'mech_inf'},
    'armor': {'label': 'Armor', 'arma': 'armor'},
    'recon': {'label': 'Recon', 'arma': 'recon'},
    'heli': {'label': 'Helicopter', 'arma': 'air'},
    'plane': {'label': 'Plane', 'arma': 'plane'},
    'uav': {'label': 'UAV', 'arma': 'uav'},
    'naval': {'label': 'Naval', 'arma': 'naval'},
    'med': {'label': 'Medical', 'arma': 'med'},
    'art': {'label': 'Artillery', 'arma': 'art'},
    'mortar': {'label': 'Mortar', 'arma': 'mortar'},
    'hq': {'label': 'HQ', 'arma': 'hq'},
    'support': {'label': 'Support', 'arma': 'support'},
    'maint': {'label': 'Maintenance', 'arma': 'maint'},
    'service': {'label': 'Service', 'arma': 'service'},
    'installation': {'label': 'Installation', 'arma': 'installation'},
    'antiair': {'label': 'Anti-air', 'arma': 'antiair'},
    'ordnance': {'label': 'Explosives', 'arma': 'Ordnance'},
    'car': {'label': 'Car', 'arma': 'car', 'civ': True},
    'ship': {'label': 'Ship', 'arma': 'ship', 'civ': True},
    'unknown': {'label': 'Unknown', 'arma': 'unknown'},
}
DEFAULT_SYMBOL = 'inf'

# What a civilian unit becomes when its symbol has no civilian marker, and
# what the two civilian-only symbols become on any other side.
_CIVILIAN = {'motor_inf': 'car', 'mech_inf': 'car', 'armor': 'car', 'naval': 'ship',
             'uav': 'plane', 'heli': 'air', 'plane': 'plane', 'car': 'car',
             'ship': 'ship', 'unknown': 'unknown'}
_MILITARY = {'car': 'motor_inf', 'ship': 'naval'}

# A map drawn when units were APP-6 symbols names them in that set. Each goes
# to the Arma marker it always exported as.
LEGACY_SYMBOLS = {
    'generic': 'unknown', 'mech': 'mech_inf', 'motor': 'motor_inf',
    'sniper': 'recon', 'mg': 'inf', 'at': 'support', 'aa': 'antiair',
    'arty': 'art', 'veh': 'motor_inf', 'air': 'plane', 'engr': 'maint',
    'logi': 'service', 'signal': 'support', 'truck': 'motor_inf',
    'apc': 'mech_inf', 'ifv': 'mech_inf', 'tank': 'armor', 'hmg': 'inf',
    'gl': 'inf', 'eod': 'ordnance',
}


def unit_class(side: str, symbol: str) -> str:
    """The Arma marker a unit is: `b_inf`, `o_armor`, `c_car`."""
    symbol = symbol if symbol in SYMBOLS else DEFAULT_SYMBOL
    if symbol == 'installation' and side in ('civ', 'unknown'):
        return 'u_installation'
    if side == 'civ':
        return f"c_{_CIVILIAN.get(symbol, 'unknown')}"
    prefix = {'friend': 'b', 'hostile': 'o'}.get(side, 'n')
    arma = SYMBOLS[_MILITARY.get(symbol, symbol)]['arma']
    name = f'{prefix}_{arma}'
    # Explosives exist for BLUFOR and OPFOR only.
    return name if picture_key(name) in UNIT_CLASSES else f'{prefix}_unknown'


# Unit size, drawn as Arma's own size marker (`group_0` … `group_11`) over the
# unit — the same pair a mission maker stacks in the editor.
ECHELONS = {
    'team': {'label': 'Fire team', 'arma': 'group_0'},
    'squad': {'label': 'Squad', 'arma': 'group_1'},
    'section': {'label': 'Section', 'arma': 'group_2'},
    'platoon': {'label': 'Platoon', 'arma': 'group_3'},
    'company': {'label': 'Company', 'arma': 'group_4'},
    'battalion': {'label': 'Battalion', 'arma': 'group_5'},
    'regiment': {'label': 'Regiment', 'arma': 'group_6'},
    'brigade': {'label': 'Brigade', 'arma': 'group_7'},
    'division': {'label': 'Division', 'arma': 'group_8'},
    'corps': {'label': 'Corps', 'arma': 'group_9'},
    'army': {'label': 'Army', 'arma': 'group_10'},
    'armygroup': {'label': 'Army group', 'arma': 'group_11'},
}

LINE_STYLES = ('solid', 'dashed', 'dotted')
LINE_STYLE_LABELS = {'solid': 'Solid', 'dashed': 'Dashed', 'dotted': 'Dotted'}

# How thick a line, an area's edge or a shape's outline is drawn, in screen
# pixels. Lines keep that width at every zoom (`vector-effect:
# non-scaling-stroke`), the way Arma's own map lines do, so the thinnest is a
# true 1px line however far in or out you are. Arma draws every line marker
# at one fixed width, so this is the website's alone — the export says so.
LINE_WEIGHTS = (1, 2, 3, 4, 6)
DEFAULT_WEIGHT = 2
MAX_WEIGHT = 8

# Where a line carries an arrow head. Each one becomes a `mil_arrow` marker in
# Arma, turned along the end of the line it sits on.
ARROWS = (('', 'None'), ('end', 'At the end'), ('start', 'At the start'),
          ('both', 'Both ends'))

# Where a line's name is written. A phase line is named at both ends, which
# is how a phase line is drawn on every overlay; a route at its end.
LABEL_PLACES = (('middle', 'In the middle'), ('ends', 'At both ends'),
                ('end', 'At the end'), ('start', 'At the start'))

# The marker a line's name rides on in Arma. A line marker shows no text, so
# the name goes on a small marker of its own at each place it is written.
# A dot at size 0: checked in game against an Empty or EmptyIcon marker, a
# dot at 0.4 and text on the line itself, it is the one that shows the name
# and nothing else — no dot, and the text in Arma's own bold map lettering.
LINE_LABEL_MARKER = 'mil_dot'
LINE_LABEL_SIZE = 0

# A dashed or dotted line in Arma, in sheet units: Arma has no dashed marker,
# so the export cuts the line into short line markers, one per dash. Measured
# in sheet units so the dashes come out as long, against the terrain, as they
# look on this map at its fitted zoom.
EXPORT_DASH = {'dashed': (14.0, 9.0), 'dotted': (2.0, 7.0)}
# The most markers one dashed item may become. A long dashed line would
# otherwise be hundreds of markers; past this the dashes get longer instead.
MAX_DASHES = 150

# A shape is a rectangle or an ellipse with a centre, a size and a rotation —
# an objective box, a sector, a no-fire zone. It is its own kind rather than an
# area with four points because that is what Arma has: RECTANGLE and ELLIPSE
# markers are the only ones the game will fill and turn, so a shape arrives in
# the mission filled and rotated where a freehand area can only be an outline.
SHAPES = {
    'rect': {'label': 'Rectangle', 'arma': 'RECTANGLE'},
    'ellipse': {'label': 'Ellipse', 'arma': 'ELLIPSE'},
}
DEFAULT_SHAPE = 'rect'

# How a shape is filled, and the Arma brush that says the same thing.
# `Solid` is the game's see-through fill, `FDiagonal` its hatching.
SHAPE_FILLS = {
    'none': {'label': 'Outline only', 'brush': 'Border'},
    'light': {'label': 'Light fill', 'brush': 'Solid'},
    'hatch': {'label': 'Hatched', 'brush': 'FDiagonal'},
}
DEFAULT_FILL = 'light'

# The smallest a shape may be, in sheet units: anything less is a click that
# slipped, and a shape nobody can see cannot be selected to be removed.
MIN_SHAPE = 4

# The gap between hatch lines at size 1, in sheet units. It scales with the
# shape's line size so a thick outline gets coarse hatching and a fine one fine.
HATCH_GAP = 14

# What a line, an area or a task marker can be painted, beyond `DEFAULT_INK`.
#
# A symbol may not take one of these — whose a unit is has to stay readable
# from its colour — but a line is a route, a boundary, a phase line or a fire
# control measure, and those have been told apart by colour on every paper map
# there has ever been. One plan wants more than the five sides can say.
#
# Picked to hold up over terrain that is bright sand in one corner and dark
# jungle in the other, which rules out anything pale or muddy; the free colour
# field is still there for anything else.
LINE_COLOURS = (
    ('#e03b3b', 'Red'),
    ('#f07f2a', 'Orange'),
    ('#f2c832', 'Yellow'),
    ('#3fb950', 'Green'),
    ('#3d8ee8', 'Blue'),
    ('#35c9c2', 'Cyan'),
    ('#e060b8', 'Pink'),
    ('#a06ee8', 'Purple'),
    ('#f5f5f5', 'White'),
    ('#1b1f24', 'Black'),
)

# The order AOs are coloured in, so two AOs on one map — overlapping ones
# above all — never share a border. Neighbours in the list are far apart
# (red, then blue, then yellow), so the first few AOs, which is most
# campaigns, are as distinct as the palette allows. Black and white are left
# out: they are what an AO's edging and an unpicked line already are.
# Pink comes before purple and cyan because Arma has neither of those two and
# would draw them as pink and blue — so the first six AOs stay distinct in
# the mission as well as on the website.
AO_COLOURS = ('#e03b3b', '#3d8ee8', '#f2c832', '#3fb950',
              '#f07f2a', '#e060b8', '#a06ee8', '#35c9c2')


def next_ao_colour(items: list, skip=None) -> str:
    """The first of `AO_COLOURS` no AO among `items` is drawn in (`skip`: an
    item to leave out, the one being coloured). With every colour taken —
    nine AOs on one map — it goes round again rather than giving up."""
    used = {item_colour(i).lower() for i in items if i is not skip and is_ao(i)}
    for colour in AO_COLOURS:
        if colour not in used:
            return colour
    return AO_COLOURS[len(used) % len(AO_COLOURS)]


def colour_aos(items: list) -> int:
    """Give every AO that has no colour of its own a distinct one, in order.
    A colour somebody picked — black included — is theirs and kept. Returns
    how many were coloured."""
    count = 0
    for item in items:
        if is_ao(item) and not item.get('color'):
            item['color'] = next_ao_colour(items, skip=item)
            count += 1
    return count

# ---------------------------------------------------------------------------
# Backgrounds
# ---------------------------------------------------------------------------
#
# Two kinds. `image` is one picture stretched across the sheet, which is what a
# screenshot or a map export is. `tiles` is the pyramid of 256 px squares that
# GDAL2Tiles produces and that **OCAP already ships for every terrain a unit
# plays on** — `{z}/{x}/{y}.png` under one folder per world. Pointing at that
# folder is worth the second kind on its own: it is a real map of the terrain,
# at a resolution one image could not carry, that the unit is already hosting.
BACKGROUND_KINDS = ('image', 'tiles')

# The pyramid doubles each level, so a level holds 4^z tiles: zoom 4 is 256
# requests and 256 elements, which is a lot already, and 6 would be 4096.
TILE_SIZE = 256
MAX_TILE_ZOOM = 5
DEFAULT_TILE_ZOOM = 4

# Tiles are counted from the **top** left, the way GDAL2Tiles writes them and
# the way OCAP reads them back — checked against OCAP's own Tanoa set, which
# only assembles into Tanoa this way up. There is deliberately no switch for
# the other convention: nothing we have seen uses it.
#
# Neighbouring tiles share an edge exactly, and a browser scaling each one to a
# fractional pixel size anti-aliases both sides of that edge — which reads as a
# grid of bright hairlines drawn over the terrain. The fix is a coarse copy of
# the same terrain laid *under* the grid, so a hairline shows blurry terrain
# instead of the dark sheet.
#
# It used to be an overlap — each tile drawn slightly over its neighbour — and
# that was the wrong fix, because making a tile bigger than its cell stretches
# what is inside it. Measured against a ruler pyramid, a feature drifted from
# 0 at a tile's left edge to 2.35px at its right and then snapped back: a
# sawtooth at every boundary, which is what made roads jump. Everything in an
# SVG scales, so zooming in scaled that error up while the seam it was covering
# stayed one device pixel wide. Leaflet, which OCAP's own viewer uses, places
# tiles at exact positions and never stretches one; this does the same.
#
# Level 0 is one tile over the whole sheet, so the backdrop costs one request.
# Its blur does not matter: it is only ever seen through a hairline.
BACKDROP_ZOOM = 0

# What the editor offers as ready-made markers. The glyph is stored, not the
# preset, so renaming one here never changes a map that was already drawn.
POINT_PRESETS = (
    ('OBJ', 'Objective'),
    ('RP', 'Rally point'),
    ('LZ', 'Landing zone'),
    ('PZ', 'Pickup zone'),
    ('CCP', 'Casualty collection'),
    ('SUP', 'Supply / cache'),
    ('OP', 'Observation post'),
    ('CP', 'Checkpoint'),
    ('TGT', 'Target'),
    ('IED', 'IED / mines'),
    ('SP', 'Start point'),
    ('WP', 'Waypoint'),
)

# ---------------------------------------------------------------------------
# Arma 3
# ---------------------------------------------------------------------------
#
# A map drawn here can be put into a running mission as ordinary Arma markers,
# with no mod on either side — see `to_sqf()` for why that is a script somebody
# pastes rather than something this bot sends.

# Terrain sizes in metres, for the preset that fills the extent in. The list is
# a convenience only: any terrain works, because what the export actually needs
# is the four corner coordinates below.
ARMA_TERRAINS = (
    ('Altis', 30720),
    ('Stratis', 8192),
    ('Malden', 12800),
    ('Tanoa', 15360),
    ('Livonia', 12800),
    ('Chernarus', 15360),
    ('Takistan', 12800),
    ('Sahrani', 20480),
)
DEFAULT_EXTENT = {'terrain': '', 'left': 0.0, 'bottom': 0.0,
                  'right': 30720.0, 'top': 30720.0}

# How much of the terrain's own place names one map shows. `groups` empty means
# all of them — a terrain whose names have just been imported should show them
# rather than nothing. See the place-names section at the foot of this module.
DEFAULT_PLACES = {'show': True, 'groups': [], 'scale': 1.0}

# The grey cover over everything outside the AOs, the way Arma's own map greys
# out what a mission is not about. An AO is any area or shape flagged `ao`:
# it cuts a clear hole in the cover and is drawn with a plain border. Off until
# somebody turns it on, or draws an AO.
DEFAULT_MASK = {'on': False, 'opacity': 0.55}
# How big a map's writing is, against what label_size() works out — the one
# setting for every name on the map, the way Arma has one marker text size.
DEFAULT_TEXT_SCALE = 1.0
MIN_TEXT_SCALE = 0.5
MAX_TEXT_SCALE = 3.0
# Arma writes marker text in Purista, its own face; where somebody has it
# installed it is used, otherwise the nearest narrow sans.
ARMA_FONT = ("Purista, 'PuristaMedium', 'Roboto Condensed', 'Arial Narrow', "
             "'Liberation Sans Narrow', 'Helvetica Neue', Arial, sans-serif")
MASK_COLOUR = '#565c63'
MIN_MASK_OPACITY = 0.15
MAX_MASK_OPACITY = 0.9

# Which side a unit is, in Arma's terms: the marker-type prefix and the colour.
ARMA_SIDES = {
    'friend': ('b', 'ColorWEST'),
    'hostile': ('o', 'ColorEAST'),
    'neutral': ('n', 'ColorGUER'),
    'civ': ('c', 'ColorCIV'),
    'unknown': ('u', 'ColorUNKNOWN'),
}

# Arma's own stock marker colours, so a line drawn red here arrives red there.
# `setMarkerColor` takes a class name out of `CfgMarkerColors` and nothing
# else — there is no hex form and a mission run from the debug console cannot
# define its own class — so a colour somebody picked has to land on the
# nearest thing the game already ships.
#
# The RGB here is only ever used to measure that distance, never written into
# the script, which is what makes it safe to be approximate: our palette is
# far enough apart that a few points either way cannot change which name
# wins. `test_every_palette_colour_maps_to_its_own_name` is what pins that.
#
# The five side colours are deliberately **not** in this table. They say whose
# a thing is, and a line somebody painted orange should not come out as
# ColorEAST because that happened to be the closest red.
ARMA_COLOURS = {
    'ColorBlack':  (0x1b, 0x1f, 0x24),
    'ColorGrey':   (0x80, 0x80, 0x80),
    'ColorRed':    (0xe0, 0x20, 0x20),
    'ColorBrown':  (0x80, 0x40, 0x00),
    'ColorOrange': (0xf0, 0x80, 0x20),
    'ColorYellow': (0xf0, 0xd0, 0x20),
    'ColorKhaki':  (0x99, 0x99, 0x55),
    'ColorGreen':  (0x20, 0xb0, 0x40),
    'ColorBlue':   (0x30, 0x70, 0xe0),
    'ColorPink':   (0xe0, 0x60, 0xb8),
    'ColorWhite':  (0xf5, 0xf5, 0xf5),
}


def _rgb(colour: str):
    """`#rrggbb` as three numbers, or None when it is not one."""
    text = (colour or '').strip().lstrip('#')
    if len(text) != 6:
        return None
    try:
        return tuple(int(text[at:at + 2], 16) for at in (0, 2, 4))
    except ValueError:
        return None


def arma_colour(item: dict) -> str:
    """The `CfgMarkerColors` name this item is drawn in.

    A colour somebody picked wins, mapped onto the nearest stock one; with
    none, the side's colour, which is what every marker used to get.
    """
    if item['kind'] == 'unit':
        return ARMA_SIDES.get(item['side'], ARMA_SIDES['unknown'])[1]
    if item['kind'] == 'point' and MARKERS[marker_key(item)].get('paint'):
        return 'Default'          # a flag is its own colours in the game too
    wanted = _rgb(item_colour(item))
    if wanted is None:
        return ARMA_SIDES.get(item['side'], ARMA_SIDES['unknown'])[1]
    return min(
        ARMA_COLOURS,
        key=lambda name: sum(
            (a - b) ** 2 for a, b in zip(ARMA_COLOURS[name], wanted)),
    )

# What a glyph means when the point was drawn before the marker shapes
# existed and so carries none: the same guesses as before, so an older map
# exports as it always did.
ARMA_POINTS = {
    'OBJ': 'mil_objective', 'TGT': 'mil_destroy', 'SP': 'mil_start',
    'RP': 'mil_end', 'IED': 'mil_warning', 'CP': 'mil_triangle',
    'LZ': 'mil_pickup', 'PZ': 'mil_pickup',
}

# Text drawn over a satellite image needs a halo or it disappears into the
# terrain; white on a dark outline is what every map tool ends up at.
_FONT = 'system-ui, -apple-system, Segoe UI, Roboto, sans-serif'
_LABEL_FILL = '#ffffff'
_LABEL_HALO = '#11161c'


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------


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


def _text(value, limit: int) -> str:
    return (value or '').strip()[:limit] if isinstance(value, str) else ''


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


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------


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


# What stands in for a marker or unit this server has no picture for — a map
# drawn before the pictures were uploaded, or one copied from another server.
MISSING_PICTURE = ('<circle cx="50" cy="50" r="22" fill="#11161c" fill-opacity="0.35" '
                   'stroke="#9aa4ad" stroke-width="5" stroke-dasharray="8 6"/>'
                   '<text x="50" y="61" text-anchor="middle" font-size="32" '
                   'font-weight="700" fill="#e6e9ec" '
                   'font-family="system-ui, sans-serif">?</text>')


def line_size(size: float) -> float:
    """The size a line draws its own geometry at — never below MIN_LINE_SIZE.

    The mirror of `label_size()`, and for the same reason: an item may be made
    as small as the slider goes, but what says *this is a line* has to survive
    it. Width, dash pattern and arrow head all read this, so they stay in
    proportion to each other rather than each growing its own floor.
    """
    return max(size, MIN_LINE_SIZE)


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


def item_colour(item: dict) -> str:
    """What an item is drawn in.

    A unit takes its side's colour, because that is how APP-6 says whose it
    is and the frame carries no other clue. Everything else — a line, an
    area, a task marker, a label — takes the colour somebody picked for it,
    and `DEFAULT_INK` when they picked none.
    """
    if item['kind'] == 'unit':
        return AFFILIATIONS[item['side']]['fill']
    # A free label included: it used to default to white at 1.6 times the
    # size, which is exactly the big white lettering Arma never draws. Arma's
    # marker text is the marker's colour, and black when nobody chose one.
    return item.get('color') or DEFAULT_INK


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


def is_ao(item: dict) -> bool:
    return item['kind'] in ('area', 'shape') and bool(item.get('ao'))


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


def marker_key(item: dict) -> str:
    """Which marker a point is drawn as — the dot when it says nothing."""
    marker = item.get('marker')
    return marker if marker in MARKERS else DEFAULT_MARKER


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


# ---------------------------------------------------------------------------
# Putting the plan into a running mission
# ---------------------------------------------------------------------------


# How many terrains one directory listing may offer. A unit's OCAP folder
# holds a few dozen; a listing far longer than that is not a map directory
# and there is no reason to render it into a dropdown.
MAX_INDEX_ENTRIES = 300

# What a folder name may be. OCAP names them after the terrain's world name
# (`tem_cham`, `tanoa`), and anything outside this is either a file or
# something that should not be pasted back into a URL.
_INDEX_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')
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


def metres_per_unit(doc: dict) -> tuple:
    """How many metres one sheet unit is across and down, from the sheet's
    Arma corners — what lengths and grid references are measured with."""
    arma = doc.get('arma') or DEFAULT_EXTENT
    return (abs(arma['right'] - arma['left']) / doc['width'],
            abs(arma['top'] - arma['bottom']) / doc['height'])


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


# ---------------------------------------------------------------------------
# Place names
# ---------------------------------------------------------------------------
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

# Arma's location types, grouped so the filter is five switches rather than
# forty. `rank` drives the type size: a capital has to read at a glance and a
# rock does not.
PLACE_GROUPS = (
    ('settlement', 'Towns and villages'),
    ('terrain', 'Hills and landmarks'),
    ('water', 'Water and coast'),
    ('poi', 'Places of interest'),
    ('other', 'Everything else'),
)

PLACE_KINDS = {
    'namecitycapital': {'label': 'Capital', 'group': 'settlement', 'rank': 5},
    'namecity': {'label': 'City', 'group': 'settlement', 'rank': 4},
    'namevillage': {'label': 'Village', 'group': 'settlement', 'rank': 3},
    'namelocal': {'label': 'Local', 'group': 'settlement', 'rank': 2},
    'airport': {'label': 'Airfield', 'group': 'terrain', 'rank': 4},
    'hill': {'label': 'Hill', 'group': 'terrain', 'rank': 2},
    'mount': {'label': 'Mountain', 'group': 'terrain', 'rank': 3},
    'rockarea': {'label': 'Rocks', 'group': 'terrain', 'rank': 1},
    'viewpoint': {'label': 'Viewpoint', 'group': 'terrain', 'rank': 1},
    'bordercrossing': {'label': 'Border crossing', 'group': 'terrain', 'rank': 2},
    'namemarine': {'label': 'Sea', 'group': 'water', 'rank': 3},
    'namecoastline': {'label': 'Coast', 'group': 'water', 'rank': 2},
    'namecoast': {'label': 'Coast', 'group': 'water', 'rank': 2},
    'ruin': {'label': 'Ruin', 'group': 'poi', 'rank': 1},
    'historic': {'label': 'Historic', 'group': 'poi', 'rank': 1},
    'hotel': {'label': 'Hotel', 'group': 'poi', 'rank': 1},
    'culturalproperty': {'label': 'Cultural property', 'group': 'poi', 'rank': 1},
    'civildefense': {'label': 'Civil defence', 'group': 'poi', 'rank': 1},
    'dangerousforces': {'label': 'Dangerous forces', 'group': 'poi', 'rank': 1},
    'strategic': {'label': 'Strategic', 'group': 'poi', 'rank': 2},
    'strategicarea': {'label': 'Strategic area', 'group': 'poi', 'rank': 2},
}

DEFAULT_PLACE_KIND = 'namelocal'

# Type size per rank, in sheet units. The sheet is DEFAULT_WIDTH wide, so these
# are the same scale as MIN_LABEL and the symbol labels next to them.
PLACE_SIZE = {1: 7.0, 2: 8.5, 3: 10.5, 4: 13.0, 5: 16.0}


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


# --- Reading the names out of an OCAP / grad_meh export --------------------
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


# ---------------------------------------------------------------------------
# Back from Arma: the markers of a running mission, onto the map
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# The game's own marker pictures
# ---------------------------------------------------------------------------
#
# Every marker and unit is drawn from Arma's own pictures (white on
# transparent PNGs exported from the game), which a guild uploads. They are
# Bohemia's artwork, so they never go into this repository: they are
# uploaded per guild, kept in the database and served only to the guild's
# members and to holders of a map's share link.

MAX_ICON_BYTES = 256 * 1024
MAX_ICON_ARCHIVE = 20 * 1024 * 1024


def _icon_stem(path: str) -> tuple:
    """(folder, name) of a picture's path, lower case, without the extension
    or the `_ca` / `_co` the game's texture names end in — what an upload's
    file and the game's own path have in common."""
    parts = [p for p in path.replace('\\', '/').lower().split('/') if p]
    name = parts[-1].rsplit('.', 1)[0] if parts else ''
    for tail in ('_ca', '_co'):
        if name.endswith(tail):
            name = name[:-len(tail)]
    return (parts[-2] if len(parts) > 1 else '', name)


def _picture_index() -> tuple:
    """Every picture this map can use, by (folder, name) and by name alone."""
    by_path, by_name = {}, {}
    entries = [(key, value['icon']) for key, value in MARKERS.items()]
    entries += [(key, value['icon']) for key, value in UNIT_CLASSES.items()]
    for key, path in entries:
        folder, name = _icon_stem(path)
        by_path.setdefault((folder, name), []).append(key)
        by_name.setdefault(name, []).append(key)
    return by_path, by_name


def picture_coverage(have) -> list:
    """Per group, how many of the game's pictures this server has and where
    the game keeps the ones it lacks — what the Terrains page lists, so
    whoever exports them from the game knows which files to fetch."""
    have = set(have or ())
    groups = [('NATO units', list(UNIT_CLASSES.items()))]
    for group in MARKER_GROUPS:
        groups.append((group, [(key, value) for key, value in MARKERS.items()
                               if value['group'] == group]))
    rows = []
    for name, entries in groups:
        missing = [value['icon'].lstrip('\\') for key, value in entries if key not in have]
        rows.append({'group': name, 'total': len(entries),
                     'have': len(entries) - len(missing), 'missing': missing})
    return rows


def read_marker_icons(data: bytes) -> tuple:
    """The marker pictures in an uploaded zip, as {marker key: PNG bytes},
    and what to tell the person.

    A picture is matched by where the game keeps it: `military/dot_ca.png`
    is `mil_dot`, `handdrawn/dot_ca.png` is `hd_dot`, `nato/b_inf.png` is
    `b_inf`. A file named after its class (`loc_Church.png`) is that class.
    Anything that is not a PNG, is too big, or is no picture the game uses is
    left out and counted.
    """
    import io
    import zipfile
    if len(data) > MAX_ICON_ARCHIVE:
        raise ValueError('That archive is far bigger than a set of marker icons.')
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError('That is not a zip file.')
    by_path, by_name = _picture_index()
    known = set(MARKERS) | set(UNIT_CLASSES)
    icons, unknown, refused = {}, [], 0
    for info in archive.infolist():
        if info.is_dir():
            continue
        name = info.filename.replace('\\', '/').rsplit('/', 1)[-1]
        if not name.lower().endswith('.png') or name.startswith('.'):
            continue
        folder, stem = _icon_stem(info.filename)
        # The game's own folder and file name first; then a file named after
        # its class (`loc_Church.png`); then a file name only one picture has.
        keys = (by_path.get((folder, stem))
                or ([stem] if stem in known else None)
                or (by_name.get(stem) if len(by_name.get(stem, ())) == 1 else None))
        if not keys:
            unknown.append(name)
            continue
        if info.file_size > MAX_ICON_BYTES:
            refused += 1
            continue
        body = archive.read(info)
        if not body.startswith(b'\x89PNG\r\n\x1a\n'):
            refused += 1
            continue
        for key in keys:
            icons[key] = body
    if not icons:
        raise ValueError('No marker pictures in that zip — expected PNGs such as '
                         'military/dot_ca.png, nato/b_inf.png or handdrawn/flag_ca.png.')
    notes = [f'{len(icons)} marker picture{"s" if len(icons) != 1 else ""} stored.']
    if unknown:
        notes.append(f'{len(unknown)} named no marker this map knows and were left out '
                     f'({", ".join(sorted(unknown)[:5])}{"…" if len(unknown) > 5 else ""}).')
    if refused:
        notes.append(f'{refused} were not PNGs, or too big, and were left out.')
    return icons, notes
