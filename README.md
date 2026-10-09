# NeuraFleet

A cloud-native fleet simulation platform: six simulated robots stream telemetry and LiDAR
point clouds to a live React dashboard, with alerts and a RAG chat assistant over the robot docs.

```
 Dashboard (React, Three.js)  ──HTTP/WebSocket──▶  Gateway :8000
                                                     │
                       ┌─────────────────────────────┼───────────────────────────┐
                       ▼                             ▼                           ▼
                LiDAR :8001                  Telemetry :8002                RAG :8003
        ray-cast point clouds, 5 Hz     fleet sim, alerts, 2 Hz      ChromaDB + Claude (optional)
```

| Service | Role |
|---|---|
| **Gateway** | Public API and WebSocket entry point. Proxies to the services; serves flagged demo data if one is down. |
| **LiDAR** | Vectorized 16-channel LiDAR simulation, motion compensation, one shared 5 Hz stream per robot. |
| **Telemetry** | Seeded multi-robot simulation, stateful alerts (raise once, update, clear with hysteresis). |
| **RAG** | Retrieval over the robot docs in ChromaDB. Uses Anthropic Claude if `ANTHROPIC_API_KEY` is set, a rule-based fallback otherwise. |

## Run it

```bash
cp .env.example .env        # ANTHROPIC_API_KEY is optional
docker compose up --build
```

Dashboard <http://localhost:3000> · API <http://localhost:8000> · OpenAPI docs <http://localhost:8000/docs>

<details>
<summary>Without Docker</summary>

Services import the shared contract as `shared.*`, so put `backend/` on the path and install from the lockfiles:

```bash
python3.11 -m venv .venv && . .venv/bin/activate
pip install -r backend/gateway/requirements.lock -r backend/lidar_service/requirements.lock \
            -r backend/telemetry_service/requirements.lock -r backend/rag_service/requirements.lock

export PYTHONPATH=$PWD/backend LOG_FORMAT=text
(cd backend/telemetry_service && uvicorn main:app --port 8002) &
(cd backend/lidar_service     && uvicorn main:app --port 8001) &
(cd backend/rag_service       && uvicorn main:app --port 8003) &
(cd backend/gateway && LIDAR_SERVICE_URL=http://localhost:8001 TELEMETRY_SERVICE_URL=http://localhost:8002 \
   RAG_SERVICE_URL=http://localhost:8003 uvicorn main:app --port 8000)

cd frontend/dashboard && npm ci && npm run dev      # proxies /api to :8000
```
</details>

## Test

```bash
scripts/test.sh --unit      # pytest + ruff + mypy (no Docker or Node needed)
scripts/test.sh             # + frontend lint/tests/build + the running Docker stack
scripts/test.sh --up        # same, building and starting the stack first
```

The stack checks cover health, status codes, alert stability, Chroma persistence across a restart,
three concurrent LiDAR viewers at 5 Hz, request-ID propagation, and a chaos step that stops the
telemetry service to confirm the dashboard is told it is looking at demo data and recovers
(`--no-chaos`, `--no-restart`, `--no-load` skip steps). `pre-commit install` runs the linters on commit.

## How it behaves

- **Outages are visible, not silent.** If a service is unreachable the gateway serves simulated data
  so the UI keeps working, but marks it: every payload gets `"demo": true`, the dashboard shows a
  **DEMO DATA** badge, `/api/health` reports each service as `live` or `mock`, and
  `gateway_mock_fallback_total` counts it. A WebSocket that drops mid-stream keeps streaming demo
  frames and switches back to live on its own.
- **One API contract.** `backend/shared/models.py` defines every payload; services use it as
  `response_model`, so responses are validated and `/docs` is accurate. A malformed upstream response
  becomes a `502`, an unknown robot a `404`.
- **Observable.** One JSON log line per event with a `request_id` that the gateway forwards to the
  services; Prometheus text at `GET /metrics` on every service. Set `LOG_FORMAT=text` for human logs.
  `/metrics` and `/docs` share the API's port, so restrict them at the ingress if the gateway is public.
- **Simulators are single-instance.** Telemetry and LiDAR keep their world in memory, so each runs
  one worker and one replica. Scaling them out means moving that state out of the process first.
- **`/health` vs `/api/health`.** `/health` never calls downstream and is the Kubernetes probe;
  `/api/health` is the observability view.

## Dependencies

Edit `backend/<service>/requirements.txt`, then regenerate its lock (images and CI install from it):

```bash
uv pip compile backend/<service>/requirements.txt -o backend/<service>/requirements.lock \
  --python-version 3.11 --python-platform linux
```

A test fails if a lock disagrees with its requirements or with another service's lock.

## Deploy (GCP)

**Terraform** provisions the network, a private GKE cluster and a Cloud NAT. Who may reach the control
plane is a **required** input with no default (`0.0.0.0/0` is rejected). No GPU pool is created.

```bash
echo 'authorized_cidrs = [{ cidr_block = "203.0.113.7/32", display_name = "office" }]' \
  > terraform/environments/dev.local.tfvars                    # untracked
cd terraform && terraform init
terraform apply -var-file=environments/dev.tfvars -var-file=environments/dev.local.tfvars
```

**Kubernetes.** The API key never goes in git; create the secret by hand (RAG falls back to rule-based
answers without it). Images use immutable tags injected by kustomize, never `:latest`; applying with
the placeholder tag fails loudly with `ImagePullBackOff`.

```bash
kubectl apply -f k8s/namespace.yaml
kubectl -n neurafleet create secret generic neurafleet-secrets --from-literal=ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY"

cd k8s
for s in gateway telemetry-service lidar-service rag-service frontend; do
  kustomize edit set image gcr.io/PROJECT_ID/neurafleet-$s=gcr.io/<your-project>/neurafleet-$s:$(git rev-parse --short HEAD)
done
kubectl apply -k .
```

`k8s/backendconfig.yaml` raises the load balancer timeout to 1 h; without it GCE closes the
WebSocket streams after 30 s.

## Layout

```
backend/{gateway,lidar_service,telemetry_service,rag_service}   one FastAPI service each (+ Dockerfile, lock)
backend/shared/                                                 API contract + logging/metrics
frontend/dashboard/                                             React app (Vite, Tailwind, Vitest)
k8s/   terraform/   docker-compose.yml                          deployment
tests/   scripts/test.sh                                        pytest suite and the end-to-end runner
```

## The fleet

| ID | Name | Type | | ID | Name | Type |
|---|---|---|---|---|---|---|
| robot-001 | Atlas-1 | explorer | | robot-004 | Sentinel-4 | sentinel |
| robot-002 | Scout-2 | scout | | robot-005 | Mapper-5 | mapper |
| robot-003 | Hauler-3 | hauler | | robot-006 | Relay-6 | relay |
