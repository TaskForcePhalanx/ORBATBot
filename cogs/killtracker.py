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

import asyncio
import os
import tempfile
from datetime import datetime, timedelta, timezone

from discord.ext import commands, tasks

from utils import database, killtracker, ocap

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


def _count(path: str, source_name: str) -> tuple:
    """Read and count one downloaded recording. CPU-bound, so it runs in a thread."""
    import json

    result = killtracker.compute(killtracker.open_recording(path), source_name)
    return result['mission'], json.dumps(result['rows'], ensure_ascii=False), result['kills']


async def import_one(base: str, op: dict) -> dict:
    """Fetch, count and store one recording. Raises `ocap.OcapError` or whatever
    reading it raised; the caller records that."""
    fd, path = tempfile.mkstemp(suffix='.json.gz', prefix='ocap-')
    os.close(fd)
    try:
        sha = await ocap.download_recording(base, op['filename'], path)
        label, rows_json, kills = await asyncio.to_thread(
            _count, path, f"{op['filename']}.json.gz")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    await database.kt_save_mission(
        op, label=label, rows_json=rows_json, counted_kills=kills,
        rules_version=killtracker.RULES_VERSION, sha256=sha)
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
            print(f"⚠️ killtracker {op['filename']}: counted {result['kills']} kills, "
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
            print(f"⚠️ killtracker: {e}")
            return
        except Exception as e:
            print(f"❌ killtracker failed: {e!r}")
            return
        # One line per pass, whatever happened: a log with nothing in it for hours
        # cannot be told from a loop that has stopped.
        print(f"ℹ️ killtracker: {result['listed']} on the server, {result['finished']} finished, "
              f"{result['stored']} stored before this pass; this pass counted "
              f"{len(result['imported'])}, failed {len(result['failed'])}; "
              f"{result['pending']} still waiting, {result['backing_off']} backing off.")
        for name, result_ in result['imported']:
            print(f"✅ killtracker: counted {name} → {result_['label']} ({result_['kills']} kills)")
        for name, error in result['failed']:
            print(f"⚠️ killtracker: could not read {name}: {error}")

    @poll.error
    async def _poll_error(self, error: BaseException):
        """`poll()` catches what it expects; this is for what it did not. A loop
        that raised is stopped for good by discord.py, so say so and start it again."""
        print(f"❌ killtracker loop stopped on {error!r}; restarting it.")
        self.poll.restart()

    @poll.before_loop
    async def _before_poll(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(KillTrackerCog(bot))
