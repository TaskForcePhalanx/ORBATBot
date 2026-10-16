"""The self-assignable game roles and their panel.

Part of `utils/database`, split out of one 3,333-line module. The package's
`__init__` re-exports every name, so `from utils import database` and
`database.anything` work exactly as they did.

The rules these queries serve are in [docs/game-roles.md](../../docs/game-roles.md).
"""
from .core import (get_pool)


async def add_game_role(guild_id: str, role_id: str, name: str,
                        emoji: str = None, description: str = None):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''INSERT INTO game_roles (guild_id, role_id, name, emoji, description)
               VALUES ($1, $2, $3, $4, $5)
               ON CONFLICT (guild_id, role_id) DO UPDATE SET
                   name = EXCLUDED.name,
                   emoji = EXCLUDED.emoji,
                   description = EXCLUDED.description''',
            guild_id, role_id, name, emoji, description,
        )


async def remove_game_role(guild_id: str, role_id: str) -> bool:
    pool = await get_pool()
    async with pool.acquire() as db:
        result = await db.execute(
            'DELETE FROM game_roles WHERE guild_id = $1 AND role_id = $2',
            guild_id, role_id,
        )
        return int(result.split()[-1]) > 0


async def get_game_roles(guild_id: str) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            'SELECT * FROM game_roles WHERE guild_id = $1 ORDER BY name',
            guild_id,
        )


async def save_game_role_panel(guild_id: str, channel_id: str, message_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''INSERT INTO game_role_panels (guild_id, channel_id, message_id)
               VALUES ($1, $2, $3)
               ON CONFLICT (guild_id) DO UPDATE SET
                   channel_id = EXCLUDED.channel_id,
                   message_id = EXCLUDED.message_id,
                   updated_at = CURRENT_TIMESTAMP''',
            guild_id, channel_id, message_id,
        )


async def get_game_role_panel(guild_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow(
            'SELECT channel_id, message_id FROM game_role_panels WHERE guild_id = $1',
            guild_id,
        )
