import React, { memo, useMemo } from 'react'
import { AlertTriangle, AlertCircle, Info, CheckCircle, Shield } from 'lucide-react'
import clsx from 'clsx'

const SEVERITY_CONFIG = {
  critical: {
    icon: AlertCircle,
    color: 'text-red-400',
    bg: 'bg-red-500/10',
    border: 'border-red-500/20',
    dot: 'bg-red-500',
  },
  warning: {
    icon: AlertTriangle,
    color: 'text-amber-400',
    bg: 'bg-amber-500/10',
    border: 'border-amber-500/20',
    dot: 'bg-amber-500',
  },
  info: {
    icon: Info,
    color: 'text-blue-400',
    bg: 'bg-blue-500/10',
    border: 'border-blue-500/20',
    dot: 'bg-blue-500',
  },
}

function timeAgo(timestamp) {
  if (!timestamp) return ''
  const now = Date.now()
  const ts =
    typeof timestamp === 'number'
      ? timestamp > 1e12
        ? timestamp
        : timestamp * 1000
      : new Date(timestamp).getTime()
  const diff = Math.max(0, now - ts)
  const seconds = Math.floor(diff / 1000)

  if (seconds < 5) return 'just now'
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  return `${days}d ago`
}

const AlertItem = memo(function AlertItem({ alert, robotName }) {
  const config = SEVERITY_CONFIG[alert.severity] || SEVERITY_CONFIG.info
  const Icon = config.icon

  return (
    <div
      className={clsx(
        'flex gap-3 p-3 rounded-lg border transition-all duration-300 animate-slide-up',
        config.bg,
        config.border,
      )}
    >
      <div className={clsx('mt-0.5 shrink-0', config.color)}>
        <Icon size={16} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between gap-2 mb-0.5">
          <span className="text-xs font-medium text-slate-200 truncate">
            {robotName || alert.robot_id}
          </span>
          <span className="text-[10px] text-slate-500 shrink-0 font-mono">
            {timeAgo(alert.timestamp)}
          </span>
        </div>
        <p className="text-xs text-slate-400 leading-relaxed">{alert.message}</p>
        <div className="flex items-center gap-2 mt-1.5">
          <span
            className={clsx(
              'text-[9px] font-bold uppercase px-1.5 py-0.5 rounded border',
              config.bg,
              config.border,
              config.color,
            )}
          >
            {alert.severity}
          </span>
          {alert.type && (
            <span className="text-[9px] uppercase text-slate-500 bg-slate-800 px-1.5 py-0.5 rounded border border-slate-700/50">
              {alert.type}
            </span>
          )}
        </div>
      </div>
    </div>
  )
})

// Takes a stable id->name map instead of the fleet array: the fleet changes at 2 Hz,
// alerts only every few seconds, so this panel should not re-render with telemetry.
function AlertPanel({ alerts, robotNames }) {
  const sortedAlerts = useMemo(
    () =>
      [...(alerts || [])].sort((a, b) => {
        const tsA =
          typeof a.timestamp === 'number'
            ? a.timestamp > 1e12
              ? a.timestamp
              : a.timestamp * 1000
            : new Date(a.timestamp).getTime()
        const tsB =
          typeof b.timestamp === 'number'
            ? b.timestamp > 1e12
              ? b.timestamp
              : b.timestamp * 1000
            : new Date(b.timestamp).getTime()
        return tsB - tsA
      }),
    [alerts],
  )

  return (
    <div className="glass-panel p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Shield size={14} className="text-slate-400" />
          <h3 className="text-sm font-semibold text-slate-300">Active Alerts</h3>
        </div>
        {alerts.length > 0 && (
          <span className="text-[10px] font-bold text-slate-500 bg-slate-800 px-2 py-0.5 rounded-full">
            {alerts.length}
          </span>
        )}
      </div>

      <div className="max-h-80 overflow-y-auto space-y-2">
        {sortedAlerts.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-8 text-center">
            <CheckCircle size={24} className="text-green-500/50 mb-2" />
            <p className="text-sm text-slate-500">No active alerts</p>
            <p className="text-xs text-slate-600 mt-0.5">All systems operating normally</p>
          </div>
        ) : (
          sortedAlerts.map((alert) => (
            <AlertItem key={alert.id} alert={alert} robotName={robotNames?.[alert.robot_id]} />
          ))
        )}
      </div>
    </div>
  )
}

export default memo(AlertPanel)
