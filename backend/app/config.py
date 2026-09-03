from functools import lru_cache
from pathlib import Path

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


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
