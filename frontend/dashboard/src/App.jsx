import React, { useState, useEffect, useRef, useCallback } from 'react'
import Layout from './components/Layout'
import FleetMonitor from './components/FleetMonitor'
import PointCloudViewer from './components/PointCloudViewer'
import ChatInterface from './components/ChatInterface'
import { createTelemetrySocket, fetchFleet, fetchAlerts } from './services/api'

export default function App() {
  const [fleet, setFleet] = useState([])
  const [selectedRobot, setSelectedRobot] = useState(null)
  const [activeView, setActiveView] = useState('dashboard')
  const [alerts, setAlerts] = useState([])
  const [connected, setConnected] = useState(false)
  const [telemetryHistory, setTelemetryHistory] = useState({})

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
            setFleet(data)
            setTelemetryHistory((prev) => {
              const next = { ...prev }
              const now = Date.now()
              data.forEach((robot) => {
                const history = next[robot.robot_id] || []
                const entry = {
                  time: now,
                  battery: robot.battery,
                  temperature: robot.temperature,
                  cpu_usage: robot.cpu_usage,
                  memory_usage: robot.memory_usage,
                }
                next[robot.robot_id] = [...history.slice(-49), entry]
              })
              return next
            })
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
      {renderView()}
    </Layout>
  )
}
