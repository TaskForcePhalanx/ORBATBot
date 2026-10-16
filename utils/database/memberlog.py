"""The join/leave log settings and the invite labels.

Part of `utils/database`, split out of one 3,333-line module. The package's
`__init__` re-exports every name, so `from utils import database` and
`database.anything` work exactly as they did.

The rules these queries serve are in [docs/memberlog.md](../../docs/memberlog.md).
"""
from .core import (get_pool)


_LOG_COLUMNS = (
    'channel_id', 'log_join', 'log_leave', 'log_kick', 'log_ban', 'log_unban',
    'track_invites', 'welcome_channel_id', 'welcome_message', 'welcome_dm',
)


async def get_log_settings(guild_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow('SELECT * FROM log_settings WHERE guild_id = $1', guild_id)


async def save_log_settings(guild_id: str, values: dict):
    columns = [name for name in _LOG_COLUMNS if name in values]
    if not columns:
        return
    updates = ', '.join(f'{name} = EXCLUDED.{name}' for name in columns)
    placeholders = ', '.join(f'${i}' for i in range(2, 2 + len(columns)))
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            f'''INSERT INTO log_settings (guild_id, {', '.join(columns)})
                VALUES ($1, {placeholders})
                ON CONFLICT (guild_id) DO UPDATE SET
                    {updates}, updated_at = CURRENT_TIMESTAMP''',
            guild_id, *[values[name] for name in columns],
        )


async def get_invite_labels(guild_id: str) -> dict:
    """{invite code: label} for one guild."""
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            'SELECT code, label FROM invite_labels WHERE guild_id = $1', guild_id
        )
        return {row['code']: row['label'] for row in rows}


async def save_invite_labels(guild_id: str, labels: dict, remove: list = None):
    """Store the labels that were filled in and drop the ones that were emptied."""
    pool = await get_pool()
    async with pool.acquire() as db:
        async with db.transaction():
            for code, label in labels.items():
                await db.execute(
                    '''INSERT INTO invite_labels (guild_id, code, label)
                       VALUES ($1, $2, $3)
                       ON CONFLICT (guild_id, code) DO UPDATE SET
                           label = EXCLUDED.label,
                           updated_at = CURRENT_TIMESTAMP''',
                    guild_id, code, label,
                )
            if remove:
                await db.execute(
                    'DELETE FROM invite_labels WHERE guild_id = $1 AND code = ANY($2::text[])',
                    guild_id, list(remove),
                )
