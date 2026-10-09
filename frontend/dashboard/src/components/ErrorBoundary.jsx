import React from 'react'

/**
 * Catches render errors (and failed lazy-chunk loads) in a subtree so one broken
 * view - e.g. WebGL unavailable for the point cloud - doesn't white-screen the app.
 */
export default class ErrorBoundary extends React.Component {
  state = { error: null }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    console.error('View crashed:', error, info?.componentStack)
  }

  componentDidUpdate(prevProps) {
    // Navigating to another view clears the error.
    if (this.state.error && prevProps.resetKey !== this.props.resetKey) {
      this.setState({ error: null })
    }
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="flex flex-col items-center justify-center h-full text-center p-8">
        <h3 className="text-lg font-semibold text-slate-300 mb-1">This view failed to load</h3>
        <p className="text-sm text-slate-500 mb-4">
          {String(this.state.error?.message || this.state.error)}
        </p>
        <button
          className="text-xs px-3 py-1.5 rounded-lg border border-slate-700 text-slate-300 hover:bg-slate-800"
          onClick={() => this.setState({ error: null })}
        >
          Retry
        </button>
      </div>
    )
  }
}
