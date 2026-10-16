"""Tactical maps, the terrains they are drawn on, their tiles and their place names.

Part of `utils/database`, split out of one 3,333-line module. The package's
`__init__` re-exports every name, so `from utils import database` and
`database.anything` work exactly as they did.

The rules these queries serve are in [docs/tacmap.md](../../docs/tacmap.md).
"""
from .core import (get_pool)


async def get_guild_tac_maps(guild_id: str) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            'SELECT * FROM tac_maps WHERE guild_id = $1 ORDER BY updated_at DESC',
            guild_id,
        )


async def get_tac_map(map_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow('SELECT * FROM tac_maps WHERE id = $1', map_id)


async def get_tac_map_by_token(token: str):
    """The map a share link names, whatever guild it belongs to.

    The token is the whole credential on that route, so an empty one must never
    match the rows that are not shared at all.
    """
    if not token:
        return None
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow(
            "SELECT * FROM tac_maps WHERE share_token = $1 AND share_mode <> 'off'",
            token,
        )


async def create_tac_map(guild_id: str, name: str, description: str, doc: str,
                         created_by: str, created_by_name: str) -> int:
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            '''INSERT INTO tac_maps
               (guild_id, name, description, doc, created_by, created_by_name,
                updated_by_name)
               VALUES ($1, $2, $3, $4::jsonb, $5, $6, $6) RETURNING id''',
            guild_id, name, description, doc, created_by, created_by_name,
        )
        return row['id']


async def save_tac_map_doc(map_id: int, doc: str, updated_by_name: str = None):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''UPDATE tac_maps
                  SET doc = $2::jsonb, updated_by_name = COALESCE($3, updated_by_name),
                      updated_at = CURRENT_TIMESTAMP, doc_rev = doc_rev + 1
                WHERE id = $1''',
            map_id, doc, updated_by_name,
        )


async def save_tac_map_doc_if(map_id: int, doc: str, updated_by_name: str,
                              base_rev: int):
    """Store the document only if nobody has written it since `base_rev` —
    the revision the editor opened on. Returns the new revision, or None when
    somebody else saved first. One statement, so two saves racing each other
    cannot both pass the check."""
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchval(
            '''UPDATE tac_maps
                  SET doc = $2::jsonb, updated_by_name = COALESCE($3, updated_by_name),
                      updated_at = CURRENT_TIMESTAMP, doc_rev = doc_rev + 1
                WHERE id = $1 AND doc_rev = $4
            RETURNING doc_rev''',
            map_id, doc, updated_by_name, base_rev,
        )


async def rename_tac_map(map_id: int, name: str, description: str = None):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''UPDATE tac_maps SET name = $2, description = $3,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = $1''',
            map_id, name, description,
        )


