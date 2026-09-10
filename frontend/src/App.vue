<script setup>
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { RefreshCw, Fan, History, Activity, CloudSun, Home } from '@lucide/vue'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import HistoryChart from '@/components/HistoryChart.vue'

// Same-origin when served by the backend; override with VITE_API_URL for dev.
const API = import.meta.env.VITE_API_URL || ''

const telemetry = ref(null)
const actions = ref([])
const nodes = ref([])
const history = ref([])
const fanHistory = ref([])
const status = ref(null)
const weather = ref(null)
const error = ref('')
const loading = ref(false)
const historyLoading = ref(false)
const nodeId = ref('basement')
// History window presets. "max" pulls the largest window the backend allows.
const ranges = [
  { label: '6h', hours: 6, limit: 2000 },
  { label: '24h', hours: 24, limit: 2000 },
  { label: '7d', hours: 168, limit: 2000 },
  { label: '1m', hours: 720, limit: 2000 },
  { label: 'max', hours: 720, limit: 10000 },
]
const range = ref(ranges[1]) // default 24h

async function fetchNodes() {
  try {
    const res = await fetch(`${API}/api/nodes`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    nodes.value = await res.json()
    // Default to the first enabled node if the current one isn't present.
    if (nodes.value.length && !nodes.value.some((n) => n.node_id === nodeId.value)) {
      nodeId.value = nodes.value[0].node_id
    }
  } catch (e) {
    console.warn('Failed to fetch nodes:', e)
  }
}

async function fetchStatus() {
  try {
    const res = await fetch(`${API}/api/nodes/${nodeId.value}/status`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    status.value = await res.json()
  } catch (e) {
    console.warn('Failed to fetch node status:', e)
  }
}

// Current outside weather (backend is the single weather authority). Optional:
// 404 when weather is disabled, so a failure must not break the dashboard.
async function fetchWeather() {
  try {
    const res = await fetch(`${API}/api/weather`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    weather.value = await res.json()
  } catch (e) {
    weather.value = null
    console.warn('Failed to fetch weather:', e)
  }
}

async function fetchLatest() {
  loading.value = true
  error.value = ''
  try {
    const tRes = await fetch(`${API}/api/telemetry/latest?node_id=${nodeId.value}`)
    if (!tRes.ok) throw new Error(`HTTP ${tRes.status}`)
    telemetry.value = await tRes.json()

    // Events are secondary: a failure here must not block telemetry updates.
    try {
      const aRes = await fetch(`${API}/api/events?node_id=${nodeId.value}&limit=10`)
      if (aRes.ok) actions.value = await aRes.json()
    } catch (e) {
      console.warn('Failed to fetch events:', e)
    }
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function fetchHistory() {
  historyLoading.value = true
  try {
    const res = await fetch(
      `${API}/api/telemetry/history?node_id=${nodeId.value}&hours=${range.value.hours}&limit=${range.value.limit}`
    )
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    history.value = await res.json()
  } catch (e) {
    console.warn('Failed to fetch history:', e)
  } finally {
    historyLoading.value = false
  }
}

// Fan runtime always reflects the past 24h, independent of the chart window.
async function fetchFanHistory() {
  try {
    const res = await fetch(
      `${API}/api/telemetry/history?node_id=${nodeId.value}&hours=24&limit=2000`
    )
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    fanHistory.value = await res.json()
  } catch (e) {
    console.warn('Failed to fetch fan history:', e)
  }
}

let timer
onMounted(() => {
  fetchNodes()
  fetchLatest()
  fetchStatus()
  fetchWeather()
  fetchHistory()
  fetchFanHistory()
  // Device POSTs telemetry every 5 min (LOOP_INTERVAL). Poll every 30s so the
  // dashboard picks up a new sample promptly without hammering the backend.
  timer = setInterval(() => {
    fetchLatest()
    fetchStatus()
    fetchWeather()
    fetchHistory()
    fetchFanHistory()
  }, 30000)
})
onUnmounted(() => clearInterval(timer))

// Reload live cards + history when the selected node changes.
watch(nodeId, () => {
  fetchLatest()
  fetchStatus()
  fetchHistory()
  fetchFanHistory()
})

// Reload history when the time window changes.
watch(range, fetchHistory)

function fmtTime(ts) {
  if (!ts) return '—'
  const d = new Date(ts)
  return isNaN(d.getTime()) ? '—' : d.toLocaleString()
}

const metrics = [
  { label: 'Temperature', value: 'temperature', unit: '°C', decimals: 1 },
  { label: 'Humidity', value: 'humidity', unit: '%', decimals: 0 },
  { label: 'AH inside', value: 'ah_inside', unit: 'g/m³', decimals: 1 },
  { label: 'Dew point', value: 'dew_point', unit: '°C', decimals: 1 },
  { label: 'Pressure', value: 'pressure', unit: 'hPa', decimals: 1 },
]

function metricValue(m) {
  // Dew point is derived (not stored) — compute it from temp + RH.
  const v =
    m.value === 'dew_point'
      ? dewPoint(telemetry.value?.temperature, telemetry.value?.humidity)
      : telemetry.value && telemetry.value[m.value]
  return v != null ? v.toFixed(m.decimals) : '—'
}

// Magnus formula dew point (°C) from temperature (°C) and relative humidity (%).
function dewPoint(temp, rh) {
  if (temp == null || rh == null) return null
  const a = 17.62
  const b = 243.12
  const gamma = Math.log(rh / 100) + (a * temp) / (b + temp)
  return (b * gamma) / (a - gamma)
}

// AH difference (inside - outside). >0 means outside is drier -> ventilating helps.
function ahDiff() {
  const t = telemetry.value
  if (!t || t.ah_inside == null || t.ah_outside == null) return null
  return t.ah_inside - t.ah_outside
}

// Climate status from current RH: Good <=60, Medium 60-70, Bad >70.
function climateStatus() {
  const rh = telemetry.value?.humidity
  if (rh == null) return null
  if (rh <= 60) return { label: 'Good', color: 'bg-emerald-500', text: 'text-emerald-600', bg: 'bg-emerald-500/15' }
  if (rh <= 70) return { label: 'Medium', color: 'bg-amber-500', text: 'text-amber-600', bg: 'bg-amber-500/15' }
  return { label: 'Bad', color: 'bg-red-500', text: 'text-red-600', bg: 'bg-red-500/15' }
}

// Fan-on time over the past 24h, estimated from fan_active samples.
function fanRuntime() {
  const rows = fanHistory.value || []
  if (rows.length < 2) return null
  let onSec = 0
  for (let i = 1; i < rows.length; i++) {
    const prev = rows[i - 1]
    const cur = rows[i]
    if (prev.fan_active) {
      const dt = (new Date(cur.timestamp) - new Date(prev.timestamp)) / 1000
      if (dt > 0 && dt < 3600) onSec += dt // ignore gaps >1h (device offline)
    }
  }
  const h = Math.floor(onSec / 3600)
  const m = Math.round((onSec % 3600) / 60)
  return h > 0 ? `${h}h ${m}m` : `${m}m`
}

// OWM icon codes (e.g. "04n") map to a hosted image; drop the raw code from the UI.
function weatherIconUrl(icon) {
  return icon ? `https://openweathermap.org/img/wn/${icon}@2x.png` : ''
}
</script>

<template>
  <div class="min-h-screen bg-background p-6">
    <div class="mx-auto max-w-6xl space-y-6">
      <header class="flex items-center justify-between gap-4">
        <div>
          <h1 class="flex items-center gap-2 text-2xl font-bold">
            Climate Dashboard
            <span
              v-if="status"
              class="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium"
              :class="status.alive ? 'bg-emerald-500/15 text-emerald-600' : 'bg-red-500/15 text-red-600'"
            >
              <span
                class="size-2 rounded-full"
                :class="status.alive ? 'bg-emerald-500' : 'bg-red-500'"
              ></span>
              {{ status.alive ? 'Online' : 'Offline' }}
            </span>
          </h1>
          <p class="text-muted-foreground text-sm">
            Last update: {{ fmtTime(telemetry?.timestamp) }}
          </p>
        </div>
        <div class="flex items-center gap-3">
          <select
            v-model="nodeId"
            class="h-9 rounded-md border border-input bg-background px-3 text-sm"
            aria-label="Select node"
          >
            <option v-for="n in nodes" :key="n.node_id" :value="n.node_id">
              {{ n.name || n.node_id }}
            </option>
            <option v-if="!nodes.length" value="basement">basement</option>
          </select>
          <Button :disabled="loading" @click="fetchLatest">
            <RefreshCw :class="loading ? 'animate-spin' : ''" />
            Refresh
          </Button>
        </div>
      </header>

      <p v-if="error" class="text-destructive text-sm">Failed to load: {{ error }}</p>

      <!-- Fan status -->
      <Card v-if="telemetry">
        <CardHeader>
          <CardTitle class="flex items-center gap-2">
            <Fan :class="telemetry.fan_active ? 'text-primary' : 'text-muted-foreground'" />
            Fan {{ telemetry.fan_active ? 'ON' : 'OFF' }}
            <span
              v-if="climateStatus()"
              class="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium"
              :class="climateStatus().bg + ' ' + climateStatus().text"
            >
              <span class="size-2 rounded-full" :class="climateStatus().color"></span>
              {{ climateStatus().label }} RH {{ telemetry.humidity != null ? telemetry.humidity.toFixed(0) : '—' }}%
            </span>
          </CardTitle>
          <CardDescription class="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span>{{ telemetry.mode }}</span>
            <span v-if="ahDiff() != null" class="text-muted-foreground">
              AH diff {{ ahDiff() >= 0 ? '+' : '' }}{{ ahDiff().toFixed(1) }} g/m³
            </span>
            <span v-if="fanRuntime()" class="text-muted-foreground">
              Fan on {{ fanRuntime() }} (last 24h)
            </span>
          </CardDescription>
        </CardHeader>
      </Card>

      <!-- Outside weather (optional; hidden when backend weather is disabled) -->
      <Card v-if="weather">
        <CardHeader>
          <CardTitle class="flex items-center gap-2">
            <CloudSun class="text-primary" />
            Outside weather
            <span
              v-if="weather.stale"
              class="rounded-full bg-amber-500/15 px-2 py-0.5 text-xs font-medium text-amber-600"
            >
              stale
            </span>
          </CardTitle>
          <CardDescription class="flex items-center gap-2">
            <img
              v-if="weather.icon"
              :src="weatherIconUrl(weather.icon)"
              :alt="weather.description || 'weather icon'"
              class="size-8"
            />
            <span>{{ weather.description || 'Current conditions' }}</span>
            <span v-if="weather.station_name" class="text-muted-foreground">
              · {{ weather.station_name }}
            </span>
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div class="grid grid-cols-2 gap-4 md:grid-cols-5">
            <div>
              <p class="text-sm text-muted-foreground">Temperature</p>
              <p class="text-2xl font-bold">
                {{ weather.temperature_c != null ? weather.temperature_c.toFixed(1) : '—' }} °C
              </p>
            </div>
            <div>
              <p class="text-sm text-muted-foreground">Humidity</p>
              <p class="text-2xl font-bold">
                {{ weather.relative_humidity_pct != null ? weather.relative_humidity_pct.toFixed(0) : '—' }} %
              </p>
            </div>
            <div>
              <p class="text-sm text-muted-foreground">AH outside</p>
              <p class="text-2xl font-bold">
                {{ weather.absolute_humidity_g_m3 != null ? weather.absolute_humidity_g_m3.toFixed(1) : '—' }} g/m³
              </p>
            </div>
            <div>
              <p class="text-sm text-muted-foreground">Wind</p>
              <p class="text-2xl font-bold">
                {{ weather.wind_speed_mps != null ? weather.wind_speed_mps.toFixed(1) : '—' }} m/s
              </p>
            </div>
            <div>
              <p class="text-sm text-muted-foreground">Cloud cover</p>
              <p class="text-2xl font-bold">
                {{ weather.cloud_pct != null ? weather.cloud_pct : '—' }} %
              </p>
            </div>
          </div>
          <p class="mt-3 text-xs text-muted-foreground">
            Observed {{ fmtTime(weather.observed_at) }} · fetched {{ fmtTime(weather.fetched_at) }}
          </p>
        </CardContent>
      </Card>

      <!-- Basement conditions (merged metric card) -->
      <Card v-if="telemetry">
        <CardHeader>
          <CardTitle class="flex items-center gap-2">
            <Home class="text-primary" />
            Basement conditions
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div class="grid grid-cols-2 gap-4 md:grid-cols-5">
            <div v-for="m in metrics" :key="m.value">
              <p class="text-sm text-muted-foreground">{{ m.label }}</p>
              <p class="text-2xl font-bold">
                {{ metricValue(m) }}
                <span class="text-base font-normal text-muted-foreground">{{ m.unit }}</span>
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      <!-- History chart -->
      <Card>
        <CardHeader class="flex flex-row items-center justify-between gap-4">
          <CardTitle class="flex items-center gap-2 text-sm font-medium text-muted-foreground">
            <Activity class="size-4" />
            History
          </CardTitle>
          <div class="flex items-center gap-1">
            <button
              v-for="r in ranges"
              :key="r.label"
              type="button"
              class="rounded-md border px-2.5 py-1 text-xs font-medium transition-colors"
              :class="
                range.label === r.label
                  ? 'border-primary bg-primary text-primary-foreground shadow-sm'
                  : 'border-transparent text-muted-foreground hover:bg-muted'
              "
              @click="range = r"
            >
              {{ r.label }}
            </button>
          </div>
        </CardHeader>
        <CardContent>
          <!-- Keep the chart mounted across range changes so it updates in place
               (no teardown/remount flash). Loading text only when no data yet. -->
          <HistoryChart v-if="history.length" :data="history" />
          <p v-else-if="historyLoading" class="text-muted-foreground text-sm">
            Loading history…
          </p>
          <p v-else class="text-muted-foreground text-sm">No history in this window yet.</p>
        </CardContent>
      </Card>

      <!-- Recent events -->
      <Card>
        <CardHeader>
          <CardTitle class="flex items-center gap-2 text-sm font-medium text-muted-foreground">
            <History class="size-4" />
            Recent events
          </CardTitle>
        </CardHeader>
        <CardContent>
          <ul v-if="actions.length" class="space-y-2">
            <li
              v-for="(a, i) in actions"
              :key="i"
              class="flex items-center justify-between border-b pb-2 text-sm last:border-0 last:pb-0"
            >
              <span>{{ a.message || a.code || a.type }}</span>
              <span class="text-muted-foreground text-xs">{{ fmtTime(a.timestamp) }}</span>
            </li>
          </ul>
          <p v-else class="text-muted-foreground text-sm">No events yet.</p>
        </CardContent>
      </Card>

      <p v-if="!telemetry && !error" class="text-muted-foreground text-sm">
        Waiting for telemetry…
      </p>
    </div>
  </div>
</template>
