"""
NeuraFleet Robot Physics
========================

The single-robot simulation model: kinematics, battery, CPU/memory/temperature,
IMU/GPS sensors, and random anomaly injection.

This is shared because two different runtimes advance the exact same model:

- ``telemetry_service.robot_simulator.FleetSimulator`` ticks all six robots in one
  process (the local/dev mode, and what the existing unit tests exercise).
- ``robot_agent`` ticks ONE robot in its own process and publishes it over MQTT
  (see ``plan-messaging.md``); ``telemetry_service.robot_simulator.FleetAggregator``
  then fills the same ``RobotSim`` fields from the received messages instead of
  from a local tick.

Keeping the formulas here means both runtimes can never drift apart: there is one
definition of how a robot moves and ages, not two copies that quietly diverge.
"""

from __future__ import annotations

import math
import random
import zlib
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class RobotConfig:
    robot_id: str
    name: str
    robot_type: str


FLEET_CONFIG: list[RobotConfig] = [
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

    # Commanded state (plan-messaging.md Phase C). A command changes these; the
    # physics below respects them. ``None`` goal = drive around on its own.
    estopped: bool = False
    goal_x: float | None = None
    goal_y: float | None = None
    speed_limit: float | None = None  # m/s, None = the robot type's own target speed


def stable_phase(robot_id: str) -> int:
    """Process-independent replacement for ``hash(str)`` (PYTHONHASHSEED-safe)."""
    return zlib.crc32(robot_id.encode()) % 100


TARGET_SPEED = {
    "explorer": 2.0,
    "hauler": 1.2,
    "scout": 2.5,
    "sentinel": 0.8,
    "mapper": 1.5,
    "relay": 0.5,
}


# ---------------------------------------------------------------------------
# Status FSM
# ---------------------------------------------------------------------------


def update_status(r: RobotSim, dt: float, rng: random.Random) -> None:
    r._status_timer -= dt

    if r.status == "charging":
        if r.battery >= r._charging_target:
            r.status = "active"
            r._status_timer = rng.uniform(20, 60)
        return

    if r.status == "error":
        if r._status_timer <= 0:
            r.status = "maintenance"
            r._status_timer = rng.uniform(5, 15)
        return

    if r.status == "maintenance":
        if r._status_timer <= 0:
            r.status = "active"
            r._status_timer = rng.uniform(30, 90)
            r._sensor_fault = False
        return

    if r.status == "idle":
        if r._status_timer <= 0:
            r.status = "active"
            r._status_timer = rng.uniform(20, 60)
        return

    # Status is "active"
    if r.battery < 15:
        r.status = "charging"
        r._charging_target = rng.uniform(85, 98)
        return

    if r._status_timer <= 0:
        # Random state transition
        roll = rng.random()
        if roll < 0.02:
            r.status = "error"
            r._status_timer = rng.uniform(5, 20)
        elif roll < 0.07:
            r.status = "idle"
            r._status_timer = rng.uniform(5, 15)
        else:
            r._status_timer = rng.uniform(10, 40)


# ---------------------------------------------------------------------------
# Kinematics
# ---------------------------------------------------------------------------


def update_kinematics(
    r: RobotSim, dt: float, rng: random.Random, area_size: float, sim_time: float
) -> None:
    if r.estopped:
        # Hard stop: no drive command is honoured until a "resume" command clears it.
        r.vx *= 0.5
        r.vy *= 0.5
    elif r.status in ("charging", "idle", "maintenance"):
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
        target_speed = (
            r.speed_limit if r.speed_limit is not None else TARGET_SPEED.get(r.robot_type, 1.5)
        )
        if r.goal_x is not None and r.goal_y is not None:
            # Commanded goal (plan-messaging.md Phase C): head straight for it, slow to a
            # stop on arrival rather than overshooting and orbiting it.
            dx, dy = r.goal_x - r.x, r.goal_y - r.y
            dist = math.hypot(dx, dy)
            if dist < 0.3:
                r.goal_x = r.goal_y = None
                target_speed = 0.0
            else:
                r.heading = math.atan2(dy, dx)
                target_speed = min(target_speed, dist / max(dt, 1e-6))
        else:
            # Active: random walk with momentum
            r.heading += rng.gauss(0, 0.3 * dt)
        r.vx += (target_speed * math.cos(r.heading) - r.vx) * 0.1
        r.vy += (target_speed * math.sin(r.heading) - r.vy) * 0.1
        # Slight vertical bobbing
        r.vz = 0.05 * math.sin(sim_time * 2 + stable_phase(r.robot_id))

    r.x += r.vx * dt
    r.y += r.vy * dt
    r.z += r.vz * dt

    # Boundary clamping
    margin = 2.0
    if r.x < margin:
        r.x = margin
        r.vx = abs(r.vx)
    elif r.x > area_size - margin:
        r.x = area_size - margin
        r.vx = -abs(r.vx)
    if r.y < margin:
        r.y = margin
        r.vy = abs(r.vy)
    elif r.y > area_size - margin:
        r.y = area_size - margin
        r.vy = -abs(r.vy)

    r.z = max(0, min(r.z, 0.5))


# ---------------------------------------------------------------------------
# Battery
# ---------------------------------------------------------------------------


def update_battery(r: RobotSim, dt: float) -> None:
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


# ---------------------------------------------------------------------------
# CPU / Memory / Temperature
# ---------------------------------------------------------------------------


def update_systems(r: RobotSim, dt: float, rng: random.Random, sim_time: float) -> None:
    if r.status == "active":
        target_cpu = 40 + 20 * abs(math.sin(sim_time * 0.1 + stable_phase(r.robot_id)))
        target_mem = 45 + 15 * abs(math.cos(sim_time * 0.08 + stable_phase(r.robot_id)))
        target_temp = 50 + 10 * abs(math.sin(sim_time * 0.05 + stable_phase(r.robot_id)))
    elif r.status == "idle":
        target_cpu = 10 + 5 * rng.random()
        target_mem = 30 + 5 * rng.random()
        target_temp = 35 + 3 * rng.random()
    elif r.status == "charging":
        target_cpu = 8
        target_mem = 25
        target_temp = 38
    elif r.status == "error":
        target_cpu = 80 + 15 * rng.random()
        target_mem = 70 + 20 * rng.random()
        target_temp = 65 + 10 * rng.random()
    else:  # maintenance
        target_cpu = 20
        target_mem = 35
        target_temp = 40

    alpha = 0.05  # smoothing
    r.cpu_usage += alpha * (target_cpu - r.cpu_usage) + rng.gauss(0, 0.5)
    r.memory_usage += alpha * (target_mem - r.memory_usage) + rng.gauss(0, 0.3)
    r.temperature += alpha * (target_temp - r.temperature) + rng.gauss(0, 0.2)

    r.cpu_usage = max(0, min(100, r.cpu_usage))
    r.memory_usage = max(0, min(100, r.memory_usage))
    r.temperature = max(15, min(110, r.temperature))


# ---------------------------------------------------------------------------
# Sensors (IMU / GPS)
# ---------------------------------------------------------------------------


def update_sensors(r: RobotSim, dt: float, rng: random.Random) -> None:
    if r._sensor_fault:
        # Produce obviously bad readings
        r.imu_ax = rng.gauss(5, 3)
        r.imu_ay = rng.gauss(5, 3)
        r.imu_az = rng.gauss(-5, 5)
        r.imu_gx = rng.gauss(0, 2)
        r.imu_gy = rng.gauss(0, 2)
        r.imu_gz = rng.gauss(0, 2)
    else:
        # Accelerometer (gravity + motion noise)
        r.imu_ax = r.vx * 0.1 + rng.gauss(0, 0.08)
        r.imu_ay = r.vy * 0.1 + rng.gauss(0, 0.08)
        r.imu_az = -9.81 + rng.gauss(0, 0.04)
        # Gyroscope
        r.imu_gx = rng.gauss(0, 0.01)
        r.imu_gy = rng.gauss(0, 0.01)
        heading_rate = r.vy * math.cos(r.heading) - r.vx * math.sin(r.heading)
        r.imu_gz = heading_rate * 0.05 + rng.gauss(0, 0.005)

    # GPS (map sim coords to lat/lon)
    r.gps_lat = 37.7749 + (r.x - 50) * 0.00001 + rng.gauss(0, 0.000002)
    r.gps_lon = -122.4194 + (r.y - 50) * 0.00001 + rng.gauss(0, 0.000002)
    r.gps_alt = 10.0 + r.z + rng.gauss(0, 0.3)


# ---------------------------------------------------------------------------
# Anomaly injection
# ---------------------------------------------------------------------------


def maybe_inject_anomaly(r: RobotSim, dt: float, rng: random.Random) -> None:
    r._anomaly_cooldown = max(0, r._anomaly_cooldown - dt)
    if r._anomaly_cooldown > 0:
        return

    roll = rng.random()
    if roll < 0.0005:  # ~0.05% chance per tick
        # Temperature spike
        r.temperature = rng.uniform(78, 95)
        r._anomaly_cooldown = rng.uniform(20, 60)
    elif roll < 0.001:
        # Sensor fault
        r._sensor_fault = True
        r._anomaly_cooldown = rng.uniform(15, 40)
    elif roll < 0.0015:
        # Sudden battery drop
        r.battery = max(0, r.battery - rng.uniform(5, 15))
        r._anomaly_cooldown = rng.uniform(30, 60)


def advance_robot(
    r: RobotSim, dt: float, rng: random.Random, area_size: float, sim_time: float
) -> None:
    """Advance one robot by *dt* seconds. This is the one place a tick happens -
    both ``FleetSimulator`` (6 robots, one process) and ``robot_agent`` (1 robot,
    its own process) call this and nothing else to move a robot forward."""
    update_status(r, dt, rng)
    update_kinematics(r, dt, rng, area_size, sim_time)
    update_battery(r, dt)
    update_systems(r, dt, rng, sim_time)
    update_sensors(r, dt, rng)
    maybe_inject_anomaly(r, dt, rng)


# ---------------------------------------------------------------------------
# Serialisation (shared telemetry shape)
# ---------------------------------------------------------------------------


def robot_telemetry(r: RobotSim, timestamp: str) -> dict:
    return {
        "robot_id": r.robot_id,
        "timestamp": timestamp,
        "position": {"x": round(r.x, 4), "y": round(r.y, 4), "z": round(r.z, 4)},
        "velocity": {"vx": round(r.vx, 4), "vy": round(r.vy, 4), "vz": round(r.vz, 4)},
        "battery": round(r.battery, 2),
        "status": r.status,
        "cpu_usage": round(r.cpu_usage, 2),
        "memory_usage": round(r.memory_usage, 2),
        "temperature": round(r.temperature, 2),
        # Commanded state (plan-messaging.md Phase C): so a viewer can see that a robot
        # is e-stopped, not just send the command blindly into the dark.
        "estopped": r.estopped,
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
