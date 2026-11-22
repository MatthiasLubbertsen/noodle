import datetime
import logging
import re

import config
from bot.history_store import append_message, has_permalink
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
    r"(?:^|\n)[^\n]*join this usergroup with[^\n]*$",
    re.IGNORECASE,
)
_THREAD_NOTE_RE = re.compile(r"please talk in the thread below.*$", re.IGNORECASE | re.DOTALL)


def _cutoff() -> datetime.time:
    try:
        h, m = config.RECAP_DAY_CUTOFF.strip().split(":")
        return datetime.time(int(h), int(m))
    except (ValueError, AttributeError):
        return datetime.time(5, 0)


def recap_date(ts: str | float | None = None) -> datetime.date:
    """the day a recap posted at `ts` (slack ts, or now when None) is ABOUT. he writes it at
    night, so one posted after midnight but before RECAP_DAY_CUTOFF is yesterday's recap."""
    when = (
        datetime.datetime.now(AMSTERDAM_TZ)
        if ts is None
        else datetime.datetime.fromtimestamp(float(ts), AMSTERDAM_TZ)
    )
    if when.time() < _cutoff():
        return when.date() - datetime.timedelta(days=1)
    return when.date()


def is_late(ts: str | float | None = None) -> bool:
    """posted after midnight, about the day before."""
    when = (
        datetime.datetime.now(AMSTERDAM_TZ)
        if ts is None
        else datetime.datetime.fromtimestamp(float(ts), AMSTERDAM_TZ)
    )
    return recap_date(ts) != when.date()


def recap_created_at(ts: str) -> str:
    """the journal timestamp for a recap: its own time, or for a late one the last second of
    the day it is about, so date filters (get_journal, the weekly recap) file it there."""
    if is_late(ts):
        day = recap_date(ts)
        return datetime.datetime.combine(day, datetime.time(23, 59, 59), AMSTERDAM_TZ).isoformat()
    return datetime.datetime.fromtimestamp(float(ts), AMSTERDAM_TZ).isoformat()


def _element_text(el: dict) -> str:
    kind = el.get("type")
    if kind == "text":
        return el.get("text", "")
    if kind == "link":
        url, label = el.get("url", ""), el.get("text")
        return f"<{url}|{label}>" if label else url
    if kind == "emoji":
        return f":{el.get('name', '')}:"
    if kind == "user":
        return f"<@{el.get('user_id', '')}>"
    if kind == "usergroup":
        return f"<!subteam^{el.get('usergroup_id', '')}>"
    if kind == "channel":
        return f"<#{el.get('channel_id', '')}>"
    if kind == "broadcast":
        return f"<!{el.get('range', 'here')}>"
    return ""


def text_from_blocks(blocks) -> str:
    """slack's rich_text blocks as slack-style text, bullets included ("• ", nested
    "◦ "). a message posted with `blocks` can carry its whole body there while its
    plain `text` is only the header (real incident: every recap since oct 16 had
    event text "<!subteam^...> today:" and the bullets only in the blocks)."""
    lines: list[str] = []

    def section(elements) -> str:
        return "".join(_element_text(e) for e in elements or [])

    for block in blocks or []:
        if block.get("type") != "rich_text":
            continue
        for el in block.get("elements", []):
            kind = el.get("type")
            if kind == "rich_text_section":
                lines.append(section(el.get("elements")))
            elif kind == "rich_text_list":
                ordered = el.get("style") == "ordered"
                indent = int(el.get("indent", 0) or 0)
                for i, item in enumerate(el.get("elements", []), 1):
                    mark = f"{i}." if ordered else ("◦" if indent else "•")
                    lines.append(f"{'    ' * indent}{mark} {section(item.get('elements'))}")
            elif kind in ("rich_text_preformatted", "rich_text_quote"):
                lines.append(section(el.get("elements")))
    return "\n".join(lines)


def message_recap(text: str, blocks=None) -> str | None:
    """the cleaned bulleted recap in a message (its text first, then its blocks),
    or None when there is none."""
    content = clean_ping_text(text)
    if content and _HAS_BULLET_RE.search(content):
        return content
    if blocks:
        content = clean_ping_text(text_from_blocks(blocks))
        if content and _HAS_BULLET_RE.search(content):
            return content
    return None


def clean_ping_text(raw: str) -> str:
    """strip the @matthias-day ping, the "today:" lead-in, and the trailing
    thread/join signature off a daily recap message, leaving just the day's
    actual bullet-point content.
    """
    text = (raw or "").strip()
    text = _PING_PREFIX_RE.sub("", text, count=1)
    text = _TODAY_RE.sub("", text, count=1)
    text = _THREAD_NOTE_RE.sub("", text)
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


