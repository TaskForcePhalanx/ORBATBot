<!-- Part of ORBATBot's documentation. The rules every session needs
     are in ../CLAUDE.md, which says when to read this file. -->

## Tactical maps (`utils/tacmap/` + `web/tacmap.py`)

The plan, drawn on the terrain — unit symbols, movement lines, objectives and
boundaries — built in the browser and shared with a link, the way the mission
planners in this hobby already work. Reading one is open to every member;
drawing, sharing and posting need the same rights as creating an event.

### One module, six files

`utils/tacmap` was one module of 3,257 lines. It is a package now — `base`,
`doc`, `places`, `drawing`, `arma`, `icons` — whose `__init__` re-exports every
name, so `from utils import tacmap` and `tacmap.anything` are exactly what they
were. Nothing outside changed, and no test changed.

**The layers only ever import downwards**, in that order: `base` depends on
nothing, `doc` on `base`, and so on up to `arma`. That is what keeps six files
a layer cake rather than a knot, and it is self-enforcing — every upward import
tried, at module level and lazily inside a function, fails at import time as a
circular import, because `base`'s own tables are built by calling its own
functions while every other module reads `base` at module level.

**It looked circular at first and was not**, which is the thing worth knowing
before moving anything back. Four per-item predicates — `item_colour`,
`is_ao`, `marker_key`, `line_size` — were sitting among the drawing code, and
four place constants among the place names, so measured by where things lived,
`base` depended on `drawing` and `doc` on `places`. They are in `base` now,
where everything may reach them. Moving one of them back out would make the
package cyclic again.

Two things about how the split was done, because they are the reason to trust
it:

- **Not a line was retyped.** Every item was lifted as its exact source span,
  comments included, and runs of items that were adjacent stay adjacent so
  their own spacing survives. Every line of the original appears exactly once
  across the six files — **0 unaccounted for** — bar ten section-banner
  comments the module docstrings replace.
- **Behaviour was fingerprinted before and after.** Every public entry point
  over 60 generated documents — `parse`, `render`, `to_sqf` both ways,
  `export_report`, `json_payload`, `ordered_items`, the coordinate and grid
  functions, `import_arma_markers`, `parse_map_index`, `parse_places`,
  `catalog` — hashed to **`cb3c261e…` on both sides**. The only names that
  left the namespace are the nine incidental stdlib imports (`re`, `json`,
  `escape` …), which were never API.

`drawing.py` is called that rather than `render.py` because `render` is also
a function: a submodule of the same name sits in the package namespace and
whether the function or the module wins depends on import order inside
`__init__`. It happened to work; a name that cannot collide is better than one
that works by luck.

`tests/test_tacmap_package.py` pins what this can quietly break — above all
that **every name a submodule defines is re-exported**, since forgetting one is
an AttributeError in whichever caller wanted it. The list is **generated** by
`scripts/gen_exports.py`, the same bargain `gen_help.py` makes with the README,
so there is nothing to remember and a stale list is a red test. `import *` is
not the alternative it looks like: it skips underscore-prefixed names, and four
of those are used outside the package (`_INK_DARK`, `_INK_LIGHT`, `_arma_type`,
`_colour`, all in `tests/test_tacmap.py`), so every module would need an
`__all__` listing them — the same list, fragmented across six files.

### The document is the map

One map is one JSON document: a background image, a grid, and a list of items.
`utils/tacmap.py` owns what an item is and nothing else does — `web/tacmap.py`
translates HTTP into it, `web/app.py` serves it, `web/static/tacmap.js` edits it
in the browser. There is no table of symbols, because **nothing hangs off a
symbol**: a save replaces the plan wholesale, exactly as the net list does, and
there is no identity for an edit to lose. That is the one thing that makes this
much smaller than the ORBAT editor next door, where a slot id carries somebody's
booking and the diff exists to protect it.

`parse()` runs on the way **out** as well as in. A row written by a newer
version, or by hand, only ever produces something this renderer is willing to
draw: finite bounded coordinates, known enumerations, strings cut to length, and
a background URL that is http(s) or nothing. Nothing downstream has to trust the
column.

### Layers, and what is drawn over what

A map holds **named layers** — phase 1, phase 2, the enemy picture — each with a
switch of its own. `doc['layers']` is a list of `{id, name, visible}` and every
item names one; an item pointing at a layer the document does not have lands on
the first rather than nowhere, because an invisible, unreachable item is worse
than a misfiled one.

Three things about them are deliberate:

- **A switch is not a delete.** Hiding a layer leaves its items in the document
  and in every save; `ordered_items()` is the only thing that skips them.
  Deleting a layer moves its items to the first one rather than taking them
  with it.
- **The switches work for whoever is looking**, including somebody who only has
  the share link and cannot edit. That is why the layer panel is outside the
  editor half of `tacmap.js` — the viewer's toggles change what their browser
  draws and nothing else, while `visible` as saved is what the server renders
  for the no-JavaScript fallback and for the first paint.
- **A hidden layer stays out of the Arma export.** Markers somebody switched
  off are not part of the plan being handed over, and Arma has no switch to
  turn them off again once they are drawn.

`ordered_items()` also fixes what gets drawn over what: layer first, then
`KIND_ORDER` — areas, lines, labels, markers, symbols — and only then the
document's own order. Leaving it to whatever was added last is what buried a
platoon under a boundary drawn after it. Within one kind the document order
still decides, which is what **Bring to front** moves.

### Every unit and marker is the game's own picture

There is no drawing of a symbol or a marker in this repository any more. The
first versions drew APP-6 frames and pictograms and a line-art copy of every
Arma marker; the unit asked for the game's own look instead, so **every unit
and every marker is drawn with Arma's picture**, tinted the way the game tints
it, and anything without a picture is not offered.

- **The pictures are Bohemia's and this repository is public**, so they are
  never committed. `utils/arma_icons.py` holds only names and paths — every
  marker in `CfgMarkers` (class, label, group, and where the game keeps its
  `.paa`) and Arma's NATO unit markers — read out of a running game. An admin
  uploads the pictures on the Terrains page; `tac_marker_icons` keeps them per
  guild, and they are served only behind guild membership
  (`/g/{guild}/mi/{key}.png`) or a live share link (`/m/{token}/mi/{key}.png`),
  `Cache-Control: private`.
