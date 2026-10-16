<!-- Part of ORBATBot's documentation. The rules every session needs
     are in ../CLAUDE.md, which says when to read this file. -->

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
