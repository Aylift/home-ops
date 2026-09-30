// Dev-only mock backend for `npm run dev`.
//
// The real backend runs on the server, so the frontend can't reach it from a
// laptop without a tunnel. This Vite plugin serves the handful of endpoints the
// dashboard calls, with plausible data, so UI work needs no backend at all.
//
// Enabled only when MOCK_API=1 (see `npm run dev:mock`). Never part of a build.

const NODE = { node_id: 'basement', name: 'Basement', enabled: true }

// Signed override state, mirroring the real router's semantics:
// sign = desired state, magnitude = duration, absolute expiry.
let override = null // { desired_state: bool, expires_at: number (ms) }

function remainingSeconds() {
  if (!override) return 0
  return Math.max(0, Math.round((override.expires_at - Date.now()) / 1000))
}

function overrideOut() {
  const remaining = remainingSeconds()
  const active = remaining > 0
  return {
    node_id: NODE.node_id,
    active,
    desired_state: active ? override.desired_state : null,
    expires_at: active ? new Date(override.expires_at).toISOString() : null,
    remaining_seconds: remaining,
  }
}

function setOverride(minutes) {
  const now = Date.now()
  const desired = minutes > 0
  const duration = Math.abs(minutes)
  const sameState = override && override.desired_state === desired && override.expires_at > now
  const base = sameState ? override.expires_at : now
  const cap = now + 24 * 60 * 60 * 1000
  override = { desired_state: desired, expires_at: Math.min(base + duration * 60000, cap) }
  return overrideOut()
}

// Deterministic-ish telemetry with a slow sine so the chart looks alive.
function telemetryAt(ts) {
  const t = ts / 1000
  const temp = 21 + Math.sin(t / 900) * 1.5
  const hum = 62 + Math.sin(t / 700) * 8
  const press = 1002 + Math.sin(t / 1800) * 3
  const ahIn = 11.5 + Math.sin(t / 800) * 1.2
  const ahOut = 10.8 + Math.sin(t / 1100) * 1.5
  return {
    node_id: NODE.node_id,
    timestamp: new Date(ts).toISOString(),
    received_at: new Date(ts).toISOString(),
    temperature: +temp.toFixed(2),
    humidity: +hum.toFixed(2),
    pressure: +press.toFixed(1),
    ah_inside: +ahIn.toFixed(2),
    ah_outside: +ahOut.toFixed(2),
    fan_active: ahIn - ahOut > 0.5,
    mode: ahIn - ahOut > 0.5 ? 'API (Outside dry)' : 'STANDBY (Normal)',
    override_seconds: remainingSeconds(),
  }
}

function history(hours, limit) {
  const now = Date.now()
  const step = 5 * 60 * 1000 // device cadence
  const count = Math.min(limit, Math.floor((hours * 3600 * 1000) / step))
  const rows = []
  for (let i = count - 1; i >= 0; i--) rows.push(telemetryAt(now - i * step))
  return rows
}

const EVENTS = [
  { type: 'action', code: 'fan_override', message: 'Fan override ON 10m' },
  { type: 'action', code: 'fan_state', message: 'Fan turned ON' },
  { type: 'action', code: 'fan_state', message: 'Fan turned OFF' },
  { type: 'action', code: 'fan_override', message: 'Fan override cleared' },
]

function events(limit) {
  const now = Date.now()
  return EVENTS.slice(0, limit).map((e, i) => ({
    ...e,
    node_id: NODE.node_id,
    timestamp: new Date(now - i * 37 * 60 * 1000).toISOString(),
  }))
}

function weather() {
  const now = Date.now()
  return {
    temperature_c: 14.2,
    relative_humidity_pct: 71,
    absolute_humidity_g_m3: 8.6,
    wind_speed_mps: 3.4,
    cloud_pct: 82,
    description: 'overcast clouds',
    icon: '04d',
    station_name: 'Mock Station',
    observed_at: new Date(now - 5 * 60 * 1000).toISOString(),
    fetched_at: new Date(now).toISOString(),
    stale: false,
  }
}

function nodeStatus() {
  return {
    node_id: NODE.node_id,
    name: NODE.name,
    enabled: true,
    alive: true,
    last_seen: new Date().toISOString(),
  }
}

function json(res, body, status = 200) {
  res.statusCode = status
  res.setHeader('Content-Type', 'application/json')
  res.end(JSON.stringify(body))
}

function readBody(req) {
  return new Promise((resolve) => {
    let raw = ''
    req.on('data', (c) => (raw += c))
    req.on('end', () => {
      try {
        resolve(raw ? JSON.parse(raw) : {})
      } catch {
        resolve({})
      }
    })
  })
}

export function mockApi() {
  return {
    name: 'mock-api',
    configureServer(server) {
      server.middlewares.use(async (req, res, next) => {
        const url = new URL(req.url, 'http://localhost')
        const path = url.pathname
        if (!path.startsWith('/api/')) return next()

        const q = url.searchParams
        const limit = Number(q.get('limit') || 10)
        const hours = Number(q.get('hours') || 24)

        if (path === '/api/nodes' && req.method === 'GET') return json(res, [NODE])
        if (path === `/api/nodes/${NODE.node_id}/status`) return json(res, nodeStatus())
        if (path === '/api/telemetry/latest') return json(res, telemetryAt(Date.now()))
        if (path === '/api/telemetry/history') return json(res, history(hours, limit))
        if (path === '/api/events') return json(res, events(limit))
        if (path === '/api/weather') return json(res, weather())

        if (path === '/api/fan/override') {
          if (req.method === 'GET') return json(res, overrideOut())
          if (req.method === 'POST') {
            const body = await readBody(req)
            return json(res, setOverride(Number(body.minutes) || 0))
          }
          if (req.method === 'DELETE') {
            override = null
            return json(res, overrideOut())
          }
        }

        return json(res, { detail: 'Not found (mock)' }, 404)
      })
    },
  }
}
