#!/usr/bin/env bash
# NeuraFleet - run every check: unit tests, lint, types, frontend, and the live Docker stack.
#
#   scripts/test.sh              everything below; the live-stack part is skipped if the stack isn't running
#   scripts/test.sh --up         ...and (re)build + start the Docker stack first
#   scripts/test.sh --unit       pytest + ruff + mypy only (fast, no Docker/Node needed)
#   scripts/test.sh --no-restart skip restarting rag-service (persistence check)
#   scripts/test.sh --no-chaos   skip stopping telemetry-service (demo-data fallback + recovery check)
#   scripts/test.sh --no-load    skip the 3-viewer streaming load test
#
# Exit code is non-zero if any check fails.
set -u
cd "$(dirname "$0")/.."

UP=0 UNIT_ONLY=0 RESTART=1 LOAD=1 CHAOS=1
for a in "$@"; do
  case "$a" in
    --up) UP=1 ;; --unit) UNIT_ONLY=1 ;; --no-restart) RESTART=0 ;; --no-load) LOAD=0 ;; --no-chaos) CHAOS=0 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option: $a"; exit 2 ;;
  esac
done

export PATH="$HOME/.local/bin:$PATH"
PY=python3; [ -x .venv/bin/python ] && PY=.venv/bin/python

if [ -t 1 ]; then G=$'\e[32m'; R=$'\e[31m'; Y=$'\e[33m'; B=$'\e[1m'; N=$'\e[0m'; else G= R= Y= B= N=; fi
PASS=0 FAIL=0 SKIP=0
pass() { PASS=$((PASS+1)); echo "  ${G}PASS${N} $1"; }
fail() { FAIL=$((FAIL+1)); echo "  ${R}FAIL${N} $1${2:+  -> $2}"; }
skip() { SKIP=$((SKIP+1)); echo "  ${Y}SKIP${N} $1${2:+  ($2)}"; }
section() { echo; echo "${B}== $1${N}"; }
# check "description" "expected" "actual"
check() { if [ "$2" = "$3" ]; then pass "$1"; else fail "$1" "expected '$2', got '$3'"; fi; }
code() { curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$1"; }

# docker, even if this shell predates the 'docker' group membership
if docker ps >/dev/null 2>&1; then dc() { docker compose "$@" 2>&1 | grep -v 'level=warning'; }
elif sg docker -c "docker ps" >/dev/null 2>&1; then dc() { sg docker -c "docker compose $*" 2>&1 | grep -v 'level=warning'; }
else dc() { return 1; }; NO_DOCKER=1; fi

# ---------------------------------------------------------------------------
section "Unit tests (pytest)"
if "$PY" -c "import pytest" 2>/dev/null; then
  if OUT=$("$PY" -m pytest -q 2>&1); then pass "pytest: $(echo "$OUT" | tail -1)"; else fail "pytest" "see output below"; echo "$OUT" | tail -25; fi
else
  fail "pytest not installed" "run: uv venv --python 3.11 .venv && uv pip install --python .venv/bin/python -r backend/{gateway,lidar_service,telemetry_service,rag_service}/requirements.txt -r tests/requirements.txt"
fi

section "Lint and types"
if "$PY" -m ruff --version >/dev/null 2>&1; then
  "$PY" -m ruff check backend tests scripts >/dev/null 2>&1 && pass "ruff check" || fail "ruff check" "run: .venv/bin/ruff check backend tests scripts"
  "$PY" -m ruff format --check backend tests scripts >/dev/null 2>&1 && pass "ruff format --check" || fail "ruff format --check" "run: .venv/bin/ruff format backend tests scripts"
else skip "ruff" "not installed in $PY"; fi
if "$PY" -m mypy --version >/dev/null 2>&1; then
  "$PY" -m mypy backend --explicit-package-bases >/dev/null 2>&1 && pass "mypy" || fail "mypy" "run: .venv/bin/mypy backend --explicit-package-bases"
else skip "mypy" "not installed in $PY"; fi

summary() {
  echo; echo "${B}== Summary${N}: ${G}$PASS passed${N}, $([ $FAIL -gt 0 ] && echo "$R")$FAIL failed$N, $SKIP skipped"
  [ $FAIL -eq 0 ]; exit $?
}
[ $UNIT_ONLY -eq 1 ] && summary

# ---------------------------------------------------------------------------
section "Frontend"
if command -v npm >/dev/null; then
  if [ ! -d frontend/dashboard/node_modules ]; then (cd frontend/dashboard && npm ci --no-audit --no-fund --loglevel=error >/dev/null 2>&1); fi
  (cd frontend/dashboard && npm run lint >/dev/null 2>&1) && pass "eslint" || fail "eslint" "run: cd frontend/dashboard && npm run lint"
  (cd frontend/dashboard && npm run format:check >/dev/null 2>&1) && pass "prettier --check" || fail "prettier" "run: cd frontend/dashboard && npm run format"
  if OUT=$(cd frontend/dashboard && npm test 2>&1); then pass "vitest: $(echo "$OUT" | grep -E '^ +Tests' | sed 's/^ *//')"; else fail "vitest" "$(echo "$OUT" | tail -5)"; fi
  if OUT=$(cd frontend/dashboard && npm run build 2>&1); then
    pass "vite build"
    MAIN=$(ls -S frontend/dashboard/dist/assets/index-*.js 2>/dev/null | head -1)
    [ -n "$MAIN" ] && KB=$(( $(stat -c %s "$MAIN") / 1024 )) && { [ $KB -lt 800 ] && pass "initial JS is ${KB} kB (three.js lazy-loaded)" || fail "initial JS is ${KB} kB" "expected < 800 kB"; }
    ls frontend/dashboard/dist/assets/PointCloudViewer-*.js >/dev/null 2>&1 && pass "PointCloudViewer is a separate chunk" || fail "PointCloudViewer chunk missing"
  else fail "vite build" "$(echo "$OUT" | tail -5)"; fi
else skip "frontend build" "npm not installed"; fi

# ---------------------------------------------------------------------------
section "Docker stack"
if [ "${NO_DOCKER:-0}" = 1 ]; then skip "all stack checks" "cannot talk to docker (install it / log out+in for the docker group)"; summary; fi
if [ $UP -eq 1 ]; then echo "  building + starting (the first build is slow)..."; dc up --build -d --remove-orphans >/dev/null; fi

for i in $(seq 1 40); do curl -sf --max-time 3 localhost:8000/api/health 2>/dev/null | grep -q '"rag":"healthy"' && break; sleep 3; done
HEALTH=$(curl -s --max-time 10 localhost:8000/api/health 2>/dev/null)
if [ -z "$HEALTH" ]; then skip "stack checks" "stack is not running - start it with: scripts/test.sh --up"; summary; fi

check "gateway /health (k8s probe path)  [0.1]" 200 "$(code localhost:8000/health)"
check "/api/health: all 3 services healthy and LIVE (no mock fallback)" \
  '{"status":"ok","services":{"telemetry":"healthy","lidar":"healthy","rag":"healthy"},"mode":{"telemetry":"live","lidar":"live","rag":"live"}}' "$HEALTH"
# HEALTHCHECK needs its start-period + one interval before reporting healthy: poll, don't sample once.
UNHEALTHY=
for i in $(seq 1 45); do
  UNHEALTHY=$(for c in neurafleet-gateway neurafleet-telemetry neurafleet-lidar neurafleet-rag neurafleet-frontend; do
    st=$(docker inspect -f '{{.State.Health.Status}}' $c 2>/dev/null || sg docker -c "docker inspect -f '{{.State.Health.Status}}' $c" 2>/dev/null)
    [ "$st" = healthy ] || echo "$c=$st"; done)
  [ -z "$UNHEALTHY" ] && break; sleep 2
done
[ -z "$UNHEALTHY" ] && pass "all 5 containers report HEALTHCHECK healthy" || fail "container healthchecks" "$UNHEALTHY"

section "Observability  [4.5]"
RID=$(curl -s -D - -o /dev/null -H 'X-Request-ID: test-trace-1' localhost:8000/health | tr -d '\r' | awk -F': ' 'tolower($1)=="x-request-id"{print $2}')
check "request id is echoed on responses" test-trace-1 "$RID"
for spec in gateway:8000 telemetry:8002 lidar:8001 rag:8003; do
  n=${spec%%:*}; port=${spec##*:}
  curl -s localhost:$port/metrics | grep -q '^http_requests_total' && pass "$n /metrics serves Prometheus text" || fail "$n /metrics"
done
echo "$(dc logs gateway | tail -50)" | grep -q '"request_id"' && pass "gateway logs are structured JSON with request_id" || fail "gateway logs not JSON"
LIVE_DEMO=$(curl -s localhost:8000/api/fleet | "$PY" -c "import sys,json;print(any(r['demo'] for r in json.load(sys.stdin)))")
check "live fleet data is not flagged demo" False "$LIVE_DEMO"
check "unknown robot via gateway -> 404  [2.3]" 404 "$(code localhost:8000/api/fleet/robot-999)"
check "unknown robot on telemetry metrics -> 404  [2.3]" 404 "$(code localhost:8002/metrics/nope)"
check "known robot -> 200" 200 "$(code localhost:8000/api/fleet/robot-001)"
check "frontend serves on :3000" 200 "$(code localhost:3000/)"

section "Single worker / replica  [0.2]"
# Count only the current container run (earlier runs of this script restart services on purpose).
since() { docker inspect -f '{{.State.StartedAt}}' "$1" 2>/dev/null || sg docker -c "docker inspect -f '{{.State.StartedAt}}' $1"; }
check "telemetry simulation loop started once" 1 "$(dc logs --since "$(since neurafleet-telemetry)" telemetry-service | grep -c 'Simulation loop started')"
check "lidar server processes started" 1 "$(dc logs --since "$(since neurafleet-lidar)" lidar-service | grep -c 'Started server process')"

section "Stateful alerts  [0.5]"
A1=$(curl -s localhost:8002/alerts); sleep 6; A2=$(curl -s localhost:8002/alerts)
RES=$("$PY" - "$A1" "$A2" <<'PYEOF'
import json, sys
a = {x["id"]: x for x in json.loads(sys.argv[1])}
b = {x["id"]: x for x in json.loads(sys.argv[2])}
if not a: print("none")                                   # nothing active right now - nothing to compare
else:
    kept = set(a) & set(b)
    grew = all(b[k]["count"] > a[k]["count"] for k in kept)
    print("ok" if kept and grew else f"ids changed: {sorted(a)} -> {sorted(b)}")
PYEOF
)
case "$RES" in ok) pass "same alert ids across polls, count increasing" ;; none) skip "alert id stability" "no alerts active right now; re-run in a minute" ;; *) fail "alert ids unstable" "$RES" ;; esac
check "resolved-alert history endpoint" 200 "$(code localhost:8002/alerts/resolved)"

