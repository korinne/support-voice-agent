import base64
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from conftest import Request

from server import desk
from server.http import ApiError
from support import store

TEAM = "https://acme.cloudflareaccess.com"
AUDIENCE = "test-audience"


def encode(value):
    return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()


def token(alg="RS256", kid="key-1", **claims):
    payload = {
        "iss": TEAM,
        "aud": [AUDIENCE],
        "email": "ana@acme.test",
        "exp": time.time() + 600,
        **claims,
    }
    return f"{encode({'alg': alg, 'kid': kid})}.{encode(payload)}.{encode('signature')}"


def staff_request(path, body=None, *, signed=None, origin="https://voice.example"):
    request = Request(path, body, origin=origin)
    request.headers[desk.TOKEN_HEADER] = signed or token()
    return request


@pytest.fixture
def staff_env(env, monkeypatch):
    # The team domain may be pasted without its scheme.
    env.ACCESS_TEAM_DOMAIN = "acme.cloudflareaccess.com/"
    env.ACCESS_AUD = AUDIENCE
    monkeypatch.setattr(desk, "_keys", {"team": None, "fetched": 0.0, "by_id": {}})
    monkeypatch.setattr(desk, "fetch_keys", AsyncMock(return_value={"key-1": {"n": "n", "e": "e"}}))
    monkeypatch.setattr(desk, "verify_rs256", AsyncMock(return_value=True))
    return env


async def test_a_signed_access_token_identifies_staff(staff_env):
    assert await desk.StaffAuth(staff_env).staff(staff_request("/desk/api/overview")) == (
        "ana@acme.test"
    )
    cookie = Request("/desk/api/overview", cookie=f"{desk.TOKEN_COOKIE}={token()}")
    assert await desk.StaffAuth(staff_env).staff(cookie) == "ana@acme.test"
    desk.fetch_keys.assert_awaited_once_with(TEAM)


@pytest.mark.parametrize(
    "signed",
    [
        token(aud=["another-app"]),
        token(iss="https://other.cloudflareaccess.com"),
        token(exp=time.time() - 3600),
        token(nbf=time.time() + 3600),
        token(email=None),
        token(alg="HS256"),
        token(kid="unknown"),
        "not-a-token",
    ],
)
async def test_tokens_for_another_app_or_time_or_algorithm_are_rejected(staff_env, signed):
    with pytest.raises(ApiError) as error:
        await desk.StaffAuth(staff_env).staff(staff_request("/desk/api/overview", signed=signed))
    assert error.value.code == "staff_login_required"


async def test_a_bad_signature_or_missing_token_is_rejected(staff_env):
    desk.verify_rs256.return_value = False
    with pytest.raises(ApiError) as error:
        await desk.StaffAuth(staff_env).staff(staff_request("/desk/api/overview"))
    assert error.value.status == 401
    with pytest.raises(ApiError) as error:
        await desk.StaffAuth(staff_env).staff(Request("/desk/api/overview"))
    assert error.value.status == 401


async def test_unknown_key_ids_refresh_the_keys_at_most_once_a_minute(staff_env):
    for _ in range(3):
        with pytest.raises(ApiError):
            await desk.StaffAuth(staff_env).staff(
                staff_request("/desk/api/overview", signed=token(kid="unknown"))
            )
    desk.fetch_keys.assert_awaited_once()


async def test_the_desk_fails_closed_without_access_settings(env):
    with pytest.raises(ApiError) as error:
        await desk.StaffAuth(env).staff(staff_request("/desk/api/overview"))
    assert error.value.code == "desk_not_configured"


async def pending_credit(db):
    return await store.create_approval(
        db,
        customer_id="cus_maya",
        order="A-1042",
        call_id="b" * 32,
        amount_cents=1000,
        reason="Bent rib",
    )


async def test_a_staff_decision_is_recorded_once_and_reaches_the_live_call(staff_env):
    request = await pending_credit(staff_env.DB)
    call = SimpleNamespace(approval_decided=AsyncMock(return_value=True))
    staff_env.CALLS = SimpleNamespace(getByName=Mock(return_value=call))
    path = f"/desk/api/approvals/{request['id']}"
    response = await desk.decide(
        staff_request(path, {"decision": "approve"}), staff_env, request["id"]
    )
    assert response.data["caller_told"] is True
    assert response.data["approval"]["status"] == "approved"
    assert "call_id" not in response.data["approval"]
    staff_env.CALLS.getByName.assert_called_once_with("b" * 32)
    call.approval_decided.assert_awaited_once_with(
        {
            "request_id": request["id"],
            "customer_id": "cus_maya",
            "order_id": "A-1042",
            "amount_cents": 1000,
            "status": "approved",
        }
    )
    assert staff_env.DB.rows("SELECT decided_by FROM approvals") == [
        {"decided_by": "ana@acme.test"}
    ]
    with pytest.raises(ApiError) as error:
        await desk.decide(staff_request(path, {"decision": "decline"}), staff_env, request["id"])
    assert error.value.code == "already_decided"


async def test_a_decision_stands_when_the_call_has_ended(staff_env):
    request = await pending_credit(staff_env.DB)
    call = SimpleNamespace(approval_decided=AsyncMock(side_effect=RuntimeError("gone")))
    staff_env.CALLS = SimpleNamespace(getByName=Mock(return_value=call))
    path = f"/desk/api/approvals/{request['id']}"
    response = await desk.decide(
        staff_request(path, {"decision": "decline"}), staff_env, request["id"]
    )
    assert response.data["caller_told"] is False
    assert response.data["approval"]["status"] == "declined"


async def test_desk_changes_require_same_origin_before_anything_else(staff_env):
    request = await pending_credit(staff_env.DB)
    path = f"/desk/api/approvals/{request['id']}"
    cross_site = staff_request(path, {"decision": "approve"}, origin="https://attacker.example")
    with pytest.raises(ApiError) as error:
        await desk.decide(cross_site, staff_env, request["id"])
    assert error.value.status == 403
    desk.verify_rs256.assert_not_awaited()
    assert staff_env.DB.rows("SELECT status FROM approvals") == [{"status": "pending"}]
