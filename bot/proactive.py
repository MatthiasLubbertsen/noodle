import datetime
import json
import logging

import config
from bot.tz import AMSTERDAM_TZ

logger = logging.getLogger("noodle")

# unprompted pings are the one place noodle could get annoying, so they all
# share two guardrails: quiet hours (held until they end) and a daily cap.
# fixed-time dms (morning, wrap-up, weekly) are expected, so they skip both.


def _parse(value: str) -> datetime.time | None:
    try:
        h, m = value.strip().split(":")
        return datetime.time(int(h), int(m))
    except Exception:  # noqa: BLE001
        return None


def in_quiet_hours(now: datetime.datetime | None = None) -> bool:
    """is it inside QUIET_HOURS ('HH:MM-HH:MM', may wrap past midnight)?"""
    try:
        start_s, end_s = config.QUIET_HOURS.split("-")
        start, end = _parse(start_s), _parse(end_s)
    except Exception:  # noqa: BLE001
        return False
    if not start or not end:
        return False
    t = (now or datetime.datetime.now(AMSTERDAM_TZ)).time()
    return start <= t < end if start <= end else (t >= start or t < end)


def _load() -> dict:
    try:
        return json.loads(config.PROACTIVE_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save(data: dict) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.PROACTIVE_STATE_PATH.write_text(json.dumps(data), encoding="utf-8")


def _today() -> str:
    return datetime.datetime.now(AMSTERDAM_TZ).strftime("%Y-%m-%d")


def can_send(ignore_quiet_hours: bool = False) -> bool:
    """under today's cap of unprompted dms and outside quiet hours? (ignore_quiet_hours
    is for the aurora alert: it's only worth sending when it's dark, i.e. often at night.)"""
    if not ignore_quiet_hours and in_quiet_hours():
        return False
    data = _load()
    return data.get("day") != _today() or data.get("count", 0) < config.PROACTIVE_DAILY_CAP


def record_sent() -> None:
    data = _load()
    if data.get("day") != _today():
        data = {"day": _today(), "count": 0}
    data["count"] = data.get("count", 0) + 1
    _save(data)
