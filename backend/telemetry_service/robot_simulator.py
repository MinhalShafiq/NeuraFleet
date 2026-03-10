"""
NeuraFleet Fleet Simulator
==========================

Simulates a fleet of heterogeneous robots with realistic telemetry:
positions, velocities, battery levels, CPU/memory usage, temperature,
IMU and GPS sensor readings, and status transitions.

Anomaly detection produces alerts for high temperatures, low batteries,
sensor malfunctions, communication loss, and collision proximity.
"""

from __future__ import annotations

import math
import random
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class RobotConfig:
    robot_id: str
    name: str
    robot_type: str


FLEET_CONFIG: List[RobotConfig] = [
    RobotConfig("robot-001", "Atlas-1", "explorer"),
    RobotConfig("robot-002", "Scout-2", "scout"),
    RobotConfig("robot-003", "Hauler-3", "hauler"),
    RobotConfig("robot-004", "Sentinel-4", "sentinel"),
    RobotConfig("robot-005", "Mapper-5", "mapper"),
    RobotConfig("robot-006", "Relay-6", "relay"),
]


@dataclass
class RobotSim:
    """Mutable simulation state for one robot."""
    robot_id: str
    name: str
    robot_type: str

    # Kinematics
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    vx: float = 0.0
    vy: float = 0.0
    vz: float = 0.0
    heading: float = 0.0  # radians

    # Systems
    battery: float = 90.0
    status: str = "active"
    cpu_usage: float = 35.0
    memory_usage: float = 45.0
    temperature: float = 50.0

    # Sensors
    imu_ax: float = 0.0
    imu_ay: float = 0.0
    imu_az: float = -9.81
    imu_gx: float = 0.0
    imu_gy: float = 0.0
    imu_gz: float = 0.0
    gps_lat: float = 37.7749
    gps_lon: float = -122.4194
    gps_alt: float = 10.0

    # Internal timers
    _status_timer: float = 0.0
    _anomaly_cooldown: float = 0.0
    _charging_target: float = 95.0
    _sensor_fault: bool = False

    # Charging station position (each robot has one assigned)
    _charge_x: float = 50.0
    _charge_y: float = 50.0


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

    def __init__(self, configs: List[RobotConfig] | None = None, area_size: float = 100.0):
        self.configs = configs or FLEET_CONFIG
        self.area_size = area_size
        self.robots: Dict[str, RobotSim] = {}
        self.alerts: List[dict] = []
        self._metrics_history: Dict[str, deque] = {}
        self._sim_time: float = 0.0

        rng = random.Random(12345)
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
        new_alerts: List[dict] = []

        for r in self.robots.values():
            self._update_status(r, dt)
            self._update_kinematics(r, dt)
            self._update_battery(r, dt)
            self._update_systems(r, dt)
            self._update_sensors(r, dt)
            self._maybe_inject_anomaly(r, dt)

            # Record metric snapshot (capped at 100 per robot)
            vel_mag = math.sqrt(r.vx ** 2 + r.vy ** 2 + r.vz ** 2)
            self._metrics_history[r.robot_id].append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "battery": round(r.battery, 2),
                "cpu_usage": round(r.cpu_usage, 2),
                "memory_usage": round(r.memory_usage, 2),
                "temperature": round(r.temperature, 2),
                "velocity_magnitude": round(vel_mag, 3),
            })

            # Anomaly detection -> alerts
            new_alerts.extend(self._detect_anomalies(r))

        # Keep only recent alerts (last 50)
        self.alerts = (self.alerts + new_alerts)[-50:]

    # ------------------------------------------------------------------
    # Status FSM
    # ------------------------------------------------------------------

    def _update_status(self, r: RobotSim, dt: float) -> None:
        r._status_timer -= dt

        if r.status == "charging":
            if r.battery >= r._charging_target:
                r.status = "active"
                r._status_timer = random.uniform(20, 60)
            return

        if r.status == "error":
            if r._status_timer <= 0:
                r.status = "maintenance"
                r._status_timer = random.uniform(5, 15)
            return

        if r.status == "maintenance":
            if r._status_timer <= 0:
                r.status = "active"
                r._status_timer = random.uniform(30, 90)
                r._sensor_fault = False
            return

        if r.status == "idle":
            if r._status_timer <= 0:
                r.status = "active"
                r._status_timer = random.uniform(20, 60)
            return

        # Status is "active"
        if r.battery < 15:
            r.status = "charging"
            r._charging_target = random.uniform(85, 98)
            return

        if r._status_timer <= 0:
            # Random state transition
            roll = random.random()
            if roll < 0.02:
                r.status = "error"
                r._status_timer = random.uniform(5, 20)
            elif roll < 0.07:
                r.status = "idle"
                r._status_timer = random.uniform(5, 15)
            else:
                r._status_timer = random.uniform(10, 40)

    # ------------------------------------------------------------------
    # Kinematics
    # ------------------------------------------------------------------

    def _update_kinematics(self, r: RobotSim, dt: float) -> None:
        if r.status in ("charging", "idle", "maintenance"):
            # Slow to a halt
            r.vx *= 0.9
            r.vy *= 0.9
            if r.status == "charging":
                # Drift toward charging station
                dx = r._charge_x - r.x
                dy = r._charge_y - r.y
                dist = math.sqrt(dx * dx + dy * dy)
                if dist > 1.0:
                    r.vx += 0.5 * dx / dist * dt
                    r.vy += 0.5 * dy / dist * dt
        elif r.status == "error":
            r.vx *= 0.95
            r.vy *= 0.95
        else:
            # Active: random walk with momentum
            r.heading += random.gauss(0, 0.3 * dt)
            target_speed = {"explorer": 2.0, "hauler": 1.2, "scout": 2.5,
                            "sentinel": 0.8, "mapper": 1.5, "relay": 0.5
                            }.get(r.robot_type, 1.5)
            r.vx += (target_speed * math.cos(r.heading) - r.vx) * 0.1
            r.vy += (target_speed * math.sin(r.heading) - r.vy) * 0.1
            # Slight vertical bobbing
            r.vz = 0.05 * math.sin(self._sim_time * 2 + hash(r.robot_id))

        r.x += r.vx * dt
        r.y += r.vy * dt
        r.z += r.vz * dt

        # Boundary clamping
        margin = 2.0
        if r.x < margin:
            r.x = margin; r.vx = abs(r.vx)
        elif r.x > self.area_size - margin:
            r.x = self.area_size - margin; r.vx = -abs(r.vx)
        if r.y < margin:
            r.y = margin; r.vy = abs(r.vy)
        elif r.y > self.area_size - margin:
            r.y = self.area_size - margin; r.vy = -abs(r.vy)

        r.z = max(0, min(r.z, 0.5))

    # ------------------------------------------------------------------
    # Battery
    # ------------------------------------------------------------------

    def _update_battery(self, r: RobotSim, dt: float) -> None:
        if r.status == "charging":
            r.battery = min(100, r.battery + 0.15 * dt)  # ~9%/min
        elif r.status == "active":
            drain = 0.01 * dt  # ~0.6%/min
            # Haulers drain faster
            if r.robot_type == "hauler":
                drain *= 1.5
            r.battery = max(0, r.battery - drain)
        elif r.status in ("idle", "maintenance"):
            r.battery = max(0, r.battery - 0.002 * dt)

    # ------------------------------------------------------------------
    # CPU / Memory / Temperature
    # ------------------------------------------------------------------

    def _update_systems(self, r: RobotSim, dt: float) -> None:
        if r.status == "active":
            target_cpu = 40 + 20 * abs(math.sin(self._sim_time * 0.1 + hash(r.robot_id)))
            target_mem = 45 + 15 * abs(math.cos(self._sim_time * 0.08 + hash(r.robot_id)))
            target_temp = 50 + 10 * abs(math.sin(self._sim_time * 0.05 + hash(r.robot_id)))
        elif r.status == "idle":
            target_cpu = 10 + 5 * random.random()
            target_mem = 30 + 5 * random.random()
            target_temp = 35 + 3 * random.random()
        elif r.status == "charging":
            target_cpu = 8
            target_mem = 25
            target_temp = 38
        elif r.status == "error":
            target_cpu = 80 + 15 * random.random()
            target_mem = 70 + 20 * random.random()
            target_temp = 65 + 10 * random.random()
        else:  # maintenance
            target_cpu = 20
            target_mem = 35
            target_temp = 40

        alpha = 0.05  # smoothing
        r.cpu_usage += alpha * (target_cpu - r.cpu_usage) + random.gauss(0, 0.5)
        r.memory_usage += alpha * (target_mem - r.memory_usage) + random.gauss(0, 0.3)
        r.temperature += alpha * (target_temp - r.temperature) + random.gauss(0, 0.2)

        r.cpu_usage = max(0, min(100, r.cpu_usage))
        r.memory_usage = max(0, min(100, r.memory_usage))
        r.temperature = max(15, min(110, r.temperature))

    # ------------------------------------------------------------------
    # Sensors (IMU / GPS)
    # ------------------------------------------------------------------

    def _update_sensors(self, r: RobotSim, dt: float) -> None:
        if r._sensor_fault:
            # Produce obviously bad readings
            r.imu_ax = random.gauss(5, 3)
            r.imu_ay = random.gauss(5, 3)
            r.imu_az = random.gauss(-5, 5)
            r.imu_gx = random.gauss(0, 2)
            r.imu_gy = random.gauss(0, 2)
            r.imu_gz = random.gauss(0, 2)
        else:
            # Accelerometer (gravity + motion noise)
            r.imu_ax = r.vx * 0.1 + random.gauss(0, 0.08)
            r.imu_ay = r.vy * 0.1 + random.gauss(0, 0.08)
            r.imu_az = -9.81 + random.gauss(0, 0.04)
            # Gyroscope
            r.imu_gx = random.gauss(0, 0.01)
            r.imu_gy = random.gauss(0, 0.01)
            heading_rate = (r.vy * math.cos(r.heading) - r.vx * math.sin(r.heading))
            r.imu_gz = heading_rate * 0.05 + random.gauss(0, 0.005)

        # GPS (map sim coords to lat/lon)
        r.gps_lat = 37.7749 + (r.x - 50) * 0.00001 + random.gauss(0, 0.000002)
        r.gps_lon = -122.4194 + (r.y - 50) * 0.00001 + random.gauss(0, 0.000002)
        r.gps_alt = 10.0 + r.z + random.gauss(0, 0.3)

    # ------------------------------------------------------------------
    # Anomaly injection
    # ------------------------------------------------------------------

    def _maybe_inject_anomaly(self, r: RobotSim, dt: float) -> None:
        r._anomaly_cooldown = max(0, r._anomaly_cooldown - dt)
        if r._anomaly_cooldown > 0:
            return

        roll = random.random()
        if roll < 0.0005:  # ~0.05% chance per tick
            # Temperature spike
            r.temperature = random.uniform(78, 95)
            r._anomaly_cooldown = random.uniform(20, 60)
        elif roll < 0.001:
            # Sensor fault
            r._sensor_fault = True
            r._anomaly_cooldown = random.uniform(15, 40)
        elif roll < 0.0015:
            # Sudden battery drop
            r.battery = max(0, r.battery - random.uniform(5, 15))
            r._anomaly_cooldown = random.uniform(30, 60)

    # ------------------------------------------------------------------
    # Anomaly detection -> alerts
    # ------------------------------------------------------------------

    def _detect_anomalies(self, r: RobotSim) -> List[dict]:
        alerts: List[dict] = []
        ts = datetime.now(timezone.utc).isoformat()

        if r.temperature > 75:
            sev = "critical" if r.temperature > 85 else "warning"
            alerts.append({
                "id": str(uuid.uuid4()),
                "robot_id": r.robot_id,
                "type": "high_temperature",
                "severity": sev,
                "message": f"Temperature at {r.temperature:.1f}°C on {r.name}",
                "timestamp": ts,
            })

        if r.battery < 20:
            sev = "critical" if r.battery < 10 else "warning"
            alerts.append({
                "id": str(uuid.uuid4()),
                "robot_id": r.robot_id,
                "type": "low_battery",
                "severity": sev,
                "message": f"Battery at {r.battery:.1f}% on {r.name}",
                "timestamp": ts,
            })

        if r._sensor_fault:
            alerts.append({
                "id": str(uuid.uuid4()),
                "robot_id": r.robot_id,
                "type": "sensor_malfunction",
                "severity": "warning",
                "message": f"IMU sensor readings anomalous on {r.name}",
                "timestamp": ts,
            })

        if r.status == "error":
            alerts.append({
                "id": str(uuid.uuid4()),
                "robot_id": r.robot_id,
                "type": "communication_loss",
                "severity": "critical",
                "message": f"Communication degraded with {r.name}",
                "timestamp": ts,
            })

        # Collision proximity: check pairwise distances
        for other in self.robots.values():
            if other.robot_id == r.robot_id:
                continue
            dist = math.sqrt((r.x - other.x) ** 2 + (r.y - other.y) ** 2)
            if dist < 3.0:
                alerts.append({
                    "id": str(uuid.uuid4()),
                    "robot_id": r.robot_id,
                    "type": "collision_proximity",
                    "severity": "warning" if dist > 1.5 else "critical",
                    "message": (
                        f"{r.name} within {dist:.1f} m of {other.name}"
                    ),
                    "timestamp": ts,
                })
                break  # one alert per robot per tick

        return alerts

    # ------------------------------------------------------------------
    # Public query methods
    # ------------------------------------------------------------------

    def get_telemetry(self) -> List[dict]:
        """Return telemetry snapshots for all robots."""
        return [self._robot_telemetry(r) for r in self.robots.values()]

    def get_robot(self, robot_id: str) -> Optional[dict]:
        """Return full state for one robot, or None."""
        r = self.robots.get(robot_id)
        if r is None:
            return None
        return self._robot_state(r)

    def get_all_robots(self) -> List[dict]:
        """Return full state (with name/type) for all robots."""
        return [self._robot_state(r) for r in self.robots.values()]

    def get_alerts(self) -> List[dict]:
        """Return the current alert list (most recent 50)."""
        return list(self.alerts)

    def get_metrics(self, robot_id: str) -> Optional[dict]:
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
        return {
            "robot_id": r.robot_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "position": {"x": round(r.x, 4), "y": round(r.y, 4), "z": round(r.z, 4)},
            "velocity": {"vx": round(r.vx, 4), "vy": round(r.vy, 4), "vz": round(r.vz, 4)},
            "battery": round(r.battery, 2),
            "status": r.status,
            "cpu_usage": round(r.cpu_usage, 2),
            "memory_usage": round(r.memory_usage, 2),
            "temperature": round(r.temperature, 2),
            "sensors": {
                "imu": {
                    "ax": round(r.imu_ax, 4),
                    "ay": round(r.imu_ay, 4),
                    "az": round(r.imu_az, 4),
                    "gx": round(r.imu_gx, 4),
                    "gy": round(r.imu_gy, 4),
                    "gz": round(r.imu_gz, 4),
                },
                "gps": {
                    "lat": round(r.gps_lat, 7),
                    "lon": round(r.gps_lon, 7),
                    "alt": round(r.gps_alt, 2),
                },
            },
        }

    @staticmethod
    def _robot_state(r: RobotSim) -> dict:
        d = FleetSimulator._robot_telemetry(r)
        d["name"] = r.name
        d["robot_type"] = r.robot_type
        return d
