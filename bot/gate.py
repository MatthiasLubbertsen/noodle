import json
import logging
import urllib.error
import urllib.request

import config
from bot import state
from bot.netlog import log_hcai

logger = logging.getLogger("noodle")

JEV_URL_SUFFIX = "/jev/systemone"

# a single, small "yes/no" question for jev (hack club ai's fast/cheap
# classifier model, https://docs.ai.hackclub.com/api/jev.html) instead of a
# full chat-completion call - this is exactly the narrow, calibrated-answer
# task it's built for, and billing is input-only (output is free), so the
# instructions stay tight while the "state" below carries the real content.
GATE_INSTRUCTIONS = (
    "the state is a recent slack conversation followed by one new message, "
    "in a channel where the assistant 'noodle' was NOT mentioned by name. "
    "should noodle reply to the new message anyway, unprompted? default to "
    "no. say yes only if the new message is clearly addressed to noodle "
    "specifically, or is a direct, unambiguous continuation of something "
    "noodle just said above. say no for ordinary chat between other people, "
    "banter, announcements, reactions, or anything that would make sense "
    "even if noodle did not exist - including questions, since most "
    "questions in a group channel are addressed to other humans, not noodle."
)

# jev returns a calibrated 0-1 probability, not a coin flip - bias the
# cutoff above 0.5 to stay conservative, matching "default to no" above.
REPLY_THRESHOLD = 0.6


def should_reply_unprompted(user_text: str, conv_key: str) -> bool:
    """fast/cheap check via jev: is it a good idea for noodle to reply here?"""
    text = (user_text or "").strip()
    if not text:
        return False
    detail = " ".join(text.split()[:4])

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
    state_text = f"recent conversation:\n{context}\n\nnew message:\n{text}"

    payload = json.dumps(
        {
            "state": state_text,
            "questions": {
                "should_reply": {"type": "noul", "instructions": GATE_INSTRUCTIONS}
            },
        }
    ).encode("utf-8")
    url = config.AI_ENDPOINT.rstrip("/") + JEV_URL_SUFFIX
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {config.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
            data = json.loads(resp.read().decode("utf-8"))
        probability = data["answers"]["should_reply"]["noul"]
        decision = probability > REPLY_THRESHOLD
        log_hcai(
            "jev",
            f"{status} ({'yes' if decision else 'no'}, p={probability:.2f})",
            detail=detail,
        )
        return decision
    except urllib.error.HTTPError as exc:
        log_hcai("jev", str(exc.code), detail=detail)
        logger.exception("jev gate check failed; defaulting to no")
        return False
    except Exception:  # noqa: BLE001
        log_hcai("jev", "error", detail=detail)
        logger.exception("jev gate check failed; defaulting to no")
        # when unsure (e.g. a backend hiccup) stay quiet rather than risk
        # noodle spamming a channel it wasn't actually asked to join.
        return False
