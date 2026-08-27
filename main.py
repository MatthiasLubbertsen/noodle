import logging
import re
import threading
import time
from pathlib import Path

from openai import APIConnectionError, OpenAI
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

import config

# --------------------------------------------------------------------------
# logging
# --------------------------------------------------------------------------
config.LOG_DIR.mkdir(exist_ok=True)
_log_handlers = [logging.StreamHandler()]
try:
    _log_handlers.append(
        logging.FileHandler(config.LOG_DIR / "noodle.log", encoding="utf-8")
    )
except OSError as exc:
    print(f"warning: cannot write log file, logging to console only: {exc}")
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=_log_handlers,
)
logger = logging.getLogger("noodle")

# --------------------------------------------------------------------------
# slack app (socket mode) + ai client
# --------------------------------------------------------------------------
app = App(token=config.SLACK_USER_TOKEN)
client = OpenAI(api_key=config.OPENAI_API_KEY, base_url=config.AI_ENDPOINT)

# resolve our own identity so we never reply to ourselves
auth = app.client.auth_test()
AUTH_USER_ID = auth.get("user_id")
BOT_ID = auth.get("bot_id")
MENTION_IDS = {mid for mid in (AUTH_USER_ID, BOT_ID) if mid}
logger.info("noodle online as user=%s bot=%s", AUTH_USER_ID, BOT_ID)

# load persona
SYSTEM_PROMPT = config.SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _is_dm(event: dict) -> bool:
    return event.get("channel_type") == "im" or str(event.get("channel", "")).startswith("D")


def _mention_in_text(text: str) -> bool:
    return any(f"<@{mid}>" in text for mid in MENTION_IDS)


def _clean_text(text: str) -> str:
    # strip @-mentions so the model does not echo them
    for mid in MENTION_IDS:
        text = text.replace(f"<@{mid}>", "")
    return text.strip()


def _should_respond(event: dict):
    # ignore our own messages and system/bot noise -> prevents reply loops
    if event.get("bot_id") or event.get("subtype"):
        return False, None
    if event.get("user") == AUTH_USER_ID:
        return False, None

    user = event.get("user")
    text = event.get("text", "")
    channel = event.get("channel")

    if _is_dm(event):
        if user != config.USER_ID:
            logger.info("ignored DM from unauthorized user %s", user)
            return False, None
        return True, _clean_text(text)

    # channel message: only in allowed channels and only when mentioned
    mentioned = ("noodle" in text.lower()) or _mention_in_text(text)
    if not mentioned:
        return False, None
    if not config.ALLOW_CHANNEL_WILDCARD and channel not in config.ALLOWED_CHANNELS:
        logger.info("ignored mention in non-allowed channel %s", channel)
        return False, None
    return True, _clean_text(text)


def _chunk_response(text: str):
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


def _ask_noodle(user_text: str) -> str:
    response = client.chat.completions.create(
        model=config.MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ],
        temperature=0.9,
        max_tokens=800,
    )
    return response.choices[0].message.content or ""


def _process(channel: str, prompt: str) -> None:
    try:
        reply = _ask_noodle(prompt)
        for fragment in _chunk_response(reply):
            app.client.chat_postMessage(channel=channel, text=fragment)
            time.sleep(config.CHUNK_DELAY_SECONDS)
    except APIConnectionError:
        logger.exception("ai backend unreachable (network/proxy issue)")
        try:
            app.client.chat_postMessage(
                channel=channel,
                text="i can't reach my brain right now, the network is wobbly :3",
            )
        except Exception:
            pass
    except Exception:
        logger.exception("failed to handle message in %s", channel)
        try:
            app.client.chat_postMessage(
                channel=channel, text="oops something went wobbly :3"
            )
        except Exception:
            pass


# --------------------------------------------------------------------------
# events
# --------------------------------------------------------------------------
@app.event("message")
def handle_message(event: dict) -> None:
    ok, prompt = _should_respond(event)
    if not ok:
        return
    channel = event.get("channel")
    # run the (slow) ai call + chunked sending off the socket thread
    threading.Thread(
        target=_process, args=(channel, prompt), daemon=True
    ).start()


# --------------------------------------------------------------------------
# entrypoint
# --------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info("starting noodle in socket mode...")
    SocketModeHandler(app, config.SLACK_APP_TOKEN).start()
