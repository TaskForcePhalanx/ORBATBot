<!-- Part of ORBATBot's documentation. The rules every session needs
     are in ../CLAUDE.md, which says when to read this file. -->

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

## Event options (`utils/eventopts.py`)

What an event's **responses**, **pings** and two numeric ranges mean. It came
out of `cogs/events.py` for the same reason `utils/eventtime.py` did — a module
importing nothing but the standard library can be covered, and the cog pulls
discord.py in behind it — and the cog imports every function straight back
under the private names it always had, so `web/service.py`'s
`from cogs.events import _parse_responses, …` and every call site are
unchanged.

**This is the split the tests table used to say was still to be decided.** The
two functions were uncovered because `_parse_responses()` asked
`discord.PartialEmoji` whether a token was an emoji and `_parse_mention_roles()`
took a `discord.Guild`. Both halves are now separate:

- **`is_emoji()` is a copy of what `PartialEmoji.from_str()` answers**, so this
  module needs no discord.py. The id length is the part that is easy to get
  wrong and was got wrong first: Discord's own pattern requires a **13–20 digit**
  id, so `<:TFP_Logo:1234>` is *not* a custom emoji to it — it falls through to
  the named-emoji path and is rejected for carrying ASCII letters. A copy can
  drift when the library is upgraded, so one test holds the two against each
  other over 2,000 fuzzed tokens (verified at **0 disagreements across 4,037**)
  and `importorskip`s discord.py, which keeps the rest of the file stdlib-only.
- **`mention_tokens()` returns ids and names; resolving them stays in the cog.**
  Turning an id into a role needs a guild, and that is the one part that
  genuinely needs Discord. `check_resolved_mentions()` is the count and
  emptiness check applied afterwards.

Two rules in it are load-bearing, and both are now pinned rather than hoped for:

- **Every `<…>` token is stripped before bare snowflakes are scanned for.**
  Without that, the digits inside a user mention `<@123…>` match the bare-id
  pattern, get stored as a role, and render as `<@&123…>` — which pings nobody,
  silently.
- **A response key must be unique within its event**, because it lands in the
  button's `custom_id` *and* in `event_signups.response`. Three `Rifleman`
  options become `rifleman`, `rifleman_2`, `rifleman_3`.

`check_duration()` and `check_repeat_delay()` are here because `event_create`
and `event_edit` applied the same two ranges in two places, which is how the
pair drifts.

---
