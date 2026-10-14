"""What the wizard's Overview tab promises.

The Overview is the back-schedule, the checks and every drop — the same thing
the no-JavaScript schedule page has always shown, as the wizard's first tab,
because *where does this mission stand* is the question somebody has on
opening one and the six step tabs each answer only for themselves.

It is one template, `_cp_schedule_body.html`, included by both pages. That is
the whole design: there is no second copy of the schedule to drift, nothing is
redrawn in the browser, and the forms in it post and reload like every other
form on this site. So what these tests pin is the sharing itself — that
neither page has grown its own copy, that both hand the partial what it asks
for, and that a form in it comes back to the page it was pressed on.
"""

import pathlib
import re

from utils import campaign as rules

ROOT = pathlib.Path(__file__).resolve().parent.parent
WIZARD = (ROOT / 'web' / 'templates' / 'cp_wizard.html').read_text(encoding='utf-8')
SCHEDULE = (ROOT / 'web' / 'templates' / 'cp_schedule.html').read_text(encoding='utf-8')
BODY = (ROOT / 'web' / 'templates' / '_cp_schedule_body.html').read_text(encoding='utf-8')
SCRIPT = (ROOT / 'web' / 'static' / 'campaign.js').read_text(encoding='utf-8')
PAGES = (ROOT / 'web' / 'campaigns.py').read_text(encoding='utf-8')
ROUTES = (ROOT / 'web' / 'routes' / 'campaigns.py').read_text(encoding='utf-8')


# -- the tab ----------------------------------------------------------------

def test_the_overview_is_the_first_tab():
    strip = WIZARD[WIZARD.index('<nav class="cpsteps"'):]
    strip = strip[:strip.index('</nav>')]
    assert strip.index('data-step="overview"') < strip.index('{% for step in steps %}')


def test_opening_a_mission_lands_on_the_overview():
    assert "if (known.indexOf(first) === -1) first = 'overview';" in SCRIPT
    assert "['overview'].concat(" in SCRIPT


def test_a_link_to_a_step_still_opens_that_step():
    # The checks and the drops panel point at one.
    assert "var first = (location.hash || '').slice(1);" in SCRIPT


# -- one template, two pages ------------------------------------------------

def test_both_pages_are_built_from_the_one_partial():
    assert "{% include '_cp_schedule_body.html' %}" in WIZARD
    assert "{% include '_cp_schedule_body.html' %}" in SCHEDULE


def test_each_page_says_where_its_forms_come_back_to():
    assert "{% with back = 'wizard' %}{% include '_cp_schedule_body.html' %}" in WIZARD
    assert "{% with back = 'schedule' %}{% include '_cp_schedule_body.html' %}" in SCHEDULE
    # and the partial writes that into every form rather than naming a page.
    assert 'name="back" value="{{ back }}"' in BODY
    assert 'name="back" value="schedule"' not in BODY


def test_neither_page_keeps_a_copy_of_the_schedule():
    """A second copy is exactly what this partial exists to prevent — the two
    would then disagree about what is due the first time either is edited."""
    for name, page in (('wizard', WIZARD), ('schedule', SCHEDULE)):
        # (the wizard's side panel has a `cpchecks` list of its own, filled by
        # campaign.js for the open step — a different thing from this loop)
        for markup in ('<table class="cptable cpschedule">', 'id="drop-{{ drop.key }}"',
                       '{% for drop in state.drops %}', '{% for c in state.checks %}'):
            assert markup not in page, f'{markup} is in cp_{name}.html as well as the partial'


def test_the_partial_carries_the_schedule_the_checks_and_the_drops():
    # The three things the page is for; a test that matched an empty partial
    # would pass for the wrong reason.
    assert '<table class="cptable cpschedule">' in BODY
    assert 'class="cpchecks"' in BODY
    assert '{% for drop in state.drops %}' in BODY
    for action in ('approve', 'unapprove', 'skip', 'unskip'):
        assert f"'{action}'" in BODY, action


