"""What an event's options mean: responses, ping tokens and the two ranges.

Pulled out of `cogs/events.py` for the rule that decides what is testable
here at all — a module importing nothing but the standard library can be
covered, and the cog pulls discord.py and asyncpg in behind it. These are the
rules `/event-create` and `/event-edit` both apply to the same arguments, so
they were also the last place where the two commands duplicated each other.

**The Discord-shaped half stays in the cog.** `mention_tokens()` says which
ids and names a string carries; turning those into roles needs a `Guild`, so
the cog does that. Nothing here touches discord.py, and nothing here knows an
interaction exists.

Every function raises `ValueError` with the message a person should read, the
same convention the rest of this project uses for "this is for the user".
"""
import re

# Two button rows, and a label that fits on a button.
MAX_RESPONSES = 10
MIN_RESPONSES = 2
MAX_RESPONSE_LABEL = 40

# events.mention_role_id holds a comma-separated list of role ids.
MAX_MENTION_ROLES = 10

# How long a series may wait after an occurrence ends before the next one is
# posted — a month, generously.
MAX_RECURRENCE_DELAY_HOURS = 24 * 31

# `<@&123>` as the client produces it, a bare snowflake someone pasted, and
# every mention-ish token — the last so user and channel mentions can be
# stripped before bare ids are looked for.
_ROLE_MENTION_RE = re.compile(r'<@&(\d+)>')
_ANY_MENTION_RE = re.compile(r'<[^>]*>')
_BARE_ID_RE = re.compile(r'(?<!\d)(\d{15,25})(?!\d)')

# A custom emoji, in the shape discord.py's own PartialEmoji._CUSTOM_EMOJI_RE
# accepts: <:name:id> or the animated <a:name:id>, the brackets and the leading
# colon group both optional, and **the id 13 to 20 digits**. That length is the
# load-bearing part — `<:TFP_Logo:1234>` is not a custom emoji to Discord, so it
# falls through to the named-emoji path and is rejected for carrying letters.
# A copy rather than an import: this module stays stdlib-only, and
# tests/test_eventopts.py fuzzes the two against each other.
_CUSTOM_EMOJI_RE = re.compile(r'^<?(?:(?:a)?:)?(?:[A-Za-z0-9_]+):(?:[0-9]{13,20})>?$')


def is_emoji(token: str) -> bool:
    """True if *token* is something Discord accepts as a button emoji.

    This is `discord.PartialEmoji.from_str()`'s answer without importing it.
    That call turns a plain word into a *named* emoji which Discord then
    rejects — taking the whole component, and so the entire button row, with
    it — so anything carrying ASCII letters is treated as an ordinary label
    word instead. A custom emoji is the one shape that may contain letters,
    because it carries its id.

    `tests/test_eventopts.py` checks this against the real
    `PartialEmoji.from_str()` over a corpus, so the two cannot drift.
    """
    if not token:
        return False
    if _CUSTOM_EMOJI_RE.match(token):
        return True
    return not any(c.isascii() and c.isalpha() for c in token)


def response_key(label: str) -> str:
    """The key a label is stored and addressed by.

    It lands in the button's `custom_id` and in `event_signups.response`, so it
    has to be stable and unique within one event.
    """
    slug = re.sub(r'[^a-z0-9]+', '_', label.lower()).strip('_')
    return (slug or 'option')[:24]


