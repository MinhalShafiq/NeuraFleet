"""
NeuraFleet API Gateway
======================

Central entry point that exposes public API endpoints and proxies
requests to internal microservices (telemetry, LiDAR, RAG).

When a downstream service is unreachable the gateway falls back to
locally-generated mock data so the frontend can always function in
standalone / demo mode.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import time
import uuid
import zlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
import websockets
from config import settings
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import ResponseValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from websockets.exceptions import ConnectionClosed

from shared.models import (
    Alert,
    ErrorDetail,
    GatewayHealth,
    LidarScan,
    LivenessResponse,
    MetricsHistory,
    RAGQuery,
    RAGResponse,
    RobotState,
)
from shared.observability import REQUEST_ID_HEADER, install_observability, request_id_var

logger = logging.getLogger("gateway")

# ---------------------------------------------------------------------------
# Mock data helpers (used when services are unreachable)
# ---------------------------------------------------------------------------

_ROBOT_NAMES = [
    ("robot-001", "Atlas-1", "explorer"),
    ("robot-002", "Scout-2", "scout"),
    ("robot-003", "Hauler-3", "hauler"),
    ("robot-004", "Sentinel-4", "sentinel"),
    ("robot-005", "Mapper-5", "mapper"),
    ("robot-006", "Relay-6", "relay"),
]

_mock_frame_counter: int = 0
_HEALTH_PROBE_TIMEOUT = 2.0  # seconds, per downstream service (probed concurrently)


def _phase(key: str) -> int:
    """Process-independent replacement for hash(str), which Python randomises per process."""
    return zlib.crc32(key.encode())


def _ts() -> str:
    return datetime.now(UTC).isoformat()


def _mock_robot(rid: str, name: str, rtype: str) -> dict:
    """Generate a single mock robot state."""
    t = time.time()
    return {
        "robot_id": rid,
        "name": name,
        "robot_type": rtype,
        "demo": True,
        "timestamp": _ts(),
        "position": {
            "x": 50 + 30 * math.sin(t * 0.1 + _phase(rid) % 100),
            "y": 50 + 30 * math.cos(t * 0.13 + _phase(rid) % 100),
            "z": 0.0,
        },
        "velocity": {
            "vx": random.uniform(-1, 1),
            "vy": random.uniform(-1, 1),
            "vz": 0.0,
        },
        "battery": max(10.0, 85.0 + 10 * math.sin(t * 0.005 + _phase(rid))),
        "status": "active",
        "cpu_usage": 30 + 20 * abs(math.sin(t * 0.07 + _phase(rid))),
        "memory_usage": 40 + 15 * abs(math.cos(t * 0.05 + _phase(rid))),
        "temperature": 45 + 10 * abs(math.sin(t * 0.03 + _phase(rid))),
        "sensors": {
            "imu": {
                "ax": random.gauss(0, 0.1),
                "ay": random.gauss(0, 0.1),
                "az": random.gauss(-9.81, 0.05),
                "gx": random.gauss(0, 0.01),
                "gy": random.gauss(0, 0.01),
                "gz": random.gauss(0, 0.01),
            },
            "gps": {
                "lat": 37.7749 + random.gauss(0, 0.0001),
                "lon": -122.4194 + random.gauss(0, 0.0001),
                "alt": 10.0 + random.gauss(0, 0.5),
            },
        },
    }


def _mock_fleet() -> list:
    return [_mock_robot(rid, name, rtype) for rid, name, rtype in _ROBOT_NAMES]


def _mock_telemetry() -> list:
    """Return telemetry-style data for all robots."""
    fleet = _mock_fleet()
    for r in fleet:
        # Telemetry has same shape but without name/robot_type at top level
        r.pop("name", None)
        r.pop("robot_type", None)
    return fleet


def _mock_lidar_scan(robot_id: str) -> dict:
    """Generate a small mock LiDAR scan."""
    global _mock_frame_counter
    _mock_frame_counter += 1
    num_points = random.randint(800, 1500)
    points = []
    for _ in range(num_points):
        angle = random.uniform(0, 2 * math.pi)
        dist = random.uniform(1, 50)
        x = dist * math.cos(angle) + random.gauss(0, 0.02)
        y = dist * math.sin(angle) + random.gauss(0, 0.02)
        z = random.uniform(-1.5, 2.0) + random.gauss(0, 0.02)
        intensity = random.uniform(0, 1)
        points.append([round(x, 3), round(y, 3), round(z, 3), round(intensity, 3)])
    return {
        "robot_id": robot_id,
        "timestamp": _ts(),
        "points": points,
        "frame_id": _mock_frame_counter,
        "num_points": num_points,
        "demo": True,
    }


def _mock_alerts() -> list:
    """Return a handful of demo alerts."""
    alerts = []
    # Randomly generate 0-3 alerts
    possible = [
        ("robot-003", "high_temperature", "warning", "Temperature elevated to 78°C on Hauler-3"),
        ("robot-005", "low_battery", "warning", "Battery at 18% on Mapper-5"),
        ("robot-001", "collision_proximity", "info", "Object detected within 1.5 m of Atlas-1"),
    ]
    for rid, atype, sev, msg in possible:
        if random.random() < 0.5:
            alerts.append(
                {
                    "id": str(uuid.uuid4()),
                    "robot_id": rid,
                    "type": atype,
                    "severity": sev,
                    "message": msg,
                    "timestamp": _ts(),
                    "demo": True,
                }
            )
    return alerts


def _mock_metrics(robot_id: str) -> dict:
    """Generate 100 mock metric points for the robot."""
    now = time.time()
    points = []
    for i in range(100):
        t = now - (99 - i) * 2  # 2 s intervals
        points.append(
            {
                "timestamp": datetime.fromtimestamp(t, tz=UTC).isoformat(),
                "battery": max(10, 85 + 10 * math.sin(t * 0.005 + _phase(robot_id))),
                "cpu_usage": 30 + 20 * abs(math.sin(t * 0.07 + _phase(robot_id))),
                "memory_usage": 40 + 15 * abs(math.cos(t * 0.05 + _phase(robot_id))),
                "temperature": 45 + 10 * abs(math.sin(t * 0.03 + _phase(robot_id))),
                "velocity_magnitude": abs(random.gauss(1.0, 0.3)),
            }
        )
    return {"robot_id": robot_id, "points": points, "demo": True}


def _mock_rag_response(query: str, robot_id: str | None) -> dict:
    """Generate a fallback RAG response."""
    ctx = f" for robot {robot_id}" if robot_id else ""
    return {
        "response": (
            f"Based on the fleet knowledge base{ctx}: "
            f"Your query '{query}' relates to robot fleet operations. "
            "Please ensure all safety protocols are followed per the operations manual. "
            "For detailed troubleshooting, consult the maintenance guide or contact the fleet operator."
        ),
        "sources": ["robot_manual.txt", "troubleshooting.txt"],
        "query": query,
        "demo": True,
    }


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

_http_client: httpx.AsyncClient | None = None


async def _get_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=settings.http_timeout)
    return _http_client


def _trace_headers() -> dict[str, str]:
    """Forward the current request ID so one user action can be followed across services."""
    return {REQUEST_ID_HEADER: request_id_var.get()}


async def _proxy_get(base_url: str, path: str) -> dict | list | None:
    """GET an internal service.  None means "unreachable/failing": the caller serves mock data.
    A 404 is a real answer ("no such robot"), so it is passed through, not mocked."""
    try:
        client = await _get_client()
        resp = await client.get(f"{base_url}{path}", headers=_trace_headers())
        if resp.status_code == 404:
            raise HTTPException(status_code=404, detail=resp.json().get("detail", "not found"))
        resp.raise_for_status()
        return resp.json()
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Upstream GET %s%s failed: %s", base_url, path, exc)
        return None


async def _proxy_post(base_url: str, path: str, body: dict) -> dict | None:
    """POST to an internal service; None means "unreachable/failing"."""
    try:
        client = await _get_client()
        resp = await client.post(f"{base_url}{path}", json=body, headers=_trace_headers())
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("Upstream POST %s%s failed: %s", base_url, path, exc)
        return None


# ---------------------------------------------------------------------------
# Application lifecycle
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("NeuraFleet API Gateway starting up")
    logger.info("  Telemetry service: %s", settings.telemetry_service_url)
    logger.info("  LiDAR service:     %s", settings.lidar_service_url)
    logger.info("  RAG service:       %s", settings.rag_service_url)
    logger.info("  CORS origins:      %s", ", ".join(settings.cors_origins))
    yield
    # Shutdown
    global _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()
    logger.info("NeuraFleet API Gateway shut down")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="NeuraFleet API Gateway",
    description="Cloud-native fleet simulation platform gateway",
    version="1.0.0",
    lifespan=lifespan,
)

# Explicit origins, no credentials: the API uses no cookies, and browsers reject a
# wildcard origin combined with credentials anyway.
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", REQUEST_ID_HEADER],
    expose_headers=[REQUEST_ID_HEADER],
)
obs = install_observability(app, "gateway")
mock_fallbacks = obs.counter(
    "gateway_mock_fallback_total",
    "Responses served from mock data because a backing service failed",
    ("endpoint",),
)


@app.exception_handler(ResponseValidationError)
async def _bad_upstream(request: Request, exc: ResponseValidationError):
    """A service answered 200 with a body that violates the shared contract."""
    logger.error("Upstream returned invalid data for %s: %s", request.url.path, exc.errors()[:3])
    return JSONResponse(status_code=502, content={"detail": "upstream returned invalid data"})


def _fallback(endpoint: str, mock):
    """Serve mock data, and make the degradation visible in metrics and logs."""
    mock_fallbacks.labels(endpoint).inc()
    logger.warning("Serving MOCK data for %s (upstream unavailable)", endpoint)
    return mock


_NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"model": ErrorDetail}}


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/health", response_model=LivenessResponse)
async def liveness():
    """Cheap liveness/readiness probe target (no downstream calls)."""
    return {"status": "ok"}


async def _probe(name: str, url: str) -> tuple:
    try:
        client = await _get_client()
        r = await client.get(
            f"{url}/health", timeout=_HEALTH_PROBE_TIMEOUT, headers=_trace_headers()
        )
        return name, ("healthy" if r.status_code == 200 else "degraded")
    except Exception:
        return name, "unreachable"


@app.get("/api/health", response_model=GatewayHealth)
async def health():
    """
    Observability endpoint: gateway status, each downstream service, and whether the data
    the dashboard is getting is live or mock.

    Probes run concurrently, so the worst case is one timeout (2 s), not the sum of three.
    This is NOT the k8s probe target (that is ``/health``): the gateway serves mock data when a
    service is down, so a sick dependency must not pull gateway pods out of rotation.
    """
    results = await asyncio.gather(
        _probe("telemetry", settings.telemetry_service_url),
        _probe("lidar", settings.lidar_service_url),
        _probe("rag", settings.rag_service_url),
    )
    services = dict(results)
    mode = {name: ("live" if state == "healthy" else "mock") for name, state in services.items()}
    return {"status": "ok", "services": services, "mode": mode}


# ---------------------------------------------------------------------------
# Fleet / Telemetry REST
# ---------------------------------------------------------------------------


@app.get("/api/fleet", response_model=list[RobotState])
async def get_fleet():
    """Return all robot states."""
    data = await _proxy_get(settings.telemetry_service_url, "/fleet")
    if data is not None:
        return data
    return _fallback("/api/fleet", _mock_fleet())


@app.get("/api/fleet/{robot_id}", response_model=RobotState, responses=_NOT_FOUND)
async def get_robot(robot_id: str):
    """Return a specific robot's state."""
    data = await _proxy_get(settings.telemetry_service_url, f"/fleet/{robot_id}")
    if data is not None:
        return data
    for rid, name, rtype in _ROBOT_NAMES:
        if rid == robot_id:
            return _fallback("/api/fleet/{robot_id}", _mock_robot(rid, name, rtype))
    raise HTTPException(status_code=404, detail=f"robot {robot_id} not found")


