"""hack club's "50 days" challenge: link each @matthias-day recap into
matthias's goal thread there. slack unfurls the link into an embed of the
recap, so the update itself stays in his own channel. off unless
FIFTY_DAYS_THREAD is set. posted by noodle (the organizer okayed that).
"""

import datetime
import logging
import re

import config
from bot.matthias_day import _HAS_BULLET_RE, clean_ping_text
from bot.tz import AMSTERDAM_TZ

logger = logging.getLogger("noodle")

_LINK_RE = re.compile(r"/archives/(C[A-Z0-9]+)/p(\d{10})(\d{6})")


def target() -> tuple[str, str] | None:
    """(channel, thread_ts) of the goal message, from its slack link or a
    plain "CHANNEL:TS". None when unset or unreadable."""
    raw = config.FIFTY_DAYS_THREAD.strip()
    m = _LINK_RE.search(raw)
    if m:
        return m.group(1), f"{m.group(2)}.{m.group(3)}"
    m = re.fullmatch(r"(C[A-Z0-9]+):(\d+\.\d+)", raw)
    return (m.group(1), m.group(2)) if m else None


def day_number(date: datetime.date) -> int | None:
    """dec 31 is day 50, so nov 12 is day 1. None outside the challenge."""
    day = 50 - (datetime.date(date.year, 12, 31) - date).days
    return day if 1 <= day <= 50 else None


def crosspost(client, channel: str, ts: str, raw_text: str) -> None:
    """reply in the goal thread (also sent to the channel) with the required
    ":50days: Day X/50" first line and a link to the recap. only a real recap
    (has bullets) counts, so a contentless re-ping never posts a second one."""
    try:
        where = target()
        if not where:
            return
        if not _HAS_BULLET_RE.search(clean_ping_text(raw_text)):
            return
        day = day_number(datetime.datetime.fromtimestamp(float(ts), AMSTERDAM_TZ).date())
        if day is None:
            return
        link = client.chat_getPermalink(channel=channel, message_ts=ts)["permalink"]
        client.chat_postMessage(
            channel=where[0],
            thread_ts=where[1],
            reply_broadcast=True,
            text=f":50days: Day {day}/50\n<{link}>",
            unfurl_links=True,
        )
        logger.info("50 days: linked day %d into %s", day, where[0])
    except Exception:  # noqa: BLE001
        logger.exception("50 days crosspost failed")
