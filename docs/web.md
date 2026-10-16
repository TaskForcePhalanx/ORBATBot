<!-- Part of ORBATBot's documentation. The rules every session needs
     are in ../CLAUDE.md, which says when to read this file. -->

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
functions — see [Approval & Denial Flow](slots.md#approval--denial-flow).

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
[Running an operation](slots.md#running-an-operation-cogsadminpy)) and turns an
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
