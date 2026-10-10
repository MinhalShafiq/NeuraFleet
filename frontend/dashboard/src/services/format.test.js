import { describe, expect, it } from 'vitest'
import { timeAgo } from './format'

describe('timeAgo', () => {
  it('returns an empty string for a missing timestamp', () => {
    expect(timeAgo(null)).toBe('')
    expect(timeAgo(undefined)).toBe('')
    expect(timeAgo(0)).toBe('')
  })

  it('reads "just now" for anything under 5 seconds old', () => {
    expect(timeAgo(new Date().toISOString())).toBe('just now')
  })

  it('formats seconds, minutes, hours and days', () => {
    const ago = (ms) => new Date(Date.now() - ms).toISOString()
    expect(timeAgo(ago(30 * 1000))).toBe('30s ago')
    expect(timeAgo(ago(5 * 60 * 1000))).toBe('5m ago')
    expect(timeAgo(ago(3 * 3600 * 1000))).toBe('3h ago')
    expect(timeAgo(ago(2 * 86400 * 1000))).toBe('2d ago')
  })

  it('accepts epoch seconds and epoch milliseconds, not just ISO strings', () => {
    const nowSec = Math.floor(Date.now() / 1000) - 30
    expect(timeAgo(nowSec)).toBe('30s ago')
    expect(timeAgo(nowSec * 1000)).toBe('30s ago')
  })

  it('never goes negative for a timestamp slightly in the future (clock skew)', () => {
    expect(timeAgo(new Date(Date.now() + 2000).toISOString())).toBe('just now')
  })
})
