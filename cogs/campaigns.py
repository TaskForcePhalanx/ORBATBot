"""Posting the campaign planner's drops (milestone 4).

Every minute the loop looks at the missions dated around now, works out each
drop's state with `campaign.drop_state()` and does what that says:

    post    an approved drop whose time has come goes out in the drop channel,
            split into parts and with its images attached
    edit    an approved change to a drop already posted edits those messages
    ping    a drop that is due but not approved (or changed since) is held,
            and the mission team channel is told once

When the drop that opens the ORBAT posts for the first time, sign-ups start on
the mission's linked ORBAT through `start_operation()` — the same call as
`/setup-slots orbat:` — unless there is no ORBAT or another operation is still
to be played. **A running operation is never replaced**; the team is told and
can start sign-ups by hand from the planner.

A test campaign's drops go to the mission team channel only (or nowhere), and
a test campaign never starts an operation.

The same loop links each played mission's OCAP recording when there is exactly
one candidate (`match_recordings()`), and marks a campaign Complete once every
planned mission is over (`complete_campaigns()`).

Not a Discord feature to type: there are no commands. `mission_drops()` is the
one place a drop's state is worked out; the web pages call it too, so the
drops panel says exactly what the loop is about to do.
"""

import logging
import asyncio
import io
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks

from utils import campaign as rules
from utils import database

log = logging.getLogger(__name__)

# How far around today the loop looks. The WARNO goes out at D-28 and the AAR
# at D+1; the margin covers a drop held for a few days and approved late.
LOOK_BACK_DAYS = 7
LOOK_AHEAD_DAYS = 35

# After a post or an edit fails (a channel gone, a permission taken away), the
# loop leaves that drop alone for this long rather than failing every minute.
FAILURE_BACKOFF = timedelta(minutes=30)

NO_MENTIONS = discord.AllowedMentions.none()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Working out what a mission's drops are doing — shared with web/campaigns.py
# ---------------------------------------------------------------------------

async def post_context(guild_id: str, plan: dict, tz: str) -> rules.PostContext:
    channels = await database.get_guild_channels(guild_id)
    orbat = channels.get('orbat')
    return rules.PostContext(settings=plan, guild_tz=tz,
                             orbat_channel=f'<#{orbat}>' if orbat else '#orbat')


async def mission_drops(guild_id: str, mission: dict, *, is_test: bool, plan: dict = None,
                        tz: str = None, now: datetime = None) -> dict:
    """Everything about one mission's six drops: the text, its parts, images,
    when it is due, the stored row and the state. `{'data', 'plan', 'tz',
    'channel', 'drops'}`."""
    plan = plan or rules.settings_with_defaults(await database.get_cp_settings(guild_id))
    tz = tz or await database.get_guild_timezone(guild_id)
    now = now or _now()
    data = {**mission['fields'], 'phases': await database.get_cp_phases(mission['id'])}
    ctx = await post_context(guild_id, plan, tz)
    times = rules.drop_times(data, plan, tz)
    rows = await database.get_cp_drops(mission['id'])
    channel = rules.drop_channel(plan, is_test)

    drops = []
    for drop in rules.DROPS:
        body = rules.build_post(data, drop.key, ctx)
        images = rules.drop_images(data, drop.key)
        digest = rules.approval_hash(body, images)
        row = rows.get(drop.key)
        due = times.get(drop.key)
        drops.append({
            'key': drop.key, 'label': drop.label, 'step': drop.step,
            'text': body, 'chars': len(body), 'parts': rules.split_message(body),
            'images': images, 'hash': digest, 'due': due, 'row': row,
            'state': rules.drop_state(row, current_hash=digest, due=due, now=now,
                                      channel=channel),
        })
    return {'data': data, 'plan': plan, 'tz': tz, 'channel': channel, 'drops': drops}


def site_url(bot, path: str) -> str:
    """A page on the website, or '' with no website."""
    web = getattr(bot, 'web', None)
    if web is None:
        return ''
    try:
        return web.config.public_url(path)
    except Exception:
        return ''


