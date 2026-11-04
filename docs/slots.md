<!-- Part of ORBATBot's documentation. The rules every session needs
     are in ../CLAUDE.md, which says when to read this file. -->

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
2. **Process exit** — the fallback, also used when the API call raises. `os._exit(1)` after a 1 s pause (so Discord can deliver the ephemeral reply) trips Railway's `ON_FAILURE` restart policy. **This is the path production uses** since the move to Portainer (no `RAILWAY_API_TOKEN` there): `restart: unless-stopped` relaunches the container on any exit.

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

## `/my-approvals` — the queue narrowed to one unit

An ephemeral list of the open requests the caller may actually decide, with one
button: **Approve all uncontested**. It exists because `#slot-approvals` is one
channel for five units, and at five to eight a Unit Leader is reading past four
other units' embeds to find their own.

**It shows less and grants nothing.** Every decision still goes through
`approve_slot_request()` and therefore `_can_action_request()`, exactly as the
button does. That is worth stating plainly because the view *feels* private
while the shared channel is still there, and the next person to read this will
be tempted to treat the narrower list as the access control. It is not: the
rule is `approvals.may_action()` and it runs per request, so the command would
be safe even if the filter that built the list were wrong.

### What came out of two surfaces to make it possible

Two decisions were previously unreachable from the tests, and the bulk case is
what made that intolerable — a warning label a human reads past is one thing, a
button that acts on several requests at once is another.

- *May this person decide this request?* lived inside `_can_action_request()`,
  which takes a `discord.Member`. The rule is now `approvals.may_action()`,
  taking role names and an admin flag; `_can_action_request()` is the adapter.
- *Is this slot contested?* was computed inline in `web/slots.queue()` and used
  only to put a badge on a row. It is now `approvals.contested_keys()`.

`utils/approvals.py` imports nothing, which is the point —
`tests/test_approvals.py` holds both rules without a database or a gateway, the
same split that put `is_emoji()` in `utils/eventopts.py`.

### The two things that are easy to get wrong

**Contention is counted over the whole queue, not over the rows this approver
may action.** Filter first and a slot that TFP and CNTO both want looks
uncontested to TFP's leader — so one press would approve it and auto-deny
CNTO's request as a side effect, which is a decision about another unit made by
a button labelled *uncontested*. `bulk_approvable()` counts first and filters
second for exactly that case, and `test_bulk_leaves_a_slot_another_unit_also_wants`
is what stops it being quietly reversed.

**The list is a snapshot, so the set is worked out again on the press.** A
second request for one of those slots can arrive between the list rendering and
the button being pressed, which turns an uncontested slot into a choice. On
confirm the queue is re-read and `approvals.still_approvable()` intersects the
rule's fresh answer with the ids that were actually shown, in the order they
were shown. Both halves of that are load-bearing: nothing that became contested
is approved, because arriving first is not how that gets decided, and nothing
the member never saw is approved either, even if it would now qualify. The
reply says how many were left alone and why.

### What it deliberately does not do

- **No per-request Approve or Deny.** Those already exist on the embeds, a
  denial needs its reason modal, and a contested slot is a choice between
  members that belongs where both requests are visible. This view does the
  boring half.
- **It is not all-or-nothing.** `approve_slot_request()` raises per request and
  rolls back its own failed sheet write, so the loop catches each one and
  carries on. The reply is a *report* — "6 approved, 2 failed" — rather than a
  confirmation, because the loop cannot be atomic and saying otherwise would be
  worse than admitting it.
- **Nothing is re-registered after a restart.** The view is ephemeral with a
  two-minute timeout; unlike `ApprovalView` there is nothing for
  `setup_hook()` to restore, and nothing that needs restoring — the member runs
  the command again.

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
