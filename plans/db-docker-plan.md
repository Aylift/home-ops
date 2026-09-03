# Plan: DB Persistence + Docker/Compose + Modular Multi-Room Architecture (v2 — revised)

> **Audience:** another AI reviewing this plan before implementation. This is the **revised** plan incorporating review feedback. It contains full context, current code, rationale, edge cases, and complete code snippets for every file.

---

## 0. Review verdict incorporated

The reviewer approved the overall direction and requested these changes, all incorporated below:

| Decision | Take |
|----------|------|
| PostgreSQL | ✅ |
| SQLAlchemy | ✅ |
| FastAPI routers | ✅ |
| node_id from day one | ✅ |
| Node registry (no auto-create) | ✅ |
| Device owns control logic | ✅ |
| Backend generic | ✅ |
| **Timezone-aware UTC datetimes** | ✅ change |
| **Composite indexes** | ✅ add |
| **Alembic now** | ✅ use instead of create_all |
| **Single multi-stage backend image (frontend built in)** | ✅ |
| **DB credentials via env** | ✅ change now |
| **CORS tightened** | ✅ remove/tighten |
| **/health DB-aware** | ✅ |
| **hours/limit validation** | ✅ add |
| **received_at column** | ✅ strongly recommend |
| **Backups documented** | ✅ document now |
| **uPlot** | ✅ pick |
| Separate Action table | 🟡 keep as `events` (extensible) |
| Raw 5-min history | ✅ fine initially |
| Downsampling | 🟡 add later |
| TimescaleDB | ❌ overkill |
| Device token/auth | 🟡 design for later, not now |

---

## 1. Current system state (what exists today)

### 1.1 Architecture

```
ESP32 (MicroPython) ──POST /api/telemetry──▶ FastAPI backend (:8001) ──▶ Vue 3 frontend
   iot/basement/main.py                          backend/app/main.py        frontend/src
```

- **Device** ([`iot/basement/main.py`](iot/basement/main.py)): reads BME280 (temp/RH/pressure), fetches outside humidity from OpenWeatherMap, decides fan state via `should_ventilate()`. Control loop sleeps **5 min** (`LOOP_INTERVAL = 300`); telemetry POSTs on a **5-min heartbeat** or an **immediate event** (fan state change / emergency entry).
- **Backend** ([`backend/app/main.py`](backend/app/main.py)): validates payload with Pydantic `Telemetry`, stores the latest sample **in memory**, logs it, returns 200. Exposes `GET /api/telemetry/latest` and `GET /api/actions`. Also serves the built frontend (`frontend/dist`) on the same port.
- **Frontend** ([`frontend/src/App.vue`](frontend/src/App.vue)): shadcn dashboard polling `GET /api/telemetry/latest` + `GET /api/actions` every 30s.
- **Host tooling** ([`iot/`](iot/)): WebREPL-based scripts to sync/reset/monitor the device.

### 1.2 Current telemetry payload (device → backend)

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

### 1.3 Current backend `main.py` (full, 114 lines)

```python
import os
import sys
from pathlib import Path
from typing import Optional

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title="Home Ops API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class Telemetry(BaseModel):
    timestamp: float
    temperature: float
    humidity: float
    pressure: float
    ah_inside: float
    ah_outside: Optional[float] = None
    fan_active: bool
    mode: str
    action: Optional[str] = None


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc):
    body = await request.body()
    print(f"[VALIDATION ERROR] body={body!r}")
    print(f"[VALIDATION ERROR] detail={exc.errors()}")
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


latest_telemetry = None
actions = []
MAX_ACTIONS = 20

MICROPY_EPOCH_OFFSET = 946684800


@app.post("/api/telemetry")
async def receive_telemetry(payload: Telemetry):
    global latest_telemetry
    data = payload.model_dump()
    data["timestamp"] = data["timestamp"] + MICROPY_EPOCH_OFFSET
    latest_telemetry = data
    if data.get("action"):
        actions.insert(0, {"timestamp": data["timestamp"], "action": data["action"]})
        del actions[MAX_ACTIONS:]
    print(f"[TELEMETRY] {data}")
    return {"status": "ok", "received": data}


@app.get("/api/telemetry/latest")
async def get_latest_telemetry():
    if latest_telemetry is None:
        return JSONResponse(status_code=404, content={"detail": "No telemetry yet"})
    return latest_telemetry


@app.get("/api/actions")
async def get_actions(limit: int = 5):
    return actions[:limit]


@app.get("/health")
async def health():
    return {"status": "ok"}


FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        file = FRONTEND_DIST / full_path
        if full_path and file.is_file():
            return FileResponse(file)
        return FileResponse(
            FRONTEND_DIST / "index.html",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
```

