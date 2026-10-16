"""Who may decide a slot request, and which ones are safe to decide in bulk.

**Stdlib only**, which is the whole point of the module. These three questions
were answered in two places that could not be tested:

- *may this person action this request?* lived inside
  `cogs/slots._can_action_request()`, which takes a `discord.Member` and reads
  `guild_permissions` off it
- *is this slot contested?* lived inline in `web/slots.queue()`, computed per
  page render and used only to put a warning on a row

The second one became load-bearing the moment approving in bulk existed: a
contested slot is a *choice* between members, and the one thing a bulk approve
must never do is make that choice by arriving first. So the rule moved here,
where `tests/test_approvals.py` can hold it, and both surfaces call it instead
of each keeping their own copy — the same split that put `is_emoji()` in
`utils/eventopts.py` rather than importing discord.py into the tests.

Requests arrive here as plain mappings carrying `id`, `unit_role` and `key`.
The key is `roster.request_key(row)`, computed by the caller: `utils/roster.py`
imports `utils.sheets` and therefore gspread, so this module cannot ask it.
"""

# The role that may decide a request. It lives here rather than in
# `cogs/slots.py` because the web, the cogs and the tests all need it and a
# module with no imports is the only place none of them has to reach sideways
# for. `cogs/slots.py` re-exports it, so `from cogs.admin import
# UNIT_LEADER_ROLE` in `cogs/gameroles.py` keeps working.
UNIT_LEADER_ROLE = 'Unit Leader'


def may_action(unit_role, role_names, is_admin: bool,
               leader_role: str = UNIT_LEADER_ROLE) -> bool:
    """Whether someone holding *role_names* may approve, deny or clear a
    request belonging to *unit_role*.

    - An admin (`manage_guild` or `administrator`) may action anything.
    - Anyone else needs the Unit Leader role **and** must share the requester's
      unit.
    - A request with no unit role may be actioned by any Unit Leader.

    The Unit Leader half is not optional and never has been: the buttons in
    `#slot-approvals` are visible to everyone who can read the channel, so
    without it any member of a unit could approve their own request by pressing
    the button on it. `/my-approvals` does not change that — it shows a smaller
    list, it does not grant anything — which is exactly why the check lives in
    one function both surfaces call.
    """
    if is_admin:
        return True
    names = set(role_names)
    if leader_role not in names:
        return False
    if not unit_role:
        return True
    return unit_role in names


def contested_keys(requests) -> set:
    """The slot keys more than one of *requests* is asking for.

    A request with **no key** is counted as contested rather than ignored. An
    empty key is what `roster.slot_key()` returns for a row that names neither
    an ORBAT slot nor a sheet cell, so two of them are not known to be the same
    slot — and they are not known to be different ones either. Bulk approval is
    the one caller that acts without a human looking at the row, so the
    unidentifiable case has to fail closed.
    """
    counts = {}
    for req in requests:
        key = req.get('key') or ''
        counts[key] = counts.get(key, 0) + 1
    return {key for key, count in counts.items() if count > 1 or not key}


def actionable(requests, role_names, is_admin: bool) -> list:
    """Just the requests this person may decide, in the order given."""
    return [req for req in requests
            if may_action(req.get('unit_role'), role_names, is_admin)]


def bulk_approvable(requests, role_names, is_admin: bool) -> list:
    """The ids that may be approved in one press, in the order given.

    Every request this person may action, minus the contested ones. Note what
    decides *contested*: the whole list passed in, not the actionable subset. A
    slot wanted by this unit and another one is contested, and approving it
    would deny the other unit's request as a side effect — see the competitor
    denials in `approve_slot_request()`. Filtering first and counting second
    would hide exactly that case, which is the one that matters.
    """
    contested = contested_keys(requests)
    return [req['id'] for req in actionable(requests, role_names, is_admin)
            if (req.get('key') or '') not in contested]


def still_approvable(shown_ids, requests, role_names, is_admin: bool) -> list:
    """Of the ids a snapshot offered, the ones still safe to approve now.

    The list a person pressed the button on was built when the command ran, and
    a second request for one of those slots can arrive in between — which turns
    an uncontested slot into a choice between members. Re-running the rule is
    not enough on its own: it has to be **intersected with what was shown**, in
    the order it was shown.

    So two things are guaranteed, and each matters for a different reason.
    Nothing that became contested is approved, because arriving first is not
    how that gets decided. And nothing the person never saw is approved either,
    even if it would now qualify — a request that landed a second before the
    press was not on the list they agreed to.
    """
    allowed = set(bulk_approvable(requests, role_names, is_admin))
    return [request_id for request_id in shown_ids if request_id in allowed]
