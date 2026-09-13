import datetime
import logging
from zoneinfo import ZoneInfo

import config
from bot import state

logger = logging.getLogger("noodle")


def _today_str() -> str:
    tz = ZoneInfo(config.DAILY_STATS_TZ)
    return datetime.datetime.now(tz).strftime("%Y-%m-%d")


def messages_sent_today(user_id: str) -> int:
    """count how many slack messages `user_id` has sent today, via slack search.

    returns -1 if the search itself failed (so callers can tell "0 messages"
    apart from "couldn't check").
    """
    query = f"from:<@{user_id}> on:{_today_str()}"
    try:
        resp = state.app.client.search_messages(query=query, count=1)
        if not resp.get("ok"):
            logger.warning("daily stats search failed: %s", resp.get("error"))
            return -1
        messages = resp.get("messages") or {}
        total = messages.get("total")
        if total is None:
            total = len(messages.get("matches", []))
        return total
    except Exception:  # noqa: BLE001
        logger.exception("daily stats search failed")
        return -1


def build_daily_summary(user_id: str) -> str:
    count = messages_sent_today(user_id)
    if count < 0:
        return (
            "i tried to pull today's slack stats but the search hiccuped, "
            "sorry. try asking me again in a bit."
        )
    if count == 0:
        return "quick check in: looks like you haven't sent any messages on slack yet today."
    plural = "message" if count == 1 else "messages"
    return (
        f"end of day check in: you've sent {count} {plural} on slack today. "
        "hope it was a good one."
    )
