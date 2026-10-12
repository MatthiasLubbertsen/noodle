import json
import logging
import urllib.request

import config

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
            data = json.loads(resp.read().decode("utf-8"))
        return data["data"][0]["embedding"]
    except Exception:  # noqa: BLE001
        logger.exception("embedding request failed")
        return None
