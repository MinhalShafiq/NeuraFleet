"""Request IDs, structured logs and /metrics (plan 4.5)."""

import asyncio
import json
import logging
import re

import httpx
import pytest
from conftest import load_module

from shared.observability import JsonFormatter, request_id_var

gw = load_module("gateway", "main")
tel = load_module("telemetry-service", "main")
lidar = load_module("lidar-service", "main")
rag = load_module("rag-service", "main")

APPS = {"gateway": gw.app, "telemetry": tel.app, "lidar": lidar.app, "rag": rag.app}
HEALTH = {"gateway": "/health", "telemetry": "/health", "lidar": "/health", "rag": "/health"}


def _get(app, path, **kw):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:
            return await c.get(path, **kw)

    return asyncio.run(run())


@pytest.mark.parametrize("name", sorted(APPS))
def test_every_service_generates_and_echoes_request_ids(name):
    generated = _get(APPS[name], HEALTH[name])
    assert re.fullmatch(r"[0-9a-f]{16}", generated.headers["x-request-id"])
    echoed = _get(APPS[name], HEALTH[name], headers={"X-Request-ID": "trace-abc.123"})
    assert echoed.headers["x-request-id"] == "trace-abc.123"


def test_hostile_request_ids_are_sanitised():
    r = _get(gw.app, "/health", headers={"X-Request-ID": 'a"b\\c{d}\t' + "x" * 200})
    rid = r.headers["x-request-id"]
    assert re.fullmatch(r"[A-Za-z0-9._-]{1,64}", rid), rid


@pytest.mark.parametrize("name", sorted(APPS))
def test_metrics_endpoint_serves_prometheus_text(name):
    _get(APPS[name], HEALTH[name])
    r = _get(APPS[name], "/metrics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert (
        f'http_requests_total{{method="GET",route="/health",service="{name}",status="200"}}'
        in r.text
    )
    assert "http_request_duration_seconds_bucket" in r.text


def test_metric_labels_use_route_templates_not_raw_paths():
    """Raw paths would create one series per robot id / attacker-chosen URL."""
    tel.fleet_sim = None
    for rid in ("robot-001", "robot-002", "does-not-exist"):
        _get(tel.app, f"/fleet/{rid}")
    text = _get(tel.app, "/metrics").text
    assert 'route="/fleet/{robot_id}"' in text
    assert "robot-001" not in text and "does-not-exist" not in text


def test_unmatched_paths_share_a_single_series():
    for i in range(5):
        _get(tel.app, f"/no/such/path/{i}")
    text = _get(tel.app, "/metrics").text
    assert 'route="unmatched"' in text and "/no/such/path" not in text


def test_domain_metrics_exist():
    assert "lidar_scan_seconds_bucket" in _get(lidar.app, "/metrics").text
    assert "telemetry_active_alerts" in _get(tel.app, "/metrics").text
    assert "# TYPE gateway_mock_fallback_total counter" in _get(gw.app, "/metrics").text
    assert "rag_documents" in _get(rag.app, "/metrics").text


def test_logs_are_json_and_carry_the_request_id(capfd):
    logging.getLogger().handlers[:] = []  # re-install ours in a known state
    from shared.observability import setup_logging

    setup_logging("gateway")
    _get(gw.app, "/api/fleet/robot-001", headers={"X-Request-ID": "trace-xyz"})
    err = capfd.readouterr().err
    lines = [json.loads(line) for line in err.splitlines() if line.startswith("{")]
    access = [line for line in lines if line.get("logger") == "gateway.access"]
    assert access, err
    assert access[-1]["request_id"] == "trace-xyz"
    assert access[-1]["route"] == "/api/fleet/{robot_id}" and "duration_ms" in access[-1]
    assert {"ts", "level", "service", "msg"} <= set(access[-1])


def test_json_formatter_includes_exceptions():
    try:
        raise ValueError("boom")
    except ValueError:
        rec = logging.LogRecord(
            "x", logging.ERROR, __file__, 1, "failed", None, __import__("sys").exc_info()
        )
    token = request_id_var.set("rid-1")
    try:
        out = json.loads(JsonFormatter("svc").format(rec))
    finally:
        request_id_var.reset(token)
    assert (
        out["request_id"] == "rid-1"
        and "ValueError: boom" in out["exc"]
        and out["service"] == "svc"
    )


def test_probe_traffic_is_logged_below_info():
    recs = []

    class H(logging.Handler):
        def emit(self, record):
            recs.append(record)

    h = H(level=logging.DEBUG)
    lg = logging.getLogger("gateway.access")
    lg.addHandler(h)
    old = lg.level
    lg.setLevel(logging.DEBUG)
    try:
        _get(gw.app, "/health")
        _get(gw.app, "/metrics")
    finally:
        lg.removeHandler(h)
        lg.setLevel(old)
    assert recs and all(r.levelno == logging.DEBUG for r in recs)


def test_gateway_forwards_the_request_id_to_upstream(monkeypatch):
    seen = {}

    class Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return []

    class Client:
        async def get(self, url, **kw):
            seen.update(kw.get("headers") or {})
            return Resp()

    async def fake_client():
        return Client()

    monkeypatch.setattr(gw, "_get_client", fake_client)
    r = _get(gw.app, "/api/alerts", headers={"X-Request-ID": "trace-propagate"})
    assert r.status_code == 200
    assert seen.get("X-Request-ID") == "trace-propagate"


def test_mock_fallback_is_counted(monkeypatch):
    async def down(base, path):
        return None

    monkeypatch.setattr(gw, "_proxy_get", down)
    _get(gw.app, "/api/fleet")
    text = _get(gw.app, "/metrics").text
    assert 'gateway_mock_fallback_total{endpoint="/api/fleet"}' in text
