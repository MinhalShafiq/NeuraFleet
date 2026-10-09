import React, { memo, useMemo, useRef } from 'react'
import {
  Bot,
  Battery,
  Activity,
  AlertTriangle,
  Thermometer,
  Cpu,
  MemoryStick,
  MapPin,
} from 'lucide-react'
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Area,
  AreaChart,
} from 'recharts'
import clsx from 'clsx'
import AlertPanel from './AlertPanel'
import TelemetryPanel from './TelemetryPanel'

const STATUS_STYLES = {
  active: 'bg-cyan-400/10 text-cyan-400 border-cyan-400/20',
  idle: 'bg-amber-400/10 text-amber-400 border-amber-400/20',
  charging: 'bg-blue-500/10 text-blue-400 border-blue-400/20',
  error: 'bg-red-500/10 text-red-400 border-red-400/20',
  maintenance: 'bg-gray-400/10 text-gray-400 border-gray-400/20',
}

const StatCard = memo(function StatCard({ icon: Icon, label, value, sub, color }) {
  return (
    <div className="stat-card">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-xs font-medium text-slate-400 uppercase tracking-wider">
            {label}
          </p>
          <p className={clsx('text-2xl font-bold mt-1', color || 'text-white')}>
            {value}
          </p>
          {sub && <p className="text-xs text-slate-500 mt-0.5">{sub}</p>}
        </div>
        <div
          className={clsx(
            'p-2.5 rounded-lg',
            color ? 'bg-slate-800/80' : 'bg-slate-700/50'
          )}
        >
          <Icon size={20} className={color || 'text-slate-400'} />
        </div>
      </div>
    </div>
  )
})

function BatteryBar({ value }) {
  const color =
    value > 50
      ? 'bg-green-500'
      : value > 20
        ? 'bg-amber-500'
        : 'bg-red-500'
  return (
    <div className="w-full bg-slate-700/50 rounded-full h-1.5">
      <div
        className={clsx('h-1.5 rounded-full transition-all duration-500', color)}
        style={{ width: `${Math.min(100, Math.max(0, value))}%` }}
      />
    </div>
  )
}

function MiniBar({ value, max = 100, color = 'bg-primary-500' }) {
  const pct = Math.min(100, Math.max(0, (value / max) * 100))
  return (
    <div className="w-full bg-slate-700/50 rounded-full h-1">
      <div
        className={clsx('h-1 rounded-full transition-all duration-500', color)}
        style={{ width: `${pct}%` }}
      />
    </div>
  )
}

