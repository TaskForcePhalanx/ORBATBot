"""Standalone events, their sign-ups and their custom response sets.

Part of `utils/database`, split out of one 3,333-line module. The package's
`__init__` re-exports every name, so `from utils import database` and
`database.anything` work exactly as they did.

The rules these queries serve are in [docs/events.md](../../docs/events.md).

event_time is naive UTC, like operations.event_time. These queries compare it against NOW() AT TIME ZONE 'UTC' rather than CURRENT_TIMESTAMP so the result is correct regardless of the database session's timezone setting.
"""
from .core import (_naive, get_pool)


async def create_event(guild_id: str, title: str, event_time, created_by: str,
                       created_by_name: str = None, description: str = None,
                       duration_minutes: int = None, location: str = None,
                       image_url: str = None, mention_role_id: str = None,
                       reminder_minutes: int = 30, recurrence: str = None,
                       recurrence_until=None, recurrence_anchor=None,
                       recurrence_delay_hours: int = None) -> int:
    event_time = _naive(event_time)
    # A new series anchors on its own first start time.
    if recurrence and recurrence_anchor is None:
        recurrence_anchor = event_time
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            '''INSERT INTO events
               (guild_id, title, event_time, created_by, created_by_name, description,
                duration_minutes, location, image_url, mention_role_id, reminder_minutes,
                recurrence, recurrence_until, recurrence_anchor, recurrence_delay_hours)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
               RETURNING id''',
            guild_id, title, event_time, created_by, created_by_name, description,
            duration_minutes, location, image_url, mention_role_id, reminder_minutes,
            recurrence, _naive(recurrence_until), _naive(recurrence_anchor),
            recurrence_delay_hours,
        )
        return row['id']


async def set_event_recurrence(event_id: int, recurrence: str = None,
                               recurrence_until=None, recurrence_anchor=None,
                               recurrence_delay_hours: int = None):
    """Set the recurrence outright. Unlike update_event this can clear it, which
    is how a series is stopped."""
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''UPDATE events
               SET recurrence = $2, recurrence_until = $3, recurrence_anchor = $4,
                   recurrence_delay_hours = $5, updated_at = CURRENT_TIMESTAMP
               WHERE id = $1''',
            event_id, recurrence, _naive(recurrence_until), _naive(recurrence_anchor),
            recurrence_delay_hours,
        )


async def get_event(event_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow('SELECT * FROM events WHERE id = $1', event_id)


async def save_event_message(event_id: int, channel_id: str, message_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            'UPDATE events SET channel_id = $1, message_id = $2 WHERE id = $3',
            channel_id, message_id, event_id,
        )


async def update_event(event_id: int, title: str = None, description: str = None,
                       event_time=None, duration_minutes: int = None,
                       location: str = None, image_url: str = None,
                       reminder_minutes: int = None):
    """Update only the fields that were passed. Changing the time re-arms the reminder."""
    if event_time is not None and hasattr(event_time, 'tzinfo') and event_time.tzinfo is not None:
        event_time = event_time.replace(tzinfo=None)
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''UPDATE events SET
                   title = COALESCE($2, title),
                   description = COALESCE($3, description),
                   event_time = COALESCE($4, event_time),
                   duration_minutes = COALESCE($5, duration_minutes),
                   location = COALESCE($6, location),
                   image_url = COALESCE($7, image_url),
                   reminder_minutes = COALESCE($8, reminder_minutes),
                   reminder_fired = CASE WHEN $4::timestamp IS NOT NULL
                                         THEN 0 ELSE reminder_fired END,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = $1''',
            event_id, title, description, event_time, duration_minutes,
            location, image_url, reminder_minutes,
        )


# Optional event columns that may legitimately be emptied again. update_event()
# uses COALESCE and so can only ever set a value, never remove one — this is its
# counterpart, the same way set_event_mentions() is for the ping roles.
_CLEARABLE_EVENT_FIELDS = frozenset({
    'description', 'location', 'image_url', 'duration_minutes', 'reminder_minutes',
})


async def clear_event_fields(event_id: int, fields: list):
    """Set the given optional columns back to NULL."""
    unknown = [name for name in fields if name not in _CLEARABLE_EVENT_FIELDS]
    if unknown:
        raise ValueError(f"Not a clearable event field: {', '.join(unknown)}")
    if not fields:
        return
    assignments = ', '.join(f'{name} = NULL' for name in fields)
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            f'UPDATE events SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = $1',
            event_id,
        )


