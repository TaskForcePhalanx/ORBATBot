"""The member log and the welcome message.

Moved out of web/app.py unchanged — see register().
"""
from cogs.memberlog import DEFAULT_WELCOME_TEMPLATE, WELCOME_PLACEHOLDERS
from fastapi import Request
from fastapi.responses import HTMLResponse
from utils import database
from web import auth, invites as invite_service
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
    guild_context = d.guild_context
    require_admin = d.require_admin

    # -- member logging -----------------------------------------------------

    @app.get('/g/{guild_id}/logs', response_class=HTMLResponse)
    async def log_settings(request: Request, guild_id: str, saved: bool = False):
        context = await guild_context(request, guild_id)
        require_admin(context)
        guild = context['guild']
        perms = guild.me.guild_permissions if guild.me else None
        return render(request, 'logs.html', {
            **context,
            'settings': await database.get_log_settings(str(guild_id)),
            'channels': postable_channels(guild),
            'member_events': bool(bot.intents.members),
            'can_read_audit': bool(perms and perms.view_audit_log),
            'welcome_placeholders': WELCOME_PLACEHOLDERS,
            'welcome_default_template': DEFAULT_WELCOME_TEMPLATE,
            **await invite_service.overview(guild),
        })

    @app.post('/g/{guild_id}/logs')
    async def save_log_settings(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_admin(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        channel_id = (form.get('channel_id') or '').strip()
        postable_ids = [str(c.id) for c in postable_channels(context['guild'])]
        if channel_id and channel_id not in postable_ids:
            return redirect(request, f"/g/{guild_id}/logs", 'warn',
                            "I can't post in that channel — pick another one.")

        welcome_channel_id = (form.get('welcome_channel_id') or '').strip()
        if welcome_channel_id and welcome_channel_id not in postable_ids:
            return redirect(request, f"/g/{guild_id}/logs", 'warn',
                            "I can't post in that welcome channel — pick another one.")

        await database.save_log_settings(str(guild_id), {
            'channel_id': channel_id or None,
            **{f'log_{kind}': 1 if form.get(f'log_{kind}') else 0
               for kind in ('join', 'leave', 'kick', 'ban', 'unban')},
            'track_invites': 1 if form.get('track_invites') else 0,
            'welcome_channel_id': welcome_channel_id or None,
            'welcome_message': (form.get('welcome_message') or '').strip()[:1000] or None,
            'welcome_dm': 1 if form.get('welcome_dm') else 0,
        })
        return redirect(request, f"/g/{guild_id}/logs", 'ok',
                        'Logging settings saved.' if channel_id else
                        'Logging is off — no channel is selected.')

    @app.post('/g/{guild_id}/logs/invites')
    async def save_invite_labels(request: Request, guild_id: str):
        context = await guild_context(request, guild_id)
        require_admin(context)
        form = await request.form()
        auth.check_csrf(context['session'], form.get('csrf'))

        labels, remove = invite_service.read_form(form)
        await database.save_invite_labels(str(guild_id), labels, remove)
        return redirect(request, f"/g/{guild_id}/logs", 'ok',
                        f"Saved {len(labels)} invite label(s).")
