import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from agent.config import CallSettings
from agent.engine import VoiceEngine


async def test_wait_until_resolves_for_cancelled_and_failed_tasks():
    handed = []
    ctx = SimpleNamespace(waitUntil=handed.append)
    engine = VoiceEngine(
        ctx,
        None,
        CallSettings(),
        {"id": "cus_test", "name": "Test Customer"},
        Mock(),
        lambda: True,
        call_id="b",
        owner_id="a",
    )
    engine.active = True

    async def broken():
        raise RuntimeError("provider failed")

    sleeping = engine.background(asyncio.sleep(10), "test-sleep")
    failing = engine.background(broken(), "test-broken")
    await asyncio.sleep(0)
    sleeping.cancel()
    await asyncio.gather(sleeping, failing, return_exceptions=True)
    # The test double for create_proxy keeps the wrapped future as `task`.
    assert [proxy.task.result() for proxy in handed] == [None, None]
    # Cancellation is a normal exit; only the failure ends the call.
    engine.on_stop.assert_called_once_with("test-broken")


async def test_announcements_wait_for_a_pause_then_let_the_model_speak(monkeypatch):
    engine = VoiceEngine(
        None,
        None,
        CallSettings(),
        {"id": "cus_test", "name": "Test Customer"},
        Mock(),
        None,
        call_id="b",
        owner_id="a",
    )
    engine.active = engine.pipeline_ready = True
    engine.control = SimpleNamespace(llm_pending=False, tts_pending=True)
    engine.worker = SimpleNamespace(queue_frame=AsyncMock())
    announcing = asyncio.create_task(engine.announce("Credit approved."))
    await asyncio.sleep(0.3)
    engine.worker.queue_frame.assert_not_awaited()
    engine.control.tts_pending = False
    await asyncio.wait_for(announcing, 2)
    frame = engine.worker.queue_frame.call_args.args[0]
    assert frame.messages == [
        {"role": "user", "content": "<runtime_context>\nCredit approved.\n</runtime_context>"}
    ]
    assert frame.run_llm
