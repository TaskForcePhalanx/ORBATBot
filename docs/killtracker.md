<!-- Part of ORBATBot's documentation. The rules every session needs
     are in ../CLAUDE.md, which says when to read this file. -->

## The Kill Tracker (`utils/killtracker.py`, `utils/ocap.py`, `cogs/killtracker.py`, `web/killtracker.py`)

The Kill Tracker is the unit's public scoreboard: per-player kills, deaths, distance, shots and so on per Arma mission, read out of **OCAP** recordings. It began as a static dashboard (`TFP_KillTracker`, GitHub Pages) over a Google Sheet that a Google Apps Script (`ImportScript`) filled by hand. It now runs here, and **`utils/killtracker.py` is a port of that script that produces the same rows in the same columns** (`HEADER`: the sheet's 35, then two the bot added — the weapon of each player's longest kill, on foot and in a vehicle) so the dashboard reads this bot's CSV as it read the sheet. A mission counted before a column existed is padded with empty cells when the CSV is served (`csv_from_stored()`), until it is counted again.

**Nobody uploads anything.** `cogs/killtracker.py` asks the OCAP server `GET /api/v1/operations` every 10 minutes. Each entry has an `id` that only grows, a `filename`, a `conversionStatus` (`completed` = finished and exported), a `tag` and the server's own kill counts. `ocap.plan()` picks the recordings that are due — finished, and never seen / counted under older rules / failed and past their back-off — at most 8 per pass, **never-tried ones first** (retries come due over and over, and oldest-first would let a few stuck ones take every place), so the first pass over sixty recordings takes a few polls. Each is streamed to a temp file from `GET /data/<filename>.json.gz`, counted in a thread, stored in `kt_missions`, and the file deleted.

**The download keeps the bytes as the server sent them** (`aiohttp` with `auto_decompress=False`, `Accept-Encoding: gzip`). The OCAP server answers a `.json.gz` with `Content-Encoding: gzip`, which is why a browser shows plain JSON; a client that honours it stores *unzipped* JSON under a `.json.gz` name and counts the unzipped size against the limit — the first deploy failed 8 of 8 recordings that way (`Not a gzipped file`, and `larger than 200 MB` for the two biggest). `open_recording()` therefore reads the first two bytes (`1f 8b`) rather than trusting the file name, and the limit (1 GB) is on bytes as sent.

| Piece | What it does |
|---|---|
| `utils/killtracker.py` | `read_recording()` streams the file and `compute()` counts it. Pure; imports only the standard library, so it is what the tests pin |
| `utils/ocap.py` | The list entry, the URL, the download, `plan()` and the back-off. `aiohttp` only where the network is touched |
| `cogs/killtracker.py` | The loop and `check()`. No slash commands — there is nothing for a member to type. **One line per pass in the log** (`ℹ️ killtracker: N on the server, …`) so a quiet log means a stopped loop; one recording is bounded by `IMPORT_TIMEOUT`, and an unexpected exception restarts the loop (`@poll.error`) |
| `web/killtracker.py` | `GET /kt/api/stats.csv` and `GET /kt/api/missions` (which says why a failed read failed). **No session, `Access-Control-Allow-Origin: *`, cached a minute** — the dashboard is a static page on another origin |

### Why it streams

A recording is 50–850 MB of JSON once unzipped (a synthetic 851 MB one, 660 units × 17 430 frames, reads in 17 s at a **115 MB** peak), nearly all of it one `positions` entry per entity per frame. `json.loads` on it peaks at **0.5–1 GB**. `read_recording()` instead reads the top-level object member by member and the `entities` array **one entity at a time** (`_Reader`: `raw_decode` on a growing buffer, in C), reducing each to an `Entity` of a few `array` columns — `veh` (vehicle id per frame, for every unit, because a *victim* may be an AI crewman), and `alive`/`x`/`y` for players only. Vehicles keep no positions. Peak memory is about 40 MB. `events` is kept whole (a few thousand rows). The reader doesn't care about key order and treats a number touching the buffer edge as unfinished; a truncated file raises, it never returns half a mission.

### The rules

They are the Apps Script's, unchanged, and `compute()` documents each in place. The ones that have been wrong before:

