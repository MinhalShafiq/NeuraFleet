import React, { useRef, useState, useEffect, useCallback, useMemo } from 'react'
import { Canvas, useThree } from '@react-three/fiber'
import { OrbitControls, Stats } from '@react-three/drei'
import * as THREE from 'three'
import { Radar, ChevronDown } from 'lucide-react'
import clsx from 'clsx'
import { createLidarSocket } from '../services/api'

// GPU buffers are allocated once at this size and reused for every frame
// (the REST scan tops out at 15,000 points; the stream sends ~2,000).
const MAX_POINTS = 20000

// Blue -> green -> red ramp for intensity in [0, 1] (the simulator already clamps it).
// Writes straight into the colour buffer: no per-point array allocation.
function writeIntensityColor(out, offset, intensity) {
  const t = intensity < 0 ? 0 : intensity > 1 ? 1 : intensity
  if (t < 0.5) {
    out[offset] = 0
    out[offset + 1] = t * 2
    out[offset + 2] = 1 - t * 2
  } else {
    out[offset] = (t - 0.5) * 2
    out[offset + 1] = 1 - (t - 0.5) * 2
    out[offset + 2] = 0
  }
}

function PointCloud({ lidarData }) {
  // One geometry, created once.  Frames overwrite its buffers in place and move the
  // draw range, so nothing is reallocated or re-created per message.
  const geometry = useMemo(() => {
    const g = new THREE.BufferGeometry()
    const pos = new THREE.BufferAttribute(new Float32Array(MAX_POINTS * 3), 3)
    const col = new THREE.BufferAttribute(new Float32Array(MAX_POINTS * 3), 3)
    pos.setUsage(THREE.DynamicDrawUsage)
    col.setUsage(THREE.DynamicDrawUsage)
    g.setAttribute('position', pos)
    g.setAttribute('color', col)
    g.setDrawRange(0, 0)
    return g
  }, [])

  useEffect(() => () => geometry.dispose(), [geometry])

  // Runs once per received message (~5 Hz), not once per rendered frame (60 Hz).
  useEffect(() => {
    const pts = lidarData?.points
    const n = pts ? Math.min(pts.length, MAX_POINTS) : 0
    if (n > 0) {
      const posArr = geometry.attributes.position.array
      const colArr = geometry.attributes.color.array
      for (let i = 0; i < n; i++) {
        const p = pts[i]
        const o = i * 3
        // LiDAR frame is z-up; three.js is y-up.
        posArr[o] = p[0]
        posArr[o + 1] = p[2] || 0
        posArr[o + 2] = p[1]
        writeIntensityColor(colArr, o, p[3] || 0)
      }
      geometry.attributes.position.needsUpdate = true
      geometry.attributes.color.needsUpdate = true
    }
    geometry.setDrawRange(0, n)
  }, [lidarData, geometry])

  return (
    // frustumCulled off: the bounding sphere is computed once and these points move.
    <points geometry={geometry} frustumCulled={false}>
      <pointsMaterial
        size={0.05}
        vertexColors
        sizeAttenuation={true}
        transparent
        opacity={0.9}
      />
    </points>
  )
}

function SceneSetup() {
  const { camera } = useThree()

  useEffect(() => {
    camera.position.set(10, 8, 10)
    camera.lookAt(0, 0, 0)
  }, [camera])

  return null
}

function Scene({ lidarData }) {
  return (
    <>
      <SceneSetup />
      <ambientLight intensity={0.2} />
      <directionalLight position={[10, 10, 5]} intensity={0.3} />

      <gridHelper args={[50, 50, 0x1a1a2e, 0x1a1a2e]} />
      <axesHelper args={[2]} />

      <PointCloud lidarData={lidarData} />

      <OrbitControls
        enableDamping
        dampingFactor={0.05}
        minDistance={2}
        maxDistance={100}
        maxPolarAngle={Math.PI * 0.85}
      />
    </>
  )
}

