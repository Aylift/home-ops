"""Manual fan override.

The dashboard sets a signed minute delta; the device polls the resulting
absolute expiry and forces the fan ON until it passes. Storing an absolute
expiry (not a countdown) means the device needs no clock agreement with the
backend and a missed poll can't extend the window.
"""

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Event, FanOverride, Node
from app.schemas import FanOverrideIn, FanOverrideOut

router = APIRouter(prefix="/api/fan", tags=["fan"])

DbDep = Annotated[Session, Depends(get_db)]

# Cap the total window so a stuck button can't pin the fan on forever.
MAX_OVERRIDE_MINUTES = 24 * 60


def _resolve_node(db: Session, node_id: str) -> Node:
    node = db.scalar(select(Node).where(Node.node_id == node_id))
    if node is None:
        raise HTTPException(status_code=400, detail=f"Unknown node: {node_id}")
    if not node.enabled:
        raise HTTPException(status_code=403, detail=f"Node disabled: {node_id}")
    return node


def _remaining(row: FanOverride | None) -> int:
    if row is None:
        return 0
    delta = (row.expires_at - datetime.now(timezone.utc)).total_seconds()
    return max(0, int(delta))


@router.get("/override", response_model=FanOverrideOut)
def get_override(
    db: DbDep,
    node_id: Annotated[str, Query(pattern=r"^[a-z0-9_]+$")] = "basement",
):
    row = db.get(FanOverride, node_id)
    remaining = _remaining(row)
    return FanOverrideOut(
        node_id=node_id,
        active=remaining > 0,
        expires_at=row.expires_at if row is not None else None,
        remaining_seconds=remaining,
    )


@router.post("/override", response_model=FanOverrideOut)
def set_override(payload: FanOverrideIn, db: DbDep):
    _resolve_node(db, payload.node_id)

    now = datetime.now(timezone.utc)
    row = db.get(FanOverride, payload.node_id)

    # Extend from the current expiry if still active, else from now.
    base = row.expires_at if row is not None and row.expires_at > now else now
    expires = base + timedelta(minutes=payload.minutes)

    # Clamp to [now, now + MAX_OVERRIDE_MINUTES]; a negative delta past zero
    # simply clears the override.
    if expires < now:
        expires = now
    cap = now + timedelta(minutes=MAX_OVERRIDE_MINUTES)
    if expires > cap:
        expires = cap

    if row is None:
        row = FanOverride(node_id=payload.node_id, expires_at=expires)
        db.add(row)
    else:
        row.expires_at = expires
        row.updated_at = now

    db.add(
        Event(
            node_id=payload.node_id,
            timestamp=now,
            type="action",
            code="fan_override",
            message=f"Fan override {payload.minutes:+d}m",
        )
    )
    db.commit()

    remaining = _remaining(row)
    return FanOverrideOut(
        node_id=payload.node_id,
        active=remaining > 0,
        expires_at=row.expires_at,
        remaining_seconds=remaining,
    )


@router.delete("/override", response_model=FanOverrideOut)
def clear_override(
    db: DbDep,
    node_id: Annotated[str, Query(pattern=r"^[a-z0-9_]+$")] = "basement",
):
    row = db.get(FanOverride, node_id)
    if row is not None:
        db.delete(row)
        db.add(
            Event(
                node_id=node_id,
                timestamp=datetime.now(timezone.utc),
                type="action",
                code="fan_override",
                message="Fan override cleared",
            )
        )
        db.commit()
    return FanOverrideOut(node_id=node_id, active=False, expires_at=None, remaining_seconds=0)
