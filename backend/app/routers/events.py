from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Event
from app.schemas import EventOut

router = APIRouter(prefix="/api/events", tags=["events"])

DbDep = Annotated[Session, Depends(get_db)]


@router.get("", response_model=list[EventOut])
def get_events(
    db: DbDep,
    node_id: Annotated[str, Query(pattern=r"^[a-z0-9_]+$")],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    rows = db.scalars(
        select(Event)
        .where(Event.node_id == node_id)
        .order_by(Event.timestamp.desc())
        .limit(limit)
    ).all()
    return rows
