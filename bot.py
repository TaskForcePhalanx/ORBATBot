import asyncio
import logging
import os
import sys
from datetime import timezone

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

from utils import database
from cogs.slots import ApprovalView, OrbatRequestButton, guild_channel
from cogs.gameroles import GameRolePanelView
from cogs.events import EventRsvpView, load_responses

log = logging.getLogger(__name__)

load_dotenv()


class ORBATBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        # Join and leave events are privileged. Requesting the intent while
        # "Server Members Intent" is unticked in the Developer Portal makes login
        # fail outright, which would take the whole bot down — so it is opt-in:
        # tick it there first, then set MEMBER_EVENTS=1. Bans and unbans are not
        # privileged and are logged either way.
        self.member_events = (os.getenv('MEMBER_EVENTS') or '').strip().lower() in (
            '1', 'true', 'yes', 'on'
        )
        if self.member_events:
            intents.members = True
        super().__init__(
            command_prefix='!',
            intents=intents,
            description='Arma 3 ORBAT Slot Management Bot',
        )
        # The optional web UI. Stays None unless it is configured — see _start_web().
        self.web = None

    async def setup_hook(self):
        import traceback
        log.info("--- setup_hook start ---")

        await database.init_db()
        log.info("✅ Database initialised.")

        # Voice intervals left open by a crash are closed at their last
        # heartbeat before anything new is recorded, so a restart can't leave a
        # session that looks like it has been running for days.
        try:
            dangling = await database.close_dangling_voice_sessions()
            if dangling:
                log.info(f"✅ Closed {dangling} voice session(s) left open by a restart.")
        except Exception:
            log.exception("❌ Failed to close dangling voice sessions:")
            traceback.print_exc()

        try:
            await self.load_extension('cogs.slots')
            log.info("✅ Loaded cogs.slots")
        except Exception:
            log.exception("❌ Failed to load cogs.slots:")
            traceback.print_exc()

        try:
            await self.load_extension('cogs.admin')
            log.info("✅ Loaded cogs.admin")
        except Exception:
            log.exception("❌ Failed to load cogs.admin:")
            traceback.print_exc()

        try:
            await self.load_extension('cogs.gameroles')
            log.info("✅ Loaded cogs.gameroles")
        except Exception:
            log.exception("❌ Failed to load cogs.gameroles:")
            traceback.print_exc()

        try:
            await self.load_extension('cogs.events')
            log.info("✅ Loaded cogs.events")
        except Exception:
            log.exception("❌ Failed to load cogs.events:")
            traceback.print_exc()

        try:
            await self.load_extension('cogs.voicelog')
            log.info("✅ Loaded cogs.voicelog")
        except Exception:
            log.exception("❌ Failed to load cogs.voicelog:")
            traceback.print_exc()

        try:
            await self.load_extension('cogs.memberlog')
            log.info("✅ Loaded cogs.memberlog"
                  + ("" if self.member_events else
                     " (joins/leaves off — set MEMBER_EVENTS=1 once the "
                     "Server Members Intent is enabled in the Developer Portal)"))
        except Exception:
            log.exception("❌ Failed to load cogs.memberlog:")
            traceback.print_exc()

        try:
            await self.load_extension('cogs.purge')
            log.info("✅ Loaded cogs.purge")
        except Exception:
            log.exception("❌ Failed to load cogs.purge:")
            traceback.print_exc()

        try:
            await self.load_extension('cogs.redditfeed')
            log.info("✅ Loaded cogs.redditfeed")
        except Exception:
            log.exception("❌ Failed to load cogs.redditfeed:")
            traceback.print_exc()

        try:
            await self.load_extension('cogs.campaigns')
            log.info("✅ Loaded cogs.campaigns")
        except Exception:
            log.exception("❌ Failed to load cogs.campaigns:")
            traceback.print_exc()

        try:
            await self.load_extension('cogs.killtracker')
            log.info("✅ Loaded cogs.killtracker")
        except Exception:
            log.exception("❌ Failed to load cogs.killtracker:")
            traceback.print_exc()

        registered = [c.name for c in self.tree.get_commands()]
        log.info(f"Commands registered in tree: {registered}")

        # Re-register approval views for all pending requests so buttons
        # continue to work after a bot restart.
        # Persistent ORBAT request button — one instance covers all guilds
        self.add_view(OrbatRequestButton(bot=self))
        # Persistent game-role panel button — likewise global
        self.add_view(GameRolePanelView(bot=self))

        pending = await database.get_all_pending_requests()
        for req in pending:
            self.add_view(ApprovalView(request_id=req['id'], bot=self))

        log.info(f"{len(pending)} pending view(s) restored.")

        # Re-register RSVP buttons for every event that is still open. The custom
        # ids encode each event's own response keys, so they have to be loaded.
        live_events = await database.get_live_events()
        for event in live_events:
            self.add_view(EventRsvpView(
                event_id=event['id'], bot=self,
                responses=await load_responses(event['id']),
            ))

        log.info(f"{len(live_events)} event view(s) restored.")

        self.reminder_task.start()
        log.info("✅ Reminder task started.")

        await self._start_web()
        log.info("--- setup_hook end ---")

    async def _start_web(self):
        """Start the browser UI, if it is configured.

        It shares this process and event loop, so a page can post a message or
        register a view through `self` directly. Every failure path here is
        non-fatal: the bot's own job must not depend on the website.
        """
        import traceback

        try:
            from web import WebServer, load_config
        except Exception:
            log.exception("ℹ️ Web UI dependencies missing (pip install -r requirements.txt) — skipped.")
            return

        try:
            config = load_config()
        except Exception:
            log.exception("❌ Web UI configuration is invalid — skipped:")
            traceback.print_exc()
            return
        if config.disabled:
            log.info("ℹ️ Web UI switched off with WEB_ENABLED=0.")
            return
        if not config.ready:
            log.info(
                "ℹ️ Web UI not configured — set "
                f"{', '.join(config.missing)} to enable it. Skipped."
            )
            return

        server = WebServer(self, config)
        try:
            await server.start()
        except Exception:
            log.exception("❌ Web UI failed to start:")
            traceback.print_exc()
            return

        self.web = server
        log.info(f"✅ Web UI listening on {config.host}:{config.port}"
              + (f" ({config.base_url})" if config.base_url else ""))

    async def close(self):
        # Record the time of everyone still in voice before the connection goes
        # down — a redeploy is the common case and must not lose it.
        cog = self.get_cog('VoiceLogCog')
        if cog is not None:
            try:
                await cog.flush_open_sessions()
            except Exception:
                pass
        if self.web is not None:
            await self.web.stop()
            self.web = None
        await super().close()

    @tasks.loop(minutes=1)
    async def reminder_task(self):
        # discord.ext.tasks stops a loop for good on an unhandled exception, so
        # one transient failure — a database blip during a redeploy, say — would
        # silently kill every operation reminder until the next restart.
        # Swallow per-operation so the rest of this tick still runs.
        try:
            ops = await database.get_operations_needing_reminder()
        except Exception as e:
            log.exception(f"reminder_task: could not load due reminders: {e!r}")
            return

        for op in ops:
            try:
                await self._send_operation_reminder(op)
            except Exception as e:
                log.exception(f"reminder_task: reminder for operation {op['id']} failed: {e!r}")

    async def _send_operation_reminder(self, op):
        await database.mark_reminder_fired(op['id'])
        members = await database.get_approved_member_ids(op['id'])
        if not members:
            return

        event_dt = op['event_time']
        if event_dt.tzinfo is None:
            event_dt = event_dt.replace(tzinfo=timezone.utc)
        event_ts = int(event_dt.timestamp())

        guild = discord.utils.get(self.guilds, id=int(op['guild_id']))
        if not guild:
            return

        # DM each approved member
        for member_id, slot_label in members:
            try:
                member = await guild.fetch_member(int(member_id))
                await member.send(
                    f"⏰ **Operation Reminder — {op['name']}**\n"
                    f"Your operation starts <t:{event_ts}:R> (<t:{event_ts}:F>).\n"
                    f"Your slot: **{slot_label}**\n"
                    f"Get ready!"
                )
            except (discord.Forbidden, discord.NotFound):
                pass

        # Ping all approved members where the board lives — #orbat unless an
        # admin pointed it somewhere else. Not created just to send a reminder.
        orbat_channel = await guild_channel(guild, 'orbat', create=False)
        if orbat_channel:
            mentions = ' '.join(
                f'<@{member_id}>' for member_id, _ in members
            )
            try:
                await orbat_channel.send(
                    f"⏰ **Operation reminder — {op['name']}** starts <t:{event_ts}:R>!\n"
                    f"{mentions}"
                )
            except (discord.Forbidden, discord.HTTPException):
                pass

    @reminder_task.before_loop
    async def before_reminder_task(self):
        await self.wait_until_ready()

    async def on_ready(self):
        log.info(f"on_ready fired. Guilds: {[g.name for g in self.guilds]}")
        # Copy global commands into each guild and sync — this is instant,
        # unlike global sync which can take up to an hour to propagate.
        for guild in self.guilds:
            try:
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                log.info(f"✅ Guild sync '{guild.name}': {len(synced)} command(s).")
            except Exception as e:
                log.exception(f"❌ Guild sync failed for '{guild.name}': {e}")


    async def on_guild_join(self, guild: discord.Guild):
        """Sync commands when the bot is added to a new server while already running."""
        try:
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info(f"✅ Joined '{guild.name}' — synced {len(synced)} command(s).")
        except Exception as e:
            log.exception(f"❌ Guild sync failed for '{guild.name}': {e}")


