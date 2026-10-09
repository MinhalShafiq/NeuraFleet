# NeuraFleet - Cloud-Native Fleet Simulation Platform

## Overview
NeuraFleet is a cloud-native fleet management platform that simulates multi-robot operations with real-time LiDAR processing, telemetry ingestion, and AI-powered analytics.

## Core Requirements

### 1. Python Microservices (Backend)
- **API Gateway** (port 8000): Central FastAPI gateway routing requests to internal services
- **LiDAR Service** (port 8001): Real-time LiDAR point cloud generation, motion compensation, and processing
- **Telemetry Service** (port 8002): Multi-robot fleet simulation, telemetry ingestion, anomaly detection, and alert generation
- **RAG Service** (port 8003): LLM-powered retrieval-augmented generation using ChromaDB vector database

### 2. React Dashboard (Frontend)
- Live 3D point cloud visualization using Three.js / react-three-fiber
- Fleet monitoring dashboard with real-time telemetry charts
- Integrated LLM chat interface for natural language fleet queries
- Alert panel with severity-based notifications
- A **DEMO DATA** badge whenever the gateway is serving simulated data because a service is down (see below)

### 3. RAG System
- Vector database (ChromaDB) storing robot documentation and sensor logs
- Natural language queries about robot performance and anomaly detection
- Automated alert generation based on sensor data patterns
- Anthropic Claude integration with rule-based fallback for demo mode

### 4. Infrastructure as Code
- **Terraform**: GCP infrastructure provisioning (GKE clusters, GCS storage, VPC networking; an optional GPU pool, off by default)
- **Kubernetes**: Deployment manifests with resource limits, health checks, and security contexts
- **Docker**: Multi-stage builds installing from lockfiles, a `HEALTHCHECK` on every image, Docker Compose for local development

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    React Dashboard                       │
│  ┌──────────┐  ┌──────────────┐  ┌────────────────────┐  │
│  │ 3D LiDAR │  │ Fleet Monitor│  │ LLM Chat Interface │  │
│  │ Viewer   │  │ + Telemetry  │  │ (RAG-powered)      │  │
│  └──────────┘  └──────────────┘  └────────────────────┘  │
└──────────────────────┬───────────────────────────────────┘
                       │ HTTP / WebSocket
              ┌────────▼────────┐
              │   API Gateway   │
              │   (port 8000)   │
              └───┬─────┬─────┬─┘
         ┌────────┘     │     └────────┐
   ┌─────▼─────┐ ┌─────▼─────┐ ┌──────▼──────┐
   │   LiDAR   │ │ Telemetry │ │    RAG      │
   │  Service  │ │  Service  │ │  Service    │
   │ (8001)    │ │ (8002)    │ │ (8003)      │
   └───────────┘ └───────────┘ └──────┬──────┘
                                      │
                               ┌──────▼──────┐
                               │  ChromaDB   │
                               │ Vector Store│
                               └─────────────┘
```

## Quick Start

### Local Development (Docker Compose)
```bash
cp .env.example .env
# Edit .env with your ANTHROPIC_API_KEY (optional - works without it)
docker compose up --build
```
- Frontend: http://localhost:3000
- API Gateway: http://localhost:8000 (OpenAPI docs: http://localhost:8000/docs)
- Prometheus metrics: `/metrics` on each service (8000-8003)

### Development (Without Docker)
All services import the shared API contract as `shared.*` (`backend/shared`), so run them from
`backend/` with it on the path. Install from the lockfiles, which are what the images and CI use:
```bash
python3.11 -m venv .venv && . .venv/bin/activate
pip install -r backend/gateway/requirements.lock -r backend/lidar_service/requirements.lock \
            -r backend/telemetry_service/requirements.lock -r backend/rag_service/requirements.lock

export PYTHONPATH=$PWD/backend LOG_FORMAT=text         # human-readable logs
(cd backend/telemetry_service && uvicorn main:app --port 8002) &
(cd backend/lidar_service     && uvicorn main:app --port 8001) &
(cd backend/rag_service       && uvicorn main:app --port 8003) &
(cd backend/gateway           && LIDAR_SERVICE_URL=http://localhost:8001 \
   TELEMETRY_SERVICE_URL=http://localhost:8002 RAG_SERVICE_URL=http://localhost:8003 \
   uvicorn main:app --port 8000)

