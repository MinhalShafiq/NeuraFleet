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
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Dict, List

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from config import settings

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
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


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mock_robot(rid: str, name: str, rtype: str) -> dict:
    """Generate a single mock robot state."""
    t = time.time()
    return {
        "robot_id": rid,
        "name": name,
        "robot_type": rtype,
        "timestamp": _ts(),
        "position": {
            "x": 50 + 30 * math.sin(t * 0.1 + hash(rid) % 100),
            "y": 50 + 30 * math.cos(t * 0.13 + hash(rid) % 100),
            "z": 0.0,
        },
        "velocity": {
            "vx": random.uniform(-1, 1),
            "vy": random.uniform(-1, 1),
            "vz": 0.0,
        },
        "battery": max(10.0, 85.0 + 10 * math.sin(t * 0.005 + hash(rid))),
        "status": "active",
        "cpu_usage": 30 + 20 * abs(math.sin(t * 0.07 + hash(rid))),
        "memory_usage": 40 + 15 * abs(math.cos(t * 0.05 + hash(rid))),
        "temperature": 45 + 10 * abs(math.sin(t * 0.03 + hash(rid))),
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
            alerts.append({
                "id": str(uuid.uuid4()),
                "robot_id": rid,
                "type": atype,
                "severity": sev,
                "message": msg,
                "timestamp": _ts(),
            })
    return alerts


def _mock_metrics(robot_id: str) -> dict:
    """Generate 100 mock metric points for the robot."""
    now = time.time()
    points = []
    for i in range(100):
        t = now - (99 - i) * 2  # 2 s intervals
        points.append({
            "timestamp": datetime.fromtimestamp(t, tz=timezone.utc).isoformat(),
            "battery": max(10, 85 + 10 * math.sin(t * 0.005 + hash(robot_id))),
            "cpu_usage": 30 + 20 * abs(math.sin(t * 0.07 + hash(robot_id))),
            "memory_usage": 40 + 15 * abs(math.cos(t * 0.05 + hash(robot_id))),
            "temperature": 45 + 10 * abs(math.sin(t * 0.03 + hash(robot_id))),
            "velocity_magnitude": abs(random.gauss(1.0, 0.3)),
        })
    return {"robot_id": robot_id, "points": points}


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


async def _proxy_get(base_url: str, path: str) -> dict | list | None:
    """Attempt a GET request to an internal service; return None on failure."""
    try:
        client = await _get_client()
        resp = await client.get(f"{base_url}{path}")
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("Upstream GET %s%s failed: %s", base_url, path, exc)
        return None


async def _proxy_post(base_url: str, path: str, body: dict) -> dict | None:
    """Attempt a POST request to an internal service; return None on failure."""
    try:
        client = await _get_client()
        resp = await client.post(f"{base_url}{path}", json=body)
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
async def liveness():
    """Cheap liveness/readiness probe target (no downstream calls)."""
    return {"status": "ok"}


async def _probe(name: str, url: str) -> tuple:
    try:
        client = await _get_client()
        r = await client.get(f"{url}/health", timeout=_HEALTH_PROBE_TIMEOUT)
        return name, ("healthy" if r.status_code == 200 else "degraded")
    except Exception:
        return name, "unreachable"


@app.get("/api/health")
async def health():
    """
    Observability endpoint: gateway status plus each downstream service.

    Probes run concurrently, so the worst case is one timeout (2 s), not the sum
    of three.  This is NOT the k8s probe target (that is ``/health``): the gateway
    serves mock data when a service is down, so a sick dependency must not pull
    gateway pods out of rotation.
    """
    results = await asyncio.gather(
        _probe("telemetry", settings.telemetry_service_url),
        _probe("lidar", settings.lidar_service_url),
        _probe("rag", settings.rag_service_url),
    )
    return {"status": "ok", "services": dict(results)}


# ---------------------------------------------------------------------------
# Fleet / Telemetry REST
# ---------------------------------------------------------------------------

@app.get("/api/fleet")
async def get_fleet():
    """Return all robot states."""
    data = await _proxy_get(settings.telemetry_service_url, "/fleet")
    if data is not None:
        return data
    logger.info("Using mock fleet data (telemetry service unreachable)")
    return _mock_fleet()


@app.get("/api/fleet/{robot_id}")
async def get_robot(robot_id: str):
    """Return a specific robot's state."""
    data = await _proxy_get(settings.telemetry_service_url, f"/fleet/{robot_id}")
    if data is not None:
        return data
    # Fallback: find mock robot
    for rid, name, rtype in _ROBOT_NAMES:
        if rid == robot_id:
            return _mock_robot(rid, name, rtype)
    raise HTTPException(status_code=404, detail=f"robot {robot_id} not found")


@app.get("/api/alerts")
async def get_alerts():
    """Return active alerts."""
    data = await _proxy_get(settings.telemetry_service_url, "/alerts")
    if data is not None:
        return data
    return _mock_alerts()


@app.get("/api/metrics/{robot_id}")
async def get_metrics(robot_id: str):
    """Return metrics history for a robot."""
    data = await _proxy_get(settings.telemetry_service_url, f"/metrics/{robot_id}")
    if data is not None:
        return data
    return _mock_metrics(robot_id)


# ---------------------------------------------------------------------------
# LiDAR REST
# ---------------------------------------------------------------------------

@app.get("/api/lidar/{robot_id}/scan")
async def get_lidar_scan(robot_id: str):
    """Return the latest LiDAR scan."""
    data = await _proxy_get(settings.lidar_service_url, f"/scan/{robot_id}")
    if data is not None:
        return data
    return _mock_lidar_scan(robot_id)


# ---------------------------------------------------------------------------
# RAG REST
# ---------------------------------------------------------------------------

@app.post("/api/rag/query")
async def rag_query(body: dict):
    """Query the RAG system."""
    data = await _proxy_post(settings.rag_service_url, "/query", body)
    if data is not None:
        return data
    return _mock_rag_response(body.get("query", ""), body.get("robot_id"))


# ---------------------------------------------------------------------------
# WebSocket: Telemetry stream
# ---------------------------------------------------------------------------

@app.websocket("/api/ws/telemetry")
async def ws_telemetry(ws: WebSocket):
    """
    Stream telemetry for all robots at ~2 Hz.
    Attempts to connect to the telemetry service's internal WS; falls back
    to locally generated mock data if the service is unavailable.
    """
    await ws.accept()
    logger.info("Telemetry WS client connected")

    # Try to proxy from upstream
    upstream_url = (
        settings.telemetry_service_url
        .replace("http://", "ws://")
        .replace("https://", "wss://")
        + "/ws/telemetry"
    )

    try:
        import websockets as _ws_lib

        async with _ws_lib.connect(upstream_url) as upstream:
            logger.info("Connected to upstream telemetry WS")
            try:
                async for message in upstream:
                    await ws.send_text(message)
            except WebSocketDisconnect:
                logger.info("Telemetry WS client disconnected (upstream mode)")
            except Exception as exc:
                logger.warning("Upstream telemetry WS error: %s", exc)
    except Exception as exc:
        logger.info(
            "Cannot reach upstream telemetry WS (%s). Streaming mock data.", exc
        )
        # Fallback: stream mock data at ~2 Hz
        try:
            while True:
                payload = json.dumps(_mock_telemetry())
                await ws.send_text(payload)
                await asyncio.sleep(0.5)
        except WebSocketDisconnect:
            logger.info("Telemetry WS client disconnected (mock mode)")
        except Exception as exc:
            logger.warning("Telemetry WS mock stream error: %s", exc)


# ---------------------------------------------------------------------------
# WebSocket: LiDAR stream
# ---------------------------------------------------------------------------

@app.websocket("/api/ws/lidar/{robot_id}")
async def ws_lidar(ws: WebSocket, robot_id: str):
    """
    Stream LiDAR point clouds for a given robot at ~5 Hz.
    Falls back to mock data when the LiDAR service is unreachable.
    """
    await ws.accept()
    logger.info("LiDAR WS client connected for %s", robot_id)

    upstream_url = (
        settings.lidar_service_url
        .replace("http://", "ws://")
        .replace("https://", "wss://")
        + f"/ws/lidar/{robot_id}"
    )

    try:
        import websockets as _ws_lib

        async with _ws_lib.connect(upstream_url) as upstream:
            logger.info("Connected to upstream LiDAR WS for %s", robot_id)
            try:
                async for message in upstream:
                    await ws.send_text(message)
            except WebSocketDisconnect:
                logger.info("LiDAR WS client disconnected (upstream, %s)", robot_id)
            except Exception as exc:
                logger.warning("Upstream LiDAR WS error: %s", exc)
    except Exception as exc:
        logger.info(
            "Cannot reach upstream LiDAR WS for %s (%s). Streaming mock data.",
            robot_id,
            exc,
        )
        try:
            while True:
                payload = json.dumps(_mock_lidar_scan(robot_id))
                await ws.send_text(payload)
                await asyncio.sleep(0.2)
        except WebSocketDisconnect:
            logger.info("LiDAR WS client disconnected (mock, %s)", robot_id)
        except Exception as exc:
            logger.warning("LiDAR WS mock stream error for %s: %s", robot_id, exc)


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
