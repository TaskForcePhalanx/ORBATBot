"""An operation and the requests against it: the board, the queue, the bookings.

Part of `utils/database`, split out of one 3,333-line module. The package's
`__init__` re-exports every name, so `from utils import database` and
`database.anything` work exactly as they did.

The rules these queries serve are in [docs/slots.md](../../docs/slots.md).
"""
from .core import (get_pool)


async def get_active_operation(guild_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow(
            'SELECT * FROM operations WHERE guild_id = $1 AND is_active = 1 ORDER BY created_at DESC LIMIT 1',
            guild_id,
        )


async def get_operation(operation_id: int):
    """One operation by id, active or not.

    A request keeps pointing at the operation it was made for. Deciding it has
    to read *that* one rather than whatever is running now, or a request left
    over from last week is written into this week's roster.
    """
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow(
            'SELECT * FROM operations WHERE id = $1', operation_id
        )


async def create_operation(guild_id: str, name: str, sheet_url: str = None,
                           sheet_id: str = None, squad_col: int = None,
                           role_col: int = None, status_col: int = None,
                           assigned_col: int = None, orbat_id: int = None) -> int:
    """Start an operation, deactivating whatever was running before.

    Either sheet_url/sheet_id or orbat_id identifies the roster; `utils/roster.py`
    is what decides which, and nothing above it needs to care.
    """
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            'UPDATE operations SET is_active = 0 WHERE guild_id = $1',
            guild_id,
        )
        row = await db.fetchrow(
            '''INSERT INTO operations
               (guild_id, name, sheet_url, sheet_id, squad_col, role_col,
                status_col, assigned_col, orbat_id)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
               RETURNING id''',
            guild_id, name, sheet_url, sheet_id, squad_col, role_col,
            status_col, assigned_col, orbat_id,
        )
        return row['id']


async def get_pending_slots(operation_id: int) -> list:
    """The slot identifiers of every pending request, in `utils/roster.py`'s
    key form so a sheet-backed and an ORBAT-backed operation read alike."""
    from utils.roster import request_key
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            """SELECT slot_id, sheet_row, sheet_col FROM requests
                WHERE operation_id = $1 AND status = 'pending'""",
            operation_id,
        )
        return [request_key(row) for row in rows]


async def get_approved_slots(operation_id: int) -> list:
    """Same, for approved requests."""
    from utils.roster import request_key
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            """SELECT slot_id, sheet_row, sheet_col FROM requests
                WHERE operation_id = $1 AND status = 'approved'""",
            operation_id,
        )
        return [request_key(row) for row in rows]


async def get_member_active_request(guild_id: str, operation_id: int, member_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow(
            """SELECT * FROM requests
               WHERE guild_id = $1 AND operation_id = $2 AND member_id = $3
               AND status IN ('pending', 'approved')""",
            guild_id, operation_id, member_id,
        )


async def create_request(guild_id: str, operation_id: int, member_id: str,
                         member_name: str, slot_label: str, sheet_row: int = None,
                         sheet_col: int = None, unit_role: str = None,
                         slot_id: int = None) -> int:
    """One booking. `sheet_row`/`sheet_col` identify the slot on a sheet-backed
    operation, `slot_id` on an ORBAT-backed one — never both."""
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            '''INSERT INTO requests
               (guild_id, operation_id, member_id, member_name, slot_label,
                sheet_row, sheet_col, unit_role, slot_id)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
               RETURNING id''',
            guild_id, operation_id, member_id, member_name, slot_label,
            sheet_row, sheet_col, unit_role, slot_id,
        )
        return row['id']


async def update_request_sheet_col(request_id: int, sheet_row: int, sheet_col: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            'UPDATE requests SET sheet_row = $1, sheet_col = $2 WHERE id = $3',
            sheet_row, sheet_col, request_id,
        )


async def update_request_message(request_id: int, message_id: str, channel_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            'UPDATE requests SET approval_message_id = $1, approval_channel_id = $2 WHERE id = $3',
            message_id, channel_id, request_id,
        )


async def get_request_by_id(request_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow('SELECT * FROM requests WHERE id = $1', request_id)


async def get_all_pending_requests() -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch("SELECT * FROM requests WHERE status = 'pending'")


async def cancel_member_request(guild_id: str, operation_id: int, member_id: str) -> bool:
    pool = await get_pool()
    async with pool.acquire() as db:
        result = await db.execute(
            """UPDATE requests SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
               WHERE guild_id = $1 AND operation_id = $2 AND member_id = $3 AND status = 'pending'""",
            guild_id, operation_id, member_id,
        )
        return int(result.split()[-1]) > 0


async def clear_pending_requests(operation_id: int) -> int:
    pool = await get_pool()
    async with pool.acquire() as db:
        result = await db.execute(
            """UPDATE requests SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
               WHERE operation_id = $1 AND status = 'pending'""",
            operation_id,
        )
        return int(result.split()[-1])


async def get_active_requests(operation_id: int) -> list:
    """Return all pending and approved requests for an operation."""
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            "SELECT * FROM requests WHERE operation_id = $1 AND status IN ('pending', 'approved') ORDER BY status DESC, created_at",
            operation_id,
        )


