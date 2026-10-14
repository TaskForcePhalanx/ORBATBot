"""The campaign planner: campaigns, missions, the wizard.

Moved out of web/app.py unchanged — see register().
"""
from cogs.redditfeed import POLL_MINUTES
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from utils import database
from utils import reddit as reddit_lib
from web import auth, campaigns as campaign_service, reddit as reddit_service
from web.auth import Forbidden
from web.guilds import forget_member, mentionable_roles, postable_channels

def register(app, d):
    """Add this feature's routes to the app.

    `d` is the namespace web/app.py builds: the bot, the config and
    the request plumbing every page shares. Rebinding them here is
    what lets every route below stay exactly as it was written.
    """
    bot = d.bot
    config = d.config
    require_session = d.require_session
    render = d.render
    redirect = d.redirect
    form_values = d.form_values
    guild_context = d.guild_context
    require_admin = d.require_admin

    # -- campaign planner ---------------------------------------------------

    def require_planner_admin(context: dict, message: str = None):
        if not context['is_admin']:
            raise Forbidden(message or
                            "Only a server admin can change the campaign planner's settings.")

    @app.get('/g/{guild_id}/campaigns/settings', response_class=HTMLResponse)
    async def campaign_settings(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_planner_admin(context)
        return render(request, 'cp_settings.html', {
            **context,
            **await campaign_service.settings_page(context['guild']),
        })

    @app.post('/g/{guild_id}/campaigns/settings')
    async def save_campaign_settings(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_planner_admin(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))
        try:
            note = await campaign_service.save_settings(context['guild'], form)
        except ValueError as e:
            return redirect(request, f"/g/{guild_id}/campaigns/settings", 'warn', str(e))
        return redirect(request, f"/g/{guild_id}/campaigns/settings", 'ok', note)

    # -- campaign planner: campaigns, missions, the wizard -------------------
    #
    # Everything here is behind `may_plan` (admins, plus Unit Leaders holding
    # the planner's unit role). Editing the objectives library is admins only.
    # The JSON routes under /cp/api are the wizard's; the rules they answer
    # with all come from utils/campaign.py via web/campaigns.py.

    async def planner_context(request: Request, guild_id: str) -> dict:
        context = await guild_context(request, guild_id)
        if not context['may_plan']:
            raise Forbidden("The campaign planner is open to server admins and to Unit "
                            "Leaders holding the planner's unit role.")
        return context

    async def campaign_ctx(request: Request, guild_id: str, campaign_id: int) -> dict:
        context = await planner_context(request, guild_id)
        try:
            context['campaign'] = await campaign_service.campaign_of(
                context['guild'], campaign_id)
        except campaign_service.NotFound as e:
            raise Forbidden(str(e))
        return context

    async def mission_ctx(request: Request, guild_id: str, mission_id: int,
                          campaign_id: int | None = None) -> dict:
        context = await planner_context(request, guild_id)
        try:
            context['mission'] = await campaign_service.mission_of(
                context['guild'], mission_id, campaign_id)
        except campaign_service.NotFound as e:
            raise Forbidden(str(e))
        return context

    async def planner_form(request: Request, context: dict):
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))
        return form

    def campaign_url(guild_id, campaign_id=None, tail=''):
        base = f"/g/{guild_id}/campaigns"
        return f"{base}/{campaign_id}{tail}" if campaign_id else base

    @app.get('/g/{guild_id}/campaigns', response_class=HTMLResponse)
    async def campaigns_list(request: Request, guild_id: str):
        context = await planner_context(request, guild_id)
        return render(request, 'cp_campaigns.html', {
            **context, **await campaign_service.campaigns_page(context['guild'], context['tz']),
        })

    @app.post('/g/{guild_id}/campaigns')
    async def campaigns_create(request: Request, guild_id: str):
        context = await planner_context(request, guild_id)
        form = await planner_form(request, context)
        try:
            campaign_id = await campaign_service.create_campaign(
                context['guild'], form, context['member'])
        except ValueError as e:
            return redirect(request, campaign_url(guild_id), 'warn', str(e))
        return redirect(request, campaign_url(guild_id, campaign_id), 'ok',
                        'Campaign created. Add its AOs and its first mission.')

    # These come before /campaigns/{campaign_id}, which would otherwise try to
    # read "delete-tests" or "objectives" as a campaign id.

    @app.post('/g/{guild_id}/campaigns/delete-tests')
    async def campaigns_delete_tests(request: Request, guild_id: str):
        context = await planner_context(request, guild_id)
        await planner_form(request, context)
        note = await campaign_service.delete_test_campaigns(bot, context['guild'])
        return redirect(request, campaign_url(guild_id), 'ok', note)

    @app.get('/g/{guild_id}/campaigns/objectives', response_class=HTMLResponse)
    async def objectives_page(request: Request, guild_id: str):
        context = await planner_context(request, guild_id)
        return render(request, 'cp_objectives.html', {
            **context, **await campaign_service.library_page(context['guild']),
        })

    @app.post('/g/{guild_id}/campaigns/objectives')
    async def objectives_add(request: Request, guild_id: str):
        context = await planner_context(request, guild_id)
        require_planner_admin(context, 'Only a server admin can change the objectives library.')
        form = await planner_form(request, context)
        try:
            note = await campaign_service.save_library_entry(context['guild'], None, form)
        except ValueError as e:
            return redirect(request, campaign_url(guild_id) + '/objectives', 'warn', str(e))
        return redirect(request, campaign_url(guild_id) + '/objectives', 'ok', note)

    @app.get('/g/{guild_id}/campaigns/objectives/{entry_id}', response_class=HTMLResponse)
    async def objectives_edit(request: Request, guild_id: str, entry_id: int):
        context = await planner_context(request, guild_id)
        require_planner_admin(context, 'Only a server admin can change the objectives library.')
        entry = await database.get_cp_objective(entry_id, str(context['guild'].id))
        if entry is None:
            raise Forbidden('No such objective in this library.')
        page = await campaign_service.library_page(context['guild'])
        return render(request, 'cp_objective_form.html', {
            **context, 'entry': entry, 'categories': page['categories'],
        })

    @app.post('/g/{guild_id}/campaigns/objectives/{entry_id}')
    async def objectives_save(request: Request, guild_id: str, entry_id: int):
        context = await planner_context(request, guild_id)
        require_planner_admin(context, 'Only a server admin can change the objectives library.')
        form = await planner_form(request, context)
        try:
            note = await campaign_service.save_library_entry(context['guild'], entry_id, form)
        except campaign_service.NotFound as e:
            raise Forbidden(str(e))
        except ValueError as e:
            return redirect(request, f"{campaign_url(guild_id)}/objectives/{entry_id}",
                            'warn', str(e))
        return redirect(request, campaign_url(guild_id) + '/objectives', 'ok', note)

    @app.post('/g/{guild_id}/campaigns/objectives/{entry_id}/delete')
    async def objectives_delete(request: Request, guild_id: str, entry_id: int):
        context = await planner_context(request, guild_id)
        require_planner_admin(context, 'Only a server admin can change the objectives library.')
        await planner_form(request, context)
        await database.delete_cp_objective(entry_id, str(context['guild'].id))
        return redirect(request, campaign_url(guild_id) + '/objectives', 'ok',
                        'Removed from the library. Missions that picked it keep their copy.')

    @app.get('/g/{guild_id}/campaigns/{campaign_id}', response_class=HTMLResponse)
    async def campaign_view(request: Request, guild_id: str, campaign_id: int):
        context = await campaign_ctx(request, guild_id, campaign_id)
        return render(request, 'cp_campaign.html', {
            **context,
            **await campaign_service.campaign_page(context['guild'], context['campaign'],
                                                   context['tz']),
        })

    @app.post('/g/{guild_id}/campaigns/{campaign_id}')
    async def campaign_update(request: Request, guild_id: str, campaign_id: int):
        context = await campaign_ctx(request, guild_id, campaign_id)
        form = await planner_form(request, context)
        try:
            await campaign_service.update_campaign(context['campaign'], form, context['member'])
        except ValueError as e:
            return redirect(request, campaign_url(guild_id, campaign_id), 'warn', str(e))
        return redirect(request, campaign_url(guild_id, campaign_id), 'ok', 'Campaign saved.')

    @app.post('/g/{guild_id}/campaigns/{campaign_id}/delete')
    async def campaign_delete(request: Request, guild_id: str, campaign_id: int):
        context = await campaign_ctx(request, guild_id, campaign_id)
        form = await planner_form(request, context)
        name = context['campaign']['name']
        # A test campaign is made to be thrown away, so one press does it; a
        # real one asks for its name, because it may be months of planning.
        if (not context['campaign']['is_test']
                and str(form.get('confirm_name') or '').strip().lower() != name.lower()):
            return redirect(request, campaign_url(guild_id, campaign_id), 'warn',
                            f'Type the campaign name ({name}) to delete it.')
        note = await campaign_service.delete_campaign(bot, context['campaign'])
        return redirect(request, campaign_url(guild_id), 'ok', note)

    @app.post('/g/{guild_id}/campaigns/{campaign_id}/make-real')
    async def campaign_make_real(request: Request, guild_id: str, campaign_id: int):
        context = await campaign_ctx(request, guild_id, campaign_id)
        await planner_form(request, context)
        if not context['campaign']['is_test']:
            return redirect(request, campaign_url(guild_id, campaign_id))
        note = await campaign_service.make_real(bot, context['campaign'], context['member'])
        return redirect(request, campaign_url(guild_id, campaign_id), 'ok', note)

    @app.post('/g/{guild_id}/campaigns/{campaign_id}/aos')
    async def campaign_ao_add(request: Request, guild_id: str, campaign_id: int):
        context = await campaign_ctx(request, guild_id, campaign_id)
        form = await planner_form(request, context)
        try:
            note = await campaign_service.add_ao(context['campaign'], form, context['member'])
        except ValueError as e:
            return redirect(request, campaign_url(guild_id, campaign_id), 'warn', str(e))
        return redirect(request, campaign_url(guild_id, campaign_id), 'ok', note)

    @app.post('/g/{guild_id}/campaigns/{campaign_id}/aos/{ao_id}')
    async def campaign_ao_rename(request: Request, guild_id: str, campaign_id: int, ao_id: int):
        context = await campaign_ctx(request, guild_id, campaign_id)
        form = await planner_form(request, context)
        try:
            note = await campaign_service.rename_ao(context['campaign'], ao_id, form,
                                                   context['member'])
        except ValueError as e:
            return redirect(request, campaign_url(guild_id, campaign_id), 'warn', str(e))
        return redirect(request, campaign_url(guild_id, campaign_id), 'ok', note)

    @app.post('/g/{guild_id}/campaigns/{campaign_id}/aos/{ao_id}/delete')
    async def campaign_ao_delete(request: Request, guild_id: str, campaign_id: int, ao_id: int):
        context = await campaign_ctx(request, guild_id, campaign_id)
        await planner_form(request, context)
        note = await campaign_service.delete_ao(context['campaign'], ao_id, context['member'])
        return redirect(request, campaign_url(guild_id, campaign_id), 'ok', note)

    @app.post('/g/{guild_id}/campaigns/{campaign_id}/map')
    async def campaign_map_open(request: Request, guild_id: str, campaign_id: int):
        """Open the campaign map in the map editor, making it the first time."""
        context = await campaign_ctx(request, guild_id, campaign_id)
        await planner_form(request, context)
        map_id, notes = await campaign_service.open_campaign_map(
            context['guild'], context['campaign'], context['member'])
        return redirect(request, f'/g/{guild_id}/maps/{map_id}',
                        'warn' if notes else 'ok',
                        ' '.join(notes) or 'The campaign map: one outline per AO, each on its '
                        'own layer. Drag them onto the ground they cover.')

    @app.post('/g/{guild_id}/campaigns/{campaign_id}/aos/{ao_id}/map')
    async def campaign_ao_map_open(request: Request, guild_id: str, campaign_id: int,
                                   ao_id: int):
        """Open an AO's map with a layer per phase fought there."""
        context = await campaign_ctx(request, guild_id, campaign_id)
        await planner_form(request, context)
        try:
            map_id, notes = await campaign_service.open_ao_map(
                context['guild'], context['campaign'], ao_id, context['member'])
        except campaign_service.NotFound as e:
            raise Forbidden(str(e))
        return redirect(request, f'/g/{guild_id}/maps/{map_id}',
                        'warn' if notes else 'ok',
                        ' '.join(notes) or 'Each phase fought in this AO has a layer here — '
                        'draw it on its own layer and it becomes that phase\'s ops trace.')

    @app.post('/g/{guild_id}/campaigns/{campaign_id}/missions')
    async def mission_create(request: Request, guild_id: str, campaign_id: int):
        context = await campaign_ctx(request, guild_id, campaign_id)
        form = await planner_form(request, context)
        try:
            mission_id = await campaign_service.create_mission(
                context['campaign'], form, context['member'])
        except ValueError as e:
            return redirect(request, campaign_url(guild_id, campaign_id), 'warn', str(e))
        return redirect(request, campaign_url(guild_id, campaign_id, f'/missions/{mission_id}'))

    @app.get('/g/{guild_id}/campaigns/{campaign_id}/missions/{mission_id}',
             response_class=HTMLResponse)
    async def mission_wizard(request: Request, guild_id: str, campaign_id: int, mission_id: int):
        context = await mission_ctx(request, guild_id, mission_id, campaign_id)
        return render(request, 'cp_wizard.html', {
            **context,
            **await campaign_service.wizard_page(context['guild'], context['mission'],
                                                 context['tz']),
        })

    @app.get('/g/{guild_id}/campaigns/{campaign_id}/missions/{mission_id}/schedule',
             response_class=HTMLResponse)
    async def mission_schedule(request: Request, guild_id: str, campaign_id: int,
                               mission_id: int):
        context = await mission_ctx(request, guild_id, mission_id, campaign_id)
        return render(request, 'cp_schedule.html', {
            **context,
            **await campaign_service.schedule_page(context['guild'], context['mission'],
                                                   context['tz']),
        })

    @app.post('/g/{guild_id}/campaigns/{campaign_id}/missions/{mission_id}/orbat')
    async def mission_orbat(request: Request, guild_id: str, campaign_id: int,
                            mission_id: int):
        context = await mission_ctx(request, guild_id, mission_id, campaign_id)
        form = await planner_form(request, context)
        back = form.get('back') or 'wizard'
        url = campaign_url(guild_id, campaign_id, f'/missions/{mission_id}')
        if back == 'schedule':
            url += '/schedule'
        try:
            note = await campaign_service.set_orbat(context['guild'], context['mission'],
                                                    form, context['member'])
        except ValueError as e:
            return redirect(request, url, 'warn', str(e))
        return redirect(request, url, 'ok', note)

    @app.post('/g/{guild_id}/campaigns/{campaign_id}/missions/{mission_id}/recording')
    async def mission_recording(request: Request, guild_id: str, campaign_id: int,
                                mission_id: int):
        """The recording card: link · none · auto · stats."""
        context = await mission_ctx(request, guild_id, mission_id, campaign_id)
        form = await planner_form(request, context)
        url = campaign_url(guild_id, campaign_id, f'/missions/{mission_id}')
        if form.get('back') == 'schedule':
            url += '/schedule'
        try:
            note = await campaign_service.set_recording(context['mission'], form,
                                                        context['member'])
        except ValueError as e:
            return redirect(request, url, 'warn', str(e))
        return redirect(request, url, 'ok', note)

    @app.get('/g/{guild_id}/campaigns/{campaign_id}/missions/{mission_id}/export/{filename}')
    async def mission_export(request: Request, guild_id: str, campaign_id: int,
                             mission_id: int, filename: str):
        """One briefing pack as a file: …/export/player.pdf, …/leaders.docx."""
        context = await mission_ctx(request, guild_id, mission_id, campaign_id)
        pack, _, fmt = filename.partition('.')
        try:
            body, name, media = await campaign_service.build_export(
                context['guild'], context['mission'], pack, fmt, context['tz'], config.brand)
        except campaign_service.NotFound as e:
            raise Forbidden(str(e))
        return Response(body, media_type=media, headers={
            'Content-Disposition': f'attachment; filename="{name}"',
            'Cache-Control': 'private, no-store',
        })

    @app.post('/g/{guild_id}/campaigns/{campaign_id}/missions/{mission_id}/move')
    async def mission_move(request: Request, guild_id: str, campaign_id: int, mission_id: int):
        context = await mission_ctx(request, guild_id, mission_id, campaign_id)
        form = await planner_form(request, context)
        await database.move_cp_mission(mission_id, campaign_id,
                                       -1 if form.get('direction') == 'up' else 1)
        await campaign_service.refresh_ao_maps(campaign_id, context['member'])
        return redirect(request, campaign_url(guild_id, campaign_id))

    @app.post('/g/{guild_id}/campaigns/{campaign_id}/missions/{mission_id}/delete')
    async def mission_delete(request: Request, guild_id: str, campaign_id: int, mission_id: int):
        context = await mission_ctx(request, guild_id, mission_id, campaign_id)
        await planner_form(request, context)
        note = await campaign_service.delete_mission(bot, context['mission'], context['member'])
        return redirect(request, campaign_url(guild_id, campaign_id), 'ok', note)

    @app.post('/g/{guild_id}/campaigns/{campaign_id}/missions/{mission_id}/drops/{kind}/{action}')
    async def mission_drop_action(request: Request, guild_id: str, campaign_id: int,
                                  mission_id: int, kind: str, action: str):
        """Approve, unapprove, skip or un-skip a drop from the schedule page or
        the wizard's Overview — the form twin of the wizard's API call below."""
        context = await mission_ctx(request, guild_id, mission_id, campaign_id)
        form = await planner_form(request, context)
        url = campaign_url(guild_id, campaign_id, f'/missions/{mission_id}')
        if form.get('back') == 'schedule':
            url += '/schedule'
        try:
            note = await campaign_service.drop_action(
                bot, context['guild'], context['mission'], kind, action, context['member'],
                form.get('hash') or None)
        except campaign_service.NotFound as e:
            raise Forbidden(str(e))
        except ValueError as e:
            return redirect(request, url + f'#drop-{kind}', 'warn', str(e))
        return redirect(request, url + f'#drop-{kind}', 'ok', note)

    @app.post('/g/{guild_id}/campaigns/{campaign_id}/missions/{mission_id}/signups')
    async def mission_signups(request: Request, guild_id: str, campaign_id: int,
                              mission_id: int):
        context = await mission_ctx(request, guild_id, mission_id, campaign_id)
        form = await planner_form(request, context)
        url = campaign_url(guild_id, campaign_id, f'/missions/{mission_id}')
        if form.get('back') == 'schedule':
            url += '/schedule'
        try:
            note = await campaign_service.start_signups_now(bot, context['guild'],
                                                            context['mission'])
        except ValueError as e:
            return redirect(request, url, 'warn', str(e))
        return redirect(request, url, 'ok', note)

    # -- the wizard's JSON --------------------------------------------------

    def api_error(message: str, status: int = 400):
        return JSONResponse({'ok': False, 'error': message}, status_code=status)

    @app.get('/g/{guild_id}/cp/api/missions/{mission_id}')
    async def cp_api_mission(request: Request, guild_id: str, mission_id: int):
        context = await mission_ctx(request, guild_id, mission_id)
        state = await campaign_service.mission_state(context['guild'], context['mission'],
                                                     context['tz'])
        return JSONResponse({'ok': True, 'state': state})

    @app.patch('/g/{guild_id}/cp/api/missions/{mission_id}')
    async def cp_api_mission_patch(request: Request, guild_id: str, mission_id: int):
        context = await mission_ctx(request, guild_id, mission_id)
        try:
            payload = await request.json()
        except Exception:
            payload = None
        if not isinstance(payload, dict):
            return api_error('That was not a set of changes.')
        auth.check_csrf(context['session'], request.headers.get('x-csrf-token'))
        try:
            state = await campaign_service.patch_mission(
                context['guild'], context['mission'], payload, context['member'], context['tz'])
        except ValueError as e:
            return api_error(str(e))
        return JSONResponse({'ok': True, 'state': state})

    @app.post('/g/{guild_id}/cp/api/missions/{mission_id}/drops/{kind}/{action}')
    async def cp_api_drop(request: Request, guild_id: str, mission_id: int, kind: str,
                          action: str):
        """approve / unapprove / skip / unskip one drop; answers with the
        recomputed state and a note to show."""
        context = await mission_ctx(request, guild_id, mission_id)
        auth.check_csrf(context['session'], request.headers.get('x-csrf-token'))
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        seen = payload.get('hash') if isinstance(payload, dict) else None
        try:
            note = await campaign_service.drop_action(
                bot, context['guild'], context['mission'], kind, action, context['member'],
                seen if isinstance(seen, str) else None)
        except campaign_service.NotFound as e:
            return api_error(str(e), 404)
        except ValueError as e:
            return api_error(str(e))
        fresh = await database.get_cp_mission(mission_id)
        state = await campaign_service.mission_state(context['guild'], fresh, context['tz'])
        return JSONResponse({'ok': True, 'note': note, 'state': state})

    @app.post('/g/{guild_id}/cp/api/images')
    async def cp_api_image(request: Request, guild_id: str):
        context = await planner_context(request, guild_id)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))
        try:
            campaign = await campaign_service.campaign_of(
                context['guild'], int(form.get('campaign_id') or 0))
        except (campaign_service.NotFound, ValueError):
            return api_error('No such campaign on this server.', 404)
        upload = form.get('file')
        if upload is None or not hasattr(upload, 'read'):
            return api_error('Choose an image to upload.')
        try:
            image = await campaign_service.upload_image(campaign, upload, context['member'])
        except ValueError as e:
            return api_error(str(e))
        return JSONResponse({'ok': True, 'image': image,
                             'url': f"/g/{guild_id}/cp/images/{image['id']}"})

    @app.get('/g/{guild_id}/cp/images/{image_id}')
    async def cp_image(request: Request, guild_id: str, image_id: int):
        context = await planner_context(request, guild_id)
        try:
            image = await campaign_service.image_of(context['guild'], image_id)
        except campaign_service.NotFound as e:
            raise Forbidden(str(e))
        # An image is never changed once stored — a new upload is a new id —
        # so the browser may keep it for good. Private: it is behind sign-in.
        return Response(bytes(image['data']), media_type=image['content_type'], headers={
            'Cache-Control': 'private, max-age=31536000, immutable',
            'X-Content-Type-Options': 'nosniff',
        })

    @app.get('/g/{guild_id}/cp/api/objectives')
    async def cp_api_objectives(request: Request, guild_id: str):
        context = await planner_context(request, guild_id)
        return JSONResponse({'ok': True,
                             'entries': await campaign_service.library(context['guild'])})

    # -- reddit feeds -------------------------------------------------------

    async def feed_context(request: Request, guild_id: str, feed_id: int) -> dict:
        context = await guild_context(request, guild_id)
        require_admin(context)
        feed = await database.get_reddit_feed(feed_id)
        if feed is None or feed['guild_id'] != str(context['guild'].id):
            raise Forbidden("No such Reddit watch on this server.")
        context['feed'] = feed
        return context

    def feed_form_values(feed) -> dict:
        return {
            'kind': feed['kind'],
            'source': feed['source'],
            'channel_id': feed['channel_id'] or '',
            'template': feed['template'] or '',
            'authors': ' '.join(
                (feed['author_filter'] or '').split(',')
            ).strip(),
            'mention_ids': (feed['mention_role_id'] or '').split(','),
            'mention_users': ' '.join(
                (feed['mention_user_id'] or '').split(',')
            ).strip(),
            'enabled': feed['enabled'],
        }

    def feed_form(request: Request, context: dict, mode: str, values: dict,
                  error: str = None, preview: dict = None, status: int = 200):
        return render(request, 'reddit_form.html', {
            **context,
            'mode': mode,
            'values': values,
            'error': error,
            'preview': preview,
            'channels': postable_channels(context['guild']),
            'roles': mentionable_roles(context['guild']),
            'kinds': reddit_lib.FEED_KINDS,
            'placeholders': reddit_lib.PLACEHOLDERS,
            'examples': reddit_service.TEMPLATE_EXAMPLES,
            'default_template': reddit_lib.DEFAULT_TEMPLATE,
        }, status=status)

    @app.get('/g/{guild_id}/reddit', response_class=HTMLResponse)
    async def reddit_feeds(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_admin(context)
        feeds = await database.get_reddit_feeds(str(guild_id))
        return render(request, 'reddit.html', {
            **context,
            'feeds': reddit_service.view_models(context['guild'], feeds),
            'poll_minutes': POLL_MINUTES,
        })

    @app.get('/g/{guild_id}/reddit/new', response_class=HTMLResponse)
    async def new_reddit_feed_form(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_admin(context)
        return feed_form(request, context, 'create', {
            'kind': reddit_lib.DEFAULT_KIND,
            'source': '',
            'channel_id': '',
            'template': reddit_lib.DEFAULT_TEMPLATE,
            'authors': '',
            'mention_ids': [],
            'mention_users': '',
            'enabled': 1,
        })

    @app.post('/g/{guild_id}/reddit/new')
    async def create_reddit_feed(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_admin(context)
        form = form_values(await request.form())
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            feed_id, warnings = await reddit_service.create(
                context['guild'], context['member'], form
            )
        except ValueError as e:
            return feed_form(request, context, 'create', form, str(e), status=400)

        text = "Watch added. The first check notes what is already there and " \
               "announces only what comes after it."
        if warnings:
            text += ' ' + ' '.join(warnings)
        return redirect(request, f"/g/{guild_id}/reddit/{feed_id}", 'ok', text)

    @app.get('/g/{guild_id}/reddit/{feed_id}', response_class=HTMLResponse)
    async def edit_reddit_feed_form(request: Request, guild_id: str, feed_id: int):
        context = await feed_context(request, guild_id, feed_id)
        return feed_form(request, context, 'edit', feed_form_values(context['feed']))

    @app.post('/g/{guild_id}/reddit/{feed_id}')
    async def edit_reddit_feed(request: Request, guild_id: str, feed_id: int):
        context = await feed_context(request, guild_id, feed_id)
        form = form_values(await request.form())
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            warnings = await reddit_service.save(context['guild'], context['feed'], form)
        except ValueError as e:
            return feed_form(request, context, 'edit', form, str(e), status=400)
        return redirect(request, f"/g/{guild_id}/reddit", 'ok',
                        ' '.join(['Watch saved.'] + warnings))

    @app.post('/g/{guild_id}/reddit/{feed_id}/preview', response_class=HTMLResponse)
    async def preview_reddit_feed(request: Request, guild_id: str, feed_id: int):
        """Show the newest post as this watch would announce it.

        Rendered in place rather than flashed, and it posts nothing and marks
        nothing as seen — it is meant to be pressed while working on the text.
        """
        context = await feed_context(request, guild_id, feed_id)
        form = form_values(await request.form())
        auth.check_csrf(context['session'], form.get('csrf'))

        # Previewed against what is in the form, not what is stored, so an
        # unsaved edit is what you see.
        try:
            values, _ = await reddit_service.read_form(context['guild'], form)
            preview = await reddit_service.preview(
                {**dict(context['feed']), **values}
            )
        except (ValueError, reddit_lib.FeedError) as e:
            return feed_form(request, context, 'edit', form, str(e), status=400)
        return feed_form(request, context, 'edit', form, preview=preview)

    @app.post('/g/{guild_id}/reddit/{feed_id}/check')
    async def check_reddit_feed(request: Request, guild_id: str, feed_id: int):
        context = await feed_context(request, guild_id, feed_id)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            note = await reddit_service.check_now(bot, context['feed'])
        except ValueError as e:
            return redirect(request, f"/g/{guild_id}/reddit", 'warn', str(e))
        return redirect(request, f"/g/{guild_id}/reddit", 'ok', note)

    @app.get('/g/{guild_id}/reddit/{feed_id}/posts', response_class=HTMLResponse)
    async def reddit_feed_posts(request: Request, guild_id: str, feed_id: int):
        """The feed's recent posts, each with an Announce button.

        Its own page rather than a panel on the watch form, because opening it
        reads the feed — the form has to open without touching Reddit, not
        least when Reddit is refusing us.
        """
        context = await feed_context(request, guild_id, feed_id)
        feed = context['feed']
        read, error = {'url': reddit_lib.feed_url(feed['kind'], feed['source']),
                       'posts': [], 'on_feed': 0,
                       'authors': reddit_service.feed_authors(feed)}, None
        try:
            read = await reddit_service.recent(feed)
        except (ValueError, reddit_lib.FeedError) as e:
            error = str(e)
        return render(request, 'reddit_posts.html', {
            **context,
            'label': reddit_lib.kind_prefix(feed['kind']) + feed['source'],
            'posts': read['posts'],
            'source_url': read['url'],
            'on_feed': read['on_feed'],
            'authors': read['authors'],
            'error': error,
        })

    @app.post('/g/{guild_id}/reddit/{feed_id}/announce')
    async def announce_reddit_post(request: Request, guild_id: str, feed_id: int):
        context = await feed_context(request, guild_id, feed_id)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            note = await reddit_service.catch_up(
                bot, context['feed'], form.get('post_id')
            )
        except ValueError as e:
            return redirect(request, f"/g/{guild_id}/reddit/{feed_id}/posts",
                            'warn', str(e))
        return redirect(request, f"/g/{guild_id}/reddit/{feed_id}/posts", 'ok', note)

    @app.post('/g/{guild_id}/reddit/{feed_id}/mark')
    async def mark_reddit_post(request: Request, guild_id: str, feed_id: int):
        """Take posts out of the queue without announcing them.

        `scope=all` marks everything the page listed, which is the one press
        that stops a backlog going out three at a time; otherwise it is the
        ticked rows.
        """
        context = await feed_context(request, guild_id, feed_id)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        # The ids come from the page, which has already read the feed. Reading
        # it again to write down a decision would fail exactly when Reddit is
        # refusing us — which is when a backlog most needs marking.
        listed = [i for i in (form.get('all_ids') or '').split(',') if i]
        chosen = listed if form.get('scope') == 'all' else form.getlist('post_id')

        try:
            note = await reddit_service.mark(context['feed'], chosen, listed)
        except ValueError as e:
            return redirect(request, f"/g/{guild_id}/reddit/{feed_id}/posts",
                            'warn', str(e))
        return redirect(request, f"/g/{guild_id}/reddit/{feed_id}/posts", 'ok', note)

    @app.post('/g/{guild_id}/reddit/{feed_id}/delete')
    async def delete_reddit_feed(request: Request, guild_id: str, feed_id: int):
        context = await feed_context(request, guild_id, feed_id)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        await database.delete_reddit_feed(feed_id)
        label = (reddit_lib.kind_prefix(context['feed']['kind'])
                 + context['feed']['source'])
        return redirect(request, f"/g/{guild_id}/reddit", 'ok',
                        f"Stopped watching {label}.")

    @app.post('/g/{guild_id}/refresh')
    async def refresh_permissions(request: Request, guild_id: str):
        """Drop the cached member so a role change shows up straight away."""
        session = require_session(request)
        auth.check_csrf(session, (await request.form()).get('csrf'))
        forget_member(guild_id, session['id'])
        return redirect(request, f"/g/{guild_id}", 'ok', 'Permissions re-read from Discord.')
