from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Postgres connection. In Docker this is set from POSTGRES_* via DATABASE_URL.
    database_url: str = "postgresql+psycopg://homeops:homeops@localhost:5432/homeops"

    # MicroPython's time.time() counts seconds since the MicroPython epoch
    # (2000-01-01 00:00:00 UTC), not the Unix epoch (1970-01-01). Add this
    # offset on ingest so stored timestamps are real Unix seconds.
    micropy_epoch_offset: int = 946684800

    # Directory of the built Vue frontend (served by this app).
    frontend_dist: Path = Path(__file__).resolve().parents[2] / "frontend" / "dist"

    # A node is considered "alive" if it reported within this many seconds.
    # The device POSTs on a 5-min heartbeat, so default to ~2x that to absorb
    # network jitter without false negatives.
    node_alive_seconds: int = 600

    # --- OpenWeatherMap (backend is the single weather authority) ---
    # Weather is an optional feature. When enabled, the key + coordinates are
    # required and validated at startup (no silent 0.0 / empty sentinels).
    weather_enabled: bool = False
    owm_api_key: str = ""
    owm_lat: float | None = None
    owm_lon: float | None = None
    # Freshness TTL: serve cached weather without touching OWM.
    weather_cache_ttl: int = 600
    # Max stale age: beyond this, refuse to serve old weather (503).
    weather_max_stale: int = 21600

    @model_validator(mode="after")
    def _validate_weather(self):
        if self.weather_enabled:
            missing = []
            if not self.owm_api_key:
                missing.append("OWM_API_KEY")
            if self.owm_lat is None:
                missing.append("OWM_LAT")
            if self.owm_lon is None:
                missing.append("OWM_LON")
            if missing:
                raise ValueError(
                    "Weather is enabled but missing required settings: "
                    + ", ".join(missing)
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
