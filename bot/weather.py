import logging

import config
from bot.httpjson import get_json

logger = logging.getLogger("noodle")

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# open-meteo's wmo weather codes -> plain words
_WMO = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "foggy",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light showers", 81: "showers", 82: "heavy showers",
    85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail",
}

# name -> (lat, lon, display name); geocoding results never change, so cache
_GEO_CACHE: dict = {}


def _geocode(name: str):
    key = name.strip().lower()
    if key in _GEO_CACHE:
        return _GEO_CACHE[key]
    data = get_json(GEOCODE_URL, {"name": name, "count": 1, "language": "en", "countryCode": "NL"})
    results = data.get("results") or []
    if not results:
        # not in the netherlands (or a typo) - retry worldwide before giving up
        data = get_json(GEOCODE_URL, {"name": name, "count": 1, "language": "en"})
        results = data.get("results") or []
    if not results:
        return None
    r = results[0]
    _GEO_CACHE[key] = (r["latitude"], r["longitude"], r.get("name", name))
    return _GEO_CACHE[key]


def _fmt_day(label: str, d: dict, i: int) -> str:
    desc = _WMO.get(d["weather_code"][i], "unknown")
    line = f"{label}: {round(d['temperature_2m_min'][i])}-{round(d['temperature_2m_max'][i])}°c, {desc}"
    rain = d["precipitation_probability_max"][i]
    mm = d["precipitation_sum"][i]
    if rain is not None and rain >= 20:
        line += f", {rain}% chance of rain ({mm:.1f}mm)"
    return line + f", wind up to {round(d['wind_speed_10m_max'][i])} km/h"


def forecast_text(location: str, days: int = 1, label: str | None = None) -> str:
    """a plain-text forecast for one place: right now + the next `days` days
    (today included). `label` replaces the place name in the output (and in
    errors) so a private place can be reported without naming it. returns an
    error string instead of raising."""
    days = max(1, min(int(days or 1), 7))
    try:
        geo = _geocode(location)
        if not geo:
            return f"couldn't find a place called {(label or location)!r}"
        lat, lon, name = geo
        name = label or name
        data = get_json(
            FORECAST_URL,
            {
                "latitude": lat,
                "longitude": lon,
                "timezone": "Europe/Amsterdam",
                "forecast_days": days,
                "current": "temperature_2m,apparent_temperature,weather_code",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                "precipitation_sum,precipitation_probability_max,wind_speed_10m_max",
            },
        )
    except Exception:  # noqa: BLE001
        logger.exception("weather lookup failed for %r", location)
        return f"weather lookup for {label or location} failed"
    cur, daily = data["current"], data["daily"]
    lines = [
        f"{name}: now {round(cur['temperature_2m'])}°c (feels {round(cur['apparent_temperature'])}°c), "
        f"{_WMO.get(cur['weather_code'], 'unknown')}"
    ]
    for i, day in enumerate(daily["time"]):
        lines.append("  " + _fmt_day("today" if i == 0 else day, daily, i))
    return "\n".join(lines)


def morning_weather_facts() -> str | None:
    """plain-text forecast lines for every configured location (the facts the
    morning dm's weather message is written from), or None if nothing could
    be fetched."""
    lines = []
    for place in config.WEATHER_LOCATIONS:
        try:
            geo = _geocode(place)
            if not geo:
                continue
            lat, lon, name = geo
            data = get_json(
                FORECAST_URL,
                {
                    "latitude": lat,
                    "longitude": lon,
                    "timezone": "Europe/Amsterdam",
                    "forecast_days": 1,
                    "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                    "precipitation_sum,precipitation_probability_max,wind_speed_10m_max",
                },
            )
            lines.append("- " + _fmt_day(name.lower(), data["daily"], 0))
        except Exception:  # noqa: BLE001
            logger.exception("morning weather failed for %r", place)
    if not lines:
        return None
    return "\n".join(lines)
