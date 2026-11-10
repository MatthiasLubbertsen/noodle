"""morning dm when matthias first comes online, instead of at a fixed time.

with the web session (features.enabled("presence"), same credentials as the typing indicator)
rtm.py subscribes to his presence over the client websocket and calls `on_online()` whenever he
turns active. the first time each amsterdam day, sends the morning dm (no quiet hours: whenever
he shows up first is when it goes out, and the greeting is written for the actual time of day).
if he hasn't been seen by `MORNING_FALLBACK_DM` (default 12:00), the scheduler calls
`send_fallback()` and it goes out anyway. without the credentials the scheduler keeps its fixed
`DAILY_MORNING_DM` time. the sent day is persisted so a restart can't send it twice.
"""
import datetime
import json
import logging
import os
import threading
import time

import config
from bot.tz import AMSTERDAM_TZ

logger = logging.getLogger("noodle")

_lock = threading.Lock()
# presence events only fire on a change, so a status that never flips (online all night, a
# reconnect that missed it) would never trigger the dm: also look every few minutes until it is out
PRESENCE_POLL_SECONDS = 30


def _today() -> str:
    return datetime.datetime.now(AMSTERDAM_TZ).strftime("%Y-%m-%d")


def _sent_today() -> bool:
    path = config.MORNING_STATE_PATH
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    except Exception:  # noqa: BLE001
        logger.warning("could not read %s, treating the morning dm as not sent", path, exc_info=True)
        return False
    try:
        return json.loads(raw).get("day") == _today()
    except Exception:  # noqa: BLE001
        # an empty/garbled file (e.g. cut off by a crash) must not silently disable the once-a-day guard
        logger.warning("%s is unreadable (%r), treating the morning dm as not sent", path, raw[:40])
        return False


def _mark_sent() -> None:
    """write the day atomically (temp file + fsync + rename): a crash or reboot mid-write used to
    leave a 0-byte file, which made the once-a-day guard forget."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = config.MORNING_STATE_PATH
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps({"day": _today()}))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _send_once(reason: str) -> None:
    from bot.scheduler import _send_morning_dm  # avoid a circular import

    with _lock:
        if _sent_today():
            return
        _mark_sent()  # before sending: a crash drops the dm rather than repeating it
    logger.info("sending the morning dm (%s)", reason)
    _send_morning_dm()


def _too_early() -> bool:
    """before MORNING_EARLIEST_DM: a night-time "active" blip must not wake him with a dm."""
    from bot.scheduler import _parse_hhmm  # avoid a circular import

    hour, minute = _parse_hhmm(config.MORNING_EARLIEST_DM, (6, 0))
    now = datetime.datetime.now(AMSTERDAM_TZ)
    return (now.hour, now.minute) < (hour, minute)


def on_online() -> None:
    """matthias just turned active: send today's morning dm if it hasn't gone out yet
    (not before MORNING_EARLIEST_DM; the 30s presence poll catches him once it's time)."""
    logger.info("matthias came online (presence)")
    if _too_early():
        logger.info("too early for the morning dm, waiting until %s", config.MORNING_EARLIEST_DM)
        return
    _send_once("matthias came online")


def send_fallback() -> None:
    """the cutoff time passed: send today's morning dm if he never came online."""
    _send_once("fallback time, he hasn't come online")


def check_presence() -> None:
    """ask slack whether matthias is online right now, and send today's dm if so. no-op once sent."""
    if _sent_today() or _too_early():
        return
    try:
        from bot import state

        if state.app and state.app.client.users_getPresence(user=config.USER_ID).get("presence") == "active":
            on_online()
    except Exception:  # noqa: BLE001
        logger.debug("presence check failed", exc_info=True)


def poll_presence() -> None:
    """background loop for `check_presence()` (started by the scheduler)."""
    while True:
        time.sleep(PRESENCE_POLL_SECONDS)
        check_presence()
