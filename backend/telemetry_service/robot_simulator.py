"""
NeuraFleet Fleet Simulator
==========================

Two ways to fill a fleet's state, sharing one physics model (``shared.robot_physics``):

``FleetSimulator``
    Ticks all six robots locally, in this process. This is the original design, kept as
    the "no MQTT needed" mode: tests use it directly, and it is what ``robot_agent``'s
    single-robot physics is extracted *from* (not a different simulation, the same one).

``FleetAggregator``
    Fills robot state from MQTT telemetry published by independent ``robot_agent``
    processes instead of ticking physics locally (see ``plan-messaging.md``). It reuses
    ``FleetSimulator`` for everything that doesn't care where the numbers came from:
    anomaly detection, stateful alerts (raise once, update, clear with hysteresis),
    metrics history, and the public query API. It adds per-robot connectivity
    (online / stale / offline), because "is this robot's data still arriving" only
    exists once robots are separate, unreliable publishers rather than one loop's
    dict reads.
"""

from __future__ import annotations

import math
import random
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime

from shared.robot_physics import (
    FLEET_CONFIG,
    RobotConfig,
    RobotSim,
    advance_robot,
    robot_telemetry,
    stable_phase,
)

__all__ = [
    "RobotConfig",
    "RobotSim",
    "FLEET_CONFIG",
    "FleetSimulator",
    "FleetAggregator",
]

# Re-exported for anything importing the old private name (docs snippets, tests).
_stable_phase = stable_phase


# ---------------------------------------------------------------------------
# Fleet Simulator
# ---------------------------------------------------------------------------


