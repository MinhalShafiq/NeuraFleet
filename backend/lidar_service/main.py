"""
NeuraFleet LiDAR Service
========================

FastAPI microservice that manages per-robot LiDAR simulators, streams
point cloud data over WebSocket, and provides REST endpoints for
on-demand scans and motion-compensated processing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Dict

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from lidar_simulator import Environment, LidarSimulator
from motion_compensation import MotionCompensator

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("lidar_service")

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

# Shared persistent environment across all simulators
_environment = Environment()

# Per-robot simulators and positions
_simulators: Dict[str, LidarSimulator] = {}
_robot_positions: Dict[str, tuple] = {}   # (x, y, z)
_robot_headings: Dict[str, float] = {}    # radians
_frame_counters: Dict[str, int] = {}

_compensator = MotionCompensator()

# Default robot IDs to pre-initialise
_DEFAULT_ROBOTS = [
    "robot-001", "robot-002", "robot-003",
    "robot-004", "robot-005", "robot-006",
]


def _init_robot(robot_id: str) -> None:
    """Lazily initialise a LiDAR simulator for a robot."""
    if robot_id not in _simulators:
        _simulators[robot_id] = LidarSimulator(environment=_environment)
        rng = random.Random(hash(robot_id))
        _robot_positions[robot_id] = (
            rng.uniform(20, 80),
            rng.uniform(20, 80),
            0.0,
        )
        _robot_headings[robot_id] = rng.uniform(0, 2 * math.pi)
        _frame_counters[robot_id] = 0
        logger.info("Initialised LiDAR simulator for %s", robot_id)


def _update_robot_position(robot_id: str, dt: float = 0.2) -> None:
    """Advance the simulated robot position (random walk with momentum)."""
    x, y, z = _robot_positions[robot_id]
    heading = _robot_headings[robot_id]

    speed = 1.5  # m/s
    heading += random.gauss(0, 0.15)  # gentle heading drift
    x += speed * math.cos(heading) * dt
    y += speed * math.sin(heading) * dt

    # Clamp to environment boundaries with margin
    x = max(5, min(_environment.area_size - 5, x))
    y = max(5, min(_environment.area_size - 5, y))

    _robot_positions[robot_id] = (x, y, z)
    _robot_headings[robot_id] = heading


def _generate_scan(robot_id: str) -> dict:
    """Generate a scan, update position, and return a JSON-friendly dict."""
    _init_robot(robot_id)
    _update_robot_position(robot_id)

    sim = _simulators[robot_id]
    pos = _robot_positions[robot_id]
    heading = _robot_headings[robot_id]

    raw_points = sim.generate_scan(pos, heading)
    _frame_counters[robot_id] = _frame_counters.get(robot_id, 0) + 1

    return _scan_to_dict(robot_id, raw_points)


def _scan_to_dict(
    robot_id: str,
    points: np.ndarray,
    max_stream_points: int = 2000,
) -> dict:
    """Convert a numpy scan to a JSON-serialisable dict (downsampled)."""
    n = points.shape[0]

    # Downsample for streaming efficiency
    if n > max_stream_points:
        indices = np.random.choice(n, max_stream_points, replace=False)
        pts = points[indices]
    else:
        pts = points

    return {
        "robot_id": robot_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "points": np.round(pts, 3).tolist(),
        "frame_id": _frame_counters.get(robot_id, 0),
        "num_points": int(pts.shape[0]),
    }


# ---------------------------------------------------------------------------
# Application lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("LiDAR Service starting, pre-initialising %d robots", len(_DEFAULT_ROBOTS))
    for rid in _DEFAULT_ROBOTS:
        _init_robot(rid)
    yield
    logger.info("LiDAR Service shutting down")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="NeuraFleet LiDAR Service",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "robots": list(_simulators.keys()),
        "environment_obstacles": (
            len(_environment.boxes)
            + len(_environment.cylinders)
            + len(_environment.walls)
        ),
    }


# ---------------------------------------------------------------------------
# REST: latest scan
# ---------------------------------------------------------------------------

@app.get("/scan/{robot_id}")
async def get_scan(robot_id: str):
    """Return the latest LiDAR scan for a robot (full resolution)."""
    _init_robot(robot_id)
    _update_robot_position(robot_id)

    sim = _simulators[robot_id]
    pos = _robot_positions[robot_id]
    heading = _robot_headings[robot_id]

    raw_points = sim.generate_scan(pos, heading)
    _frame_counters[robot_id] = _frame_counters.get(robot_id, 0) + 1

    # Return full scan (not downsampled) for REST endpoint
    return _scan_to_dict(robot_id, raw_points, max_stream_points=15000)


# ---------------------------------------------------------------------------
# REST: motion-compensated processing
# ---------------------------------------------------------------------------

class ProcessRequest(BaseModel):
    robot_id: str
    imu_data: dict
    scan_duration: float = 0.1


@app.post("/process")
async def process_scan(req: ProcessRequest):
    """
    Generate a scan and apply motion compensation using the provided
    IMU data.  Returns the de-skewed point cloud.
    """
    _init_robot(req.robot_id)
    _update_robot_position(req.robot_id)

    sim = _simulators[req.robot_id]
    pos = _robot_positions[req.robot_id]
    heading = _robot_headings[req.robot_id]

    raw = sim.generate_scan(pos, heading)
    _frame_counters[req.robot_id] = _frame_counters.get(req.robot_id, 0) + 1

    compensated = _compensator.compensate_vectorized(
        raw, req.imu_data, req.scan_duration
    )

    return _scan_to_dict(req.robot_id, compensated)


# ---------------------------------------------------------------------------
# WebSocket: LiDAR stream (~5 Hz)
# ---------------------------------------------------------------------------

@app.websocket("/ws/lidar/{robot_id}")
async def ws_lidar_stream(ws: WebSocket, robot_id: str):
    """Stream downsampled LiDAR scans at approximately 5 Hz."""
    await ws.accept()
    logger.info("LiDAR WS client connected for %s", robot_id)
    _init_robot(robot_id)

    try:
        while True:
            scan = _generate_scan(robot_id)
            await ws.send_text(json.dumps(scan))
            await asyncio.sleep(0.2)  # 5 Hz
    except WebSocketDisconnect:
        logger.info("LiDAR WS client disconnected for %s", robot_id)
    except Exception as exc:
        logger.error("LiDAR WS error for %s: %s", robot_id, exc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8001,
        reload=False,
        log_level="info",
    )
