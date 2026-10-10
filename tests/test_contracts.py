"""The shared Pydantic models are the API contract (plan 2.4): every service's real output,
and every gateway mock, must validate against them."""

import asyncio

import httpx
import pytest
from conftest import load_module
from pydantic import ValidationError

from shared.models import (
    Alert,
    LidarScan,
    MetricsHistory,
    RAGResponse,
    RobotState,
    TelemetryData,
)

sim_mod = load_module("telemetry-service", "robot_simulator")
tel = load_module("telemetry-service", "main")
gw = load_module("gateway", "main")
lidar = load_module("lidar-service", "main")


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


# --------------------------------------------------------------- simulator output
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_simulator_output_always_satisfies_the_contract(seed):
    """Long runs, several seeds: anything out of range (battery 100.4, cpu -0.3, an unknown
    status) would be a 500 in production once response_model validates it."""
    sim = sim_mod.FleetSimulator(seed=seed)
    for tick in range(4000):
        sim.update(0.1)
        if tick % 25 == 0:
            for r in sim.get_all_robots():
                RobotState(**r)
            for r in sim.get_telemetry():
                TelemetryData(**r)
            for a in sim.get_alerts() + sim.get_resolved_alerts():
                Alert(**a)
    for rid in sim.robots:
        MetricsHistory(**sim.get_metrics(rid))


def test_contract_rejects_out_of_range_values():
    good = sim_mod.FleetSimulator(seed=1).get_all_robots()[0]
    with pytest.raises(ValidationError):
        RobotState(**{**good, "battery": 140})
    with pytest.raises(ValidationError):
        RobotState(**{**good, "status": "exploding"})


# --------------------------------------------------------------- gateway mocks
def test_gateway_mocks_satisfy_the_contract_and_are_flagged_demo():
    for r in gw._mock_fleet():
        assert RobotState(**r).demo is True
    for r in gw._mock_telemetry():
        assert TelemetryData(**r).demo is True
    for _ in range(20):  # alerts are randomised
        for a in gw._mock_alerts():
            assert Alert(**a).demo is True
    assert MetricsHistory(**gw._mock_metrics("robot-001")).demo is True
    assert LidarScan(**gw._mock_lidar_scan("robot-001")).demo is True
    assert RAGResponse(**gw._mock_rag_response("q", None)).demo is True


def test_live_data_is_not_flagged_demo():
    assert RobotState(**sim_mod.FleetSimulator(seed=1).get_all_robots()[0]).demo is False


def test_mock_positions_are_stable_across_processes():
    """hash(str) is randomised per process; the mocks must not depend on it."""
    import subprocess
    import sys

    code = (
        "import sys; sys.path[:0]=['backend','backend/gateway']; "
        "import importlib.util as u; s=u.spec_from_file_location('g','backend/gateway/main.py'); "
        "m=u.module_from_spec(s); s.loader.exec_module(m); print(m._phase('robot-001'))"
    )
    from conftest import ROOT

    outs = {
        subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            cwd=ROOT,
            env={"PYTHONHASHSEED": seed, "PATH": ""},
        ).stdout.strip()
        for seed in ("1", "2")
    }
    assert len(outs) == 1 and outs != {""}


# --------------------------------------------------------------- HTTP responses
def test_telemetry_endpoints_return_contract_shapes_and_404s():
    tel.fleet_sim = sim_mod.FleetSimulator(seed=1)
    for _ in range(50):
        tel.fleet_sim.update(0.1)

    async def run():
        async with _client(tel.app) as c:
            fleet = (await c.get("/fleet")).json()
            one = await c.get("/fleet/robot-001")
            missing = await c.get("/fleet/robot-999")
            alerts = (await c.get("/alerts")).json()
            metrics = await c.get("/metrics/robot-001")
            bad_metrics = await c.get("/metrics/nope")
            health = (await c.get("/health")).json()
        return fleet, one, missing, alerts, metrics, bad_metrics, health

    try:
        fleet, one, missing, alerts, metrics, bad_metrics, health = asyncio.run(run())
    finally:
        tel.fleet_sim = None
    assert [RobotState(**r).robot_id for r in fleet][:2] == ["robot-001", "robot-002"]
    assert one.status_code == 200 and RobotState(**one.json())
    assert missing.status_code == 404 and missing.json() == {"detail": "robot robot-999 not found"}
    assert all(Alert(**a) for a in alerts)
    assert metrics.status_code == 200 and MetricsHistory(**metrics.json())
    assert bad_metrics.status_code == 404
    assert health["status"] == "ok" and health["robots"] == 6


