"""Ray-caster characterization (plan 4.1). Write these BEFORE vectorizing (plan 1.1)."""
import random
import time

import numpy as np
import pytest

from conftest import load_module

lidar = load_module("lidar-service", "lidar_simulator")


def _sim_with_box():
    env = lidar.Environment(
        area_size=100.0,
        boxes=[lidar.Box(cx=20.0, cy=0.0, half_w=1.0, half_d=5.0, height=10.0)],
        cylinders=[],
        walls=[],
    )
    return lidar.LidarSimulator(environment=env, max_range=50.0)


def test_known_box_hit_distance():
    random.seed(0)  # the simulator adds unseeded 1 cm Gaussian noise
    pts = _sim_with_box().generate_scan((0.0, 0.0, 0.0), 0.0)
    above_ground = pts[pts[:, 2] > 0.5]
    # Front face of the box is the plane x = 19; everything the box returns lies on it.
    face = above_ground[np.abs(above_ground[:, 0] - 19.0) < 0.1]
    assert len(face) > 100
    assert np.all(np.abs(face[:, 1]) <= 5.0 + 0.1)
    # Nothing above ground should be *in front of* the face (closer than 19 m).
    assert above_ground[:, 0].min() > 19.0 - 0.1


def test_scan_shape_and_intensity_range():
    pts = lidar.LidarSimulator().generate_scan((50.0, 50.0, 0.0), 0.3)
    assert pts.ndim == 2 and pts.shape[1] == 4 and len(pts) > 0
    assert pts[:, 3].min() >= 0.0 and pts[:, 3].max() <= 1.0


@pytest.mark.xfail(reason="pure-Python ray-caster; fixed in Phase 2 (plan 1.1)", strict=False)
def test_scan_fits_frame_budget():
    sim = lidar.LidarSimulator()
    t = time.perf_counter()
    sim.generate_scan((50.0, 50.0, 0.0), 0.0)
    assert time.perf_counter() - t < 0.05