def recap_content(client, channel: str, ts: str, event_text: str, blocks=None) -> str | None:
    """the cleaned bulleted recap for the message at `ts`, or None when it has none.
    reads the event's text, then the event's blocks, and if neither has bullets re-reads
    the message itself from slack (text, then blocks). the event's text can be only the
    header (real incident: every recap since oct 16 was journaled as "no recap", and the
    50 days post silently skipped). shared by persist_ping() and fifty_days.crosspost()
    so they can't disagree."""
    content = message_recap(event_text, blocks)
    if content:
        return content
    try:
        resp = client.conversations_history(
            channel=channel, latest=ts, inclusive=True, limit=1
        )
        msgs = (resp.get("messages") or []) if resp.get("ok") else []
        if msgs and msgs[0].get("ts") == ts:
            stored = message_recap(msgs[0].get("text", "") or "", msgs[0].get("blocks"))
            if stored:
                logger.info("matthias-day %s: recap found on re-reading the message", ts)
                return stored
    except Exception:  # noqa: BLE001
        logger.exception("could not re-read matthias-day message %s", ts)
    return None


def conv_key(channel: str) -> str:
    return f"matthias_day:{channel}"


def persist_ping(
    client, channel: str, ts: str, raw_text: str, blocks=None, notify: bool = True
) -> None:
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

    when a real recap can't be saved, or a message that looks like one has no bullets,
    matthias gets a dm (`notify`; the backfill script turns it off, it would send one
    per failed old day).
    """
    content = recap_content(client, channel, ts, raw_text, blocks)
    if content:
        if not _persist_recap(client, channel, ts, content) and notify:
            alert(
                "i couldn't save today's @matthias-day recap to my journal (no permalink "
                "or the embedding failed, check the logs). `python -m scripts.backfill_matthias_day` "
                "will pick it up later."
            )
        return
    logger.info(
        "non-bullet @matthias-day match (re-ping, not a recap itself): raw=%r "
        "block_types=%r - looking for the recap above it",
        (raw_text or "")[:120],
        [b.get("type") for b in (blocks or [])],
    )
    if _recover_recap_before(client, channel, ts):
        return
    if notify and _looks_like_recap(raw_text, blocks):
        alert(
            "your @matthias-day message looked like a real recap but i found no bullets in it, "
            "so it's not in my journal (and not in the 50 days thread). check the logs, then "
            "`python -m scripts.backfill_matthias_day` once it's fixed."
        )


def _looks_like_recap(raw_text: str, blocks) -> bool:
    """a real recap, as opposed to a short "^^" re-ping: it has a rich_text list or
    more than a few words once the ping is stripped."""
    if any(
        el.get("type") == "rich_text_list"
        for b in blocks or []
        for el in b.get("elements", [])
    ):
        return True
    return len(clean_ping_text(raw_text)) > 25


def alert(text: str) -> None:
    """tell matthias in his dm that the recap pipeline failed. plain text on purpose
    (like the error fallbacks in handlers.py: this is the thing reporting a failure).
    never raises."""
    try:
        from bot.scheduler import _post_dm  # late import: scheduler imports half the bot

        _post_dm(text)
    except Exception:  # noqa: BLE001
        logger.exception("could not dm the matthias-day failure alert")


def _recover_recap_before(client, channel: str, ts: str) -> bool:
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
        return False
    if not resp.get("ok"):
        logger.error("conversations_history failed: %s", resp.get("error"))
        return False
    for msg in resp.get("messages", []):  # newest first
        if float(ts) - float(msg.get("ts", 0)) > _RECOVER_MAX_GAP_SECONDS:
            break
        if msg.get("user") != config.USER_ID:
            continue
        text = msg.get("text", "") or ""
        content = message_recap(text, msg.get("blocks"))
        if content:
            logger.info("recovered recap above re-ping %s: %r", ts, content[:60])
            return _persist_recap(client, channel, msg["ts"], content)
        if _matthias_day_pinged(text):
            continue  # another re-ping - keep looking further up
        break
    logger.info("no recap found above re-ping %s, skipping", ts)
    return False


def _persist_recap(client, channel: str, ts: str, content: str) -> bool:
    """store one cleaned recap, dated to the day it is about (recap_created_at), with its permalink."""
    permalink = None
    try:
        resp = client.chat_getPermalink(channel=channel, message_ts=ts)
        if resp.get("ok"):
            permalink = resp.get("permalink")
    except Exception:  # noqa: BLE001
        logger.exception("failed to fetch permalink for matthias-day ping %s", ts)
    if not permalink:
        # the permalink is what dedups re-runs - a row without one would
        # get inserted again on every backfill. skip it instead; the next
        # backfill run retries it.
        logger.warning("skipping matthias-day ping %s (no permalink) - safe to retry later", ts)
        return False
    created_at = recap_created_at(ts)
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
    return has_permalink(permalink)
