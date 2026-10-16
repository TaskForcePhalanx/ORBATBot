<!-- Part of ORBATBot's documentation. The rules every session needs
     are in ../CLAUDE.md, which says when to read this file. -->

## ORBATs (`utils/orbat.py` + `web/orbat.py`)

The slot roster, built and maintained in the browser instead of in a Google
Sheet. Admin-only, one tab on the guild page.

**An operation can be run on one.** `/setup-slots orbat:<name>` starts an
operation against an ORBAT instead of a sheet, and from there the whole flow —
requesting, approving, the live board, `/assign-slot`, `/clear-slot` — works
against the database. See
[the roster provider](slots.md#the-roster-provider-utilsrosterpy) for how the two
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
