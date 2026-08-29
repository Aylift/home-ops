# Home Ops

IoT basement climate control: an ESP32 (MicroPython) sensor node streams telemetry to a FastAPI backend, with a Vue 3 frontend dashboard.

## Layout

- `iot/` — ESP32 device code + host-side tooling (WebREPL sync, reset, monitor)
- `backend/` — FastAPI API that ingests telemetry (`POST /api/telemetry`)
- `frontend/` — Vue 3 + Tailwind dashboard

## Setup

1. Copy `.env.template` → `.env` and fill in `ESP_IP`, `WEBREPL_PASS`.
2. Copy `iot/basement/config.template.py` → `iot/basement/config.py` and fill in WiFi, weather API, and `DASHBOARD_URL`.
3. Build the frontend once: `cd frontend && npm run build`
4. Backend (serves the built dashboard + API on one port): `cd backend && .\.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8001`

## LAN access

The backend serves both the dashboard and the API on port 8001, so any device on your network can open it at:

```
http://<your-pc-ip>:8001
```

Find your IP with `ipconfig` (the `192.168.x.x` IPv4 address). The backend already binds `0.0.0.0`, so no extra config is needed. For dev-only frontend work, run `cd frontend && npm run dev` and set `VITE_API_URL=http://<your-pc-ip>:8001`.

## ESP32 workflow

```bash
# Upload device code
python iot/sync.py push basement

# Reboot device (interrupts loop, reloads main.py)
python iot/soft_reset.py

# Watch live device output
python iot/check_esp.py

# Download current device files
python iot/sync.py pull basement
```

Note: on Windows use `set PYTHONIOENCODING=utf-8 &&` before the monitor/reset scripts if the console shows encoding errors.

## Notes for AI agents

See [`agents.md`](agents.md) for architecture, findings, and gotchas.
