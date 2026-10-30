"""Promotion from the browser: Cadet → Member after enough Kill Tracker missions.

Like `web/roles.py`, this holds no rules of its own. Who is due is
`utils/promotion.py`, the role swap and the admin-channel messages are
`cogs/killtracker.py`; this reads the settings form and shows the list. A
`ValueError` raised here is a message meant for the admin.
"""

from cogs import killtracker as killtracker_cog
from utils import activeduty, database, promotion
from web.guilds import postable_channels


async def promotion_view(guild) -> dict:
    """The settings, the roles and channels to pick from, and who is due now."""
    settings = await database.kt_promotion_settings(guild.id)
    due = await killtracker_cog.due_for_promotion(guild, settings)
    for d in due:
        member = guild.get_member(int(d['user_id']))
        d['member'] = member.display_name if member else d['user_id']
    return {
        'settings': settings,
        'roles': [r for r in reversed(guild.roles) if not r.is_default() and not r.managed],
        'channels': postable_channels(guild),
        'due': due,
        'mapped': len(await database.kt_player_members(guild.id)),
        'limits': (promotion.MIN_MISSIONS, promotion.MAX_MISSIONS),
        'default_message': promotion.DEFAULT_MESSAGE,
        'placeholders': promotion.PLACEHOLDERS,
        'duty': await database.kt_active_duty_settings(guild.id),
        'duty_limits': (activeduty.MIN_OPERATIONS, activeduty.MAX_OPERATIONS),
    }


async def save_promotion(guild, form) -> str:
    """Apply the settings form. Raises `ValueError`."""
    fields = ('missions', 'from_role_id', 'to_role_id', 'channel_id', 'message')
    values = {f: form.get(f) for f in fields}
    values.update(enabled=form.get('enabled'), auto=form.get('auto'))
    settings = promotion.clean_settings(values)
    for key in ('from_role_id', 'to_role_id'):
        if settings[key] and guild.get_role(int(settings[key])) is None:
            raise ValueError('That role is not on this server.')
    if settings['channel_id'] and guild.get_channel(int(settings['channel_id'])) is None:
        raise ValueError('That channel is not on this server.')
    await database.kt_save_promotion_settings(guild.id, settings)
    if not settings['enabled']:
        return 'Saved. Promotions are off.'
    return 'Saved. ' + ('Members are promoted automatically.' if settings['auto']
                        else 'The admin channel is told who is due; you promote them here.')


async def post_test(guild, member, form) -> str:
    """Post a test message with the form as it stands — saved or not — about the
    admin pressing the button. Raises `ValueError`."""
    fields = ('missions', 'from_role_id', 'to_role_id', 'channel_id', 'message')
    values = {f: form.get(f) for f in fields}
    settings = promotion.clean_settings(values)       # `enabled` is left off: a test needs none
    problem = await killtracker_cog.post_test_ask(guild, settings, member)
    if problem:
        raise ValueError(problem)
    return 'Test message posted. Nothing was saved and nobody was promoted.'


async def promote_now(guild, user_ids) -> str:
    """Promote the ticked people, or everyone due when `user_ids` is empty-handed
    by choice (the caller passes `None`). Only people who are actually due."""
    settings = await database.kt_promotion_settings(guild.id)
    due = {d['user_id'] for d in await killtracker_cog.due_for_promotion(guild, settings)}
    wanted = [u for u in (due if user_ids is None else user_ids) if u in due]
    if not wanted:
        raise ValueError('Nobody on that list is due any more.')
    promoted, problems = await killtracker_cog.promote_members(guild, wanted, settings)
    note = f'Promoted {len(promoted)}.' if promoted else 'Nobody was promoted.'
    return ' '.join([note] + problems)


async def dismiss_promotion(guild, user_ids) -> str:
    """Pass over the ticked people for good: they are not listed or announced again."""
    if not user_ids:
        raise ValueError('Tick at least one person.')
    await database.kt_set_promotion(guild.id, user_ids, promotion.DISMISSED)
    return f'{len(user_ids)} passed over.'


async def save_active_duty(guild, form) -> str:
    """Apply the Active Duty form and bring the role up to date. Raises `ValueError`."""
    settings = activeduty.clean_settings(
        {f: form.get(f) for f in ('operations', 'role_id', 'enabled')})
    if settings['role_id'] and guild.get_role(int(settings['role_id'])) is None:
        raise ValueError('That role is not on this server.')
    await database.kt_save_active_duty_settings(guild.id, settings)
    if not settings['enabled']:
        return 'Saved. Active Duty is off.'
    given, taken, problems = await killtracker_cog.sync_active_duty(guild, settings)
    return ' '.join([f'Saved. Given to {len(given)}, taken from {len(taken)}.'] + problems)
