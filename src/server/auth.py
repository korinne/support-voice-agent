"""Browser authentication boundary; SFU adapter credentials live in transport.auth."""

from dataclasses import dataclass
from typing import Protocol

from workers import Response


@dataclass(frozen=True)
class Caller:
    # Owns calls. Each sign-in gets its own, so a call ID alone grants nothing.
    owner: str
    # The customer whose account the agent may read and change.
    customer_id: str


class BrowserAuth(Protocol):
    """Providers return the signed-in caller or raise ApiError for rejected requests.

    Identity verification is async so providers can fetch and cache JWT keys.
    Call ownership and same-origin checks remain outside the provider.
    """

    async def identity(self, request) -> Caller: ...

    async def session(self, request) -> dict:
        """Returns {"configured": bool, "customer": {"id", "name", "email"} or None}."""
        ...

    async def login(self, request) -> Response:
        """Starts a sign-in. Redirect providers send the browser to the identity provider."""
        ...

    async def callback(self, request) -> Response:
        """Finishes a redirect sign-in and sets the session."""
        ...

    async def logout(self, request) -> Response: ...


def browser_auth(env) -> BrowserAuth:
    """Replace this factory to sign customers in with your own identity provider."""
    from server.auth_cloudflare import CloudflareAuth

    return CloudflareAuth(env)
