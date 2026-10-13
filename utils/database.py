import asyncpg
import os

DATABASE_URL = os.getenv('DATABASE_URL')

_pool = None


async def get_pool():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL)
    return _pool


async def init_db():
    pool = await get_pool()
    async with pool.acquire() as db:
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


async def get_guild_timezone(guild_id: str) -> str:
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            'SELECT timezone FROM guild_settings WHERE guild_id = $1', guild_id
        )
        return row['timezone'] if row else 'UTC'


async def get_ocap_base_url(guild_id: str) -> str:
    """Where this guild's OCAP serves its terrains — '' until one is saved."""
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            'SELECT ocap_base_url FROM guild_settings WHERE guild_id = $1', guild_id
        )
        return (row['ocap_base_url'] or '') if row else ''


async def set_ocap_base_url(guild_id: str, url: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''INSERT INTO guild_settings (guild_id, timezone, ocap_base_url)
               VALUES ($1, 'UTC', $2)
               ON CONFLICT (guild_id)
               DO UPDATE SET ocap_base_url = EXCLUDED.ocap_base_url''',
            guild_id, url or None,
        )


async def set_guild_timezone(guild_id: str, timezone: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''INSERT INTO guild_settings (guild_id, timezone)
               VALUES ($1, $2)
               ON CONFLICT (guild_id) DO UPDATE SET timezone = EXCLUDED.timezone''',
            guild_id, timezone,
        )


# The channels the bot posts into, and the name each falls back to. A guild that
# has never touched the settings has NULL in all three and is served entirely by
# the fallbacks, exactly as before they existed.
CHANNEL_KINDS = {
    'orbat': ('orbat_channel_id', 'orbat'),
    'approvals': ('approvals_channel_id', 'slot-approvals'),
    'archive': ('archive_channel_id', 'approval-archive'),
}


async def get_guild_channels(guild_id: str) -> dict:
    """`{kind: channel id or None}` for every kind in `CHANNEL_KINDS`."""
    columns = ', '.join(column for column, _ in CHANNEL_KINDS.values())
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            f'SELECT {columns} FROM guild_settings WHERE guild_id = $1', guild_id
        )
    return {
        kind: (row[column] if row else None)
        for kind, (column, _) in CHANNEL_KINDS.items()
    }


async def set_guild_channels(guild_id: str, channels: dict):
    """Write the channel choices. A kind mapped to None goes back to its name."""
    unknown = set(channels) - set(CHANNEL_KINDS)
    if unknown:
        raise ValueError(f"Unknown channel kind(s): {', '.join(sorted(unknown))}")
    if not channels:
        return
    columns = [CHANNEL_KINDS[kind][0] for kind in channels]
    values = [channels[kind] for kind in channels]
    names = ', '.join(columns)
    placeholders = ', '.join(f'${i}' for i in range(2, len(columns) + 2))
    updates = ', '.join(f'{column} = EXCLUDED.{column}' for column in columns)
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            f'''INSERT INTO guild_settings (guild_id, {names})
                VALUES ($1, {placeholders})
                ON CONFLICT (guild_id) DO UPDATE SET {updates}''',
            guild_id, *values,
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


async def add_game_role(guild_id: str, role_id: str, name: str,
                        emoji: str = None, description: str = None):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''INSERT INTO game_roles (guild_id, role_id, name, emoji, description)
               VALUES ($1, $2, $3, $4, $5)
               ON CONFLICT (guild_id, role_id) DO UPDATE SET
                   name = EXCLUDED.name,
                   emoji = EXCLUDED.emoji,
                   description = EXCLUDED.description''',
            guild_id, role_id, name, emoji, description,
        )


async def remove_game_role(guild_id: str, role_id: str) -> bool:
    pool = await get_pool()
    async with pool.acquire() as db:
        result = await db.execute(
            'DELETE FROM game_roles WHERE guild_id = $1 AND role_id = $2',
            guild_id, role_id,
        )
        return int(result.split()[-1]) > 0


async def get_game_roles(guild_id: str) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            'SELECT * FROM game_roles WHERE guild_id = $1 ORDER BY name',
            guild_id,
        )


async def save_game_role_panel(guild_id: str, channel_id: str, message_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''INSERT INTO game_role_panels (guild_id, channel_id, message_id)
               VALUES ($1, $2, $3)
               ON CONFLICT (guild_id) DO UPDATE SET
                   channel_id = EXCLUDED.channel_id,
                   message_id = EXCLUDED.message_id,
                   updated_at = CURRENT_TIMESTAMP''',
            guild_id, channel_id, message_id,
        )


