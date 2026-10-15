import logging

import datetime

from bot import state
from bot.history_store import append_message_async
from bot.llm import _call_chat
from bot.slack_text import _clean_reply
from bot.tz import AMSTERDAM_TZ

logger = logging.getLogger("noodle")


def complete(prompt: str, max_tokens: int = 400, detail: str = "") -> str | None:
    """one-off persona completion (not part of any conversation's memory).
    returns None if the call fails or comes back empty."""
    try:
        resp = _call_chat(
            [
                {"role": "system", "content": state.SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            detail=detail or " ".join(prompt.split()[:4]),
            max_tokens=max_tokens,
        )
        return _clean_reply(resp.choices[0].message.content or "") or None
    except Exception:  # noqa: BLE001
        logger.exception("one-off completion failed")
        return None


def remember_facts(label: str, facts: str) -> None:
    """persist the raw numbers behind an AI-written message (embedded, so
    search_memory finds them: "how much music did i play on tuesday").
    the message itself is persisted separately when it's delivered, but the
    model may leave details out of it - this keeps the facts verbatim.
    one conv_key per day, kind "daily_facts"; never part of live context."""
    today = datetime.datetime.now(AMSTERDAM_TZ).strftime("%Y-%m-%d")
    append_message_async(f"facts:{today}", "daily_facts", "assistant", f"{label} ({today}):\n{facts}")
