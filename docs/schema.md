<!-- Part of ORBATBot's documentation. The rules every session needs
     are in ../CLAUDE.md, which says when to read this file. -->

## Database Schema

All tables live in PostgreSQL. `utils/database/` holds the queries;
**`utils/schema.py` holds the shape they run against, as a numbered list of
migrations** — see [the schema](#the-schema-utilsschemapy). `init_db()` applies
whatever this database has not run and records it in `schema_version`, so a boot
with nothing to do is one statement rather than the 127 it used to send.

Everything below describes the schema **as it now stands**, which is what the
migrations add up to; it is not a description of migration 1.

### `operations`
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `guild_id` | TEXT | Discord guild (server) ID |
| `name` | TEXT | Operation name — from the ORBAT or the spreadsheet title, or overridden on `/setup-slots` |
| `sheet_url` | TEXT | Full Google Sheets URL. **NULL on an ORBAT-backed operation** |
| `sheet_id` | TEXT | Extracted sheet ID. NULL likewise |
| `orbat_id` | INTEGER | → `orbats.id`. NULL on a sheet-backed operation. Exactly one of this and `sheet_url` is set, and `utils/roster.py` is the only thing that looks |
| `squad_col` | INTEGER | **Always NULL** — see [Google Sheets Integration](slots.md#google-sheets-integration-utilssheetspy) |
| `role_col` | INTEGER | **Always NULL** |
| `status_col` | INTEGER | **Always NULL** |
| `assigned_col` | INTEGER | **Always NULL** |
| `is_active` | INTEGER | 1 = active, 0 = archived. Only one active per guild |
| `event_time` | TIMESTAMP | Naive UTC. NULL if not set |
| `reminder_minutes` | INTEGER | Default 30 |
| `reminder_fired` | INTEGER | 0/1 — prevents double-firing |
| `created_at` | TIMESTAMP | |

### `requests`
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `guild_id` | TEXT | |
| `operation_id` | INTEGER | FK → operations.id |
| `member_id` | TEXT | Discord user ID |
| `member_name` | TEXT | Display name at time of request |
| `slot_label` | TEXT | Human-readable label e.g. "1-2 (TFP) – Rifleman" |
| `sheet_row` | INTEGER | Row index in the sheet. NULL on an ORBAT-backed request |
| `sheet_col` | INTEGER | Column index (ORBAT-style sheets) |
| `status` | TEXT | `pending` / `approved` / `denied` / `cancelled` |
| `approval_message_id` | TEXT | Discord message ID in #slot-approvals |
| `approval_channel_id` | TEXT | Discord channel ID for above |
| `approved_by` | TEXT | Display name of approver/denier |
| `denial_reason` | TEXT | Optional reason text |
| `unit_role` | TEXT | Unit role of the requester at submission time |
| `slot_id` | INTEGER | → `orbat_slots.id` on an ORBAT-backed operation; NULL on a sheet-backed one, where `sheet_row`/`sheet_col` identify the slot instead. No foreign key: `apply_orbat_structure()` releases the bookings itself when a slot goes, so the promise the confirmation page makes is kept |
| `created_at` | TIMESTAMP | |
| `updated_at` | TIMESTAMP | |

### `orbat_messages`
| Column | Type | Notes |
|---|---|---|
| `guild_id` | TEXT PK | |
| `channel_id` | TEXT | Channel where ORBAT embed lives |
| `message_id` | TEXT | Message ID of the live ORBAT embed |
| `updated_at` | TIMESTAMP | |

### `open_slots_messages`
Same structure as `orbat_messages`. Reserved for a planned secondary "open slots" message. **Nothing reads or writes it** — the table is still created so existing deployments aren't orphaned, but the accessors were removed as dead code. Re-add them if the feature is picked up.

### `orbats`
The slot roster held here rather than read out of a Google Sheet — see
[ORBATs](orbats.md#orbats-utilsorbatpy--weborbatpy).

| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `guild_id` | TEXT | |
| `name` | TEXT | Shown in the list |
| `description` | TEXT | Optional |
| `nets_text` | TEXT | The net list as its author typed it, for the same reason as below |
| `source_text` | TEXT | The roster as its author typed it. The squads and slots below are the source of truth; this is kept alongside so comments, blank lines and their own spacing survive a reload, which regenerating the text from the structure would flatten |
| `created_by` / `created_by_name` | TEXT | |
| `created_at` / `updated_at` | TIMESTAMP | |

### `orbat_squads`
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `orbat_id` | INTEGER | FK → `orbats.id` **ON DELETE CASCADE** |
| `name` | TEXT | The embed field's heading |
| `column_side` | INTEGER | 0 = left, 1 = right. What `_build_orbat_embed()` infers from sheet geometry is stated outright here |
| `exclude_from_count` | INTEGER | 0/1 — replaces the case-insensitive `Reservists` name match |
| `reserved_unit` | TEXT | The unit the whole squad belongs to, matched by name against `UNIT_ROLES`. It began on the slot; migration 1 lifts any values entered there up to their squad and drops the slot column |
| `radio` | TEXT | The channel the squad talks on internally, e.g. `343 CHN:3`. Free text — every unit writes these slightly differently |
| `sort_order` | INTEGER | |

### `orbat_slots`
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | The stable slot identity a booking hangs off |
| `squad_id` | INTEGER | FK → `orbat_squads.id` **ON DELETE CASCADE** |
| `role_name` | TEXT | |
| `sort_order` | INTEGER | |

**A slot carries no booking.** Who holds one lives in `requests`, keyed by
`(operation_id, slot_id)`, which is what makes an ORBAT a reusable template
rather than one night's board.

### `orbat_nets`
The long-range nets the whole operation shares — platoon, logistics, air, high
command — as against `orbat_squads.radio`, which is one squad's internal channel.

| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `orbat_id` | INTEGER | FK → `orbats.id` **ON DELETE CASCADE** |
| `name` | TEXT | *Platoon Net*, *Logi*, *High Com Net* |
| `channel` | TEXT | Free text, e.g. `152 CHN : 1`. May be NULL for a net whose frequency isn't decided |
| `inactive` | INTEGER | 0/1 — struck through on the board: planned, but not in use this time |
| `sort_order` | INTEGER | |

**Nothing hangs off a net**, so unlike the squads and slots a save replaces the
list wholesale — there is no identity for an edit to lose.

### `reddit_feeds`
One row is one watch — see [Reddit announcements](reddit.md#reddit-announcements-utilsredditpy--cogsredditfeedpy--webredditpy).

| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `guild_id` | TEXT | |
| `kind` | TEXT | `user` or `subreddit` |
| `source` | TEXT | The name, without the `u/` or `r/` |
| `channel_id` | TEXT | Where posts are announced. NULL = the watch has nowhere to post, so it is skipped |
| `template` | TEXT | The announcement text. NULL = `reddit.DEFAULT_TEMPLATE` |
| `author_filter` | TEXT | Comma-separated account names. NULL = announce every post the feed carries — see [following an author through a subreddit](reddit.md#following-an-author-through-a-subreddit) |
| `mention_role_id` | TEXT | **Comma-separated** role ids, the same convention as `events` |
| `mention_user_id` | TEXT | Comma-separated user ids — the "tag these people" half |
| `enabled` | INTEGER | 0/1 |
| `seen_ids` | TEXT | The post ids already announced, newest first, capped at `MAX_SEEN`. **NULL means the watch has never been read**, which is what makes the first read seed instead of announcing 25 old posts |
| `last_checked_at` / `last_post_at` | TIMESTAMP | Naive UTC |
| `last_error` | TEXT | What the last read went wrong with, shown on the list page. NULL after a clean read |
| `retry_at` | TIMESTAMP | Don't read this watch again before then — see [being refused](reddit.md#being-refused-is-about-us-not-about-the-feed). NULL, and cleared by any read that gets through |
| `created_by` / `created_by_name` | TEXT | |
| `created_at` / `updated_at` | TIMESTAMP | |

`UNIQUE (guild_id, kind, lower(source))` — two rows for the same author would
announce every post twice, which reads as the bot being broken. Both writers turn
the violation into a `ValueError`, so it is a message rather than a 500.

### `tac_maps`
One row is one tactical map — see
[Tactical maps](tacmap.md#tactical-maps-utilstacmap--webtacmappy).

| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `guild_id` | TEXT | |
| `name` / `description` | TEXT | |
| `doc` | JSONB | **The whole map** — background, grid, the Arma corners and every symbol on it. Held as one document rather than a table of items because nothing hangs off a symbol: a save replaces the plan wholesale, the same reasoning as `orbat_nets`. `utils/tacmap.parse()` is what makes the column safe to render, and it runs on the way out as well as in. **`jsonb` rather than text since migration 3**, so the one query that searches inside it can ask the structure — see [one map per terrain](#the-column-that-had-to-be-searched) |
| `share_token` | TEXT | The `/m/{token}` link. NULL = never shared. Unique where it is set |
| `share_mode` | TEXT | `off` / `view` / `edit` — what the link may do |
| `channel_id` / `message_id` | TEXT | Where the map was last announced |
| `created_by` / `created_by_name` | TEXT | |
| `updated_by_name` | TEXT | Who last drew on it — including *someone with the link* |
| `doc_unreadable` | TEXT | A `doc` that was not valid JSON when migration 3 converted the column, kept rather than discarded. **Always NULL in practice** — `tacmap.parse()` could not read such a row either, so that map already opened empty. NULL on every row the migration found convertible |
| `doc_rev` | INTEGER | Bumped by every write of `doc`. A save names the revision it was made on and is refused if somebody else saved since |
| `created_at` / `updated_at` | TIMESTAMP | |

### `tac_terrains` and `tac_terrain_tiles`
A terrain uploaded as a tile archive and served back by this bot — see
[the background](tacmap.md#the-background-is-a-picture-a-terrain-from-ocap-or-one-you-upload).

| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | The `/t/{id}` a map's background points at |
| `guild_id` | TEXT | |
| `name` | TEXT | From the archive's `map.json`, or typed |
| `world_size` | DOUBLE PRECISION | The terrain's edge in metres — what the Arma corners come from |
| `max_zoom` | INTEGER | The deepest level actually stored |
| `tile_count` / `bytes` | INTEGER / BIGINT | What the list page shows, so the cost of a terrain is visible |
| `places` | TEXT | This terrain's own copy of its town names. Written on upload; what a map actually reads is `tac_places`, because an OCAP-backed map has no row here — see [place names](tacmap.md#place-names-the-terrain-knows-its-own-towns-the-tiles-do-not) |
| `created_by` / `created_by_name` | TEXT | |
| `created_at` | TIMESTAMP | |

`tac_terrain_tiles` is `(terrain_id, zoom, x, y)` → `image BYTEA`, primary-keyed
on exactly that, with **ON DELETE CASCADE**. The tiles are in the database and
not on disk because a container's filesystem does not survive a redeploy, and a
map whose background disappears on every deploy is worse than one with no
background at all.

### `tac_places`
The town names for one terrain, whether or not this bot stores that terrain —
see [place names](tacmap.md#place-names-the-terrain-knows-its-own-towns-the-tiles-do-not).

| Column | Type | Notes |
|---|---|---|
| `guild_id` / `scope` | TEXT | Composite primary key. `scope` is what `tacmap.place_scope()` returns: `t:7` for a terrain uploaded here, or the world name (`tem_cham`) for a map on an OCAP server. **That is the whole reason this is not a column on `tac_terrains`** — half the terrains a guild draws on have no row there |
| `label` | TEXT | What to call it in a list — the terrain's name, or the world name |
| `places` | TEXT | The names as JSON, replaced wholesale: nothing hangs off a place name, the same reasoning as `orbat_nets` |
| `source` | TEXT | `ocap`, `upload`, `paste` or `terrain` — where this set came from, so a re-import can say what it replaced |
| `updated_by_name` | TEXT | |
| `updated_at` | TIMESTAMP | |

### `kt_missions`
One row per OCAP recording — see [the Kill Tracker](killtracker.md#the-kill-tracker-utilskilltrackerpy-utilsocappy-cogskilltrackerpy-webkilltrackerpy). **Not scoped to a guild:** the OCAP server is the unit's, not a Discord server's.

| Column | Type | Notes |
|---|---|---|
| `ocap_id` | INTEGER PK | The OCAP server's own id for the recording. It only grows, so it is what "new" means |
| `filename` | TEXT UNIQUE | Without `.json.gz`; the file is `{base}/data/{filename}.json.gz` |
| `mission` / `world` / `tag` / `played_on` / `duration` | | From the OCAP list. `tag` is `Weekly OP` or `JointOP` |
| `label` | TEXT | `Mission (YYYY-MM-DD)` — what the dashboard's Mission column holds |
| `ocap_kills` / `ocap_player_kills` | INTEGER | The server's own counts, kept to hold ours against |
| `counted_kills` | INTEGER | Enemy kills by non-Zeus players, as counted here |
| `rows` | TEXT | **JSON array of the mission's per-player rows**, in `killtracker.HEADER` order. `'[]'` until counted. Replaced wholesale on every re-count, the same reasoning as `orbat_nets` |
| `name_override` | TEXT | The name an admin gave the mission, shown instead of OCAP's, **beside** `rows` so a re-count cannot erase it. The CSV applies it (`killtracker.apply_name`), keeping the recording's own date |
| `rules_version` | INTEGER | `killtracker.RULES_VERSION` it was counted with. **0 = never counted.** A mission below the current version is counted again by itself |
| `status` | TEXT | `ok` or `failed` |
| `error` / `attempts` / `retry_at` | | Why the last read failed, how many have, and not-before — doubling from 10 minutes to a day (`ocap.retry_delay_minutes`). A failed *re*-count leaves the old rows in place |
| `far_kills` | TEXT | **JSON list of the mission's farthest kills that feed the distance statistics** (infantry only — a vehicle's long shots are real; the 25 farthest at least 200 m, **plus every player's own longest**; struck ones are kept besides, flagged `x`, and do not take a place — striking the farthest lets the next one fill up at the next count) — what the *Longest kills* page shows without opening the recording. Replaced on every re-count |
| `sha256` | TEXT | Of the downloaded file |
| `imported_at` | TIMESTAMP | Naive UTC. Part of the cache key `kt_generation()` |

### `kt_campaigns`, `kt_campaign_missions`
A named group of missions. `kt_campaign_missions (campaign_id, ocap_id)` is by **recording**, ON DELETE CASCADE on both sides, so a renamed mission stays in its campaigns; `/kt/api/campaigns.json` writes each campaign's missions out under the names they are shown with now. In `kt_campaigns`: `name` (unique, case-insensitive), `patterns` (JSON list — the old substring matching), `extra_missions` (JSON list of labels imported from `campaigns.json` that matched no recording; passed through unchanged) and who last saved it.

### `kt_kill_exclusions`
`(ocap_id, kill_key)` → a kill an admin struck from the distance statistics. `kill_key` is `killtracker.kill_key()`: `frame:killer_id:victim_id`, ids as the recording has them, which is why it is kept beside the mission and applied by `compute(…, exclude)` on every re-count. The killer, victim, weapon and distance are copied in so a list can say what was struck. ON DELETE CASCADE with the mission. Striking or restoring one sets `kt_missions.rules_version = 0`, which is all it takes for the import loop to count that mission again.

### `kt_player_claims`
`(guild_id, user_id)` (PK) → `player` (the name as the dashboard shows it), `requested_at`. A member's open claim to be a Kill Tracker player, migration 8. **One open claim per member**; claiming again replaces it. It is not the mapping: an admin confirming it on the Players page writes `kt_player_members` (what the promotion rules read) and deletes the claim, rejecting only deletes it. Nobody but an admin ever writes `kt_player_members`.

### `kt_aliases`
`alias` (PK) → `canonical`: names that belong to one player and the one shown. The shape of the dashboard's `player_aliases.json`, which the dashboard applies on top of its own automatic merging. **Never a chain** — `killtracker.link_names()` pulls in everything already tied to any of the names, in either direction, because the dashboard doesn't follow one.

### `kt_unit_overrides`
`player` (PK) → `unit`, where the dashboard's guess is wrong. `''` is *Unknown* (the file's `null`); no row is *automatic*. Units are `killtracker.UNITS`, mirroring `UNIT_ORDER` in the dashboard.

### Campaign planner (`cp_`)
Created by `_init_campaign_planner()` at the end of migration 1, after the tables they reference.

| Table | What it holds |
|---|---|
| `cp_settings` | One row per guild: `unit_role_id`, `drop_channel_id`, `team_channel_id`, `drop_weekday` (0 = Monday), `drop_time` (`HH:MM`, guild time zone), `orbat_opens` (a drop key), `survey_url`. NULL = default; read it through `campaign.settings_with_defaults()`. `library_seeded` — the objectives library has been seeded once and never will be again. `orbat_access` (`admins` / `planners`, NULL = planners) and `orbat_role_ids` (comma-separated extra roles) — who may edit mission ORBATs, read through `campaign.may_edit_orbats()`. `auto_complete` — a finished campaign marks itself Complete (NULL = yes) |
| `cp_campaigns` | `guild_id`, `name` (unique per guild, case-insensitive), `era`, `theatre`, `summary`, `planned_missions` (the empty slots on the campaign page), `status` (`active` / `complete` / `archived`), `is_test` (see *Test campaigns*), `kt_campaign_id` → `kt_campaigns` (found or created by name when a real campaign is created), `kt_created` (the planner made that Kill Tracker campaign rather than linking one that existed — the only kind a delete may take with it, and the only kind a rename carries over to), `map_id` → `tac_maps` (the campaign map, made by the planner and deleted with the campaign), `auto_completed_at` (the bot closed it, or a person reopened it after it was closed — either way the bot leaves it open until a mission is added) |
| `cp_aos` | An area of operations: `campaign_id`, `name`, `position`, `area` (the campaign-map **layer** its outline is on, `cpao<id>`), `map_id` → its detailed `tac_maps` (made by the planner; deleted with the AO) |
| `cp_missions` | `campaign_id`, `position`, `name`, `op_date`, `fields` (JSON of the wizard's fields, keys as in `campaign.STEPS` — everything but the phases), `orbat_id` → `orbats`, `orbat_created` (the card's New or Copy made it, rather than Link — the only kind deleting a *test* mission may take along), `ocap_id` → `kt_missions` (the recording that is this mission), `ocap_manual` (a person chose it, or chose none — the loop stops guessing), `ocap_kt_added` (the planner put it in the Kill Tracker campaign — the only case unlinking takes it out), `operation_id` → the `operations` row sign-ups started. `name` and `op_date` mirror the fields of the same names. A save merges in SQL (`fields::jsonb \|\| patch`), so two people saving different fields both keep theirs |
| `cp_mission_aos` | Which AOs a mission is fought in (many-to-many) |
| `cp_phases` | A mission phase: `position`, `title`, `detail`, `ao_id`, `layer_id` (the layer of its AO's map it is drawn on, always `cpp<phase id>`). A row, not part of `fields`, because a layer hangs off it — `save_cp_phases()` keeps a phase's id across saves for exactly that reason |
| `cp_objective_library` | Per guild, seeded from `utils/objectives_seed.py`, admin-edited: `category`, `kind` (`objective` / `complication`), `name`, `players_do`, `complete_when`, `pairs_with` (free text; `campaign.pairing_ids()` turns it into the picker's suggestions — never a rule), `intsum_hint`, `position` |
| `cp_phase_objectives` | An objective chosen for a phase: `library_id` (where it was picked from, or NULL), `role` (`primary` / `secondary` / `complication`), and its own copy of `name`, `players_do`, `complete_when`, `intsum_hint`; `result` for the AAR. Replaced wholesale on each save — nothing hangs off one |
| `cp_drops` | PK (`mission_id`, `kind`). Decisions only — the state is `campaign.drop_state()`. `approved_hash` + `due_at` (naive UTC) are **the version and the time that were approved**, stored together; `approved_by`/`approved_by_name`/`approved_at`; `held_pinged_at` (the team was told once; cleared by a new approval); `posted_at`, `channel_id`, `message_ids` (JSON, every message incl. image-only ones), `posted_hash` (what is in Discord now); `skipped`/`skipped_by_name` (Don't post) |
| `cp_images` | Uploaded images for drops and exports: `campaign_id`, `filename`, `content_type` (sniffed from the bytes, `campaign.image_type()`), `data` (BYTEA), `bytes`, `created_by_name`. Never changed once stored, so served `immutable`. Per campaign, so a mission carrying over the unit image shares the same row |

### `guild_settings`
| Column | Type | Notes |
|---|---|---|
| `guild_id` | TEXT PK | |
| `timezone` | TEXT | IANA timezone string, default `UTC` |
| `orbat_channel_id` | TEXT | Where the live board and the reminder ping go |
| `approvals_channel_id` | TEXT | Where a new request goes to be decided |
| `archive_channel_id` | TEXT | Where every decided request is recorded |
| `ocap_base_url` | TEXT | The OCAP folder this guild's terrains are served from, so a terrain is picked from a list rather than its world name typed out. NULL until a directory has actually answered — see [the background](tacmap.md#the-background-is-a-picture-a-terrain-from-ocap-or-one-you-upload) |

**All three channel columns are NULL until an admin picks something**, and NULL
means the channel *named* `#orbat` / `#slot-approvals` / `#approval-archive`,
created when it is missing — which is exactly what every guild did before the
columns existed, so an upgrade changes nothing. `cogs/slots.guild_channel()` is
the only thing that reads them; `database.CHANNEL_KINDS` maps each kind to its
column and its fallback name.

### `game_roles`
Self-assignable cosmetic roles (Minecraft, DCS, …) — see Game Roles below.

| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `guild_id` | TEXT | |
| `role_id` | TEXT | Discord role ID |
| `name` | TEXT | Label shown in the picker |
| `emoji` | TEXT | Optional emoji shown on the select option |
| `description` | TEXT | Optional one-liner shown under the option |
| `created_at` | TIMESTAMP | |

`UNIQUE (guild_id, role_id)` — re-adding the same role updates it in place.

### `game_role_panels`
Same structure as `orbat_messages`. Tracks the live game-role self-assign panel message.

### `events`
Standalone events, independent of `operations` and Google Sheets — see Events below.

| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | Shown to users as `#id` |
| `guild_id` | TEXT | |
| `channel_id` | TEXT | Channel the event message lives in |
| `message_id` | TEXT | The event message; NULL until posted |
| `title` | TEXT | |
| `description` | TEXT | Optional |
| `event_time` | TIMESTAMP | **Naive UTC**, like `operations.event_time` |
| `duration_minutes` | INTEGER | Optional; drives the end time and "finished" detection |
| `location` | TEXT | Optional free text |
| `image_url` | TEXT | Optional banner |
| `mention_role_id` | TEXT | **Comma-separated** role ids pinged on post and on the reminder. Singular name kept: a row written before multi-role support holds one id, which parses as a one-item list — which is why this needed no migration |
| `created_by` | TEXT | Discord user ID of the organiser |
| `created_by_name` | TEXT | Display name at creation time |
| `reminder_minutes` | INTEGER | Default 30; NULL means no reminder |
| `reminder_fired` | INTEGER | 0/1 — reset to 0 when `event_time` changes |
| `status` | TEXT | `scheduled` / `cancelled` / `completed` |
| `recurrence` | TEXT | NULL, or `daily` / `weekly` / `biweekly` / `monthly` / `monthly_nth` / `monthly_last` / `weekly_not_last` |
| `recurrence_until` | TIMESTAMP | Optional end of the series (naive UTC) |
| `recurrence_anchor` | TIMESTAMP | The **first** occurrence's start, carried unchanged down the series |
| `recurrence_delay_hours` | INTEGER | Hours to wait after this occurrence ends before its successor is posted. NULL/0 = post it the moment this one is marked completed |
| `handover_fired` | INTEGER | 0/1 — whether this **completed** event's handover (spawning its successor) has already run, so the delayed handover step in `event_task` doesn't repeat it every tick. Backfilled to 1 for every event already `completed` before this column existed, since those already had their handover done synchronously at completion time under the old code |
| `created_at` / `updated_at` | TIMESTAMP | |

Index `idx_events_guild_status` on `(guild_id, status, event_time)` backs the upcoming-events lookup.

### `event_signups`
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `event_id` | INTEGER | FK → `events.id` **ON DELETE CASCADE** |
| `member_id` | TEXT | |
| `member_name` | TEXT | Display name at signup time |
| `response` | TEXT | A key from the event's response set — `accepted` / `tentative` / `declined` unless the event defines its own |
| `created_at` / `updated_at` | TIMESTAMP | |

`UNIQUE (event_id, member_id)` — one row per member, changing your answer updates it in place. Withdrawing deletes the row entirely, so "no response" and "declined" stay distinct.

### `event_responses`
Custom sign-up options for one event. **No rows means the event uses `DEFAULT_RESPONSES`** — that fallback is what keeps every event created before this feature working unchanged.

| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `event_id` | INTEGER | FK → `events.id` **ON DELETE CASCADE** |
| `key` | TEXT | Slug of the label; goes into the button `custom_id` and into `event_signups.response` |
| `label` | TEXT | Button text |
| `emoji` | TEXT | Optional |
| `is_decline` | INTEGER | 1 = "not coming"; excluded from reminders and cancellation DMs |
| `sort_order` | INTEGER | Button and embed-field order |

`UNIQUE (event_id, key)`. The default keys are deliberately `accepted` / `tentative` / `declined` so existing `event_signups` rows keep resolving.

### `embeds`
Rich messages built in the web UI — see [Embeds](embeds.md#embeds-utilsembedspy--webembedspy).

| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `guild_id` | TEXT | |
| `name` | TEXT | Internal label; only used to find it again in the list |
| `channel_id` / `message_id` | TEXT | Where it was posted. `message_id` NULL = still a draft |
| `content` | TEXT | Plain text above the embed — the only part where a mention pings |
| `title` / `description` / `url` | TEXT | `url` makes the title a link |
| `color` | TEXT | `#rrggbb`, normalised on save |
| `author_name` / `author_icon_url` | TEXT | |
| `thumbnail_url` / `image_url` | TEXT | |
| `footer_text` / `footer_icon_url` | TEXT | |
| `show_timestamp` | INTEGER | 0/1 — stamps the message with the time it was posted |
| `created_by` / `created_by_name` | TEXT | |
| `created_at` / `updated_at` | TIMESTAMP | |

### `embed_fields`
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `embed_id` | INTEGER | FK → `embeds.id` **ON DELETE CASCADE** |
| `name` / `value` | TEXT | Heading and body of one field |
| `inline` | INTEGER | 0/1 — up to three inline fields sit side by side |
| `sort_order` | INTEGER | |

### `log_settings`
One row per guild, written by the web UI. No row, or a NULL `channel_id`, means
nothing is announced — see [Member log](memberlog.md#member-log-cogsmemberlogpy).

| Column | Type | Notes |
|---|---|---|
| `guild_id` | TEXT PK | |
| `channel_id` | TEXT | Where announcements go. NULL = logging off |
| `log_join` / `log_leave` / `log_kick` / `log_ban` / `log_unban` | INTEGER | 0/1 per event type |
| `track_invites` | INTEGER | 0/1 — whether to work out which invite a join used |
| `welcome_channel_id` | TEXT | Where the welcome message is posted publicly. NULL = not posted anywhere — independent of `channel_id` above |
| `welcome_message` | TEXT | The welcome template as typed. NULL = `memberlog.DEFAULT_WELCOME_TEMPLATE` |
| `welcome_dm` | INTEGER | 0/1 — also send the welcome message as a DM |
| `updated_at` | TIMESTAMP | |

### `invite_labels`
Where each invite link was published, so a join can name the source.

| Column | Type | Notes |
|---|---|---|
| `guild_id` / `code` | TEXT | Composite primary key |
| `label` | TEXT | Free text — *Steam*, *Website*, *Reddit* |
| `updated_at` | TIMESTAMP | |

Rows outlive the invite: a code that has expired keeps its label, because old
join messages still refer to it.

### `voice_sessions`
One row per **counted interval**, not per visit — see [Voice time](voice.md#voice-time-cogsvoicelogpy).

| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `guild_id` / `member_id` / `channel_id` | TEXT | |
| `member_name` / `channel_name` | TEXT | Copied at the time, so a rename or a deleted channel doesn't erase history |
| `started_at` | TIMESTAMP | Naive UTC |
| `heartbeat_at` | TIMESTAMP | Refreshed every 5 min while open; bounds what a crash can lose |
| `ended_at` | TIMESTAMP | NULL = still running |
| `seconds` | INTEGER | Filled when the interval closes |

Indexes on `(guild_id, started_at)` and a partial one on the open rows.

### `voice_settings`
| Column | Type | Notes |
|---|---|---|
| `guild_id` | TEXT PK | |
| `enabled` | INTEGER | 0/1 — **off by default**, nothing is recorded until an admin turns it on |
| `board_enabled` | INTEGER | 0/1 — the self-updating top-10 message |
| `board_channel_id` / `board_message_id` | TEXT | Where the board is and which message it is |
| `board_period` | TEXT | Which window it shows — a key from `PERIODS` |
| `board_hour` | INTEGER | Local hour it refreshes at |
| `board_updated_on` | DATE | The local day it last refreshed — what makes it once-a-day |
| `channel_id` | TEXT | Where finished visits are announced. NULL = statistics only |
| `min_log_minutes` | INTEGER | Visits shorter than this are not announced |
| `count_afk` / `count_solo` | INTEGER | 0/1 — both off by default |
| `excluded_channels` | TEXT | Comma-separated voice channel ids |
| `updated_at` | TIMESTAMP | |

---

## The queries (`utils/database/`)

One module of 3,333 lines and 216 functions — a phone book, where finding the
one that writes `cp_drops` meant scrolling. It is thirteen modules now, one per
feature, grouped by **the tables each query touches**, behind an `__init__`
that re-exports every name: `from utils import database` and
`database.anything` are unchanged, and nothing outside the package changed.

**The split was safe because the coupling was almost nothing.** Of 233 calls
between functions in that file, all but 18 were to `get_pool()`, and not one of
the 18 crossed a feature group — `duplicate_orbat` calls orbat functions,
`duplicate_tac_map` calls map ones, the planner's helpers call each other. So
`core` holds the pool, `init_db()`, `ping()` and `_naive()`, and every feature
module imports from `core` and nothing else.

**With one exception, and it is a real one**: `campaigns` imports
`_link_kt_campaign` from `killtracker`. That function writes `kt_campaigns` —
which is why it lives in the Kill Tracker module, grouped by its table — and
only the campaign planner ever calls it, because a real campaign gets a Kill
Tracker campaign when it is created. Moving it would make "a module is named
after the tables it writes" untrue; allowing sideways imports generally would
make the grouping mean nothing. `tests/test_database_package.py` names that one
edge and fails on a second.

**The `__init__` re-export list is generated**, not hand-maintained:
`python scripts/gen_exports.py` writes it from the modules and `--check` says
when it has fallen behind, which is a test. A name missing from it is an
AttributeError in whichever cog or route wanted that query — possibly on a page
nobody opens until it matters.

**Nine section banners carried real rules in their prose** — why the event
queries compare against `NOW() AT TIME ZONE 'UTC'` rather than
`CURRENT_TIMESTAMP`, why the embed writer sets every column outright instead of
through `COALESCE`, why `seen_ids` is a rolling set rather than a high-water
timestamp, what `heartbeat_at` buys. Those went into the docstring of the
module that owns the code below them, because dropping them would have lost the
only place those rules are written down. Every other line of the original
appears exactly once across the thirteen files: **0 unaccounted for**.

### `SELECT *`, and why most of it stays

51 of the queries are `SELECT *`, which looks like something to fix and mostly
is not: the callers use the wide rows. The maps list parses every map's
document because it shows a per-kind item count; the campaign page needs every
mission's `fields` to work out what is locked and what is due. Two things are
worth knowing rather than changing:

- **The maps list page parses every document on every view.** `summarise()` is
  a count per item kind, and `doc` is `jsonb` now, so SQL could count without
  transferring a single map. It is not done because the count would then come
  from the raw document while the map is drawn from `parse()`'s — which drops
  items it will not render — so the list could disagree with the map. At real
  map sizes the parse is cheap; if maps get big, that is the trade to revisit.
- **Two of the four `list_cp_missions` callers need three columns**, not every
  mission's `fields`. Small, and splitting the query in two would be a second
  thing to keep in step.

---

## The schema (`utils/schema.py`)

`utils/database/` holds the queries; this holds what they run against. The
schema used to be one 950-line `init_db()` of `CREATE TABLE IF NOT EXISTS` and
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, every statement of it idempotent
because every statement of it ran on **every boot** — 127 queries to discover
that nothing had changed. It is now a numbered list of migrations, applied once
each and written down in `schema_version`, and a boot with nothing to do is one
`SELECT`.

### Migration 1 is the old `init_db()`, unchanged

That is the whole reason this was safe to introduce. On a database that already
has that schema every statement in it is a no-op, exactly as it was on
yesterday's boot; it is then recorded and never runs again. On an empty one it
builds everything. So the first boot after this shipped did precisely what the
boot before it did, and every boot since does nothing.

Verified against a real PostgreSQL 16 before it was merged, and worth repeating
if the runner is ever touched: a **fresh** database and an **existing populated**
one end up with byte-identical schemas (`pg_dump --schema-only`), the populated
one's rows are untouched, and the one data-writing statement in the baseline
(the `handover_fired` backfill) does not fire a second time.

### Three rules

- **A shipped migration is never edited.** Editing one changes nothing on a
  database that has already run it, so the code and that database silently
  disagree from then on. A change to the schema is always a *new* migration.
  `tests/test_schema.py` hashes migration 1 for exactly this reason — the
  failure message says to add one instead.
- **A migration runs inside a transaction**, with `lock_timeout` set.
  PostgreSQL has transactional DDL, so a migration that fails halfway leaves
  the database as it was — which the old `init_db()` could not promise. A
  migration that cannot get its locks fails the boot rather than holding every
  other query behind it; Railway restarts, and by then the container it was
  waiting on has gone.
- **Nothing creates a table outside the list.** Otherwise a database can be
  up to date and still be missing something. A test reads `init_db()` and
  checks.

### The column that had to be searched

`tac_maps.doc` holds a whole map, and one query has to look **inside** it:
`tac_terrain_usage()`, which decides whether deleting a terrain is refused.
Getting that wrong in the lenient direction leaves every plan drawn on that
terrain rendering on an empty sheet, so it is worth being exactly right.

It was a substring match over the text column:

```sql
WHERE doc LIKE '%' || '"/t/7"' || '%'
```

Two things wrong with asking it that way, and the second is a bug somebody
could hit:

- **A leading wildcard can use no index**, so it read every map in the table.
  Measured on 20,008 maps: 22.5 ms and a sequential scan, against a bitmap
  index lookup now.
- **It matched the characters, not the map.** A *label* that is exactly `/t/7`
  serialises to `"label": "/t/7"`, which contains the pattern — so that map
  counted as usage and the terrain could never be deleted, with nothing to say
  why. Verified against PostgreSQL 16 before and after: the old query named
  that map, the new one does not. (A label like `see "/t/7"` did *not* trigger
  it, because JSON escapes the quotes — which is what made the bug narrow
  enough to go unnoticed.)

Migration 3 makes the column `jsonb`, so the query asks what it means —
`doc -> 'background' ->> 'url' = $1` — behind
`idx_tac_maps_background_url`.

**Two details decided by measurement rather than by reading:**

- **The index is not partial.** `WHERE … LIKE '/t/%'` — index only the maps on
  a terrain this bot serves — looks obviously right and produces an index the
  query cannot use: PostgreSQL does not infer that `= '/t/7'` implies
  `LIKE '/t/%'`. It stayed a sequential scan, indistinguishable from having no
  index, which is the kind of thing that is never noticed.
- **Reads did not change.** asyncpg hands a `jsonb` column back as `str`, the
  same as `text`, so every reader — `parse()` included — is untouched. The
  three writers take an explicit `$n::jsonb`.

**What a `jsonb` conversion can fail on, and what was done about it.** The
cast runs over every row the product has ever stored, inside the migration,
inside the boot — so a row it refuses takes the bot down for everybody:

- **`jsonb` refuses `\u0000`** ("unsupported Unicode escape sequence") where
  `text` and Python's `json` both accept it. A label is typed by anybody with
  an edit link and `tacmap._text()` did not strip control characters until
  this shipped, so the migration repairs those rows before converting, and
  `_text()` is what stops new ones
  ([docs/tacmap.md](tacmap.md#what-a-label-may-not-contain)).
- **Anything still not valid JSON is not a map.** `parse()` cannot read it
  either, so that map already opened empty; its text goes to `doc_unreadable`
  and `'{}'` takes its place. Keeping the original matters — blanking it
  outright would be destroying somebody's plan on the strength of a guess.
  **And it says so in the log, naming each map and its guild.** It used to
  `RAISE WARNING`, which reached nobody: asyncpg drops server notices unless a
  connection has `add_log_listener()` attached and `init_db()` attaches none —
  checked against a real server both ways. Setting a map's text aside rather
  than failing the boot is only defensible if somebody finds out.

Checked against a real PostgreSQL 16, which is the only way any of the above
is knowable: a **fresh** database and one **upgraded from version 2** end with
**byte-identical schemas** (`pg_dump --schema-only`), the upgraded one's rows
and their `doc_rev` are untouched, a row carrying a NUL converts after repair,
a row of garbage is set aside, and two further boots change nothing.

### The lock, and why the table is made inside it

Railway can have the new container up before the old one is down, so `migrate()`
does its work under a `pg_advisory_lock`: whoever arrives second finds nothing
left to do.

`schema_version` is created **inside** that lock rather than before it, and that
is not tidiness. `CREATE TABLE IF NOT EXISTS` checks and then creates, and two
sessions doing it at the same instant is not a no-op for the loser but
`duplicate key value violates unique constraint "pg_type_typname_nsp_index"` out
of the catalogue — a crashed boot. Eight simultaneous boots against an empty
database is what found it, and is what the fix was checked with.

So the fast path asks for the versions straight out and treats *undefined table*
as "never migrated": one statement in the steady state, and every write
serialised behind the lock.

---
