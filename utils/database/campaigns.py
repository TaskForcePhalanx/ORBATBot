"""The campaign planner: campaigns, AOs, missions, phases, objectives, drops and images.

Part of `utils/database`, split out of one 3,333-line module. The package's
`__init__` re-exports every name, so `from utils import database` and
`database.anything` work exactly as they did.

The rules these queries serve are in [docs/campaigns.md](../../docs/campaigns.md).

A mission's wizard fields are one JSON document in `cp_missions.fields`, merged in SQL (`fields::jsonb || patch`) so two people saving different fields at the same moment both keep their change. Phases are rows, because milestone 5 hangs a map layer off each one.
"""
import asyncpg
import json as _json
from .core import (get_pool)
from .killtracker import (_link_kt_campaign)


_CP_SETTINGS_COLUMNS = (
    'unit_role_id', 'drop_channel_id', 'team_channel_id', 'drop_weekday',
    'drop_time', 'orbat_opens', 'survey_url', 'orbat_access', 'orbat_role_ids',
    'auto_complete',
)


async def get_cp_settings(guild_id: str):
    """The stored row, or None. Pass it through
    `utils.campaign.settings_with_defaults()` before reading it."""
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow('SELECT * FROM cp_settings WHERE guild_id = $1', guild_id)


async def save_cp_settings(guild_id: str, values: dict):
    columns = [name for name in _CP_SETTINGS_COLUMNS if name in values]
    if not columns:
        return
    updates = ', '.join(f'{name} = EXCLUDED.{name}' for name in columns)
    placeholders = ', '.join(f'${i}' for i in range(2, 2 + len(columns)))
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            f'''INSERT INTO cp_settings (guild_id, {', '.join(columns)})
                VALUES ($1, {placeholders})
                ON CONFLICT (guild_id) DO UPDATE SET
                    {updates}, updated_at = CURRENT_TIMESTAMP''',
            guild_id, *[values[name] for name in columns],
        )


_CP_CAMPAIGN_COLUMNS = ('name', 'era', 'theatre', 'summary', 'planned_missions', 'status')


async def list_cp_campaigns(guild_id: str) -> list:
    """Every campaign of a guild, each with its missions in order."""
    pool = await get_pool()
    async with pool.acquire() as db:
        campaigns = await db.fetch(
            """SELECT * FROM cp_campaigns WHERE guild_id = $1
               ORDER BY status = 'archived', updated_at DESC""", guild_id)
        missions = await db.fetch(
            """SELECT m.id, m.campaign_id, m.position, m.name, m.op_date, m.fields
                 FROM cp_missions m JOIN cp_campaigns c ON c.id = m.campaign_id
                WHERE c.guild_id = $1 ORDER BY m.position, m.id""", guild_id)
    by_campaign: dict = {}
    for row in missions:
        by_campaign.setdefault(row['campaign_id'], []).append(_mission_dict(row))
    return [{**dict(c), 'missions': by_campaign.get(c['id'], [])} for c in campaigns]


def _mission_dict(row) -> dict:
    out = dict(row)
    try:
        out['fields'] = _json.loads(row['fields'] or '{}')
    except (TypeError, ValueError):
        out['fields'] = {}
    if not isinstance(out['fields'], dict):
        out['fields'] = {}
    return out


async def get_cp_campaign(campaign_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            """SELECT c.*, k.name AS kt_name FROM cp_campaigns c
                 LEFT JOIN kt_campaigns k ON k.id = c.kt_campaign_id
                WHERE c.id = $1""", campaign_id)
    return dict(row) if row else None


async def create_cp_campaign(guild_id: str, values: dict, user_id: str, user_name: str,
                             is_test: bool = False) -> int:
    """A new campaign. A real one is linked to the Kill Tracker campaign of the
    same name — found when one exists (names are unique regardless of case),
    created when it does not. A test campaign gets none, so nothing about it
    reaches the public dashboard. Raises ValueError when the guild already has
    a planner campaign of that name."""
    pool = await get_pool()
    async with pool.acquire() as db:
        async with db.transaction():
            kt_id, kt_created = (None, False) if is_test else \
                await _link_kt_campaign(db, values['name'], user_name)
            try:
                return await db.fetchval(
                    """INSERT INTO cp_campaigns
                         (guild_id, name, era, theatre, summary, planned_missions, status,
                          is_test, kt_campaign_id, kt_created, created_by, updated_by_name)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12) RETURNING id""",
                    guild_id, values['name'], values['era'], values['theatre'],
                    values['summary'], values['planned_missions'], values['status'],
                    is_test, kt_id, kt_created, user_id, user_name)
            except asyncpg.UniqueViolationError:
                raise ValueError(f"There's already a campaign called {values['name']}.")


