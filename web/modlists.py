"""Modlists in the browser: the channel and keywords, the history, an upload.

What a preset is, how it is classified and how a version is kept live in
`utils/modlist.py` and `cogs/modlists.py`; this module only shapes them for a
page and validates the forms.
"""

import discord

from cogs.modlists import store_preset
from utils import database, modlist


def readable_channels(guild: discord.Guild) -> list:
    """Text channels the bot can read the history of — what the catch-up needs."""
    me = guild.me
    if me is None:
        return []
    channels = [c for c in guild.text_channels
                if c.permissions_for(me).view_channel
                and c.permissions_for(me).read_message_history]
    channels.sort(key=lambda c: (c.category.position if c.category else -1, c.position))
    return channels


def read_settings_form(guild: discord.Guild, form) -> tuple:
    """`(channel_id, keywords)` from the settings form. Raises ValueError."""
    channel_id = (form.get('channel_id') or '').strip()
    if channel_id and channel_id not in [str(c.id) for c in readable_channels(guild)]:
        raise ValueError("I can't read that channel — pick another one.")
    typed = {slot: form.get(f'keywords_{slot}') for slot in modlist.SLOT_KEYS}
    return channel_id or None, modlist.keywords_from_form(typed)


async def overview(guild_id: str) -> dict:
    """Everything the page shows: settings, and per list its current version and history."""
    settings = await database.get_modlist_settings(guild_id)
    current = await database.get_current_modlists(guild_id)
    lists = []
    for slot, label in modlist.SLOTS:
        lists.append({
            'slot': slot,
            'label': label,
            'current': current.get(slot),
            'versions': await database.list_modlist_versions(guild_id, slot, limit=20),
            'keywords': ', '.join(settings['keywords'].get(slot, [])),
            'default_keywords': ', '.join(modlist.DEFAULT_KEYWORDS[slot]),
        })
    return {'settings': settings, 'lists': lists}


async def upload(guild: discord.Guild, member: discord.Member, slot: str,
                 data: bytes, filename: str) -> str:
    """Store an uploaded preset as the list's newest version. Raises ValueError."""
    if slot not in modlist.SLOT_KEYS:
        raise ValueError('Pick which list this is.')
    if len(data) > modlist.MAX_BYTES:
        raise ValueError('That file is larger than 1 MB — not an Arma preset.')
    parsed = modlist.parse_preset(data.decode('utf-8-sig', 'replace'))
    result = await store_preset(
        str(guild.id), slot, parsed, label=(filename or '').rsplit('.', 1)[0],
        posted_by=str(member.id), posted_by_name=member.display_name)
    label = modlist.SLOT_LABELS[slot]
    if result['status'] == 'stored':
        extra = f": {result['summary']}" if result['summary'] else ''
        return f'{label} updated{extra}.'
    return f'{label} is already up to date.'
