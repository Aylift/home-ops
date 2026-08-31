# agents.md — AI Entrypoint

Read this first before modifying the project. It captures architecture, resolved issues, and gotchas that are easy to trip over.

## Architecture

```
ESP32 (MicroPython) ──POST /api/telemetry──▶ FastAPI backend (:8001) ──▶ Vue 3 frontend
   iot/basement/main.py                          backend/app/main.py        frontend/src
```

- **Device** ([`iot/basement/main.py`](iot/basement/main.py)): reads BME280 (temp/RH/pressure), fetches outside humidity from OpenWeatherMap, decides fan state via [`should_ventilate()`](iot/basement/main.py:68). Control loop sleeps **5 min** (`LOOP_INTERVAL`) between cycles; telemetry POSTs on a **5-min heartbeat** or an **immediate event** (fan state change / emergency entry) to save power.
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

### 7. Static IP on the ESP32
The device uses a **static IP `192.168.1.49`** (outside the router's DHCP range) so it never changes. Configured in [`boot.py`](iot/basement/boot.py) via `station.ifconfig()` and values from [`config.py`](iot/basement/config.py) (`STATIC_IP`/`NETMASK`/`GATEWAY`/`DNS`). **Gotcha:** the interface must be torn down first (`active(False)` → `active(True)` → `disconnect()`) before applying the static tuple, or a stale DHCP lease wins. Also, `machine.reset()` via WebREPL only works if the socket stays open a few seconds after the command so it fully processes — closing immediately can drop the reset. Host tooling defaults (`.env` `ESP_IP`, `check_esp.py`, `soft_reset.py`) now point at `192.168.1.49`.

### 8. main.py still didn't auto-start after reboot (watchdog)
Even with `import main` in `boot.py`, a transient failure during `main.py` module-level init (NTP sync, BME280 init, relay pin) could raise and leave the device idle at the REPL prompt. **Fix (two layers):**
- [`boot.py`](iot/basement/boot.py) wraps `import main` in a `while True` retry loop — on any exception it logs and retries every 5s instead of giving up.
- [`main.py`](iot/basement/main.py) wraps hardware init (I2C/BME280/relay) in `try/except` and re-raises so the boot.py watchdog can retry, rather than aborting the whole boot.
- [`soft_reset.py`](iot/soft_reset.py) keeps the WebREPL socket open 3s after `machine.reset()` so the reset fully processes before the socket closes.

### 9. Fan never turned on (decision not applied to relay)
`should_ventilate()` returned a `vent_decision` but the main loop never applied it — the relay was only ever set to 0 at init, so the fan could never turn on regardless of the logic. **Fix:** in [`main.py`](iot/basement/main.py:137), apply the decision to the relay right after computing it: `relay.value(1 if vent_decision else 0)`.

### 10. Dashboard showed "Last update: 1996" (MicroPython epoch)
MicroPython's `time.time()` counts seconds since the **MicroPython epoch (2000-01-01)**, not the Unix epoch (1970-01-01). The frontend treated the raw value as Unix time, so it rendered ~1996. **Fix:** the backend adds `MICROPY_EPOCH_OFFSET = 946684800` in [`receive_telemetry()`](backend/app/main.py:61) so stored timestamps are real Unix seconds the frontend renders directly.

### 11. Fan rapidly toggled on/off + wrong weather location
Two issues surfaced together. (a) When inside/outside AH are nearly equal (e.g. 11.77 vs 11.78), the comparison flipped on sensor/API noise, cycling the fan every few seconds. (b) Weather came from `CITY="Wroclaw,PL"` (~30km away), giving inaccurate outside AH. **Fixes in [`main.py`](iot/basement/main.py:85):**
- **Hysteresis:** `AH_HYSTERESIS = 0.5` (g/m³) dead-band — the fan only flips when the AH difference exceeds ±0.5, otherwise it holds the current state.
- **Anti-cycling:** `MIN_RUN_TIME = 300` / `MIN_OFF_TIME = 300` — the fan must dwell at least 5 min in a state before it can flip again.
- **Coordinates:** switched weather query from city name to `lat`/`lon` (values live in the gitignored [`config.py`](iot/basement/config.py); template leaves them blank) for precise local outside AH.

### 12. False "Flood" emergency during heavy rain (AH-aware emergency)
`EMERGENCY_RH = 75.0` forced the fan ON unconditionally at high RH, even when outside AH was higher than inside (e.g. inside 13 vs outside 14.2 during rain) — ventilating pulled MORE moisture in and the fan cycled at the 75% boundary. **Fix in [`should_ventilate()`](iot/basement/main.py:85):** the emergency branch is now AH-aware. It computes `int_ah` first, then only forces the fan ON when outside is actually drier (`diff > AH_HYSTERESIS` → `"EMERGENCY (Outside dry)"`); if outside is wetter it forces OFF (`"EMERGENCY (Outside wet)"`), and if outside AH is unknown it forces OFF (`"EMERGENCY (Outside unknown)"`). The emergency branch decides immediately (no `MIN_OFF_TIME`) so a genuine flood is never delayed by anti-cycling.

### 13. Backend 404 after PC IP change (DASHBOARD_URL hardcoded)
The ESP32's `DASHBOARD_URL` in the gitignored [`config.py`](iot/basement/config.py) was hardcoded to the PC's old DHCP IP, so when the PC's IP changed the device POSTed to a dead address and `/api/telemetry/latest` returned 404 (no telemetry received). **Fix:** update `DASHBOARD_URL` to the PC's current IP and push+soft-reset. **Root cause:** the PC has no static IP — it should be given one (outside the DHCP range) so this stops recurring.

### 14. Sleep-5m cadence + no emergency spam (single timing knob)
The device ran a 5s loop with a 60s heartbeat and 300s API refresh — ~17k wake cycles/day and it spammed "EMERGENCY: high humidity" every loop while RH stayed ≥ 75%. **Fix in [`main.py`](iot/basement/main.py:23):** one authoritative `LOOP_INTERVAL = 300` cadence constant, with every timing interval derived from it (no scattered magic numbers):
```python
LOOP_INTERVAL = 300
MIN_RUN_TIME = LOOP_INTERVAL       # fan dwells ≥1 cycle before flipping
MIN_OFF_TIME = LOOP_INTERVAL
API_INTERVAL = 3 * LOOP_INTERVAL   # outside weather refreshed every 15 min
HEARTBEAT_INTERVAL = LOOP_INTERVAL # telemetry every cycle (5 min)
```
The main loop now `time.sleep(LOOP_INTERVAL)` between cycles (~288 wake-ups/day, ~12x less radio/CPU). The emergency **action** is logged only on the transition INTO emergency (`emergency_entered = emergency and not prev_emergency`), not every cycle; the emergency **control decision** still runs every wake-up. Trade-off: the fan reacts at most once per 5-min cycle and outside AH can be up to 15 min stale — intentional for the power savings.

### 15. Dashboard polling "dead" after rebuild (stale cached index.html)
After the device cadence change, the dashboard showed **zero requests in dev tools** even after 30 min — data only updated on manual refresh. The polling code was correct; the real cause was the browser caching the **old `index.html`**, which kept loading the old hashed Vite bundle (the old polling code). **Fixes:**
- **Backend** ([`main.py`](backend/app/main.py:98)): the SPA fallback now returns `index.html` with `Cache-Control: no-cache, no-store, must-revalidate` so a rebuild is picked up on the next normal reload (index.html is the pointer to the current hashed bundle; the hashed JS/CSS assets themselves stay long-cacheable).
- **Frontend** ([`App.vue`](frontend/src/App.vue:21)): `fetchLatest()` no longer uses `Promise.all` — a failure fetching `/api/actions` can no longer block the telemetry update. Polling interval is 30s (data changes every 5 min, so 30s is responsive without hammering the backend).
- **Deploy note:** after `npm run build`, restart uvicorn (backend reads `frontend/dist` from disk per-request, but a Python change needs a restart), then hard-refresh (Ctrl+F5) once to clear the stale cached `index.html`.
- **Gotcha:** restarting the backend clears the in-memory `latest_telemetry`, so `/api/telemetry/latest` returns 404 until the device's next 5-min heartbeat POST repopulates it.

## Gotchas

- **Windows console encoding:** prefix monitor/reset scripts with `set PYTHONIOENCODING=utf-8 &&` to avoid cp1252 errors.
- **Shell is cmd.exe**, not PowerShell. Use `del` (not `Remove-Item`), `timeout /t N` (not `Start-Sleep`), `&&` for chaining.
- **Device runs from memory:** after pushing new `main.py`, you MUST soft-reset (with Ctrl-C) for the change to take effect. The file on disk and the running process can differ.
- **`config.py` and `.env` are gitignored** (secrets). Templates: [`iot/basement/config.template.py`](iot/basement/config.template.py) and [`.env.template`](.env.template).
- **No Polish anywhere in device code** — comments, logging, and mode strings are all English. Frontend handles display localization.
- **Device static IP is `192.168.1.49`** — update `.env` `ESP_IP` and the host scripts if it ever changes.

## Host tooling reference

| Script | Purpose |
|--------|---------|
| [`iot/sync.py`](iot/sync.py) | `push`/`pull` device files over WebREPL |
| [`iot/soft_reset.py`](iot/soft_reset.py) | Interrupt loop + reboot device |
| [`iot/check_esp.py`](iot/check_esp.py) | Stream live device output |
| [`iot/webrepl_cli.py`](iot/webrepl_cli.py) | WebREPL protocol library (dependency) |
