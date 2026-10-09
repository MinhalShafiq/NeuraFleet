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


POSES = {
    "centre": (50.0, 50.0, 0.0, 0.0),
    "oblique": (20.0, 30.0, 0.0, 1.234),
    "near_corner": (5.0, 5.0, 0.0, -2.0),
    "east_side": (95.0, 60.0, 0.0, 3.0),
    "raised": (40.0, 70.0, 2.5, 0.7),
}


@pytest.mark.parametrize("name", sorted(POSES))
def test_matches_golden_scans_from_original_raycaster(name, root):
    """Golden scans were recorded from the pre-vectorization pure-Python implementation
    (see tests/data/make_lidar_golden.py): same points, same order, same values."""
    golden = np.load(root / "tests/data/lidar_golden.npz")[name]
    x, y, z, heading = POSES[name]
    out = lidar.LidarSimulator().generate_scan((x, y, z), heading, noise=False)
    assert out.shape == golden.shape
    np.testing.assert_allclose(out, golden, atol=1e-4)


def test_scan_is_ordered_by_azimuth_then_channel():
    """MotionCompensator assigns per-point timestamps by index, so order must be azimuthal."""
    pts = lidar.LidarSimulator().generate_scan((50.0, 50.0, 0.0), 0.0, noise=False)
    azimuth = np.unwrap(np.arctan2(pts[:, 1] - 50.0, pts[:, 0] - 50.0))
    # Allow the small within-azimuth channel jitter; the overall trend must be monotone.
    assert np.all(np.diff(azimuth) > -0.02)


def test_noise_seed_is_reproducible():
    a = lidar.LidarSimulator(seed=7).generate_scan((50.0, 50.0, 0.0), 0.0)
    b = lidar.LidarSimulator(seed=7).generate_scan((50.0, 50.0, 0.0), 0.0)
    np.testing.assert_array_equal(a, b)


def test_scan_fits_frame_budget():
    """5 Hz stream => 200 ms per frame; the plan's target is < 50 ms per scan."""
    sim = lidar.LidarSimulator()
    sim.generate_scan((50.0, 50.0, 0.0), 0.0)  # warm up
    best = min(
        _timed(lambda: sim.generate_scan((50.0, 50.0, 0.0), 0.3)) for _ in range(5)
    )
    assert best < 0.05, f"scan took {best * 1000:.1f} ms"


def _timed(fn):
    t = time.perf_counter()
    fn()
    return time.perf_counter() - t
