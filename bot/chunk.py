import re

import config


def chunk_response(text: str):
    # primary strategy: keep the reply as one block, splitting only on
    # paragraph breaks (blank lines) if the model used any.
    parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    if not parts:
        parts = [text.strip()]

    # hard cap very long paragraphs so slack messages stay a reasonable size
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
