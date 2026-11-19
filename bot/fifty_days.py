"""hack club's "50 days" challenge: link each @matthias-day recap into
matthias's goal thread there. slack unfurls the link into an embed of the
recap, so the update itself stays in his own channel. off unless
FIFTY_DAYS_THREAD is set. posted by noodle (the organizer okayed that).
"""

import datetime
import logging
import re

import config
from bot.matthias_day import alert, recap_content
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


def day_text(day: int, link: str) -> str:
    """the post: the organizers want the first line to be exactly ":50days: Day X/50"."""
    return f":50days: Day {day}/50\n<{link}>"


def post_day(client, where: tuple[str, str], day: int, link: str, broadcast: bool = True) -> None:
    """reply in the goal thread; the live post is ticked "also send to the channel"
    (reply_broadcast), the backfill is not (it would be a burst of old days in the channel)."""
    client.chat_postMessage(
        channel=where[0],
        thread_ts=where[1],
        reply_broadcast=broadcast,
        text=day_text(day, link),
        unfurl_links=True,
    )


_DAY_LINE_RE = re.compile(r"^:50days: Day (\d+)/50\b")


def days_already_posted(client, where: tuple[str, str]) -> set[int]:
    """the day numbers that already have a post in the goal thread (by anyone: the
    first line is what the organizers read, so a day posted by hand counts too)."""
    done: set[int] = set()
    cursor = None
    while True:
        resp = client.conversations_replies(
            channel=where[0], ts=where[1], cursor=cursor, limit=200
        )
        for msg in resp.get("messages", []):
            m = _DAY_LINE_RE.match((msg.get("text") or "").strip())
            if m:
                done.add(int(m.group(1)))
        cursor = (resp.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            return done


def crosspost(client, channel: str, ts: str, raw_text: str, blocks=None) -> None:
    """reply in the goal thread (also sent to the channel) with the required
    ":50days: Day X/50" first line and a link to the recap. only a real recap
    (has bullets) counts, so a contentless re-ping never posts a second one."""
    try:
        where = target()
        if not where:
            return
        if not recap_content(client, channel, ts, raw_text, blocks):
            logger.info("50 days: %s has no recap bullets, not posting", ts)
            return
        day = day_number(datetime.datetime.fromtimestamp(float(ts), AMSTERDAM_TZ).date())
        if day is None:
            return
        link = client.chat_getPermalink(channel=channel, message_ts=ts)["permalink"]
        post_day(client, where, day, link)
        logger.info("50 days: linked day %d into %s", day, where[0])
    except Exception:  # noqa: BLE001
        logger.exception("50 days crosspost failed")
        alert("i couldn't post today's recap in your 50 days thread (check the logs). you may need to post it by hand.")
