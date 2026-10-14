"""Tactical maps, terrains, share links and marker pictures.

Moved out of web/app.py unchanged — see register().
"""
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from utils import database
from utils import tacmap as tacmap_lib
from web import (
    auth,
    campaigns as campaign_service,
    tacmap as tacmap_service,
    terrain as terrain_service,
)
from web.auth import Forbidden
from web.guilds import postable_channels

def register(app, d):
    """Add this feature's routes to the app.

    `d` is the namespace web/app.py builds: the bot, the config and
    the request plumbing every page shares. Rebinding them here is
    what lets every route below stay exactly as it was written.
    """
    config = d.config
    render = d.render
    redirect = d.redirect
    guild_context = d.guild_context
    require_admin = d.require_admin

    # -- tactical maps ------------------------------------------------------

    # The palette never changes between requests, so it is built once.
    _map_catalog = tacmap_lib.json_payload(tacmap_lib.catalog())

    async def map_context(request: Request, guild_id: str, map_id: int) -> dict:
        context = await guild_context(request, guild_id)
        record = await database.get_tac_map(map_id)
        if record is None or record['guild_id'] != str(context['guild'].id):
            raise Forbidden('No such map on this server.')
        context['record'] = record
        # Everyone in the guild may read the plan; drawing on it is the same
        # audience that may create an event — a Unit Leader or an admin.
        context['may_draw'] = context['may_create']
        return context

    def require_draw(context: dict):
        if not context['may_create']:
            raise Forbidden('Only a Unit Leader or a server admin can change a map.')

    async def map_list_page(request: Request, context: dict, error: str = None,
                            status: int = 200):
        rows = await database.get_guild_tac_maps(str(context['guild'].id))
        return render(request, 'tacmaps.html', {
            **context,
            'maps': [
                {'record': row,
                 'summary': tacmap_lib.summarise(tacmap_service.load(row))}
                for row in rows
            ],
            'may_draw': context['may_create'],
            'error': error,
        }, status=status)

    async def map_editor(request: Request, context: dict, error: str = None,
                         status: int = 200, panel: str = None,
                         channel: int = tacmap_lib.DEFAULT_CHANNEL,
                         ocap_maps: list = None, ocap_base: str = None):
        record = context['record']
        doc = tacmap_service.load(record)
        # The terrain's own town names, drawn under the plan. They belong to
        # the terrain, so a map on an OCAP server has none — the page says so.
        places = await tacmap_service.places_for(context['guild'].id, doc)
        terrains = await database.get_guild_tac_terrains(str(context['guild'].id))
        # The directory is remembered per guild, so it is typed once and the
        # terrains are a dropdown from then on. `ocap_maps` is only filled by
        # the listing route — opening the page must never read somebody
        # else's server.
        if ocap_base is None:
            ocap_base = (await database.get_ocap_base_url(str(context['guild'].id))
                         or tacmap_service.DEFAULT_OCAP_BASE)
        icons = await tacmap_service.marker_images(
            context['guild'].id, f"/g/{context['guild'].id}/mi")
        return render(request, 'tacmap_edit.html', {
            **context,
            'doc_json': tacmap_lib.json_payload(doc),
            'catalog_json': _map_catalog,
            'svg': tacmap_lib.render(doc, places=places, marker_images=icons),
            'icon_keys_json': tacmap_lib.json_payload(sorted(icons)),
            'places_json': tacmap_lib.json_payload(places),
            'places_sqf': tacmap_lib.places_sqf(),
            'summary': tacmap_lib.summarise(doc),
            'editable': context['may_draw'],
            'sqf': (sqf := tacmap_lib.to_sqf(doc, prefix=tacmap_service.arma_prefix(record),
                                             title=record['name'])),
            'export_report': tacmap_lib.export_report(doc, sqf),
            'arma_dump_sqf': tacmap_lib.ARMA_DUMP_SQF,
            'sqf_editable': tacmap_lib.to_sqf(
                doc, prefix=tacmap_service.arma_prefix(record),
                title=record['name'], editable=True, channel=channel),
            'arma_channel': channel,
            'arma_channels': tacmap_lib.ARMA_CHANNELS,
            'save_url': f"/g/{context['guild'].id}/maps/{record['id']}/save",
            'map_rev': str(record['doc_rev']),
            'share_modes': tacmap_service.SHARE_MODES,
            # The canonical origin, not the one this request came in on: a
            # share link is made to be copied somewhere else, and it has to
            # carry the unit's own domain even when it was made from a
            # different one.
            'share_url': config.public_url(tacmap_service.share_path(record),
                                           request),
            'channels': postable_channels(context['guild']),
            'terrains': terrains,
            'ocap_base': ocap_base,
            'ocap_maps': ocap_maps or [],
            'panel': panel,
            'error': error,
        }, status=status)

    async def map_json(request: Request):
        """The body of a save — a ValueError the editor shows as it is."""
        try:
            payload = await request.json()
        except Exception:
            payload = None
        if not isinstance(payload, dict):
            raise ValueError('That was not a map document.')
        return payload

    @app.get('/g/{guild_id}/maps', response_class=HTMLResponse)
    async def map_list(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        return await map_list_page(request, context)

    @app.post('/g/{guild_id}/maps')
    async def new_map(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_draw(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            map_id = await tacmap_service.create(
                context['guild'], context['member'], form.get('name'),
                form.get('description'), form.get('background'),
            )
        except ValueError as e:
            return await map_list_page(request, context, error=str(e), status=400)
        return redirect(request, f"/g/{guild_id}/maps/{map_id}", 'ok',
                        'Map created — now draw the plan.')

    @app.get('/g/{guild_id}/maps/{map_id}', response_class=HTMLResponse)
    async def map_edit(request: Request, guild_id: str, map_id: int,
                       channel: int = tacmap_lib.DEFAULT_CHANNEL):
        context = await map_context(request, guild_id, map_id)
        return await map_editor(request, context, channel=channel)

    @app.post('/g/{guild_id}/maps/{map_id}/save')
    async def map_save(request: Request, guild_id: str, map_id: int):
        context = await map_context(request, guild_id, map_id)
        require_draw(context)
        try:
            payload = await map_json(request)
            auth.check_csrf(context['session'], payload.get('csrf'))
            notes, rev = await tacmap_service.save(
                context['record'], payload.get('doc'),
                context['member'].display_name,
                base_rev=payload.get('rev'), force=bool(payload.get('force')),
            )
        except tacmap_service.Conflict as e:
            return JSONResponse({'ok': False, 'conflict': True, 'error': str(e)},
                                status_code=409)
        except ValueError as e:
            return JSONResponse({'ok': False, 'error': str(e)}, status_code=400)
        # A campaign map's AOs are the planner's: drawing one there makes it.
        planner = await campaign_service.after_map_save(
            context['guild'], context['record'], context['member'])
        if planner:
            notes = notes + planner['notes']
            if planner.get('doc'):
                fresh = await database.get_tac_map(context['record']['id'])
                return JSONResponse({'ok': True, 'notes': notes, 'doc': planner['doc'],
                                     'rev': fresh['doc_rev']})
        return JSONResponse({'ok': True, 'notes': notes, 'rev': rev})

    @app.get('/g/{guild_id}/maps/{map_id}/arma.sqf', response_class=PlainTextResponse)
    async def map_sqf(request: Request, guild_id: str, map_id: int,
                      editable: int = 0,
                      channel: int = tacmap_lib.DEFAULT_CHANNEL):
        """The markers as a script, for pasting into a running mission.

        Served as a file as well as shown on the page, because a plan being
        briefed off a second screen is easier to keep somewhere than to
        re-copy out of the browser each time it changes. `editable=1` is the
        variant whose markers can be moved and deleted in game.
        """
        context = await map_context(request, guild_id, map_id)
        return PlainTextResponse(
            tacmap_service.sqf(context['record'], editable=bool(editable),
                               channel=channel))

    # -- terrains, uploaded and served from here ----------------------------

    async def terrain_page(request: Request, context: dict, error: str = None,
                           status: int = 200):
        terrains = await database.get_guild_tac_terrains(str(context['guild'].id))
        return render(request, 'terrains.html', {
            **context,
            'terrains': [
                {'record': row,
                 'size': terrain_service.megabytes(row['bytes']),
                 'places': terrain_service.load_places(row),
                 'used_by': await database.tac_terrain_usage(row['id'])}
                for row in terrains
            ],
            'may_upload': context['is_admin'],
            'zoom_choices': terrain_service.ZOOM_CHOICES,
            'default_zoom': terrain_service.DEFAULT_ZOOM,
            # The script that copies a terrain's names out of a running
            # mission — the general way in, since OCAP archives carry none.
            'places_sqf': tacmap_lib.places_sqf(),
            'place_groups': tacmap_lib.PLACE_GROUPS,
            'icon_coverage': tacmap_lib.picture_coverage(
                await database.tac_marker_icon_keys(str(context['guild'].id))),
            'error': error,
        }, status=status)

    @app.get('/g/{guild_id}/terrains', response_class=HTMLResponse)
    async def terrain_list(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_draw(context)
        return await terrain_page(request, context)

    @app.post('/g/{guild_id}/terrains')
    async def terrain_upload(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_admin(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        archive = form.get('archive')
        if archive is None or not getattr(archive, 'filename', ''):
            return await terrain_page(request, context, status=400,
                                      error='Choose a zip or 7z of the tile folder.')
        try:
            note = await terrain_service.upload(
                context['guild'], context['member'], archive.file, archive.filename,
                form.get('name'), form.get('world_size'), form.get('max_zoom'),
                form.get('places'),
            )
        except ValueError as e:
            return await terrain_page(request, context, error=str(e), status=400)
        return redirect(request, f"/g/{guild_id}/terrains", 'ok', note)

    @app.post('/g/{guild_id}/terrains/{terrain_id}/places')
    async def terrain_places(request: Request, guild_id: str, terrain_id: int):
        """Replace a terrain's place names — the paste box on the list page."""
        context = await guild_context(request, guild_id)
        require_admin(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        record = await database.get_tac_terrain(terrain_id)
        if record is None or record['guild_id'] != str(context['guild'].id):
            raise Forbidden('No such terrain on this server.')
        try:
            note = await terrain_service.set_places(
                record, form.get('places'), context['member'].display_name)
        except ValueError as e:
            return await terrain_page(request, context, error=str(e), status=400)
        return redirect(request, f"/g/{guild_id}/terrains", 'ok', note)

    @app.post('/g/{guild_id}/terrains/{terrain_id}/delete')
    async def terrain_delete(request: Request, guild_id: str, terrain_id: int):
        context = await guild_context(request, guild_id)
        require_admin(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        record = await database.get_tac_terrain(terrain_id)
        if record is None or record['guild_id'] != str(context['guild'].id):
            raise Forbidden('No such terrain on this server.')
        try:
            note = await terrain_service.delete(record)
        except ValueError as e:
            return await terrain_page(request, context, error=str(e), status=400)
        return redirect(request, f"/g/{guild_id}/terrains", 'ok', note)

    # One tile. No session: a map's background has to load for everybody the
    # share link was sent to, and a terrain render is not a secret. The cache
    # headers are what keep a page of 256 tiles to one round of requests.
    @app.get('/t/{terrain_id}/{zoom}/{column}/{row}.png')
    async def terrain_tile(terrain_id: int, zoom: int, column: int, row: int):
        image = await database.get_tac_terrain_tile(terrain_id, zoom, column, row)
        if image is None:
            return Response(status_code=404)
        return Response(content=bytes(image), media_type='image/png', headers={
            'Cache-Control': 'public, max-age=31536000, immutable',
        })

    @app.post('/g/{guild_id}/maps/{map_id}/ocap-places', response_class=HTMLResponse)
    async def map_ocap_places(request: Request, guild_id: str, map_id: int):
        """Read the terrain's town names off the OCAP server the map sits on."""
        context = await map_context(request, guild_id, map_id)
        require_draw(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        doc = tacmap_service.load(context['record'])
        try:
            note = await tacmap_service.import_ocap_places(
                context['guild'].id, doc, context['member'].display_name)
        except ValueError as e:
            return await map_editor(request, context, error=str(e), status=400,
                                    panel='terrain')
        return redirect(request, f"/g/{guild_id}/maps/{map_id}", 'ok', note)

    @app.post('/g/{guild_id}/maps/{map_id}/places', response_class=HTMLResponse)
    async def map_places(request: Request, guild_id: str, map_id: int):
        """Paste in this terrain's place names.

        On the map rather than only on the Terrains page, because a map backed
        by an OCAP server has no terrain row there to paste into — which made
        the one remaining path unreachable for exactly the deployments that
        needed it.
        """
        context = await map_context(request, guild_id, map_id)
        require_draw(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        doc = tacmap_service.load(context['record'])
        try:
            note = await tacmap_service.set_places(
                context['guild'].id, doc, form.get('places'),
                context['member'].display_name)
        except ValueError as e:
            return await map_editor(request, context, error=str(e), status=400,
                                    panel='terrain')
        return redirect(request, f"/g/{guild_id}/maps/{map_id}", 'ok', note)

    @app.post('/g/{guild_id}/maps/{map_id}/terrain')
    async def map_terrain(request: Request, guild_id: str, map_id: int):
        context = await map_context(request, guild_id, map_id)
        require_draw(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        terrain = await database.get_tac_terrain(int(form.get('terrain_id') or 0))
        if terrain is None or terrain['guild_id'] != str(context['guild'].id):
            return await map_editor(request, context, panel='terrain', status=400,
                              error='Pick one of this server\'s terrains.')
        note = await terrain_service.apply_to_map(
            context['record'], terrain, context['member'].display_name
        )
        return redirect(request, f"/g/{guild_id}/maps/{map_id}", 'ok', note)

    @app.post('/g/{guild_id}/maps/{map_id}/import-arma')
    async def map_import_arma(request: Request, guild_id: str, map_id: int):
        """Markers copied out of a running mission, onto the map."""
        context = await map_context(request, guild_id, map_id)
        require_draw(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))
        try:
            notes = await tacmap_service.import_from_arma(
                context['record'], form.get('dump') or '', bool(form.get('players_only')),
                context['member'].display_name)
        except ValueError as e:
            return await map_editor(request, context, error=str(e), status=400,
                                    panel='import')
        return redirect(request, f"/g/{guild_id}/maps/{map_id}", 'ok', ' '.join(notes))

    @app.post('/g/{guild_id}/maps/{map_id}/ocap-list')
    async def map_ocap_list(request: Request, guild_id: str, map_id: int):
        """Read the OCAP directory and come back with its terrains as a list.

        Its own route rather than part of opening the page: reading it is a
        request to somebody else's server, and that must happen because a
        person asked for it, not because they looked at a map.
        """
        context = await map_context(request, guild_id, map_id)
        require_draw(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))
        base = form.get('base') or ''
        try:
            names = await tacmap_service.list_ocap_maps(base)
        except ValueError as e:
            return await map_editor(request, context, error=str(e), status=400,
                                    panel='ocap', ocap_base=base)
        # Only a directory that answered is worth remembering.
        await database.set_ocap_base_url(
            str(context['guild'].id), tacmap_service.clean_base_url(base)
        )
        return await map_editor(request, context, panel='ocap', ocap_maps=names,
                                ocap_base=tacmap_service.clean_base_url(base))

    @app.post('/g/{guild_id}/maps/{map_id}/ocap')
    async def map_ocap(request: Request, guild_id: str, map_id: int):
        context = await map_context(request, guild_id, map_id)
        require_draw(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))
        url = (form.get('url') or '').strip()
        chosen = (form.get('name') or '').strip()
        if chosen:
            base = tacmap_service.clean_base_url(form.get('base') or '')
            url = f'{base}/{chosen}' if base else chosen
        try:
            note = await tacmap_service.import_ocap(
                context['record'], url, context['member'].display_name
            )
        except ValueError as e:
            return await map_editor(request, context, error=str(e), status=400,
                                    panel='ocap')
        return redirect(request, f"/g/{guild_id}/maps/{map_id}", 'ok', note)

    @app.post('/g/{guild_id}/maps/{map_id}/rename')
    async def map_rename(request: Request, guild_id: str, map_id: int):
        context = await map_context(request, guild_id, map_id)
        require_draw(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))
        try:
            await tacmap_service.rename(
                context['record'], form.get('name'), form.get('description')
            )
        except ValueError as e:
            return await map_editor(request, context, error=str(e), status=400, panel='about')
        return redirect(request, f"/g/{guild_id}/maps/{map_id}", 'ok', 'Renamed.')

    @app.post('/g/{guild_id}/maps/{map_id}/duplicate')
    async def map_duplicate(request: Request, guild_id: str, map_id: int):
        context = await map_context(request, guild_id, map_id)
        require_draw(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))
        try:
            new_id = await tacmap_service.duplicate(
                context['record'], context['member'], form.get('name')
            )
        except ValueError as e:
            return await map_editor(request, context, error=str(e), status=400, panel='about')
        return redirect(request, f"/g/{guild_id}/maps/{new_id}", 'ok',
                        'Copied — the plan, not the share link.')

    @app.post('/g/{guild_id}/maps/{map_id}/share')
    async def map_share(request: Request, guild_id: str, map_id: int):
        context = await map_context(request, guild_id, map_id)
        require_draw(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))
        try:
            note = (await tacmap_service.regenerate(context['record'])
                    if form.get('action') == 'new'
                    else await tacmap_service.share(context['record'], form.get('mode')))
        except ValueError as e:
            return await map_editor(request, context, error=str(e), status=400, panel='share')
        return redirect(request, f"/g/{guild_id}/maps/{map_id}", 'ok', note)

    @app.post('/g/{guild_id}/maps/{map_id}/post')
    async def map_post(request: Request, guild_id: str, map_id: int):
        context = await map_context(request, guild_id, map_id)
        require_draw(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))
        try:
            note = await tacmap_service.post(
                context['guild'], context['record'], form.get('channel_id'),
                tacmap_service.map_url(config.public_origin(request), guild_id,
                                       context['record']),
                context['member'],
            )
        except ValueError as e:
            return await map_editor(request, context, error=str(e), status=400, panel='post')
        return redirect(request, f"/g/{guild_id}/maps/{map_id}", 'ok', note)

    @app.post('/g/{guild_id}/maps/{map_id}/delete')
    async def map_delete(request: Request, guild_id: str, map_id: int):
        context = await map_context(request, guild_id, map_id)
        require_draw(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))
        note = await tacmap_service.delete(context['record'])
        return redirect(request, f"/g/{guild_id}/maps", 'ok', note)

    # The share link. No session, no Discord — the token in the URL is the whole
    # credential, which is what makes it forwardable to people who only ever see
    # the plan, and why taking it away has to be one click on the editor page.
    @app.get('/m/{token}', response_class=HTMLResponse)
    async def shared_map_page(request: Request, token: str):
        record = await database.get_tac_map_by_token(token)
        if record is None:
            return render(request, 'error.html', {
                'title': 'No such map',
                'message': "That link doesn't open a map any more. Ask whoever "
                           "sent it to you for the current one.",
            }, status=404)
        doc = tacmap_service.load(record)
        places = await tacmap_service.places_for(record['guild_id'], doc)
        icons = await tacmap_service.marker_images(record['guild_id'], f'/m/{token}/mi')
        return render(request, 'tacmap_view.html', {
            'record': record,
            'doc_json': tacmap_lib.json_payload(doc),
            'catalog_json': _map_catalog,
            'places_json': tacmap_lib.json_payload(places),
            'svg': tacmap_lib.render(doc, places=places, marker_images=icons),
            'icon_keys_json': tacmap_lib.json_payload(sorted(icons)),
            'summary': tacmap_lib.summarise(doc),
            'editable': record['share_mode'] == 'edit',
            'save_url': f'/m/{token}/save',
            'map_rev': str(record['doc_rev']),
        })

    def _icon_response(body):
        if body is None:
            return Response(status_code=404)
        # Private: these are Bohemia's pictures, uploaded for this guild, and
        # must not sit in a shared cache. The URL carries a version, so the
        # browser may keep them.
        return Response(bytes(body), media_type='image/png', headers={
            'Cache-Control': 'private, max-age=31536000, immutable',
            'X-Content-Type-Options': 'nosniff'})

    @app.get('/g/{guild_id}/mi/{key}.png')
    async def marker_icon(request: Request, guild_id: str, key: str):
        """One of the guild's marker pictures — its members only."""
        context = await guild_context(request, guild_id)
        return _icon_response(await database.get_tac_marker_icon(
            str(context['guild'].id), key))

    @app.get('/m/{token}/mi/{key}.png')
    async def shared_marker_icon(request: Request, token: str, key: str):
        """The same, for whoever holds a share link to one of the guild's maps."""
        record = await database.get_tac_map_by_token(token)
        if record is None:
            return Response(status_code=404)
        return _icon_response(await database.get_tac_marker_icon(record['guild_id'], key))

    @app.post('/g/{guild_id}/terrains/marker-icons')
    async def marker_icons_upload(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_admin(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))
        if form.get('action') == 'delete':
            count = await database.delete_tac_marker_icons(str(context['guild'].id))
            return redirect(request, f"/g/{guild_id}/terrains", 'ok',
                            f'Removed {count} marker pictures. Upload them again to draw '
                            'markers and units on the maps.')
        upload = form.get('icons')
        if upload is None or not getattr(upload, 'filename', ''):
            return await terrain_page(request, context, status=400,
                                      error='Choose the zip of marker pictures.')
        try:
            note = await tacmap_service.upload_marker_icons(context['guild'].id, upload.file)
        except ValueError as e:
            return await terrain_page(request, context, error=str(e), status=400)
        return redirect(request, f"/g/{guild_id}/terrains", 'ok', note)

    @app.post('/m/{token}/save')
    async def shared_map_save(request: Request, token: str):
        record = await database.get_tac_map_by_token(token)
        if record is None or record['share_mode'] != 'edit':
            return JSONResponse(
                {'ok': False, 'error': 'This link may look at the map, not change it.'},
                status_code=403,
            )
        try:
            payload = await map_json(request)
            notes, rev = await tacmap_service.save(
                record, payload.get('doc'), 'someone with the link',
                base_rev=payload.get('rev'), force=bool(payload.get('force')),
            )
        except tacmap_service.Conflict as e:
            return JSONResponse({'ok': False, 'conflict': True, 'error': str(e)},
                                status_code=409)
        except ValueError as e:
            return JSONResponse({'ok': False, 'error': str(e)}, status_code=400)
        return JSONResponse({'ok': True, 'notes': notes, 'rev': rev})