async def get_game_role_panel(guild_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow(
            'SELECT channel_id, message_id FROM game_role_panels WHERE guild_id = $1',
            guild_id,
        )


# ---------------------------------------------------------------------------
# Events
#
# event_time is naive UTC, like operations.event_time. These queries compare it
# against NOW() AT TIME ZONE 'UTC' rather than CURRENT_TIMESTAMP so the result
# is correct regardless of the database session's timezone setting.
# ---------------------------------------------------------------------------

def _naive(dt):
    """Strip tzinfo so values match the naive-UTC columns."""
    if dt is not None and hasattr(dt, 'tzinfo') and dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


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


async def get_approved_member_ids(operation_id: int) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            "SELECT member_id, slot_label FROM requests WHERE operation_id = $1 AND status = 'approved'",
            operation_id,
        )
        return [(row['member_id'], row['slot_label']) for row in rows]


# ---------------------------------------------------------------------------
# Embeds
#
# Every column the builder can set is written outright rather than through
# COALESCE: the form always submits the whole embed, and emptying a field has to
# be able to clear it.
# ---------------------------------------------------------------------------

_EMBED_COLUMNS = (
    'name', 'channel_id', 'content', 'title', 'description', 'url', 'color',
    'author_name', 'author_icon_url', 'thumbnail_url', 'image_url',
    'footer_text', 'footer_icon_url', 'show_timestamp',
)


async def create_embed(guild_id: str, created_by: str, created_by_name: str,
                       values: dict) -> int:
    columns = [name for name in _EMBED_COLUMNS if name in values]
    placeholders = ', '.join(f'${i}' for i in range(4, 4 + len(columns)))
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            f'''INSERT INTO embeds (guild_id, created_by, created_by_name, {', '.join(columns)})
                VALUES ($1, $2, $3, {placeholders})
                RETURNING id''',
            guild_id, created_by, created_by_name, *[values[name] for name in columns],
        )
        return row['id']


async def update_embed(embed_id: int, values: dict):
    columns = [name for name in _EMBED_COLUMNS if name in values]
    if not columns:
        return
    assignments = ', '.join(f'{name} = ${i}' for i, name in enumerate(columns, start=2))
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            f'UPDATE embeds SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = $1',
            embed_id, *[values[name] for name in columns],
        )


async def get_embed(embed_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow('SELECT * FROM embeds WHERE id = $1', embed_id)


async def get_guild_embeds(guild_id: str) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            'SELECT * FROM embeds WHERE guild_id = $1 ORDER BY updated_at DESC', guild_id
        )


