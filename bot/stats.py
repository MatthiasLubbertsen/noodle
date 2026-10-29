import datetime
import json
import logging

from slack_sdk import WebClient

import config
from bot import state
from bot.tz import AMSTERDAM_TZ

logger = logging.getLogger("noodle")

_user_client: WebClient | None = None


def _today_str() -> str:
    return datetime.datetime.now(AMSTERDAM_TZ).strftime("%Y-%m-%d")


def _counting_client():
    """the slack client used to count today's messages. matthias's OWN token
    (MATTHIAS_USER_TOKEN) when set, so private channels and dms count too;
    otherwise noodle's own, narrower view. this is the ONLY place that token is
    ever used or even constructed into a client - never reuse it elsewhere."""
    global _user_client
    if not config.MATTHIAS_USER_TOKEN:
        return state.app.client
    if _user_client is None:
        _user_client = WebClient(token=config.MATTHIAS_USER_TOKEN)
    return _user_client


def messages_sent_on(user_id: str, day: str) -> int:
    """count how many slack messages `user_id` sent on `day` (YYYY-MM-DD, amsterdam),
    via slack search. only the total is read (`count=1`), never any message content.

    returns -1 if the search itself failed (so callers can tell "0 messages"
    apart from "couldn't check").
    """
    query = f"from:<@{user_id}> on:{day}"
    try:
        resp = _counting_client().search_messages(query=query, count=1)
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


def messages_sent_today(user_id: str) -> int:
    return messages_sent_on(user_id, _today_str())


AVERAGE_WINDOW_DAYS = 14


def _load_counts() -> dict[str, int]:
    try:
        return {k: int(v) for k, v in json.loads(config.MESSAGE_COUNTS_PATH.read_text()).items()}
    except (OSError, ValueError):
        return {}


def average_per_day(user_id: str) -> float | None:
    """his average slack messages per day over the last AVERAGE_WINDOW_DAYS full days
    (today excluded, it isn't finished). finished days never change, so each is counted
    once and kept in data/message_counts.json; only missing days cost a search (the
    first call fills the window, after that it is one search a day). None if there's
    nothing to average."""
    today = datetime.datetime.now(AMSTERDAM_TZ).date()
    window = [(today - datetime.timedelta(days=i)).isoformat() for i in range(1, AVERAGE_WINDOW_DAYS + 1)]
    counts = _load_counts()
    changed = False
    for day in window:
        if day not in counts:
            n = messages_sent_on(user_id, day)
            if n >= 0:
                counts[day] = n
                changed = True
    if changed:
        try:
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
            keep = {d: counts[d] for d in sorted(counts)[-60:]}
            config.MESSAGE_COUNTS_PATH.write_text(json.dumps(keep))
        except OSError:
            logger.exception("could not save message counts")
    values = [counts[d] for d in window if d in counts]
    return sum(values) / len(values) if values else None


def average_fact(user_id: str) -> str | None:
    """the average as a fact line for the model, or None."""
    avg = average_per_day(user_id)
    if avg is None:
        return None
    return f"his usual average is about {avg:.0f} slack messages a day (last {AVERAGE_WINDOW_DAYS} days)"


def build_daily_summary(user_id: str) -> str:
    """the "messages sent today" message, written by the model (never a
    templated line). it is posted publicly in a channel, so it only ever
    contains the number."""
    from bot.oneshot import complete  # lazy: oneshot -> llm -> tools -> stats would be a cycle

    count = messages_sent_today(user_id)
    if count < 0:
        facts = "the search for today's message count failed, so you don't know the number"
    else:
        facts = f"matthias has sent {count} slack message{'' if count == 1 else 's'} today"
        avg = average_fact(user_id)
        if avg:
            facts += f"\n{avg}"
    text = complete(
        "someone just pinged matthias's daily recap group, so you're posting today's "
        "message count in the channel for everyone to see. say it in your own voice, "
        "1-2 short sentences, a bit playful. mention the number. if you're given his "
        "average you may play with it however you like (above, below, right on it, "
        "or ignore it), be as creative as you want, no fixed format. nothing else "
        "about what he wrote or where. use ONLY these facts.\n\nfacts: " + facts,
        max_tokens=120,
    )
    if text:
        return text
    return "i couldn't count today's messages, sorry" if count < 0 else f"{count} messages today"
