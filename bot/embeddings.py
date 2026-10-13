import json
import logging
import time
import urllib.error
import urllib.request

import config
from bot.netlog import log_hcai

logger = logging.getLogger("noodle")

# transient read timeouts/connection hiccups happen occasionally against
# hack club ai's proxy, especially for longer inputs (a full day's recap can
# be a lot more text than a chat message) - one quick retry clears most of
# them instead of permanently losing that row's embedding.
_MAX_ATTEMPTS = 2
_RETRY_DELAY_SECONDS = 2
_TIMEOUT_SECONDS = 30


def _embed_once(text: str, detail: str) -> list[float] | None:
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
    with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
        log_hcai("embeddings", str(resp.status), detail=detail)
        data = json.loads(resp.read().decode("utf-8"))
    return data["data"][0]["embedding"]


def embed_text(text: str) -> list[float] | None:
    """embed text via the configured ai endpoint's /embeddings route.

    built against hack club ai's openai-compatible embeddings proxy
    (https://docs.ai.hackclub.com/api/embeddings.html), which lives at
    AI_ENDPOINT + "/embeddings" - same base url/key as chat completions.
    retries once after a transient failure (timeout, connection reset);
    returns None (never raises) only if every attempt fails, so a flaky
    embedding call never breaks the surrounding chat/memory flow.
    """
    text = (text or "").strip()
    if not text:
        return None
    detail = " ".join(text.split()[:4])
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return _embed_once(text, detail)
        except urllib.error.HTTPError as exc:
            log_hcai("embeddings", str(exc.code), detail=detail)
            logger.exception("embedding request failed (attempt %d/%d)", attempt, _MAX_ATTEMPTS)
            return None  # an HTTP error response won't fix itself on retry
        except Exception:  # noqa: BLE001
            log_hcai("embeddings", "error", detail=detail)
            logger.exception("embedding request failed (attempt %d/%d)", attempt, _MAX_ATTEMPTS)
            if attempt < _MAX_ATTEMPTS:
                time.sleep(_RETRY_DELAY_SECONDS)
    return None
