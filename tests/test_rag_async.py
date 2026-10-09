"""RAG service must not block the event loop on Chroma or LLM calls (plan 1.2)."""
import asyncio
import time
import types

import httpx
import pytest

pytest.importorskip("chromadb")
from conftest import load_module  # noqa: E402

rag = load_module("rag-service", "main")
llm_mod = load_module("rag-service", "llm_client")

DELAY = 0.3


async def _max_loop_lag(coro_factory):
    """Run coro_factory() while a 10 ms ticker measures how late the loop wakes up."""
    lags, stop = [], False

    async def ticker():
        while not stop:
            t = time.perf_counter()
            await asyncio.sleep(0.01)
            lags.append(time.perf_counter() - t - 0.01)

    task = asyncio.create_task(ticker())
    await asyncio.sleep(0.05)
    result = await coro_factory()
    stop = True
    await task
    return result, max(lags)


class _SlowStore:
    """Stands in for Chroma: a synchronous query that takes DELAY seconds."""

    def query(self, query_text, n_results=5, filter=None):
        time.sleep(DELAY)
        return [{"text": "Battery below 20% triggers an alert.", "metadata": {"source": "m.txt"}, "distance": 0.1}]

    class _C:
        @staticmethod
        def count():
            return 1

    _collection = _C()


def test_query_does_not_block_event_loop(monkeypatch):
    monkeypatch.setattr(rag, "vector_store", _SlowStore())
    monkeypatch.setattr(rag, "llm_client", llm_mod.LLMClient())  # no key -> rule-based

    async def call():
        transport = httpx.ASGITransport(app=rag.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await c.post("/query", json={"query": "battery?"})

    resp, lag = asyncio.run(_max_loop_lag(call))
    assert resp.status_code == 200
    assert lag < 0.1, f"event loop blocked for {lag * 1000:.0f} ms during a {DELAY * 1000:.0f} ms store query"


def test_anthropic_call_is_awaited_not_blocking(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    client = llm_mod.LLMClient()
    assert type(client._client).__name__ == "AsyncAnthropic"

    class _Messages:
        async def create(self, **kw):
            await asyncio.sleep(DELAY)  # a real async client yields here
            return types.SimpleNamespace(content=[types.SimpleNamespace(text="from claude")])

    client._client = types.SimpleNamespace(messages=_Messages())

    out, lag = asyncio.run(_max_loop_lag(lambda: client.generate_response("q", ["ctx"])))
    assert out == "from claude"
    assert lag < 0.1
