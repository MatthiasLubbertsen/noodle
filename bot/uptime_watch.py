import concurrent.futures
import datetime
import json
import logging
import socket
import ssl
import urllib.error
import urllib.request

import config
from bot.httpjson import USER_AGENT
from bot.oneshot import complete, remember_facts
from bot.tz import AMSTERDAM_TZ

logger = logging.getLogger("noodle")

# pings every url in UPTIME_URLS. a url is "down" after UPTIME_FAILS failed probes in a row (5xx, timeout,
# dns/tls error; a 404 or 401 still means something answered). state per url in data/uptime_state.json:
#   {"fails": n, "down_since": iso|None, "reason": str, "alerted": bool, "recovered_at": iso|None}
# an outage nobody was told about (it ended while held back by quiet hours) is dropped silently.
TIMEOUT = 10


def targets() -> list[str]:
    return [u if "://" in u else f"https://{u}" for u in config.UPTIME_URLS]


def probe(url: str) -> str:
    """"" if the url answers, else a short reason it is down."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            resp.read(1)
        return ""
    except urllib.error.HTTPError as e:
        return f"http {e.code}" if e.code >= 500 else ""
    except urllib.error.URLError as e:
        reason = e.reason
        if isinstance(reason, ssl.SSLCertVerificationError):
            return "certificate error"
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return "timed out"
        if isinstance(reason, socket.gaierror):
            return "dns lookup failed"
        return str(reason)[:80] or "unreachable"
    except (socket.timeout, TimeoutError):
        return "timed out"
    except Exception as e:  # noqa: BLE001
        return type(e).__name__


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
    """fold one round of probe results ({url: "" or reason}) into the state."""
    out = {}
    for url, reason in results.items():
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
    """probe everything, save the state, return what is still to be announced."""
    urls = targets()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = dict(zip(urls, pool.map(probe, urls)))
    state = update(_load(), results, datetime.datetime.now(datetime.timezone.utc))
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


def format_facts(down: list[str], up: list[str]) -> str:
    state, lines = _load(), []
    for u in down:
        s = state.get(u, {})
        since = datetime.datetime.fromisoformat(s["down_since"]).astimezone(AMSTERDAM_TZ).strftime("%H:%M")
        lines.append(f"- DOWN {u}: since {since} ({s.get('reason') or 'unreachable'})")
    for u in up:
        s = state.get(u, {})
        end = datetime.datetime.fromisoformat(s["recovered_at"])
        lines.append(f"- BACK UP {u}: was down {_span(s['down_since'], end)}")
    return "\n".join(lines)


def compose_message(down: list[str], up: list[str]) -> str:
    facts = format_facts(down, up)
    remember_facts("uptime", facts)
    text = complete(
        "matthias's sites changed state. tell him in your own voice, short and casual: what is down "
        "(and why, since when) and what came back (and how long it was down). if several are down at "
        "once, say so in one go, it is probably one cause. keep every url exactly as given. "
        "use ONLY these facts.\n\nfacts:\n" + facts,
        max_tokens=300,
    )
    if text and all(u in text for u in down + up):
        return text
    return "uptime:\n" + facts
