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
from bot.slack_text import (
    _channel_link,
    _clean_text,
    _is_dm,
    _matthias_day_pinged,
    _mention_in_text,
)
from bot.stats import build_daily_summary

logger = logging.getLogger("noodle")


def _user_display(uid: str) -> str:
    # prefer slack's own users_info for a friendly name
    try:
        info = state.app.client.users_info(user=uid)
        if info.get("ok"):
            u = info.get("user", {}) or {}
            prof = u.get("profile", {}) or {}
            return (
                prof.get("display_name")
                or u.get("real_name")
                or u.get("name")
                or uid
            )
    except Exception:  # noqa: BLE001
        logger.exception("users_info failed for %s", uid)
    return uid


def location_label(event: dict) -> str:
    """describe where noodle is right now, including the channel/dm name."""
    channel = event.get("channel")
    if _is_dm(event):
        try:
            info = state.app.client.conversations_info(channel=channel)
            if info.get("ok"):
                ch = info.get("channel", {}) or {}
                uid = ch.get("user")
                if uid:
                    return f"you are in a direct message with {_user_display(uid)} (id {uid})."
        except Exception:  # noqa: BLE001
            logger.exception("dm info failed for %s", channel)
        return "you are in a direct message."
    return f"you are in {_channel_link(channel)}."


def _is_noise(event: dict) -> bool:
    # our own messages and system/bot noise -> prevents reply loops
    if event.get("bot_id") or event.get("subtype"):
        return True
    if event.get("user") == state.AUTH_USER_ID:
        return True
    return False


def _should_respond(event: dict):
    if _is_noise(event):
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

    # allowed channels, no mention: let the gate decide if we should chime in,
    # but not more than once per UNPROMPTED_COOLDOWN_SECONDS in the same
    # channel, so noodle doesn't jump into every other message back to back.
    if config.ALLOW_CHANNEL_WILDCARD or channel in config.ALLOWED_CHANNELS:
        now = time.time()
        last = state.LAST_UNPROMPTED_REPLY.get(channel, 0.0)
        if now - last < config.UNPROMPTED_COOLDOWN_SECONDS:
            return False, None, None
        key = conv_key(event)
        if should_reply_unprompted(text, key):
            logger.info("gate allowed unprompted reply in %s", channel)
            state.LAST_UNPROMPTED_REPLY[channel] = now
            return True, _clean_text(text), thread_ts

    return False, None, None


def _process(channel: str, prompt: str, thread_ts: str | None, key: str,
             location: str | None = None) -> None:
    try:
        if thread_ts:
            # remember this thread so we keep answering in it
            state.PARTICIPATING_THREADS.add(thread_ts)
        reply = ask_noodle(key, prompt, location=location)
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
                text="i can't reach my brain right now, the network is being weird",
            )
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        logger.exception("failed to handle message in %s", channel)
        try:
            state.app.client.chat_postMessage(
                channel=channel, text="oops, something went wrong on my end"
            )
        except Exception:  # noqa: BLE001
            pass


def _send_daily_stats_reply(channel: str) -> None:
    # always a fresh top-level message, never a thread reply
    try:
        summary = build_daily_summary(config.USER_ID)
        state.app.client.chat_postMessage(channel=channel, text=summary)
    except Exception:  # noqa: BLE001
        logger.exception("failed to send daily stats reply in %s", channel)


def _is_ignored(text: str) -> bool:
    # a message starting with "# " is always ignored, no matter what
    return text.lstrip().startswith("# ")


def handle_message(event: dict) -> None:
    if _is_noise(event):
        return

    text = event.get("text", "") or ""
    if _is_ignored(text):
        return

    # pinging the @matthias-day usergroup always gets today's stats recap,
    # regardless of mentions/allowed-channels/the unprompted-reply gate.
    if _matthias_day_pinged(text):
        threading.Thread(
            target=_send_daily_stats_reply,
            args=(event.get("channel"),),
            daemon=True,
        ).start()
        return

    ok, prompt, thread_ts = _should_respond(event)
    if not ok:
        return
    channel = event.get("channel")
    key = conv_key(event)
    location = location_label(event)
    # run the (slow) ai call + chunked sending off the socket thread
    threading.Thread(
        target=_process, args=(channel, prompt, thread_ts, key, location), daemon=True
    ).start()
