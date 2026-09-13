import datetime
import logging
import threading
import time
from zoneinfo import ZoneInfo

import config
from bot import state
from bot.stats import build_daily_summary

logger = logging.getLogger("noodle")


def _next_run_at(tz: ZoneInfo, hour: int, minute: int) -> datetime.datetime:
    now = datetime.datetime.now(tz)
    run_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if run_at <= now:
        run_at += datetime.timedelta(days=1)
    return run_at


def _send_daily_dm() -> None:
    try:
        summary = build_daily_summary(config.USER_ID)
        state.app.client.chat_postMessage(channel=config.USER_ID, text=summary)
        logger.info("sent daily stats dm to %s", config.USER_ID)
    except Exception:  # noqa: BLE001
        logger.exception("failed to send daily stats dm")


def _loop() -> None:
    tz = ZoneInfo(config.DAILY_STATS_TZ)
    while True:
        run_at = _next_run_at(tz, config.DAILY_STATS_HOUR, config.DAILY_STATS_MINUTE)
        wait_seconds = (run_at - datetime.datetime.now(tz)).total_seconds()
        logger.info("next daily stats dm scheduled for %s", run_at.isoformat())
        time.sleep(max(wait_seconds, 1))
        _send_daily_dm()


def start_daily_stats_scheduler() -> None:
    threading.Thread(target=_loop, daemon=True).start()
