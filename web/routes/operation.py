"""The running operation and the actions on it.

Moved out of web/app.py unchanged — see register().
"""
from fastapi import Request
from fastapi.responses import HTMLResponse
from utils import database
from web import auth, operations as operation_service
from web.auth import Forbidden
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

    # -- the operation ------------------------------------------------------

    async def operation_context(request: Request, guild_id: str) -> dict:
        context = await guild_context(request, guild_id)
        if not context['is_admin']:
            raise Forbidden('Only a server admin can run an operation.')
        return context

    async def operation_page(request: Request, context: dict, error: str = None,
                             status: int = 200, debug: dict = None,
                             panel: str = None, preselect: str = None):
        """The operation page. *panel* names a collapsed section to re-open,
        so a form that comes back with an error is not folded away with what
        the person typed still in it."""
        return render(request, 'operation.html', {
            **context,
            **await operation_service.overview(context['guild'], context['tz']),
            'channels_choices': postable_channels(context['guild']),
            'debug': debug,
            'panel': panel,
            'preselect_orbat': preselect,
            'error': error,
        }, status=status)

    async def operation_settings_page(request: Request, context: dict,
                                      error: str = None, status: int = 200):
        return render(request, 'operation_settings.html', {
            **context,
            'channels': await operation_service.channel_settings(context['guild']),
            'channels_choices': postable_channels(context['guild']),
            'timezone': context['tz'],
            'timezone_choices': operation_service.TIMEZONE_CHOICES,
            'error': error,
        }, status=status)

    async def operation_action(request: Request, guild_id: str, run,
                               panel: str = None, settings: bool = False):
        """Every form on both pages: check CSRF, run one service call, flash it.

        `run(context, form)` returns the message, or raises ValueError with one
        for the person who submitted — the convention the rest of `web/` uses.
        *panel* re-opens the section the error came from; *settings* sends the
        answer back to the settings page instead of the operation page.
        """
        context = await operation_context(request, guild_id)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))
        page = f"/g/{guild_id}/operation" + ('/settings' if settings else '')
        try:
            note = await run(context, form)
        except ValueError as e:
            if settings:
                return await operation_settings_page(
                    request, context, error=str(e), status=400
                )
            return await operation_page(request, context, error=str(e),
                                        status=400, panel=panel)
        return redirect(request, page, 'ok', note)

    @app.get('/g/{guild_id}/operation/settings', response_class=HTMLResponse)
    async def operation_settings(request: Request, guild_id: str):
        return await operation_settings_page(
            request, await operation_context(request, guild_id)
        )

    @app.get('/g/{guild_id}/operation', response_class=HTMLResponse)
    async def operation_overview(request: Request, guild_id: str):
        # ?orbat=<id> comes from the ORBAT editor: open the start panel with that
        # roster already picked.
        orbat = request.query_params.get('orbat') or ''
        return await operation_page(
            request, await operation_context(request, guild_id),
            panel='start' if orbat.isdigit() else None,
            preselect=orbat if orbat.isdigit() else None,
        )

    @app.post('/g/{guild_id}/operation/start', response_class=HTMLResponse)
    async def operation_start(request: Request, guild_id: str):
        return await operation_action(request, guild_id, lambda context, form:
            operation_service.start(bot, context['guild'], form, context['tz']),
            panel='start')

    @app.post('/g/{guild_id}/operation/time', response_class=HTMLResponse)
    async def operation_time(request: Request, guild_id: str):
        async def run(context, form):
            op = await database.get_active_operation(str(context['guild'].id))
            return await operation_service.set_time(
                bot, context['guild'], op, form, context['tz']
            )
        return await operation_action(request, guild_id, run)

    @app.post('/g/{guild_id}/operation/timezone', response_class=HTMLResponse)
    async def operation_timezone(request: Request, guild_id: str):
        return await operation_action(request, guild_id, lambda context, form:
            operation_service.set_timezone(context['guild'], form), settings=True)

    @app.post('/g/{guild_id}/operation/channels', response_class=HTMLResponse)
    async def operation_channels(request: Request, guild_id: str):
        return await operation_action(request, guild_id, lambda context, form:
            operation_service.save_channels(context['guild'], form), settings=True)

    @app.post('/g/{guild_id}/operation/board', response_class=HTMLResponse)
    async def operation_board(request: Request, guild_id: str):
        async def run(context, form):
            op = await database.get_active_operation(str(context['guild'].id))
            return await operation_service.post_board(bot, context['guild'], op, form)
        return await operation_action(request, guild_id, run, panel='board')

    @app.post('/g/{guild_id}/operation/announce', response_class=HTMLResponse)
    async def operation_announce(request: Request, guild_id: str):
        async def run(context, form):
            op = await database.get_active_operation(str(context['guild'].id))
            return await operation_service.post_announcement(
                bot, context['guild'], context['member'], op, form, context['tz']
            )
        return await operation_action(request, guild_id, run, panel='announce')

    @app.post('/g/{guild_id}/operation/clear-requests', response_class=HTMLResponse)
    async def operation_clear_requests(request: Request, guild_id: str):
        async def run(context, form):
            op = await database.get_active_operation(str(context['guild'].id))
            return await operation_service.clear_pending(bot, context['guild'], op)
        return await operation_action(request, guild_id, run)

    @app.post('/g/{guild_id}/operation/slots', response_class=HTMLResponse)
    async def operation_debug_slots(request: Request, guild_id: str):
        """The raw roster — `/debug-slots`. Rendered in place, not redirected to,
        because a flash message is the wrong shape for forty lines of output."""
        context = await operation_context(request, guild_id)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))
        op = await database.get_active_operation(str(context['guild'].id))
        try:
            debug = await operation_service.raw_slots(op, form.get('squad'))
        except ValueError as e:
            return await operation_page(request, context, error=str(e), status=400)
        debug['squad'] = (form.get('squad') or '').strip()
        return await operation_page(request, context, debug=debug)
