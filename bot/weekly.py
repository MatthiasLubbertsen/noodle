import datetime
import logging
import time

import config
from bot import state
from bot.history_store import load_kind_between
from bot.oneshot import complete
from bot.tz import AMSTERDAM_TZ

logger = logging.getLogger("noodle")

DIGEST_MIN_MESSAGES = 10  # quieter channels aren't worth a line
DIGEST_TOP_N = 5
DIGEST_MAX_CHANNELS = 60  # history calls are rate limited, so bound the scan
DIGEST_TRANSCRIPT_CHARS = 3000


def _today() -> datetime.date:
    return datetime.datetime.now(AMSTERDAM_TZ).date()


def build_journal_recap() -> str | None:
    """a 'week in review' built ONLY from the last 7 days of @matthias-day
    journal entries, or None if there weren't any."""
    end = _today()
    entries = load_kind_between(
        "matthias_day", (end - datetime.timedelta(days=6)).isoformat(), end.isoformat()
    )
    if not entries:
        return None
    body = "\n\n".join(f"[{e['created_at'][:10]}]\n{e['content']}" for e in entries)
    return complete(
        "it's sunday evening. below are matthias's own daily recaps from this week. "
        "write a short 'your week' message in your own voice: 3-5 sentences or a few "
        "short bullets covering what he worked on, anything that kept coming back, "
        "and one nice thing you noticed. use ONLY what's in the recaps - never invent "
        "anything.\n\n" + body,
        max_tokens=500,
    )


def _busy_channels() -> list[tuple[str, int, list[dict]]]:
    """(channel id, message count, messages newest-first) for the busiest
    channels noodle is in over the last 7 days."""
    skip = {config.LOGS_CHANNEL_ID, config.MATTHIAS_DAY_CHANNEL_ID}
    oldest = str(time.time() - 7 * 86400)
    channels, cursor = [], None
    while len(channels) < DIGEST_MAX_CHANNELS:
        resp = state.app.client.users_conversations(
            types="public_channel,private_channel",
            exclude_archived=True,
            limit=200,
            cursor=cursor,
        )
        channels += [c["id"] for c in resp.get("channels", []) if c["id"] not in skip]
        cursor = (resp.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break
    results = []
    for cid in channels[:DIGEST_MAX_CHANNELS]:
        try:
            resp = state.app.client.conversations_history(channel=cid, oldest=oldest, limit=200)
            msgs = [m for m in resp.get("messages", []) if not m.get("subtype")]
            if len(msgs) >= DIGEST_MIN_MESSAGES:
                results.append((cid, len(msgs), msgs))
        except Exception:  # noqa: BLE001
            logger.exception("digest: history failed for %s", cid)
        time.sleep(1.3)  # conversations.history is tier 3 (~50/min)
    results.sort(key=lambda r: r[1], reverse=True)
    return results[:DIGEST_TOP_N]


def _transcript(msgs: list[dict]) -> str:
    lines = [
        f"<@{m.get('user', '?')}>: {(m.get('text') or '').replace(chr(10), ' ')[:200]}"
        for m in reversed(msgs[:40])
    ]
    return "\n".join(lines)[-DIGEST_TRANSCRIPT_CHARS:]


def build_channel_digest() -> str | None:
    """a weekly digest of the busiest channels, or None if none were busy."""
    try:
        top = _busy_channels()
    except Exception:  # noqa: BLE001
        logger.exception("channel digest scan failed")
        return None
    if not top:
        return None
    blocks = "\n\n".join(f"### {cid}\n{_transcript(msgs)}" for cid, _, msgs in top)
    raw = complete(
        "below are recent messages from the busiest slack channels this week. for "
        "EACH channel write ONE short plain sentence on what it was mostly about, "
        "no mentions or names. answer with exactly one line per channel, in the "
        "same order, formatted `CHANNELID|sentence` and nothing else.\n\n" + blocks,
        max_tokens=400,
    )
    summaries = {}
    for line in (raw or "").splitlines():
        cid, _, sentence = line.partition("|")
        if sentence.strip():
            summaries[cid.strip()] = sentence.strip()
    lines = ["busiest channels this week:"]
    for cid, count, _ in top:
        suffix = f" - {summaries[cid]}" if cid in summaries else ""
        lines.append(f"- <#{cid}> ({count}{'+' if count >= 200 else ''} messages){suffix}")
    return "\n".join(lines)
