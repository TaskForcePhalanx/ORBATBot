<!-- Part of ORBATBot's documentation. The rules every session needs
     are in ../CLAUDE.md, which says when to read this file. -->

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