- **Everything is keyed by Arma class in lower case** (`picture_key()`):
  `MARKERS['mil_dot']`, `MARKERS['loc_church']`, `UNIT_CLASSES['b_inf']`. That
  is the stored marker, the picture's key and its mask id (`tmk-mil_dot`).
  migration 1 renames pictures stored under the first release's short keys
  (`dot` → `mil_dot`).
- **An upload is matched by where the game keeps the picture**
  (`read_marker_icons()`): the last folder and the file name, without `_ca` /
  `_co`, against each entry's path — `military/dot_ca.png` is `mil_dot`,
  `nato/b_inf.png` is `b_inf`, `mapcontrol/Church_CA.png` is `loc_church` —
  then a file named after its class, then a file name only one picture has.
  `picture_coverage()` is what the Terrains page lists: per group, how many
  pictures this server has and the game paths of the ones it lacks.
- **Tinted by multiplying, as the game does.** Arma draws a marker picture
  multiplied by the marker colour: white becomes the colour, black stays
  black, transparency is the picture's — which is why a NATO marker is a
  coloured frame with black line work and its size dots are black. So each
  picture is an `<image>` in the defs, placed with `<use>` through a colour
  matrix filter per colour (`tint_filter()`, id `tmt-<hex>`; `render()` emits
  one for every colour on the map, `tintId()` in `tacmap.js` adds any new
  one). It replaced a luminance `<mask>`, which made exactly the black line
  work disappear. A flag (`paint`, the Flags group) is placed as it is. Ops
  traces get the pictures as data URIs (`marker_images_inline()`), since
  resvg fetches nothing.
- **Missing is a placeholder, never a guess.** A map drawn before the
  pictures were uploaded, or copied from another server, draws a dashed **?**
  (`MISSING_PICTURE`) where a picture is missing. The editor's lists offer only
  what has a picture (plus, in the inspector, whatever the selected item
  already is, marked *no picture*).

**A unit is one of Arma's NATO markers.** `SYMBOLS` is the game's set
(infantry, motorized, mechanized, armor, recon, helicopter, plane, UAV, naval,
medical, artillery, mortar, HQ, support, maintenance, service, installation,
anti-air, explosives, unknown, and the civilian car and ship);
`unit_class(side, symbol)` is the marker: the side gives the prefix (`b_`,
`o_`, `n_`, `c_`; the unknown side uses `n_` in the unknown colour and
`u_installation`), and a symbol a side has no marker for falls to that side's
unknown. Its colour is the side's Arma colour (`AFFILIATIONS`), as in game.
**Size is Arma's own size marker** (`ECHELONS` → `group_0` … `group_11`),
drawn in the same box over the unit and exported as a second marker at the
same spot; the import folds a size marker lying on a unit back into its size
(`_stack_sizes()`).

`parse()` reads older maps as what they always exported as: the APP-6 symbol
keys through `LEGACY_SYMBOLS` (with `hq: true` → `hq`, an air-dimension symbol →
helicopter, and a civilian one onto the civilian markers), and the military
markers' short keys through `LEGACY_MARKERS`. Strength, mobility, dimension,
status, abbreviation and higher formation are gone — Arma's markers have none
of them — and are dropped on the next save.

`UNIT_BOX` is small on purpose. A platoon plan puts twenty symbols on one
sheet. **`label_size()` floors the type at `MIN_LABEL`** however small the
symbol gets, and **`DEFAULT_SIZE` is `MIN_SIZE`**: a new item starts at the
bottom of the slider, while `parse()`'s fallback stays 1.0 so an old item
with no size draws as it always did. `line_size()` floors a line's geometry
at `MIN_LINE_SIZE` and `MIN_HIT` floors the pointer targets, for the same
reason.

### Colour says whose a *unit* is, and nothing else

`item_colour()` is the one rule, and it used to read
`item['color'] or AFFILIATIONS[side]['fill']` for everything. That made a
friendly plan come out in one flat pale blue: every phase line, every
boundary and every objective inherited the side, so the colour on the sheet
carried no information at all. The reference everybody plans from draws
control measures black and keeps colour for the symbols.

So the rule is now per kind:

| | drawn in |
|---|---|
| `unit` | its side's Arma colour (`AFFILIATIONS`) — what tells whose it is, as in game; a unit may not take a colour of its own |
| `line`, `area`, `point`, `text` | the colour somebody picked, else `DEFAULT_INK` — a free label too: black, at marker-text size, as Arma writes it |

**A task marker carries its own colour now too.** `parse()` only kept
`color` for a line, an area and a label, so a point had no way to be
anything but its side's — which is why the swatch row and the inspector's
colour field both reach `point` as well.

`LINE_COLOURS` is the palette the editor offers — red, orange, yellow,
green, blue, cyan, pink, purple, white, black, picked to hold up over
terrain that is bright sand in one corner and dark jungle in the other. The
free colour field is still there beside the swatches for anything else.
`arrow_head()` computes the head as a polygon rather than using an SVG
`marker` — a marker cannot take the colour of the line it sits on, so a
recoloured line would have kept its side's arrow.

**A line is drawn once, as thin as it says.** Lines, areas' edges and shape
outlines carry a `weight` in **screen pixels** (`LINE_WEIGHTS`, 1–6, old
items get one from their size) and are drawn with
`vector-effect: non-scaling-stroke`, so they keep that width at every zoom —
which is what Arma's own map lines do, and what makes the thinnest a true
1px line. There is **no backing pass** under them any more (`ink_backing()`
is still used for task markers and AO tabs): the halo made the thinnest line
three times as wide as it said, and a planner asked for lines without an
outline. Black ink on the bare dark sheet is therefore hard to see — on a
terrain, which every real map has, it is not.

**resvg ignores `non-scaling-stroke`** (checked: it draws the width in sheet
units), so `render()` works out `px` — sheet units per pixel of the picture,
`view width / size width` — whenever it is given a `size`, and `_stroke()`
multiplies the width and the dash pattern by it. On a page `px` is 1 and the
browser does the rest; in an ops trace the line comes out as thick as on
screen. Arrow heads (`arrow_head()`) are sized from the weight times `px`
for the same reason.

`style` is `solid`, `dashed` or `dotted` (`dash_pattern()`: a dot is a
zero-length dash with a round cap, as wide as the line). `arrow` on a line is
`''`, `end`, `start` or `both` (`ARROWS`); a line saved when it was a plain
`true` reads as `end`.

### The same colour in Arma

`arma_colour()` is what the export reads, and it used to read the side
alone — so a line painted red here arrived as `ColorWEST` blue there.
It now maps `item_colour()` onto the nearest name in `ARMA_COLOURS`.

