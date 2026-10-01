"""Kubernetes probes must hit routes the service actually serves (plan 0.1),
and stateful services must not be scaled out (plan 0.2)."""
import re

import pytest
import yaml

from conftest import ROOT, SERVICE_DIRS, load_module

STATEFUL = ["telemetry-service", "lidar-service"]


def _deployment(service):
    with open(ROOT / "k8s" / service / "deployment.yaml") as f:
        return next(d for d in yaml.safe_load_all(f) if d and d["kind"] == "Deployment")


def _routes(service):
    app = load_module(service, "main").app
    return {r.path for r in app.routes}


@pytest.mark.parametrize("service", sorted(SERVICE_DIRS))
def test_probe_paths_are_served(service):
    dep = _deployment(service)
    for container in dep["spec"]["template"]["spec"]["containers"]:
        for probe in ("livenessProbe", "readinessProbe"):
            path = container[probe]["httpGet"]["path"]
            assert path in _routes(service), f"{service} {probe} -> {path} is not a route"


@pytest.mark.parametrize("service", STATEFUL)
def test_stateful_services_run_single_replica(service):
    assert _deployment(service)["spec"]["replicas"] == 1


@pytest.mark.parametrize("service", STATEFUL)
def test_stateful_services_run_single_worker(service):
    dockerfile = (SERVICE_DIRS[service] / "Dockerfile").read_text()
    assert re.search(r'"--workers",\s*"1"', dockerfile)
