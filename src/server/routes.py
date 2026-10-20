"""Declarative URL dispatch with native Workers requests and responses."""

from urllib.parse import urlparse

from werkzeug.exceptions import MethodNotAllowed, NotFound
from werkzeug.routing import BaseConverter, Map, Rule

from agent.config import ASSISTANT_NAME, MAX_CALL_SECONDS
from server import desk
from server.auth import browser_auth
from server.http import ApiError, check_origin, reply
from server.observability import exception_fields, log
from support import store


class CallIdConverter(BaseConverter):
    regex = r"[a-f0-9]{32}"


class ApprovalIdConverter(BaseConverter):
    regex = r"R-[0-9]{4}"


async def session(request, env):
    identity = await browser_auth(env).session(request)
    setup = [] if identity["configured"] else ["sign_in"]
    if not all(getattr(env, key, "") for key in ("REALTIME_APP_ID", "REALTIME_AUTH_TOKEN")):
        setup.append("realtime")
    signed_in = identity["customer"]
    customer = None
    try:
        if signed_in:
            # The first visit creates the account. After a demo reset, the next one recreates it.
            customer = await store.ensure_customer(
                env.DB, signed_in["id"], name=signed_in["name"], email=signed_in["email"]
            )
        else:
            await store.check(env.DB)
    except Exception as exc:
        # Usually the D1 migrations haven't been applied yet.
        log.warning("database_unavailable", operation="session", **exception_fields(exc))
        setup.append("database")
    return reply(
        {
            "configured": not setup,
            "setup_required": setup,
            "authenticated": customer is not None,
            "customer": customer and {key: customer[key] for key in ("id", "name", "email")},
            "assistant": ASSISTANT_NAME,
            "max_call_seconds": MAX_CALL_SECONDS,
        }
    )


async def login(request, env):
    return await browser_auth(env).login(request)


async def callback(request, env):
    return await browser_auth(env).callback(request)


async def logout(request, env):
    return await browser_auth(env).logout(request)


async def account(request, env):
    caller = await browser_auth(env).identity(request)
    customer = await store.get_customer(env.DB, caller.customer_id)
    if not customer:
        raise ApiError("login_required", 401)
    return reply(
        {
            "customer": customer,
            "orders": await store.list_orders(env.DB, caller.customer_id),
            "tickets": await store.list_tickets(env.DB, caller.customer_id),
            "approvals": await store.list_approvals(env.DB, caller.customer_id),
        }
    )


async def call(request, env, call_id, action):
    caller = await browser_auth(env).identity(request)
    if request.method != "GET":
        check_origin(request)
    if action == "create":
        limited = await env.CALL_LIMITER.limit({"key": "voice-call:" + caller.owner})
        if not limited.success:
            raise ApiError("too_many_calls", 429)
    return await env.CALLS.getByName(call_id).fetch(request)


async def media(request, env, call_id, role):
    # The Durable Object verifies the adapter token independently of browser auth.
    return await env.CALLS.getByName(call_id).fetch(request)


async def assets(request, env):
    return await env.ASSETS.fetch(request)


_map = Map(
    [
        Rule("/api/session", methods=["GET"], endpoint=session),
        Rule("/auth/login", methods=["GET"], endpoint=login),
        Rule("/auth/callback", methods=["GET"], endpoint=callback),
        Rule("/api/logout", methods=["POST"], endpoint=logout),
        Rule("/api/account", methods=["GET"], endpoint=account),
        Rule(
            "/api/calls/<call_id:call_id>/<any(events,status,warm):action>",
            methods=["GET"],
            endpoint=call,
        ),
        Rule(
            "/api/calls/<call_id:call_id>/<any(create,publish,attach,subscribe,renegotiate,finish):action>",
            methods=["POST"],
            endpoint=call,
        ),
        Rule("/media/<call_id:call_id>/<any(input,output):role>", methods=["GET"], endpoint=media),
        # Cloudflare Access protects /desk at the edge; each handler verifies its token.
        Rule("/desk/api/overview", methods=["GET"], endpoint=desk.overview),
        Rule(
            "/desk/api/approvals/<approval_id:approval_id>",
            methods=["POST"],
            endpoint=desk.decide,
        ),
        Rule("/desk/api/help-center", methods=["POST"], endpoint=desk.publish_help),
        Rule("/desk/api/demo-data", methods=["POST"], endpoint=desk.reset_demo),
    ],
    converters={"call_id": CallIdConverter, "approval_id": ApprovalIdConverter},
    merge_slashes=False,
)
# WebSocket upgrades must reach the handler without routing redirects.
_router = _map.bind("worker.invalid")


def match(request):
    path = urlparse(request.url).path
    try:
        return _router.match(path_info=path, method=request.method)
    except MethodNotAllowed:
        raise ApiError("method_not_allowed", 405) from None
    except NotFound:
        if path.startswith(("/api/", "/auth/", "/media/", "/desk/api/")):
            raise ApiError("not_found", 404) from None
        return assets, {}
