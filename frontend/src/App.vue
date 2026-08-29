<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { RefreshCw, Fan, Thermometer, Droplets, Gauge, Wind } from '@lucide/vue'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'

const API = import.meta.env.VITE_API_URL || 'http://localhost:8001'

const telemetry = ref(null)
const error = ref('')
const loading = ref(false)

async function fetchLatest() {
  loading.value = true
  error.value = ''
  try {
    const res = await fetch(`${API}/api/telemetry/latest`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    telemetry.value = await res.json()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

let timer
onMounted(() => {
  fetchLatest()
  timer = setInterval(fetchLatest, 5000)
})
onUnmounted(() => clearInterval(timer))

function fmtTime(ts) {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleTimeString()
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

      <p v-if="!telemetry && !error" class="text-muted-foreground text-sm">
        Waiting for telemetry…
      </p>
    </div>
  </div>
</template>
