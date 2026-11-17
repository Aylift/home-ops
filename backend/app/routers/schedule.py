"""Fan schedule CRUD.

Blocks are recurring local wall-clock windows in which the fan is forced to
`desired_state` (default OFF — heating-season lockout). The device never sees
this table directly: `GET /api/fan/override` projects an active block as a
synthetic override.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import FanSchedule, Node
from app.schemas import FanScheduleIn, FanScheduleOut, FanScheduleUpdate

router = APIRouter(prefix="/api/fan/schedule", tags=["fan"])

DbDep = Annotated[Session, Depends(get_db)]


def _resolve_node(db: Session, node_id: str) -> Node:
    node = db.scalar(select(Node).where(Node.node_id == node_id))
    if node is None:
        raise HTTPException(status_code=400, detail=f"Unknown node: {node_id}")
    return node


def _get_or_404(db: Session, schedule_id: int) -> FanSchedule:
    row = db.get(FanSchedule, schedule_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return row


@router.get("", response_model=list[FanScheduleOut])
def list_schedules(
    db: DbDep,
    node_id: Annotated[str, Query(pattern=r"^[a-z0-9_]+$")] = "basement",
):
    return db.scalars(
        select(FanSchedule)
        .where(FanSchedule.node_id == node_id)
        .order_by(FanSchedule.id.asc())
    ).all()


@router.post("", response_model=FanScheduleOut, status_code=201)
def create_schedule(payload: FanScheduleIn, db: DbDep):
    _resolve_node(db, payload.node_id)
    row = FanSchedule(**payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/{schedule_id}", response_model=FanScheduleOut)
def update_schedule(schedule_id: int, payload: FanScheduleUpdate, db: DbDep):
    row = _get_or_404(db, schedule_id)
    # exclude_unset (not exclude_none) so nullable dates can be cleared to null.
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: int, db: DbDep):
    row = _get_or_404(db, schedule_id)
    db.delete(row)
    db.commit()
