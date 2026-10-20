import base64
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import pytest
from conftest import Request

import server.auth_cloudflare as cloudflare
from server.auth import browser_auth
from server.auth_cloudflare import COOKIE, FLOW_COOKIE, customer_id
from server.http import ApiError, check_origin


def set_cookies(response):
    return [value for key, value in response.headers if key == "Set-Cookie"]


def location(response):
    return next(value for key, value in response.headers if key == "Location")


def pair(set_cookie):
    """The name=value part a browser sends back."""
    return set_cookie.split(";", 1)[0]


def answer(status, data):
    return SimpleNamespace(status=status, json=AsyncMock(return_value=data))


class FakeCloudflare:
    """Cloudflare's OAuth endpoints, recording what the Worker sends."""

    def __init__(self, *, token_status=200, profile_status=200):
        self.token_status = token_status
        self.profile_status = profile_status
        self.requests = []

    async def __call__(self, url, method="GET", headers=None, body=None):
        self.requests.append(SimpleNamespace(url=url, headers=headers or {}, body=body))
        if url.endswith("/oauth2/token"):
            return answer(self.token_status, {"access_token": "token-1", "token_type": "bearer"})
        if url.endswith("/oauth2/userinfo"):
            # Cloudflare publishes only the "sub" claim.
            return answer(200, {"sub": "user-1"})
        if url.endswith("/client/v4/user"):
            profile = {"result": {"email": "korinne@example.com", "first_name": "Korinne"}}
            return answer(self.profile_status, profile if self.profile_status == 200 else {})
        return answer(200, {})


@pytest.fixture
def cloudflare_api(monkeypatch):
    api = FakeCloudflare()
    monkeypatch.setattr(cloudflare, "fetch", api)
    return api


async def start(env):
    """Begins a sign-in. Returns the state sent to Cloudflare and the flow cookie."""
    response = await browser_auth(env).login(Request("/auth/login"))
    state = parse_qs(urlparse(location(response)).query)["state"][0]
    return state, pair(set_cookies(response)[0])


async def test_sign_in_sends_the_browser_to_cloudflare_with_pkce(env):
    response = await browser_auth(env).login(Request("/auth/login"))
    assert response.status == 303
    target = urlparse(location(response))
    query = {key: values[0] for key, values in parse_qs(target.query).items()}
    assert f"{target.scheme}://{target.netloc}{target.path}" == (
        "https://dash.cloudflare.com/oauth2/auth"
    )
    assert query["client_id"] == "test-client"
    assert query["redirect_uri"] == "https://voice.example/auth/callback"
    assert query["scope"] == "user-details.read"
    assert query["code_challenge_method"] == "S256"
    [flow] = set_cookies(response)
    # Lax, so the cookie comes back on the navigation from Cloudflare's site.
    for attribute in ("HttpOnly", "Secure", "SameSite=Lax", "Path=/auth/callback"):
        assert attribute in flow
    saved = browser_auth(env).signer("sign-in").loads(pair(flow).split("=", 1)[1])
    assert saved["state"] == query["state"]
    digest = hashlib.sha256(saved["verifier"].encode()).digest()
    assert base64.urlsafe_b64encode(digest).decode().rstrip("=") == query["code_challenge"]


