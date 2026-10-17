"""Keeping the Kill Tracker's numbers up to date from the OCAP server.

Every few minutes the loop asks the OCAP server which recordings exist, reads
the finished ones it has not counted — or counted under older rules — and stores
the per-player rows. Nobody uploads anything. See `utils/killtracker.py` for the
counting and `utils/ocap.py` for what is read and when a recording is due.

Not a Discord feature: there are no commands, and the numbers are served by
`web/killtracker.py` without a login. This is a cog only because the bot's event
loop is what runs periodic work here, and `setup_hook` is where it is started.

`check()` is the whole implementation, as `check_feed()` is for Reddit: the loop
calls it, and so would anything else that wanted a manual run.
"""

import logging
import asyncio
import os
import tempfile
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks

from utils import database, killtracker, ocap, promotion

log = logging.getLogger(__name__)

# How often the OCAP server is asked. Recordings appear once a week or so; the
# interval only decides how long after a mission the numbers show up.
POLL_MINUTES = 10

# The longest one recording may take, download and count together. Nothing should
# need anywhere near this; it is what stops one stuck read from holding up the loop
# for good (a loop does not start its next pass until the last one is over).
IMPORT_TIMEOUT = 30 * 60

# How many recordings one pass reads. The first pass over a server with sixty
# recordings is the only one that comes near this; it simply takes a few polls.
MAX_PER_PASS = 8

_FALSEY = ('0', 'false', 'no', 'off')


def enabled() -> bool:
    """On unless `KT_SYNC` says otherwise."""
    return (os.getenv('KT_SYNC') or '1').strip().lower() not in _FALSEY


