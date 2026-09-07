<script setup>
import { onMounted, onUnmounted, ref, watch } from 'vue'
import uPlot from 'uplot'
import 'uplot/dist/uPlot.min.css'

const props = defineProps({
  // Array of telemetry rows from GET /api/telemetry/history (ISO timestamps)
  data: { type: Array, default: () => [] },
})

const el = ref(null)
let plot = null

const SERIES = [
  { key: 'temperature', label: 'Temp °C', color: '#ef4444', scale: 'temp' },
  { key: 'humidity', label: 'RH %', color: '#3b82f6', scale: 'rh' },
  { key: 'ah_inside', label: 'AH in g/m³', color: '#10b981', scale: 'ah' },
  { key: 'ah_outside', label: 'AH out g/m³', color: '#8b5cf6', scale: 'ah' },
]

function buildData(rows) {
  const times = rows.map((r) => new Date(r.timestamp).getTime() / 1000)
  const cols = SERIES.map((s) => rows.map((r) => r[s.key] ?? null))
  return [times, ...cols]
}

function render() {
  if (!el.value) return
  const rows = props.data || []
  const data = buildData(rows)

  if (plot) {
    plot.setData(data)
    return
  }

  const opts = {
    width: el.value.clientWidth,
    height: 320,
    legend: { show: true, live: true },
    scales: {
      temp: { auto: true },
      rh: { auto: true },
      ah: { auto: true },
    },
    axes: [
      {
        stroke: '#888',
        grid: { stroke: 'rgba(128,128,128,0.15)' },
        values: (u, ticks) => ticks.map((t) => new Date(t * 1000).toLocaleTimeString()),
      },
      { scale: 'temp', stroke: '#ef4444', grid: { show: false }, size: 50 },
      { scale: 'rh', stroke: '#3b82f6', grid: { show: false }, size: 50 },
      { scale: 'ah', stroke: '#10b981', grid: { show: false }, size: 50 },
    ],
    series: [
      {},
      ...SERIES.map((s) => ({
        label: s.label,
        stroke: s.color,
        scale: s.scale,
        width: 1.5,
        points: { show: false },
      })),
    ],
  }

  plot = new uPlot(opts, data, el.value)
}

function resize() {
  if (plot && el.value) plot.setSize({ width: el.value.clientWidth, height: 320 })
}

onMounted(() => {
  render()
  window.addEventListener('resize', resize)
})

onUnmounted(() => {
  window.removeEventListener('resize', resize)
  if (plot) {
    plot.destroy()
    plot = null
  }
})

watch(
  () => props.data,
  () => render(),
  { deep: false }
)
</script>

<template>
  <div ref="el" class="w-full"></div>
</template>
