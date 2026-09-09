"""WeatherService: backend is the single weather authority.

Fetches OpenWeatherMap once, caches the normalized result in memory, and serves
it to both the Vue dashboard and every ESP32. Owns cache freshness, a refresh
lock (anti-stampede), AH calculation, stale-cache policy, and logging — so the
HTTP router stays dumb.

The transport is injectable for tests (see `_OWMTransport`); production uses an
httpx.AsyncClient created in the FastAPI lifespan and passed in via `set_client`.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import datetime, timezone
from typing import Protocol

import httpx

from app.config import settings
from app.schemas import WeatherCurrentOut, WeatherOut

logger = logging.getLogger("weather")

OWM_URL = "https://api.openweathermap.org/data/2.5/weather"


class WeatherTransport(Protocol):
    """Minimal async transport so tests can stub OWM without the network."""

    async def get(self, url: str, params: dict) -> httpx.Response: ...


class _OWMTransport:
    """Real transport backed by an httpx.AsyncClient."""

    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    async def get(self, url: str, params: dict) -> httpx.Response:
        return await self._client.get(url, params=params)


def calculate_ah(temp_c: float, rh_pct: float) -> float:
    """Absolute humidity (g/m3) from the Magnus equation."""
    es = 6.112 * math.exp((17.67 * temp_c) / (243.5 + temp_c))
    e = es * (rh_pct / 100.0)
    return (e * 216.74) / (273.15 + temp_c)


class WeatherService:
    """Cached, serialized, normalized access to current weather.

    Cache semantics (separate concepts):
      - fresh: served without touching OWM (age <= cache_ttl)
      - stale: age in (cache_ttl, max_stale]; refresh attempted, stale served on failure
      - expired: age > max_stale; never served, 503 instead
    """

    def __init__(
        self,
        *,
        transport: WeatherTransport | None = None,
        cache_ttl: int | None = None,
        max_stale: int | None = None,
    ):
        self._transport = transport
        self._cache_ttl = cache_ttl if cache_ttl is not None else settings.weather_cache_ttl
        self._max_stale = max_stale if max_stale is not None else settings.weather_max_stale
        self._lock = asyncio.Lock()

        self._data: dict | None = None
        self._fetched_at: float | None = None
        self._last_attempt: float | None = None
        self._last_error: str | None = None

    # --- lifecycle ---
    def set_client(self, client: httpx.AsyncClient) -> None:
        """Attach the app-level AsyncClient (called from FastAPI lifespan)."""
        self._transport = _OWMTransport(client)

    # --- public API ---
    async def get_weather(self) -> WeatherOut:
        if not settings.weather_enabled:
            raise WeatherDisabledError()
        if self._fresh():
            return self._serve()
        async with self._lock:
            # Double-check after acquiring the lock: another caller may have
            # refreshed while we waited, so we don't stampede OWM.
            if self._fresh():
                return self._serve()
            await self._refresh()
        return self._serve()

    async def get_current(self) -> WeatherCurrentOut:
        full = await self.get_weather()
        return WeatherCurrentOut(
            temperature_c=full.temperature_c,
            relative_humidity_pct=full.relative_humidity_pct,
            absolute_humidity_g_m3=full.absolute_humidity_g_m3,
            fetched_at=full.fetched_at,
            stale=full.stale,
        )

    # --- internals ---
    def _age(self) -> float | None:
        if self._fetched_at is None:
            return None
        return time.time() - self._fetched_at

    def _fresh(self) -> bool:
        age = self._age()
        return age is not None and age <= self._cache_ttl

    def _serve(self) -> WeatherOut:
        age = self._age()
        stale = age is not None and age > self._cache_ttl
        return WeatherOut(**self._data, stale=stale)

    async def _refresh(self) -> None:
        self._last_attempt = time.time()
        try:
            data = await self._fetch_owm()
        except Exception as exc:  # noqa: BLE001 - any transport/parse failure
            self._last_error = repr(exc)
            logger.warning("[WEATHER] refresh failed: %s; serving stale cache", exc)
            # If we have no cache at all, or the cache is beyond max_stale,
            # there is nothing safe to serve -> raise so the router returns 503.
            if self._data is None or self._age() is None or self._age() > self._max_stale:
                raise WeatherUnavailableError() from exc
            return
        self._data = data
        self._fetched_at = time.time()
        self._last_error = None
        logger.info("[WEATHER] refreshed successfully")

    async def _fetch_owm(self) -> dict:
        if self._transport is None:
            raise WeatherUnavailableError("weather transport not configured")
        resp = await self._transport.get(
            OWM_URL,
            params={
                "lat": settings.owm_lat,
                "lon": settings.owm_lon,
                "appid": settings.owm_api_key,
                "units": "metric",
            },
        )
        resp.raise_for_status()
        raw = resp.json()
        return self._normalize(raw)

    def _normalize(self, raw: dict) -> dict:
        """Map OWM's response onto the canonical, self-describing model."""
        main = raw.get("main") or {}
        weather = (raw.get("weather") or [{}])[0]
        wind = raw.get("wind") or {}
        clouds = raw.get("clouds") or {}

        temp_c = main.get("temp")
        rh_pct = main.get("humidity")
        if temp_c is None or rh_pct is None:
            raise ValueError("OWM response missing main.temp/main.humidity")

        observed_at = None
        if raw.get("dt"):
            observed_at = datetime.fromtimestamp(raw["dt"], tz=timezone.utc)

        return {
            "temperature_c": float(temp_c),
            "relative_humidity_pct": float(rh_pct),
            "absolute_humidity_g_m3": calculate_ah(float(temp_c), float(rh_pct)),
            "description": weather.get("description", ""),
            "icon": weather.get("icon", ""),
            "wind_speed_mps": wind.get("speed"),
            "cloud_pct": clouds.get("all"),
            "fetched_at": datetime.now(timezone.utc),
            "observed_at": observed_at,
        }


class WeatherDisabledError(Exception):
    """Weather feature is disabled in settings."""


class WeatherUnavailableError(Exception):
    """No fresh-or-stale weather can be served (empty cache or past max_stale)."""


weather_service = WeatherService()
