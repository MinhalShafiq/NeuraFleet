"""
NeuraFleet LiDAR Service
========================

FastAPI microservice that manages per-robot LiDAR simulators, streams
point cloud data over WebSocket, and provides REST endpoints for
on-demand scans and motion-compensated processing.
"""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import math
import random
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from lidar_simulator import Environment, LidarSimulator
from motion_compensation import MotionCompensator
from pydantic import BaseModel

from shared.models import ErrorDetail, LidarHealth, LidarScan
from shared.observability import install_observability

logger = logging.getLogger("lidar_service")

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

# Shared persistent environment across all simulators
_environment = Environment()

# Per-robot simulators and positions
_simulators: dict[str, LidarSimulator] = {}
_robot_positions: dict[str, tuple] = {}  # (x, y, z)
_robot_headings: dict[str, float] = {}  # radians
_frame_counters: dict[str, int] = {}

_compensator = MotionCompensator()

# Default robot IDs to pre-initialise
_DEFAULT_ROBOTS = [
    "robot-001",
    "robot-002",
    "robot-003",
    "robot-004",
    "robot-005",
    "robot-006",
]


def _require_known(robot_id: str) -> None:
    """Only the fleet's robots have a simulator.  Without this, any URL (/scan/<anything>)
    allocated a new simulator that was never freed - an unbounded-memory hole."""
    if robot_id not in _DEFAULT_ROBOTS:
        raise HTTPException(status_code=404, detail=f"robot {robot_id} not found")


def _init_robot(robot_id: str) -> None:
    """Lazily initialise a LiDAR simulator for a robot."""
    if robot_id not in _simulators:
        _simulators[robot_id] = LidarSimulator(environment=_environment)
        rng = random.Random(zlib.crc32(robot_id.encode()))  # hash(str) varies per process
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


_scan_locks: dict[str, asyncio.Lock] = {}

# All CPU-bound work (ray-casting, JSON encoding) runs on ONE dedicated thread.
# numpy and json.dumps hold the GIL for long stretches; with a wide thread pool
# several of them queue up and the event-loop thread starves behind the lot
# (measured: 100-150 ms stalls with the default pool, vs. one scan's worth here).
_cpu_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lidar-cpu")


async def _cpu(fn, *args):
    return await asyncio.get_running_loop().run_in_executor(_cpu_pool, fn, *args)


async def _scan_async(robot_id: str, max_stream_points: int = 2000) -> str:
    """
    Advance the robot, ray-cast on a worker thread, and return the scan as JSON text.

    ``generate_scan`` is CPU-bound numpy; running it inline would stall the event
    loop (and every other client/health probe) for its whole duration.  A per-robot
    lock serialises scans of the same simulator (its noise generator isn't thread-safe).
    """
    _init_robot(robot_id)
    lock = _scan_locks.setdefault(robot_id, asyncio.Lock())
    async with lock:
        _update_robot_position(robot_id)
        sim = _simulators[robot_id]
        pos = _robot_positions[robot_id]
        heading = _robot_headings[robot_id]
        with scan_seconds.time():
            raw_points = await _cpu(sim.generate_scan, pos, heading)
        frames_total.inc()
        _frame_counters[robot_id] = _frame_counters.get(robot_id, 0) + 1
        frame_id = _frame_counters[robot_id]
    # Serialising ~10k points costs tens of ms (FastAPI's jsonable_encoder alone is
    # ~60 ms for a full scan); do it off the loop and hand back ready-made text.
    return await _cpu(_scan_json, robot_id, raw_points, max_stream_points, frame_id)