**A dashed line is its dashes in Arma.** Checked in game: a polyline marker
ignores `setMarkerBrush` and `setMarkerSize` — every line is solid and one
width. So `dash_runs()` cuts a dashed or dotted line (or area edge) into
`EXPORT_DASH` lengths, in sheet units so the dashes are as long against the
terrain as on the website at its fitted zoom, and `to_sqf()` writes one
POLYLINE marker per dash. `MAX_DASHES` (150) caps the cost: past it the
dashes get longer rather than more numerous. A dashed shape becomes its
outline's dashes (`shape_outline()`; an ellipse as a 48-gon) plus, when it is
filled, the shape marker carrying only the fill. Arrow heads are a
`mil_arrow` marker at each end that has one. Thickness has no Arma
counterpart and stays on the website.

`setMarkerColor` takes a class out of `CfgMarkerColors` and nothing else —
there is no hex form, and a plan pasted into the debug console cannot
define its own class — so a picked colour has to land on something the game
already ships. Three things about that table:

- **The RGB in it is only ever used to measure distance**, never written
  into the script, which is what makes it safe to be approximate: the
  palette is far enough apart that a few points either way cannot change
  which name wins.
- **The five side colours are deliberately not in the pool.** `ColorEAST`
  is a dark red and `ColorCIV` a purple, so without holding them out an
  orange line could arrive in the mission claiming a side.
- **A unit still exports its side**, whatever else is set — the `b_`/`o_`
  marker prefix and `ColorWEST`/`ColorEAST` have to keep agreeing.

Arma has no cyan and no purple among its plain colours, so those two are
the only palette entries that do not survive the trip intact: they land on
`ColorBlue` and `ColorPink`.

### There is JavaScript here, and only here

The rest of the site is server-rendered with no script files on purpose — the
campaign planner's editor is the one other planned exception (see *The campaign
planner*). A map cannot be: placing a symbol on terrain and dragging it where it belongs *is* the
feature, and a page of coordinate fields would be worse than the paper map it
replaces. So `web/static/tacmap.js` exists — vanilla, no build step, no CDN,
served from the bot's own container like everything else.

**With scripting off the map still renders.** `render()` produces the whole SVG
server-side and the editor takes that markup over rather than building it from
nothing, so the fallback is a map you can read, not an empty box. That is also
why the read-only page and the share link need no JavaScript at all.

#### A line is plotted by clicking or dragged freehand, as in Arma

Hold **Ctrl** and **click** each corner, let go of Ctrl to finish — Arma's
own gesture — or hold Ctrl and **drag** for freehand. The Line and Area
tools do the same without Ctrl, finishing on a double-click or Enter. Both
can be mixed in one line: a drag while plotting carries the same line on
freehand. The Area tool closes the shape.

How it tells them apart: a press that never moves 5px before it is let go is
a **click**, and the first click turns the draft into a chain
(`draft.chain`); `draft.byCtrl` says whether letting go of Ctrl ends it.
Between clicks a rubber band runs from the last corner to the pointer.
**The double-click is detected in pointerdown** (`draft.lastClick`, 450 ms
and 6px), not from the browser's `dblclick`, which never fired: the draft
redraws under the pointer between the two clicks. The draft itself carries
`pointer-events="none"` for the same reason.

**A line never gets an arrowhead on its own.** One says *this way*, and most
lines on a plan are boundaries and phase lines that say no such thing; the
inspector puts one on the lines that mean it.

**Shift keeps a segment straight.** While plotting it snaps the next corner
to 15° from the last (`snapCorner()`); while dragging it straightens the
segment from the last point fixed before Shift went down (`draft.anchor`),
and letting go carries on freehand from the straight end.

**The status line never wraps** (unless it is an error): a hint that grew a
line between two clicks moved the map under the pointer.

Three details are load-bearing:

- **The stroke is thinned as it is drawn.** A pointer reports every pixel it
  crosses and a document holds `MAX_POINTS` of them, so an unthinned stroke
  spends the whole budget in one gesture. The step is a fraction of the
  *view* rather than of the sheet, so it thins by how far the line looks
  rather than by how far zoomed in it happens to be.
- **A stroke that goes nowhere is dropped in silence** (`draft.free`). A
  click-by-click draft that is too short was deliberate and is told why; a
  slipped finger does not need an error.
- **Escape is handled before the modifier guard** in the key handler,
  because a ctrl-drag is cancelled with Ctrl still held down. Otherwise the
  one key you reach for to get out is the one that cannot reach it.
- **`data-for` is a list in the toolbar as well as the inspector.** It was
  compared whole in `setTool()`, so every field belonging to more than one
  tool was silently hidden — which is what happened to the State picker's
  `data-for="unit point"`, invisible from the day it shipped.

### Place names: the terrain knows its own towns, the tiles do not

A tile pyramid arrives with roads, buildings and contours and **not one label**:
the renders come out of Arma's own map export as pure topography, and the game
draws the names over that afterwards from `CfgWorlds >> worldName >> Names`.

**Whether they can be read off OCAP depends on how that terrain was built, and
both answers have been got wrong here in turn.** First it was "OCAP has no
locations at all", on the strength of `map.json` carrying only `worldName`,
`worldSize`, `imageSize` and `multiplier`. Then it was "OCAP has them", on the
strength of the **current** maptool building a terrain from a **grad_meh**
export (`internal/maptool/vector.go` walks `geojson/**/*.geojson.gz`), which
writes the locations beside the tiles as
`geojson/locations/<locationtype>.geojson.gz` — one gzipped GeoJSON file per
Arma location type, `Point` geometry already in Arma's CRS, the name in
`properties.name`. The file names are lowercase location types, exactly what
`PLACE_KINDS` is keyed on, so **the file a place came from is its kind**.

Both were too general, and in the same way: **the answer is per terrain, not per
server.** OCAP's **older** route — the one in its own tile guide, `.emf` out of
Arma through gdal2tiles — produces raster tiles and no vector data whatever, and
one OCAP serves terrains imported both ways side by side. So a guild can have one
map importing its names in a single press and another, on the same server, with
nothing to find however hard the import looks. That is why `import_ocap_places()`
names the **terrain** in its refusal and says the neighbours may still work:
"no place names on that server" reads as a broken feature and sends somebody off
to check a server that is behaving perfectly.

