# Changelog

All notable changes to TFP Kill Tracker are documented here.

---

## Unreleased

### Added
- **Multi Killer** (Hall of Fame) — the player with the biggest multikill, with its mission and time on hover. The profile no longer shows the multikill's position either; OCAP has it
- **CQC Enthusiast** (Hall of Fame) — the shortest average on-foot kill distance. Needs at least 20 on-foot kills, so a few point-blank kills cannot win it

### Fixed
- **The Hall of Fame stays in one row** with its eight cards, like the Hall of Shame, instead of the last card wrapping alone onto a second row

---

## v1.039 — 2026-10-17 (latest)

### Changed
- **All weapons are grouped by model** — not only the M249. Camo, attachment and mod versions are one entry (`M4A1 Block II` and `M4A1 PIP` are *M4A1*; `HK416 D14.5` and `HK416N (GL)` are *HK416*; `M249E2` and `M249 PIP` are *M249*), so the weapon leaderboard shows guns, not loadouts. A revision stays its own gun: M4A1 and M4A3 are two entries. Vehicle weapons are left as they are. Renames are in `WEAPON_RENAMES`

---

## v1.037 — 2026-10-17

### Changed
- **More weapons merged** — every M249 (`M249E2`, `M249 PIP`, any rail kit) is one weapon, and the PIP mod's upgraded guns (`M4A1 PIP`) count as the gun they upgrade

---

## v1.036 — 2026-10-17

### Changed
- **Weapon names** — one weapon is now one entry. Camo, stock and rail variants (`M4A1 Block II (Desert)` / `(SOPMOD Stock)`) were counted separately in *Top Weapons*; they are merged, and the few names that said nothing (a plain `Rifle` that is the SCAR-H) are renamed. The list is `WEAPON_RENAMES` in `tracker.js`

---

## v1.035 — 2026-10-17

