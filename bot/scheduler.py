import datetime
import logging
import threading
import time

import config
from bot import github, github_watch, hackatime, hackatime_watch, proactive, reminders, sky, state, watch
from bot.chunk import chunk_response
from bot.history_store import append_message_async
from bot.llm import _kind_for
from bot.memory import dm_conv_key, trim_memory
from bot.morning import build_greeting_message, build_news_message, build_weather_message
from bot.tz import AMSTERDAM_TZ
from bot.oneshot import complete
from bot.weekly import build_journal_recap, build_music_recap
from bot.wrapup import build_wrapup_message

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
    state.LAST_REPLY[key] = (time.time(), text)
    history = state.MEMORY.setdefault(key, [])
    history.append({"role": "assistant", "content": text})
    trim_memory(history)
    append_message_async(key, _kind_for(key), "assistant", text)


def _post_dm(text: str) -> None:
    _deliver(config.USER_ID, None, text)


def _send_ping_reminder() -> None:
    try:
        if config.MATTHIAS_DAY_GROUP_ID:
            mention = f"<!subteam^{config.MATTHIAS_DAY_GROUP_ID}|@matthias-day>"
            fallback = PING_REMINDER_TEXT.format(group_id=config.MATTHIAS_DAY_GROUP_ID)
        else:
            mention = "@matthias-day"
            fallback = PING_REMINDER_TEXT_NO_GROUP
        text = complete(
            "it's time for matthias to send today's daily recap ping. nudge him to do "
            f"it, in your own voice, one or two short sentences. include exactly this "
            f"text once, as given: {mention}\n(you're only reminding him - he writes "
            "the recap himself, so don't draft or ask about its content.)",
            max_tokens=100,
        )
        _post_dm(text if text and mention in text else fallback)
        logger.info("sent @matthias-day ping reminder to %s", config.USER_ID)
    except Exception:  # noqa: BLE001
        logger.exception("failed to send ping reminder dm")


def _send_morning_dm() -> None:
    try:
        _post_dm(build_greeting_message())
        weather = build_weather_message()
        if weather:
            time.sleep(config.CHUNK_DELAY_SECONDS)
            _post_dm(weather)
        news = build_news_message()
        if news:
            time.sleep(config.CHUNK_DELAY_SECONDS)
            _post_dm(news)
        logger.info("sent morning wakeup dm(s) to %s", config.USER_ID)
    except Exception:  # noqa: BLE001
        logger.exception("failed to send morning wakeup dm")


def _send_wrapup_dm() -> None:
    try:
        text = build_wrapup_message()
        if text:
            _post_dm(text)
            logger.info("sent end-of-day wrap-up dm to %s", config.USER_ID)
    except Exception:  # noqa: BLE001
        logger.exception("failed to send wrap-up dm")


def _send_weekly_journal_dm() -> None:
    try:
        text = build_journal_recap()
        if text:
            _post_dm(text)
            logger.info("sent weekly journal recap dm to %s", config.USER_ID)
    except Exception:  # noqa: BLE001
        logger.exception("failed to send weekly journal recap dm")


def _send_weekly_music_dm() -> None:
    try:
        text = build_music_recap()
        if text:
            _post_dm(text)
            logger.info("sent weekly music recap dm to %s", config.USER_ID)
    except Exception:  # noqa: BLE001
        logger.exception("failed to send weekly music recap dm")


def _check_github() -> None:
    """dm about new github notifications - one dm per poll, held back during
    quiet hours and once the daily cap is hit (they stay unreported, so they
    go out in the next allowed poll)."""
    if not proactive.can_send():
        return
    items = github_watch.new_notifications()
    if not items:
        return
    _post_dm(github_watch.compose_message(items))
    github_watch.mark_seen(items)
    proactive.record_sent()
    logger.info("sent github watcher dm (%d notification(s))", len(items))


def _run_github_watcher() -> None:
    while True:
        try:
            _check_github()
        except Exception:  # noqa: BLE001
            logger.exception("github watcher poll failed")
        time.sleep(config.GITHUB_POLL_SECONDS)


def _check_hackatime() -> None:
    """every full hour on a project: a plain line in the logs channel; every 5 hours: a
    persona post in his own channel. these are about his public coding, not dms to him,
    so they don't go through the proactive guardrails. new_milestones() has already marked
    them reported, so one failing post must not stop the others."""
    for name, before, hours in hackatime_watch.new_milestones():
        try:
            watch._log(hackatime_watch.log_line(name, before, hours))
            mark = hackatime_watch.five_hour_mark(before, hours)
            if mark and config.MATTHIAS_DAY_CHANNEL_ID:
                _deliver(config.MATTHIAS_DAY_CHANNEL_ID, None, hackatime_watch.compose_milestone(name, mark))
            logger.info("hackatime milestone: %s %dh -> %dh", name, before, hours)
        except Exception:  # noqa: BLE001
            logger.exception("hackatime milestone post failed for %s (%dh)", name, hours)


def _run_hackatime_watcher() -> None:
    while True:
        try:
            _check_hackatime()
        except Exception:  # noqa: BLE001
            logger.exception("hackatime watcher poll failed")
        time.sleep(config.HACKATIME_POLL_SECONDS)


# the night an aurora dm (forecast at sunset, or live) already went out: one per night
_aurora_night = None


