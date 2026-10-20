"""Native test doubles for the Workers boundary; media tests use the runtime."""

import json
import sqlite3
import sys
from contextlib import nullcontext
from email.message import Message
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


class Response:
    def __init__(self, data=None, status=200, headers=None, **kwargs):
        self.data, self.status, self.headers = data, status, headers or {}

    @classmethod
    def json(cls, data, **kwargs):
        return cls(data, **kwargs)


class RawResponse:
    """What the AI binding returns for a request made with returnRawResponse."""

    def __init__(self, data, status=200):
        self.data, self.status = data, status

    async def text(self):
        return json.dumps(self.data)


class DurableObject:
    def __init__(self, ctx, env):
        self.ctx, self.env = ctx, env


class Proxy:
    def __init__(self, task):
        self.task = task

    def destroy(self):
        pass


workers = ModuleType("workers")
workers.Response = Response
workers.DurableObject = DurableObject
workers.WorkerEntrypoint = DurableObject
workers.fetch = None
sys.modules["workers"] = workers
asgi = ModuleType("workers.asgi")
asgi.acquire_js_buffer = nullcontext
sys.modules["workers.asgi"] = asgi
ffi = ModuleType("pyodide.ffi")
ffi.create_proxy = Proxy
sys.modules["pyodide.ffi"] = ffi
js = ModuleType("js")
js.WebSocketPair = None
sys.modules["js"] = js


class Request:
    def __init__(
        self, path, body=None, *, cookie=None, origin="https://voice.example", method=None
    ):
        self.url = "https://voice.example" + path
        self.method = method or ("POST" if body is not None else "GET")
        self.headers = Message()
        self.headers["Origin"] = origin
        self.headers["Content-Type"] = "application/json"
        if cookie:
            self.headers["Cookie"] = cookie
        self.raw = json.dumps(body).encode() if body is not None else b""
        self.body = SimpleNamespace(getReader=lambda: Reader(self.raw))


class Reader:
    def __init__(self, raw):
        self.raw = raw

    async def read(self):
        if self.raw is None:
            return SimpleNamespace(done=True)
        raw, self.raw = self.raw, None
        return SimpleNamespace(done=False, value=raw)

    async def cancel(self):
        self.raw = None

    def releaseLock(self):
        pass


class D1:
    """SQLite behind D1's prepared-statement API, with the app's real schema."""

    def __init__(self, *, seeded=True):
        self.connection = sqlite3.connect(":memory:", isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        # D1 enforces foreign keys.
        self.connection.execute("PRAGMA foreign_keys = ON")
        for migration in sorted((Path(__file__).parents[2] / "migrations").glob("*.sql")):
            self.connection.executescript(migration.read_text())
        if seeded:
            self.connection.executescript((Path(__file__).parent / "seed.sql").read_text())

    def prepare(self, sql):
        return Statement(self, sql)

    async def batch(self, statements):
        self.connection.execute("BEGIN")
        try:
            results = [statement.execute() for statement in statements]
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        self.connection.execute("COMMIT")
        return results

    def rows(self, sql, *params):
        return [dict(row) for row in self.connection.execute(sql, params)]


class Statement:
    def __init__(self, db, sql, params=()):
        self.db, self.sql, self.params = db, sql, params

    def bind(self, *params):
        return Statement(self.db, self.sql, params)

    def execute(self):
        return {"results": self.db.rows(self.sql, *self.params), "success": True}

    async def all(self):
        return self.execute()

    async def first(self):
        rows = self.execute()["results"]
        return rows[0] if rows else None

    async def run(self):
        return self.execute()


class Storage:
    def __init__(self):
        self.values = {}
        self.alarm = None

    async def get(self, key):
        return self.values.get(key)

    async def put(self, key, value):
        self.values[key] = value

    async def setAlarm(self, value):
        self.alarm = value

    async def deleteAlarm(self):
        self.alarm = None

    async def deleteAll(self):
        self.values.clear()


@pytest.fixture
def env():
    return SimpleNamespace(
        OAUTH_CLIENT_ID="test-client",
        OAUTH_CLIENT_SECRET="test-client-secret",
        SESSION_SECRET="test-signing-secret-" * 3,
        REALTIME_APP_ID="test-app",
        REALTIME_AUTH_TOKEN="test-app-token",
        DB=D1(),
    )


@pytest.fixture
def ctx():
    return SimpleNamespace(storage=Storage(), waitUntil=lambda _: None)


def session_cookie(env, owner="a" * 32, customer="cus_maya", email="maya@example.com", name="Maya"):
    from server.auth_cloudflare import COOKIE, CloudflareAuth

    value = {"owner": owner, "customer": customer, "email": email, "name": name}
    return f"{COOKIE}={CloudflareAuth(env).signer('session').dumps(value)}"


@pytest.fixture
def owner_cookie(env):
    return session_cookie(env)
