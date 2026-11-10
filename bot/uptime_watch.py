import datetime
import json
import logging
import re
import threading
import time

import config
from bot.oneshot import complete, remember_facts
from bot.tz import AMSTERDAM_TZ

logger = logging.getLogger("noodle")

# reads his uptime kuma over its socket.io feed (the same one its own dashboard uses) and announces monitors
# that go down or come back. kuma's rest endpoints (/metrics, status page, badges) carry no error message,
# the socket feed does: every monitor (name, type, url/host) plus its newest heartbeat (status + msg, e.g.
# "Request failed with status code 502"). with auth disabled in kuma (settings > api keys says so) no login is
# needed; with auth on, set KUMA_USERNAME + KUMA_PASSWORD (no 2fa). kuma status: 0 down, 1 up, 2 pending,
# 3 maintenance; pending/maintenance change nothing. a monitor is "down" after UPTIME_FAILS polls in a row
# (kuma retries on its own, so 1). state per monitor name in data/uptime_state.json:
#   {"fails": n, "down_since": iso|None, "reason": str, "alerted": bool, "recovered_at": iso|None,
#    "type": str, "target": str}
# an outage nobody was told about (it ended while held back by quiet hours) is dropped silently.
TIMEOUT = 15
SETTLE = 1.5  # seconds without a new heartbeat list before the snapshot counts as complete
_WEB_TYPES = ("http", "keyword", "json-query", "real-browser")


def _target(m: dict) -> str:
    """what a monitor points at: the url for web monitors, host[:port] for the rest ("" if unknown)."""
    url = (m.get("url") or "").strip()
    url = "" if url == "https://" else url  # kuma's placeholder for non-web monitors
    if m.get("type") in _WEB_TYPES and url:
        return url
    host = (m.get("hostname") or "").strip()
    if host:
        return host + (f":{m['port']}" if m.get("port") else "")
    return url


def _flat(text: str | None, limit: int = 160) -> str:
    return " ".join((text or "").split())[:limit]


def snapshot(monitors: dict, beats: dict) -> dict[str, dict]:
    """{monitor name: {status, type, target, msg}} for every active monitor that has a heartbeat."""
    out = {}
    for mid, m in monitors.items():
        beat = beats.get(mid)
        if beat is None or m.get("active") is False:
            continue
        name = m.get("name") or f"monitor {mid}"
        if name in out:  # two monitors with one name must not overwrite each other
            name = f"{name} #{mid}"
        out[name] = {"status": int(beat.get("status", 2)), "type": m.get("type") or "", "target": _target(m),
                     "msg": _flat(beat.get("msg"))}
    return out


def fetch(transports: list[str] | None = None) -> dict[str, dict]:
    """one snapshot from kuma. raises if kuma can't be reached or sends nothing."""
    import socketio

    monitors, beats = {}, {}
    got_list = threading.Event()
    last = [time.monotonic()]
    sio = socketio.Client(reconnection=False, request_timeout=TIMEOUT)

    @sio.on("monitorList")
    def _monitor_list(data):
        monitors.update({int(k): v for k, v in (data or {}).items()})
        last[0] = time.monotonic()
        got_list.set()

    @sio.on("heartbeatList")
    def _heartbeat_list(monitor_id, data, overwrite=False):
        if data:
            beats[int(monitor_id)] = data[-1]  # oldest first, so the last one is the newest
        last[0] = time.monotonic()

    try:
        sio.connect(config.KUMA_URL, transports=transports, wait_timeout=TIMEOUT)
        if config.KUMA_USERNAME and config.KUMA_PASSWORD:
            res = sio.call("login", {"username": config.KUMA_USERNAME, "password": config.KUMA_PASSWORD, "token": ""},
                           timeout=TIMEOUT) or {}
            if not res.get("ok"):
                raise RuntimeError("kuma login failed: " + _flat(res.get("msg") or "no reason given"))
        if not got_list.wait(TIMEOUT):
            raise RuntimeError("kuma sent no monitors (is auth on? set KUMA_USERNAME and KUMA_PASSWORD)")
        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline and time.monotonic() - last[0] < SETTLE:
            active = [i for i, m in monitors.items() if m.get("active") is not False]
            if all(i in beats for i in active):
                break
            time.sleep(0.05)
    finally:
        try:
            sio.disconnect()
        except Exception:  # noqa: BLE001
            pass
    return snapshot(monitors, beats)


