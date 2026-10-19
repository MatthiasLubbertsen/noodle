import datetime
import logging
import time

from bot import lastfm
from bot.history_store import load_kind_between
from bot.oneshot import complete, remember_facts
from bot.tz import AMSTERDAM_TZ

logger = logging.getLogger("noodle")


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
