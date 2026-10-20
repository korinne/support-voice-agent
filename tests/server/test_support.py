from types import SimpleNamespace
from unittest.mock import AsyncMock

from conftest import RawResponse

from support import help_center
from support.triage import QUESTIONS, triage


class Items:
    def __init__(self):
        self.uploaded = {}

    async def upload(self, name, text):
        self.uploaded[name] = text

    async def list(self, options):
        return {"result": [{"key": name, "status": "completed"} for name in self.uploaded]}


class Instance:
    def __init__(self, chunks=()):
        self.items = Items()
        self.search = AsyncMock(return_value={"chunks": list(chunks)})

    def __getitem__(self, name):
        # The Python Workers SDK exposes nested RPC objects by subscript.
        return getattr(self, name)


class Namespace:
    def __init__(self, instance, *, exists=True):
        self.instance, self.exists = instance, exists

    async def create(self, options):
        if self.exists:
            raise RuntimeError("AiSearchError: instance already_exists")
        self.exists = True

    async def get(self, name):
        if not self.exists:
            raise RuntimeError("AiSearchError: not_found")
        return self.instance


async def test_search_returns_article_titles_and_short_excerpts():
    chunks = [
        {
            "item": {"key": "returns-and-refunds.md"},
            "text": "Returns are free " * 100,
            "score": 0.8,
        },
        {"item": {"key": "unknown.md"}, "text": "Other", "score": 0.4},
    ]
    instance = Instance(chunks)
    results = await help_center.search(Namespace(instance), "return window")
    assert results[0]["article"] == "Returns and refunds"
    assert len(results[0]["excerpt"]) == help_center.MAX_EXCERPT_CHARACTERS
    assert results[1] == {"article": "unknown", "excerpt": "Other"}
    query = instance.search.call_args.args[0]
    assert query["query"] == "return window"
    assert query["ai_search_options"]["retrieval"]["max_num_results"] == help_center.MAX_RESULTS


async def test_publishing_creates_the_instance_once_and_uploads_every_article():
    namespace = Namespace(Instance(), exists=False)
    before = await help_center.status(namespace)
    assert before["published"] is False
    published = await help_center.publish(namespace)
    articles = help_center.articles()
    assert len(articles) == 8
    assert set(namespace.instance.items.uploaded) == set(articles)
    assert published == {
        "articles": 8,
        "indexed": 8,
        "indexing": 0,
        "published": True,
    }
    # Publishing again replaces the articles in the existing instance.
    assert (await help_center.publish(namespace))["indexed"] == 8


async def test_triage_asks_clef_three_questions_through_the_calls_gateway():
    answers = {
        "team": {"choice": "safety", "probabilities": {"safety": 0.9312, "product": 0.0688}},
        "urgency": {"score": 2.8761, "legend": {}},
        "wants_person": {"noul": 0.2049},
    }
    ai = SimpleNamespace(run=AsyncMock(return_value=RawResponse({"answers": answers})))
    gateway = {"id": "default", "metadata": {"customer_id": "cus_rosa", "call_id": "b" * 32}}
    ticket = {"summary": "Fuel smell", "details": "Smells of fuel.", "order_id": "A-1033"}
    assert await triage(ai, ticket, gateway) == {
        "team": "safety",
        "team_confidence": 0.931,
        "urgency": 2.88,
        "wants_person": 0.205,
    }
    model, body, options = ai.run.call_args.args
    assert model == "@cf/cloudflare/clef"
    assert body["model"] == "clef"
    assert body["state"] == {
        "summary": "Fuel smell",
        "details": "Smells of fuel.",
        "order": "A-1033",
    }
    assert body["questions"] is QUESTIONS
    assert options == {"gateway": gateway, "returnRawResponse": True}
