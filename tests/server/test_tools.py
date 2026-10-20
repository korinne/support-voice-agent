import asyncio
import copy
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from conftest import D1, RawResponse
from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.frames.frames import InterruptionFrame, LLMContextFrame, TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.turns.user_turn_strategies import ExternalUserTurnStrategies
from pipecat.workers.runner import WorkerRunner

import services.llm as llm_module
from agent.config import LLM_MODEL, system_prompt
from agent.fillers import HOLD_ON, PHRASES, speak_fillers
from agent.resources import AgentResources
from agent.speech_history import SpeechHistory
from agent.tools import account_snapshot, build_tools
from services.llm import WorkersAIAdapter, WorkersAILLM
from services.models import get_model

logger.disable("pipecat")


class Model:
    def __init__(
        self,
        *,
        tool_name="get_account",
        arguments=None,
        rounds=1,
        text="Your umbrella was delivered on Monday.",
    ):
        self.requests = []
        self.options = []
        self.tool_name = tool_name
        self.arguments = arguments if arguments is not None else {}
        self.rounds = rounds
        self.text = text

    async def run(self, model, parameters, options):
        self.requests.append(copy.deepcopy(parameters))
        self.options.append(copy.deepcopy(options))
        if len(self.requests) <= self.rounds:
            arguments = (
                self.arguments[len(self.requests) - 1]
                if isinstance(self.arguments, list)
                else self.arguments
            )
            tool_name = (
                self.tool_name[len(self.requests) - 1]
                if isinstance(self.tool_name, list)
                else self.tool_name
            )
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"lookup-{len(self.requests)}",
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": json.dumps(arguments),
                        },
                    }
                ],
            }
            finish_reason = "tool_calls"
        else:
            message = {"role": "assistant", "content": self.text}
            finish_reason = "stop"
        return RawResponse(
            {"choices": [{"index": 0, "finish_reason": finish_reason, "message": message}]}
        )


class Speech(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.replies = []
        self.frames = []
        self.received = asyncio.Event()

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSSpeakFrame):
            self.replies.append(frame.text)
            self.frames.append(frame)
            self.received.set()
        await self.push_frame(frame, direction)


class Background:
    """Collects a call's detached work so a test can wait for it."""

    def __init__(self):
        self.tasks = []

    def __call__(self, coroutine, name):
        self.tasks.append(asyncio.ensure_future(coroutine))

    async def wait(self):
        await asyncio.gather(*self.tasks)


def agent_resources(notify=None, *, customer_id="cus_maya", db=None, background=None):
    return AgentResources(
        env=SimpleNamespace(DB=db or D1(), AI=None, AI_SEARCH=None),
        call_id="test-call",
        owner_id="test-owner",
        customer_id=customer_id,
        notify=notify or (lambda event: None),
        background=background or Background(),
    )


def conversation(*said):
    return LLMContext(messages=[{"role": "user", "content": text} for text in said or ["Hi."]])


async def invoke(tools, name, arguments, resources, context=None):
    """Runs one tool handler the way Pipecat does and returns its result."""
    results = []

    async def callback(result):
        results.append(result)

    tool = next(tool for tool in tools if tool.name == name)
    await tool.handler(
        SimpleNamespace(
            arguments=arguments,
            tool_call_id=f"{name}-call",
            result_callback=callback,
            app_resources=resources,
            context=context or conversation(),
        )
    )
    return results[0]


