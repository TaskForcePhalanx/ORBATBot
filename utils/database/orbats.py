"""The rosters held here rather than in a sheet, and their squads, slots and nets.

Part of `utils/database`, split out of one 3,333-line module. The package's
`__init__` re-exports every name, so `from utils import database` and
`database.anything` work exactly as they did.

The rules these queries serve are in [docs/orbats.md](../../docs/orbats.md).
"""
from .core import (get_pool)


async def get_guild_orbats(guild_id: str) -> list:
    """Every ORBAT on this guild, with its size and whether it is running.

    `live_operation` is the name of the active operation backed by this ORBAT,
    or NULL. Only one operation per guild is active at a time, so at most one
    row can carry it — which is the point: with several ORBATs in the list,
    that is the only thing that says which one tonight's board comes from.
    """
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            '''SELECT o.*,
                      (SELECT COUNT(*) FROM orbat_squads q WHERE q.orbat_id = o.id)
                        AS squad_count,
                      (SELECT COUNT(*) FROM orbat_slots s
                         JOIN orbat_squads q ON q.id = s.squad_id
                        WHERE q.orbat_id = o.id) AS slot_count,
                      (SELECT op.name FROM operations op
                        WHERE op.orbat_id = o.id AND op.is_active = 1
                        ORDER BY op.created_at DESC LIMIT 1) AS live_operation
                 FROM orbats o
                WHERE o.guild_id = $1
                ORDER BY o.updated_at DESC''',
            guild_id,
        )


async def get_orbat(orbat_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow('SELECT * FROM orbats WHERE id = $1', orbat_id)


async def create_orbat(guild_id: str, name: str, description: str,
                       created_by: str, created_by_name: str) -> int:
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            '''INSERT INTO orbats (guild_id, name, description, created_by, created_by_name)
               VALUES ($1, $2, $3, $4, $5) RETURNING id''',
            guild_id, name, description, created_by, created_by_name,
        )
        return row['id']


