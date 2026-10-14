"""The embed builder.

Moved out of web/app.py unchanged — see register().
"""
from fastapi import Request
from fastapi.responses import HTMLResponse
from utils import database
from utils import embeds as embedlib
from web import auth, embeds as embed_service
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
    form_values = d.form_values
    guild_context = d.guild_context
    require_admin = d.require_admin

    # -- embeds -------------------------------------------------------------

    async def embed_context(request: Request, guild_id: str, embed_id: int) -> dict:
        context = await guild_context(request, guild_id)
        require_admin(context)
        record = await database.get_embed(embed_id)
        if record is None or record['guild_id'] != str(context['guild'].id):
            raise Forbidden("No such embed on this server.")
        context['record'] = record
        context['fields'] = await database.get_embed_fields(embed_id)
        return context

    def embed_form(request: Request, context: dict, mode: str, values: dict,
                   error: str = None, status: int = 200):
        return render(request, 'embed_form.html', {
            **context,
            'mode': mode,
            'values': values,
            'channels': postable_channels(context['guild']),
            'slots': range(embed_service.FIELD_SLOTS),
            'error': error,
        }, status=status)

    @app.get('/g/{guild_id}/embeds', response_class=HTMLResponse)
    async def embed_list(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_admin(context)
        return render(request, 'embeds.html', {
            **context, 'embeds': await database.get_guild_embeds(str(guild_id)),
        })

    @app.get('/g/{guild_id}/embeds/new', response_class=HTMLResponse)
    async def new_embed_form(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_admin(context)
        return embed_form(request, context, 'create', {'color': embedlib.DEFAULT_COLOR})

    @app.post('/g/{guild_id}/embeds/new')
    async def create_embed(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_admin(context)
        form = form_values(await request.form())
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            embed_id = await embed_service.create(context['guild'], context['member'], form)
        except ValueError as e:
            return embed_form(request, context, 'create', form, str(e), status=400)
        return redirect(request, f"/g/{guild_id}/embeds/{embed_id}", 'ok',
                        "Saved. Pick a channel below to post it.")

    @app.get('/g/{guild_id}/embeds/{embed_id}', response_class=HTMLResponse)
    async def embed_detail(request: Request, guild_id: str, embed_id: int):
        context = await embed_context(request, guild_id, embed_id)
        return render(request, 'embed_detail.html', {
            **context,
            'channels': postable_channels(context['guild']),
            'posted_in': context['guild'].get_channel(int(context['record']['channel_id']))
                         if context['record']['channel_id'] else None,
        })

    @app.get('/g/{guild_id}/embeds/{embed_id}/edit', response_class=HTMLResponse)
    async def edit_embed_form(request: Request, guild_id: str, embed_id: int):
        context = await embed_context(request, guild_id, embed_id)
        return embed_form(request, context, 'edit',
                          embed_service.form_values(context['record'], context['fields']))

    @app.post('/g/{guild_id}/embeds/{embed_id}/edit')
    async def edit_embed(request: Request, guild_id: str, embed_id: int):
        context = await embed_context(request, guild_id, embed_id)
        form = form_values(await request.form())
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            notes = await embed_service.save(bot, context['record'], form)
        except ValueError as e:
            return embed_form(request, context, 'edit', form, str(e), status=400)
        return redirect(request, f"/g/{guild_id}/embeds/{embed_id}", 'ok',
                        ' '.join(['Saved.'] + notes))

    @app.post('/g/{guild_id}/embeds/{embed_id}/send')
    async def send_embed(request: Request, guild_id: str, embed_id: int):
        context = await embed_context(request, guild_id, embed_id)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            notes = await embed_service.send(
                bot, context['guild'], context['record'], form.get('channel_id')
            )
        except ValueError as e:
            return redirect(request, f"/g/{guild_id}/embeds/{embed_id}", 'warn', str(e))
        return redirect(request, f"/g/{guild_id}/embeds/{embed_id}", 'ok', ' '.join(notes))

    @app.post('/g/{guild_id}/embeds/{embed_id}/delete')
    async def delete_embed(request: Request, guild_id: str, embed_id: int):
        context = await embed_context(request, guild_id, embed_id)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        notes = await embed_service.delete(bot, context['record'], bool(form.get('delete_message')))
        return redirect(request, f"/g/{guild_id}/embeds", 'ok',
                        ' '.join(['Embed deleted.'] + notes))
