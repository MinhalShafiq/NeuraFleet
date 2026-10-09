import React, { useState, useEffect, useRef, useCallback, useMemo, lazy, Suspense } from 'react'
import Layout from './components/Layout'
import FleetMonitor from './components/FleetMonitor'
import ErrorBoundary from './components/ErrorBoundary'
import { createTelemetrySocket, fetchFleet, fetchAlerts } from './services/api'

// three.js + react-three-fiber + drei are the bulk of the bundle and only the LiDAR
// view needs them; the dashboard (default view) shouldn't pay for them on first paint.
const PointCloudViewer = lazy(() => import('./components/PointCloudViewer'))
const ChatInterface = lazy(() => import('./components/ChatInterface'))

const HISTORY_LEN = 50
const HISTORY_COMMIT_MS = 1000 // charts re-render at ~1 Hz, not at the 2 Hz telemetry rate

function ViewFallback() {
  return (
    <div className="flex items-center justify-center h-full">
      <div className="w-8 h-8 rounded-full border-2 border-primary-500/30 border-t-primary-500 animate-spin" />
    </div>
  )
}

export default function App() {
  const [fleet, setFleet] = useState([])
  // Store only the id: a robot object captured at click time would never update.
  const [selectedRobotId, setSelectedRobotId] = useState(null)
  const [activeView, setActiveView] = useState('dashboard')
  const [alerts, setAlerts] = useState([])
  const [connected, setConnected] = useState(false)
  const [telemetryHistory, setTelemetryHistory] = useState({})

  const selectedRobot = useMemo(
    () => fleet.find((r) => r.robot_id === selectedRobotId) ?? null,
    [fleet, selectedRobotId],
  )
  const setSelectedRobot = useCallback(
    (robot) => setSelectedRobotId(robot ? robot.robot_id : null),
    [],
  )

  // Raw history lives in a ref (mutated in place); React state is only a throttled snapshot.
  const historyRef = useRef({})
  const historyTimerRef = useRef(null)

  const wsRef = useRef(null)
  const reconnectTimerRef = useRef(null)
  const alertIntervalRef = useRef(null)

  const connectWebSocket = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) return

    try {
      const ws = createTelemetrySocket()

      ws.onopen = () => {
        setConnected(true)
        if (reconnectTimerRef.current) {
          clearTimeout(reconnectTimerRef.current)
          reconnectTimerRef.current = null
        }
      }

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          if (Array.isArray(data)) {
            // WS frames omit name/robot_type; keep them from the REST snapshot.
            setFleet((prev) => {
              const byId = new Map(prev.map((r) => [r.robot_id, r]))
              return data.map((r) => ({ ...byId.get(r.robot_id), ...r }))
            })
            const now = Date.now()
            const hist = historyRef.current
            for (const robot of data) {
              const list = hist[robot.robot_id] || (hist[robot.robot_id] = [])
              list.push({
                time: now,
                battery: robot.battery,
                temperature: robot.temperature,
                cpu_usage: robot.cpu_usage,
                memory_usage: robot.memory_usage,
              })
              if (list.length > HISTORY_LEN) list.shift()
            }
            if (!historyTimerRef.current) {
              historyTimerRef.current = setTimeout(() => {
                historyTimerRef.current = null
                const snapshot = {}
                for (const [id, list] of Object.entries(historyRef.current)) snapshot[id] = list.slice()
                setTelemetryHistory(snapshot)
              }, HISTORY_COMMIT_MS)
            }
          }
        } catch (err) {
          console.error('Failed to parse telemetry:', err)
        }
      }

      ws.onclose = () => {
        setConnected(false)
        wsRef.current = null
        reconnectTimerRef.current = setTimeout(connectWebSocket, 3000)
      }

      ws.onerror = () => {
        ws.close()
      }

      wsRef.current = ws
    } catch (err) {
      console.error('WebSocket connection failed:', err)
      reconnectTimerRef.current = setTimeout(connectWebSocket, 3000)
    }
  }, [])

  useEffect(() => {
    // Initial fleet fetch via REST as fallback
    fetchFleet()
      .then(setFleet)
      .catch((err) => console.error('Initial fleet fetch failed:', err))

    connectWebSocket()

    return () => {
      if (wsRef.current) {
        wsRef.current.close()
        wsRef.current = null
      }
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current)
      }
      if (historyTimerRef.current) {
        clearTimeout(historyTimerRef.current)
        historyTimerRef.current = null
      }
    }
  }, [connectWebSocket])

  // Fetch alerts periodically
  useEffect(() => {
    const loadAlerts = () => {
      fetchAlerts()
        .then(setAlerts)
        .catch((err) => console.error('Failed to fetch alerts:', err))
    }

    loadAlerts()
    alertIntervalRef.current = setInterval(loadAlerts, 5000)

    return () => {
      if (alertIntervalRef.current) {
        clearInterval(alertIntervalRef.current)
      }
    }
  }, [])

  const renderView = () => {
    switch (activeView) {
      case 'lidar':
        return (
          <PointCloudViewer
            fleet={fleet}
            selectedRobot={selectedRobot}
            onSelectRobot={setSelectedRobot}
          />
        )
      case 'chat':
        return (
          <ChatInterface
            fleet={fleet}
            selectedRobot={selectedRobot}
          />
        )
      case 'dashboard':
      default:
        return (
          <FleetMonitor
            fleet={fleet}
            alerts={alerts}
            selectedRobot={selectedRobot}
            onSelectRobot={setSelectedRobot}
            telemetryHistory={telemetryHistory}
          />
        )
    }
  }

  return (
    <Layout
      fleet={fleet}
      alerts={alerts}
      activeView={activeView}
      onChangeView={setActiveView}
      selectedRobot={selectedRobot}
      onSelectRobot={setSelectedRobot}
      connected={connected}
    >
      <ErrorBoundary resetKey={activeView}>
        <Suspense fallback={<ViewFallback />}>{renderView()}</Suspense>
      </ErrorBoundary>
    </Layout>
  )
}
