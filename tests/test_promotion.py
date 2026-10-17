"""`utils/promotion.py` — who is due a Cadet → Member promotion.

The cases that matter: a person is promoted once and only when mapped, holding
the first role, not yet holding the second, with enough missions; and the form
refuses what would silently do nothing.
"""

import pytest

from utils import promotion

CADET, MEMBER = '111111111111111111', '222222222222222222'
SETTINGS = {'enabled': True, 'auto': False, 'missions': 5,
            'from_role_id': CADET, 'to_role_id': MEMBER, 'channel_id': None}


def player(name, missions, *also):
    return {'name': name, 'names': sorted({name, *also}), 'missions': missions,
            'kills': 0, 'last': ''}


def due(people, mapped, roles, done=None, **settings):
    return promotion.candidates(people, mapped, roles, {**SETTINGS, **settings}, done or {})


def test_due_needs_every_condition():
    people = [player('Ann', 5), player('Bob', 4), player('Cy', 9), player('Di', 9), player('Ed', 9)]
    mapped = {'Ann': '1', 'Bob': '2', 'Cy': '3', 'Di': '4'}   # Ed is not mapped
    roles = {'1': {CADET}, '2': {CADET}, '3': {CADET, MEMBER}, '4': {'x'}}
    got = due(people, mapped, roles)
    assert [d['user_id'] for d in got] == ['1']          # Bob too few, Cy already member, Di no cadet
    assert got[0]['missions'] == 5 and got[0]['players'] == ['Ann']


def test_a_member_off_the_server_is_skipped():
    assert due([player('Ann', 9)], {'Ann': '1'}, {}) == []


def test_a_linked_name_is_found_by_any_of_its_recorded_names():
    people = [player('[X] Ann', 6, 'Ann')]
    assert [d['user_id'] for d in due(people, {'Ann': '1'}, {'1': {CADET}})] == ['1']


def test_a_member_mapped_to_two_players_has_their_missions_added():
    people = [player('Ann', 3), player('Ann2', 2)]
    got = due(people, {'Ann': '1', 'Ann2': '1'}, {'1': {CADET}})
    assert len(got) == 1 and got[0]['missions'] == 5 and got[0]['players'] == ['Ann', 'Ann2']


def test_promoted_and_dismissed_are_never_due_again_but_notified_still_is():
    people, mapped, roles = [player('Ann', 9)], {'Ann': '1'}, {'1': {CADET}}
    assert due(people, mapped, roles, {'1': promotion.PROMOTED}) == []
    assert due(people, mapped, roles, {'1': promotion.DISMISSED}) == []
    got = due(people, mapped, roles, {'1': promotion.NOTIFIED})
    assert got[0]['notified'] is True


def test_off_or_half_set_up_means_nobody():
    people, mapped, roles = [player('Ann', 9)], {'Ann': '1'}, {'1': {CADET}}
    assert due(people, mapped, roles, enabled=False) == []
    assert due(people, mapped, roles, to_role_id=None) == []


def test_most_missions_first():
    people = [player('Ann', 6), player('Bob', 9)]
    got = due(people, {'Ann': '1', 'Bob': '2'}, {'1': {CADET}, '2': {CADET}})
    assert [d['user_id'] for d in got] == ['2', '1']


def test_the_form():
    form = {'missions': '5', 'from_role_id': CADET, 'to_role_id': MEMBER, 'enabled': 'on'}
    got = promotion.clean_settings(form)
    assert got['enabled'] and not got['auto'] and got['missions'] == 5 and got['channel_id'] is None
    for bad in ({'missions': 'x'}, {'missions': '0'}, {'missions': '101'},
                {'to_role_id': CADET}, {'from_role_id': ''}, {'channel_id': 'abc'}):
        with pytest.raises(ValueError):
            promotion.clean_settings({**form, **bad})
    # Off, the roles may still be unset
    assert promotion.clean_settings({'missions': '5'})['enabled'] is False
