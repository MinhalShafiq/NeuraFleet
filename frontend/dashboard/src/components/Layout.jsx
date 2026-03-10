import React, { useState } from 'react'
import {
  LayoutDashboard,
  Radar,
  MessageSquare,
  Bot,
  ChevronLeft,
  ChevronRight,
  Bell,
  Wifi,
  WifiOff,
  Cpu,
} from 'lucide-react'
import clsx from 'clsx'

const STATUS_COLORS = {
  active: 'bg-cyan-400 ring-cyan-400/30',
  idle: 'bg-amber-400 ring-amber-400/30',
  charging: 'bg-blue-500 ring-blue-500/30',
  error: 'bg-red-500 ring-red-500/30',
  maintenance: 'bg-gray-400 ring-gray-400/30',
}

const NAV_ITEMS = [
  { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { id: 'lidar', label: 'LiDAR Viewer', icon: Radar },
  { id: 'chat', label: 'Fleet Chat', icon: MessageSquare },
]

const VIEW_TITLES = {
  dashboard: 'Fleet Dashboard',
  lidar: 'LiDAR Point Cloud Viewer',
  chat: 'Fleet AI Assistant',
}

export default function Layout({
  fleet,
  alerts,
  activeView,
  onChangeView,
  selectedRobot,
  onSelectRobot,
  connected,
  children,
}) {
  const [collapsed, setCollapsed] = useState(false)

  const now = new Date()
  const timeStr = now.toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
  const dateStr = now.toLocaleDateString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  })

  const criticalAlerts = alerts.filter((a) => a.severity === 'critical').length

  return (
    <div className="flex h-screen overflow-hidden bg-slate-900">
      {/* Sidebar */}
      <aside
        className={clsx(
          'flex flex-col border-r border-slate-700/50 bg-slate-900 transition-all duration-300 shrink-0',
          collapsed ? 'w-16' : 'w-64'
        )}
      >
        {/* Logo */}
        <div className="flex items-center gap-3 px-4 py-5 border-b border-slate-700/50">
          <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-primary-600 shrink-0">
            <Cpu className="w-5 h-5 text-white" />
          </div>
          {!collapsed && (
            <div className="animate-fade-in">
              <h1 className="text-base font-bold text-white tracking-tight">
                NeuraFleet
              </h1>
              <p className="text-[10px] text-slate-500 font-medium uppercase tracking-widest">
                Command Center
              </p>
            </div>
          )}
          <button
            onClick={() => setCollapsed(!collapsed)}
            className="ml-auto p-1 rounded hover:bg-slate-800 text-slate-400 hover:text-white transition-colors"
          >
            {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
          </button>
        </div>

        {/* Navigation */}
        <nav className="px-2 py-3 space-y-1">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon
            const isActive = activeView === item.id
            return (
              <button
                key={item.id}
                onClick={() => onChangeView(item.id)}
                className={clsx(
                  'nav-item w-full',
                  isActive ? 'nav-item-active' : 'nav-item-inactive'
                )}
                title={collapsed ? item.label : undefined}
              >
                <Icon size={18} className="shrink-0" />
                {!collapsed && <span>{item.label}</span>}
              </button>
            )
          })}
        </nav>

        {/* Fleet Status */}
        <div className="flex-1 overflow-hidden flex flex-col px-2 pt-2">
          {!collapsed && (
            <h3 className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider px-3 mb-2">
              Fleet Status
            </h3>
          )}
          <div className="flex-1 overflow-y-auto space-y-0.5">
            {fleet.map((robot) => (
              <button
                key={robot.robot_id}
                onClick={() => onSelectRobot(robot)}
                className={clsx(
                  'w-full flex items-center gap-2.5 px-3 py-2 rounded-lg transition-all duration-150 text-left',
                  selectedRobot?.robot_id === robot.robot_id
                    ? 'bg-slate-800 border border-slate-600/50'
                    : 'hover:bg-slate-800/50'
                )}
                title={collapsed ? `${robot.name} - ${robot.status}` : undefined}
              >
                <span
                  className={clsx('status-dot shrink-0', STATUS_COLORS[robot.status])}
                />
                {!collapsed && (
                  <div className="min-w-0 flex-1 animate-fade-in">
                    <p className="text-sm font-medium text-slate-200 truncate">
                      {robot.name}
                    </p>
                    <p className="text-[11px] text-slate-500 capitalize">
                      {robot.robot_type} - {robot.status}
                    </p>
                  </div>
                )}
                {!collapsed && (
                  <div className="text-right shrink-0">
                    <span
                      className={clsx(
                        'text-xs font-mono font-medium',
                        robot.battery > 50
                          ? 'text-green-400'
                          : robot.battery > 20
                            ? 'text-amber-400'
                            : 'text-red-400'
                      )}
                    >
                      {robot.battery?.toFixed(0)}%
                    </span>
                  </div>
                )}
              </button>
            ))}
            {fleet.length === 0 && !collapsed && (
              <p className="text-xs text-slate-600 text-center py-4">
                No robots detected
              </p>
            )}
          </div>
        </div>

        {/* Connection Status */}
        <div className="px-3 py-3 border-t border-slate-700/50">
          <div className="flex items-center gap-2">
            {connected ? (
              <Wifi size={14} className="text-green-400 shrink-0" />
            ) : (
              <WifiOff size={14} className="text-red-400 shrink-0 animate-pulse" />
            )}
            {!collapsed && (
              <span
                className={clsx(
                  'text-xs font-medium',
                  connected ? 'text-green-400' : 'text-red-400'
                )}
              >
                {connected ? 'Connected' : 'Reconnecting...'}
              </span>
            )}
          </div>
        </div>
      </aside>

      {/* Main Area */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {/* Top Bar */}
        <header className="flex items-center justify-between px-6 py-3 border-b border-slate-700/50 bg-slate-900/80 backdrop-blur-sm shrink-0">
          <div className="flex items-center gap-3">
            <h2 className="text-lg font-semibold text-white">
              {VIEW_TITLES[activeView]}
            </h2>
            {selectedRobot && (
              <span className="text-xs bg-slate-800 border border-slate-700 text-slate-300 px-2.5 py-1 rounded-full font-medium">
                <Bot size={12} className="inline mr-1 -mt-0.5" />
                {selectedRobot.name}
              </span>
            )}
          </div>
          <div className="flex items-center gap-4">
            {/* Alert Badge */}
            <button className="relative p-2 rounded-lg hover:bg-slate-800 transition-colors text-slate-400 hover:text-white">
              <Bell size={18} />
              {alerts.length > 0 && (
                <span
                  className={clsx(
                    'absolute -top-0.5 -right-0.5 min-w-[18px] h-[18px] flex items-center justify-center rounded-full text-[10px] font-bold text-white px-1',
                    criticalAlerts > 0
                      ? 'bg-red-500 animate-pulse'
                      : 'bg-amber-500'
                  )}
                >
                  {alerts.length}
                </span>
              )}
            </button>
            {/* Timestamp */}
            <div className="text-right">
              <p className="text-xs font-mono text-slate-300">{timeStr}</p>
              <p className="text-[10px] text-slate-500">{dateStr}</p>
            </div>
          </div>
        </header>

        {/* Content */}
        <main className="flex-1 overflow-auto p-4 lg:p-6">{children}</main>
      </div>
    </div>
  )
}