async def pipeline(
    tools,
    *,
    model=None,
    messages=None,
    adapter=None,
    utc_offset_minutes=0,
    fillers=False,
    resources=None,
):
    model = model or Model()
    summary = {
        "requests": [],
        "responses": [],
        "errors": [],
        "cancelled": 0,
        "stale_dropped": 0,
        "interruptions": 0,
    }
    control = SimpleNamespace(
        generation=0,
        llm_pending=False,
        tts_pending=False,
    )
    llm = WorkersAILLM(
        SimpleNamespace(AI=model),
        summary,
        control,
        model=adapter or get_model(LLM_MODEL),
        speech_history=SpeechHistory(),
        utc_offset_minutes=utc_offset_minutes,
    )
    messages = messages or [{"role": "user", "content": "Where is my umbrella?"}]
    if not any(message["role"] == "system" for message in messages):
        messages = [{"role": "system", "content": "You are a voice assistant."}, *messages]
    context = LLMContext(messages=messages, tools=tools)
    pair = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            user_turn_strategies=ExternalUserTurnStrategies(),
            vad_analyzer=None,
            empty_user_turn=None,
        ),
    )
    speech = Speech()
    previews = []
    if fillers:
        speak_fillers(llm, speech, control)

    @llm.event_handler("on_response_text")
    async def on_response_text(_service, text, generation):
        previews.append(
            {"text": text, "generation": generation, "speech_received": bool(speech.replies)}
        )

    worker = PipelineWorker(
        Pipeline([pair.user(), llm, speech, pair.assistant()]),
        params=PipelineParams(),
        app_resources=resources or agent_resources(),
        enable_rtvi=False,
        idle_timeout_secs=None,
    )
    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False, force_gc=False)
    started = asyncio.Event()

    @worker.event_handler("on_pipeline_started")
    async def on_started(*_):
        started.set()

    await runner.add_workers(worker)
    task = asyncio.create_task(runner.run())
    await asyncio.wait_for(started.wait(), 5)
    await worker.queue_frame(LLMContextFrame(context))
    return SimpleNamespace(
        model=model,
        context=context,
        speech=speech,
        previews=previews,
        worker=worker,
        runner=runner,
        task=task,
        control=control,
        summary=summary,
    )


async def close(run):
    await run.runner.cancel(reason="test_finished")
    await asyncio.wait_for(run.task, 5)


async def test_reply_preview_and_tts_share_plain_text_before_context_is_updated():
    run = await pipeline(
        [],
        model=Model(
            rounds=0,
            text="**Muse** has 100000 users. See [the docs](https://example.com/).",
        ),
    )
    try:
        await asyncio.wait_for(run.speech.received.wait(), 5)
        expected = "Muse has 100000 users. See the docs."
        assert run.previews == [{"text": expected, "generation": 0, "speech_received": False}]
        assert run.speech.replies == [expected]
        assert not any(message.get("role") == "assistant" for message in run.context.get_messages())
    finally:
        await close(run)


@pytest.mark.parametrize(
    "model_id",
    [
        "@cf/google/gemma-4-26b-a4b-it",
        "@cf/deepseek-ai/deepseek-v4-flash-0731",
    ],
)
async def test_native_service_uses_pipecat_tool_results_to_resume_inference(model_id):
    class BlipModel(Model):
        failed = False

        async def run(self, model, parameters, options):
            if len(self.requests) == 1 and not self.failed:
                self.failed = True
                raise ConnectionError("Temporary model connection failure")
            return await super().run(model, parameters, options)

    events = []
    adapter = get_model(model_id)
    run = await pipeline(build_tools(events.append), model=BlipModel(), adapter=adapter)
    try:
        await asyncio.wait_for(run.speech.received.wait(), 5)
        assert len(run.model.requests) == 2
        assert run.model.failed
        affinity = run.model.options[0]["extraHeaders"]["x-session-affinity"]
        assert affinity
        assert run.model.options[1]["extraHeaders"]["x-session-affinity"] == affinity
        assert "tools" in run.model.requests[0]
        assert "tools" in run.model.requests[1]
        messages = run.model.requests[1]["messages"]
        call = next(message for message in messages if message.get("tool_calls"))
        assert call["content"] == ""
        result = next(message for message in messages if message.get("role") == "tool")
        assert call["tool_calls"][0]["id"] == result["tool_call_id"]
        assert json.loads(result["content"])["store_credit"] == "$0.00"
        assert [event["type"] for event in events] == ["tool_started", "tool_finished"]
        assert events[-1]["summary"] == "2 orders, $0.00 store credit"
        assert run.speech.replies == ["Your umbrella was delivered on Monday."]
        assert not run.summary["errors"]
    finally:
        await close(run)


