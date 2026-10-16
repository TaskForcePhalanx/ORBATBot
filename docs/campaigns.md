<!-- Part of ORBATBot's documentation. The rules every session needs
     are in ../CLAUDE.md, which says when to read this file. -->

## The campaign planner (`utils/campaign.py`, `web/campaigns.py`)

Plans a campaign of three to five missions and the weekly cycle of drops that
announces each one. It combines what were two separate tools — the Op Cycle
Planner and the Mission Objectives Library — with this bot's ORBATs and
tactical maps. The design is in the *TFP Campaign Planner — Design Spec*; it is
being built in milestones:

1. **Foundations** — the rules module, the `cp_` tables, access, settings. *Done.*
2. **Campaigns, missions and the wizard** — campaign folders, the campaign
   page, AOs, missions (carrying over stable details), the interactive
   six-step wizard, the objective picker with the library, the back-schedule
   with checks and every drop ready to copy. *Done.*
3. **The ORBAT link and PDF/DOCX export** — link, copy or create a mission's
   ORBAT, edit it from the planner, and the three briefing packs as PDF and
   Word. *Done.*
4. **Drops and approvals** — approving, scheduled posting, holds and pings,
   edits after posting, sign-ups at the opening drop. *Done.*
5. **Maps** — the campaign map with an outline per AO, a detailed map per
   AO with a layer per phase, and each phase's layer drawn into the leaders'
   pack as its ops trace. *Done.*
6. **After action and the Kill Tracker** — the OCAP recording matched to the
   mission, added to the campaign's Kill Tracker campaign, its numbers offered
   to the AAR. *Done.*

### Who can plan

`web.guilds.can_plan(member, unit_role_id)`: server admins always; Unit Leaders
only when they also hold the role set as **Unit role** in the planner's
settings. With no role set, admins only — the planner never opens wider than
somebody chose. Settings are admins only.

### The rules live in Python

`utils/campaign.py` is stdlib only and tested. The editor pages will be
interactive, but they ask the server for the schedule, each drop's text and the
checks rather than working them out in the browser, so the preview, the export
and the bot's own posting can never disagree. In particular:

- **Drop dates** — WARNO at D-28, the four weekly drops on the chosen weekday
  in each of the four weeks before the op (the last such day before D-21, D-14,
  D-7, D-0), AAR at D+1. Drop times are read in the guild's time zone; op times
  (orders, H-Hour, FragO timings) in the mission's own zone, falling back to the
  guild's.
- **Every time in a post is a Discord timestamp.**
- **The ORBAT line** — before the drop that opens the ORBAT, a drop says when it
  opens; on it, that it is open; after it, that slots are filling. The ORBAT
  opens for everyone at once.
- **Splitting** — `split_message()` splits over 2,000 characters on paragraphs,
  then lines, then spaces, and ends each part with `-# Part i/n`.
- **Approval** — `approval_hash()` of the text and image ids. A drop only posts
  when the hash stored at approval matches what it would post now, so any edit
  after approval needs approving again.
- **Private fields** (`Field.private`, e.g. *Held back for Zeus*) are never read
  by `build_post()` or the checks.

### The pages

| Page | |
|---|---|
| Campaigns (`campaigns`) | Every campaign as a folder: its missions with date, steps locked and what is next; empty slots up to `planned_missions`; archived ones folded away |
| Campaign | Missions in order (↑ ↓), **Plan the next mission**, the AOs, the details form, delete (type the name) |
| Mission wizard | The Overview, then the six steps one at a time; tips, checks and the step's drop preview beside it. **JavaScript** |
| Back-schedule | Every lock and drop with its status, the checks, every drop as it will post with its state and **Approve / Withdraw / Don't post**, and **Start sign-ups now**. **No JavaScript** — the fallback for the wizard |
| Objectives (`cpobjectives`) | The library by category. Admins edit, add and remove |

### The wizard

