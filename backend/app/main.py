import sys
from typing import Optional

# Windows console cp1252 can't encode Polish chars (ą) in mode field.
# Force UTF-8 with replacement so print() never crashes the request handler.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc):
    body = await request.body()
    print(f"[VALIDATION ERROR] body={body!r}")
    print(f"[VALIDATION ERROR] detail={exc.errors()}")
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


# In-memory store of the latest telemetry sample
latest_telemetry = None


@app.post("/api/telemetry")
async def receive_telemetry(payload: Telemetry):
    global latest_telemetry
    latest_telemetry = payload.model_dump()
    print(f"[TELEMETRY] {latest_telemetry}")
    return {"status": "ok", "received": latest_telemetry}


@app.get("/api/telemetry/latest")
async def get_latest_telemetry():
    if latest_telemetry is None:
        return JSONResponse(status_code=404, content={"detail": "No telemetry yet"})
    return latest_telemetry


@app.get("/health")
async def health():
    return {"status": "ok"}
