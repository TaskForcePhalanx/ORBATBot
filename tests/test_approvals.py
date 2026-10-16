"""The approval rules: who may decide, and what is safe to decide in bulk.

These were untestable until `utils/approvals.py` existed — one answer lived
behind a `discord.Member`, the other inline in a web page's render. The bulk
case is the reason they had to come out: it acts on several requests with
nobody reading the rows, so *contested* stops being a warning label and becomes
the thing standing between a press and somebody's slot being given away.
"""

import pytest

from utils import approvals


def req(id, unit_role=None, key='db:1'):
    return {'id': id, 'unit_role': unit_role, 'key': key}


# --------------------------------------------------------------------------
# may_action
# --------------------------------------------------------------------------

def test_an_admin_may_action_anything():
    assert approvals.may_action('TFP', [], is_admin=True)
    assert approvals.may_action(None, [], is_admin=True)
    # Not even the Unit Leader role, and not their unit.
    assert approvals.may_action('CNTO', ['Recruit'], is_admin=True)


def test_a_unit_leader_may_action_their_own_unit():
    assert approvals.may_action('TFP', ['Unit Leader', 'TFP'], is_admin=False)


def test_a_unit_leader_may_not_action_another_unit():
    assert not approvals.may_action('CNTO', ['Unit Leader', 'TFP'], is_admin=False)


def test_a_request_with_no_unit_may_be_actioned_by_any_unit_leader():
    assert approvals.may_action(None, ['Unit Leader'], is_admin=False)
    assert approvals.may_action('', ['Unit Leader', 'TFP'], is_admin=False)


def test_the_unit_role_alone_is_not_enough():
    """The half that stops a member approving their own request.

    The buttons in #slot-approvals are visible to everyone who can read the
    channel, so sharing the unit cannot be sufficient — otherwise anyone in
    TFP could approve a TFP request, including their own.
    """
    assert not approvals.may_action('TFP', ['TFP'], is_admin=False)


def test_nobody_without_the_leader_role_may_action_an_unassigned_request():
    assert not approvals.may_action(None, ['TFP'], is_admin=False)


def test_the_leader_role_name_is_the_one_the_module_publishes():
    assert approvals.UNIT_LEADER_ROLE == 'Unit Leader'
    assert approvals.may_action(
        'TFP', [approvals.UNIT_LEADER_ROLE, 'TFP'], is_admin=False)


# --------------------------------------------------------------------------
# contested_keys
# --------------------------------------------------------------------------

def test_one_request_per_slot_is_never_contested():
    rows = [req(1, key='db:1'), req(2, key='db:2'), req(3, key='sheet:r4c2')]
    assert approvals.contested_keys(rows) == set()


def test_two_requests_for_one_slot_are_contested():
    rows = [req(1, key='db:7'), req(2, key='db:7'), req(3, key='db:8')]
    assert approvals.contested_keys(rows) == {'db:7'}


def test_contention_does_not_care_which_units_are_asking():
    """Two units wanting one slot is the case this exists for.

    Approving either one denies the other as a side effect, which is a choice
    about another unit's member — so it is contested even though each Unit
    Leader can only see, and only action, their own row.
    """
    rows = [req(1, 'TFP', 'db:7'), req(2, 'CNTO', 'db:7')]
    assert approvals.contested_keys(rows) == {'db:7'}


def test_a_request_with_no_key_is_treated_as_contested():
    """Fail closed: an empty key is what `slot_key()` gives a row naming
    neither a slot id nor a sheet cell, so it is not known to be a slot of its
    own."""
    assert approvals.contested_keys([req(1, key='')]) == {''}
    assert approvals.contested_keys([req(1, key=None)]) == {''}
    rows = [req(1, key=''), req(2, key='db:3')]
    assert approvals.contested_keys(rows) == {''}


def test_an_empty_queue_has_nothing_contested():
    assert approvals.contested_keys([]) == set()


# --------------------------------------------------------------------------
# actionable
# --------------------------------------------------------------------------

def test_actionable_keeps_only_this_leaders_unit_and_the_order_given():
    rows = [req(1, 'CNTO'), req(2, 'TFP'), req(3, None), req(4, 'TFP')]
    got = approvals.actionable(rows, ['Unit Leader', 'TFP'], is_admin=False)
    assert [r['id'] for r in got] == [2, 3, 4]


def test_actionable_gives_an_admin_the_whole_queue():
    rows = [req(1, 'CNTO'), req(2, 'TFP'), req(3, None)]
    got = approvals.actionable(rows, [], is_admin=True)
    assert [r['id'] for r in got] == [1, 2, 3]


# --------------------------------------------------------------------------
# bulk_approvable — where the two rules meet
# --------------------------------------------------------------------------

def test_bulk_takes_the_uncontested_requests_of_my_own_unit():
    rows = [req(1, 'TFP', 'db:1'), req(2, 'CNTO', 'db:2'), req(3, 'TFP', 'db:3')]
    assert approvals.bulk_approvable(
        rows, ['Unit Leader', 'TFP'], is_admin=False) == [1, 3]


