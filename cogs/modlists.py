"""Arma 3 Launcher presets posted to a channel become the guild's modlists.

A member with the right to post in the modlist channel drops the `.html` the
Launcher exports; this reads it, works out which of the four lists it is, keeps
it as a new version and says what changed. It replaces `TFP-Mods`'s GitHub
Actions cron, which polled the same channel every five minutes with a second
bot token.

**Reading a message's text needs the message content intent**, which is
privileged for the same reason the members intent is (see `bot.py`): it is
requested only when `MESSAGE_CONTENT` is set, after "Message Content Intent" is
ticked in the Developer Portal. Without it a message arrives with no
attachments and no text, and this cog does nothing — it says so once at load
rather than silently ignoring every post.

`handle_message()` is the whole implementation: the gateway event, the catch-up
after a reconnect and the web upload all end in `store_preset()`, so a preset
means the same whichever way it arrived.
"""

import logging

import discord
from discord.ext import commands

from utils import database, modlist

log = logging.getLogger(__name__)

# How far back the catch-up reads. The gateway delivers everything while the
# bot is connected; this only covers the time it was not.
CATCHUP_MESSAGES = 50

# How long a question about an unclear post stays answerable.
ASK_TIMEOUT = 3600

REACT_OK, REACT_UNCLEAR, REACT_BAD = '✅', '❓', '❌'


async def store_preset(guild_id: str, slot: str, parsed: dict, *, label: str = '',
                       source_ref: str = None, channel_id: str = None,
                       message_id: str = None, posted_by: str = None,
                       posted_by_name: str = None) -> dict:
    """Keep a parsed preset as the list's newest version.

    Returns `{'status': 'stored' | 'unchanged' | 'duplicate', 'id', 'summary',
    'diff'}`. A preset whose mods are exactly the current version's is
    `unchanged` and not stored: re-posting the same file would otherwise bury
    the history under identical versions.
    """
    if slot not in modlist.SLOT_KEYS:
        raise ValueError('Unknown list.')
    if source_ref and await database.modlist_source_seen(guild_id, source_ref):
        return {'status': 'duplicate', 'id': None, 'summary': '', 'diff': None}
    current = await database.get_current_modlist(guild_id, slot)
    diff = modlist.diff(current['mods'] if current else [], parsed['mods'])
    summary = modlist.diff_summary(diff)
    name = parsed['name'] or label
    if current and not summary and current['name'] == name:
        return {'status': 'unchanged', 'id': current['id'], 'summary': '', 'diff': diff}
    new_id = await database.add_modlist(
        guild_id, slot, name=name, mods=parsed['mods'], dlcs=parsed['dlcs'],
        summary=summary or ('first version' if not current else 'renamed'),
        source_ref=source_ref, channel_id=channel_id, message_id=message_id,
        posted_by=posted_by, posted_by_name=posted_by_name)
    if new_id is None:
        return {'status': 'duplicate', 'id': None, 'summary': '', 'diff': None}
    return {'status': 'stored', 'id': new_id, 'summary': summary, 'diff': diff}


def _preset_attachment(message: discord.Message):
    for attachment in message.attachments:
        if attachment.filename.lower().endswith('.html'):
            return attachment
    return None


class SlotChoice(discord.ui.View):
    """Asks which list a preset is for, when the message did not say."""

    def __init__(self, cog, message, attachment, parsed, candidates):
        super().__init__(timeout=ASK_TIMEOUT)
        self.cog, self.message, self.attachment, self.parsed = cog, message, attachment, parsed
        self.answered = False
        for slot in (candidates or modlist.SLOT_KEYS):
            button = discord.ui.Button(label=modlist.SLOT_LABELS[slot],
                                       style=discord.ButtonStyle.primary)
            button.callback = self._pick(slot)
            self.add_item(button)
        ignore = discord.ui.Button(label='Ignore', style=discord.ButtonStyle.secondary)
        ignore.callback = self._ignore
        self.add_item(ignore)

    def _pick(self, slot):
        async def callback(interaction: discord.Interaction):
            if self.answered:
                return await interaction.response.send_message('Already answered.', ephemeral=True)
            self.answered = True
            result = await self.cog.apply(self.message, self.attachment, self.parsed, slot,
                                          who=interaction.user)
            self.stop()
            await interaction.response.edit_message(content=result, view=None)
        return callback

    async def _ignore(self, interaction: discord.Interaction):
        self.answered = True
        self.stop()
        await interaction.response.edit_message(content='Ignored.', view=None)

    async def on_timeout(self):
        # Leave the ❓ on the post: it is the record that nothing was stored.
        pass


class ModlistsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        if not getattr(bot, 'message_content', False):
            log.warning('⚠️ modlists: MESSAGE_CONTENT is not set, so posted presets '
                        'cannot be read. Tick "Message Content Intent" in the Developer '
                        'Portal, then set MESSAGE_CONTENT=1.')

    async def apply(self, message, attachment, parsed, slot, *, who=None) -> str:
        """Store a preset for a list and say what happened, in one line."""
        poster = who or message.author
        result = await store_preset(
            str(message.guild.id), slot, parsed, label=attachment.filename.rsplit('.', 1)[0],
            source_ref=f'{message.id}:{attachment.id}', channel_id=str(message.channel.id),
            message_id=str(message.id), posted_by=str(poster.id),
            posted_by_name=poster.display_name)
        label = modlist.SLOT_LABELS[slot]
        if result['status'] == 'stored':
            await self._react(message, REACT_OK)
            extra = f': {result["summary"]}' if result['summary'] else ''
            return f'{REACT_OK} {label} updated{extra}.'
        await self._react(message, REACT_OK)
        return f'{REACT_OK} {label} is already up to date.'

    async def _react(self, message, emoji):
        try:
            await message.add_reaction(emoji)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass

    async def handle_message(self, message: discord.Message, *, announce: bool = True):
        """Read one message in the modlist channel. Never raises into the caller."""
        attachment = _preset_attachment(message)
        if attachment is None or message.guild is None or message.author.bot:
            return
        guild_id = str(message.guild.id)
        settings = await database.get_modlist_settings(guild_id)
        if settings['channel_id'] != str(message.channel.id):
            return
        source_ref = f'{message.id}:{attachment.id}'
        if await database.modlist_source_seen(guild_id, source_ref):
            return
        try:
            if attachment.size > modlist.MAX_BYTES:
                raise ValueError('That file is larger than 1 MB — not an Arma preset.')
            parsed = modlist.parse_preset((await attachment.read()).decode('utf-8-sig', 'replace'))
        except ValueError as e:
            if announce:
                await self._react(message, REACT_BAD)
                await message.reply(f'{REACT_BAD} {e}', mention_author=False, delete_after=120)
            return
        slot, candidates = modlist.classify(
            message.content, attachment.filename, parsed['name'], settings['keywords'])
        if slot:
            line = await self.apply(message, attachment, parsed, slot)
            if announce:
                await message.reply(line, mention_author=False)
            return
        if not announce:
            return  # a catch-up must not ask about a post from last week
        await self._react(message, REACT_UNCLEAR)
        asked = ('It matches more than one list' if candidates else
                 "I couldn't tell which list this is")
        await message.reply(f'{REACT_UNCLEAR} {asked} — which one is it?',
                            view=SlotChoice(self, message, attachment, parsed, candidates),
                            mention_author=False)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.attachments:
            return  # nearly every message: no database, no work
        try:
            await self.handle_message(message)
        except Exception:
            log.exception('❌ modlists: could not handle message %s', message.id)

    @commands.Cog.listener()
    async def on_ready(self):
        """Read what was posted while the bot was away. Idempotent: a message
        already stored is skipped by its `source_ref`."""
        if not getattr(self.bot, 'message_content', False):
            return
        try:
            channels = await database.get_modlist_channels()
        except Exception:
            log.exception('❌ modlists: could not load the channels')
            return
        for guild_id, channel_id in channels:
            channel = self.bot.get_channel(int(channel_id))
            if channel is None:
                continue
            try:
                # Oldest first, so the newest post ends up the current version.
                messages = [m async for m in channel.history(limit=CATCHUP_MESSAGES)]
                for message in reversed(messages):
                    await self.handle_message(message, announce=False)
            except Exception:
                log.exception('❌ modlists: catch-up failed for channel %s', channel_id)


async def setup(bot: commands.Bot):
    await bot.add_cog(ModlistsCog(bot))