async def make_cp_campaign_real(campaign_id: int, user_name: str) -> str:
    """Turn a test campaign into a real one: it gets its Kill Tracker campaign
    now, exactly as if it had been created real. Returns that campaign's name."""
    pool = await get_pool()
    async with pool.acquire() as db:
        async with db.transaction():
            name = await db.fetchval('SELECT name FROM cp_campaigns WHERE id = $1 FOR UPDATE',
                                     campaign_id)
            kt_id, kt_created = await _link_kt_campaign(db, name, user_name)
            await db.execute(
                """UPDATE cp_campaigns SET is_test = FALSE, kt_campaign_id = $2, kt_created = $3,
                       updated_by_name = $4, updated_at = (NOW() AT TIME ZONE 'UTC')
                    WHERE id = $1""", campaign_id, kt_id, kt_created, user_name)
            # What it posted as a test went to the team channel; as a real
            # campaign every drop is still to be approved and posted publicly.
            await db.execute(
                """DELETE FROM cp_drops WHERE mission_id IN
                       (SELECT id FROM cp_missions WHERE campaign_id = $1)""", campaign_id)
            return name


async def update_cp_campaign(campaign_id: int, values: dict, user_name: str):
    columns = [c for c in _CP_CAMPAIGN_COLUMNS if c in values]
    sets = ', '.join(f'{c} = ${i}' for i, c in enumerate(columns, 3))
    pool = await get_pool()
    async with pool.acquire() as db:
        try:
            async with db.transaction():
                # Reopening a closed campaign is a person deciding it stays
                # open: the bot must not close it again (`auto_completed_at`)
                # until a new mission is added.
                reopen = ''
                if values.get('status') == 'active':
                    reopen = (", auto_completed_at = CASE WHEN status <> 'active' "
                              "THEN COALESCE(auto_completed_at, NOW() AT TIME ZONE 'UTC') "
                              "ELSE auto_completed_at END")
                await db.execute(
                    f"""UPDATE cp_campaigns SET {sets}, updated_by_name = $2,
                            updated_at = (NOW() AT TIME ZONE 'UTC'){reopen} WHERE id = $1""",
                    campaign_id, user_name, *[values[c] for c in columns])
                # A Kill Tracker campaign the planner made follows a rename, so
                # the dashboard does not keep the old name. One linked by hand
                # is an admin's and keeps whatever they called it.
                if values.get('name'):
                    await db.execute(
                        """UPDATE kt_campaigns k SET name = $2, updated_at = (NOW() AT TIME ZONE 'UTC')
                             FROM cp_campaigns c
                            WHERE c.id = $1 AND c.kt_created AND k.id = c.kt_campaign_id
                              AND k.name <> $2
                              AND NOT EXISTS (SELECT 1 FROM kt_campaigns o
                                               WHERE lower(o.name) = lower($2) AND o.id <> k.id)""",
                        campaign_id, values['name'])
        except asyncpg.UniqueViolationError:
            raise ValueError(f"There's already a campaign called {values.get('name')}.")


async def delete_cp_campaign(campaign_id: int) -> dict:
    """Deletes the campaign with every AO, mission, phase, objective, drop and
    image in it (they cascade), and the campaign and AO maps the planner made
    for it. Returns what happened to its Kill Tracker
    campaign: `{'kt_removed': name}` when that went too, `{'kt_kept': name}`
    when it stayed, or `{}` when there was none.

    The Kill Tracker campaign goes only when it is provably a leftover: the
    planner created it (`kt_created`), no recording has been put in it, it has
    no name patterns or imported missions, and no other planner campaign points
    at it. One an admin made, or one holding stats, is always kept — those
    numbers are public.
    """
    pool = await get_pool()
    async with pool.acquire() as db:
        async with db.transaction():
            row = await db.fetchrow(
                """SELECT c.kt_campaign_id, c.kt_created, k.name AS kt_name,
                          (k.patterns = '[]' AND k.extra_missions = '[]'
                           AND NOT EXISTS (SELECT 1 FROM kt_campaign_missions m
                                            WHERE m.campaign_id = k.id)
                           AND NOT EXISTS (SELECT 1 FROM cp_campaigns o
                                            WHERE o.kt_campaign_id = k.id AND o.id <> c.id))
                            AS kt_empty
                     FROM cp_campaigns c LEFT JOIN kt_campaigns k ON k.id = c.kt_campaign_id
                    WHERE c.id = $1 FOR UPDATE OF c""", campaign_id)
            # The campaign map and the AO maps were made by the planner for
            # this campaign and hold its plan, so they go with it.
            maps = [r['map_id'] for r in await db.fetch(
                """SELECT map_id FROM cp_campaigns WHERE id = $1 AND map_id IS NOT NULL
                   UNION SELECT map_id FROM cp_aos WHERE campaign_id = $1
                                                    AND map_id IS NOT NULL""", campaign_id)]
            await db.execute('DELETE FROM cp_campaigns WHERE id = $1', campaign_id)
            if maps:
                await db.execute('DELETE FROM tac_maps WHERE id = ANY($1::int[])', maps)
            if not row or not row['kt_name']:
                return {}
            if row['kt_created'] and row['kt_empty']:
                await db.execute('DELETE FROM kt_campaigns WHERE id = $1', row['kt_campaign_id'])
                return {'kt_removed': row['kt_name']}
            return {'kt_kept': row['kt_name']}


