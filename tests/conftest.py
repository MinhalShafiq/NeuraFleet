"""Shared helpers: load each service's modules by path (they all call theirs ``main``)."""
import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"

# Only the service directories themselves; ``main`` collisions are avoided by load_module.
SERVICE_DIRS = {
    "gateway": BACKEND / "gateway",
    "lidar-service": BACKEND / "lidar_service",
    "telemetry-service": BACKEND / "telemetry_service",
    "rag-service": BACKEND / "rag_service",
}


def load_module(service: str, filename: str):
    """Import ``backend/<service>/<filename>.py`` under a unique module name."""
    service_dir = SERVICE_DIRS[service]
    name = f"{service_dir.name}__{filename}"
    if name in sys.modules:
        return sys.modules[name]
    sys.path.insert(0, str(service_dir))
    try:
        spec = importlib.util.spec_from_file_location(name, service_dir / f"{filename}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(service_dir))
    return mod


@pytest.fixture(scope="session")
def root() -> Path:
    return ROOT