### 1.4 Current device `main.py` (relevant parts)

```python
from machine import Pin, I2C
import bme280
import time
import math
import urequests
import ntptime
import config

API_KEY = config.API_KEY
LAT = config.LAT
LON = config.LON
WEATHER_URL = f"http://api.openweathermap.org/data/2.5/weather?lat={LAT}&lon={LON}&appid={API_KEY}&units=metric"

THRESHOLD_ON = 60.0
THRESHOLD_OFF = 50.0
EMERGENCY_RH = 75.0
AH_HYSTERESIS = 0.5

LOOP_INTERVAL = 300
MIN_RUN_TIME = LOOP_INTERVAL
MIN_OFF_TIME = LOOP_INTERVAL
API_INTERVAL = 3 * LOOP_INTERVAL
HEARTBEAT_INTERVAL = LOOP_INTERVAL

ENABLE_DASHBOARD = True
DASHBOARD_URL = config.DASHBOARD_URL

try:
    i2c = I2C(0, scl=Pin(22), sda=Pin(21))
    bme = bme280.BME280(i2c=i2c)
    relay = Pin(19, Pin.OUT)
    relay.value(0)
except Exception as e:
    print(f"[HARDWARE INIT ERROR] {e}")
    raise

try:
    print("Syncing time from internet...")
    ntptime.settime()
except Exception as e:
    print(f"NTP time sync error: {e}")

prev_fan = None
prev_emergency = False
last_state_change = 0
last_api_check = 0
last_heartbeat = 0
ext_ah = None
```

### 1.5 Current `config.py` (gitignored, device-side)

```python
SSID = "TCL-FGM3-2.4GHz"
PASSWORD = "aBc12345"

STATIC_IP = "192.168.1.49"
NETMASK = "255.255.255.0"
GATEWAY = "192.168.1.1"
DNS = "8.8.8.8"

API_KEY = "3acf1a062b7972300fcd289318bb064a"
LAT = "51.05"
LON = "16.62"

DASHBOARD_URL = "http://192.168.1.67:8001/api/telemetry"

WEBREPL_PASS = "admin"
```

### 1.6 Current `requirements.txt`

```
fastapi==0.141.1
uvicorn==0.52.4
pydantic==2.13.5
pydantic-settings==2.15.0
SQLAlchemy==2.0.52
python-dotenv==1.2.3
... (transitive deps)
```

### 1.7 Current `.gitignore`

```
# Secrets
.env
iot/basement/config.py

# Python
__pycache__/
*.pyc
backend/.venv/

# Node
frontend/node_modules/
frontend/dist/
```

### 1.8 Current `.env.template`

```
# --- ESP32 Node (host-side sync) ---
ESP_IP=
WEBREPL_PASS=
```

---

## 2. Goals

1. **Persist telemetry history** (temperature, humidity, pressure, AH inside/outside, fan state, mode) to PostgreSQL.
2. **Add a history/chart API endpoint** for the dashboard.
3. **Containerize** backend + frontend + postgres with Docker Compose for easy deployment to the server PC.
4. **Design for multiple rooms / multiple automation nodes** from day one — modular, not fan-only.

---

## 3. Target architecture

```
ESP32 basement ──POST /api/telemetry──▶ FastAPI (:8001) ──▶ PostgreSQL (:5432)
   iot/basement/main.py                    backend/app            db (volume)
                                                                    │
Vue 3 dashboard (served by backend) ◀──GET /api/telemetry/history──┘
```

