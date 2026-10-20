from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from conftest import D1, Request, Response, session_cookie

from entry import Default

CALL = "b" * 32


@pytest.mark.parametrize(
    "path,method,status",
    [
        (f"/api/calls/{CALL}/unknown", "POST", 404),
        ("/api/calls/not-a-call/events", "GET", 404),
        (f"/api/calls/{CALL}/events", "POST", 405),
        (f"/api/calls/{CALL}/finish", "GET", 405),
        (f"/api/calls/{CALL}/warm", "POST", 405),
        (f"/media/{CALL}/input", "POST", 405),
        (f"/media/{CALL}/input/", "GET", 404),
        (f"/media//{CALL}/output", "GET", 404),
        ("/auth/callback", "POST", 405),
        ("/auth/unknown", "GET", 404),
        ("/api/login", "POST", 404),
    ],
)
async def test_reserved_routes_reject_invalid_paths_and_methods(ctx, env, path, method, status):
    env.CALLS = SimpleNamespace(getByName=Mock())
    env.ASSETS = SimpleNamespace(fetch=AsyncMock())
    response = await Default(ctx, env).fetch(Request(path, method=method))
    assert response.status == status
    env.CALLS.getByName.assert_not_called()
    env.ASSETS.fetch.assert_not_awaited()


async def test_media_forwarding_preserves_the_native_upgrade(ctx, env):
    upgrade = Response(status=101)
    stub = SimpleNamespace(fetch=AsyncMock(return_value=upgrade))
    env.CALLS = SimpleNamespace(getByName=Mock(return_value=stub))
    request = Request(f"/media/{CALL}/input?token=test-only")
    request.headers["Upgrade"] = "websocket"
    assert await Default(ctx, env).fetch(request) is upgrade
    env.CALLS.getByName.assert_called_once_with(CALL)
    stub.fetch.assert_awaited_once_with(request)


async def test_call_create_checks_origin_and_rate_limit_before_forwarding(ctx, env, owner_cookie):
    stub = SimpleNamespace(fetch=AsyncMock(return_value=Response(status=201)))
    env.CALLS = SimpleNamespace(getByName=lambda _: stub)
    env.CALL_LIMITER = SimpleNamespace(limit=AsyncMock(return_value=SimpleNamespace(success=False)))
    worker = Default(ctx, env)
    path = f"/api/calls/{CALL}/create"
    request = Request(path, {}, cookie=owner_cookie, origin="https://another.example")
    assert (await worker.fetch(request)).status == 403
    env.CALL_LIMITER.limit.assert_not_awaited()
    request = Request(path, {}, cookie=owner_cookie)
    assert (await worker.fetch(request)).status == 429
    stub.fetch.assert_not_awaited()
    env.CALL_LIMITER.limit.return_value.success = True
    assert (await worker.fetch(request)).status == 201
    stub.fetch.assert_awaited_once_with(request)


async def test_warming_forwards_without_spending_the_call_limit(ctx, env, owner_cookie):
    stub = SimpleNamespace(fetch=AsyncMock(return_value=Response(status=200)))
    env.CALLS = SimpleNamespace(getByName=Mock(return_value=stub))
    env.CALL_LIMITER = SimpleNamespace(limit=AsyncMock())
    request = Request(f"/api/calls/{CALL}/warm", cookie=owner_cookie)
    assert (await Default(ctx, env).fetch(request)).status == 200
    env.CALL_LIMITER.limit.assert_not_awaited()
    env.CALLS.getByName.assert_called_once_with(CALL)
    unauthenticated = Request(f"/api/calls/{CALL}/warm")
    assert (await Default(ctx, env).fetch(unauthenticated)).status == 401
    stub.fetch.assert_awaited_once_with(request)


async def test_frontend_paths_use_the_asset_binding(ctx, env):
    response = Response(data="frontend")
    env.ASSETS = SimpleNamespace(fetch=AsyncMock(return_value=response))
    request = Request("/settings")
    assert await Default(ctx, env).fetch(request) is response
    env.ASSETS.fetch.assert_awaited_once_with(request)


async def test_session_reports_who_is_signed_in(ctx, env, owner_cookie):
    signed_in = await Default(ctx, env).fetch(Request("/api/session", cookie=owner_cookie))
    assert signed_in.data["configured"] is True
    assert signed_in.data["authenticated"] is True
    assert signed_in.data["customer"] == {
        "id": "cus_maya",
        "name": "Maya Chen",
        "email": "maya@example.com",
    }
    signed_out = await Default(ctx, env).fetch(Request("/api/session"))
    assert signed_out.data["authenticated"] is False
    assert signed_out.data["customer"] is None


async def test_a_first_visit_creates_the_account_and_a_reset_recreates_it(ctx, env):
    from server.auth_cloudflare import customer_id
    from support import store

    customer = customer_id("user-9")
    cookie = session_cookie(env, customer=customer, email="new@example.com", name="Avery")
    session = await Default(ctx, env).fetch(Request("/api/session", cookie=cookie))
    assert session.data["authenticated"] is True
    assert session.data["customer"] == {"id": customer, "name": "Avery", "email": "new@example.com"}
    orders = await store.list_orders(env.DB, customer)
    assert [order["status"] for order in orders] == [
        "processing",
        "delivered",
        "shipped",
        "delivered",
    ]
    assert all(order["id"].startswith("A-") and len(order["id"]) == 6 for order in orders)
    # Visiting again keeps the same account.
    await Default(ctx, env).fetch(Request("/api/session", cookie=cookie))
    assert await store.list_orders(env.DB, customer) == orders
    await store.reset_demo(env.DB)
    again = await Default(ctx, env).fetch(Request("/api/session", cookie=cookie))
    assert again.data["authenticated"] is True
    assert len(await store.list_orders(env.DB, customer)) == 4


async def test_session_reports_missing_tables_as_setup(ctx, env):
    env.DB = D1(seeded=False)
    assert (await Default(ctx, env).fetch(Request("/api/session"))).data["configured"] is True
    env.DB.connection.executescript(
        "DROP TABLE approvals; DROP TABLE tickets; DROP TABLE orders; DROP TABLE customers;"
    )
    response = await Default(ctx, env).fetch(Request("/api/session"))
    assert response.data["configured"] is False
    assert response.data["setup_required"] == ["database"]


async def test_account_shows_only_the_signed_in_customers_records(ctx, env):
    cookie = session_cookie(env, customer="cus_sam")
    response = await Default(ctx, env).fetch(Request("/api/account", cookie=cookie))
    assert response.data["customer"]["name"] == "Sam Rivera"
    assert [order["id"] for order in response.data["orders"]] == ["A-1057"]
    assert response.data["tickets"] == [] and response.data["approvals"] == []
    assert (await Default(ctx, env).fetch(Request("/api/account"))).status == 401


@pytest.mark.parametrize(
    "path,method,body,status",
    [
        ("/desk/api/overview", "GET", None, 503),
        ("/desk/api/approvals/R-1234", "POST", {"decision": "approve"}, 503),
        ("/desk/api/approvals/T-1234", "POST", {"decision": "approve"}, 404),
        ("/desk/api/demo-data", "GET", None, 405),
    ],
)
async def test_desk_routes_fail_closed_until_access_is_configured(
    ctx, env, path, method, body, status
):
    env.ASSETS = SimpleNamespace(fetch=AsyncMock())
    response = await Default(ctx, env).fetch(Request(path, body, method=method))
    assert response.status == status
    env.ASSETS.fetch.assert_not_awaited()
