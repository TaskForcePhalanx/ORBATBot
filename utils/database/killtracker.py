"""The Kill Tracker: a recording per row, its campaigns, aliases, units and struck kills.

Part of `utils/database`, split out of one 3,333-line module. The package's
`__init__` re-exports every name, so `from utils import database` and
`database.anything` work exactly as they did.

The rules these queries serve are in [docs/killtracker.md](../../docs/killtracker.md).

One row per OCAP recording. Naive UTC like every stored time here.
"""
import asyncpg
import json as _json
from .core import (get_pool)


async def _link_kt_campaign(db, name: str, user_name: str) -> tuple:
    """(kt id, created) — the Kill Tracker campaign of this name, created when
    there is none. `created` is what lets a later delete take it along."""
    created = await db.fetchval(
        """INSERT INTO kt_campaigns (name, updated_by_name) VALUES ($1, $2)
           ON CONFLICT ((lower(name))) DO NOTHING RETURNING id""", name, user_name)
    if created:
        return created, True
    return await db.fetchval(
        'SELECT id FROM kt_campaigns WHERE lower(name) = lower($1)', name), False


# -- the OCAP recording (milestone 6) ---------------------------------------

async def kt_recordings_between(first: str, last: str) -> list:
    """Recordings played between two dates ('YYYY-MM-DD', inclusive), without
    their rows — what matching a mission looks at."""
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            """SELECT ocap_id, filename, mission, label, name_override, world, tag, played_on,
                      duration, status, rows <> '[]' AS counted
                 FROM kt_missions WHERE played_on BETWEEN $1 AND $2
                ORDER BY played_on, ocap_id""", first, last)
    return [dict(r) for r in rows]


async def kt_recording(ocap_id: int):
    """One recording with its rows parsed, or None."""
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            """SELECT ocap_id, mission, label, name_override, world, tag, played_on, duration,
                      status, rows FROM kt_missions WHERE ocap_id = $1""", ocap_id)
    if row is None:
        return None
    out = dict(row)
    try:
        out['rows'] = _json.loads(row['rows'] or '[]')
    except (TypeError, ValueError):
        out['rows'] = []
    return out


async def kt_known() -> dict:
    """`{ocap_id: row}` for every recording seen, without the (large) rows."""
    pool = await get_pool()
    async with pool.acquire() as db:
        found = await db.fetch(
            '''SELECT ocap_id, filename, rules_version, status, attempts, retry_at
                 FROM kt_missions'''
        )
    return {r['ocap_id']: r for r in found}


async def kt_save_mission(op: dict, *, label: str, rows_json: str, counted_kills: int,
                          rules_version: int, sha256: str, far_json: str = '[]'):
    """Store one counted recording, replacing whatever an earlier count left."""
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''INSERT INTO kt_missions
                   (ocap_id, filename, mission, label, world, tag, played_on, duration,
                    ocap_kills, ocap_player_kills, counted_kills, rows, rules_version,
                    status, error, attempts, retry_at, sha256, imported_at, far_kills)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
                       'ok', NULL, 0, NULL, $14, (NOW() AT TIME ZONE 'UTC'), $15)
               ON CONFLICT (ocap_id) DO UPDATE SET
                   filename = EXCLUDED.filename, mission = EXCLUDED.mission,
                   label = EXCLUDED.label, world = EXCLUDED.world, tag = EXCLUDED.tag,
                   played_on = EXCLUDED.played_on, duration = EXCLUDED.duration,
                   ocap_kills = EXCLUDED.ocap_kills,
                   ocap_player_kills = EXCLUDED.ocap_player_kills,
                   counted_kills = EXCLUDED.counted_kills, rows = EXCLUDED.rows,
                   rules_version = EXCLUDED.rules_version, status = 'ok', error = NULL,
                   attempts = 0, retry_at = NULL, sha256 = EXCLUDED.sha256,
                   imported_at = EXCLUDED.imported_at, far_kills = EXCLUDED.far_kills''',
            op['id'], op['filename'], op['mission'], label, op['world'], op['tag'],
            op['date'], op['duration'], op['kills'], op['player_kills'], counted_kills,
            rows_json, rules_version, sha256, far_json,
        )


async def kt_save_failure(op: dict, *, error: str, retry_at):
    """Remember that a recording could not be read, and when to try it again.

    A recording already counted keeps its rows: a failed *re*-count must not
    blank a mission that was fine.
    """
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''INSERT INTO kt_missions
                   (ocap_id, filename, mission, world, tag, played_on, duration,
                    ocap_kills, ocap_player_kills, rules_version, status, error,
                    attempts, retry_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 0, 'failed', $10, 1, $11)
               ON CONFLICT (ocap_id) DO UPDATE SET
                   error = EXCLUDED.error, attempts = kt_missions.attempts + 1,
                   retry_at = EXCLUDED.retry_at,
                   status = CASE WHEN kt_missions.rows <> '[]'
                                 THEN kt_missions.status ELSE 'failed' END''',
            op['id'], op['filename'], op['mission'], op['world'], op['tag'],
            op['date'], op['duration'], op['kills'], op['player_kills'], error, retry_at,
        )


