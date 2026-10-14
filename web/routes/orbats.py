"""The ORBAT list and the roster editor.

Moved out of web/app.py unchanged — see register().
"""
from fastapi import Request
from fastapi.responses import HTMLResponse
from utils import database
from web import auth, orbat as orbat_service
from web.auth import Forbidden

def register(app, d):
    """Add this feature's routes to the app.

    `d` is the namespace web/app.py builds: the bot, the config and
    the request plumbing every page shares. Rebinding them here is
    what lets every route below stay exactly as it was written.
    """
    render = d.render
    redirect = d.redirect
    guild_context = d.guild_context

    # -- ORBATs -------------------------------------------------------------

    def require_orbat_admin(context: dict):
        if not context['is_admin']:
            raise Forbidden("Only a server admin can build ORBATs.")

    async def orbat_context(request: Request, guild_id: str, orbat_id: int) -> dict:
        """One ORBAT, for the editor. Admins may open any; a campaign planner may
        open one that a planner mission links — the ORBAT is part of the plan
        they are writing. Deleting stays admin-only (`orbat_delete`)."""
        context = await guild_context(request, guild_id)
        record = await database.get_orbat(orbat_id)
        if record is None or record['guild_id'] != str(context['guild'].id):
            if not context['is_admin']:
                require_orbat_admin(context)
            raise Forbidden("No such ORBAT on this server.")
        if not context['is_admin']:
            if not (context['may_edit_orbats']
                    and await database.cp_orbat_linked(str(context['guild'].id), orbat_id)):
                require_orbat_admin(context)
        context['record'] = record
        return context

    async def orbat_list_page(request: Request, context: dict, error: str = None,
                              status: int = 200):
        orbats = await database.get_guild_orbats(str(context['guild'].id))
        if not context['is_admin']:
            # Somebody allowed to edit mission ORBATs, and nothing more: the
            # list is the ORBATs campaign missions use, and they create none.
            linked = await database.cp_linked_orbat_ids(str(context['guild'].id))
            orbats = [o for o in orbats if o['id'] in linked]
        return render(request, 'orbats.html', {
            **context,
            'orbats': orbats,
            'error': error,
        }, status=status)

    async def orbat_editor(request: Request, context: dict, text: str, nets_text: str,
                           checked: dict = None, pending: bool = False,
                           error: str = None, status: int = 200):
        return render(request, 'orbat_form.html', {
            **context,
            'text': text,
            'nets_text': nets_text,
            'result': (checked or {}).get('result'),
            'nets': (checked or {}).get('nets'),
            'diff': (checked or {}).get('diff'),
            'board': (checked or {}).get('board'),
            'summary': (checked or {}).get('summary'),
            'pending': pending,
            'stored': await orbat_service.stored_board(context['record']['id']),
            'live_operation': await database.orbat_live_operation(context['record']['id']),
            'error': error,
        }, status=status)

    @app.get('/g/{guild_id}/orbats', response_class=HTMLResponse)
    async def orbat_list(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        if not context['may_edit_orbats']:
            require_orbat_admin(context)
        return await orbat_list_page(request, context)

    @app.post('/g/{guild_id}/orbats')
    async def new_orbat(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_orbat_admin(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            orbat_id = await orbat_service.create(
                context['guild'], context['member'],
                form.get('name'), form.get('description'),
            )
        except ValueError as e:
            return await orbat_list_page(request, context, error=str(e), status=400)
        return redirect(request, f"/g/{guild_id}/orbats/{orbat_id}", 'ok',
                        'ORBAT created — now write the roster.')

    @app.get('/g/{guild_id}/orbats/{orbat_id}', response_class=HTMLResponse)
    async def orbat_edit(request: Request, guild_id: str, orbat_id: int):
        context = await orbat_context(request, guild_id, orbat_id)
        return await orbat_editor(
            request, context,
            await orbat_service.editor_text(context['record']),
            await orbat_service.editor_nets_text(context['record']),
        )

    @app.post('/g/{guild_id}/orbats/{orbat_id}', response_class=HTMLResponse)
    async def orbat_save(request: Request, guild_id: str, orbat_id: int):
        context = await orbat_context(request, guild_id, orbat_id)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        text = form.get('text') or ''
        nets_text = form.get('nets') or ''
        action = form.get('action') or 'preview'
        checked = await orbat_service.review(orbat_id, text, nets_text)

        if not checked['ok'] or action == 'preview':
            return await orbat_editor(request, context, text, nets_text, checked)

        # An edit that would unseat somebody, or move them onto a differently
        # named role, always stops for a confirmation. Everything else saves
        # straight away: asking on every edit would train people to click
        # through the one that matters.
        if action != 'confirm' and checked['diff'].needs_confirmation:
            return await orbat_editor(request, context, text, nets_text, checked,
                                      pending=True)

        note = await orbat_service.apply(orbat_id, text, nets_text, checked)
        return redirect(request, f"/g/{guild_id}/orbats/{orbat_id}", 'ok', note)

    @app.post('/g/{guild_id}/orbats/{orbat_id}/rename', response_class=HTMLResponse)
    async def orbat_rename(request: Request, guild_id: str, orbat_id: int):
        context = await orbat_context(request, guild_id, orbat_id)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            await orbat_service.rename(orbat_id, form.get('name'), form.get('description'))
        except ValueError as e:
            return await orbat_editor(
                request, context,
                await orbat_service.editor_text(context['record']),
                await orbat_service.editor_nets_text(context['record']),
                error=str(e), status=400,
            )
        return redirect(request, f"/g/{guild_id}/orbats/{orbat_id}", 'ok', 'Renamed.')

    @app.post('/g/{guild_id}/orbats/{orbat_id}/export', response_class=HTMLResponse)
    async def orbat_export(request: Request, guild_id: str, orbat_id: int):
        context = await orbat_context(request, guild_id, orbat_id)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            tab = await orbat_service.export(
                context['guild'], context['record'], form.get('sheet_url'),
                bool(form.get('with_bookings')),
            )
        except ValueError as e:
            return await orbat_editor(
                request, context,
                await orbat_service.editor_text(context['record']),
                await orbat_service.editor_nets_text(context['record']),
                error=str(e), status=400,
            )
        return redirect(request, f"/g/{guild_id}/orbats/{orbat_id}", 'ok',
                        f'Exported into a new tab named "{tab}".')

    @app.post('/g/{guild_id}/orbats/{orbat_id}/duplicate')
    async def orbat_duplicate(request: Request, guild_id: str, orbat_id: int):
        context = await orbat_context(request, guild_id, orbat_id)
        require_orbat_admin(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            new_id = await orbat_service.duplicate(
                orbat_id, context['member'], form.get('name')
            )
        except ValueError as e:
            return await orbat_editor(
                request, context,
                await orbat_service.editor_text(context['record']),
                await orbat_service.editor_nets_text(context['record']),
                error=str(e), status=400,
            )
        return redirect(request, f"/g/{guild_id}/orbats/{new_id}", 'ok',
                        'Copied — the structure, not the bookings.')

    @app.post('/g/{guild_id}/orbats/{orbat_id}/delete')
    async def orbat_delete(request: Request, guild_id: str, orbat_id: int):
        context = await orbat_context(request, guild_id, orbat_id)
        require_orbat_admin(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))
        try:
            note = await orbat_service.delete(context['record'])
        except ValueError as e:
            return await orbat_editor(
                request, context,
                await orbat_service.editor_text(context['record']),
                await orbat_service.editor_nets_text(context['record']),
                error=str(e), status=400,
            )
        return redirect(request, f"/g/{guild_id}/orbats", 'ok', note)