class FleetSimulator:
    """
    Manages the full simulated fleet.

    Call ``update(dt)`` every tick (typically 0.1 s at 10 Hz) to advance
    the simulation.  Query methods return snapshots suitable for
    serialisation to JSON / Pydantic models.
    """

    def __init__(
        self, configs: list[RobotConfig] | None = None, area_size: float = 100.0, seed: int = 12345
    ):
        self.configs = configs or FLEET_CONFIG
        self.area_size = area_size
        self.robots: dict[str, RobotSim] = {}
        # Stateful alerts keyed by (robot_id, alert_type); see _sync_alerts.
        self._active_alerts: dict[tuple[str, str], dict] = {}
        self._resolved_alerts: deque = deque(maxlen=50)
        self._alert_seq: int = 0
        self._metrics_history: dict[str, deque] = {}
        self._sim_time: float = 0.0

        self._rng = random.Random(seed)
        rng = self._rng
        for cfg in self.configs:
            r = RobotSim(
                robot_id=cfg.robot_id,
                name=cfg.name,
                robot_type=cfg.robot_type,
                x=rng.uniform(10, area_size - 10),
                y=rng.uniform(10, area_size - 10),
                z=0.0,
                vx=rng.uniform(-0.5, 0.5),
                vy=rng.uniform(-0.5, 0.5),
                battery=rng.uniform(70, 100),
                heading=rng.uniform(0, 2 * math.pi),
                cpu_usage=rng.uniform(20, 50),
                memory_usage=rng.uniform(30, 60),
                temperature=rng.uniform(40, 60),
                _charge_x=rng.uniform(20, 80),
                _charge_y=rng.uniform(20, 80),
            )
            # GPS offset per robot so they spread across a plausible area
            r.gps_lat = 37.7749 + (r.x - 50) * 0.00001
            r.gps_lon = -122.4194 + (r.y - 50) * 0.00001
            self.robots[cfg.robot_id] = r
            self._metrics_history[cfg.robot_id] = deque(maxlen=100)

    # ------------------------------------------------------------------
    # Simulation tick
    # ------------------------------------------------------------------

    def update(self, dt: float = 0.1) -> None:
        """Advance the entire fleet simulation by *dt* seconds."""
        self._sim_time += dt

        for r in self.robots.values():
            advance_robot(r, dt, self._rng, self.area_size, self._sim_time)
            self._record_metrics(r)
            # Anomaly detection -> alert state transitions
            self._sync_alerts(r, self._detect_anomalies(r))

    def _record_metrics(self, r: RobotSim) -> None:
        vel_mag = math.sqrt(r.vx**2 + r.vy**2 + r.vz**2)
        self._metrics_history[r.robot_id].append(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "battery": round(r.battery, 2),
                "cpu_usage": round(r.cpu_usage, 2),
                "memory_usage": round(r.memory_usage, 2),
                "temperature": round(r.temperature, 2),
                "velocity_magnitude": round(vel_mag, 3),
            }
        )

    # ------------------------------------------------------------------
    # Anomaly detection -> alerts
    # ------------------------------------------------------------------

    # Hysteresis: a condition raises at the threshold but only clears once the
    # value has moved clear of it, so readings hovering near a limit don't flap.
    _TEMP_RAISE, _TEMP_CLEAR = 75.0, 72.0
    _BATT_RAISE, _BATT_CLEAR = 20.0, 23.0
    _PROX_RAISE, _PROX_CLEAR = 3.0, 3.5

    def _detect_anomalies(self, r: RobotSim) -> dict[str, tuple[str, str]]:
        """Return ``{alert_type: (severity, message)}`` for conditions currently true."""

        def active(atype: str) -> bool:
            return (r.robot_id, atype) in self._active_alerts

        found: dict[str, tuple[str, str]] = {}

        if r.temperature > self._TEMP_RAISE or (
            active("high_temperature") and r.temperature > self._TEMP_CLEAR
        ):
            sev = "critical" if r.temperature > 85 else "warning"
            found["high_temperature"] = (sev, f"Temperature at {r.temperature:.1f}°C on {r.name}")

        if r.battery < self._BATT_RAISE or (active("low_battery") and r.battery < self._BATT_CLEAR):
            sev = "critical" if r.battery < 10 else "warning"
            found["low_battery"] = (sev, f"Battery at {r.battery:.1f}% on {r.name}")

        if r._sensor_fault:
            found["sensor_malfunction"] = ("warning", f"IMU sensor readings anomalous on {r.name}")

        if r.status == "error":
            found["communication_loss"] = ("critical", f"Communication degraded with {r.name}")

        # Collision proximity: nearest other robot
        nearest, nearest_d = None, float("inf")
        for other in self.robots.values():
            if other.robot_id == r.robot_id:
                continue
            d = math.sqrt((r.x - other.x) ** 2 + (r.y - other.y) ** 2)
            if d < nearest_d:
                nearest, nearest_d = other, d
        if nearest is not None and (
            nearest_d < self._PROX_RAISE
            or (active("collision_proximity") and nearest_d < self._PROX_CLEAR)
        ):
            sev = "warning" if nearest_d > 1.5 else "critical"
            found["collision_proximity"] = (
                sev,
                f"{r.name} within {nearest_d:.1f} m of {nearest.name}",
            )

        return found

    def _sync_alerts(self, r: RobotSim, found: dict[str, tuple[str, str]]) -> None:
        """Raise / update / clear this robot's alerts based on *found*."""
        now = datetime.now(UTC).isoformat()

        for atype, (sev, msg) in found.items():
            key = (r.robot_id, atype)
            alert = self._active_alerts.get(key)
            if alert is None:
                self._alert_seq += 1
                self._active_alerts[key] = {
                    "id": f"{r.robot_id}:{atype}:{self._alert_seq}",
                    "robot_id": r.robot_id,
                    "type": atype,
                    "severity": sev,
                    "message": msg,
                    "timestamp": now,  # == first_seen; kept for API compatibility
                    "first_seen": now,
                    "last_seen": now,
                    "count": 1,
                }
            else:
                alert.update(severity=sev, message=msg, last_seen=now, count=alert["count"] + 1)

        for key in [k for k in self._active_alerts if k[0] == r.robot_id and k[1] not in found]:
            alert = self._active_alerts.pop(key)
            alert["resolved_at"] = now
            self._resolved_alerts.append(alert)

    # ------------------------------------------------------------------
    # Public query methods
    # ------------------------------------------------------------------

    def get_telemetry(self) -> list[dict]:
        """Return telemetry snapshots for all robots."""
        return [self._robot_telemetry(r) for r in self.robots.values()]

    def get_robot(self, robot_id: str) -> dict | None:
        """Return full state for one robot, or None."""
        r = self.robots.get(robot_id)
        if r is None:
            return None
        return self._robot_state(r)

    def get_all_robots(self) -> list[dict]:
        """Return full state (with name/type) for all robots."""
        return [self._robot_state(r) for r in self.robots.values()]

    def get_alerts(self) -> list[dict]:
        """Return currently active alerts, oldest first."""
        return [dict(a) for a in self._active_alerts.values()]

    def get_resolved_alerts(self) -> list[dict]:
        """Return the bounded history (last 50) of cleared alerts."""
        return [dict(a) for a in self._resolved_alerts]

    def get_metrics(self, robot_id: str) -> dict | None:
        """Return metrics history for a robot (up to 100 points)."""
        history = self._metrics_history.get(robot_id)
        if history is None:
            return None
        return {
            "robot_id": robot_id,
            "points": list(history),
        }

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _robot_telemetry(r: RobotSim) -> dict:
        return robot_telemetry(r, datetime.now(UTC).isoformat())

    @staticmethod
    def _robot_state(r: RobotSim) -> dict:
        d = FleetSimulator._robot_telemetry(r)
        d["name"] = r.name
        d["robot_type"] = r.robot_type
        return d


# ---------------------------------------------------------------------------
# Fleet Aggregator - fills robot state from MQTT instead of local ticks
# ---------------------------------------------------------------------------

# How long without a telemetry message before a robot is considered stale, then
# offline, in the absence of an explicit status message (its birth/LWT message).
# 10 Hz telemetry means 3 s is ~30 missed messages: generous enough that normal
# jitter never flaps, fast enough that a demo notices within a few seconds.
STALE_AFTER_SECONDS = 3.0
OFFLINE_AFTER_SECONDS = 10.0