- **Backend** stays the single HTTP entrypoint, backed by Postgres.
- **Postgres** runs as a separate compose service with a named volume.
- **Frontend is built INTO the backend Docker image** (multi-stage) — no host dist mount, no scratch volume, no separate frontend service. Deployment = `docker compose up -d --build` with just `db` + `backend`.
- **Multi-room** via `node_id` on every row + a `nodes` registry.

### Layer boundaries (from review)

```
             ┌──────────────────────────────┐
             │            ESP32             │
             │  sensor + control + safety   │
             └──────────────┬───────────────┘
                            │ telemetry
                            ▼
             ┌──────────────────────────────┐
             │           FastAPI            │
             │  ingestion / node registry   │
             │  query API / health          │
             └──────────────┬───────────────┘
                            ▼
             ┌──────────────────────────────┐
             │         PostgreSQL           │
             │  nodes / telemetry / events  │
             └──────────────┬───────────────┘
                            ▲ query/history
             ┌──────────────┴───────────────┐
             │        Vue dashboard         │
             └──────────────────────────────┘
```

The backend understands **generic** concepts (node online/offline, events, capabilities, commands) but **not** room-specific logic (fan hysteresis, AH thresholds). Those stay on the device.

---

## 4. Modularity decisions (multi-room ready)

1. **`node_id` on every telemetry row** (string, e.g. `"basement"`). The device sends it; the backend defaults to `"basement"` **only on the ingestion endpoint** for backward compatibility.
2. **`nodes` table** is the deliberate registry. **No auto-create on ingestion** — an unknown node is rejected (400), a disabled node is rejected (403). The initial `basement` node is seeded via Alembic migration.
3. **Fan/relay logic stays device-side.** The backend is a generic telemetry + history + events store.
4. **All timing/control constants stay in device `main.py`.**

---

## 5. Database schema (v1 — revised)

### 5.1 `nodes`

| column | type | notes |
|--------|------|-------|
| id | String PK | e.g. `"basement"` |
| name | String | display name |
| room | String | e.g. `"Basement"` |
| type | String | device type: `climate`/`utility`/`water`/`security`/`power`/`generic` |
| enabled | Bool | default true |
| created_at | timestamptz | |
| last_seen_at | timestamptz NULL | updated on ingestion |

### 5.2 `telemetry`

| column | type | notes |
|--------|------|-------|
| id | Integer PK auto | |
| node_id | String FK→nodes.id | |
| timestamp | timestamptz | device-reported measurement time (UTC) |
| received_at | timestamptz | server receive time, `default=now()` |
| temperature | Float | |
| humidity | Float | |
| pressure | Float | |
| ah_inside | Float | |
| ah_outside | Float NULL | |
| fan_active | Bool | |
| mode | String | display string (kept for v1) |

**Composite index:** `(node_id, timestamp)`.

### 5.3 `events` (replaces the separate `actions` table; extensible)

| column | type | notes |
|--------|------|-------|
| id | Integer PK auto | |
| node_id | String FK→nodes.id | |
| timestamp | timestamptz | device-reported event time |
| received_at | timestamptz | server receive time |
| type | String | e.g. `control` |
| code | String | e.g. `fan_started` |
| message | String | human-readable |

**Composite index:** `(node_id, timestamp)`.

> **Why `events` over `actions`:** the reviewer's point — `telemetry.action` + a separate `actions` table duplicates data. We keep `telemetry` as the raw measurement stream and `events` as the discrete event stream. The device's `action` field maps to an `events` row (`type="control"`, `code` derived from the action string). This is more extensible (future `type`/`code`/`message` for any event kind) while staying simple.

### 5.4 Indexing rationale
- Primary history query: `WHERE node_id = ? AND timestamp >= ? ORDER BY timestamp ASC` → composite `(node_id, timestamp)`.
- Latest query: `WHERE node_id = ? ORDER BY timestamp DESC LIMIT 1` → same composite index (reverse scan).
- `received_at` is not indexed (used only for diagnostics).

---

## 6. Backend file layout

```
backend/
  alembic.ini
  alembic/
    env.py
    versions/
      0001_initial.py
  app/
    __init__.py
    main.py          # FastAPI app, routers, static hosting
    config.py        # pydantic-settings: DATABASE_URL, etc.
    database.py      # engine, SessionLocal, Base, get_db
    models.py        # SQLAlchemy models (Node, Telemetry, Event)
    schemas.py       # Pydantic schemas
    routers/
      __init__.py
      telemetry.py   # POST /api/telemetry, GET latest, GET history
      events.py      # GET /api/events
      nodes.py       # GET /api/nodes, POST /api/nodes
  requirements.txt
  Dockerfile
```

