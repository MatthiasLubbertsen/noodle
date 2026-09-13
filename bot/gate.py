import logging

import config
from bot import state

logger = logging.getLogger("noodle")

GATE_SYSTEM = (
    "you are the gatekeeper for a friendly slack bot named noodle. "
    "noodle was NOT mentioned by name in the incoming message, so it is only "
    "one voice among many people chatting normally in this channel. decide "
    "whether noodle should chime in unprompted. reply with exactly one word: "
    "'yes' or 'no'. "
    "default to 'no'. only say 'yes' if the message is clearly addressed to "
    "noodle specifically (e.g. it directly asks 'noodle' or an assistant for "
    "help), or it is a direct, unambiguous continuation of something noodle "
    "just said in the recent conversation above. say 'no' for ordinary chat "
    "between other people, banter, announcements, reactions, or any message "
    "that would make sense even if noodle did not exist, EVEN IF it contains "
    "a question mark or is phrased as a question, since most questions in a "
    "group channel are addressed to other humans, not to noodle."
)


def should_reply_unprompted(user_text: str, conv_key: str) -> bool:
    """cheap LLM check: is it a good idea for noodle to reply here?"""
    text = (user_text or "").strip()
    if not text:
        return False

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
        logger.exception("gate llm failed; defaulting to no")
        # when unsure (e.g. the model backend hiccups) stay quiet rather than
        # risk noodle spamming a channel it wasn't actually asked to join.
        return False