@dataclass
class _ConnState:
    state: str = "online"  # online | stale | offline
    last_seen: float = 0.0  # time.monotonic() of the last telemetry message
    explicit_offline: bool = False  # a status/LWT message said so - timers don't override it


class FleetAggregator(FleetSimulator):
    """A ``FleetSimulator`` whose robots are filled from MQTT messages rather than a
    local physics tick. ``update()`` still works (useful in tests, and harmless if
    something calls it), it just races whatever the MQTT bridge is also writing.
    """

    def __init__(
        self, configs: list[RobotConfig] | None = None, area_size: float = 100.0, seed: int = 12345
    ):
        super().__init__(configs, area_size, seed)
        now = time.monotonic()
        # Optimistic at startup: a robot is "online" until proven otherwise, so a fresh
        # aggregator (before any agent has even had time to connect) reports healthy
        # rather than instantly degraded. check_staleness() ages this out if nothing
        # ever arrives.
        self._conn: dict[str, _ConnState] = {
            rid: _ConnState(state="online", last_seen=now) for rid in self.robots
        }

    # ------------------------------------------------------------------
    # Ingest (called by the MQTT bridge)
    # ------------------------------------------------------------------

    def ingest_telemetry(self, robot_id: str, data: dict) -> None:
        """Apply one telemetry message (the same shape ``robot_agent`` publishes,
        i.e. ``shared.robot_physics.robot_telemetry``'s output) to robot *robot_id*."""
        r = self.robots.get(robot_id)
        if r is None:
            return  # an agent publishing under an id we don't know about; ignore it
        pos, vel, sensors = data["position"], data["velocity"], data["sensors"]
        r.x, r.y, r.z = pos["x"], pos["y"], pos["z"]
        r.vx, r.vy, r.vz = vel["vx"], vel["vy"], vel["vz"]
        r.battery = data["battery"]
        r.status = data["status"]
        r.cpu_usage = data["cpu_usage"]
        r.memory_usage = data["memory_usage"]
        r.temperature = data["temperature"]
        r.estopped = data.get(
            "estopped", False
        )  # .get(): tolerate an older agent publishing without it
        imu, gps = sensors["imu"], sensors["gps"]
        r.imu_ax, r.imu_ay, r.imu_az = imu["ax"], imu["ay"], imu["az"]
        r.imu_gx, r.imu_gy, r.imu_gz = imu["gx"], imu["gy"], imu["gz"]
        r.gps_lat, r.gps_lon, r.gps_alt = gps["lat"], gps["lon"], gps["alt"]

        conn = self._conn[robot_id]
        conn.last_seen = time.monotonic()
        conn.state = "online"
        conn.explicit_offline = False

        self._record_metrics(r)
        self._sync_alerts(r, self._detect_anomalies(r))

    def mark_status(self, robot_id: str, online: bool) -> None:
        """Apply a ``neurafleet/{id}/status`` message (the birth message, or the
        broker publishing the robot's Last Will after it disappeared)."""
        conn = self._conn.get(robot_id)
        if conn is None:
            return
        if online:
            conn.state = "online"
            conn.last_seen = time.monotonic()
            conn.explicit_offline = False
        else:
            conn.state = "offline"
            conn.explicit_offline = True

    def check_staleness(self) -> None:
        """Age connectivity forward and re-run alert detection for everyone.

        This runs on its own timer (not just when a message arrives) for two
        reasons: a robot that stops publishing must still be *noticed* even
        though nothing triggers a check for it, and a condition like
        "collision proximity" depends on a robot that didn't just report
        (its neighbour moved, it didn't).
        """
        now = time.monotonic()
        for conn in self._conn.values():
            if conn.explicit_offline:
                continue  # a status message is authoritative until the next one
            age = now - conn.last_seen
            if age > OFFLINE_AFTER_SECONDS:
                conn.state = "offline"
            elif age > STALE_AFTER_SECONDS:
                conn.state = "stale"
        for r in self.robots.values():
            self._sync_alerts(r, self._detect_anomalies(r))

    def connectivity_counts(self) -> dict[str, int]:
        counts = {"online": 0, "stale": 0, "offline": 0}
        for conn in self._conn.values():
            counts[conn.state] += 1
        return counts

    def connectivity(self, robot_id: str) -> str:
        conn = self._conn.get(robot_id)
        return conn.state if conn else "offline"

    # ------------------------------------------------------------------
    # Query overrides: merge in connectivity (additive field, see shared.models)
    # ------------------------------------------------------------------

    def get_telemetry(self) -> list[dict]:
        out = super().get_telemetry()
        for d in out:
            d["connectivity"] = self.connectivity(d["robot_id"])
        return out

    def get_all_robots(self) -> list[dict]:
        out = super().get_all_robots()
        for d in out:
            d["connectivity"] = self.connectivity(d["robot_id"])
        return out

    def get_robot(self, robot_id: str) -> dict | None:
        d = super().get_robot(robot_id)
        if d is not None:
            d["connectivity"] = self.connectivity(robot_id)
        return d
