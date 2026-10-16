import datetime
import logging

from slack_sdk import WebClient

import config
from bot import state
from bot.oneshot import complete
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


def messages_sent_today(user_id: str) -> int:
    """count how many slack messages `user_id` has sent today, via slack search.
    only the total is read (`count=1`), never any message content.

    returns -1 if the search itself failed (so callers can tell "0 messages"
    apart from "couldn't check").
    """
    query = f"from:<@{user_id}> on:{_today_str()}"
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


def build_daily_summary(user_id: str) -> str:
    """the "messages sent today" message, written by the model (never a
    templated line). it is posted publicly in a channel, so it only ever
    contains the number."""
    count = messages_sent_today(user_id)
    if count < 0:
        facts = "the search for today's message count failed, so you don't know the number"
    else:
        facts = f"matthias has sent {count} slack message{'' if count == 1 else 's'} today"
    text = complete(
        "someone just pinged matthias's daily recap group, so you're posting today's "
        "message count in the channel for everyone to see. say it in your own voice, "
        "1-2 short sentences, a bit playful. only mention the number, nothing else "
        "about what he wrote or where. use ONLY this fact.\n\nfact: " + facts,
        max_tokens=120,
    )
    if text:
        return text
    return "i couldn't count today's messages, sorry" if count < 0 else f"{count} messages today"
