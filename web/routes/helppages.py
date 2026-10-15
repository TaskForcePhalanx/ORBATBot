"""The how-tos, and the health check.

Moved out of web/app.py unchanged — see register().
"""
from fastapi import Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from utils import database
from utils import help as help_lib

def register(app, d):
    """Add this feature's routes to the app.

    `d` is the namespace web/app.py builds: the bot, the config and
    the request plumbing every page shares. Rebinding them here is
    what lets every route below stay exactly as it was written.
    """
    bot = d.bot
    render = d.render

    # -- help ---------------------------------------------------------------
    #
    # Deliberately outside the guild: the how-tos are the same on every server,
    # they name tabs rather than linking at them, and somebody who cannot get
    # past the sign-in page is exactly the person who needs to read one. There
    # is no `web/help.py` to go with these two routes because there would be
    # nothing in it — `utils/help.py` holds the content and there are no
    # permissions to check and no form to translate.

    @app.get('/help', response_class=HTMLResponse)
    async def help_index(request: Request, q: str = ''):
        query = (q or '').strip()[:100]
        return render(request, 'help.html', {
            'catalog': help_lib.catalog(),
            'query': query,
            'results': help_lib.search(query) if query else [],
            'total': len(help_lib.TOPICS),
        })

    @app.get('/help/{key}', response_class=HTMLResponse)
    async def help_topic(request: Request, key: str):
        topic = help_lib.topic(key)
        if topic is None:
            return render(request, 'error.html', {
                'title': 'No such how-to',
                'message': "That help page doesn't exist. Try the index.",
            }, status=404)
        return render(request, 'help_topic.html', {
            'topic': topic,
            'shelf': help_lib.group(topic.group),
            'related': help_lib.related(topic),
        })

    @app.get('/healthz', response_class=PlainTextResponse)
    async def healthz():
        """Whether the bot is up, and whether the database answers.

        **The status code stays 200 while the process is alive**, and the body
        carries the detail. Nothing consumes this today — `railway.json` sets
        no `healthcheckPath` — so a non-200 would restart nothing; the moment
        one is wired up, a failing code would restart the container on a
        momentary database blip, possibly mid-operation. A monitor that wants
        to alert reads the body.
        """
        ready = bot.is_ready()
        alive = await database.ping()
        return 'bot %s \u00b7 db %s' % ('ok' if ready else 'starting',
                                    'ok' if alive else 'unreachable')
