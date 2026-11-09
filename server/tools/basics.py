"""Everyday tools that need no API key."""

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp
from pipecat.services.llm_service import FunctionCallParams

from tools import slow

_WEATHER_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 71: "light snow", 73: "snow",
    75: "heavy snow", 80: "rain showers", 81: "heavy rain showers", 82: "violent rain showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm with hail",
}


async def get_current_time(params: FunctionCallParams, timezone: str = "UTC"):
    """Get the current date and time.

    Args:
        timezone: IANA timezone name, e.g. "Asia/Kolkata" or "America/New_York". Defaults to UTC.
    """
    try:
        now = datetime.now(ZoneInfo(timezone))
    except ZoneInfoNotFoundError:
        await params.result_callback({"error": f"Unknown timezone {timezone!r}"})
        return
    await params.result_callback(
        {"timezone": timezone, "datetime": now.strftime("%A, %d %B %Y, %I:%M %p")}
    )


@slow
async def get_weather(params: FunctionCallParams, city: str):
    """Get the current weather for a city (via the free Open-Meteo API).

    Args:
        city: City name, e.g. "Bhubaneswar" or "London".
    """
    timeout = aiohttp.ClientTimeout(total=8)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1},
        ) as r:
            geo = await r.json()
        if not geo.get("results"):
            await params.result_callback({"error": f"Couldn't find a place called {city!r}"})
            return
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

    await params.result_callback(
        {
            "place": f"{place['name']}, {place.get('country', '')}".strip(", "),
            "temperature_c": current["temperature_2m"],
            "feels_like_c": current["apparent_temperature"],
            "conditions": _WEATHER_CODES.get(current["weather_code"], "unknown"),
            "wind_kmh": current["wind_speed_10m"],
        }
    )


TOOLS = [get_current_time, get_weather]
