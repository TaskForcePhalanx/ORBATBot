"""What the wizard's Overview tab promises.

The overview decides nothing: every row on it is drawn from `mission_state()`,
the same answer the step tabs and the no-JavaScript schedule page are drawn
from. So there are no rules here to test — what there is instead is a contract
between three files that have no other way of noticing each other:

* `utils/campaign.STEPS` is the list of fields;
* `web/templates/cp_wizard.html` draws a row per field, twice over — once as
  the step's editor and once on the overview;
* `web/static/campaign.js` fills the second from the first's values.

A field added to `STEPS` lands on its step automatically, because that loop is
over `step.fields`. It lands on the overview automatically too — and these
tests are what keep both loops honest, because a field that quietly stopped
appearing on one of them would look like nothing at all.
"""

import pathlib
import re

from utils import campaign as rules

ROOT = pathlib.Path(__file__).resolve().parent.parent
WIZARD = (ROOT / 'web' / 'templates' / 'cp_wizard.html').read_text(encoding='utf-8')
SCRIPT = (ROOT / 'web' / 'static' / 'campaign.js').read_text(encoding='utf-8')
STYLE = (ROOT / 'web' / 'static' / 'style.css').read_text(encoding='utf-8')

PLAIN = ('text', 'date', 'time', 'textarea')
FIELDS = [f for step in rules.STEPS for f in step.fields]


# -- the tab itself ---------------------------------------------------------

def test_the_overview_is_the_first_tab():
    # It answers "where does this mission stand", which is the question
    # somebody has on opening one; the six step tabs each answer only for
    # themselves.
    strip = WIZARD[WIZARD.index('<nav class="cpsteps"'):]
    strip = strip[:strip.index('</nav>')]
    assert strip.index('data-step="overview"') < strip.index('{% for step in steps %}')


def test_opening_a_mission_lands_on_the_overview():
    assert "if (known.indexOf(first) === -1) first = 'overview';" in SCRIPT
    assert "['overview'].concat(" in SCRIPT


def test_a_link_to_a_step_still_opens_that_step():
    # The drops panel and the checks point at one, so #orders has to work.
    assert "var first = (location.hash || '').slice(1);" in SCRIPT


# -- every field is on it ---------------------------------------------------

# These read the template rather than a rendered page, the way
# `tests/test_logging.py` and `tests/test_help.py` read source: what is being
# pinned is that the overview walks the *same* list the step editors walk, so
# a field added to `STEPS` cannot land on one and not the other.

def section(name):
    """The markup between `<section class=... data-body=...>` and its end."""
    start = WIZARD.index(f'data-body="{name}"')
    depth, i = 0, WIZARD.index('<section', start - 200)
    for m in re.finditer(r'</?section\b', WIZARD[i:]):
        depth += 1 if m.group(0) == '<section' else -1
        if depth == 0:
            return WIZARD[i:i + m.end()]
    raise AssertionError(f'unclosed section for {name}')


def test_the_overview_walks_the_same_fields_as_the_steps():
    over = section('overview')
    assert '{% for step in steps %}' in over
    assert '{% for f in step.fields %}' in over
    # And the step editors walk it the same way, one loop each.
    assert WIZARD.count('{% for f in step.fields %}{{ field_editor(f) }}{% endfor %}') == 1


def test_every_row_says_which_step_and_which_kind_it_is():
    # The row's button opens that step and its kind decides whether it may be
    # edited here, so both come off the loop variables rather than a copy.
    over = section('overview')
    assert 'data-ovfield="{{ f.key }}"' in over
    assert 'data-ovkind="{{ f.kind }}"' in over
    assert 'data-ovin="{{ step.key }}"' in over


def test_the_field_list_is_not_written_out_by_hand():
    """55 fields across six steps. Any of them named literally in the overview
    is one that stops following `STEPS`."""
    over = section('overview')
    for field in FIELDS:
        assert f'"{field.key}"' not in over, field.key


def test_there_are_fields_to_walk():
    # A contract test that matched an empty list would pass for the wrong
    # reason.
    assert len(FIELDS) > 40
    assert {f.kind for f in FIELDS} > set(PLAIN)


# -- what may be edited in place, and what may not --------------------------

def test_only_plain_fields_are_editable_in_place():
    """A text box can be the same box in two places. The phases, a list, a
    table of rows or an image cannot — those are editors, not values, and
    nobody fixes one in passing. They show a summary and a way through."""
    over = section('overview')
    assert "'structural' if f.kind not in ('text', 'date', 'time', 'textarea')" in over


