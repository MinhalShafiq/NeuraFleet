import { describe, expect, it } from 'vitest'
import { BACKOFF_MAX_MS, backoffDelay, connectWithBackoff } from './reconnect'

describe('backoffDelay', () => {
  it('grows exponentially within [cap/2, cap]', () => {
    for (const [attempt, cap] of [
      [0, 1000],
      [1, 2000],
      [2, 4000],
      [3, 8000],
    ]) {
      expect(backoffDelay(attempt, () => 0)).toBe(cap / 2)
      expect(backoffDelay(attempt, () => 0.999999)).toBeGreaterThan(cap * 0.99)
      expect(backoffDelay(attempt, () => 0.999999)).toBeLessThanOrEqual(cap)
    }
  })

  it('is capped, however many attempts have failed', () => {
    expect(backoffDelay(50, () => 1)).toBeLessThanOrEqual(BACKOFF_MAX_MS)
    expect(backoffDelay(50, () => 0)).toBe(BACKOFF_MAX_MS / 2)
  })

  it('is jittered: different random draws give different delays', () => {
    expect(new Set([0.1, 0.5, 0.9].map((r) => backoffDelay(3, () => r))).size).toBe(3)
  })
})

function harness() {
  const sockets = []
  const timers = []
  class FakeSocket {
    constructor() {
      this.closed = false
      sockets.push(this)
    }
    close() {
      this.closed = true
      this.onclose?.()
    }
  }
  const opts = {
    random: () => 0, // delay is exactly cap/2
    setTimer: (fn, ms) => {
      const t = { fn, ms, cancelled: false }
      timers.push(t)
      return t
    },
    clearTimer: (t) => {
      t.cancelled = true
    },
  }
  const fire = () => {
    const t = timers.filter((x) => !x.cancelled).pop()
    t.cancelled = true
    t.fn()
  }
  return { sockets, timers, opts, create: () => new FakeSocket(), fire }
}

describe('connectWithBackoff', () => {
  it('reconnects with doubling delays while the server keeps failing', () => {
    const h = harness()
    connectWithBackoff(h.create, {}, h.opts)
    for (let i = 0; i < 4; i++) {
      h.sockets.at(-1).onclose() // server drops us before sending anything
      h.fire()
    }
    expect(h.timers.map((t) => t.ms)).toEqual([500, 1000, 2000, 4000])
    expect(h.sockets).toHaveLength(5)
  })

  it('resets the delay after a message is received, not merely after connecting', () => {
    const h = harness()
    connectWithBackoff(h.create, {}, h.opts)
    h.sockets.at(-1).onclose()
    h.fire()
    h.sockets.at(-1).onclose()
    h.fire() // two failures: next delay would be 2000
    h.sockets.at(-1).onopen()
    h.sockets.at(-1).onclose() // opened but never delivered: still backing off
    expect(h.timers.at(-1).ms).toBe(2000)
    h.fire()
    h.sockets.at(-1).onmessage({ data: '{}' }) // healthy now
    h.sockets.at(-1).onclose()
    expect(h.timers.at(-1).ms).toBe(500)
  })

  it('forwards open/message/close to the handlers', () => {
    const h = harness()
    const events = []
    connectWithBackoff(
      h.create,
      {
        onOpen: () => events.push('open'),
        onMessage: (e) => events.push(e.data),
        onClose: () => events.push('close'),
      },
      h.opts,
    )
    const ws = h.sockets[0]
    ws.onopen()
    ws.onmessage({ data: 'hello' })
    ws.onclose()
    expect(events).toEqual(['open', 'hello', 'close'])
  })

  it('stops reconnecting once close() is called, and cancels a pending retry', () => {
    const h = harness()
    const conn = connectWithBackoff(h.create, {}, h.opts)
    h.sockets[0].onclose() // schedules a retry
    const pending = h.timers.at(-1)
    conn.close()
    expect(pending.cancelled).toBe(true)
    expect(h.sockets).toHaveLength(1)
  })

  it('close() on a live socket closes it without scheduling a retry', () => {
    const h = harness()
    const conn = connectWithBackoff(h.create, {}, h.opts)
    conn.close()
    expect(h.sockets[0].closed).toBe(true)
    expect(h.timers.filter((t) => !t.cancelled)).toHaveLength(0)
  })

  it('retries (with backoff) when the socket constructor throws', () => {
    const h = harness()
    let calls = 0
    const create = () => {
      if (calls++ === 0) throw new Error('boom')
      return h.create()
    }
    connectWithBackoff(create, {}, h.opts)
    expect(h.timers[0].ms).toBe(500)
    h.fire()
    expect(h.sockets).toHaveLength(1)
  })
})
