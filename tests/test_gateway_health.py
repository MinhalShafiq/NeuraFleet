"""Gateway health: liveness never depends on downstream, observability probes run concurrently (plan 1.7)."""

import asyncio
import time

import httpx
from conftest import load_module

gw = load_module("gateway", "main")


class _SlowClient:
    """Every downstream /health hangs for 0.3 s then answers 200 with a healthy body
    (every real service's health model carries ``status: "ok"`` - see shared.models)."""

    async def get(self, url, timeout=None, headers=None):
        await asyncio.sleep(0.3)
        return httpx.Response(200, json={"status": "ok"})


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
    assert body["services"]["telemetry"] == "healthy"
    assert body["services"]["lidar"] == "healthy"
    assert body["services"]["rag"] == "healthy"
    # mqtt isn't probed by _probe() at all (command_bus's own .connected flag feeds it
    # directly) and the lifespan that would start it never ran in this raw-ASGI test.
    assert body["services"]["mqtt"] == "unreachable"
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


def test_a_200_health_body_is_live_even_if_its_own_status_says_degraded(monkeypatch):
    """telemetry-service's /health reports "degraded" in its BODY when every robot is
    stale/offline (an MQTT-side outage - see FleetAggregator), but still answers 200 (a
    restart would not fix a broker outage, so it must stay out of the k8s probe's way).
    The gateway's live/mock split only cares whether it got real vs fabricated data, and
    this data is real - merely stale, which `connectivity` says honestly per robot - so
    this must NOT flip the dashboard to the DEMO DATA badge. See shared.models.Connectivity
    for why "stale/offline" and "demo" are kept deliberately separate."""

    class SaysDegradedButResponds:
        async def get(self, url, timeout=None, headers=None):
            return httpx.Response(200, json={"status": "degraded"})

    async def fake_client():
        return SaysDegradedButResponds()

    monkeypatch.setattr(gw, "_get_client", fake_client)

    async def run():
        async with _app_client() as c:
            return (await c.get("/api/health")).json()

    body = asyncio.run(run())
    assert body["services"]["telemetry"] == "healthy"
    assert body["mode"]["telemetry"] == "live"


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
