from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Node
from app.schemas import NodeIn, NodeOut

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