async def set_tac_map_share(map_id: int, token: str = None, mode: str = 'off'):
    """Point the share link at this map, or take it away.

    A new token is a new link and the old one stops working at once — which is
    the only way to un-share a map somebody has already sent on.
    """
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''UPDATE tac_maps SET share_token = $2, share_mode = $3,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = $1''',
            map_id, token, mode,
        )


async def save_tac_map_message(map_id: int, channel_id: str, message_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            'UPDATE tac_maps SET channel_id = $2, message_id = $3 WHERE id = $1',
            map_id, channel_id, message_id,
        )


async def delete_tac_map(map_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as db:
        result = await db.execute('DELETE FROM tac_maps WHERE id = $1', map_id)
        return int(result.split()[-1]) > 0


async def duplicate_tac_map(map_id: int, name: str, created_by: str,
                            created_by_name: str) -> int:
    """A copy of the plan, with neither the share link nor the posted message.

    Both belong to the original: a copy that answered to the same token would
    hand everyone holding that link a different map than the one they were sent.
    """
    source = await get_tac_map(map_id)
    return await create_tac_map(
        source['guild_id'], name, source['description'], source['doc'],
        created_by, created_by_name,
    )


async def get_guild_tac_terrains(guild_id: str) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            'SELECT * FROM tac_terrains WHERE guild_id = $1 ORDER BY name',
            guild_id,
        )


async def get_tac_terrain(terrain_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow('SELECT * FROM tac_terrains WHERE id = $1', terrain_id)


async def get_tac_terrain_tile(terrain_id: int, zoom: int, x: int, y: int):
    """One tile's bytes, or None. Asked for a few hundred times per map view."""
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchval(
            '''SELECT image FROM tac_terrain_tiles
                WHERE terrain_id = $1 AND zoom = $2 AND x = $3 AND y = $4''',
            terrain_id, zoom, x, y,
        )


async def create_tac_terrain(guild_id: str, name: str, world_size: float,
                             max_zoom: int, tiles: list, created_by: str,
                             created_by_name: str, places: str = None) -> int:
    """Store a terrain and all of its tiles, or neither.

    One transaction: a half-written terrain would render as a map with holes in
    it, and nothing about the upload says which tiles are missing.
    """
    pool = await get_pool()
    async with pool.acquire() as db:
        async with db.transaction():
            row = await db.fetchrow(
                '''INSERT INTO tac_terrains
                   (guild_id, name, world_size, max_zoom, tile_count, bytes,
                    created_by, created_by_name, places)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) RETURNING id''',
                guild_id, name, float(world_size), max_zoom, len(tiles),
                sum(len(tile.image) for tile in tiles), created_by, created_by_name,
                places,
            )
            await db.copy_records_to_table(
                'tac_terrain_tiles',
                columns=('terrain_id', 'zoom', 'x', 'y', 'image'),
                records=[(row['id'], tile.zoom, tile.x, tile.y, tile.image)
                         for tile in tiles],
            )
            return row['id']


async def set_tac_terrain_places(terrain_id: int, places: str) -> None:
    """Replace a terrain's place names.

    Wholesale, like the ORBAT net list: nothing hangs off a place name, so
    there is no identity an edit could lose. NULL clears them again.
    """
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            'UPDATE tac_terrains SET places = $2 WHERE id = $1',
            terrain_id, places or None,
        )


async def get_tac_places(guild_id: str, scope: str):
    """The stored place names for one terrain scope, or None."""
    if not scope:
        return None
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow(
            'SELECT * FROM tac_places WHERE guild_id = $1 AND scope = $2',
            str(guild_id), scope,
        )


async def set_tac_places(guild_id: str, scope: str, places: str,
                         label: str = None, source: str = None,
                         updated_by_name: str = None) -> None:
    """Replace one scope's place names, or clear them.

    Wholesale, like the ORBAT net list: nothing hangs off a place name, so
    there is no identity an edit could lose.
    """
    pool = await get_pool()
    async with pool.acquire() as db:
        if not places:
            await db.execute(
                'DELETE FROM tac_places WHERE guild_id = $1 AND scope = $2',
                str(guild_id), scope)
            return
        await db.execute(
            """INSERT INTO tac_places
                   (guild_id, scope, label, places, source, updated_by_name,
                    updated_at)
               VALUES ($1, $2, $3, $4, $5, $6, CURRENT_TIMESTAMP)
               ON CONFLICT (guild_id, scope) DO UPDATE
                   SET label = EXCLUDED.label,
                       places = EXCLUDED.places,
                       source = EXCLUDED.source,
                       updated_by_name = EXCLUDED.updated_by_name,
                       updated_at = CURRENT_TIMESTAMP""",
            str(guild_id), scope, label, places, source, updated_by_name,
        )


async def tac_terrain_usage(terrain_id: int) -> list:
    """The maps drawn on this terrain.

    A map points at a terrain by the address it is served from, which is what
    this matches on — the alternative, a column, would be a second place for the
    same fact to live and to go stale.

    **It reads the document's structure, not its characters.** This was a
    `LIKE '%"/t/7"%'` over the whole column until `doc` became `jsonb`
    (migration 3), which read every map in the table — a leading wildcard can
    use no index — and answered about the text rather than the map: a label
    somebody typed containing `"/t/7"` counted as usage. The answer decides
    whether deleting a terrain is refused, so being wrong in the lenient
    direction leaves every plan on that terrain rendering on an empty sheet.
    `idx_tac_maps_background_url` is the index for exactly this.
    """
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            '''SELECT id, name FROM tac_maps
                WHERE doc -> 'background' ->> 'url' = $1 ORDER BY name''',
            f'/t/{int(terrain_id)}',
        )


async def delete_tac_terrain(terrain_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as db:
        result = await db.execute('DELETE FROM tac_terrains WHERE id = $1', terrain_id)
        return int(result.split()[-1]) > 0


# -- the game's own marker pictures -----------------------------------------

async def set_tac_marker_icons(guild_id: str, icons: dict):
    """Store a guild's marker pictures, replacing any of the same marker."""
    pool = await get_pool()
    async with pool.acquire() as db:
        async with db.transaction():
            for key, image in icons.items():
                await db.execute(
                    '''INSERT INTO tac_marker_icons (guild_id, key, image)
                       VALUES ($1, $2, $3)
                       ON CONFLICT (guild_id, key)
                       DO UPDATE SET image = EXCLUDED.image, updated_at = CURRENT_TIMESTAMP''',
                    guild_id, key, image)


async def tac_marker_icon_keys(guild_id: str) -> dict:
    """{marker key: a version string} for the guild's uploaded pictures."""
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            '''SELECT key, EXTRACT(EPOCH FROM updated_at)::BIGINT AS v
                 FROM tac_marker_icons WHERE guild_id = $1''', guild_id)
    return {row['key']: str(row['v']) for row in rows}


async def get_tac_marker_icon(guild_id: str, key: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchval(
            'SELECT image FROM tac_marker_icons WHERE guild_id = $1 AND key = $2',
            guild_id, key)


async def get_tac_marker_icons(guild_id: str) -> dict:
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            'SELECT key, image FROM tac_marker_icons WHERE guild_id = $1', guild_id)
    return {row['key']: bytes(row['image']) for row in rows}


async def delete_tac_marker_icons(guild_id: str) -> int:
    pool = await get_pool()
    async with pool.acquire() as db:
        result = await db.execute('DELETE FROM tac_marker_icons WHERE guild_id = $1', guild_id)
    return int(result.split()[-1])
