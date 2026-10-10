"""
NeuraFleet Telemetry Service
=============================

FastAPI microservice that aggregates a fleet of robots over MQTT (plan-messaging.md
Phase A) and serves REST + WebSocket endpoints for robot states, alerts, and metrics
history. The robots themselves are simulated independently by ``robot_agent``; this
service's job is to subscribe, detect anomalies, raise stateful alerts, and answer
queries - it does not move anything itself any more.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from mqtt_bridge import MqttBridge
from robot_simulator import FleetAggregator
from websockets.exceptions import ConnectionClosed

from shared.models import Alert, ErrorDetail, MetricsHistory, RobotState, TelemetryHealth
from shared.observability import install_observability

logger = logging.getLogger("telemetry_service")

MQTT_HOST = os.getenv("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
# How often to age connectivity forward and re-check fleet-wide alerts (e.g. collision
# proximity, which depends on robots that didn't just report). Independent of the 10 Hz
# telemetry rate - this is the "is anyone still talking to us at all" heartbeat.
STALENESS_CHECK_INTERVAL = 1.0

# ---------------------------------------------------------------------------
# Aggregator + MQTT bridge
# ---------------------------------------------------------------------------

fleet_sim: FleetAggregator | None = None
mqtt_bridge: MqttBridge | None = None
_mqtt_task: asyncio.Task | None = None
_staleness_task: asyncio.Task | None = None


async def _staleness_loop() -> None:
    """Ages robots online -> stale -> offline when their telemetry stops arriving, and
    re-runs alert detection for the whole fleet on a fixed cadence (not just when a
    specific robot's message arrives - see FleetAggregator.check_staleness)."""
    assert fleet_sim is not None
    while True:
        try:
            fleet_sim.check_staleness()
        except Exception:
            logger.exception("Staleness check error")
        await asyncio.sleep(STALENESS_CHECK_INTERVAL)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    global fleet_sim, mqtt_bridge, _mqtt_task, _staleness_task
    fleet_sim = FleetAggregator()
    logger.info("Fleet aggregator initialised for %d robots", len(fleet_sim.robots))
    mqtt_bridge = MqttBridge(fleet_sim, MQTT_HOST, MQTT_PORT)
    _mqtt_task = asyncio.create_task(mqtt_bridge.run())
    _staleness_task = asyncio.create_task(_staleness_loop())
    yield
    # Shutdown
    for task in (_mqtt_task, _staleness_task):
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    logger.info("Telemetry Service shut down")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="NeuraFleet Telemetry Service",
    version="1.0.0",
    lifespan=lifespan,
)
obs = install_observability(app, "telemetry")
active_alerts = obs.gauge("telemetry_active_alerts", "Alerts currently raised")
active_alerts.set_function(lambda: len(fleet_sim.get_alerts()) if fleet_sim else 0)
robots_online = obs.gauge("telemetry_robots_online", "Robots whose telemetry is currently arriving")
robots_online.set_function(lambda: fleet_sim.connectivity_counts()["online"] if fleet_sim else 0)
mqtt_connected = obs.gauge("telemetry_mqtt_connected", "1 if the MQTT broker link is up")
mqtt_connected.set_function(lambda: int(bool(mqtt_bridge and mqtt_bridge.connected)))

_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorDetail},
    503: {"model": ErrorDetail},
}


@app.get("/health", response_model=TelemetryHealth)
async def health():
    """Liveness/observability. Always HTTP 200 (this is the k8s probe target, and a
    restart would not fix an MQTT outage) - ``status`` in the body says "degraded"
    instead when no robot's telemetry is currently arriving, for direct observability
    (``curl .../health``, dashboards). This deliberately does NOT drive the gateway's
    DEMO DATA badge: robots being offline is real, honest data (see each robot's own
    ``connectivity`` field and shared.models.Connectivity), not fabricated data, so it
    must not look the same as telemetry-service being unreachable."""
    if fleet_sim is None:
        return {"status": "starting"}
    counts = fleet_sim.connectivity_counts()
    degraded = counts["online"] == 0
    return {
        "status": "degraded" if degraded else "ok",
        "robots": len(fleet_sim.robots),
        "alerts": len(fleet_sim.get_alerts()),
        "mqtt": "connected" if (mqtt_bridge and mqtt_bridge.connected) else "disconnected",
        "robots_online": counts["online"],
        "robots_stale": counts["stale"],
        "robots_offline": counts["offline"],
    }


# ---------------------------------------------------------------------------
# REST: Fleet state
# ---------------------------------------------------------------------------


@app.get("/fleet", response_model=list[RobotState])
async def get_fleet():
    """Return full state for all robots."""
    if fleet_sim is None:
        return []
    return fleet_sim.get_all_robots()


@app.get("/fleet/{robot_id}", response_model=RobotState, responses=_NOT_FOUND)
async def get_robot(robot_id: str):
    """Return state for a single robot."""
    if fleet_sim is None:
        raise HTTPException(status_code=503, detail="simulator not ready")
    data = fleet_sim.get_robot(robot_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"robot {robot_id} not found")
    return data


# ---------------------------------------------------------------------------
# REST: Alerts
# ---------------------------------------------------------------------------


@app.get("/alerts", response_model=list[Alert])
async def get_alerts():
    """Return active alerts."""
    if fleet_sim is None:
        return []
    return fleet_sim.get_alerts()


@app.get("/alerts/resolved", response_model=list[Alert])
async def get_resolved_alerts():
    """Return recently cleared alerts (bounded history)."""
    if fleet_sim is None:
        return []
    return fleet_sim.get_resolved_alerts()


# ---------------------------------------------------------------------------
# REST: Metrics history
# ---------------------------------------------------------------------------


@app.get("/metrics/{robot_id}", response_model=MetricsHistory, responses=_NOT_FOUND)
async def get_metrics(robot_id: str):
    """Return up to 100 historical metric points for a robot."""
    if fleet_sim is None:
        raise HTTPException(status_code=503, detail="simulator not ready")
    data = fleet_sim.get_metrics(robot_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"robot {robot_id} not found")
    return data


# ---------------------------------------------------------------------------
# WebSocket: Telemetry stream (~2 Hz)
# ---------------------------------------------------------------------------


@app.websocket("/ws/telemetry")
async def ws_telemetry(ws: WebSocket):
    """Stream telemetry for all robots at approximately 2 Hz."""
    await ws.accept()
    logger.info("Telemetry WS client connected")
    try:
        while True:
            if fleet_sim is not None:
                payload = json.dumps(fleet_sim.get_telemetry())
                await ws.send_text(payload)
            await asyncio.sleep(0.5)  # 2 Hz
    except (WebSocketDisconnect, ConnectionClosed):
        # A viewer leaving is normal; uvicorn's websockets transport reports it as
        # ConnectionClosed(OK) from send(), not as Starlette's WebSocketDisconnect.
        logger.info("Telemetry WS client disconnected")
    except Exception as exc:
        logger.error("Telemetry WS error: %s", exc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8002,
        reload=False,
        log_level="info",
    )
