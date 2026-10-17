<!-- Part of ORBATBot's documentation. The rules every session needs
     are in ../CLAUDE.md, which says when to read this file. -->

## Modlists (`utils/modlist.py`, `cogs/modlists.py`, `web/modlists.py`)

Arma 3 Launcher presets (the `.html` the Launcher exports) are kept per guild as
four lists: **Weekly**, **Joint Op**, **Core**, **Optional**. It replaces the
`TFP-Mods` repository: its cron job that polled a channel, and its selector page.

### Versions

A version is never edited. `modlists` holds every version, one per list flagged
`is_current` (a partial unique index allows only one). Rolling back is flipping
that flag, so nothing is lost and a bad post can be undone. Only a version that
is not current can be deleted; an event that carried it loses the link
(`ON DELETE SET NULL`).

`cogs.modlists.store_preset()` is the **only** place a preset is kept. The
channel, the catch-up after a restart and the web upload all end there, so a
preset means the same whichever way it arrived. A preset whose mods are exactly
the current version's is not stored again.

### Which list a post is

`modlist.classify()` reads the message text, then the file name, then the
preset's own name, and **does not guess**: two matching lists, or none, gives
the bot's ❓ and buttons. The old script took the first rule that matched. The
words per list are the guild's setting; an empty box means `DEFAULT_KEYWORDS`
(`keywords_from_form`), never "match nothing".

### The web

| Page | Who | What |
|---|---|---|
| `/g/{id}/modlists` (**Server → Modlists**) | admins | channel, keywords, history with *Make current*, delete, upload |
| `/g/{id}/modlists/{id}/preset.html` | any member | one version as a Launcher preset |
| `/m/{guild}` | **anyone, no login** | the selector: an evening's list as it is, or Core/Optional picked mod by mod |
| `/mods` | anyone | redirects to `/m/{guild}` when the bot is in exactly one server |
| `/m/{guild}/preset.html?operation=&core=&optional=` | anyone | the selection; `build_selection()` merges, de-duplicates by workshop id and puts CBA_A3 first |
| `/m/{guild}/v/{id}/preset.html` | anyone | one stored version — what an event's message links |

The selector is a plain GET form: no JavaScript, and the download is a link that
can be pasted in Discord. Keys in a link that are no longer in the list are
ignored, not refused — the page may be older than the list.

### Events

`events.modlist_id` points at a **version**, so what an organiser picked is what
members get. The event form offers each list's current version; the embed shows
a **Mods** field linking the download, and the detail page lists it. A recurring
series follows its list: `_spawn_next_occurrence()` gives the next occurrence
the *current* version of the same list, and keeps the old one only if the list
is gone. Set on the web only; `/event-create` has no option for it.

### Notes for future changes

- Reading attachments needs the privileged Message Content intent
  (`MESSAGE_CONTENT=1`, ticked in the Developer Portal first — same rule as
  `MEMBER_EVENTS`). Without it the cog does nothing and says so once.
- `render_preset` escapes every value and keeps http(s) links only, because it
  writes back out what a stranger uploaded.
- The selector is public on purpose: the lists are the unit's join instructions.
