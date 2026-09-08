from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Node
from app.schemas import NodeIn, NodeOut, NodeStatusOut

router = APIRouter(prefix="/api/nodes", tags=["nodes"])

DbDep = Annotated[Session, Depends(get_db)]


@router.get("", response_model=list[NodeOut])
def list_nodes(db: DbDep):
    return db.scalars(select(Node).order_by(Node.node_id)).all()


@router.post("", response_model=NodeOut, status_code=201)
def create_node(payload: NodeIn, db: DbDep):
    existing = db.scalar(select(Node).where(Node.node_id == payload.node_id))
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Node exists: {payload.node_id}")
    node = Node(
        node_id=payload.node_id,
        name=payload.name,
        enabled=payload.enabled,
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    return node


@router.get("/{node_id}/status", response_model=NodeStatusOut)
def get_node_status(node_id: str, db: DbDep):
    node = db.scalar(select(Node).where(Node.node_id == node_id))
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")

    seconds_since_seen = None
    alive = False
    if node.last_seen_at is not None:
        seconds_since_seen = int(
            (datetime.now(timezone.utc) - node.last_seen_at).total_seconds()
        )
        alive = seconds_since_seen <= settings.node_alive_seconds

    return NodeStatusOut(
        node_id=node.node_id,
        name=node.name,
        enabled=node.enabled,
        alive=alive,
        last_seen_at=node.last_seen_at,
        seconds_since_seen=seconds_since_seen,
    )
