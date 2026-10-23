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
[the roster provider](docs/slots.md#the-roster-provider-utilsrosterpy).

**Current state:** Fully operational bot deployed on Railway, plus an optional
web UI (`web/`) with Discord OAuth2 login for events, game roles, embeds, the
member log, voice time, the ORBAT editor, the slot-approval queue and the
[tactical map](docs/tacmap.md#tactical-maps-utilstacmap--webtacmappy).
**The website is for admins and organisers, and that is a decision rather than
a stage.** Requesting a slot stays in Discord, where the members already are;
the site exists so the people *running* an operation have somewhere better than
a slash command. Deciding a request is on the web as well because an approval
queue is organiser work — but **a member never needs to open the site at all**,
and nothing here is aiming to change that. What that rules out, and what is
actually left, is under
[Slots on the web](docs/web.md#slots-on-the-web--what-is-here-and-what-is-not).

---

## Where the rest of this is

This file holds what every session needs whatever it is working on: what the
project is, the layout, the testing rule, the variables, the channels, who may
do what, and how it deploys. **Everything else is one file per feature under
`docs/`, and the table says when to read which.** It was all one 4,100-line
file until it became the thing every branch conflicted on and every session
paid for in full before reading a line of code.

**Read the file for a feature before changing that feature's code.** Each one
holds the rules that are not visible in the source — why a decision went the
way it did, what was tried and broke, and the invariants nothing enforces. A
change made without reading it is how a rule that cost a week to learn gets
undone in a minute.

| Changing… | Read |
|---|---|
| `utils/database/`, `utils/schema.py`, or anything about a table or column | [docs/schema.md](docs/schema.md) |
| `cogs/slots.py`, `cogs/admin.py`, `utils/roster.py`, `utils/sheets.py` — requesting, approving, assigning, the board | [docs/slots.md](docs/slots.md) |
| `utils/orbat.py`, `web/orbat.py` — the roster editor and its diff | [docs/orbats.md](docs/orbats.md) |
| `utils/tacmap/`, `web/tacmap.py`, `web/static/tacmap.js`, `utils/tiles.py`, `utils/arma_icons.py`, `web/terrain.py` | [docs/tacmap.md](docs/tacmap.md) |
| `utils/campaign.py`, `web/campaigns.py`, `cogs/campaigns.py`, `web/static/campaign.js`, `web/exports.py`, `web/maptrace.py` | [docs/campaigns.md](docs/campaigns.md) |
| anything under `web/` — the app, the routes, auth, the nav, the front end | [docs/web.md](docs/web.md) |
| `cogs/events.py`, `utils/eventtime.py`, `utils/eventopts.py`, `web/service.py` | [docs/events.md](docs/events.md) |
| `utils/killtracker.py`, `utils/ocap.py`, `cogs/killtracker.py`, `web/killtracker.py`, `dashboard/` | [docs/killtracker.md](docs/killtracker.md) |
| `utils/reddit.py`, `cogs/redditfeed.py`, `web/reddit.py` | [docs/reddit.md](docs/reddit.md) |
| `cogs/memberlog.py` — joins, leaves, invites, welcome messages | [docs/memberlog.md](docs/memberlog.md) |
| `cogs/voicelog.py`, `web/voice.py` | [docs/voice.md](docs/voice.md) |
| `cogs/gameroles.py`, `web/roles.py` | [docs/game-roles.md](docs/game-roles.md) |
| `utils/embeds.py`, `web/embeds.py` | [docs/embeds.md](docs/embeds.md) |
| `cogs/purge.py` | [docs/purge.md](docs/purge.md) |
| `utils/modlist.py`, `cogs/modlists.py`, `web/modlists.py` — Launcher presets, the selector, an event's modlist | [docs/modlists.md](docs/modlists.md) |
| `bot.py` | its startup and its one-process assumptions are below; the operation reminder loop it runs is [docs/slots.md](docs/slots.md#event-reminders) |

Three rules govern every file here, and each is argued where it actually
bites rather than restated as policy:

- **A module that decides anything imports nothing but the standard library**,
  so the tests can reach it — the testing table below, and
  [docs/orbats.md](docs/orbats.md#notes-for-future-changes) for the case that
  set the precedent.
- **One function per decision, called by every surface** — the Discord button,
  the slash command and the web form all call the same one, which is what
  keeps them from drifting:
  [docs/slots.md](docs/slots.md#approval--denial-flow).
- **No JavaScript on the website**, with exactly two deliberate exceptions
  (the map editor and the mission wizard), each argued where it is made:
  [docs/tacmap.md](docs/tacmap.md#there-is-javascript-here-and-only-here),
  [docs/campaigns.md](docs/campaigns.md#the-wizard).

`docs/` is for people and for sessions, not for the website — it is not served
anywhere and `utils/help.py` is still the only thing a member reads
([docs/web.md](docs/web.md#the-help-pages-utilshelppy)).

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
  modlists.py           # Reads Arma Launcher presets posted to a channel into versioned modlists — no commands
  killtracker.py        # Polls the OCAP server and counts new recordings — no commands
  campaigns.py          # Posts the planner's approved drops, pings holds, starts sign-ups — no commands
utils/
  database/             # All PostgreSQL queries (asyncpg), one module per feature
    core.py             #   the pool, the schema runner, and what every query uses
    slots.py            #   an operation and the requests against it
    orbats.py           #   the rosters, their squads, slots and nets
    events.py           #   events, sign-ups, custom responses
    maps.py             #   tactical maps, terrains, tiles, place names
    campaigns.py        #   the campaign planner's tables
    killtracker.py      #   a recording per row, its campaigns, aliases, units
    reddit.py  voice.py  roles.py  embeds.py  memberlog.py  guilds.py
  schema.py             # The schema those queries run against, as numbered migrations
  sheets.py             # Google Sheets read/write (gspread)
  roster.py             # One normalised slot, from a sheet or from an ORBAT
  orbat.py              # DB-held ORBATs: the text format, the safe edit, the board
  embeds.py             # Builder-made rich messages → discord.Embed, post and edit
  reddit.py             # One Reddit feed, read and rendered — no Discord, no database
  killtracker.py        # One OCAP recording → the Kill Tracker's per-player rows. Stdlib only
  promotion.py          # Who is due a Cadet → Member promotion, from mission counts and the Discord mapping. Stdlib only
  campaign.py           # The campaign planner's rules: schedule, drop text, splitting, checks, cleaning. Stdlib only
  sitenav.py            # The unit's website menu, read off its homepage for the stats page. Stdlib only
  discordmd.py          # Discord message markdown → HTML, for previewing a drop as Discord shows it. Stdlib only
  eventtime.py          # Reading a typed time, and walking a repeat: the event date rules. Stdlib only
  eventopts.py          # What an event's responses, pings and two ranges mean: the option rules. Stdlib only
  approvals.py          # Who may decide a slot request, and which are safe to decide in bulk. Stdlib only
  objectives_seed.py    # The Objectives Library as first written — what a guild's library is seeded from
  ocap.py               # The OCAP server's list and downloads, and which recordings are due
  tacmap/               # One tactical map: the symbols, the document, the SVG
    base.py             #   the vocabulary: Arma's tables, the limits, the per-item predicates
    doc.py              #   what an item is, and what a saved map may contain
    places.py           #   the terrain's own town names
    drawing.py          #   the map as an SVG
    arma.py             #   the plan into a running mission, and back out of one
    icons.py            #   the game's own marker pictures, as a guild uploads them
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
  promotion.py          # Cadet → Member after enough missions: the settings form and the due list, on top of cogs/killtracker.py
  tacmap.py             # Tactical maps, share links and posting, on top of utils/tacmap/
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
dashboard/              # The Kill Tracker's public page — static, hosted on GitHub Pages, not by the bot
  index.html tracker.js # Reads everything from the bot's /kt/api/* — no Google Sheet
  *.json                # campaigns / player_aliases / unit_overrides — the fallback the bot's API replaces
scripts/
  gen_help.py           # utils/help.py → the README's help:start/help:end section
  gen_exports.py        # the two packages' __init__ re-export lists, from their modules
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

There is no linter config. `.github/workflows/deploy.yml` runs the tests on
every push and pull request, and the deploy step after them is skipped unless
`RAILWAY_TOKEN` is set — so the suite is the gate whether or not that token
exists. The tests are `python -m pytest tests`,
and almost every one of them covers a module that imports nothing
beyond the standard library — which is the rule that decides what is testable
here at all. The two exceptions read source rather than call it:
`tests/test_logging.py` parses the whole service with `ast`, and
`tests/test_schema.py` reaches into `utils/schema.py`, which imports asyncpg
for one exception class:

| | what it pins |
|---|---|
| `tests/test_orbat_parser.py`, `tests/test_orbat_diff.py` | `utils/orbat.py`'s parser and diff — the two places where a bug silently deletes somebody's slot. They lived in `lab/tests` and reached the real module through re-export shims; the playground is gone and they import it directly |
| `tests/test_reddit.py` | feed parsing, templating, and how a refusal is told apart from a broken feed |
| `tests/test_redditfeed.py` | what `check_feed()` promises: a post is announced exactly once |
| `tests/test_tacmap.py` | the document rules, lines (weights, dashes cut into Arma markers, arrows, phase-line names), measuring and grid references, reading markers back from Arma and the export report, the SVG and SQF escaping, units and markers drawn from uploaded pictures, how an upload is matched and how old maps read, shapes (their parsing, hatching and Arma area markers), the grey mask and its AO holes, the tile layout, the body cap measured against the largest document the parser accepts, and the place-name parser and geometry (the renderer's crop and tile swapping are pinned in `test_campaign.py`, which is what uses them) |
| `tests/test_tiles.py` | what an uploaded archive keeps and what it drops |
| the scale-bar cases in `tests/test_tacmap.py` | that a bar is only ever a round number, that it is the largest one that **fits** its share rather than the nearest (an overrunning bar creeps across the plan), that it measures the **crop** and not the sheet, and that the chrome is off unless the picture is a fixed one — every page showing this SVG can be zoomed, and a bar sized for the first viewBox would then lie |
| `tests/test_webconfig.py` | which origin a link gets — the page's own host, or the canonical one |
| `tests/test_killtracker.py` | the counting rules (crew kills and distance, end-of-mission teamkills, disconnect "suicides", shots, hits, Zeus), JavaScript-style rounding, and that the streaming reader agrees with the in-memory path at every buffer size |
| `tests/test_ocap.py` | what the OCAP list entry means, and which recordings are due: new, counted under older rules, or failed and past their back-off |
| `tests/test_campaign.py` | the planner's rules: drop dates and time zones, what each drop says and where the ORBAT line falls, the 2,000-character split, the approval hash, the settings form, the consistency checks (phase objectives included), what a new mission carries over, what the wizard may store (`clean_fields`, `clean_phases`), image sniffing, the library seed and its pairings, what each briefing pack contains (`document_blocks`), and each drop's state (`drop_state`: approved for one version at one time, held and pinged once, edited after posting), where a test campaign's drops go, when an operation still counts as running, when a mission is over and a campaign may close itself, and the maps: one layer per AO on the campaign map and per phase on an AO map, what an AO drawn, relabelled or deleted on the campaign map does to the planner's AOs, what an AO map starts from (masked outside its AO), which layers an ops trace shows, where it crops and how deep its terrain goes, and after action: which recordings could be a mission, which one links itself, and the numbers and line it gives the AAR |
| `tests/test_sitenav.py` | reading the website's menu off a Google Sites page: one copy of it (the page holds it three times), the tree by `data-level`, Google's redirect wrapper taken off, non-http links dropped, no menu answering empty rather than guessing, and what is escaped when it is drawn |
| `tests/test_discordmd.py` | that nothing typed becomes HTML, links stay http(s), and headings, quotes, lists, emphasis, code, timestamps and mentions render the way Discord shows them |
| `tests/test_logging.py` | the logging rules: no `print()` left in the service, no `log.exception()` outside an `except`, every logging module has its own logger, and every package that logs is one `bot.py` raises above WARNING |
| `tests/test_schema.py` | the migration list's numbering — versions unique and contiguous from 1, listed in the order they run — that migration 1 is unedited (its source is hashed), and that `init_db()` creates nothing outside the list |
| `tests/test_nav.py` | the tab bar: who sees which groups, that a group links to its first reachable page and that every page knows its group, that the top row stays within the width it was measured to fit — and that no page heads itself with the guild's name or says who is signed in |
| `tests/test_wizard_overview.py` | that the wizard's Overview and the schedule page are **one template** — neither has grown a copy of the schedule, both hand it what it asks for, every form in it comes back to the page it was pressed on, and nothing in it is redrawn in the browser |
| `tests/test_static_ids.py` | that every element id the site's two scripts reach for is one a template actually renders. Neither script is reachable from pytest — one DOM-bound IIFE apiece — and that is the one failure mode it leaves completely uncovered: a handler wired to a button nobody added throws on load and, because it is a single IIFE, takes the **whole editor** down with it. An id built by a Jinja expression is matched as a pattern; one that is nothing but an expression vouches for nothing and is dropped, or it would make the check pass for any id at all. It also pins that **every control a viewer can see is wired above the read-only gate**: the zoom buttons, the pinch and the drag-to-pan were all below it while the buttons render for everyone, so a shared map on a phone had no way to zoom or pan at all — nothing failed, the controls were just inert. The rule reads the id's first mention **anywhere** in the script rather than its `getElementById`, because the zoom buttons are wired through an array literal and the first version of the check never examined the two controls that started it. Which ids a viewer sees is read out of the template's `{% if editable %}` blocks, and a companion test proves that split is read correctly — mis-read it and everything looks editor-only, so the real check passes having examined nothing |
| `tests/test_approvals.py` | who may decide a request and what is safe to decide in bulk — the two rules `/my-approvals` made load-bearing. **Contention is counted over the whole queue, not over the rows the approver may action**: count after filtering and a slot two units both want looks uncontested, so one press would approve it and auto-deny the other unit's request. A request with no roster key counts as contested, because bulk approval is the one caller acting with nobody reading the row. And `still_approvable()` for the press itself: the fresh answer intersected with what was actually shown, so a slot that became contested in between is left alone **and** a request that arrived after the list is not approved either. Every rule here was mutation-tested |
| `tests/test_eventtime.py` | the event date rules: a month shift that clamps rather than overflows, which weekday of the month a date is, a month with no fifth Saturday answering *none of this one* rather than *the series is over*, a monthly series measured from its anchor so it keeps the 31st, a bot back after ten weeks catching up in one step, and a typed time read day-first in the guild's zone and stored naive. **Every expected date in it was worked out from a calendar rather than from what the code returns**, and the calendar it assumes is checked against the standard library in its first test |
| `tests/test_eventopts.py` | what an event's options mean: a response set's labels, emoji, decline flags and distinct keys and the six ways it is refused, the ids and names a mention argument carries — **including that the digits inside a user mention are not a role** — and the two ranges both commands apply. One test holds `is_emoji()` against `discord.PartialEmoji` over 2,000 fuzzed tokens and **skips when discord.py is absent**, so the rest of the file keeps its stdlib-only promise |
| `tests/test_database_package.py` | that the thirteen modules of `utils/database/` stay one namespace: the `__init__` re-export list is generated and current (`scripts/gen_exports.py --check`) and every name a module defines is on `database`, nothing imports sideways between feature modules bar the one named edge, and **every global a query references resolves in its own module** — a missing `import json` in one of them would otherwise be a `NameError` the first time that query ran in production, which no test here calls |
| `tests/test_promotion.py` | who is due a Cadet → Member promotion: mapped, holds the first role, lacks the second, enough missions, not promoted or passed over before; and what the settings form refuses |
| `tests/test_tacmap_package.py` | that the six files of `utils/tacmap/` stay a layer cake: the `__init__` re-export list is generated and current and every name a submodule defines is on `tacmap` (a forgotten one is an AttributeError in whichever caller wanted it), the layers only import downwards, and the package still needs nothing but the standard library |
| `tests/test_help.py` | that every slash command has a how-to — it reads the names out of `cogs/`, so forgetting to document a new command is a red test |

**There is deliberately no case count written down here.** There was one, and
it was the single line every branch touched — so two PRs open at once conflicted
on it every time, in a file where a conflict is the most tedious kind to resolve.
The number is whatever `pytest` prints, it tells nobody anything the command
does not, and a figure that must be edited by every change is a figure that is
wrong between changes.

**Every decision-making function in this project is now covered.** The last two
were `_parse_responses()` and `_parse_mention_roles()` in `cogs/events.py`, and
the reason they held out for so long was real: one asked
`discord.PartialEmoji` whether a token was an emoji, the other took a
`discord.Guild`. Covering them meant either importing discord.py into the tests
or splitting the emoji check out — and the second is what was done, see
[event options](docs/events.md#event-options-utilseventoptspy).

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
| `WEB_BASE_URL` | **Optional.** Public origin, no trailing slash — or **several**, separated by spaces or commas, when the site answers on more than one name. Each one needs its own redirect URI registered in the Developer Portal. The first is canonical: it is what a link built with no request in hand uses. Empty → derived from the request, which is only meant for local runs. See [answering on more than one name](docs/web.md#answering-on-more-than-one-name) |
| `WEB_HOST` / `WEB_PORT` | **Optional.** Listen address. `PORT` (injected by Railway) wins over `WEB_PORT` |
| `WEB_ENABLED` | **Optional.** `0` keeps the site off even when everything else is set |
| `WEB_BRAND` | **Optional.** Site name in the header, tab title and footer. Defaults to `TFP BOT` |
| `OCAP_BASE_URL` | **Optional.** The unit's OCAP server, no trailing slash. Defaults to `https://ocap.taskforcephalanx.com`. The Kill Tracker reads `/api/v1/operations` and `/data/<file>.json.gz` from it — both public — see [the Kill Tracker](docs/killtracker.md#the-kill-tracker-utilskilltrackerpy-utilsocappy-cogskilltrackerpy-webkilltrackerpy) |
| `KT_SYNC` | **Optional.** `0` stops the Kill Tracker polling the OCAP server. Everything already counted keeps being served |
| `REDDIT_USER_AGENT` | **Optional.** How the bot identifies itself to Reddit when reading a watched feed. Reddit answers 429 to a client that doesn't say who it is, so a descriptive value naming your own Reddit account is best; empty falls back to `utils/reddit.DEFAULT_USER_AGENT`. There is nothing else to configure — the feeds are public RSS, so no API registration, OAuth or client secret is involved |
| `MEMBER_EVENTS` | **Optional.** `1` requests the privileged members intent, which `cogs/memberlog.py` needs for joins and leaves. **Only set it once "Server Members Intent" is ticked in the Developer Portal** — requesting an ungranted privileged intent makes login fail, taking the whole bot down. Bans and unbans need no intent |
| `MESSAGE_CONTENT` | **Optional.** `1` requests the privileged message content intent, which `cogs/modlists.py` needs to read a posted preset's text and attachment. **Only set it once "Message Content Intent" is ticked in the Developer Portal**, for the same reason as `MEMBER_EVENTS`. Without it the cog loads and does nothing, and says so in the log |
| `SITE_NAV_URL` | **Optional.** The unit's website (Google Sites), whose menu the bot's `/stats` page shows at the top. `utils/sitenav.py` reads it off the homepage at most hourly and keeps the last menu that parsed. Defaults to `https://www.taskforcephalanx.com/`; set it **empty** to show no menu. See [the stats page](docs/killtracker.md#the-dashboard-reads-only-the-bot) |
| `LOG_LEVEL` | **Optional.** How much this project's own code logs — `DEBUG`, `INFO` (the default), `WARNING`, `ERROR`. It does **not** reach discord.py, which `setup_logging()` pins to WARNING: the bot starts with `bot.start()` rather than `bot.run()`, so the library has never configured logging here, and turning the root up writes a line per gateway heartbeat and buries everything else |

Railway injects these at runtime; nothing sets them manually. `_railway_restart()` reads them to find the deployment to restart:

| Variable | Used for |
|---|---|
| `RAILWAY_DEPLOYMENT_ID` | The running container's own deployment — preferred, because it guarantees the bot restarts *itself* |
| `RAILWAY_SERVICE_ID` / `RAILWAY_ENVIRONMENT_ID` | Fallback when the deployment id is absent: query the service's latest `SUCCESS` deployment |
| `RAILWAY_PROJECT_ID` | Narrows that fallback query when present |

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

**Unit Leader role name:** `Unit Leader` — `UNIT_LEADER_ROLE` in `utils/approvals.py`,
re-exported by `cogs/slots.py` (which is where it used to live) and from there by
`cogs/admin.py`, so the existing imports all still resolve. It sits in a module
that imports nothing because the cogs, the web and the tests all need it and
none of them should have to reach sideways for it.

### Access matrix

| Action | Member | Unit Leader | Admin |
|---|---|---|---|
| `/request-slot`, `/cancel-request`, `/change-slot`, `/leave-operation` | ✅ | ✅ | ✅ |
| `/clear-slot` — or **Release** / **Withdraw** on the web | ❌ | ✅ own unit | ✅ |
| `/assign-slot` — or **Assign** on the web | ❌ | ✅ own unit | ✅ |
| Approve / Deny — in `#slot-approvals` or on the web | ❌ | ✅ own unit | ✅ |
| `/my-approvals` — the open requests you may decide, and approve-all for the uncontested ones | ❌ | ✅ own unit | ✅ all units |
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
**Unit gating:** the rule is `approvals.may_action()` — admins bypass all unit checks; anyone else needs the `Unit Leader` role **and** the requester's unit role; a request with no unit role can be actioned by any Unit Leader. The Unit Leader half is not optional: the buttons in `#slot-approvals` are visible to everyone who can read the channel, so without it any member of a unit could approve their own request by pressing the button on it. `_can_action_request()` in `slots.py` is the adapter that reads the permissions and role names off a `discord.Member` and asks — **three surfaces share it** (the buttons, the web queue, `/my-approvals`), and a second copy of the rule in any of them is how that last sentence stops being true. `/my-approvals` shows a narrower list and grants nothing: see [per-unit approvals](docs/slots.md#my-approvals--the-queue-narrowed-to-one-unit).

**Unit roles and `Unit Leader` can never become game roles** — `PROTECTED_ROLE_NAMES` in `cogs/gameroles.py` blocks it, so members can't self-assign their way into approval rights.

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

### Portainer (alongside Railway during the move)
- `deploy.yml`'s `portainer` job builds the image, pushes it to
  `ghcr.io/<owner>/orbatbot` and POSTs the stack's webhook. It does nothing until
  the secret `PORTAINER_WEBHOOK` exists, so merging it changes nothing on Railway.
- In Portainer set `BOT_IMAGE=ghcr.io/<owner>/orbatbot:latest` on the stack and
  enable image re-pull on the webhook. Never run both hosts with the same
  `DISCORD_TOKEN` — see [One process](#one-process-and-what-assumes-it).

### GitHub Pages (the Kill Tracker page)
- `dashboard/` is a static page; `.github/workflows/dashboard.yml` publishes it to the
  `gh-pages` branch (`main` → root, `claude/**` → `dev/`). Pages must be switched on once:
  Settings → Pages → Deploy from a branch → `gh-pages` / root. URL: `https://armatfp.github.io/ORBATBot/`.
- `deploy.yml` has **no** `paths-ignore` for `dashboard/**`: the bot serves `/stats` from
  its own copy of `dashboard/` (`web/killtracker.py`), so a page-only change must redeploy
  (and restart) the bot too. It once ignored them, and `/stats` stayed stale after every
  dashboard change while only the GitHub Pages copy updated.
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

### One process, and what assumes it

**The bot runs as a single instance, and at least seven things depend on
that.** Nothing enforces it, nothing warns about it, and the way it surfaces
is a campaign drop posted twice to a public channel on the night somebody
scales the Railway service to two replicas. Written down here because it is
invisible in every file that relies on it:

| | What a second process would do |
|---|---|
| `cogs/campaigns.py` — the per-mission `_lock` | Two processes hold two locks, so the same drop posts twice. The approval hash does not save you: both see the same valid approval |
| `cogs/campaigns.py` — `_failed` / `FAILURE_BACKOFF` | A failing drop is stood down in memory, so the other process retries it every minute anyway |
| `cogs/killtracker.py` — the poll loop | Both poll, both download the same recordings and both write them |
| `bot.py reminder_task`, `EventsCog.event_task` | Every reminder DM and every channel ping goes out twice; `reminder_fired` narrows the window but does not close it |
| `cogs/voicelog.py` — `_lock` | Both recompute "is anybody still in this channel" from the same stale view, so intervals are double-counted |
| `web/guilds.resolve_member()` — the 60 s cache | Doubles the REST calls, and `forget_member()` after a role change only clears the replica that served the form |
| `web/reddit.py remember_posts()` — `RECENT_TTL` | **Announce** on the catch-up page reads one process's copy of the feed while the other's is stale |

Underneath all of it: two processes signed in as the same bot both receive
every gateway event and both act on it. Making the service multi-instance is
therefore not a deployment setting but a piece of work — advisory locks around
the loops, the caches moved to the database or dropped — and until it is done,
**keep Railway at one replica**.

### `/healthz` reports, it does not judge

It answers `bot ok · db ok`, with `starting` before the bot is ready and
`unreachable` when `database.ping()`'s `SELECT 1` does not come back — and
**the status code stays 200 while the process is alive**. Nothing consumes it
today: `railway.json` sets no `healthcheckPath` and compose's only healthcheck
is on postgres, so a non-200 would restart nothing. The moment one is wired
up, a failing code would restart the container on a momentary database blip,
possibly mid-operation — which is worse than the thing it would be reporting.
A monitor that wants to alert reads the body.

### Startup sequence
1. `init_db()` — applies any pending migration (`utils/schema.py`) — then
   `close_dangling_voice_sessions()`: schema first, then voice
   intervals left open by a crash are closed at their last heartbeat before
   anything new is recorded
2. Load `cogs.slots`, `cogs.admin`, `cogs.gameroles`, `cogs.events`, `cogs.voicelog`,
   `cogs.memberlog`, `cogs.purge`, `cogs.redditfeed`, `cogs.campaigns`, `cogs.modlists` and `cogs.killtracker`. Each is wrapped in its own
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