async def set_event_mentions(event_id: int, role_ids: str = None):
    """Set the ping roles outright. update_event() uses COALESCE and so cannot
    clear them, which is what passing None here does."""
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''UPDATE events SET mention_role_id = $2, updated_at = CURRENT_TIMESTAMP
               WHERE id = $1''',
            event_id, role_ids,
        )


async def set_event_status(event_id: int, status: str) -> bool:
    pool = await get_pool()
    async with pool.acquire() as db:
        result = await db.execute(
            "UPDATE events SET status = $1, updated_at = CURRENT_TIMESTAMP WHERE id = $2",
            status, event_id,
        )
        return int(result.split()[-1]) > 0


async def get_upcoming_events(guild_id: str, limit: int = 25) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            """SELECT * FROM events
               WHERE guild_id = $1 AND status = 'scheduled'
               ORDER BY event_time
               LIMIT $2""",
            guild_id, limit,
        )


async def delete_event(event_id: int) -> bool:
    """Remove an event outright. Sign-ups and custom responses go with it via
    ON DELETE CASCADE."""
    pool = await get_pool()
    async with pool.acquire() as db:
        result = await db.execute('DELETE FROM events WHERE id = $1', event_id)
        return int(result.split()[-1]) > 0


async def get_guild_events(guild_id: str, limit: int = 25) -> list:
    """Recent and upcoming events of any status — for picking one to delete."""
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            '''SELECT * FROM events WHERE guild_id = $1
               ORDER BY event_time DESC LIMIT $2''',
            guild_id, limit,
        )


async def get_live_events() -> list:
    """Every event whose message still needs working buttons — used to restore
    persistent views after a restart."""
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            "SELECT * FROM events WHERE status = 'scheduled' AND message_id IS NOT NULL"
        )


async def set_event_signup(event_id: int, member_id: str, member_name: str, response: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''INSERT INTO event_signups (event_id, member_id, member_name, response)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT (event_id, member_id) DO UPDATE SET
                   member_name = EXCLUDED.member_name,
                   response = EXCLUDED.response,
                   updated_at = CURRENT_TIMESTAMP''',
            event_id, member_id, member_name, response,
        )


async def remove_event_signup(event_id: int, member_id: str) -> bool:
    pool = await get_pool()
    async with pool.acquire() as db:
        result = await db.execute(
            'DELETE FROM event_signups WHERE event_id = $1 AND member_id = $2',
            event_id, member_id,
        )
        return int(result.split()[-1]) > 0


async def get_event_signup(event_id: int, member_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow(
            'SELECT * FROM event_signups WHERE event_id = $1 AND member_id = $2',
            event_id, member_id,
        )


async def get_event_signups(event_id: int) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            'SELECT * FROM event_signups WHERE event_id = $1 ORDER BY created_at',
            event_id,
        )


async def get_event_responses(event_id: int) -> list:
    """Custom responses for an event, in display order. Empty means defaults."""
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            'SELECT * FROM event_responses WHERE event_id = $1 ORDER BY sort_order',
            event_id,
        )


async def set_event_responses(event_id: int, responses: list):
    """Replace an event's response set. An empty list restores the defaults."""
    pool = await get_pool()
    async with pool.acquire() as db:
        async with db.transaction():
            await db.execute('DELETE FROM event_responses WHERE event_id = $1', event_id)
            for order, item in enumerate(responses):
                await db.execute(
                    '''INSERT INTO event_responses
                       (event_id, key, label, emoji, is_decline, sort_order)
                       VALUES ($1, $2, $3, $4, $5, $6)''',
                    event_id, item['key'], item['label'], item.get('emoji'),
                    int(item.get('is_decline', 0)), order,
                )


async def drop_signups_not_in(event_id: int, valid_keys: list) -> int:
    """Remove sign-ups whose response no longer exists, so a changed response set
    can't leave rows that render nowhere."""
    pool = await get_pool()
    async with pool.acquire() as db:
        result = await db.execute(
            'DELETE FROM event_signups WHERE event_id = $1 AND NOT (response = ANY($2::text[]))',
            event_id, list(valid_keys),
        )
        return int(result.split()[-1])


async def get_events_needing_reminder() -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            """SELECT * FROM events
               WHERE status = 'scheduled'
                 AND reminder_fired = 0
                 AND reminder_minutes IS NOT NULL
                 AND event_time - (reminder_minutes * INTERVAL '1 minute')
                     <= (NOW() AT TIME ZONE 'UTC')
                 AND event_time > (NOW() AT TIME ZONE 'UTC')"""
        )


async def mark_event_reminder_fired(event_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute('UPDATE events SET reminder_fired = 1 WHERE id = $1', event_id)


async def get_finished_events() -> list:
    """Scheduled events whose start time (plus duration) has passed."""
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            """SELECT * FROM events
               WHERE status = 'scheduled'
                 AND event_time + (COALESCE(duration_minutes, 0) * INTERVAL '1 minute')
                     < (NOW() AT TIME ZONE 'UTC')"""
        )


async def get_events_ready_for_handover() -> list:
    """Completed, recurring events whose handover delay has elapsed and whose
    next occurrence hasn't been spawned yet.

    Kept separate from get_finished_events() so an event is marked completed —
    and its message loses its buttons — the moment it ends, while the next
    occurrence can go up later, per recurrence_delay_hours.
    """
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            """SELECT * FROM events
               WHERE status = 'completed'
                 AND recurrence IS NOT NULL
                 AND handover_fired = 0
                 AND event_time + (COALESCE(duration_minutes, 0) * INTERVAL '1 minute')
                     + (COALESCE(recurrence_delay_hours, 0) * INTERVAL '1 hour')
                     <= (NOW() AT TIME ZONE 'UTC')"""
        )


async def mark_event_handover_fired(event_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute('UPDATE events SET handover_fired = 1 WHERE id = $1', event_id)