def test_bulk_leaves_a_slot_my_own_unit_is_fighting_over():
    rows = [req(1, 'TFP', 'db:9'), req(2, 'TFP', 'db:9'), req(3, 'TFP', 'db:4')]
    assert approvals.bulk_approvable(
        rows, ['Unit Leader', 'TFP'], is_admin=False) == [3]


def test_bulk_leaves_a_slot_another_unit_also_wants():
    """The case that decides the implementation.

    Contention is counted over the *whole* queue, not over the rows this
    leader may action. Count after filtering and request 1 looks uncontested —
    so one press would approve it and auto-deny CNTO's request for the same
    slot, which is a decision about another unit made by a button that said
    "uncontested".
    """
    rows = [req(1, 'TFP', 'db:9'), req(2, 'CNTO', 'db:9')]
    assert approvals.bulk_approvable(
        rows, ['Unit Leader', 'TFP'], is_admin=False) == []


def test_bulk_for_an_admin_still_skips_contested_slots():
    rows = [req(1, 'TFP', 'db:9'), req(2, 'CNTO', 'db:9'), req(3, 'PXG', 'db:5')]
    assert approvals.bulk_approvable(rows, [], is_admin=True) == [3]


def test_bulk_gives_a_plain_member_nothing():
    rows = [req(1, 'TFP', 'db:1'), req(2, 'TFP', 'db:2')]
    assert approvals.bulk_approvable(rows, ['TFP'], is_admin=False) == []


def test_bulk_never_includes_an_unidentifiable_request():
    rows = [req(1, 'TFP', ''), req(2, 'TFP', 'db:2')]
    assert approvals.bulk_approvable(
        rows, ['Unit Leader', 'TFP'], is_admin=False) == [2]


def test_bulk_keeps_the_order_it_was_given():
    rows = [req(7, 'TFP', 'db:7'), req(3, 'TFP', 'db:3'), req(5, 'TFP', 'db:5')]
    assert approvals.bulk_approvable(
        rows, ['Unit Leader', 'TFP'], is_admin=False) == [7, 3, 5]


def test_bulk_on_an_empty_queue_is_empty():
    assert approvals.bulk_approvable([], ['Unit Leader', 'TFP'],
                                     is_admin=False) == []


# --------------------------------------------------------------------------
# still_approvable — the snapshot re-check
# --------------------------------------------------------------------------

LEADER = ['Unit Leader', 'TFP']


def test_an_unchanged_queue_approves_everything_that_was_shown():
    rows = [req(1, 'TFP', 'db:1'), req(2, 'TFP', 'db:2')]
    assert approvals.still_approvable([1, 2], rows, LEADER, False) == [1, 2]


def test_a_slot_that_became_contested_is_dropped():
    """Someone else asked for db:2 between the list and the press."""
    rows = [req(1, 'TFP', 'db:1'), req(2, 'TFP', 'db:2'),
            req(9, 'CNTO', 'db:2')]
    assert approvals.still_approvable([1, 2], rows, LEADER, False) == [1]


def test_a_request_that_arrived_after_the_list_is_not_approved():
    """Even though it now qualifies on its own.

    It was not on the list the person agreed to, and a bulk approve that
    reaches past what it displayed is the one thing it must not do.
    """
    rows = [req(1, 'TFP', 'db:1'), req(5, 'TFP', 'db:5')]
    assert approvals.still_approvable([1], rows, LEADER, False) == [1]


def test_a_request_decided_in_the_meantime_is_dropped():
    """Approved or denied elsewhere, so it is no longer in the pending queue."""
    rows = [req(1, 'TFP', 'db:1')]
    assert approvals.still_approvable([1, 2], rows, LEADER, False) == [1]


def test_the_shown_order_is_kept_not_the_queue_order():
    rows = [req(3, 'TFP', 'db:3'), req(1, 'TFP', 'db:1'), req(2, 'TFP', 'db:2')]
    assert approvals.still_approvable([1, 2, 3], rows, LEADER, False) == [1, 2, 3]


def test_losing_the_leader_role_between_list_and_press_approves_nothing():
    rows = [req(1, 'TFP', 'db:1')]
    assert approvals.still_approvable([1], rows, ['TFP'], False) == []


def test_an_empty_queue_approves_nothing():
    assert approvals.still_approvable([1, 2], [], LEADER, False) == []


# --------------------------------------------------------------------------
# the stdlib-only promise
# --------------------------------------------------------------------------

def test_the_module_imports_nothing_outside_the_standard_library():
    """What makes every test above possible. A single `from utils import
    database` in here and none of this could run without a server."""
    import ast
    import pathlib
    import sys

    source = pathlib.Path(approvals.__file__).read_text()
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split('.')[0])
    outside = imported - set(sys.stdlib_module_names)
    assert not outside, f"utils/approvals.py imports {sorted(outside)}"
