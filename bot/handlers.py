import logging
import threading
import time

import config
from openai import APIConnectionError

from bot import state
from bot.chunk import chunk_response
from bot.gate import should_reply_unprompted
from bot.llm import ask_noodle
from bot.memory import conv_key
from bot.slack_text import _clean_text, _is_dm, _mention_in_text

logger = logging.getLogger("noodle")


def _should_respond(event: dict):
    # ignore our own messages and system/bot noise -> prevents reply loops
    if event.get("bot_id") or event.get("subtype"):
        return False, None, None
    if event.get("user") == state.AUTH_USER_ID:
        return False, None, None

    user = event.get("user")
    text = event.get("text", "")
    channel = event.get("channel")
    thread_ts = event.get("thread_ts")

    if _is_dm(event):
        if user != config.USER_ID:
            logger.info("ignored DM from unauthorized user %s", user)
            return False, None, None
        return True, _clean_text(text), thread_ts

    mentioned = ("noodle" in text.lower()) or _mention_in_text(text)
    if mentioned:
        if (
            not config.ALLOW_CHANNEL_WILDCARD
            and channel not in config.ALLOWED_CHANNELS
        ):
            logger.info("ignored mention in non-allowed channel %s", channel)
            return False, None, None
        return True, _clean_text(text), thread_ts

    # keep talking inside threads noodle has joined, even without a mention
    if thread_ts and thread_ts in state.PARTICIPATING_THREADS:
        return True, _clean_text(text), thread_ts

    # allowed channels, no mention: let the gate decide if we should chime in
    if config.ALLOW_CHANNEL_WILDCARD or channel in config.ALLOWED_CHANNELS:
        key = conv_key(event)
        if should_reply_unprompted(text, key):
            logger.info("gate allowed unprompted reply in %s", channel)
            return True, _clean_text(text), thread_ts

    return False, None, None


def _process(channel: str, prompt: str, thread_ts: str | None, key: str) -> None:
    try:
        if thread_ts:
            # remember this thread so we keep answering in it
            state.PARTICIPATING_THREADS.add(thread_ts)
        reply = ask_noodle(key, prompt)
        # cap fragments so noodle never spams the channel
        fragments = chunk_response(reply)[:8]
        for fragment in fragments:
            payload = {"channel": channel, "text": fragment}
            if thread_ts:
                payload["thread_ts"] = thread_ts
            state.app.client.chat_postMessage(**payload)
            time.sleep(config.CHUNK_DELAY_SECONDS)
    except APIConnectionError:
        logger.exception("ai backend unreachable (network/proxy issue)")
        try:
            state.app.client.chat_postMessage(
                channel=channel,
                text="i can't reach my brain right now, the network is wobbly :3",
            )
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        logger.exception("failed to handle message in %s", channel)
        try:
            state.app.client.chat_postMessage(
                channel=channel, text="oops something went wobbly :3"
            )
        except Exception:  # noqa: BLE001
            pass


def handle_message(event: dict) -> None:
    ok, prompt, thread_ts = _should_respond(event)
    if not ok:
        return
    channel = event.get("channel")
    key = conv_key(event)
    # run the (slow) ai call + chunked sending off the socket thread
    threading.Thread(
        target=_process, args=(channel, prompt, thread_ts, key), daemon=True
    ).start()
