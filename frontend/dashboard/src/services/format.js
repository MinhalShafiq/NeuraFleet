// Small presentation helpers shared across components. Pure functions, unit-tested
// without rendering anything - same pattern as pointcloud.js / robotModels.js.

/** "3s ago", "5m ago", ... from an ISO timestamp, epoch seconds, or epoch ms. */
export function timeAgo(timestamp) {
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
