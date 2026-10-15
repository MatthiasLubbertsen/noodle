import logging

from bot import state
from bot.llm import _call_chat
from bot.slack_text import _clean_reply

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
