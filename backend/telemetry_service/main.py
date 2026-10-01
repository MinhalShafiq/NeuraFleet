"""
NeuraFleet Telemetry Service
=============================

FastAPI microservice that runs a fleet simulation, streams telemetry
over WebSocket at ~2 Hz, and exposes REST endpoints for robot states,
alerts, and metrics history.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect

from robot_simulator import FleetSimulator

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("telemetry_service")

# ---------------------------------------------------------------------------
# Simulation instance
# ---------------------------------------------------------------------------

fleet_sim: FleetSimulator | None = None
_sim_task: asyncio.Task | None = None


async def _simulation_loop() -> None:
    """Background coroutine that ticks the simulation at 10 Hz."""
    assert fleet_sim is not None
    dt = 0.1  # 10 Hz
    logger.info("Simulation loop started (dt=%.2f s)", dt)
    while True:
        try:
            fleet_sim.update(dt)
        except Exception:
            logger.exception("Simulation tick error")
        await asyncio.sleep(dt)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global fleet_sim, _sim_task
    fleet_sim = FleetSimulator()
    logger.info(
        "Fleet simulator initialised with %d robots", len(fleet_sim.robots)
    )
    _sim_task = asyncio.create_task(_simulation_loop())
    yield
    # Shutdown
    if _sim_task:
        _sim_task.cancel()
        try:
            await _sim_task
        except asyncio.CancelledError:
            pass
    logger.info("Telemetry Service shut down")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="NeuraFleet Telemetry Service",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    if fleet_sim is None:
        return {"status": "starting"}
    return {
        "status": "ok",
        "robots": len(fleet_sim.robots),
        "alerts": len(fleet_sim.get_alerts()),
    }


# ---------------------------------------------------------------------------
# REST: Fleet state
# ---------------------------------------------------------------------------

@app.get("/fleet")
async def get_fleet():
    """Return full state for all robots."""
    if fleet_sim is None:
        return []
    return fleet_sim.get_all_robots()


@app.get("/fleet/{robot_id}")
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

@app.get("/alerts")
async def get_alerts():
    """Return active alerts."""
    if fleet_sim is None:
        return []
    return fleet_sim.get_alerts()


@app.get("/alerts/resolved")
async def get_resolved_alerts():
    """Return recently cleared alerts (bounded history)."""
    if fleet_sim is None:
        return []
    return fleet_sim.get_resolved_alerts()


# ---------------------------------------------------------------------------
# REST: Metrics history
# ---------------------------------------------------------------------------

@app.get("/metrics/{robot_id}")
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
    except WebSocketDisconnect:
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