* **Crew kills are kills, not distances.** When a player downs a helicopter, OCAP logs each crewman as an infantry kill at the helicopter's distance — 1944 m with a Stinger. Such a kill still counts, but doesn't feed longest/avg kill distance when the victim sat in a vehicle (this frame or the one before — the Stinger can kill a passenger a moment before the helicopter is logged as destroyed) or died in the same frame as a vehicle kill by the same player. The 1500 m on-foot ceiling stays as a net for launchers logged with the wrong weapon.
* **End-of-mission teamkills** (the last 5 minutes, close-range ones in the last 7, bursts of three in the last 10) aren't counted: players celebrate by shooting each other.
* **A crash isn't a suicide.** A self-kill within a frame of the player's `disconnected` event is the engine's, looked up by *name* because a reconnect gets a new entity id.
* **Distances nobody believes** (rules version 2). A kill still counts, and its distance does not feed longest/avg: **indirect fire** (`_INDIRECT`: mortars, howitzers, Mk6, Sochor, Sholef, Scorcher, MLRS…) because 1.5 km with a mortar is the weapon working; a **pistol** (`_PISTOL`) beyond `MAX_PISTOL_KILL_DIST` (300 m), which is a weapon OCAP attributed wrongly; and anything an admin struck on the *Longest kills* page. These names are guesses at how Arma spells its weapons — extend the two patterns when a new one slips through, and bump `RULES_VERSION`. In a vehicle, nothing else caps the distance: an in-vehicle kill was the one path with no ceiling at all before this.
* **Phantom shots**: OCAP v5 logs shots whose target is thousands of metres away; on-foot shots beyond 800 m are dropped, in-vehicle ones never are. Shots are deduplicated by `(frame, target)` and hits by `(shooter, victim, frame)`.
* **Names**: an entity without a name takes the first per-frame name; one still unnamed takes the name of the **only** named player sharing its group and role; an unnamed one with under an hour of playtime is dropped. Zeus (group or role containing "zeus") has every combat stat zeroed.

JavaScript rounds halves **up** (`toFixed`, `Math.round`); Python rounds them to even. `to_fixed()` and `js_round()` do what the script did, because the rows are compared against the sheet's.

### Changing a rule

**Bump `RULES_VERSION`.** Every stored mission carries the version it was counted with, and the loop counts again — from the file, which stays on the OCAP server — any mission below the current one. That replaces "re-import every mission by hand". A re-count that fails leaves the old rows in place.

### Checked against the original

The port was run cell by cell against the original `ImportScript` (under Node with the Apps Script services stubbed) on three real recordings — 10, 8 and 8 rows, **0 differences** — and the dashboard's own CSV parser reads the output. That needs the recordings, which are 2–3 MB each and not committed, so it is not a test; do it again when a rule changes, since the script is the reference for everything the tests don't cover.

### The admin pages

**📊 Kill Tracker** in the tab bar, **server admins only for the whole area** (`kt_context()` in `web/app.py`; `nav.build()` leaves the group out for anyone else). It changes what the public answers say, and nothing it does touches the counted statistics:

| Page | What it does |
|---|---|
| Missions | Give missions clean names (`kt_missions.name_override`) — **every name on the page in one form, saved with one press** (`plan_name_edits()`: only changed rows are written, and none if one name is invalid). Empty = OCAP's name again |
| Campaigns | Create, edit, delete campaigns; pick missions by recording, optionally add name patterns |
| Players | **Missions per player** and **which Discord member a player is** — see below. The list is filtered by the mapped member's roles, by default Member / Veteran / Recruit / Cadet (`default_roles()`, matched by name, ignoring case); a player with no member to read roles from stays visible while *not mapped yet* is ticked, because hiding them would hide exactly the ones that still need mapping. An unmapped player gets up to three suggested members as one-press buttons (`killtracker.suggest_members()`: tags and ranks stripped, containment or a 0.8 `difflib` ratio, members already mapped to someone else left out); they come from the bot's member cache, so a guild larger than the cache gets fewer suggestions and the form still takes any id |
| Link names | Link names that are one player and pick the one shown (`kt_aliases`); show another / unlink. Ticks survive a search (the search is a POST that redirects with the ticked names in the address, so the CSRF token never lands in a URL); the panel above the list suggests the name with the most missions, or adds the ticked names to a player who is already linked |
| Longest kills | The farthest **infantry** kills of all missions (100 of them; a mission or player filter shows all of theirs), farthest first, with a tick to **strike** the unbelievable ones; a struck kill leaves the list at once (the page overlays `kt_kill_exclusions` on the stored list) and shows under *Struck kills*, where unticking puts it back (`kt_kill_exclusions`). A struck kill still counts, only its distance leaves the statistics; the mission is counted again by the import loop within minutes (`rules_version = 0`) and the decision survives every later re-count. `compute()` hands back `far` for this; `far_listing()` / `plan_strikes()` are the pure parts (a form cannot strike a kill it was not shown) |
| Units | Set a player's unit where the dashboard's guess is wrong (`kt_unit_overrides`), one form, only changed rows written |
| Import | Take over the old work once: mission names from the sheet's CSV, `campaigns.json`, `player_aliases.json`, `unit_overrides.json` |