---

## 7. Complete code snippets

### 7.1 `backend/app/config.py`
```python
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://homeops:homeops@db:5432/homeops"
    micropy_epoch_offset: int = 946684800
    frontend_dist: str = ""

    class Config:
        env_file = ".env"
        env_prefix = ""


settings = Settings()
```

### 7.2 `backend/app/database.py`
```python
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

### 7.3 `backend/app/models.py`
```python
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Node(Base):
    __tablename__ = "nodes"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    room: Mapped[str] = mapped_column(String, default="")
    type: Mapped[str] = mapped_column(String, default="climate")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Telemetry(Base):
    __tablename__ = "telemetry"
    __table_args__ = (
        Index("ix_telemetry_node_timestamp", "node_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(String, ForeignKey("nodes.id"))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    temperature: Mapped[float] = mapped_column(Float)
    humidity: Mapped[float] = mapped_column(Float)
    pressure: Mapped[float] = mapped_column(Float)
    ah_inside: Mapped[float] = mapped_column(Float)
    ah_outside: Mapped[float | None] = mapped_column(Float, nullable=True)
    fan_active: Mapped[bool] = mapped_column(Boolean)
    mode: Mapped[str] = mapped_column(String)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_node_timestamp", "node_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(String, ForeignKey("nodes.id"))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    type: Mapped[str] = mapped_column(String)
    code: Mapped[str] = mapped_column(String)
    message: Mapped[str] = mapped_column(String)
```

### 7.4 `backend/app/schemas.py`
```python
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class TelemetryIn(BaseModel):
    # MicroPython epoch seconds; converted to UTC in the router.
    timestamp: float
    temperature: float
    humidity: float
    pressure: float
    ah_inside: float
    ah_outside: Optional[float] = None
    fan_active: bool
    mode: str
    action: Optional[str] = None
    # Backward-compat default on the INGESTION endpoint only.
    node_id: str = "basement"


class TelemetryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    node_id: str
    timestamp: datetime
    received_at: datetime
    temperature: float
    humidity: float
    pressure: float
    ah_inside: float
    ah_outside: Optional[float]
    fan_active: bool
    mode: str


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    node_id: str
    timestamp: datetime
    received_at: datetime
    type: str
    code: str
    message: str


class NodeIn(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    name: str
    room: str = ""
    type: str = "climate"
    enabled: bool = True


class NodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    room: str
    type: str
    enabled: bool
    created_at: datetime
    last_seen_at: Optional[datetime]
```

### 7.5 `backend/app/routers/telemetry.py`
```python
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import Event, Node, Telemetry
from ..schemas import TelemetryIn, TelemetryOut

router = APIRouter(prefix="/api/telemetry", tags=["telemetry"])


@router.post("")
def receive_telemetry(payload: TelemetryIn, db: Session = Depends(get_db)):
    node = db.get(Node, payload.node_id)
    if node is None:
        raise HTTPException(status_code=400, detail="Unknown node")
    if not node.enabled:
        raise HTTPException(status_code=403, detail="Node disabled")

    # MicroPython time.time() is seconds since 2000-01-01; convert to UTC.
    ts = datetime.fromtimestamp(
        payload.timestamp + settings.micropy_epoch_offset,
        tz=timezone.utc,
    )

    row = Telemetry(
        node_id=payload.node_id,
        timestamp=ts,
        temperature=payload.temperature,
        humidity=payload.humidity,
        pressure=payload.pressure,
        ah_inside=payload.ah_inside,
        ah_outside=payload.ah_outside,
        fan_active=payload.fan_active,
        mode=payload.mode,
    )
    db.add(row)

    if payload.action:
        db.add(
            Event(
                node_id=payload.node_id,
                timestamp=ts,
                type="control",
                code=payload.action,
                message=payload.action,
            )
        )

    node.last_seen_at = datetime.now(timezone.utc)
    db.commit()
    return {"status": "ok"}


@router.get("/latest", response_model=TelemetryOut)
def get_latest(
    node_id: str = Query(description="Node id"),
    db: Session = Depends(get_db),
):
    row = db.execute(
        select(Telemetry)
        .where(Telemetry.node_id == node_id)
        .order_by(Telemetry.timestamp.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="No telemetry yet")
    return row


@router.get("/history", response_model=list[TelemetryOut])
def get_history(
    node_id: str = Query(description="Node id"),
    hours: Annotated[int, Query(ge=1, le=24 * 30)] = 24,
    db: Session = Depends(get_db),
):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = db.execute(
        select(Telemetry)
        .where(Telemetry.node_id == node_id, Telemetry.timestamp >= since)
        .order_by(Telemetry.timestamp.asc())
    ).scalars().all()
    return rows
```

> **Note:** `node_id` is **required** on `latest`/`history` (no silent `"basement"` default). Backward compatibility lives only on `POST /api/telemetry`. This prevents a future garage dashboard silently showing basement data.

### 7.6 `backend/app/routers/events.py`
```python
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Event
from ..schemas import EventOut

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("", response_model=list[EventOut])
def get_events(
    node_id: str = Query(description="Node id"),
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(Event)
        .where(Event.node_id == node_id)
        .order_by(Event.timestamp.desc())
        .limit(limit)
    ).scalars().all()
    return rows
```

### 7.7 `backend/app/routers/nodes.py`
```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Node
from ..schemas import NodeIn, NodeOut

router = APIRouter(prefix="/api/nodes", tags=["nodes"])


@router.get("", response_model=list[NodeOut])
def list_nodes(db: Session = Depends(get_db)):
    return db.execute(select(Node).order_by(Node.id)).scalars().all()


@router.post("", response_model=NodeOut, status_code=201)
def create_node(payload: NodeIn, db: Session = Depends(get_db)):
    if db.get(Node, payload.id) is not None:
        raise HTTPException(status_code=409, detail="Node already exists")
    node = Node(**payload.model_dump())
    db.add(node)
    db.commit()
    db.refresh(node)
    return node
```

### 7.8 `backend/app/main.py` (rewritten)
```python
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from .config import settings
from .database import SessionLocal
from .routers import events, nodes, telemetry

app = FastAPI(title="Home Ops API")

# Frontend and backend are same-origin, so CORS is only needed for Vue dev
# mode (localhost:5173). Tighten to that; production needs none.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(telemetry.router)
app.include_router(events.router)
app.include_router(nodes.router)


@app.get("/health/live")
def health_live():
    return {"status": "ok"}


@app.get("/health/ready")
def health_ready():
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
    return {"status": "ok"}


# --- Frontend static hosting ---
if settings.frontend_dist:
    FRONTEND_DIST = Path(settings.frontend_dist)
else:
    FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

if FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        file = FRONTEND_DIST / full_path
        if full_path and file.is_file():
            return FileResponse(file)
        return FileResponse(
            FRONTEND_DIST / "index.html",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
```

### 7.9 `backend/requirements.txt`
```
fastapi==0.141.1
uvicorn==0.52.4
pydantic==2.13.5
pydantic-settings==2.15.0
SQLAlchemy==2.0.52
psycopg[binary]==3.2.9
alembic==1.16.4
python-dotenv==1.2.3
```

### 7.10 `backend/Dockerfile` — single multi-stage image (frontend built in)
```dockerfile
# --- Stage 1: build the Vue frontend ---
FROM node:22-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

# --- Stage 2: Python runtime serving API + built frontend ---
FROM python:3.13-slim
WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY backend/alembic.ini .
COPY backend/alembic ./alembic
COPY --from=frontend-build /frontend/dist ./frontend/dist

ENV FRONTEND_DIST=/app/frontend/dist

EXPOSE 8001
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
```

> **Build context note:** the Dockerfile references `frontend/` and `backend/` from the repo root, so the build context must be the **repo root** (`.`), not `./backend`. The compose `build.context` handles this (see 7.11).

### 7.11 `docker-compose.yml` (root)
```yaml
services:
  db:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
      interval: 5s
      timeout: 5s
      retries: 5
    # No ports: Postgres stays internal to the compose network.

  backend:
    build:
      context: .
      dockerfile: backend/Dockerfile
    restart: unless-stopped
    depends_on:
      db:
        condition: service_healthy
    environment:
      DATABASE_URL: postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB}
      FRONTEND_DIST: /app/frontend/dist
    ports:
      - "8001:8001"
    # Run migrations, then start the app.
    command: >
      sh -c "alembic upgrade head &&
             uvicorn app.main:app --host 0.0.0.0 --port 8001"

volumes:
  pgdata:
```

> **No `ports` on `db`** — Postgres is only reachable via the compose network (`db:5432`). This keeps it off the LAN.

### 7.12 `.env.template` (extended — no real secrets)
```
# --- ESP32 Node (host-side sync) ---
ESP_IP=
WEBREPL_PASS=

# --- Backend / Postgres ---
POSTGRES_USER=homeops
POSTGRES_PASSWORD=
POSTGRES_DB=homeops
DATABASE_URL=postgresql+psycopg://homeops:homeops@db:5432/homeops
```

> `POSTGRES_PASSWORD` is left **blank** in the template. The real `.env` (gitignored) holds a generated long password. `DATABASE_URL` in the template is a placeholder; compose builds the real URL from the `POSTGRES_*` vars.

### 7.13 `.gitignore` (additions)
```
# Docker / volumes
pgdata/
```

### 7.14 Alembic setup

`backend/alembic.ini`:
```ini
[alembic]
script_location = alembic
prepend_sys_path = .
sqlalchemy.url = postgresql+psycopg://homeops:homeops@db:5432/homeops

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

`backend/alembic/env.py` (key parts):
```python
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import settings
from app.database import Base
from app import models  # noqa: F401  (register models)

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

`backend/alembic/versions/0001_initial.py` (creates tables + seeds the `basement` node):
```python
"""initial schema: nodes, telemetry, events"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nodes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("node_id", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "telemetry",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("node_id", sa.String(64), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("humidity", sa.Float(), nullable=True),
        sa.Column("pressure", sa.Float(), nullable=True),
        sa.Column("ah_inside", sa.Float(), nullable=True),
        sa.Column("ah_outside", sa.Float(), nullable=True),
        sa.Column("fan_active", sa.Boolean(), nullable=True),
        sa.Column("mode", sa.String(64), nullable=True),
        sa.Column("action", sa.String(128), nullable=True),
    )
    op.create_index("ix_telemetry_node_timestamp", "telemetry", ["node_id", "timestamp"])
    op.create_index("ix_telemetry_timestamp", "telemetry", ["timestamp"])

    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("node_id", sa.String(64), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("code", sa.String(64), nullable=True),
        sa.Column("message", sa.String(256), nullable=True),
    )
    op.create_index("ix_events_node_timestamp", "events", ["node_id", "timestamp"])

    op.execute(
        sa.text(
            "INSERT INTO nodes (node_id, name, enabled) VALUES ('basement', 'Basement', true)"
        )
    )


def downgrade() -> None:
    op.drop_table("events")
    op.drop_table("telemetry")
    op.drop_table("nodes")
```

---

## 8. Device-side changes (ESP32)

Minimal — the device stays the single source of truth for control logic. Only two changes:

1. **Add `node_id` to the payload** in [`iot/basement/main.py`](iot/basement/main.py) `send_to_dashboard()`:
   ```python
   payload = {
       "node_id": NODE_ID,  # from config.py
       "timestamp": time.time(),
       "temperature": ...,
       ...
   }
   ```
2. **Add `NODE_ID = "basement"`** to the gitignored [`iot/basement/config.py`](iot/basement/config.py) (and to `config.template.py`).

No control logic moves to the backend. The backend never decides fan state — it only stores and serves what the device reports. This keeps the multi-room story clean: a second room is a second `node_id` with its own `main.py`/`config.py`, and the backend needs zero changes.

---

## 9. Migration & data notes

- **Alembic is the only schema authority.** `Base.metadata.create_all()` is never called at runtime. `docker-compose` runs `alembic upgrade head` before uvicorn starts.
- **No data migration needed** — the current backend keeps telemetry in memory only; there is no existing DB to convert.
- **`received_at` vs `timestamp`:** `timestamp` is the device's clock (MicroPython epoch + offset, can drift); `received_at` is the server's clock at insert. Use `received_at` for "when did we actually get this" and `timestamp` for "when did the device measure it". Charts should plot on `timestamp` but be aware of drift; ops queries use `received_at`.
- **Node seeding:** the `basement` node is inserted by the `0001_initial` migration. New rooms are added via `POST /api/nodes` (or a future migration), never implicitly by telemetry.

---

## 10. Edge cases & hardening

| Concern | Handling |
|---------|----------|
| Unknown `node_id` in telemetry | Reject with **400** (no auto-create) |
| Disabled node | Reject with **403** |
| DB down at startup | `pool_pre_ping=True`; uvicorn starts anyway, `/health/ready` reports 503 until DB is reachable |
| DB down mid-request | 500 with logged error; device retries on next heartbeat |
| MicroPython epoch | Offset `946684800` applied once on ingest in the router |
| Non-ASCII mode strings | Defensive `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` stays at top of `main.py` |
| `hours`/`limit` abuse | Bounded via `Query(ge=..., le=...)` |
| CORS | Only `http://localhost:5173` in dev; same-origin in production (no CORS needed) |
| Retention | No auto-purge yet; documented `pg_dump` backup. A retention job (e.g. keep 30d raw + downsample) is a later item |

---

## 11. Backup strategy

- **DB volume is NOT a backup.** Document `pg_dump` as the backup path:
  ```bash
  docker compose exec db pg_dump -U homeops homeops > backup_$(date +%F).sql
  ```
- Restore: `docker compose exec -T db psql -U homeops homeops < backup.sql`.
- A cron/systemd timer on the server PC can run the dump nightly. (Optional later: `pg_dump` into a mounted host dir.)

---

## 12. Frontend (deferred — next phase)

- Add a **history/charts view** using **uPlot** (lightweight, ~40KB, ideal for many points).
- Endpoints consumed: `GET /api/telemetry/history?node_id=basement&hours=24` and `GET /api/events?node_id=basement&limit=20`.
- Node selector for multi-room (defaults to `basement`).
- Polling stays 30s for live cards; history chart fetches on mount + on node change (not on the 30s poll).

---

## 13. Integration tests

Before touching the ESP32, add backend integration tests (pytest + a test Postgres or SQLite-in-memory for schema):

- POST telemetry for an unknown node → 400
- POST telemetry for a disabled node → 403
- POST valid telemetry → 200, row persisted, `last_seen_at` updated
- POST telemetry with `action` → an `events` row is created
- GET `/api/telemetry/history` with `node_id` → bounded rows, ordered by timestamp
- GET `/api/telemetry/history` without `node_id` → 422
- GET `/health/ready` with DB up → 200; with DB down → 503

---

## 14. Execution order (implementation)

1. **DB foundation** — `requirements.txt`, `config.py`, `database.py`, `models.py`, `schemas.py`, Alembic setup + `0001_initial`.
2. **Ingestion** — `routers/telemetry.py` POST (node validation, epoch offset, insert + event, `last_seen_at`).
3. **Queries** — `routers/telemetry.py` GET latest/history, `routers/events.py`, `routers/nodes.py`; rewrite `main.py` to mount routers + health + CORS.
4. **Docker** — `backend/Dockerfile` (multi-stage), `docker-compose.yml`, `.env.template`, `.gitignore`.
5. **ESP32** — add `NODE_ID` to `config.py` + payload; push + soft-reset.
6. **Frontend** — uPlot history view + node selector (separate phase).
7. **Hardening** — integration tests, backup docs, retention.

---

## 15. Open questions (for approval)

1. **Retention policy** — keep raw data forever, or auto-purge after N days? (Affects storage sizing.)
2. **`mode` field** — keep as a free string for now (deferred to a structured enum), or model it now?
3. **Postgres version** — `postgres:16-alpine` acceptable?
4. **Backend port** — keep `8001` on the server PC, or standardize on `8000`?
5. **Auth** — the dashboard is LAN-only today. Add any auth layer now, or keep it open on the LAN?
