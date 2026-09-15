import datetime
import json
import logging
import urllib.error
import urllib.request

from bot.tz import AMSTERDAM_TZ

logger = logging.getLogger("noodle")

BASE_URL = "https://en.wikipedia.org/api/rest_v1/feed/onthisday"
HEADERS = {"User-Agent": "noodle-slack-bot/1.0 (matthiaslubbertsen@gmail.com)"}


def _get_json(path: str):
    req = urllib.request.Request(BASE_URL + path, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _today_month_day() -> tuple[int, int]:
    now = datetime.datetime.now(AMSTERDAM_TZ)
    return now.month, now.day


def fun_holidays_today() -> list[str]:
    """quirky/named 'days' today (e.g. 'Roald Dahl Day'), via wikipedia.

    religious feast days are filtered out since they exist for nearly every
    date and drown out the actually fun/notable ones.
    """
    month, day = _today_month_day()
    try:
        data = _get_json(f"/holidays/{month}/{day}")
    except Exception:  # noqa: BLE001
        logger.exception("failed to fetch onthisday holidays")
        return []
    names = []
    for entry in data.get("holidays", []):
        text = (entry.get("text") or "").strip()
        if not text or text.lower().startswith("christian feast day"):
            continue
        # some entries are "label:\nname" - keep just the name
        names.append(text.splitlines()[-1].strip())
    return names


def history_blurb_today() -> str | None:
    """one notable 'on this day in history' anniversary, via wikipedia."""
    month, day = _today_month_day()
    try:
        data = _get_json(f"/selected/{month}/{day}")
    except Exception:  # noqa: BLE001
        logger.exception("failed to fetch onthisday selected events")
        return None
    selected = data.get("selected") or []
    if not selected:
        return None
    pick = selected[0]
    text = (pick.get("text") or "").strip()
    if not text:
        return None
    year = pick.get("year")
    return f"{year}: {text}" if year else text
