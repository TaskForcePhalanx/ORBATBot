"""Rich messages built in the browser, and their fields.

Part of `utils/database`, split out of one 3,333-line module. The package's
`__init__` re-exports every name, so `from utils import database` and
`database.anything` work exactly as they did.

The rules these queries serve are in [docs/embeds.md](../../docs/embeds.md).

Every column the builder can set is written outright rather than through COALESCE: the form always submits the whole embed, and emptying a field has to be able to clear it.
"""
from .core import (get_pool)


_EMBED_COLUMNS = (
    'name', 'channel_id', 'content', 'title', 'description', 'url', 'color',
    'author_name', 'author_icon_url', 'thumbnail_url', 'image_url',
    'footer_text', 'footer_icon_url', 'show_timestamp',
)


async def create_embed(guild_id: str, created_by: str, created_by_name: str,
                       values: dict) -> int:
    columns = [name for name in _EMBED_COLUMNS if name in values]
    placeholders = ', '.join(f'${i}' for i in range(4, 4 + len(columns)))
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            f'''INSERT INTO embeds (guild_id, created_by, created_by_name, {', '.join(columns)})
                VALUES ($1, $2, $3, {placeholders})
                RETURNING id''',
            guild_id, created_by, created_by_name, *[values[name] for name in columns],
        )
        return row['id']


async def update_embed(embed_id: int, values: dict):
    columns = [name for name in _EMBED_COLUMNS if name in values]
    if not columns:
        return
    assignments = ', '.join(f'{name} = ${i}' for i, name in enumerate(columns, start=2))
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            f'UPDATE embeds SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = $1',
            embed_id, *[values[name] for name in columns],
        )


async def get_embed(embed_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow('SELECT * FROM embeds WHERE id = $1', embed_id)


async def get_guild_embeds(guild_id: str) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            'SELECT * FROM embeds WHERE guild_id = $1 ORDER BY updated_at DESC', guild_id
        )


async def save_embed_message(embed_id: int, channel_id: str, message_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''UPDATE embeds SET channel_id = $2, message_id = $3,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = $1''',
            embed_id, channel_id, message_id,
        )


async def clear_embed_message(embed_id: int):
    """Forget where the embed was posted — used when its message is gone, so the
    next send posts a new one instead of failing to edit a deleted message."""
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            'UPDATE embeds SET message_id = NULL WHERE id = $1', embed_id
        )


async def delete_embed(embed_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as db:
        result = await db.execute('DELETE FROM embeds WHERE id = $1', embed_id)
        return int(result.split()[-1]) > 0


async def get_embed_fields(embed_id: int) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            'SELECT * FROM embed_fields WHERE embed_id = $1 ORDER BY sort_order', embed_id
        )


async def set_embed_fields(embed_id: int, fields: list):
    """Replace an embed's fields. An empty list removes them all."""
    pool = await get_pool()
    async with pool.acquire() as db:
        async with db.transaction():
            await db.execute('DELETE FROM embed_fields WHERE embed_id = $1', embed_id)
            for order, item in enumerate(fields):
                await db.execute(
                    '''INSERT INTO embed_fields (embed_id, name, value, inline, sort_order)
                       VALUES ($1, $2, $3, $4, $5)''',
                    embed_id, item['name'], item['value'],
                    int(item.get('inline', 0)), order,
                )
