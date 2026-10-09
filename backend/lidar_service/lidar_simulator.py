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

import numpy as np

# ---------------------------------------------------------------------------
# Environment primitives
# ---------------------------------------------------------------------------


@dataclass
class Box:
    """Axis-aligned box obstacle."""

    cx: float
    cy: float
    half_w: float  # half-width along x
    half_d: float  # half-depth along y
    height: float  # total height


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
    boxes: list[Box] = field(default_factory=list)
    cylinders: list[Cylinder] = field(default_factory=list)
    walls: list[Wall] = field(default_factory=list)

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
        self.walls.extend(
            [
                Wall(0, 0, s, 0, wall_h),  # south
                Wall(s, 0, s, s, wall_h),  # east
                Wall(s, s, 0, s, wall_h),  # north
                Wall(0, s, 0, 0, wall_h),  # west
            ]
        )
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
    seed : int, optional
        Seed for the measurement-noise generator (default: nondeterministic).
    """

    def __init__(
        self,
        environment: Environment | None = None,
        max_range: float = 100.0,
        num_channels: int = 16,
        h_resolution: float = 0.4,
        v_fov: tuple[float, float] = (-15.0, 15.0),
        seed: int | None = None,
    ):
        self.env = environment or Environment()
        self.max_range = max_range
        self.num_channels = num_channels
        self.h_resolution = h_resolution
        self.v_fov = v_fov

        # Pre-compute vertical angles for channels
        v_min, v_max = v_fov
        self._v_angles = np.linspace(np.radians(v_min), np.radians(v_max), num_channels)
        # Horizontal angles (full 360 deg)
        self._h_angles = np.arange(0, 360, h_resolution)
        self._h_angles_rad = np.radians(self._h_angles)

        # Unit ray directions for heading = 0, flattened h-major / channel-minor
        # (the same order the scalar implementation emitted points in).  A robot
        # heading is then a single 2D rotation of (dx, dy); dz never changes.
        cos_v, sin_v = np.cos(self._v_angles), np.sin(self._v_angles)
        cos_h, sin_h = np.cos(self._h_angles_rad), np.sin(self._h_angles_rad)
        self._dx0 = (cos_h[:, None] * cos_v[None, :]).ravel()
        self._dy0 = (sin_h[:, None] * cos_v[None, :]).ravel()
        self._dz = np.broadcast_to(sin_v[None, :], (len(cos_h), len(cos_v))).ravel().copy()

        self._rng = np.random.default_rng(seed)

    # ------------------------------------------------------------------
    # Vectorized ray-casting helpers.  Each takes the (N,) ray directions and
    # returns an (N,) array of hit distances, +inf where the ray misses.
    # ------------------------------------------------------------------

    @staticmethod
    def _boxes_hit(ox, oy, oz, dx, dy, dz, box: Box) -> np.ndarray:
        """Axis-aligned box via the slab method (origin inside a box counts as a miss)."""
        n = dx.shape[0]
        tmin = np.full(n, -1e30)
        tmax = np.full(n, 1e30)
        ok = np.ones(n, dtype=bool)
        for o, d, lo, hi in (
            (ox, dx, box.cx - box.half_w, box.cx + box.half_w),
            (oy, dy, box.cy - box.half_d, box.cy + box.half_d),
            (oz, dz, 0.0, box.height),
        ):
            parallel = np.abs(d) < 1e-12
            if o < lo or o > hi:
                ok &= ~parallel
            safe = np.where(parallel, 1.0, d)
            t1 = (lo - o) / safe
            t2 = (hi - o) / safe
            near = np.where(parallel, -1e30, np.minimum(t1, t2))
            far = np.where(parallel, 1e30, np.maximum(t1, t2))
            tmin = np.maximum(tmin, near)
            tmax = np.minimum(tmax, far)
        ok &= (tmin <= tmax) & (tmin >= 0)
        return np.where(ok, tmin, np.inf)

    @staticmethod
    def _cylinders_hit(ox, oy, oz, dx, dy, dz, cyl: Cylinder) -> np.ndarray:
        """Vertical cylinder: nearest positive root whose hit height is within [0, height]."""
        lx, ly = ox - cyl.cx, oy - cyl.cy
        a = dx * dx + dy * dy
        b = 2 * (lx * dx + ly * dy)
        c = lx * lx + ly * ly - cyl.radius * cyl.radius
        disc = b * b - 4 * a * c
        valid = (disc >= 0) & (a >= 1e-12)
        sqrt_disc = np.sqrt(np.where(valid, disc, 0.0))
        denom = np.where(valid, 2 * a, 1.0)
        t1 = (-b - sqrt_disc) / denom
        t2 = (-b + sqrt_disc) / denom
        ok1 = valid & (t1 > 0) & (oz + t1 * dz >= 0) & (oz + t1 * dz <= cyl.height)
        ok2 = valid & (t2 > 0) & (oz + t2 * dz >= 0) & (oz + t2 * dz <= cyl.height)
        return np.where(ok1, t1, np.where(ok2, t2, np.inf))

    @staticmethod
    def _walls_hit(ox, oy, oz, dx, dy, dz, wall: Wall) -> np.ndarray:
        """Thin vertical plane segment."""
        wx, wy = wall.x2 - wall.x1, wall.y2 - wall.y1
        denom = dx * wy - dy * wx
        valid = np.abs(denom) >= 1e-12
        t = ((wall.x1 - ox) * wy - (wall.y1 - oy) * wx) / np.where(valid, denom, 1.0)
        s = (ox + t * dx - wall.x1) / wx if abs(wx) > abs(wy) else (oy + t * dy - wall.y1) / wy
        z_hit = oz + t * dz
        ok = valid & (t >= 0) & (s >= 0) & (s <= 1) & (z_hit >= 0) & (z_hit <= wall.height)
        return np.where(ok, t, np.inf)

    # ------------------------------------------------------------------
    # Main scan generation
    # ------------------------------------------------------------------

    def generate_scan(
        self,
        robot_position: tuple[float, float, float],
        robot_heading: float,
        noise: bool = True,
    ) -> np.ndarray:
        """
        Generate one complete 360-degree LiDAR scan.

        Parameters
        ----------
        robot_position : (x, y, z)  in world frame
        robot_heading  : radians, 0 = +X
        noise : add Gaussian range/intensity noise (disable for reference tests)

        Returns
        -------
        np.ndarray of shape (N, 4) with columns [x, y, z, intensity], ordered
        by azimuth then channel (``MotionCompensator`` relies on this order).
        N typically ranges from ~5 000 to ~15 000.
        """
        ox, oy, oz = robot_position
        oz += 1.8  # sensor mounted 1.8 m above ground

        c, s = math.cos(robot_heading), math.sin(robot_heading)
        dx = self._dx0 * c - self._dy0 * s
        dy = self._dx0 * s + self._dy0 * c
        dz = self._dz

        best = np.full(dx.shape[0], self.max_range)

        # Ground plane (z = 0)
        down = dz < -1e-6
        t_ground = np.where(down, -oz / np.where(down, dz, -1.0), np.inf)
        t_ground = np.where(t_ground > 0, t_ground, np.inf)
        best = np.where(t_ground < best, t_ground, best)

        for box in self.env.boxes:
            best = np.minimum(best, self._boxes_hit(ox, oy, oz, dx, dy, dz, box))
        for cyl in self.env.cylinders:
            best = np.minimum(best, self._cylinders_hit(ox, oy, oz, dx, dy, dz, cyl))
        for wall in self.env.walls:
            best = np.minimum(best, self._walls_hit(ox, oy, oz, dx, dy, dz, wall))

        # A ray that found nothing closer than max_range is a miss (strict <).
        hit = best < self.max_range
        if not hit.any():
            return np.zeros((1, 4), dtype=np.float32)

        t = best[hit]
        pts = np.empty((t.shape[0], 4))
        pts[:, 0] = ox + t * dx[hit]
        pts[:, 1] = oy + t * dy[hit]
        pts[:, 2] = oz + t * dz[hit]
        intensity = 1.0 - t / self.max_range
        if noise:
            intensity = intensity + self._rng.normal(0.0, 0.03, t.shape[0])
            pts[:, :3] += self._rng.normal(0.0, 0.01, (t.shape[0], 3))  # 1 cm
        pts[:, 3] = np.clip(intensity, 0.0, 1.0)
        return pts.astype(np.float32)