# Levels this project's own log lines are written at:
#   info      something happened that the next person reading the log wants
#   warning   something is set up wrong, and the bot carried on
#   error     an action failed
#   exception as error, with the traceback — what every `except` should use
LOG_LEVELS = ('__main__', 'cogs', 'utils', 'web')


def setup_logging():
    """Timestamped, levelled lines on stdout, which is what Railway reads.

    The root sits at WARNING and only this project's loggers go to INFO.
    discord.py is pinned explicitly, and that is the part worth keeping: the
    bot starts with `bot.start()` inside `asyncio.run()`, not `bot.run()`, so
    the library has never configured logging here and only its warnings have
    been reaching the log at all. Turning the root up to INFO switches on its
    gateway, heartbeat and reconnect stream and buries every line we write.

    `LOG_LEVEL` overrides the project's own level, so a quiet deployment can be
    turned up to DEBUG without a code change.
    """
    logging.basicConfig(
        level=logging.WARNING,
        format='%(asctime)s %(levelname)-8s %(name)s | %(message)s',
        datefmt='%H:%M:%S',
        stream=sys.stdout,
    )
    level = getattr(logging, os.getenv('LOG_LEVEL', 'INFO').upper(), logging.INFO)
    for name in LOG_LEVELS:
        logging.getLogger(name).setLevel(level)
    logging.getLogger('discord').setLevel(logging.WARNING)


def main():
    setup_logging()
    token = os.getenv('DISCORD_TOKEN')
    if not token:
        raise RuntimeError("DISCORD_TOKEN is not set. Check your .env file or Railway variables.")

    bot = ORBATBot()
    asyncio.run(bot.start(token))


if __name__ == '__main__':
    main()
