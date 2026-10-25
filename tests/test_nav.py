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

    Measured in Chromium against the real stylesheet: seven group tabs plus the
    `?` came to 824px, which fits; nine wrapped onto a second line at *every*
    width. The page finder now shares the row, so the budget is five groups --
    a new top-level tab goes inside a group, or the row wraps again.
    """
    assert len(nav.build('1', **ADMIN)['groups']) <= 5


def test_the_admin_housekeeping_is_one_group():
    built = nav.build('1', **ADMIN)
    assert pages(built, 'community') == [
        'roles', 'voice', 'logs', 'embeds', 'reddit', 'promotion', 'opsettings']


# --- who sees what -----------------------------------------------------------

def test_a_plain_member_gets_the_pages_a_plain_member_may_open():
    built = nav.build('1', **MEMBER)
    assert keys(built) == ['missions', 'community', 'maps']
    assert pages(built, 'missions') == ['events']
    assert pages(built, 'community') == ['roles', 'voice']


def test_a_unit_leader_gets_the_queue_beside_the_events():
    built = nav.build('1', **LEADER)
    assert pages(built, 'missions') == ['events', 'slots']


def test_the_mission_makers_work_is_one_group_in_order():
    built = nav.build('1', **ADMIN)
    assert pages(built, 'missions') == [
        'events', 'modlists', 'orbats', 'slots', 'operation']


def test_the_missions_group_lands_on_the_events_for_everybody():
    for who in (ADMIN, LEADER, MEMBER):
        built = nav.build('1', **who)
        assert built['groups'][0]['href'] == '/g/1'


def test_the_group_still_being_tried_out_goes_last_and_says_so():
    built = nav.build('1', **ADMIN)
    assert keys(built)[-2:] == ['cp', 'maps']
    flagged = [g['key'] for g in built['groups'] if g.get('beta')]
    assert flagged == ['cp', 'maps']


def test_a_group_nobody_may_open_is_not_built():
    built = nav.build('1', **MEMBER)
    assert pages(built, 'kt') == [] and 'kt' not in keys(built)
    assert pages(built, 'cp') == [] and 'cp' not in keys(built)


def test_planning_without_being_an_admin_gets_the_planner_but_not_its_settings():
    built = nav.build('1', is_admin=False, may_action_slots=False, may_plan=True)
    assert pages(built, 'cp') == ['campaigns', 'cpobjectives']


# --- the page finder ---------------------------------------------------------

def found(who, query):
    return [p['key'] for p in nav.search(nav.build('1', **who), query)]


def test_search_finds_a_page_by_name_or_keyword():
    assert found(ADMIN, 'mod') == ['modlists']
    assert found(ADMIN, 'subreddit') == ['reddit']
    assert found(ADMIN, 'MODLISTS') == ['modlists']


def test_search_needs_every_word_to_match():
    assert found(ADMIN, 'kill longest') == ['ktkills']
    assert found(ADMIN, 'mods zzz') == []


def test_search_puts_a_name_match_before_a_keyword_match():
    # "roster" is a keyword of ORBATs; "slot" is in the name Slot Approvals and
    # a keyword of ORBATs -- the name wins.
    assert found(ADMIN, 'slot')[0] == 'slots'


def test_search_never_offers_a_page_the_member_may_not_open():
    for who in (LEADER, MEMBER):
        assert found(who, 'modlists') == []
        assert found(who, 'embeds') == []
    assert found(MEMBER, 'voice') == ['voice']
    assert found(MEMBER, 'maps') == ['maps']


def test_an_empty_search_finds_nothing():
    assert found(ADMIN, '') == [] and found(ADMIN, '   ') == []


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
