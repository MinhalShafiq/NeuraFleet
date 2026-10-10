"""
NeuraFleet Shared Pydantic Models

The API contract used by every NeuraFleet service: fleet telemetry, LiDAR point
clouds, alerts, RAG interactions and health.  Services declare these as
``response_model=`` so responses are validated and ``/docs`` is accurate.

``demo`` is True on anything the gateway fabricated because a backing service was
unreachable, so the dashboard can show a DEMO DATA badge instead of silently
looking healthy.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

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
    demo: bool = Field(default=False, description="True if fabricated by the gateway fallback")


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
    demo: bool = Field(default=False, description="True if fabricated by the gateway fallback")


# ---------------------------------------------------------------------------
# LiDAR
# ---------------------------------------------------------------------------


class LidarScan(BaseModel):
    """A single LiDAR scan frame."""

    robot_id: str
    timestamp: str
    points: list[list[float]] = Field(
        description="List of [x, y, z, intensity] points in the WORLD frame (metres)"
    )
    frame_id: int
    num_points: int
    origin: Position | None = Field(
        default=None,
        description="Where the robot (sensor base) was in the world frame; the sensor is 1.8 m above. "
        "Viewers draw points relative to this, otherwise a scan taken 50 m from the origin "
        "lands 50 m away from a camera looking at the origin.",
    )
    heading: float | None = Field(default=None, description="Sensor heading in radians, 0 = +X")
    demo: bool = False


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


class Alert(BaseModel):
    """A stateful alert: raised once per (robot, type), updated while it persists."""

    id: str
    robot_id: str
    type: AlertType
    severity: AlertSeverity
    message: str
    timestamp: str = Field(description="When the alert was first raised (== first_seen)")
    first_seen: str | None = None
    last_seen: str | None = None
    count: int = Field(default=1, description="Number of ticks the condition has been observed")
    resolved_at: str | None = Field(default=None, description="Set once the condition cleared")
    demo: bool = False


# ---------------------------------------------------------------------------
# RAG
# ---------------------------------------------------------------------------


class RAGQuery(BaseModel):
    query: str = Field(min_length=1, max_length=2000, description="Natural-language question")
    robot_id: str | None = Field(default=None, description="Optional robot ID to scope the query")


class RAGResponse(BaseModel):
    response: str
    sources: list[str]
    query: str
    demo: bool = False


class DocumentIn(BaseModel):
    text: str = Field(min_length=1)
    metadata: dict[str, Any] | None = None


class IngestRequest(BaseModel):
    documents: list[DocumentIn] = Field(min_length=1)


class IngestResponse(BaseModel):
    added: int


class DocumentOut(BaseModel):
    id: str
    text: str = Field(description="First 200 characters")
    metadata: dict[str, Any] | None = None


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
    points: list[MetricPoint]
    demo: bool = False


# ---------------------------------------------------------------------------
# Health / errors
# ---------------------------------------------------------------------------


class ErrorDetail(BaseModel):
    detail: str


class LivenessResponse(BaseModel):
    status: str


class TelemetryHealth(BaseModel):
    status: str
    robots: int = 0
    alerts: int = 0


class LidarHealth(BaseModel):
    status: str
    robots: list[str] = []
    environment_obstacles: int = 0


class RagHealth(BaseModel):
    status: str
    documents: int = 0
    llm_available: bool = False


class GatewayHealth(BaseModel):
    status: str
    services: dict[str, str] = Field(description="healthy | degraded | unreachable, per service")
    mode: dict[str, str] = Field(description="live | mock: where each service's data comes from")
