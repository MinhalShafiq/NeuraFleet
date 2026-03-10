import React from 'react'
import {
  Navigation,
  Gauge,
  Thermometer,
  Cpu,
  MemoryStick,
  Compass,
  MapPin,
  Zap,
} from 'lucide-react'
import clsx from 'clsx'

function GaugeBar({ label, value, max = 100, unit = '%', icon: Icon, thresholds }) {
  const pct = Math.min(100, Math.max(0, (value / max) * 100))
  const defaultThresholds = { good: 50, warn: 80 }
  const t = thresholds || defaultThresholds

  let color = 'text-green-400'
  let barColor = 'bg-green-500'
  if (pct > t.warn) {
    color = 'text-red-400'
    barColor = 'bg-red-500'
  } else if (pct > t.good) {
    color = 'text-amber-400'
    barColor = 'bg-amber-500'
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <span className="text-[11px] text-slate-400 flex items-center gap-1">
          {Icon && <Icon size={11} />}
          {label}
        </span>
        <span className={clsx('text-xs font-mono font-semibold', color)}>
          {typeof value === 'number' ? value.toFixed(1) : value}
          {unit}
        </span>
      </div>
      <div className="w-full bg-slate-700/50 rounded-full h-1.5">
        <div
          className={clsx('h-1.5 rounded-full transition-all duration-500', barColor)}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

function DataRow({ label, value, unit = '', mono = true }) {
  return (
    <div className="flex items-center justify-between py-0.5">
      <span className="text-[11px] text-slate-500">{label}</span>
      <span
        className={clsx(
          'text-xs text-slate-300',
          mono && 'font-mono'
        )}
      >
        {typeof value === 'number' ? value.toFixed(3) : value}
        {unit && <span className="text-slate-500 ml-0.5">{unit}</span>}
      </span>
    </div>
  )
}

function Section({ title, icon: Icon, children }) {
  return (
    <div>
      <div className="flex items-center gap-1.5 mb-2">
        {Icon && <Icon size={12} className="text-slate-500" />}
        <h4 className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
          {title}
        </h4>
      </div>
      {children}
    </div>
  )
}

export default function TelemetryPanel({ robot }) {
  if (!robot) {
    return (
      <div className="glass-panel p-4">
        <p className="text-sm text-slate-500 text-center py-4">
          Select a robot to view telemetry
        </p>
      </div>
    )
  }

  const { position, velocity, sensors, cpu_usage, memory_usage, temperature } = robot

  return (
    <div className="glass-panel p-4 space-y-4 animate-fade-in">
      <div className="flex items-center gap-2 pb-2 border-b border-slate-700/50">
        <Gauge size={14} className="text-primary-400" />
        <h3 className="text-sm font-semibold text-slate-300">
          Live Telemetry
        </h3>
        <span className="text-[10px] bg-cyan-500/10 text-cyan-400 border border-cyan-500/20 px-1.5 py-0.5 rounded-full font-medium ml-auto">
          LIVE
        </span>
      </div>

      {/* Position & Velocity */}
      <Section title="Position" icon={MapPin}>
        <div className="grid grid-cols-3 gap-2 bg-slate-800/30 rounded-lg p-2">
          {['x', 'y', 'z'].map((axis) => (
            <div key={axis} className="text-center">
              <p className="text-[10px] text-slate-500 uppercase">{axis}</p>
              <p className="text-xs font-mono text-slate-200 font-medium">
                {position?.[axis]?.toFixed(2) ?? '--'}
              </p>
            </div>
          ))}
        </div>
      </Section>

      <Section title="Velocity" icon={Navigation}>
        <div className="grid grid-cols-3 gap-2 bg-slate-800/30 rounded-lg p-2">
          {['vx', 'vy', 'vz'].map((axis) => (
            <div key={axis} className="text-center">
              <p className="text-[10px] text-slate-500 uppercase">{axis}</p>
              <p className="text-xs font-mono text-slate-200 font-medium">
                {velocity?.[axis]?.toFixed(2) ?? '--'}
              </p>
              <p className="text-[9px] text-slate-600">m/s</p>
            </div>
          ))}
        </div>
      </Section>

      {/* IMU */}
      {sensors?.imu && (
        <Section title="IMU Sensor" icon={Compass}>
          <div className="space-y-1 bg-slate-800/30 rounded-lg p-2">
            <p className="text-[10px] text-slate-500 mb-1">Accelerometer (m/s2)</p>
            <div className="grid grid-cols-3 gap-2">
              {['ax', 'ay', 'az'].map((k) => (
                <DataRow
                  key={k}
                  label={k.toUpperCase()}
                  value={sensors.imu[k]}
                />
              ))}
            </div>
            <p className="text-[10px] text-slate-500 mt-1.5 mb-1">Gyroscope (rad/s)</p>
            <div className="grid grid-cols-3 gap-2">
              {['gx', 'gy', 'gz'].map((k) => (
                <DataRow
                  key={k}
                  label={k.toUpperCase()}
                  value={sensors.imu[k]}
                />
              ))}
            </div>
          </div>
        </Section>
      )}

      {/* GPS */}
      {sensors?.gps && (
        <Section title="GPS" icon={MapPin}>
          <div className="space-y-0.5 bg-slate-800/30 rounded-lg p-2">
            <DataRow label="Latitude" value={sensors.gps.lat} unit="" />
            <DataRow label="Longitude" value={sensors.gps.lon} unit="" />
            <DataRow label="Altitude" value={sensors.gps.alt} unit="m" />
          </div>
        </Section>
      )}

      {/* System Gauges */}
      <Section title="System" icon={Zap}>
        <div className="space-y-3">
          <GaugeBar
            label="CPU Usage"
            value={cpu_usage}
            icon={Cpu}
            thresholds={{ good: 50, warn: 80 }}
          />
          <GaugeBar
            label="Memory"
            value={memory_usage}
            icon={MemoryStick}
            thresholds={{ good: 60, warn: 85 }}
          />
          <GaugeBar
            label="Temperature"
            value={temperature}
            max={120}
            unit="C"
            icon={Thermometer}
            thresholds={{ good: 50, warn: 66 }}
          />
        </div>
      </Section>

      {/* Last Updated */}
      {robot.last_update && (
        <div className="text-[10px] text-slate-600 text-center pt-1 border-t border-slate-700/30">
          Last update:{' '}
          {new Date(
            typeof robot.last_update === 'number'
              ? robot.last_update > 1e12
                ? robot.last_update
                : robot.last_update * 1000
              : robot.last_update
          ).toLocaleTimeString()}
        </div>
      )}
    </div>
  )
}
