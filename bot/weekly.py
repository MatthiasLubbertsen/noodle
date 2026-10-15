import datetime
import logging
import time

import config
from bot import lastfm, state
from bot.history_store import load_kind_between
from bot.oneshot import complete, remember_facts
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
    blocks = "\n\n".join(f"### <#{cid}> ({count} messages)\n{_transcript(msgs)}" for cid, count, msgs in top)
    facts = "\n".join(f"- <#{cid}>: {count}{'+' if count >= 200 else ''} messages" for cid, count, _ in top)
    remember_facts("busiest channels this week", facts)
    text = complete(
        "below are recent messages from the busiest slack channels this week. write "
        "matthias a short weekly digest in your own voice: one line per channel on "
        "what it was mostly about, with the message count. refer to each channel by "
        "its <#CHANNELID> mention exactly as given (never plain #names), and don't "
        "name or tag people. use ONLY what's below.\n\n" + blocks,
        max_tokens=500,
    )
    if text and all(f"<#{cid}>" in text for cid, _, _ in top):
        return text
    return "busiest channels this week:\n" + facts


def build_music_recap() -> str | None:
    """a weekly listening recap from last.fm (this week vs last week, top
    artists and tracks), or None if last.fm isn't set up / has nothing."""
    if not lastfm.configured():
        return None
    now = int(time.time())
    this_week = lastfm.scrobbles_between(now - 7 * 86400, now)
    last_week = lastfm.scrobbles_between(now - 14 * 86400, now - 7 * 86400)
    artists = lastfm.top("artists", "7day", 5)
    tracks = lastfm.top("tracks", "7day", 5)
    facts = []
    if this_week is not None:
        facts.append(f"tracks played in the last 7 days: {this_week}")
    if last_week is not None:
        facts.append(f"tracks played the 7 days before that: {last_week}")
    if artists:
        facts.append("top artists this week: " + "; ".join(artists))
    if tracks:
        facts.append("top tracks this week: " + "; ".join(tracks))
    if not facts or not this_week:
        return None
    facts_text = "\n".join(facts)
    remember_facts("weekly music stats", facts_text)
    return complete(
        "it's the weekly music recap. tell matthias about his listening this week in "
        "your own voice: 3-4 sentences or a few short lines. mention the totals "
        "(and whether it's up or down vs the week before), his top artists and a "
        "standout track, and anything fun you notice. use ONLY these facts.\n\n"
        "facts:\n" + facts_text,
        max_tokens=350,
    )
