"""Voice intervals and what a guild counts.

Part of `utils/database`, split out of one 3,333-line module. The package's
`__init__` re-exports every name, so `from utils import database` and
`database.anything` work exactly as they did.

The rules these queries serve are in [docs/voice.md](../../docs/voice.md).

`seconds` is filled when an interval closes. `heartbeat_at` is refreshed while one is open, so a hard crash costs at most one heartbeat of unrecorded time instead of the whole session — and so a live total can include the interval somebody is in right now.
"""
from .core import (_naive, get_pool)


# The elapsed time of one row, closed or still running.
_VOICE_SECONDS = """
    COALESCE(seconds, GREATEST(0, EXTRACT(EPOCH FROM
        (COALESCE(heartbeat_at, started_at) - started_at))))
"""


async def get_voice_settings(guild_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow('SELECT * FROM voice_settings WHERE guild_id = $1', guild_id)


_VOICE_SETTING_COLUMNS = (
    'enabled', 'channel_id', 'min_log_minutes', 'count_afk', 'count_solo',
    'excluded_channels', 'board_enabled', 'board_channel_id', 'board_period',
    'board_hour',
)


async def save_voice_settings(guild_id: str, values: dict):
    columns = [name for name in _VOICE_SETTING_COLUMNS if name in values]
    if not columns:
        return
    updates = ', '.join(f'{name} = EXCLUDED.{name}' for name in columns)
    placeholders = ', '.join(f'${i}' for i in range(2, 2 + len(columns)))
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            f'''INSERT INTO voice_settings (guild_id, {', '.join(columns)})
                VALUES ($1, {placeholders})
                ON CONFLICT (guild_id) DO UPDATE SET
                    {updates}, updated_at = CURRENT_TIMESTAMP''',
            guild_id, *[values[name] for name in columns],
        )


async def start_voice_session(guild_id: str, member_id: str, member_name: str,
                              channel_id: str, channel_name: str, started_at) -> int:
    started_at = _naive(started_at)
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            '''INSERT INTO voice_sessions
               (guild_id, member_id, member_name, channel_id, channel_name,
                started_at, heartbeat_at)
               VALUES ($1, $2, $3, $4, $5, $6, $6)
               RETURNING id''',
            guild_id, member_id, member_name, channel_id, channel_name, started_at,
        )
        return row['id']


async def end_voice_session(session_id: int, ended_at) -> int:
    """Close one interval and return how many seconds it lasted."""
    ended_at = _naive(ended_at)
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            '''UPDATE voice_sessions
               SET ended_at = $2, heartbeat_at = $2,
                   seconds = GREATEST(0, EXTRACT(EPOCH FROM ($2 - started_at))::int)
               WHERE id = $1 AND ended_at IS NULL
               RETURNING seconds''',
            session_id, ended_at,
        )
        return row['seconds'] if row else 0


async def heartbeat_voice_sessions(session_ids: list, moment) -> None:
    if not session_ids:
        return
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            'UPDATE voice_sessions SET heartbeat_at = $2 WHERE id = ANY($1::int[])',
            list(session_ids), _naive(moment),
        )


async def close_dangling_voice_sessions() -> int:
    """Close intervals left open by a crash, at their last heartbeat.

    Never invents time: without a heartbeat the interval closes at its start and
    counts zero.
    """
    pool = await get_pool()
    async with pool.acquire() as db:
        result = await db.execute(
            '''UPDATE voice_sessions
               SET ended_at = COALESCE(heartbeat_at, started_at),
                   seconds = GREATEST(0, EXTRACT(EPOCH FROM
                       (COALESCE(heartbeat_at, started_at) - started_at))::int)
               WHERE ended_at IS NULL'''
        )
        return int(result.split()[-1])


async def get_voice_leaderboard(guild_id: str, since=None, limit: int = 25) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            f'''SELECT member_id,
                       MAX(member_name) AS member_name,
                       SUM({_VOICE_SECONDS})::bigint AS total_seconds,
                       COUNT(*) AS sessions,
                       MAX(COALESCE(ended_at, heartbeat_at)) AS last_seen
                FROM voice_sessions
                WHERE guild_id = $1 AND ($2::timestamp IS NULL OR started_at >= $2)
                GROUP BY member_id
                HAVING SUM({_VOICE_SECONDS}) > 0
                ORDER BY total_seconds DESC
                LIMIT $3''',
            guild_id, _naive(since), limit,
        )


async def get_voice_member_total(guild_id: str, member_id: str, since=None):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow(
            f'''SELECT SUM({_VOICE_SECONDS})::bigint AS total_seconds,
                       COUNT(*) AS sessions
                FROM voice_sessions
                WHERE guild_id = $1 AND member_id = $2
                  AND ($3::timestamp IS NULL OR started_at >= $3)''',
            guild_id, member_id, _naive(since),
        )


async def get_voice_channel_totals(guild_id: str, since=None, limit: int = 10) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            f'''SELECT channel_id,
                       MAX(channel_name) AS channel_name,
                       SUM({_VOICE_SECONDS})::bigint AS total_seconds
                FROM voice_sessions
                WHERE guild_id = $1 AND ($2::timestamp IS NULL OR started_at >= $2)
                GROUP BY channel_id
                HAVING SUM({_VOICE_SECONDS}) > 0
                ORDER BY total_seconds DESC
                LIMIT $3''',
            guild_id, _naive(since), limit,
        )


async def get_voice_boards() -> list:
    """Every guild whose self-updating leaderboard is switched on."""
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            """SELECT * FROM voice_settings
               WHERE board_enabled = 1 AND board_channel_id IS NOT NULL"""
        )


async def set_voice_board_state(guild_id: str, message_id: str = None,
                                updated_on=None, channel_id: str = None):
    """Remember which message the board is, and which local day it last showed.

    Passing message_id=None clears it, which is how a deleted message makes the
    next refresh post a fresh one.
    """
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            """UPDATE voice_settings
               SET board_message_id = $2,
                   board_channel_id = COALESCE($4, board_channel_id),
                   board_updated_on = COALESCE($3, board_updated_on),
                   updated_at = CURRENT_TIMESTAMP
               WHERE guild_id = $1""",
            guild_id, message_id, updated_on, channel_id,
        )