async def test_cp_campaign_ids(guild_id: str) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            'SELECT id FROM cp_campaigns WHERE guild_id = $1 AND is_test ORDER BY id', guild_id)
    return [r['id'] for r in rows]


async def touch_cp_campaign(db, campaign_id: int, user_name: str):
    await db.execute(
        """UPDATE cp_campaigns SET updated_by_name = $2,
               updated_at = (NOW() AT TIME ZONE 'UTC') WHERE id = $1""",
        campaign_id, user_name)


# -- AOs --------------------------------------------------------------------

async def list_cp_aos(campaign_id: int) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            'SELECT * FROM cp_aos WHERE campaign_id = $1 ORDER BY position, id', campaign_id)
    return [dict(r) for r in rows]


async def cp_campaign_by_map(map_id: int):
    """The planner campaign whose campaign map this is, or None."""
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow('SELECT * FROM cp_campaigns WHERE map_id = $1', map_id)
    return dict(row) if row else None


async def add_cp_ao(campaign_id: int, name: str) -> int:
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchval(
            """INSERT INTO cp_aos (campaign_id, name, position)
               VALUES ($1, $2, COALESCE((SELECT MAX(position) + 1 FROM cp_aos
                                          WHERE campaign_id = $1), 0))
               RETURNING id""", campaign_id, name)


async def rename_cp_ao(ao_id: int, campaign_id: int, name: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute('UPDATE cp_aos SET name = $3 WHERE id = $1 AND campaign_id = $2',
                         ao_id, campaign_id, name)


async def delete_cp_ao(ao_id: int, campaign_id: int):
    """Phases fought in it keep their place and lose the AO (ON DELETE SET NULL).
    Its detailed map was made by the planner for it and goes too."""
    pool = await get_pool()
    async with pool.acquire() as db:
        async with db.transaction():
            map_id = await db.fetchval(
                'DELETE FROM cp_aos WHERE id = $1 AND campaign_id = $2 RETURNING map_id',
                ao_id, campaign_id)
            if map_id:
                await db.execute('DELETE FROM tac_maps WHERE id = $1', map_id)


# -- missions ---------------------------------------------------------------

async def list_cp_missions(campaign_id: int) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            'SELECT * FROM cp_missions WHERE campaign_id = $1 ORDER BY position, id',
            campaign_id)
    return [_mission_dict(r) for r in rows]


async def get_cp_mission(mission_id: int):
    """One mission with its campaign's guild, or None."""
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            """SELECT m.*, c.guild_id, c.name AS campaign_name
                 FROM cp_missions m JOIN cp_campaigns c ON c.id = m.campaign_id
                WHERE m.id = $1""", mission_id)
    return _mission_dict(row) if row else None


async def create_cp_mission(campaign_id: int, name: str, op_date, fields: dict,
                            user_name: str) -> int:
    pool = await get_pool()
    async with pool.acquire() as db:
        async with db.transaction():
            mission_id = await db.fetchval(
                """INSERT INTO cp_missions (campaign_id, position, name, op_date, fields,
                                            updated_by_name)
                   VALUES ($1, COALESCE((SELECT MAX(position) + 1 FROM cp_missions
                                          WHERE campaign_id = $1), 0),
                           $2, $3, $4, $5)
                   RETURNING id""",
                campaign_id, name, op_date, _json.dumps(fields), user_name)
            # A new mission is more to play: the campaign may close itself
            # again once that one is over too.
            await db.execute('UPDATE cp_campaigns SET auto_completed_at = NULL WHERE id = $1',
                             campaign_id)
            await touch_cp_campaign(db, campaign_id, user_name)
    return mission_id


async def patch_cp_mission(mission_id: int, campaign_id: int, patch: dict,
                           name, op_date, user_name: str):
    """Merges `patch` into the stored fields — only the keys sent change.
    `name` and `op_date` mirror the fields into their columns when given
    (None leaves the column alone; op_date '' clears it). The wizard's fields
    hold the date as ISO text, and asyncpg wants a date for a date column."""
    if isinstance(op_date, str) and op_date:
        from datetime import date as _date
        try:
            op_date = _date.fromisoformat(op_date)
        except ValueError:
            op_date = ''
    pool = await get_pool()
    async with pool.acquire() as db:
        async with db.transaction():
            await db.execute(
                """UPDATE cp_missions
                      SET fields = (fields::jsonb || $2::jsonb)::text,
                          name = COALESCE($3, name),
                          op_date = CASE WHEN $4 THEN $5::date ELSE op_date END,
                          updated_by_name = $6,
                          updated_at = (NOW() AT TIME ZONE 'UTC')
                    WHERE id = $1""",
                mission_id, _json.dumps(patch), name, op_date is not None,
                op_date or None, user_name)
            await touch_cp_campaign(db, campaign_id, user_name)


