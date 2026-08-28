import logging

import config
from bot import state

logger = logging.getLogger("noodle")

GATE_SYSTEM = (
    "you are the gatekeeper for a shy, friendly slack bot named noodle. "
    "noodle was NOT mentioned by name in the incoming message. decide whether "
    "noodle should chime in. reply with exactly one word: 'yes' or 'no'. "
    "say 'yes' generously: if the message is a question, a request, asks for "
    "help, is clearly addressed to noodle, refers to something noodle just said, "
    "or continues a conversation noodle is already part of. say 'no' only for "
    "pure announcements, system noise, or messages clearly not meant for anyone "
    "to answer."
)


def should_reply_unprompted(user_text: str, conv_key: str) -> bool:
    """cheap LLM check: is it a good idea for noodle to reply here?"""
    text = (user_text or "").strip()
    if not text:
        return False
    # questions almost always deserve a reply
    if "?" in text:
        return True

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
        f"incoming message:\n{text}\n\n"
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
        logger.exception("gate llm failed; defaulting to yes")
        # when unsure (e.g. the model backend hiccups) we lean toward replying
        # so noodle does not miss messages it should have answered.
        return True
