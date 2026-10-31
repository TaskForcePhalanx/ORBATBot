"""The database's shape, as a list of migrations.

`utils/database.py` holds the queries; this holds what they run against. A
migration is applied once and written down in `schema_version`, so a boot with
nothing to do costs **one SELECT** instead of the hundred-odd CREATE and ALTER
statements that building the schema from nothing takes. Those statements were
all idempotent and all re-ran on every start, which is a tenth of a second on
a local database and several seconds across a network — and, worse, meant
there was no way to say "this has already happened" about anything.

Two rules, and the second is the one that matters:

- **A migration is a numbered function taking a connection**, listed in
  `MIGRATIONS`, and it runs inside a transaction. Postgres has transactional
  DDL, so a migration that fails halfway leaves the database as it was.
- **A shipped migration is never edited.** Changing one changes nothing on any
  database that has already run it, so the two would silently disagree; a
  change to the schema is always a *new* migration. `tests/test_schema.py`
  checks the numbering, not this — nothing can.

**Migration 1 is the schema as it stood the day migrations arrived**, which is
the whole reason this was safe to introduce: it is the old `init_db()` body,
moved here unchanged. On a database that already has that schema every
statement in it is a no-op, exactly as it was on yesterday's boot, and it is
then recorded and never run again. On an empty one it builds everything. So
the first boot after this shipped behaved exactly like the boot before it.
"""

import logging

import asyncpg
from typing import Callable, NamedTuple

log = logging.getLogger(__name__)

# One arbitrary, fixed key, so two containers overlapping across a deploy
# cannot run the same migration twice. Any constant does; this one is
# 'orbatdb' read as digits.
_ADVISORY_LOCK = 6_714_288_241_503


class Migration(NamedTuple):
    version: int
    name: str
    run: Callable


