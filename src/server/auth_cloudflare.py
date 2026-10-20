"""Sign in with Cloudflare: OAuth with PKCE, then a signed session cookie.

Cloudflare tells the Worker who the visitor is. The Worker keeps the user's ID,
email, and first name, revokes the access token right away, and signs a cookie
that names the visitor's Acme account. The browser can't change whose account
it holds.

A new OAuth client is private, so only members of the Cloudflare account that
owns it can sign in. That keeps strangers from running up model costs.
"""

import base64
import hashlib
import hmac
import secrets
import uuid
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlencode, urlparse

from itsdangerous import BadSignature, URLSafeTimedSerializer
from workers import Response, fetch

from server.auth import Caller
from server.http import ApiError, check_origin, reply
from server.observability import exception_fields, log

OAUTH = "https://dash.cloudflare.com/oauth2"
USER_API = "https://api.cloudflare.com/client/v4/user"
# Reads the user's own details. It grants nothing on their Cloudflare account.
SCOPE = "user-details.read"
CALLBACK = "/auth/callback"
COOKIE = "acme_session"
SESSION_SECONDS = 8 * 60 * 60
# Holds the state and PKCE verifier while the visitor is on Cloudflare's site.
FLOW_COOKIE = "acme_sign_in"
FLOW_SECONDS = 10 * 60


class SignInFailed(Exception):
    def __init__(self, stage, status):
        self.stage, self.status = stage, status
        super().__init__(f"{stage} returned {status}")


def customer_id(subject: str) -> str:
    """Acme's customer ID for a Cloudflare user.

    A real store looks up its own customer record for the verified login. The
    demo derives the ID instead, so a data reset can recreate the same account.
    """
    return "cus_" + hashlib.sha256(f"cloudflare:{subject}".encode()).hexdigest()[:24]


def cookie(request, name, value, max_age, *, same_site="Strict", path="/"):
    secure = "; Secure" if urlparse(request.url).scheme == "https" else ""
    return f"{name}={value}; Path={path}; HttpOnly; SameSite={same_site}; Max-Age={max_age}{secure}"


def redirect(location, cookies=()):
    headers = [("Location", location), ("Cache-Control", "no-store")]
    return Response(None, status=303, headers=headers + [("Set-Cookie", c) for c in cookies])


def setting(env, key):
    return str(getattr(env, key, "") or "")


def callback_url(request):
    url = urlparse(request.url)
    return f"{url.scheme}://{url.netloc}{CALLBACK}"