async def save_embed_message(embed_id: int, channel_id: str, message_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''UPDATE embeds SET channel_id = $2, message_id = $3,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = $1''',
            embed_id, channel_id, message_id,
        )


async def clear_embed_message(embed_id: int):
    """Forget where the embed was posted — used when its message is gone, so the
    next send posts a new one instead of failing to edit a deleted message."""
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            'UPDATE embeds SET message_id = NULL WHERE id = $1', embed_id
        )


async def delete_embed(embed_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as db:
        result = await db.execute('DELETE FROM embeds WHERE id = $1', embed_id)
        return int(result.split()[-1]) > 0


async def get_embed_fields(embed_id: int) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            'SELECT * FROM embed_fields WHERE embed_id = $1 ORDER BY sort_order', embed_id
        )


async def set_embed_fields(embed_id: int, fields: list):
    """Replace an embed's fields. An empty list removes them all."""
    pool = await get_pool()
    async with pool.acquire() as db:
        async with db.transaction():
            await db.execute('DELETE FROM embed_fields WHERE embed_id = $1', embed_id)
            for order, item in enumerate(fields):
                await db.execute(
                    '''INSERT INTO embed_fields (embed_id, name, value, inline, sort_order)
                       VALUES ($1, $2, $3, $4, $5)''',
                    embed_id, item['name'], item['value'],
                    int(item.get('inline', 0)), order,
                )


# ---------------------------------------------------------------------------
# Member logging
# ---------------------------------------------------------------------------

_LOG_COLUMNS = (
    'channel_id', 'log_join', 'log_leave', 'log_kick', 'log_ban', 'log_unban',
    'track_invites', 'welcome_channel_id', 'welcome_message', 'welcome_dm',
)


async def get_log_settings(guild_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow('SELECT * FROM log_settings WHERE guild_id = $1', guild_id)


async def save_log_settings(guild_id: str, values: dict):
    columns = [name for name in _LOG_COLUMNS if name in values]
    if not columns:
        return
    updates = ', '.join(f'{name} = EXCLUDED.{name}' for name in columns)
    placeholders = ', '.join(f'${i}' for i in range(2, 2 + len(columns)))
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            f'''INSERT INTO log_settings (guild_id, {', '.join(columns)})
                VALUES ($1, {placeholders})
                ON CONFLICT (guild_id) DO UPDATE SET
                    {updates}, updated_at = CURRENT_TIMESTAMP''',
            guild_id, *[values[name] for name in columns],
        )


# ---------------------------------------------------------------------------
# Campaign planner settings
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Campaign planner: campaigns, AOs, missions, phases, the library, images
#
# A mission's wizard fields are one JSON document in `cp_missions.fields`,
# merged in SQL (`fields::jsonb || patch`) so two people saving different
# fields at the same moment both keep their change. Phases are rows, because
# milestone 5 hangs a map layer off each one.
# ---------------------------------------------------------------------------

import json as _json

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
#
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


# ---------------------------------------------------------------------------
# Reddit feeds
#
# `seen_ids` is a comma-separated list of the post ids already announced, newest
# first and capped — a rolling set rather than a high-water timestamp, because a
# post that is edited moves its timestamp and would otherwise be announced twice.
# NULL means the watch has never been read: the first read seeds it and
# announces nothing.
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Voice time
#
# `seconds` is filled when an interval closes. `heartbeat_at` is refreshed while
# one is open, so a hard crash costs at most one heartbeat of unrecorded time
# instead of the whole session — and so a live total can include the interval
# somebody is in right now.
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Invite labels
# ---------------------------------------------------------------------------

async def get_invite_labels(guild_id: str) -> dict:
    """{invite code: label} for one guild."""
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            'SELECT code, label FROM invite_labels WHERE guild_id = $1', guild_id
        )
        return {row['code']: row['label'] for row in rows}


async def save_invite_labels(guild_id: str, labels: dict, remove: list = None):
    """Store the labels that were filled in and drop the ones that were emptied."""
    pool = await get_pool()
    async with pool.acquire() as db:
        async with db.transaction():
            for code, label in labels.items():
                await db.execute(
                    '''INSERT INTO invite_labels (guild_id, code, label)
                       VALUES ($1, $2, $3)
                       ON CONFLICT (guild_id, code) DO UPDATE SET
                           label = EXCLUDED.label,
                           updated_at = CURRENT_TIMESTAMP''',
                    guild_id, code, label,
                )
            if remove:
                await db.execute(
                    'DELETE FROM invite_labels WHERE guild_id = $1 AND code = ANY($2::text[])',
                    guild_id, list(remove),
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


# ---------------------------------------------------------------------------
# ORBATs
# ---------------------------------------------------------------------------

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


async def orbat_operations(orbat_id: int) -> list:
    """The operations running on this ORBAT, newest first."""
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            '''SELECT * FROM operations WHERE orbat_id = $1
               ORDER BY is_active DESC, created_at DESC''',
            orbat_id,
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


# ---------------------------------------------------------------------------
# Tactical maps
# ---------------------------------------------------------------------------

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
               VALUES ($1, $2, $3, $4, $5, $6, $6) RETURNING id''',
            guild_id, name, description, doc, created_by, created_by_name,
        )
        return row['id']


async def save_tac_map_doc(map_id: int, doc: str, updated_by_name: str = None):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''UPDATE tac_maps
                  SET doc = $2, updated_by_name = COALESCE($3, updated_by_name),
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
                  SET doc = $2, updated_by_name = COALESCE($3, updated_by_name),
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


# ---------------------------------------------------------------------------
# Uploaded terrains
# ---------------------------------------------------------------------------

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


async def get_guild_tac_places(guild_id: str) -> list:
    """Every terrain this guild has names for, for the list page."""
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            'SELECT * FROM tac_places WHERE guild_id = $1 ORDER BY scope',
            str(guild_id),
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
    """
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            '''SELECT id, name FROM tac_maps
                WHERE doc LIKE '%' || $1 || '%' ORDER BY name''',
            f'"/t/{terrain_id}"',
        )


async def delete_tac_terrain(terrain_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as db:
        result = await db.execute('DELETE FROM tac_terrains WHERE id = $1', terrain_id)
        return int(result.split()[-1]) > 0


# ---------------------------------------------------------------------------
# Kill Tracker
#
# One row per OCAP recording. Naive UTC like every stored time here.
# ---------------------------------------------------------------------------

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


async def kt_struck_kills() -> list:
    """Everything struck so far, newest first, with what it was."""
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            '''SELECT e.ocap_id, e.kill_key, e.killer, e.victim, e.weapon, e.distance,
                      e.struck_by_name, e.struck_at, m.label, m.name_override
                 FROM kt_kill_exclusions e JOIN kt_missions m USING (ocap_id)
                ORDER BY e.struck_at DESC'''
        )


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