async def move_cp_mission(mission_id: int, campaign_id: int, step: int):
    """Swap a mission with its neighbour (step -1 up, +1 down)."""
    pool = await get_pool()
    async with pool.acquire() as db:
        async with db.transaction():
            rows = await db.fetch(
                'SELECT id FROM cp_missions WHERE campaign_id = $1 ORDER BY position, id FOR UPDATE',
                campaign_id)
            order = [r['id'] for r in rows]
            if mission_id not in order:
                return
            i = order.index(mission_id)
            j = i + step
            if not 0 <= j < len(order):
                return
            order[i], order[j] = order[j], order[i]
            for position, mid in enumerate(order):
                await db.execute('UPDATE cp_missions SET position = $2 WHERE id = $1',
                                 mid, position)


async def delete_cp_mission(mission_id: int, campaign_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute('DELETE FROM cp_missions WHERE id = $1 AND campaign_id = $2',
                         mission_id, campaign_id)


async def set_cp_mission_orbat(mission_id: int, orbat_id, user_name: str,
                              created: bool = False):
    """Point a mission at an ORBAT (or at none). The ORBAT is a link, not part of
    the mission: deleting a real mission leaves it, and an ORBAT linked to
    several missions is one ORBAT. `created` says the planner made it for this
    mission (New or Copy on the card), which is what lets deleting a *test*
    mission take it along."""
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            """UPDATE cp_missions SET orbat_id = $2, updated_by_name = $3,
                   orbat_created = $4,
                   updated_at = (NOW() AT TIME ZONE 'UTC') WHERE id = $1""",
            mission_id, orbat_id, user_name, bool(created and orbat_id))


async def cp_linked_orbat_ids(guild_id: str) -> set:
    """Every ORBAT a planner mission on this guild links."""
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            """SELECT DISTINCT m.orbat_id FROM cp_missions m
                 JOIN cp_campaigns c ON c.id = m.campaign_id
                WHERE c.guild_id = $1 AND m.orbat_id IS NOT NULL""", guild_id)
    return {r['orbat_id'] for r in rows}


async def cp_orbat_linked(guild_id: str, orbat_id: int) -> bool:
    """Whether a planner mission on this guild links this ORBAT — what lets a
    planner who is not an admin open it in the ORBAT editor."""
    pool = await get_pool()
    async with pool.acquire() as db:
        return bool(await db.fetchval(
            """SELECT 1 FROM cp_missions m JOIN cp_campaigns c ON c.id = m.campaign_id
                WHERE c.guild_id = $1 AND m.orbat_id = $2 LIMIT 1""", guild_id, orbat_id))


async def cp_created_orbats(campaign_id: int = None, mission_id: int = None) -> list:
    """ORBATs the planner created for a campaign's (or one mission's) missions
    that nothing else uses: no other planner mission links them and no
    operation has ever run on them. What deleting a test campaign or mission
    may delete along with it — anything else is somebody's roster."""
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            """SELECT DISTINCT m.orbat_id FROM cp_missions m
                WHERE m.orbat_created AND m.orbat_id IS NOT NULL
                  AND ($1::int IS NULL OR m.campaign_id = $1)
                  AND ($2::int IS NULL OR m.id = $2)
                  AND NOT EXISTS (SELECT 1 FROM cp_missions o
                                   WHERE o.orbat_id = m.orbat_id
                                     AND NOT (COALESCE(o.campaign_id = $1, FALSE)
                                              OR COALESCE(o.id = $2, FALSE)))
                  AND NOT EXISTS (SELECT 1 FROM operations op WHERE op.orbat_id = m.orbat_id)""",
            campaign_id, mission_id)
    return [r['orbat_id'] for r in rows]


# -- maps (milestone 5) -----------------------------------------------------

async def set_cp_campaign_map(campaign_id: int, map_id):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute('UPDATE cp_campaigns SET map_id = $2 WHERE id = $1', campaign_id, map_id)


async def set_cp_ao_map(ao_id: int, map_id):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute('UPDATE cp_aos SET map_id = $2 WHERE id = $1', ao_id, map_id)


async def set_cp_ao_areas(areas: dict):
    """ao id → the campaign map layer that holds its outline."""
    if not areas:
        return
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.executemany('UPDATE cp_aos SET area = $2 WHERE id = $1',
                             [(k, v) for k, v in areas.items()])


