<script setup>
import { onMounted, onUnmounted, ref, watch } from 'vue'
import uPlot from 'uplot'
import 'uplot/dist/uPlot.min.css'

const props = defineProps({
  // Array of telemetry rows from GET /api/telemetry/history (ISO timestamps)
  data: { type: Array, default: () => [] },
})

const el = ref(null)
const tip = ref(null)
let plot = null

const SERIES = [
  { key: 'temperature', label: 'Temp', color: '#ef4444', scale: 'temp', unit: '°C' },
  { key: 'humidity', label: 'RH', color: '#3b82f6', scale: 'rh', unit: '%' },
  { key: 'ah_inside', label: 'AH in', color: '#10b981', scale: 'ah', unit: 'g/m³' },
  { key: 'ah_outside', label: 'AH out', color: '#8b5cf6', scale: 'ah', unit: 'g/m³' },
]

function buildData(rows) {
  const times = rows.map((r) => new Date(r.timestamp).getTime() / 1000)
  const cols = SERIES.map((s) => rows.map((r) => r[s.key] ?? null))
  return [times, ...cols]
}

// Pick a time-axis format that fits the window: minutes for short ranges,
// dates for multi-day ranges (never both at once).
function timeValues(u, ticks) {
  const span = (u.data[0][u.data[0].length - 1] - u.data[0][0]) / 3600 // hours
  return ticks.map((t) => {
    const d = new Date(t * 1000)
    if (span <= 48) {
      const hh = String(d.getHours()).padStart(2, '0')
      const mm = String(d.getMinutes()).padStart(2, '0')
      return `${hh}:${mm}`
    }
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
  })
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
    legend: { show: false }, // custom tooltip instead
    scales: {
      temp: { auto: true },
      rh: { auto: true },
      ah: { auto: true },
    },
    axes: [
      {
        stroke: '#888',
        grid: { stroke: 'rgba(128,128,128,0.12)' },
        values: timeValues,
      },
      // Hide the right-side numeric axes — values show in the hover tooltip.
      { scale: 'temp', show: false },
      { scale: 'rh', show: false },
      { scale: 'ah', show: false },
    ],
    series: [
      {},
      ...SERIES.map((s) => ({
        label: s.label,
        stroke: s.color,
        scale: s.scale,
        width: 2,
        points: { show: false },
      })),
    ],
    cursor: { y: false },
    hooks: {
      setCursor: [(u) => {
        const idx = u.cursor.idx
        if (!tip.value) return
        if (idx == null) {
          tip.value.style.display = 'none'
          return
        }
        const t = u.data[0][idx]
        const d = new Date(t * 1000)
        const time = d.toLocaleString(undefined, {
          month: 'short',
          day: 'numeric',
          hour: '2-digit',
          minute: '2-digit',
        })
        const rows = SERIES.map((s, i) => {
          const v = u.data[i + 1][idx]
          return v == null
            ? null
            : `<div class="flex items-center justify-between gap-4">
                 <span class="flex items-center gap-1.5">
                   <span class="size-2 rounded-full" style="background:${s.color}"></span>
                   ${s.label}
                 </span>
                 <span class="font-semibold tabular-nums">${Number(v).toFixed(1)} ${s.unit}</span>
               </div>`
        })
          .filter(Boolean)
          .join('')
        tip.value.innerHTML =
          `<div class="mb-1 border-b pb-1 text-xs font-medium text-muted-foreground">${time}</div>` + rows
        tip.value.style.display = 'block'
        // Keep the tooltip inside the plot bounds.
        const px = u.cursor.left + 14
        const py = u.cursor.top + 14
        tip.value.style.left = Math.min(px, u.width - 170) + 'px'
        tip.value.style.top = Math.min(py, u.height - 120) + 'px'
      }],
    },
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
  <div class="relative w-full">
    <div ref="el" class="w-full"></div>
    <div
      ref="tip"
      class="pointer-events-none absolute z-10 hidden rounded-md border bg-background/95 px-2.5 py-1.5 text-xs shadow-md backdrop-blur"
    ></div>
  </div>
</template>
