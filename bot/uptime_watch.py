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
# 3 maintenance; pending/maintenance change nothing. a monitor only counts as "down" (and gets a dm) once
# every poll has seen it down for UPTIME_DOWN_MINUTES in a row (default 5): a restart or a one-minute blip
# is never announced, in either direction. state per monitor name in data/uptime_state.json:
#   {"fails": n, "first_fail": iso|None, "down_since": iso|None, "reason": str, "alerted": bool,
#    "recovered_at": iso|None, "type": str, "target": str}
# first_fail = when the current run of failed polls began; down_since = the same moment, set only once the
# run has lasted long enough. an outage nobody was told about (too short, or it ended while held back by
# quiet hours) is dropped silently.
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


def _fresh() -> dict:
    return {"fails": 0, "first_fail": None, "down_since": None, "reason": "", "alerted": False, "recovered_at": None}


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
        st = dict(state.get(url) or _fresh())
        if reason:
            st["fails"] += 1
            st["reason"] = reason
            st["recovered_at"] = None  # flapping inside one outage stays one outage
            if not st["down_since"]:
                first = st.get("first_fail") or now.isoformat()
                st["first_fail"] = first
                waited = (now - datetime.datetime.fromisoformat(first)).total_seconds()
                if waited >= config.UPTIME_DOWN_MINUTES * 60:
                    st["down_since"] = first  # the real start, not the moment we decided to tell him
        else:
            st["fails"] = 0
            st["first_fail"] = None
            if st["down_since"] and not st["recovered_at"]:
                if st["alerted"]:
                    st["recovered_at"] = now.isoformat()
                else:
                    st = _fresh()
        out[url] = st
    # one outage = one dm: when something is due to be announced, everything that is failing right now
    # goes in the same message (real incident: two sites crossed the 5 minutes one poll after four others
    # and got their own dm). their "since" stays the real start of their failing run.
    if any(s.get("down_since") and not s.get("alerted") and not s.get("recovered_at") for s in out.values()):
        for s in out.values():
            if s.get("fails") and s.get("first_fail") and not s.get("down_since"):
                s["down_since"] = s["first_fail"]
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
    down, up = pending(state)
    if down or up:  # so the logs can say what noodle saw when a dm looks wrong
        logger.info("uptime poll: kuma says down=%s, back=%s",
                    [n for n, m in mons.items() if m["status"] == 0], up)
    return down, up


def mark_sent(down: list[str], up: list[str]) -> None:
    state = _load()
    for u in down:
        if u in state:
            state[u]["alerted"] = True
    for u in up:
        if u in state:
            state[u] = _fresh()
    _save(state)


def _span(start: str, end: datetime.datetime) -> str:
    mins = max(1, int((end - datetime.datetime.fromisoformat(start)).total_seconds() // 60))
    return f"{mins // 60}h {mins % 60}m" if mins >= 60 else f"{mins}m"


def _what(st: dict) -> str:
    bits = [x for x in (st.get("type"), st.get("target")) if x]
    return f" ({', '.join(bits)})" if bits else ""


def _since(s: dict) -> str:
    return datetime.datetime.fromisoformat(s["down_since"]).astimezone(AMSTERDAM_TZ).strftime("%H:%M")


def _groups(names: list[str], state: dict, key) -> list[tuple[str, list[str]]]:
    """names grouped by key(state), in first-seen order: sites that share a cause read as one line."""
    out: dict[str, list[str]] = {}
    for n in names:
        out.setdefault(key(state.get(n, {})), []).append(n)
    return list(out.items())


def _still_down(state: dict, skip: list[str]) -> list[str]:
    return [u for u, s in state.items() if s.get("down_since") and not s.get("recovered_at") and u not in skip]


def format_facts(down: list[str], up: list[str]) -> str:
    """what just went down / came back (grouped when they share a cause or duration), plus the names of
    everything that is STILL down. no urls or monitor types: the name is enough in a dm."""
    state, lines = _load(), []
    for key, names in _groups(down, state, lambda s: f"{_since(s)}|{_plain_error(s.get('reason'))}"):
        since, err = key.split("|", 1)
        lines.append(f"- DOWN since {since}, error: {err}: {', '.join(names)}")
    for took, names in _groups(up, state, lambda s: _span(s["down_since"], datetime.datetime.fromisoformat(s["recovered_at"]))):
        lines.append(f"- BACK UP after {took}: {', '.join(names)}")
    still = _still_down(state, down)
    if still:
        lines.append(f"- STILL DOWN: {', '.join(still)}")
    return "\n".join(lines)


def compose_message(down: list[str], up: list[str]) -> str:
    facts = format_facts(down, up)
    remember_facts("uptime", facts)
    text = complete(
        "some of matthias's sites changed state (from his uptime kuma). tell him in your own voice, in ONE or TWO "
        "short sentences, one paragraph, no lists: what went down (names, since when, the error once if it is the "
        "same for all), what came back (and after how long), and what is still down if anything. do NOT repeat "
        "yourself, do NOT write urls or monitor types, do NOT speculate about causes. write every monitor name "
        "exactly as given. use ONLY these facts.\n\nfacts:\n" + facts,
        max_tokens=120,
    )
    # names are compared case-insensitively: the persona writes in lowercase ("beszel" for "Beszel")
    if text and all(u.lower() in text.lower() for u in down + up):
        return re.sub(r"\n\s*\n", "\n", text)  # one slack message: a blank line would start a new one
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
    """lowercase, human fallback for when the model call fails. one short line per group of monitors that
    share a cause or duration, no blank lines, so it stays ONE slack message and a few lines at most."""
    state, parts = _load(), []
    for key, names in _groups(down, state, lambda s: f"{_since(s)}|{_plain_error(s.get('reason'))}"):
        since, err = key.split("|", 1)
        parts.append(f"{', '.join(names)} down since {since} ({err})")
    for took, names in _groups(up, state, lambda s: _span(s["down_since"], datetime.datetime.fromisoformat(s["recovered_at"]))):
        parts.append(f"{', '.join(names)} back up after {took}")
    still = _still_down(state, down)
    if still:
        parts.append("still down: " + ", ".join(still))
    return "\n".join(parts)
