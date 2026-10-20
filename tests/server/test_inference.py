import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from conftest import RawResponse

import services.inference as inference_module
from server.observability import log
from services.inference import run_inference


class SharedOptionsBinding:
    """Like the Workers AI binding: every request shares one options slot,
    which the binding reads again when each response arrives."""

    def __init__(self):
        self.options = {}
        self.calls = []

    async def run(self, model, inputs, options):
        self.options = options
        self.calls.append(model)
        # Let the other request start before this response arrives.
        for _ in range(3):
            await asyncio.sleep(0)
        response = RawResponse({"model": model})
        if self.options.get("returnRawResponse") or self.options.get("websocket"):
            return response
        return response.data


async def test_overlapping_requests_each_get_the_result_their_caller_expects():
    for first, second in (("speech", "triage"), ("triage", "speech")):
        binding = SharedOptionsBinding()
        results = await asyncio.gather(
            *(
                run_inference(
                    binding, name, {}, {}, log=log, is_current=lambda: True, raw=name == "speech"
                )
                for name in (first, second)
            )
        )
        speech, triage = results if first == "speech" else reversed(results)
        assert speech.status == 200
        assert triage == {"model": "triage"}
        # Neither request needed a retry.
        assert binding.calls == [first, second]


async def test_an_error_response_is_classified_by_the_code_in_its_body(monkeypatch):
    monkeypatch.setattr(inference_module, "RETRY_DELAY_SECONDS", 0)
    capacity = {"internalCode": 3040, "description": "Capacity temporarily exceeded"}
    run = AsyncMock(side_effect=[RawResponse(capacity, status=429), RawResponse({"ok": True})])
    result = await run_inference(
        SimpleNamespace(run=run), "model", {}, {}, log=log, is_current=lambda: True
    )
    # Code 3040 means the model is busy for a moment, so the request is retried.
    assert result == {"ok": True}
    assert run.await_count == 2
