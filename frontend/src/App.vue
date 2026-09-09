<script setup>
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { RefreshCw, Fan, Thermometer, Droplets, Gauge, Wind, History, Activity, CloudSun } from '@lucide/vue'
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
const status = ref(null)
const weather = ref(null)
const error = ref('')
const loading = ref(false)
const historyLoading = ref(false)
const nodeId = ref('basement')
const hours = ref(24)

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
      `${API}/api/telemetry/history?node_id=${nodeId.value}&hours=${hours.value}&limit=2000`
    )
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    history.value = await res.json()
  } catch (e) {
    console.warn('Failed to fetch history:', e)
  } finally {
    historyLoading.value = false
  }
}

let timer
onMounted(() => {
  fetchNodes()
  fetchLatest()
  fetchStatus()
  fetchWeather()
  fetchHistory()
  // Device POSTs telemetry every 5 min (LOOP_INTERVAL). Poll every 30s so the
  // dashboard picks up a new sample promptly without hammering the backend.
  timer = setInterval(() => {
    fetchLatest()
    fetchStatus()
    fetchWeather()
  }, 30000)
})
onUnmounted(() => clearInterval(timer))

// Reload live cards + history when the selected node changes.
watch(nodeId, () => {
  fetchLatest()
  fetchStatus()
  fetchHistory()
})

// Reload history when the time window changes.
watch(hours, fetchHistory)

function fmtTime(ts) {
  if (!ts) return '—'
  const d = new Date(ts)
  return isNaN(d.getTime()) ? '—' : d.toLocaleString()
}

const metrics = [
  { label: 'Temperature', value: 'temperature', unit: '°C', icon: Thermometer },
  { label: 'Humidity', value: 'humidity', unit: '%', icon: Droplets },
  { label: 'Pressure', value: 'pressure', unit: 'hPa', icon: Gauge },
  { label: 'AH inside', value: 'ah_inside', unit: 'g/m³', icon: Wind },
  { label: 'AH outside', value: 'ah_outside', unit: 'g/m³', icon: Wind },
]
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
          <select
            v-model.number="hours"
            class="h-9 rounded-md border border-input bg-background px-3 text-sm"
            aria-label="History window"
          >
            <option :value="6">6h</option>
            <option :value="24">24h</option>
            <option :value="72">3d</option>
            <option :value="168">7d</option>
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
          </CardTitle>
          <CardDescription>{{ telemetry.mode }}</CardDescription>
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
          <CardDescription>
            {{ weather.description || 'Current conditions' }}
            <span v-if="weather.icon" class="ml-1">({{ weather.icon }})</span>
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div class="grid grid-cols-2 gap-4 md:grid-cols-4">
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
              <p class="text-sm text-muted-foreground">Wind / Clouds</p>
              <p class="text-2xl font-bold">
                {{ weather.wind_speed_mps != null ? weather.wind_speed_mps.toFixed(1) : '—' }} m/s
                <span class="text-base font-normal text-muted-foreground">
                  · {{ weather.cloud_pct != null ? weather.cloud_pct : '—' }}%
                </span>
              </p>
            </div>
          </div>
          <p class="mt-3 text-xs text-muted-foreground">
            Observed {{ fmtTime(weather.observed_at) }} · fetched {{ fmtTime(weather.fetched_at) }}
          </p>
        </CardContent>
      </Card>

      <!-- Metric cards -->
      <div class="grid grid-cols-2 gap-4 md:grid-cols-3">
        <Card v-for="m in metrics" :key="m.value">
          <CardHeader>
            <CardTitle class="flex items-center gap-2 text-sm font-medium text-muted-foreground">
              <component :is="m.icon" class="size-4" />
              {{ m.label }}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p class="text-3xl font-bold">
              {{ telemetry && telemetry[m.value] != null ? telemetry[m.value].toFixed(1) : '—' }}
              <span class="text-base font-normal text-muted-foreground">{{ m.unit }}</span>
            </p>
          </CardContent>
        </Card>
      </div>

      <!-- History chart -->
      <Card>
        <CardHeader>
          <CardTitle class="flex items-center gap-2 text-sm font-medium text-muted-foreground">
            <Activity class="size-4" />
            History (last {{ hours }}h)
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p v-if="historyLoading" class="text-muted-foreground text-sm">Loading history…</p>
          <HistoryChart v-else-if="history.length" :data="history" />
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
