const BASE_URL = import.meta.env.VITE_API_URL || ''
const WS_URL =
  import.meta.env.VITE_WS_URL ||
  `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}`

export async function fetchFleet() {
  const res = await fetch(`${BASE_URL}/api/fleet`)
  if (!res.ok) throw new Error(`Failed to fetch fleet: ${res.status}`)
  return res.json()
}

export async function fetchRobot(robotId) {
  const res = await fetch(`${BASE_URL}/api/fleet/${robotId}`)
  if (!res.ok) throw new Error(`Failed to fetch robot ${robotId}: ${res.status}`)
  return res.json()
}

export async function fetchAlerts() {
  const res = await fetch(`${BASE_URL}/api/alerts`)
  if (!res.ok) throw new Error(`Failed to fetch alerts: ${res.status}`)
  return res.json()
}

export async function fetchMetrics(robotId) {
  const res = await fetch(`${BASE_URL}/api/metrics/${robotId}`)
  if (!res.ok) throw new Error(`Failed to fetch metrics for ${robotId}: ${res.status}`)
  return res.json()
}

export async function queryRAG(query, robotId = null) {
  const body = { query }
  if (robotId) body.robot_id = robotId
  const res = await fetch(`${BASE_URL}/api/rag/query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`RAG query failed: ${res.status}`)
  return res.json()
}

export function createTelemetrySocket() {
  return new WebSocket(`${WS_URL}/api/ws/telemetry`)
}

export function createLidarSocket(robotId) {
  return new WebSocket(`${WS_URL}/api/ws/lidar/${robotId}`)
}

export async function fetchLidarScan(robotId) {
  const res = await fetch(`${BASE_URL}/api/lidar/${robotId}/scan`)
  if (!res.ok) throw new Error(`Failed to fetch LiDAR scan for ${robotId}: ${res.status}`)
  return res.json()
}
