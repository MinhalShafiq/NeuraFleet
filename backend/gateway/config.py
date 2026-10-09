"""
NeuraFleet API Gateway - Configuration

All settings are read from environment variables with sensible defaults
for container-based deployment.  Override via .env or shell exports.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    """Immutable gateway configuration."""

    # Internal microservice URLs
    lidar_service_url: str = os.getenv(
        "LIDAR_SERVICE_URL", "http://lidar-service:8001"
    )
    telemetry_service_url: str = os.getenv(
        "TELEMETRY_SERVICE_URL", "http://telemetry-service:8002"
    )
    rag_service_url: str = os.getenv(
        "RAG_SERVICE_URL", "http://rag-service:8003"
    )

    # Gateway settings
    host: str = os.getenv("GATEWAY_HOST", "0.0.0.0")
    port: int = int(os.getenv("GATEWAY_PORT", "8000"))
    debug: bool = os.getenv("GATEWAY_DEBUG", "false").lower() == "true"

    # Timeouts (seconds)
    http_timeout: float = float(os.getenv("HTTP_TIMEOUT", "10.0"))
    ws_reconnect_delay: float = float(os.getenv("WS_RECONNECT_DELAY", "2.0"))


settings = Settings()
