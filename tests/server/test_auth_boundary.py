import sys
import uuid
from types import SimpleNamespace

import pytest
from conftest import Request, Storage, session_cookie

import entry
import server.call as call_module
import server.routes as routes_module
from server.auth import Caller, browser_auth
from server.auth_cloudflare import COOKIE, SESSION_SECONDS, CloudflareAuth
from server.call import VoiceCall
from server.http import ApiError
from transport.auth import AdapterAuth

CALL = "b" * 32


async def test_an_async_identity_provider_preserves_call_ownership(monkeypatch, ctx, env):
    class Provider:
        async def identity(self, request):
            # A provider's verified principal need not be a cookie or a hex ID.
            return Caller(owner=request.headers["X-Test-Principal"], customer_id="cus_maya")

    monkeypatch.setattr(call_module, "browser_auth", lambda _: Provider())
    call = VoiceCall(ctx, env)
    create = Request(f"/api/calls/{CALL}/create", {})
    create.headers["X-Test-Principal"] = "example-idp:user-1"
    assert (await call.fetch(create)).status == 201
    assert call.state["owner"] == "example-idp:user-1"
    assert call.state["customer"] == {"id": "cus_maya", "name": "Maya Chen"}
    for principal, expected in [("example-idp:user-1", 200), ("example-idp:user-2", 404)]:
        status = Request(f"/api/calls/{CALL}/status")
        status.headers["X-Test-Principal"] = principal
        assert (await call.fetch(status)).status == expected


async def test_adapter_routes_skip_browser_auth_without_opening_call_apis(monkeypatch, ctx, env):
    def deny(_env):
        raise ApiError("login_required", 401)

    monkeypatch.setattr(routes_module, "browser_auth", deny)
    env.CALLS = SimpleNamespace(getByName=lambda _: VoiceCall(ctx, env))
    worker = entry.Default(ctx, env)
    # No live call/adapter token: media reaches its own authorization boundary.
    assert (await worker.fetch(Request(f"/media/{CALL}/input"))).status == 403
    assert (await worker.fetch(Request(f"/media/{CALL}/output"))).status == 403
    assert (await worker.fetch(Request(f"/media/{CALL}/status"))).status == 404
    assert (await worker.fetch(Request(f"/api/calls/{CALL}/create", {}))).status == 401


def test_adapter_tokens_are_scoped_revocable_and_allow_reconnects(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "js",
        SimpleNamespace(crypto=SimpleNamespace(randomUUID=lambda: str(uuid.uuid4()))),
    )
    first, other = AdapterAuth(), AdapterAuth()
    token = first.issue()
    other.issue()
    assert first.accepts(token)
    assert first.accepts(token)
    assert not first.accepts("")
    assert not first.accepts("wrong")
    assert not other.accepts(token)
    next_token = first.issue()
    assert not first.accepts(token)
    assert first.accepts(next_token)
    first.revoke()
    assert not first.accepts(next_token)


async def test_sessions_expire(env, monkeypatch):
    from itsdangerous import TimestampSigner

    signer = CloudflareAuth(env).signer("session")
    token = signer.dumps({"owner": "a" * 32, "customer": "cus_maya", "email": "", "name": ""})
    now = TimestampSigner("key").get_timestamp()
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda _: now + SESSION_SECONDS + 1)
    request = Request("/api/session", cookie=f"{COOKIE}={token}")
    assert (await browser_auth(env).session(request))["customer"] is None
    with pytest.raises(ApiError) as error:
        await browser_auth(env).identity(request)
    assert error.value.status == 401


async def test_a_call_keeps_its_customer_and_rejects_removed_ones(ctx, env):
    call = VoiceCall(ctx, env)
    create = Request(f"/api/calls/{CALL}/create", {}, cookie=session_cookie(env))
    assert (await call.fetch(create)).status == 201
    # A browser that switches customers can't reuse the call for someone else.
    switched = Request(
        f"/api/calls/{CALL}/create", {}, cookie=session_cookie(env, customer="cus_sam")
    )
    assert (await call.fetch(switched)).status == 409
    env.DB.connection.execute("DELETE FROM orders WHERE customer_id = 'cus_rosa'")
    env.DB.connection.execute("DELETE FROM customers WHERE id = 'cus_rosa'")
    removed = session_cookie(env, customer="cus_rosa")
    other = VoiceCall(SimpleNamespace(storage=Storage(), waitUntil=lambda _: None), env)
    response = await other.fetch(Request(f"/api/calls/{CALL}/create", {}, cookie=removed))
    assert response.status == 401
