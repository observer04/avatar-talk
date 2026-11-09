"""Everyday tools that need no API key."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp
from livekit.agents import RunContext, ToolError, function_tool

from agent.tools import filler

_WEATHER_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 71: "light snow", 73: "snow",
    75: "heavy snow", 80: "rain showers", 81: "heavy rain showers", 82: "violent rain showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm with hail",
}


@function_tool
async def get_current_time(context: RunContext, timezone: str = "UTC") -> dict:
    """Get the current date and time.

    Args:
        timezone: IANA timezone name, e.g. "Asia/Kolkata" or "America/New_York".
    """
    try:
        now = datetime.now(ZoneInfo(timezone))
    except ZoneInfoNotFoundError as e:
        raise ToolError(f"Unknown timezone {timezone!r}") from e
    return {"timezone": timezone, "datetime": now.strftime("%A, %d %B %Y, %I:%M %p")}


@function_tool
async def get_weather(context: RunContext, city: str) -> dict:
    """Get the current weather for a city.

    Args:
        city: City name, e.g. "Bhubaneswar" or "London".
    """
    filler(context)
    timeout = aiohttp.ClientTimeout(total=8)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(
            "https://geocoding-api.open-meteo.com/v1/search", params={"name": city, "count": 1}
        ) as r:
            geo = await r.json()
        if not geo.get("results"):
            raise ToolError(f"Couldn't find a place called {city!r}")
        place = geo["results"][0]
        async with session.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
            },
        ) as r:
            current = (await r.json())["current"]
    return {
        "place": f"{place['name']}, {place.get('country', '')}".strip(", "),
        "temperature_c": current["temperature_2m"],
        "feels_like_c": current["apparent_temperature"],
        "conditions": _WEATHER_CODES.get(current["weather_code"], "unknown"),
        "wind_kmh": current["wind_speed_10m"],
    }


TOOLS = [get_current_time, get_weather]
