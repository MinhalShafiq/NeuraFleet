"""LiDAR service behaviour: no event-loop stalls (plan 1.1.2) and one producer per robot (1.1.3)."""

import asyncio
import json
import time

import httpx
import pytest
from conftest import load_module

svc = load_module("lidar-service", "main")


@pytest.fixture(scope="module")
def live_server():
    """A real uvicorn server on a free port.  (Starlette's TestClient busy-polls
    receive() with sleep(0), which starves the ray-casting thread of the GIL and
    makes any WebSocket timing test meaningless.)"""
    import socket
    import threading

    import uvicorn

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(svc.app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn did not start"
    yield f"ws://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


def _connect(base, robot_id):
    from websockets.sync.client import connect

    return connect(f"{base}/ws/lidar/{robot_id}", open_timeout=10, close_timeout=2)


def _recv_frames(ws, n):
    return [json.loads(ws.recv(timeout=10)) for _ in range(n)]


def test_clients_share_one_producer(live_server):
    """3 viewers of one robot must see the *same* frames, not advance the robot 3x."""
    before = svc._frame_counters.get("robot-001", 0)
    with (
        _connect(live_server, "robot-001") as a,
        _connect(live_server, "robot-001") as b,
        _connect(live_server, "robot-001") as c,
    ):
        frames = [_recv_frames(w, 4) for w in (a, b, c)]
    produced = svc._frame_counters["robot-001"] - before

    for fs in frames:
        ids = [f["frame_id"] for f in fs]
        # A single producer increments the counter by exactly 1 per frame.  Three
        # independent per-client generators would make consecutive ids jump by ~3.
        assert ids == list(range(ids[0], ids[0] + 4)), ids
    assert {f["frame_id"] for f in frames[0]} & {f["frame_id"] for f in frames[1]}
    assert produced <= 4 + 3, f"{produced} scans produced for 3 clients x 4 frames"


def test_producer_stops_when_last_client_leaves(live_server):
    with _connect(live_server, "robot-002") as ws:
        _recv_frames(ws, 1)
        assert "robot-002" in svc.hub._tasks
    deadline = time.time() + 5
    while "robot-002" in svc.hub._tasks and time.time() < deadline:
        time.sleep(0.05)
    assert "robot-002" not in svc.hub._tasks


def test_stream_rate_is_about_5hz_with_three_viewers(live_server):
    """Exit criterion: sustained ~5 Hz with 3 concurrent viewers."""
    with (
        _connect(live_server, "robot-003") as a,
        _connect(live_server, "robot-003") as b,
        _connect(live_server, "robot-004") as c,
    ):
        for w in (a, b, c):
            _recv_frames(w, 1)
        t = time.perf_counter()
        for w in (a, b, c):
            _recv_frames(w, 6)
        rate = 6 / (time.perf_counter() - t)
    assert 3.5 < rate < 6.0, f"{rate:.1f} Hz"


def test_downsampling_preserves_order():
    import numpy as np

    pts = np.arange(10_000 * 4, dtype=float).reshape(-1, 4)
    out = svc._scan_to_dict("r", pts, max_stream_points=2000)["points"]
    firsts = [p[0] for p in out]
    assert len(out) == 2000 and firsts == sorted(firsts)


def test_event_loop_is_not_blocked_by_scans():
    """No loop stall > 50 ms while full-resolution scans are being generated (plan exit criterion)."""

    async def run():
        lags = []
        stop = False

        async def ticker():
            while not stop:
                t = time.perf_counter()
                await asyncio.sleep(0.01)
                lags.append(time.perf_counter() - t - 0.01)

        transport = httpx.ASGITransport(app=svc.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            task = asyncio.create_task(ticker())
            await asyncio.sleep(0.05)
            for _ in range(3):
                rs = await asyncio.gather(*(client.get(f"/scan/robot-00{i}") for i in range(1, 5)))
                assert all(r.status_code == 200 for r in rs)
            stop = True
            await task
        return max(lags)

    worst = asyncio.run(run())
    assert worst < 0.05, f"event loop stalled for {worst * 1000:.0f} ms"


def test_frames_are_compact():
    """float32 rounded then .tolist() gives reprs like 56.43299865722656 (81 B/point, 2.5x bloat)."""
    import numpy as np

    pts = svc.LidarSimulator(seed=1).generate_scan((50.0, 50.0, 0.0), 0.3)
    frame = svc._scan_json("r", pts, 2000, 1)
    per_point = len(frame) / json.loads(frame)["num_points"]
    assert per_point < 45, f"{per_point:.0f} bytes/point"
    assert np.allclose(json.loads(frame)["points"][0], np.round(json.loads(frame)["points"][0], 3))


def test_lidar_stream_has_per_message_deflate_disabled():
    """Compression runs synchronously on the event-loop thread per viewer (~11 ms/frame each)."""
    from conftest import SERVICE_DIRS

    assert (
        '"--ws-per-message-deflate", "false"'
        in (SERVICE_DIRS["lidar-service"] / "Dockerfile").read_text()
    )


def test_unknown_robot_stream_is_refused_and_allocates_nothing(live_server):
    import websockets

    with pytest.raises(websockets.exceptions.InvalidHandshake):
        _connect(live_server, "not-a-robot")
    assert "not-a-robot" not in svc._simulators and "not-a-robot" not in svc.hub._tasks
