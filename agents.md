# agents.md — AI Entrypoint

Read this first before modifying the project. It captures architecture, resolved issues, and gotchas that are easy to trip over.

## Architecture

```
ESP32 (MicroPython) ──POST /api/telemetry──▶ FastAPI backend (:8001) ──▶ Vue 3 frontend
   iot/basement/main.py                          backend/app/main.py        frontend/src
```

- **Device** ([`iot/basement/main.py`](iot/basement/main.py)): reads BME280 (temp/RH/pressure), fetches outside humidity from OpenWeatherMap, decides fan state via [`should_ventilate()`](iot/basement/main.py:68). Control loop runs every 5s, but only POSTs telemetry on a **60s heartbeat** or an **immediate event** (fan state change / emergency) to save power.
- **Backend** ([`backend/app/main.py`](backend/app/main.py)): validates payload with Pydantic `Telemetry`, stores the latest sample in memory, logs it, returns 200. Exposes `GET /api/telemetry/latest` and `GET /api/actions` (rolling log of major events) for the dashboard. Also serves the built frontend (`frontend/dist`) on the same port, so the whole app is reachable at `http://<host-ip>:8001` from any LAN device.
- **Frontend** ([`frontend/src/App.vue`](frontend/src/App.vue)): shadcn dashboard that polls `GET /api/telemetry/latest` + `GET /api/actions` every 5s and renders fan status, metric cards, and a recent-actions list. Uses a **relative** API URL (same origin) so it works when served by the backend; override with `VITE_API_URL` for dev.
- **Host tooling** ([`iot/`](iot/)): WebREPL-based scripts to sync/reset/monitor the device.

## Telemetry payload

```json
{
  "timestamp": 841357778.0,
  "temperature": 21.28,
  "humidity": 63.29,
  "pressure": 1002.4,
  "ah_inside": 11.78,
  "ah_outside": 10.99,
  "fan_active": true,
  "mode": "API (Outside dry)"
}
```

`mode` is one of: `EMERGENCY (Flood)`, `STANDBY (Normal)`, `API (Outside dry)`, `API (Outside wet)`, `GUARD (No NTP time)`, `GUARD (Winter)`, `GUARD (Summer)`.

## Resolved issues (do not regress)

### 1. JSON truncation on ESP32 (urequests Content-Length bug)
`urequests.post()` computes `Content-Length` from `len(str)` (Unicode char count), not byte length. Multibyte UTF-8 chars undercount the length and truncate the body → backend 422.
**Fix:** in [`send_to_dashboard()`](iot/basement/main.py:95), encode the body to UTF-8 bytes first:
```python
body = ujson.dumps(payload).encode("utf-8")
response = urequests.post(DASHBOARD_URL, data=body, headers={"Content-Type": "application/json"})
```
Also always call `response.close()` — urequests leaks RAM otherwise.

### 2. Polish characters crash the backend console
The device previously sent Polish mode strings (e.g. `ą` in `Z zewnątrz`). The Windows console uses cp1252, so `print()` in the FastAPI handler raised `UnicodeEncodeError` → 500.
**Fix (two layers):**
- Device now sends **English-only ASCII** strings (per project convention — no Polish in device code).
- Backend has a defensive [`sys.stdout.reconfigure(encoding="utf-8", errors="replace")`](backend/app/main.py:6) at the top of `main.py` so a stray non-ASCII char can never crash a request handler.

### 3. sync.py couldn't find webrepl_cli.py
`sync.py` used a bare `CLI = "webrepl_cli.py"` path, which failed when run from the repo root. **Fix:** resolve the CLI path relative to the script location:
```python
CLI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webrepl_cli.py")
```

### 4. soft_reset.py didn't actually reload main.py
Sending `machine.reset()` while the device is mid-loop in `main.py` does nothing — the WebREPL text isn't processed as a REPL command. **Fix:** send Ctrl-C (`\x03`) first to interrupt the loop, wait, then `machine.reset()`:
```python
ws.write(b"\x03", wc.WEBREPL_FRAME_TXT)
time.sleep(1)
ws.write(b"import machine\r\nmachine.reset()\r\n", wc.WEBREPL_FRAME_TXT)
```

### 5. main.py didn't auto-start after reboot
`boot.py` only connected WiFi and started WebREPL — it never launched `main.py`, so after a reset the device sat idle at the REPL prompt. **Fix:** add `import main` at the end of [`boot.py`](iot/basement/boot.py) so the climate loop starts on every boot.

### 6. Power: heartbeat + event override instead of 5s POSTs
The device originally POSTed telemetry every 5s (~17k requests/day). WiFi TX is the dominant power draw. **Fix:** the control loop still runs every 5s (fan stays responsive), but telemetry only POSTs on a **60s heartbeat** or an **immediate event** (fan state change / emergency) — cutting radio activity ~12x to ~1.5k requests/day. Events carry an `action` field (e.g. `"Fan turned ON"`) that the backend logs.

## Gotchas

- **Windows console encoding:** prefix monitor/reset scripts with `set PYTHONIOENCODING=utf-8 &&` to avoid cp1252 errors.
- **Shell is cmd.exe**, not PowerShell. Use `del` (not `Remove-Item`), `timeout /t N` (not `Start-Sleep`), `&&` for chaining.
- **Device runs from memory:** after pushing new `main.py`, you MUST soft-reset (with Ctrl-C) for the change to take effect. The file on disk and the running process can differ.
- **`config.py` and `.env` are gitignored** (secrets). Templates: [`iot/basement/config.template.py`](iot/basement/config.template.py) and [`.env.template`](.env.template).
- **No Polish anywhere in device code** — comments, logging, and mode strings are all English. Frontend handles display localization.

## Host tooling reference

| Script | Purpose |
|--------|---------|
| [`iot/sync.py`](iot/sync.py) | `push`/`pull` device files over WebREPL |
| [`iot/soft_reset.py`](iot/soft_reset.py) | Interrupt loop + reboot device |
| [`iot/check_esp.py`](iot/check_esp.py) | Stream live device output |
| [`iot/webrepl_cli.py`](iot/webrepl_cli.py) | WebREPL protocol library (dependency) |
