"""The pool, the schema runner, and the two things every query here uses.

Part of `utils/database`, split out of one 3,333-line module. The package's
`__init__` re-exports every name, so `from utils import database` and
`database.anything` work exactly as they did.
"""
import asyncpg
import logging
import os
from utils import schema


log = logging.getLogger(__name__)


DATABASE_URL = os.getenv('DATABASE_URL')


_pool = None


async def get_pool():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL)
    return _pool


async def ping() -> bool:
    """Can the database be reached right now?

    One `SELECT 1` on a pooled connection. Used by `/healthz`, which reports it
    rather than failing on it: a momentary blip must not look like a reason to
    restart the container (see *Deployment* in CLAUDE.md).
    """
    try:
        pool = await get_pool()
        async with pool.acquire() as db:
            await db.fetchval('SELECT 1')
        return True
    except Exception:
        log.exception('⚠️ healthz: the database did not answer')
        return False


async def init_db():
    """Bring the database up to date — see `utils/schema.py` for what that is.

    Every boot calls this. Once the migrations have run it is two statements,
    not the hundred-odd it used to be.
    """
    pool = await get_pool()
    async with pool.acquire() as db:
        applied = await schema.migrate(db)
    if applied:
        log.info('database: %d migration(s) applied — %s',
                 len(applied), '; '.join(applied))


# The channels the bot posts into, and the name each falls back to. A guild that
# has never touched the settings has NULL in all three and is served entirely by
# the fallbacks, exactly as before they existed.
CHANNEL_KINDS = {
    'orbat': ('orbat_channel_id', 'orbat'),
    'approvals': ('approvals_channel_id', 'slot-approvals'),
    'archive': ('archive_channel_id', 'approval-archive'),
}


def _naive(dt):
    """Strip tzinfo so values match the naive-UTC columns."""
    if dt is not None and hasattr(dt, 'tzinfo') and dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt
