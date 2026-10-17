"""Who is due a promotion: a Cadet with enough missions becomes a Member.

The Kill Tracker counts the missions (`killtracker.player_missions()`), the
admin page maps players to Discord members, and this decides who is due. It
decides nothing else: handing out the role, and telling the admins, are
`cogs/killtracker.py`. Imports nothing but the standard library, so the tests
reach it.

A promotion is **off until an admin turns it on**, and when it is on it either
only tells the admins who is due (the default) or does it (`auto`).
"""

MIN_MISSIONS, MAX_MISSIONS = 1, 100
DEFAULT_MISSIONS = 5

# What a member's promotion can be, once it is not simply "due".
NOTIFIED, PROMOTED, DISMISSED = 'notified', 'promoted', 'dismissed'


def defaults() -> dict:
    return {'enabled': False, 'auto': False, 'missions': DEFAULT_MISSIONS,
            'from_role_id': None, 'to_role_id': None, 'channel_id': None}


def _id(text, what: str):
    """A Discord id from a form, or `None` for empty."""
    text = str(text or '').strip()
    if not text:
        return None
    if not text.isdigit() or not 15 <= len(text) <= 22:
        raise ValueError(f'That {what} is not valid.')
    return text


def clean_settings(form: dict) -> dict:
    """The settings form as a settings dict. Raises `ValueError` with a sentence
    for the admin. `form` is `{field: text}`; a checkbox is present when ticked."""
    try:
        missions = int(str(form.get('missions') or '').strip())
    except ValueError:
        raise ValueError('The number of missions must be a whole number.')
    if not MIN_MISSIONS <= missions <= MAX_MISSIONS:
        raise ValueError(f'The number of missions must be between {MIN_MISSIONS} and {MAX_MISSIONS}.')
    settings = {
        'enabled': bool(form.get('enabled')), 'auto': bool(form.get('auto')),
        'missions': missions,
        'from_role_id': _id(form.get('from_role_id'), 'role'),
        'to_role_id': _id(form.get('to_role_id'), 'role'),
        'channel_id': _id(form.get('channel_id'), 'channel'),
    }
    if settings['from_role_id'] and settings['from_role_id'] == settings['to_role_id']:
        raise ValueError('The two roles must be different.')
    if settings['enabled'] and not (settings['from_role_id'] and settings['to_role_id']):
        raise ValueError('Pick both roles before turning promotions on.')
    return settings


def candidates(people, mapped: dict, roles: dict, settings: dict, done: dict) -> list:
    """Everyone who is due, most missions first.

    `people` are `player_missions()` results; `mapped` is `{name as recorded →
    Discord user id}`; `roles` is `{user id → set of role ids}` for the members
    who are on the server (anyone missing from it is skipped); `done` is
    `{user id → status}` from an earlier pass.

    Due means: mapped, holds the from-role, does not hold the to-role, has at
    least `missions` missions, and was neither promoted nor dismissed before.
    A member mapped to several players has their missions added up — each name
    is a different recording, because a player has one name per mission.
    Each result has `user_id`, `players`, `missions` and `notified`.
    """
    from_id, to_id = settings.get('from_role_id'), settings.get('to_role_id')
    if not (settings.get('enabled') and from_id and to_id):
        return []
    per_user = {}
    for person in people:
        user_id = next((mapped[n] for n in person['names'] if n in mapped), None)
        if user_id is None:
            continue
        entry = per_user.setdefault(user_id, {'user_id': user_id, 'players': [], 'missions': 0})
        entry['players'].append(person['name'])
        entry['missions'] += person['missions']
    due = []
    for user_id, entry in per_user.items():
        held = roles.get(user_id)
        if held is None or str(from_id) not in held or str(to_id) in held:
            continue
        if entry['missions'] < settings['missions']:
            continue
        status = done.get(user_id)
        if status in (PROMOTED, DISMISSED):
            continue
        due.append(dict(entry, notified=status == NOTIFIED))
    return sorted(due, key=lambda e: (-e['missions'], e['players'][0].lower()))