# Frontend (proxies /api to :8000)
cd frontend/dashboard && npm ci && npm run dev
```

## Testing
```bash
scripts/test.sh              # unit tests, lint, types, frontend build + the live Docker stack
scripts/test.sh --up         # ...rebuilding and starting the stack first
scripts/test.sh --unit       # fast: pytest + ruff + mypy only (no Docker/Node needed)
```
The live-stack part checks health, real HTTP status codes, single-worker simulators, stateful
alerts, Chroma persistence across a restart, three concurrent LiDAR viewers at 5 Hz, request-ID
propagation, and (unless `--no-chaos`) stops the telemetry service to confirm the dashboard is told
it is looking at demo data and recovers when the service returns.

Individually:
```bash
pytest                                              # backend + infra tests
ruff check backend tests scripts && ruff format --check backend tests scripts
mypy backend --explicit-package-bases
cd frontend/dashboard && npm run lint && npm run format:check && npm test && npm run build
pip install pre-commit && pre-commit install        # run the linters on every commit
```

## How it behaves when a service is down
The gateway never returns an error just because a backing service is unreachable: it serves
simulated data so the UI keeps working. That is convenient for demos and dangerous for operators, so
the degradation is made visible:

- every fabricated payload carries `"demo": true`, and the dashboard shows a **DEMO DATA** badge;
- `GET /api/health` reports each service as `healthy`/`degraded`/`unreachable` **and** `live`/`mock`;
- `gateway_mock_fallback_total{endpoint}` counts fallbacks, so an outage can be alerted on;
- a WebSocket whose upstream drops mid-stream keeps streaming (as demo data) and switches back to
  live by itself when the service returns; browsers reconnect with exponential backoff and jitter.

`GET /health` (not `/api/health`) is the Kubernetes probe target and never calls downstream, so a sick
dependency cannot pull gateway pods out of rotation.

## The API contract
`backend/shared/models.py` is the single definition of every payload (robots, telemetry, alerts,
LiDAR scans, RAG, health). Services declare these as `response_model=`, so responses are validated
and `/docs` is accurate; the gateway turns a malformed upstream response into a `502` rather than
forwarding it. Real HTTP status codes are used throughout (`404` for an unknown robot, `422` for a
bad request).

## Observability
Every service logs one JSON object per line (`LOG_FORMAT=text` for humans, `LOG_LEVEL` for
verbosity) with a `request_id`. The gateway accepts or generates `X-Request-ID`, echoes it on the
response, and forwards it to the services (including WebSocket upstreams), so one user action can be
followed across all four services' logs. `GET /metrics` serves Prometheus text: request counts and
latency by *route template*, open WebSockets, plus domain metrics (`lidar_scan_seconds`,
`telemetry_active_alerts`, `rag_documents`, `gateway_mock_fallback_total`).

> `/metrics` and `/docs` are served by the same process as the API. If the gateway is exposed
> publicly, restrict them at the ingress or network-policy level.

## Design notes
- **The simulators are single-instance by design.** The telemetry and LiDAR services keep their
  world in process memory, so they run one worker and one replica; more would give each client a
  different universe. Scaling them out means moving that state out of the process first.
- **Dependencies are locked.** Edit `backend/<service>/requirements.txt`, then regenerate the lock:
  `uv pip compile backend/<service>/requirements.txt -o backend/<service>/requirements.lock --python-version 3.11 --python-platform linux`.
  A test fails if a lock disagrees with its requirements or another service's lock.
- **Images are immutable.** Kubernetes manifests use a placeholder tag; real tags are injected by
  kustomize (below), never `:latest`.

### Terraform Deployment
`authorized_cidrs` (who may reach the GKE control plane) is **required** and has no default;
`0.0.0.0/0` is rejected. Put your ranges in an untracked per-environment file:
```bash
cat > terraform/environments/dev.local.tfvars <<'EOF2'
authorized_cidrs = [{ cidr_block = "203.0.113.7/32", display_name = "office" }]
EOF2

cd terraform
terraform init
terraform plan  -var-file=environments/dev.tfvars -var-file=environments/dev.local.tfvars
terraform apply -var-file=environments/dev.tfvars -var-file=environments/dev.local.tfvars
```
No GPU node pool is created (`gpu_node_count = 0`): nothing in the platform uses a GPU. Set it above 0 only if you add GPU workloads.

### Kubernetes Deployment
The Anthropic API key is **not** stored in the repo. Create the secret out-of-band (the RAG service
runs in rule-based fallback mode if it is absent):
```bash
kubectl apply -f k8s/namespace.yaml
kubectl -n neurafleet create secret generic neurafleet-secrets \
  --from-literal=ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY"
```
Build and push the five images tagged with the git SHA, point kustomize at them, and apply:
```bash
cd k8s
for s in gateway telemetry-service lidar-service rag-service frontend; do
  kustomize edit set image gcr.io/PROJECT_ID/neurafleet-$s=gcr.io/<your-project>/neurafleet-$s:$(git rev-parse --short HEAD)
done
kubectl apply -k .
```
Applying without setting tags fails loudly (`ImagePullBackOff` on the placeholder `0.0.0-unset`)
instead of running whatever `:latest` happens to be. `k8s/backendconfig.yaml` raises the load
balancer's backend timeout to 1 h; without it GCE closes the WebSocket streams after 30 s.

## Simulated Fleet
The platform simulates 6 robots:
| Robot ID   | Name       | Type      |
|-----------|------------|-----------|
| robot-001 | Atlas-1    | Explorer  |
| robot-002 | Scout-2    | Scout     |
| robot-003 | Hauler-3   | Hauler    |
| robot-004 | Sentinel-4 | Sentinel  |
| robot-005 | Mapper-5   | Mapper    |
| robot-006 | Relay-6    | Relay     |

Each robot generates realistic telemetry including position, velocity, battery, temperature, IMU, and GPS data with simulated anomalies and alerts.
