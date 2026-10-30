"""Who is on Active Duty: everyone who played in one of the last few operations.

The Kill Tracker counts the recordings, the admin page maps players to Discord
members, and this decides who holds the role. It decides nothing else: handing
the role out and taking it back is `cogs/killtracker.py`. Imports nothing but the
standard library, so the tests reach it.

The role is **managed**: it follows the last `operations` recordings, so whoever
holds it without having played in them loses it, however they came by it.
"""

MIN_OPERATIONS, MAX_OPERATIONS = 1, 20
DEFAULT_OPERATIONS = 4


def defaults() -> dict:
    return {'enabled': False, 'role_id': None, 'operations': DEFAULT_OPERATIONS}


def clean_settings(form: dict) -> dict:
    """The settings form as a settings dict. Raises `ValueError` with a sentence
    for the admin. A checkbox is present when ticked."""
    try:
        operations = int(str(form.get('operations') or '').strip())
    except ValueError:
        raise ValueError('The number of operations must be a whole number.')
    if not MIN_OPERATIONS <= operations <= MAX_OPERATIONS:
        raise ValueError(f'The number of operations must be between {MIN_OPERATIONS} and {MAX_OPERATIONS}.')
    role_id = str(form.get('role_id') or '').strip() or None
    if role_id is not None and (not role_id.isdigit() or not 15 <= len(role_id) <= 22):
        raise ValueError('That role is not valid.')
    enabled = bool(form.get('enabled'))
    if enabled and not role_id:
        raise ValueError('Pick the role before turning Active Duty on.')
    return {'enabled': enabled, 'role_id': role_id, 'operations': operations}


def active_users(recordings, mapped: dict) -> set:
    """Discord user ids who appear in any of `recordings`.

    `recordings` is one collection of player names (as recorded) per operation;
    `mapped` is `{name as recorded → Discord user id}`. A player nobody has mapped
    cannot hold a role, so they are not here."""
    users = set()
    for names in recordings:
        users.update(mapped[n] for n in names if n in mapped)
    return users


def changes(active: set, holders: set, present: set) -> tuple:
    """`(to give, to take back)` as sorted user-id lists.

    `holders` are the ids holding the role now, `present` the ids on the server:
    someone who left cannot be given anything."""
    give = sorted((active & present) - holders)
    take = sorted(holders - active)
    return give, take
