"""Per-guild settings: the timezone and which channels the bot posts into.

Part of `utils/database`, split out of one 3,333-line module. The package's
`__init__` re-exports every name, so `from utils import database` and
`database.anything` work exactly as they did.

The rules these queries serve are in [docs/schema.md](../../docs/schema.md).
"""
from .core import (CHANNEL_KINDS, get_pool)


async def get_guild_timezone(guild_id: str) -> str:
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            'SELECT timezone FROM guild_settings WHERE guild_id = $1', guild_id
        )
        return row['timezone'] if row else 'UTC'


async def get_ocap_base_url(guild_id: str) -> str:
    """Where this guild's OCAP serves its terrains — '' until one is saved."""
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            'SELECT ocap_base_url FROM guild_settings WHERE guild_id = $1', guild_id
        )
        return (row['ocap_base_url'] or '') if row else ''


async def set_ocap_base_url(guild_id: str, url: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''INSERT INTO guild_settings (guild_id, timezone, ocap_base_url)
               VALUES ($1, 'UTC', $2)
               ON CONFLICT (guild_id)
               DO UPDATE SET ocap_base_url = EXCLUDED.ocap_base_url''',
            guild_id, url or None,
        )


async def set_guild_timezone(guild_id: str, timezone: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''INSERT INTO guild_settings (guild_id, timezone)
               VALUES ($1, $2)
               ON CONFLICT (guild_id) DO UPDATE SET timezone = EXCLUDED.timezone''',
            guild_id, timezone,
        )


async def get_guild_channels(guild_id: str) -> dict:
    """`{kind: channel id or None}` for every kind in `CHANNEL_KINDS`."""
    columns = ', '.join(column for column, _ in CHANNEL_KINDS.values())
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            f'SELECT {columns} FROM guild_settings WHERE guild_id = $1', guild_id
        )
    return {
        kind: (row[column] if row else None)
        for kind, (column, _) in CHANNEL_KINDS.items()
    }


async def set_guild_channels(guild_id: str, channels: dict):
    """Write the channel choices. A kind mapped to None goes back to its name."""
    unknown = set(channels) - set(CHANNEL_KINDS)
    if unknown:
        raise ValueError(f"Unknown channel kind(s): {', '.join(sorted(unknown))}")
    if not channels:
        return
    columns = [CHANNEL_KINDS[kind][0] for kind in channels]
    values = [channels[kind] for kind in channels]
    names = ', '.join(columns)
    placeholders = ', '.join(f'${i}' for i in range(2, len(columns) + 2))
    updates = ', '.join(f'{column} = EXCLUDED.{column}' for column in columns)
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            f'''INSERT INTO guild_settings (guild_id, {names})
                VALUES ($1, {placeholders})
                ON CONFLICT (guild_id) DO UPDATE SET {updates}''',
            guild_id, *values,
        )
