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

### 3. RAG System
- Vector database (ChromaDB) storing robot documentation and sensor logs
- Natural language queries about robot performance and anomaly detection
- Automated alert generation based on sensor data patterns
- Anthropic Claude integration with rule-based fallback for demo mode

### 4. Infrastructure as Code
- **Terraform**: GCP infrastructure provisioning (GKE clusters, GCS storage, VPC networking; an optional GPU pool, off by default)
- **Kubernetes**: Deployment manifests with resource limits, health checks, and security contexts
- **Docker**: Multi-stage builds, Docker Compose for local development

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
docker-compose up --build
```
- Frontend: http://localhost:3000
- API Gateway: http://localhost:8000
- API Docs: http://localhost:8000/docs

### Development (Without Docker)
```bash
# Backend services (each in separate terminal)
cd backend/gateway && pip install -r requirements.txt && uvicorn main:app --port 8000 --reload
cd backend/lidar_service && pip install -r requirements.txt && uvicorn main:app --port 8001 --reload
cd backend/telemetry_service && pip install -r requirements.txt && uvicorn main:app --port 8002 --reload
cd backend/rag_service && pip install -r requirements.txt && uvicorn main:app --port 8003 --reload

# Frontend
cd frontend/dashboard && npm install && npm run dev
```

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
Then replace `PROJECT_ID` in the image names and apply everything else:
```bash
kubectl apply -f k8s/configmap.yaml -f k8s/backendconfig.yaml \
  -f k8s/gateway -f k8s/telemetry-service -f k8s/lidar-service -f k8s/rag-service -f k8s/frontend \
  -f k8s/ingress.yaml
```
`k8s/backendconfig.yaml` raises the load balancer's backend timeout to 1 h; without it GCE closes the
WebSocket streams after 30 s. The telemetry and LiDAR services hold their simulation state in memory,
so they run as a single replica.

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
