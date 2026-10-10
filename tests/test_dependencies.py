"""Lockfiles (plan 4.4): reproducible installs, and no cross-service version skew."""

import re
from collections import defaultdict

import pytest
from conftest import BACKEND

SERVICES = ["gateway", "lidar_service", "telemetry_service", "rag_service", "robot_agent"]


def _pins(path):
    out = {}
    for line in path.read_text().splitlines():
        m = re.match(r"^([A-Za-z0-9_.\-\[\]]+)==([^\s;#]+)", line)
        if m:
            out[re.sub(r"\[.*\]", "", m.group(1)).lower().replace("_", "-")] = m.group(2)
    return out


@pytest.mark.parametrize("svc", SERVICES)
def test_lock_honours_every_direct_pin(svc):
    direct = _pins(BACKEND / svc / "requirements.txt")
    locked = _pins(BACKEND / svc / "requirements.lock")
    assert direct, svc
    for name, version in direct.items():
        assert locked.get(name) == version, (
            f"{svc}: {name}=={version} in requirements.txt but {locked.get(name)} in the lock"
        )


@pytest.mark.parametrize("svc", SERVICES)
def test_lock_pins_transitive_dependencies(svc):
    assert len(_pins(BACKEND / svc / "requirements.lock")) > len(
        _pins(BACKEND / svc / "requirements.txt")
    )


def test_services_agree_on_shared_package_versions():
    """CI installs all four locks into one environment; a conflict would break it (this caught
    websockets 12.0 vs 17.2) and, worse, means services are tested on versions they don't ship."""
    seen = defaultdict(dict)
    for svc in SERVICES:
        for name, version in _pins(BACKEND / svc / "requirements.lock").items():
            seen[name][svc] = version
    conflicts = {n: v for n, v in seen.items() if len(set(v.values())) > 1}
    assert not conflicts, conflicts


def test_numpy_stays_below_2_in_every_lock_that_has_it():
    for svc in SERVICES:
        v = _pins(BACKEND / svc / "requirements.lock").get("numpy")
        assert v is None or int(v.split(".")[0]) < 2, svc


def test_dockerfiles_install_from_the_lock_not_the_loose_requirements():
    for svc in SERVICES:
        text = (BACKEND / svc / "Dockerfile").read_text()
        assert "requirements.lock" in text and "requirements.txt" not in text, svc
        assert "HEALTHCHECK" in text and "AS builder" in text and "AS runtime" in text, svc
        assert "build-essential" not in text, f"{svc}: compilers must not ship in the runtime image"
