# CLAUDE.md — ORBATBot / orbat-platform

This file gives full context on the project. Read it before making any changes.

---

## What This Is

A Discord bot for managing Arma 3 operation slot requests across multiple military simulation units. Members request slots; Unit Leaders and admins approve or deny via Discord buttons.

**An operation runs on one of two rosters**, and `utils/roster.py` is the only
thing that knows which: an **ORBAT** held in this bot's own database and edited
in the browser, or a **Google Sheet**, which is where it all started. The sheet
is written on approval; an ORBAT has nothing to write, because the approved
request *is* the booking. See
[the roster provider](#the-roster-provider-utilsrosterpy).

**Current state:** Fully operational bot deployed on Railway, plus an optional
web UI (`web/`) with Discord OAuth2 login for events, game roles, embeds, the
member log, voice time, the ORBAT editor, the slot-approval queue and the
[tactical map](#tactical-maps-utilstacmappy--webtacmappy).
**Next phase:** *requesting* a slot is still Discord-side; approving one is not.
The remaining steps are listed under
[Slots on the web](#slots-on-the-web--what-is-done-and-what-is-left).

---

## Repository Structure

```
bot.py                  # Entry point — ORBATBot class, startup, reminder task
cogs/
  slots.py              # All member-facing commands + approval/denial flow + views
  admin.py              # All admin/unit-leader commands
  gameroles.py          # Self-assignable game roles (Minecraft, DCS, …) + panel
  events.py             # Standalone events with RSVP buttons + reminder loop
  memberlog.py          # Join/leave/kick/ban announcements + invite attribution
  voicelog.py           # Time spent in voice channels, counted in intervals
  purge.py              # /purge — bulk message deletion, by count or back to a date
  redditfeed.py         # Watches a Reddit user or subreddit and announces new posts
  killtracker.py        # Polls the OCAP server and counts new recordings — no commands
  campaigns.py          # Posts the planner's approved drops, pings holds, starts sign-ups — no commands
utils/
  database.py           # All PostgreSQL queries (asyncpg)
  schema.py             # The schema those queries run against, as numbered migrations
  sheets.py             # Google Sheets read/write (gspread)
  roster.py             # One normalised slot, from a sheet or from an ORBAT
  orbat.py              # DB-held ORBATs: the text format, the safe edit, the board
  embeds.py             # Builder-made rich messages → discord.Embed, post and edit
  reddit.py             # One Reddit feed, read and rendered — no Discord, no database
  killtracker.py        # One OCAP recording → the Kill Tracker's per-player rows. Stdlib only
  campaign.py           # The campaign planner's rules: schedule, drop text, splitting, checks, cleaning. Stdlib only
  discordmd.py          # Discord message markdown → HTML, for previewing a drop as Discord shows it. Stdlib only
  eventtime.py          # Reading a typed time, and walking a repeat: the event date rules. Stdlib only
  objectives_seed.py    # The Objectives Library as first written — what a guild's library is seeded from
  ocap.py               # The OCAP server's list and downloads, and which recordings are due
  tacmap.py             # One tactical map: the symbols, the document, the SVG
  arma_icons.py         # Every vanilla Arma marker and NATO unit marker: class, label, the game's picture path — no artwork
  tiles.py              # A terrain's tile pyramid, read out of an uploaded archive
  help.py               # Every feature as a how-to — the site's wiki and the README's
web/                    # Optional browser UI — Discord OAuth2 login, events, roster, approvals, maps
  config.py             # Env-driven config; the feature is off until it is complete
  server.py             # uvicorn driven from inside the bot's event loop
  app.py                # The app: setup, the shared request plumbing, and the list of features
  routes/               # One module per feature — its routes and nothing else
  auth.py               # OAuth2 flow, signed-cookie sessions, CSRF, flash messages
  guilds.py             # Signed-in user → discord.Member, and what they may do
  service.py            # Create/edit/cancel/delete/RSVP, on top of cogs/events.py
  roles.py              # Game roles, on top of cogs/gameroles.py
  embeds.py             # Embed builder forms, on top of utils/embeds.py
  orbat.py              # ORBAT editor forms, on top of utils/orbat.py
  slots.py              # The approval queue, on top of cogs/slots.py
  operations.py         # Starting and steering an operation, on top of cogs/admin.py
  reddit.py             # The Reddit watches, on top of cogs/redditfeed.py
  killtracker.py        # The Kill Tracker's public CSV/JSON (no login, GET only) and what its admin pages do
  tacmap.py             # Tactical maps, share links and posting, on top of utils/tacmap.py
  terrain.py            # Uploaded terrains, on top of utils/tiles.py
  nav.py                # The two-level tab bar, built once rather than per template
  campaigns.py          # The campaign planner's pages, on top of utils/campaign.py
  exports.py            # Briefing packs as PDF (reportlab) and Word (python-docx)
  maptrace.py           # A phase's AO-map layer as a PNG ops trace (resvg) for the packs
  # There is no web/help.py: utils/help.py holds the content, and the two
  # routes have no permissions to check and no form to translate.
  voice.py              # Voice leaderboard shaping, the settings form and posting
  invites.py            # Invite labels — where each link was published
  helpers.py            # Guild-timezone formatting and datetime-local parsing
  templates/ static/    # Jinja2 templates, one stylesheet, two scripts (the map editor, the mission wizard)
lab/                    # Standalone ORBAT-editor playground — no Discord, no Postgres
  # (inside scripts/ below)
dashboard/              # The Kill Tracker's public page — static, hosted on GitHub Pages, not by the bot
  index.html tracker.js # Reads everything from the bot's /kt/api/* — no Google Sheet
  *.json                # campaigns / player_aliases / unit_overrides — the fallback the bot's API replaces
scripts/
  gen_help.py           # utils/help.py → the README's help:start/help:end section
  legacy/ImportScript   # The Apps Script utils/killtracker.py was ported from — the reference, not run
tests/                  # pytest — reddit, tacmap, tiles, web/config.py, check_feed(), help
requirements.txt
Dockerfile
docker-compose.yml      # Bot + PostgreSQL 16
Procfile                # Railway: python bot.py
railway.json            # Railway build + ON_FAILURE restart policy (backs /restart)
.env.example            # Template for local/Docker runs — keep in sync with the table below
.python-version         # 3.11
README.md               # User-facing docs — commands, setup, deployment
CLAUDE.md               # This file
```

There is no CI or linter config. The tests are `python -m pytest tests lab/tests`
(704 cases), and almost every one of them covers a module that imports nothing
beyond the standard library — which is the rule that decides what is testable
here at all. The two exceptions read source rather than call it:
`tests/test_logging.py` parses the whole service with `ast`, and
`tests/test_schema.py` reaches into `utils/schema.py`, which imports asyncpg
for one exception class:

| | what it pins |
|---|---|
| `lab/tests` | `utils/orbat.py`'s parser and diff — the two places where a bug silently deletes somebody's slot |
| `tests/test_reddit.py` | feed parsing, templating, and how a refusal is told apart from a broken feed |
| `tests/test_redditfeed.py` | what `check_feed()` promises: a post is announced exactly once |
| `tests/test_tacmap.py` | the document rules, lines (weights, dashes cut into Arma markers, arrows, phase-line names), measuring and grid references, reading markers back from Arma and the export report, the SVG and SQF escaping, units and markers drawn from uploaded pictures, how an upload is matched and how old maps read, shapes (their parsing, hatching and Arma area markers), the grey mask and its AO holes, the tile layout, and the place-name parser and geometry (the renderer's crop and tile swapping are pinned in `test_campaign.py`, which is what uses them) |
| `tests/test_tiles.py` | what an uploaded archive keeps and what it drops |
| `tests/test_webconfig.py` | which origin a link gets — the page's own host, or the canonical one |
| `tests/test_killtracker.py` | the counting rules (crew kills and distance, end-of-mission teamkills, disconnect "suicides", shots, hits, Zeus), JavaScript-style rounding, and that the streaming reader agrees with the in-memory path at every buffer size |
| `tests/test_ocap.py` | what the OCAP list entry means, and which recordings are due: new, counted under older rules, or failed and past their back-off |
| `tests/test_campaign.py` | the planner's rules: drop dates and time zones, what each drop says and where the ORBAT line falls, the 2,000-character split, the approval hash, the settings form, the consistency checks (phase objectives included), what a new mission carries over, what the wizard may store (`clean_fields`, `clean_phases`), image sniffing, the library seed and its pairings, what each briefing pack contains (`document_blocks`), and each drop's state (`drop_state`: approved for one version at one time, held and pinged once, edited after posting), where a test campaign's drops go, when an operation still counts as running, when a mission is over and a campaign may close itself, and the maps: one layer per AO on the campaign map and per phase on an AO map, what an AO drawn, relabelled or deleted on the campaign map does to the planner's AOs, what an AO map starts from (masked outside its AO), which layers an ops trace shows, where it crops and how deep its terrain goes, and after action: which recordings could be a mission, which one links itself, and the numbers and line it gives the AAR |
| `tests/test_discordmd.py` | that nothing typed becomes HTML, links stay http(s), and headings, quotes, lists, emphasis, code, timestamps and mentions render the way Discord shows them |
| `tests/test_logging.py` | the logging rules: no `print()` left in the service, no `log.exception()` outside an `except`, every logging module has its own logger, and every package that logs is one `bot.py` raises above WARNING |
| `tests/test_schema.py` | the migration list's numbering — versions unique and contiguous from 1, listed in the order they run — that migration 1 is unedited (its source is hashed), and that `init_db()` creates nothing outside the list |
| `tests/test_nav.py` | the tab bar: who sees which groups, that a group links to its first reachable page and that every page knows its group, that the top row stays within the width it was measured to fit — and that no page heads itself with the guild's name or says who is signed in |
| `tests/test_wizard_overview.py` | that the wizard's Overview and the schedule page are **one template** — neither has grown a copy of the schedule, both hand it what it asks for, every form in it comes back to the page it was pressed on, and nothing in it is redrawn in the browser |
| `tests/test_eventtime.py` | the event date rules: a month shift that clamps rather than overflows, which weekday of the month a date is, a month with no fifth Saturday answering *none of this one* rather than *the series is over*, a monthly series measured from its anchor so it keeps the 31st, a bot back after ten weeks catching up in one step, and a typed time read day-first in the guild's zone and stored naive. **Every expected date in it was worked out from a calendar rather than from what the code returns**, and the calendar it assumes is checked against the standard library in its first test |
| `tests/test_help.py` | that every slash command has a how-to — it reads the names out of `cogs/`, so forgetting to document a new command is a red test |

`_parse_responses()` and `_parse_mention_roles()` in `cogs/events.py` are the
two decision-making functions still uncovered. Neither is Discord-free —
the first asks `discord.PartialEmoji` whether a token is an emoji, the second
takes a `discord.Guild` — so covering them means either importing discord.py in
the tests or splitting the emoji check out, and that is the choice to make
before writing any.

---

## Environment Variables

| Variable | Description |
|---|---|
| `DISCORD_TOKEN` | Bot token from Discord Developer Portal |
| `GOOGLE_CREDENTIALS` | **Optional.** Full JSON content of the service account key file. Only read when a sheet-backed operation is actually touched — `sheets.get_client()` raises on the first call, not at import — so the bot starts and runs without it as long as every operation is ORBAT-backed |
| `DB_PASSWORD` | PostgreSQL password (docker-compose only) |
| `DATABASE_URL` | Full connection string — injected automatically by Railway; constructed by docker-compose; set by hand for local development |
| `RAILWAY_API_TOKEN` | **Optional.** Account or team token that lets `/restart` trigger a clean deployment restart via the Railway GraphQL API. Without it `/restart` still works, by exiting non-zero so Railway's `ON_FAILURE` policy relaunches the container. Project tokens do **not** work — `_railway_restart()` authenticates with a `Bearer` header |
| `DISCORD_CLIENT_ID` / `DISCORD_CLIENT_SECRET` | **Optional.** OAuth2 credentials for the web UI. Missing → no web server is started |
| `WEB_SECRET_KEY` | **Optional.** Signs the session, OAuth-state and flash cookies. Changing it signs everyone out |
| `WEB_BASE_URL` | **Optional.** Public origin, no trailing slash — or **several**, separated by spaces or commas, when the site answers on more than one name. Each one needs its own redirect URI registered in the Developer Portal. The first is canonical: it is what a link built with no request in hand uses. Empty → derived from the request, which is only meant for local runs. See [answering on more than one name](#answering-on-more-than-one-name) |
| `WEB_HOST` / `WEB_PORT` | **Optional.** Listen address. `PORT` (injected by Railway) wins over `WEB_PORT` |
| `WEB_ENABLED` | **Optional.** `0` keeps the site off even when everything else is set |
| `WEB_BRAND` | **Optional.** Site name in the header, tab title and footer. Defaults to `TFP BOT` |
| `OCAP_BASE_URL` | **Optional.** The unit's OCAP server, no trailing slash. Defaults to `https://ocap.taskforcephalanx.com`. The Kill Tracker reads `/api/v1/operations` and `/data/<file>.json.gz` from it — both public — see [the Kill Tracker](#the-kill-tracker-utilskilltrackerpy--utilsocappy--cogskilltrackerpy--webkilltrackerpy) |
| `KT_SYNC` | **Optional.** `0` stops the Kill Tracker polling the OCAP server. Everything already counted keeps being served |
| `REDDIT_USER_AGENT` | **Optional.** How the bot identifies itself to Reddit when reading a watched feed. Reddit answers 429 to a client that doesn't say who it is, so a descriptive value naming your own Reddit account is best; empty falls back to `utils/reddit.DEFAULT_USER_AGENT`. There is nothing else to configure — the feeds are public RSS, so no API registration, OAuth or client secret is involved |
| `MEMBER_EVENTS` | **Optional.** `1` requests the privileged members intent, which `cogs/memberlog.py` needs for joins and leaves. **Only set it once "Server Members Intent" is ticked in the Developer Portal** — requesting an ungranted privileged intent makes login fail, taking the whole bot down. Bans and unbans need no intent |
| `LOG_LEVEL` | **Optional.** How much this project's own code logs — `DEBUG`, `INFO` (the default), `WARNING`, `ERROR`. It does **not** reach discord.py, which `setup_logging()` pins to WARNING: the bot starts with `bot.start()` rather than `bot.run()`, so the library has never configured logging here, and turning the root up writes a line per gateway heartbeat and buries everything else |

Railway injects these at runtime; nothing sets them manually. `_railway_restart()` reads them to find the deployment to restart:

| Variable | Used for |
|---|---|
| `RAILWAY_DEPLOYMENT_ID` | The running container's own deployment — preferred, because it guarantees the bot restarts *itself* |
| `RAILWAY_SERVICE_ID` / `RAILWAY_ENVIRONMENT_ID` | Fallback when the deployment id is absent: query the service's latest `SUCCESS` deployment |
| `RAILWAY_PROJECT_ID` | Narrows that fallback query when present |

---

## Database Schema

All tables live in PostgreSQL. `utils/database.py` holds the queries;
**`utils/schema.py` holds the shape they run against, as a numbered list of
migrations** — see [the schema](#the-schema-utilsschemapy). `init_db()` applies
whatever this database has not run and records it in `schema_version`, so a boot
with nothing to do is one statement rather than the 127 it used to send.

Everything below describes the schema **as it now stands**, which is what the
migrations add up to; it is not a description of migration 1.

### `operations`
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `guild_id` | TEXT | Discord guild (server) ID |
| `name` | TEXT | Operation name — from the ORBAT or the spreadsheet title, or overridden on `/setup-slots` |
| `sheet_url` | TEXT | Full Google Sheets URL. **NULL on an ORBAT-backed operation** |
| `sheet_id` | TEXT | Extracted sheet ID. NULL likewise |
| `orbat_id` | INTEGER | → `orbats.id`. NULL on a sheet-backed operation. Exactly one of this and `sheet_url` is set, and `utils/roster.py` is the only thing that looks |
| `squad_col` | INTEGER | **Always NULL** — see [Google Sheets Integration](#google-sheets-integration-utilssheetspy) |
| `role_col` | INTEGER | **Always NULL** |
| `status_col` | INTEGER | **Always NULL** |
| `assigned_col` | INTEGER | **Always NULL** |
| `is_active` | INTEGER | 1 = active, 0 = archived. Only one active per guild |
| `event_time` | TIMESTAMP | Naive UTC. NULL if not set |
| `reminder_minutes` | INTEGER | Default 30 |
| `reminder_fired` | INTEGER | 0/1 — prevents double-firing |
| `created_at` | TIMESTAMP | |

### `requests`
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `guild_id` | TEXT | |
| `operation_id` | INTEGER | FK → operations.id |
| `member_id` | TEXT | Discord user ID |
| `member_name` | TEXT | Display name at time of request |
| `slot_label` | TEXT | Human-readable label e.g. "1-2 (TFP) – Rifleman" |
| `sheet_row` | INTEGER | Row index in the sheet. NULL on an ORBAT-backed request |
| `sheet_col` | INTEGER | Column index (ORBAT-style sheets) |
| `status` | TEXT | `pending` / `approved` / `denied` / `cancelled` |
| `approval_message_id` | TEXT | Discord message ID in #slot-approvals |
| `approval_channel_id` | TEXT | Discord channel ID for above |
| `approved_by` | TEXT | Display name of approver/denier |
| `denial_reason` | TEXT | Optional reason text |
| `unit_role` | TEXT | Unit role of the requester at submission time |
| `slot_id` | INTEGER | → `orbat_slots.id` on an ORBAT-backed operation; NULL on a sheet-backed one, where `sheet_row`/`sheet_col` identify the slot instead. No foreign key: `apply_orbat_structure()` releases the bookings itself when a slot goes, so the promise the confirmation page makes is kept |
| `created_at` | TIMESTAMP | |
| `updated_at` | TIMESTAMP | |

### `orbat_messages`
| Column | Type | Notes |
|---|---|---|
| `guild_id` | TEXT PK | |
| `channel_id` | TEXT | Channel where ORBAT embed lives |
| `message_id` | TEXT | Message ID of the live ORBAT embed |
| `updated_at` | TIMESTAMP | |

### `open_slots_messages`
Same structure as `orbat_messages`. Reserved for a planned secondary "open slots" message. **Nothing reads or writes it** — the table is still created so existing deployments aren't orphaned, but the accessors were removed as dead code. Re-add them if the feature is picked up.

### `orbats`
The slot roster held here rather than read out of a Google Sheet — see
[ORBATs](#orbats-utilsorbatpy--weborbatpy).

| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `guild_id` | TEXT | |
| `name` | TEXT | Shown in the list |
| `description` | TEXT | Optional |
| `nets_text` | TEXT | The net list as its author typed it, for the same reason as below |
| `source_text` | TEXT | The roster as its author typed it. The squads and slots below are the source of truth; this is kept alongside so comments, blank lines and their own spacing survive a reload, which regenerating the text from the structure would flatten |
| `created_by` / `created_by_name` | TEXT | |
| `created_at` / `updated_at` | TIMESTAMP | |

### `orbat_squads`
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `orbat_id` | INTEGER | FK → `orbats.id` **ON DELETE CASCADE** |
| `name` | TEXT | The embed field's heading |
| `column_side` | INTEGER | 0 = left, 1 = right. What `_build_orbat_embed()` infers from sheet geometry is stated outright here |
| `exclude_from_count` | INTEGER | 0/1 — replaces the case-insensitive `Reservists` name match |
| `reserved_unit` | TEXT | The unit the whole squad belongs to, matched by name against `UNIT_ROLES`. It began on the slot; migration 1 lifts any values entered there up to their squad and drops the slot column |
| `radio` | TEXT | The channel the squad talks on internally, e.g. `343 CHN:3`. Free text — every unit writes these slightly differently |
| `sort_order` | INTEGER | |

### `orbat_slots`
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | The stable slot identity a booking hangs off |
| `squad_id` | INTEGER | FK → `orbat_squads.id` **ON DELETE CASCADE** |
| `role_name` | TEXT | |
| `sort_order` | INTEGER | |

**A slot carries no booking.** Who holds one lives in `requests`, keyed by
`(operation_id, slot_id)`, which is what makes an ORBAT a reusable template
rather than one night's board.

### `orbat_nets`
The long-range nets the whole operation shares — platoon, logistics, air, high
command — as against `orbat_squads.radio`, which is one squad's internal channel.

| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `orbat_id` | INTEGER | FK → `orbats.id` **ON DELETE CASCADE** |
| `name` | TEXT | *Platoon Net*, *Logi*, *High Com Net* |
| `channel` | TEXT | Free text, e.g. `152 CHN : 1`. May be NULL for a net whose frequency isn't decided |
| `inactive` | INTEGER | 0/1 — struck through on the board: planned, but not in use this time |
| `sort_order` | INTEGER | |

**Nothing hangs off a net**, so unlike the squads and slots a save replaces the
list wholesale — there is no identity for an edit to lose.

### `reddit_feeds`
One row is one watch — see [Reddit announcements](#reddit-announcements-utilsredditpy--cogsredditfeedpy--webredditpy).

| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `guild_id` | TEXT | |
| `kind` | TEXT | `user` or `subreddit` |
| `source` | TEXT | The name, without the `u/` or `r/` |
| `channel_id` | TEXT | Where posts are announced. NULL = the watch has nowhere to post, so it is skipped |
| `template` | TEXT | The announcement text. NULL = `reddit.DEFAULT_TEMPLATE` |
| `author_filter` | TEXT | Comma-separated account names. NULL = announce every post the feed carries — see [following an author through a subreddit](#following-an-author-through-a-subreddit) |
| `mention_role_id` | TEXT | **Comma-separated** role ids, the same convention as `events` |
| `mention_user_id` | TEXT | Comma-separated user ids — the "tag these people" half |
| `enabled` | INTEGER | 0/1 |
| `seen_ids` | TEXT | The post ids already announced, newest first, capped at `MAX_SEEN`. **NULL means the watch has never been read**, which is what makes the first read seed instead of announcing 25 old posts |
| `last_checked_at` / `last_post_at` | TIMESTAMP | Naive UTC |
| `last_error` | TEXT | What the last read went wrong with, shown on the list page. NULL after a clean read |
| `retry_at` | TIMESTAMP | Don't read this watch again before then — see [being refused](#being-refused-is-about-us-not-about-the-feed). NULL, and cleared by any read that gets through |
| `created_by` / `created_by_name` | TEXT | |
| `created_at` / `updated_at` | TIMESTAMP | |

`UNIQUE (guild_id, kind, lower(source))` — two rows for the same author would
announce every post twice, which reads as the bot being broken. Both writers turn
the violation into a `ValueError`, so it is a message rather than a 500.

### `tac_maps`
One row is one tactical map — see
[Tactical maps](#tactical-maps-utilstacmappy--webtacmappy).

| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `guild_id` | TEXT | |
| `name` / `description` | TEXT | |
| `doc` | TEXT | **The whole map, as JSON** — background, grid, the Arma corners and every symbol on it. Held as one document rather than a table of items because nothing hangs off a symbol: a save replaces the plan wholesale, the same reasoning as `orbat_nets`. `utils/tacmap.parse()` is what makes the column safe to render, and it runs on the way out as well as in |
| `share_token` | TEXT | The `/m/{token}` link. NULL = never shared. Unique where it is set |
| `share_mode` | TEXT | `off` / `view` / `edit` — what the link may do |
| `channel_id` / `message_id` | TEXT | Where the map was last announced |
| `created_by` / `created_by_name` | TEXT | |
| `updated_by_name` | TEXT | Who last drew on it — including *someone with the link* |
| `doc_rev` | INTEGER | Bumped by every write of `doc`. A save names the revision it was made on and is refused if somebody else saved since |
| `created_at` / `updated_at` | TIMESTAMP | |

### `tac_terrains` and `tac_terrain_tiles`
A terrain uploaded as a tile archive and served back by this bot — see
[the background](#the-background-is-a-picture-a-terrain-from-ocap-or-one-you-upload).

| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | The `/t/{id}` a map's background points at |
| `guild_id` | TEXT | |
| `name` | TEXT | From the archive's `map.json`, or typed |
| `world_size` | DOUBLE PRECISION | The terrain's edge in metres — what the Arma corners come from |
| `max_zoom` | INTEGER | The deepest level actually stored |
| `tile_count` / `bytes` | INTEGER / BIGINT | What the list page shows, so the cost of a terrain is visible |
| `places` | TEXT | This terrain's own copy of its town names. Written on upload; what a map actually reads is `tac_places`, because an OCAP-backed map has no row here — see [place names](#place-names-the-terrain-knows-its-own-towns-the-tiles-do-not) |
| `created_by` / `created_by_name` | TEXT | |
| `created_at` | TIMESTAMP | |

`tac_terrain_tiles` is `(terrain_id, zoom, x, y)` → `image BYTEA`, primary-keyed
on exactly that, with **ON DELETE CASCADE**. The tiles are in the database and
not on disk because a container's filesystem does not survive a redeploy, and a
map whose background disappears on every deploy is worse than one with no
background at all.

### `tac_places`
The town names for one terrain, whether or not this bot stores that terrain —
see [place names](#place-names-the-terrain-knows-its-own-towns-the-tiles-do-not).

| Column | Type | Notes |
|---|---|---|
| `guild_id` / `scope` | TEXT | Composite primary key. `scope` is what `tacmap.place_scope()` returns: `t:7` for a terrain uploaded here, or the world name (`tem_cham`) for a map on an OCAP server. **That is the whole reason this is not a column on `tac_terrains`** — half the terrains a guild draws on have no row there |
| `label` | TEXT | What to call it in a list — the terrain's name, or the world name |
| `places` | TEXT | The names as JSON, replaced wholesale: nothing hangs off a place name, the same reasoning as `orbat_nets` |
| `source` | TEXT | `ocap`, `upload`, `paste` or `terrain` — where this set came from, so a re-import can say what it replaced |
| `updated_by_name` | TEXT | |
| `updated_at` | TIMESTAMP | |

### `kt_missions`
One row per OCAP recording — see [the Kill Tracker](#the-kill-tracker-utilskilltrackerpy--utilsocappy--cogskilltrackerpy--webkilltrackerpy). **Not scoped to a guild:** the OCAP server is the unit's, not a Discord server's.

| Column | Type | Notes |
|---|---|---|
| `ocap_id` | INTEGER PK | The OCAP server's own id for the recording. It only grows, so it is what "new" means |
| `filename` | TEXT UNIQUE | Without `.json.gz`; the file is `{base}/data/{filename}.json.gz` |
| `mission` / `world` / `tag` / `played_on` / `duration` | | From the OCAP list. `tag` is `Weekly OP` or `JointOP` |
| `label` | TEXT | `Mission (YYYY-MM-DD)` — what the dashboard's Mission column holds |
| `ocap_kills` / `ocap_player_kills` | INTEGER | The server's own counts, kept to hold ours against |
| `counted_kills` | INTEGER | Enemy kills by non-Zeus players, as counted here |
| `rows` | TEXT | **JSON array of the mission's per-player rows**, in `killtracker.HEADER` order. `'[]'` until counted. Replaced wholesale on every re-count, the same reasoning as `orbat_nets` |
| `name_override` | TEXT | The name an admin gave the mission, shown instead of OCAP's, **beside** `rows` so a re-count cannot erase it. The CSV applies it (`killtracker.apply_name`), keeping the recording's own date |
| `rules_version` | INTEGER | `killtracker.RULES_VERSION` it was counted with. **0 = never counted.** A mission below the current version is counted again by itself |
| `status` | TEXT | `ok` or `failed` |
| `error` / `attempts` / `retry_at` | | Why the last read failed, how many have, and not-before — doubling from 10 minutes to a day (`ocap.retry_delay_minutes`). A failed *re*-count leaves the old rows in place |
| `far_kills` | TEXT | **JSON list of the mission's farthest kills that feed the distance statistics** (infantry only — a vehicle's long shots are real; the 25 farthest at least 200 m, **plus every player's own longest**; struck ones are kept besides, flagged `x`, and do not take a place — striking the farthest lets the next one fill up at the next count) — what the *Longest kills* page shows without opening the recording. Replaced on every re-count |
| `sha256` | TEXT | Of the downloaded file |
| `imported_at` | TIMESTAMP | Naive UTC. Part of the cache key `kt_generation()` |

### `kt_campaigns`, `kt_campaign_missions`
A named group of missions. `kt_campaign_missions (campaign_id, ocap_id)` is by **recording**, ON DELETE CASCADE on both sides, so a renamed mission stays in its campaigns; `/kt/api/campaigns.json` writes each campaign's missions out under the names they are shown with now. In `kt_campaigns`: `name` (unique, case-insensitive), `patterns` (JSON list — the old substring matching), `extra_missions` (JSON list of labels imported from `campaigns.json` that matched no recording; passed through unchanged) and who last saved it.

### `kt_kill_exclusions`
`(ocap_id, kill_key)` → a kill an admin struck from the distance statistics. `kill_key` is `killtracker.kill_key()`: `frame:killer_id:victim_id`, ids as the recording has them, which is why it is kept beside the mission and applied by `compute(…, exclude)` on every re-count. The killer, victim, weapon and distance are copied in so a list can say what was struck. ON DELETE CASCADE with the mission. Striking or restoring one sets `kt_missions.rules_version = 0`, which is all it takes for the import loop to count that mission again.

### `kt_aliases`
`alias` (PK) → `canonical`: names that belong to one player and the one shown. The shape of the dashboard's `player_aliases.json`, which the dashboard applies on top of its own automatic merging. **Never a chain** — `killtracker.link_names()` pulls in everything already tied to any of the names, in either direction, because the dashboard doesn't follow one.

### `kt_unit_overrides`
`player` (PK) → `unit`, where the dashboard's guess is wrong. `''` is *Unknown* (the file's `null`); no row is *automatic*. Units are `killtracker.UNITS`, mirroring `UNIT_ORDER` in the dashboard.

### Campaign planner (`cp_`)
Created by `_init_campaign_planner()` at the end of migration 1, after the tables they reference.

| Table | What it holds |
|---|---|
| `cp_settings` | One row per guild: `unit_role_id`, `drop_channel_id`, `team_channel_id`, `drop_weekday` (0 = Monday), `drop_time` (`HH:MM`, guild time zone), `orbat_opens` (a drop key), `survey_url`. NULL = default; read it through `campaign.settings_with_defaults()`. `library_seeded` — the objectives library has been seeded once and never will be again. `orbat_access` (`admins` / `planners`, NULL = planners) and `orbat_role_ids` (comma-separated extra roles) — who may edit mission ORBATs, read through `campaign.may_edit_orbats()`. `auto_complete` — a finished campaign marks itself Complete (NULL = yes) |
| `cp_campaigns` | `guild_id`, `name` (unique per guild, case-insensitive), `era`, `theatre`, `summary`, `planned_missions` (the empty slots on the campaign page), `status` (`active` / `complete` / `archived`), `is_test` (see *Test campaigns*), `kt_campaign_id` → `kt_campaigns` (found or created by name when a real campaign is created), `kt_created` (the planner made that Kill Tracker campaign rather than linking one that existed — the only kind a delete may take with it, and the only kind a rename carries over to), `map_id` → `tac_maps` (the campaign map, made by the planner and deleted with the campaign), `auto_completed_at` (the bot closed it, or a person reopened it after it was closed — either way the bot leaves it open until a mission is added) |
| `cp_aos` | An area of operations: `campaign_id`, `name`, `position`, `area` (the campaign-map **layer** its outline is on, `cpao<id>`), `map_id` → its detailed `tac_maps` (made by the planner; deleted with the AO) |
| `cp_missions` | `campaign_id`, `position`, `name`, `op_date`, `fields` (JSON of the wizard's fields, keys as in `campaign.STEPS` — everything but the phases), `orbat_id` → `orbats`, `orbat_created` (the card's New or Copy made it, rather than Link — the only kind deleting a *test* mission may take along), `ocap_id` → `kt_missions` (the recording that is this mission), `ocap_manual` (a person chose it, or chose none — the loop stops guessing), `ocap_kt_added` (the planner put it in the Kill Tracker campaign — the only case unlinking takes it out), `operation_id` → the `operations` row sign-ups started. `name` and `op_date` mirror the fields of the same names. A save merges in SQL (`fields::jsonb || patch`), so two people saving different fields both keep theirs |
| `cp_mission_aos` | Which AOs a mission is fought in (many-to-many) |
| `cp_phases` | A mission phase: `position`, `title`, `detail`, `ao_id`, `layer_id` (the layer of its AO's map it is drawn on, always `cpp<phase id>`). A row, not part of `fields`, because a layer hangs off it — `save_cp_phases()` keeps a phase's id across saves for exactly that reason |
| `cp_objective_library` | Per guild, seeded from `utils/objectives_seed.py`, admin-edited: `category`, `kind` (`objective` / `complication`), `name`, `players_do`, `complete_when`, `pairs_with` (free text; `campaign.pairing_ids()` turns it into the picker's suggestions — never a rule), `intsum_hint`, `position` |
| `cp_phase_objectives` | An objective chosen for a phase: `library_id` (where it was picked from, or NULL), `role` (`primary` / `secondary` / `complication`), and its own copy of `name`, `players_do`, `complete_when`, `intsum_hint`; `result` for the AAR. Replaced wholesale on each save — nothing hangs off one |
| `cp_drops` | PK (`mission_id`, `kind`). Decisions only — the state is `campaign.drop_state()`. `approved_hash` + `due_at` (naive UTC) are **the version and the time that were approved**, stored together; `approved_by`/`approved_by_name`/`approved_at`; `held_pinged_at` (the team was told once; cleared by a new approval); `posted_at`, `channel_id`, `message_ids` (JSON, every message incl. image-only ones), `posted_hash` (what is in Discord now); `skipped`/`skipped_by_name` (Don't post) |
| `cp_images` | Uploaded images for drops and exports: `campaign_id`, `filename`, `content_type` (sniffed from the bytes, `campaign.image_type()`), `data` (BYTEA), `bytes`, `created_by_name`. Never changed once stored, so served `immutable`. Per campaign, so a mission carrying over the unit image shares the same row |

### `guild_settings`
| Column | Type | Notes |
|---|---|---|
| `guild_id` | TEXT PK | |
| `timezone` | TEXT | IANA timezone string, default `UTC` |
| `orbat_channel_id` | TEXT | Where the live board and the reminder ping go |
| `approvals_channel_id` | TEXT | Where a new request goes to be decided |
| `archive_channel_id` | TEXT | Where every decided request is recorded |
| `ocap_base_url` | TEXT | The OCAP folder this guild's terrains are served from, so a terrain is picked from a list rather than its world name typed out. NULL until a directory has actually answered — see [the background](#the-background-is-a-picture-a-terrain-from-ocap-or-one-you-upload) |

**All three channel columns are NULL until an admin picks something**, and NULL
means the channel *named* `#orbat` / `#slot-approvals` / `#approval-archive`,
created when it is missing — which is exactly what every guild did before the
columns existed, so an upgrade changes nothing. `cogs/slots.guild_channel()` is
the only thing that reads them; `database.CHANNEL_KINDS` maps each kind to its
column and its fallback name.

### `game_roles`
Self-assignable cosmetic roles (Minecraft, DCS, …) — see Game Roles below.

| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `guild_id` | TEXT | |
| `role_id` | TEXT | Discord role ID |
| `name` | TEXT | Label shown in the picker |
| `emoji` | TEXT | Optional emoji shown on the select option |
| `description` | TEXT | Optional one-liner shown under the option |
| `created_at` | TIMESTAMP | |

`UNIQUE (guild_id, role_id)` — re-adding the same role updates it in place.

### `game_role_panels`
Same structure as `orbat_messages`. Tracks the live game-role self-assign panel message.

### `events`
Standalone events, independent of `operations` and Google Sheets — see Events below.

| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | Shown to users as `#id` |
| `guild_id` | TEXT | |
| `channel_id` | TEXT | Channel the event message lives in |
| `message_id` | TEXT | The event message; NULL until posted |
| `title` | TEXT | |
| `description` | TEXT | Optional |
| `event_time` | TIMESTAMP | **Naive UTC**, like `operations.event_time` |
| `duration_minutes` | INTEGER | Optional; drives the end time and "finished" detection |
| `location` | TEXT | Optional free text |
| `image_url` | TEXT | Optional banner |
| `mention_role_id` | TEXT | **Comma-separated** role ids pinged on post and on the reminder. Singular name kept: a row written before multi-role support holds one id, which parses as a one-item list — which is why this needed no migration |
| `created_by` | TEXT | Discord user ID of the organiser |
| `created_by_name` | TEXT | Display name at creation time |
| `reminder_minutes` | INTEGER | Default 30; NULL means no reminder |
| `reminder_fired` | INTEGER | 0/1 — reset to 0 when `event_time` changes |
| `status` | TEXT | `scheduled` / `cancelled` / `completed` |
| `recurrence` | TEXT | NULL, or `daily` / `weekly` / `biweekly` / `monthly` / `monthly_nth` / `monthly_last` / `weekly_not_last` |
| `recurrence_until` | TIMESTAMP | Optional end of the series (naive UTC) |
| `recurrence_anchor` | TIMESTAMP | The **first** occurrence's start, carried unchanged down the series |
| `recurrence_delay_hours` | INTEGER | Hours to wait after this occurrence ends before its successor is posted. NULL/0 = post it the moment this one is marked completed |
| `handover_fired` | INTEGER | 0/1 — whether this **completed** event's handover (spawning its successor) has already run, so the delayed handover step in `event_task` doesn't repeat it every tick. Backfilled to 1 for every event already `completed` before this column existed, since those already had their handover done synchronously at completion time under the old code |
| `created_at` / `updated_at` | TIMESTAMP | |

Index `idx_events_guild_status` on `(guild_id, status, event_time)` backs the upcoming-events lookup.

### `event_signups`
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `event_id` | INTEGER | FK → `events.id` **ON DELETE CASCADE** |
| `member_id` | TEXT | |
| `member_name` | TEXT | Display name at signup time |
| `response` | TEXT | A key from the event's response set — `accepted` / `tentative` / `declined` unless the event defines its own |
| `created_at` / `updated_at` | TIMESTAMP | |

`UNIQUE (event_id, member_id)` — one row per member, changing your answer updates it in place. Withdrawing deletes the row entirely, so "no response" and "declined" stay distinct.

### `event_responses`
Custom sign-up options for one event. **No rows means the event uses `DEFAULT_RESPONSES`** — that fallback is what keeps every event created before this feature working unchanged.

| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `event_id` | INTEGER | FK → `events.id` **ON DELETE CASCADE** |
| `key` | TEXT | Slug of the label; goes into the button `custom_id` and into `event_signups.response` |
| `label` | TEXT | Button text |
| `emoji` | TEXT | Optional |
| `is_decline` | INTEGER | 1 = "not coming"; excluded from reminders and cancellation DMs |
| `sort_order` | INTEGER | Button and embed-field order |

`UNIQUE (event_id, key)`. The default keys are deliberately `accepted` / `tentative` / `declined` so existing `event_signups` rows keep resolving.

### `embeds`
Rich messages built in the web UI — see [Embeds](#embeds-utilsembedspy--webembedspy).

| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `guild_id` | TEXT | |
| `name` | TEXT | Internal label; only used to find it again in the list |
| `channel_id` / `message_id` | TEXT | Where it was posted. `message_id` NULL = still a draft |
| `content` | TEXT | Plain text above the embed — the only part where a mention pings |
| `title` / `description` / `url` | TEXT | `url` makes the title a link |
| `color` | TEXT | `#rrggbb`, normalised on save |
| `author_name` / `author_icon_url` | TEXT | |
| `thumbnail_url` / `image_url` | TEXT | |
| `footer_text` / `footer_icon_url` | TEXT | |
| `show_timestamp` | INTEGER | 0/1 — stamps the message with the time it was posted |
| `created_by` / `created_by_name` | TEXT | |
| `created_at` / `updated_at` | TIMESTAMP | |

### `embed_fields`
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `embed_id` | INTEGER | FK → `embeds.id` **ON DELETE CASCADE** |
| `name` / `value` | TEXT | Heading and body of one field |
| `inline` | INTEGER | 0/1 — up to three inline fields sit side by side |
| `sort_order` | INTEGER | |

### `log_settings`
One row per guild, written by the web UI. No row, or a NULL `channel_id`, means
nothing is announced — see [Member log](#member-log-cogsmemberlogpy).

| Column | Type | Notes |
|---|---|---|
| `guild_id` | TEXT PK | |
| `channel_id` | TEXT | Where announcements go. NULL = logging off |
| `log_join` / `log_leave` / `log_kick` / `log_ban` / `log_unban` | INTEGER | 0/1 per event type |
| `track_invites` | INTEGER | 0/1 — whether to work out which invite a join used |
| `welcome_channel_id` | TEXT | Where the welcome message is posted publicly. NULL = not posted anywhere — independent of `channel_id` above |
| `welcome_message` | TEXT | The welcome template as typed. NULL = `memberlog.DEFAULT_WELCOME_TEMPLATE` |
| `welcome_dm` | INTEGER | 0/1 — also send the welcome message as a DM |
| `updated_at` | TIMESTAMP | |

### `invite_labels`
Where each invite link was published, so a join can name the source.

| Column | Type | Notes |
|---|---|---|
| `guild_id` / `code` | TEXT | Composite primary key |
| `label` | TEXT | Free text — *Steam*, *Website*, *Reddit* |
| `updated_at` | TIMESTAMP | |

Rows outlive the invite: a code that has expired keeps its label, because old
join messages still refer to it.

### `voice_sessions`
One row per **counted interval**, not per visit — see [Voice time](#voice-time-cogsvoicelogpy).

| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `guild_id` / `member_id` / `channel_id` | TEXT | |
| `member_name` / `channel_name` | TEXT | Copied at the time, so a rename or a deleted channel doesn't erase history |
| `started_at` | TIMESTAMP | Naive UTC |
| `heartbeat_at` | TIMESTAMP | Refreshed every 5 min while open; bounds what a crash can lose |
| `ended_at` | TIMESTAMP | NULL = still running |
| `seconds` | INTEGER | Filled when the interval closes |

Indexes on `(guild_id, started_at)` and a partial one on the open rows.

### `voice_settings`
| Column | Type | Notes |
|---|---|---|
| `guild_id` | TEXT PK | |
| `enabled` | INTEGER | 0/1 — **off by default**, nothing is recorded until an admin turns it on |
| `board_enabled` | INTEGER | 0/1 — the self-updating top-10 message |
| `board_channel_id` / `board_message_id` | TEXT | Where the board is and which message it is |
| `board_period` | TEXT | Which window it shows — a key from `PERIODS` |
| `board_hour` | INTEGER | Local hour it refreshes at |
| `board_updated_on` | DATE | The local day it last refreshed — what makes it once-a-day |
| `channel_id` | TEXT | Where finished visits are announced. NULL = statistics only |
| `min_log_minutes` | INTEGER | Visits shorter than this are not announced |
| `count_afk` / `count_solo` | INTEGER | 0/1 — both off by default |
| `excluded_channels` | TEXT | Comma-separated voice channel ids |
| `updated_at` | TIMESTAMP | |

---

## The schema (`utils/schema.py`)

`utils/database.py` holds the queries; this holds what they run against. The
schema used to be one 950-line `init_db()` of `CREATE TABLE IF NOT EXISTS` and
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, every statement of it idempotent
because every statement of it ran on **every boot** — 127 queries to discover
that nothing had changed. It is now a numbered list of migrations, applied once
each and written down in `schema_version`, and a boot with nothing to do is one
`SELECT`.

### Migration 1 is the old `init_db()`, unchanged

That is the whole reason this was safe to introduce. On a database that already
has that schema every statement in it is a no-op, exactly as it was on
yesterday's boot; it is then recorded and never runs again. On an empty one it
builds everything. So the first boot after this shipped did precisely what the
boot before it did, and every boot since does nothing.

Verified against a real PostgreSQL 16 before it was merged, and worth repeating
if the runner is ever touched: a **fresh** database and an **existing populated**
one end up with byte-identical schemas (`pg_dump --schema-only`), the populated
one's rows are untouched, and the one data-writing statement in the baseline
(the `handover_fired` backfill) does not fire a second time.

### Three rules

- **A shipped migration is never edited.** Editing one changes nothing on a
  database that has already run it, so the code and that database silently
  disagree from then on. A change to the schema is always a *new* migration.
  `tests/test_schema.py` hashes migration 1 for exactly this reason — the
  failure message says to add one instead.
- **A migration runs inside a transaction**, with `lock_timeout` set.
  PostgreSQL has transactional DDL, so a migration that fails halfway leaves
  the database as it was — which the old `init_db()` could not promise. A
  migration that cannot get its locks fails the boot rather than holding every
  other query behind it; Railway restarts, and by then the container it was
  waiting on has gone.
- **Nothing creates a table outside the list.** Otherwise a database can be
  up to date and still be missing something. A test reads `init_db()` and
  checks.

### The lock, and why the table is made inside it

Railway can have the new container up before the old one is down, so `migrate()`
does its work under a `pg_advisory_lock`: whoever arrives second finds nothing
left to do.

`schema_version` is created **inside** that lock rather than before it, and that
is not tidiness. `CREATE TABLE IF NOT EXISTS` checks and then creates, and two
sessions doing it at the same instant is not a no-op for the loser but
`duplicate key value violates unique constraint "pg_type_typname_nsp_index"` out
of the catalogue — a crashed boot. Eight simultaneous boots against an empty
database is what found it, and is what the fix was checked with.

So the fast path asks for the versions straight out and treats *undefined table*
as "never migrated": one statement in the steady state, and every write
serialised behind the lock.

---

## Discord Channels (auto-created)

| Channel | Created by | Purpose |
|---|---|---|
| `#orbat` | `/setup-slots` | Live ORBAT embed with **📋 Request a Slot** button |
| `#slot-approvals` | First slot request | Pending approval embeds with Approve/Deny buttons |
| `#approval-archive` | First approval or denial | Compact record of every actioned request |

These are the **defaults**, not the only option: an admin can point each one at
an existing channel from the Operation page. `guild_channel(guild, kind)` in
`cogs/slots.py` is the single resolver — a stored id wins, a stored id whose
channel has since been deleted falls back to the name rather than posting
nowhere, and nothing stored behaves exactly as it always did. **Anything that
needs one of these three channels must go through it**; a fresh
`discord.utils.get(..., name='orbat')` would ignore the admin's choice.

---

## Unit Roles & Access Control

**Unit roles** (defined in `cogs/slots.py` as `UNIT_ROLES`):
`2nd USC`, `CNTO`, `PXG`, `TFP`, `SKUA`

**Unit Leader role name:** `Unit Leader` (defined in `cogs/admin.py` as `UNIT_LEADER_ROLE`)

### Access matrix

| Action | Member | Unit Leader | Admin |
|---|---|---|---|
| `/request-slot`, `/cancel-request`, `/change-slot`, `/leave-operation` | ✅ | ✅ | ✅ |
| `/clear-slot` — or **Release** / **Withdraw** on the web | ❌ | ✅ own unit | ✅ |
| `/assign-slot` — or **Assign** on the web | ❌ | ✅ own unit | ✅ |
| Approve / Deny — in `#slot-approvals` or on the web | ❌ | ✅ own unit | ✅ |
| `/clear-requests`, `/post-orbat`, `/set-event-time`, `/set-timezone`, `/post-event` — all on the web's **Operation** tab | ❌ | ❌ | ✅ |
| `/setup-slots`, `/current-operation`, `/debug-slots` — likewise | ❌ | ❌ | ✅ |
| Choosing the ORBAT / approvals / archive channels — web only | ❌ | ❌ | ✅ |
| Watching a Reddit feed — web only | ❌ | ❌ | ✅ |
| `/sync`, `/restart`, `/archive-old-approvals` — Discord only, bot maintenance rather than slot work | ❌ | ❌ | ✅ |
| `/purge` | ❌ | ❌ | ✅ — anyone with **Manage Messages** in that channel |
| `/game-roles`, `/game-role-list` | ✅ | ✅ | ✅ |
| `/game-role-add`, `/game-role-remove`, `/game-role-panel` | ❌ | ❌ | ✅ |
| `/event-list`, RSVP buttons on an event | ✅ | ✅ | ✅ |
| `/event-create` | ❌ | ✅ | ✅ |
| `/event-edit`, `/event-cancel`, `/event-delete` | ❌ | ✅ own events | ✅ |
| Reading a tactical map — web only | ✅ | ✅ | ✅ |
| Drawing on one, sharing it, posting it — web only | ❌ | ✅ | ✅ |
| Uploading or deleting a terrain — web only | ❌ | ❌ | ✅ |
| Campaign planner — web only | ❌ | ✅ with the planner's unit role | ✅ |
| Approving drops, **Start sign-ups now** — web only | ❌ | ✅ with the planner's unit role | ✅ |
| Editing an ORBAT a planner mission links — web only | ❌ (✅ with a role the setting adds) | ✅ planners, unless the setting says admins only | ✅ |
| Editing the objectives library — web only | ❌ | ❌ | ✅ |
| Campaign planner settings — web only | ❌ | ❌ | ✅ |

**Admin** = `manage_guild` or `administrator` Discord permission.
**Unit gating:** `_can_action_request()` in `slots.py` — admins bypass all unit checks; anyone else needs the `Unit Leader` role **and** the requester's unit role; a request with no unit role can be actioned by any Unit Leader. The Unit Leader half is not optional: the buttons in `#slot-approvals` are visible to everyone who can read the channel, so without it any member of a unit could approve their own request by pressing the button on it.

**Unit roles and `Unit Leader` can never become game roles** — `PROTECTED_ROLE_NAMES` in `cogs/gameroles.py` blocks it, so members can't self-assign their way into approval rights.

---

## Game Roles (`cogs/gameroles.py`)

Self-assignable cosmetic roles for games (Minecraft, DCS, …), separate from the slot system. Members opt in themselves; the roles exist purely so people can @mention everyone who plays a given game.

### Guarantee: no permissions

Roles the bot creates are created with `discord.Permissions.none()` and `mentionable=True`. Registering a pre-existing role runs `_role_rejection()`, which refuses the role if it:
- grants any permission at all (`role.permissions.value != 0`)
- is `@everyone`, or is `managed` (bot/integration/booster role)
- is a unit role or `Unit Leader` (`PROTECTED_ROLE_NAMES`)
- sits at or above the bot's top role, which Discord won't let the bot assign

### Commands

**`/game-role-add <name> [emoji] [description]`** (Admin)
Creates a permission-free, mentionable role named `name` and registers it. If a role with that **exact name** already exists it is reused instead of duplicated, after passing `_role_rejection()`. Re-running with the same name updates the emoji/description in place. Capped at 25 roles per guild (Discord's select-menu limit). Refreshes the panel.

**`/game-role-remove <role> [delete_role]`** (Admin)
Unregisters the role. By default the Discord role survives and members keep it; `delete_role: True` deletes it outright. Refreshes the panel.

**`/game-role-list`** (everyone)
Ephemeral list of every registered game role.

**`/game-role-panel [channel]`** (Admin)
Posts the persistent panel: an embed listing the roles plus a **🎮 Choose your game roles** button. One panel per guild (`game_role_panels`, keyed on `guild_id`); it self-updates whenever a role is added or removed via `_update_game_role_panel()`.

**`/game-roles`** (everyone)
Opens the picker directly — same flow as the panel button.

### Self-assign flow

The panel button and `/game-roles` both call `_send_role_picker()`, which sends an ephemeral `GameRoleSelectView`: one multi-select listing every game role, with the member's current roles **pre-ticked** (`SelectOption.default`) and `min_values=0` so deselecting everything is a valid submission.

On submit the member's game roles are set to exactly what's ticked — the view diffs the selection against their current roles and issues only the needed `add_roles` / `remove_roles` calls, then reports what changed. Selecting no change makes no API calls at all.

**Removal has two paths.** Unticking an option in that select already removes the role, but deselecting a pre-ticked option is easy to miss in Discord's UI, so the picker also carries a **➖ Remove a role** button — added only when the member holds at least one game role. It swaps in `GameRoleRemoveView`: a select listing *only* the roles they currently hold, `min_values=1`, nothing pre-ticked, so selecting is unambiguously "remove this". Both views share `_apply_role_changes()` (add/remove plus Forbidden/HTTPException handling) and `_change_summary()`.

Both paths re-check `interaction.user.roles` at submit time, so a role removed by someone else in the meantime results in a no-op with an explanation rather than a failed API call.

### Notes for future changes

- **Persistence:** `GameRolePanelView` has one static `custom_id` (`game_roles_open`) and is registered once in `setup_hook()`, so the panel button keeps working after a restart with no per-guild bookkeeping. This is why the panel is a button opening an ephemeral picker rather than a select menu directly on the public message — a shared message can't pre-tick per-viewer state.
- **No new intents.** `interaction.user.roles` comes from the interaction payload and role add/remove are REST calls, so `Intents.default()` still suffices. Member *counts* are deliberately not displayed anywhere: without the privileged members intent `role.members` is unreliable.
- **Emoji validation:** `_is_renderable_emoji()` guards both input and render. `PartialEmoji.from_str()` silently accepts plain text like `"minecraft"`, and Discord then rejects the whole component — which would break the picker for everyone — so anything containing ASCII letters is rejected on input and skipped at render time.
- **`guild_only` is applied per command, not on the cog class.** A class-level `@app_commands.guild_only()` is only honoured on `commands.GroupCog`; on a plain `Cog` it is silently ignored (see `ext/commands/cog.py`, the `__cog_is_app_commands_group__` branch).
- Registrations whose Discord role was deleted are pruned from the DB by `_resolve_game_roles()` on next read.

---

## All Slash Commands

### Member commands (`cogs/slots.py`)

**`/request-slot`**
Opens a two-step ephemeral squad → slot picker. Validates no existing active request. Submits request to DB, posts embed to `#slot-approvals`, DMs the member.

**`/cancel-request`**
Cancels the member's pending request. Voids the approval message (grey embed, buttons removed via `_void_approval_message()`).

**`/change-slot`**
Cancels the current slot (pending or approved) then opens the squad → slot picker again. An approved slot goes through `roster.clear()`, which clears the sheet cell on a sheet-backed operation and does nothing on an ORBAT.

**`/leave-operation`**
Shows a confirmation button. On confirm: cancels the request, releases the slot through `roster.clear()` if it was approved, DMs the member.

**`/post-orbat [channel]`** (Admin — `default_permissions(manage_guild=True)`)
Posts a fresh ORBAT embed to the specified channel (defaults to current). Saves
message ID to DB. **It lives in `slots.py`, not `admin.py`**, next to
`_build_orbat_embed()` and `OrbatRequestButton` — the only admin command that does.

### Admin/Unit Leader commands (`cogs/admin.py`)

**`/setup-slots [orbat] [sheet_url] [event_time] [reminder_minutes] [name]`**
Parses the time, then hands everything to `start_operation()` — see
[Running an operation](#running-an-operation-cogsadminpy).
- Takes **either** an `orbat` (autocompleted over the guild's ORBATs) **or** a
  `sheet_url` — giving both or neither is refused
- Deactivates previous operation (`is_active = 0`)
- Creates the operation record, with `orbat_id` or with the sheet columns
- Parses event time in the guild's configured timezone → stores as naive UTC
- Auto-posts the live ORBAT embed to `#orbat`
- Reminder options: 15 / 30 / 60 minutes
- `name` overrides the operation's name, which otherwise comes from the ORBAT
  or from the spreadsheet's title

**`/assign-slot @member`**
Direct assignment — bypasses the approval flow, recording the request as already
approved. The picker is the command's own; the decision itself is
`assign_slot_request()`, shared with the web. `roster.assign()` writes the sheet
on a sheet-backed operation. Blocked if the member already holds a slot.

**`check_can_assign()` is stricter than `_can_action_request()`, on purpose.** A
Unit Leader needs a unit of their own *and* the member must share it, so a
member with no unit cannot be assigned by just any Unit Leader — unlike deciding
a request that carries no unit, which any of them may do. Choosing who goes on
the roster is not the same act as answering someone who asked.

**`/clear-slot`**
Dropdown of active (pending + approved) slots. On select it calls
`clear_slot_request()`, the same function the web queue's Release and Withdraw
buttons call: `roster.clear()` for an approved one, cancels the DB record, DMs
the member, greys out a pending request's approval message, refreshes the board.
Unit Leaders scoped to own unit.

**`/clear-requests`**
Cancels all pending requests for the active operation, through
`clear_pending_queue()`. Nobody is DMed and nothing is archived — this empties a
queue that was never going to be decided rather than turning anyone down — but
the messages in `#slot-approvals` are greyed out, or a request cancelled here
would keep its buttons and read as actionable to whoever found it next.

**`/set-timezone <tz>`**
Stores IANA timezone in `guild_settings`. Used when parsing all event time inputs.

**`/set-event-time <time> [reminder_minutes]`**
Updates `event_time` and `reminder_minutes` on the active operation, resets `reminder_fired = 0`, refreshes ORBAT.

**`/post-event [channel] [mission_name] [event_time]`**
Posts a formatted event announcement embed. Defaults to active operation name and event time. "Sign up" field links to `#orbat` channel mention. Footer shows who posted it.

**`/archive-old-approvals`**
One-time migration. Scans up to 500 messages in `#slot-approvals` for old bot-posted embeds that were actioned before the delete-and-archive flow existed. Detects:
- Green embed + "Approved" field → approved
- Red or dark-gray embed + "Denied" field → denied
Copies each to `#approval-archive`, deletes from `#slot-approvals`.

**`/current-operation`**
Shows the active operation and where its roster comes from — the ORBAT's name, or a link to the sheet.

**`/debug-slots [squad]`**
Shows the raw slot data as the bot sees it, whichever roster is in use, keyed by
`db:…` or `sheet:…`. Useful for diagnosing missing slots.

**`/sync`**
Force-syncs slash commands with Discord, and refreshes the ORBAT. It also repairs
stale `sheet_col` values on pending requests — a sheet-only concern, since
inserting a row in a spreadsheet moves every cell below it while a slot id never
goes stale, so the repair is skipped outright on an ORBAT-backed operation.

**`/restart`**
Restarts the bot. Two paths, in order:
1. **Railway GraphQL API** — used when `RAILWAY_API_TOKEN` is set. `_railway_restart()` prefers `RAILWAY_DEPLOYMENT_ID` so the bot restarts *itself* rather than whatever a list query happens to return, falling back to the service's latest `SUCCESS` deployment. Returns the deployment id, which the reply shows truncated.
2. **Process exit** — the fallback, also used when the API call raises. `os._exit(1)` after a 1 s pause (so Discord can deliver the ephemeral reply) trips Railway's `ON_FAILURE` restart policy.

Nothing is lost either way: state lives in PostgreSQL and every view is re-registered by `setup_hook()`. Every invocation is printed with the requesting user and guild.

---

## Approval & Denial Flow

**`approve_slot_request()`, `deny_slot_request()` and `clear_slot_request()` in
`cogs/slots.py` are the whole implementation.** The Discord buttons, the
`/clear-slot` dropdown and the web page are all thin callers: each defers, calls
one of the three, and reports what came back. That
is what keeps the two surfaces from drifting — a decision made in the browser
does exactly what a decision made in `#slot-approvals` does, down to the
competitor denials and the archive record.

Both raise `ActionError` for everything a person can get wrong (the request is
gone, someone already actioned it, the approver's unit does not match). The
button renders it as an ephemeral `⚠️`, the web route as a flash message on the
queue page. They return a dict, so the caller can say how many competing
requests went with the approval without re-querying.

`_dm()`, `_archive_channel()`, `_drop_approval_message()` and
`_void_approval_message()` came out of the button and dropdown callbacks in the
same move. **None of them takes an `Interaction`** —
they take a `discord.Guild` and ids, which is the only reason the web can reach
them at all.

**A decision reads the request's own operation** (`database.get_operation(
req['operation_id'])`), not the guild's active one. `/setup-slots` deactivates
its predecessor but leaves that operation's pending requests alone, and
`setup_hook()` re-registers an `ApprovalView` for **every** pending request, so
an old message in `#slot-approvals` stays clickable indefinitely. Approving one
against the active operation wrote the old request's row and column into the
*new* operation's sheet, named the wrong operation in the archive and the DM,
and looked for competitors under the wrong operation id — so the loser of a
contested slot stayed pending. The board is only refreshed when that operation
is the active one; a decision on a finished one must not redraw it.

### Approval
1. Member submits → `requests` row created with `status = pending`
2. Embed posted to `#slot-approvals` — description: `**Op Name**  ·  @UnitRole\n@Member → **Slot**`. Footer: `Request ID: {id}`. Unit role is a Discord role mention (pings Unit Leaders).
3. Approver clicks **✅ Approve** in Discord, or on `/g/{guild}/slots`:
   - `_can_action_request()` checks the Unit Leader role and the unit
   - DB updated to `approved`
   - `roster.assign()` — writes the sheet on a sheet-backed operation, and does
     nothing on an ORBAT-backed one
   - If that write fails → DB rolled back to `denied`, error shown. Only
     reachable on a sheet: a failed network call is the only thing this ever
     protected against
   - Approval message deleted from `#slot-approvals`
   - Compact green embed posted to `#approval-archive`
   - Member DMed
   - Competing requests for same slot auto-denied (their messages edited grey in `#slot-approvals`, competitors DMed)
   - ORBAT refreshed (fire-and-forget)

### Denial
1. Approver clicks **❌ Deny** → `DenialModal` shown (optional reason, max 200
   chars). On the web the reason is a text field next to the button, with the
   same cap
2. On submit:
   - DB updated to `denied`
   - Message deleted from `#slot-approvals`
   - Compact red embed posted to `#approval-archive` (includes reason)
   - Member DMed
   - ORBAT refreshed

### Cancellation
`_void_approval_message()` — edits the approval message to grey with "📋 Slot Request — Cancelled" title, removes buttons. Does not delete. Its footer names
who did it: the member withdrew, or a Unit Leader cleared it out from under them.

### Clearing
`clear_slot_request()` — the third decision, reached from `/clear-slot` and from
**Release** / **Withdraw** on the web queue. It gives the slot back
(`roster.clear()`, which is nothing at all on an ORBAT), cancels the row, DMs the
member naming who removed them, and refreshes the board when that operation is
the live one. A **pending** request also has its `#slot-approvals` message voided,
so it stops being clickable; an **approved** one had that message deleted when it
was approved, and its `#approval-archive` record is left alone — the archive says
what was decided, and this is a later decision rather than a correction. A failed
sheet write removes nobody: the `ActionError` comes back before anything is
cancelled, so the sheet and the roster still agree.

**`database.cancel_request_by_id()` matches `pending` as well as `approved`.**
It used to be `approved`-only, which meant clearing a pending request DMed the
member and greyed out the approval message while the row stayed pending — the
board kept showing 🟡 and nobody could action the request any more.

### Persistence after restart
`bot.py` `setup_hook()` re-registers `ApprovalView` for every `pending` request and `OrbatRequestButton` as a global persistent view. custom_ids: `orbat_approve:{id}`, `orbat_deny:{id}`, `orbat_request_slot`.

---

## Running an operation (`cogs/admin.py`)

`start_operation()`, `set_operation_time()` and `build_announcement_embed()` are
to `/setup-slots`, `/set-event-time` and `/post-event` what
`approve_slot_request()` is to the ✅ button: the command parses its arguments
and calls one of them, and so does the web's Operation page. They raise
`ActionError` for everything a person can get wrong and know nothing about
interactions or forms.

**Neither surface parses a date in here.** `start_operation()` takes
`event_time` already parsed to naive UTC, because a slash command reads
`25/06/2025 19:00` and a browser sends `2026-06-25T19:00` — two different
readers of the same guild timezone, and the shared function should see neither.

Three details worth keeping:

- **The board post is non-fatal.** A guild where the bot cannot create or post
  to the ORBAT channel still gets its operation; `start_operation()` returns
  `channel=None` and the caller says so. Failing the whole thing over a message
  would leave the roster loaded and the admin thinking it wasn't.
- **The operation is re-read before the board is drawn.** `create_operation()`
  deactivates the predecessor and `set_event_time()` may have written a time, so
  the row that goes to `publish_board()` has to be the one that now exists.
- **`publish_board()` in `cogs/slots.py` is the only place a board is first
  posted** — `/post-orbat`, the auto-post above, and the web button all call it,
  so all three produce the same message with the same persistent button.
  `_update_orbat()` is the other half: it edits the message this one recorded.

`/setup-slots` and `/set-event-time` used to render their timestamps with
`int(naive.timestamp())`, which reads a naive datetime as **process-local** time
— correct only because Railway runs in UTC. Both now stamp `timezone.utc` first.

---

## Google Sheets Integration (`utils/sheets.py`)

**This is one of two roster backends now** — see
[the roster provider](#the-roster-provider-utilsrosterpy). An operation only
comes here when it was created with a `sheet_url` rather than an ORBAT.

**One format is implemented: the ORBAT-style sheet**, where slots are cell values
rather than rows under column headers. There is no header detection anywhere in
this module — a sheet without `<Insert Name>` markers makes `load_slots()` raise
"No available slots found", whatever its columns are called.

- Only `sheet1` is read; the operation name is the **spreadsheet title**
- A **slot entry** is a cell starting with `1.` or `1-` (`_SLOT_PREFIX`; a digit
  after the hyphen means `1-1 Rangers`, a squad id, not a slot)
- A **squad header** is the nearest non-slot cell above in the same column
  (`_is_squad_header()` rejects radio frequencies, `Slots:`-style headings,
  sentences and anything under 3 characters)
- **Available:** the assignment cell contains `<Insert Name>`
- **Filled:** `[TAG] Name`, `[] Name`, `Role — Name`, or a plain name in a cell
  to the right
- **On assign:** `<Insert Name>` → the member's name, `[]` → `[UNIT_TAG]`, cell bolded
- **On clear:** restored to `[] <Insert Name>` (strips the unit tag, unbolds)

**Split cells:** when a slot entry is found, up to 4 columns to the right in the
same row are searched for the assignment cell, stopping at the next slot entry so
one squad's column can't steal the neighbouring one's cell.

`load_slots()` returns available slots only. `load_all_slots()` returns everything
plus `assigned_to` and `col_idx` (used for the ORBAT display and its two-column
layout). Both run in a thread executor — gspread blocks.

**`squad_col` / `role_col` / `status_col` / `assigned_col` on `operations` are
always NULL.** `load_slots()` returns them as `None` because per-cell updates make
them meaningless here; they are the vestige of a tabular reader that no longer
exists. Nothing reads them back. Re-add header detection before giving them
meaning again.

---

## The roster provider (`utils/roster.py`)

**An operation is backed either by a Google Sheet or by an ORBAT.** This module
is the only thing that knows which. Everything above it — `/request-slot`, the
approval buttons, the live board, `/assign-slot`, `/clear-slot` — works on one
normalised slot and never branches on the source.

### The key is the slot's identity

`db:412` for an ORBAT slot, `sheet:r12c4` for a spreadsheet cell. That replaced
the `(sheet_row, sheet_col)` pair the whole flow used to compare on, and it is
why one `requests` row can point at either kind without anything downstream
caring. `slot_key()` builds it; `request_key(row)` reads it off a request.

`get_pending_slots()`, `get_approved_slots()` and `get_competing_requests()`
therefore all speak keys. The last one matches in Python rather than SQL,
because the key is derived from two different columns and an operation only
ever has a handful of open requests.

### The normalised slot

| Field | |
|---|---|
| `key` / `value` | the identity above; `value` is what a select menu returns |
| `slot_id` | the ORBAT slot, or None |
| `row`, `col` | the sheet cell, or None |
| `squad`, `role`, `label` | what it is; `label` is what a request records |
| `assigned_to` | who holds it, or None |
| `col_idx` | layout hint — the sheet column, or the ORBAT's 0/1 |
| `excluded` | left out of the counts: a `Reservists` squad, or `nocount` |
| `unit`, `radio` | the squad's unit tag and internal channel, ORBAT only |

`col_idx` is what lets `_build_orbat_embed()` draw both: the midpoint split it
already did on sheet geometry works unchanged on a 0/1 column side.

### Writing is nothing on the ORBAT side

`assign()` and `clear()` write to the sheet for a sheet-backed operation and do
**nothing** for an ORBAT-backed one — the approved `requests` row *is* the
booking, and there is no second copy to keep in step.

That is what removes the approve path's rollback. `ApprovalView._approve_callback`
still has the branch that resets a request to `denied` when the write fails, but
it can now only fire on a sheet: a failed network call is the only thing it was
ever protecting against. The same goes for the 30 s sheet read that used to run
on **every** board refresh.

---

## ORBAT Embed

Built by `_build_orbat_embed()` in `slots.py`, from `utils/roster.py`'s
normalised slots — so a sheet-backed and an ORBAT-backed operation render
identically:

- Title: `🗺️ ORBAT — {operation_name}`
- Description: open / pending / filled counts + optional event timestamp
- Two-column layout by `col_idx`, with spacer fields
- The squad's unit in the field name (`1-1 Alpha  [TFP]`) and its internal
  channel as the first line of the value (`📻 343 CHN:3`), both ORBAT only
- Slot indicators: 🟢 open, 🟡 pending, 🔴 filled
- The shared net list as one final field, struck through where a net is not in
  use — which is why the squads get **7** rows instead of 8 when there is one:
  7 × 3 + 1 = 22, and 8 × 3 + 1 = 25 is the ceiling
- Updated by `_update_orbat()` — fetches the stored message id, re-reads the
  roster through `roster.load_all()`, edits the message

**A squad left out of the counts is displayed anyway.** The header counts the
slots people are expected to fill, and a reserve bench would otherwise make the
operation look permanently under-strength. An ORBAT says so with `nocount`; on a
sheet it is still the case-insensitive `Reservists` name match, which is all
that side has to go on.

---

## Event Reminders

`bot.py` runs `reminder_task` every 60 seconds. Fires when:
`event_time - reminder_minutes <= NOW < event_time` and `reminder_fired = 0`

On fire: sets `reminder_fired = 1`, DMs all approved members, posts mention in `#orbat`.

---

## Deployment

### Railway (production)
- Bot service: `python -u bot.py` (`railway.json` `startCommand`; the `Procfile`
  declares the same as a `worker` process)
- PostgreSQL service: `DATABASE_URL` injected automatically
- Auto-deploys on push to `main` — the single deployment branch. There is no
  `master`; an earlier one was folded into `main` and deleted, so never
  recreate it or target a PR at it.
- Deploys happen one of two ways, never both. Either Railway watches this repo
  (its GitHub App, **which needs access to the account that owns the repo**), or
  `.github/workflows/deploy.yml` runs the tests and then `railway up` with a
  project token — needing the secret `RAILWAY_TOKEN` and the variable
  `RAILWAY_SERVICE`, and no GitHub App at all. With both on, every push deploys
  twice, so switch Railway's own source off when the workflow takes over.
  Without the token the workflow's deploy job is skipped and only the tests run.

### GitHub Pages (the Kill Tracker page)
- `dashboard/` is a static page; `.github/workflows/dashboard.yml` publishes it to the
  `gh-pages` branch (`main` → root, `claude/**` → `dev/`). Pages must be switched on once:
  Settings → Pages → Deploy from a branch → `gh-pages` / root. URL: `https://armatfp.github.io/ORBATBot/`.
- `deploy.yml` has `paths-ignore: ['dashboard/**']` on push, so a page-only change does
  **not** redeploy (and restart) the bot. Keep it that way.
- The page lives on another origin than the bot, which is why `/kt/api/*` answers with
  `Access-Control-Allow-Origin: *`.

### Docker (self-hosted)
- `docker-compose.yml`: bot + postgres:16-alpine containers
- Named volume `postgres_data` persists DB across restarts
- `.env` file: `DISCORD_TOKEN`, `GOOGLE_CREDENTIALS`, `DB_PASSWORD`
- **Every optional variable has to be listed in the compose `environment:` block
  to reach the container.** A variable that only exists in `.env` is read by
  compose for interpolation, not passed through — so anything added to
  `.env.example` needs a line here too, or it silently does nothing under Docker.

### Logging

`bot.py`'s `setup_logging()` configures it once, in `main()`, and every module
writes through `log = logging.getLogger(__name__)`. There are no `print()`
calls left in `bot.py`, `cogs/`, `utils/` or `web/` — `scripts/` still prints,
because those are command-line tools talking to whoever ran them.

- **The root sits at WARNING; only `__main__`, `cogs`, `utils` and `web` go to
  INFO** (`LOG_LEVELS`, overridable with `LOG_LEVEL`). `__main__` is in that
  list because `bot.py` is run as a script, so its own logger is not called
  `bot`.
- **discord.py is pinned to WARNING, and that is the load-bearing part.** The
  bot starts with `bot.start()` inside `asyncio.run()` rather than `bot.run()`,
  which is the call that would have run discord.py's own `setup_logging()` — so
  the library has never had logging configured here and only its warnings ever
  reached the log. A plain `basicConfig(level=INFO)` therefore does something
  that looks harmless and is not: it switches on the gateway, heartbeat and
  reconnect stream and buries every line this project writes.
- **uvicorn is left alone.** `web/server.py` passes `log_level='info'` and
  uvicorn's `dictConfig` carries `disable_existing_loggers: False`, so it
  configures its own three loggers and silences none of ours.
- **An `except` that reports uses `log.exception()`**, which keeps the
  traceback the old `print(f"… {e}")` threw away. Every loop that must not die
  still catches `Exception` and carries on — the catching did not change, only
  what it writes.

`tests/test_logging.py` reads the source the way `tests/test_help.py` does and
pins four rules: nothing in `bot.py`, `cogs/`, `utils/` or `web/` calls
`print()`; no `log.exception()` sits outside an `except`, where it would write
`NoneType: None` under the message; every module that logs defines its own
`log`; and every package that logs is named in `LOG_LEVELS`, or its INFO lines
would sit under the root's WARNING and never be seen.

### Startup sequence
1. `init_db()` — applies any pending migration (`utils/schema.py`) — then
   `close_dangling_voice_sessions()`: schema first, then voice
   intervals left open by a crash are closed at their last heartbeat before
   anything new is recorded
2. Load `cogs.slots`, `cogs.admin`, `cogs.gameroles`, `cogs.events`, `cogs.voicelog`,
   `cogs.memberlog`, `cogs.purge`, `cogs.redditfeed`, `cogs.campaigns` and `cogs.killtracker`. Each is wrapped in its own
   `try`, so one cog failing to import doesn't take the others down
3. Re-register persistent views:
   - `OrbatRequestButton` and `GameRolePanelView` — one global instance each
   - one `ApprovalView` per `pending` request
   - one `EventRsvpView` per event from `get_live_events()`, each built with **that event's own responses** via `load_responses()` — a restored view carrying the default keys wouldn't match the message's `custom_id`s
4. Start `reminder_task` (operations). `EventsCog.event_task` starts separately, from the cog's `__init__`
5. `_start_web()` — starts the web UI when it is configured. Every failure here is non-fatal and only printed: missing dependencies, missing variables, `WEB_ENABLED=0` or a crash in `WebServer.start()` all leave the bot running normally. `close()` stops the server before the Discord connection goes down
6. `on_ready`: `copy_global_to` + guild sync for each guild (instant, vs up-to-1-hour global sync)
7. `on_guild_join`: sync immediately when bot joins a new server

---

## Event dates (`utils/eventtime.py`)

Reading a time somebody typed, and working out when a repeat happens next.
Both halves came out of `cogs/admin.py` and `cogs/events.py` unchanged, and
they moved for the rule that decides what is testable here at all: a module
importing nothing but the standard library can be covered, and the two cogs
pull discord.py and asyncpg in behind them. Both cogs import the functions
back under the private names they always had, so every call site — in the
cogs, in `web/service.py`, `web/helpers.py`, `web/app.py` and `cogs/purge.py`
— is unchanged.

This is the arithmetic nobody can check by reading. A monthly series that
drifts off the 31st, or a reminder an hour out because a date was read in the
wrong zone, looks exactly like a correct one until the night it matters.

Two rules to know before touching any of it:

- **A repeat is measured from the series anchor, never from the previous
  occurrence.** Measuring from the previous one turns a 31 January series into
  28 February → 28 **March**, losing the 31st for good; anchoring gives 28
  February → 31 March. It is the same property that makes a bot which was
  offline for ten weeks post *one* occurrence in the future rather than ten in
  the past.
- **`None` from `nth_occurrence()` means "not this time round", not "the series
  is over".** June has no fifth Saturday; a fifth-Saturday series steps over it
  and carries on. Every caller skips rather than stops — which is also the
  whole of how `weekly_not_last` is implemented, and why adding that pattern
  was three lines.

**`parse_event_time()` falls back to UTC for both ways zoneinfo refuses a
key.** An unknown name raises `ZoneInfoNotFoundError`; an empty or path-shaped
one raises a plain `ValueError`, which used to come back out of here — and
every caller reads a `ValueError` from this function as *I could not read that
date*, so the person would have been told their perfectly good date was
unparseable. Not reachable through either surface (the slash command offers a
fixed list and the web form checks membership in the same list); the fallback
now matches what its own shape says it does.

`tests/test_eventtime.py` covers all of it. **Every expected date in it was
worked out from a calendar, not from what these functions return** — the point
of covering arithmetic this old is lost if the expectations are taken from the
behaviour being checked. The 2026 calendar the tests assume is written out in
the file's docstring and checked against `calendar` in its first test, so a
wrong assumption fails as itself rather than as a confusing date comparison.

---

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
| Players | Link names that are one player and pick the one shown (`kt_aliases`); show another / unlink |
| Longest kills | The farthest **infantry** kills of all missions (100 of them; a mission or player filter shows all of theirs), farthest first, with a tick to **strike** the unbelievable ones; a struck kill leaves the list at once (the page overlays `kt_kill_exclusions` on the stored list) and shows under *Struck kills*, where unticking puts it back (`kt_kill_exclusions`). A struck kill still counts, only its distance leaves the statistics; the mission is counted again by the import loop within minutes (`rules_version = 0`) and the decision survives every later re-count. `compute()` hands back `far` for this; `far_listing()` / `plan_strikes()` are the pure parts (a form cannot strike a kill it was not shown) |
| Units | Set a player's unit where the dashboard's guess is wrong (`kt_unit_overrides`), one form, only changed rows written |
| Import | Take over the old work once: mission names from the sheet's CSV, `campaigns.json`, `player_aliases.json`, `unit_overrides.json` |

Each of them is a function in `web/killtracker.py` (what it does, in one place) over `utils/killtracker.py` (the pure rules: `clean_name`, `plan_name_import`, `link_names`, `campaigns_document`, `parse_*_json`, tested). **Import order matters:** mission names first, because `campaigns.json` names its missions the way the sheet did and they are matched against the names *as shown*. Anything already set is kept (except mission names, with the checkbox), and a campaign mission name that matches no recording stays on the campaign as `extra_missions`.

The public side serves them in the shape of the dashboard's own files — `/kt/api/campaigns.json`, `/kt/api/player_aliases.json`, `/kt/api/unit_overrides.json` — cached under `kt_generation()`, which includes names, campaigns, aliases and units, so an edit shows within the cache window rather than at the next import. **The CSV keeps the recorded player names**: the dashboard applies the aliases itself, as it always has.

### The dashboard reads only the bot

`dashboard/tracker.js` reads `/kt/api/stats.csv` and the three JSON files from the bot and **nothing from a Google Sheet**. The JSON files in `dashboard/` are only a fallback when the bot cannot be reached (`botJson()`): an *empty* answer is an answer, so deleting every link on the admin page does not bring the old file back. Old links with `?source=bot` still work; the parameter is ignored.

Rules versions 2 and 3 deliberately **differ from the ImportScript** (indirect fire, pistols and struck kills no longer feed the distance), so the cell-by-cell comparison in *Checked against the original* only holds for a rule the script also has.

* The dashboard is hosted on GitHub Pages (see Deployment). It could also be served by the bot itself (one origin); it is a static page, so that is a copy, not a rewrite.
* The old `armatfp/TFP_KillTracker` repo redirects here and is archived; release number and date live in `dashboard/tracker.js` (`VERSION`, `RELEASE_DATE`) and must be bumped together with `dashboard/CHANGELOG.md`.

---

## Events (`cogs/events.py`)

Apollo-style standalone events. **Deliberately independent of the slot/ORBAT system** — no Google Sheet, no `operations` row, no `requests`. An event is a message with Accept / Tentative / Decline buttons and a live attendee list.

Staged build toward Apollo parity. Done: sign-ups, reminders, editing, cancelling, auto close-out, **recurring events**, **custom responses**. Still to come: **sign-up roles with per-role caps**, then waitlist, templates and a calendar view.

### Commands

**`/event-create <title> <start_time> [description] [duration] [location] [channel] [mention] [reminder] [image_url] [repeat] [repeat_until] [repeat_delay] [responses]`** (Admin or Unit Leader)
Parses `start_time` with `_parse_event_time()` from `admin.py` in the guild's timezone, rejects times in the past, posts the event and registers its view. If the send fails (`Forbidden`, or `HTTPException` from a bad `image_url`) the row is set to `cancelled` so it can't linger as a phantom event. `repeat_delay` only makes sense together with `repeat` — see [Recurrence](#recurrence).

**`/event-edit <event> [title] [start_time] [description] [duration] [location] [reminder] [repeat] [repeat_until] [repeat_delay] [responses] [mention]`** (organiser or admin)
Only the passed fields change — `update_event()` uses `COALESCE`, so omitted fields keep their value. Changing the time resets `reminder_fired` to 0 so the reminder fires again for the new time.

**`/event-cancel <event> [reason] [stop_series]`** (organiser or admin)
Sets status `cancelled`, re-renders the message in red without buttons, and DMs everyone who accepted or was tentative. On a recurring event, `stop_series` defaults to **False** — cancelling one occurrence posts the next one, since "this week is off, next week isn't" is the common case. `stop_series: True` clears the recurrence first, so nothing can spawn a successor afterwards.

**`/event-delete <event>`** (organiser or admin)
Deletes the row and its message for good; sign-ups and custom responses go with it via `ON DELETE CASCADE`. Behind a confirmation button because it can't be undone. Its autocomplete uses `get_guild_events()` — **cancelled and completed events included**, since those are the ones being cleaned up, unlike the other `event` params which only offer scheduled ones.

The confirmation states the sign-up count and, when someone is signed up to a scheduled event, warns they will **not** be told and points at `/event-cancel` instead. That is the split: cancel keeps the record and DMs everyone, delete removes it silently.

**`/event-list`** (everyone) — upcoming events with per-response counts and a jump link.

`event` params use autocomplete over the guild's upcoming events, so users pick a title rather than typing an ID.

### RSVP flow

`EventRsvpView` builds one button per response, with `custom_id` `event_rsvp:{event_id}:{key}`. Pressing **the response you already gave withdraws it** (deletes the signup row) — that toggle is stated in the embed footer, because it isn't otherwise discoverable. Pressing a different one updates in place via the `UNIQUE (event_id, member_id)` upsert.

Every change re-reads the event and refreshes the message through `_refresh_event_message()`, fire-and-forget.

### Ping roles

`mention` on `/event-create` and `/event-edit` is a **string**, not a `discord.Role` — the app-command system has no multi-role option type, and a string field still lets the client turn `@Role` into a proper `<@&id>` token.

`_parse_mention_roles()` collects `<@&id>` tokens, then **strips every `<…>` token** before scanning for bare snowflakes. Without that strip, the digits inside a user mention `<@123>` are matched by the bare-id pattern and get stored as a role, which then renders as `<@&123>` and silently pings nobody. If nothing mention-shaped is found it falls back to comma-separated role *names*.

Capped at `MAX_MENTION_ROLES = 10`. Unresolvable entries are reported rather than dropped silently, and roles that aren't `mentionable` produce a warning, since the bot needs **Mention All Roles** to actually notify through those.

`/event-edit mention:none` clears the pings via `set_event_mentions()` — `update_event()` uses `COALESCE` and so cannot write NULL, the same reason `set_event_recurrence()` exists.

### Custom responses

`responses` on `/event-create` and `/event-edit` takes `✅ Coming | ❓ Maybe | -❌ Can't`. `_parse_responses()` splits on `|`, treats a leading `-` as `is_decline`, and pulls a leading emoji off the front when `_is_emoji()` accepts it — the same ASCII-letter guard as `gameroles._is_renderable_emoji`, since a word Discord rejects as an emoji would break the whole button row.

Keys come from `_response_key()` (slug of the label, deduplicated with a numeric suffix). They land in both the `custom_id` and `event_signups.response`, so they must be stable and unique per event.

**`load_responses(event_id)` is the single entry point** — it returns the stored set or a copy of `DEFAULT_RESPONSES`. Anything that renders buttons, groups sign-ups, or decides who gets a reminder goes through it, including `bot.py`'s view restoration: a restored view has to carry that event's own keys or its `custom_id`s won't match.

`_attending()` replaces the old hard-coded `response in ('accepted', 'tentative')` test — reminders and cancellation DMs now go to everyone whose response is not flagged `is_decline`.

Two limits: at least 2 responses, at most `MAX_RESPONSES = 10` (two button rows), labels under 40 characters. A set where *every* option is a decline is rejected, since nobody could sign up.

**Changing the set on a live event clears sign-ups whose key no longer exists** (`drop_signups_not_in()`), and `/event-edit` reports how many. Leaving them would keep rows that render nowhere and silently skew nothing visible.

A recurring event copies its response set to each new occurrence in `_spawn_next_occurrence()`.

### Recurrence

Stored key → what the user picks:

| Key | Choice | Pattern |
|---|---|---|
| `daily` | Daily | |
| `weekly` | Weekly | |
| `biweekly` | Every 2 weeks | |
| `monthly` | Monthly — same date | The 15th every month, clamped in short months |
| `monthly_last` | Monthly — last weekday | **Last Saturday of the month** |
| `monthly_nth` | Monthly — same weekday | 2nd Saturday of the month |
| `weekly_not_last` | Weekly — except the last one | Every Saturday **except** the last of the month |

`_RECURRENCE_LABELS` holds the mapping and `_REPEAT_CHOICES` the command choices; the choice values are exactly those keys plus `none`.

**The weekday variants take both weekday and position from the anchor** — there is no separate column for them. `monthly_last` uses the anchor's weekday with position `-1`; `monthly_nth` uses `_weekday_position(anchor)`, i.e. `(day - 1) // 7 + 1`. So a series created on Saturday 13 June means "2nd Saturday" under `monthly_nth`, and "last Saturday" under `monthly_last` regardless of where the anchor sits — `/event-create` warns when the first date isn't itself the last weekday, so the jump doesn't surprise anyone a month later.

`_weekday_day()` returns None when a month has no such day — there is no 5th Saturday in June. That is why `_next_occurrence()` **skips** a None candidate and keeps walking instead of returning None: a 5th-Saturday series must jump over months that lack one rather than end. The only thing that ends the walk is an invalid recurrence key, which is checked before the loop.

**`weekly_not_last` is the one exclusion pattern, and it reuses that same skip.** `_nth_occurrence()` advances weekly and returns None whenever the candidate is the last weekday of its month, so the walk steps over it. No extra machinery: adding it was three lines because the skip already existed for the 5th-Saturday case.

It is exactly complementary to `monthly_last` on the same weekday — over an 81-Saturday window the two sets never collide and their union is every Saturday. That is the intended pairing: a monthly op on the last Saturday plus weekly ops on the others.

`_DAY_NAMES` is a fixed English tuple rather than `calendar.day_name`, which follows the process locale and would leak into user-facing text.

`_recurrence_text(event)` renders the human description ("Monthly · last Saturday of the month") and is used by the embed, `/event-list` and the create/edit replies. It accepts any mapping with `recurrence`, `recurrence_anchor` and `event_time`, so callers can pass a plain dict before a row exists.

`repeat` on `/event-create` and `/event-edit` sets `recurrence`; `repeat_until` bounds the series. `/event-edit repeat:none` stops it — that path goes through `set_event_recurrence()` rather than `update_event()`, because `update_event()` uses `COALESCE` and therefore cannot write NULL.

**One live occurrence at a time.** There is no pre-generated calendar of rows. When `event_task` finishes an occurrence it calls `_spawn_next_occurrence()`, which creates and posts the next one and registers its view. Sign-ups deliberately do **not** carry over — each occurrence is answered fresh.

**`recurrence_anchor` is why monthly repeats don't drift.** `_next_occurrence()` always measures from the anchor via `_nth_occurrence(anchor, recurrence, n)`, never from the previous occurrence. Measuring from the previous one would turn a 31 January series into 28 Feb → 28 Mar, permanently losing the 31st; anchoring gives 28 Feb → 31 Mar. `_add_months()` clamps to the last valid day of the target month.

The same property fixes catch-up: `_next_occurrence()` walks `n` upward until the candidate is past the cutoff, so a bot that was offline for ten weeks posts **one** occurrence in the future rather than one per missed week. The walk is bounded at 4000 iterations.

If the next occurrence can't be posted (channel gone, `Forbidden`), the freshly created row is set to `cancelled` so the series stops cleanly instead of retrying every minute.

**`recurrence_delay_hours` decides how long after an occurrence ends its successor goes up.** NULL/0 (the default, and the only behaviour before this existed) posts it the moment this occurrence is marked `completed`; any other value holds it back that many hours. It rides along with `recurrence`/`recurrence_until`/`recurrence_anchor` down the series — set on `/event-create`, changed on `/event-edit` via `repeat_delay`, carried forward by `_spawn_next_occurrence()`, and cleared whenever the recurrence itself is cleared. It only ever applies to the *scheduled handover* in `event_task` below — cancelling a single occurrence (`/event-cancel` without `stop_series`) still spawns its successor immediately, because there was no "end" for a delay to count from.

### Background loop

`EventsCog.event_task` runs every 60 s (separate from `bot.py`'s `reminder_task`, which stays operation-only):

1. **Reminders** — `reminder_fired` is set *before* sending, so a failure can't cause a retry storm. DMs go to everyone `_attending()` returns, i.e. every response not flagged `is_decline`; the channel ping adds `mention_role_id` if set.
2. **Finishing** — events past `event_time + duration` flip to `completed` and their message is re-rendered grey with buttons removed. This step is unaffected by `recurrence_delay_hours`: the message loses its buttons the moment the event ends, whatever the handover delay is.
3. **Handover** — a **separate** step, `get_events_ready_for_handover()`: a completed, recurring event whose `event_time + duration + recurrence_delay_hours` has passed and whose `handover_fired` is still 0 spawns its next occurrence. `handover_fired` is set *before* spawning, same reasoning as `reminder_fired`, so a failure can't retry the handover every tick; it is also what stops a delayed handover firing twice, since a completed event otherwise satisfies the "past its cutoff" condition on every later tick too. Splitting this from Finishing is what makes the delay possible: without it, completing and handing over were the same step and the next occurrence always went up immediately.

`cog_unload()` cancels the loop; verified not to leak past unload.

**Both stages catch per-item.** `discord.ext.tasks` stops a loop permanently on an unhandled exception — it logs and never runs again — so one transient failure (a database blip during a redeploy, say) would silently kill every reminder and close-out until the next restart. Each item is therefore wrapped individually, and the two fetch queries separately, so a single bad event can't take the tick down. `bot.py`'s `reminder_task` does the same, which is why its body lives in `_send_operation_reminder()`. **Anything added to either loop needs the same treatment.**

### Notes for future changes

- **Time comparisons.** `event_time` is naive UTC. The event queries compare against `NOW() AT TIME ZONE 'UTC'`, not `CURRENT_TIMESTAMP` — the latter is a `timestamptz` and would be cast using the *session* timezone, which is only correct while the server runs in UTC. The older `operations` queries still use `CURRENT_TIMESTAMP`; prefer the new form for anything added.
- **Per-user timezones come free for display.** All times render as Discord timestamps (`<t:…:F>`), which every client localises automatically. Only *input* is guild-timezone based, so a per-user input timezone is the only part still missing.
- **`publish_event(bot, channel, event_id)` is the only place an event reaches Discord.** The slash command, the recurrence hand-over and the web UI all call it, so all three produce an identical message and all three register the persistent view. It deliberately does not catch `Forbidden` / `HTTPException` — each caller discards the event differently (the command explains it, the hand-over stops the series, the web form shows it on the page).
- **Persistence** mirrors `ApprovalView`: `setup_hook()` calls `get_live_events()` and re-adds one `EventRsvpView` per open event. Events without a `message_id` are skipped.
- `_format_attendees()` trims mention lists to Discord's 1024-char field limit and appends "…and N more", while the field *name* keeps the true total.
- `_group_signups()` ignores unrecognised `response` values rather than raising, so a stale key can't break an embed.

---

## Web UI (`web/`)

An optional browser interface for **events**. It is off until configured: `bot.py`'s
`_start_web()` prints which of `DISCORD_CLIENT_ID` / `DISCORD_CLIENT_SECRET` /
`WEB_SECRET_KEY` is missing and opens no listener. Missing dependencies,
`WEB_ENABLED=0` and a crash in `WebServer.start()` are all handled the same way —
**the bot's own job must never depend on the website**.

### It runs inside the bot process

`web/server.py` drives uvicorn programmatically on the bot's own event loop, so a
request handler can call `channel.send()`, `bot.add_view()` and
`guild.fetch_member()` directly. That is the whole reason the earlier plan's
outbox/polling bridge isn't here: there is no second process to bridge to, one
Railway service still runs everything, and a web-created event is posted and
persistent-view-registered before the response is written.

Two consequences to keep in mind:

- `_BotOwnedServer` overrides `install_signal_handlers()` to a no-op. uvicorn
  installs SIGINT/SIGTERM handlers in `serve()`, which would take the process
  down out from under discord.py.
- `ORBATBot.close()` stops the server first. A slow HTTP handler must not
  outlive the Discord connection it is calling into.

### One module per feature (`web/routes/`)

`create_app()` was a single 2,638-line closure holding all 157 routes, every
helper a local, so nothing in it could be imported or tested and a new route was
added by scrolling. The routes now live in `web/routes/`, one module per
feature, each exposing `register(app, d)`; `web/app.py` keeps the setup, the
static files, the error handlers and the **request plumbing** they share, and
hands that over as one namespace.

Three things about the split are deliberate:

- **The bodies moved verbatim.** Each `register()` opens by rebinding what it
  uses — `render = d.render`, `guild_context = d.guild_context` — so not one
  line inside a route changed, and the diff reads as a move rather than a
  rewrite. That unpacking also states, in one place at the top, exactly which
  shared pieces the module depends on.
- **The plumbing stayed as closures rather than becoming FastAPI
  `Depends()`.** Converting would have rewritten the signature of all 157
  endpoints for no behaviour anybody could see. `d` is a `SimpleNamespace`
  built once per app.
- **`maps.py` holds the terrains, and `campaigns.py` the wizard's JSON.** Those
  sections share helpers with the feature above them (`map_context`,
  `map_json`, `require_draw`; `planner_context`, `mission_ctx`), and splitting
  them would have meant promoting private helpers to the shared namespace to no
  end. They are the two large modules as a result.

**`require_admin()` moved into the plumbing**, because six features ask it and
only one defined it. Its message had to stop naming that one: a non-admin who
posted the voice settings was told *"Only a server admin can manage the game
roles themselves."* It now reads *"Only a server admin can change this."*

**What a refactor here has to prove** is that the route table did not move:
157 rows of path, methods and handler name, identical before and after. That
check is worth rerunning on any later reshuffle — it catches a lost decorator
or a double registration, which is exactly what it caught the first time (the
last section swallowed `create_app`'s tail, so `killtracker_service.install()`
ran twice and five public routes were registered twice).

### Auth and permissions

OAuth2 with the **`identify` scope only**. The access token is used once to read
the profile and then discarded — guild membership and roles come from the bot's
own connection instead, so the site can't be tricked by a stale token and needs
no `guilds.members.read` consent.

**The session is a signed cookie** (`itsdangerous`), holding only user id, display
name, avatar hash and a CSRF token. No session table, and a redeploy signs nobody
out. Rotating `WEB_SECRET_KEY` invalidates every session, OAuth state and flash
message at once.

Every permission decision is re-made per request from a live `discord.Member`:

| Action | Who | Same check as |
|---|---|---|
| View events, RSVP, pick own game roles | any member of the guild | — |
| Create an event | Unit Leader or admin | `_is_unit_leader_or_admin()` |
| Edit / cancel / delete | organiser or admin | `_is_organiser()` |
| Add / remove game roles, post the panel | admin | `default_permissions(manage_guild=True)` on the cog's commands |
| Build and post embeds, configure the member log and voice tracking | admin | `is_admin()` — `manage_guild` or `administrator` |
| Build and edit ORBATs | admin | `is_admin()` |
| Edit an ORBAT that a campaign mission links (not copy or delete it) | planners and/or extra roles, per the planner's settings | `may_edit_orbats()` + `cp_orbat_linked()` |
| Watch a Reddit feed | admin | `is_admin()` |
| Start an operation, move its time, post the board or the announcement, empty the queue, choose the channels, set the timezone | admin | `is_admin()` |
| Assign somebody to a slot outright | Unit Leader (own unit, and must have one) or admin | `check_can_assign()` |
| Approve, deny or release slot requests | Unit Leader (own unit) or admin | `_can_action_request()` |
| See the voice leaderboard | any member of the guild | — |
| Read a tactical map | any member of the guild | — |
| Draw on a map, share it, post it, put it on a terrain | Unit Leader or admin | `_is_unit_leader_or_admin()` |
| Upload a terrain, or delete one | admin | `is_admin()` |

Those are the cog's own functions, imported by `web/guilds.py` — the web UI and
the slash commands cannot drift apart on access control.

`resolve_member()` caches for 60 s because `Intents.default()` has no members
intent, so `guild.get_member()` is usually empty and each check would otherwise
cost a REST call. `POST /g/{id}/refresh` calls `forget_member()` so someone who
was just given a role doesn't have to wait it out.

### Answering on more than one name

`WEB_BASE_URL` takes a list, and `config.request_origin(request)` is what
decides which of them a given request is on — from `x-forwarded-host`, since
Railway's own host header is the internal one. The OAuth callback and the
`Secure` flag go through it.

**A link that leaves the site does not.** `public_origin()` / `public_url()`
answer a different question and always give the **canonical** origin, the
first in `WEB_BASE_URL`: a share link is copied into Discord and a posted map
link is read by people who were never on this site at all, so both have to
carry the unit's own domain whatever host the person who made them happened
to be browsing. Otherwise somebody tidying up on the old Railway URL hands
out links back to it, which is how a name you are retiring stays alive. They
fall back to the request only when nothing is configured, which is a local
run. Nothing persists an absolute site URL, so this is decided at render
time every time — **except a map already announced in Discord**, whose embed
keeps the link it was posted with; re-post it to refresh that.

Three things about `request_origin()` are load-bearing:

- **A host nobody configured is not echoed back.** The `Host` header belongs
  to whoever sent the request, so an unknown one falls back to the canonical
  origin rather than being written into a redirect or a link.
- **Every name needs its own redirect URI in the Developer Portal.** Discord
  compares the `redirect_uri` against its list exactly; a name configured here
  and missing there fails that login rather than quietly using the other one.
- **`Secure` is decided per request** (`cookie_secure_for()`), because the
  origins can differ in scheme — a production domain on https beside a local
  run on http — and a Secure cookie set over http never comes back.

### CSRF and redirects

Cookies are `SameSite=Lax`, and every POST additionally carries the session's CSRF
token as a hidden field, checked by `auth.check_csrf()`. The OAuth `state` is
signed *and* mirrored into a short-lived cookie, so a callback this site didn't
start is rejected. `safe_next()` only ever redirects to a path on this origin.

### Routes

```
GET  /                                  login page, or the guild picker
GET  /login  ·  GET /auth/callback  ·  POST /logout
GET  /g/{guild}                         upcoming + recently closed events
GET  /g/{guild}/events/new              create form      POST to create
GET  /g/{guild}/events/{id}             detail + RSVP
GET  /g/{guild}/events/{id}/edit        edit form        POST to save
POST /g/{guild}/events/{id}/cancel      reason, optional stop_series
GET  /g/{guild}/events/{id}/delete      confirmation     POST to delete
POST /g/{guild}/events/{id}/rsvp        toggle, exactly like the buttons
GET  /g/{guild}/roles                   game roles: pick your own, admins manage
POST /g/{guild}/roles                   set your game roles to what is ticked
POST /g/{guild}/roles/add               admin — register/create a game role
POST /g/{guild}/roles/remove            admin — unregister, optionally delete
POST /g/{guild}/roles/panel             admin — post the self-assign panel
GET  /g/{guild}/slots                   the approval queue for the live operation
POST /g/{guild}/slots/{id}/approve      exactly what the ✅ button does
POST /g/{guild}/slots/{id}/deny         optional reason, exactly what ❌ does
POST /g/{guild}/slots/{id}/clear        exactly what /clear-slot does
POST /g/{guild}/slots/assign            admin/UL — exactly what /assign-slot does
GET  /g/{guild}/operation               admin — the running operation and its actions
GET  /g/{guild}/operation/settings      admin — the channels and the timezone
POST /g/{guild}/operation/start         /setup-slots
POST /g/{guild}/operation/time          /set-event-time
POST /g/{guild}/operation/timezone      /set-timezone
POST /g/{guild}/operation/board         /post-orbat
POST /g/{guild}/operation/announce      /post-event
POST /g/{guild}/operation/clear-requests  /clear-requests
POST /g/{guild}/operation/channels      which channels the bot posts into
POST /g/{guild}/operation/slots         /debug-slots — rendered in place
GET  /g/{guild}/orbats                  admin — ORBAT list, POST to create
GET  /g/{guild}/orbats/{id}             the roster editor
POST /g/{guild}/orbats/{id}             action=preview | save | confirm
POST /g/{guild}/orbats/{id}/rename      name and description
POST /g/{guild}/orbats/{id}/duplicate   copy the structure, not the bookings
POST /g/{guild}/orbats/{id}/export      admin — write it into a new sheet tab
POST /g/{guild}/orbats/{id}/delete      cascades to squads and slots
GET  /g/{guild}/embeds                  admin — saved embeds
GET  /g/{guild}/embeds/new              builder          POST to save a draft
GET  /g/{guild}/embeds/{id}             preview, send, delete
GET  /g/{guild}/embeds/{id}/edit        builder          POST to save
POST /g/{guild}/embeds/{id}/send        post as a new message
POST /g/{guild}/embeds/{id}/delete      optionally deletes the Discord message
GET  /g/{guild}/maps                    the guild's tactical maps, POST to create
GET  /g/{guild}/maps/{id}               the map, and the editor for those who may draw
POST /g/{guild}/maps/{id}/save          the document, as JSON — the only JSON route here
POST /g/{guild}/maps/{id}/rename        name and description
POST /g/{guild}/maps/{id}/duplicate     the plan, not the share link
POST /g/{guild}/maps/{id}/share         off / view / edit, or action=new for a fresh link
POST /g/{guild}/maps/{id}/post          announce it in a channel, as a link
GET  /g/{guild}/maps/{id}/arma.sqf      the markers as a script for a live mission
POST /g/{guild}/maps/{id}/ocap-list     read the OCAP directory, list its terrains
POST /g/{guild}/maps/{id}/ocap          read an OCAP map folder: terrain + calibration
POST /g/{guild}/maps/{id}/ocap-places  read the terrain's town names off OCAP
POST /g/{guild}/maps/{id}/places       paste this terrain's town names in
POST /g/{guild}/maps/{id}/terrain       put the map on one of this guild's terrains
GET  /g/{guild}/terrains                the uploaded terrains, POST to upload one
POST /g/{guild}/terrains/{id}/places    replace its town names, wholesale
POST /g/{guild}/terrains/{id}/delete    refused while a map is drawn on it
GET  /t/{id}/{z}/{x}/{y}.png            one tile — no sign-in, cached for a year
POST /g/{guild}/maps/{id}/delete
GET  /m/{token}                         the share link — no sign-in, no guild
POST /m/{token}/save                    only when the link's mode is edit
GET  /g/{guild}/reddit                  admin — the Reddit watches
GET  /g/{guild}/reddit/new              add form           POST to create
GET  /g/{guild}/reddit/{id}             edit form          POST to save
POST /g/{guild}/reddit/{id}/preview     the newest post as it would be announced
POST /g/{guild}/reddit/{id}/check       the scheduled check, run now
GET  /g/{guild}/reddit/{id}/posts       what the feed still carries, to catch one up
POST /g/{guild}/reddit/{id}/announce    post one of them by hand
POST /g/{guild}/reddit/{id}/mark        mark the ticked posts as announced — posts nothing
POST /g/{guild}/reddit/{id}/delete      stop watching
GET  /g/{guild}/logs                    admin — member log settings, POST to save
GET  /g/{guild}/campaigns               planner — campaigns, POST to create one
GET  /g/{guild}/campaigns/objectives    planner — the library; admins POST to add
GET  /g/{guild}/campaigns/objectives/{id}  admin — edit one entry, POST to save
POST /g/{guild}/campaigns/objectives/{id}/delete
GET  /g/{guild}/campaigns/settings      admin — campaign planner settings, POST to save
GET  /g/{guild}/campaigns/{id}          planner — one campaign, POST to save its details
POST /g/{guild}/campaigns/delete-tests  every test campaign, with everything in them
POST /g/{guild}/campaigns/{id}/delete   confirmed by typing the name (a test campaign: one press)
POST /g/{guild}/campaigns/{id}/make-real  a test campaign becomes real, gets its Kill Tracker campaign
POST /g/{guild}/campaigns/{id}/aos      add an AO;  …/aos/{ao} rename, …/aos/{ao}/delete
POST /g/{guild}/campaigns/{id}/map      open the campaign map (made the first time), syncing its AO layers
POST /g/{guild}/campaigns/{id}/aos/{ao}/map  open the AO's map (made the first time), syncing its phase layers
POST /g/{guild}/campaigns/{id}/missions create one, copying the last one's stable details
GET  /g/{guild}/campaigns/{id}/missions/{m}           the wizard (needs JavaScript)
GET  /g/{guild}/campaigns/{id}/missions/{m}/schedule  back-schedule, checks, every drop
POST /g/{guild}/campaigns/{id}/missions/{m}/orbat     action=link | copy | new | unlink
POST /g/{guild}/campaigns/{id}/missions/{m}/drops/{kind}/{action}  approve | unapprove | skip | unskip
POST /g/{guild}/campaigns/{id}/missions/{m}/signups   Start sign-ups now — the same rules as the opening drop
POST /g/{guild}/campaigns/{id}/missions/{m}/recording  action=link | none | auto | stats — the OCAP recording card
GET  /g/{guild}/campaigns/{id}/missions/{m}/export/{pack}.{pdf|docx}  a briefing pack
POST /g/{guild}/campaigns/{id}/missions/{m}/move      up / down;  …/delete
GET  /g/{guild}/cp/api/missions/{m}     the wizard's state, as JSON
PATCH /g/{guild}/cp/api/missions/{m}    save changed fields (+ phases); X-CSRF-Token header
POST /g/{guild}/cp/api/missions/{m}/drops/{kind}/{action}  the wizard's Approve; {hash} seen; answers the state
POST /g/{guild}/cp/api/images           upload one image (multipart, csrf + campaign_id)
GET  /g/{guild}/cp/images/{id}          an uploaded image — planner access only
GET  /g/{guild}/cp/api/objectives       the library with its pairings, for the picker
GET  /g/{guild}/voice                   voice leaderboard; admins also get the settings
POST /g/{guild}/voice                   admin — save the voice settings
POST /g/{guild}/voice/post              admin — post the top 10 into a channel
POST /g/{guild}/logs/invites            admin — label the invite links
POST /g/{guild}/refresh                 drop the cached member
GET  /help                              every how-to, searchable — no sign-in
GET  /help/{key}                        one how-to
GET  /healthz                           'ok' once the bot is ready
```

### Where the logic lives

`web/service.py` owns no rules of its own — it translates form fields and calls
into `cogs/events.py` (`publish_event`, `_parse_responses`, `_refresh_event_message`,
`_spawn_next_occurrence`, `_attending`, the recurrence helpers) and
`utils/database.py`. A `ValueError` raised in there is a message for the user and
is rendered back on the form. **Anything added to the event model belongs in the
cog, not here.**

Two behaviours worth knowing before changing that file:

- **The start time is only passed to `update_event()` when it actually moved.**
  That call re-arms `reminder_fired` whenever it is given a time, and an
  unchanged form submission must not make a reminder fire twice.
- **Emptied fields are cleared through `database.clear_event_fields()`.**
  `update_event()` uses `COALESCE` and so can only ever set a value — the same
  reason `set_event_mentions()` and `set_event_recurrence()` exist. The
  clearable columns are whitelisted in `_CLEARABLE_EVENT_FIELDS`.

`web/roles.py` does the same for game roles, on top of `cogs/gameroles.py`
(`_resolve_game_roles`, `_role_rejection`, `_parse_emoji`, `_apply_role_changes`,
`_update_game_role_panel`, `_build_panel_embed`). Three things there are worth
knowing:

- **`_apply_role_changes()` takes a `discord.Member`, not an `Interaction`** —
  that is why the web can reuse it. Changing it back would fork the one place
  that hands out a game role.
- **Submitted role ids are intersected with the registered game roles** before
  anything is added or removed, so a hand-edited form can't grant an arbitrary
  role. This is the security boundary of that page.
- **`forget_member()` runs after a self-assign**, otherwise the 60 s member cache
  would render the page from the roles the member held *before* the change.

`_plain()` strips `**` and backticks off the cog's messages: they are written for
a Discord message and would otherwise show their markdown literally on a page.

Times are entered and shown in the **guild timezone**: `web/helpers.py` parses
`<input type="datetime-local">` through the cog's own `_parse_event_time()`, so
"local time" means the same thing on the web as in a slash command. The Discord
messages keep using `<t:…>` timestamps and localise themselves.

### Front end

Server-rendered Jinja2 plus one hand-written stylesheet. No build step, no CDN
and no script files — the page has to work from a fresh container with nothing
but the bot's own dependencies installed. The one exception is a handful of
inline `confirm()`s — the ORBAT delete button, and Release/Withdraw on the slot
queue — each of which degrades to acting without the prompt if scripting is off;
nothing else on the site depends on JavaScript. Keep them free of interpolated
data: a display name with an apostrophe in it would end the JavaScript string,
and Jinja's HTML escaping does not reach inside an attribute the browser hands
to the script parser.

**The top bar carries the brand, the guild and you.** `base.html` renders the
guild's name there whenever a page has one, as a link back to that guild — see
[the tab bar](#the-tab-bar-webnavpy) for why it is not a heading on every page.
A long name is truncated rather than squeezing the sign-out control off the bar
(`.topbarstart { min-width: 0 }`).

**Branding is data, not markup.** The name comes from `config.brand` (`WEB_BRAND`,
default `TFP BOT`) and the logo from `_logo_url()`, which looks for
`web/static/logo.*` at startup and returns `''` when there is none — every
template guards on that, so a deployment without a logo file renders the name
alone instead of a broken image. The URL carries the file's mtime so a replaced
logo isn't served from a browser cache. Both are Jinja globals; they don't vary
per request.

**Every stylesheet and script is linked through `static_url()`**, which gives
it the address `/sv/<hash>/<name>`, the hash being of the file's **contents**. The stylesheet was once
linked bare while `campaign.js` carried a version, so a deploy shipped the new
script with browsers still holding the old stylesheet — and the Discord preview
arrived as an unstyled page. Contents rather than mtime, because a build may
reset every file's mtime to one value. Anything added to `web/static/` and
linked from a template goes through it too.

**The hash is in the path, not a `?v=` query.** It was a query until a deploy
went out with the site still serving the previous `tacmap.js` under the new
`?v=` — a cache in front of the site keyed on the path alone — while the
stylesheet beside it had already been refetched, so the editor drew labels the
old way on a page with the new CSS. `/sv/{version}/{name}` serves the current
file whatever the version says (an old page gets the new file, not a 404),
`immutable` only when the hash matches, and `static_file()` refuses any path
outside `web/static/`. `/static/` stays mounted for the logo and old links.

### The approval queue (`web/slots.py`)

`/g/{guild}/slots` — the **Slot Approvals** tab — lists the live operation's
requests: the pending ones with **Approve**, a denial-reason field and
**Withdraw**, the approved ones with **Release**. It is the same decision as the
buttons in `#slot-approvals` and as `/clear-slot`, taken through the same three
functions — see [Approval & Denial Flow](#approval--denial-flow).

Three things about the page are deliberate:

- **A Unit Leader sees the whole queue but can only act on their own unit.**
  `may_action` is re-computed per row from `_can_action_request()`, and a row
  they may not decide shows *"CNTO only"* instead of the buttons. Hiding those
  rows entirely would make the page lie about how many people are waiting.
- **Competing requests are marked `contested`.** Two people wanting the same
  slot is the one case where approving is a choice rather than a rubber stamp,
  and the request rows give no hint of it on their own — in Discord they are
  simply two messages. Approving one still auto-denies the other, as it always
  did.
- **Releasing is offered on both lists, and is one action, not two.**
  **Release** on an approved row and **Withdraw** on a pending one are the same
  `POST …/clear` and the same `clear_slot_request()`; they read differently
  because giving a booking back and taking an undecided request out of the queue
  feel like different things to whoever clicks. That is exactly the pair
  `/clear-slot` already offered in one dropdown. Both carry a `confirm()`,
  because neither can be undone and both DM the member.

`queue()` returns the source line (*ORBAT: Zug-ORBAT*, or *Google Sheet*), so it
is obvious which backend the operation runs on without opening the ORBAT tab.

**Assign is on the same page**, because it is the same audience: it puts
somebody on a slot with no request and no approval, exactly as `/assign-slot`
does. Two things about it are worth knowing:

- **Who** is a free-text box taking a Discord ID, a mention or a name, because
  `Intents.default()` cannot list a guild's members and so there is no dropdown
  to offer. An id or a mention is a `fetch_member()`; a name goes through
  `guild.query_members()`, which is a gateway *search* and needs no privileged
  intent (only listing everybody does). More than one match is reported with
  their ids rather than guessed at — assigning the wrong person means a DM to
  the wrong person and a slot held by somebody who does not know they hold it.
- **The free-slot list is loaded with the queue, and its failure is contained.**
  On a sheet-backed operation that is a network read; if it fails, the assign
  form says so and the queue itself still renders. The queue is the reason to be
  on this page.

`approve()`, `deny()`, `clear()` and `assign()` translate `ActionError` into `ValueError`,
which is the convention the rest of `web/` already uses for "this is a message for the user";
the route renders it as a flash on the same page. The denial reason is capped at
`MAX_REASON = 200`, matching `DenialModal`.

### The tab bar (`web/nav.py`)

Eight flat tabs stopped saying anything: nothing showed that Operation, Slot
Approvals and ORBATs are three views of the same evening. The bar is now two
levels, and `build()` returns the whole structure — the top row, the second row
per group, and the page-key → group map, so a page names only itself and
`_nav.html` works out where that sits.

**The top row has to fit 860px, and that is a measurement rather than a
preference.** `main` is 900px wide whatever the window is, so the row's nine
tabs wrapped onto a second line at *every* width — 919px of tabs in 860px of
bar. Embeds, the member log and the Reddit watches are one **🛠️ Server** group
now (all three admin-only, none of them touched during an operation), and the
two longest labels are *Roles* and *Voice* beside their icons; seven group tabs
plus the `?` come to 824px. So anything added to the top row goes inside a
group, or the row wraps again for everybody. `tests/test_nav.py` holds the
count; the width itself was measured in Chromium against the real stylesheet.

Four things about it are deliberate:

- **Permissions decide what is in the structure, not what the template hides.**
  A group whose every page is out of reach is not built at all, and the group's
  own link is its *first reachable page* — an admin lands on Operation, a Unit
  Leader on the queue, a plain member on Maps, which is the one page in that
  group everybody may open.
- **A group of one renders no second row**, where it would only repeat the tab
  above it. That is exactly the Unit Leader's view.
- **It is built in Python.** The shape of the bar — which groups exist, who sees
  what, where each lands — is precisely what goes wrong when it is spread across
  template conditionals in nine files.
- **The bar is the top of the page.** It used to sit under an `<h1>` of the
  guild's name and a *Signed in as …* line, repeated identically on all 21
  pages — a heading that said nothing, over an avatar and a name the top bar
  was already showing. The guild's name is in the top bar now, as what it is
  (which server you are looking at), and each page's own name is its `<h1>`,
  under the tabs, where ten of them used to repeat it in the first `<h2>`.
  Measured in Chromium: a page's content started **353px** down and now starts
  at **238px**.

### The help pages (`utils/help.py`)

Fifty-eight how-tos — one per thing somebody might want to do — served at `/help`
and written into the README by `scripts/gen_help.py`. The module is data plus a
renderer; there is no `web/help.py`, because the two routes have no permissions
to check and no form to translate, which is all such a module has ever been for
here.

**One copy, two surfaces.** That is the same bargain `approve_slot_request()`
makes behind a button and a web page, applied to the documentation: the README
section between `<!-- help:start -->` and `<!-- help:end -->` is generated, and
`python scripts/gen_help.py --check` fails when it has fallen behind. Editing
that section by hand is the one thing not to do — the next run overwrites it.

Four things about it are deliberate:

- **`tests/test_help.py` reads `cogs/` with a regex.** Every slash command the
  cogs register must be covered by a topic, and every command a topic claims
  must exist. So a command added without a how-to is a failing test rather than
  something noticed a year later, and a renamed one cannot leave the docs
  telling people to run something that is gone. A companion test asserts the
  regex matches more than twenty commands, because a coverage test that
  silently matches nothing passes for the wrong reason.
- **Every key in `web/nav.py` must have a topic too**, which is what makes the
  `?` at the end of the tab row safe to render unconditionally. A page names
  itself with the key the nav already gave it and gets its own how-to; it
  cannot forget to, and it cannot point at a page that does not exist.
- **The help is not permission-filtered.** Audience is a label on the topic
  rather than a check, and the pages are reachable **signed out** — a wiki that
  hides half of itself is how people conclude a feature does not exist, and
  somebody stuck at the sign-in screen is exactly who needs to read one.
- **`inline_html()` escapes first and marks up second.** The topics are written
  in this file rather than typed by anybody, so it is not a sanitiser — but a
  future how-to about `<Insert Name>` markers must render the `<` rather than
  eat the rest of the sentence, and the result is marked safe on the way out.
  `test_every_topic_renders_without_stray_markup` catches an unbalanced `**`
  or backtick across all of them, which is the kind of thing nobody spots by
  reading.

`utils/help.py` imports nothing but the standard library, like `utils/orbat.py`,
`utils/tacmap.py` and `utils/reddit.py`. Keep it that way — it is the only
reason any of the above is testable.

### The Operation page (`web/operations.py`)

`/g/{guild}/operation` — admin only, under the **Operations** group. What is
running now, its start time, and then one collapsed panel each for posting the
board, posting the announcement, emptying the queue, reading the raw roster, and
starting a different operation.

**The panels are `<details>`.** Native, so they work with scripting off like
everything else here, and they are what keeps the page short: the status and the
start time are the only things anybody looks at most weeks. `operation_page()`
takes a `panel` argument so a form that comes back with an error re-opens the
section it came from, rather than folding away with what the person typed still
in it.

**The channels and the timezone are on `/operation/settings` instead.** They
belong to the server rather than to an operation and are set once; having them
here put three channel dropdowns on one screen, which is what made the page read
as repeating itself. `overview()` therefore no longer returns them.

Like the approval queue, the page owns **no rules** — every form calls into
`cogs/admin.py` or `cogs/slots.py` (see
[Running an operation](#running-an-operation-cogsadminpy)) and turns an
`ActionError` into the `ValueError` the route flashes. `operation_action()` in
`web/app.py` is the shape every form shares: check CSRF, run one service call,
flash what came back or re-render with the error.

`/debug-slots` is the one exception to that shape — it renders **in place**
rather than redirecting, because a flash message is the wrong container for
forty lines of output.

**The channel form's empty option is "Default", not blank.** Nothing chosen and
"chosen, and it happens to be #orbat" mean different things the day somebody
renames a channel, so the page says which one is in force; a stored channel that
has since been deleted is called out rather than quietly showing the default.

### Deliberately not covered yet

- **Booking a slot from the web** — an ORBAT can back a live operation, and the
  whole admin half is on the web now, but the *requesting* itself is still
  Discord-side (`/request-slot`, `/change-slot`, `/cancel-request`,
  `/leave-operation` and the board's button), and is meant to stay there.
- **`/sync`, `/restart` and `/archive-old-approvals`** — bot maintenance and a
  one-time migration rather than slot work, so they stayed slash commands.
- **Moving an event to another channel** — the message would have to be deleted
  and reposted, losing the sign-up history's continuity; cancel and recreate.
- **Per-user input timezones** — display is already per-user via Discord
  timestamps, but everything typed in is guild-timezone based.

### Slots on the web — what is done and what is left

Done: the tables, the editor at `/g/{guild}/orbats`, `operations.orbat_id`, and
the provider that makes `cogs/slots.py` blind to where the roster came from. An
operation runs on an ORBAT today.

What remains, in the order it makes sense to do it:

- Booking someone who is not on Discord — `requests.member_id` is read as a
  snowflake in six places: the two archive-embed mentions in `cogs/slots.py`,
  the `fetch_member()` in `_dm()`, the one in `/clear-slot`, and the fetch plus
  the `<@…>` ping in `bot.py`'s reminder loop. It needs to become nullable with
  each of those guarded (pulling the button callbacks into `_dm()` already
  removed three of them)
- Click-a-slot-to-request on the web, posting to `#slot-approvals` as today —
  approve/deny from the web is done, see
  [the approval queue](#the-approval-queue-webslotspy)
- Importing an existing sheet into an ORBAT once, via `sheets.load_all_slots()`,
  mapping live requests onto the new slots by `slot_label`
- An operation archive
- Live updates were sketched as Server-Sent Events; running in-process makes that
  straightforward, since the request handler already sees every change

---

## ORBATs (`utils/orbat.py` + `web/orbat.py`)

The slot roster, built and maintained in the browser instead of in a Google
Sheet. Admin-only, one tab on the guild page.

**An operation can be run on one.** `/setup-slots orbat:<name>` starts an
operation against an ORBAT instead of a sheet, and from there the whole flow —
requesting, approving, the live board, `/assign-slot`, `/clear-slot` — works
against the database. See
[the roster provider](#the-roster-provider-utilsrosterpy) for how the two
backends meet.

### Why a text field and not a slot editor

`web/` has no JavaScript and no build step, which rules out drag-and-drop. The
alternative — an up/down button per row — is worse than the sheet it replaces at
forty slots. So the editor is one indented-text field, which is how ORBATs get
written down anyway:

```
1-1 Alpha  | right, unit:TFP, radio:343 CHN:3
  Squad Leader
  Rifleman

Reservists  | right, nocount
  Reserve
```

Squad lines start at the left margin, slots are indented (space or tab). Options
after a pipe, all on the squad: `left` / `right`, `unit:TAG`, `radio:…`,
`nocount`. `#` starts a comment.

Options are **separated by commas**, and that is what the help text tells people
to write. `_peel_options()` also reads them without one: it splits before a
keyword that takes a value (`unit:`, `radio:`, `net:`) and peels the standalone
keywords off either end, so `| left unit:CNTO` is two options rather than one
unknown one. Both rules key on names this module already knows, which is what
keeps a value containing spaces whole — `unit:2nd USC` and `radio:152 CHN : 1`
are followed by neither. Without this the whole chunk read as one unknown
option and the column and the unit were lost together, in silence.

**`_split_options()` keeps an option's case** and lower-cases only the keyword
when matching. It used to lower-case the whole list, which turned a channel
written `343 CHN:3` into `343 chn:3` on the way in.

**The unit tag is stored as typed** and spelled by `web/orbat.py` against the
real `UNIT_ROLES` — `unit:tfp` becomes `TFP`, `unit:2nd usc` becomes `2nd USC`.
Upper-casing it in the parser would have rendered a unit genuinely called
`2nd USC` as `2ND USC`.

**The unit is per squad, not per slot.** A squad belongs to a unit as a whole —
that is how the rosters are actually organised — so tagging every line of it
would be the same tag repeated six times. `utils/orbat.py` takes the tag as free
text and knows nothing about units; `web/orbat.py` warns when it matches none of
`UNIT_ROLES`, as a warning rather than an error because a unit could be renamed
in Discord tomorrow and a roster that stops saving over that would be worse. A
`unit:` written on a slot line produces a warning naming the squad it belongs on,
rather than being dropped in silence. A leading `1.` / `2)` / `3 -` is stripped, so lines pasted out
of a sheet land clean — the number is load-bearing there (it keeps two "Rifleman"
cells apart) and noise here, where every slot has an id.

The price is that there is no live preview: **Preview** is a button. That is the
one thing to weigh if this ever gets reconsidered.

`assign_columns()` splits the squads down the middle when nobody wrote `left` or
`right`, reproducing what the sheet reader infers from column geometry. One
explicit marker turns the guessing off for the whole ORBAT.

### Which ORBAT is live

An ORBAT is a template and a guild can hold several, so **which one backs the
operation running now** is the first thing you need from the list.
`get_guild_orbats()` carries `live_operation` — the active operation's name, or
NULL — as a correlated subquery, and only one row can ever have it, because a
guild has one active operation. It is said in three places, all of which are
"you are about to act on tonight's board":

- the ORBAT list, as a 🔴 chip naming the operation
- the editor, as a banner, because an edit here changes the live board
- the Operation page's start dropdown, so restarting on the same ORBAT is a
  deliberate choice rather than a surprise

`orbat_live_operation(orbat_id)` is the single-row form the editor uses.
`web/orbat.delete()` already refused a live ORBAT; the banner means you know
before you get there rather than after.

### The edit must never unseat anybody silently

A slot's id is what a booking hangs off, so re-parsing the text cannot drop every
slot and recreate it. `build_diff()` matches in three passes, most to least
confident: squads by name, then leftover squads pairwise by position (a rename);
inside a matched squad, slots by role name in order, then leftover slots pairwise
by position (a rename). A rename therefore keeps the id, and with it the booking.

Two properties fall out and are worth keeping:

- **Reordering is free.** Moving lines around matches every slot by name in
  pass 1, so nothing is added or removed.
- **Duplicate role names pair up in order.** Three `Rifleman` lines cut to two
  keep the *first two* existing slots, so the booked one is not the casualty.

`needs_confirmation` is deliberately wider than `destructive`. Removing a booked
slot is destructive. Renaming one is not — nobody is unseated — but the person's
role changes under them, which is right when the edit was a typo fix and wrong
when it was meant as a replacement. The text cannot tell those apart, so both
stop at a confirmation page naming the people affected. Everything else saves
straight away: asking on every edit trains people to click through the one that
matters.

**Applying a removal releases the booking.** `apply_orbat_structure()` sets those
requests to `cancelled` with `slot_id = NULL` before the `DELETE`, and does the
same for the slots a deleted squad cascades away. `slot_id` carries no foreign
key, so without that the request would survive as an approved booking pointing at
a slot that no longer exists — invisible on every board, and a contradiction of
what the confirmation page just promised.

**Deleting the whole ORBAT obeys the same rule, and refuses outright while it is
live.** `web/orbat.delete()` checks `database.orbat_operations()` first: an ORBAT
running the guild's **active** operation cannot be deleted, because the cascade
would take tonight's entire board with it and no confirmation prompt makes that
recoverable. Once that operation is over, deleting is allowed and
`database.delete_orbat()` releases the bookings in the same transaction as the
`DELETE`, exactly as an edit does — `operations.orbat_id` and `requests.slot_id`
both carry no foreign key, so nothing else would.

### The net list is a second field, not part of the roster

The shared nets are their own textarea and their own parser (`parse_nets()`),
one net per line as `Platoon Net | 152 CHN : 1`. A leading `-` marks a net that
is planned but not in use, rendered struck through — the same convention
`cogs/events.py` uses for a decline response.

They are kept out of the roster grammar deliberately. A net has no identity for
an edit to lose, so it needs none of the diff machinery above and is simply
replaced on save; folding it into the roster text would put a second grammar
into the one parser that must not get slots wrong. Their problems are also
reported in their own panel, so a line number means something: line 3 of the
roster and line 3 of the nets are different places.

### Exporting to a sheet

`sheets.export_orbat()` writes the roster into a **new tab** of a spreadsheet
and never touches an existing one — that is the whole safety story, because an
export must not be able to overwrite the sheet another operation is running on.
A title collision gets a `(2)` suffix rather than replacing anything.

The layout is the one `load_slots()` reads: a squad header, then `N. Role`
beside `[] <Insert Name>` or `[TAG] Name`. Two consequences worth knowing:

- Moved to first position, an exported tab reads back **exactly** like a sheet
  the bot had been filling in itself — `assign_slot()` writes `[TAG] Name` into
  the cell the same way, and `_extract_role()` keeps the number prefix on
  purpose so two `Rifleman` slots stay apart.
- It is still one-way. `load_slots()` only ever reads `sheet1`, so an exported
  tab is never picked up on its own; nothing is stored about the export either.

The unit rides in the squad header (`1-1 Alpha [TFP]`) and the radio goes on its
own line below it, where `_is_squad_header()` skips it as a frequency — so the
squad name above stays the header for the slots underneath.

### The board, and Discord's limits

`build_board()` groups squads into the same left/right columns
`_build_orbat_embed()` uses, with the same 🟢/🟡/🔴 line per slot and the same
counted header. The difference is where the layout comes from: the cog infers it
from the sheet's geometry, this reads `column_side` off the squad.

`check_limits()` has no counterpart today. An ORBAT built in a browser can
outgrow what Discord will render — 25 fields, 1024 characters per field value,
6000 per embed — and finding that out when the board silently loses its last
three squads is too late. The editor says so while it can still be changed.

The net list rides along as **one more field**, which is what makes eight rows
the cap rather than eight-and-a-bit: 8 × 3 + 1 is exactly 25.

### Notes for future changes

- **`utils/orbat.py` imports nothing.** No discord.py, no FastAPI, no asyncpg —
  which is why the parser and the diff are the only tested code in this repo
  (`lab/tests`, 47 cases). Keep it that way: these are the two places where a
  bug silently deletes slots.
- **`lab/` is the same code.** The standalone playground that prototyped this
  re-exports `utils/orbat.py` rather than keeping a second copy; a drifting
  parser would be the worst possible bug here. It has no other reason to exist
  and can be deleted whenever.
- **The editor page steps outside the 900px column** via `.widepage`, because
  the text field and the preview do not fit side by side inside it. It is the
  only page that does.

---

## Tactical maps (`utils/tacmap.py` + `web/tacmap.py`)

The plan, drawn on the terrain — unit symbols, movement lines, objectives and
boundaries — built in the browser and shared with a link, the way the mission
planners in this hobby already work. Reading one is open to every member;
drawing, sharing and posting need the same rights as creating an event.

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

- **`utils/tacmap.py` imports nothing but the standard library**, like
  `utils/orbat.py` and `utils/reddit.py`. Keep it that way: it is the only
  reason the document rules and the escaping are tested at all
  (`tests/test_tacmap.py`).
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

## Reddit announcements (`utils/reddit.py` + `cogs/redditfeed.py` + `web/reddit.py`)

A watch on a Reddit user or a subreddit that announces every new post in a
channel, with the guild's own wording and whoever it wants pinged. Admin-only,
one tab on the guild page, and no slash command — the text and the ping list are
a form, not something to type into a modal.

### It announces. It does not ask for votes.

A Discord message asking people to go and upvote a post is **vote manipulation**
under Reddit's content policy, and it is one of the few things Reddit acts on
hard: not only the posting account but the accounts that reliably answer the
call, because the same group voting minutes after the same author posts is
exactly what the voting timeline shows. Nothing in the wording avoids that, so
there is no vote wording anywhere here: not in `DEFAULT_TEMPLATE`, not in
`TEMPLATE_EXAMPLES`, and the help text under the field says so. Asking people to
read and comment is fine, and weighs more in Reddit's own ranking anyway.

The post's score is deliberately never read, rendered or referred to — a feature
that displays it is one step from a feature that asks people to change it.

### No API registration

`utils/reddit.py` reads the public Atom feed — `/user/<name>/submitted.rss` or
`/r/<name>/new.rss` — which is the same page a browser gets, not the OAuth API
surface. So there is no client id, no secret and nothing to register. It does
want `REDDIT_USER_AGENT`: Reddit rate-limits a client that doesn't identify
itself down to nothing.

### Being refused is about us, not about the feed

Identifying ourselves is not always enough, and this is the thing to know before
changing any of it. **Reddit also refuses the address the request comes from.**
A hosting provider's IP — Railway's included — is turned away from the public
feeds with 429 however politely it asks and however rarely it asks, so this is
not a frequency that can be tuned down into working. One watch polled every five
minutes is 288 requests a day, which is nothing; the refusal is not about that.

Two things follow, and both are load-bearing:

- **`fetch()` tries both hosts.** `www.reddit.com` carries the bot detection;
  `old.reddit.com` is the legacy renderer and is markedly less fussy about who
  is asking. The second one is only ever asked on a check that has already been
  refused, so the normal case is still one request. A 404 or a 403 is *not*
  retried there — those are about the feed and say the same thing from either
  host.
- **A refusal stands the watch down.** `RateLimited` carries a wait (Reddit's
  own `Retry-After` where it gives one, bounded, otherwise
  `DEFAULT_RETRY_AFTER`), `check_feed()` writes it to `retry_at`, and
  `get_due_reddit_feeds()` skips the row until it passes. Asking again on the
  next tick is what turns a passing throttle into a standing one. **Check now**
  ignores the wait, because a person pressing a button may try; the loop may
  not.

If a watch is refused from both hosts every time, the feed is not the way in
from that host and the OAuth API (a script app, `oauth.reddit.com`) is — which
is a different fetcher, not a different frequency.

`utils/reddit.py` imports **nothing but the standard library** — `aiohttp` is
imported inside `fetch()`, the same trick `utils/sheets.py` plays with its
credentials — which is what makes the parsing and the templating testable
(`tests/test_reddit.py`).

### What comes back is not always a feed

`fetch()` hands `parse_feed()` the **raw bytes**. An XML document declares its
own encoding and that declaration is the authority — not the HTTP header, and
not a guess made from the bytes. Decoding first (`response.text()`) put a third
party in charge of it, which is how one accented character in a post title turns
into `not well-formed (invalid token): line 20, column 195`.

Three kinds of not-a-feed, told apart because they need different answers:

- **An HTML body is a refusal in disguise.** Reddit serves its block page with a
  200, so `parse_feed()` sniffs for one and raises `RateLimited` — the same
  thing a 429 means, handled the same way. (It is worth knowing that such a page
  fails XML parsing as *mismatched tag*, not *invalid token*, so the two
  symptoms are genuinely different problems.)
- **Characters XML forbids are dropped.** No valid feed can contain a control
  character, so removing them cannot change a well-formed document — and one
  stray byte in a title must not cost the whole feed.
- **Anything else raises `NotAFeed`**, which quotes the fragment it choked on as
  `repr()`. The line and column mean nothing to whoever reads the flash message;
  the characters themselves separate a stray `&`, an invisible byte and a
  mis-encoded one at a glance. `NotAFeed` is also retried on the other host,
  because a body that isn't the feed is usually about who is asking — but it
  does not stand the watch down, since it is not a refusal.

Two details in the parsing are load-bearing:

- **`published` beats `updated`.** Editing a post moves `updated` and leaves
  `published` alone; reading `updated` would make an edit look like a new post.
- **An entry with no id or no link is skipped, not fatal.** One malformed entry
  must not cost the rest of the feed.
- **The category's `term` beats its `label`.** Reddit writes them as
  `term="arma" label="r/arma"`, and for a post made on the author's own profile
  as `term="u_Name" label="u/Name"`. The term is already what belongs after an
  `r/` in both cases — a profile post really does live in `r/u_Name` — while the
  label would render as `r/u/Name`, which is not a place. A user watch carries
  both kinds, since `/user/<name>/submitted.rss` is everything that author
  submits anywhere.

`render()` substitutes `{title}`, `{url}`, `{author}` and `{subreddit}` with one
literal replace each rather than `str.format()`: a template is text somebody
typed, so a stray `{` in it has to be harmless.

### Following an author through a subreddit

A `user` watch reads that account's **profile listing**, and Reddit lets an
account hide its posts from its own profile (Settings → *Curate your profile* →
*Content and activities*). The posts stay where they were written — a subreddit
lists them whatever the profile says — so the way in is to watch the subreddit
and keep only what that account wrote. `author_filter` is that filter, and
`reddit.by_authors()` applies it.

It is applied in `check_feed()` **before anything else looks at the posts**, so
`seen_ids` only ever holds posts this watch would announce and `last_post_at` is
the newest *matching* one. The remembered window is therefore effectively much
longer than `MAX_SEEN` on a busy subreddit, since the other authors' posts never
enter it.

Two things it cannot do anything about:

- **A busy subreddit can push a post out of its own feed between two checks.**
  `new.rss` carries about 25 posts; a subreddit that gets more than that in five
  minutes can lose one. The form says so rather than pretending otherwise.
- **Widening the filter makes old posts new.** Nothing is reset when it changes
  — narrowing it should lose nothing — so `save()` warns and points at *Recent
  posts → Mark all* instead, which is the same answer as any other backlog.

`remember_posts()` deliberately holds the feed **unnarrowed**: announcing one by
hand is an explicit choice and should be able to reach a post the filter skips.

### The first read announces nothing

`seen_ids` is NULL until a watch has been read once, and that is the whole
difference between the first read and every later one: the first records what is
already on the feed and posts nothing. Without it, switching a watch on would
dump the author's last 25 posts into the channel.

It is a rolling set of post ids rather than a high-water timestamp for the same
reason `published` is preferred above — a timestamp moves when a post is edited,
an id doesn't.

`MAX_PER_CHECK` caps one check at three announcements. Nothing is dropped: the
ids of the posts that did go out are the only ones marked seen, so the rest
follow on the next check.

### `check_feed()` is the whole implementation

The polling loop calls it and so does the **Check now** button, exactly as the
approval buttons and the web queue share `approve_slot_request()`. It takes a
feed row and the bot, and it knows nothing about interactions or forms; both
`reddit.FeedError` and `ValueError` carry a message meant for a person, which
the loop prints and the web page flashes.

Its two send-failure paths differ on purpose:

- **`Forbidden`** leaves the post unannounced. A permission problem is fixable,
  and the post goes out on the next check once it is.
- **Any other `HTTPException`** marks the post announced anyway. Retrying a
  message Discord refuses, every five minutes for ever, would wedge the watch
  behind one bad post.

**`allowed_mentions` names exactly the roles and people the watch stores.** The
post title goes into the message verbatim, so a title containing `@everyone`
would otherwise ping the whole server — the announcement is quoting Reddit, not
speaking for the admin who set the watch up.

### The page

`/g/{guild}/reddit` lists the watches; the form adds and edits one. Three things
about it are worth knowing:

- **People are a free-text box of user ids**, for the same reason the assign form
  on the slot queue takes one: `Intents.default()` cannot list a guild's members,
  so there is no dropdown to offer. Roles are checkboxes, as everywhere else.
- **Preview posts nothing and marks nothing as seen**, and renders what is
  *in the form* rather than what is stored — it is meant to be pressed while
  working on the text. **Check now** is the opposite: it is the scheduled check,
  run early, and it does announce.
- **Pointing a watch at a different source resets `seen_ids`**
  (`reset_reddit_feed_seen()`), so the new feed is seeded on its next read
  instead of announcing its history. The page says so.

### Catching a post up by hand

`/g/{guild}/reddit/{id}/posts` lists what the feed still carries, says which of
them have been announced, and gives each an **Announce** button —
`announce_post()`, which is the second thing besides `check_feed()` that sends
one of these messages. It exists because there are three ways a post ends up
never reaching the channel and none of them heals itself: the bot was down when
it went up, Discord refused that one message (which `check_feed()` marks as
announced *on purpose*, so one bad post can't wedge the watch for ever), or the
watch was pointed somewhere and seeded past it.

The page says **which address it read and how many entries came back**, and
names where each post lives — `r/arma`, or *the author's own profile* for one in
`u_Name`. Both exist because "the bot only found one post" and "that address
only carries one post" are different problems, and this is the only place the
difference can be seen. A `u_Name` post rendered as `r/u_Name` reads like a bug
next to `r/arma`, which is what hid the question in the first place.

**The same page marks posts as announced without posting them** —
tick the ones to skip, or **Mark all**. That is the way past a flood: a watch
whose feed suddenly fills up (an account that un-hid its posts, a subreddit that
woke up) would otherwise work through the backlog `MAX_PER_CHECK` at a time
until all of it had been announced. On a watch that has **never been read** it
marks everything the page listed whatever was ticked, because its first check
would have done exactly that — marking only the ticked posts would leave the
rest queued, which is the flood being prevented.

**`mark_announced()` reads nothing.** The ids come from the page, which has just
read the feed; going back to Reddit to write down a decision somebody has
already made fails at the one moment it matters — when Reddit is refusing us,
which is when a backlog most needs marking. It writes through
`set_reddit_feed_seen()` rather than `record_reddit_read()` for the same reason:
marking is bookkeeping, not a read, so it must not clear `last_error` or the
stand-down, which say something about Reddit rather than about what has been
announced.

**The rest of the page reuses that read too.** `remember_posts()` holds what was
last read for `RECENT_TTL`, and `announce_post()` takes it from there rather
than asking again — pressing a button on the page is the second half of one
interaction. `check_feed()` deliberately does not use it: the scheduled check
must see the feed as it is now.

The ticking form holds no rows of its own. The checkboxes point at it with the
`form` attribute, which is what lets each row keep its own **Announce** form
without nesting one form inside another.

Two things about it are load-bearing:

- **It marks the post seen**, so the next scheduled check doesn't announce it
  again — and on a watch that has **never been read** it seeds the rest of the
  feed at the same time, exactly as a first read would. Without that, catching
  one post up by hand would make the next check announce the other twenty-four.
- **A send that fails records nothing.** The point of the button is to get the
  post out; if it didn't go out, nothing should be stored as though it had.

It is its own page rather than a panel on the watch form, because opening it
reads the feed — and the form has to open without touching Reddit, not least
when Reddit is refusing us.

---

## Embeds (`utils/embeds.py` + `web/embeds.py`)

Rich messages built in the browser — the server-info and rules posts that would
otherwise be written by hand. There are no slash commands for these: composing an
embed in a Discord modal is unpleasant, and the web form already exists.

**A stored embed is a description, not a message.** `embeds` holds the columns and
`embed_fields` the fields; `build_embed()` turns a row into a `discord.Embed`.
`message_id` is NULL until it is posted, which is the whole point of the split:

- `post()` sends a new message and records where it landed.
- `edit_posted()` updates that message. Saving an edit in the web UI calls it, so
  a pinned server-info post is updated **in place** instead of being reposted.
- A message deleted in Discord makes `edit_posted()` clear `message_id`, turning
  the embed back into a draft rather than failing the same way forever.

**Discord's limits are enforced on save, not on send** (`validate()`). A rejected
send would otherwise leave a stored embed that can never be posted, and the person
who wrote it is no longer looking at the form by then. The same function refuses an
embed with no title, description, image or fields, which Discord rejects outright.

`clean_color()` normalises to `#rrggbb` on input; `parse_color()` is deliberately
forgiving on read, since a stored value can only have come from `clean_color()`.

The builder offers `MAX_FIELDS = 10` fixed field slots. Discord allows 25 — the
limit here is the form, which has no JavaScript to add rows with.

---

## Member log (`cogs/memberlog.py`)

Announces joins, leaves, kicks, bans and unbans in a channel chosen per guild
(`log_settings`). Nothing is posted until a channel is set; each event type has its
own flag.

### Welcome messages are a separate, member-facing thing

The join log above is written for staff — one line in whatever channel they
picked, saying who joined and how. `on_member_join` also greets the member
directly, through `_send_welcome()`, and the two are deliberately independent:

- **A different channel, or none.** `log_settings.welcome_channel_id` is its
  own column, not `channel_id`. The log channel is often a private mod channel;
  a welcome message wants a public one, or no channel at all if only the DM is
  wanted.
- **Or a DM, or both, or neither.** `welcome_dm` sends the same rendered text
  to the member directly. A `discord.Forbidden` there — DMs closed, or no
  shared guild left to send through — is caught and dropped silently, the same
  as a channel post Discord refuses.
- **Neither destination depends on the join log being switched on**, or on
  `log_join`, or on the members intent being anything other than what it
  already is — the welcome message uses the same `on_member_join` listener, so
  it needs the same privileged intent to fire at all, but nothing about
  `log_settings.channel_id` or `log_join`.
- **`render_welcome()` is one literal replace per placeholder**
  (`WELCOME_PLACEHOLDERS`: `member`, `name`, `server`, `member_count`), the
  same reasoning as `reddit.render()` — the template is text an admin typed,
  not a format string, so a stray `{` in it must stay harmless. An empty
  template falls back to `DEFAULT_WELCOME_TEMPLATE`. `{member}` is the only
  placeholder that pings; the others are plain text.
- The web form for both — the join log and the welcome message — lives on the
  same `/g/{guild}/logs` page and posts as **one form**, so a save always
  carries every checkbox; splitting it into two forms would silently zero out
  whichever section wasn't submitted.

### The intent is opt-in, and that is deliberate

`on_member_join` and `on_member_remove` are **privileged**. Requesting an intent
that isn't ticked in the Developer Portal makes `bot.start()` raise
`PrivilegedIntentsRequired` — the bot would not come up at all. So `bot.py` only
sets `intents.members` when `MEMBER_EVENTS` is truthy, and the cog loads either
way: bans and unbans are not privileged and work without it.

**Never turn the intent on unconditionally.** A deploy that lands before the portal
switch is flipped would take the whole bot down, slots and all.

### Telling a kick from a leave

`on_member_remove` fires for a voluntary leave, a kick and a ban alike. The audit
log is the only way to tell them apart:

- The listener waits `AUDIT_DELAY` seconds first, because the audit entry appears a
  moment after the event.
- A **ban** entry means `on_member_ban` will report it, so this path stays silent —
  otherwise every ban is logged twice. Checking the audit log for both actions makes
  it independent of which gateway event arrives first.
- `AUDIT_WINDOW` guards against an old kick of the same user being mistaken for
  this leave. Anything older is treated as unrelated.

Without **View Audit Log** every kick reads as a plain leave and bans have no
moderator — deliberately not an error, just less detail.

### Invite attribution

`_invites` caches each guild's invite use counts; a join re-reads them and the
code whose counter went up is the one that was used. `_invite_lock` serialises
that, or two joins seconds apart would each credit the other's increment. Two
joins in the *same instant* still can't be told apart — the counter is corrected
either way, only that one attribution may be wrong.

**`_used_invite()` returns an `Attribution`, and it always explains itself.**
When it can't name a link it sets `reason`, and the join embed prints that
instead of dropping the field: a missing line looked identical whether the bot
lacked **Manage Server**, had never read the invite list, or genuinely saw no
counter move, which made the feature impossible to debug from Discord. The
reasons are in `REASONS`; `off` is the only one that prints nothing, since an
admin who unticked the box does not need telling on every join.

Reading invites needs **Manage Server**. `_fetch_invites()` returns **None**
rather than an empty list when it may not read them — the two must stay
distinct, because diffing a join against an empty snapshot reads every existing
invite as freshly incremented and credits the first one seen. The same reason
`before is None` reports `nocache` rather than guessing.

A guild with `VANITY_URL` falls back to `vanity_invite()` when no counter moved,
labelled `vanity` so it isn't looked up like an ordinary code.

**Naming the link and naming who made it are two lookups against two different
permissions**, which is why `_used_invite()` keeps them apart: `_match_invite()`
needs **Manage Server**, and `_invite_creator()` needs **View Audit Log**. Either
can succeed without the other. A live invite already carries its `inviter`, so
the audit-log walk only runs for a link that is already gone — the single-use one
an admin made for one person, where who sent it is the whole answer. It is
skipped for a vanity URL, which is a guild setting and has no `invite_create`
entry to find. One walk caches every code it passes, misses included, so a code
the log can no longer reach doesn't cost a scan on every join; a walk that
*fails* is not cached, so a permission granted afterwards takes effect at once.

**A single-use link never shows its increment.** Discord deletes an invite the
moment it hits `max_uses`, so the code that let the member in is simply gone.
Two paths find it, because `INVITE_DELETE` and `GUILD_MEMBER_ADD` race: if the
delete arrived first the code is in `_deleted`, and if it hasn't the code is
still in the cached snapshot but already absent from the freshly fetched list.
Both are bounded by `CONSUMED_WINDOW` — an unbounded "gone since the last
snapshot" would credit a link an admin tidied up yesterday to the next person
through the door. Exactly one candidate is credited (kind `consumed`); two is
ambiguous and reported as unknown. `_inviters`, filled by `on_invite_create`,
is how such a join can still name who made the link; the ordinary path reads
`inviter` off the live invite object.

**`invite_labels` is what makes the code useful.** The join message shows the
label next to the code, which is the whole point: the alternative is keeping a
spreadsheet of which link was posted where and consulting it by hand. A shared
link reads *created by*, a consumed one *invited by* — the first is used by many
people, the second was made for the one who just walked through it.

Every listener body is wrapped in a `try`/`except` that prints and moves on: a
failure to *log* an event must never propagate into the gateway handler.

---

## Purge (`cogs/purge.py`)

`/purge [amount] [since]` deletes messages in the channel it is run in. `amount`
takes the newest 1–`MAX_MESSAGES` (1000); `since` takes everything posted after a
point in time. Both together means "at most this many, and nothing older than
that" — at least one is required, since a bare `/purge` has no sensible default.

`_parse_since()` accepts an age (`30m`, `2h`, `7d`, `1w`), a bare date, which
means midnight in the guild's timezone, or a date and time, which goes through
`admin._parse_event_time()` so `/purge` and `/event-create` read the same
formats. It returns an **aware** UTC datetime, unlike `event_time` elsewhere in
this codebase, because it is compared against `Message.created_at`.

### The 14-day rule shapes the whole command

Discord's bulk endpoint refuses messages older than 14 days, and rejects the
**entire batch** if one of them is — hence `BULK_MARGIN`, which keeps a message
that crosses the line between the preview and the delete call out of the batch.
Older messages are deleted one REST call at a time, roughly one a second, so
they are capped at `MAX_SLOW_DELETES` (200) per run and the reply says how many
were left for the next one. The cap is applied to a newest-first list, so a
second run continues where the first stopped.

`discord.Forbidden` is a subclass of `discord.HTTPException`, which is why both
delete helpers catch it **first** and return a `blocked` flag: without that,
permission taken away mid-run would fall into the "salvage the batch one at a
time" path and spend 100 doomed REST calls per chunk.

### Other things worth knowing

- **The confirmation holds the messages, not the query.** `ConfirmPurgeView` is
  built from the exact list the preview counted, so anything posted while the
  admin reads it is left alone — deletion can't be undone, and widening the set
  after the preview would be the one way to delete something nobody saw listed.
- **`_collect()` walks newest-first and breaks at `since`**, rather than passing
  `after=` to `history()`. discord.py would keep paging through older history
  until the limit is exhausted, so a `since` of two hours would cost ten pages in
  a busy channel instead of one.
- **Permissions are read off the channel**, not `guild_permissions`, so a
  channel overwrite that grants or denies Manage Messages is honoured. The check
  is re-made for the bot too, before anything is previewed.
- Pinned messages are deleted like any other, but the confirmation says how many
  are in the set, so a pinned rules post can't go silently.
- `Message.delete()` takes no audit-log reason, so only the bulk path names the
  admin in the audit log. Every run is printed either way.

---

## Voice time (`cogs/voicelog.py`)

Records how long members spend in voice channels. **Off per guild until an admin
switches it on** (`voice_settings.enabled`), and no privileged intent or extra
permission is involved — `Intents.default()` already carries voice states.

### A visit is not a session

A *visit* is one stay in one channel, held in memory. It is split into **counted
intervals**, and only intervals become rows:

- Counting **pauses** when the rules stop applying — the member is left alone
  (unless `count_solo`), the channel is the AFK channel (unless `count_afk`), or
  the channel is excluded.
- It **resumes** when they apply again, as a new row.

So `SUM(seconds)` is time that actually counted, not time connected. `_sync_channel()`
is what enforces this: after every voice event it re-evaluates *everyone* in the
affected channels, because one person arriving or leaving changes whether everybody
else is "alone".

**Mute, deafen and stream toggles fire `on_voice_state_update` with the channel
unchanged** and are ignored — following them would shred every visit into fragments.

### Restarts and crashes

Two mechanisms, and both matter:

- `ORBATBot.close()` calls `flush_open_sessions()` **before** the Discord
  connection goes down, so a redeploy — the common case — records everything.
- Open rows carry `heartbeat_at`, refreshed every `HEARTBEAT_SECONDS`. On the next
  start, `close_dangling_voice_sessions()` closes anything still open **at its last
  heartbeat**, so a hard crash costs at most one heartbeat. An interval with no
  heartbeat counts zero — the time is never rounded up.

`on_ready` re-scans every voice channel and picks up whoever is already in one. It
runs on every reconnect, so it is written to be idempotent.

### Notes for future changes

- **The settings are cached for `SETTINGS_TTL` seconds**, because they are read on
  every voice event. `forget_settings()` drops that, and the web route calls it
  after a save so a change applies immediately.
- **`_lock` serialises the whole event path.** Two people leaving a channel at once
  would otherwise both compute "is anyone still here" from the same stale view.
- The leaderboard query counts open intervals too, via
  `COALESCE(heartbeat_at, started_at)`, so somebody currently in voice appears
  within one heartbeat rather than only after they leave.
- **`build_leaderboard_embed()` and the `PERIODS` helpers live in the cog**, not in
  `web/`: the daily board needs them too, and `web/` may depend on `cogs/` but not
  the other way round. `web/voice.py` re-exports them, which is what its `__all__`
  is for.
- **The daily board is driven by "has today's refresh happened", not by firing at
  a minute.** `board_updated_on` holds the local day it last ran, so a restart or
  an outage across the chosen hour still produces one refresh — late, but never
  skipped and never doubled. `daily_board` therefore only has to run often enough
  to bound the lateness (`BOARD_CHECK_MINUTES`).
- **The board message is edited, not reposted**, so it keeps its place and can be
  pinned. A `NotFound` clears `board_message_id` and the next refresh posts a
  fresh one.
  It names members instead of mentioning them — a leaderboard that pings ten
  people every time it is posted would be worse than useless.
- `member_name` and `channel_name` are stored per row on purpose: a leaderboard
  should not need a REST call per row, and a deleted channel should still show up
  in the history under its old name.
