<!-- Part of ORBATBot's documentation. The rules every session needs
     are in ../CLAUDE.md, which says when to read this file. -->

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
