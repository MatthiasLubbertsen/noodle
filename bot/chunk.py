import re

import config


def chunk_response(text: str):
    # primary strategy: the model is told to put each fragment on its own line
    parts = [p.strip() for p in text.splitlines() if p.strip()]

    # fallback: if there are no line breaks, split on sentence boundaries
    if len(parts) <= 1:
        parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]

    # hard cap very long fragments so slack messages stay small
    result = []
    for part in parts:
        if len(part) <= config.MAX_FRAGMENT_CHARS:
            result.append(part)
            continue
        words = part.split(" ")
        current = ""
        for word in words:
            if len(current) + len(word) + 1 <= config.MAX_FRAGMENT_CHARS:
                current = (current + " " + word).strip()
            else:
                if current:
                    result.append(current)
                current = word
        if current:
            result.append(current)
    return result or [text.strip()]