Each of them is a function in `web/killtracker.py` (what it does, in one place) over `utils/killtracker.py` (the pure rules: `clean_name`, `plan_name_import`, `link_names`, `campaigns_document`, `parse_*_json`, tested). **Import order matters:** mission names first, because `campaigns.json` names its missions the way the sheet did and they are matched against the names *as shown*. Anything already set is kept (except mission names, with the checkbox), and a campaign mission name that matches no recording stays on the campaign as `extra_missions`.

The public side serves them in the shape of the dashboard's own files — `/kt/api/campaigns.json`, `/kt/api/player_aliases.json`, `/kt/api/unit_overrides.json` — cached under `kt_generation()`, which includes names, campaigns, aliases and units, so an edit shows within the cache window rather than at the next import. **The CSV keeps the recorded player names**: the dashboard applies the aliases itself, as it always has.

### Missions per player, and who is who on Discord

**Every recording counts as a mission** — no duration, campaign or test filter. A test recording is deleted on the OCAP server by hand; the bot does not try to detect one. `killtracker.player_missions()` counts the distinct recordings a player appears in, with linked names as one player (two names of one person in the same recording are one mission). It is public as `GET /kt/api/players.json`, cached with the CSV.

The mapping to a Discord member (`kt_player_members`, migration 5) is **admin-page only and deliberately not in the public API**: that answer has no login, and tying an in-game name to a Discord account is not something to publish. It is stored against *recorded* names, and a linked player's mapping is written to every name in the group and read from any of them, so linking, unlinking or showing another name never orphans it. The form takes an id, a mention or an exact member name.

### Promotion: Cadet → Member after N missions (`utils/promotion.py`)

Built on the two things above: the mission count and the Discord mapping. **Off until an admin turns it on** (`kt_promotion_settings`, migration 6, one row per guild: the two roles by id, the number of missions, the admin channel, `enabled`, `auto`). `promotion.candidates()` is the whole rule — mapped, holds the from-role, lacks the to-role, enough missions, not already promoted or passed over (`kt_promotions`). A member mapped to several players has their missions added up.

`cogs/killtracker.py` runs it after every poll pass: with `auto` off it posts **one message per person** to the admin channel, once (`notified`), with **Approve** / **Reject** buttons (`PromotionButton`, a `DynamicItem` so the member's id lives in the `custom_id` and nothing is re-registered after a restart; admins only; Approve re-checks that the person is still due; at most `MAX_ASKS_PER_PASS` per pass) — the same can be done on **Server → Promotion**; with `auto` on it swaps the roles itself and posts what it did. The page and the loop both go through `due_for_promotion()` / `promote_members()`, so a promotion is the same whoever starts it. Needs Manage Roles and a bot role above the target role; if not, the problem is posted once and the member is left alone. Turning `auto` on promotes everyone who already qualifies at the next pass — the page says so.

### The dashboard reads only the bot

`dashboard/tracker.js` reads `/kt/api/stats.csv` and the three JSON files from the bot and **nothing from a Google Sheet**. The JSON files in `dashboard/` are only a fallback when the bot cannot be reached (`botJson()`): an *empty* answer is an answer, so deleting every link on the admin page does not bring the old file back. Old links with `?source=bot` still work; the parameter is ignored.

Rules versions 2 and 3 deliberately **differ from the ImportScript** (indirect fire, pistols and struck kills no longer feed the distance), so the cell-by-cell comparison in *Checked against the original* only holds for a rule the script also has.

* The dashboard is hosted on GitHub Pages (see Deployment). It could also be served by the bot itself (one origin); it is a static page, so that is a copy, not a rewrite.
* The old `armatfp/TFP_KillTracker` repo redirects here and is archived; release number and date live in `dashboard/tracker.js` (`VERSION`, `RELEASE_DATE`) and must be bumped together with `dashboard/CHANGELOG.md`.

---
