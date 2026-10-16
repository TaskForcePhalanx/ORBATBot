"""The Reddit watches — one row is one watch.

Part of `utils/database`, split out of one 3,333-line module. The package's
`__init__` re-exports every name, so `from utils import database` and
`database.anything` work exactly as they did.

The rules these queries serve are in [docs/reddit.md](../../docs/reddit.md).

`seen_ids` is a comma-separated list of the post ids already announced, newest first and capped — a rolling set rather than a high-water timestamp, because a post that is edited moves its timestamp and would otherwise be announced twice. NULL means the watch has never been read: the first read seeds it and announces nothing.
"""
import asyncpg
from .core import (get_pool)


_REDDIT_COLUMNS = (
    'kind', 'source', 'channel_id', 'template', 'author_filter',
    'mention_role_id', 'mention_user_id', 'enabled',
)


async def get_reddit_feeds(guild_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            'SELECT * FROM reddit_feeds WHERE guild_id = $1 ORDER BY kind, lower(source)',
            guild_id,
        )


async def get_reddit_feed(feed_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow('SELECT * FROM reddit_feeds WHERE id = $1', feed_id)


async def get_due_reddit_feeds():
    """Every watch the polling loop should read now.

    Enabled, with somewhere to post, and not standing down after a refusal. A
    watch whose channel was cleared keeps its row and its text, so it can be
    switched back on later, but nothing is read for it in the meantime.
    """
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            '''SELECT * FROM reddit_feeds
                WHERE enabled = 1 AND channel_id IS NOT NULL
                  AND (retry_at IS NULL OR retry_at <= (NOW() AT TIME ZONE 'UTC'))
                ORDER BY id'''
        )


# The unique index turns "this source is already watched here" into a database
# error, and both writers want to say that in words rather than 500.
_DUPLICATE_FEED = "That Reddit user or subreddit is already watched on this server."


async def create_reddit_feed(guild_id: str, values: dict,
                             created_by: str = None,
                             created_by_name: str = None) -> int:
    columns = [name for name in _REDDIT_COLUMNS if name in values]
    placeholders = ', '.join(f'${i}' for i in range(4, 4 + len(columns)))
    pool = await get_pool()
    async with pool.acquire() as db:
        try:
            return await db.fetchval(
                f'''INSERT INTO reddit_feeds
                        (guild_id, created_by, created_by_name, {', '.join(columns)})
                    VALUES ($1, $2, $3, {placeholders})
                    RETURNING id''',
                guild_id, created_by, created_by_name,
                *[values[name] for name in columns],
            )
        except asyncpg.UniqueViolationError:
            raise ValueError(_DUPLICATE_FEED)


async def save_reddit_feed(feed_id: int, values: dict):
    columns = [name for name in _REDDIT_COLUMNS if name in values]
    if not columns:
        return
    assignments = ', '.join(f'{name} = ${i}' for i, name in enumerate(columns, start=2))
    pool = await get_pool()
    async with pool.acquire() as db:
        try:
            await db.execute(
                f'''UPDATE reddit_feeds SET {assignments},
                        updated_at = CURRENT_TIMESTAMP
                     WHERE id = $1''',
                feed_id, *[values[name] for name in columns],
            )
        except asyncpg.UniqueViolationError:
            raise ValueError(_DUPLICATE_FEED)


async def set_reddit_feed_seen(feed_id: int, seen_ids: str):
    """Write what has been announced, and nothing else.

    Marking a post by hand is bookkeeping, not a read, so it deliberately does
    not go through `record_reddit_read()`: that would clear `last_error` and the
    stand-down after a refusal, both of which say something about Reddit rather
    than about what has been announced.
    """
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''UPDATE reddit_feeds SET seen_ids = $2,
                    updated_at = CURRENT_TIMESTAMP
                 WHERE id = $1''',
            feed_id, seen_ids,
        )


async def reset_reddit_feed_seen(feed_id: int):
    """Forget what this watch has announced.

    Called when it is pointed at a different source: the next read then seeds
    from that feed's current posts instead of announcing its whole front page.
    """
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''UPDATE reddit_feeds
                  SET seen_ids = NULL, last_post_at = NULL, last_error = NULL,
                      retry_at = NULL
                WHERE id = $1''',
            feed_id,
        )


async def delete_reddit_feed(feed_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute('DELETE FROM reddit_feeds WHERE id = $1', feed_id)


async def record_reddit_read(feed_id: int, *, seen_ids: str = None,
                             last_post_at=None, error: str = None,
                             retry_at=None):
    """What the last read of this watch found.

    `seen_ids` and `last_post_at` are only written when they are given, so a
    failed read records its error without forgetting what has already been
    announced. `retry_at` is written every time, including as NULL: a read that
    got through is exactly what clears a stand-down.
    """
    sets = ["last_checked_at = (NOW() AT TIME ZONE 'UTC')",
            'last_error = $2', 'retry_at = $3']
    args = [feed_id, error, retry_at]
    if seen_ids is not None:
        sets.append(f'seen_ids = ${len(args) + 1}')
        args.append(seen_ids)
    if last_post_at is not None:
        sets.append(f'last_post_at = ${len(args) + 1}')
        args.append(last_post_at)
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            f'UPDATE reddit_feeds SET {", ".join(sets)} WHERE id = $1', *args
        )