async def cp_ao_phases(campaign_id: int, ao_id: int) -> list:
    """Every phase fought in an AO, across the campaign's missions in order:
    id, title, its number within its mission, and the mission's name."""
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            """SELECT p.id, p.title, p.ao_id, m.name AS mission_name, m.id AS mission_id,
                      ROW_NUMBER() OVER (PARTITION BY p.mission_id
                                         ORDER BY p.position, p.id) AS number
                 FROM cp_phases p JOIN cp_missions m ON m.id = p.mission_id
                WHERE m.campaign_id = $1
                ORDER BY m.position, m.id, p.position, p.id""", campaign_id)
    return [dict(r) for r in rows if r['ao_id'] == ao_id]


async def set_cp_phase_layers(layers: dict):
    """phase id → the layer it is drawn on (None for one with no map)."""
    if not layers:
        return
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.executemany('UPDATE cp_phases SET layer_id = $2 WHERE id = $1',
                             [(k, v) for k, v in layers.items()])


async def cp_campaign_map_ids(campaign_id: int) -> list:
    """The campaign map and every AO map — the maps the planner made."""
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            """SELECT map_id FROM cp_campaigns WHERE id = $1 AND map_id IS NOT NULL
               UNION SELECT map_id FROM cp_aos WHERE campaign_id = $1 AND map_id IS NOT NULL""",
            campaign_id)
    return [r['map_id'] for r in rows]


async def set_cp_mission_recording(mission_id: int, ocap_id, *, manual: bool):
    """Say which recording a mission is (None for none) and keep the campaign's
    Kill Tracker campaign in step: the new recording is added to it, and the
    old one taken out — but only when the planner was the one who put it in
    and no other mission of the campaign is that recording. A test campaign
    has no Kill Tracker campaign, so nothing there changes."""
    pool = await get_pool()
    async with pool.acquire() as db:
        async with db.transaction():
            current = await db.fetchrow(
                """SELECT m.ocap_id, m.ocap_kt_added, m.campaign_id, c.kt_campaign_id
                     FROM cp_missions m JOIN cp_campaigns c ON c.id = m.campaign_id
                    WHERE m.id = $1 FOR UPDATE OF m""", mission_id)
            if current is None:
                return
            kt = current['kt_campaign_id']
            if (current['ocap_id'] and current['ocap_kt_added'] and kt
                    and current['ocap_id'] != ocap_id):
                shared = await db.fetchval(
                    """SELECT 1 FROM cp_missions WHERE campaign_id = $1 AND ocap_id = $2
                          AND id <> $3 LIMIT 1""",
                    current['campaign_id'], current['ocap_id'], mission_id)
                if not shared:
                    await db.execute(
                        'DELETE FROM kt_campaign_missions WHERE campaign_id = $1 AND ocap_id = $2',
                        kt, current['ocap_id'])
            added = bool(current['ocap_kt_added'] and current['ocap_id'] == ocap_id)
            if ocap_id and kt and current['ocap_id'] != ocap_id:
                added = await db.fetchval(
                    """INSERT INTO kt_campaign_missions (campaign_id, ocap_id) VALUES ($1, $2)
                       ON CONFLICT DO NOTHING RETURNING 1""", kt, ocap_id) is not None
                await db.execute(
                    """UPDATE kt_campaigns SET updated_at = (NOW() AT TIME ZONE 'UTC')
                        WHERE id = $1""", kt)
            await db.execute(
                """UPDATE cp_missions SET ocap_id = $2, ocap_manual = $3, ocap_kt_added = $4
                    WHERE id = $1""", mission_id, ocap_id, manual, bool(ocap_id) and added)


async def cp_missions_awaiting_recording(first, last) -> list:
    """Missions played between two days whose recording nobody has chosen yet,
    in campaigns that are not archived — what the loop tries to match."""
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            """SELECT m.id, m.op_date FROM cp_missions m
                 JOIN cp_campaigns c ON c.id = m.campaign_id
                WHERE m.ocap_id IS NULL AND NOT m.ocap_manual
                  AND m.op_date BETWEEN $1 AND $2 AND c.status <> 'archived'""", first, last)
    return [dict(r) for r in rows]


async def cp_campaigns_may_complete() -> list:
    """Active campaigns the bot has not closed before, on servers that have
    not switched closing off, each with its missions' dates and recording
    state — what `campaign.campaign_finished()` decides on."""
    pool = await get_pool()
    async with pool.acquire() as db:
        campaigns = await db.fetch(
            """SELECT c.id, c.guild_id, c.name, c.status, c.planned_missions,
                      c.auto_completed_at, c.is_test
                 FROM cp_campaigns c
                 LEFT JOIN cp_settings s ON s.guild_id = c.guild_id
                WHERE c.status = 'active' AND c.auto_completed_at IS NULL
                  AND s.auto_complete IS DISTINCT FROM FALSE""")
        if not campaigns:
            return []
        missions = await db.fetch(
            """SELECT campaign_id, id, op_date, ocap_id, ocap_manual FROM cp_missions
                WHERE campaign_id = ANY($1::int[])""", [c['id'] for c in campaigns])
    by_campaign: dict = {}
    for m in missions:
        by_campaign.setdefault(m['campaign_id'], []).append(dict(m))
    return [{**dict(c), 'missions': by_campaign.get(c['id'], [])} for c in campaigns]


