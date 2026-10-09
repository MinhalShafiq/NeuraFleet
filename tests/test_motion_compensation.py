"""MotionCompensator (plan 2.8): the one remaining implementation, with hand-derived expectations."""

import numpy as np
import pytest
from conftest import load_module

mc = load_module("lidar-service", "motion_compensation")
GRAVITY_ONLY = {"ax": 0.0, "ay": 0.0, "az": -9.81, "gx": 0.0, "gy": 0.0, "gz": 0.0}


@pytest.fixture
def cloud():
    rng = np.random.default_rng(0)
    pts = rng.uniform(-20, 20, (500, 4))
    pts[:, 3] = rng.uniform(0, 1, 500)
    return pts


def test_dead_code_is_gone():
    assert not hasattr(mc.MotionCompensator, "compensate_vectorized")
    assert not hasattr(mc.MotionCompensator, "estimate_ego_motion")


def test_stationary_robot_leaves_cloud_unchanged(cloud):
    out = mc.MotionCompensator().compensate(cloud, GRAVITY_ONLY)
    np.testing.assert_allclose(out, cloud, atol=1e-12)


def test_input_is_not_mutated_and_intensity_preserved(cloud):
    before = cloud.copy()
    out = mc.MotionCompensator().compensate(cloud, {**GRAVITY_ONLY, "gz": 1.0, "ax": 2.0})
    np.testing.assert_array_equal(cloud, before)
    np.testing.assert_array_equal(out[:, 3], cloud[:, 3])
    assert out.shape == cloud.shape


def test_empty_cloud_returns_empty_copy():
    empty = np.zeros((0, 4))
    out = mc.MotionCompensator().compensate(empty, GRAVITY_ONLY)
    assert out.shape == (0, 4) and out is not empty


def test_yaw_rate_shifts_points_by_minus_gz_times_acquisition_time():
    """For p = (1, 0, 0): omega x p = (0, gz, 0), so the corrected y is -gz * t, with
    t = (i / (N-1)) * scan_duration - the first point is untouched, the last moves most."""
    n, gz, duration = 11, 0.5, 0.1
    pts = np.tile([1.0, 0.0, 0.0, 0.7], (n, 1))
    out = mc.MotionCompensator().compensate(pts, {**GRAVITY_ONLY, "gz": gz}, scan_duration=duration)
    t = np.linspace(0, 1, n) * duration
    np.testing.assert_allclose(out[:, 1], -gz * t, atol=1e-12)
    np.testing.assert_allclose(out[:, 0], 1.0, atol=1e-12)
    assert out[0, 1] == 0.0


def test_constant_acceleration_removes_half_a_t_squared():
    n, ax, duration = 5, 4.0, 0.1
    pts = np.zeros((n, 4))
    out = mc.MotionCompensator().compensate(pts, {**GRAVITY_ONLY, "ax": ax}, scan_duration=duration)
    t = np.linspace(0, 1, n) * duration
    np.testing.assert_allclose(out[:, 0], -0.5 * ax * t * t, atol=1e-12)