**Three remote sources were tried and all three are closed**, which is worth
recording so nobody spends another round on it. A unit's own OCAP on the older
layout: 404, by construction. `maps.gruppe-adler.de`, added as a public
grad_meh mirror: unreachable, its repository archived in 2023. OCAP's own
`pmtiles.ocap2.com`, which its client falls back to: alive, but answers
*Archive not found* for a mod terrain. The gruppe-adler probe was **removed
again** rather than left in — a dead host costs a request on every failed
import and puts a confusing line in the message somebody is reading to work out
what went wrong. The PMTiles CDN was never added: its data is vector tiles,
protobuf-encoded MVT with coordinates already converted to degrees, which is
hundreds of lines to read and only pays off for a terrain that CDN carries.

**So the fallback is the script, and the script needs somewhere to paste.**
That was the real bug in all of this: the paste box hung off a `tac_terrains`
row, and a map on an OCAP server has none — so the refusal pointed at the
Terrains page, where there was no entry for that terrain to paste into. The one
remaining path was unreachable for exactly the deployment that needed it.
`web/tacmap.set_places()` takes the scope from the map instead, and the box and
the script sit in the same panel as the import button, so the whole loop is on
one screen.

They hang off a **terrain**, not a map, because every plan drawn on Tanoa wants
the same ones — and a terrain here is one of two things: a row we store (`/t/7`)
or somebody else's OCAP folder (`…/maps/tem_cham`). Only the first has a row to
hang anything off, so **`tacmap.place_scope()`** turns both into one key
(`t:7`, `tem_cham`) and **`tac_places`** is keyed on that. Two maps on the same
OCAP terrain therefore share one import, which is the whole point.

`tac_terrains.places` still exists and is written on upload; migration 1 copies
it into `tac_places` once so the one release that stored them there strands
nothing. A map stores only how much of them to show (`doc['places']`: `show`,
`groups`, `scale`).

Four things are deliberate:

- **`import_ocap_places()` probes and reports; it does not assume.** What a
  given OCAP instance serves cannot be known from here, so it tries each of
  `GEOJSON_DIRS`, listing the folder where the server lists directories and
  otherwise asking for `PROBE_KINDS` by name, and a server carrying none of it
  gets a message **naming every address tried**. That is the difference between
  "this feature is broken" and "your OCAP only serves tiles", and it is the only
  honest thing to build when the answer depends on somebody else's deployment.
- **`places_sqf()` is the last resort, and for an older OCAP it is the only
  resort.** It is the mirror of `to_sqf()` and the same door — vanilla Arma
  cannot send anything out, so the clipboard is how data leaves it. Leading with
  it was the first cut's mistake; treating it as avoidable for every deployment
  was the second. It is once per terrain, and both the README and the panel say
  so, because "once per terrain" was read as "every time" and that reading is
  what made it sound unacceptable.
- **SQF spells a literal quote `""` and has no backslash escape.** A `\"` in
  that script is a syntax error costing the whole thing, which is what
  `test_the_dump_script_uses_sqf_quote_escaping` pins. The script also strips
  quotes out of a name so what lands on the clipboard is always valid JSON.
- **Drawn under the plan, inside `#tm-back`.** These are the terrain talking; a
  symbol somebody placed must never end up behind a village name.
  `test_places_are_drawn_under_the_plan` is what keeps that true. Biggest name
  last, so where two collide the one people navigate by survives the overlap.
- **A group filter, because "all of them" is the honest default and a wall of
  text on a briefing sheet.** Tanoa knows several hundred places. An empty
  `groups` list means all — a terrain whose names were just imported should show
  them rather than nothing — and the editor ticks every box rather than showing
  them all off while all of them draw.

Nameless entries are dropped on the way in: Arma's terrains are full of
`FlatArea` and `Invisible` helpers that position things and have nothing to say.
They are counted and reported, because silence there would read as data loss.

Place names are **never exported to Arma** — the game already draws its own.

### What a label may not contain

`_text()` is the one place every string in a document goes through — all
twelve of them, labels included — which is why it is where control characters
are stripped rather than at each field.

