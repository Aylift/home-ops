# Ideas backlog

Candidate work for home-ops, grouped by effort. Nothing here is committed to —
pick one and it graduates into its own plan file.

Context: ESP32 (MicroPython) basement climate node → FastAPI (:8001, Postgres) →
Vue 3 dashboard. The backend is the single weather authority (OWM cache) and
already multi-room (`node_id` + node registry). Fan control logic lives on the
device; the backend stores telemetry/events and serves the dashboard.

---

## Quick wins (frontend, <1h each)

### 1. Fan runtime stats card
Total minutes the fan ran today / last 7 days, derived from `fan_active`
transitions in `GET /api/telemetry/history`. Answers "is the fan actually doing
work?" No backend change — compute client-side from the existing history payload.

### 2. Ventilation efficiency score
For each fan-on period, compare inside AH at start vs end. Show "avg ΔAH per hour
of runtime". Tells you whether ventilating is actually drying the basement or
just moving air. Pure frontend over the history series.

### 3. "Why is the fan on?" tooltip
Parse `mode` into a plain-English sentence plus the numbers that drove it (inside
AH vs outside AH vs threshold). The mode strings already carry the reason; this
is presentation only.

### 4. CSV export
One button exporting the current history window. `Blob` + `URL.createObjectURL`,
no backend change.

### 5. Stale-data banner
If `received_at` is older than ~12 min, show a yellow "device offline" strip
instead of silently rendering old numbers. Cheap and prevents misreading a dead
device as a healthy one.

### 6. Sparklines in the metric cards
Tiny inline SVG trend from the last 24h next to each big number. No chart library
needed.

---

## Medium (backend + frontend, an evening)

### 7. Alerting / notifications
A `rules` table (e.g. "RH > 80 for 30 min", "device offline > 15 min") evaluated
on ingest, delivered via ntfy.sh / Telegram / email. Biggest quality-of-life
jump: today nothing tells you the basement is flooding unless you look.

### 8. Daily digest
Scheduled job writing one summary row per day (min/max/avg temp, RH, fan minutes,
event count) and a "last 30 days" table. Makes long-range history readable.

### 9. Retention + downsampling
Keep raw data 30d, roll older into hourly aggregates. Prevents unbounded table
growth. Listed as deferred in the original DB plan.

### 10. Second node
A cheap ESP32 + DHT22 in another room (garage/attic). The backend is already
multi-room (`node_id`, node registry, node selector in the UI). Mostly hardware +
config; proves the architecture.

### 11. Fan override scheduling
Beyond "N minutes from now": "run 20 min at 07:00 daily" via a small `schedules`
table. Useful for a recurring workout slot.

---

## Bigger / fun

### 12. Weather-forecast-aware ventilation
OWM's free 5-day/3h forecast endpoint. Instead of reacting to current outside AH,
look ahead: "outside will be drier in 2h, hold off" or "rain coming in 3h,
ventilate now while it's dry". A genuine control-logic upgrade.

### 13. Adaptive thresholds
Learn the basement's drying curve (AH drop per hour of runtime at a given ΔAH)
and pick the threshold that minimizes runtime for a target RH. Start with a
simple regression over stored history.

### 14. Grafana / Prometheus
Expose `/metrics` and point Grafana at it. The data already exists; this gives
dashboards, alerting, and long-term storage for free.

### 15. Local-only weather fallback
An outside BME280 (or a second node) so the system works with zero internet.
Removes the OWM dependency entirely.

### 16. Mobile PWA
Manifest + service worker so the dashboard installs to a phone home screen. Small
change, big usability win for "check the basement".

### 17. Voice / Home Assistant integration
Expose override + status via MQTT so HA/Alexa/Google can control the fan. MQTT is
a natural fit for the ESP32.

---

## Suggested picks

- **#7 (alerting)** — highest impact; turns a dashboard you must remember to check
  into a system that tells you when something is wrong.
- **#12 (forecast-aware)** — most interesting engineering.
- **#1 + #3** — something visible in the next 30 minutes.