def _load() -> dict:
    try:
        return json.loads(config.UPTIME_STATE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:  # noqa: BLE001
        logger.exception("failed to load uptime state, starting fresh")
        return {}


def _save(state: dict) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.UPTIME_STATE_PATH.write_text(json.dumps(state), encoding="utf-8")


def update(state: dict, results: dict[str, str], now: datetime.datetime) -> dict:
    """fold one round of results ({monitor: "" up, reason down, None unknown}) into the state."""
    out = {}
    for url, reason in results.items():
        if reason is None:  # pending / maintenance: keep whatever we knew
            if url in state:
                out[url] = state[url]
            continue
        st = dict(state.get(url) or {"fails": 0, "down_since": None, "reason": "", "alerted": False, "recovered_at": None})
        if reason:
            st["fails"] += 1
            st["reason"] = reason
            st["recovered_at"] = None  # flapping inside one outage stays one outage
            if st["fails"] >= config.UPTIME_FAILS and not st["down_since"]:
                st["down_since"] = now.isoformat()
        else:
            st["fails"] = 0
            if st["down_since"] and not st["recovered_at"]:
                if st["alerted"]:
                    st["recovered_at"] = now.isoformat()
                else:
                    st = {"fails": 0, "down_since": None, "reason": "", "alerted": False, "recovered_at": None}
        out[url] = st
    return out


def pending(state: dict) -> tuple[list[str], list[str]]:
    """(urls to announce as down, urls to announce as back up)."""
    down = [u for u, s in state.items() if s.get("down_since") and not s.get("alerted") and not s.get("recovered_at")]
    up = [u for u, s in state.items() if s.get("recovered_at")]
    return down, up


def poll() -> tuple[list[str], list[str]]:
    """read kuma, save the state, return what is still to be announced. a kuma that can't be reached
    raises (the scheduler logs it) and changes nothing, so it never looks like every site went down."""
    mons = fetch()
    results = {n: ("" if m["status"] == 1 else (m["msg"] or "no error message") if m["status"] == 0 else None)
               for n, m in mons.items()}
    state = update(_load(), results, datetime.datetime.now(datetime.timezone.utc))
    for name, m in mons.items():
        if name in state:
            state[name]["type"], state[name]["target"] = m["type"], m["target"]
    _save(state)
    return pending(state)


def mark_sent(down: list[str], up: list[str]) -> None:
    state = _load()
    for u in down:
        if u in state:
            state[u]["alerted"] = True
    for u in up:
        if u in state:
            state[u] = {"fails": 0, "down_since": None, "reason": "", "alerted": False, "recovered_at": None}
    _save(state)


def _span(start: str, end: datetime.datetime) -> str:
    mins = max(1, int((end - datetime.datetime.fromisoformat(start)).total_seconds() // 60))
    return f"{mins // 60}h {mins % 60}m" if mins >= 60 else f"{mins}m"


def _what(st: dict) -> str:
    bits = [x for x in (st.get("type"), st.get("target")) if x]
    return f" ({', '.join(bits)})" if bits else ""


def format_facts(down: list[str], up: list[str]) -> str:
    """what just went down / came back, plus everything that is STILL down from earlier (so the dm can
    always say what is down right now)."""
    state, lines = _load(), []
    for u in down:
        s = state.get(u, {})
        since = datetime.datetime.fromisoformat(s["down_since"]).astimezone(AMSTERDAM_TZ).strftime("%H:%M")
        lines.append(f"- DOWN {u}{_what(s)}: since {since}, error: {s.get('reason') or 'unknown'}")
    for u in up:
        s = state.get(u, {})
        end = datetime.datetime.fromisoformat(s["recovered_at"])
        lines.append(f"- BACK UP {u}{_what(s)}: was down {_span(s['down_since'], end)}")
    still = [u for u, s in state.items()
             if s.get("down_since") and not s.get("recovered_at") and u not in down]
    for u in still:
        s = state[u]
        lines.append(f"- STILL DOWN {u}{_what(s)}: error: {s.get('reason') or 'unknown'}")
    return "\n".join(lines)


def compose_message(down: list[str], up: list[str]) -> str:
    facts = format_facts(down, up)
    remember_facts("uptime", facts)
    text = complete(
        "some of matthias's sites changed state (from his uptime kuma). tell him in your own voice, short and casual: "
        "exactly what is down (name, what it points at, the error, since when), what came back (and how long it was "
        "down), and what is STILL down. if several are down at once, say so in one go, it is probably one cause. "
        "keep every monitor name and url exactly as given. "
        "use ONLY these facts.\n\nfacts:\n" + facts,
        max_tokens=300,
    )
    # names are compared case-insensitively: the persona writes in lowercase ("beszel" for "Beszel")
    if text and all(u.lower() in text.lower() for u in down + up):
        return text
    return _plain_message(down, up)


def _plain_error(reason: str | None) -> str:
    """openssl/node errors come with hex ids and source paths; keep the readable part."""
    r = (reason or "").strip()
    m = re.search(r"SSL routines:[^:]*:([^:]+)", r)
    if m:
        r = "tls error: " + m.group(1).strip()
    if len(r) > 120:
        r = r[:120].rsplit(" ", 1)[0] + "..."
    return r or "unknown error"


def _plain_message(down: list[str], up: list[str]) -> str:
    """lowercase, human fallback for when the model call fails (no raw fact dump)."""
    state, parts = _load(), []
    for u in down:
        s = state.get(u, {})
        since = datetime.datetime.fromisoformat(s["down_since"]).astimezone(AMSTERDAM_TZ).strftime("%H:%M")
        parts.append(f"{u}{_what(s)} went down at {since}: {_plain_error(s.get('reason'))}")
    for u in up:
        s = state.get(u, {})
        end = datetime.datetime.fromisoformat(s["recovered_at"])
        parts.append(f"{u}{_what(s)} is back up after {_span(s['down_since'], end)}")
    still = [u for u, s in state.items() if s.get("down_since") and not s.get("recovered_at") and u not in down]
    if still:
        parts.append("still down: " + ", ".join(still))
    return "\n\n".join(parts)
