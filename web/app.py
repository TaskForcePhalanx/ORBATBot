"""The FastAPI application behind the web UI.

It runs inside the bot's own process and event loop, so a page can post a
Discord message, register a persistent view or read a member's roles directly —
no polling, no second deployment, no outbox table. `web/server.py` starts it.
"""

import hashlib
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from cogs.events import _RECURRENCE_LABELS, _recurrence_text
from utils import campaign as campaign_lib
from utils import database
from utils import help as help_lib
from web import auth, nav as nav_service, killtracker as killtracker_service, service
from web.auth import Forbidden, NotAuthenticated
from web.config import LOGO_NAMES, WebConfig
from web.guilds import (
    can_action_slots,
    can_plan,
    can_create_events,
    can_manage_event,
    is_admin,
    resolve_member,
)
from web.helpers import fmt_date, fmt_dt, fmt_input, message_link, relative
from web.routes import (
    campaigns,
    embeds,
    helppages,
    killtracker,
    login,
    logs,
    maps,
    modlists,
    promotion,
    operation,
    orbats,
    pages,
    roles,
    slots,
    voice,
)

_HERE = Path(__file__).parent


def _logo_url() -> str:
    """The site logo, if one was dropped into web/static — '' when there is none.

    The file's modification time rides along as a query string so replacing the
    logo isn't hidden behind a cached copy in someone's browser.
    """
    for name in LOGO_NAMES:
        path = _HERE / 'static' / name
        if path.exists():
            return f"/static/{name}?v={int(path.stat().st_mtime)}"
    return ''


def _static_version(name: str) -> str:
    """A short hash of a static file's contents, for its `?v=`. The contents,
    not the modification time: a build can reset every file's mtime to the
    same value, and then a changed stylesheet keeps the old address and the
    browser keeps the old copy — which is how a new feature arrived unstyled."""
    path = _HERE / 'static' / name
    if not path.exists():
        return '0'
    return hashlib.sha1(path.read_bytes()).hexdigest()[:10]


_STATIC_VERSIONS: dict[str, str] = {}


def static_url(name: str) -> str:
    """`/sv/<hash>/<name>` — every stylesheet and script goes through this, so
    a deploy that changes one is never served from a stale cache. Hashed once
    per file per process; a deploy is a new process.

    The version is in the **path**, not a `?v=` query. A cache in front of the
    site that keys on the path alone (a CDN set to ignore query strings) went on
    serving the previous deploy's `tacmap.js` under the new `?v=`, while the
    stylesheet beside it had already expired — so the editor drew labels the
    old way on a page whose CSS was new. Nothing keys a path that way."""
    if name not in _STATIC_VERSIONS:
        _STATIC_VERSIONS[name] = _static_version(name)
    return f'/sv/{_STATIC_VERSIONS[name]}/{name}'


def static_file(name: str):
    """The file `static_url()` names, or None — never anything outside
    `web/static/`, whatever the path says."""
    root = (_HERE / 'static').resolve()
    path = (root / name).resolve()
    if root not in path.parents or not path.is_file():
        return None
    return path


def _help_for(page: str) -> str:
    """The how-to URL for one web page, or '' when there is none."""
    topics = help_lib.for_page(page)
    return f"/help/{topics[0].key}" if topics else ''