async def test_callback_signs_in_the_cloudflare_user_and_revokes_the_token(env, cloudflare_api):
    state, flow = await start(env)
    response = await browser_auth(env).callback(
        Request(f"/auth/callback?code=code-1&state={state}", cookie=flow)
    )
    assert location(response) == "/"
    cleared, session = set_cookies(response)
    assert cleared.startswith(f"{FLOW_COOKIE}=;") and "Max-Age=0" in cleared
    assert "SameSite=Strict" in session and "HttpOnly" in session
    signed_in = Request("/api/session", cookie=pair(session))
    caller = await browser_auth(env).identity(signed_in)
    assert caller.customer_id == customer_id("user-1")
    assert len(caller.owner) == 32
    assert (await browser_auth(env).session(signed_in))["customer"] == {
        "id": customer_id("user-1"),
        "name": "Korinne",
        "email": "korinne@example.com",
    }
    token, *_, revoke = cloudflare_api.requests
    form = parse_qs(token.body)
    verifier = browser_auth(env).signer("sign-in").loads(flow.split("=", 1)[1])["verifier"]
    assert form["code"] == ["code-1"] and form["code_verifier"] == [verifier]
    assert form["redirect_uri"] == ["https://voice.example/auth/callback"]
    credentials = base64.b64encode(b"test-client:test-client-secret").decode()
    assert token.headers["Authorization"] == f"Basic {credentials}"
    assert revoke.url.endswith("/oauth2/revoke")
    assert parse_qs(revoke.body)["token"] == ["token-1"]


async def test_sign_in_works_without_the_profile(env, cloudflare_api):
    cloudflare_api.profile_status = 403
    state, flow = await start(env)
    response = await browser_auth(env).callback(
        Request(f"/auth/callback?code=code-1&state={state}", cookie=flow)
    )
    assert location(response) == "/"
    signed_in = Request("/api/session", cookie=pair(set_cookies(response)[1]))
    assert (await browser_auth(env).session(signed_in))["customer"] == {
        "id": customer_id("user-1"),
        "name": "",
        "email": "",
    }


@pytest.mark.parametrize("tamper", ["other-state", "no-cookie", "forged-cookie"])
async def test_a_sign_in_this_browser_did_not_start_is_refused(env, cloudflare_api, tamper):
    state, flow = await start(env)
    if tamper == "other-state":
        state = "x" * len(state)
    cookie = {"no-cookie": None, "forged-cookie": f"{FLOW_COOKIE}=forged"}.get(tamper, flow)
    response = await browser_auth(env).callback(
        Request(f"/auth/callback?code=code-1&state={state}", cookie=cookie)
    )
    assert location(response) == "/?sign_in=expired"
    assert not any(value.startswith(f"{COOKIE}=") for value in set_cookies(response))
    assert cloudflare_api.requests == []


async def test_cancelled_and_failed_sign_ins_set_no_session(env, cloudflare_api):
    cloudflare_api.token_status = 400
    state, flow = await start(env)
    cancelled = await browser_auth(env).callback(
        Request(f"/auth/callback?error=access_denied&state={state}", cookie=flow)
    )
    failed = await browser_auth(env).callback(
        Request(f"/auth/callback?code=code-1&state={state}", cookie=flow)
    )
    assert location(cancelled) == "/?sign_in=cancelled"
    assert location(failed) == "/?sign_in=failed"
    for response in (cancelled, failed):
        assert not any(value.startswith(f"{COOKIE}=") for value in set_cookies(response))


async def test_missing_or_forged_sessions_are_rejected(env):
    for cookie in (None, f"{COOKIE}=forged"):
        with pytest.raises(ApiError) as error:
            await browser_auth(env).identity(Request("/api/session", cookie=cookie))
        assert error.value.status == 401


async def test_missing_configuration_fails_closed(env):
    env.OAUTH_CLIENT_SECRET = ""
    with pytest.raises(ApiError) as error:
        await browser_auth(env).login(Request("/auth/login"))
    assert error.value.status == 503
    assert (await browser_auth(env).session(Request("/api/session")))["configured"] is False


async def test_sign_out_clears_the_session(env):
    response = await browser_auth(env).logout(Request("/api/logout", {}))
    assert response.headers["Set-Cookie"].startswith(f"{COOKIE}=;")
    assert "Max-Age=0" in response.headers["Set-Cookie"]


def test_mutations_require_same_origin():
    check_origin(Request("/api/calls", {}))
    for origin in ("", "https://attacker.example", "http://voice.example"):
        with pytest.raises(ApiError) as error:
            check_origin(Request("/api/calls", {}, origin=origin))
        assert error.value.status == 403
