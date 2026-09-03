from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Event, Node, Telemetry
from app.schemas import TelemetryIn, TelemetryOut

router = APIRouter(prefix="/api/telemetry", tags=["telemetry"])

DbDep = Annotated[Session, Depends(get_db)]


def _resolve_node(db: Session, node_id: str) -> Node:
    node = db.scalar(select(Node).where(Node.node_id == node_id))
    if node is None:
        raise HTTPException(status_code=400, detail=f"Unknown node: {node_id}")
    if not node.enabled:
        raise HTTPException(status_code=403, detail=f"Node disabled: {node_id}")
    return node


@router.post("", status_code=200)
def receive_telemetry(payload: TelemetryIn, db: DbDep):
    node = _resolve_node(db, payload.node_id)

    # MicroPython epoch (2000-01-01) -> Unix epoch (1970-01-01).
    unix_ts = payload.timestamp + settings.micropy_epoch_offset
    ts = datetime.fromtimestamp(unix_ts, tz=timezone.utc)

    row = Telemetry(
        node_id=node.node_id,
        timestamp=ts,
        temperature=payload.temperature,
        humidity=payload.humidity,
        pressure=payload.pressure,
        ah_inside=payload.ah_inside,
        ah_outside=payload.ah_outside,
        fan_active=payload.fan_active,
        mode=payload.mode,
        action=payload.action,
    )
    db.add(row)

    if payload.action:
        db.add(
            Event(
                node_id=node.node_id,
                timestamp=ts,
                type="action",
                code=payload.action,
                message=payload.action,
            )
        )

    node.last_seen_at = datetime.now(timezone.utc)
    db.commit()

    print(f"[TELEMETRY] node={node.node_id} ts={ts.isoformat()} {payload.model_dump()}")
    return {"status": "ok", "received": payload.model_dump()}


@router.get("/latest", response_model=TelemetryOut)
def get_latest_telemetry(
    db: DbDep,
    node_id: Annotated[str, Query(pattern=r"^[a-z0-9_]+$")],
):
    row = db.scalar(
        select(Telemetry)
        .where(Telemetry.node_id == node_id)
        .order_by(Telemetry.timestamp.desc())
        .limit(1)
    )
    if row is None:
        raise HTTPException(status_code=404, detail="No telemetry yet")
    return row


@router.get("/history", response_model=list[TelemetryOut])
def get_history(
    db: DbDep,
    node_id: Annotated[str, Query(pattern=r"^[a-z0-9_]+$")],
    hours: Annotated[int, Query(ge=1, le=24 * 30)] = 24,
    limit: Annotated[int, Query(ge=1, le=10000)] = 1000,
):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = db.scalars(
        select(Telemetry)
        .where(Telemetry.node_id == node_id, Telemetry.timestamp >= since)
        .order_by(Telemetry.timestamp.asc())
        .limit(limit)
    ).all()
    return rows
