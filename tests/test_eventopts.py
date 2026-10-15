"""What an event's options mean.

`_parse_responses` and `_parse_mention_roles` were the last rule-bearing code
in this project with no tests, and the reason was real: one asked
`discord.PartialEmoji` whether a token was an emoji, the other took a
`discord.Guild`. The review called that "a decision, not a chore" — either
import discord.py into the tests, or split the Discord-shaped half out.

**The split was the choice.** `utils/eventopts.py` holds the rules and imports
nothing but the standard library; resolving a role id against a guild stays in
the cog, which is the only part that genuinely needs Discord. So almost
everything here runs with no discord.py at all.

The one exception is deliberate and guarded: `is_emoji()` is a copy of what
`PartialEmoji.from_str()` answers, and a copy can drift when the library is
upgraded, so one test holds the two against each other and skips when
discord.py is not installed.
"""

import random
import string

import pytest

from utils import eventopts


# -- responses --------------------------------------------------------------

def test_a_response_set_is_split_on_pipes():
    got = eventopts.parse_responses("✅ Coming | ❓ Maybe | -❌ Can't make it")
    assert [r['label'] for r in got] == ['Coming', 'Maybe', "Can't make it"]
    assert [r['emoji'] for r in got] == ['✅', '❓', '❌']
    assert [r['is_decline'] for r in got] == [0, 0, 1]
    assert [r['sort_order'] for r in got] == [0, 1, 2]


def test_a_leading_dash_means_not_coming():
    """Those people are left out of reminders, the way Declined always was."""
    got = eventopts.parse_responses('Yes | -No')
    assert got[0]['is_decline'] == 0
    assert got[1]['is_decline'] == 1
    assert got[1]['label'] == 'No', 'the dash is not part of the label'


def test_a_response_without_an_emoji_keeps_its_whole_label():
    got = eventopts.parse_responses('Coming | Not coming')
    assert [r['label'] for r in got] == ['Coming', 'Not coming']
    assert [r['emoji'] for r in got] == [None, None]


def test_duplicate_labels_get_distinct_keys():
    """The key lands in the button's custom_id and in event_signups.response,
    so two of them in one event would make a button answer for the wrong row."""
    got = eventopts.parse_responses('Rifleman | Rifleman | Rifleman')
    keys = [r['key'] for r in got]
    assert keys == ['rifleman', 'rifleman_2', 'rifleman_3']
    assert len(set(keys)) == 3


def test_a_key_is_a_slug_of_the_label():
    assert eventopts.response_key("Can't make it") == 'can_t_make_it'
    assert eventopts.response_key('  Coming!  ') == 'coming'
    assert eventopts.response_key('✅✅✅') == 'option', 'never empty'
    assert len(eventopts.response_key('x' * 90)) <= 24


@pytest.mark.parametrize('raw, because', [
    ('Only one', 'fewer than MIN_RESPONSES'),
    (' | '.join('r%d' % n for n in range(11)), 'more than MAX_RESPONSES'),
    ('✅ | Maybe', 'an emoji with no wording after it'),
    ('-|-', 'no label at all'),
    ('Coming | ' + 'x' * 41, 'a label too long for a button'),
    ('-Nope | -Also nope', 'every option marked as a decline'),
])
def test_a_response_set_is_refused_when(raw, because):
    with pytest.raises(ValueError):
        eventopts.parse_responses(raw)


def test_the_all_declines_refusal_is_about_signing_up():
    """Not a cosmetic rule: with every option a decline nobody could ever
    answer yes, and the event would collect no attendees at all."""
    with pytest.raises(ValueError, match='nobody could ever sign up'):
        eventopts.parse_responses('-A | -B')


# -- what a mention argument carries ----------------------------------------

def test_role_mentions_are_read_as_ids():
    ids, names = eventopts.mention_tokens('<@&123456789012345678> <@&876543210987654321>')
    assert ids == ['123456789012345678', '876543210987654321']
    assert names == []


def test_a_user_mention_is_not_mistaken_for_a_role():
    """The load-bearing one. Without stripping every `<…>` token first, the
    digits inside `<@123…>` match the bare-id pattern, get stored as a role,
    and then render as `<@&123…>` — which silently pings nobody."""
    ids, names = eventopts.mention_tokens('<@123456789012345678>')
    assert ids == [], 'a user mention is not a role'
    assert names == []


def test_a_channel_mention_is_not_mistaken_for_a_role():
    ids, names = eventopts.mention_tokens('<#123456789012345678>')
    assert (ids, names) == ([], [])


def test_a_role_mention_beside_a_user_mention_keeps_only_the_role():
    ids, _ = eventopts.mention_tokens('<@&111111111111111111> ping <@222222222222222222>')
    assert ids == ['111111111111111111']


