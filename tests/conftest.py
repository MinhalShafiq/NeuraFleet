"""Shared helpers: load each service's modules by path (they all call theirs ``main``)."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"

# Services import the shared contract as ``shared.*`` (in Docker it sits at /app/shared).
sys.path.insert(0, str(BACKEND))

# Only the service directories themselves; ``main`` collisions are avoided by load_module.
SERVICE_DIRS = {
    "gateway": BACKEND / "gateway",
    "lidar-service": BACKEND / "lidar_service",
    "telemetry-service": BACKEND / "telemetry_service",
    "rag-service": BACKEND / "rag_service",
}
# robot_agent is deliberately NOT in SERVICE_DIRS: test_manifests.py parametrizes over
# every entry expecting a matching k8s/<name>/deployment.yaml, and robot_agent has none
# on purpose (plan-messaging.md is docker-compose only; six per-robot Deployments with
# distinct ROBOT_ID env vars is a real design question, not something to default into
# here). test_robot_agent.py loads it directly - see load_robot_agent_module() below.
ROBOT_AGENT_DIR = BACKEND / "robot_agent"


def _load_from_dir(service_dir: Path, filename: str):
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


def load_module(service: str, filename: str):
    """Import ``backend/<service>/<filename>.py`` under a unique module name."""
    return _load_from_dir(SERVICE_DIRS[service], filename)


def load_robot_agent_module(filename: str):
    """Same as ``load_module``, for ``backend/robot_agent`` (kept out of SERVICE_DIRS -
    see the comment above it)."""
    return _load_from_dir(ROBOT_AGENT_DIR, filename)


@pytest.fixture(scope="session")
def root() -> Path:
    return ROOT
