export const BACKOFF_BASE_MS = 1000
export const BACKOFF_MAX_MS = 30000

/**
 * Exponential backoff with "equal jitter": the delay is uniform in [cap/2, cap], where
 * cap = min(max, base * 2^attempt). The jitter stops many tabs from re-dialling a
 * recovering gateway in lockstep; the floor stops a flapping server from being hammered.
 */
export function backoffDelay(
  attempt,
  random = Math.random,
  base = BACKOFF_BASE_MS,
  max = BACKOFF_MAX_MS,
) {
  const cap = Math.min(max, base * 2 ** attempt)
  return Math.round(cap / 2 + (random() * cap) / 2)
}

/**
 * Keep a WebSocket connected, reconnecting with growing delays until close() is called.
 *
 * The attempt counter resets on the first *message* after connecting, not on open: a server
 * that accepts connections and immediately drops them must still back off.
 *
 * `opts` exists for tests (injectable timers and RNG).
 */
export function connectWithBackoff(createSocket, handlers = {}, opts = {}) {
  const { random = Math.random, setTimer = setTimeout, clearTimer = clearTimeout } = opts
  let attempt = 0
  let ws = null
  let timer = null
  let closed = false

  const schedule = () => {
    if (closed) return
    timer = setTimer(connect, backoffDelay(attempt++, random))
  }

  function connect() {
    timer = null
    if (closed) return
    try {
      ws = createSocket()
    } catch (err) {
      console.error('WebSocket connection failed:', err)
      schedule()
      return
    }
    ws.onopen = () => handlers.onOpen?.()
    ws.onmessage = (event) => {
      attempt = 0
      handlers.onMessage?.(event)
    }
    ws.onclose = () => {
      ws = null
      handlers.onClose?.()
      schedule()
    }
    ws.onerror = () => ws?.close()
  }

  connect()

  return {
    close() {
      closed = true
      if (timer !== null) clearTimer(timer)
      timer = null
      ws?.close()
    },
  }
}
