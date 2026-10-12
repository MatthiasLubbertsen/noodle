import json
import logging
import urllib.error
import urllib.request

import config
from bot.netlog import log_hcai

logger = logging.getLogger("noodle")

ANSWER_URL_SUFFIX = "/exa/answer"


def web_answer(query: str) -> str | None:
    """ask exa (hack club ai's web search proxy, docs.ai.hackclub.com/api/exa.html)
    a question and get a short answer grounded in live web results, with
    sources. returns None (never raises) if the request fails.
    """
    query = (query or "").strip()
    if not query:
        return None
    detail = " ".join(query.split()[:4])
    url = config.AI_ENDPOINT.rstrip("/") + ANSWER_URL_SUFFIX
    payload = json.dumps({"query": query}).encode("utf-8")
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
        with urllib.request.urlopen(req, timeout=20) as resp:
            log_hcai("exa", str(resp.status), detail=detail)
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        log_hcai("exa", str(exc.code), detail=detail)
        logger.exception("exa web search failed")
        return None
    except Exception:  # noqa: BLE001
        log_hcai("exa", "error", detail=detail)
        logger.exception("exa web search failed")
        return None

    answer = (data.get("answer") or "").strip()
    if not answer:
        return None
    urls = [
        c.get("url")
        for c in (data.get("citations") or [])
        if isinstance(c, dict) and c.get("url")
    ]
    if urls:
        answer += "\n\nsources: " + ", ".join(urls[:3])
    return answer
