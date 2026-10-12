"""The campaign planner's web side — the glue between the routes in
`web/app.py`, the rules in `utils/campaign.py` and the tables in
`utils/database.py`.

Milestone 1 is the planner's settings: who may plan, where drops go, when
they go out and which drop opens the ORBAT.
"""

from utils import campaign as rules
from utils import database
from web.guilds import mentionable_roles, postable_channels


async def settings(guild_id) -> dict:
    """A guild's planner settings, with defaults filled in."""
    return rules.settings_with_defaults(await database.get_cp_settings(str(guild_id)))


async def settings_page(guild) -> dict:
    """Everything `cp_settings.html` needs besides the usual guild context."""
    current = await settings(guild.id)
    channels = postable_channels(guild)
    roles = mentionable_roles(guild)
    channel_ids = {str(c.id) for c in channels}
    role_ids = {str(r.id) for r in roles}
    # A setting whose channel or role has gone (deleted, or the bot lost
    # access) would silently stop drops — say so on the page.
    problems = []
    for key, what in (('drop_channel_id', 'drop channel'),
                      ('team_channel_id', 'mission team channel')):
        if current[key] and current[key] not in channel_ids:
            problems.append(f"I can't post in the {what} any more — pick it again.")
    if current['unit_role_id'] and current['unit_role_id'] not in role_ids:
        problems.append('The unit role no longer exists — pick it again.')
    return {
        'settings': current,
        'channels': channels,
        'roles': roles,
        'weekdays': list(enumerate(rules.WEEKDAYS)),
        'openers': [(key, rules.DROP[key].label) for key in rules.ORBAT_OPENERS],
        'default_survey_url': rules.DEFAULT_SURVEY_URL,
        'problems': problems,
    }


async def save_settings(guild, form) -> str:
    """Validate and store the settings form. Raises ValueError for a person."""
    clean = rules.clean_settings(
        form,
        role_ids=[r.id for r in mentionable_roles(guild)],
        channel_ids=[c.id for c in postable_channels(guild)],
    )
    await database.save_cp_settings(str(guild.id), clean)
    if not clean['drop_channel_id']:
        return 'Planner settings saved. Drops will not post until a drop channel is set.'
    return 'Planner settings saved.'