const RobotCard = memo(function RobotCard({ robot, isSelected, onSelect }) {
  return (
    <div
      onClick={() => onSelect(robot)}
      className={clsx(
        'glass-panel p-4 cursor-pointer transition-all duration-200 animate-fade-in',
        isSelected
          ? 'border-primary-500/40 bg-primary-600/5 ring-1 ring-primary-500/20'
          : 'hover:border-slate-600/50 hover:bg-slate-800/70'
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2 min-w-0">
          <Bot
            size={16}
            className={clsx(
              robot.status === 'active'
                ? 'text-cyan-400'
                : robot.status === 'error'
                  ? 'text-red-400'
                  : 'text-slate-400'
            )}
          />
          <div className="min-w-0">
            <h4 className="text-sm font-semibold text-white truncate">
              {robot.name}
            </h4>
            <p className="text-[10px] text-slate-500 uppercase">{robot.robot_type}</p>
          </div>
        </div>
        <span
          className={clsx(
            'text-[10px] font-semibold uppercase px-2 py-0.5 rounded-full border',
            STATUS_STYLES[robot.status]
          )}
        >
          {robot.status}
        </span>
      </div>

      {/* Battery */}
      <div className="mb-3">
        <div className="flex items-center justify-between mb-1">
          <span className="text-[11px] text-slate-400 flex items-center gap-1">
            <Battery size={11} /> Battery
          </span>
          <span
            className={clsx(
              'text-xs font-mono font-bold',
              robot.battery > 50
                ? 'text-green-400'
                : robot.battery > 20
                  ? 'text-amber-400'
                  : 'text-red-400'
            )}
          >
            {robot.battery?.toFixed(1)}%
          </span>
        </div>
        <BatteryBar value={robot.battery} />
      </div>

      {/* Position */}
      <div className="flex items-center gap-1 text-[11px] text-slate-400 mb-2">
        <MapPin size={11} />
        <span className="font-mono">
          ({robot.position?.x?.toFixed(1)}, {robot.position?.y?.toFixed(1)},{' '}
          {robot.position?.z?.toFixed(1)})
        </span>
      </div>

      {/* Temperature */}
      <div className="flex items-center justify-between mb-2">
        <span className="text-[11px] text-slate-400 flex items-center gap-1">
          <Thermometer size={11} /> Temp
        </span>
        <span
          className={clsx(
            'text-xs font-mono font-medium',
            robot.temperature > 80
              ? 'text-red-400'
              : robot.temperature > 60
                ? 'text-amber-400'
                : 'text-green-400'
          )}
        >
          {robot.temperature?.toFixed(1)}C
        </span>
      </div>

      {/* CPU & Memory bars */}
      <div className="space-y-1.5">
        <div>
          <div className="flex items-center justify-between mb-0.5">
            <span className="text-[10px] text-slate-500 flex items-center gap-1">
              <Cpu size={9} /> CPU
            </span>
            <span className="text-[10px] text-slate-400 font-mono">
              {robot.cpu_usage?.toFixed(0)}%
            </span>
          </div>
          <MiniBar
            value={robot.cpu_usage}
            color={
              robot.cpu_usage > 80
                ? 'bg-red-500'
                : robot.cpu_usage > 50
                  ? 'bg-amber-500'
                  : 'bg-cyan-500'
            }
          />
        </div>
        <div>
          <div className="flex items-center justify-between mb-0.5">
            <span className="text-[10px] text-slate-500 flex items-center gap-1">
              <MemoryStick size={9} /> MEM
            </span>
            <span className="text-[10px] text-slate-400 font-mono">
              {robot.memory_usage?.toFixed(0)}%
            </span>
          </div>
          <MiniBar
            value={robot.memory_usage}
            color={
              robot.memory_usage > 80
                ? 'bg-red-500'
                : robot.memory_usage > 50
                  ? 'bg-amber-500'
                  : 'bg-purple-500'
            }
          />
        </div>
      </div>
    </div>
  )
})

function ChartTooltip({ active, payload }) {
  if (!active || !payload?.length) return null
  return (
    <div className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 shadow-xl">
      {payload.map((p) => (
        <p key={p.name} className="text-xs" style={{ color: p.color }}>
          {p.name}: {p.value?.toFixed(1)}
        </p>
      ))}
    </div>
  )
}

const EMPTY_HISTORY = []

const TelemetryCharts = memo(function TelemetryCharts({ robotId, telemetryHistory }) {
  const data = telemetryHistory[robotId] || EMPTY_HISTORY

  const chartData = useMemo(
    () =>
      data.map((d, i) => ({
        idx: i,
        battery: d.battery,
        temperature: d.temperature,
        cpu: d.cpu_usage,
      })),
    [data],
  )

  if (data.length === 0) {
    return (
      <div className="glass-panel p-6 flex items-center justify-center h-64">
        <p className="text-sm text-slate-500">
          Select a robot to view telemetry charts
        </p>
      </div>
    )
  }

  return (
    <div className="glass-panel p-4">
      <h3 className="text-sm font-semibold text-slate-300 mb-3">
        Telemetry History
      </h3>
      <div className="h-56">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={chartData}>
            <defs>
              <linearGradient id="gradBattery" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#22d3ee" stopOpacity={0.15} />
                <stop offset="95%" stopColor="#22d3ee" stopOpacity={0} />
              </linearGradient>
              <linearGradient id="gradTemp" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.15} />
                <stop offset="95%" stopColor="#f59e0b" stopOpacity={0} />
              </linearGradient>
              <linearGradient id="gradCpu" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#a78bfa" stopOpacity={0.15} />
                <stop offset="95%" stopColor="#a78bfa" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
            <XAxis
              dataKey="idx"
              tick={false}
              axisLine={{ stroke: '#475569' }}
              tickLine={false}
            />
            <YAxis
              tick={{ fontSize: 10, fill: '#94a3b8' }}
              axisLine={{ stroke: '#475569' }}
              tickLine={false}
              domain={[0, 100]}
            />
            <Tooltip content={<ChartTooltip />} />
            <Area
              name="Battery"
              type="monotone"
              dataKey="battery"
              stroke="#22d3ee"
              fill="url(#gradBattery)"
              strokeWidth={2}
              dot={false}
            />
            <Area
              name="Temperature"
              type="monotone"
              dataKey="temperature"
              stroke="#f59e0b"
              fill="url(#gradTemp)"
              strokeWidth={2}
              dot={false}
            />
            <Area
              name="CPU"
              type="monotone"
              dataKey="cpu"
              stroke="#a78bfa"
              fill="url(#gradCpu)"
              strokeWidth={2}
              dot={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <div className="flex items-center gap-4 mt-2 justify-center">
        {Object.entries({ Battery: '#22d3ee', Temperature: '#f59e0b', CPU: '#a78bfa' }).map(
          ([label, color]) => (
            <div key={label} className="flex items-center gap-1.5">
              <div className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: color }} />
              <span className="text-[10px] text-slate-400">{label}</span>
            </div>
          )
        )}
      </div>
    </div>
  )
})