def test_the_two_places_agree_on_what_plain_means():
    # The template decides by Jinja, campaign.js by PLAIN; a field editable in
    # one and not the other is either an editor that never saves or a value
    # with no way to type into it.
    assert 'var PLAIN = { text: 1, date: 1, time: 1, textarea: 1 };' in SCRIPT


# -- the ids stay unique ----------------------------------------------------

def test_the_overview_does_not_repeat_the_step_editors_ids():
    """`campaign.js` binds every `input[data-key]` once at startup and finds a
    step's box by `#cpf-<key>`. A second element with that id would take the
    binding, and a value typed on the step would stop saving."""
    # Every one of those ids is inside `field_editor`, one branch per kind,
    # and exactly one branch runs per field.
    macro = WIZARD[WIZARD.index('{% macro field_editor(f) %}'):WIZARD.index('{% endmacro %}')]
    assert macro.count('id="cpf-{{ f.key }}"') == WIZARD.count('id="cpf-{{ f.key }}"') > 1
    assert 'id="cpf-' not in section('overview')
    # The overview's cells are addressed by attribute instead.
    assert 'data-ovvalue="{{ f.key }}"' in section('overview')


def test_an_overview_editor_carries_the_key_so_the_step_follows_along():
    # touch() mirrors a value into every other box holding that key, which is
    # what makes the overview and the step two views of one field.
    assert "input.dataset.key = key;" in SCRIPT
    assert "app.querySelectorAll('[data-key=\"' + key + '\"]')" in SCRIPT
    assert "other !== document.activeElement" in SCRIPT, \
        'writing .value back into the box being typed in moves the caret to the end'


# -- a save reply must not take an open editor away -------------------------

def test_a_save_reply_leaves_an_open_editor_alone():
    """`apply()` runs on every save reply and redraws the overview. A reply
    landing mid-word must not replace the box somebody is typing in."""
    assert "if (!cell || editing === key) return;" in SCRIPT


def test_the_overview_is_redrawn_with_everything_else():
    body = SCRIPT[SCRIPT.index('drawProgress();'):]
    assert 'drawOverview();' in body[:400]


# -- the side panel ---------------------------------------------------------

def test_the_step_cards_stand_down_on_the_overview():
    """Tips, Checks and the drop preview are about one step. Beside the
    overview, which carries its own schedule and its whole check list, they
    would say the same thing twice — and the preview would be blank, there
    being no one drop the overview is about."""
    assert WIZARD.count('data-side="step"') == 3
    assert "card.hidden = key === 'overview';" in SCRIPT


def test_the_mission_tools_stay():
    # The ORBAT, the maps, the recording and the briefing packs are about the
    # mission, not a step, so they belong beside the overview.
    tools = WIZARD[WIZARD.index('_cp_mission_tools.html') - 200:]
    assert 'data-side="step"' not in tools.split('_cp_mission_tools.html')[0][-200:]


# -- no second copy of a rule ----------------------------------------------

def test_the_overview_does_not_reimplement_next_due():
    """`campaign.next_due()` ranks overdue locks first and is tested. The tab's
    label is the first unfinished item in date order, which is a label rather
    than that rule — and the comment says so, because the two coming to
    disagree is exactly how this would go wrong."""
    assert 'campaign.next_due()' in SCRIPT


def test_the_checks_shown_are_all_of_them():
    # The side panel filters to the open step. This page answers "what is
    # left", so filtering it would defeat the point.
    over = SCRIPT[SCRIPT.index('function drawOverview'):]
    over = over[:over.index('\n  }\n')]
    assert 'state.checks.forEach' in over
    assert 'filter' not in over.split('state.checks.forEach')[0].split('cp-overchecks')[1]


# -- the stylesheet ---------------------------------------------------------

def test_the_overview_has_styles_of_its_own():
    for selector in ('.cpover', '.cpovfield', '.cpovsched', '.cpovinput', '.cpovrow'):
        assert selector in STYLE, selector


def test_a_schedule_row_has_a_column_for_every_part():
    """A drop row carries a note ("ORBAT opens for everyone") as well as its
    date, label, D-day, status and its button. Measured in Chromium: with one
    column short, every row with a note wrapped onto a second line."""
    assert 'grid-template-columns: 112px minmax(0, 1fr) repeat(4, auto);' in STYLE


def test_a_label_sits_at_the_top_of_its_value():
    """`align-items: baseline` lines a one-line label up against the last line
    of a three-line value, which reads as the label belonging to the field
    below. Measured in Chromium on the Concept step."""
    block = STYLE[STYLE.index('.cpovfield {'):]
    assert 'align-items: start;' in block[:block.index('}')]
