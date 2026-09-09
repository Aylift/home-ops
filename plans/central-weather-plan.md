# Plan: Centralize Weather Fetch in the Backend (serve ESP32 + frontend) — v2

> Incorporates external review. The architecture direction is unchanged (backend = single weather authority); this revision hardens the **WeatherService** into a proper async/cache abstraction with explicit cache, concurrency, failure, and configuration semantics.

## Why

Today each ESP32 independently calls OpenWeatherMap every 15 min and discards everything except temp/RH. The dashboard only sees derived `ah_outside` + a coarse dry/wet `mode`, so the user can't judge conditions (overcast but not raining, wind, clouds). Problems: N devices = N redundant OWM calls, the OWM secret is flashed onto every device, and rich weather never reaches the frontend.

## Target architecture

```
                   OpenWeatherMap
                         │
                         │ async HTTP (httpx.AsyncClient)
                         ▼
                ┌──────────────────┐
                │  WeatherService  │   cache · refresh lock · normalization
                │                  │   AH calc · stale policy · logging
                └────────┬─────────┘
                         │ canonical model
              ┌──────────┴──────────┐
              ▼                     ▼
       GET /api/weather      GET /api/weather/current
              │                     │
              ▼                     ▼
          Vue dashboard          ESP32 devices
```

- **Backend is the single weather authority.** One process fetches OWM, caches, normalizes, computes AH, and serves both the dashboard and every ESP32.
- **ESP32 stops calling OWM** and polls the backend over LAN. No API key on device.
- **Frontend reads weather from the backend**, independent of telemetry cadence.
- **No DB migration.** Weather is cache/state, not application history. (Revisit only if we later need to correlate historical ventilation decisions with historical weather.)

## Current architecture (what exists today)

```
ESP32 basement ──POST /api/telemetry──▶ FastAPI (:8001) ──▶ PostgreSQL
   iot/basement/climate.py                backend/app            db
        │  ▲
        │  │ GET api.openweathermap.org (every 15 min, per device)
        ▼  │
   OpenWeatherMap (API_KEY + LAT/LON in device config.py)

Vue dashboard (served by backend) ◀──GET /api/telemetry/latest, /history, /events
```

### Device side today — [`iot/basement/climate.py`](iot/basement/climate.py)
- `WEATHER_URL` built from `config.API_KEY`/`LAT`/`LON` (lines 9-15).
- `fetch_external_ah()` (line 46) GETs OWM, extracts temp/RH, computes AH, returns only the AH float.
- `run()` (line 139) refreshes every `API_INTERVAL = 3 * LOOP_INTERVAL` = 15 min (line 173).
- Telemetry payload (lines 217-228) carries `ah_outside` + `mode` only.

### Backend today — [`backend/app/`](backend/app)
- Routers `telemetry.py`/`events.py`/`nodes.py` mounted in [`main.py`](backend/app/main.py:38).
- `config.py` (pydantic-settings, reads `.env`); Postgres via SQLAlchemy + Alembic; frontend baked into the Docker image.

## Design decisions (revised)

| Decision | Take |
|----------|------|
| Fetch | Backend, **async** `httpx.AsyncClient`, one app-level client reused (created on startup, closed on shutdown) |
| Cache | In-memory per process; documented single-worker assumption |
| Refresh | **Serialized** via an `asyncio.Lock` (double-checked after acquiring) to prevent cache stampede |
| Freshness TTL | 10 min |
| Max stale age | Configurable (default e.g. 6 h); beyond it → 503, never serve ancient weather |
| Failure semantics | Owned by the **service**, not the router (router stays dumb) |
| Stale signaling | Response carries `fetched_at` + `stale` so consumers know |
| Storage | In-memory only; no Postgres table |
| ESP32 transport | Poll `GET /api/weather/current` over LAN |
| API key | Backend `.env` only; removed from device `config.py` |
| AH | Computed in backend; device reads it, no Magnus formula on device |
| Workers | Document: run 1 worker unless cache moves to shared storage (Redis deferred — overkill for 5 devices) |

