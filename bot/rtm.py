"""presence + typing indicator, over slack's real-client websocket (not documented).

the official apis (bolt / socket mode) can't show the green dot or "noodle is typing...".
the web client does both over its own websocket, which needs the client's session
credentials: an `xoxc-` token and the `d` cookie of noodle's own account. optional - without
`SLACK_WEB_TOKEN` + `SLACK_WEB_COOKIE` (see features.py) nothing here runs and `typing()` is a no-op.

flow: `client.getWebSocketURL` (web api, xoxc + cookie) -> `primary_websocket_url`; connect with
`?token=<xoxc>` and header `cookie: d=<cookie>`; send `{"type": "ping"}` every ~40s; follow
`reconnect_url` messages; typing is `{"id": n, "type": "typing", "channel": ...}`.
being connected and pinging is what keeps the account online.
"""
import contextlib
import itertools
import json
import logging
import threading
import time
from urllib.parse import quote

import websocket
from slack_sdk import WebClient

import config
from bot import features

logger = logging.getLogger("noodle")

PING_SECONDS = 40
# a typing event shows for ~3s on the other side, so refresh it a bit sooner
TYPING_REFRESH_SECONDS = 2.5
RECONNECT_DELAY_SECONDS = 5

_ws = None
_ws_lock = threading.Lock()
_ids = itertools.count(1)
_started = False


def _websocket_url() -> str:
    client = WebClient(
        token=config.SLACK_WEB_TOKEN,
        headers={"Cookie": f"d={config.SLACK_WEB_COOKIE}"},
    )
    return client.api_call("client.getWebSocketURL")["primary_websocket_url"]


def _send(payload: dict) -> bool:
    with _ws_lock:
        ws = _ws
        if ws is None:
            return False
        try:
            ws.send(json.dumps({"id": next(_ids), **payload}))
            return True
        except Exception:  # noqa: BLE001
            logger.debug("rtm send failed", exc_info=True)
            return False


def _pinger(ws) -> None:
    while True:
        time.sleep(PING_SECONDS)
        with _ws_lock:
            if _ws is not ws:
                return
        if not _send({"type": "ping"}):
            return


def _run_once(url: str) -> str | None:
    """one connection, until it drops. returns a reconnect url if slack sent one."""
    global _ws
    ws = websocket.create_connection(
        f"{url}{'&' if '?' in url else '?'}token={quote(config.SLACK_WEB_TOKEN)}",
        header=[f"Cookie: d={config.SLACK_WEB_COOKIE}"],
        timeout=PING_SECONDS * 2,
    )
    with _ws_lock:
        _ws = ws
    threading.Thread(target=_pinger, args=(ws,), daemon=True).start()
    logger.info("rtm connected (presence + typing on)")
    reconnect = None
    try:
        while True:
            raw = ws.recv()
            if not raw:
                break
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if msg.get("type") == "reconnect_url":
                reconnect = msg.get("url") or reconnect
    except Exception:  # noqa: BLE001
        logger.info("rtm connection closed", exc_info=True)
    finally:
        with _ws_lock:
            _ws = None
        with contextlib.suppress(Exception):
            ws.close()
    return reconnect


def _loop() -> None:
    reconnect = None
    while True:
        try:
            url = reconnect or _websocket_url()
            reconnect = _run_once(url)
        except Exception:  # noqa: BLE001
            logger.warning("rtm connect failed, retrying", exc_info=True)
            reconnect = None
        time.sleep(RECONNECT_DELAY_SECONDS)


def start() -> None:
    """connect in the background and stay connected (reconnects on its own)."""
    global _started
    if _started or not features.enabled("typing"):
        return
    _started = True
    threading.Thread(target=_loop, daemon=True, name="rtm").start()


def send_typing(channel: str, thread_ts: str | None = None) -> bool:
    payload = {"type": "typing", "channel": channel}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    return _send(payload)


@contextlib.contextmanager
def typing(channel: str, thread_ts: str | None = None):
    """show "noodle is typing..." in the channel/thread for as long as the block runs."""
    if not features.enabled("typing"):
        yield
        return
    done = threading.Event()

    def _keep_typing() -> None:
        while not done.is_set():
            send_typing(channel, thread_ts)
            done.wait(TYPING_REFRESH_SECONDS)

    thread = threading.Thread(target=_keep_typing, daemon=True)
    thread.start()
    try:
        yield
    finally:
        done.set()