// id -> name map whose identity only changes when a name does, so memoized
// children (AlertPanel) don't re-render on every 2 Hz telemetry frame.
function useStableNames(fleet) {
  const ref = useRef({})
  const next = {}
  for (const r of fleet) next[r.robot_id] = r.name
  const prev = ref.current
  const prevKeys = Object.keys(prev)
  const same =
    prevKeys.length === Object.keys(next).length &&
    prevKeys.every((k) => prev[k] === next[k])
  if (!same) ref.current = next
  return ref.current
}

export default function FleetMonitor({
  fleet,
  alerts,
  selectedRobot,
  onSelectRobot,
  telemetryHistory,
}) {
  const { activeCount, errorCount, avgBattery, minBattery } = useMemo(() => {
    let active = 0
    let errors = 0
    let sum = 0
    let min = 100
    for (const r of fleet) {
      if (r.status === 'active') active++
      if (r.status === 'error') errors++
      sum += r.battery || 0
      min = Math.min(min, r.battery || 100)
    }
    return {
      activeCount: active,
      errorCount: errors,
      avgBattery: fleet.length > 0 ? sum / fleet.length : 0,
      minBattery: min,
    }
  }, [fleet])
  const criticalAlerts = useMemo(
    () => alerts.filter((a) => a.severity === 'critical').length,
    [alerts],
  )
  const robotNames = useStableNames(fleet)

  if (fleet.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full animate-fade-in">
        <div className="w-16 h-16 rounded-full bg-slate-800 flex items-center justify-center mb-4">
          <Bot size={28} className="text-slate-600" />
        </div>
        <h3 className="text-lg font-semibold text-slate-400 mb-1">
          Waiting for fleet data...
        </h3>
        <p className="text-sm text-slate-600">
          Connecting to telemetry stream
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-4 animate-fade-in">
      {/* Stat Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatCard
          icon={Bot}
          label="Total Robots"
          value={fleet.length}
          sub={`${errorCount} with errors`}
        />
        <StatCard
          icon={Activity}
          label="Active Robots"
          value={activeCount}
          sub={`${((activeCount / fleet.length) * 100).toFixed(0)}% utilization`}
          color="text-cyan-400"
        />
        <StatCard
          icon={Battery}
          label="Avg Battery"
          value={`${avgBattery.toFixed(0)}%`}
          sub={`Min: ${minBattery.toFixed(0)}%`}
          color={
            avgBattery > 50
              ? 'text-green-400'
              : avgBattery > 20
                ? 'text-amber-400'
                : 'text-red-400'
          }
        />
        <StatCard
          icon={AlertTriangle}
          label="Active Alerts"
          value={alerts.length}
          sub={criticalAlerts > 0 ? `${criticalAlerts} critical` : 'All clear'}
          color={
            criticalAlerts > 0
              ? 'text-red-400'
              : alerts.length > 0
                ? 'text-amber-400'
                : 'text-green-400'
          }
        />
      </div>

      {/* Middle Row: Robot Grid + Alerts */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        {/* Robot Cards Grid */}
        <div className="xl:col-span-2">
          <div className="grid grid-cols-1 md:grid-cols-2 2xl:grid-cols-3 gap-3">
            {fleet.map((robot) => (
              <RobotCard
                key={robot.robot_id}
                robot={robot}
                isSelected={selectedRobot?.robot_id === robot.robot_id}
                onSelect={onSelectRobot}
              />
            ))}
          </div>
        </div>

        {/* Alerts + Telemetry Detail */}
        <div className="space-y-4">
          <AlertPanel alerts={alerts} robotNames={robotNames} />
          {selectedRobot && (
            <TelemetryPanel robot={selectedRobot} />
          )}
        </div>
      </div>

      {/* Bottom Row: Charts */}
      <TelemetryCharts
        robotId={selectedRobot?.robot_id}
        telemetryHistory={telemetryHistory}
      />
    </div>
  )
}