async def auto_complete_cp_campaign(campaign_id: int) -> bool:
    """Mark a campaign Complete as the bot. False when somebody changed it in
    the meantime (it is no longer active, or was closed before)."""
    pool = await get_pool()
    async with pool.acquire() as db:
        done = await db.fetchval(
            """UPDATE cp_campaigns SET status = 'complete',
                      auto_completed_at = (NOW() AT TIME ZONE 'UTC'),
                      updated_by_name = 'ORBATBot', updated_at = (NOW() AT TIME ZONE 'UTC')
                WHERE id = $1 AND status = 'active' AND auto_completed_at IS NULL
               RETURNING id""", campaign_id)
    return done is not None


async def link_cp_campaign_recordings(campaign_id: int):
    """After a test campaign is made real: put the recordings its missions are
    into its new Kill Tracker campaign."""
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            'SELECT id, ocap_id, ocap_manual FROM cp_missions WHERE campaign_id = $1 '
            'AND ocap_id IS NOT NULL', campaign_id)
    for r in rows:
        # Re-linking from nothing is what adds it; the ocap_id is put back.
        async with pool.acquire() as db:
            await db.execute('UPDATE cp_missions SET ocap_id = NULL, ocap_kt_added = FALSE '
                             'WHERE id = $1', r['id'])
        await set_cp_mission_recording(r['id'], r['ocap_id'], manual=r['ocap_manual'])


# -- phases -----------------------------------------------------------------

async def get_cp_phases(mission_id: int) -> list:
    """A mission's phases in order, each with its objectives."""
    pool = await get_pool()
    async with pool.acquire() as db:
        phases = await db.fetch(
            'SELECT * FROM cp_phases WHERE mission_id = $1 ORDER BY position, id', mission_id)
        objectives = await db.fetch(
            """SELECT o.* FROM cp_phase_objectives o JOIN cp_phases p ON p.id = o.phase_id
                WHERE p.mission_id = $1 ORDER BY o.position, o.id""", mission_id)
    by_phase: dict = {}
    for o in objectives:
        by_phase.setdefault(o['phase_id'], []).append(
            {k: o[k] for k in ('library_id', 'role', 'name', 'players_do',
                               'complete_when', 'intsum_hint', 'result')})
    return [{'id': p['id'], 'title': p['title'], 'detail': p['detail'],
             'ao_id': p['ao_id'], 'layer_id': p['layer_id'],
             'objectives': by_phase.get(p['id'], [])} for p in phases]


async def save_cp_phases(mission_id: int, phases: list) -> None:
    """Replace a mission's phase list with `phases` (from campaign.clean_phases).

    A phase sent with the id of one of this mission's phases keeps that row —
    and so, from milestone 5, its map layer — while one without an id is new
    and any phase not sent is deleted. Objectives have nothing hanging off
    them and are replaced wholesale.
    """
    pool = await get_pool()
    async with pool.acquire() as db:
        async with db.transaction():
            existing = {r['id'] for r in await db.fetch(
                'SELECT id FROM cp_phases WHERE mission_id = $1 FOR UPDATE', mission_id)}
            kept = []
            for position, phase in enumerate(phases):
                if phase.get('id') in existing and phase['id'] not in kept:
                    phase_id = phase['id']
                    await db.execute(
                        """UPDATE cp_phases SET position = $2, title = $3, detail = $4,
                                  ao_id = $5 WHERE id = $1""",
                        phase_id, position, phase['title'], phase['detail'], phase['ao_id'])
                    await db.execute('DELETE FROM cp_phase_objectives WHERE phase_id = $1',
                                     phase_id)
                else:
                    phase_id = await db.fetchval(
                        """INSERT INTO cp_phases (mission_id, position, title, detail, ao_id)
                           VALUES ($1, $2, $3, $4, $5) RETURNING id""",
                        mission_id, position, phase['title'], phase['detail'], phase['ao_id'])
                kept.append(phase_id)
                for o_position, o in enumerate(phase['objectives']):
                    await db.execute(
                        """INSERT INTO cp_phase_objectives
                             (phase_id, library_id, position, role, name, players_do,
                              complete_when, intsum_hint, result)
                           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
                        phase_id, o['library_id'], o_position, o['role'], o['name'],
                        o['players_do'], o['complete_when'], o['intsum_hint'], o['result'])
            gone = existing - set(kept)
            if gone:
                await db.execute('DELETE FROM cp_phases WHERE id = ANY($1::int[])', list(gone))


async def cp_phase_titles(mission_ids) -> dict:
    """mission id → its phases' titles and details, in order. What the list
    pages need to tell whether the Orders step is locked, in one query."""
    ids = [int(i) for i in mission_ids]
    if not ids:
        return {}
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            """SELECT mission_id, title, detail FROM cp_phases
                WHERE mission_id = ANY($1::int[]) ORDER BY position, id""", ids)
    out: dict = {}
    for r in rows:
        out.setdefault(r['mission_id'], []).append({'title': r['title'], 'detail': r['detail']})
    return out


# -- the objectives library -------------------------------------------------

async def ensure_objective_library(guild_id: str, seed: list) -> None:
    """Seed a guild's library the first time anybody looks at it. Once seeded
    it is never seeded again, so an admin who empties it gets an empty one."""
    pool = await get_pool()
    async with pool.acquire() as db:
        async with db.transaction():
            await db.execute(
                'INSERT INTO cp_settings (guild_id) VALUES ($1) ON CONFLICT (guild_id) DO NOTHING',
                guild_id)
            seeded = await db.fetchval(
                'SELECT library_seeded FROM cp_settings WHERE guild_id = $1 FOR UPDATE', guild_id)
            if seeded:
                return
            await db.executemany(
                """INSERT INTO cp_objective_library
                     (guild_id, category, kind, name, players_do, complete_when,
                      pairs_with, intsum_hint, position)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
                [(guild_id, e['category'], e['kind'], e['name'], e['players_do'],
                  e['complete_when'], e['pairs_with'], e['intsum_hint'], e['position'])
                 for e in seed])
            await db.execute('UPDATE cp_settings SET library_seeded = TRUE WHERE guild_id = $1',
                             guild_id)