async def test_interruption_cancels_a_pending_tool_and_drops_its_reply():
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def long_tool(params):
        assert params.app_resources.owner_id == "test-owner"
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    run = await pipeline(
        [
            FunctionSchema(
                name="get_account",
                description="Read the account",
                properties={},
                required=[],
                handler=long_tool,
            )
        ]
    )
    try:
        await asyncio.wait_for(started.wait(), 5)
        await run.worker.queue_frame(InterruptionFrame())
        await asyncio.wait_for(cancelled.wait(), 5)
        await asyncio.sleep(0.05)
        assert not run.speech.replies
        assert len(run.model.requests) == 1
        assert run.control.generation == 1
    finally:
        await close(run)


async def test_lookups_queue_one_filler_that_stays_out_of_the_conversation():
    async def search(params):
        await params.result_callback({"status": "found", "results": []})

    tool = FunctionSchema(
        name="search_help_center", description="Search", properties={}, required=[], handler=search
    )
    model = Model(tool_name="search_help_center", arguments={}, rounds=2)
    run = await pipeline([tool], model=model, fillers=True)
    try:
        async with asyncio.timeout(5):
            while len(run.speech.frames) < 2:
                await asyncio.sleep(0.01)
        filler, reply = run.speech.frames
        assert filler.text in PHRASES["search_help_center"]
        assert not filler.append_to_context
        assert filler.workers_generation == 0
        assert reply.text == "Your umbrella was delivered on Monday."
        assert reply.append_to_context
        # Two lookup rounds in one turn share a single filler.
        assert len(run.model.requests) == 3
    finally:
        await close(run)


async def test_a_slow_reply_gets_a_holding_line_and_one_past_the_limit_a_fallback(monkeypatch):
    monkeypatch.setattr(llm_module, "HOLD_ON_SECONDS", 0.05)
    monkeypatch.setattr(llm_module, "LLM_TIMEOUT_SECONDS", 0.5)
    delays = [0.2, 2]

    class SlowModel(Model):
        async def run(self, model, parameters, options):
            await asyncio.sleep(delays.pop(0))
            return await super().run(model, parameters, options)

    run = await pipeline([], model=SlowModel(rounds=0), fillers=True)
    try:
        async with asyncio.timeout(5):
            while len(run.speech.frames) < 2:
                await asyncio.sleep(0.01)
        hold_on, reply = run.speech.frames
        assert hold_on.text in HOLD_ON
        assert not hold_on.append_to_context
        assert reply.text == "Your umbrella was delivered on Monday."
        run.speech.received.clear()
        run.context.add_message({"role": "user", "content": "And my sneakers?"})
        await run.worker.queue_frame(InterruptionFrame())
        await run.worker.queue_frame(LLMContextFrame(run.context))
        async with asyncio.timeout(5):
            while len(run.speech.frames) < 4:
                await asyncio.sleep(0.01)
        assert run.speech.frames[2].text in HOLD_ON
        assert run.speech.replies[-1] == (
            "Sorry, I lost my train of thought. Could you say that again?"
        )
    finally:
        await close(run)


