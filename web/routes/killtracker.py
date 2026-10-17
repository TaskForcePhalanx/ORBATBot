"""The Kill Tracker admin pages.

Moved out of web/app.py unchanged — see register().
"""
from fastapi import Request
from fastapi.responses import HTMLResponse
from urllib.parse import urlencode
from utils import database
from web import auth, killtracker as killtracker_service
from web.auth import Forbidden
from web.guilds import user_guilds

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
    require_session = d.require_session

    # -- claiming a player ---------------------------------------------------
    #
    # Any signed-in member, not only admins: it opens a claim and nothing more.
    # The path is `@me` because a player could be called `me`, and the dashboard's
    # own `/stats/{player}` is registered after this and would otherwise take it.

    @app.get('/stats/@me', response_class=HTMLResponse)
    async def kt_me(request: Request):
        session = require_session(request)
        known = killtracker_service.killtracker.known_players(await database.kt_rows())
        names = killtracker_service.killtracker.claimable_players(known, await database.kt_aliases())
        items = [{'guild': g['guild'], **await killtracker_service.profile_of(g['guild'], session['id'])}
                 for g in await user_guilds(bot, session['id'])]
        return render(request, 'kt_me.html', {'items': items, 'names': names})

    @app.post('/stats/@me')
    async def kt_me_claim(request: Request):
        session = require_session(request)
        form = await request.form()
        auth.check_csrf(session, form.get('csrf'))
        guild = next((g['guild'] for g in await user_guilds(bot, session['id'])
                      if str(g['guild'].id) == form.get('guild')), None)
        if guild is None:
            raise Forbidden("You are not a member of that server.")
        try:
            note = await killtracker_service.submit_claim(guild, session['id'], form.get('player'))
        except ValueError as e:
            return redirect(request, '/stats/@me', 'warn', str(e))
        return redirect(request, '/stats/@me', 'ok', note)

    # -- kill tracker admin -------------------------------------------------
    # Server admins only, for the whole area. What these change is what the
    # public numbers say (web/killtracker.py serves them without a login).

    async def kt_context(request: Request, guild_id: str) -> dict:
        context = await guild_context(request, guild_id)
        if not context['is_admin']:
            raise Forbidden("Only a server admin can manage the Kill Tracker.")
        return context

    async def kt_post(request: Request, guild_id: str):
        """The admin context and the submitted form, with the CSRF token checked."""
        context = await kt_context(request, guild_id)
        # The units form sends two fields per player, and Starlette stops at 1000
        # fields by default — which would make saving fail for a unit of 500.
        form = await request.form(max_fields=20000, max_files=4)
        auth.check_csrf(context['session'], form.get('csrf'))
        return context, form

    async def kt_read_text(form, file_field: str, text_field: str) -> str:
        """An uploaded file, or failing that pasted text. Bounded: it is a form."""
        upload = form.get(file_field)
        if upload is not None and hasattr(upload, 'read'):
            data = await upload.read()
            if data:
                if len(data) > 8 * 1024 * 1024:
                    raise ValueError('That file is larger than 8 MB.')
                return data.decode('utf-8-sig', errors='replace')
        return (form.get(text_field) or '')[:8 * 1024 * 1024]

    def kt_back(request: Request, guild_id: str, path: str, kind: str, text: str):
        return redirect(request, f"/g/{guild_id}/killtracker{path}", kind, text)

    @app.get('/g/{guild_id}/killtracker', response_class=HTMLResponse)
    async def kt_missions_page(request: Request, guild_id: str):
        context = await kt_context(request, guild_id)
        return render(request, 'kt_missions.html', {
            **context, 'rows': await killtracker_service.missions_view(),
            'progress': await killtracker_service.count_progress(),
        })

    @app.post('/g/{guild_id}/killtracker/missions/names')
    async def kt_rename(request: Request, guild_id: str):
        """Every name on the page, saved at once."""
        context, form = await kt_post(request, guild_id)
        try:
            note = await killtracker_service.save_names(form)
        except ValueError as e:
            return kt_back(request, guild_id, '', 'warn', str(e))
        return kt_back(request, guild_id, '', 'ok', note)

    # campaigns

    async def kt_campaign_page(request, context, mode, campaign, values, error=None, status=200):
        missions = await database.kt_missions()
        shown = killtracker_service.shown_labels(missions)
        return render(request, 'kt_campaign_form.html', {
            **context, 'mode': mode, 'campaign': campaign, 'values': values, 'error': error,
            'missions': [{'id': m['ocap_id'], 'label': shown[m['ocap_id']],
                          'tag': m['tag'], 'status': m['status']} for m in missions],
        }, status=status)

    @app.get('/g/{guild_id}/killtracker/campaigns', response_class=HTMLResponse)
    async def kt_campaigns_page(request: Request, guild_id: str):
        context = await kt_context(request, guild_id)
        return render(request, 'kt_campaigns.html', {
            **context, 'campaigns': await database.kt_campaigns(),
        })

    @app.get('/g/{guild_id}/killtracker/campaigns/new', response_class=HTMLResponse)
    async def kt_campaign_new(request: Request, guild_id: str):
        context = await kt_context(request, guild_id)
        return await kt_campaign_page(request, context, 'create', None,
                                      {'name': '', 'patterns': '', 'ids': [], 'extra': []})

    @app.post('/g/{guild_id}/killtracker/campaigns/new')
    async def kt_campaign_create(request: Request, guild_id: str):
        context, form = await kt_post(request, guild_id)
        try:
            data = killtracker_service.read_campaign_form(form)
            campaign_id = await database.kt_save_campaign(
                None, updated_by_name=context['member'].display_name, **data)
        except ValueError as e:
            values = {'name': form.get('name') or '', 'patterns': form.get('patterns') or '',
                      'ids': [int(x) for x in form.getlist('mission') if x.isdigit()],
                      'extra': form.getlist('extra')}
            return await kt_campaign_page(request, context, 'create', None, values, str(e), 400)
        return kt_back(request, guild_id, '/campaigns', 'ok', f"Campaign “{data['name']}” created.")

    async def kt_campaign_of(request, guild_id, campaign_id):
        context = await kt_context(request, guild_id)
        campaign = await database.kt_campaign(campaign_id)
        if campaign is None:
            raise Forbidden("No such campaign.")
        return context, campaign

    @app.get('/g/{guild_id}/killtracker/campaigns/{campaign_id}', response_class=HTMLResponse)
    async def kt_campaign_edit(request: Request, guild_id: str, campaign_id: int):
        context, campaign = await kt_campaign_of(request, guild_id, campaign_id)
        return await kt_campaign_page(request, context, 'edit', campaign, {
            'name': campaign['name'], 'patterns': '\n'.join(campaign['patterns']),
            'ids': campaign['ocap_ids'], 'extra': campaign['extra']})

    @app.post('/g/{guild_id}/killtracker/campaigns/{campaign_id}')
    async def kt_campaign_save(request: Request, guild_id: str, campaign_id: int):
        context, form = await kt_post(request, guild_id)
        campaign = await database.kt_campaign(campaign_id)
        if campaign is None:
            raise Forbidden("No such campaign.")
        try:
            data = killtracker_service.read_campaign_form(form)
            await database.kt_save_campaign(
                campaign_id, updated_by_name=context['member'].display_name, **data)
        except ValueError as e:
            values = {'name': form.get('name') or '', 'patterns': form.get('patterns') or '',
                      'ids': [int(x) for x in form.getlist('mission') if x.isdigit()],
                      'extra': form.getlist('extra')}
            return await kt_campaign_page(request, context, 'edit', campaign, values, str(e), 400)
        return kt_back(request, guild_id, '/campaigns', 'ok', f"Campaign “{data['name']}” saved.")

    @app.post('/g/{guild_id}/killtracker/campaigns/{campaign_id}/delete')
    async def kt_campaign_delete(request: Request, guild_id: str, campaign_id: int):
        context, form = await kt_post(request, guild_id)
        if not await database.kt_delete_campaign(campaign_id):
            return kt_back(request, guild_id, '/campaigns', 'warn', 'No such campaign.')
        return kt_back(request, guild_id, '/campaigns', 'ok', 'Campaign deleted. The missions are untouched.')

    # players

    @app.get('/g/{guild_id}/killtracker/players', response_class=HTMLResponse)
    async def kt_players_page(request: Request, guild_id: str, q: str = ''):
        context = await kt_context(request, guild_id)
        params = request.query_params
        # Nothing asked for means the default roles; the form always sends `f`,
        # so an empty tick list is "no roles" rather than "the default".
        if params.get('all'):
            roles = 'all'
        elif 'f' in params:
            roles = params.getlist('role')
        else:
            roles = None
        unmapped = bool(params.get('unmapped'))
        return render(request, 'kt_players.html', {
            **context, **await killtracker_service.players_view(
                context['guild'], q.strip(), roles=roles, unmapped=unmapped),
        })

    @app.get('/g/{guild_id}/killtracker/names', response_class=HTMLResponse)
    async def kt_names_page(request: Request, guild_id: str, q: str = ''):
        context = await kt_context(request, guild_id)
        return render(request, 'kt_names.html', {
            **context, **await killtracker_service.players_view(
                context['guild'], q.strip(), request.query_params.getlist('name')),
        })

    @app.post('/g/{guild_id}/killtracker/players/search')
    async def kt_players_search(request: Request, guild_id: str):
        """Search again, or review the ticks — as a redirect, so the ticked names
        travel in the address rather than being lost (and the CSRF token never
        does)."""
        context, form = await kt_post(request, guild_id)
        pairs = [('q', (form.get('q') or '').strip())] + [('name', n) for n in form.getlist('name')]
        return kt_back(request, guild_id, '/names?' + urlencode([p for p in pairs if p[1]]), 'ok', '')

    @app.post('/g/{guild_id}/killtracker/players/link')
    async def kt_link(request: Request, guild_id: str):
        context, form = await kt_post(request, guild_id)
        try:
            names, display = form.getlist('name'), form.get('display')
            existing = (form.get('existing') or '').strip()
            if existing:
                # Adding to a player who already exists: they are shown as they were.
                names, display = names + [existing], existing
            note = await killtracker_service.link(names, display)
        except ValueError as e:
            return kt_back(request, guild_id, '/names', 'warn', str(e))
        return kt_back(request, guild_id, '/names', 'ok', note)

    @app.post('/g/{guild_id}/killtracker/players/member')
    async def kt_member(request: Request, guild_id: str):
        context, form = await kt_post(request, guild_id)
        try:
            note = await killtracker_service.map_member(
                context['guild'], form.get('player'), form.get('member'))
        except ValueError as e:
            return kt_back(request, guild_id, '/players', 'warn', str(e))
        return kt_back(request, guild_id, '/players', 'ok', note)

    @app.post('/g/{guild_id}/killtracker/players/claim')
    async def kt_claim(request: Request, guild_id: str):
        context, form = await kt_post(request, guild_id)
        try:
            note = await killtracker_service.decide_claim(
                context['guild'], form.get('user'), form.get('decision') == 'approve')
        except ValueError as e:
            return kt_back(request, guild_id, '/players', 'warn', str(e))
        return kt_back(request, guild_id, '/players', 'ok', note)

    @app.post('/g/{guild_id}/killtracker/players/show')
    async def kt_show(request: Request, guild_id: str):
        context, form = await kt_post(request, guild_id)
        try:
            note = await killtracker_service.show_instead(form.get('name'))
        except ValueError as e:
            return kt_back(request, guild_id, '/names', 'warn', str(e))
        return kt_back(request, guild_id, '/names', 'ok', note)

    @app.post('/g/{guild_id}/killtracker/players/unlink')
    async def kt_unlink(request: Request, guild_id: str):
        context, form = await kt_post(request, guild_id)
        try:
            note = await killtracker_service.unlink(form.get('name'))
        except ValueError as e:
            return kt_back(request, guild_id, '/names', 'warn', str(e))
        return kt_back(request, guild_id, '/names', 'ok', note)

    # units

    @app.get('/g/{guild_id}/killtracker/units', response_class=HTMLResponse)
    async def kt_units_page(request: Request, guild_id: str, q: str = ''):
        context = await kt_context(request, guild_id)
        return render(request, 'kt_units.html', {
            **context, **await killtracker_service.units_view(q.strip()),
        })

    @app.post('/g/{guild_id}/killtracker/units')
    async def kt_units_save(request: Request, guild_id: str):
        context, form = await kt_post(request, guild_id)
        try:
            note = await killtracker_service.save_units(form)
        except ValueError as e:
            return kt_back(request, guild_id, '/units', 'warn', str(e))
        return kt_back(request, guild_id, '/units', 'ok', note)

    # the farthest kills

    @app.get('/g/{guild_id}/killtracker/kills', response_class=HTMLResponse)
    async def kt_kills_page(request: Request, guild_id: str, mission: str = '', q: str = ''):
        context = await kt_context(request, guild_id)
        return render(request, 'kt_kills.html', {
            **context, **await killtracker_service.kills_view(mission.strip(), q.strip()),
        })

    @app.post('/g/{guild_id}/killtracker/kills')
    async def kt_kills_save(request: Request, guild_id: str):
        context, form = await kt_post(request, guild_id)
        # Back to the same filter: somebody checking one mission strikes several
        # kills in a row and should not land on the unfiltered list each time.
        keep = urlencode({k: v for k, v in (('mission', form.get('mission')), ('q', form.get('q'))) if v})
        path = '/kills' + (f'?{keep}' if keep else '')
        try:
            note = await killtracker_service.save_strikes(form, context['member'].display_name)
        except ValueError as e:
            return kt_back(request, guild_id, path, 'warn', str(e))
        return kt_back(request, guild_id, path, 'ok', note)

    # the biggest multikills

    @app.get('/g/{guild_id}/killtracker/multikills', response_class=HTMLResponse)
    async def kt_multikills_page(request: Request, guild_id: str, mission: str = '', q: str = ''):
        context = await kt_context(request, guild_id)
        return render(request, 'kt_multikills.html', {
            **context, **await killtracker_service.multikills_view(mission.strip(), q.strip()),
        })

    @app.post('/g/{guild_id}/killtracker/multikills')
    async def kt_multikills_save(request: Request, guild_id: str):
        context, form = await kt_post(request, guild_id)
        keep = urlencode({k: v for k, v in (('mission', form.get('mission')), ('q', form.get('q'))) if v})
        path = '/multikills' + (f'?{keep}' if keep else '')
        try:
            note = await killtracker_service.save_multikill_strikes(form, context['member'].display_name)
        except ValueError as e:
            return kt_back(request, guild_id, path, 'warn', str(e))
        return kt_back(request, guild_id, path, 'ok', note)

    # import from the old files

    @app.get('/g/{guild_id}/killtracker/import', response_class=HTMLResponse)
    async def kt_import_page(request: Request, guild_id: str):
        context = await kt_context(request, guild_id)
        return render(request, 'kt_import.html', {**context, 'report': None})

    @app.post('/g/{guild_id}/killtracker/import/{what}', response_class=HTMLResponse)
    async def kt_import(request: Request, guild_id: str, what: str):
        context, form = await kt_post(request, guild_id)
        steps = {
            'names': ('sheet', 'sheet_text', lambda text: killtracker_service.import_names(
                text, overwrite=bool(form.get('overwrite')))),
            'campaigns': ('campaigns', 'campaigns_text', lambda text: killtracker_service.import_campaigns(
                text, context['member'].display_name)),
            'aliases': ('aliases', 'aliases_text', killtracker_service.import_aliases),
            'units': ('units', 'units_text', killtracker_service.import_units),
        }
        if what not in steps:
            raise Forbidden("No such import.")
        file_field, text_field, run = steps[what]
        try:
            text = await kt_read_text(form, file_field, text_field)
            if not text.strip():
                raise ValueError('Choose a file or paste its contents first.')
            report = await run(text)
        except ValueError as e:
            return render(request, 'kt_import.html', {
                **context, 'report': None, 'error': str(e), 'what': what}, status=400)
        return render(request, 'kt_import.html', {
            **context, 'report': report, 'what': what, 'error': None})
