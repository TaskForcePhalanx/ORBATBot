"""Launcher presets posted to a channel, and which one each list is on.

Part of `utils/database`. The rules these serve are in
[docs/modlists.md](../../docs/modlists.md).

A version is never edited. The current one per list is the row flagged
`is_current`, and a partial unique index allows only one, so two presets
arriving at once cannot both be current.
"""
import json
from .core import (get_pool)


def _decode(row):
    """A row with its two JSON columns as lists (asyncpg returns jsonb as text)."""
    if row is None:
        return None
    out = dict(row)
    for column, empty in (('mods', []), ('dlcs', [])):
        value = out.get(column)
        out[column] = json.loads(value) if isinstance(value, str) else (value or empty)
    return out


async def get_modlist_settings(guild_id: str) -> dict:
    """`{'channel_id', 'keywords'}` — empty values for a guild that never set any."""
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            'SELECT channel_id, keywords FROM modlist_settings WHERE guild_id = $1',
            guild_id)
    if row is None:
        return {'channel_id': None, 'keywords': {}}
    keywords = row['keywords']
    if isinstance(keywords, str):
        keywords = json.loads(keywords)
    return {'channel_id': row['channel_id'], 'keywords': keywords or {}}


async def save_modlist_settings(guild_id: str, channel_id, keywords: dict):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''INSERT INTO modlist_settings (guild_id, channel_id, keywords)
               VALUES ($1, $2, $3::jsonb)
               ON CONFLICT (guild_id) DO UPDATE
                  SET channel_id = EXCLUDED.channel_id,
                      keywords = EXCLUDED.keywords,
                      updated_at = CURRENT_TIMESTAMP''',
            guild_id, channel_id, json.dumps(keywords or {}))


async def get_modlist_channels():
    """Every guild that has a channel to watch: `[(guild_id, channel_id), …]`."""
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            'SELECT guild_id, channel_id FROM modlist_settings '
            'WHERE channel_id IS NOT NULL')
    return [(r['guild_id'], r['channel_id']) for r in rows]


async def modlist_source_seen(guild_id: str, source_ref: str) -> bool:
    pool = await get_pool()
    async with pool.acquire() as db:
        return bool(await db.fetchval(
            'SELECT 1 FROM modlists WHERE guild_id = $1 AND source_ref = $2',
            guild_id, source_ref))


async def add_modlist(guild_id: str, slot: str, *, name: str, mods: list,
                      dlcs: list, summary: str = None, source_ref: str = None,
                      channel_id: str = None, message_id: str = None,
                      posted_by: str = None, posted_by_name: str = None,
                      make_current: bool = True):
    """Store a version and, normally, make it the list's current one.

    Returns its id, or None when this message was already stored — the unique
    index on `source_ref` is what makes reading a message twice harmless, and
    it is checked here rather than before so two readers racing cannot both win.
    """
    pool = await get_pool()
    async with pool.acquire() as db:
        async with db.transaction():
            if make_current:
                await db.execute(
                    'UPDATE modlists SET is_current = FALSE '
                    'WHERE guild_id = $1 AND slot = $2 AND is_current',
                    guild_id, slot)
            return await db.fetchval(
                '''INSERT INTO modlists
                       (guild_id, slot, name, mods, dlcs, summary, source_ref,
                        channel_id, message_id, posted_by, posted_by_name, is_current)
                   VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6, $7, $8, $9, $10, $11, $12)
                   ON CONFLICT (guild_id, source_ref) WHERE source_ref IS NOT NULL
                   DO NOTHING
                   RETURNING id''',
                guild_id, slot, name, json.dumps(mods), json.dumps(dlcs), summary,
                source_ref, channel_id, message_id, posted_by, posted_by_name,
                make_current)


async def get_modlist(guild_id: str, modlist_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        return _decode(await db.fetchrow(
            'SELECT * FROM modlists WHERE guild_id = $1 AND id = $2',
            guild_id, modlist_id))


async def get_current_modlist(guild_id: str, slot: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        return _decode(await db.fetchrow(
            'SELECT * FROM modlists WHERE guild_id = $1 AND slot = $2 AND is_current',
            guild_id, slot))


async def get_current_modlists(guild_id: str) -> dict:
    """`{slot: version}` for every list that has one."""
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            'SELECT * FROM modlists WHERE guild_id = $1 AND is_current', guild_id)
    return {r['slot']: _decode(r) for r in rows}


async def list_modlist_versions(guild_id: str, slot: str = None, limit: int = 50):
    """Newest first. The mods are left out: a history page needs a count, not a list."""
    pool = await get_pool()
    async with pool.acquire() as db:
        return [dict(r) for r in await db.fetch(
            '''SELECT id, slot, name, summary, posted_by_name, is_current,
                      created_at, channel_id, message_id,
                      jsonb_array_length(mods) AS mod_count
                 FROM modlists
                WHERE guild_id = $1 AND ($2::text IS NULL OR slot = $2)
                ORDER BY id DESC LIMIT $3''',
            guild_id, slot, limit)]


async def set_current_modlist(guild_id: str, modlist_id: int) -> bool:
    """Put an earlier version back. False if it isn't this guild's."""
    pool = await get_pool()
    async with pool.acquire() as db:
        async with db.transaction():
            slot = await db.fetchval(
                'SELECT slot FROM modlists WHERE guild_id = $1 AND id = $2',
                guild_id, modlist_id)
            if slot is None:
                return False
            await db.execute(
                'UPDATE modlists SET is_current = FALSE '
                'WHERE guild_id = $1 AND slot = $2 AND is_current', guild_id, slot)
            await db.execute('UPDATE modlists SET is_current = TRUE WHERE id = $1',
                             modlist_id)
            return True


async def delete_modlist(guild_id: str, modlist_id: int) -> bool:
    """Delete a version that isn't current. Events that carried it lose the link."""
    pool = await get_pool()
    async with pool.acquire() as db:
        result = await db.execute(
            'DELETE FROM modlists WHERE guild_id = $1 AND id = $2 AND NOT is_current',
            guild_id, modlist_id)
    return result.endswith(' 1')


async def set_event_modlist(event_id: int, modlist_id):
    """Attach a version to an event, or None to detach.

    Its own function rather than a field of `update_event()`, which uses
    COALESCE and therefore cannot write NULL.
    """
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute('UPDATE events SET modlist_id = $2 WHERE id = $1',
                         event_id, modlist_id)
