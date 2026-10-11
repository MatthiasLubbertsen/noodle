import re


def chunk_response(text: str):
    """split a reply into slack messages: one message per paragraph.

    paragraphs are separated by a blank line. a paragraph is NEVER cut
    mid-way, no matter how long - slack's own per-message limit is far
    bigger than anything the model realistically writes.
    """
    parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return parts or [text.strip()]
