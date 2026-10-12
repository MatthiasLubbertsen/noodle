import datetime
import logging
import threading
import time

import config
from bot import state
from bot.chunk import chunk_response
from bot.history_store import append_message_async
from bot.memory import dm_conv_key, trim_memory
from bot.morning import build_greeting_message, build_news_message
from bot.tz import AMSTERDAM_TZ

logger = logging.getLogger("noodle")

PING_REMINDER_TEXT = (
    "hey, it's that time again: time to send today's <!subteam^{group_id}|@matthias-day> "
    "ping."
)
PING_REMINDER_TEXT_NO_GROUP = (
    "hey, it's that time again: time to send today's @matthias-day ping."
)


def _parse_hhmm(value: str, default: tuple[int, int]) -> tuple[int, int]:
    try:
        hour_s, minute_s = value.strip().split(":")
        return int(hour_s), int(minute_s)
    except Exception:  # noqa: BLE001
        logger.warning("invalid time %r, falling back to %s", value, default)
        return default


def _next_run_at(hour: int, minute: int) -> datetime.datetime:
    now = datetime.datetime.now(AMSTERDAM_TZ)
    run_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if run_at <= now:
        run_at += datetime.timedelta(days=1)
    return run_at


def _post_dm(text: str) -> None:
    fragments = chunk_response(text)[:8]
    for fragment in fragments:
        state.app.client.chat_postMessage(channel=config.USER_ID, text=fragment)
        time.sleep(config.CHUNK_DELAY_SECONDS)

    # remember noodle sent this, both live and persisted, so it has the
    # context if matthias replies to it (even across a restart)
    key = dm_conv_key()
    history = state.MEMORY.setdefault(key, [])
    history.append({"role": "assistant", "content": text})
    trim_memory(history)
    append_message_async(key, "dm", "assistant", text)


def _send_ping_reminder() -> None:
    try:
        if config.MATTHIAS_DAY_GROUP_ID:
            text = PING_REMINDER_TEXT.format(group_id=config.MATTHIAS_DAY_GROUP_ID)
        else:
            text = PING_REMINDER_TEXT_NO_GROUP
        _post_dm(text)
        logger.info("sent @matthias-day ping reminder to %s", config.USER_ID)
    except Exception:  # noqa: BLE001
        logger.exception("failed to send ping reminder dm")


def _send_morning_dm() -> None:
    try:
        _post_dm(build_greeting_message())
        news = build_news_message()
        if news:
            time.sleep(config.CHUNK_DELAY_SECONDS)
            _post_dm(news)
        logger.info("sent morning wakeup dm(s) to %s", config.USER_ID)
    except Exception:  # noqa: BLE001
        logger.exception("failed to send morning wakeup dm")


def _run_daily(hour: int, minute: int, job) -> None:
    while True:
        run_at = _next_run_at(hour, minute)
        wait_seconds = (run_at - datetime.datetime.now(AMSTERDAM_TZ)).total_seconds()
        logger.info("next %s scheduled for %s", job.__name__, run_at.isoformat())
        time.sleep(max(wait_seconds, 1))
        job()


def start_daily_scheduler() -> None:
    ping_hour, ping_minute = _parse_hhmm(config.DAILY_PING_REMINDER, (19, 0))
    threading.Thread(
        target=_run_daily, args=(ping_hour, ping_minute, _send_ping_reminder), daemon=True
    ).start()
    morning_hour, morning_minute = _parse_hhmm(config.DAILY_MORNING_DM, (8, 0))
    threading.Thread(
        target=_run_daily, args=(morning_hour, morning_minute, _send_morning_dm), daemon=True
    ).start()
