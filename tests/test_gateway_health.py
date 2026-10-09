"""Gateway health: liveness never depends on downstream, observability probes run concurrently (plan 1.7)."""

import asyncio
import time

import httpx
from conftest import load_module

gw = load_module("gateway", "main")


class _SlowClient:
    """Every downstream /health hangs for 0.3 s then answers 200."""

    async def get(self, url, timeout=None, headers=None):
        await asyncio.sleep(0.3)
        return httpx.Response(200)


def _app_client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=gw.app), base_url="http://t")


def test_probes_run_concurrently(monkeypatch):
    async def fake_client():
        return _SlowClient()

    monkeypatch.setattr(gw, "_get_client", fake_client)

    async def run():
        async with _app_client() as c:
            t = time.perf_counter()
            r = await c.get("/api/health")
            return r.json(), time.perf_counter() - t

    body, elapsed = asyncio.run(run())
    assert body["services"] == {"telemetry": "healthy", "lidar": "healthy", "rag": "healthy"}
    assert set(body["mode"].values()) == {"live"}
    assert elapsed < 0.6, f"3 x 0.3 s probes took {elapsed:.2f} s - they are running sequentially"


def test_liveness_does_not_touch_downstream(monkeypatch):
    async def boom():
        raise AssertionError("/health must not call downstream services")

    monkeypatch.setattr(gw, "_get_client", boom)

    async def run():
        async with _app_client() as c:
            return await c.get("/health")

    r = asyncio.run(run())
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_unreachable_service_is_reported_not_raised(monkeypatch):
    class Dead:
        async def get(self, url, timeout=None, headers=None):
            raise httpx.ConnectError("down")

    async def fake_client():
        return Dead()

    monkeypatch.setattr(gw, "_get_client", fake_client)

    async def run():
        async with _app_client() as c:
            return (await c.get("/api/health")).json()

    body = asyncio.run(run())
    assert body["status"] == "ok"
    assert set(body["services"].values()) == {"unreachable"}
    assert set(body["mode"].values()) == {"mock"}, "unreachable services are served from mock data"