async def rename_orbat(orbat_id: int, name: str, description: str = None):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''UPDATE orbats SET name = $2, description = $3,
                                 updated_at = CURRENT_TIMESTAMP
               WHERE id = $1''',
            orbat_id, name, description,
        )


async def delete_orbat(orbat_id: int) -> bool:
    """Delete an ORBAT, taking everyone booked into it off the roster first.

    The cascade removes the squads and slots; `requests.slot_id` carries no
    foreign key, so without releasing the bookings here an approved request
    would survive pointing at a slot that no longer exists — the same orphan
    `apply_orbat_structure()` goes out of its way to avoid on an edit.
    """
    pool = await get_pool()
    async with pool.acquire() as db:
        async with db.transaction():
            await db.execute(
                """UPDATE requests
                      SET status = 'cancelled',
                          slot_id = NULL,
                          denial_reason = COALESCE(denial_reason,
                                                   'ORBAT deleted'),
                          updated_at = CURRENT_TIMESTAMP
                    WHERE status IN ('pending', 'approved')
                      AND slot_id IN (
                            SELECT s.id FROM orbat_slots s
                              JOIN orbat_squads q ON q.id = s.squad_id
                             WHERE q.orbat_id = $1)""",
                orbat_id,
            )
            result = await db.execute('DELETE FROM orbats WHERE id = $1', orbat_id)
        return result.endswith('1')


async def get_orbat_structure(orbat_id: int, operation_id: int = None) -> list:
    """Squads with their slots, in order, each slot carrying its bookings.

    A booking is a row in `requests` pointing at the slot. They are read here
    rather than stored on the slot so the same ORBAT can back several operations
    at once — and so an edit can say who it would unseat.

    `bookings` is always every operation's, which is what the editor's
    confirmation page needs. `booking` and `pending` are the one operation's
    when *operation_id* is given, so a board shows that night and no other.
    """
    pool = await get_pool()
    async with pool.acquire() as db:
        squads = [
            dict(row) for row in await db.fetch(
                'SELECT * FROM orbat_squads WHERE orbat_id = $1 ORDER BY sort_order, id',
                orbat_id,
            )
        ]
        if not squads:
            return []
        by_id = {}
        for squad in squads:
            squad['slots'] = []
            by_id[squad['id']] = squad

        slots = await db.fetch(
            '''SELECT * FROM orbat_slots WHERE squad_id = ANY($1::int[])
               ORDER BY sort_order, id''',
            list(by_id),
        )
        bookings: dict = {}
        if slots:
            rows = await db.fetch(
                '''SELECT r.id AS request_id, r.slot_id, r.member_name, r.unit_role,
                          r.status, r.operation_id, o.name AS operation_name
                     FROM requests r
                     JOIN operations o ON o.id = r.operation_id
                    WHERE r.slot_id = ANY($1::int[])
                      AND r.status IN ('pending', 'approved')''',
                [row['id'] for row in slots],
            )
            for row in rows:
                bookings.setdefault(row['slot_id'], []).append(dict(row))

        for row in slots:
            slot = dict(row)
            here = bookings.get(slot['id'], [])
            slot['bookings'] = here
            mine = ([b for b in here if b['operation_id'] == operation_id]
                    if operation_id is not None else here)
            slot['booking'] = next((b for b in mine if b['status'] == 'approved'), None)
            slot['pending'] = any(b['status'] == 'pending' for b in mine)
            by_id[slot['squad_id']]['slots'].append(slot)

        return squads


async def apply_orbat_structure(orbat_id: int, parsed_squads: list, diff,
                                source_text: str = None):
    """Write the parsed structure, keeping the ids the diff matched.

    Order matters: removals go first, so a squad name freed by this edit can be
    reused by another squad in the same edit.
    """
    squad_of_new, slot_of_new = {}, {}
    remove_squads, remove_slots = [], []

    for change in diff.squads:
        if change.kind == 'removed':
            remove_squads.append(change.old['id'])
            continue
        squad_of_new[id(change.new)] = change.old['id'] if change.old else None
        for slot_change in change.slots:
            if slot_change.kind == 'removed':
                remove_slots.append(slot_change.old['id'])
            elif slot_change.new is not None:
                slot_of_new[id(slot_change.new)] = (
                    slot_change.old['id'] if slot_change.old else None
                )

    pool = await get_pool()
    async with pool.acquire() as db:
        async with db.transaction():
            if remove_squads:
                # ON DELETE CASCADE takes the squad's slots with it, so their
                # bookings need releasing here just as much as a single removed
                # slot's do.
                await db.execute(
                    """UPDATE requests
                          SET status = 'cancelled',
                              slot_id = NULL,
                              denial_reason = COALESCE(denial_reason,
                                                       'Slot removed from the ORBAT'),
                              updated_at = CURRENT_TIMESTAMP
                        WHERE status IN ('pending', 'approved')
                          AND slot_id IN (SELECT id FROM orbat_slots
                                           WHERE squad_id = ANY($1::int[]))""",
                    remove_squads,
                )
                await db.execute(
                    'DELETE FROM orbat_squads WHERE id = ANY($1::int[])', remove_squads
                )
            if remove_slots:
                # Take the people on those slots off the roster first. The
                # confirmation page promises exactly that, and `slot_id` carries
                # no foreign key — without this the request would survive as an
                # approved booking pointing at a slot that no longer exists,
                # invisible on every board.
                await db.execute(
                    """UPDATE requests
                          SET status = 'cancelled',
                              slot_id = NULL,
                              denial_reason = COALESCE(denial_reason,
                                                       'Slot removed from the ORBAT'),
                              updated_at = CURRENT_TIMESTAMP
                        WHERE slot_id = ANY($1::int[])
                          AND status IN ('pending', 'approved')""",
                    remove_slots,
                )
                await db.execute(
                    'DELETE FROM orbat_slots WHERE id = ANY($1::int[])', remove_slots
                )

            for order, squad in enumerate(parsed_squads):
                squad_id = squad_of_new.get(id(squad))
                values = (squad.name, squad.column, int(squad.exclude_from_count),
                          squad.reserved_unit, squad.radio, order)
                if squad_id:
                    await db.execute(
                        '''UPDATE orbat_squads
                              SET name = $2, column_side = $3, exclude_from_count = $4,
                                  reserved_unit = $5, radio = $6, sort_order = $7
                            WHERE id = $1''',
                        squad_id, *values,
                    )
                else:
                    row = await db.fetchrow(
                        '''INSERT INTO orbat_squads
                           (orbat_id, name, column_side, exclude_from_count,
                            reserved_unit, radio, sort_order)
                           VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id''',
                        orbat_id, *values,
                    )
                    squad_id = row['id']

                for slot_order, slot in enumerate(squad.slots):
                    slot_id = slot_of_new.get(id(slot))
                    fields = (slot.role_name, slot_order, squad_id)
                    if slot_id:
                        await db.execute(
                            '''UPDATE orbat_slots
                                  SET role_name = $2, sort_order = $3, squad_id = $4
                                WHERE id = $1''',
                            slot_id, *fields,
                        )
                    else:
                        await db.execute(
                            '''INSERT INTO orbat_slots
                               (role_name, sort_order, squad_id)
                               VALUES ($1, $2, $3)''',
                            *fields,
                        )

            await db.execute(
                '''UPDATE orbats SET updated_at = CURRENT_TIMESTAMP,
                                     source_text = COALESCE($2, source_text)
                   WHERE id = $1''',
                orbat_id, source_text,
            )


async def get_orbat_nets(orbat_id: int) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            'SELECT * FROM orbat_nets WHERE orbat_id = $1 ORDER BY sort_order, id',
            orbat_id,
        )


async def set_orbat_nets(orbat_id: int, nets: list, nets_text: str = None):
    """Replace the net list. Nothing hangs off a net, so unlike the squads and
    slots there is nothing to match up — the old rows go and the new ones land."""
    pool = await get_pool()
    async with pool.acquire() as db:
        async with db.transaction():
            await db.execute('DELETE FROM orbat_nets WHERE orbat_id = $1', orbat_id)
            for order, net in enumerate(nets):
                await db.execute(
                    '''INSERT INTO orbat_nets (orbat_id, name, channel, inactive, sort_order)
                       VALUES ($1, $2, $3, $4, $5)''',
                    orbat_id, net.name, net.channel, int(net.inactive), order,
                )
            await db.execute(
                '''UPDATE orbats SET nets_text = $2, updated_at = CURRENT_TIMESTAMP
                   WHERE id = $1''',
                orbat_id, nets_text,
            )


async def duplicate_orbat(orbat_id: int, name: str, created_by: str,
                          created_by_name: str) -> int:
    """Copy the structure, never the bookings — that is what a template is."""
    source = await get_orbat(orbat_id)
    squads = await get_orbat_structure(orbat_id)
    nets = await get_orbat_nets(orbat_id)
    new_id = await create_orbat(
        source['guild_id'], name, source['description'], created_by, created_by_name
    )
    pool = await get_pool()
    async with pool.acquire() as db:
        async with db.transaction():
            for squad in squads:
                row = await db.fetchrow(
                    '''INSERT INTO orbat_squads
                       (orbat_id, name, column_side, exclude_from_count,
                        reserved_unit, radio, sort_order)
                       VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id''',
                    new_id, squad['name'], squad['column_side'],
                    squad['exclude_from_count'], squad['reserved_unit'],
                    squad['radio'], squad['sort_order'],
                )
                for slot in squad['slots']:
                    await db.execute(
                        '''INSERT INTO orbat_slots (squad_id, role_name, sort_order)
                           VALUES ($1, $2, $3)''',
                        row['id'], slot['role_name'], slot['sort_order'],
                    )
            for order, net in enumerate(nets):
                await db.execute(
                    '''INSERT INTO orbat_nets (orbat_id, name, channel, inactive, sort_order)
                       VALUES ($1, $2, $3, $4, $5)''',
                    new_id, net['name'], net['channel'], net['inactive'], order,
                )
            await db.execute(
                'UPDATE orbats SET source_text = $2, nets_text = $3 WHERE id = $1',
                new_id, source['source_text'], source['nets_text'],
            )
    return new_id
