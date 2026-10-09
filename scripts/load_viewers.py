"""Open N LiDAR WebSocket viewers and report their frame rates (used by scripts/test.sh).

usage: load_viewers.py WS_BASE SECONDS ROBOT [ROBOT ...]
e.g.   load_viewers.py ws://localhost:8001/ws/lidar 8 robot-001 robot-001 robot-002
Prints one JSON line: {"rates": [...], "consecutive": bool, "points": int, "shared_ok": bool}
"""
import json
import sys
import threading
import time

from websockets.sync.client import connect


def viewer(url, secs, out):
    ts, ids, pts = [], [], 0
    with connect(url, open_timeout=10) as ws:
        end = time.time() + secs
        while time.time() < end:
            m = json.loads(ws.recv(timeout=5))
            ts.append(time.perf_counter())
            ids.append(m["frame_id"])
            pts = m["num_points"]
    out.append((url, ts, ids, pts))


def main():
    base, secs, robots = sys.argv[1], float(sys.argv[2]), sys.argv[3:]
    outs = []
    threads = [threading.Thread(target=viewer, args=(f"{base}/{r}", secs, outs)) for r in robots]
    [t.start() for t in threads]
    [t.join() for t in threads]
    rates = [round((len(ts) - 1) / (ts[-1] - ts[0]), 2) for _, ts, _, _ in outs]
    consecutive = all(b - a >= 1 for _, _, ids, _ in outs for a, b in zip(ids, ids[1:]))
    by_robot = {}
    for url, _, ids, _ in outs:
        by_robot.setdefault(url, []).append(set(ids))
    # viewers of the same robot must be served the same frames (one producer, fanned out)
    shared_ok = all(len(a & b) >= min(len(a), len(b)) - 3 for sets in by_robot.values() for a in sets for b in sets)
    print(json.dumps({"rates": rates, "consecutive": consecutive, "points": outs[0][3], "shared_ok": shared_ok}))


if __name__ == "__main__":
    main()