section "RAG / Chroma  [0.3, 0.4]"
LOGS=$(dc logs rag-service)
echo "$LOGS" | grep -q 'Traceback' && fail "rag-service logs contain a traceback" || pass "no traceback in rag-service logs"
echo "$LOGS" | grep -q 'persist=/app/chroma_data' && pass "Chroma persists to /app/chroma_data" || fail "persist path not logged"
echo "$(dc exec -T rag-service ls /app/chroma_data)" | grep -q chroma.sqlite3 && pass "volume holds chroma.sqlite3" || fail "no chroma.sqlite3 in /app/chroma_data"
DOCS=$(curl -s localhost:8003/health | "$PY" -c "import sys,json;print(json.load(sys.stdin)['documents'])" 2>/dev/null)
[ "${DOCS:-0}" -gt 0 ] && pass "$DOCS documents indexed" || fail "no documents indexed"
if [ $RESTART -eq 1 ]; then
  dc restart rag-service >/dev/null
  for i in $(seq 1 40); do curl -sf --max-time 3 localhost:8003/health >/dev/null 2>&1 && break; sleep 3; done
  AFTER=$(curl -s localhost:8003/health | "$PY" -c "import sys,json;print(json.load(sys.stdin)['documents'])" 2>/dev/null)
  check "documents survive a restart (persistence)" "$DOCS" "$AFTER"
