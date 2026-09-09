"""WeatherService unit tests.

Uses an injectable stub transport so no network is touched. The service reads
`settings.weather_enabled` at call time, so tests enable it on the module-level
singleton and restore it afterward.
"""
import asyncio
import time

import httpx
import pytest

from app.config import settings
from app.services.weather import (
    WeatherDisabledError,
    WeatherService,
    WeatherUnavailableError,
    calculate_ah,
)

# A realistic OWM payload (units=metric).
OWM_OK = {
    "main": {"temp": 21.5, "humidity": 60.0},
    "weather": [{"description": "scattered clouds", "icon": "03d"}],
    "wind": {"speed": 3.1},
    "clouds": {"all": 40},
    "dt": 1700000000,
}

OWM_URL = "https://api.openweathermap.org/data/2.5/weather"


def _resp(status=200, json=None):
    """Build an httpx.Response with a request attached so raise_for_status works."""
    return httpx.Response(
        status,
        json=json if json is not None else OWM_OK,
        request=httpx.Request("GET", OWM_URL),
    )


class StubTransport:
    """Configurable stub implementing the WeatherTransport protocol."""

    def __init__(self, responses=None, fail=None):
        # responses: list of httpx.Response to pop in order; last one repeats.
        self._responses = list(responses or [])
        self._fail = fail  # exception to raise, or None
        self.calls = 0

    async def get(self, url, params):
        self.calls += 1
        if self._fail is not None:
            raise self._fail
        if self._responses:
            resp = self._responses.pop(0)
            if self._responses:
                self._responses.append(resp)  # repeat last
        else:
            resp = _resp()
        return resp


@pytest.fixture(autouse=True)
def _enable_weather(monkeypatch):
    monkeypatch.setattr(settings, "weather_enabled", True)
    monkeypatch.setattr(settings, "owm_api_key", "test-key")
    monkeypatch.setattr(settings, "owm_lat", 51.1)
    monkeypatch.setattr(settings, "owm_lon", 17.0)
    yield
    monkeypatch.setattr(settings, "weather_enabled", False)


def _service(transport, ttl=600, max_stale=21600):
    return WeatherService(transport=transport, cache_ttl=ttl, max_stale=max_stale)


# --- AH math ---
def test_calculate_ah():
    # At 21.5 C / 60% RH, AH should be ~11.2 g/m3.
    ah = calculate_ah(21.5, 60.0)
    assert 10.5 < ah < 12.0


# --- disabled ---
@pytest.mark.asyncio
async def test_disabled_raises(monkeypatch):
    monkeypatch.setattr(settings, "weather_enabled", False)
    svc = _service(StubTransport())
    with pytest.raises(WeatherDisabledError):
        await svc.get_weather()


# --- empty cache + success -> fresh ---
@pytest.mark.asyncio
async def test_empty_cache_success_fresh():
    svc = _service(StubTransport())
    w = await svc.get_weather()
    assert w.temperature_c == 21.5
    assert w.relative_humidity_pct == 60.0
    assert w.absolute_humidity_g_m3 == pytest.approx(calculate_ah(21.5, 60.0))
    assert w.description == "scattered clouds"
    assert w.icon == "03d"
    assert w.wind_speed_mps == 3.1
    assert w.cloud_pct == 40
    assert w.stale is False
    assert w.observed_at is not None


# --- fresh cache -> no OWM request ---
@pytest.mark.asyncio
async def test_fresh_cache_no_request():
    transport = StubTransport()
    svc = _service(transport)
    await svc.get_weather()
    first_calls = transport.calls
    await svc.get_weather()
    assert transport.calls == first_calls  # served from cache


# --- expired cache + success -> new weather ---
@pytest.mark.asyncio
async def test_expired_cache_success_refreshes(monkeypatch):
    transport = StubTransport()
    svc = _service(transport, ttl=10, max_stale=100)
    await svc.get_weather()

    # Age the cache past TTL.
    monkeypatch.setattr(svc, "_fetched_at", time.time() - 20)
    calls_before = transport.calls
    w = await svc.get_weather()
    assert transport.calls == calls_before + 1
    assert w.stale is False


# --- expired cache + failure -> stale served ---
@pytest.mark.asyncio
async def test_expired_cache_failure_serves_stale(monkeypatch):
    transport = StubTransport()
    svc = _service(transport, ttl=10, max_stale=100)
    await svc.get_weather()

    monkeypatch.setattr(svc, "_fetched_at", time.time() - 20)
    transport._fail = httpx.ConnectError("boom")
    w = await svc.get_weather()
    assert w.stale is True  # stale but within max_stale -> served


# --- empty cache + failure -> 503 (WeatherUnavailableError) ---
@pytest.mark.asyncio
async def test_empty_cache_failure_unavailable():
    transport = StubTransport(fail=httpx.ConnectError("boom"))
    svc = _service(transport)
    with pytest.raises(WeatherUnavailableError):
        await svc.get_weather()


# --- cache past max_stale + failure -> unavailable ---
@pytest.mark.asyncio
async def test_past_max_stale_failure_unavailable(monkeypatch):
    transport = StubTransport()
    svc = _service(transport, ttl=10, max_stale=100)
    await svc.get_weather()

    monkeypatch.setattr(svc, "_fetched_at", time.time() - 200)  # > max_stale
    transport._fail = httpx.ConnectError("boom")
    with pytest.raises(WeatherUnavailableError):
        await svc.get_weather()


# --- concurrent refreshes -> single OWM request ---
@pytest.mark.asyncio
async def test_concurrent_single_request():
    transport = StubTransport()
    svc = _service(transport)
    results = await asyncio.gather(*[svc.get_weather() for _ in range(10)])
    assert transport.calls == 1
    assert all(r.temperature_c == 21.5 for r in results)


# --- malformed OWM response -> controlled failure ---
@pytest.mark.asyncio
async def test_malformed_response_unavailable():
    transport = StubTransport(responses=[_resp(json={"main": {}})])
    svc = _service(transport)
    with pytest.raises(WeatherUnavailableError):
        await svc.get_weather()


# --- OWM 401/403 -> controlled failure ---
@pytest.mark.asyncio
async def test_http_error_unavailable():
    transport = StubTransport(responses=[_resp(401, {"message": "bad key"})])
    svc = _service(transport)
    with pytest.raises(WeatherUnavailableError):
        await svc.get_weather()


# --- get_current projection contract ---
@pytest.mark.asyncio
async def test_get_current_projection():
    svc = _service(StubTransport())
    cur = await svc.get_current()
    assert cur.temperature_c == 21.5
    assert cur.relative_humidity_pct == 60.0
    assert cur.absolute_humidity_g_m3 == pytest.approx(calculate_ah(21.5, 60.0))
    assert cur.fetched_at is not None
    assert cur.stale is False
    # ESP32 contract: no extra fields beyond the minimal projection.
    assert set(cur.model_dump().keys()) == {
        "temperature_c",
        "relative_humidity_pct",
        "absolute_humidity_g_m3",
        "fetched_at",
        "stale",
    }
