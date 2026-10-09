"""Gateway CORS (plan 2.5) and WebSocket mid-stream fallback + recovery (plan 2.7)."""

import asyncio
import dataclasses
import json

import httpx
import pytest
import websockets
from conftest import load_module
from fastapi import WebSocketDisconnect

from shared.observability import request_id_var

gw = load_module("gateway", "main")


# ------------------------------------------------------------------------- CORS
def _preflight(origin):
    async def run():
        transport = httpx.ASGITransport(app=gw.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await c.options(
                "/api/fleet",
                headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
            )

    return asyncio.run(run())


def test_cors_allows_configured_origin_without_credentials():
    r = _preflight("http://localhost:3000")
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "access-control-allow-credentials" not in r.headers


def test_cors_rejects_unlisted_origin():
    r = _preflight("https://evil.example")
    assert "access-control-allow-origin" not in r.headers


def test_cors_is_never_wildcard_with_credentials():
    cors = next(m for m in gw.app.user_middleware if m.cls.__name__ == "CORSMiddleware")
    assert "*" not in cors.options["allow_origins"]
    assert cors.options["allow_credentials"] is False


# ------------------------------------------------------------- WebSocket fallback
class FakeBrowser:
    """Records frames; behaves like a closed socket after ``limit`` of them."""

    def __init__(self, limit):
        self.limit = limit
        self.frames = []

    async def send_text(self, text):
        if len(self.frames) >= self.limit:
            raise WebSocketDisconnect()
        self.frames.append(json.loads(text))


@pytest.fixture
def fast_retry(monkeypatch):
    monkeypatch.setattr(gw, "settings", dataclasses.replace(gw.settings, ws_reconnect_delay=0.05))


async def _serve(handler):
    server = await websockets.serve(handler, "127.0.0.1", 0)
    return server, f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"


def test_midstream_drop_falls_back_to_mock_then_recovers(fast_retry):
    """Upstream sends two live frames then dies. The browser must keep receiving frames
    (flagged demo) and go back to live data when the upstream accepts connections again."""
    connections = []

    async def flaky(ws):
        connections.append(ws.request_headers.get("X-Request-ID"))  # Headers is case-insensitive
        for i in range(2):
            await ws.send(json.dumps({"live": len(connections), "i": i}))
        # returning closes the socket: a mid-stream failure

    async def run():
        server, url = await _serve(flaky)
        browser = FakeBrowser(limit=30)
        token = request_id_var.set("trace-ws")
        try:
            await asyncio.wait_for(
                gw._relay_with_fallback(browser, url, lambda: {"demo": True}, 0.01, "test"), 10
            )
        finally:
            request_id_var.reset(token)
            server.close()
            await server.wait_closed()
        return browser.frames

    frames = asyncio.run(run())
    kinds = ["live" if "live" in f else "demo" for f in frames]
    assert kinds[:2] == ["live", "live"], kinds
    assert "demo" in kinds, "no mock frames after the upstream dropped"
    first_demo = kinds.index("demo")
    assert "live" in kinds[first_demo:], "never returned to live data after recovery"
    lives = {f["live"] for f in frames if "live" in f}
    assert {1, 2} <= lives, f"never opened a second upstream connection: {lives}"
    assert connections[0] == "trace-ws", "request id must reach the upstream"


def test_unreachable_upstream_streams_mock_and_keeps_retrying(fast_retry):
    async def run():
        # grab a free port, then close it so connections are refused
        server, url = await _serve(lambda ws: None)
        server.close()
        await server.wait_closed()
        browser = FakeBrowser(limit=25)
        await asyncio.wait_for(
            gw._relay_with_fallback(browser, url, lambda: {"demo": True}, 0.01, "test"), 10
        )
        return browser.frames

    frames = asyncio.run(run())
    assert len(frames) == 25 and all(f == {"demo": True} for f in frames)


def test_relay_returns_promptly_when_the_browser_leaves(fast_retry):
    async def steady(ws):
        while True:
            await ws.send(json.dumps({"live": 1}))
            await asyncio.sleep(0.01)

    async def run():
        server, url = await _serve(steady)
        browser = FakeBrowser(limit=3)
        try:
            await asyncio.wait_for(
                gw._relay_with_fallback(browser, url, lambda: {"demo": True}, 0.01, "test"), 5
            )
        finally:
            server.close()
            await server.wait_closed()
        return browser.frames

    frames = asyncio.run(run())
    assert len(frames) == 3 and all("live" in f for f in frames)


def test_fallback_is_counted_per_stream(fast_retry):
    async def run():
        server, url = await _serve(lambda ws: None)
        server.close()
        await server.wait_closed()
        await asyncio.wait_for(
            gw._relay_with_fallback(
                FakeBrowser(limit=2), url, lambda: {"demo": True}, 0.01, "counted"
            ),
            5,
        )

    asyncio.run(run())
    sample = gw.mock_fallbacks.labels("ws:counted")._value.get()
    assert sample >= 1
