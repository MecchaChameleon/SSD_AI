from __future__ import annotations

import asyncio
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import unquote

import httpx


KST = timezone(timedelta(hours=9))
KMA_BASE_URL = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0"
JEJU_BOUNDS = (32.9, 33.7, 125.9, 127.1)
_CACHE: dict[tuple[str, int, int, str, str], tuple[float, list[dict[str, Any]]]] = {}
_CACHE_TTL_SECONDS = 600


@dataclass
class WeatherContext:
    temperature: float = 20.0
    precipitation: float = 0.0
    wind_speed: float = 3.0
    summary: str = "날씨 정보 없음"
    forecast_temperature: float | None = None
    forecast_precipitation: float = 0.0
    forecast_wind_speed: float = 3.0
    source: str = "fallback"
    observed_at: str | None = None


def is_jeju(latitude: float, longitude: float) -> bool:
    min_lat, max_lat, min_lon, max_lon = JEJU_BOUNDS
    return min_lat <= latitude <= max_lat and min_lon <= longitude <= max_lon


def to_kma_grid(latitude: float, longitude: float) -> tuple[int, int]:
    """Convert WGS84 coordinates to the KMA 5 km Lambert grid."""
    re = 6371.00877 / 5.0
    slat1, slat2, olon, olat = map(math.radians, (30.0, 60.0, 126.0, 38.0))
    sn = math.log(math.cos(slat1) / math.cos(slat2)) / math.log(
        math.tan(math.pi * 0.25 + slat2 * 0.5) / math.tan(math.pi * 0.25 + slat1 * 0.5)
    )
    sf = math.tan(math.pi * 0.25 + slat1 * 0.5) ** sn * math.cos(slat1) / sn
    ro = re * sf / math.tan(math.pi * 0.25 + olat * 0.5) ** sn
    ra = re * sf / math.tan(math.pi * 0.25 + math.radians(latitude) * 0.5) ** sn
    theta = math.radians(longitude) - olon
    if theta > math.pi:
        theta -= 2.0 * math.pi
    if theta < -math.pi:
        theta += 2.0 * math.pi
    theta *= sn
    return int(ra * math.sin(theta) + 43.0 + 1.5), int(ro - ra * math.cos(theta) + 136.0 + 1.5)


def _base_time(now: datetime, forecast: bool) -> datetime:
    # 실황 기준시각은 HH00(약 HH40 제공), 예보 기준시각은 HH30(약 HH45 제공)이다.
    if forecast:
        base = now.replace(minute=30, second=0, microsecond=0)
        return base if now.minute >= 45 else base - timedelta(hours=1)
    base = now.replace(minute=0, second=0, microsecond=0)
    return base if now.minute >= 40 else base - timedelta(hours=1)


async def _kma_items(endpoint: str, nx: int, ny: int, base: datetime) -> list[dict[str, Any]]:
    cache_key = (endpoint, nx, ny, base.strftime("%Y%m%d"), base.strftime("%H%M"))
    cached = _CACHE.get(cache_key)
    if cached and time.monotonic() - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    service_key = os.getenv("DATA_GO_KR_KEY", "").strip()
    if not service_key:
        raise RuntimeError("DATA_GO_KR_KEY is not configured")
    params = {
        "serviceKey": unquote(service_key),
        "pageNo": 1,
        "numOfRows": 1000,
        "dataType": "JSON",
        "base_date": base.strftime("%Y%m%d"),
        "base_time": base.strftime("%H%M"),
        "nx": nx,
        "ny": ny,
    }
    async with httpx.AsyncClient(timeout=4.0) as client:
        response = await client.get(f"{KMA_BASE_URL}/{endpoint}", params=params)
        response.raise_for_status()
        payload = response.json().get("response", {})
    header = payload.get("header", {})
    if header.get("resultCode") != "00":
        raise RuntimeError(f"KMA API error: {header.get('resultCode')} {header.get('resultMsg')}")
    items = payload.get("body", {}).get("items", {}).get("item", []) or []
    _CACHE[cache_key] = (time.monotonic(), items)
    return items