def _scan_json(robot_id: str, points: np.ndarray, max_stream_points: int, frame_id: int) -> str:
    """
    Serialise a scan.  ``json.dumps`` holds the GIL for its whole C call (~30 ms for
    a full scan), so the points are encoded in slices with a ``sleep(0)`` between
    them to let the event-loop thread run.  Output is identical to a single dumps.
    """
    d = _scan_to_dict(robot_id, points, max_stream_points, frame_id)
    pts = d.pop("points")
    parts = []
    for i in range(0, len(pts), 1000):
        parts.append(json.dumps(pts[i : i + 1000])[1:-1])
        time.sleep(0)  # release the GIL
    head = json.dumps(d)
    return head[:-1] + ', "points": [' + ", ".join(parts) + "]}"


def _scan_to_dict(
    robot_id: str,
    points: np.ndarray,
    max_stream_points: int = 2000,
    frame_id: int | None = None,
) -> dict:
    """Convert a numpy scan to a JSON-serialisable dict (downsampled)."""
    n = points.shape[0]

    # Downsample for streaming efficiency.  Evenly strided (not random) so the
    # azimuthal ordering that MotionCompensator's per-point timestamps rely on is kept.
    if n > max_stream_points:
        indices = np.linspace(0, n - 1, max_stream_points).astype(np.intp)
        pts = points[indices]
    else:
        pts = points

    return {
        "robot_id": robot_id,
        "timestamp": datetime.now(UTC).isoformat(),
        # Round in float64: rounding float32 and converting with tolist() yields reprs like
        # 56.43299865722656 (81 B/point instead of 32), which bloats the frame 2.5x.
        "points": np.round(pts.astype(np.float64), 3).tolist(),
        "frame_id": _frame_counters.get(robot_id, 0) if frame_id is None else frame_id,
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
    # Everything allocated so far (FastAPI, numpy, the environment) lives for the whole
    # process.  Freezing it keeps full (gen-2) collections from re-traversing ~70k objects:
    # those passes measured 25-55 ms, i.e. a quarter of a frame period of loop stall.
    gc.collect()
    gc.freeze()
    yield
    await hub.shutdown()
    logger.info("LiDAR Service shutting down")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="NeuraFleet LiDAR Service",
    version="1.0.0",
    lifespan=lifespan,
)
obs = install_observability(app, "lidar")
scan_seconds = obs.histogram(
    "lidar_scan_seconds",
    "Wall time to ray-cast one scan on the worker thread (includes queueing)",
    (0.005, 0.01, 0.02, 0.05, 0.1, 0.25, 0.5, 1),
)
frames_total = obs.counter("lidar_frames_total", "Scans generated")

_NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"model": ErrorDetail}}


@app.get("/health", response_model=LidarHealth)
async def health():
    return {
        "status": "ok",
        "robots": list(_simulators.keys()),
        "environment_obstacles": (
            len(_environment.boxes) + len(_environment.cylinders) + len(_environment.walls)
        ),
    }


# ---------------------------------------------------------------------------
# REST: latest scan
# ---------------------------------------------------------------------------


@app.get(
    "/scan/{robot_id}",
    # Documented as LidarScan but returned as a pre-encoded Response: validating and
    # re-encoding ~10k points would put ~60 ms of CPU back on the event loop.
    response_model=None,
    responses={200: {"model": LidarScan}, **_NOT_FOUND},
)
async def get_scan(robot_id: str):
    """Return the latest LiDAR scan for a robot (full resolution)."""
    _require_known(robot_id)
    text = await _scan_async(robot_id, max_stream_points=15000)
    return Response(content=text, media_type="application/json")


# ---------------------------------------------------------------------------
# REST: motion-compensated processing
# ---------------------------------------------------------------------------


class ProcessRequest(BaseModel):
    robot_id: str
    imu_data: dict
    scan_duration: float = 0.1