@app.get("/api/alerts", response_model=list[Alert])
async def get_alerts():
    """Return active alerts."""
    data = await _proxy_get(settings.telemetry_service_url, "/alerts")
    if data is not None:
        return data
    return _fallback("/api/alerts", _mock_alerts())


@app.get("/api/metrics/{robot_id}", response_model=MetricsHistory, responses=_NOT_FOUND)
async def get_metrics(robot_id: str):
    """Return metrics history for a robot."""
    data = await _proxy_get(settings.telemetry_service_url, f"/metrics/{robot_id}")
    if data is not None:
        return data
    return _fallback("/api/metrics/{robot_id}", _mock_metrics(robot_id))


# ---------------------------------------------------------------------------
# LiDAR REST
# ---------------------------------------------------------------------------


@app.get("/api/lidar/{robot_id}/scan", response_model=LidarScan, responses=_NOT_FOUND)
async def get_lidar_scan(robot_id: str):
    """Return the latest LiDAR scan."""
    data = await _proxy_get(settings.lidar_service_url, f"/scan/{robot_id}")
    if data is not None:
        return data
    return _fallback("/api/lidar/{robot_id}/scan", _mock_lidar_scan(robot_id))


# ---------------------------------------------------------------------------
# RAG REST
# ---------------------------------------------------------------------------


