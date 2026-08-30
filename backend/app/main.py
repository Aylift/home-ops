import os
import sys
from pathlib import Path
from typing import Optional

# Windows console cp1252 can't encode Polish chars (ą) in mode field.
# Force UTF-8 with replacement so print() never crashes the request handler.
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


# In-memory store of the latest telemetry sample
latest_telemetry = None

# Rolling log of major events (fan on/off, emergencies), newest first
actions = []
MAX_ACTIONS = 20

# MicroPython's time.time() counts seconds since the MicroPython epoch
# (2000-01-01 00:00:00 UTC), not the Unix epoch (1970-01-01). Add this offset
# so timestamps are real Unix seconds the frontend can render directly.
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


# --- Frontend static hosting ---
# Serve the built Vue app from frontend/dist so the whole dashboard + API live
# on one port (http://<host-ip>:8001), reachable from any device on the LAN.
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        file = FRONTEND_DIST / full_path
        if full_path and file.is_file():
            return FileResponse(file)
        return FileResponse(FRONTEND_DIST / "index.html")