@app.post(
    "/process",
    response_model=None,
    responses={200: {"model": LidarScan}, **_NOT_FOUND},
)
async def process_scan(req: ProcessRequest):
    """
    Generate a scan and apply motion compensation using the provided
    IMU data.  Returns the de-skewed point cloud.
    """
    _require_known(req.robot_id)
    _init_robot(req.robot_id)
    lock = _scan_locks.setdefault(req.robot_id, asyncio.Lock())
    async with lock:
        _update_robot_position(req.robot_id)
        sim = _simulators[req.robot_id]
        pos = _robot_positions[req.robot_id]
        heading = _robot_headings[req.robot_id]

        def work():
            raw = sim.generate_scan(pos, heading)
            return _compensator.compensate(raw, req.imu_data, req.scan_duration)

        compensated = await _cpu(work)
        _frame_counters[req.robot_id] = _frame_counters.get(req.robot_id, 0) + 1
        frame_id = _frame_counters[req.robot_id]

    text = await _cpu(_scan_json, req.robot_id, compensated, 2000, frame_id)
    return Response(content=text, media_type="application/json")


# ---------------------------------------------------------------------------
# WebSocket: LiDAR stream (~5 Hz)
# ---------------------------------------------------------------------------


class ScanHub:
    """
    One producer task per robot, fanned out to any number of subscribers.

    Previously every WebSocket client ray-cast its own scan of the same robot, so
    N viewers cost N times the CPU *and* advanced the robot N times per tick.  Now
    the producer runs at 5 Hz while at least one client is connected, serialises the
    frame to JSON once, and hands the same string to every subscriber.  Each
    subscriber has a 1-slot queue: a slow client drops stale frames, it never
    delays the producer or other clients.
    """

    PERIOD = 0.2  # 5 Hz

    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    def subscribe(self, robot_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1)
        self._subs.setdefault(robot_id, set()).add(q)
        task = self._tasks.get(robot_id)
        if task is None or task.done():
            self._tasks[robot_id] = asyncio.create_task(self._produce(robot_id))
        return q

    def unsubscribe(self, robot_id: str, q: asyncio.Queue) -> None:
        subs = self._subs.get(robot_id)
        if subs is not None:
            subs.discard(q)
            if not subs:
                del self._subs[robot_id]
                task = self._tasks.pop(robot_id, None)
                if task:
                    task.cancel()

    async def _produce(self, robot_id: str) -> None:
        next_tick = time.monotonic()
        while True:
            try:
                frame = await _scan_async(robot_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("LiDAR producer error for %s", robot_id)
                frame = None
            if frame is not None:
                for q in tuple(self._subs.get(robot_id, ())):
                    if q.full():
                        q.get_nowait()  # drop the stale frame for a slow client
                    q.put_nowait(frame)
            next_tick += self.PERIOD
            await asyncio.sleep(max(0.0, next_tick - time.monotonic()))

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._subs.clear()


hub = ScanHub()


@app.websocket("/ws/lidar/{robot_id}")
async def ws_lidar_stream(ws: WebSocket, robot_id: str):
    """Stream downsampled LiDAR scans at approximately 5 Hz."""
    if robot_id not in _DEFAULT_ROBOTS:
        await ws.close(code=4404)  # unknown robot: refuse the handshake
        return
    await ws.accept()
    logger.info("LiDAR WS client connected for %s", robot_id)
    _init_robot(robot_id)
    q = hub.subscribe(robot_id)

    async def watch_disconnect() -> None:
        # The stream is send-only, but we must still read so a client going away is
        # noticed immediately rather than at the next frame (or never, if the
        # producer has stopped).  Anything the client sends is ignored.
        try:
            while True:
                await ws.receive_text()
        except Exception:
            return

    watcher = asyncio.create_task(watch_disconnect())
    try:
        while True:
            getter = asyncio.ensure_future(q.get())
            done, _ = await asyncio.wait({getter, watcher}, return_when=asyncio.FIRST_COMPLETED)
            if watcher in done:
                getter.cancel()
                break
            await ws.send_text(getter.result())
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.error("LiDAR WS error for %s: %s", robot_id, exc)
    finally:
        watcher.cancel()
        hub.unsubscribe(robot_id, q)
        logger.info("LiDAR WS client disconnected for %s", robot_id)


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
        ws_per_message_deflate=False,  # see Dockerfile
    )
