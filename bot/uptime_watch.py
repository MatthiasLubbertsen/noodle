import base64
import datetime
import json
import logging
import re
import urllib.request

import config
from bot.httpjson import USER_AGENT
from bot.oneshot import complete, remember_facts
from bot.tz import AMSTERDAM_TZ

logger = logging.getLogger("noodle")

# reads uptime kuma's prometheus /metrics (basic auth: empty user, the api key as password) and
# announces monitors that go down or come back. a monitor is "down" after UPTIME_FAILS polls in a row
# (kuma retries on its own, so 1). state per monitor name in data/uptime_state.json:
#   {"fails": n, "down_since": iso|None, "reason": str, "alerted": bool, "recovered_at": iso|None}
# an outage nobody was told about (it ended while held back by quiet hours) is dropped silently.
# kuma status codes: 0 down, 1 up, 2 pending, 3 maintenance. pending/maintenance change nothing.
TIMEOUT = 15
_LINE = re.compile(r"^monitor_status\{(?P<labels>.*)\}\s+(?P<value>-?\d+)\s*$")
_LABEL = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


def parse_metrics(text: str) -> dict[str, tuple[int, str]]:
    """{monitor name: (kuma status code, url or "")} from the /metrics text."""
    out = {}
    for line in text.splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        labels = dict(_LABEL.findall(m["labels"]))
        name = labels.get("monitor_name")
        if name:
            url = labels.get("monitor_url", "")
            out[name] = (int(m["value"]), "" if url in ("", "null") else url)
    return out


def fetch() -> dict[str, tuple[int, str]]:
    auth = base64.b64encode(f":{config.KUMA_API_KEY}".encode()).decode()
    req = urllib.request.Request(
        config.KUMA_URL + "/metrics", headers={"Authorization": f"Basic {auth}", "User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return parse_metrics(resp.read().decode("utf-8", "replace"))


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
    metrics = fetch()
    results = {name: ("" if code == 1 else "down in uptime kuma" if code == 0 else None) for name, (code, _) in metrics.items()}
    state = update(_load(), results, datetime.datetime.now(datetime.timezone.utc))
    for name, (_, url) in metrics.items():
        if name in state:
            state[name]["url"] = url
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


def _url(st: dict) -> str:
    return f" ({st['url']})" if st.get("url") else ""


def format_facts(down: list[str], up: list[str]) -> str:
    state, lines = _load(), []
    for u in down:
        s = state.get(u, {})
        since = datetime.datetime.fromisoformat(s["down_since"]).astimezone(AMSTERDAM_TZ).strftime("%H:%M")
        lines.append(f"- DOWN {u}{_url(s)}: since {since} ({s.get('reason') or 'unreachable'})")
    for u in up:
        s = state.get(u, {})
        end = datetime.datetime.fromisoformat(s["recovered_at"])
        lines.append(f"- BACK UP {u}{_url(s)}: was down {_span(s['down_since'], end)}")
    return "\n".join(lines)


def compose_message(down: list[str], up: list[str]) -> str:
    facts = format_facts(down, up)
    remember_facts("uptime", facts)
    text = complete(
        "some of matthias's sites changed state (from his uptime kuma). tell him in your own voice, short and casual: what is down "
        "(and why, since when) and what came back (and how long it was down). if several are down at "
        "once, say so in one go, it is probably one cause. keep every monitor name and url exactly as given. "
        "use ONLY these facts.\n\nfacts:\n" + facts,
        max_tokens=300,
    )
    if text and all(u in text for u in down + up):
        return text
    return "uptime:\n" + facts