async def list_cp_objectives(guild_id: str) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            'SELECT * FROM cp_objective_library WHERE guild_id = $1 ORDER BY position, id',
            guild_id)
    return [dict(r) for r in rows]


async def get_cp_objective(entry_id: int, guild_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            'SELECT * FROM cp_objective_library WHERE id = $1 AND guild_id = $2',
            entry_id, guild_id)
    return dict(row) if row else None


async def save_cp_objective(guild_id: str, entry_id, values: dict) -> int:
    """Insert (entry_id None) or update one library entry."""
    pool = await get_pool()
    async with pool.acquire() as db:
        if entry_id is None:
            return await db.fetchval(
                """INSERT INTO cp_objective_library
                     (guild_id, category, kind, name, players_do, complete_when,
                      pairs_with, intsum_hint, position)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8,
                           COALESCE((SELECT MAX(position) + 1 FROM cp_objective_library
                                      WHERE guild_id = $1), 1))
                   RETURNING id""",
                guild_id, values['category'], values['kind'], values['name'],
                values['players_do'], values['complete_when'], values['pairs_with'],
                values['intsum_hint'])
        await db.execute(
            """UPDATE cp_objective_library SET category = $3, kind = $4, name = $5,
                   players_do = $6, complete_when = $7, pairs_with = $8, intsum_hint = $9
                WHERE id = $1 AND guild_id = $2""",
            entry_id, guild_id, values['category'], values['kind'], values['name'],
            values['players_do'], values['complete_when'], values['pairs_with'],
            values['intsum_hint'])
        return entry_id


async def delete_cp_objective(entry_id: int, guild_id: str):
    """Objectives already picked into a mission keep their own copy."""
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute('DELETE FROM cp_objective_library WHERE id = $1 AND guild_id = $2',
                         entry_id, guild_id)


# -- images -----------------------------------------------------------------

async def add_cp_image(campaign_id: int, filename: str, content_type: str, data: bytes,
                       user_name: str) -> int:
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchval(
            """INSERT INTO cp_images (campaign_id, filename, content_type, data, bytes,
                                      created_by_name)
               VALUES ($1, $2, $3, $4, $5, $6) RETURNING id""",
            campaign_id, filename, content_type, data, len(data), user_name)


async def get_cp_image(image_id: int):
    """The image with its campaign's guild, or None."""
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            """SELECT i.*, c.guild_id FROM cp_images i
                 JOIN cp_campaigns c ON c.id = i.campaign_id WHERE i.id = $1""", image_id)
    return dict(row) if row else None


async def cp_image_data(campaign_id: int, ids) -> dict:
    """id → bytes for those of `ids` that belong to the campaign — what an
    export embeds."""
    ids = [int(i) for i in ids]
    if not ids:
        return {}
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            'SELECT id, data FROM cp_images WHERE campaign_id = $1 AND id = ANY($2::int[])',
            campaign_id, ids)
    return {r['id']: bytes(r['data']) for r in rows}


async def cp_image_meta(campaign_id: int, ids) -> dict:
    """id → {filename, bytes} for those of `ids` that belong to the campaign."""
    ids = [int(i) for i in ids]
    if not ids:
        return {}
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            """SELECT id, filename, bytes, content_type FROM cp_images
                WHERE campaign_id = $1 AND id = ANY($2::int[])""", campaign_id, ids)
    return {r['id']: dict(r) for r in rows}


