"""Weather endpoints. The router stays dumb — all cache/refresh/failure logic
lives in WeatherService."""
from fastapi import APIRouter, HTTPException

from app.schemas import WeatherCurrentOut, WeatherOut
from app.services.weather import (
    WeatherDisabledError,
    WeatherUnavailableError,
    weather_service,
)

router = APIRouter(prefix="/api/weather", tags=["weather"])


@router.get("", response_model=WeatherOut)
async def get_weather():
    try:
        return await weather_service.get_weather()
    except WeatherDisabledError:
        raise HTTPException(status_code=404, detail="Weather is not enabled")
    except WeatherUnavailableError:
        raise HTTPException(status_code=503, detail="Weather unavailable")


@router.get("/current", response_model=WeatherCurrentOut)
async def get_weather_current():
    try:
        return await weather_service.get_current()
    except WeatherDisabledError:
        raise HTTPException(status_code=404, detail="Weather is not enabled")
    except WeatherUnavailableError:
        raise HTTPException(status_code=503, detail="Weather unavailable")
