"""The vocabulary every other module here shares.

Arma's own tables — the sides and their colours, the markers, the unit
symbols, the echelons — the limits a document is held to, and the small
helpers and predicates that read one item: what colour it is drawn in
(`item_colour`), whether it is an AO (`is_ao`), which picture it wants
(`marker_key`), how thick its line is (`line_size`).

**It imports nothing from its neighbours**, which is what makes the rest of
the package a layer cake rather than a knot. Four of those predicates used to
sit among the drawing code and two place constants among the place names;
they are here because everything needs them, and leaving them where they were
is what made the split look circular when it is not.

Part of `utils/tacmap`, split out of one 3,257-line module. The package's
`__init__` re-exports every name, so `from utils import tacmap` and
`tacmap.anything` work exactly as they did.
"""
import math
import re


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
    from .. import arma_icons as _icons
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


def metres_per_unit(doc: dict) -> tuple:
    """How many metres one sheet unit is across and down, from the sheet's
    Arma corners — what lengths, grid references and the scale bar measure
    with.

    It lives in the base layer rather than beside the Arma export it was
    written for, because the renderer needs it too and the renderer sits
    *below* that export: the layers only import downwards. `arma.py` takes it
    from here, which is the direction that is allowed.
    """
    arma = doc.get('arma') or DEFAULT_EXTENT
    return (abs(arma['right'] - arma['left']) / doc['width'],
            abs(arma['top'] - arma['bottom']) / doc['height'])


# How much of the picture's width the scale bar aims to take up. A fifth is
# long enough to read a round number off and short enough to sit in a corner
# without crossing the plan.
SCALE_BAR_SHARE = 0.2

# The only lengths a scale bar is ever drawn as, times a power of ten. Anything
# else is a bar you have to do arithmetic on, which defeats the point of it.
SCALE_BAR_STEPS = (1, 2, 5)


def distance_label(metres: float) -> str:
    """A ground distance as a map reader expects to see it: `500 m`, `1 km`,
    `1.5 km`. Never `1.0 km`, and never `1500 m`."""
    if metres < 1000:
        return f'{int(round(metres))} m'
    km = metres / 1000
    return f'{km:g} km'


def scale_bar(span_m: float) -> tuple:
    """`(metres, label)` for a bar across a picture spanning *span_m* on the
    ground — the largest round number that fits its share of the width.

    Largest that *fits*, not nearest: a bar allowed to overrun its share
    creeps across the plan on exactly the crops where the plan is busiest.
    A span too small for any round number at all gives `(0, '')`, and the
    renderer draws nothing rather than a bar labelled `0 m`.
    """
    target = span_m * SCALE_BAR_SHARE
    if target <= 0:
        return (0, '')
    power = 10 ** math.floor(math.log10(target))
    best = 0
    for step in SCALE_BAR_STEPS:
        if step * power <= target:
            best = step * power
    if not best:
        # log10's floor can land a power just above the target through floating
        # point; the step below is always small enough.
        best = power / 10
    if best <= 0:
        return (0, '')
    return (best, distance_label(best))


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


def is_ao(item: dict) -> bool:
    return item['kind'] in ('area', 'shape') and bool(item.get('ao'))


def marker_key(item: dict) -> str:
    """Which marker a point is drawn as — the dot when it says nothing."""
    marker = item.get('marker')
    return marker if marker in MARKERS else DEFAULT_MARKER


# What a folder name may be. OCAP names them after the terrain's world name
# (`tem_cham`, `tanoa`), and anything outside this is either a file or
# something that should not be pasted back into a URL.
_INDEX_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')


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