## WeatherService design

### Cache state — separate the concepts
```python
class WeatherCache:
    data: dict | None          # normalized weather
    fetched_at: float | None   # when backend last fetched successfully
    last_attempt: float | None # last refresh attempt (success or fail)
    last_error: str | None     # last error message
```
Two durations: **freshness TTL** (10 min) and **max stale age** (configurable). Policy:
- `0–TTL` → fresh → serve.
- `TTL–max_stale` → stale → try refresh; success → new data; failure → serve stale (marked `stale: true`).
- `> max_stale` → 503 (don't pretend old weather is current).

### Refresh — serialized, no stampede
```python
async def get_weather(self) -> WeatherOut:
    if self._fresh():
        return self._serve()
    async with self._lock:
        if self._fresh():          # double-check after acquiring lock
            return self._serve()
        return await self._refresh()
```

### Canonical (self-describing) model
```python
class WeatherOut(BaseModel):
    temperature_c: float
    relative_humidity_pct: float
    absolute_humidity_g_m3: float   # AH computed here
    description: str
    icon: str
    wind_speed_mps: float | None
    cloud_pct: int | None
    fetched_at: datetime            # when backend fetched
    observed_at: datetime | None    # OWM observation time, if available
    stale: bool
```
Self-describing field names embed units/meaning; the device adapter maps `absolute_humidity_g_m3` → `ah_outside`.

### Async client + params (no manual URL, no key in logs)
```python
async with httpx.AsyncClient(timeout=10) as client:  # or app-level reused client
    resp = await client.get(
        OWM_URL,
        params={"lat": ..., "lon": ..., "appid": ..., "units": "metric"},
    )
```
Never log the URL (contains the key). Log `[WEATHER] refreshed` / `[WEATHER] refresh failed: timeout; serving stale`.

### Router stays dumb
```python
@router.get("/weather", response_model=WeatherOut)
async def weather():
    return await weather_service.get_weather()
```
The service owns cache/refresh/timeout/retry/stale/OWM errors.

## API contract

Two representations of **one** underlying resource (not two independent APIs):
- `GET /api/weather` → full canonical model (frontend).
- `GET /api/weather/current` → minimal projection for ESP32 (only if the smaller payload genuinely matters for MicroPython memory/parsing; otherwise drop it and let the device ignore extra fields).

ESP32 contract (kept stable — contract-tested):
```json
{ "temperature_c": 17.4, "relative_humidity_pct": 82,
  "absolute_humidity_g_m3": 11.9, "fetched_at": "...", "stale": false }
```

## Configuration — validated, no silent sentinels

```python
# backend/app/config.py
owm_api_key: str = ""      # required; fail fast if empty when weather enabled
owm_lat: float | None = None   # required; None, not 0.0, as "missing"
owm_lon: float | None = None
weather_cache_ttl: int = 600
weather_max_stale: int = 21600   # 6 h
```
Validate at startup: if weather is enabled but key/coords are missing → raise (not a silent 0,0). If weather is an optional feature, gate behind `weather_enabled: bool` instead of sentinel values.

## New/changed files

### Backend
1. `backend/app/config.py` — OWM settings + validation.
2. `backend/app/services/__init__.py`, `backend/app/services/weather.py` — `WeatherService` (cache, lock, async client, normalization, AH, stale policy, logging).
3. `backend/app/routers/weather.py` — `/api/weather` (+ `/api/weather/current` if kept).
4. `backend/app/schemas.py` — `WeatherOut` (+ `WeatherCurrentOut`).
5. `backend/app/main.py` — create/reuse `AsyncClient` in lifespan, mount router, instantiate service.
6. `backend/requirements.txt` — add `httpx`.
7. `.env.template` — OWM placeholders.

### Device — [`iot/basement/climate.py`](iot/basement/climate.py)
- **Explicit backend URL config** (no `.replace()` derivation):
  ```python
  # config.py
  BACKEND_URL = "http://192.168.1.67:8001"
  WEATHER_URL = BACKEND_URL + "/api/weather/current"
  TELEMETRY_URL = BACKEND_URL + "/api/telemetry"
  ```
- Replace OWM call with a defensive poll of the backend:
  ```python
  def fetch_external_weather():
      try:
          response = urequests.get(WEATHER_URL, timeout=10)
          try:
              if response.status_code != 200:
                  return None
              data = response.json()
          finally:
              response.close()          # no leak if json() throws
          if "absolute_humidity_g_m3" not in data:
              return None
          return data
      except Exception as e:
          print(f"[API ERROR] {e}")
          return None
  ```
- `run()` maps `weather["absolute_humidity_g_m3"]` → `ext_ah`; `should_ventilate()` unchanged.
- Remove `API_KEY`/`LAT`/`LON` from device `config.py` + `config.template.py`.
- Preserve GUARD fallback when weather unavailable. (Future: distinguish fresh/stale/unavailable without changing the API shape.)

### Frontend — [`frontend/src/App.vue`](frontend/src/App.vue)
- Current Weather card from `GET /api/weather`: description + icon, outside temp/RH, wind, clouds, outside AH, and `fetched_at`/`stale` ("Updated X min ago" / "stale").
- Reuse the existing 30s poll (one extra tiny GET is fine; **frontend poll frequency ≠ weather refresh frequency** — state this explicitly).

## Edge cases

| Concern | Handling |
|---------|----------|
| Cache stampede | `asyncio.Lock` + double-checked refresh → one OWM call per TTL |
| OWM down | Serve stale (≤ max_stale) marked `stale: true`; else 503 |
| Backend restart (empty cache) | First request refreshes; until then device gets 503 → GUARD |
| Multiple workers | Documented single-process assumption; service structured so a shared cache (Redis) can slot in later without API rewrite |
| Device can't reach backend | Weather poll fails → GUARD (unchanged) |
| Malformed / 401 / 403 OWM | Controlled failure in service → stale or 503, logged |
| API key leak | Key only in backend `.env`; never logged (don't log URL) |

## Observability
- `/api/weather` returns `fetched_at` + `stale`.
- Backend logs: `[WEATHER] refreshed successfully`, `[WEATHER] refresh failed: <err>; serving stale cache`.

## Tests (before touching the ESP32)

| Scenario | Expected |
|----------|----------|
| Empty cache + OWM succeeds | 200 fresh weather |
| Fresh cache | No OWM request |
| Expired cache + OWM succeeds | New weather |
| Expired cache + OWM fails | Stale weather (≤ max_stale) |
| Empty cache + OWM fails | 503 |
| Multiple simultaneous refreshes | One OWM request |
| Malformed OWM response | Controlled failure |
| OWM 401/403 | Controlled failure |
| Backend restart | First successful request repopulates cache |
| ESP32 gets 503 | GUARD behavior preserved |
| **ESP32 API contract** | Backend still emits `temperature_c`/`relative_humidity_pct`/`absolute_humidity_g_m3`/`fetched_at`/`stale` |

## Execution order (revised)

1. **Backend foundation** — OWM settings + validation, add `httpx`, `WeatherService` (async client, cache, refresh lock, normalization, AH, stale policy, logging), canonical schema.
2. **Tests** — cache behavior, OWM parsing, failure/stale, concurrent refresh, API contract.
3. **API** — `/api/weather` (+ `/api/weather/current` only if the ESP32 genuinely benefits), mount router.
4. **Frontend** — weather card with `fetched_at`/`stale`, reuse existing poll.
5. **ESP32** — explicit backend weather URL, replace OWM request, validate status + payload, always close response, preserve GUARD, remove OWM creds from device config.
6. **Deployment** — deploy backend, verify API + dashboard + one ESP32 + ventilation decisions, then roll out to remaining devices; remove old OWM code/config after successful rollout.

## Explicitly out of scope (do not build)
No Redis, no Postgres weather table, no WebSockets, no Celery/broker, no repository layer, no microservice, no background-worker container. A well-written in-process `WeatherService` is the right level of engineering for ~5 devices and one external API.