def _send_sky_tonight(sunset: datetime.datetime) -> None:
    """at sunset: a dm if tonight's sky has something worth going outside for.
    unprompted, so it skips (never holds - it'd be stale) on quiet hours or the cap."""
    global _aurora_night
    try:
        facts = sky.tonight_facts(sunset)
        if not facts:
            logger.info("sky: nothing special tonight")
            return
        if sky.overcast(sunset, sunset + datetime.timedelta(hours=4)):
            logger.info("sky: %d thing(s) tonight but overcast, not sending", len(facts))
            return
        if not proactive.can_send():
            logger.info("sky: quiet hours or daily cap, not sending")
            return
        _post_dm(sky.compose_tonight(facts))
        proactive.record_sent()
        if sky.has_aurora(facts):
            _aurora_night = sky.night_of(sunset)  # this dm already told him, no second one live
        logger.info("sent tonight's sky dm (%d thing(s))", len(facts))
    except Exception:  # noqa: BLE001
        logger.exception("tonight's sky check failed")


def _run_sky_tonight() -> None:
    while True:
        try:
            sunset = sky.next_sunset()
            logger.info("next _send_sky_tonight scheduled for %s", sunset.isoformat())
            time.sleep(max((sunset - datetime.datetime.now(AMSTERDAM_TZ)).total_seconds(), 1))
            _send_sky_tonight(sunset)
            time.sleep(60)  # so next_sunset() moves on to tomorrow's
        except Exception:  # noqa: BLE001
            # e.g. no sunset found: never let the thread die, just try again later
            logger.exception("sky scheduler failed, retrying in an hour")
            time.sleep(3600)


def _run_aurora_watch() -> None:
    """every 10 minutes: dm once a night when the aurora is likely up right now."""
    global _aurora_night
    while True:
        try:
            night = sky.night_of()
            if night != _aurora_night and proactive.can_send(ignore_quiet_hours=config.AURORA_IN_QUIET_HOURS):
                facts = sky.aurora_now()
                if facts:
                    _post_dm(sky.compose_aurora(facts))
                    proactive.record_sent()
                    _aurora_night = night
                    logger.info("sent aurora alert dm")
        except Exception:  # noqa: BLE001
            logger.exception("aurora check failed")
        time.sleep(600)


def _fire_reminder(reminder: dict) -> None:
    channel, thread_ts = reminder["channel"], reminder["thread_ts"]
    mention = "" if channel == config.USER_ID else f"<@{config.USER_ID}>"
    late_minutes = int((time.time() - reminder["next_run_ts"]) // 60)
    late = late_minutes > 5
    prompt = (
        f"matthias asked you to remind him of this: {reminder['text']!r}. it's time. "
        "write the reminder in your own voice, one short sentence (two at most), "
        "friendly, not a template."
        + (f" start with exactly {mention} so he gets pinged." if mention else "")
        + (f" you're {late_minutes} minutes late because you were offline - say sorry briefly." if late else "")
    )
    text = complete(prompt, max_tokens=100)
    if not text:
        text = f"{mention} reminder: {reminder['text']}".strip()
    elif mention and mention not in text:
        text = f"{mention} {text}"
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


_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _parse_weekly(value: str, default: tuple[int, int, int]) -> tuple[int, int, int]:
    """'sun 18:00' -> (weekday 0=mon, hour, minute)"""
    try:
        day, hhmm = value.strip().lower().split()
        hour, minute = _parse_hhmm(hhmm, (default[1], default[2]))
        return _WEEKDAYS.index(day[:3]), hour, minute
    except Exception:  # noqa: BLE001
        logger.warning("invalid weekly time %r, falling back to %s", value, default)
        return default


def _run_weekly(weekday: int, hour: int, minute: int, job) -> None:
    while True:
        run_at = _next_run_at(hour, minute)
        while run_at.weekday() != weekday:
            run_at += datetime.timedelta(days=1)
        wait_seconds = (run_at - datetime.datetime.now(AMSTERDAM_TZ)).total_seconds()
        logger.info("next %s scheduled for %s", job.__name__, run_at.isoformat())
        time.sleep(max(wait_seconds, 1))
        job()


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
    wrap_hour, wrap_minute = _parse_hhmm(config.DAILY_WRAPUP_DM, (21, 30))
    threading.Thread(
        target=_run_daily, args=(wrap_hour, wrap_minute, _send_wrapup_dm), daemon=True
    ).start()
    for raw, default, job in (
        (config.WEEKLY_JOURNAL, (6, 18, 0), _send_weekly_journal_dm),
        (config.WEEKLY_MUSIC, (6, 18, 30), _send_weekly_music_dm),
    ):
        weekday, hour, minute = _parse_weekly(raw, default)
        threading.Thread(target=_run_weekly, args=(weekday, hour, minute, job), daemon=True).start()
    if github.configured():
        threading.Thread(target=_run_github_watcher, daemon=True).start()
    if hackatime.configured():
        threading.Thread(target=_run_hackatime_watcher, daemon=True).start()
    threading.Thread(target=_run_reminder_poller, daemon=True).start()
    threading.Thread(target=_run_sky_tonight, daemon=True).start()
    threading.Thread(target=_run_aurora_watch, daemon=True).start()
