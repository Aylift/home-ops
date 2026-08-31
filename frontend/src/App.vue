<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { RefreshCw, Fan, Thermometer, Droplets, Gauge, Wind, History } from '@lucide/vue'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'

// Same-origin when served by the backend; override with VITE_API_URL for dev.
const API = import.meta.env.VITE_API_URL || ''

const telemetry = ref(null)
const actions = ref([])
const error = ref('')
const loading = ref(false)

async function fetchLatest() {
  loading.value = true
  error.value = ''
  try {
    const [tRes, aRes] = await Promise.all([
      fetch(`${API}/api/telemetry/latest`),
      fetch(`${API}/api/actions?limit=5`),
    ])
    if (!tRes.ok) throw new Error(`HTTP ${tRes.status}`)
    telemetry.value = await tRes.json()
    if (aRes.ok) actions.value = await aRes.json()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

let timer
onMounted(() => {
  fetchLatest()
  // Device POSTs telemetry every 5 min (LOOP_INTERVAL); polling faster is wasteful.
  timer = setInterval(fetchLatest, 300000)
})
onUnmounted(() => clearInterval(timer))

function fmtTime(ts) {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleString()
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
    <div class="mx-auto max-w-4xl space-y-6">
      <header class="flex items-center justify-between">
        <div>
          <h1 class="text-2xl font-bold">Basement Climate</h1>
          <p class="text-muted-foreground text-sm">
            Last update: {{ fmtTime(telemetry?.timestamp) }}
          </p>
        </div>
        <Button :disabled="loading" @click="fetchLatest">
          <RefreshCw :class="loading ? 'animate-spin' : ''" />
          Refresh
        </Button>
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
              {{ telemetry ? telemetry[m.value]?.toFixed(1) : '—' }}
              <span class="text-base font-normal text-muted-foreground">{{ m.unit }}</span>
            </p>
          </CardContent>
        </Card>
      </div>

      <!-- Recent actions -->
      <Card>
        <CardHeader>
          <CardTitle class="flex items-center gap-2 text-sm font-medium text-muted-foreground">
            <History class="size-4" />
            Recent actions
          </CardTitle>
        </CardHeader>
        <CardContent>
          <ul v-if="actions.length" class="space-y-2">
            <li
              v-for="(a, i) in actions"
              :key="i"
              class="flex items-center justify-between border-b pb-2 text-sm last:border-0 last:pb-0"
            >
              <span>{{ a.action }}</span>
              <span class="text-muted-foreground text-xs">{{ fmtTime(a.timestamp) }}</span>
            </li>
          </ul>
          <p v-else class="text-muted-foreground text-sm">No major events yet.</p>
        </CardContent>
      </Card>

      <p v-if="!telemetry && !error" class="text-muted-foreground text-sm">
        Waiting for telemetry…
      </p>
    </div>
  </div>
</template>