def _float(value: Any, default: float = 0.0) -> float:
    try:
        text = str(value).replace("강수없음", "0").replace("mm", "").strip()
        if text.startswith("1.0mm 미만"):
            return 0.5
        return float(text)
    except (TypeError, ValueError):
        return default


def _category_map(items: list[dict[str, Any]]) -> dict[str, float]:
    return {str(item.get("category")): _float(item.get("obsrValue")) for item in items}


def _forecast_values(items: list[dict[str, Any]], now: datetime) -> tuple[float | None, float, float]:
    by_time: dict[tuple[str, str], dict[str, float]] = {}
    for item in items:
        key = (str(item.get("fcstDate", "")), str(item.get("fcstTime", "")))
        by_time.setdefault(key, {})[str(item.get("category"))] = _float(item.get("fcstValue"))
    horizon = now + timedelta(hours=3)
    upcoming = []
    for (date, clock), values in by_time.items():
        try:
            at = datetime.strptime(date + clock.zfill(4), "%Y%m%d%H%M").replace(tzinfo=KST)
        except ValueError:
            continue
        if now - timedelta(minutes=10) <= at <= horizon:
            upcoming.append((at, values))
    upcoming.sort(key=lambda row: row[0])
    temperatures = [values["T1H"] for _, values in upcoming if "T1H" in values]
    rains = [values.get("RN1", 0.0) for _, values in upcoming]
    winds = [values.get("WSD", 0.0) for _, values in upcoming]
    return (temperatures[-1] if temperatures else None, max(rains, default=0.0), max(winds, default=0.0))


async def _kma_weather(latitude: float, longitude: float) -> WeatherContext:
    now = datetime.now(KST)
    nx, ny = to_kma_grid(latitude, longitude)
    current_items, forecast_items = await asyncio.gather(
        _kma_items("getUltraSrtNcst", nx, ny, _base_time(now, False)),
        _kma_items("getUltraSrtFcst", nx, ny, _base_time(now, True)),
    )
    current = _category_map(current_items)
    forecast_temp, forecast_rain, forecast_wind = _forecast_values(forecast_items, now)
    temperature = current.get("T1H", 20.0)
    precipitation = current.get("RN1", 0.0)
    wind = current.get("WSD", 3.0)
    summary = f"현재 {temperature:.1f}°C · 강수 {precipitation:.1f}mm · 바람 {wind:.1f}m/s"
    return WeatherContext(
        temperature=temperature,
        precipitation=precipitation,
        wind_speed=wind,
        summary=summary,
        forecast_temperature=forecast_temp,
        forecast_precipitation=forecast_rain,
        forecast_wind_speed=forecast_wind,
        source="KMA_ULTRA_SHORT",
        observed_at=now.isoformat(),
    )


async def _open_meteo_weather(latitude: float, longitude: float) -> WeatherContext:
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": "temperature_2m,precipitation,wind_speed_10m",
        "timezone": "Asia/Seoul",
    }
    async with httpx.AsyncClient(timeout=2.5) as client:
        response = await client.get("https://api.open-meteo.com/v1/forecast", params=params)
        response.raise_for_status()
        current = response.json().get("current", {})
    rain = _float(current.get("precipitation"))
    wind = _float(current.get("wind_speed_10m"), 10.8) / 3.6
    temp = _float(current.get("temperature_2m"), 20.0)
    return WeatherContext(
        temp, rain, wind, f"현재 {temp:.1f}°C · 강수 {rain:.1f}mm · 바람 {wind:.1f}m/s",
        source="OPEN_METEO_FALLBACK", observed_at=datetime.now(KST).isoformat(),
    )


async def current_weather(latitude: float | None, longitude: float | None) -> WeatherContext:
    if latitude is None or longitude is None or not is_jeju(latitude, longitude):
        return WeatherContext(summary="제주 지역 좌표 없음")
    try:
        return await _kma_weather(latitude, longitude)
    except Exception:
        try:
            return await _open_meteo_weather(latitude, longitude)
        except Exception:
            return WeatherContext()