def test_the_mission_tools_card_is_only_on_the_schedule_side():
    """The wizard's side panel already carries it, and two of the same card on
    one screen is what this guard is for."""
    assert "{% if back == 'schedule' %}" in BODY
    tools = BODY[BODY.index("{% if back == 'schedule' %}"):]
    assert "_cp_mission_tools.html" in tools[:tools.index('{% endif %}')]
    assert "_cp_mission_tools.html" not in SCHEDULE


# -- what the partial asks for, both pages hand over ------------------------

def test_both_pages_hand_over_the_steps_by_key():
    """The wizard's own `steps` is the ordered list its tabs loop over, so the
    partial names the lookup separately — it was `steps[c.step]` and rendered
    an empty step name on the wizard, where that is a list."""
    assert 'steps_by_key[c.step]' in BODY
    assert PAGES.count("'steps_by_key': {s.key: s for s in rules.STEPS}") == 2


def test_the_partial_reads_the_time_zone_off_the_state():
    # `tz` is a schedule-page variable; `state.tz` is on both.
    assert '{{ state.tz }}' in BODY
    assert '({{ tz }})' not in BODY


def test_the_wizard_still_loops_the_ordered_steps():
    assert "{% for step in steps %}" in WIZARD
    assert len(rules.STEPS) == 6


# -- a form comes back where it was pressed ---------------------------------

def test_every_mission_form_honours_back():
    """Four forms post out of the shared partial or the tools card beside it.
    The drop one always redirected to the schedule page, which from the
    Overview meant being thrown onto another page to approve a drop."""
    for route in ('orbat', 'recording', 'signups', 'drops'):
        block = ROUTES[ROUTES.index(f"/missions/{{mission_id}}/{route}"):]
        block = block[:block.index('@app.', 20)] if '@app.' in block[20:] else block
        assert "if form.get('back') == 'schedule':" in block or \
               "back = form.get('back') or 'wizard'" in block, route


def test_a_drop_action_comes_back_to_its_panel():
    assert "url + f'#drop-{kind}'" in ROUTES
    # ...which on the wizard is a panel inside the hidden Overview section.
    assert "first.slice(0, 5) === 'drop-'" in SCRIPT
    assert 'anchored.scrollIntoView();' in SCRIPT


# -- the browser draws none of it -------------------------------------------

def test_nothing_in_the_overview_is_redrawn_in_the_browser():
    """It is server-rendered, and the rules behind it live in Python. A
    renderer here would be a second copy of `campaign.schedule()`,
    `drop_state()` and the checks."""
    for gone in ('overviewValue', 'editField', 'fieldSummary', 'cpovedit',
                 'data-ovvalue', 'cpovinput'):
        assert gone not in SCRIPT, gone
    over = SCRIPT[SCRIPT.index('function drawOverview'):]
    over = over[:over.index('\n  }\n')]
    for gone in ('state.schedule', 'state.checks', 'state.drops'):
        assert gone not in over, f'drawOverview() reads {gone}'


def test_the_field_list_is_gone():
    for gone in ('data-ovfield', 'cpovfield', 'data-ovvalue', 'cpovfields'):
        assert gone not in WIZARD, gone


def test_it_admits_when_it_has_gone_stale():
    """An edit on another tab can move the op date or change a drop's words,
    and the schedule below was worked out when the page loaded."""
    assert 'id="cp-overstale"' in WIZARD
    assert 'id="cp-overreload"' in WIZARD
    assert 'stale = true;' in SCRIPT
    assert 'staleNote.hidden = !stale;' in SCRIPT


def test_the_step_cards_stand_down_on_the_overview():
    """Tips, Checks and the drop preview are about one step; the Overview
    carries its own checks and is about no one drop."""
    assert WIZARD.count('data-side="step"') == 3
    assert "card.hidden = key === 'overview';" in SCRIPT


# -- the no-JavaScript page is untouched ------------------------------------

def test_the_schedule_page_still_needs_no_javascript():
    assert '<script' not in SCHEDULE
    assert 'campaign.js' not in SCHEDULE