async def cancel_request_by_id(request_id: int) -> bool:
    """Cancel one request, whether it was approved or still waiting.

    Both are cleared the same way — `/clear-slot` and the web queue offer it on
    a pending request too — so restricting this to `approved` left the pending
    ones cancelled everywhere except in the database: the member was DMed and
    the approval message went grey, while the row stayed pending and the board
    kept showing it as 🟡 forever.
    """
    pool = await get_pool()
    async with pool.acquire() as db:
        result = await db.execute(
            """UPDATE requests SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
               WHERE id = $1 AND status IN ('pending', 'approved')""",
            request_id,
        )
        return int(result.split()[-1]) > 0


async def approve_request(request_id: int, approved_by: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            """UPDATE requests
               SET status = 'approved', approved_by = $1, updated_at = CURRENT_TIMESTAMP
               WHERE id = $2""",
            approved_by, request_id,
        )


async def deny_request(request_id: int, denied_by: str, reason: str = None):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            """UPDATE requests
               SET status = 'denied', approved_by = $1, denial_reason = $2, updated_at = CURRENT_TIMESTAMP
               WHERE id = $3""",
            denied_by, reason, request_id,
        )


async def save_orbat_message(guild_id: str, channel_id: str, message_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''INSERT INTO orbat_messages (guild_id, channel_id, message_id)
               VALUES ($1, $2, $3)
               ON CONFLICT (guild_id) DO UPDATE SET
                   channel_id = EXCLUDED.channel_id,
                   message_id = EXCLUDED.message_id,
                   updated_at = CURRENT_TIMESTAMP''',
            guild_id, channel_id, message_id,
        )


async def get_orbat_message(guild_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow(
            'SELECT channel_id, message_id FROM orbat_messages WHERE guild_id = $1',
            guild_id,
        )


async def set_event_time(operation_id: int, event_time, reminder_minutes: int):
    # Store as naive UTC — the column is TIMESTAMP WITHOUT TIME ZONE
    if hasattr(event_time, 'tzinfo') and event_time.tzinfo is not None:
        event_time = event_time.replace(tzinfo=None)
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''UPDATE operations
               SET event_time = $1, reminder_minutes = $2, reminder_fired = 0
               WHERE id = $3''',
            event_time, reminder_minutes, operation_id,
        )


async def get_operations_needing_reminder():
    """Return active operations whose reminder window has arrived but not yet fired."""
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            '''SELECT * FROM operations
               WHERE is_active = 1
               AND event_time IS NOT NULL
               AND reminder_fired = 0
               AND event_time - (reminder_minutes * INTERVAL '1 minute') <= CURRENT_TIMESTAMP
               AND event_time > CURRENT_TIMESTAMP'''
        )


async def mark_reminder_fired(operation_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            'UPDATE operations SET reminder_fired = 1 WHERE id = $1',
            operation_id,
        )


async def get_competing_requests(operation_id: int, key: str, exclude_request_id: int) -> list:
    """Every other pending request for the same slot.

    Matched on the roster key rather than in SQL, because the key is derived
    from either a slot id or a pair of sheet coordinates and there are only ever
    a handful of open requests on one operation.
    """
    from utils.roster import request_key
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            """SELECT * FROM requests
               WHERE operation_id = $1 AND id != $2 AND status = 'pending'""",
            operation_id, exclude_request_id,
        )
    return [row for row in rows if request_key(row) == key]


async def get_approved_member_ids(operation_id: int) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            "SELECT member_id, slot_label FROM requests WHERE operation_id = $1 AND status = 'approved'",
            operation_id,
        )
        return [(row['member_id'], row['slot_label']) for row in rows]


async def orbat_live_operation(orbat_id: int):
    """The active operation running on this ORBAT, or None.

    What `orbat_operations()` returns filtered down to the one that matters for
    a warning: editing the roster under a live board is the case worth saying
    something about.
    """
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow(
            '''SELECT * FROM operations
                WHERE orbat_id = $1 AND is_active = 1
                ORDER BY created_at DESC LIMIT 1''',
            orbat_id,
        )


async def orbat_operations(orbat_id: int) -> list:
    """The operations running on this ORBAT, newest first."""
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            '''SELECT * FROM operations WHERE orbat_id = $1
               ORDER BY is_active DESC, created_at DESC''',
            orbat_id,
        )