`web/static/campaign.js` is the **second deliberate exception** to the
no-JavaScript rule (after `tacmap.js`, below), for the same reason — the editor
*is* the feature: autosave, drag to reorder, paste a screenshot, switch steps
without a reload. Vanilla, no build step, no CDN. It decides nothing:

- **The page is drawn empty and filled from JSON.** `wizard_page()` renders a
  blank editor per field (`campaign.STEPS` is the field list) and the state is
  handed over as `<script type="application/json">` (Jinja's `tojson`, which
  escapes `<`). The same `mission_state()` answers the API and draws the
  schedule page, so the two cannot disagree.
- **Saving is a PATCH of what changed**, a quarter of a second after the last
  edit, with the CSRF token in `X-CSRF-Token`. Edits made while one is in
  flight wait for the next. The answer is the recomputed state — progress,
  schedule, drops, checks — and **inputs are never overwritten from it**, only
  what is derived from them, so a reply landing mid-word eats nothing. A
  refused save puts the batch back and says why; a network failure retries.
- **What may be stored is decided on the server** — `campaign.clean_fields()`
  (known keys, their shapes, cut to length) and `clean_phases()` (at most 12,
  the layers a tactical map holds; an AO or library id that isn't the
  campaign's is dropped). Every image id is checked against the campaign
  (`cp_image_meta`) before a save, so a hand-made request cannot point a
  mission at another server's image.
- **Phases go whole.** The phase list is sent entire whenever it changes;
  `save_cp_phases()` keeps the ids of phases it already has, inserts the new
  and deletes the missing. The script takes the new ids from the reply only
  when nothing was edited in the meantime.
- **Nested drag.** Objectives sit inside phases; a drop only counts in the list
  the drag started from, and none of the drag events bubble.
- **Preview in Discord** opens the step's drop in a pop-up (`<dialog>`) drawn
  the way Discord shows it. The formatting is the server's:
  `utils/discordmd.render()` turns each part into HTML in `mission_state()`
  (`drop.html`, with mention names from the bot's view of the guild and the
  drop channel's name in `settings.channel_name`), so the schedule page shows
  the same thing in a fold-out with no JavaScript. The renderer escapes first
  and marks up second — the text is what somebody typed — and only http(s)
  links survive. The browser does only what Discord does in a reader's
  client: timestamps (`data-ts`, `data-style`) redrawn in the viewer's zone,
  spoilers revealed on a click. The raw text pane stays, because it is
  exactly what is sent and what **Copy** copies.
- **Images** are sniffed from their bytes (`campaign.check_image()` — PNG,
  JPEG, GIF, WebP, 8 MB, what Discord takes) and served behind planner access
  with `nosniff`. An upload error is shown beside the field: the status line is
  overwritten by the next save.

#### The Overview is the first tab

Opening a mission lands here, because *where does this mission stand* is the
question somebody has on opening one and the six step tabs each answer only
for themselves. A link to a step (`#orders`) still opens that step, so the
checks and a drop can point at one.

**It is the schedule page**: the back-schedule with every lock and drop and
what each is waiting on, the checks, and every drop with **Approve / Withdraw
/ Don't post** — which is what the planner wanted first on opening a mission,
and was already built and already correct.

- **One template, two pages.** `_cp_schedule_body.html` is included by
  `cp_schedule.html` and by the wizard's Overview, and it is the schedule
  page's own markup, moved: checked by rebuilding the page from the partial
  and diffing against the version before the move — byte for byte identical.
  A second copy is the thing this prevents, since the two would disagree about
  what is due the first time either was edited.
- **`back` says where a form returns to**, `'schedule'` or `'wizard'`, the
  same convention `_cp_mission_tools.html` already used — and it now reaches
  the drop actions too, which always redirected to the schedule page and so
  threw somebody onto another page for approving a drop from the Overview. A
  drop action comes back to `#drop-<kind>`, which on the wizard is a panel
  inside the hidden Overview: `campaign.js` opens the tab and scrolls to it,
  which the browser cannot do by itself while the section is hidden.
- **`back == 'schedule'` also brings the mission-tools card**, since the
  wizard's side panel already carries the same one.
- **The partial asks for `steps_by_key`**, which both pages now hand over. It
  was `steps[c.step].title`, and the wizard's `steps` is the ordered list its
  tabs loop over — so a check's step name rendered empty there. The time zone
  comes off `state.tz` for the same reason: `tz` is a schedule-page variable.
- **Nothing in it is redrawn in the browser.** A renderer here would be a
  second copy of `campaign.schedule()`, `drop_state()` and the checks, which
  live in Python and are tested there. `drawOverview()` writes the tab's
  locked count and the line under the heading, and nothing else.
- **It admits when it has gone stale instead.** An edit on another tab can
  move the op date, lock a step or change a drop's words, and the schedule
  below was worked out when the page loaded — so a note appears offering
  **Refresh**. Approving a drop whose words have changed is refused by the
  server anyway (an approval names the hash it was made against), so this is a
  prompt rather than a safety net.
- **Tips, Checks and the drop preview stand down beside it**
  (`data-side="step"`): they are about one step, and the Overview carries its
  own checks and is about no one drop. The mission tools — the ORBAT, the
  maps, the recording, the briefing packs — stay, being about the mission.
- **No server change beyond the context.** `/schedule` is untouched and
  remains the fallback for a browser with no JavaScript.

**A new mission copies `campaign.CARRY_OVER`** from the last one in the
campaign — the details that stay the same week to week. The next date is
suggested four weeks on, one cycle of drops. **With "Success looks like" left
empty, the OPORD uses the primary objectives' success conditions**, as the
library tells mission makers to write it.

### Test campaigns, and what a delete leaves behind

A campaign created with **Test campaign** ticked (`is_test`) exists to be thrown
away, so everything about it is arranged for that:

- **No Kill Tracker campaign** is made or linked, so nothing of it reaches the
  public dashboard.
- **One press deletes it** (no typing the name), and **Delete all test
  campaigns** on the list clears every one.
- **Its drops never post publicly, and it never starts an operation.** Its
  drops go to the mission team channel (`campaign.drop_channel()`), or nowhere
  without one; `start_signups()` refuses outright. Deleting it — or one of its
  missions — deletes those messages from Discord too (`clear_test_posts()`).
- **Make it real** gives it its Kill Tracker campaign then, exactly as if it had
  been created real (`make_cp_campaign_real()`), deletes its test posts and
  clears its `cp_drops`, so every drop is approved again and posts publicly.
- **The ORBATs the planner made for it go too** — those the mission card's
  New or Copy created (`cp_missions.orbat_created`), and only when no other
  mission links them and no operation has ever run on them
  (`cp_created_orbats()`). An ORBAT that was only *linked* is somebody's
  roster and stays. The same holds for deleting one test mission.

**Any** campaign's maps go with it (`delete_cp_campaign()` deletes the campaign
map and the AO maps in the same transaction): the planner made them for that
campaign and they hold its plan. A **real** campaign's ORBATs and posted drops
stay — the drops are the public record, and partner servers may have followed
them — and `delete_campaign()`'s flash says exactly what went and what stayed.

`delete_cp_campaign()` removes the campaign and, by cascade, every AO, mission,
phase, objective, drop and image in it. **Its Kill Tracker campaign goes too
only when it is provably a leftover**: `kt_created`, no recording in it, no
name patterns or imported missions, and no other planner campaign pointing at
it. Anything else is kept and the flash message says so — those numbers are
public, and an admin may have built that campaign by hand. ORBATs, maps and
operations a mission links to (milestones 3–5) are links, not contents: a
delete drops the link and leaves them — except the maps, which the planner
made for the campaign and which go with it, and a test campaign's
planner-made ORBATs (above).

### Drops: approval, posting, holds (`cogs/campaigns.py`)

`campaign.drop_state(row, current_hash, due, now, channel)` is the whole
decision, tested, and both the drops panel and the loop read it — so what the
page says will happen is what the bot does. Its `action` is `post`, `edit`,
`ping` or nothing. `cogs/campaigns.mission_drops()` gathers its inputs (text,
parts, images, `approval_hash()`, due time, the row) for one mission; the web
calls the same function, so there is no second copy of that either.

- **An approval is for one version at one time.** `approve_cp_drop()` stores
  the hash *and* the due time. A change to the words or images, or a moved op
  date (which moves every drop not yet posted), reads as **Needs
  re-approval**. Approving checks the hash the person was looking at
  (`seen_hash`) and refuses a stale one: an approval is a statement about
  words somebody read.
- **Held** = due and not validly approved. The mission team channel is told
  once (`held_pinged_at`), and only for a drop that fell due within
  `HOLD_PING_WINDOW` — a mission planned late should not ping about a WARNO
  that went by two weeks before it existed. It posts the minute it is approved:
  `drop_action()` runs `CampaignsCog.process_mission_id()` straight away rather
  than waiting for the next tick.
- **After posting**, only the words matter. An approved change (`posted_hash`
  ≠ `approved_hash` = current) edits the messages in place; extra parts are
  sent after them, surplus ones deleted, a message deleted in Discord sent
  again. Moving the op date makes a posted WARNO read **Changed** — it names
  the op night — and approving it updates the post.
- **Images ride on the last text part**, grouped by `image_groups()` under ten
  files and the guild's upload limit; any left over follow in messages of their
  own, which `message_ids` records too.
- **A half-posted drop is taken back.** If one part fails, the parts already
  sent are deleted, so the retry posts it whole. A failed post or edit leaves
  that drop alone for `FAILURE_BACKOFF` (30 min, in memory) and tells the team
  channel, rather than failing every minute.
- **One lock** around a mission's processing, so the loop and an approval on
  the website can never post the same drop twice. The loop looks at missions
  dated `LOOK_BACK_DAYS` before to `LOOK_AHEAD_DAYS` after today in campaigns
  that are not archived; an archived campaign posts nothing and its drops
  cannot be approved.
- **Don't post** (`skipped`) is for a drop posted by hand or not wanted at all.
- `allowed_mentions` is none: a drop quotes the mission team's text, which is
  not a licence to ping.
- **A drop in an Announcement channel is published** (`publish_later()`), or
  the servers that Follow it never receive it — Discord does not publish a
  bot's message by itself. Edits reach followers on their own; a part sent new
  during an edit is published too. Publishing runs as a background task,
  **never inside the mission lock**: Discord allows few publishes per channel
  per hour and discord.py waits out that limit rather than failing, which in
  the loop would stall every drop behind it. A failed publish tells the team
  channel. Test campaigns never publish. The settings page warns when the bot
  lacks **Attach Files** in the drop or team channel, since a drop with images
  would otherwise fail only when it falls due.

**Sign-ups at the opening drop.** When the drop named by `orbat_opens` posts
for the first time, `start_signups()` calls `start_operation()` on the
mission's ORBAT with `h_hour_utc()` as the start — exactly `/setup-slots
orbat:`. It does nothing for a test campaign, a mission that already started
one (`cp_missions.operation_id`), or one with no ORBAT, and **never replaces a
running operation**: `operation_running()` counts an active operation as
running until `OPERATION_OVER_AFTER` (6 h) past its start, and forever when it
has none. Any refusal is posted in the team channel; **Start sign-ups now** on
the Schedule page runs the same function later.

### Maps (`utils/campaign.py` → *Maps*, `web/campaigns.py`, `web/maptrace.py`)

A campaign has one **campaign map** with an outline per AO, and each AO a
**detailed map** with a layer per phase fought there. They are ordinary
tactical maps, opened in the existing editor; the planner makes them on the
first **Make the campaign map** / **Make its map** and from then on only keeps
their **layers** in step with its AOs and phases.

- **Layers carry the link, not items.** A map item has no id that survives a
  save; a layer does. So an AO's outline lives on the campaign-map layer
  `cpao<ao id>` and a phase's graphics on the AO-map layer `cpp<phase id>`
  (`ao_layer_id()`, `phase_layer_id()`). Every other layer — the base *Plan*,
  an enemy picture somebody adds — is the planner's to leave alone.
- **A campaign map is masked, and its AOs are drawn on it.** It is made with
  the grey mask on (see *The grey mask* under the tactical maps), and each AO is
  an AO item (`ao: true`) on its layer — a clear hole with a black border.
- **`sync_campaign_map()`**: a new AO gets a layer and a clear box to move onto
  its ground; an AO whose layer has lost its outline gets a box back; a rename
  renames the layer, and the outline's label when it still read the old name; a
  deleted AO's layer goes with what is on it (the outline was the planner's). A
  map from before the mask has its first area or shape per AO layer made the AO
  and the mask switched on once. Run on AO add, rename and delete, and when the
  map is opened.
- **Drawing on the map changes the planner** (`campaign_map_changes()`, applied
  by `web/campaigns.reconcile_campaign_map()`). After a campaign map is saved in
  the editor by somebody who may plan (`after_map_save()`, called by the save
  route), an AO drawn off any AO layer becomes a new AO named by its label and
  moves onto a layer of its own (`place_ao_items()`); a relabelled outline
  renames its AO; a deleted outline deletes its AO — **unless phases are fought
  in it or its own map has drawings** (`is_planner_outline()` tells the copied
  outline from work), when the AO is kept and the very outline that was deleted
  is put back from the map as it stood before the save. An outline whose layer
  was deleted in the editor lands on the first layer; if its label names an AO
  that lost its outline it goes home instead of becoming a twin. The save route
  answers with the map as it now is, and the editor swaps it in, because new
  AOs sit on new layers. A save by somebody who may not plan, or through a share
  link, changes no AO: opening the map from the planner (`open_campaign_map()`)
  reconciles it then.
- **`sync_phase_layers()`**: one layer per phase fought in the AO, across the
  campaign's missions in order, named `Mission · P2 Title`
  (`phase_layer_name()`, fitted to 40 characters), after every other layer so
  later phases draw over earlier ones. A phase that left the AO (deleted,
  moved) loses its layer **only when nothing is drawn on it** — otherwise it is
  kept and the flash says so. Run when phases or the mission name are saved,
  missions are reordered or deleted, an AO is renamed, and the map is opened.
- **`ao_map_doc()`**: a new AO map starts on the campaign map's terrain,
  corners and place-name settings, with the AO's outline copied in on its own
  layer and the mask on — everything outside the AO greyed out, the plan on its
  ground. Its ops traces carry the same look.
- **12 layers per map** (`tacmap.MAX_LAYERS`). Both syncs stop there and say
  which AO or phase did not fit.
- **A sync rewrites the stored document**, which bumps `tac_maps.doc_rev`, so
  somebody with that map open in the editor is told on their next save that
  it changed, and chooses (see *Two people saving one map*).
- **The planner's layers are the planner's in the editor too.** `tacmap.js`
  (`plannerLayer()`) shows a `cpao`/`cpp` layer's name read-only; an AO
  layer's ✕ deletes the layer **with its outline**, which is deleting the AO;
  a phase layer has no ✕. Deleting an AO layer used to move the outline to
  the first layer, where `campaign_map_changes()` — which skipped an AO
  whose layer was missing as "never on this map" — read it as a brand-new AO
  of the same name, and the sync gave the old one its layer back: **the
  duplicated layer**. Now `campaign_map_changes(…, previous)` counts an AO
  whose layer was on the map before the save as deleted, so an outline moved
  by an old editor finds its way home by its name and a deleted one deletes
  the AO (or, if the AO is in use, `reconcile_campaign_map()` puts the layer
  and the outline back).

The campaign page draws the campaign map server-side (`campaign_map_svg()`,
`tacmap.render()`), and a mission's **Maps** card lists the AOs, the ones its
phases are fought in first.

**Ops traces.** `web/maptrace.py` draws, for each phase fought in an AO with a
map, that phase's layer as a PNG, and `document_blocks(…, phase_traces=)` puts
them first among the leaders' pack's and full op file's annexes. The rules are
in `utils/campaign.py`:

- `trace_doc()` — the phase's own layer, every non-phase layer as it was left
  switched, and the other phases hidden;
- `trace_view()` — the box around the phase's graphics and the AO outline,
  with a margin, at 3:2, never closer than 12% of the sheet; **no trace at all
  when the phase's layer is empty**;
- `trace_zoom()` — the terrain level giving about one tile pixel per picture
  pixel at `TRACE_WIDTH` (1600), never deeper than the terrain has.

`tacmap.render()` gained `view` (crop), `tile_zoom`, `tile_href` (where each
tile comes from) and `size`; `visible_tiles()` is the tiles a crop touches. The
SVG is turned into a PNG by **resvg** (`resvg-py`), chosen because it is one
wheel needing nothing from the operating system — the bot is built by
Nixpacks, where cairo is not a given. resvg fetches nothing, so every tile is
read first (from `tac_terrain_tiles` for an uploaded terrain, over HTTP for an
OCAP one, eight at a time) and handed in as a data URI; a tile that cannot be
had is a transparent pixel, as a 404 is on the site. Labels use Bitstream Vera
from reportlab's own fonts folder, so traces and PDFs share a font. resvg is
imported where it is used: without it, packs render without traces.

### After action: the recording (`utils/campaign.py` → *After action*)

The Kill Tracker counts every OCAP recording on its own; the planner's part is
saying which recording **is** a mission, so that recording lands in the
campaign's Kill Tracker campaign and the AAR can quote it.

- **Matched by date.** `recording_candidates()` are the recordings played on
  the op date or the day either side (OCAP dates a recording by its start, and
  a late op crosses midnight), exact dates first, longest first.
- **`auto_recording()` links only the unambiguous one**: exactly one counted
  recording on the op date at least `MIN_OP_SECONDS` (30 min) long. Two on the
  same night is a choice for a person. `cogs/campaigns.match_recordings()` runs
  it each minute for missions of the past `LOOK_BACK_DAYS` with no recording
  and nobody's decision (`cp_missions_awaiting_recording()`), so the link
  appears once the Kill Tracker has counted the file.
- **`set_cp_mission_recording()` keeps the Kill Tracker campaign in step**, in
  one transaction: the new recording is inserted into `kt_campaign_missions`
  (`ocap_kt_added` says whether that insert did it), and the old one is taken
  out **only** if the planner put it there and no other mission of the campaign
  is that recording. One an admin added by hand is never removed. A test
  campaign has no Kill Tracker campaign, so it links but adds nothing;
  `link_cp_campaign_recordings()` adds them on **Make it real**. Deleting a
  mission unlinks first.
- **The card** (`recording_panel()`, `set_recording()`): link one of the
  candidates (anything else is refused), **None of these** (`ocap_manual`, the
  bot stops), **Let the bot choose**, and **Use in the AAR stats**, which writes
  `stats_line()` of `recording_summary()` — players (Zeus excluded, as on the
  dashboard), length, enemy killed, vehicles, friendly deaths, teamkills, the
  longest kill with its weapon, the top killer — into the Stats field.
  `recording_summary()` takes `killtracker.HEADER` as an argument so
  `utils/campaign.py` stays standalone.

### A campaign closes itself

`campaign.campaign_finished()` decides, and the drops loop asks it every
minute (`cogs/campaigns.complete_campaigns()`): an **active** campaign with
every planned mission created and every one **over** — its AAR day gone by and
its recording settled (`mission_over()`: linked, ruled out by a person, or
past `RECORDING_WAIT`, when the bot stops looking) — is marked Complete and the
mission team channel told. Off per server with `cp_settings.auto_complete`.

**A person's decision wins.** `auto_completed_at` is set when the bot closes a
campaign *and* when somebody sets a closed one back to Active
(`update_cp_campaign()`), and the bot only ever looks at campaigns where it is
NULL — so a reopened campaign stays open. Adding a mission clears it: there is
more to play, and the campaign closes again once that is over too.

### The mission's ORBAT

`cp_missions.orbat_id` is a **link**: the ORBAT is built in the existing editor
and stays an ordinary ORBAT. The card on the wizard and the schedule page
(`orbat_panel()`, `set_orbat()`) links one as it is, copies one (through
`web.orbat.duplicate()` — structure, never bookings) or creates a blank one,
and starts on the ORBAT the previous mission used. Unlinking leaves the ORBAT;
deleting the mission leaves it too.

**Who may edit mission ORBATs is a planner setting.** `guild_context()` works
out `may_edit_orbats` with `campaign.may_edit_orbats()`: admins always;
planners when **Who can edit mission ORBATs** is *admins and planners* (the
default — otherwise TFP's Unit Leaders could plan a mission but not its roster);
and anybody holding one of the **Also allow these roles**. `orbat_context()`
then lets such a member into an ORBAT when `database.cp_orbat_linked()` finds a
planner mission on this guild linking it, and the **ORBATs** tab and list open
to them showing only those ORBATs (`cp_linked_orbat_ids()`), with no create
form. Somebody given a role but no planner access gets there through that tab.
Copy and delete stay admin-only (`orbat_duplicate`, `orbat_delete` call
`require_orbat_admin`), the editor hides them, and its crumb points at the
campaigns instead of the admin-only ORBAT list. A copy made in the editor would
not be linked to anything, so a planner could not even open it — copying for a
mission is the card's job.

### Briefing packs

What a pack contains is `campaign.document_blocks()` — a list of blocks (h1,
h2, kv, p, note, list, table, img, break) — tested like the rest of the rules.
`web/exports.py` has two renderers of that list, `render_pdf()` (reportlab)
and `render_docx()` (python-docx), so the two files cannot say different
things. `build_export()` gathers the inputs (phases, AO names, the linked
ORBAT's structure and nets, the campaign's images) and renders in a thread,
because laying out a PDF is CPU work on the bot's own event loop.

| Pack | Contents |
|---|---|
| Player briefing | Warning order, situation, intelligence, OPORD (phases with primary and secondary objectives) |
| Leaders' pack | That, plus the FragO: timings, tasks, actions on, loadout, medical, the ORBAT, the ops traces as annexes |
| Full op file | That, plus complications in the phases, the back-schedule and the after action |

Rules the renderers rely on, not decide: **private fields are never in a pack**
(the field's own help promises "never posted or exported"); **complications
only in the full op file** — they are Zeus's plan, and players get the
INTSUM's hints; **times in the op's zone**, with the guild's beside them in the
timings when they differ. Branding is `WEB_BRAND` and the logo in
`web/static/` (an SVG logo is skipped — neither library draws SVG).

Three layout details that were got wrong first and are worth keeping:

- **The PDF font is Bitstream Vera**, the TrueType that ships inside reportlab,
  registered as a family so bold and italic work. Characters it has no glyph
  for are swapped for a stand-in (`_FALLBACKS`: → becomes ->) or dropped
  (emoji), rather than printing black boxes.
- **Headings keep with what follows** — `keepWithNext` in the PDF,
  `keep_with_next` in Word — or "Ground" ends a page with its text on the next.
- **A page break in Word is "page break before" on the next heading**, never
  an empty paragraph: a break paragraph landing at the foot of a full page is
  what makes Word show a blank page. Table widths need `autofit = False` and
  the column widths set as well as the cells', or LibreOffice and Google Docs
  split every table in half.

### The objectives library

Seeded once per guild, the first time anybody opens it
(`ensure_objective_library()`, under `cp_settings.library_seeded` so an admin
who empties it gets an empty one, not the seed again). Picking an entry copies
its wording into the phase, so editing either never changes the other. The
picker lifts the entries the phase's existing picks pair with to the top
(`campaign.pairing_ids()` — forgiving prefix matching on names, bracketed
acronyms and initials, so "BDA", "OP" and "IED" find their entries).
**The checks warn** when a phase with objectives has other than one primary,
or no secondary or complication — the library's own rule, as a suggestion.

---
