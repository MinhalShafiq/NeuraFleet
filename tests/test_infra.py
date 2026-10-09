"""Static checks on the k8s manifests, compose file and Terraform (Phase 3).

These run without a cluster; they encode the review findings so they cannot regress:
  3.2 no GPU, 3.1 control plane not open, 3.5 writable /tmp, 3.6 no Redis,
  3.8 frontend deployed, 3.9 long WebSocket timeout, 3.13 no secrets in git.
"""

import glob
import json
import re
from pathlib import Path

import pytest
import yaml
from conftest import ROOT


def _docs(path):
    with open(path) as f:
        return [d for d in yaml.safe_load_all(f) if d]


def _all_k8s():
    out = []
    for path in sorted(glob.glob(str(ROOT / "k8s" / "**" / "*.yaml"), recursive=True)):
        for d in _docs(path):
            out.append((path, d))
    return out


K8S = _all_k8s()


def _of_kind(kind):
    return [d for _, d in K8S if d["kind"] == kind]


def _pod_specs():
    return [(d["metadata"]["name"], d["spec"]["template"]["spec"]) for d in _of_kind("Deployment")]


# ---------------------------------------------------------------- 3.2 GPUs
def test_no_gpu_requests_anywhere_in_k8s():
    text = "".join(
        Path(p).read_text() for p in glob.glob(str(ROOT / "k8s" / "**" / "*.yaml"), recursive=True)
    )
    assert "nvidia.com/gpu" not in text
    for _, spec in _pod_specs():
        assert "nodeSelector" not in spec or "gpu" not in spec["nodeSelector"]


@pytest.mark.parametrize("env", ["dev", "staging", "prod"])
def test_no_gpu_nodes_provisioned(env):
    tfvars = (ROOT / f"terraform/environments/{env}.tfvars").read_text()
    assert re.search(r"^gpu_node_count\s*=\s*0\b", tfvars, re.M)


# ---------------------------------------------------------------- 3.1 control plane
def test_control_plane_is_not_open_to_the_internet():
    tf = "".join(Path(p).read_text() for p in glob.glob(str(ROOT / "terraform/modules/k8s/*.tf")))
    assert "0.0.0.0/0" not in tf
    assert 'dynamic "cidr_blocks"' in tf


def test_authorized_cidrs_is_required_and_rejects_open_range():
    tf = (ROOT / "terraform/variables.tf").read_text()
    block = tf[tf.index('variable "authorized_cidrs"') :]
    block = block[: block.index("\n}\n") + 3]
    assert "default" not in re.sub(r"<<-EOT.*?EOT", "", block, flags=re.S), (
        "authorized_cidrs must have no default"
    )
    assert '"0.0.0.0/0"' in block and "validation" in block


# ---------------------------------------------------------------- 3.5 writable volumes
def test_read_only_root_workloads_have_a_tmp_emptydir():
    for name, spec in _pod_specs():
        volumes = {v["name"]: v for v in spec.get("volumes", [])}
        for c in spec["containers"]:
            if c["securityContext"].get("readOnlyRootFilesystem"):
                mounts = {m["mountPath"]: m["name"] for m in c.get("volumeMounts", [])}
                assert "/tmp" in mounts, (
                    f"{name}: readOnlyRootFilesystem but nothing mounted at /tmp"
                )
                assert "emptyDir" in volumes[mounts["/tmp"]], f"{name}: /tmp must be an emptyDir"


def test_every_workload_has_read_only_root_and_runs_non_root():
    for name, spec in _pod_specs():
        assert spec["securityContext"]["runAsNonRoot"] is True, name
        for c in spec["containers"]:
            sc = c["securityContext"]
            assert sc.get("readOnlyRootFilesystem") is True, f"{name}: root filesystem is writable"
            assert sc.get("allowPrivilegeEscalation") is False, name


# ---------------------------------------------------------------- 3.6 Redis
def test_no_redis_left():
    for rel in [
        "docker-compose.yml",
        "k8s/configmap.yaml",
        "backend/gateway/config.py",
        "backend/gateway/requirements.txt",
    ]:
        assert "redis" not in (ROOT / rel).read_text().lower(), rel
    assert not (ROOT / "k8s" / "redis").exists()


# ---------------------------------------------------------------- 3.13 secrets
def test_no_secret_manifest_or_key_material_in_repo():
    assert not (ROOT / "k8s" / "secrets.yaml").exists()
    assert not _of_kind("Secret"), "Secrets must be created out-of-band, not committed"
    for path in glob.glob(str(ROOT / "**" / "*"), recursive=True):
        if re.search(
            r"(node_modules|\.venv|\.git/|/dist/|__pycache__|OPTIMIZATION_PLAN)", path
        ) or not re.search(r"\.(ya?ml|py|js|jsx|tf|tfvars|sh|env|example)$", path):
            continue
        assert not re.search(r"sk-ant-[A-Za-z0-9_-]{20,}", Path(path).read_text(errors="ignore")), (
            path
        )


