"""Signing in and out — the OAuth2 dance.

Moved out of web/app.py unchanged — see register().
"""
from fastapi import Request
from fastapi.responses import RedirectResponse
from web import auth
from web.auth import Forbidden

def register(app, d):
    """Add this feature's routes to the app.

    `d` is the namespace web/app.py builds: the bot, the config and
    the request plumbing every page shares. Rebinding them here is
    what lets every route below stay exactly as it was written.
    """
    config = d.config
    session_of = d.session_of

    # -- auth ---------------------------------------------------------------

    @app.get('/login')
    async def login(request: Request, next: str = '/'):
        if session_of(request):
            return RedirectResponse(auth.safe_next(next), status_code=303)
        url, state = auth.authorize_url(config, request, next)
        response = RedirectResponse(url, status_code=303)
        # The nonce inside the signed state is compared against this cookie on
        # the way back, so a callback nobody here started is rejected.
        response.set_cookie(
            auth.STATE_COOKIE, state, max_age=auth.STATE_MAX_AGE, httponly=True,
            samesite='lax', secure=config.cookie_secure_for(request), path='/',
        )
        return response

    @app.get('/auth/callback')
    async def callback(request: Request, code: str = None, state: str = None,
                       error: str = None, error_description: str = None):
        if error:
            raise Forbidden(f"Discord cancelled the login: {error_description or error}")
        if not code or not state:
            raise Forbidden("That callback was missing its code — start again from the front page.")
        if state != request.cookies.get(auth.STATE_COOKIE):
            raise Forbidden("That login didn't start here. Try again from the front page.")

        payload = auth.read_state(config, state)
        profile = await auth.exchange_code(config, request, code)

        response = RedirectResponse(auth.safe_next(payload.get('n')), status_code=303)
        auth.write_session(response, config, auth.new_session(profile), request)
        response.delete_cookie(auth.STATE_COOKIE, path='/')
        return response

    @app.post('/logout')
    async def logout(request: Request):
        session = session_of(request)
        if session:
            auth.check_csrf(session, (await request.form()).get('csrf'))
        response = RedirectResponse('/', status_code=303)
        auth.clear_session(response)
        return response
