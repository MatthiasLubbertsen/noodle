import datetime
import logging

from bot.httpjson import get_json
from bot.interests import is_avoided
from bot.tz import AMSTERDAM_TZ

logger = logging.getLogger("noodle")

# public holidays from nager.date (no key): the dutch ones go into the morning
# greeting, any country is available through the public_holidays tool
BASE_URL = "https://date.nager.at/api/v3"

# (year, country) -> holidays; a year's holidays don't change, so cache them
_CACHE: dict = {}


def holidays(year: int, country: str = "NL") -> list[dict]:
    """raises on failure (unknown country, network) - callers decide how to degrade."""
    key = (int(year), country.strip().upper())
    if key not in _CACHE:
        _CACHE[key] = get_json(f"{BASE_URL}/PublicHolidays/{key[0]}/{key[1]}")
    return _CACHE[key]


def describe(h: dict) -> str:
    day = datetime.date.fromisoformat(h["date"])
    line = f"{h['date']} ({day.strftime('%A').lower()}): {h['name']}"
    if h.get("localName") and h["localName"] != h["name"]:
        line += f" ({h['localName']})"
    # nager also lists days that are only off for schools/government, or just observed
    line += ", public holiday (day off)" if "Public" in (h.get("types") or []) else ", observance, not a day off"
    if not h.get("global", True) and h.get("counties"):
        line += f", only in {', '.join(h['counties'])}"
    return line


def today_nl() -> str | None:
    """today's dutch holiday, described, or None (none today, or the lookup failed)."""
    today = datetime.datetime.now(AMSTERDAM_TZ).date()
    try:
        days = holidays(today.year)
    except Exception:  # noqa: BLE001
        logger.exception("failed to fetch dutch holidays")
        return None
    return next(
        (
            describe(h) for h in days
            if h["date"] == today.isoformat() and not is_avoided(f"{h['name']} {h.get('localName', '')}")
        ),
        None,
    )


def holidays_text(country: str = "NL", year: int | None = None) -> str:
    """a whole year's holidays, or (no year) the next 12 months'. returns an
    error string instead of raising."""
    country = (country or "NL").strip().upper()
    today = datetime.datetime.now(AMSTERDAM_TZ).date()
    try:
        if year:
            days = holidays(year, country)
        else:
            end = today + datetime.timedelta(days=365)
            days = [
                h for y in (today.year, today.year + 1) for h in holidays(y, country)
                if today.isoformat() <= h["date"] <= end.isoformat()
            ]
    except Exception:  # noqa: BLE001
        logger.exception("holiday lookup failed for %s %s", country, year)
        return f"couldn't get holidays for {country!r} (is it a 2-letter country code?)"
    if not days:
        return f"no holidays found for {country}"
    header = f"holidays in {country} " + (f"in {year}" if year else f"from today ({today.isoformat()}) for the next 12 months")
    return header + ":\n" + "\n".join(describe(h) for h in days)
