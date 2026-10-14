"""Game roles: pick your own, admins manage the list.

Moved out of web/app.py unchanged — see register().
"""
from fastapi import Request
from fastapi.responses import HTMLResponse
from web import auth, roles as roles_service
from web.guilds import forget_member, postable_channels

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

    # -- game roles ---------------------------------------------------------

    async def roles_page(request: Request, guild_id: str, context: dict,
                         error: str = None, status: int = 200):
        guild = context['guild']
        return render(request, 'roles.html', {
            **context,
            'entries': await roles_service.role_entries(guild, context['member']),
            'panel_in': await roles_service.panel_location(guild),
            'channels': postable_channels(guild) if context['is_admin'] else [],
            'can_assign': roles_service.can_assign(guild),
            'max_roles': roles_service.MAX_GAME_ROLES,
            'error': error,
        }, status=status)

    @app.get('/g/{guild_id}/roles', response_class=HTMLResponse)
    async def game_roles(request: Request, guild_id: str):
        return await roles_page(request, guild_id, await guild_context(request, guild_id))

    @app.post('/g/{guild_id}/roles')
    async def save_game_roles(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            note = await roles_service.set_member_roles(
                context['guild'], context['member'], form.getlist('role_ids')
            )
        except ValueError as e:
            return await roles_page(request, guild_id, context, error=str(e), status=400)

        # The member object is cached; drop it so the page that follows shows the
        # roles as they now are rather than as they were up to a minute ago.
        forget_member(guild_id, context['session']['id'])
        return redirect(request, f"/g/{guild_id}/roles", 'ok', note)

    @app.post('/g/{guild_id}/roles/add')
    async def add_game_role(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_admin(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            notes = await roles_service.add_role(
                bot, context['guild'], context['member'],
                form.get('name'), form.get('emoji'), form.get('description'),
            )
        except ValueError as e:
            return await roles_page(request, guild_id, context, error=str(e), status=400)
        return redirect(request, f"/g/{guild_id}/roles", 'ok', ' '.join(notes))

    @app.post('/g/{guild_id}/roles/remove')
    async def remove_game_role(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_admin(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            notes = await roles_service.remove_role(
                bot, context['guild'], context['member'],
                form.get('role_id'), bool(form.get('delete_role')),
            )
        except ValueError as e:
            return await roles_page(request, guild_id, context, error=str(e), status=400)
        return redirect(request, f"/g/{guild_id}/roles", 'ok', ' '.join(notes))

    @app.post('/g/{guild_id}/roles/panel')
    async def post_game_role_panel(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_admin(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            note = await roles_service.post_panel(bot, context['guild'], form.get('channel_id'))
        except ValueError as e:
            return await roles_page(request, guild_id, context, error=str(e), status=400)
        return redirect(request, f"/g/{guild_id}/roles", 'ok', note)