async def kt_missions() -> list:
    """Every recording's bookkeeping, newest first, without the rows."""
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            '''SELECT ocap_id, filename, mission, label, name_override, world, tag,
                      played_on, duration, ocap_kills, ocap_player_kills,
                      counted_kills, rules_version, status, error, attempts,
                      retry_at, imported_at
                 FROM kt_missions ORDER BY ocap_id DESC'''
        )


async def kt_far_kills() -> list:
    """`(ocap_id, label, name_override, far_kills JSON)` for every counted mission."""
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            "SELECT ocap_id, label, name_override, far_kills FROM kt_missions "
            "WHERE rows <> '[]' ORDER BY ocap_id"
        )


async def kt_exclusions(ocap_id: int = None) -> dict:
    """`{ocap_id: {kill_key, …}}` — the kills struck from the distance statistics,
    for one recording or for all."""
    pool = await get_pool()
    async with pool.acquire() as db:
        if ocap_id is None:
            found = await db.fetch('SELECT ocap_id, kill_key FROM kt_kill_exclusions')
        else:
            found = await db.fetch(
                'SELECT ocap_id, kill_key FROM kt_kill_exclusions WHERE ocap_id = $1', ocap_id)
    out = {}
    for r in found:
        out.setdefault(r['ocap_id'], set()).add(r['kill_key'])
    return out


async def kt_set_exclusions(changes, by_name: str) -> int:
    """Strike or restore kills. `changes` is `[(ocap_id, kill_key, struck, info)]`.

    Every mission touched is marked for a re-count (`rules_version = 0`): the
    stored rows were counted with the old list, and the next pass over the
    recording applies the new one. Returns how many missions that is.
    """
    touched = set()
    pool = await get_pool()
    async with pool.acquire() as db:
        async with db.transaction():
            for ocap_id, key, struck, info in changes:
                if struck:
                    await db.execute(
                        '''INSERT INTO kt_kill_exclusions
                               (ocap_id, kill_key, killer, victim, weapon, distance, struck_by_name)
                           SELECT $1, $2, $3, $4, $5, $6, $7
                            WHERE EXISTS (SELECT 1 FROM kt_missions WHERE ocap_id = $1)
                           ON CONFLICT (ocap_id, kill_key) DO NOTHING''',
                        ocap_id, key, info.get('killer'), info.get('victim'),
                        info.get('w'), info.get('d'), by_name)
                else:
                    await db.execute(
                        'DELETE FROM kt_kill_exclusions WHERE ocap_id = $1 AND kill_key = $2',
                        ocap_id, key)
                touched.add(ocap_id)
            for ocap_id in touched:
                await db.execute('UPDATE kt_missions SET rules_version = 0 WHERE ocap_id = $1', ocap_id)
    return len(touched)


async def kt_rows() -> list:
    """`(rows JSON, name override)` for every counted mission, oldest recording first."""
    pool = await get_pool()
    async with pool.acquire() as db:
        found = await db.fetch(
            "SELECT rows, name_override FROM kt_missions WHERE rows <> '[]' ORDER BY ocap_id"
        )
    return [(r['rows'], r['name_override']) for r in found]


async def kt_generation() -> str:
    """A string that changes whenever anything the public answers show does — the
    cache key. Counts, names and campaigns all feed it, so an admin's edit is
    visible as soon as the cache window passes, not only after the next import."""
    pool = await get_pool()
    async with pool.acquire() as db:
        r = await db.fetchrow(
            """SELECT COUNT(*) AS n, MAX(imported_at) AS last, MIN(rules_version) AS rules,
                      md5(COALESCE(string_agg(ocap_id || ':' || COALESCE(name_override, ''),
                                              ',' ORDER BY ocap_id), '')) AS names
                 FROM kt_missions WHERE rows <> '[]'"""
        )
        c = await db.fetchrow(
            """SELECT COUNT(*) AS n, MAX(updated_at) AS last FROM kt_campaigns"""
        )
        m = await db.fetchrow("SELECT COUNT(*) AS n FROM kt_campaign_missions")
        a = await db.fetchval(
            """SELECT md5(COALESCE((SELECT string_agg(alias || '>' || canonical, ',' ORDER BY alias)
                                        FROM kt_aliases), '')
                          || '|' ||
                          COALESCE((SELECT string_agg(player || '=' || unit, ',' ORDER BY player)
                                      FROM kt_unit_overrides), ''))""")
    return f"{r['n']}:{r['last']}:{r['rules']}:{r['names']}:{c['n']}:{c['last']}:{m['n']}:{a}"


