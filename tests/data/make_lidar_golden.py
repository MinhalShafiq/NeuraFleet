"""Regenerate tests/data/lidar_golden.npz.

The golden scans were recorded from the ORIGINAL pure-Python ray-caster (before
it was vectorized) with Gaussian noise disabled.  Do not regenerate them from the
vectorized implementation - that would make the characterization test circular.
Run from the repo root:  python tests/data/make_lidar_golden.py
"""
import random
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "lidar_service"))
import lidar_simulator as ls  # noqa: E402

POSES = {
    "centre": (50.0, 50.0, 0.0, 0.0),
    "oblique": (20.0, 30.0, 0.0, 1.234),
    "near_corner": (5.0, 5.0, 0.0, -2.0),
    "east_side": (95.0, 60.0, 0.0, 3.0),
    "raised": (40.0, 70.0, 2.5, 0.7),
}

if __name__ == "__main__":
    random.gauss = lambda *a, **k: 0.0  # noise-free
    sim = ls.LidarSimulator()
    out = {}
    for name, (x, y, z, h) in POSES.items():
        out[name] = sim.generate_scan((x, y, z), h)
    np.savez_compressed(Path(__file__).with_name("lidar_golden.npz"), **out)
    print({k: v.shape for k, v in out.items()})
