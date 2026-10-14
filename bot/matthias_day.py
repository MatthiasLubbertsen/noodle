import datetime
import logging
import re

import config
from bot.history_store import append_message
from bot.slack_text import _matthias_day_pinged
from bot.tz import AMSTERDAM_TZ

logger = logging.getLogger("noodle")

# the leading @matthias-day ping: a real <!subteam^ID> mention (how it
# normally renders), an unexpanded literal "@matthias-day"/"matthias-day"
# typed as plain text, or the rare "@…" glitched-mention slack sometimes
# renders instead (slack occasionally doesn't expand it right - see
# slack_text._matthias_day_pinged, which this mirrors).
_PING_PREFIX_RE = re.compile(
    r"^\s*(?:<!subteam\^[A-Z0-9]+(?:\|[^>]*)?>|@?matthias-day|@…)\s*",
    re.IGNORECASE,
)
# a real daily recap is always a bullet list - a short follow-up re-ping
# ("<!subteam^ID> ^^", "@matthias-day see above :)", "... yeah this one
# Slack") also opens with the ping but has no actual content of its own
# (it points at the real recap right above it), so requiring at least one
# bullet line is what tells a real entry apart from one of these.
_HAS_BULLET_RE = re.compile(r"^\s*• ", re.MULTILINE)
# how far back a re-ping may reach for the recap it points at: messages
# fetched, and max age relative to the re-ping itself.
_RECOVER_LOOKBACK = 10
_RECOVER_MAX_GAP_SECONDS = 12 * 60 * 60
# the "today:" / "today" / "Today i did:" lead-in right after the ping
_TODAY_RE = re.compile(r"^\s*today\s*(?:i\s+did)?\s*:?\s*\n?", re.IGNORECASE)
# the signature newer pings end with: "please talk in the thread below! •
# join this usergroup with ..." normally, or a one-off line like "I'm ooo,
# so I likely won't be reading reactions ... • join this usergroup with
# ..." while on vacation - so match the whole last line containing "join
# this usergroup with", whatever comes before it.
_TRAILING_RE = re.compile(
    r"\n?(?:please talk in the thread below.*|[^\n]*join this usergroup with[^\n]*)$",
    re.IGNORECASE | re.DOTALL,
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
    text = _normalize_bullets(text.strip())
    return text


def _normalize_bullets(text: str) -> str:
    """some days were posted as markdown-style "-"/"*" lists instead of
    slack's own "•" bullets - normalize them so the journal reads
    consistently: a top-level "- "/"* " line becomes "• ", an indented one
    becomes "◦ " (slack's own nested-bullet character), keeping its
    indentation. lines that already use "•"/"◦" are left alone.
    """
    lines = text.split("\n")
    fixed = []
    for line in lines:
        m = re.match(r"^(\s*)[-*] (.*)$", line)
        if m:
            bullet = "◦" if m.group(1) else "•"
            fixed.append(f"{m.group(1)}{bullet} {m.group(2)}")
        else:
            fixed.append(line)
    return "\n".join(fixed)


def conv_key(channel: str) -> str:
    return f"matthias_day:{channel}"


def persist_ping(client, channel: str, ts: str, raw_text: str) -> None:
    """clean + persist one @matthias-day ping into memory, timestamped to
    the day it actually happened (the message's own ts) and linked back to
    the original slack message so it can be read later. used by both the
    live listener (bot/handlers.py) and scripts/backfill_matthias_day.py.
    safe to call more than once for the same message: append_message()
    skips a permalink it has already stored.

    a short re-ping with no bullet content of its own ("^^", "see above
    :)", "yeah this one Slack") is never stored itself - instead it's
    treated as a pointer to the real recap right above it, which gets
    persisted in its place (see _recover_recap_before()).
    """
    content = clean_ping_text(raw_text)
    if content and _HAS_BULLET_RE.search(content):
        _persist_recap(client, channel, ts, content)
        return
    logger.info(
        "non-bullet @matthias-day match (re-ping, not a recap itself): %r - "
        "looking for the recap above it",
        content[:60],
    )
    _recover_recap_before(client, channel, ts)


def _recover_recap_before(client, channel: str, ts: str) -> None:
    """a re-ping exists because the real recap right above it didn't notify
    (its own ping didn't fire/expand) - so that recap may never have been
    matched as a ping at all. walk back through the messages just before
    the re-ping and persist matthias's nearest bulleted message. if that
    recap DID ping correctly it's already stored, and the permalink dedup
    makes this a no-op.

    only looks at matthias's own top-level messages, skips over further
    re-pings (people sometimes re-pinged twice), and stops at the first of
    his messages that is neither a recap nor a re-ping, or anything more
    than _RECOVER_MAX_GAP_SECONDS older than the re-ping - so it can't
    grab some unrelated bulleted message from days earlier.
    """
    try:
        resp = client.conversations_history(
            channel=channel, latest=ts, inclusive=False, limit=_RECOVER_LOOKBACK
        )
    except Exception:  # noqa: BLE001
        logger.exception("failed to fetch history before matthias-day re-ping %s", ts)
        return
    if not resp.get("ok"):
        logger.error("conversations_history failed: %s", resp.get("error"))
        return
    for msg in resp.get("messages", []):  # newest first
        if float(ts) - float(msg.get("ts", 0)) > _RECOVER_MAX_GAP_SECONDS:
            break
        if msg.get("user") != config.USER_ID:
            continue
        text = msg.get("text", "") or ""
        content = clean_ping_text(text)
        if content and _HAS_BULLET_RE.search(content):
            logger.info("recovered recap above re-ping %s: %r", ts, content[:60])
            _persist_recap(client, channel, msg["ts"], content)
            return
        if _matthias_day_pinged(text):
            continue  # another re-ping - keep looking further up
        break
    logger.info("no recap found above re-ping %s, skipping", ts)


def _persist_recap(client, channel: str, ts: str, content: str) -> None:
    """store one cleaned recap, dated to its own ts, with its permalink."""
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