async def kt_set_name_overrides(pairs) -> int:
    """Set many names at once — `(ocap_id, name)` pairs, in one transaction."""
    pool = await get_pool()
    async with pool.acquire() as db:
        async with db.transaction():
            n = 0
            for ocap_id, name in pairs:
                result = await db.execute(
                    'UPDATE kt_missions SET name_override = $2 WHERE ocap_id = $1',
                    ocap_id, name or None,
                )
                n += int(result.split()[-1])
    return n


def _campaign(record, ids) -> dict:
    import json as _json

    return {
        'id': record['id'], 'name': record['name'],
        'patterns': _json.loads(record['patterns'] or '[]'),
        'extra': _json.loads(record['extra_missions'] or '[]'),
        'ocap_ids': sorted(ids),
        'updated_by_name': record['updated_by_name'], 'updated_at': record['updated_at'],
    }


async def kt_campaigns() -> list:
    """Every campaign with its patterns, leftover labels and recording ids."""
    pool = await get_pool()
    async with pool.acquire() as db:
        campaigns = await db.fetch('SELECT * FROM kt_campaigns ORDER BY lower(name)')
        links = await db.fetch('SELECT campaign_id, ocap_id FROM kt_campaign_missions')
    by_campaign = {}
    for link in links:
        by_campaign.setdefault(link['campaign_id'], []).append(link['ocap_id'])
    return [_campaign(c, by_campaign.get(c['id'], ())) for c in campaigns]


async def kt_campaign(campaign_id: int):
    for campaign in await kt_campaigns():
        if campaign['id'] == campaign_id:
            return campaign
    return None


async def kt_save_campaign(campaign_id, *, name: str, patterns: list, extra: list,
                           ocap_ids: list, updated_by_name: str = None) -> int:
    """Create (`campaign_id` None) or replace a campaign. Returns its id.

    Raises `ValueError` when another campaign already has the name. Membership is
    replaced wholesale, in one transaction, like every other list here.
    """
    import json as _json
    import asyncpg

    pool = await get_pool()
    async with pool.acquire() as db:
        try:
            async with db.transaction():
                if campaign_id is None:
                    campaign_id = await db.fetchval(
                        '''INSERT INTO kt_campaigns (name, patterns, extra_missions, updated_by_name)
                           VALUES ($1, $2, $3, $4) RETURNING id''',
                        name, _json.dumps(patterns), _json.dumps(extra), updated_by_name)
                else:
                    await db.execute(
                        '''UPDATE kt_campaigns SET name = $2, patterns = $3, extra_missions = $4,
                                  updated_by_name = $5, updated_at = (NOW() AT TIME ZONE 'UTC')
                            WHERE id = $1''',
                        campaign_id, name, _json.dumps(patterns), _json.dumps(extra), updated_by_name)
                    await db.execute('DELETE FROM kt_campaign_missions WHERE campaign_id = $1', campaign_id)
                for ocap_id in sorted(set(ocap_ids)):
                    await db.execute(
                        '''INSERT INTO kt_campaign_missions (campaign_id, ocap_id)
                           SELECT $1, ocap_id FROM kt_missions WHERE ocap_id = $2''',
                        campaign_id, ocap_id)
        except asyncpg.UniqueViolationError:
            raise ValueError(f'There is already a campaign called "{name}".')
    return campaign_id


async def kt_delete_campaign(campaign_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as db:
        result = await db.execute('DELETE FROM kt_campaigns WHERE id = $1', campaign_id)
    return int(result.split()[-1]) > 0


async def kt_aliases() -> dict:
    """`{name as recorded → name shown}`."""
    pool = await get_pool()
    async with pool.acquire() as db:
        found = await db.fetch('SELECT alias, canonical FROM kt_aliases')
    return {r['alias']: r['canonical'] for r in found}


async def kt_set_aliases(aliases: dict) -> None:
    """Replace the alias map. The caller works on the whole map (see
    `killtracker.link_names()`), so this writes it back as it is."""
    pool = await get_pool()
    async with pool.acquire() as db:
        async with db.transaction():
            await db.execute('DELETE FROM kt_aliases')
            for alias, canonical in aliases.items():
                await db.execute(
                    'INSERT INTO kt_aliases (alias, canonical) VALUES ($1, $2)', alias, canonical)


async def kt_unit_overrides() -> dict:
    """`{player → unit}`; '' is Unknown."""
    pool = await get_pool()
    async with pool.acquire() as db:
        found = await db.fetch('SELECT player, unit FROM kt_unit_overrides')
    return {r['player']: r['unit'] for r in found}


async def kt_set_unit_overrides(changes: dict) -> None:
    """Apply `{player → unit}`; a `None` unit removes the override (automatic)."""
    pool = await get_pool()
    async with pool.acquire() as db:
        async with db.transaction():
            for player, unit in changes.items():
                if unit is None:
                    await db.execute('DELETE FROM kt_unit_overrides WHERE player = $1', player)
                else:
                    await db.execute(
                        """INSERT INTO kt_unit_overrides (player, unit) VALUES ($1, $2)
                           ON CONFLICT (player) DO UPDATE SET unit = EXCLUDED.unit""",
                        player, unit)