**One of them the column cannot survive.** `tac_maps.doc` is `jsonb`, and
`jsonb` refuses `\u0000` outright ("unsupported Unicode escape sequence")
where `text` and Python's `json` both accept it without complaint. A label is
text anybody with an edit link can type, so before this a single NUL in one
stored perfectly well and would then have failed every later save of that map
— and it would have failed the conversion of the column itself, which happens
inside a migration, inside the boot
([docs/schema.md](schema.md#the-column-that-had-to-be-searched)).

The other C0 controls go too: they are junk in a label and the same class of
problem. **Newline and tab stay**, because `_label_svg()` and `_sqf_string()`
both already handle a label with newlines in it, so those are expected rather
than noise. `utils/reddit.py` drops the characters XML forbids for the same
reason — a feed must not be lost to one stray byte in a title.

### The share link is the whole credential

`/m/{token}` opens a map with no session and no Discord — which is the point,
because the people who need tonight's plan are in Discord, not necessarily
signed in here. Three things follow:

- **The token is long, random and revocable.** Regenerating it is the only way
  to un-share a map that has been forwarded further than intended, and
  `share_mode` (`off` / `view` / `edit`) decides whether the link may draw.
  Changing the mode keeps the token, so tightening the rights does not break
  every copy of the link that is already out there.
- **`get_tac_map_by_token()` never matches a map that is not shared**, so an
  empty or stale token cannot fall through to a row whose sharing was turned off.
- **A label is text anybody with an edit link can type**, which is why the two
  escaping paths are tested: `render()` escapes into the SVG, and
  `json_payload()` escapes `<` so a `</script>` in a label cannot end the tag
  the document is handed to the page in.

### A save is capped before it is read

`tacmap.MAX_DOC_BYTES` (4 MB) is checked in `map_json()` **before the body is
parsed**, on both save routes. `parse()` already bounds what a document may
hold, but only once the whole request is in memory — and the website runs
inside the bot, so a large enough POST takes the Discord connection down with
the page. Both routes check who is asking first (`require_draw()`, or an
`edit` share link), so this bounds what a Unit Leader or a link holder can
send rather than anonymous traffic.

**The number is measured, and the measurement is a test.** The largest
document `parse()` accepts — 400 freehand lines at the 120-point cap, each
with a 48-character label — serialises to **1.39 MB**;
`test_the_body_cap_clears_the_largest_document_the_parser_accepts` builds that
document and fails if it ever outgrows the cap. It is there because the first
figure proposed for this was 1 MB, *below* the real ceiling, which would have
refused a legitimately full map. A declared `Content-Length` over the cap is
refused outright; a chunked body with none is read in pieces and abandoned the
moment it goes over, so neither shape can spend more than the cap.

### The background is a picture, a terrain from OCAP, or one you upload

`background.kind` is `image` — one picture stretched across the sheet — or
`tiles`, the `{z}/{x}/{y}.png` pyramid GDAL2Tiles produces. The second kind
exists because **OCAP already renders every terrain the unit plays on** and
serves exactly that layout, so a unit running OCAP has a real map of its own
terrains sitting there, at a resolution no single image would carry.

A tile set gets onto a map two ways, and they meet in the same background:

| | `POST …/ocap` | `POST …/terrains` then `POST …/maps/{id}/terrain` |
|---|---|---|
| Where the tiles live | the OCAP server | this bot's database |
| `background.url` | `https://ocap…/maps/tanoa` | `/t/7` |
| Who has to be reachable | OCAP, for **everyone** who opens the map | nobody |

The upload is the one to prefer for anything shared outside the unit: a share
link is meant for people who do not sign in here, and expecting them to reach an
OCAP instance as well is how a link ends up showing an empty sheet.

The directory field starts filled in with `DEFAULT_OCAP_BASE`. It is only a
suggestion — nothing is read until somebody presses the button, and the first
directory that actually answers replaces it for that guild.

**The terrain is picked from a list, not typed.** OCAP names a folder after
the terrain's *world* name — Cham is `tem_cham` — which is exactly the thing
nobody remembers, so `POST …/ocap-list` reads the directory it keeps them in
and the panel offers what is in it. The directory is remembered per guild
(`guild_settings.ocap_base_url`) and only **once it has answered**: a saved
address that does not work is worse than none. Listing is its own route
rather than part of opening the map, because reading it is a request to
somebody else's server and should happen when somebody asks, not when they
look at a page. Pasting one map's address still works as the fallback it now
is, since some servers do not list a directory at all.

`tacmap.parse_map_index()` is what reads the answer, in `utils/tacmap.py`
with the rest of the stdlib-only code. What a listing looks like differs per
web server and none of them is worth requiring — nginx's JSON autoindex, a
plain list of names, and nginx's or Apache's HTML page all answer the same
question. Two rules in it matter: the HTML path **prefers links with a
trailing slash**, which is the only thing on such a page that tells a
terrain from the readme beside it, and falls back to every link when a
hand-written index marks none; and a link pointing off this folder —
absolute, rooted, `mailto:`, the parent — is **dropped rather than
followed**, because a listing is somebody else's page.

`POST …/ocap` takes the address of one of those map folders, reads its
`map.json` and settles both halves of setting a map up:

```json
{"name": "Cham", "worldName": "tem_cham", "worldSize": 8192,
 "imageSize": 16384, "multiplier": 2, "maxZoom": 6}
```

- `worldSize` is the terrain's edge in metres, so **the Arma corners fall out of
  the import** — the one number the export could not work out for itself, and
  the reason this is an import rather than a tile-URL field.
- `imageSize / 256` is how many tiles the bottom level has per side, so its zoom
  is `log₂` of that. `ocap_settings()` believes the smaller of that and the
  stated `maxZoom`: asking for a level the folder has not got is a screenful of
  missing tiles.

Four details are load-bearing:

- **Tiles are counted from the top left** (XYZ, not TMS). Checked against
  OCAP's own Tanoa set, which only assembles into Tanoa this way up — and there
  is deliberately no switch for the other convention, because nothing we have
  seen writes it.
- **`web/tacmap.import_ocap()` reads `map.json` server-side**, with `aiohttp`
  imported inside the function the way `utils/sheets.py` treats its
  credentials. Not a preference: the tile server is somebody else's origin, and
  a fetch from the page would need CORS headers nobody has set. The tiles
  themselves are loaded by the browser, so that server has to be reachable for
  whoever opens the map, not only for the bot.
- **The zoom is capped at `MAX_TILE_ZOOM`** and defaults to 4. A level holds
  4^z tiles, so 4 is 256 elements and 256 requests and 6 would be 4096.
- **Every tile is drawn at exactly its own size, over a coarse backdrop**
  (`BACKDROP_ZOOM`). A browser scaling each tile to a fractional pixel size
  anti-aliases both sides of a shared edge, which reads as a grid of dark
  hairlines over the terrain, so level 0 — one tile — is stretched across the
  whole sheet underneath: a seam then shows blurry terrain instead of the dark
  sheet. Measured on a smooth pyramid, the darkest seam goes from **44 grey
  levels** below its surroundings to **0.3**, which is invisible.

  **This replaced an overlap, and that history is the point.** Drawing each
  tile slightly over its neighbour was the obvious fix and the wrong one:
  making a tile bigger than its cell stretches what is inside it, so a feature
  drifted from 0 at the tile's left edge to **2.35px at its right** and then
  snapped back — a sawtooth at every boundary, which is what made roads jump
  and left duplicated slivers of thin features where one tile was painted over
  the next. Everything in an SVG scales, so zooming in magnified that error
  while the seam it was covering stayed one device pixel wide. Leaflet, which
  OCAP's own viewer uses, places tiles at exact positions and never stretches
  one; `test_a_tile_is_never_stretched_past_its_own_cell` is what keeps this
  side doing the same.

An OCAP import also squares the sheet, since the pyramid is square and a
landscape sheet would stretch the terrain sideways.

#### Uploading the archive instead (`utils/tiles.py` + `web/terrain.py`)

The same archives a unit prepares for its own OCAP — zip or 7z — go straight in.
`read_archive()` is what decides what comes out, and four things in it matter:

- **The names are read before any body is.** An archive goes as deep as OCAP's
  viewer zooms, which is several levels deeper than a planning sheet draws, so
  the levels to keep are chosen from the name list and only those are
  decompressed. Reading a 4096-tile level in full and then discarding it is the
  thing this avoids.
- **Deeper levels are dropped and counted**, never refused, and the reply says
  how many — otherwise a terrain arriving smaller than the file suggests reads
  as data loss.
- **7z goes through `py7zr`, imported where it is used**, the same as `aiohttp`
  in `utils/reddit.py`. It is a declared dependency; the lazy import is what
  keeps a deployment that lacks it reading zips instead of failing at startup.
  py7zr 1.x has no `read()` — members come out through
  `py7zr.io.BytesIOFactory`, which keeps them in memory rather than on a
  filesystem that will not outlive the container.
- **The world size comes from the archive's own `map.json`** when it has one,
  and is asked for when it does not. Without it the tiles would still draw and
  the Arma export would be nonsense, so it is the one thing an upload cannot go
  ahead without.

`create_tac_terrain()` writes the row and every tile **in one transaction**
(`copy_records_to_table`): a half-written terrain renders as a map with holes
and nothing about the upload says which tiles are missing.

Serving is `GET /t/{id}/{z}/{x}/{y}.png` — **no session**, because a map's
background has to load for everybody a share link was sent to, and a terrain
render is not a secret. It is `immutable` for a year, which is what keeps a
page of 256 tiles to one round of requests.

**Deleting a terrain is refused while a map is drawn on it**, and the refusal
names the maps. Nothing would break loudly otherwise — the tiles would simply
stop answering and every plan on that terrain would render on an empty sheet.
`tac_terrain_usage()` finds them by matching `"/t/{id}"` in the stored document
rather than keeping a column, so there is no second place for that fact to go
stale.

### Getting the plan into Arma 3

`to_sqf()` turns the map into `createMarker` calls somebody pastes into the
**debug console** of a running mission. That shape is forced, and worth knowing
before anybody proposes something neater: **vanilla Arma cannot fetch anything
from outside** — no HTTP, no file read — so without a mod on the server there is
no channel for this bot to push markers down. The console is the one door that
is already open, and `createMarker` is global, so a logged-in admin pressing
GLOBAL EXEC draws the plan for every player. (`enableDebugConsole` in the
mission's `description.ext` is what decides whether an admin has it.)

Four things in there are load-bearing:

- **The prefix is the map's row id** (`web/tacmap.arma_prefix()`), and the script
  deletes every marker carrying it before drawing. So pasting a corrected plan
  *replaces* the old markers rather than laying a second set over them — and two
  maps can never delete each other's, which a shared prefix would do silently.
  The prefix is also stripped to letters and digits, because it is written into
  the script as code.
- **`doc['arma']` is the sheet's four corners in world metres**, not a terrain
  size. A terrain preset fills them in for a whole-map image; a cropped one gets
  the corners read off the map in game, and nothing else has to change.
  `to_world()` flips the vertical axis, since the sheet's y grows downward and
  Arma's grows north. Corners that are inside-out are refused at parse time —
  they would divide by zero and put every marker in one spot.
- **A label goes through `_sqf_string()`**, which doubles quotes the way SQF
  wants and flattens newlines. The text comes from anybody with an edit link and
  ends up in a script an admin runs with full rights, so this is the same
  boundary as the HTML escaping, with a different escape.
- **Lines and areas are `POLYLINE` markers** (Arma 2.00+), an area being a
  polyline that repeats its first point, because Arma has no closed one. A
  line's arrow is a second `mil_arrow` marker turned along the last segment —
  there is no arrowhead on a polyline. Dashed is lost: Arma has no dashed
  marker.

A unit exports as exactly the NATO marker it is drawn as (`unit_class()`),
plus its size marker on top, and a marker as its own class — the editor only
draws what the game has.

**`editable=True` is the variant the plan can be touched in**, and it names
its markers differently because that is the only thing that decides it. Arma
works out who owns a marker by parsing the name it gives its own —
`_USER_DEFINED #owner/index/channel`, all three numeric — and only the owner
may move or delete one. Sticking `_USER_DEFINED ` in front of our own prefix
is not that shape; that was shipped first and the markers stayed read-only in
a mission, which is what `ARMA_CHANNELS` and `_map_number()` exist to fix.

Four things follow, and each is a promise the plain export made by other
means:

- **The owner is `clientOwner`, so it is run once, locally.** GLOBAL EXEC runs
  the code on every machine and each would write its own id into the name,
  drawing one plan per player. `createMarker` is global anyway, so LOCAL EXEC
  is both enough and required — the script's header says so.
- **The map's prefix cannot ride in the name**, since the index is numeric.
  `_map_number()` takes the digits out of the prefix instead, so two maps'
  markers cannot collide, and the script remembers what it drew in a public
  mission variable (`tacmap_map7`) and deletes exactly that on the next run.
  Every `createMarker` is matched by a `pushBack`, which a test pins: one
  missed and the next run leaves markers behind.
- **The channel is the last part of the name** (`ARMA_CHANNELS`, Arma's own
  ids). It is a choice on the panel rather than a constant because a plan
  meant for one side should not sit in Global.
- **It is still the second script, not the first.** The same click that drags
  a misplaced objective into place deletes it, and nothing asks first — so
  the editor shows both, side by side and named for what they are, rather
  than hiding this one behind a fold where it reads as the better version.
- **Both are LOCAL EXEC**, and the page says so. `createMarker` is global, so
  one machine running either script draws the markers for everybody; GLOBAL
  EXEC is wasted on the plain one and actively wrong on this one, where the
  name carries `clientOwner`.

Lines and areas are `POLYLINE` markers and the map's own editing is built
around icon markers, so those may stay fixed while the symbols move.

### The editor's layout

One bar, a rail, the map, a side panel (`_tacmap_canvas.html`, `.tmeditor`):

- **The bar** carries the page's menus on the left — terrain, OCAP, share,
  post, to and from Arma, name/copy/delete — and saving on the right. The
  menus are the old panels under the map, now `<details class="tmmenu">`
  dropdowns: `tacmap_edit.html` builds them into `map_menus` with
  `{% set %}` and the canvas renders that in its bar, so the share-link page
  (which sets none) has a bar with saving only. Being `<details>` they open
  without JavaScript and `panel=` still opens the one a form came back to;
  `tacmap.js` only keeps one open and closes it on a click elsewhere.
- **The rail** holds the tools; **the side panel** shows what the tool in
  hand draws (`#tmtoolpanel`, the old toolbar pickers) — stepping aside while
  something is selected with Select, when the inspector matters — then the
  layers, Map settings, Arma coordinates and Keys.
- **The map takes the window's height**; **Whole window** (`tm-focus`) makes
  the editor fixed over the page. `.widepage` is centred with a transform,
  which traps a fixed element, so `setFocus()` moves the editor to `<body>`
  and back. Esc leaves it when nothing is selected. **+ − ⤢** zoom and fit.
- **Autosave**, on by default and remembered per browser, saves 1.5 s after
  the last change, waits while something is being drawn or dragged, and stops
  after a refused save (`state.blocked`) until the person chooses.
- **Layers** can be reordered (↑ ↓ — order is drawing order) and **locked**
  (`locked: true`, written only when set): drawn, but nothing on them can be
  selected, boxed, Ctrl+A'd or dragged.
- Below 900 px the rail runs across the top and the side panel goes under
  the map.

### Editing, measuring and moving several things at once

- **A drawing tool draws one thing.** After a line, area, phase line, box,
  ellipse or AO the editor goes back to Select with it selected
  (`afterDrawn()`), because a click on empty ground used to start the next
  drawing when people meant to deselect. Double-clicking a tool in the rail
  locks it (`state.toolLock`, 🔒): drawings then come out unselected, an AO or
  phase line keeps its selection for naming and the next click on empty ground
  only deselects (`state.afterDraw`); Esc with nothing selected puts the tool
  down. Unit, marker and label placement keep their tool, since placing several
  in a row is the common case.
- **Names are written as Arma writes marker text** (`_label_svg()`): a narrow
  face (`ARMA_FONT`, Purista where installed), the item's own colour, a thin
  shadow in `ink_backing()`, beside a unit or marker rather than under it
  (`UNIT_LABEL_GAP`, `POINT_LABEL_GAP`). One map-wide `doc['text_scale']`
  (0.5–3, **Map settings → Text size**) multiplies every name and AO tab, as
  Arma has one marker-text size. Markers carry a `rotation` too, drawn and
  exported with `setMarkerDir`, and read back from Arma's direction.

All of it lives in `tacmap.js`; what has to agree with the server (lengths,
grid references, label places) has a Python twin in `utils/tacmap.py`.

- **A drag redraws what moved, not the plan** (`redrawItems()`). `render()`
  rebuilds every item's markup, and the three dragging paths — an item, a
  line's corner, a shape's handle — used to call it on every pointer move.
  Measured with CDP's main-thread counters: **14.6 ms a move at 156 items and
  31.9 ms at 396**, so a drag ran under 30 fps and got worse the fuller the
  map. Rebuilding only the groups that changed is **1.2 ms and 1.4 ms** at the
  same two sizes — flat, where the old path scaled with the map.

  **The items are still mutated first and the markup redrawn from them**, which
  is the whole reason this is a narrower redraw rather than a `transform` on
  the dragged group. A transform moves the picture without moving the plan, and
  then the mask's AO holes, the selection rings and every `getBBox()` are
  reading geometry that no longer matches. The grey mask is redrawn with the
  items when it is on, so dragging an AO moves its hole as you drag it.

- **A selected line's corners are handles** (`drawVertexHandles()`): drag one
  to move it, drag a segment's middle dot to add a corner there, Alt+click or
  right-click to delete one (never below two, or three for an area).
  **Handles answer only the Select tool** — with a drawing tool in hand a
  click on a corner starts a new line there, which is what Snap is for.
- **Measuring** reads the sheet's Arma corners (`metres_per_unit()`,
  `length_m()`, `grid_ref()`). The readout in the map's top corner shows the
  6-figure grid reference under the pointer for everybody, and while drawing
  the segment's length and bearing and the total; the inspector shows a
  line's length, an area's perimeter and size, a shape's size. The grid is
  counted from the terrain's south-west corner, as most terrains' are. The
  **Measure** tool (`state.ruler`) draws nothing into the document.
- **Snap** (`snapPoint()`, remembered per browser in `localStorage`) puts a
  corner being placed or dragged on the nearest corner, shape corner or
  centre, or symbol within 12 screen pixels, and closes a line on its own
  first corner.
- **Several selected** is `state.selected` (the last picked) plus
  `state.extra`; `selection()` is the list. Shift+click toggles, Shift+drag on
  empty ground boxes (wholly inside: every corner of a line), Ctrl+A takes
  everything shown. Dragging any selected item moves them all; Delete removes
  them all; the inspector hides while more than one is selected.
- **Copy and paste** go through `localStorage` (`tmclip`) as well as memory,
  so a plan copied on one map pastes into another — under the pointer when it
  is over the map. A pasted copy is never an AO and never lands on a planner
  layer: either would quietly create an AO in the campaign planner.
- **Phase lines** are lines with `label_at: 'ends'` (`LABEL_PLACES`); the
  tool starts the name as *PL* and drops the cursor after it.
  `line_label_spots()` puts a name above the middle corner or just past an
  end, reading away from the line. **In Arma a line marker shows no text**,
  so `to_sqf()` writes the name on a `LINE_LABEL_MARKER` — a `mil_dot` at
  **size 0** — at each place it is drawn. Checked in game against an `Empty`
  or `EmptyIcon` marker (no text at all), a dot at 0.4 (a visible dot) and
  `setMarkerText` on the polyline (nothing): only the size-0 dot shows the
  name and nothing else.

### Back from Arma, and what does not survive the trip

`ARMA_DUMP_SQF` is the mirror of the export: run in the debug console it
copies `allMapMarkers` — shape, type, position, text, colour, size,
direction, brush and polyline — to the clipboard with `str`, and
`import_arma_markers()` reads it back (`parse_sqf_value()` is a small reader
for what `str` writes: nested arrays, strings with doubled quotes, numbers,
booleans). Positions go through `from_world()`. Lines and closed polylines
(areas), rectangles and ellipses, `mil_`/`hd_` markers and NATO unit markers
come back as themselves; anything else as a dot named after its type, so
nothing is silently lost. *Only markers players drew* keeps `_USER_DEFINED`
ones. `web/tacmap.import_from_arma()` puts them on a new *From Arma* layer.

`export_report()` sits above the scripts: how many markers (it counts
`createMarker` in the script itself, so it cannot disagree with it) and what
will not survive — hidden layers, dashes, thickness, unfilled freehand areas,
shape labels, symbols with only a nearest Arma marker, the mask.

### Rectangles and ellipses are their own kind

`kind: 'shape'` holds a centre, a width and height, a rotation, a `shape`
(`rect` / `ellipse`, `SHAPES`) and a `fill` (`none` / `light` / `hatch`,
`SHAPE_FILLS`). It is not an area with four corners, for one reason: **Arma's
RECTANGLE and ELLIPSE markers are the only ones the game fills and turns**, so
a shape exports as one of those — `setMarkerSize` (half-sizes in metres, from
the sheet's corners), `setMarkerDir` and `setMarkerBrush` (`Border`, `Solid`,
`FDiagonal`) — where an area can only be a polyline outline. Arma shows no text
on an area marker, so a shape's label stays on the website's map; the help and
the inspector say so.

Four things are deliberate:

- **Drawn in its own frame, centred on 0,0, and turned into place** with one
  `transform`, so the outline and the hatching turn together and the handles in
  `tacmap.js` work in that same frame: a corner handle keeps the opposite
  corner fixed *along the shape's own sides*, and the round handle above it
  sets the rotation (Shift snaps to 15°).
- **Hatching is geometry, not a `<pattern>`** (`hatch_segments()`): a pattern
  cannot take the colour of the shape it fills, and a clip path needs an id per
  item that both renderers would have to agree on. The lines are cut to the
  rectangle or the ellipse analytically, so the server and the browser draw the
  same segments — checked to the hundredth when it was written. The hatching is
  backed like the outline, or the default black ink vanishes on the dark sheet.
- **A shape is drawn at the areas' level** (`KIND_ORDER`), under lines and
  symbols, for the same reason areas are.
- **`shape_corners()` is a shape's extent** — the campaign planner's ops-trace
  crop (`_item_bounds()`) uses it, because a shape has an `x` and would
  otherwise be measured like a unit.

`Shift` while dragging one out makes it a square or a circle; a drag that ends
smaller than `MIN_SHAPE` is a slipped click and adds nothing, the same as a
stroke that goes nowhere.

### The grey mask, and AOs as holes in it

`doc['mask']` (`{on, opacity}`, `DEFAULT_MASK`, off) lays a grey cover over the
whole sheet, the way a covered Arma map greys out what a mission is not about.
**An AO is any area or shape with `ao: true`**: it is cut out of the cover and
drawn as a border with nothing in it (`is_ao()`). The **AO** tool in the editor
draws one as a rectangle, an ellipse or freehand, names it `AO n` and drops the
cursor into its label, and turns the mask on if it was off; the inspector's
*AO* tick turns any area or shape into one and back.

- **A `<mask>`, not an even-odd path** (`mask_svg()`): two AOs that overlap
  must merge into one clear area, and an even-odd fill turns the overlap grey
  again. Only AOs on **visible** layers cut holes, like everything else a layer
  switch decides.
- **Between the terrain and the plan**, in its own `#tm-mask` group, so
  symbols stay readable over the grey and the editor can redraw it on every
  move without touching either neighbour.
- **The mask's id is a hash of what is in it**, because the campaign page puts
  a map on a page that may hold another, and `url(#id)` resolves to the first
  match in the document. The editor's live mask has an id of its own.
- **It does not go to Arma.** A cover with holes cut in it is not a marker, and
  Arma's own cover-map effect takes one rectangle; the export says so in a
  comment and brings the AO borders (an AO shape always exports with the
  `Border` brush).

#### Telling overlapping AOs apart

Overlapping AOs merge into one clear area, which is right for the mask and
leaves only the borders to say which ground is whose. Three things carry that:

- **Every AO its own colour.** `next_ao_colour()` is the first of `AO_COLOURS`
  no other AO on the map is drawn in; the editor's AO tool and *AO* tick
  (`nextAOColour()`) and `colour_aos()` — run by `sync_campaign_map()` on
  placeholders and on maps from before colours — all use it. **Only an empty
  `color` is filled in**: one somebody picked, black included, is theirs.
  The order keeps neighbours far apart and puts pink before purple and cyan,
  which Arma draws as pink and blue — so the first six AOs are distinct in the
  mission too (`arma_colour()` is unchanged; it is the colour that changed).
- **The name in a tab, not the middle** (`ao_corner()`, `ao_tab()`,
  `_ao_tab_svg()`, mirrored by `aoCorner()`/`aoTabSVG()`): a box in the AO's
  colour hung inside the corner nearest the top left (smallest x + y), text in
  `ink_backing()` so it reads on yellow and blue alike. An ellipse's tab is on
  the ellipse itself, at its point furthest towards the top left — the corner
  of its box is off it. Overlapping AOs have different top-left corners, which
  is what keeps their names off each other. **The tab's width is estimated
  from the character count**, not measured, because the server cannot measure
  text and the two renderers must draw the same box.
- **A key beside the campaign map** (`campaign.ao_legend()`, visible AOs in
  layer order, through `web/campaigns.campaign_map_view()`). The preview colours
  any uncoloured AO for the picture only, as the next sync will store it, so
  the preview and the key never disagree with the map as it opens.

An AO map copies its outline, colour included. One made before colours takes
its AO's colour from the campaign map when opened (`match_ao_colour()`) —
again only into an empty `color`, so a colour changed later on either map is
not chased.

### Posting is a link, not a picture

`post()` sends an embed with a link and the counts. Discord renders neither an
SVG attachment nor anything this bot could turn one into without a drawing
library it does not have — and the link is the thing people actually want, since
it stays current while the plan is still being edited.

### Notes for future changes

- **`utils/tacmap/` imports nothing but the standard library**, like
  `utils/orbat.py` and `utils/reddit.py` (`utils/arma_icons.py` is the one
  exception, and is itself stdlib-only — names and paths, never artwork). Keep
  it that way: it is the only reason the document rules and the escaping are
  tested at all (`tests/test_tacmap.py`), and a test now says so.
- **The marker list is the game's.** A new vanilla marker is one row in
  `utils/arma_icons.py` (class, label, group, the game's path); a unit symbol
  is one entry in `SYMBOLS` naming the Arma type. `catalog()` puts either in
  the editor, which offers it once a server has its picture.
- **The editor page is `.widepage`**, the same escape from the 900px column the
  ORBAT editor uses.
- **Two people saving one map** no longer overwrite each other silently.
  Every write of the document bumps `tac_maps.doc_rev`; the editor sends the
  revision it opened on, and `database.save_tac_map_doc_if()` stores only if
  it still matches (one `UPDATE … WHERE doc_rev = $n`). Otherwise the route
  answers 409 and the editor offers *Reload theirs* or *Save mine over it*
  (`force`). An edit made while a save was in flight is kept: the editor
  only swaps in a campaign map's returned document when nothing changed
  since it sent (`state.edits`). After an undo, a delete or a returned
  document, `refreshLayers()` redraws the layer panel and pickers, whose
  rows otherwise kept editing layer objects the document no longer had.
- **Live collaboration is not here.** Two people drawing at once are told, not
  merged. Server-Sent Events
  were sketched for the roster and would fit here the same way — the request
  handler already sees every change, because it runs in the bot's process.

---