def planner_url(bot, mission: dict) -> str:
    """The mission's schedule page on the website, or '' with no website."""
    return site_url(bot, f"/g/{mission['guild_id']}/campaigns/{mission['campaign_id']}"
                         f"/missions/{mission['id']}/schedule")


async def _text_channel(bot, channel_id):
    if not channel_id:
        return None
    channel = bot.get_channel(int(channel_id))
    if channel is None:
        try:
            channel = await bot.fetch_channel(int(channel_id))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None
    return channel


async def tell_team(bot, plan: dict, text: str) -> bool:
    """Post a note in the mission team channel. False when there is none."""
    channel = await _text_channel(bot, plan.get('team_channel_id'))
    if channel is None:
        log.info(f"ℹ️ campaigns: no team channel for: {text}")
        return False
    try:
        await channel.send(text, allowed_mentions=NO_MENTIONS)
        return True
    except discord.HTTPException as e:
        log.warning(f"⚠️ campaigns: could not tell the team channel: {e}")
        return False


# ---------------------------------------------------------------------------
# Posting and editing
# ---------------------------------------------------------------------------

async def _files(campaign_id: int, image_ids: list, limit_bytes: int) -> list:
    """The drop's images as message groups of discord.File, in order. An image
    deleted since it was attached is left out."""
    meta = await database.cp_image_meta(campaign_id, image_ids)
    blobs = await database.cp_image_data(campaign_id, image_ids)
    present = [i for i in image_ids if int(i) in blobs]
    groups = rules.image_groups([len(blobs[int(i)]) for i in present], limit_bytes=limit_bytes)
    return [[discord.File(io.BytesIO(blobs[int(present[k])]),
                          filename=meta[int(present[k])]['filename'])
             for k in group] for group in groups]


async def _messages(campaign_id: int, drop: dict, channel) -> list:
    """What a drop goes out as: (content, files) per message. The images ride
    on the last text part; any that do not fit follow in messages of their own."""
    limit = getattr(getattr(channel, 'guild', None), 'filesize_limit', 10 * 1024 * 1024)
    groups = await _files(campaign_id, drop['images'], limit)
    out = [[part, []] for part in drop['parts']]
    if groups:
        out[-1][1] = groups[0]
        out += [[None, group] for group in groups[1:]]
    return out


async def publish_drop(bot, mission: dict, drop: dict, channel_id: str) -> list:
    """Send a drop and return its message ids."""
    channel = await _text_channel(bot, channel_id)
    if channel is None:
        raise RuntimeError(f"I can't see the drop channel ({channel_id}).")
    ids = []
    try:
        for content, files in await _messages(mission['campaign_id'], drop, channel):
            message = await channel.send(content=content, files=files or None,
                                         allowed_mentions=NO_MENTIONS)
            ids.append(message.id)
    except Exception:
        # Half a drop is worse than none: take back what did go out, so the
        # retry posts it whole rather than repeating the first part.
        await delete_messages(bot, [(channel.id, i) for i in ids])
        raise
    return ids


async def update_drop(bot, mission: dict, drop: dict) -> tuple[str, list]:
    """Edit a posted drop to what was approved: messages are edited in place,
    any extra are sent after them, and any no longer needed are deleted. A
    message deleted in Discord meanwhile is sent again."""
    row = drop['row']
    channel = await _text_channel(bot, row.get('channel_id'))
    if channel is None:
        raise RuntimeError("I can't see the channel this drop was posted in any more.")
    wanted = await _messages(mission['campaign_id'], drop, channel)
    old = list(row.get('message_ids') or [])
    ids = []
    for i, (content, files) in enumerate(wanted):
        if i < len(old):
            try:
                await channel.get_partial_message(int(old[i])).edit(
                    content=content or '', attachments=files, allowed_mentions=NO_MENTIONS)
                ids.append(int(old[i]))
                continue
            except discord.NotFound:
                pass
        message = await channel.send(content=content, files=files or None,
                                     allowed_mentions=NO_MENTIONS)
        ids.append(message.id)
    for surplus in old[len(wanted):]:
        try:
            await channel.get_partial_message(int(surplus)).delete()
        except discord.HTTPException:
            pass
    return str(channel.id), ids


