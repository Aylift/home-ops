import sys
from contextlib import asynccontextmanager

# Windows console cp1252 can't encode Polish chars (ą) in mode field.
# Force UTF-8 with replacement so print() never crashes the request handler.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.config import settings
from app.database import engine
from app.routers import events, nodes, telemetry


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    engine.dispose()


app = FastAPI(title="Home Ops API", lifespan=lifespan)

# Same-origin in production (frontend served by this app). Only the Vite dev
# server needs CORS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(telemetry.router)
app.include_router(events.router)
app.include_router(nodes.router)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc):
    body = await request.body()
    print(f"[VALIDATION ERROR] body={body!r}")
    print(f"[VALIDATION ERROR] detail={exc.errors()}")
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.get("/health/live")
async def health_live():
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    return {"status": "ok"}


# --- Frontend static hosting ---
# Serve the built Vue app from settings.frontend_dist so the whole dashboard +
# API live on one port, reachable from any device on the LAN.
FRONTEND_DIST = settings.frontend_dist
if FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        file = FRONTEND_DIST / full_path
        if full_path and file.is_file():
            return FileResponse(file)
        # index.html is the pointer to the current hashed Vite bundle. Never
        # cache it, or a stale index.html keeps serving an old bundle after a
        # rebuild (the browser would keep running the old polling code).
        return FileResponse(
            FRONTEND_DIST / "index.html",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