def test_lidar_scan_endpoint_matches_contract_and_rejects_unknown_robots():
    async def run():
        async with _client(lidar.app) as c:
            ok = await c.get("/scan/robot-001")
            unknown = await c.get("/scan/anything-at-all")
        return ok, unknown

    ok, unknown = asyncio.run(run())
    scan = LidarScan(**ok.json())
    assert scan.num_points == len(scan.points) > 0 and scan.demo is False
    _assert_scan_is_centred_on_origin(scan)
    assert unknown.status_code == 404
    assert "anything-at-all" not in lidar._simulators, "unknown ids must not allocate a simulator"


def test_gateway_turns_an_invalid_upstream_payload_into_502(monkeypatch):
    """A service answering 200 with a body that breaks the contract is a bug upstream;
    it must not be forwarded to the dashboard (and must not silently become mock data)."""
    bad = [{**sim_mod.FleetSimulator(seed=1).get_all_robots()[0], "battery": 999}]

    async def fake_get(base, path):
        return bad

    monkeypatch.setattr(gw, "_proxy_get", fake_get)

    async def run():
        async with _client(gw.app) as c:
            return await c.get("/api/fleet")

    r = asyncio.run(run())
    assert r.status_code == 502 and r.json() == {"detail": "upstream returned invalid data"}


def test_gateway_passes_upstream_404_through_instead_of_mocking(monkeypatch):
    class Resp:
        status_code = 404

        def json(self):
            return {"detail": "robot robot-001 not found"}

    class Client:
        async def get(self, url, **kw):
            return Resp()

    async def fake_client():
        return Client()

    monkeypatch.setattr(gw, "_get_client", fake_client)

    async def run():
        async with _client(gw.app) as c:
            return await c.get("/api/fleet/robot-001")

    r = asyncio.run(run())
    assert r.status_code == 404, "a real 'no such robot' must not be papered over with a mock robot"


def test_rag_query_is_validated_at_the_gateway():
    async def run():
        async with _client(gw.app) as c:
            return await c.post("/api/rag/query", json={"query": ""})

    assert asyncio.run(run()).status_code == 422


# --------------------------------------------------------------- OpenAPI
@pytest.mark.parametrize(
    ("app", "path", "model"),
    [
        (tel.app, "/fleet", "RobotState"),
        (tel.app, "/alerts", "Alert"),
        (gw.app, "/api/fleet", "RobotState"),
        (gw.app, "/api/alerts", "Alert"),
        (gw.app, "/api/metrics/{robot_id}", "MetricsHistory"),
        (gw.app, "/api/rag/query", "RAGResponse"),
        (gw.app, "/api/health", "GatewayHealth"),
        (lidar.app, "/scan/{robot_id}", "LidarScan"),
    ],
)
def test_openapi_documents_typed_responses(app, path, model):
    schema = app.openapi()["paths"][path]
    op = schema.get("get") or schema["post"]
    assert model in str(op["responses"]["200"]), f"{path} does not document {model}"


def test_telemetry_ws_treats_connection_closed_as_a_normal_disconnect(caplog):
    """Regression: a viewer leaving surfaces from uvicorn's transport as ConnectionClosedOK,
    which used to be logged as 'Telemetry WS error' at ERROR level."""
    import logging

    from websockets.exceptions import ConnectionClosedOK

    class Socket:
        sent = 0

        async def accept(self):
            pass

        async def send_text(self, text):
            self.sent += 1
            if self.sent > 1:
                raise ConnectionClosedOK(None, None)

    tel.fleet_sim = sim_mod.FleetSimulator(seed=1)
    with caplog.at_level(logging.INFO, logger="telemetry_service"):
        try:
            asyncio.run(asyncio.wait_for(tel.ws_telemetry(Socket()), 5))
        finally:
            tel.fleet_sim = None
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR], caplog.text
    assert any("disconnected" in r.getMessage() for r in caplog.records)


def _assert_scan_is_centred_on_origin(scan):
    """Points are in the WORLD frame, so a viewer can only draw them if it knows where the sensor
    was.  Regression: without ``origin`` the 3D viewer looked at (0,0,0) while the cloud sat 40-90 m
    away, and showed an empty grid although frames were streaming."""
    import math

    assert scan.origin is not None, "scan must say where the sensor was"
    assert 0 <= scan.origin.x <= 100 and 0 <= scan.origin.y <= 100  # inside the arena
    assert scan.heading is not None
    reach = max(math.hypot(p[0] - scan.origin.x, p[1] - scan.origin.y) for p in scan.points)
    assert reach <= 100.5, (
        f"points reach {reach:.0f} m from the reported origin (max range is 100 m)"
    )


def test_gateway_mock_scan_is_sensor_relative():
    scan = LidarScan(**gw._mock_lidar_scan("robot-001"))
    assert (scan.origin.x, scan.origin.y, scan.origin.z) == (0.0, 0.0, 0.0)
    assert max(math_hypot(p[0], p[1]) for p in scan.points) <= 51


def math_hypot(x, y):
    return (x * x + y * y) ** 0.5
