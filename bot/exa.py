import json
import logging
import urllib.error
import urllib.parse
import urllib.request

import config
from bot.netlog import log_hcai

logger = logging.getLogger("noodle")

ANSWER_URL_SUFFIX = "/exa/answer"
CONTENTS_URL_SUFFIX = "/exa/contents"


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


def page_contents(page_url: str, max_chars: int) -> dict | None:
    """ask exa for the readable text of one url (POST /exa/contents, same hack club ai proxy).
    exa renders js and gets past most walls, which is why fetch_url falls back to it. returns
    {"url", "title", "text"} or None (never raises) when exa couldn't read the page.
    """
    detail = urllib.parse.urlsplit(page_url).netloc
    url = config.AI_ENDPOINT.rstrip("/") + CONTENTS_URL_SUFFIX
    payload = json.dumps({"urls": [page_url], "text": {"maxCharacters": max_chars}}).encode("utf-8")
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
        with urllib.request.urlopen(req, timeout=45) as resp:
            log_hcai("exa contents", str(resp.status), detail=detail)
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        log_hcai("exa contents", str(exc.code), detail=detail)
        return None
    except Exception:  # noqa: BLE001
        log_hcai("exa contents", "error", detail=detail)
        logger.exception("exa contents failed")
        return None

    for r in data.get("results") or []:
        text = (r.get("text") or "").strip() if isinstance(r, dict) else ""
        if text:
            return {"url": r.get("url") or page_url, "title": (r.get("title") or "").strip(), "text": text}
    # exa answers 200 with no results and the reason per url (e.g. x.com / reddit: SOURCE_NOT_AVAILABLE 403)
    for st in data.get("statuses") or []:
        err = st.get("error") if isinstance(st, dict) else None
        if isinstance(err, dict):
            reason = f"exa couldn't read it ({err.get('tag') or 'error'}, {err.get('httpStatusCode') or '?'})"
            logger.info("exa contents for %s: %s", detail, reason)
            return {"url": page_url, "title": "", "text": "", "error": reason}
    return None