@app.post("/api/rag/query", response_model=RAGResponse)
async def rag_query(body: RAGQuery):
    """Query the RAG system."""
    data = await _proxy_post(settings.rag_service_url, "/query", body.model_dump())
    if data is not None:
        return data
    return _fallback("/api/rag/query", _mock_rag_response(body.query, body.robot_id))


# ---------------------------------------------------------------------------
# WebSocket streams
# ---------------------------------------------------------------------------


class _ClientGone(Exception):
    """The browser side of the socket is closed (as opposed to the upstream side)."""


async def _send_to_client(ws: WebSocket, text: str) -> None:
    try:
        await ws.send_text(text)
    # Which exception a departed browser produces depends on the layer: Starlette raises
    # WebSocketDisconnect/RuntimeError, but uvicorn's websockets transport lets
    # ConnectionClosed (e.g. ConnectionClosedOK, code 1000) escape from send().
    except (WebSocketDisconnect, RuntimeError, ConnectionClosed) as exc:
        raise _ClientGone from exc


def _ws_url(http_url: str, path: str) -> str:
    return http_url.replace("http://", "ws://").replace("https://", "wss://") + path


async def _relay_with_fallback(
    ws: WebSocket, upstream_url: str, mock_frame, period: float, label: str
):
    """
    Forward frames from the upstream service to the browser.

    If the upstream is unreachable *or drops mid-stream*, keep the browser fed with mock
    frames (flagged ``demo``) and retry the upstream every few seconds, so the stream heals
    itself without the browser having to reconnect.  Returns when the browser goes away.
    """
    loop = asyncio.get_running_loop()
    while True:
        try:
            async with websockets.connect(
                upstream_url,
                extra_headers={REQUEST_ID_HEADER: request_id_var.get()},
                open_timeout=3,
            ) as upstream:
                logger.info("Connected to upstream %s stream", label)
                async for message in upstream:
                    await _send_to_client(
                        ws, message if isinstance(message, str) else message.decode()
                    )
            logger.warning("Upstream %s stream closed; serving mock data until it returns", label)
        except _ClientGone:
            return
        except Exception as exc:
            logger.warning("Upstream %s stream unavailable (%s); serving mock data", label, exc)

        mock_fallbacks.labels(f"ws:{label}").inc()
        # Jittered so many gateways don't re-dial a recovering service in lockstep.
        retry_at = loop.time() + settings.ws_reconnect_delay * random.uniform(0.75, 1.5)
        try:
            while loop.time() < retry_at:
                await _send_to_client(ws, json.dumps(mock_frame()))
                await asyncio.sleep(period)
        except _ClientGone:
            return


@app.websocket("/api/ws/telemetry")
async def ws_telemetry(ws: WebSocket):
    """Stream telemetry for all robots at ~2 Hz (live, or mock while the service is down)."""
    await ws.accept()
    logger.info("Telemetry WS client connected")
    await _relay_with_fallback(
        ws,
        _ws_url(settings.telemetry_service_url, "/ws/telemetry"),
        _mock_telemetry,
        0.5,
        "telemetry",
    )
    logger.info("Telemetry WS client disconnected")


@app.websocket("/api/ws/lidar/{robot_id}")
async def ws_lidar(ws: WebSocket, robot_id: str):
    """Stream LiDAR point clouds for a robot at ~5 Hz (live, or mock while the service is down)."""
    await ws.accept()
    logger.info("LiDAR WS client connected for %s", robot_id)
    await _relay_with_fallback(
        ws,
        _ws_url(settings.lidar_service_url, f"/ws/lidar/{robot_id}"),
        lambda: _mock_lidar_scan(robot_id),
        0.2,
        "lidar",
    )
    logger.info("LiDAR WS client disconnected for %s", robot_id)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level="info",
    )