export default function PointCloudViewer({ fleet, selectedRobot, onSelectRobot }) {
  // One state object per message => one render per message (was two setState calls).
  const [frame, setFrame] = useState(null)
  const lidarData = frame
  const frameInfo = frame && {
    frameId: frame.frame_id,
    numPoints: frame.num_points,
    timestamp: frame.timestamp,
    robotId: frame.robot_id,
  }
  const [wsConnected, setWsConnected] = useState(false)
  const [showDropdown, setShowDropdown] = useState(false)
  const [showStats, setShowStats] = useState(false)

  const wsRef = useRef(null)
  const reconnectRef = useRef(null)

  const robotId = selectedRobot?.robot_id

  const connectLidar = useCallback(
    (rid) => {
      if (!rid) return

      if (wsRef.current) {
        wsRef.current.close()
        wsRef.current = null
      }
      if (reconnectRef.current) {
        clearTimeout(reconnectRef.current)
        reconnectRef.current = null
      }

      try {
        const ws = createLidarSocket(rid)

        ws.onopen = () => setWsConnected(true)

        ws.onmessage = (event) => {
          try {
            setFrame(JSON.parse(event.data))
          } catch (err) {
            console.error('Failed to parse LiDAR data:', err)
          }
        }

        ws.onclose = () => {
          setWsConnected(false)
          wsRef.current = null
          reconnectRef.current = setTimeout(() => connectLidar(rid), 3000)
        }

        ws.onerror = () => {
          ws.close()
        }

        wsRef.current = ws
      } catch (err) {
        console.error('LiDAR WS connection failed:', err)
        reconnectRef.current = setTimeout(() => connectLidar(rid), 3000)
      }
    },
    []
  )

  useEffect(() => {
    setFrame(null)

    if (robotId) {
      connectLidar(robotId)
    }

    return () => {
      if (wsRef.current) {
        wsRef.current.close()
        wsRef.current = null
      }
      if (reconnectRef.current) {
        clearTimeout(reconnectRef.current)
        reconnectRef.current = null
      }
    }
  }, [robotId, connectLidar])

  const robotName =
    selectedRobot?.name ||
    fleet.find((r) => r.robot_id === robotId)?.name ||
    robotId

  return (
    <div className="h-full flex flex-col animate-fade-in">
      {/* Controls Bar */}
      <div className="flex items-center justify-between mb-3 flex-shrink-0">
        <div className="flex items-center gap-3">
          {/* Robot Selector */}
          <div className="relative">
            <button
              onClick={() => setShowDropdown(!showDropdown)}
              className="flex items-center gap-2 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 hover:border-slate-600 transition-colors min-w-[200px]"
            >
              <Radar size={14} className="text-primary-400" />
              <span className="flex-1 text-left truncate">
                {selectedRobot ? robotName : 'Select a robot...'}
              </span>
              <ChevronDown
                size={14}
                className={clsx(
                  'text-slate-400 transition-transform',
                  showDropdown && 'rotate-180'
                )}
              />
            </button>
            {showDropdown && (
              <div className="absolute top-full mt-1 left-0 w-full bg-slate-800 border border-slate-700 rounded-lg shadow-xl z-50 py-1 animate-fade-in max-h-60 overflow-y-auto">
                {fleet.map((robot) => (
                  <button
                    key={robot.robot_id}
                    onClick={() => {
                      onSelectRobot(robot)
                      setShowDropdown(false)
                    }}
                    className={clsx(
                      'w-full text-left px-3 py-2 text-sm hover:bg-slate-700/50 transition-colors',
                      selectedRobot?.robot_id === robot.robot_id
                        ? 'text-primary-400 bg-primary-600/10'
                        : 'text-slate-300'
                    )}
                  >
                    {robot.name}
                    <span className="text-[10px] text-slate-500 ml-2">
                      {robot.robot_type}
                    </span>
                  </button>
                ))}
                {fleet.length === 0 && (
                  <p className="text-xs text-slate-500 px-3 py-2">
                    No robots available
                  </p>
                )}
              </div>
            )}
          </div>

          {/* Connection indicator */}
          <div
            className={clsx(
              'flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-full',
              wsConnected
                ? 'bg-green-500/10 text-green-400'
                : 'bg-slate-700/50 text-slate-500'
            )}
          >
            <div
              className={clsx(
                'w-1.5 h-1.5 rounded-full',
                wsConnected ? 'bg-green-400 animate-pulse' : 'bg-slate-600'
              )}
            />
            {wsConnected ? 'Streaming' : 'Disconnected'}
          </div>
        </div>

        {/* Right controls */}
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowStats(!showStats)}
            className={clsx(
              'text-xs px-2.5 py-1.5 rounded-lg border transition-colors',
              showStats
                ? 'bg-primary-600/20 border-primary-500/30 text-primary-400'
                : 'bg-slate-800 border-slate-700 text-slate-400 hover:text-slate-200'
            )}
          >
            FPS Stats
          </button>
        </div>
      </div>

      {/* 3D Viewport */}
      <div className="flex-1 rounded-xl overflow-hidden border border-slate-700/50 relative bg-[#0a0a0f]">
        {!robotId ? (
          <div className="absolute inset-0 flex flex-col items-center justify-center z-10">
            <div className="w-16 h-16 rounded-full bg-slate-800/80 flex items-center justify-center mb-4">
              <Radar size={28} className="text-slate-600" />
            </div>
            <h3 className="text-base font-semibold text-slate-400 mb-1">
              No Robot Selected
            </h3>
            <p className="text-sm text-slate-600">
              Select a robot from the dropdown to view its LiDAR feed
            </p>
          </div>
        ) : !lidarData ? (
          <div className="absolute inset-0 flex flex-col items-center justify-center z-10">
            <div className="w-12 h-12 rounded-full border-2 border-primary-500/30 border-t-primary-500 animate-spin mb-4" />
            <h3 className="text-base font-semibold text-slate-400 mb-1">
              Waiting for LiDAR data...
            </h3>
            <p className="text-sm text-slate-600">
              Connecting to {robotName}
            </p>
          </div>
        ) : null}

        <Canvas
          camera={{ position: [10, 8, 10], fov: 60, near: 0.1, far: 1000 }}
          gl={{ antialias: true, alpha: false }}
          onCreated={({ gl }) => {
            gl.setClearColor(new THREE.Color(0x0a0a0f))
          }}
          style={{ background: '#0a0a0f' }}
        >
          <Scene lidarData={lidarData} />
          {showStats && <Stats />}
        </Canvas>

        {/* Frame Info Overlay */}
        {frameInfo && (
          <div className="absolute bottom-3 left-3 bg-slate-900/80 backdrop-blur-sm border border-slate-700/50 rounded-lg px-3 py-2 text-xs space-y-0.5 pointer-events-none">
            <div className="flex items-center gap-2">
              <span className="text-slate-500">Robot:</span>
              <span className="text-primary-400 font-medium">{robotName}</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-slate-500">Frame:</span>
              <span className="text-slate-300 font-mono">{frameInfo.frameId}</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-slate-500">Points:</span>
              <span className="text-slate-300 font-mono">
                {frameInfo.numPoints?.toLocaleString()}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-slate-500">Time:</span>
              <span className="text-slate-300 font-mono">
                {frameInfo.timestamp
                  ? new Date(frameInfo.timestamp).toLocaleTimeString()
                  : '--'}
              </span>
            </div>
          </div>
        )}

        {/* Color legend */}
        {lidarData && (
          <div className="absolute bottom-3 right-3 bg-slate-900/80 backdrop-blur-sm border border-slate-700/50 rounded-lg px-3 py-2 pointer-events-none">
            <p className="text-[10px] text-slate-500 mb-1">Intensity</p>
            <div className="flex items-center gap-1">
              <span className="text-[10px] text-blue-400">Low</span>
              <div className="w-20 h-1.5 rounded-full bg-gradient-to-r from-blue-500 via-green-500 to-red-500" />
              <span className="text-[10px] text-red-400">High</span>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
