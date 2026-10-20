import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from conftest import Request, session_cookie
from pipecat.frames.frames import TTSSpeakFrame

import agent.engine as engine_module
from agent.config import CallSettings, greeting
from agent.engine import VoiceEngine
from server.call import VoiceCall
from server.http import ApiError

CALL = "b" * 32
BASE = f"/api/calls/{CALL}"
OFFER = {"mid": "0", "sessionDescription": {"type": "offer", "sdp": "offer"}}
ANSWER = {"sessionDescription": {"type": "answer", "sdp": "answer"}}
ENDPOINTS = {role: f"wss://voice.example/media/{CALL}/{role}" for role in ("input", "output")}


@pytest.fixture
def prepared(monkeypatch):
    """Replaces the real pipeline and speech services with a recorded prepare."""
    prepare = AsyncMock(return_value=ENDPOINTS)
    monkeypatch.setattr(VoiceEngine, "prepare", prepare)
    return prepare


@pytest.fixture
async def active_call(ctx, env, owner_cookie):
    call = VoiceCall(ctx, env)
    await call.fetch(Request(BASE + "/create", {}, cookie=owner_cookie))
    call.engine = VoiceEngine(
        ctx,
        env,
        CallSettings(),
        call.state["customer"],
        call.request_finish,
        lambda: True,
        call_id=CALL,
        owner_id=call.state["owner"],
    )
    call.engine.active = True
    call.state["phase"] = "active"
    sfu = AsyncMock()
    sfu.cleanup.return_value = True
    call.sfu = lambda: sfu
    return call


@pytest.fixture
def greeter(active_call, monkeypatch):
    """The call's engine with its audio connection and speech replaced."""
    engine = active_call.engine
    engine.control = SimpleNamespace(generation=0)
    engine.tts = SimpleNamespace(queue_frame=AsyncMock())
    engine.audio_ready = True
    monkeypatch.setattr(engine, "_ready", lambda: engine.audio_ready)
    return engine


async def test_the_agent_says_hello_once_the_caller_can_hear_it(greeter):
    greeter.audio_ready = False
    task = asyncio.create_task(greeter.greet())
    await asyncio.sleep(0.15)
    greeter.tts.queue_frame.assert_not_awaited()
    greeter.audio_ready = True
    await asyncio.wait_for(task, 1)
    await greeter.greet()
    greeter.tts.queue_frame.assert_awaited_once()
    speech = greeter.tts.queue_frame.await_args.args[0]
    assert isinstance(speech, TTSSpeakFrame)
    assert speech.text == "Hi Maya Chen, this is Matt from Acme support. How can I help?"
    # The greeting enters the history like a reply, so the model knows it was said.
    assert speech.append_to_context
    assert speech.workers_generation == 0
    shown = [event for event in greeter.client_events if event["type"] == "assistant_response"]
    assert [(event["text"], event["generation"]) for event in shown] == [(speech.text, 0)]


async def test_a_caller_without_a_first_name_still_gets_a_greeting():
    assert greeting("") == "Hi, this is Matt from Acme support. How can I help?"


@pytest.mark.parametrize("caller", ["speaking", "spoke"])
async def test_the_agent_does_not_talk_over_a_caller_who_spoke_first(greeter, caller):
    if caller == "speaking":
        greeter.user_speaking = True
    else:
        greeter.control.generation = 1
    await greeter.greet()
    greeter.tts.queue_frame.assert_not_awaited()
    assert not greeter.client_events


async def test_the_greeting_gives_up_without_an_audio_connection(greeter, monkeypatch):
    monkeypatch.setattr(engine_module, "GREETING_WAIT_SECONDS", 0.05)
    greeter.audio_ready = False
    await asyncio.wait_for(greeter.greet(), 1)
    greeter.tts.queue_frame.assert_not_awaited()


async def test_the_greeting_starts_when_the_browser_can_receive_audio(
    active_call, owner_cookie, monkeypatch
):
    call = active_call
    greet = AsyncMock()
    monkeypatch.setattr(VoiceEngine, "greet", greet)
    call.state["responses"]["subscribe"] = {"fingerprint": "", "response": {}}
    call.sfu().renegotiate.return_value = {}
    response = await call.fetch(Request(BASE + "/renegotiate", ANSWER, cookie=owner_cookie))
    assert response.status == 200
    await asyncio.sleep(0)
    greet.assert_awaited_once()