### Added
- **Multikill** — the most enemies a player killed inside any 10 seconds, as a column in the infantry table and the per-mission breakdown (the best one of a player's missions) and on the player page. The crew of a shot-down vehicle does not count. Missions the bot has not counted again yet show a dash

---

## v1.034 — 2026-10-17

### Added
- **Downed** — how often a player went unconscious (ACE), as a column next to *Deaths* in the infantry table and the per-mission breakdown, and as *Times Unconscious* on the player page. The bot counts it from the recordings (two new CSV columns); missions it has not counted again yet show a dash

---

## v1.033 — 2026-10-11

### Fixed
- **Wrong weapon next to the longest kill** — the Pro Sniper card and the player page showed the player's most-used weapon of that mission (a rifle at 1216 m). The bot now records the weapon the longest kill was made with (two new CSV columns, *Longest Kill Weapon*), and that is what is shown. Missions the bot has not counted again yet show no weapon rather than a wrong one

---

## v1.031 — 2026-10-11

### Changed
- **The dashboard reads only the bot.** The numbers, campaigns, linked names and unit corrections all come from `bot.taskforcephalanx.com/kt/api/`; nothing reads the Google Sheet any more, and `?source=bot` is no longer needed (it is ignored). The JSON files next to the page are used only when the bot cannot be reached

- **Longest kills page (bot admin):** a struck kill leaves the list at once and the next-farthest kill fills its place; struck kills are listed separately, where they can be put back

### Removed
- The Google Sheet as a data source, and the `[BOT DATA]` tab-title prefix that marked the other one

---

## v1.030 — 2026-10-11

### Changed
- **Longest kills page (bot admin)** lists infantry kills only — a vehicle's long shots are real — offers every player's own longest kill (the number on the player card), and can be filtered by mission or player

---

## v1.029 — 2026-10-11

### Fixed
- **Unbelievable long kills** — indirect fire (mortars, artillery) and pistols beyond 300 m no longer feed the longest/average kill distance (they still count as kills). Until now an in-vehicle kill had no distance ceiling at all. All missions are counted again by the bot automatically

### Added
- **Admin page "Longest kills"** (bot, 📊 Kill Tracker): the farthest kills of all missions with a tick to strike the ones that cannot be true

---

## v1.028 — 2026-10-11

### Removed
- **Orange data-source badge** in the corner of `?source=bot`; the tab title still starts with `[BOT DATA]`

---

## v1.027 — 2026-10-11

### Changed
- **Moved into the ORBATBot repository** (`dashboard/`), published from there by GitHub Pages at `https://armatfp.github.io/ORBATBot/`. The code is unchanged; the old `TFP_KillTracker` repository only redirects here. The `ImportScript` is now `scripts/legacy/ImportScript`

### Added
- **The numbers are now counted by the ORBATBot itself**: it reads every finished recording from the OCAP server automatically (no upload, no Google Drive) and serves the same 35-column CSV. Mission names, campaigns, linked names and unit corrections are edited on the bot's admin pages (Kill Tracker tab) and served as `campaigns.json`, `player_aliases.json` and `unit_overrides.json`
- **`?source=bot`** — loads the same 35-column rows from the bot (`/kt/api/stats.csv`, counted straight from the OCAP server) instead of the Google Sheet, with a badge so the two can't be confused. For comparing the numbers side by side before the sheet is switched off

### Fixed
- **Longest kill from destroyed vehicles** — crew members of a destroyed vehicle (victim sitting in a vehicle when killed, or killed in the same frame as a vehicle kill by the same player, e.g. a helicopter shot down with a Stinger) still count as kills but no longer feed longest/avg kill distance. Re-import missions to apply

### Removed
- **"Wrong unit?" buttons** on the player modal and career page (the copy-to-clipboard unit correction helper); unit overrides stay in `unit_overrides.json`

### Added
- **Campaign page** — click the 📊 button on a campaign pill (or right-click the pill, or open `?campaign=<name>`) to get a one-page overview of a campaign: totals (missions, players, kills, deaths, K/D, vehicles destroyed, TK, accuracy, longest kill, time, distance), highlights, per-mission table, per-player table and top weapons. Zeus rows are excluded; filters don't apply.

---

## v1.026 — 2026-06-27

### Fixed
- **Implausible longest kill** — on-foot kills farther than 1500 m (e.g. launcher/vehicle kills logged with the wrong weapon) no longer count towards longest/avg kill distance. The ImportScript skips them on import (re-import to fix avg), and the dashboard ignores such longest-kill values in existing sheet data

### Fixed (ImportScript — OCAP v5 compatibility)
- **Empty player names** — OCAP v5 sometimes leaves `entity.name` empty while storing the correct name in per-frame position data (`positions[i][4]`). The ImportScript now falls back to the first non-empty frame name, so players no longer appear as "Unknown &lt;id&gt;"
- **Duplicate shot entries** — OCAP v5 logs multiple `framesFired` entries per frame for the same shot (raycast duplicates with identical target positions, typically 2–3× inflation). Shots are now deduplicated by `(frame, targetPosition)`, preserving legitimate multi-bullet bursts on the same frame while removing duplicates
- **Duplicate hit events** — OCAP v5 generates duplicate hit events for the same shooter–victim pair on the same frame (penetration, ricochet, splash damage artifacts — up to 49 hits from one shot). Hits are now deduplicated by `(shooter, victim, frame)`, reducing total hit counts by ~75%
- **Vehicle shot distance filter** — the 800 m phantom-shot filter (which removes OCAP v5 raycast artifacts) was incorrectly applied to in-vehicle shots, discarding legitimate long-range vehicle weapon engagements (Bofors, 2-pounder, Vickers HMG). The filter now only applies to on-foot shots
- **Incorrect `isUnknown` flag** — entities whose name was resolved from per-frame data were still marked as unknown (`isUnknown: !e.name` instead of `!resolvedName`), causing the group+role rename logic to incorrectly merge them with a different player sharing the same squad slot — creating duplicate rows with inflated stats (double distance, split kills)

### Fixed (Dashboard)
- **Joint Op detection for new filename format** — `isJointOp()` now also matches the new OCAP filename format `_YYYYMMDD_` (e.g. `East_Africa_20260627_213936.json.gz`), in addition to the old `YYYY_MM_DD` format. Without this fix, all missions with the new filename format were incorrectly classified as Regular Ops

---

## v1.025 — 2026-04-20

### Added
- **Campaign grouping** — missions can be grouped into named campaigns via `campaigns.json`. Each campaign supports an explicit `missions` list (manual assignment) and/or `patterns` (substring match for auto-catching future missions). A **Campaigns** pill selector appears below the Missions filter; clicking a campaign pill selects all its missions at once.
- **Campaign medals** — players automatically earn a ribbon medal for every campaign they participated in (at least one mission played). Medals appear as procedurally generated ribbon bars in the **Campaign Medals** section at the top of the career page and player modal. Each campaign always produces the same unique ribbon (Alea PRNG seeded by campaign name, 73-colour US military palette, symmetric stripe pattern + fabric grille + gold frame). Adapted from [medal-bar-generator](https://github.com/Mchl/medal-bar-generator) (MIT).

### Fixed
- **`_comment` key visible as campaign pill** — JSON comment keys (starting with `_`) are now filtered out from the campaign pill list

---

## v1.024 — 2026-03-31

### Added
- **Weapon ammo merging** — weapon entries that share the same base weapon but differ only by ammunition type are now collapsed into one entry throughout the site (weapon leaderboard, career modal weapon breakdown, award cards). The `[AmmoType]` suffix (e.g. `[M62 Tracer]`, `[M80A1 EPR]`) is stripped at aggregation time, so `M240G [M62 Tracer]` and `M240G [M80A1 EPR]` both count as `M240G`. Weapons without an ammo suffix are unaffected.

---

## v1.023 — 2026-03-31

### Added
- **Player name merging** — variant names for the same player are now automatically collapsed into one entry throughout the site. Three-pass strategy:
  1. **Exact normalisation** — removes spaces inside `[brackets]`, normalises `]Name` → `] Name`, `S.Fig` → `S. Fig`, and treats `_` as a space separator so `[SR]_Gronk` = `[SR] Gronk`
  2. **Suffix matching** — if a longer name ends with ` callsign` or `]callsign` (min 4 chars), it merges into the bare callsign. Handles `[2nd USC]Fre3ky`, `HMC.P Fre3ky`, `[57th] Fre3ky` → all become `Fre3ky`
  3. **`player_aliases.json`** — explicit overrides for cases the above can't resolve (rank variants, completely different names)
- **"Also known as" on career page** — when a player has merged aliases, a dimmed italic line listing all absorbed name variants appears below the subtitle
- **Player pills deduplicated** — the Players filter now shows one pill per canonical name instead of one per raw name variant

### Fixed
- **Unit overrides updated** — Salt, DAN → PXG; 1LT. Evil, WC. Picker, SPC. Jesus Juice → Unknown

---

## v1.022 — 2026-03-31

### Fixed
- **Zeus detection in ImportScript: `Co Zeus` and `Zeus@Zeus` not flagged** — the old check split on `@` and used `startsWith("zeus")`, missing roles where "zeus" is not the first word. Changed to `role.includes("zeus")`. Also added fallback to read the role from the OCAP `positions` frame array (index 6) when `entity.role` is empty, covering older OCAP captures that only store role inside frame data.
- **Zeus filter dropdown not working for role-based Zeuses** — `tracker.js` was only checking `Group === "zeus"` in three places (Players dropdown filter, squad co-occurrence, Passenger Princess shame card). Players with Zeus in the `Role` column (`Co Zeus`, `Zeus@Zeus`) were not excluded. Added top-level `rowIsZeus(r)` helper that checks both Group and Role with `includes("zeus")` and used it consistently across all three sites.
- **`rowIsZeus is not defined` runtime error** — `rowIsZeus` was incorrectly scoped inside `classifyPlayerUnits`; moved to module top level so `filterChanged` and the shame card section can access it.

---

## v1.021 — 2026-03-30

### Added
- **Hits/Kill in Mission History** — new sortable column in the Mission History table showing total hits dealt divided by total kills for each mission

---

## v1.020 — 2026-03-22

### Added
- **Hits Dealt tracking** — ImportScript now records outgoing hits per player (on foot and in vehicle), mirroring the existing Hits Taken columns. New sheet columns: `Hits Dealt (On Foot)`, `Hits Dealt (In Vehicle)`.
- **Accuracy stat** — derived from Hits Dealt / Shots fired; shown as a percentage in both the infantry and vehicle stats tables (e.g. `14.3%`). Also computed per-mission in the player career modal.

---

## v1.019 — 2026-03-22

### Fixed
- **EOM TK suppression: hard cutoff corrected to 5 min** — `EOM_WINDOW_S` was accidentally set to 120 s (2 min); corrected to 300 s (5 min) as intended
- **EOM TK suppression: burst check now bounded to end of mission** — the burst heuristic (3+ TKs in 60 s) previously had no time constraint, meaning a bad round mid-mission could falsely suppress legitimate TKs; the check now only activates within the last 10 min (`EOM_BURST_MAX_FROMEND_S = 600`)
- **EOM range window updated to 7 min** — extended close-range window raised from 180 s to 420 s to stay proportional now that the hard cutoff is 5 min

---

## v1.018 — 2026-03-21

### Added
- **Version number** — `VERSION` constant in `tracker.js`; displayed in the site footer next to the release date (`v1.018 · 21 Mar 2026`); version tags added to all changelog entries going forward
- **End-of-mission TK suppression** — ImportScript now detects and ignores teamkills that occur during end-of-mission celebrations (players shooting each other / dropping grenades after the mission is won). Three overlapping heuristics:
  - **Time window** — any TK in the last **5 minutes** before `endMission` is suppressed unconditionally
  - **Close-range extended window** — TKs within **15 m** (grenade / pistol range) in the last **7 minutes** are suppressed
  - **Burst detection** — if **3 or more** TKs occur within any **60-second rolling window** and within the last **10 minutes**, all TKs in that cluster are suppressed (catches group grenade throws earlier in the timeline)
- **Vehicle Kills section** — dedicated table below Mission History listing every vehicle destruction per mission; columns: vehicle, killer, weapon; linked from the sticky section nav bar
- **Vehicle kills in Mission History** — per-mission vehicle kill count shown alongside kills/deaths in the mission table
- **Unknown entity IDs** — unnamed OCAP entities now shown as "Unknown 257" (entity ID appended) to help cross-reference replays

### Changed
- **ImportScript: single entry point** — `importOCAP()` removed; `reimportOCAP()` is now the only function to run; it deletes existing rows for the file before writing, making every run idempotent
- **ImportScript: sheet formatting** — `player_stats` sheet has column alignment and table formatting applied automatically after each import
- **Analytics** — replaced Google Analytics with Microsoft Clarity

### Fixed
- **Distance Run broken after column insertion** — adding the Vehicle Kills (JSON) column shifted column indices, zeroing out Distance Run; column order in ImportScript now inserts Vehicle Kills (JSON) without disturbing Distance Run
- **Vehicle Kills (JSON) quotes stripped** — CSV parser was removing quotes around the JSON blob; fixed quoting so the field round-trips correctly
- **Unknown → player rename: trailing spaces** — group/role strings from OCAP sometimes include trailing spaces; both sides are now trimmed before the key is built
- **Unknown → player rename: duplicate candidates** — a player who disconnects and reconnects gets two entities with identical group+role, causing `candidates.length > 1` and blocking the rename; candidates are now deduplicated by name before the uniqueness check
- **Zeus detection in ImportScript** — roles formatted as `Rifleman@Zeus` are now correctly identified as Zeus; previously only `Zeus` as the full role string was caught
- **Unknown players with < 1 h playtime excluded** — short-lived unknown entities (reconnect ghosts, JIP entities) are no longer written to the sheet

---

## 2026-03-20 (latest)

### Fixed
- **Nav bar in embedded iframes** — switched from `position: fixed` to `position: sticky` so the nav bar stays visible when the tracker is embedded in a CMS iframe (e.g. Squarespace); `position: fixed` inside an iframe binds to the iframe document top, not the browser viewport, causing the bar to scroll off-screen when the parent page scrolled
- **K/D Trend & Compare charts blank on first open** — charts now re-render when their section is opened via the nav bar; previously they could render into a hidden container at load time and appear blank when the section was expanded
- **Nav bar scroll offset** — replaced `scrollIntoView` with a manual `window.scrollTo` + `getBoundingClientRect` calculation so the section header lands below the nav bar rather than behind it
- **Spacing below nav bar** — added 16 px gap between the nav bar and the first content cards

---

## 2026-03-19

### Added
- **Unit Leaderboard** — new table below the vehicle stats that aggregates all player stats by unit (2nd USC, CNTO, PXG, TFP, Unknown); columns: players, kills, deaths, K/D, TK, vehicle kills, kills/player, avg K/D, missions, distance run, time played; all columns sortable; right-click on Kills, Deaths, Veh Kills, Kills/Player, Dist Run, and Time Played toggles per-mission averages (same `~/m` system as the infantry/vehicle tables)
- **Mission History** — chronological table of every mission with date, world, player count, total kills/deaths, K/D, TK, and top killer
- **Weapon Leaderboard** — aggregates weapon kill JSON across all filtered players; shows total kills, user count, top user, and kill share %; top 50 weapons shown
- **Map Stats** — kills, deaths, K/D, TK, and mission/player counts grouped by world/map
- **Role Leaderboard** — kills, deaths, K/D, and avg K/D grouped by each player's primary role (Rifleman, Medic, Squad Leader, etc.)
- **Attendance Tracker** — per-player mission count, attendance %, first seen, last seen, and avg time per mission
- **K/D Trend chart** — SVG line chart of K/D ratio over time; switchable between overall, per unit, or any individual player via dropdown; yellow dashed line marks K/D = 1.0; dots show exact values on hover
- **Player Comparison** — pick any two players from dropdowns for a side-by-side stat table across all infantry and vehicle stats; winning value highlighted in green
- **Collapsible sections** — every section header is now a clickable toggle (▼/▶); the 8 new sections default to collapsed, Infantry and Vehicle tables default to open; open/closed state persists in `localStorage`

### Added
- **Sticky section nav bar** — dark tab strip that sticks to the top of the viewport; one button per section (Infantry, Vehicle, Units, Missions, Weapons, Maps, Roles, Attendance, K/D Trend, Compare); clicking a tab expands the section if collapsed and smooth-scrolls to it; bar hides in career/player view
- **Back-to-top button** — floating circular button (bottom-right) that appears after scrolling 400 px; smooth-scrolls back to the top
- **Active filter badge** — amber banner below the filter panel that appears whenever any filter is active; shows player count vs total and labelled pills per active filter; includes a "× Clear" button to reset all filters at once
- **Weapon search input** — text input above the Weapon Leaderboard table; live-filters rows by weapon name as you type

### Changed
- **Weapon Leaderboard** — right-click the Total Kills column header or any cell to toggle between total kills and kills/user (per-user average); active mode shows an amber `~/u` prefix in the header and highlights values in amber; sorting ranks by kills/user when the toggle is active
- **Right-click hints** — eligible column headers in all tables (Infantry, Vehicle, Unit, Weapon) now show a native tooltip on hover explaining the right-click action

### Fixed
- K/D Trend chart Y-axis generating 40+ grid labels at high K/D values (e.g. max K/D ≈ 20 with 0.5 step); replaced with a "nice number" algorithm targeting ~6 grid lines regardless of data range

---

## 2026-03-18 (latest)

### Added
- **Distance Run (km) stat** — tracked per player from OCAP movement data; teleport steps (>100 m jumps) filtered out to avoid GPS drift. Shown in infantry table, player modal, and career page
- **Ultra Runner award** (Hall of Fame) — player with the most total distance run
- **Passenger Princess award** (Hall of Shame) — player with the least distance run; requires ≥1h30m playtime, excludes Zeus players, and only awards when a real value (>0) exists
- **Per-mission avg toggle on table columns** — right-click any raw-count column header or cell (Kills, Deaths, Shots, Hits Taken, TK, Suicides, Dist Run, Veh Kills) to toggle between totals and per-mission averages; active column shows an amber `~/m` prefix in the header; sorting also ranks by the average value when avg mode is active
- **Hover tooltip on Hall of Fame/Shame stats** — hovering the stat line on any award card shows the per-mission average and mission count

### Changed
- Hall of Fame and Hall of Shame award cards no longer show emoji icons
- Hall of Shame row forced to a single line on desktop (no wrapping at 1080p+)

### Fixed
- Distance Run column not populating after import — migration code was overwriting the `Time Played (s)` header; fixed by placing `Distance Run (km)` last in the ImportScript schema so it always appends safely

---

## 2026-03-18

### Added
- **Unit classification** — players are automatically assigned to 2nd USC, CNTO, PXG, or TFP using squad co-occurrence analysis; classification requires ≥99% dominant co-occurrence weight to avoid misclassifying players who guest in other squads
- **`unit_overrides.json`** — repo-level file for manual unit corrections applied on top of auto-classification; includes 8 initial overrides
- **Unit filter pills** — pill buttons in the filter panel (styled with unit colours) auto-narrow the player list on selection; badge shown on player modal and career page
- **"Wrong unit?" selector** — in player modal and career page; copies the correction JSON for reporting
- **Always On Duty award** — Hall of Fame card for the player with the most time played
- **Background colours** on all Hall of Fame and Hall of Shame award cards

### Changed
- Moved inline `<script>` to external `tracker.js` file — fixes Content-Security-Policy violation that blocked JavaScript on GitHub Pages

### Fixed
- Unit filter pill active-state background colour not applying correctly

---

## 2026-03-18 (earlier)

### Added
- **Time Played stat** — seconds present in mission, tracked per player and shown in the infantry table, player modal, and career page header
- **Hall of Fame & Hall of Shame** — award cards for best/worst K/D, shots-per-kill, longest kill, most teamkills, and most hits taken
- **`forceReimport` support in ImportScript** — `reimportOCAP()` entry point deletes existing rows for a file before re-importing, no manual sheet cleanup needed

### Fixed
- Missing Time Played column when running ImportScript against an existing sheet (now migrates header automatically)

---

## 2026-03-17

### Added
- **Player Career Stats page** — full-screen per-player view with overall stats, weapon kill breakdown bar chart, best single mission card, and per-mission kill table
- **Maximize button** on player modal — opens the full career page
- **Multi-select player filter** — left-click player pills to filter the table; right-click to open the career page directly
- **Mission multi-select** — mission pills support the same left-click toggle behaviour
- **Top Role in career header** — shows most-played role and how many missions it was used
- **Active date range** — player's first and last mission dates shown in the career header, sorted chronologically using extracted `YYYY-MM-DD` dates from filenames
- **Weapon kill breakdown** — ranked bar chart of kills per weapon in the player modal (top 15 weapons)
- **Query-param deep links** — `?player=Name` in the URL opens a career page directly; browser back/forward navigation works
- **ImportScript** (Google Apps Script) — imports OCAP `.json.gz` files from Google Drive into the `player_stats` Google Sheet; archives processed files automatically
- **GitHub Actions deploy workflow** — auto-deploys `main` to production and `claude/*` branches to `/dev/` preview
- **Favicon** (`favicon.svg`) — fixes 404 error in browser tab

### Changed
- Row click now filters the player list; right-click on a row opens the full career page
- Role normalisation strips `@ suffix`, colour names (Red, Blue, etc.), and NATO phonetic suffixes before aggregating to top role
- Career modal shows merged stats when a player appears in multiple rows for the same mission
- Center-aligned all table columns except Player Name

### Fixed
- Player pill active state not updating when toggling filters
- Active date range sort comparing raw mission name strings instead of extracted dates

### Reverted
- postMessage iframe deep-link support (removed due to side effects)

---

## 2026-03-17 — Initial Release

### Added
- CSV-driven leaderboard pulling from a published Google Sheet
- Separate infantry and vehicle combat tables
- Award cards: Executioner (most kills), Pro Sniper (longest kill), Perfect Aim (best shots-per-kill), Best K/D
- Top 10 kills horizontal bar chart
- Mission and player filter pills
- Zeus filter — exclude or isolate Zeus (game master) players
- Event type filter — Joint Ops vs regular weekly events (auto-detected by date)
- Vehicle kills, shots/kill, avg kill distance, longest kill columns in infantry table
- Player stats modal with per-mission breakdown
- Teamkill row highlighting in red
