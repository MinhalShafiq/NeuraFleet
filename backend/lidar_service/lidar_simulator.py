"""
NeuraFleet LiDAR Simulator
==========================

Generates realistic synthetic 3D point clouds emulating a Velodyne VLP-16
spinning LiDAR scanner in a persistent simulated environment.

The environment contains:
- A flat ground plane
- Box obstacles (buildings / containers)
- Cylindrical obstacles (pillars / trees)
- Perimeter walls / boundaries

Each point carries (x, y, z, intensity).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import List, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Environment primitives
# ---------------------------------------------------------------------------

@dataclass
class Box:
    """Axis-aligned box obstacle."""
    cx: float
    cy: float
    half_w: float   # half-width along x
    half_d: float   # half-depth along y
    height: float   # total height


@dataclass
class Cylinder:
    """Cylindrical obstacle (pillar / tree)."""
    cx: float
    cy: float
    radius: float
    height: float


@dataclass
class Wall:
    """A thin vertical wall segment between two 2D endpoints."""
    x1: float
    y1: float
    x2: float
    y2: float
    height: float


# ---------------------------------------------------------------------------
# Simulated environment (persistent between scans)
# ---------------------------------------------------------------------------

@dataclass
class Environment:
    """Holds all static obstacles for the simulated world."""
    area_size: float = 100.0
    boxes: List[Box] = field(default_factory=list)
    cylinders: List[Cylinder] = field(default_factory=list)
    walls: List[Wall] = field(default_factory=list)

    def __post_init__(self):
        if not self.boxes:
            self._generate()

    def _generate(self):
        rng = random.Random(42)  # deterministic environment
        # Boxes (buildings / containers)
        for _ in range(8):
            cx = rng.uniform(10, self.area_size - 10)
            cy = rng.uniform(10, self.area_size - 10)
            hw = rng.uniform(2, 6)
            hd = rng.uniform(2, 6)
            h = rng.uniform(3, 12)
            self.boxes.append(Box(cx, cy, hw, hd, h))

        # Cylinders (trees / pillars)
        for _ in range(12):
            cx = rng.uniform(5, self.area_size - 5)
            cy = rng.uniform(5, self.area_size - 5)
            r = rng.uniform(0.3, 1.5)
            h = rng.uniform(2, 8)
            self.cylinders.append(Cylinder(cx, cy, r, h))

        # Perimeter walls
        s = self.area_size
        wall_h = 4.0
        self.walls.extend([
            Wall(0, 0, s, 0, wall_h),     # south
            Wall(s, 0, s, s, wall_h),      # east
            Wall(s, s, 0, s, wall_h),      # north
            Wall(0, s, 0, 0, wall_h),      # west
        ])
        # A few interior walls
        for _ in range(3):
            x1 = rng.uniform(15, s - 15)
            y1 = rng.uniform(15, s - 15)
            angle = rng.uniform(0, math.pi)
            length = rng.uniform(5, 15)
            x2 = x1 + length * math.cos(angle)
            y2 = y1 + length * math.sin(angle)
            self.walls.append(Wall(x1, y1, x2, y2, rng.uniform(2, 5)))


# ---------------------------------------------------------------------------
# LiDAR Simulator
# ---------------------------------------------------------------------------

class LidarSimulator:
    """
    Simulates a 16-channel spinning LiDAR (Velodyne VLP-16 style).

    Parameters
    ----------
    environment : Environment, optional
        Shared persistent world.  A default one is created if omitted.
    max_range : float
        Maximum detection range in metres (default 100 m).
    num_channels : int
        Vertical channels (default 16).
    h_resolution : float
        Horizontal angular resolution in degrees (default 0.4).
    v_fov : tuple[float, float]
        Vertical field-of-view in degrees (min, max), default (-15, +15).
    """

    def __init__(
        self,
        environment: Environment | None = None,
        max_range: float = 100.0,
        num_channels: int = 16,
        h_resolution: float = 0.4,
        v_fov: Tuple[float, float] = (-15.0, 15.0),
    ):
        self.env = environment or Environment()
        self.max_range = max_range
        self.num_channels = num_channels
        self.h_resolution = h_resolution
        self.v_fov = v_fov

        # Pre-compute vertical angles for channels
        v_min, v_max = v_fov
        self._v_angles = np.linspace(
            np.radians(v_min), np.radians(v_max), num_channels
        )
        # Horizontal angles (full 360 deg)
        self._h_angles = np.arange(0, 360, h_resolution)
        self._h_angles_rad = np.radians(self._h_angles)

    # ------------------------------------------------------------------
    # Ray-casting helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _ray_box_distance(
        ox: float, oy: float, oz: float,
        dx: float, dy: float, dz: float,
        box: Box,
    ) -> float | None:
        """Axis-aligned box ray intersection distance (or None)."""
        x_min = box.cx - box.half_w
        x_max = box.cx + box.half_w
        y_min = box.cy - box.half_d
        y_max = box.cy + box.half_d
        z_min = 0.0
        z_max = box.height

        tmin = -1e30
        tmax = 1e30
        for axis, (o, d, lo, hi) in enumerate([
            (ox, dx, x_min, x_max),
            (oy, dy, y_min, y_max),
            (oz, dz, z_min, z_max),
        ]):
            if abs(d) < 1e-12:
                if o < lo or o > hi:
                    return None
            else:
                t1 = (lo - o) / d
                t2 = (hi - o) / d
                if t1 > t2:
                    t1, t2 = t2, t1
                tmin = max(tmin, t1)
                tmax = min(tmax, t2)
                if tmin > tmax:
                    return None
        if tmin < 0:
            return None
        return tmin

    @staticmethod
    def _ray_cylinder_distance(
        ox: float, oy: float, oz: float,
        dx: float, dy: float, dz: float,
        cyl: Cylinder,
    ) -> float | None:
        """Infinite-height cylinder, then clamp to height."""
        # Shift origin
        lx = ox - cyl.cx
        ly = oy - cyl.cy
        a = dx * dx + dy * dy
        b = 2 * (lx * dx + ly * dy)
        c = lx * lx + ly * ly - cyl.radius * cyl.radius
        disc = b * b - 4 * a * c
        if disc < 0 or a < 1e-12:
            return None
        sqrt_disc = math.sqrt(disc)
        t1 = (-b - sqrt_disc) / (2 * a)
        t2 = (-b + sqrt_disc) / (2 * a)
        for t in (t1, t2):
            if t > 0:
                z_hit = oz + t * dz
                if 0 <= z_hit <= cyl.height:
                    return t
        return None

    @staticmethod
    def _ray_wall_distance(
        ox: float, oy: float, oz: float,
        dx: float, dy: float, dz: float,
        wall: Wall,
    ) -> float | None:
        """Thin vertical plane segment intersection."""
        wx = wall.x2 - wall.x1
        wy = wall.y2 - wall.y1
        denom = dx * wy - dy * wx
        if abs(denom) < 1e-12:
            return None
        t = ((wall.x1 - ox) * wy - (wall.y1 - oy) * wx) / denom
        if t < 0:
            return None
        # Check wall parameter s (0..1)
        if abs(wx) > abs(wy):
            s = (ox + t * dx - wall.x1) / wx
        else:
            s = (oy + t * dy - wall.y1) / wy
        if s < 0 or s > 1:
            return None
        z_hit = oz + t * dz
        if z_hit < 0 or z_hit > wall.height:
            return None
        return t

    # ------------------------------------------------------------------
    # Main scan generation
    # ------------------------------------------------------------------

    def generate_scan(
        self,
        robot_position: Tuple[float, float, float],
        robot_heading: float,
    ) -> np.ndarray:
        """
        Generate one complete 360-degree LiDAR scan.

        Parameters
        ----------
        robot_position : (x, y, z)  in world frame
        robot_heading  : radians, 0 = +X

        Returns
        -------
        np.ndarray of shape (N, 4) with columns [x, y, z, intensity].
        N typically ranges from ~5 000 to ~15 000.
        """
        ox, oy, oz = robot_position
        oz += 1.8  # sensor mounted 1.8 m above ground

        points: list = []

        for h_angle in self._h_angles_rad:
            # Apply robot heading
            abs_h = h_angle + robot_heading
            cos_h = math.cos(abs_h)
            sin_h = math.sin(abs_h)

            for v_angle in self._v_angles:
                cos_v = math.cos(v_angle)
                sin_v = math.sin(v_angle)

                dx = cos_h * cos_v
                dy = sin_h * cos_v
                dz = sin_v

                best_t = self.max_range
                hit = False

                # Ground plane (z = 0)
                if dz < -1e-6:
                    t_ground = -oz / dz
                    if 0 < t_ground < best_t:
                        best_t = t_ground
                        hit = True

                # Boxes
                for box in self.env.boxes:
                    t = self._ray_box_distance(ox, oy, oz, dx, dy, dz, box)
                    if t is not None and t < best_t:
                        best_t = t
                        hit = True

                # Cylinders
                for cyl in self.env.cylinders:
                    t = self._ray_cylinder_distance(ox, oy, oz, dx, dy, dz, cyl)
                    if t is not None and t < best_t:
                        best_t = t
                        hit = True

                # Walls
                for wall in self.env.walls:
                    t = self._ray_wall_distance(ox, oy, oz, dx, dy, dz, wall)
                    if t is not None and t < best_t:
                        best_t = t
                        hit = True

                if hit:
                    px = ox + best_t * dx
                    py = oy + best_t * dy
                    pz = oz + best_t * dz
                    # Intensity: inversely proportional to distance, with noise
                    intensity = max(
                        0.0,
                        min(1.0, (1.0 - best_t / self.max_range) + random.gauss(0, 0.03)),
                    )
                    # Add Gaussian noise to the point
                    noise_std = 0.01  # 1 cm
                    px += random.gauss(0, noise_std)
                    py += random.gauss(0, noise_std)
                    pz += random.gauss(0, noise_std)
                    points.append([px, py, pz, intensity])

        if not points:
            # Degenerate case: return a minimal scan
            return np.zeros((1, 4), dtype=np.float32)

        return np.array(points, dtype=np.float32)
