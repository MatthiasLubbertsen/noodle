import json
import logging
import urllib.error
import urllib.request

import config
from bot.netlog import log_hcai

logger = logging.getLogger("noodle")


def embed_text(text: str) -> list[float] | None:
    """embed text via the configured ai endpoint's /embeddings route.

    built against hack club ai's openai-compatible embeddings proxy
    (https://docs.ai.hackclub.com/api/embeddings.html), which lives at
    AI_ENDPOINT + "/embeddings" - same base url/key as chat completions.
    returns None (never raises) if the request fails, so a flaky embedding
    call never breaks the surrounding chat/memory flow.
    """
    text = (text or "").strip()
    if not text:
        return None
    detail = " ".join(text.split()[:4])
    url = config.AI_ENDPOINT.rstrip("/") + "/embeddings"
    payload = json.dumps({"model": config.EMBEDDING_MODEL, "input": text}).encode("utf-8")
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
        with urllib.request.urlopen(req, timeout=15) as resp:
            log_hcai("embeddings", str(resp.status), detail=detail)
            data = json.loads(resp.read().decode("utf-8"))
        return data["data"][0]["embedding"]
    except urllib.error.HTTPError as exc:
        log_hcai("embeddings", str(exc.code), detail=detail)
        logger.exception("embedding request failed")
        return None
    except Exception:  # noqa: BLE001
        log_hcai("embeddings", "error", detail=detail)
        logger.exception("embedding request failed")
        return None
