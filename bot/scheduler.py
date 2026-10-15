import datetime
import logging
import threading
import time

import config
from bot import reminders, state
from bot.chunk import chunk_response
from bot.history_store import append_message_async
from bot.llm import _kind_for
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


def _deliver(channel: str, thread_ts: str | None, text: str) -> None:
    """post noodle's own message (dm, channel or thread) and remember it,
    live and persisted, so it has the context if someone replies to it."""
    fragments = chunk_response(text)[:8]
    for fragment in fragments:
        payload = {"channel": channel, "text": fragment}
        if thread_ts:
            payload["thread_ts"] = thread_ts
        resp = state.app.client.chat_postMessage(**payload)
        if not thread_ts and channel != config.USER_ID:
            # a reply in thread to this message must count as a thread
            # noodle joined (same reason as handlers._process_locked)
            sent_ts = resp.get("ts")
            if sent_ts:
                state.PARTICIPATING_THREADS.add(sent_ts)
        time.sleep(config.CHUNK_DELAY_SECONDS)

    if channel == config.USER_ID:
        key = dm_conv_key()
    elif thread_ts:
        key = f"thread:{thread_ts}"
    else:
        key = f"chan:{channel}"
    history = state.MEMORY.setdefault(key, [])
    history.append({"role": "assistant", "content": text})
    trim_memory(history)
    append_message_async(key, _kind_for(key), "assistant", text)


def _post_dm(text: str) -> None:
    _deliver(config.USER_ID, None, text)


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


def _fire_reminder(reminder: dict) -> None:
    channel, thread_ts = reminder["channel"], reminder["thread_ts"]
    mention = "" if channel == config.USER_ID else f"<@{config.USER_ID}> "
    late_minutes = int((time.time() - reminder["next_run_ts"]) // 60)
    text = f"{mention}reminder: {reminder['text']}"
    if late_minutes > 5:
        text += f" (sorry, this is {late_minutes} minutes late, i was offline)"
    _deliver(channel, thread_ts, text)
    logger.info("fired reminder #%s", reminder["id"])


def _run_reminder_poller() -> None:
    while True:
        try:
            for reminder in reminders.pop_due():
                try:
                    _fire_reminder(reminder)
                except Exception:  # noqa: BLE001
                    logger.exception("failed to fire reminder #%s", reminder.get("id"))
        except Exception:  # noqa: BLE001
            logger.exception("reminder poll failed")
        time.sleep(15)


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
    threading.Thread(target=_run_reminder_poller, daemon=True).start()
