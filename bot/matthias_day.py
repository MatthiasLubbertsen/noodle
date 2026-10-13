import datetime
import logging
import re

import config
from bot.history_store import append_message
from bot.tz import AMSTERDAM_TZ

logger = logging.getLogger("noodle")

# the leading @matthias-day ping: a real <!subteam^ID> mention (how it
# normally renders), or an unexpanded literal "@matthias-day"/"matthias-day"
# typed as plain text (slack occasionally doesn't expand it - see
# slack_text._matthias_day_pinged, which this mirrors).
_PING_PREFIX_RE = re.compile(
    r"^\s*(?:<!subteam\^[A-Z0-9]+(?:\|[^>]*)?>|@?matthias-day)\s*",
    re.IGNORECASE,
)
# the "today:" / "today" / "Today i did:" lead-in right after the ping
_TODAY_RE = re.compile(r"^\s*today\s*(?:i\s+did)?\s*:?\s*\n?", re.IGNORECASE)
# the "please talk in the thread below! ..." signature newer pings end with
_TRAILING_RE = re.compile(
    r"\n?please talk in the thread below.*$", re.IGNORECASE | re.DOTALL
)


def clean_ping_text(raw: str) -> str:
    """strip the @matthias-day ping, the "today:" lead-in, and the trailing
    thread/join signature off a daily recap message, leaving just the day's
    actual bullet-point content.
    """
    text = (raw or "").strip()
    text = _PING_PREFIX_RE.sub("", text, count=1)
    text = _TODAY_RE.sub("", text, count=1)
    text = _TRAILING_RE.sub("", text)
    return text.strip()


def conv_key(channel: str) -> str:
    return f"matthias_day:{channel}"


def persist_ping(client, channel: str, ts: str, raw_text: str) -> None:
    """clean + persist one @matthias-day ping into memory, timestamped to
    the day it actually happened (the message's own ts) and linked back to
    the original slack message so it can be read later. used by both the
    live listener (bot/handlers.py) and scripts/backfill_matthias_day.py.
    safe to call more than once for the same message: append_message()
    skips a permalink it has already stored.
    """
    content = clean_ping_text(raw_text)
    if not content:
        return
    permalink = None
    try:
        resp = client.chat_getPermalink(channel=channel, message_ts=ts)
        if resp.get("ok"):
            permalink = resp.get("permalink")
    except Exception:  # noqa: BLE001
        logger.exception("failed to fetch permalink for matthias-day ping %s", ts)
    created_at = datetime.datetime.fromtimestamp(float(ts), AMSTERDAM_TZ).isoformat()
    append_message(
        conv_key(channel),
        "matthias_day",
        "user",
        content,
        user_id=config.USER_ID,
        created_at=created_at,
        permalink=permalink,
        # these entries exist ONLY to be semantically searched later (never
        # hydrated into live context) - a row with a failed embedding would
        # be permanently invisible to search_memory, so skip persisting
        # entirely on failure rather than storing a dead row. combined with
        # the permalink dedup, this means a failed day just gets retried
        # cleanly on the next backfill run instead of being silently lost.
        require_embedding=True,
    )