async def test_help_search_retry_and_ticket_preserve_history_with_tool_headroom(monkeypatch):
    excerpt = {"article": "Shipping and tracking", "excerpt": "We trace a package after 3 days."}
    search = AsyncMock(side_effect=[[], [excerpt]])
    monkeypatch.setattr("agent.tools.help_center.search", search)
    route = {"team": "shipping", "team_confidence": 0.91, "urgency": 1.2, "wants_person": 0.1}
    triage = AsyncMock(return_value=route)
    monkeypatch.setattr("agent.tools.triage", triage)
    events = []
    resources = agent_resources(events.append, customer_id="cus_sam")
    history = [
        {"role": "user", "content": "Hi, this is Sam."},
        {"role": "assistant", "content": "Hi Sam, how can I help?"},
        {"role": "user", "content": "My sneakers have been stuck in transit."},
    ]
    ticket = {"summary": "Sneakers stuck", "details": "No scan in 5 days.", "order_id": "a 1057"}
    run = await pipeline(
        build_tools(events.append),
        model=Model(
            tool_name=["search_help_center", "search_help_center", "open_ticket"],
            arguments=[{"query": "stuck package"}, {"query": "package trace"}, ticket],
            rounds=3,
        ),
        messages=copy.deepcopy(history),
        resources=resources,
    )
    try:
        await asyncio.wait_for(run.speech.received.wait(), 5)
        await resources.background.wait()
        assert len(run.model.requests) == 4
        assert "tools" in run.model.requests[1]
        assert run.model.requests[3].get("tool_choice") != "none"
        messages = run.model.requests[3]["messages"]
        assert messages[1:4] == history
        calls = [call for message in messages for call in message.get("tool_calls", [])]
        results = [message for message in messages if message.get("role") == "tool"]
        assert [result["tool_call_id"] for result in results] == [call["id"] for call in calls]
        outcomes = [json.loads(result["content"]) for result in results]
        assert [outcome["status"] for outcome in outcomes] == ["empty", "found", "opened"]
        ticket_id = outcomes[-1]["ticket_id"]
        # Triage runs detached, so its update can land before or after the tool's own.
        updates = [event for event in events if event["type"] != "ticket_triaged"]
        assert [event["type"] for event in updates] == [
            "tool_started",
            "tool_empty",
            "tool_started",
            "tool_finished",
            "tool_started",
            "tool_finished",
        ]
        assert updates[3]["sources"] == [{"title": "Shipping and tracking"}]
        assert updates[5]["summary"] == f"Opened ticket {ticket_id}"
        assert {"type": "ticket_triaged", "ticket_id": ticket_id, **route} in events
        assert resources.env.DB.rows(
            "SELECT customer_id, order_id, call_id, team FROM tickets"
        ) == [
            {
                "customer_id": "cus_sam",
                "order_id": "A-1057",
                "call_id": "test-call",
                "team": "shipping",
            }
        ]
        assert [call.args[1] for call in search.call_args_list] == [
            "stuck package",
            "package trace",
        ]
        assert triage.call_args.args[2] == {
            "id": "default",
            "metadata": {"customer_id": "cus_sam", "call_id": "test-call"},
        }
        assert not run.summary["errors"]
    finally:
        await close(run)


async def test_parallel_calls_share_the_budget_and_a_new_user_turn_resets_it():
    class ParallelModel(Model):
        async def run(self, model, parameters, options):
            self.requests.append(copy.deepcopy(parameters))
            # A model ignoring tool_choice must still obey the execution limit.
            message = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": f"batch-{len(self.requests)}-{index}",
                        "type": "function",
                        "function": {"name": "get_account", "arguments": "{}"},
                    }
                    for index in range(4)
                ],
            }
            return RawResponse({"choices": [{"finish_reason": "tool_calls", "message": message}]})

    events = []
    run = await pipeline(build_tools(events.append), model=ParallelModel())
    try:
        await asyncio.wait_for(run.speech.received.wait(), 5)
        assert len([event for event in events if event["type"] == "tool_started"]) == 5
        assert run.model.requests[-1]["tool_choice"] == "none"
        last_messages = run.model.requests[-1]["messages"]
        assert [m["role"] for m in last_messages].count("system") == 1
        assert last_messages[0] == run.model.requests[0]["messages"][0]
        assert "tool-call limit" in last_messages[-1]["content"]
        run.speech.received.clear()
        run.context.add_message({"role": "user", "content": "Check again."})
        await run.worker.queue_frame(InterruptionFrame())
        await run.worker.queue_frame(LLMContextFrame(run.context))
        await asyncio.wait_for(run.speech.received.wait(), 5)
        assert len([event for event in events if event["type"] == "tool_started"]) == 10
        assert len(run.model.requests) == 6
        assert run.model.requests[3].get("tool_choice") != "none"
        assert all(
            "tool-call limit" not in (message.get("content") or "")
            for message in run.model.requests[3]["messages"]
        )
        assert (
            run.speech.replies
            == ["Sorry, I lost my train of thought. Could you say that again?"] * 2
        )
        messages = run.model.requests[-1]["messages"]
        calls = [call["id"] for message in messages for call in message.get("tool_calls", [])]
        results = [message["tool_call_id"] for message in messages if message.get("role") == "tool"]
        assert sorted(calls) == sorted(results)
    finally:
        await close(run)


