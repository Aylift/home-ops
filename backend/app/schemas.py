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
    # Remaining manual-override seconds reported by the device (0 when none).
    override_seconds: Optional[int] = None


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
    override_seconds: Optional[int] = None


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


class NodeStatusOut(BaseModel):
    node_id: str
    name: str
    enabled: bool
    alive: bool
    last_seen_at: Optional[datetime] = None
    seconds_since_seen: Optional[int] = None


# --- Weather ---
# Canonical, self-describing weather model. Field names embed units/meaning so
# consumers (Vue dashboard, ESP32) never need to know OWM's response shape.
class WeatherOut(BaseModel):
    temperature_c: float
    relative_humidity_pct: float
    absolute_humidity_g_m3: float
    description: str
    icon: str
    wind_speed_mps: Optional[float] = None
    cloud_pct: Optional[int] = None
    # Place name of the weather station the data was fetched from (OWM returns
    # the nearest locality name for the configured coordinates).
    station_name: Optional[str] = None
    fetched_at: datetime
    observed_at: Optional[datetime] = None
    stale: bool


# Minimal projection for the ESP32 (smaller payload for MicroPython parsing).
class WeatherCurrentOut(BaseModel):
    temperature_c: float
    relative_humidity_pct: float
    absolute_humidity_g_m3: float
    fetched_at: datetime
    stale: bool


# --- Fan override ---
class FanOverrideIn(BaseModel):
    node_id: str = Field(default="basement", pattern=r"^[a-z0-9_]+$")
    # Signed minutes to add to the current override window. Negative shortens it.
    minutes: int = Field(ge=-1440, le=1440)


class FanOverrideOut(BaseModel):
    node_id: str
    active: bool
    expires_at: Optional[datetime] = None
    remaining_seconds: int = 0
