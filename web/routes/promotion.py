"""Promotion: Cadet → Member after enough Kill Tracker missions."""
from fastapi import Request
from fastapi.responses import HTMLResponse

from web import auth, promotion as promotion_service
from web.auth import Forbidden


def register(app, d):
    """Add this feature's routes to the app (see web/app.py for `d`)."""
    render = d.render
    redirect = d.redirect
    guild_context = d.guild_context
    require_admin = d.require_admin

    async def admin_context(request: Request, guild_id: str) -> dict:
        context = await guild_context(request, guild_id)
        require_admin(context)
        return context

    async def post_context(request: Request, guild_id: str):
        """The admin context and the submitted form, with the CSRF token checked."""
        context = await admin_context(request, guild_id)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))
        return context, form

    def back(request: Request, guild_id: str, kind: str, text: str):
        return redirect(request, f"/g/{guild_id}/promotion", kind, text)

    @app.get('/g/{guild_id}/promotion', response_class=HTMLResponse)
    async def promotion_page(request: Request, guild_id: str):
        context = await admin_context(request, guild_id)
        return render(request, 'promotion.html', {
            **context, **await promotion_service.promotion_view(context['guild'])})

    @app.post('/g/{guild_id}/promotion')
    async def promotion_save(request: Request, guild_id: str):
        context, form = await post_context(request, guild_id)
        try:
            note = await promotion_service.save_promotion(context['guild'], form)
        except ValueError as e:
            return back(request, guild_id, 'warn', str(e))
        return back(request, guild_id, 'ok', note)

    @app.post('/g/{guild_id}/promotion/test')
    async def promotion_test(request: Request, guild_id: str):
        context, form = await post_context(request, guild_id)
        try:
            note = await promotion_service.post_test(context['guild'], context['member'], form)
        except ValueError as e:
            return back(request, guild_id, 'warn', str(e))
        return back(request, guild_id, 'ok', note)

    @app.post('/g/{guild_id}/promotion/promote')
    async def promotion_promote(request: Request, guild_id: str):
        context, form = await post_context(request, guild_id)
        try:
            ticked = None if form.get('all') else form.getlist('user')
            note = await promotion_service.promote_now(context['guild'], ticked)
        except ValueError as e:
            return back(request, guild_id, 'warn', str(e))
        return back(request, guild_id, 'ok', note)

    @app.post('/g/{guild_id}/promotion/dismiss')
    async def promotion_dismiss(request: Request, guild_id: str):
        context, form = await post_context(request, guild_id)
        try:
            note = await promotion_service.dismiss_promotion(
                context['guild'], form.getlist('user'))
        except ValueError as e:
            return back(request, guild_id, 'warn', str(e))
        return back(request, guild_id, 'ok', note)
