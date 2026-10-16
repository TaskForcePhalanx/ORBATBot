<!-- Part of ORBATBot's documentation. The rules every session needs
     are in ../CLAUDE.md, which says when to read this file. -->

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