async def test_event_poll_waits_for_changes_and_replays_from_the_cursor(active_call, owner_cookie):
    call = active_call
    async with asyncio.TaskGroup() as tasks:
        for after, text in [(0, "hello"), (1, "again")]:
            poll = tasks.create_task(
                call.fetch(Request(f"{BASE}/events?after={after}", cookie=owner_cookie))
            )
            await asyncio.sleep(0)
            assert not poll.done()
            call.engine.notify({"type": "user_transcript", "text": text})
            response = await asyncio.wait_for(poll, 1)
            assert response.status == 200
            assert [event["text"] for event in response.data["events"]] == [text]
    replay = await asyncio.wait_for(
        call.fetch(Request(BASE + "/events?after=0", cookie=owner_cookie)), 1
    )
    assert [event["text"] for event in replay.data["events"]] == ["hello", "again"]


async def test_event_poll_does_not_block_stop(active_call, owner_cookie):
    call = active_call
    async with asyncio.TaskGroup() as tasks:
        poll = tasks.create_task(call.fetch(Request(BASE + "/events", cookie=owner_cookie)))
        await asyncio.sleep(0)
        assert not poll.done()
        stopped = await asyncio.wait_for(
            call.fetch(Request(BASE + "/finish", {}, cookie=owner_cookie)), 1
        )
        assert stopped.data["cleanup_complete"]
        response = await asyncio.wait_for(poll, 1)
        assert response.data["phase"] in {"ending", "ended"}
        assert response.data["events"][-1]["type"] == "closing"


async def test_event_poll_timeout_returns_current_state(active_call, owner_cookie, monkeypatch):
    monkeypatch.setattr("server.call.EVENT_WAIT_SECONDS", 0.01)
    response = await asyncio.wait_for(
        active_call.fetch(Request(BASE + "/events", cookie=owner_cookie)), 1
    )
    assert response.status == 200
    assert response.data["phase"] == "active"
    assert response.data["events"] == []


async def test_every_call_operation_requires_its_owner(ctx, env, owner_cookie):
    call = VoiceCall(ctx, env)
    assert (await call.fetch(Request(BASE + "/create", {}, cookie=owner_cookie))).status == 201
    intruder = session_cookie(env, owner="c" * 32)
    for action in ("status", "events", "publish", "attach", "subscribe", "renegotiate", "finish"):
        request = Request(
            BASE + "/" + action, None if action in ("status", "events") else {}, cookie=intruder
        )
        assert (await call.fetch(request)).status == 404
    assert not call.stopping


async def test_warming_needs_a_login_and_leaves_no_call_behind(ctx, env, owner_cookie):
    call = VoiceCall(ctx, env)
    assert (await call.fetch(Request(BASE + "/warm"))).status == 401
    warmed = await call.fetch(Request(BASE + "/warm", cookie=owner_cookie))
    assert warmed.status == 200
    assert ctx.storage.values == {}
    assert ctx.storage.alarm is None
    assert (await call.fetch(Request(BASE + "/create", {}, cookie=owner_cookie))).status == 201


async def test_publish_retry_is_idempotent_and_conflicting_offer_is_rejected(
    ctx, env, owner_cookie, prepared
):
    call = VoiceCall(ctx, env)
    await call.fetch(Request(BASE + "/create", {}, cookie=owner_cookie))
    sfu = AsyncMock()
    sfu.publish.return_value = ANSWER
    call.sfu = lambda: sfu
    body = json.loads(json.dumps(OFFER))
    first = await call.fetch(Request(BASE + "/publish", body, cookie=owner_cookie))
    second = await call.fetch(Request(BASE + "/publish", body, cookie=owner_cookie))
    assert first.data == second.data
    sfu.publish.assert_awaited_once()
    prepared.assert_awaited_once()
    body["sessionDescription"]["sdp"] = "different offer"
    assert (await call.fetch(Request(BASE + "/publish", body, cookie=owner_cookie))).status == 409


async def test_publish_prepares_the_agent_while_the_media_session_starts(
    ctx, env, owner_cookie, monkeypatch
):
    call = VoiceCall(ctx, env)
    await call.fetch(Request(BASE + "/create", {}, cookie=owner_cookie))
    started = set()
    both = asyncio.Event()

    async def overlapping(name, value):
        started.add(name)
        if len(started) == 2:
            both.set()
        # Run one after the other, these would time out and fail the publish.
        await asyncio.wait_for(both.wait(), 1)
        return value

    async def prepare(self, origin, call_id):
        return await overlapping("agent", ENDPOINTS)

    async def publish(*_):
        return await overlapping("media", ANSWER)

    monkeypatch.setattr(VoiceEngine, "prepare", prepare)
    sfu = AsyncMock()
    sfu.publish.side_effect = publish
    call.sfu = lambda: sfu
    published = await call.fetch(Request(BASE + "/publish", OFFER, cookie=owner_cookie))
    assert published.status == 200
    assert published.data["sessionDescription"] == ANSWER["sessionDescription"]
    assert "agent_prepare" in [row["operation"] for row in published.data["timings"]]
    assert call.state["phase"] == "published"
    attached = await call.fetch(Request(BASE + "/attach", {}, cookie=owner_cookie))
    assert attached.data["attached"]
    sfu.attach.assert_awaited_once_with(CALL, ENDPOINTS)
    assert call.endpoints is None


