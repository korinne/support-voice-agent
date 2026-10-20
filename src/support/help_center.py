"""Acme's help center: articles ship with the Worker, and AI Search indexes them.

Staff publish the articles from the desk. The Worker creates the AI Search
instance through its namespace binding, so no AI Search API token is needed.
"""

from functools import cache
from pathlib import Path

ARTICLES = Path(__file__).parents[1] / "help"
INSTANCE = "help-center"
MAX_RESULTS = 3
MAX_EXCERPT_CHARACTERS = 900


@cache
def articles() -> dict[str, str]:
    """Article file names mapped to their titles, the first heading in each file."""
    return {
        path.name: path.read_text().splitlines()[0].removeprefix("# ").strip()
        for path in sorted(ARTICLES.glob("*.md"))
    }


async def search(ai_search, query):
    instance = await ai_search.get(INSTANCE)
    found = await instance.search(
        {"query": query, "ai_search_options": {"retrieval": {"max_num_results": MAX_RESULTS}}}
    )
    titles = articles()
    results = []
    for chunk in found.get("chunks") or []:
        key = (chunk.get("item") or {}).get("key", "")
        results.append(
            {
                "article": titles.get(key, key.removesuffix(".md")),
                "excerpt": str(chunk.get("text", ""))[:MAX_EXCERPT_CHARACTERS],
            }
        )
    return results


async def publish(ai_search):
    """Creates the instance if needed and uploads every article. Indexing continues afterwards."""
    try:
        await ai_search.create({"id": INSTANCE})
    except Exception as exc:
        if "already_exist" not in str(exc):
            raise
    instance = await ai_search.get(INSTANCE)
    # The SDK treats instance.items as a method; subscript access returns the object.
    items = instance["items"]
    for name in articles():
        await items.upload(name, (ARTICLES / name).read_text())
    return await status(ai_search)


async def status(ai_search):
    local = articles()
    try:
        instance = await ai_search.get(INSTANCE)
        listed = await instance["items"].list({"per_page": 50})
    except Exception:
        return {"articles": len(local), "indexed": 0, "indexing": 0, "published": False}
    states = [item.get("status") for item in listed.get("result") or [] if item.get("key") in local]
    return {
        "articles": len(local),
        "indexed": states.count("completed"),
        "indexing": sum(state in {"queued", "running"} for state in states),
        "published": bool(states),
    }