def test_secret_reference_is_optional_so_pods_start_without_it():
    rag = next(s for n, s in _pod_specs() if n == "rag-service")
    ref = next(e for e in rag["containers"][0]["env"] if e["name"] == "ANTHROPIC_API_KEY")[
        "valueFrom"
    ]["secretKeyRef"]
    assert ref.get("optional") is True


# ---------------------------------------------------------------- 3.8 / 3.9 routing
def test_services_select_real_deployments_and_ports_match():
    deployments = {d["metadata"]["name"]: d for d in _of_kind("Deployment")}
    for svc in _of_kind("Service"):
        name = svc["metadata"]["name"]
        assert name in deployments, f"Service {name} has no Deployment"
        labels = deployments[name]["spec"]["template"]["metadata"]["labels"]
        assert all(labels.get(k) == v for k, v in svc["spec"]["selector"].items()), name
        container_ports = {
            p["containerPort"]
            for c in deployments[name]["spec"]["template"]["spec"]["containers"]
            for p in c.get("ports", [])
        }
        assert {p["targetPort"] for p in svc["spec"]["ports"]} <= container_ports, name


def test_ingress_backends_exist():
    services = {
        s["metadata"]["name"]: {p["port"] for p in s["spec"]["ports"]} for s in _of_kind("Service")
    }
    ing = _of_kind("Ingress")
    assert ing
    for i in ing:
        backends = [i["spec"]["defaultBackend"]["service"]] if "defaultBackend" in i["spec"] else []
        for rule in i["spec"]["rules"]:
            backends += [p["backend"]["service"] for p in rule["http"]["paths"]]
        for b in backends:
            assert b["name"] in services, f"Ingress points at missing Service {b['name']}"
            assert b["port"]["number"] in services[b["name"]], (
                f"{b['name']}: port {b['port']['number']} not exposed"
            )


def test_dashboard_is_deployed_and_routed():
    assert "frontend" in {d["metadata"]["name"] for d in _of_kind("Deployment")}
    hosts = {
        r["host"]: r["http"]["paths"][0]["backend"]["service"]["name"]
        for i in _of_kind("Ingress")
        for r in i["spec"]["rules"]
    }
    assert "frontend" in hosts.values() and "gateway" in hosts.values()


def test_websocket_carrying_services_use_long_backend_timeout():
    bc = {b["metadata"]["name"]: b for b in _of_kind("BackendConfig")}
    assert bc
    for svc in _of_kind("Service"):
        name = svc["metadata"]["name"]
        if name not in ("gateway", "frontend"):
            continue  # only these sit behind the load balancer
        ref = json.loads(svc["metadata"]["annotations"]["cloud.google.com/backend-config"])[
            "default"
        ]
        assert bc[ref]["spec"]["timeoutSec"] >= 3600, (
            f"{name}: LB would cut WebSockets at {bc[ref]['spec']['timeoutSec']} s"
        )


def test_managed_certificate_covers_every_ingress_host():
    certs = _of_kind("ManagedCertificate")
    covered = {d for c in certs for d in c["spec"]["domains"]}
    for i in _of_kind("Ingress"):
        assert {r["host"] for r in i["spec"]["rules"]} <= covered


def test_frontend_nginx_is_unprivileged_and_exposes_health():
    conf = (ROOT / "frontend/dashboard/nginx.conf").read_text()
    assert re.search(r"listen\s+8080;", conf) and "location = /healthz" in conf
    assert "nginx-unprivileged" in (ROOT / "frontend/dashboard/Dockerfile").read_text()


# ---------------------------------------------------------------- compose consistency
def test_compose_depends_on_only_defined_services():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    for name, svc in compose["services"].items():
        for dep in svc.get("depends_on") or {}:
            assert dep in compose["services"], f"{name} depends on undefined service {dep}"


# ---------------------------------------------------------------- 3.10 immutable images
def test_no_manifest_uses_a_floating_tag():
    for name, spec in _pod_specs():
        for c in spec["containers"]:
            tag = (
                c["image"].rsplit(":", 1)[-1] if ":" in c["image"].rsplit("/", 1)[-1] else "latest"
            )
            assert tag != "latest", f"{name}: {c['image']} floats"
            assert c.get("imagePullPolicy") == "IfNotPresent", (
                f"{name}: set imagePullPolicy explicitly"
            )


def test_kustomization_covers_every_manifest_and_image():
    kz = yaml.safe_load((ROOT / "k8s" / "kustomization.yaml").read_text())
    listed = set(kz["resources"])
    on_disk = {
        str(Path(p).relative_to(ROOT / "k8s"))
        for p in glob.glob(str(ROOT / "k8s" / "**" / "*.yaml"), recursive=True)
        if not p.endswith("kustomization.yaml")
    }
    assert listed == on_disk, f"kustomization out of sync: {listed ^ on_disk}"
    overrides = {i["name"] for i in kz["images"]}
    used = {c["image"].rsplit(":", 1)[0] for _, spec in _pod_specs() for c in spec["containers"]}
    assert used <= overrides, f"images with no kustomize override: {used - overrides}"