async def test_local_date_uses_one_initial_system_message_without_rewriting_history(monkeypatch):
    clock = SimpleNamespace(now=datetime(2026, 10, 7, 21, 59, tzinfo=UTC))
    monkeypatch.setattr(
        "services.llm.datetime", SimpleNamespace(now=lambda tz: clock.now.astimezone(tz))
    )
    history = [
        {"role": "system", "content": "Stable instructions."},
        {"role": "system", "content": "More instructions."},
        {"role": "user", "content": "Check the time."},
    ]
    run = await pipeline(
        build_tools(lambda event: None), messages=copy.deepcopy(history), utc_offset_minutes=120
    )
    try:
        await asyncio.wait_for(run.speech.received.wait(), 5)
        for request in run.model.requests:
            assert request["messages"][0]["role"] == "system"
            assert [m["content"] for m in request["messages"] if m["role"] == "system"] == [
                "Stable instructions.\n\nMore instructions."
            ]
            assert [
                m["content"]
                for m in request["messages"]
                if (m.get("content") or "").startswith("<runtime_context>")
            ] == ["<runtime_context>\nCurrent local date: 2026-10-07.\n</runtime_context>"]
        assert run.context.get_messages()[: len(history)] == history
        prefix = copy.deepcopy(run.context.get_messages())
        clock.now = datetime(2026, 10, 7, 22, 1, tzinfo=UTC)
        run.speech.received.clear()
        run.context.add_message({"role": "user", "content": "Check again."})
        await run.worker.queue_frame(InterruptionFrame())
        await run.worker.queue_frame(LLMContextFrame(run.context))
        await asyncio.wait_for(run.speech.received.wait(), 5)
        messages = run.context.get_messages()
        assert messages[: len(prefix)] == prefix
        assert run.model.requests[-1]["messages"][0] == run.model.requests[0]["messages"][0]
        assert messages[-1] == {
            "role": "user",
            "content": "<runtime_context>\nCurrent local date: 2026-10-08.\n</runtime_context>",
        }
    finally:
        await close(run)


@pytest.mark.parametrize("failure", ["api", "incomplete", "context_limit"])
async def test_model_failure_speaks_a_fallback_and_accepts_the_next_turn(failure):
    class FailOnceModel(Model):
        async def run(self, model, parameters, options):
            if not self.requests:
                self.requests.append(copy.deepcopy(parameters))
                if failure == "api":
                    raise RuntimeError("Private upstream error details")
                if failure == "context_limit":
                    error = {
                        "internalCode": 5021,
                        "description": "This model's maximum context length was exceeded",
                    }
                    return RawResponse(error, status=400)
                return RawResponse(
                    {
                        "choices": [
                            {
                                "finish_reason": "length",
                                "message": {"role": "assistant", "content": "Unfinished answer"},
                            }
                        ]
                    }
                )
            return await super().run(model, parameters, options)

    run = await pipeline([], model=FailOnceModel(rounds=0))
    try:
        await asyncio.wait_for(run.speech.received.wait(), 5)
        expected = {
            "api": "Sorry, I lost my train of thought. Could you say that again?",
            "incomplete": "That answer got too long. Could you ask me to focus on one part?",
            "context_limit": "This conversation is too long for the model. Please start a new call.",
        }[failure]
        assert run.speech.replies == [expected]
        assert run.previews[0]["text"] == run.speech.replies[0]
        assert run.summary["requests"][0]["outcome"] == "fallback"
        assert not run.task.done()
        run.speech.received.clear()
        run.context.add_message({"role": "user", "content": "Try again."})
        await run.worker.queue_frame(InterruptionFrame())
        await run.worker.queue_frame(LLMContextFrame(run.context))
        await asyncio.wait_for(run.speech.received.wait(), 5)
        assert run.speech.replies[-1] == "Your umbrella was delivered on Monday."
        assert len(run.model.requests) == 2
    finally:
        await close(run)