def test_a_bare_snowflake_counts():
    ids, names = eventopts.mention_tokens('123456789012345678')
    assert ids == ['123456789012345678']
    assert names == []


def test_ids_are_deduplicated_in_the_order_given():
    ids, _ = eventopts.mention_tokens(
        '<@&222222222222222222> <@&111111111111111111> <@&222222222222222222>')
    assert ids == ['222222222222222222', '111111111111111111']


def test_names_are_the_fallback_when_nothing_looks_like_a_mention():
    """Typing the names out still works."""
    ids, names = eventopts.mention_tokens('Unit Leader, TFP , CNTO')
    assert ids == []
    assert names == ['Unit Leader', 'TFP', 'CNTO']


def test_names_are_deduplicated_without_regard_to_case():
    _, names = eventopts.mention_tokens('TFP, tfp, Tfp')
    assert names == ['TFP']


def test_nothing_in_means_nothing_out():
    assert eventopts.mention_tokens('') == ([], [])
    assert eventopts.mention_tokens(None) == ([], [])
    assert eventopts.mention_tokens('   ') == ([], [])


def test_resolved_mentions_are_checked_for_emptiness_and_count():
    with pytest.raises(ValueError, match="couldn't find any of those roles"):
        eventopts.check_resolved_mentions([])
    eventopts.check_resolved_mentions(['r'] * eventopts.MAX_MENTION_ROLES)
    with pytest.raises(ValueError, match='the most'):
        eventopts.check_resolved_mentions(['r'] * (eventopts.MAX_MENTION_ROLES + 1))


# -- the two ranges both commands apply -------------------------------------

def test_a_duration_is_positive_minutes_or_absent():
    eventopts.check_duration(None)
    eventopts.check_duration(1)
    for bad in (0, -1, -90):
        with pytest.raises(ValueError, match='positive number of minutes'):
            eventopts.check_duration(bad)


def test_a_repeat_delay_is_bounded_at_a_month():
    eventopts.check_duration(None)
    eventopts.check_repeat_delay(None)
    eventopts.check_repeat_delay(0)
    eventopts.check_repeat_delay(eventopts.MAX_RECURRENCE_DELAY_HOURS)
    for bad in (-1, eventopts.MAX_RECURRENCE_DELAY_HOURS + 1):
        with pytest.raises(ValueError):
            eventopts.check_repeat_delay(bad)


# -- the one copy, held against its original --------------------------------

def test_the_emoji_check_still_agrees_with_discord_py():
    """`is_emoji()` is what `PartialEmoji.from_str()` answers, rewritten so this
    module needs no discord.py. A copy can drift when the library is upgraded,
    so this holds the two against each other — including the id length, which
    is the part that is easy to get wrong: `<:TFP_Logo:1234>` is *not* a custom
    emoji to Discord (the id is too short), so it falls through to the
    named-emoji path and is rejected for carrying letters.

    Skipped rather than failed where discord.py is absent, so the rest of this
    file keeps its stdlib-only promise.
    """
    discord = pytest.importorskip('discord')

    def original(token):
        try:
            parsed = discord.PartialEmoji.from_str(token)
        except Exception:
            return False
        if parsed.id is not None:
            return True
        return bool(token) and not any(c.isascii() and c.isalpha() for c in token)

    corpus = [
        '', ' ', '✅', '❓', '❌', '🎮', '🇬🇧', '👍🏽', '⚠️',
        'Coming', 'minecraft', 'OK', 'a', '1', '42', '-', '!', 'é', 'Ω', '中', '→', '€',
        '<:tfp:123456789012345678>', '<a:spin:987654321098765432>',
        '<:TFP_Logo:1234>', '<:x:1>', '<:x:1234567890123>',
        '<@123456789012345678>', '<@&123>', '<#456>', ':smile:', ':+1:',
        'tfp:123456789012345678', ':tfp:123456789012345678',
    ]
    alphabet = string.ascii_letters + string.digits + '<>:_-✅❓🎮 .,!'
    rnd = random.Random(20261015)
    corpus += [''.join(rnd.choice(alphabet) for _ in range(rnd.randint(1, 26)))
               for _ in range(2000)]

    disagreed = [t for t in corpus if original(t) != eventopts.is_emoji(t)]
    assert not disagreed, 'is_emoji has drifted from PartialEmoji: %r' % disagreed[:5]


def test_the_module_needs_nothing_but_the_standard_library():
    """The whole reason any of the above can be tested."""
    import subprocess
    import sys
    out = subprocess.run(
        [sys.executable, '-c',
         'import utils.eventopts, sys; print("discord" in sys.modules or "asyncpg" in sys.modules)'],
        capture_output=True, text=True, cwd=str(__import__('pathlib').Path(__file__).parent.parent))
    assert out.stdout.strip() == 'False', out.stderr