else skip "restart persistence" "--no-restart"; fi
Q=$(curl -s --max-time 30 -X POST localhost:8000/api/rag/query -H 'Content-Type: application/json' -d '{"query":"How do I charge a robot?"}')
echo "$Q" | grep -qi 'battery\|charg' && pass "RAG query through the gateway answers from the docs" || fail "RAG query" "$Q"

section "LiDAR streaming  [1.1]"
if [ $LOAD -eq 1 ] && "$PY" -c "import websockets" 2>/dev/null; then
  ( for i in $(seq 1 300); do curl -s -o /dev/null -w '%{time_total}\n' --max-time 5 localhost:8001/health; sleep 0.02; done > /tmp/nf_health_lat.txt ) &
  LP=$!
  LOADRES=$("$PY" scripts/load_viewers.py ws://localhost:8001/ws/lidar 8 robot-001 robot-001 robot-002 2>&1 | tail -1)
  wait $LP
  eval "$("$PY" - "$LOADRES" <<'PYEOF'
import json, sys
try: r = json.loads(sys.argv[1])
except Exception: print('OK_RATE=0 CONSEC=0 SHARED=0 PTS=0 RATES="unparseable"'); sys.exit()
ok = all(4.5 <= x <= 5.5 for x in r["rates"])
print(f'OK_RATE={int(ok)} CONSEC={int(r["consecutive"])} SHARED={int(r["shared_ok"])} PTS={r["points"]} RATES="{r["rates"]}"')
PYEOF
)"
  [ "$OK_RATE" = 1 ] && pass "3 concurrent viewers each at ~5 Hz: $RATES" || fail "stream rate" "$RATES"
  [ "$CONSEC" = 1 ] && pass "frame ids strictly increasing (one producer per robot)" || fail "frame ids not increasing"
  [ "$SHARED" = 1 ] && pass "two viewers of one robot get the same frames" || fail "viewers of one robot saw different frames"
  [ "${PTS:-0}" -gt 0 ] && pass "$PTS points/frame (real LiDAR data, not mock)" || fail "no points in frames"
  P99=$(tr ',' '.' < /tmp/nf_health_lat.txt | LC_ALL=C sort -n | LC_ALL=C awk '{a[NR]=$1*1000} END{printf "%.0f", a[int(NR*0.99)]}')
  MAX=$(tr ',' '.' < /tmp/nf_health_lat.txt | LC_ALL=C sort -n | LC_ALL=C awk '{a[NR]=$1*1000} END{printf "%.0f", a[NR]}')
  # idle baseline is ~4 ms; a blocked loop (the pre-Phase-2 behaviour) is 400+ ms. 100 ms leaves room for host/curl jitter.
  [ "$P99" -lt 100 ] && pass "lidar /health stays responsive while streaming: p99=${P99} ms, max=${MAX} ms" || fail "lidar /health p99=${P99} ms while streaming" "event loop is stalling"
  [ "$MAX" -ge 50 ] && echo "       note: max ${MAX} ms - occasional ~50 ms gen-2 GC pause (known, see Phase 2 notes)"
