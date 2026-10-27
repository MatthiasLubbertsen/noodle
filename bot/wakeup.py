"""morning dm when matthias first comes online, instead of at a fixed time.

with the web session (features.enabled("presence"), same credentials as the typing indicator)
rtm.py subscribes to his presence over the client websocket and calls `on_online()` whenever he
turns active. the first time each amsterdam day, outside QUIET_HOURS (so a 3am scroll isn't
"the morning"), sends the morning dm. without the credentials the scheduler keeps its fixed
`DAILY_MORNING_DM` time. the sent day is persisted so a restart can't send it twice.
"""
import datetime
import json
import logging
import threading

import config
from bot import proactive
from bot.tz import AMSTERDAM_TZ

logger = logging.getLogger("noodle")

_lock = threading.Lock()


def _today() -> str:
    return datetime.datetime.now(AMSTERDAM_TZ).strftime("%Y-%m-%d")


def _sent_today() -> bool:
    try:
        return json.loads(config.MORNING_STATE_PATH.read_text(encoding="utf-8")).get("day") == _today()
    except Exception:  # noqa: BLE001
        return False


def _mark_sent() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.MORNING_STATE_PATH.write_text(json.dumps({"day": _today()}), encoding="utf-8")


def on_online() -> None:
    """matthias just turned active: send today's morning dm if it hasn't gone out yet."""
    from bot.scheduler import _send_morning_dm  # avoid a circular import

    with _lock:
        if proactive.in_quiet_hours() or _sent_today():
            return
        _mark_sent()  # before sending: a crash drops the dm rather than repeating it
    logger.info("matthias came online, sending the morning dm")
    _send_morning_dm()
