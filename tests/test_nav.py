"""What the tab bar promises.

`web/nav.py` imports nothing, which is the only reason this is testable: the
shape of the bar — which groups exist, who sees what, where each one lands —
is exactly what goes wrong when it is spread across template conditionals, and
it is not the kind of thing anybody notices from a screenshot of their own
account's view.
"""

import pathlib
import re

from web import nav

ROOT = pathlib.Path(__file__).resolve().parent.parent

ADMIN = dict(is_admin=True, may_action_slots=True, may_plan=True,
             may_edit_orbats=True)
LEADER = dict(is_admin=False, may_action_slots=True)
MEMBER = dict(is_admin=False, may_action_slots=False)


def keys(built):
    return [group['key'] for group in built['groups']]


def pages(built, group):
    return [item['key'] for item in built['items'].get(group, [])]


# --- the row fits one line ---------------------------------------------------

def test_the_top_row_stays_short_enough_to_fit():
    """The column is 900px whatever the window is, so the row has 860px.

    Measured in Chromium against the real stylesheet: the seven group tabs an
    admin sees plus the `?` come to 824px, which fits; the nine tabs before
    Embeds, the member log and the Reddit watches moved into **Server** came
    to 919px and wrapped onto a second line at *every* width. So this is a
    budget, not a preference — a new top-level tab goes inside a group, or
    the row wraps again for everybody.
    """
    assert len(nav.build('1', **ADMIN)['groups']) <= 7


def test_the_admin_only_pages_are_a_group_rather_than_three_tabs():
    built = nav.build('1', **ADMIN)
    assert pages(built, 'server') == ['embeds', 'logs', 'reddit']
    assert 'server' in keys(built)
    # And nobody else sees it at all.
    for who in (LEADER, MEMBER):
        other = nav.build('1', **who)
        assert pages(other, 'server') == []
        assert 'server' not in keys(other)


# --- who sees what -----------------------------------------------------------

def test_a_plain_member_gets_the_pages_a_plain_member_may_open():
    built = nav.build('1', **MEMBER)
    assert keys(built) == ['events', 'ops', 'roles', 'voice']
    # Maps is the one page in the Operations group with no permission on it:
    # the plan is for everyone who has to fight it.
    assert pages(built, 'ops') == ['maps']


def test_a_unit_leader_gets_the_queue_and_lands_on_it():
    built = nav.build('1', **LEADER)
    assert pages(built, 'ops') == ['slots', 'maps']
    ops = next(g for g in built['groups'] if g['key'] == 'ops')
    assert ops['href'].endswith('/slots'), (
        "a group's own link is its first reachable page, so a Unit Leader "
        "lands on the queue and an admin on Operation"
    )


def test_an_admin_lands_on_the_operation():
    built = nav.build('1', **ADMIN)
    ops = next(g for g in built['groups'] if g['key'] == 'ops')
    assert ops['href'].endswith('/operation')


def test_a_group_nobody_may_open_is_not_built():
    built = nav.build('1', **MEMBER)
    assert pages(built, 'kt') == [] and 'kt' not in keys(built)
    assert pages(built, 'cp') == [] and 'cp' not in keys(built)


def test_planning_without_being_an_admin_gets_the_planner_but_not_its_settings():
    built = nav.build('1', is_admin=False, may_action_slots=False, may_plan=True)
    assert pages(built, 'cp') == ['campaigns', 'cpobjectives']


# --- the structure holds together -------------------------------------------

def test_every_group_links_to_its_own_first_page():
    for who in (ADMIN, LEADER, MEMBER):
        built = nav.build('1', **who)
        for group in built['groups']:
            items = built['items'].get(group['key'])
            if items:
                assert group['href'] == items[0]['href'], group['key']


def test_every_page_in_a_group_knows_which_group_it_is_in():
    # `_nav.html` renders the second row from `group_of[active]`, so a page
    # missing from it would land on a bar that does not mark it anywhere.
    built = nav.build('1', **ADMIN)
    for group, items in built['items'].items():
        for item in items:
            assert built['group_of'][item['key']] == group


def test_every_href_is_under_this_guild():
    built = nav.build('99', **ADMIN)
    every = built['groups'] + [i for items in built['items'].values() for i in items]
    for item in every:
        assert item['href'].startswith('/g/99'), item


def test_no_key_is_used_twice():
    built = nav.build('1', **ADMIN)
    every = [g['key'] for g in built['groups']] + list(built['group_of'])
    assert len(set(every)) == len(every)


# --- what the templates expect ----------------------------------------------

def test_no_page_still_heads_itself_with_the_guild_name():
    """The guild's name is in the top bar, once, not an `<h1>` on every page.

    It was the same heading on all of them, so it said nothing and pushed each
    page's own content a line and a half down — with the tab row wrapping as
    well, the content started 353px into the page and now starts at 238px.
    """
    stale = sorted(p.name for p in (ROOT / 'web' / 'templates').glob('*.html')
                   if '<h1>{{ guild.name }}</h1>' in p.read_text(encoding='utf-8'))
    assert stale == [], stale


def test_no_page_says_who_is_signed_in():
    # The avatar and the name are in the top bar on every page already.
    stale = sorted(p.name for p in (ROOT / 'web' / 'templates').glob('*.html')
                   if re.search(r'Signed in as', p.read_text(encoding='utf-8')))
    assert stale == [], stale
