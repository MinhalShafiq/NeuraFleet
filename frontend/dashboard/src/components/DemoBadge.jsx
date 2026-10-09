import React from 'react'
import { FlaskConical } from 'lucide-react'

/**
 * Shown whenever the gateway had to fabricate the data it is returning because a backing
 * service was unreachable. Without it the dashboard looks identical whether the backend
 * is working or not.
 */
export default function DemoBadge({ className = '' }) {
  return (
    <span
      title="A backing service is unreachable: this data is simulated by the gateway, not live."
      className={`inline-flex items-center gap-1 text-[10px] font-bold tracking-wider uppercase px-2 py-0.5 rounded-full border bg-amber-500/10 border-amber-500/30 text-amber-400 ${className}`}
    >
      <FlaskConical size={10} />
      Demo data
    </span>
  )
}