async def test_failed_publish_waits_for_the_agent_then_cleans_up(
    ctx, env, owner_cookie, monkeypatch
):
    call = VoiceCall(ctx, env)
    await call.fetch(Request(BASE + "/create", {}, cookie=owner_cookie))
    prepared = asyncio.Event()

    async def prepare(self, origin, call_id):
        await asyncio.sleep(0.01)
        prepared.set()
        return ENDPOINTS

    async def finish(self, reason):
        assert prepared.is_set()
        self.cleanup_complete = True

    monkeypatch.setattr(VoiceEngine, "prepare", prepare)
    monkeypatch.setattr(VoiceEngine, "finish", finish)
    sfu = AsyncMock()
    sfu.publish.side_effect = ApiError("sfu_unavailable", 502)
    sfu.cleanup.return_value = True
    call.sfu = lambda: sfu
    response = await call.fetch(Request(BASE + "/publish", OFFER, cookie=owner_cookie))
    assert response.status == 502
    assert call.state["phase"] == "ended"
    assert call.state["finish_reason"] == "setup_failed"
    sfu.cleanup.assert_awaited_once()


async def test_a_restart_after_publish_ends_the_call(ctx, env, owner_cookie):
    call = VoiceCall(ctx, env)
    await call.fetch(Request(BASE + "/create", {}, cookie=owner_cookie))
    call.state["phase"] = "published"
    await call.save()
    restarted = VoiceCall(ctx, env)
    sfu = AsyncMock()
    sfu.cleanup.return_value = True
    restarted.sfu = lambda: sfu
    response = await restarted.fetch(Request(BASE + "/attach", {}, cookie=owner_cookie))
    assert response.status == 409
    assert restarted.state["finish_reason"] == "session_restarted"
    sfu.attach.assert_not_awaited()


async def test_cleanup_retries_owned_resources_after_a_restart(ctx, env, owner_cookie):
    call = VoiceCall(ctx, env)
    await call.fetch(Request(BASE + "/create", {}, cookie=owner_cookie))
    call.state["resources"]["adapters"] = ["owned"]
    sfu = AsyncMock()
    sfu.cleanup.return_value = False
    call.sfu = lambda: sfu
    result = await call.fetch(Request(BASE + "/finish", {}, cookie=owner_cookie))
    assert result.data["phase"] == "ending"
    assert ctx.storage.alarm is not None
    assert json.loads(ctx.storage.values["call"])["resources"]["adapters"] == ["owned"]
    restarted = VoiceCall(ctx, env)
    sfu.cleanup.return_value = True
    restarted.sfu = lambda: sfu
    await restarted.alarm()
    assert restarted.state["phase"] == "ended"
    result = await restarted.fetch(Request(BASE + "/finish", {}, cookie=owner_cookie))
    assert result.data["cleanup_complete"]
    assert sfu.cleanup.await_count == 2


async def test_owner_stop_invalidates_setup_while_waiting_for_the_lock(ctx, env, owner_cookie):
    call = VoiceCall(ctx, env)
    await call.fetch(Request(BASE + "/create", {}, cookie=owner_cookie))
    sfu = AsyncMock()
    sfu.cleanup.return_value = True
    call.sfu = lambda: sfu
    await call.lock.acquire()
    stop = asyncio.create_task(call.fetch(Request(BASE + "/finish", {}, cookie=owner_cookie)))
    await asyncio.sleep(0)
    assert call.stopping
    call.lock.release()
    assert (await stop).data["cleanup_complete"]


async def test_a_staff_decision_is_announced_only_on_the_customers_live_call(active_call):
    call = active_call
    call.engine.announce = AsyncMock()
    decision = {
        "request_id": "R-1234",
        "customer_id": "cus_maya",
        "order_id": "A-1042",
        "amount_cents": 1000,
        "status": "approved",
    }
    assert not await call.approval_decided({**decision, "customer_id": "cus_sam"})
    assert await call.approval_decided(decision)
    await asyncio.gather(*call.engine.tasks)
    notices = [event for event in call.engine.client_events if event["type"] == "approval_decided"]
    assert len(notices) == 1 and notices[0]["request_id"] == "R-1234"
    call.engine.announce.assert_awaited_once_with(
        "A member of the Acme support team approved credit request R-1234 for $10.00 on order "
        "A-1042. The credit is on the customer's account now."
    )
    call.engine.active = False
    assert not await call.approval_decided(decision)