# -- drops (milestone 4) ----------------------------------------------------
# A row records decisions only — who approved which version for which time,
# that the team was pinged about a hold, what was posted where. Whether a drop
# is approved *now* is `campaign.drop_state()`, from this row and what the drop
# would post now.

def _drop_dict(row) -> dict:
    out = dict(row)
    out['message_ids'] = _json.loads(out.get('message_ids') or '[]')
    return out


async def get_cp_drops(mission_id: int) -> dict:
    """kind → row for one mission."""
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch('SELECT * FROM cp_drops WHERE mission_id = $1', mission_id)
    return {r['kind']: _drop_dict(r) for r in rows}


async def approve_cp_drop(mission_id: int, kind: str, approved_hash: str, due_at,
                          user_id: str, user_name: str):
    """Approve what the drop would post now, for the time it would post. A
    new approval forgets an earlier hold ping, so a later hold pings again."""
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            """INSERT INTO cp_drops (mission_id, kind, due_at, approved_hash, approved_by,
                                     approved_by_name, approved_at)
               VALUES ($1, $2, $3, $4, $5, $6, (NOW() AT TIME ZONE 'UTC'))
               ON CONFLICT (mission_id, kind) DO UPDATE
                  SET due_at = EXCLUDED.due_at, approved_hash = EXCLUDED.approved_hash,
                      approved_by = EXCLUDED.approved_by,
                      approved_by_name = EXCLUDED.approved_by_name,
                      approved_at = EXCLUDED.approved_at, held_pinged_at = NULL""",
            mission_id, kind, due_at, approved_hash, user_id, user_name)


async def unapprove_cp_drop(mission_id: int, kind: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            """UPDATE cp_drops SET approved_hash = NULL, approved_by = NULL,
                   approved_by_name = NULL, approved_at = NULL
                WHERE mission_id = $1 AND kind = $2 AND posted_at IS NULL""",
            mission_id, kind)


async def skip_cp_drop(mission_id: int, kind: str, user_name: str, skipped: bool):
    """Mark a drop as not to be posted by the bot (or undo that)."""
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            """INSERT INTO cp_drops (mission_id, kind, skipped, skipped_by_name)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT (mission_id, kind) DO UPDATE
                  SET skipped = EXCLUDED.skipped, skipped_by_name = EXCLUDED.skipped_by_name
                WHERE cp_drops.posted_at IS NULL OR cp_drops.skipped = 1""",
            mission_id, kind, 1 if skipped else 0, user_name if skipped else None)


async def mark_cp_drop_posted(mission_id: int, kind: str, channel_id: str,
                              message_ids: list, posted_hash: str):
    """Record what went out where — on a first post and after an edit."""
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            """UPDATE cp_drops SET channel_id = $3, message_ids = $4, posted_hash = $5,
                   posted_at = COALESCE(posted_at, (NOW() AT TIME ZONE 'UTC'))
                WHERE mission_id = $1 AND kind = $2""",
            mission_id, kind, channel_id, _json.dumps([str(m) for m in message_ids]),
            posted_hash)


async def mark_cp_drop_pinged(mission_id: int, kind: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            """INSERT INTO cp_drops (mission_id, kind, held_pinged_at)
               VALUES ($1, $2, (NOW() AT TIME ZONE 'UTC'))
               ON CONFLICT (mission_id, kind) DO UPDATE
                  SET held_pinged_at = EXCLUDED.held_pinged_at""", mission_id, kind)


async def cp_missions_near(start, end) -> list:
    """Missions dated between two days, in campaigns that are not archived,
    with their guild and whether their campaign is a test — what the posting
    loop looks at each minute."""
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            """SELECT m.*, c.guild_id, c.name AS campaign_name, c.is_test
                 FROM cp_missions m JOIN cp_campaigns c ON c.id = m.campaign_id
                WHERE m.op_date BETWEEN $1 AND $2 AND c.status <> 'archived'
                ORDER BY m.op_date, m.id""", start, end)
    return [_mission_dict(r) for r in rows]


async def cp_posted_messages(campaign_id: int = None, mission_id: int = None) -> list:
    """(channel_id, message_id) for every drop message a campaign or mission
    posted — what deleting a test campaign takes out of Discord."""
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            """SELECT d.channel_id, d.message_ids FROM cp_drops d
                 JOIN cp_missions m ON m.id = d.mission_id
                WHERE ($1::int IS NULL OR m.campaign_id = $1)
                  AND ($2::int IS NULL OR m.id = $2)
                  AND d.channel_id IS NOT NULL""", campaign_id, mission_id)
    return [(r['channel_id'], mid) for r in rows
            for mid in _json.loads(r['message_ids'] or '[]')]


async def set_cp_mission_operation(mission_id: int, operation_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute('UPDATE cp_missions SET operation_id = $2 WHERE id = $1',
                         mission_id, operation_id)
