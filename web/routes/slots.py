"""The slot approval queue.

Moved out of web/app.py unchanged — see register().
"""
from fastapi import Request
from fastapi.responses import HTMLResponse
from web import auth, slots as slots_service
from web.auth import Forbidden

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

    # -- slot approvals -----------------------------------------------------

    async def slots_context(request: Request, guild_id: str) -> dict:
        context = await guild_context(request, guild_id)
        if not context['may_action_slots']:
            raise Forbidden(
                'Only a Unit Leader or a server admin can action slot requests.'
            )
        return context

    async def slots_page(request: Request, context: dict, error: str = None,
                         status: int = 200):
        return render(request, 'slots.html', {
            **context,
            **await slots_service.queue(context['guild'], context['member']),
            'error': error,
        }, status=status)

    @app.get('/g/{guild_id}/slots', response_class=HTMLResponse)
    async def slot_queue(request: Request, guild_id: str):
        return await slots_page(request, await slots_context(request, guild_id))

    @app.post('/g/{guild_id}/slots/{request_id}/approve', response_class=HTMLResponse)
    async def slot_approve(request: Request, guild_id: str, request_id: int):
        context = await slots_context(request, guild_id)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))
        try:
            note = await slots_service.approve(
                bot, context['guild'], context['member'], request_id
            )
        except ValueError as e:
            return await slots_page(request, context, error=str(e), status=400)
        return redirect(request, f"/g/{guild_id}/slots", 'ok', note)

    @app.post('/g/{guild_id}/slots/{request_id}/deny', response_class=HTMLResponse)
    async def slot_deny(request: Request, guild_id: str, request_id: int):
        context = await slots_context(request, guild_id)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))
        try:
            note = await slots_service.deny(
                bot, context['guild'], context['member'], request_id,
                form.get('reason'),
            )
        except ValueError as e:
            return await slots_page(request, context, error=str(e), status=400)
        return redirect(request, f"/g/{guild_id}/slots", 'ok', note)

    @app.post('/g/{guild_id}/slots/assign', response_class=HTMLResponse)
    async def slot_assign(request: Request, guild_id: str):
        context = await slots_context(request, guild_id)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))
        try:
            note = await slots_service.assign(
                bot, context['guild'], context['member'], form
            )
        except ValueError as e:
            return await slots_page(request, context, error=str(e), status=400)
        return redirect(request, f"/g/{guild_id}/slots", 'ok', note)

    @app.post('/g/{guild_id}/slots/{request_id}/clear', response_class=HTMLResponse)
    async def slot_clear(request: Request, guild_id: str, request_id: int):
        context = await slots_context(request, guild_id)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))
        try:
            note = await slots_service.clear(
                bot, context['guild'], context['member'], request_id
            )
        except ValueError as e:
            return await slots_page(request, context, error=str(e), status=400)
        return redirect(request, f"/g/{guild_id}/slots", 'ok', note)
