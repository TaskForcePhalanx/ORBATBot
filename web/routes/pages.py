"""The guild picker, the events pages and the RSVP.

Moved out of web/app.py unchanged — see register().
"""
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from utils import database
from web import auth, nav as nav_service, modlists as modlist_service, service
from web.auth import Forbidden
from web.guilds import mentionable_roles, postable_channels, user_guilds
from web.helpers import fmt_input

def register(app, d):
    """Add this feature's routes to the app.

    `d` is the namespace web/app.py builds: the bot, the config and
    the request plumbing every page shares. Rebinding them here is
    what lets every route below stay exactly as it was written.
    """
    bot = d.bot
    session_of = d.session_of
    render = d.render
    redirect = d.redirect
    form_values = d.form_values
    guild_context = d.guild_context
    event_context = d.event_context
    require_manage = d.require_manage

    # -- pages --------------------------------------------------------------

    @app.get('/', response_class=HTMLResponse)
    async def index(request: Request):
        session = session_of(request)
        # What needs no account: linked from here so a visitor, signed in or not,
        # can see what the site holds. Mods only when a list has been published.
        published = set(await database.get_modlist_guild_ids())
        has_mods = any(str(g.id) in published for g in bot.guilds)
        if session is None:
            return render(request, 'login.html', {'has_mods': has_mods})
        guilds = await user_guilds(bot, session['id'])
        if len(guilds) == 1:
            return RedirectResponse(f"/g/{guilds[0]['guild'].id}", status_code=303)
        return render(request, 'guilds.html', {'guilds': guilds, 'has_mods': has_mods})

    @app.get('/g/{guild_id}/search', response_class=HTMLResponse)
    async def search_pages(request: Request, guild_id: str, q: str = ''):
        context = await guild_context(request, guild_id)
        q = q.strip()[:60]
        return render(request, 'search.html', {
            **context, 'search_query': q,
            'results': nav_service.search(context['nav'], q),
        })

    @app.get('/g/{guild_id}', response_class=HTMLResponse)
    async def guild_events(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        tz = context['tz']

        upcoming = []
        for event in await database.get_upcoming_events(str(guild_id), limit=25):
            upcoming.append(await service.event_view_model(event, tz))
        past = [
            event for event in await database.get_guild_events(str(guild_id), limit=25)
            if event['status'] != 'scheduled'
        ][:10]

        return render(request, 'events.html', {
            **context, 'upcoming': upcoming, 'past': past,
        })

    @app.get('/g/{guild_id}/events/new', response_class=HTMLResponse)
    async def new_event_form(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        if not context['may_create']:
            raise Forbidden(
                "You need the Unit Leader role or Manage Server permission to create events."
            )
        return render(request, 'event_form.html', {
            **context,
            'mode': 'create',
            'channels': postable_channels(context['guild']),
            'roles': mentionable_roles(context['guild']),
            'modlist_choices': await modlist_service.choices(context['guild'], None),
            'values': {'reminder': 30, 'repeat': 'none', 'repeat_delay': '', 'mention_ids': []},
            'error': None,
        })

    @app.post('/g/{guild_id}/events/new')
    async def create_event(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        if not context['may_create']:
            raise Forbidden(
                "You need the Unit Leader role or Manage Server permission to create events."
            )
        form = form_values(await request.form())
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            event_id, warnings = await service.create_event(
                bot, context['guild'], context['member'], context['tz'], form
            )
        except ValueError as e:
            return render(request, 'event_form.html', {
                **context,
                'mode': 'create',
                'channels': postable_channels(context['guild']),
                'roles': mentionable_roles(context['guild']),
            'modlist_choices': await modlist_service.choices(context['guild'], form.get('modlist_id')),
                'values': form,
                'error': str(e),
            }, status=400)

        text = f"Event #{event_id} created and posted."
        if warnings:
            text += ' ' + ' '.join(warnings)
        return redirect(request, f"/g/{guild_id}/events/{event_id}", 'ok', text)

    @app.get('/g/{guild_id}/events/{event_id}', response_class=HTMLResponse)
    async def event_detail(request: Request, guild_id: str, event_id: int):
        context = await event_context(request, guild_id, event_id)
        view = await service.event_view_model(context['event'], context['tz'])
        mine = await database.get_event_signup(event_id, str(context['session']['id']))
        return render(request, 'event_detail.html', {
            **context, **view, 'my_response': mine['response'] if mine else None,
        })

    @app.get('/g/{guild_id}/events/{event_id}/edit', response_class=HTMLResponse)
    async def edit_event_form(request: Request, guild_id: str, event_id: int):
        context = await event_context(request, guild_id, event_id)
        require_manage(context)
        event = context['event']
        if event['status'] != 'scheduled':
            raise Forbidden(f"Event #{event_id} is {event['status']} and can't be changed.")

        custom = await database.get_event_responses(event_id)
        return render(request, 'event_form.html', {
            **context,
            'mode': 'edit',
            'channels': postable_channels(context['guild']),
            'roles': mentionable_roles(context['guild']),
            'modlist_choices': await modlist_service.choices(context['guild'], event['modlist_id']),
            'values': {
                'title': event['title'],
                'start_time': fmt_input(event['event_time'], context['tz']),
                'duration': event['duration_minutes'] or '',
                'description': event['description'] or '',
                'location': event['location'] or '',
                'image_url': event['image_url'] or '',
                'reminder': event['reminder_minutes'] or 0,
                'repeat': event['recurrence'] or 'none',
                'repeat_until': fmt_input(event['recurrence_until'], context['tz']),
                'repeat_delay': event['recurrence_delay_hours'] if event['recurrence_delay_hours'] is not None else '',
                'mention_ids': (event['mention_role_id'] or '').split(','),
                'modlist_id': event['modlist_id'] or '',
                'responses': ' | '.join(
                    ('-' if row['is_decline'] else '')
                    + (f"{row['emoji']} " if row['emoji'] else '')
                    + row['label']
                    for row in custom
                ),
            },
            'error': None,
        })

    @app.post('/g/{guild_id}/events/{event_id}/edit')
    async def edit_event(request: Request, guild_id: str, event_id: int):
        context = await event_context(request, guild_id, event_id)
        require_manage(context)
        if context['event']['status'] != 'scheduled':
            raise Forbidden(f"Event #{event_id} is {context['event']['status']}.")
        form = form_values(await request.form())
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            notes = await service.edit_event(
                bot, context['guild'], context['event'], context['tz'], form
            )
        except ValueError as e:
            return render(request, 'event_form.html', {
                **context,
                'mode': 'edit',
                'channels': postable_channels(context['guild']),
                'roles': mentionable_roles(context['guild']),
            'modlist_choices': await modlist_service.choices(context['guild'], form.get('modlist_id')),
                'values': form,
                'error': str(e),
            }, status=400)

        return redirect(request, f"/g/{guild_id}/events/{event_id}", 'ok',
                        ' '.join(['Event updated.'] + notes))

    @app.post('/g/{guild_id}/events/{event_id}/cancel')
    async def cancel_event(request: Request, guild_id: str, event_id: int):
        context = await event_context(request, guild_id, event_id)
        require_manage(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        if context['event']['status'] != 'scheduled':
            return redirect(request, f"/g/{guild_id}/events/{event_id}", 'warn',
                            f"Event #{event_id} is already {context['event']['status']}.")

        notes = await service.cancel_event(
            bot, context['guild'], context['event'], context['member'],
            (form.get('reason') or '').strip(), bool(form.get('stop_series')),
            context['tz'],
        )
        return redirect(request, f"/g/{guild_id}/events/{event_id}", 'ok',
                        ' '.join([f"Cancelled #{event_id}."] + notes))

    @app.get('/g/{guild_id}/events/{event_id}/delete', response_class=HTMLResponse)
    async def delete_event_form(request: Request, guild_id: str, event_id: int):
        context = await event_context(request, guild_id, event_id)
        require_manage(context)
        view = await service.event_view_model(context['event'], context['tz'])
        return render(request, 'event_delete.html', {**context, **view})

    @app.post('/g/{guild_id}/events/{event_id}/delete')
    async def delete_event(request: Request, guild_id: str, event_id: int):
        context = await event_context(request, guild_id, event_id)
        require_manage(context)
        auth.check_csrf(context['session'], (await request.form()).get('csrf'))

        notes = await service.delete_event(bot, context['event'])
        return redirect(request, f"/g/{guild_id}", 'ok',
                        ' '.join([f"Deleted event #{event_id}."] + notes))

    @app.post('/g/{guild_id}/events/{event_id}/rsvp')
    async def rsvp(request: Request, guild_id: str, event_id: int):
        context = await event_context(request, guild_id, event_id)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        try:
            note = await service.rsvp(
                bot, context['event'], context['member'], form.get('response')
            )
        except ValueError as e:
            return redirect(request, f"/g/{guild_id}/events/{event_id}", 'warn', str(e))
        return redirect(request, f"/g/{guild_id}/events/{event_id}", 'ok', note)
