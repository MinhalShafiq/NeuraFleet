"""
NeuraFleet LiDAR Motion Compensation
=====================================

De-skews point clouds acquired during robot motion.  A spinning LiDAR
acquires points at different timestamps across the scan rotation,
so robot motion during that interval introduces distortion.

This module provides ``MotionCompensator.compensate()``, which corrects per-point
distortion using IMU-derived motion estimates.
"""

from __future__ import annotations

import numpy as np


class MotionCompensator:
    """
    Compensates LiDAR point clouds for ego-motion during scan acquisition.

    The VLP-16 takes approximately 100 ms per full 360-degree rotation.
    During that time the robot may translate and rotate, distorting the
    resulting point cloud.  Using IMU data we integrate the angular
    velocity and linear acceleration to build per-point correction
    transforms.
    """

    def compensate(
        self,
        points: np.ndarray,
        imu_data: dict,
        scan_duration: float = 0.1,
    ) -> np.ndarray:
        """
        Apply motion de-skewing to a point cloud.

        Uses a first-order (small-angle) correction: each point's acquisition time is
        taken from its index (points are ordered by azimuth, so index / N is the
        fraction of the rotation), then the IMU-integrated translation and rotation
        accumulated up to that time are removed.

        Parameters
        ----------
        points : np.ndarray, shape (N, 4)
            Point cloud with columns [x, y, z, intensity].
        imu_data : dict
            IMU reading with keys ``ax, ay, az`` (m/s^2, ``az`` includes gravity)
            and ``gx, gy, gz`` (rad/s).
        scan_duration : float
            Duration of the full scan rotation in seconds (default 0.1 s).

        Returns
        -------
        np.ndarray, shape (N, 4)
            De-skewed point cloud (intensity preserved).
        """
        if points.shape[0] == 0:
            return points.copy()

        n = points.shape[0]
        result = points.copy()

        ax = imu_data.get("ax", 0.0)
        ay = imu_data.get("ay", 0.0)
        az = imu_data.get("az", 0.0) + 9.81
        gx = imu_data.get("gx", 0.0)
        gy = imu_data.get("gy", 0.0)
        gz = imu_data.get("gz", 0.0)

        fractions = np.linspace(0.0, 1.0, n).reshape(-1, 1)
        t = fractions * scan_duration

        # Translation offsets per point
        dx = 0.5 * ax * t * t
        dy = 0.5 * ay * t * t
        dz = 0.5 * az * t * t
        translations = np.hstack([dx, dy, dz])

        # Small-angle linearized rotation: approximate R^T as (I - [omega]x * t)
        # where [omega]x is the skew-symmetric matrix of angular velocity
        xyz = points[:, :3] - translations
        # Apply inverse small-angle rotation: p' = p - omega x p * t
        omega_x_p = np.column_stack(
            [
                gy * xyz[:, 2] - gz * xyz[:, 1],
                gz * xyz[:, 0] - gx * xyz[:, 2],
                gx * xyz[:, 1] - gy * xyz[:, 0],
            ]
        )
        corrected = xyz - omega_x_p * t

        result[:, :3] = corrected
        return result
