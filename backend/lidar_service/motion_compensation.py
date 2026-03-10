"""
NeuraFleet LiDAR Motion Compensation
=====================================

De-skews point clouds acquired during robot motion.  A spinning LiDAR
acquires points at different timestamps across the scan rotation,
so robot motion during that interval introduces distortion.

This module provides:
- ``MotionCompensator.compensate()`` -- corrects per-point distortion
  using IMU-derived motion estimates.
- ``MotionCompensator.estimate_ego_motion()`` -- estimates rigid-body
  motion between consecutive scans (simplified point-to-point ICP).
"""

from __future__ import annotations

import math
from typing import Tuple

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

        Parameters
        ----------
        points : np.ndarray, shape (N, 4)
            Point cloud with columns [x, y, z, intensity].
        imu_data : dict
            IMU reading with keys ``ax, ay, az`` (m/s^2) and
            ``gx, gy, gz`` (rad/s).
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

        # IMU values
        ax = imu_data.get("ax", 0.0)
        ay = imu_data.get("ay", 0.0)
        az = imu_data.get("az", 0.0) + 9.81  # remove gravity component
        gx = imu_data.get("gx", 0.0)
        gy = imu_data.get("gy", 0.0)
        gz = imu_data.get("gz", 0.0)

        # Each point is assigned a fractional timestamp based on its index
        # (0 = start of scan, 1 = end of scan).
        fractions = np.linspace(0.0, 1.0, n)

        for i in range(n):
            t = fractions[i] * scan_duration

            # Translation from linear acceleration (simple integration: s = 0.5*a*t^2)
            dx = 0.5 * ax * t * t
            dy = 0.5 * ay * t * t
            dz = 0.5 * az * t * t

            # Rotation from angular velocity (small-angle approximation for speed)
            roll = gx * t
            pitch = gy * t
            yaw = gz * t

            # Build rotation matrix (Rz * Ry * Rx, small angle)
            cr, sr = math.cos(roll), math.sin(roll)
            cp, sp = math.cos(pitch), math.sin(pitch)
            cy, sy = math.cos(yaw), math.sin(yaw)

            # Combined rotation matrix
            R = np.array([
                [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
                [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
                [-sp,     cp * sr,                 cp * cr],
            ])

            # Correct point: undo the motion that occurred up to time t
            pt = points[i, :3]
            corrected = R.T @ (pt - np.array([dx, dy, dz]))
            result[i, :3] = corrected

        return result

    def compensate_vectorized(
        self,
        points: np.ndarray,
        imu_data: dict,
        scan_duration: float = 0.1,
    ) -> np.ndarray:
        """
        Vectorized (faster) version of ``compensate`` using a first-order
        linear interpolation of the correction transform.

        Suitable for real-time applications where per-point rotation
        matrix construction is too slow.

        Parameters are the same as ``compensate``.
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
        omega_x_p = np.column_stack([
            gy * xyz[:, 2] - gz * xyz[:, 1],
            gz * xyz[:, 0] - gx * xyz[:, 2],
            gx * xyz[:, 1] - gy * xyz[:, 0],
        ])
        corrected = xyz - omega_x_p * t

        result[:, :3] = corrected
        return result

    def estimate_ego_motion(
        self,
        source: np.ndarray,
        target: np.ndarray,
        max_iterations: int = 20,
        tolerance: float = 1e-4,
        max_correspondence_dist: float = 2.0,
    ) -> Tuple[np.ndarray, np.ndarray, float]:
        """
        Estimate rigid-body motion between two consecutive scans using a
        simplified point-to-point ICP (Iterative Closest Point).

        Parameters
        ----------
        source : np.ndarray, shape (M, 4)
            Point cloud from the previous scan (with intensity).
        target : np.ndarray, shape (N, 4)
            Point cloud from the current scan.
        max_iterations : int
            Maximum ICP iterations.
        tolerance : float
            Convergence threshold on mean correspondence distance change.
        max_correspondence_dist : float
            Maximum distance for a valid correspondence pair.

        Returns
        -------
        R : np.ndarray, shape (3, 3)
            Estimated rotation matrix.
        t : np.ndarray, shape (3,)
            Estimated translation vector.
        error : float
            Final mean correspondence error.
        """
        src = source[:, :3].copy()
        tgt = target[:, :3].copy()

        # Subsample for speed
        max_pts = 500
        if src.shape[0] > max_pts:
            idx = np.random.choice(src.shape[0], max_pts, replace=False)
            src = src[idx]
        if tgt.shape[0] > max_pts:
            idx = np.random.choice(tgt.shape[0], max_pts, replace=False)
            tgt = tgt[idx]

        # Current transformation
        R_total = np.eye(3)
        t_total = np.zeros(3)
        prev_error = float("inf")

        for iteration in range(max_iterations):
            # Transform source points
            transformed = (R_total @ src.T).T + t_total

            # Find nearest neighbours (brute force, small point set)
            # Compute pairwise distances
            diffs = transformed[:, np.newaxis, :] - tgt[np.newaxis, :, :]
            dists = np.linalg.norm(diffs, axis=2)
            nearest_idx = np.argmin(dists, axis=1)
            nearest_dist = dists[np.arange(len(transformed)), nearest_idx]

            # Filter by max correspondence distance
            mask = nearest_dist < max_correspondence_dist
            if mask.sum() < 3:
                break

            src_matched = transformed[mask]
            tgt_matched = tgt[nearest_idx[mask]]

            # Compute centroids
            src_centroid = src_matched.mean(axis=0)
            tgt_centroid = tgt_matched.mean(axis=0)

            # Centre the points
            src_c = src_matched - src_centroid
            tgt_c = tgt_matched - tgt_centroid

            # SVD to find optimal rotation
            H = src_c.T @ tgt_c
            U, S, Vt = np.linalg.svd(H)
            R_step = Vt.T @ U.T
            # Ensure proper rotation (det = +1)
            if np.linalg.det(R_step) < 0:
                Vt[-1, :] *= -1
                R_step = Vt.T @ U.T
            t_step = tgt_centroid - R_step @ src_centroid

            # Accumulate
            R_total = R_step @ R_total
            t_total = R_step @ t_total + t_step

            # Check convergence
            mean_error = nearest_dist[mask].mean()
            if abs(prev_error - mean_error) < tolerance:
                break
            prev_error = mean_error

        return R_total, t_total, prev_error
