"""The staff desk API, behind Cloudflare Access.

Access signs staff in at the edge, before a request reaches the Worker, and
attaches a signed token. The Worker still verifies that token: an edge
setting can be removed or misconfigured, and Preview URLs aren't covered by
the production Access application.
"""

import base64
import json
import time
from http.cookies import SimpleCookie
from typing import Literal

from pydantic import BaseModel, ConfigDict

from server.http import ApiError, check_origin, read_json, reply
from server.observability import exception_fields, log
from support import help_center, store

TOKEN_HEADER = "Cf-Access-Jwt-Assertion"
TOKEN_COOKIE = "CF_Authorization"
KEYS_SECONDS = 60 * 60
# Access rotates its keys, so an unknown key ID triggers a refresh, at most
# once a minute so forged tokens can't make every request fetch them.
KEY_REFRESH_SECONDS = 60
CLOCK_SKEW_SECONDS = 60
# Signing keys, cached per isolate.
_keys = {"team": None, "fetched": 0.0, "by_id": {}}


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    decision: Literal["approve", "decline"]


def _decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


async def verify_rs256(jwk: dict, signed: bytes, signature: bytes) -> bool:
    """Checks an RS256 signature with the Workers runtime's Web Crypto API."""
    from js import Object, crypto
    from pyodide.ffi import to_js

    def js(value):
        return to_js(value, dict_converter=Object.fromEntries)

    key = await crypto.subtle.importKey(
        "jwk",
        js({"kty": "RSA", "n": jwk["n"], "e": jwk["e"], "alg": "RS256", "ext": True}),
        js({"name": "RSASSA-PKCS1-v1_5", "hash": "SHA-256"}),
        False,
        js(["verify"]),
    )
    return bool(await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, js(signature), js(signed)))


async def fetch_keys(team: str) -> dict:
    from workers import fetch

    response = await fetch(f"{team}/cdn-cgi/access/certs")
    if response.status != 200:
        raise ApiError("staff_login_unavailable", 503)
    return {key["kid"]: key for key in (await response.json())["keys"]}


async def signing_key(team: str, key_id: str):
    age = time.time() - _keys["fetched"]
    unknown = key_id not in _keys["by_id"] and age > KEY_REFRESH_SECONDS
    if _keys["team"] != team or age > KEYS_SECONDS or unknown:
        _keys.update(team=team, fetched=time.time(), by_id=await fetch_keys(team))
    return _keys["by_id"].get(key_id)


class StaffAuth:
    def __init__(self, env):
        team = str(getattr(env, "ACCESS_TEAM_DOMAIN", "") or "").strip().rstrip("/")
        if team and not team.startswith("https://"):
            team = "https://" + team.removeprefix("http://")
        self.team = team
        self.audience = str(getattr(env, "ACCESS_AUD", "") or "").strip()

    def token(self, request):
        token = request.headers.get(TOKEN_HEADER)
        if not token:
            cookies = SimpleCookie()
            cookies.load(request.headers.get("Cookie", ""))
            token = cookies[TOKEN_COOKIE].value if TOKEN_COOKIE in cookies else None
        return token

    async def staff(self, request) -> str:
        """Returns the signed-in staff member's email, or raises ApiError."""
        if not self.team or not self.audience:
            raise ApiError("desk_not_configured", 503)
        token = self.token(request)
        try:
            header_part, payload_part, signature_part = token.split(".")
            header = json.loads(_decode(header_part))
            claims = json.loads(_decode(payload_part))
            if header.get("alg") != "RS256":
                raise ValueError("unexpected algorithm")
            key = await signing_key(self.team, header.get("kid"))
            signed = f"{header_part}.{payload_part}".encode()
            if not key or not await verify_rs256(key, signed, _decode(signature_part)):
                raise ValueError("bad signature")
        except ApiError:
            raise
        except Exception:
            raise ApiError("staff_login_required", 401) from None
        audience = claims.get("aud")
        audiences = audience if isinstance(audience, list) else [audience]
        now = time.time()
        if (
            claims.get("iss") != self.team
            or self.audience not in audiences
            or not isinstance(claims.get("exp"), (int, float))
            or claims["exp"] < now - CLOCK_SKEW_SECONDS
            or claims.get("nbf", 0) > now + CLOCK_SKEW_SECONDS
            or not isinstance(claims.get("email"), str)
        ):
            raise ApiError("staff_login_required", 401)
        return claims["email"]


async def overview(request, env):
    staff = await StaffAuth(env).staff(request)
    records = await store.desk_overview(env.DB)
    return reply(
        {"staff": staff, **records, "help_center": await help_center.status(env.AI_SEARCH)}
    )


async def decide(request, env, approval_id):
    check_origin(request)
    staff = await StaffAuth(env).staff(request)
    body = await read_json(request, Decision)
    decided = await store.decide_approval(
        env.DB, approval_id, approve=body.decision == "approve", staff=staff
    )
    if not decided:
        raise ApiError("already_decided", 409)
    told = False
    if decided["call_id"]:
        # The call's Durable Object is reachable only through this Worker's
        # binding, so the method needs no separate credential.
        try:
            call = env.CALLS.getByName(decided["call_id"])
            told = bool(
                await call.approval_decided(
                    {
                        "request_id": decided["id"],
                        "customer_id": decided["customer_id"],
                        "order_id": decided["order_id"],
                        "amount_cents": decided["amount_cents"],
                        "status": decided["status"],
                    }
                )
            )
        except Exception as exc:
            log.bind(call_id=decided["call_id"]).warning(
                "decision_delivery_failed", operation="approval", **exception_fields(exc)
            )
    log.bind(call_id=decided["call_id"]).info(
        "approval_decided", operation="approval", status=decided["status"]
    )
    public = {key: value for key, value in decided.items() if key not in {"call_id"}}
    return reply({"approval": public, "caller_told": told})


async def publish_help(request, env):
    check_origin(request)
    await StaffAuth(env).staff(request)
    return reply({"help_center": await help_center.publish(env.AI_SEARCH)})


async def reset_demo(request, env):
    check_origin(request)
    await StaffAuth(env).staff(request)
    await store.reset_demo(env.DB)
    return reply({"reset": True})