class CloudflareAuth:
    def __init__(self, env):
        self.env = env

    @property
    def configured(self):
        return (
            bool(setting(self.env, "OAUTH_CLIENT_ID"))
            and bool(setting(self.env, "OAUTH_CLIENT_SECRET"))
            and len(setting(self.env, "SESSION_SECRET")) >= 32
        )

    def signer(self, purpose):
        if not self.configured:
            raise ApiError("setup_required", 503)
        return URLSafeTimedSerializer(str(self.env.SESSION_SECRET), salt=f"acme-{purpose}")

    def read(self, request, name, purpose, max_age):
        cookies = SimpleCookie()
        cookies.load(request.headers.get("Cookie", ""))
        token = cookies.get(name)
        return self.signer(purpose).loads(token.value, max_age=max_age) if token else None

    def signed_in(self, request) -> dict:
        """The verified session, or ApiError when there isn't one."""
        try:
            value = self.read(request, COOKIE, "session", SESSION_SECONDS)
            owner = value["owner"]
            if not isinstance(owner, str) or len(owner) != 32:
                raise ValueError()
            if not all(isinstance(value[key], str) for key in ("customer", "email", "name")):
                raise ValueError()
            return value
        except BadSignature, ValueError, AttributeError, KeyError, TypeError:
            raise ApiError("login_required", 401) from None

    async def identity(self, request):
        value = self.signed_in(request)
        return Caller(owner=value["owner"], customer_id=value["customer"])

    async def session(self, request):
        value = None
        if self.configured:
            try:
                value = self.signed_in(request)
            except ApiError:
                pass
        return {
            "configured": self.configured,
            "customer": value
            and {"id": value["customer"], "name": value["name"], "email": value["email"]},
        }

    async def login(self, request):
        signer = self.signer("sign-in")
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        query = urlencode(
            {
                "response_type": "code",
                "client_id": str(self.env.OAUTH_CLIENT_ID),
                "redirect_uri": callback_url(request),
                "scope": SCOPE,
                "state": state,
                "code_challenge": challenge.decode().rstrip("="),
                "code_challenge_method": "S256",
            }
        )
        flow = signer.dumps({"state": state, "verifier": verifier})
        # Lax, because the trip back is a navigation that starts on Cloudflare's site.
        flow_cookie = cookie(
            request, FLOW_COOKIE, flow, FLOW_SECONDS, same_site="Lax", path=CALLBACK
        )
        return redirect(f"{OAUTH}/auth?{query}", [flow_cookie])

    async def callback(self, request):
        query = parse_qs(urlparse(request.url).query)
        state, code, error = ((query.get(key) or [""])[0] for key in ("state", "code", "error"))

        def finish(outcome=None, session=None):
            cleared = cookie(request, FLOW_COOKIE, "", 0, same_site="Lax", path=CALLBACK)
            target = f"/?sign_in={outcome}" if outcome else "/"
            return redirect(target, [cleared, *([session] if session else [])])

        try:
            flow = self.read(request, FLOW_COOKIE, "sign-in", FLOW_SECONDS)
            expected, verifier = flow["state"], flow["verifier"]
        except BadSignature, KeyError, TypeError:
            return finish("expired")
        # The state proves this browser started the sign-in it's finishing.
        if not hmac.compare_digest(state.encode(), str(expected).encode()):
            return finish("expired")
        if error or not code:
            return finish("cancelled" if error == "access_denied" else "failed")
        try:
            token = await self.exchange(request, code, verifier)
            try:
                user = await self.user(token)
            finally:
                await self.revoke(token)
        except SignInFailed as exc:
            log.warning("sign_in_failed", operation="sign_in", stage=exc.stage, status=exc.status)
            return finish("failed")
        except Exception as exc:
            log.warning("sign_in_failed", operation="sign_in", **exception_fields(exc))
            return finish("failed")
        value = self.signer("session").dumps(
            {
                # Owns this browser's calls. A new sign-in starts a new owner.
                "owner": uuid.uuid4().hex,
                "customer": customer_id(user["sub"]),
                "email": user["email"],
                "name": user["name"],
            }
        )
        log.info("signed_in", operation="sign_in")
        return finish(session=cookie(request, COOKIE, value, SESSION_SECONDS))

    def client_headers(self):
        pair = f"{self.env.OAUTH_CLIENT_ID}:{self.env.OAUTH_CLIENT_SECRET}"
        return {
            "Authorization": "Basic " + base64.b64encode(pair.encode()).decode(),
            "Content-Type": "application/x-www-form-urlencoded",
        }

    async def exchange(self, request, code, verifier):
        response = await fetch(
            f"{OAUTH}/token",
            method="POST",
            headers=self.client_headers(),
            body=urlencode(
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": callback_url(request),
                    "code_verifier": verifier,
                }
            ),
        )
        data = await response.json() if response.status == 200 else {}
        token = data.get("access_token") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token:
            raise SignInFailed("token", response.status)
        return token

    async def user(self, token):
        bearer = {"Authorization": f"Bearer {token}"}
        response = await fetch(f"{OAUTH}/userinfo", headers=bearer)
        info = await response.json() if response.status == 200 else {}
        subject = info.get("sub") if isinstance(info, dict) else None
        if not isinstance(subject, str) or not subject:
            raise SignInFailed("userinfo", response.status)
        # Cloudflare's userinfo has only the user ID, so the email and first name
        # come from the user API. Sign-in works without them; Matt then uses no
        # name, and the desk shows no email.
        email = name = ""
        try:
            profile = await fetch(USER_API, headers=bearer)
            if profile.status == 200:
                result = (await profile.json()).get("result") or {}
                email = str(result.get("email") or "").strip()
                name = str(result.get("first_name") or "").strip()
            else:
                log.warning("profile_unavailable", operation="sign_in", status=profile.status)
        except Exception as exc:
            log.warning("profile_unavailable", operation="sign_in", **exception_fields(exc))
        return {"sub": subject, "email": email[:254], "name": name[:80]}

    async def revoke(self, token):
        """Acme needs only who the visitor is, so it gives the token back."""
        try:
            await fetch(
                f"{OAUTH}/revoke",
                method="POST",
                headers=self.client_headers(),
                body=urlencode({"token": token}),
            )
        except Exception as exc:
            log.warning("token_revoke_failed", operation="sign_in", **exception_fields(exc))

    async def logout(self, request):
        check_origin(request)
        return reply(
            {"authenticated": False}, headers={"Set-Cookie": cookie(request, COOKIE, "", 0)}
        )
