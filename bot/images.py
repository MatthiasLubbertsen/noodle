"""image support: reading images people send, and making new ones.

reading: slack file attachments are downloaded with noodle's own token and handed to the
model as base64 data urls (hack club ai's "image inputs", openai-style `image_url` parts).
writing: an image model on hack club ai (chat completions with `modalities: ["image", "text"]`,
https://docs.ai.hackclub.com/api/image-generation.html) returns base64 data urls in
`message.images`. both go through the same AI_ENDPOINT/key as everything else.
"""
import base64
import json
import logging
import urllib.error
import urllib.request

import config
from bot.netlog import log_hcai

logger = logging.getLogger("noodle")

_READABLE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
MAX_IMAGES = 4
_MAX_BYTES = 8 * 1024 * 1024
_DOWNLOAD_TIMEOUT_SECONDS = 30
_GENERATE_TIMEOUT_SECONDS = 120
ASPECT_RATIOS = ("1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9")


def _download(url: str) -> bytes | None:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {config.SLACK_USER_TOKEN}"})
    with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT_SECONDS) as resp:
        ctype = (resp.headers.get_content_type() or "").lower()
        if ctype not in _READABLE_TYPES:
            # without the files:read scope slack answers with an html login page, not the image
            logger.warning("slack file download returned %s instead of an image (files:read scope?)", ctype)
            return None
        data = resp.read(_MAX_BYTES + 1)
    return data if len(data) <= _MAX_BYTES else None


def read_slack_images(files: list[dict] | None) -> list[dict]:
    """download the image attachments of a slack message (max MAX_IMAGES).

    returns [{"name", "mime", "data_url"}]; anything that isn't a supported image, is too
    big or fails to download is skipped (never raises), so a bad attachment can't break a reply.
    """
    images = []
    for f in files or []:
        mime = (f.get("mimetype") or "").lower()
        url = f.get("url_private_download") or f.get("url_private")
        if mime not in _READABLE_TYPES or not url:
            continue
        if len(images) >= MAX_IMAGES:
            break
        name = f.get("name") or "image"
        try:
            data = _download(url)
        except Exception:  # noqa: BLE001
            logger.exception("downloading slack image %s failed", name)
            continue
        if data is None:
            logger.warning("skipping slack image %s (not downloadable or over %d bytes)", name, _MAX_BYTES)
            continue
        b64 = base64.b64encode(data).decode("ascii")
        images.append({"name": name, "mime": mime, "data_url": f"data:{mime};base64,{b64}"})
    return images


def user_content(text: str, images: list[dict]) -> list[dict]:
    """an openai-style multimodal user message body: the text, then every image."""
    parts = [{"type": "text", "text": text}]
    parts += [{"type": "image_url", "image_url": {"url": i["data_url"]}} for i in images]
    return parts


def attached_note(images: list[dict]) -> str:
    """what stays in memory instead of the image bytes."""
    return "[attached image: " + ", ".join(i["name"] for i in images) + "]"


def generate(prompt: str, aspect_ratio: str | None = None, references: list[dict] | None = None):
    """make (or, with references, edit) an image. returns (images, text) where images is a list
    of (png bytes, filename), or an error string. never raises."""
    detail = " ".join(prompt.split()[:4])
    content = user_content(prompt, references) if references else prompt
    body = {
        "model": config.IMAGE_MODEL,
        "messages": [{"role": "user", "content": content}],
        "modalities": ["image", "text"],
    }
    if aspect_ratio in ASPECT_RATIOS:
        body["image_config"] = {"aspect_ratio": aspect_ratio}
    req = urllib.request.Request(
        config.AI_ENDPOINT.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_GENERATE_TIMEOUT_SECONDS) as resp:
            log_hcai("image", str(resp.status), detail=detail)
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        log_hcai("image", str(exc.code), detail=detail)
        return f"the image model returned an error ({exc.code})."
    except Exception:  # noqa: BLE001
        log_hcai("image", "error", detail=detail)
        logger.exception("image generation failed")
        return "the image model didn't answer, try again in a bit."

    message = (data.get("choices") or [{}])[0].get("message") or {}
    out = []
    for n, item in enumerate(message.get("images") or [], 1):
        url = (item.get("image_url") or {}).get("url") or ""
        if not url.startswith("data:") or "," not in url:
            continue
        header, b64 = url.split(",", 1)
        ext = header[len("data:"):].split(";")[0].split("/")[-1] or "png"
        try:
            out.append((base64.b64decode(b64), f"noodle-image-{n}.{ext}"))
        except ValueError:
            continue
    if not out:
        # the model usually explains a refusal in its text
        reason = (message.get("content") or "").strip()
        return f"no image came back{': ' + reason[:300] if reason else '.'}"
    return out, (message.get("content") or "").strip()