def test_adapter_keeps_tool_messages_and_rejects_multimodal_content():
    adapter = WorkersAIAdapter()
    context = LLMContext(messages=[{"role": "user", "content": [{"type": "image"}]}])
    with pytest.raises(ValueError, match="Multimodal"):
        adapter.get_llm_invocation_params(context)


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("get_account", {}),
        ("search_help_center", {"query": "returns"}),
        ("cancel_order", {"order_id": "A-1061"}),
        ("open_ticket", {"summary": "Fuel smell", "details": "The pogo stick smells of fuel."}),
        ("request_store_credit", {"order_id": "A-1033", "amount": 20, "reason": "Dented tank"}),
    ],
)
async def test_each_tool_reports_interruption_during_result_delivery(monkeypatch, name, arguments):
    monkeypatch.setattr("agent.tools.help_center.search", AsyncMock(return_value=[]))
    delivering = asyncio.Event()

    async def callback(_result):
        delivering.set()
        await asyncio.Event().wait()

    events = []
    resources = agent_resources(
        events.append, customer_id="cus_rosa", background=lambda coroutine, _: coroutine.close()
    )
    tool = next(tool for tool in build_tools(events.append) if tool.name == name)
    task = asyncio.create_task(
        tool.handler(
            SimpleNamespace(
                arguments=arguments,
                tool_call_id="lookup",
                result_callback=callback,
                app_resources=resources,
                context=conversation(),
            )
        )
    )
    await asyncio.wait_for(delivering.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert [event["type"] for event in events] == ["tool_started", "tool_cancelled"]
    assert all(event["call_id"] == "lookup" and event["name"] == name for event in events)


async def test_tools_act_only_on_the_signed_in_customers_orders():
    db = D1()
    tools = build_tools(lambda event: None)
    sam = agent_resources(customer_id="cus_sam", db=db)
    credit = {"order_id": "A-1042", "amount": 10, "reason": "Bent rib"}
    assert (await invoke(tools, "cancel_order", {"order_id": "A-1061"}, sam))["code"] == (
        "order_not_found"
    )
    assert (await invoke(tools, "request_store_credit", credit, sam))["code"] == "order_not_found"
    account = await invoke(tools, "get_account", {}, sam)
    assert [order["order_id"] for order in account["orders"]] == ["A-1057"]
    assert db.rows("SELECT status FROM orders WHERE id = 'A-1061'") == [{"status": "processing"}]
    assert db.rows("SELECT id FROM approvals") == []


def order_status(db, order_id):
    return db.rows(f"SELECT status FROM orders WHERE id = '{order_id}'")[0]["status"]


async def test_cancel_order_waits_for_a_yes_in_the_customers_next_turn():
    db = D1()
    tools = build_tools(lambda event: None)
    rosa = agent_resources(customer_id="cus_rosa", db=db)
    context = conversation("Cancel my tunnel kit.")
    asked = await invoke(tools, "cancel_order", {"order_id": "a 1061"}, rosa, context)
    assert asked["status"] == "needs_confirmation"
    assert asked["order"]["order_id"] == "A-1061"
    assert asked["order"]["total"] == "$45.00"
    # Calling again in the same turn, or after an app update, isn't a yes.
    again = await invoke(tools, "cancel_order", {"order_id": "A-1061"}, rosa, context)
    assert again["status"] == "needs_confirmation"
    context.add_message(
        {"role": "user", "content": "<runtime_context>\nAn update.\n</runtime_context>"}
    )
    again = await invoke(tools, "cancel_order", {"order_id": "A-1061"}, rosa, context)
    assert again["status"] == "needs_confirmation"
    assert order_status(db, "A-1061") == "processing"
    context.add_message({"role": "user", "content": "Yes, cancel it."})
    cancelled = await invoke(tools, "cancel_order", {"order_id": "A1061"}, rosa, context)
    assert cancelled == {"status": "cancelled", "order_id": "A-1061", "refund": "$45.00"}
    assert order_status(db, "A-1061") == "cancelled"


async def test_a_read_back_expires_after_the_customers_next_turn():
    db = D1()
    tools = build_tools(lambda event: None)
    rosa = agent_resources(customer_id="cus_rosa", db=db)
    context = conversation("Cancel my tunnel kit.")
    await invoke(tools, "cancel_order", {"order_id": "A-1061"}, rosa, context)
    context.add_message({"role": "user", "content": "Hmm, let me think."})
    context.add_message({"role": "user", "content": "Okay, go ahead."})
    # Two turns later, Matt has to read the order back again.
    asked = await invoke(tools, "cancel_order", {"order_id": "A-1061"}, rosa, context)
    assert asked["status"] == "needs_confirmation"
    assert order_status(db, "A-1061") == "processing"


async def test_cancel_order_only_cancels_orders_that_havent_shipped():
    db = D1()
    tools = build_tools(lambda event: None)
    rosa = agent_resources(customer_id="cus_rosa", db=db)
    context = conversation("Cancel my tunnel kit.")
    await invoke(tools, "cancel_order", {"order_id": "A-1061"}, rosa, context)
    context.add_message({"role": "user", "content": "Yes."})
    cancelled = await invoke(tools, "cancel_order", {"order_id": "A-1061"}, rosa, context)
    assert cancelled["status"] == "cancelled"
    # An order that can't be cancelled fails at once, before any read-back.
    again = await invoke(tools, "cancel_order", {"order_id": "A-1061"}, rosa)
    assert again["code"] == "not_cancellable"
    delivered = await invoke(tools, "cancel_order", {"order_id": "A-1033"}, rosa)
    assert delivered["code"] == "not_cancellable"


async def test_store_credit_stays_pending_until_staff_decide():
    db = D1()
    tools = build_tools(lambda event: None)
    maya = agent_resources(customer_id="cus_maya", db=db)
    request = {"order_id": "1042", "amount": 10.5, "reason": "The umbrella has a bent rib."}
    too_high = await invoke(tools, "request_store_credit", {**request, "amount": 50}, maya)
    assert too_high["code"] == "amount_too_high"
    invalid = await invoke(tools, "request_store_credit", {**request, "amount": "ten"}, maya)
    assert invalid["code"] == "invalid_arguments"
    rosa = agent_resources(customer_id="cus_rosa", db=db)
    unshipped = {**request, "order_id": "A-1061"}
    assert (await invoke(tools, "request_store_credit", unshipped, rosa))["code"] == "not_delivered"
    pending = await invoke(tools, "request_store_credit", request, maya)
    assert pending["status"] == "pending_approval"
    assert pending["amount"] == "$10.50"
    assert db.rows(
        "SELECT id, customer_id, order_id, call_id, amount_cents, status FROM approvals"
    ) == [
        {
            "id": pending["request_id"],
            "customer_id": "cus_maya",
            "order_id": "A-1042",
            "call_id": "test-call",
            "amount_cents": 1050,
            "status": "pending",
        }
    ]
    assert db.rows("SELECT credit_cents FROM customers WHERE id = 'cus_maya'") == [
        {"credit_cents": 0}
    ]


async def test_a_failed_triage_leaves_the_ticket_open_and_unrouted(monkeypatch):
    monkeypatch.setattr("agent.tools.triage", AsyncMock(side_effect=ConnectionError("down")))
    events = []
    resources = agent_resources(events.append, customer_id="cus_rosa")
    details = {"summary": "Fuel smell", "details": "The pogo stick smells of fuel."}
    opened = await invoke(build_tools(events.append), "open_ticket", details, resources)
    await resources.background.wait()
    assert opened["status"] == "opened"
    assert resources.env.DB.rows("SELECT id, team FROM tickets") == [
        {"id": opened["ticket_id"], "team": None}
    ]
    assert "ticket_triaged" not in [event["type"] for event in events]


async def test_the_prompt_carries_the_account_from_the_start_of_the_call():
    account = await account_snapshot(agent_resources(customer_id="cus_rosa"))
    assert [order["order_id"] for order in account["orders"]] == ["A-1061", "A-1033"]
    prompt = system_prompt("Rosa", 0, account)
    assert '"order_id": "A-1061"' in prompt and "<account>" in prompt
    assert "Use get_account before discussing any order" not in prompt
    # Without the account, Matt looks it up first, and no name means none is used.
    fallback = system_prompt("", 0)
    assert "<account>" not in fallback
    assert "Use get_account before discussing any order" in fallback
    assert "You are talking with a signed-in customer." in fallback
