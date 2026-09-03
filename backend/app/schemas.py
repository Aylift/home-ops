from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# --- Inbound telemetry (from the device) ---
class TelemetryIn(BaseModel):
    # node_id is optional on ingestion only: a single-node device may omit it
    # and the router defaults to "basement". Query endpoints require it.
    node_id: str = Field(default="basement", pattern=r"^[a-z0-9_]+$")
    timestamp: float
    temperature: float
    humidity: float
    pressure: float
    ah_inside: float
    ah_outside: Optional[float] = None
    fan_active: bool
    mode: str
    action: Optional[str] = None


# --- Outbound telemetry (to the dashboard) ---
class TelemetryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    node_id: str
    timestamp: datetime
    received_at: datetime
    temperature: Optional[float] = None
    humidity: Optional[float] = None
    pressure: Optional[float] = None
    ah_inside: Optional[float] = None
    ah_outside: Optional[float] = None
    fan_active: Optional[bool] = None
    mode: Optional[str] = None
    action: Optional[str] = None


# --- Events ---
class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    node_id: str
    timestamp: datetime
    received_at: datetime
    type: str
    code: Optional[str] = None
    message: Optional[str] = None


# --- Nodes ---
class NodeIn(BaseModel):
    node_id: str = Field(pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=1, max_length=128)
    enabled: bool = True


class NodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    node_id: str
    name: str
    enabled: bool
    created_at: datetime
    last_seen_at: Optional[datetime] = None