# Publishing tasks still running, so they are not garbage-collected mid-way.
_publishing: set = set()


def publish_later(bot, plan: dict, channel_id, message_ids, what: str) -> None:
    """Publish a drop's messages when the drop channel is an Announcement
    channel — without that, servers that Follow it never receive them.

    In the background, never inside the mission lock: Discord allows only a
    handful of publishes per channel per hour, and discord.py waits out that
    limit (up to an hour) rather than failing, which would stall every other
    drop if it ran in the loop itself."""
    if not message_ids:
        return
    task = asyncio.create_task(_publish(bot, plan, channel_id, list(message_ids), what))
    _publishing.add(task)
    task.add_done_callback(_publishing.discard)


async def _publish(bot, plan: dict, channel_id, message_ids: list, what: str) -> None:
    try:
        channel = await _text_channel(bot, channel_id)
        if channel is None or not getattr(channel, 'is_news', lambda: False)():
            return                  # an ordinary channel has nothing to publish
        failed = None
        for message_id in message_ids:
            try:
                await channel.get_partial_message(int(message_id)).publish()
            except discord.HTTPException as e:
                if getattr(e, 'code', None) == 40033:     # already published
                    continue
                failed = e
        if failed is not None:
            log.warning(f"⚠️ campaigns: publishing {what} failed: {failed!r}")
            await tell_team(bot, plan,
                            f"⚠️ {what} posted in {channel.mention}, but I couldn't publish it, "
                            f"so servers following the channel didn't get it: {failed}. "
                            'Publish it by hand from the message menu.')
    except Exception as e:          # a background task has nobody to raise to
        log.exception(f"❌ campaigns: publishing {what} failed: {e!r}")


async def delete_messages(bot, pairs) -> int:
    """Delete posted drop messages — what clearing a test campaign away takes
    out of Discord. Returns how many went; one already gone counts as gone."""
    gone = 0
    for channel_id, message_id in pairs:
        channel = await _text_channel(bot, channel_id)
        if channel is None:
            continue
        try:
            await channel.get_partial_message(int(message_id)).delete()
            gone += 1
        except discord.NotFound:
            gone += 1
        except discord.HTTPException:
            pass
    return gone


# ---------------------------------------------------------------------------
# Sign-ups
# ---------------------------------------------------------------------------

async def start_signups(bot, mission: dict, plan: dict, tz: str, *, is_test: bool) -> tuple[bool, str]:
    """Start the operation for a mission's sign-ups, on its linked ORBAT with
    H-Hour as the start. (started, what happened) — the reason is meant for
    the team channel or the page. Never replaces an operation still to be
    played, and never runs for a test campaign."""
    from cogs.admin import start_operation
    from cogs.slots import ActionError

    if is_test:
        return False, 'This is a test campaign, so sign-ups were not started.'
    if mission.get('operation_id'):
        return False, 'Sign-ups for this mission have already been started.'
    if not mission.get('orbat_id'):
        return False, 'No ORBAT is linked to this mission, so sign-ups could not start.'
    guild = bot.get_guild(int(mission['guild_id']))
    if guild is None:
        return False, "I'm not in that server any more."
    active = await database.get_active_operation(str(guild.id))
    if rules.operation_running(dict(active) if active else None, _now()):
        return False, (f"{active['name']} is still running, and I never replace a running "
                       'operation. Start sign-ups from the planner once it is over.')
    data = {**mission['fields']}
    try:
        result = await start_operation(
            bot, guild, orbat_id=mission['orbat_id'],
            name=rules.text(data, 'name') or mission['name'],
            event_time=rules.h_hour_utc(data, plan, tz))
    except ActionError as e:
        return False, str(e)
    op = result['operation']
    await database.set_cp_mission_operation(mission['id'], op['id'])
    where = f" in {result['channel'].mention}" if result['channel'] else ''
    return True, f"Sign-ups are open for {op['name']}{where} ({result['slot_count']} slots)."


# ---------------------------------------------------------------------------
# After the op: which OCAP recording is the mission (milestone 6)
# ---------------------------------------------------------------------------

