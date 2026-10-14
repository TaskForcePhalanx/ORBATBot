"""The voice leaderboard and its settings.

Moved out of web/app.py unchanged — see register().
"""
from cogs.voicelog import refresh_leaderboard_board as refresh_board
from fastapi import Request
from fastapi.responses import HTMLResponse
from utils import database
from web import auth, voice as voice_service
from web.guilds import postable_channels

def register(app, d):
    """Add this feature's routes to the app.

    `d` is the namespace web/app.py builds: the bot, the config and
    the request plumbing every page shares. Rebinding them here is
    what lets every route below stay exactly as it was written.
    """
    bot = d.bot
    render = d.render
    redirect = d.redirect
    guild_context = d.guild_context
    require_admin = d.require_admin

    # -- voice time ---------------------------------------------------------

    async def voice_page(request: Request, context: dict, period: str,
                         error: str = None, status: int = 200):
        guild = context['guild']
        settings = await database.get_voice_settings(str(guild.id))
        return render(request, 'voice.html', {
            **context,
            **await voice_service.overview(guild, context['member'], period),
            'settings': settings,
            'excluded': voice_service.excluded_set(settings),
            'periods': voice_service.PERIODS,
            # `channels` is already the busiest-channels list from overview().
            'post_channels': postable_channels(guild) if context['is_admin'] else [],
            'voice_channels': guild.voice_channels if context['is_admin'] else [],
            'afk_channel': guild.afk_channel,
            'board_channel': (guild.get_channel(int(settings['board_channel_id']))
                              if settings and settings['board_channel_id'] else None),
            'error': error,
        }, status=status)

    @app.get('/g/{guild_id}/voice', response_class=HTMLResponse)
    async def voice_stats(request: Request, guild_id: str, period: str = None):
        context = await guild_context(request, guild_id)
        return await voice_page(request, context, voice_service.clean_period(period))

    @app.post('/g/{guild_id}/voice')
    async def save_voice_settings(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_admin(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            values = voice_service.read_settings_form(context['guild'], form)
        except ValueError as e:
            return await voice_page(request, context, voice_service.DEFAULT_PERIOD,
                                    error=str(e), status=400)

        previous = await database.get_voice_settings(str(guild_id))
        await database.save_voice_settings(str(guild_id), values)
        # The cog caches the settings for a few seconds; drop that so a change
        # here takes effect on the very next voice event.
        cog = bot.get_cog('VoiceLogCog')
        if cog is not None:
            cog.forget_settings(str(guild_id))

        notes = ['Voice tracking is on.' if values['enabled']
                 else 'Voice tracking is off — nothing is recorded.']

        moved = previous and previous['board_channel_id'] != values['board_channel_id']
        if moved:
            # The old message stays where it was; the next refresh posts a new
            # one in the new channel rather than trying to edit across channels.
            await database.set_voice_board_state(str(guild_id), None)

        if values['board_enabled']:
            try:
                what = await refresh_board(bot, context['guild'])
                notes.append(f"Daily board {what}.")
            except ValueError as e:
                notes.append(f"The daily board couldn't be updated: {e}")
        return redirect(request, f"/g/{guild_id}/voice", 'ok', ' '.join(notes))

    @app.post('/g/{guild_id}/voice/post')
    async def post_voice_leaderboard(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_admin(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            note = await voice_service.post_leaderboard(
                bot, context['guild'], form.get('channel_id'), form.get('period'),
            )
        except ValueError as e:
            return redirect(request, f"/g/{guild_id}/voice", 'warn', str(e))
        return redirect(request, f"/g/{guild_id}/voice", 'ok', note)