def _now() -> datetime:
    """Naive UTC, like every stored time here."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _count(path: str, source_name: str, exclude=frozenset()) -> tuple:
    """Read and count one downloaded recording. CPU-bound, so it runs in a thread."""
    import json

    result = killtracker.compute(killtracker.open_recording(path), source_name, exclude)
    return (result['mission'], json.dumps(result['rows'], ensure_ascii=False),
            result['kills'], json.dumps(result['far'], ensure_ascii=False))


async def import_one(base: str, op: dict) -> dict:
    """Fetch, count and store one recording. Raises `ocap.OcapError` or whatever
    reading it raised; the caller records that."""
    fd, path = tempfile.mkstemp(suffix='.json.gz', prefix='ocap-')
    os.close(fd)
    try:
        sha = await ocap.download_recording(base, op['filename'], path)
        # Kills an admin struck from the distance statistics are applied here, so
        # a re-count never undoes a decision.
        exclude = (await database.kt_exclusions(op['id'])).get(op['id'], set())
        label, rows_json, kills, far_json = await asyncio.to_thread(
            _count, path, f"{op['filename']}.json.gz", exclude)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    await database.kt_save_mission(
        op, label=label, rows_json=rows_json, counted_kills=kills,
        rules_version=killtracker.RULES_VERSION, sha256=sha, far_json=far_json)
    return {'label': label, 'kills': kills}


def _describe(error: BaseException) -> str:
    """What went wrong, in words: some exceptions (a timeout) have an empty message."""
    if isinstance(error, asyncio.TimeoutError):
        return f'took longer than {IMPORT_TIMEOUT // 60} minutes'
    return str(error) or type(error).__name__


async def check(base: str = None, limit: int = MAX_PER_PASS) -> dict:
    """One pass: list the server, read what is due. Returns what happened.

    Raises `ocap.OcapError` when the list itself cannot be read. A recording that
    cannot be read is recorded (with a back-off) and does not stop the others.
    """
    base = base or ocap.base_url()
    ops = await ocap.fetch_operations(base)
    known = await database.kt_known()
    every_due = ocap.plan(ops, known, _now(), killtracker.RULES_VERSION, len(ops))
    due = every_due[:limit]

    done, failed = [], []
    for op in due:
        try:
            result = await asyncio.wait_for(import_one(base, op), IMPORT_TIMEOUT)
        except Exception as e:  # one bad file must not stop the rest
            attempts = (known[op['id']]['attempts'] if op['id'] in known else 0) + 1
            delay = ocap.retry_delay_minutes(attempts)
            why = _describe(e)
            await database.kt_save_failure(
                op, error=why[:500], retry_at=_now() + timedelta(minutes=delay))
            failed.append((op['filename'], why))
            continue
        done.append((op['filename'], result))
        # The server counts kills its own way, so a difference is not an error —
        # but a large one is the first sign that the rules and the game disagree.
        if op['player_kills'] and abs(result['kills'] - op['player_kills']) > max(5, op['player_kills'] // 10):
            log.warning(f"⚠️ killtracker {op['filename']}: counted {result['kills']} kills, "
                  f"the OCAP server says {op['player_kills']}.")
    now = _now()
    return {
        'listed': len(ops),
        'finished': len([op for op in ops if op['completed']]),
        'stored': len([r for r in known.values() if r['status'] == 'ok']),
        'backing_off': len([r for r in known.values()
                            if r['status'] == 'failed' and r['retry_at'] and r['retry_at'] > now]),
        'imported': done, 'failed': failed, 'pending': len(every_due) - len(due),
    }


# -- promotion: Cadet → Member after enough missions -------------------------
#
# The rules are `utils/promotion.py`; this is the part that touches Discord. The
# loop and the web page both go through these three functions, so a promotion
# is the same thing whoever starts it.

async def due_for_promotion(guild: discord.Guild, settings: dict = None) -> list:
    """Everyone in `guild` who is due a promotion right now."""
    settings = settings or await database.kt_promotion_settings(guild.id)
    if not (settings['enabled'] and settings['from_role_id'] and settings['to_role_id']):
        return []
    aliases = await database.kt_aliases()
    people = killtracker.player_missions(await database.kt_rows(), aliases)
    mapped = await database.kt_player_members(guild.id)
    roles = {}
    for user_id in set(mapped.values()):
        member = guild.get_member(int(user_id))
        if member is not None:
            roles[user_id] = {str(r.id) for r in member.roles}
    return promotion.candidates(people, mapped, roles, settings,
                                await database.kt_promotion_log(guild.id))


async def promote_members(guild: discord.Guild, user_ids, settings: dict) -> tuple:
    """Swap the from-role for the to-role. Returns `(promoted ids, problems)`;
    a problem is a sentence for the admin, and the member it names is left as
    they were."""
    from_role = guild.get_role(int(settings['from_role_id'] or 0))
    to_role = guild.get_role(int(settings['to_role_id'] or 0))
    if from_role is None or to_role is None:
        return [], ['One of the two roles no longer exists; pick them again.']
    if not guild.me.guild_permissions.manage_roles or to_role >= guild.me.top_role:
        return [], [f'I cannot hand out **{to_role.name}**: I need Manage Roles and a role '
                    'above it in Server Settings → Roles.']
    promoted, problems = [], []
    for user_id in user_ids:
        member = guild.get_member(int(user_id))
        if member is None:
            problems.append(f'<@{user_id}> is not on the server any more.')
            continue
        try:
            await member.add_roles(to_role, reason='Kill Tracker: enough missions')
            if from_role in member.roles:
                await member.remove_roles(from_role, reason='Kill Tracker: enough missions')
        except discord.HTTPException as e:
            problems.append(f'{member.display_name}: Discord refused ({e.status}).')
            continue
        promoted.append(str(user_id))
    if promoted:
        await database.kt_set_promotion(guild.id, promoted, promotion.PROMOTED)
    return promoted, problems


async def _tell_admins(guild: discord.Guild, settings: dict, text: str, view=None) -> None:
    channel = guild.get_channel(int(settings['channel_id'] or 0))
    if channel is None or not channel.permissions_for(guild.me).send_messages:
        return
    try:
        await channel.send(text[:2000], view=view,
                           allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        log.warning(f'⚠️ killtracker: could not post the promotion note in #{channel.name}')


# One message per person, so each has its own Approve / Reject. A first pass over
# a long backlog posts this many and the rest on the next pass, rather than
# flooding the channel at once.
MAX_ASKS_PER_PASS = 15


class PromotionButton(discord.ui.DynamicItem[discord.ui.Button],
                      template=r'ktpromo:(?P<action>approve|reject):(?P<user>\d+)'):
    """Approve or Reject under a "due for promotion" message.

    A dynamic item rather than a view per message: the member's id is in the
    button's `custom_id`, so a button keeps working after a restart without
    anything having to be re-registered for it. Admins only — the channel may be
    readable by more people than that."""

    def __init__(self, action: str, user_id):
        approve = action == 'approve'
        super().__init__(discord.ui.Button(
            label='Approve' if approve else 'Reject',
            style=discord.ButtonStyle.success if approve else discord.ButtonStyle.danger,
            custom_id=f'ktpromo:{action}:{user_id}'))
        self.action, self.user_id = action, str(user_id)

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match['action'], match['user'])

    async def callback(self, interaction: discord.Interaction):
        perms = interaction.user.guild_permissions
        if not (perms.manage_guild or perms.administrator):
            await interaction.response.send_message(
                'Only a server admin can decide a promotion.', ephemeral=True)
            return
        guild = interaction.guild
        settings = await database.kt_promotion_settings(guild.id)
        who = interaction.user.display_name
        if self.action == 'reject':
            await database.kt_set_promotion(guild.id, [self.user_id], promotion.DISMISSED)
            outcome = f'❌ Passed over by {who}.'
        else:
            due = {d['user_id'] for d in await due_for_promotion(guild, settings)}
            if self.user_id not in due:
                outcome = f'ℹ️ No longer due; nothing done ({who}).'
            else:
                promoted, problems = await promote_members(guild, [self.user_id], settings)
                if not promoted:
                    await interaction.response.send_message(
                        ' '.join(problems) or 'That did not work.', ephemeral=True)
                    return
                outcome = f'✅ Promoted by {who}.'
        await interaction.response.edit_message(
            content=f'{interaction.message.content}\n{outcome}', view=None)


def _ask_view(user_id) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(PromotionButton('approve', user_id))
    view.add_item(PromotionButton('reject', user_id))
    return view


def _names(due: list) -> str:
    return '\n'.join(f"• <@{d['user_id']}> ({d['players'][0]}): {d['missions']} missions"
                     for d in due)


async def run_promotions(bot: commands.Bot) -> None:
    """One pass over every guild with promotions on. Tells the admins about each
    person once, and promotes them itself only when the guild chose `auto`."""
    for guild in bot.guilds:
        settings = await database.kt_promotion_settings(guild.id)
        if not settings['enabled']:
            continue
        due = await due_for_promotion(guild, settings)
        if settings['auto'] and due:
            promoted, problems = await promote_members(guild, [d['user_id'] for d in due], settings)
            done = [d for d in due if d['user_id'] in promoted]
            note = []
            if done:
                to_role = guild.get_role(int(settings['to_role_id']))
                note.append(f"🎖️ Promoted to **{to_role.name}** after {settings['missions']} missions:\n"
                            + _names(done))
            note += problems
            if note and (done or any(not d['notified'] for d in due)):
                await _tell_admins(guild, settings, '\n'.join(note))
            await database.kt_set_promotion(
                guild.id, [d['user_id'] for d in due if d['user_id'] not in promoted
                           and not d['notified']], promotion.NOTIFIED)
        elif due:
            fresh = [d for d in due if not d['notified']][:MAX_ASKS_PER_PASS]
            to_role = guild.get_role(int(settings['to_role_id']))
            for d in fresh:
                await _tell_admins(
                    guild, settings,
                    f"🎖️ <@{d['user_id']}> ({d['players'][0]}) has {d['missions']} missions "
                    f"({settings['missions']} needed) and is due for **{to_role.name}**.",
                    view=_ask_view(d['user_id']))
            await database.kt_set_promotion(
                guild.id, [d['user_id'] for d in fresh], promotion.NOTIFIED)


class KillTrackerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        if enabled():
            self.poll.start()

    def cog_unload(self):
        self.poll.cancel()

    @tasks.loop(minutes=POLL_MINUTES)
    async def poll(self):
        """`discord.ext.tasks` ends a loop for good on an unhandled exception, so
        everything is caught here: an OCAP server that is down for an evening must
        not mean the numbers stop for good."""
        try:
            result = await check()
        except ocap.OcapError as e:
            log.warning(f"⚠️ killtracker: {e}")
            return
        except Exception as e:
            log.exception(f"❌ killtracker failed: {e!r}")
            return
        # One line per pass, whatever happened: a log with nothing in it for hours
        # cannot be told from a loop that has stopped.
        log.info(f"ℹ️ killtracker: {result['listed']} on the server, {result['finished']} finished, "
              f"{result['stored']} stored before this pass; this pass counted "
              f"{len(result['imported'])}, failed {len(result['failed'])}; "
              f"{result['pending']} still waiting, {result['backing_off']} backing off.")
        for name, result_ in result['imported']:
            log.info(f"✅ killtracker: counted {name} → {result_['label']} ({result_['kills']} kills)")
        for name, error in result['failed']:
            log.warning(f"⚠️ killtracker: could not read {name}: {error}")
        try:
            await run_promotions(self.bot)
        except Exception as e:
            log.exception(f"❌ killtracker promotions failed: {e!r}")

    @poll.error
    async def _poll_error(self, error: BaseException):
        """`poll()` catches what it expects; this is for what it did not. A loop
        that raised is stopped for good by discord.py, so say so and start it again."""
        log.error(f"❌ killtracker loop stopped on {error!r}; restarting it.")
        self.poll.restart()

    @poll.before_loop
    async def _before_poll(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    bot.add_dynamic_items(PromotionButton)
    await bot.add_cog(KillTrackerCog(bot))
