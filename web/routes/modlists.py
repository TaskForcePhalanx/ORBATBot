"""Modlists: the watched channel, keywords, history with rollback, upload."""
from fastapi import Request
from fastapi.responses import HTMLResponse, Response
from utils import database, modlist
from web import auth, modlists as modlist_service


def register(app, d):
    """Add this feature's routes to the app (see web/app.py for `d`)."""
    render = d.render
    redirect = d.redirect
    guild_context = d.guild_context
    require_admin = d.require_admin

    async def modlist_page(request: Request, context: dict, error: str = None,
                           status: int = 200):
        guild = context['guild']
        return render(request, 'modlists.html', {
            **context,
            **await modlist_service.overview(str(guild.id)),
            'channels': modlist_service.readable_channels(guild),
            'error': error,
        }, status=status)

    @app.get('/g/{guild_id}/modlists', response_class=HTMLResponse)
    async def modlists_page(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_admin(context)
        return await modlist_page(request, context)

    @app.post('/g/{guild_id}/modlists/settings')
    async def save_modlist_settings(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_admin(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))
        try:
            channel_id, keywords = modlist_service.read_settings_form(
                context['guild'], form)
        except ValueError as e:
            return await modlist_page(request, context, error=str(e), status=400)
        await database.save_modlist_settings(str(guild_id), channel_id, keywords)
        text = ('Watching the channel for presets.' if channel_id
                else 'No channel is watched — presets are not read.')
        return redirect(request, f"/g/{guild_id}/modlists", 'ok', text)

    @app.post('/g/{guild_id}/modlists/upload')
    async def upload_modlist(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_admin(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))
        upload = form.get('preset')
        if upload is None or not getattr(upload, 'filename', ''):
            return await modlist_page(request, context, status=400,
                                      error='Choose the .html a Launcher exported.')
        try:
            note = await modlist_service.upload(
                context['guild'], context['member'], form.get('slot'),
                await upload.read(), upload.filename)
        except ValueError as e:
            return await modlist_page(request, context, error=str(e), status=400)
        return redirect(request, f"/g/{guild_id}/modlists", 'ok', note)

    @app.post('/g/{guild_id}/modlists/{modlist_id}/restore')
    async def restore_modlist(request: Request, guild_id: str, modlist_id: int):
        context = await guild_context(request, guild_id)
        require_admin(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))
        if not await database.set_current_modlist(str(guild_id), modlist_id):
            return redirect(request, f"/g/{guild_id}/modlists", 'warn',
                            'That version no longer exists.')
        return redirect(request, f"/g/{guild_id}/modlists", 'ok',
                        'Put back as the current version.')

    @app.post('/g/{guild_id}/modlists/{modlist_id}/delete')
    async def delete_modlist(request: Request, guild_id: str, modlist_id: int):
        context = await guild_context(request, guild_id)
        require_admin(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))
        if not await database.delete_modlist(str(guild_id), modlist_id):
            return redirect(request, f"/g/{guild_id}/modlists", 'warn',
                            "Nothing deleted — the current version can't be, "
                            'and a version that is gone already is gone.')
        return redirect(request, f"/g/{guild_id}/modlists", 'ok', 'Version deleted.')

    @app.get('/g/{guild_id}/modlists/{modlist_id}/preset.html')
    async def download_modlist(request: Request, guild_id: str, modlist_id: int):
        """Any member may take a preset: the selector page will offer the same."""
        context = await guild_context(request, guild_id)
        version = await database.get_modlist(str(guild_id), modlist_id)
        if version is None:
            return Response('Not found', status_code=404)
        body = modlist.render_preset(version['name'], version['mods'], version['dlcs'])
        return Response(body, media_type='text/html; charset=utf-8', headers={
            'Content-Disposition':
                f'attachment; filename="{modlist.filename_for(version["name"])}"'})