async def candidate_recordings(op_date) -> list:
    """The recordings that could be a mission played on `op_date`."""
    if not op_date:
        return []
    day = op_date if not isinstance(op_date, str) else rules.parse_date(op_date)
    if day is None:
        return []
    found = await database.kt_recordings_between(
        (day - timedelta(days=1)).isoformat(), (day + timedelta(days=1)).isoformat())
    return rules.recording_candidates(day, found)


async def match_recordings(today) -> list[str]:
    """Link the recording to every mission of the past week that has an
    unambiguous one (`campaign.auto_recording()`), which also puts it in the
    campaign's Kill Tracker campaign. A mission somebody already decided for
    is left alone. The Kill Tracker counts recordings every ten minutes, so a
    mission is tried again each minute until its recording turns up."""
    done = []
    for mission in await database.cp_missions_awaiting_recording(
            today - timedelta(days=LOOK_BACK_DAYS), today):
        found = await candidate_recordings(mission['op_date'])
        ocap_id = rules.auto_recording(mission['op_date'], found)
        if ocap_id:
            await database.set_cp_mission_recording(mission['id'], ocap_id, manual=False)
            done.append(f"linked recording {ocap_id} to mission {mission['id']}")
    return done


async def _complete_campaign(bot, campaign: dict, today) -> bool:
    guild_id = str(campaign['guild_id'])
    if not guild_id.isdigit() or bot.get_guild(int(guild_id)) is None:
        return False
    if not rules.campaign_finished(campaign, campaign['missions'], today):
        return False
    if not await database.auto_complete_cp_campaign(campaign['id']):
        return False
    plan = rules.settings_with_defaults(await database.get_cp_settings(guild_id))
    count = len(campaign['missions'])
    url = site_url(bot, f"/g/{guild_id}/campaigns/{campaign['id']}")
    await tell_team(bot, plan,
                    f"🏁 **{campaign['name']}** is complete: all {count} "
                    f"mission{'s' if count != 1 else ''} played. I've marked it Complete; "
                    'set it back to Active on the campaign page to keep it open.'
                    + (f"\n{url}" if url else ''))
    return True


async def complete_campaigns(bot, today) -> list[str]:
    """Mark every campaign whose missions are all over Complete
    (`campaign.campaign_finished()`), and tell its mission team. Skipped on a
    server that switched it off in the planner settings."""
    done = []
    for campaign in await database.cp_campaigns_may_complete():
        try:
            if await _complete_campaign(bot, campaign, today):
                done.append(f"marked campaign {campaign['id']} complete")
        except Exception as e:      # one campaign must not stop the others
            log.exception(f"❌ campaigns: completing campaign {campaign['id']} failed: {e!r}")
    return done


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------

class CampaignsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._lock = asyncio.Lock()
        self._failed: dict = {}          # (mission_id, kind) → when it last failed
        self.drops.start()

    def cog_unload(self):
        self.drops.cancel()

    async def process_mission(self, mission: dict) -> list[str]:
        """Do whatever one mission's drops are waiting for. Returns one line
        per thing done. Serialised, so the loop and an approval on the website
        can never post the same drop twice."""
        async with self._lock:
            return await self._process(mission)

    async def process_mission_id(self, mission_id: int) -> list[str]:
        mission = await database.get_cp_mission(mission_id)
        if mission is None:
            return []
        campaign = await database.get_cp_campaign(mission['campaign_id'])
        if campaign is None or campaign['status'] == 'archived':
            return []               # an archived campaign posts nothing
        mission['is_test'] = bool(campaign['is_test'])
        return await self.process_mission(mission)

    async def _process(self, mission: dict) -> list[str]:
        # Re-read inside the lock: what the caller held may be a minute old.
        fresh = await database.get_cp_mission(mission['id'])
        if fresh is None:
            return []
        mission = {**fresh, 'is_test': bool(mission.get('is_test'))}
        is_test = mission['is_test']
        state = await mission_drops(mission['guild_id'], mission, is_test=is_test)
        plan, tz, channel = state['plan'], state['tz'], state['channel']
        name = mission['name']
        done = []
        now = _now()

        for drop in state['drops']:
            action = drop['state']['action']
            key = (mission['id'], drop['key'])
            if action is None:
                continue
            if action in ('post', 'edit') and now - self._failed.get(key, now - FAILURE_BACKOFF) < FAILURE_BACKOFF:
                continue
            try:
                if action == 'post':
                    ids = await publish_drop(self.bot, mission, drop, channel)
                    await database.mark_cp_drop_posted(mission['id'], drop['key'], channel,
                                                       ids, drop['hash'])
                    done.append(f"posted {drop['label']} for {name}")
                    if not is_test:
                        publish_later(self.bot, plan, channel, ids,
                                      f"**{name}** — {drop['label']}")
                    if drop['key'] == plan['orbat_opens'] and not is_test:
                        started, note = await start_signups(self.bot, mission, plan, tz,
                                                            is_test=is_test)
                        done.append(note)
                        if not started:
                            url = planner_url(self.bot, mission)
                            await tell_team(self.bot, plan,
                                            f"⚠️ **{name}**: {drop['label']} has posted, but "
                                            f"sign-ups did not start. {note}"
                                            + (f"\n{url}" if url else ''))
                elif action == 'edit':
                    before = {int(i) for i in (drop['row'].get('message_ids') or [])}
                    channel_id, ids = await update_drop(self.bot, mission, drop)
                    await database.mark_cp_drop_posted(mission['id'], drop['key'], channel_id,
                                                       ids, drop['hash'])
                    # Edits reach followers by themselves; a part sent new
                    # (the drop grew, or one was deleted in Discord) does not.
                    if not is_test:
                        publish_later(self.bot, plan, channel_id,
                                      [i for i in ids if int(i) not in before],
                                      f"**{name}** — {drop['label']}")
                    done.append(f"updated {drop['label']} for {name}")
                elif action == 'ping':
                    await database.mark_cp_drop_pinged(mission['id'], drop['key'])
                    why = ('it has changed since it was approved'
                           if drop['state']['key'] == 'heldchange' else 'nobody has approved it')
                    url = planner_url(self.bot, mission)
                    await tell_team(
                        self.bot, plan,
                        f"⏸️ **{name}** — {drop['label']} was due "
                        f"{rules.discord_ts(drop['due'], 'R')} and is held: {why}. "
                        'It posts the minute it is approved.' + (f"\n{url}" if url else ''))
                    done.append(f"held {drop['label']} for {name}")
                self._failed.pop(key, None)
            except Exception as e:
                self._failed[key] = now
                log.warning(f"⚠️ campaigns: {action} {drop['key']} for mission {mission['id']} "
                      f"failed: {e!r}")
                if action in ('post', 'edit'):
                    await tell_team(self.bot, plan,
                                    f"⚠️ **{name}**: I couldn't {action} {drop['label']}: {e}. "
                                    'I will try again in half an hour.')
        return done

    @tasks.loop(minutes=1)
    async def drops(self):
        today = _now().date()
        try:
            missions = await database.cp_missions_near(
                today - timedelta(days=LOOK_BACK_DAYS), today + timedelta(days=LOOK_AHEAD_DAYS))
        except Exception as e:
            log.exception(f"❌ campaigns: could not read the missions: {e!r}")
            return
        for mission in missions:
            if self.bot.get_guild(int(mission['guild_id'])) is None:
                continue
            try:
                for line in await self.process_mission(mission):
                    log.info(f"✅ campaigns: {line}")
            except Exception as e:
                log.exception(f"❌ campaigns: mission {mission['id']} failed: {e!r}")
        try:
            for line in await match_recordings(today):
                log.info(f"✅ campaigns: {line}")
        except Exception as e:
            log.exception(f"❌ campaigns: matching recordings failed: {e!r}")
        try:
            for line in await complete_campaigns(self.bot, today):
                log.info(f"✅ campaigns: {line}")
        except Exception as e:
            log.exception(f"❌ campaigns: completing campaigns failed: {e!r}")

    @drops.error
    async def _drops_error(self, error: BaseException):
        log.error(f"❌ campaigns loop stopped on {error!r}; restarting it.")
        self.drops.restart()

    @drops.before_loop
    async def _before_drops(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(CampaignsCog(bot))
