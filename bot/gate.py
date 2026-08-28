import logging

import config
from bot import state

logger = logging.getLogger("noodle")

GATE_SYSTEM = (
    "you are the gatekeeper for a shy slack bot named noodle. "
    "noodle was NOT mentioned by name in the incoming message. decide whether "
    "noodle should chime in. reply with exactly one word: 'yes' or 'no'. "
    "say 'yes' only if the message is clearly addressed to noodle, is a direct "
    "question or request noodle could help with, or clearly continues a talk "
    "noodle is already part of. say 'no' for casual chatter, announcements, "
    "jokes, or anything not meant for noodle."
)


def should_reply_unprompted(user_text: str, conv_key: str) -> bool:
    """cheap LLM check: is it a good idea for noodle to reply here?"""
    history = state.MEMORY.get(conv_key, [])
    recent = []
    for m in history[-6:]:
        role = m.get("role")
        content = m.get("content") or ""
        if role == "user":
            recent.append(f"user: {content}")
        elif role == "assistant":
            recent.append(f"noodle: {content}")
    context = "\n".join(recent)
    prompt = (
        f"recent conversation:\n{context}\n\n"
        f"incoming message:\n{user_text}\n\n"
        "should noodle reply? answer yes or no:"
    )
    try:
        resp = state.client.chat.completions.create(
            model=config.MODEL,
            messages=[
                {"role": "system", "content": GATE_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            max_tokens=3,
        )
        answer = (resp.choices[0].message.content or "").strip().lower()
        return answer.startswith("yes")
    except Exception:  # noqa: BLE001
        logger.exception("gate llm failed; defaulting to no")
        return False