def parse_responses(raw: str, emoji_check=is_emoji) -> list:
    """Parse `✅ Coming | ❓ Maybe | -❌ Can't make it` into response definitions.

    A leading `-` marks a response that means "not coming": those people are
    left out of reminders, the way Declined always has been.

    `emoji_check` is injected so the cog can hand in discord.py's own answer if
    it ever needs to; the default is the stdlib one above.
    """
    entries = [part.strip() for part in raw.split('|')]
    entries = [part for part in entries if part]

    if len(entries) < MIN_RESPONSES:
        raise ValueError(
            f"Give at least {MIN_RESPONSES} responses, separated by `|` — "
            "for example `✅ Coming | ❓ Maybe | -❌ Can't make it`."
        )
    if len(entries) > MAX_RESPONSES:
        raise ValueError(
            f"That's {len(entries)} responses; {MAX_RESPONSES} is the most that fits on a message."
        )

    parsed, used = [], set()
    for order, entry in enumerate(entries):
        is_decline = 0
        if entry.startswith('-'):
            is_decline, entry = 1, entry[1:].strip()

        emoji = None
        parts = entry.split(None, 1)
        if parts and emoji_check(parts[0]):
            if len(parts) == 1:
                raise ValueError(f"`{parts[0]}` has no wording after it — every response needs a label.")
            emoji, entry = parts[0], parts[1].strip()

        if not entry:
            raise ValueError("Every response needs a label, e.g. `✅ Coming`.")
        if len(entry) > MAX_RESPONSE_LABEL:
            raise ValueError(
                f"`{entry[:20]}…` is too long — keep response labels under "
                f"{MAX_RESPONSE_LABEL} characters so they fit on a button."
            )

        key = base = response_key(entry)
        suffix = 2
        while key in used:
            key = f"{base[:22]}_{suffix}"
            suffix += 1
        used.add(key)
        parsed.append({'key': key, 'label': entry, 'emoji': emoji,
                       'is_decline': is_decline, 'sort_order': order})

    if all(item['is_decline'] for item in parsed):
        raise ValueError(
            "At least one response has to mean *coming* — right now every one is "
            "marked with `-`, so nobody could ever sign up."
        )
    return parsed


def mention_tokens(raw: str):
    """What a `mention` argument carries: `(ids, names)`.

    Role ids come from `<@&123>` tokens and from bare snowflakes — but only
    after **every** `<…>` token has been stripped out, or the digits inside a
    user mention `<@123>` would be taken for a role id, stored, and then
    rendered as `<@&123>`, which silently pings nobody.

    `names` is the fallback: when nothing in the string looked like a mention,
    the comma-separated words are role names somebody typed out. The two are
    never both filled, and resolving either needs a guild, which is the cog's
    half of this.
    """
    raw = (raw or '').strip()
    if not raw:
        return [], []

    ids = _ROLE_MENTION_RE.findall(raw)
    leftover = _ANY_MENTION_RE.sub(' ', raw)
    ids += _BARE_ID_RE.findall(leftover)

    seen, ordered = set(), []
    for role_id in ids:
        if role_id not in seen:
            seen.add(role_id)
            ordered.append(role_id)
    if ordered:
        return ordered, []

    names, lowered = [], set()
    for name in (part.strip() for part in leftover.split(',')):
        if name and name.lower() not in lowered:
            lowered.add(name.lower())
            names.append(name)
    return [], names


def check_resolved_mentions(roles: list) -> None:
    """What `mention_tokens()` cannot know: whether anything resolved, and how
    many. Raises the message the person should read."""
    if not roles:
        raise ValueError(
            "I couldn't find any of those roles. Type `@` and pick them from the "
            "list, or give their exact names separated by commas."
        )
    if len(roles) > MAX_MENTION_ROLES:
        raise ValueError(
            f"That's {len(roles)} roles; {MAX_MENTION_ROLES} is the most I'll ping at once."
        )


def check_duration(minutes) -> None:
    """A duration is a positive number of minutes, or not given at all."""
    if minutes is not None and minutes <= 0:
        raise ValueError('Duration has to be a positive number of minutes.')


def check_repeat_delay(hours) -> None:
    """How long after an occurrence ends its successor goes up."""
    if hours is None:
        return
    if hours < 0 or hours > MAX_RECURRENCE_DELAY_HOURS:
        raise ValueError(
            f'`repeat_delay` has to be between 0 and {MAX_RECURRENCE_DELAY_HOURS} hours.'
        )