def create_app(bot, config: WebConfig) -> FastAPI:
    app = FastAPI(title='ORBAT', docs_url=None, redoc_url=None, openapi_url=None)
    app.mount('/static', StaticFiles(directory=_HERE / 'static'), name='static')

    @app.get('/sv/{version}/{name:path}', include_in_schema=False)
    async def versioned_static(version: str, name: str):
        # The current file whatever the version says: a page from before a
        # deploy asking for its old hash gets the new file rather than a 404,
        # and that page reloads onto the new address soon enough. Immutable
        # only when the hash is the one this process serves.
        path = static_file(name)
        if path is None:
            return PlainTextResponse('Not found', status_code=404)
        current = version == _STATIC_VERSIONS.get(name) or version == _static_version(name)
        cache = ('public, max-age=31536000, immutable' if current
                 else 'no-cache')
        return FileResponse(path, headers={'Cache-Control': cache})

    templates = Jinja2Templates(directory=str(_HERE / 'templates'))
    templates.env.globals.update(
        brand=config.brand,
        logo_url=_logo_url(),
        # Absolute origin, needed for the link-preview image: og:image is fetched
        # by other sites, so a relative path is useless there.
        site_url=config.base_url,
        fmt_dt=fmt_dt,
        fmt_date=fmt_date,
        fmt_input=fmt_input,
        relative=relative,
        message_link=message_link,
        recurrence_text=_recurrence_text,
        recurrence_labels=_RECURRENCE_LABELS,
        reminder_choices=service.REMINDER_CHOICES,
        repeat_choices=service.REPEAT_CHOICES,
        # The `?` beside a page heading. A page names itself with the key
        # `web/nav.py` gave it and gets the URL of its how-to, or '' when it
        # has none — which `tests/test_help.py` is there to prevent.
        help_for=_help_for,
        # Cache-busting for every stylesheet and script — see static_url().
        static_url=static_url,
    )
    # The little Markdown the how-tos are written in. It escapes first, so the
    # result is safe to mark safe — see `utils/help.inline_html`.
    templates.env.filters['inline_md'] = lambda text: Markup(
        help_lib.inline_html(text or ''))

    # -- request plumbing ---------------------------------------------------

    def session_of(request: Request):
        return auth.read_session(request, config)

    def require_session(request: Request) -> dict:
        session = session_of(request)
        if session is None:
            raise NotAuthenticated(request.url.path)
        return session

    def render(request: Request, name: str, context: dict, status: int = 200):
        session = session_of(request)
        flash = auth.read_flash(request, config)
        context = {
            'session': session,
            'avatar': auth.avatar_url(session) if session else None,
            'flash': flash,
            'csrf': (session or {}).get('csrf', ''),
            **context,
        }
        response = templates.TemplateResponse(
            request=request, name=name, context=context, status_code=status
        )
        if flash:
            response.delete_cookie(auth.FLASH_COOKIE, path='/')
        return response

    def redirect(request: Request, path: str, kind: str = None, text: str = None):
        response = RedirectResponse(path, status_code=303)
        if text:
            auth.set_flash(response, config, kind or 'ok', text, request)
        return response

    def form_values(form) -> dict:
        """Flatten a submitted form, keeping the ping roles as a list."""
        data = {key: form.get(key) for key in form.keys()}
        data['mention_ids'] = form.getlist('mention_ids')
        return data

    async def guild_context(request: Request, guild_id: str) -> dict:
        """The signed-in user seen as a member of one guild, or an error."""
        session = require_session(request)
        guild = bot.get_guild(int(guild_id)) if str(guild_id).isdigit() else None
        if guild is None:
            raise Forbidden("I'm not in that server.")
        member = await resolve_member(bot, guild, session['id'])
        if member is None:
            raise Forbidden(f"You're not a member of {guild.name}.")
        # The campaign planner is open to admins and the unit's own Unit
        # Leaders; which role counts as "the unit" is a planner setting.
        planner = await database.get_cp_settings(str(guild.id))
        may_plan = can_plan(member, planner['unit_role_id'] if planner else None)
        # Who may edit the ORBATs campaign missions link — a planner setting,
        # see utils.campaign.may_edit_orbats().
        may_edit_orbats = campaign_lib.may_edit_orbats(
            campaign_lib.settings_with_defaults(planner), is_admin=is_admin(member),
            may_plan=may_plan, role_ids=[r.id for r in member.roles])
        return {
            'session': session,
            'guild': guild,
            'member': member,
            'tz': await database.get_guild_timezone(str(guild.id)),
            'may_create': can_create_events(member),
            'may_action_slots': can_action_slots(member),
            'may_plan': may_plan,
            'may_edit_orbats': may_edit_orbats,
            'is_admin': is_admin(member),
            # The tab bar, built from what this member may open — see web/nav.py.
            'nav': nav_service.build(
                guild.id,
                is_admin=is_admin(member),
                may_action_slots=can_action_slots(member),
                may_plan=may_plan,
                may_edit_orbats=may_edit_orbats,
            ),
        }

    async def event_context(request: Request, guild_id: str, event_id: int) -> dict:
        context = await guild_context(request, guild_id)
        event = await database.get_event(event_id)
        if event is None or event['guild_id'] != str(context['guild'].id):
            raise Forbidden("No such event on this server.")
        context['event'] = event
        context['may_manage'] = can_manage_event(context['member'], event)
        return context

    def require_manage(context: dict):
        if not context['may_manage']:
            raise Forbidden("Only the organiser or a server admin can change this event.")

    def require_admin(context: dict):
        # Six features ask this, so the message cannot name one of them. It
        # used to live in the game-roles section and say "manage the game roles
        # themselves", which is what a non-admin saw on the voice settings too.
        if not context['is_admin']:
            raise Forbidden("Only a server admin can change this.")

    # -- error handling -----------------------------------------------------

    @app.exception_handler(NotAuthenticated)
    async def _not_authenticated(request: Request, exc: NotAuthenticated):
        return RedirectResponse(f"/login?next={exc.next_path}", status_code=303)

    @app.exception_handler(Forbidden)
    async def _forbidden(request: Request, exc: Forbidden):
        return render(request, 'error.html',
                      {'title': 'Not allowed', 'message': exc.message}, status=403)


    # -- the features -------------------------------------------------------
    #
    # Each module below owns one feature's routes and nothing else. They were
    # one 2,600-line create_app() until this split; the bodies moved out
    # unchanged, and what stayed here is the setup above and the plumbing they
    # all share, handed over as one namespace. A new feature is a new module
    # and one line in this list — not another screen of scrolling.

    d = SimpleNamespace(
        bot=bot,
        config=config,
        templates=templates,
        session_of=session_of,
        require_session=require_session,
        render=render,
        redirect=redirect,
        form_values=form_values,
        guild_context=guild_context,
        event_context=event_context,
        require_manage=require_manage,
        require_admin=require_admin,
    )

    for feature in (login, pages, roles, slots, operation, orbats, maps, embeds,
                    voice, logs, campaigns, helppages, killtracker, modlists, promotion):
        feature.register(app, d)

    # The Kill Tracker's numbers: public, read-only, no session. See web/killtracker.py.
    killtracker_service.install(app)

    return app