else skip "streaming load test" "--no-load or 'websockets' not installed in $PY"; fi

section "Demo-data fallback and recovery  [4.6, 2.7]"
if [ $CHAOS -eq 1 ]; then
  dc stop telemetry-service >/dev/null; sleep 3
  DEMO=$(curl -s localhost:8000/api/fleet | "$PY" -c "import sys,json;d=json.load(sys.stdin);print(all(r['demo'] for r in d) and len(d))")
  [ "$DEMO" = 6 ] && pass "telemetry down -> gateway serves 6 robots, all flagged demo" || fail "fallback not flagged demo" "$DEMO"
  check "/api/health reports telemetry as mock" mock "$(curl -s localhost:8000/api/health | "$PY" -c "import sys,json;print(json.load(sys.stdin)['mode']['telemetry'])")"
  WSDEMO=$("$PY" - <<'PYEOF'
import json
from websockets.sync.client import connect
with connect("ws://localhost:8000/api/ws/telemetry", open_timeout=5) as ws:
    print(all(r.get("demo") for r in json.loads(ws.recv(timeout=5))))
PYEOF
)
  check "WebSocket telemetry is also flagged demo while down" True "$WSDEMO"
  dc start telemetry-service >/dev/null
  RECOVERED=no; for i in $(seq 1 40); do
    D=$(curl -s localhost:8000/api/fleet | "$PY" -c "import sys,json;print(any(r['demo'] for r in json.load(sys.stdin)))" 2>/dev/null)
    [ "$D" = False ] && { RECOVERED=yes; break; }; sleep 2; done
  check "recovers to live data after telemetry restarts" yes "$RECOVERED"
  WSLIVE=$("$PY" - <<'PYEOF'
import json, time
from websockets.sync.client import connect
# a long-lived gateway stream opened now must serve live frames (upstream is back)
with connect("ws://localhost:8000/api/ws/telemetry", open_timeout=5) as ws:
    frames = [json.loads(ws.recv(timeout=5)) for _ in range(3)]
print(not any(r.get("demo") for f in frames for r in f))
PYEOF
)
  check "WebSocket telemetry is live again" True "$WSLIVE"
else skip "demo-data fallback" "--no-chaos"; fi

summary
