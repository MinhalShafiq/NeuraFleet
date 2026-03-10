"""
NeuraFleet Shared Pydantic Models

Defines all data types used across NeuraFleet microservices for
fleet telemetry, LiDAR point clouds, alerts, and RAG interactions.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class RobotStatus(str, Enum):
    active = "active"
    idle = "idle"
    charging = "charging"
    error = "error"
    maintenance = "maintenance"


class RobotType(str, Enum):
    explorer = "explorer"
    hauler = "hauler"
    sentinel = "sentinel"
    mapper = "mapper"
    relay = "relay"
    scout = "scout"


class AlertSeverity(str, Enum):
    info = "info"
    warning = "warning"
    critical = "critical"


class AlertType(str, Enum):
    high_temperature = "high_temperature"
    low_battery = "low_battery"
    sensor_malfunction = "sensor_malfunction"
    communication_loss = "communication_loss"
    collision_proximity = "collision_proximity"


# ---------------------------------------------------------------------------
# Geometry / Sensor primitives
# ---------------------------------------------------------------------------

class Position(BaseModel):
    x: float = Field(description="X coordinate in metres")
    y: float = Field(description="Y coordinate in metres")
    z: float = Field(default=0.0, description="Z coordinate in metres")


class Velocity(BaseModel):
    vx: float = Field(default=0.0, description="Velocity X m/s")
    vy: float = Field(default=0.0, description="Velocity Y m/s")
    vz: float = Field(default=0.0, description="Velocity Z m/s")


class IMUData(BaseModel):
    """Inertial Measurement Unit reading."""
    ax: float = Field(description="Accelerometer X (m/s^2)")
    ay: float = Field(description="Accelerometer Y (m/s^2)")
    az: float = Field(description="Accelerometer Z (m/s^2)")
    gx: float = Field(description="Gyroscope X (rad/s)")
    gy: float = Field(description="Gyroscope Y (rad/s)")
    gz: float = Field(description="Gyroscope Z (rad/s)")


class GPSData(BaseModel):
    lat: float = Field(description="Latitude in degrees")
    lon: float = Field(description="Longitude in degrees")
    alt: float = Field(default=0.0, description="Altitude in metres")


class SensorData(BaseModel):
    imu: IMUData
    gps: GPSData


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------

class TelemetryData(BaseModel):
    """Full telemetry snapshot for a single robot."""
    robot_id: str
    timestamp: str = Field(description="ISO-8601 timestamp")
    position: Position
    velocity: Velocity
    battery: float = Field(ge=0, le=100, description="Battery percentage")
    status: RobotStatus
    cpu_usage: float = Field(ge=0, le=100)
    memory_usage: float = Field(ge=0, le=100)
    temperature: float = Field(description="Degrees Celsius")
    sensors: SensorData


# ---------------------------------------------------------------------------
# Robot state (extended with metadata)
# ---------------------------------------------------------------------------

class RobotState(BaseModel):
    """Full state of a robot including identity and telemetry."""
    robot_id: str
    name: str
    robot_type: RobotType
    timestamp: str
    position: Position
    velocity: Velocity
    battery: float = Field(ge=0, le=100)
    status: RobotStatus
    cpu_usage: float = Field(ge=0, le=100)
    memory_usage: float = Field(ge=0, le=100)
    temperature: float
    sensors: SensorData


# ---------------------------------------------------------------------------
# LiDAR
# ---------------------------------------------------------------------------

class LidarScan(BaseModel):
    """A single LiDAR scan frame."""
    robot_id: str
    timestamp: str
    points: List[List[float]] = Field(
        description="List of [x, y, z, intensity] points"
    )
    frame_id: int
    num_points: int


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

class Alert(BaseModel):
    id: str
    robot_id: str
    type: AlertType
    severity: AlertSeverity
    message: str
    timestamp: str


# ---------------------------------------------------------------------------
# RAG
# ---------------------------------------------------------------------------

class RAGQuery(BaseModel):
    query: str = Field(description="Natural-language question")
    robot_id: Optional[str] = Field(
        default=None, description="Optional robot ID to scope the query"
    )


class RAGResponse(BaseModel):
    response: str
    sources: List[str]
    query: str


# ---------------------------------------------------------------------------
# Metrics history
# ---------------------------------------------------------------------------

class MetricPoint(BaseModel):
    timestamp: str
    battery: float
    cpu_usage: float
    memory_usage: float
    temperature: float
    velocity_magnitude: float


class MetricsHistory(BaseModel):
    robot_id: str
    points: List[MetricPoint]