async def migrate(db) -> list:
    """Apply every migration this database has not seen, and name them.

    Takes a connection rather than finding one, so a caller can hold it for
    the whole run — `utils/database.init_db()` is the only one.
    """
    done = await _applied(db)
    if done is not None and not _pending(done):
        return []
    # Railway can have the new container up before the old one is down, so
    # everything past here happens under an advisory lock: whoever arrives
    # second finds the table made and nothing left to do. The lock is why the
    # table is created here rather than above — `CREATE TABLE IF NOT EXISTS`
    # checks and then creates, and two sessions doing that at the same moment
    # is not a no-op for the loser but a duplicate-key error out of the
    # catalogue, which is a crashed boot. Tested; it really does happen.
    await db.execute('SELECT pg_advisory_lock($1)', _ADVISORY_LOCK)
    try:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC')
            )
        """)
        applied = []
        for migration in _pending(await _applied(db) or set()):
            log.info('database: applying migration %d — %s',
                     migration.version, migration.name)
            async with db.transaction():
                # A migration that cannot get its locks fails the boot rather
                # than holding every other query behind it; Railway restarts,
                # and by then the container it was waiting on has gone.
                await db.execute("SET LOCAL lock_timeout = '10s'")
                await migration.run(db)
                await db.execute(
                    'INSERT INTO schema_version (version, name) VALUES ($1, $2)',
                    migration.version, migration.name)
            applied.append(f'{migration.version} — {migration.name}')
        return applied
    finally:
        # Best-effort: a connection that has gone has dropped the lock anyway,
        # and failing in here would hide whatever failed above.
        try:
            await db.execute('SELECT pg_advisory_unlock($1)', _ADVISORY_LOCK)
        except Exception:  # pragma: no cover - the connection is already gone
            log.warning('database: could not release the migration lock')


async def _applied(db):
    """The versions this database has run, or None if it has never run one.

    One statement, which is what a boot with nothing to do costs. The table is
    missing exactly once in a database's life, so asking and being told is
    cheaper than checking for it every time.
    """
    try:
        rows = await db.fetch('SELECT version FROM schema_version')
    except asyncpg.UndefinedTableError:
        return None
    return {row['version'] for row in rows}


def _pending(done) -> list:
    return [m for m in MIGRATIONS if m.version not in done]


async def _v1_baseline(db):  # noqa: C901 — one table after another
    """The schema as it stood when migrations arrived: the old `init_db()`.

    Every statement is idempotent, because until this was recorded it ran on
    every boot. Do not edit it — see the note at the top of this file.
    """
    await db.execute('''
        CREATE TABLE IF NOT EXISTS operations (
            id SERIAL PRIMARY KEY,
            guild_id TEXT NOT NULL,
            name TEXT NOT NULL,
            sheet_url TEXT NOT NULL,
            sheet_id TEXT NOT NULL,
            squad_col INTEGER,
            role_col INTEGER,
            status_col INTEGER,
            assigned_col INTEGER,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    await db.execute('''
        CREATE TABLE IF NOT EXISTS requests (
            id SERIAL PRIMARY KEY,
            guild_id TEXT NOT NULL,
            operation_id INTEGER NOT NULL,
            member_id TEXT NOT NULL,
            member_name TEXT NOT NULL,
            slot_label TEXT NOT NULL,
            sheet_row INTEGER NOT NULL,
            sheet_col INTEGER,
            status TEXT DEFAULT 'pending',
            approval_message_id TEXT,
            approval_channel_id TEXT,
            approved_by TEXT,
            denial_reason TEXT,
            unit_role TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    await db.execute('''
        CREATE TABLE IF NOT EXISTS orbat_messages (
            guild_id TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # Reserved for a planned secondary "open slots" message. Nothing reads or
    # writes it today — the accessors were removed as dead code. Kept so an
    # existing deployment's table isn't orphaned; drop it here if the idea
    # is abandoned for good.
    await db.execute('''
        CREATE TABLE IF NOT EXISTS open_slots_messages (
            guild_id TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    await db.execute('''
        CREATE TABLE IF NOT EXISTS guild_settings (
            guild_id TEXT PRIMARY KEY,
            timezone TEXT NOT NULL DEFAULT 'UTC'
        )
    ''')
    await db.execute('''
        CREATE TABLE IF NOT EXISTS game_roles (
            id SERIAL PRIMARY KEY,
            guild_id TEXT NOT NULL,
            role_id TEXT NOT NULL,
            name TEXT NOT NULL,
            emoji TEXT,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (guild_id, role_id)
        )
    ''')
    await db.execute('''
        CREATE TABLE IF NOT EXISTS game_role_panels (
            guild_id TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    await db.execute('''
        CREATE TABLE IF NOT EXISTS events (
            id SERIAL PRIMARY KEY,
            guild_id TEXT NOT NULL,
            channel_id TEXT,
            message_id TEXT,
            title TEXT NOT NULL,
            description TEXT,
            event_time TIMESTAMP NOT NULL,
            duration_minutes INTEGER,
            location TEXT,
            image_url TEXT,
            mention_role_id TEXT,
            created_by TEXT NOT NULL,
            created_by_name TEXT,
            reminder_minutes INTEGER DEFAULT 30,
            reminder_fired INTEGER DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'scheduled',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    await db.execute('''
        CREATE TABLE IF NOT EXISTS event_signups (
            id SERIAL PRIMARY KEY,
            event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            member_id TEXT NOT NULL,
            member_name TEXT NOT NULL,
            response TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (event_id, member_id)
        )
    ''')
    await db.execute('''
        CREATE INDEX IF NOT EXISTS idx_events_guild_status
            ON events (guild_id, status, event_time)
    ''')
    # Per-event custom sign-up responses. No rows means the event uses the
    # built-in Accepted / Tentative / Declined set.
    await db.execute('''
        CREATE TABLE IF NOT EXISTS event_responses (
            id SERIAL PRIMARY KEY,
            event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
            key TEXT NOT NULL,
            label TEXT NOT NULL,
            emoji TEXT,
            is_decline INTEGER NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL DEFAULT 0,
            UNIQUE (event_id, key)
        )
    ''')
    # Reusable rich messages built in the web UI. `message_id` is NULL until
    # the embed is posted; keeping it lets an already-sent message be edited
    # in place rather than reposted.
    await db.execute('''
        CREATE TABLE IF NOT EXISTS embeds (
            id SERIAL PRIMARY KEY,
            guild_id TEXT NOT NULL,
            name TEXT NOT NULL,
            channel_id TEXT,
            message_id TEXT,
            content TEXT,
            title TEXT,
            description TEXT,
            url TEXT,
            color TEXT,
            author_name TEXT,
            author_icon_url TEXT,
            thumbnail_url TEXT,
            image_url TEXT,
            footer_text TEXT,
            footer_icon_url TEXT,
            show_timestamp INTEGER NOT NULL DEFAULT 0,
            created_by TEXT,
            created_by_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    await db.execute('''
        CREATE TABLE IF NOT EXISTS embed_fields (
            id SERIAL PRIMARY KEY,
            embed_id INTEGER NOT NULL REFERENCES embeds(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            value TEXT NOT NULL,
            inline INTEGER NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL DEFAULT 0
        )
    ''')
    # Where member joins/leaves/kicks/bans are announced, and which of them.
    await db.execute('''
        CREATE TABLE IF NOT EXISTS log_settings (
            guild_id TEXT PRIMARY KEY,
            channel_id TEXT,
            log_join INTEGER NOT NULL DEFAULT 1,
            log_leave INTEGER NOT NULL DEFAULT 1,
            log_kick INTEGER NOT NULL DEFAULT 1,
            log_ban INTEGER NOT NULL DEFAULT 1,
            log_unban INTEGER NOT NULL DEFAULT 1,
            track_invites INTEGER NOT NULL DEFAULT 1,
            welcome_channel_id TEXT,
            welcome_message TEXT,
            welcome_dm INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # Older deployments already have the table without the welcome columns.
    await db.execute('ALTER TABLE log_settings ADD COLUMN IF NOT EXISTS welcome_channel_id TEXT')
    await db.execute('ALTER TABLE log_settings ADD COLUMN IF NOT EXISTS welcome_message TEXT')
    await db.execute(
        'ALTER TABLE log_settings ADD COLUMN IF NOT EXISTS welcome_dm INTEGER NOT NULL DEFAULT 0'
    )
    # Where each invite link was published, so a join can say "came in through
    # the Steam group" instead of only naming a code.
    await db.execute('''
        CREATE TABLE IF NOT EXISTS invite_labels (
            guild_id TEXT NOT NULL,
            code TEXT NOT NULL,
            label TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (guild_id, code)
        )
    ''')
    # Time spent in voice channels. One row per counted interval: a member's
    # visit is split whenever the rules stop applying (they end up alone, or
    # move to an excluded channel), so the sum is time that actually counted.
    await db.execute('''
        CREATE TABLE IF NOT EXISTS voice_sessions (
            id SERIAL PRIMARY KEY,
            guild_id TEXT NOT NULL,
            member_id TEXT NOT NULL,
            member_name TEXT,
            channel_id TEXT NOT NULL,
            channel_name TEXT,
            started_at TIMESTAMP NOT NULL,
            heartbeat_at TIMESTAMP,
            ended_at TIMESTAMP,
            seconds INTEGER
        )
    ''')
    await db.execute('''
        CREATE INDEX IF NOT EXISTS idx_voice_guild_started
            ON voice_sessions (guild_id, started_at)
    ''')
    await db.execute('''
        CREATE INDEX IF NOT EXISTS idx_voice_open
            ON voice_sessions (guild_id) WHERE ended_at IS NULL
    ''')
    # ORBATs — the slot roster held here rather than read out of a Google
    # Sheet. A squad and a slot carry no booking of their own: who holds a
    # slot lives in `requests`, keyed by (operation_id, slot_id), which is
    # what makes an ORBAT a reusable template rather than one night's board.
    await db.execute('''
        CREATE TABLE IF NOT EXISTS orbats (
            id SERIAL PRIMARY KEY,
            guild_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            -- The text as its author typed it. The squads and slots below
            -- are the source of truth; this is kept alongside so comments,
            -- blank lines and their own spacing survive a reload, which
            -- regenerating the text from the structure would flatten.
            source_text TEXT,
            -- The net list as its author typed it, for the same reason
            -- source_text exists: regenerating it would flatten comments,
            -- blank lines and their own alignment.
            nets_text TEXT,
            created_by TEXT,
            created_by_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    await db.execute('''
        CREATE TABLE IF NOT EXISTS orbat_squads (
            id SERIAL PRIMARY KEY,
            orbat_id INTEGER NOT NULL REFERENCES orbats(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            column_side INTEGER NOT NULL DEFAULT 0,
            exclude_from_count INTEGER NOT NULL DEFAULT 0,
            reserved_unit TEXT,
            radio TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0
        )
    ''')
    await db.execute('''
        CREATE TABLE IF NOT EXISTS orbat_slots (
            id SERIAL PRIMARY KEY,
            squad_id INTEGER NOT NULL REFERENCES orbat_squads(id) ON DELETE CASCADE,
            role_name TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0
        )
    ''')
    # The long-range nets the whole operation shares, as against the
    # short-range channel on orbat_squads.radio. A flat list with no
    # identity of its own, so a save replaces it wholesale.
    await db.execute('''
        CREATE TABLE IF NOT EXISTS orbat_nets (
            id SERIAL PRIMARY KEY,
            orbat_id INTEGER NOT NULL REFERENCES orbats(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            channel TEXT,
            inactive INTEGER NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL DEFAULT 0
        )
    ''')
    await db.execute('''
        CREATE INDEX IF NOT EXISTS idx_orbat_nets
            ON orbat_nets (orbat_id, sort_order)
    ''')
    await db.execute('''
        CREATE INDEX IF NOT EXISTS idx_orbat_squads
            ON orbat_squads (orbat_id, sort_order)
    ''')
    await db.execute('''
        CREATE INDEX IF NOT EXISTS idx_orbat_slots
            ON orbat_slots (squad_id, sort_order)
    ''')
    await db.execute('''
        CREATE TABLE IF NOT EXISTS voice_settings (
            guild_id TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 0,
            channel_id TEXT,
            min_log_minutes INTEGER NOT NULL DEFAULT 5,
            count_afk INTEGER NOT NULL DEFAULT 0,
            count_solo INTEGER NOT NULL DEFAULT 0,
            excluded_channels TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # The self-updating top-10 board, added after voice tracking shipped.
    for column, definition in (
        ('board_enabled', 'INTEGER NOT NULL DEFAULT 0'),
        ('board_channel_id', 'TEXT'),
        ('board_message_id', 'TEXT'),
        ('board_period', "TEXT NOT NULL DEFAULT '7'"),
        ('board_hour', 'INTEGER NOT NULL DEFAULT 9'),
        ('board_updated_on', 'DATE'),
    ):
        await db.execute(
            f'ALTER TABLE voice_settings ADD COLUMN IF NOT EXISTS {column} {definition}'
        )
    # Reddit feeds watched for new posts. One row is one watch: where it
    # points, which channel it announces in, and the text it announces with.
    # `seen_ids` being NULL is what tells the first read apart from every
    # later one — a fresh watch records what is already there and announces
    # nothing, instead of dumping the author's last 25 posts into a channel.
    await db.execute('''
        CREATE TABLE IF NOT EXISTS reddit_feeds (
            id SERIAL PRIMARY KEY,
            guild_id TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'user',
            source TEXT NOT NULL,
            channel_id TEXT,
            template TEXT,
            author_filter TEXT,
            mention_role_id TEXT,
            mention_user_id TEXT,
            enabled INTEGER NOT NULL DEFAULT 1,
            seen_ids TEXT,
            last_checked_at TIMESTAMP,
            last_post_at TIMESTAMP,
            last_error TEXT,
            retry_at TIMESTAMP,
            created_by TEXT,
            created_by_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # One watch per source per guild: two rows for the same author would
    # announce every post twice, which reads as the bot being broken.
    await db.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_reddit_feed_source
            ON reddit_feeds (guild_id, kind, lower(source))
    ''')
    # Don't read this watch again before this time. Added after the table
    # shipped, when Reddit turned out to refuse a hosting provider's address
    # outright — asking again every five minutes is what keeps a throttle
    # warm, so a refusal now stands the watch down instead.
    await db.execute('''
        ALTER TABLE reddit_feeds ADD COLUMN IF NOT EXISTS retry_at TIMESTAMP
    ''')
    # Announce only these accounts' posts. Added when an account whose
    # profile hides its posts turned out to be reachable through the
    # subreddit it posts in, which lists them whatever the profile says.
    await db.execute('''
        ALTER TABLE reddit_feeds ADD COLUMN IF NOT EXISTS author_filter TEXT
    ''')
    # One tactical map is one document — see utils/tacmap.py. It is held as
    # JSON rather than a table of items because nothing hangs off a symbol:
    # a save replaces the whole plan, the same reasoning as orbat_nets.
    await db.execute('''
        CREATE TABLE IF NOT EXISTS tac_maps (
            id SERIAL PRIMARY KEY,
            guild_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            doc TEXT NOT NULL,
            share_token TEXT,
            share_mode TEXT NOT NULL DEFAULT 'off',
            channel_id TEXT,
            message_id TEXT,
            created_by TEXT,
            created_by_name TEXT,
            updated_by_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # The share link is looked up by its token on every anonymous request,
    # and two maps must never answer to the same one.
    await db.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_tac_map_token
            ON tac_maps (share_token) WHERE share_token IS NOT NULL
    ''')
    # Bumped by every write of the document, so a save made from an editor
    # opened before somebody else's save is refused rather than quietly
    # putting the older plan back over theirs.
    await db.execute(
        'ALTER TABLE tac_maps ADD COLUMN IF NOT EXISTS doc_rev INTEGER NOT NULL DEFAULT 0')
    # The game's own marker pictures, uploaded per guild. Bohemia's
    # artwork, so kept here rather than in the (public) repository, and
    # served only to the guild's members and share-link holders.
    await db.execute('''
        CREATE TABLE IF NOT EXISTS tac_marker_icons (
            guild_id TEXT NOT NULL,
            key TEXT NOT NULL,
            image BYTEA NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (guild_id, key)
        )
    ''')
    # The first release stored the military pictures under the marker's
    # old name (`dot`); every picture is keyed by its Arma class now.
    await db.execute('''
        UPDATE tac_marker_icons SET key = 'mil_' || key
         WHERE key IN ('dot', 'circle', 'box', 'triangle', 'objective', 'destroy',
                       'warning', 'unknown', 'flag', 'arrow', 'arrow2', 'ambush',
                       'start', 'end', 'pickup', 'join', 'marker')
    ''')
    # A terrain uploaded as a tile archive, and served back by this bot —
    # see utils/tiles.py. The tiles live here rather than on disk because
    # the container's filesystem does not survive a redeploy, and a map
    # whose background disappears on every deploy is worse than none.
    await db.execute('''
        CREATE TABLE IF NOT EXISTS tac_terrains (
            id SERIAL PRIMARY KEY,
            guild_id TEXT NOT NULL,
            name TEXT NOT NULL,
            world_size DOUBLE PRECISION NOT NULL,
            max_zoom INTEGER NOT NULL,
            tile_count INTEGER NOT NULL DEFAULT 0,
            bytes BIGINT NOT NULL DEFAULT 0,
            created_by TEXT,
            created_by_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    await db.execute('''
        CREATE TABLE IF NOT EXISTS tac_terrain_tiles (
            terrain_id INTEGER NOT NULL
                REFERENCES tac_terrains (id) ON DELETE CASCADE,
            zoom INTEGER NOT NULL,
            x INTEGER NOT NULL,
            y INTEGER NOT NULL,
            image BYTEA NOT NULL,
            PRIMARY KEY (terrain_id, zoom, x, y)
        )
    ''')
    # Recurrence, added after the events tables shipped
    await db.execute('''
        ALTER TABLE events ADD COLUMN IF NOT EXISTS recurrence TEXT
    ''')
    await db.execute('''
        ALTER TABLE events ADD COLUMN IF NOT EXISTS recurrence_until TIMESTAMP
    ''')
    # The first occurrence's start time, carried unchanged down the series so
    # monthly repeats keep anchoring on the original day instead of drifting
    # once a short month clamps the date.
    await db.execute('''
        ALTER TABLE events ADD COLUMN IF NOT EXISTS recurrence_anchor TIMESTAMP
    ''')
    # How long after an occurrence ends before the next one is posted.
    # NULL/0 keeps the original behaviour: the handover happens the moment
    # this occurrence is marked completed.
    await db.execute('''
        ALTER TABLE events ADD COLUMN IF NOT EXISTS recurrence_delay_hours INTEGER
    ''')
    # Whether this completed event's handover (spawning its successor) has
    # already run, so the delayed handover step doesn't repeat it every
    # tick. A row that was already completed before this column existed
    # already had its handover done synchronously at completion time under
    # the old code, so it is backfilled as fired here — only an event
    # completed after this column exists should start at 0.
    handover_column_existed = await db.fetchval(
        """SELECT 1 FROM information_schema.columns
            WHERE table_name = 'events' AND column_name = 'handover_fired'"""
    )
    await db.execute('''
        ALTER TABLE events ADD COLUMN IF NOT EXISTS handover_fired INTEGER DEFAULT 0
    ''')
    if not handover_column_existed:
        await db.execute("UPDATE events SET handover_fired = 1 WHERE status = 'completed'")
    # Add event scheduling columns to existing operations tables
    await db.execute('''
        ALTER TABLE operations ADD COLUMN IF NOT EXISTS
            event_time TIMESTAMP
    ''')
    await db.execute('''
        ALTER TABLE operations ADD COLUMN IF NOT EXISTS
            reminder_minutes INTEGER DEFAULT 30
    ''')
    await db.execute('''
        ALTER TABLE operations ADD COLUMN IF NOT EXISTS
            reminder_fired INTEGER DEFAULT 0
    ''')
    # The unit an ORBAT squad belongs to. It started out on the slot, which
    # meant repeating the same tag on every line of a squad that belongs to
    # one unit as a whole; these three statements lift any values already
    # entered up to their squad and then retire the slot column. All three
    # are no-ops once they have run.
    await db.execute('''
        ALTER TABLE orbat_squads ADD COLUMN IF NOT EXISTS reserved_unit TEXT
    ''')
    if await db.fetchval(
        """SELECT 1 FROM information_schema.columns
            WHERE table_name = 'orbat_slots' AND column_name = 'reserved_unit'"""
    ):
        # The squad takes the first unit any of its slots named, in slot
        # order — with one unit per squad there is nothing to choose between.
        await db.execute('''
            UPDATE orbat_squads q SET reserved_unit = (
                SELECT s.reserved_unit FROM orbat_slots s
                 WHERE s.squad_id = q.id AND s.reserved_unit IS NOT NULL
                 ORDER BY s.sort_order, s.id LIMIT 1)
             WHERE q.reserved_unit IS NULL
        ''')
        await db.execute('ALTER TABLE orbat_slots DROP COLUMN reserved_unit')

    # The squad's own radio channel and the shared net list, both added
    # after the ORBAT tables shipped.
    await db.execute('''
        ALTER TABLE orbat_squads ADD COLUMN IF NOT EXISTS radio TEXT
    ''')
    await db.execute('''
        ALTER TABLE orbats ADD COLUMN IF NOT EXISTS nets_text TEXT
    ''')
    # An operation is backed either by a Google Sheet or by an ORBAT held
    # here. Both stay possible: the sheet columns lose their NOT NULL so a
    # DB-backed operation can exist without one, and orbat_id is NULL on
    # every sheet-backed operation.
    await db.execute('''
        ALTER TABLE operations ADD COLUMN IF NOT EXISTS orbat_id INTEGER
    ''')
    await db.execute('ALTER TABLE operations ALTER COLUMN sheet_url DROP NOT NULL')
    await db.execute('ALTER TABLE operations ALTER COLUMN sheet_id DROP NOT NULL')
    # A request against an ORBAT slot has no sheet row.
    await db.execute('ALTER TABLE requests ALTER COLUMN sheet_row DROP NOT NULL')

    # Which ORBAT slot a request is for. NULL on every sheet-backed request,
    # which is all of them until slots can be booked from the board — the
    # column exists now so an ORBAT knows who its slots are promised to.
    await db.execute('''
        ALTER TABLE requests ADD COLUMN IF NOT EXISTS slot_id INTEGER
    ''')

    # Where the bot posts. NULL on every column means "the channel called
    # #orbat / #slot-approvals / #approval-archive, created if it is
    # missing" — which is what every guild did before these existed, so an
    # upgrade changes nothing until an admin picks something else.
    for column in ('orbat_channel_id', 'approvals_channel_id',
                   'archive_channel_id'):
        await db.execute(
            f'ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS {column} TEXT'
        )
    # The OCAP map directory this guild's terrains are served from, so a
    # map is picked from a list instead of its world name being typed out
    # from memory. NULL means nobody has said where it is yet.
    await db.execute(
        'ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS ocap_base_url TEXT'
    )

    # The terrain's own place names, as JSON — see the place-names section
    # of utils/tacmap.py. They hang off the terrain rather than off a map
    # because every plan drawn on Tanoa wants the same ones, and they are
    # one column rather than a table for the same reason orbat_nets is
    # replaced wholesale: nothing hangs off a place name, so there is no
    # identity for an edit to lose.
    await db.execute(
        'ALTER TABLE tac_terrains ADD COLUMN IF NOT EXISTS places TEXT'
    )

    # Place names, filed under a terrain rather than a map — see
    # `tacmap.place_scope()`. This exists beside `tac_terrains.places`
    # because half the terrains have no row here at all: a map drawn on an
    # OCAP server points at somebody else's folder, and that folder's world
    # name is the only identity it has. One table keyed on that string
    # serves both, so a map gains its names from whichever import reached
    # them first.
    await db.execute('''
        CREATE TABLE IF NOT EXISTS tac_places (
            guild_id TEXT NOT NULL,
            scope TEXT NOT NULL,
            label TEXT,
            places TEXT NOT NULL,
            source TEXT,
            updated_by_name TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (guild_id, scope)
        )
    ''')
    # Anything already pasted onto an uploaded terrain moves across, so the
    # one release that stored them there does not strand them.
    await db.execute('''
        INSERT INTO tac_places (guild_id, scope, label, places, source)
            SELECT guild_id, 't:' || id, name, places, 'terrain'
              FROM tac_terrains
             WHERE places IS NOT NULL AND places <> ''
        ON CONFLICT (guild_id, scope) DO NOTHING
    ''')
    # Kill Tracker: one row per OCAP recording, holding the per-player rows
    # counted from it — see utils/killtracker.py. It is not scoped to a
    # guild, because the OCAP server it reads is the unit's, not Discord's.
    # `rows` is a JSON document replaced wholesale on every re-count, for the
    # reason orbat_nets and tac_maps.doc are: nothing hangs off a row.
    # `rules_version` is what lets a rule change re-count old missions by
    # itself; `status` and `retry_at` stop one corrupt file being fetched
    # every five minutes for ever.
    await db.execute('''
        CREATE TABLE IF NOT EXISTS kt_missions (
            ocap_id INTEGER PRIMARY KEY,
            filename TEXT NOT NULL UNIQUE,
            mission TEXT,
            label TEXT,
            world TEXT,
            tag TEXT,
            played_on TEXT,
            duration INTEGER,
            ocap_kills INTEGER,
            ocap_player_kills INTEGER,
            counted_kills INTEGER,
            rows TEXT NOT NULL DEFAULT '[]',
            rules_version INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'ok',
            error TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            retry_at TIMESTAMP,
            sha256 TEXT,
            imported_at TIMESTAMP
        )
    ''')
    # The name an admin gave the mission, shown instead of the one OCAP has.
    # Held beside `rows` and not in them: a re-count replaces the rows, and a
    # name somebody typed must survive that. Applied when the CSV is served.
    await db.execute('''
        ALTER TABLE kt_missions ADD COLUMN IF NOT EXISTS name_override TEXT
    ''')
    # The farthest kills of the mission that feed the distance statistics, as
    # JSON (`killtracker.compute()['far']`), so an admin can check them without
    # the recording. Replaced on every re-count, like `rows`.
    await db.execute('''
        ALTER TABLE kt_missions ADD COLUMN IF NOT EXISTS far_kills TEXT NOT NULL DEFAULT '[]'
    ''')
    # Kills an admin struck from the distance statistics: the kill still counts,
    # its distance does not. Keyed by the kill inside the recording
    # (`killtracker.kill_key()`), kept beside the mission so a re-count applies it.
    # The description is stored too, so the list can say what was struck even
    # after a re-count has moved on.
    await db.execute('''
        CREATE TABLE IF NOT EXISTS kt_kill_exclusions (
            ocap_id INTEGER NOT NULL REFERENCES kt_missions(ocap_id) ON DELETE CASCADE,
            kill_key TEXT NOT NULL,
            killer TEXT, victim TEXT, weapon TEXT, distance DOUBLE PRECISION,
            struck_by_name TEXT,
            struck_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
            PRIMARY KEY (ocap_id, kill_key)
        )
    ''')
    # Campaigns: a named group of missions. Membership is by recording id, so
    # renaming a mission moves it in every campaign; `extra_missions` holds
    # labels imported from the old campaigns.json that matched no recording
    # (yet), and `patterns` is the old substring matching, as a JSON list.
    await db.execute('''
        CREATE TABLE IF NOT EXISTS kt_campaigns (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            patterns TEXT NOT NULL DEFAULT '[]',
            extra_missions TEXT NOT NULL DEFAULT '[]',
            updated_by_name TEXT,
            updated_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC')
        )
    ''')
    await db.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_kt_campaign_name
            ON kt_campaigns (lower(name))
    ''')
    await db.execute('''
        CREATE TABLE IF NOT EXISTS kt_campaign_missions (
            campaign_id INTEGER NOT NULL REFERENCES kt_campaigns(id) ON DELETE CASCADE,
            ocap_id INTEGER NOT NULL REFERENCES kt_missions(ocap_id) ON DELETE CASCADE,
            PRIMARY KEY (campaign_id, ocap_id)
        )
    ''')
    # Names that belong to one player, `alias` → the name shown — the shape of the
    # dashboard's player_aliases.json, which the dashboard applies on top of its
    # own automatic merging. Never a chain: `killtracker.link_names()` keeps it flat.
    await db.execute('''
        CREATE TABLE IF NOT EXISTS kt_aliases (
            alias TEXT PRIMARY KEY,
            canonical TEXT NOT NULL
        )
    ''')
    # Which unit a player is in, where the dashboard's guess is wrong. '' is
    # "Unknown" (the file's `null`); no row at all is "automatic".
    await db.execute('''
        CREATE TABLE IF NOT EXISTS kt_unit_overrides (
            player TEXT PRIMARY KEY,
            unit TEXT NOT NULL DEFAULT ''
        )
    ''')
    await _init_campaign_planner(db)


async def _init_campaign_planner(db):  # noqa: C901 — one table after another
    """The campaign planner's tables (`cp_`). Created after everything they
    point at — Kill Tracker campaigns, tactical maps, ORBATs, operations.

    A campaign is three to five missions. Each mission's wizard fields live in
    one JSON document (`cp_missions.fields`), keyed as in `utils/campaign.STEPS`
    — they are only ever read and written whole, and the rules that read them
    are in `utils/campaign.py`. Everything the bot has to query or schedule
    (dates, drops, links to other features) is a real column.
    """
    # One row per guild. NULL means "not set"; utils.campaign supplies defaults.
    await db.execute('''
        CREATE TABLE IF NOT EXISTS cp_settings (
            guild_id TEXT PRIMARY KEY,
            unit_role_id TEXT,
            drop_channel_id TEXT,
            team_channel_id TEXT,
            drop_weekday INTEGER,
            drop_time TEXT,
            orbat_opens TEXT,
            survey_url TEXT,
            library_seeded BOOLEAN NOT NULL DEFAULT FALSE,
            orbat_access TEXT,
            orbat_role_ids TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # Who may edit mission ORBATs: 'admins' / 'planners' (NULL = planners), and
    # extra roles, comma-separated — see utils.campaign.may_edit_orbats().
    for column in ('orbat_access', 'orbat_role_ids'):
        await db.execute(f'ALTER TABLE cp_settings ADD COLUMN IF NOT EXISTS {column} TEXT')
    # Whether a campaign whose missions are all over marks itself Complete.
    # NULL = yes — see utils.campaign.settings_with_defaults().
    await db.execute('ALTER TABLE cp_settings ADD COLUMN IF NOT EXISTS auto_complete BOOLEAN')
    await db.execute('''
        ALTER TABLE cp_settings ADD COLUMN IF NOT EXISTS
            library_seeded BOOLEAN NOT NULL DEFAULT FALSE
    ''')
    # The campaign record. `kt_campaign_id` ties it to the Kill Tracker's
    # campaign of the same name, so after-action stats come from one place.
    await db.execute('''
        CREATE TABLE IF NOT EXISTS cp_campaigns (
            id SERIAL PRIMARY KEY,
            guild_id TEXT NOT NULL,
            name TEXT NOT NULL,
            era TEXT NOT NULL DEFAULT '',
            theatre TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            planned_missions INTEGER NOT NULL DEFAULT 4,
            status TEXT NOT NULL DEFAULT 'active',
            is_test BOOLEAN NOT NULL DEFAULT FALSE,
            kt_campaign_id INTEGER REFERENCES kt_campaigns(id) ON DELETE SET NULL,
            kt_created BOOLEAN NOT NULL DEFAULT FALSE,
            map_id INTEGER REFERENCES tac_maps(id) ON DELETE SET NULL,
            created_by TEXT,
            created_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC'),
            updated_by_name TEXT,
            updated_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC')
        )
    ''')
    # Milestone 1 shipped `archived`; milestone 2 replaced it with `status`
    # before anything was stored, so it simply goes.
    for column, definition in (('era', "TEXT NOT NULL DEFAULT ''"),
                               ('theatre', "TEXT NOT NULL DEFAULT ''"),
                               ('planned_missions', 'INTEGER NOT NULL DEFAULT 4'),
                               ('status', "TEXT NOT NULL DEFAULT 'active'"),
                               # A test campaign has no Kill Tracker campaign and,
                               # from milestone 4, never posts publicly.
                               ('is_test', 'BOOLEAN NOT NULL DEFAULT FALSE'),
                               # Whether the planner made its Kill Tracker campaign
                               # (rather than linking one that existed) — the only
                               # kind a delete may take with it.
                               ('kt_created', 'BOOLEAN NOT NULL DEFAULT FALSE'),
                               # When the bot marked it Complete by itself, or a
                               # person reopened it after it was closed. Set, the
                               # bot leaves it alone until a mission is added.
                               ('auto_completed_at', 'TIMESTAMP')):
        await db.execute(
            f'ALTER TABLE cp_campaigns ADD COLUMN IF NOT EXISTS {column} {definition}')
    await db.execute('ALTER TABLE cp_campaigns DROP COLUMN IF EXISTS archived')
    await db.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_cp_campaign_name
            ON cp_campaigns (guild_id, lower(name))
    ''')
    # Areas of operations: one region of the campaign map, each with its own
    # detailed tactical map. Phases are layers on that map.
    await db.execute('''
        CREATE TABLE IF NOT EXISTS cp_aos (
            id SERIAL PRIMARY KEY,
            campaign_id INTEGER NOT NULL REFERENCES cp_campaigns(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            area TEXT,
            map_id INTEGER REFERENCES tac_maps(id) ON DELETE SET NULL
        )
    ''')
    await db.execute('''
        CREATE TABLE IF NOT EXISTS cp_missions (
            id SERIAL PRIMARY KEY,
            campaign_id INTEGER NOT NULL REFERENCES cp_campaigns(id) ON DELETE CASCADE,
            position INTEGER NOT NULL DEFAULT 0,
            name TEXT NOT NULL,
            op_date DATE,
            fields TEXT NOT NULL DEFAULT '{}',
            orbat_id INTEGER REFERENCES orbats(id) ON DELETE SET NULL,
            operation_id INTEGER REFERENCES operations(id) ON DELETE SET NULL,
            updated_by_name TEXT,
            updated_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC')
        )
    ''')
    await db.execute('''
        CREATE INDEX IF NOT EXISTS idx_cp_missions_campaign
            ON cp_missions (campaign_id, position)
    ''')
    await db.execute('''
        CREATE TABLE IF NOT EXISTS cp_mission_aos (
            mission_id INTEGER NOT NULL REFERENCES cp_missions(id) ON DELETE CASCADE,
            ao_id INTEGER NOT NULL REFERENCES cp_aos(id) ON DELETE CASCADE,
            PRIMARY KEY (mission_id, ao_id)
        )
    ''')
    # A mission phase, drawn as one layer (`layer_id`, a layer of the AO's map).
    await db.execute('''
        CREATE TABLE IF NOT EXISTS cp_phases (
            id SERIAL PRIMARY KEY,
            mission_id INTEGER NOT NULL REFERENCES cp_missions(id) ON DELETE CASCADE,
            position INTEGER NOT NULL DEFAULT 0,
            title TEXT NOT NULL DEFAULT '',
            detail TEXT NOT NULL DEFAULT '',
            ao_id INTEGER REFERENCES cp_aos(id) ON DELETE SET NULL,
            layer_id TEXT
        )
    ''')
    # The objectives library, seeded per guild from utils/objectives_seed.py
    # and admin-edited after that. `pairs_with` is the free text people read
    # ("Counter-attack, Defend"); utils.campaign.pairing_ids() turns it into
    # the picker's suggestions — never a rule.
    await db.execute('''
        CREATE TABLE IF NOT EXISTS cp_objective_library (
            id SERIAL PRIMARY KEY,
            guild_id TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL DEFAULT 'objective',
            name TEXT NOT NULL,
            players_do TEXT NOT NULL DEFAULT '',
            complete_when TEXT NOT NULL DEFAULT '',
            pairs_with TEXT NOT NULL DEFAULT '',
            intsum_hint TEXT NOT NULL DEFAULT '',
            position INTEGER NOT NULL DEFAULT 0
        )
    ''')
    # An objective chosen for a phase, with its own copy of the wording — so
    # editing it in a mission never changes the library, or the reverse.
    await db.execute('''
        CREATE TABLE IF NOT EXISTS cp_phase_objectives (
            id SERIAL PRIMARY KEY,
            phase_id INTEGER NOT NULL REFERENCES cp_phases(id) ON DELETE CASCADE,
            library_id INTEGER REFERENCES cp_objective_library(id) ON DELETE SET NULL,
            position INTEGER NOT NULL DEFAULT 0,
            role TEXT NOT NULL DEFAULT 'primary',
            name TEXT NOT NULL,
            players_do TEXT NOT NULL DEFAULT '',
            complete_when TEXT NOT NULL DEFAULT '',
            intsum_hint TEXT NOT NULL DEFAULT '',
            result TEXT NOT NULL DEFAULT ''
        )
    ''')
    # Milestone 1's shapes for these two, replaced before anything was stored.
    for table, columns in (
            ('cp_objective_library', ('kind', 'players_do', 'complete_when', 'intsum_hint')),
            ('cp_phase_objectives', ('role', 'players_do', 'complete_when',
                                     'intsum_hint', 'result'))):
        for column in columns:
            default = "'objective'" if column == 'kind' else (
                "'primary'" if column == 'role' else "''")
            await db.execute(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS '
                             f'{column} TEXT NOT NULL DEFAULT {default}')
        await db.execute(f'ALTER TABLE {table} DROP COLUMN IF EXISTS wording')
    await db.execute("ALTER TABLE cp_objective_library ALTER COLUMN pairs_with SET DEFAULT ''")
    await db.execute('''
        CREATE INDEX IF NOT EXISTS idx_cp_library_guild
            ON cp_objective_library (guild_id, position)
    ''')
    # One row per drop per mission. `due_at` is naive UTC. `approved_hash` is
    # utils.campaign.approval_hash() of what was approved; when what the drop
    # would post now hashes differently, the approval no longer counts.
    # `message_ids` is a JSON list — one per part of a split post.
    await db.execute('''
        CREATE TABLE IF NOT EXISTS cp_drops (
            mission_id INTEGER NOT NULL REFERENCES cp_missions(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            due_at TIMESTAMP,
            approved_hash TEXT,
            approved_by TEXT,
            approved_by_name TEXT,
            approved_at TIMESTAMP,
            held_pinged_at TIMESTAMP,
            posted_at TIMESTAMP,
            channel_id TEXT,
            message_ids TEXT NOT NULL DEFAULT '[]',
            posted_hash TEXT,
            PRIMARY KEY (mission_id, kind)
        )
    ''')
    await db.execute('''
        CREATE INDEX IF NOT EXISTS idx_cp_drops_due
            ON cp_drops (due_at) WHERE posted_at IS NULL
    ''')
    await db.execute('''
        CREATE TABLE IF NOT EXISTS cp_images (
            id SERIAL PRIMARY KEY,
            campaign_id INTEGER NOT NULL REFERENCES cp_campaigns(id) ON DELETE CASCADE,
            filename TEXT NOT NULL,
            content_type TEXT NOT NULL,
            data BYTEA NOT NULL,
            bytes INTEGER NOT NULL DEFAULT 0,
            created_by_name TEXT,
            created_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC')
        )
    ''')
    await db.execute('ALTER TABLE cp_images ADD COLUMN IF NOT EXISTS bytes INTEGER NOT NULL DEFAULT 0')
    await db.execute('ALTER TABLE cp_images ADD COLUMN IF NOT EXISTS created_by_name TEXT')
    # Milestone 4: a drop somebody chose not to post at all (posted by hand,
    # or a WARNO for a mission planned too late for one).
    await db.execute('ALTER TABLE cp_drops ADD COLUMN IF NOT EXISTS skipped INTEGER NOT NULL DEFAULT 0')
    await db.execute('ALTER TABLE cp_drops ADD COLUMN IF NOT EXISTS skipped_by_name TEXT')
    # Milestone 5: an ORBAT the planner's card created (New or Copy) rather
    # than linked — the only kind deleting a test mission may take with it.
    await db.execute('ALTER TABLE cp_missions ADD COLUMN IF NOT EXISTS orbat_created '
                     'BOOLEAN NOT NULL DEFAULT FALSE')
    # Milestone 6: the OCAP recording that is this mission. `ocap_manual`
    # says a person chose it (or chose none), so the loop stops guessing;
    # `ocap_kt_added` says the planner put it in the Kill Tracker campaign,
    # which is the only case in which unlinking may take it out again.
    await db.execute('ALTER TABLE cp_missions ADD COLUMN IF NOT EXISTS ocap_id INTEGER '
                     'REFERENCES kt_missions(ocap_id) ON DELETE SET NULL')
    await db.execute('ALTER TABLE cp_missions ADD COLUMN IF NOT EXISTS ocap_manual '
                     'BOOLEAN NOT NULL DEFAULT FALSE')
    await db.execute('ALTER TABLE cp_missions ADD COLUMN IF NOT EXISTS ocap_kt_added '
                     'BOOLEAN NOT NULL DEFAULT FALSE')


async def _v2_guild_indexes(db):
    """Three lookups that had no index behind them.

    All five `requests` reads of an operation filter on its status too; a
    guild's live operation is found by `guild_id` and `is_active`, and the
    ORBAT list runs one such lookup per row to say which ORBAT is live; the
    maps page lists by guild. None of these tables is large yet, which is why
    it was never noticed — `requests` is the one that grows with every
    operation ever run.
    """
    await db.execute('CREATE INDEX IF NOT EXISTS idx_requests_operation '
                     'ON requests (operation_id, status)')
    await db.execute('CREATE INDEX IF NOT EXISTS idx_operations_guild_active '
                     'ON operations (guild_id, is_active)')
    await db.execute('CREATE INDEX IF NOT EXISTS idx_operations_orbat '
                     'ON operations (orbat_id)')
    await db.execute('CREATE INDEX IF NOT EXISTS idx_tac_maps_guild '
                     'ON tac_maps (guild_id)')


async def _v3_tac_maps_doc_jsonb(db):
    """`tac_maps.doc` becomes `jsonb`, so the column can be asked questions.

    It held the whole map as **text**, which meant the one query that has to
    search inside it did so with a substring match:

        WHERE doc LIKE '%' || '"/t/7"' || '%'

    That is `tac_terrain_usage()`, and it decides whether deleting a terrain is
    refused — so it is the thing standing between an admin and every plan drawn
    on that terrain rendering on an empty sheet. Two problems with asking it
    that way. A leading wildcard cannot use an index, so it reads every map in
    the table. And it matches the characters rather than the structure: a
    *label* somebody typed containing `"/t/7"` counts as usage, while a
    background stored any other way would not count at all. As `jsonb` the
    query asks what it means — `doc->'background'->>'url' = '/t/7'` — and the
    index below answers it.

    **Converting a column the whole product keeps its work in is the risk
    here, so the two ways the cast can fail are dealt with before it runs.**

    `jsonb` refuses `\u0000` ("unsupported Unicode escape sequence") where
    `text` and Python's `json` both accept it. A label is typed by anybody with
    an edit link and `utils/tacmap._text()` did not strip control characters
    until this shipped, so a stored map may carry one — and a single such row
    would fail this migration, which fails the boot, which takes the bot down
    for everybody. Those rows are repaired first; `_text()` is what stops new
    ones.

    Anything *still* not valid JSON afterwards is not a map: `tacmap.parse()`
    cannot read it either, so that map already opens empty. Its text is moved
    to `doc_unreadable` and `'{}'` put in its place, which loses nothing that
    worked and keeps the column convertible. Blanking it without keeping the
    original would be destroying somebody's plan on the strength of a guess.
    """
    # The escape, not the character: json.dumps() writes a NUL as the six
    # characters \u0000, and that is what the column holds.
    await db.execute(r"""UPDATE tac_maps SET doc = replace(doc, '\u0000', '')
                          WHERE doc LIKE '%\u0000%'""")

    await db.execute('ALTER TABLE tac_maps ADD COLUMN IF NOT EXISTS doc_unreadable TEXT')

    # Per row, and only the ones that fail: `pg_input_is_valid()` would say the
    # same thing in one statement but arrived in PostgreSQL 16, and a migration
    # should not be the thing that decides the minimum server version.
    #
    # **It reports through `log`, not `RAISE WARNING`.** It did raise one, and
    # that warning reached nobody: asyncpg drops server notices unless a
    # connection has `add_log_listener()` attached, and `init_db()` attaches
    # none — checked against a real server, where the same `RAISE WARNING` is
    # invisible without a listener and arrives as `PostgresWarning:` with one.
    # Setting a map's text aside rather than failing the boot is only
    # defensible if somebody finds out, so the ids come back and are logged
    # here.
    await db.execute('''
        DO $$
        DECLARE r RECORD;
        BEGIN
          FOR r IN SELECT id, doc FROM tac_maps WHERE doc_unreadable IS NULL LOOP
            BEGIN
              PERFORM r.doc::jsonb;
            EXCEPTION WHEN others THEN
              UPDATE tac_maps SET doc_unreadable = r.doc, doc = '{}' WHERE id = r.id;
            END;
          END LOOP;
        END $$;
    ''')
    setaside = await db.fetch('SELECT id, guild_id, name FROM tac_maps '
                              'WHERE doc_unreadable IS NOT NULL ORDER BY id')
    if setaside:
        log.warning(
            '⚠️ migration 3: %d map(s) held a document that was not valid JSON. '
            'The text is kept in tac_maps.doc_unreadable and the map opens '
            'empty, which is what it already did — tacmap.parse() could not '
            'read it either. Maps: %s',
            len(setaside),
            ', '.join('#%s %r (guild %s)' % (r['id'], r['name'], r['guild_id'])
                      for r in setaside))

    await db.execute('ALTER TABLE tac_maps ALTER COLUMN doc TYPE JSONB USING doc::jsonb')

    # What tac_terrain_usage() now asks.
    #
    # **Not partial.** The obvious `WHERE … LIKE '/t/%'` — index only the maps
    # on a terrain this bot serves — makes an index the query cannot use:
    # PostgreSQL will not infer that `= '/t/7'` implies `LIKE '/t/%'`, so it
    # ignores the index and reads the table. Measured on 20,008 maps: a plain
    # expression index turns that scan into an index lookup, while the partial
    # one left it a sequential scan it could not tell apart from before.
    await db.execute("""CREATE INDEX IF NOT EXISTS idx_tac_maps_background_url
                          ON tac_maps ((doc -> 'background' ->> 'url'))""")


async def _v4_modlists(db):
    """Launcher presets posted to a channel, kept as versions.

    A row is one preset as it was posted, never edited afterwards: the current
    one per list is a flag (`is_current`), so putting an older one back is
    flipping it rather than re-uploading it. `source_ref` is the Discord
    message and attachment it came from, which is what makes reading the same
    message twice (a restart, a catch-up) a no-op instead of a second version.

    `events.modlist_id` hangs one on an event. It points at the *version*, so an
    event keeps showing the list it was announced with after a newer one is
    posted; `ON DELETE SET NULL` because a deleted version must not take the
    event with it.
    """
    await db.execute("""
        CREATE TABLE IF NOT EXISTS modlist_settings (
            guild_id TEXT PRIMARY KEY,
            channel_id TEXT,
            keywords JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS modlists (
            id SERIAL PRIMARY KEY,
            guild_id TEXT NOT NULL,
            slot TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            mods JSONB NOT NULL DEFAULT '[]'::jsonb,
            dlcs JSONB NOT NULL DEFAULT '[]'::jsonb,
            summary TEXT,
            source_ref TEXT,
            channel_id TEXT,
            message_id TEXT,
            posted_by TEXT,
            posted_by_name TEXT,
            is_current BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""CREATE INDEX IF NOT EXISTS idx_modlists_guild_slot
                          ON modlists (guild_id, slot, id DESC)""")
    await db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_modlists_current
                          ON modlists (guild_id, slot) WHERE is_current""")
    await db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_modlists_source
                          ON modlists (guild_id, source_ref)
                          WHERE source_ref IS NOT NULL""")
    await db.execute('ALTER TABLE events ADD COLUMN IF NOT EXISTS modlist_id '
                     'INTEGER REFERENCES modlists(id) ON DELETE SET NULL')


async def _v5_kt_player_members(db):
    # Which Discord member a Kill Tracker player is. Keyed by a name as recorded
    # (not the shown one), so linking or re-showing names never orphans it; a
    # linked group reads the mapping off any of its names.
    await db.execute("""
        CREATE TABLE IF NOT EXISTS kt_player_members (
            guild_id TEXT NOT NULL,
            player TEXT NOT NULL,
            user_id TEXT NOT NULL,
            PRIMARY KEY (guild_id, player)
        )
    """)
    await db.execute("""CREATE INDEX IF NOT EXISTS idx_kt_player_members_user
                          ON kt_player_members (guild_id, user_id)""")


async def _v6_kt_promotion(db):
    # Cadet → Member after N missions. One settings row per guild, off until an
    # admin turns it on; `kt_promotions` remembers who was already told about,
    # promoted or passed over, so nobody is announced twice.
    await db.execute("""
        CREATE TABLE IF NOT EXISTS kt_promotion_settings (
            guild_id TEXT PRIMARY KEY,
            enabled BOOLEAN NOT NULL DEFAULT FALSE,
            auto BOOLEAN NOT NULL DEFAULT FALSE,
            missions INTEGER NOT NULL DEFAULT 5,
            from_role_id TEXT,
            to_role_id TEXT,
            channel_id TEXT
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS kt_promotions (
            guild_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            status TEXT NOT NULL,
            updated_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
            PRIMARY KEY (guild_id, user_id)
        )
    """)


async def _v7_kt_promotion_message(db):
    # The text of the "due for promotion" message; '' means the built-in one.
    await db.execute("ALTER TABLE kt_promotion_settings "
                     "ADD COLUMN IF NOT EXISTS message TEXT NOT NULL DEFAULT ''")


async def _v8_kt_player_claims(db):
    # A member's request to be recorded as a Kill Tracker player. One open claim
    # per member; an admin approving it writes kt_player_members, so what the
    # promotion rules read is still only ever set by an admin.
    await db.execute("""
        CREATE TABLE IF NOT EXISTS kt_player_claims (
            guild_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            player TEXT NOT NULL,
            requested_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
            PRIMARY KEY (guild_id, user_id)
        )
    """)


async def _v9_kt_active_duty(db):
    # The Active Duty role: held by whoever played in the last N recordings. One
    # row per guild, off until an admin turns it on.
    await db.execute("""
        CREATE TABLE IF NOT EXISTS kt_active_duty_settings (
            guild_id TEXT PRIMARY KEY,
            enabled BOOLEAN NOT NULL DEFAULT FALSE,
            role_id TEXT,
            operations INTEGER NOT NULL DEFAULT 4
        )
    """)


async def _v10_kt_mission_excluded(db):
    # A recording an admin has taken out of the statistics (a test, a broken
    # run). It stays stored and listed on the admin page; nothing counts it.
    await db.execute("ALTER TABLE kt_missions "
                     "ADD COLUMN IF NOT EXISTS excluded BOOLEAN NOT NULL DEFAULT FALSE")


MIGRATIONS = [
    Migration(1, 'baseline', _v1_baseline),
    Migration(2, 'guild indexes', _v2_guild_indexes),
    Migration(3, 'tac_maps.doc is jsonb', _v3_tac_maps_doc_jsonb),
    Migration(4, 'modlists', _v4_modlists),
    Migration(5, 'kill tracker players to discord members', _v5_kt_player_members),
    Migration(6, 'kill tracker promotion', _v6_kt_promotion),
    Migration(7, 'promotion message text', _v7_kt_promotion_message),
    Migration(8, 'kill tracker player claims', _v8_kt_player_claims),
    Migration(9, 'kill tracker active duty', _v9_kt_active_duty),
    Migration(10, 'kill tracker excluded missions', _v10_kt_mission_excluded),
]
