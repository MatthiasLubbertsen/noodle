import datetime
import logging
import urllib.error
import urllib.parse

import config
from bot import features
from bot.httpjson import get_json
from bot.tz import AMSTERDAM_TZ

logger = logging.getLogger("noodle")

# matthias's coding time from hackatime (hack club's wakatime). the `hackatime` tool is
# public on purpose, so anyone can ask how much he coded. the key is only needed when his
# hackatime stats aren't public; the username alone is enough otherwise.
BASE_URL = "https://hackatime.hackclub.com/api/v1/users"
MAX_PROJECTS = 40
# the spans endpoint returns every heartbeat span, so keep day-by-day lookups to a year
MAX_DAYS = 366
# spans closer together than this are one coding session
SESSION_GAP = datetime.timedelta(minutes=15)
ALL_TIME = datetime.date(2016, 1, 1)  # before hackatime (and its wakatime imports) existed


def configured() -> bool:
    return features.enabled("hackatime")


def _get(path: str, params: dict | None = None):
    headers = {"Authorization": f"Bearer {config.HACKATIME_API_KEY}"} if config.HACKATIME_API_KEY else None
    user = urllib.parse.quote(config.HACKATIME_USERNAME, safe="")
    return get_json(f"{BASE_URL}/{user}{path}", params, headers=headers, timeout=25)


def _dur(seconds: float) -> str:
    minutes = int(seconds // 60)
    return f"{minutes // 60}h {minutes % 60}m" if minutes >= 60 else f"{minutes}m"


def _day(value: str | None) -> datetime.date | None:
    return datetime.date.fromisoformat(value) if value else None


def _range(start: datetime.date | None, end: datetime.date | None) -> dict:
    """hackatime takes datetimes; whole amsterdam days, end date included."""
    params = {}
    if start:
        params["start_date"] = datetime.datetime.combine(start, datetime.time(), AMSTERDAM_TZ).isoformat()
    if end:
        next_day = end + datetime.timedelta(days=1)
        params["end_date"] = datetime.datetime.combine(next_day, datetime.time(), AMSTERDAM_TZ).isoformat()
    return params


def project_totals() -> dict[str, float]:
    """{project: all-time seconds} for every project ever (used by the milestone watcher)."""
    data = _get("/stats", {"features": "projects"})["data"]
    return {p["name"]: p["total_seconds"] for p in data.get("projects") or []}


def project_repo(name: str) -> str | None:
    try:
        return _get(f"/project/{urllib.parse.quote(name, safe='')}").get("repo_url")
    except Exception:  # noqa: BLE001
        return None


def _top(items: list[dict], n: int) -> str:
    return ", ".join(f"{i['name']} {_dur(i['total_seconds'])}" for i in (items or [])[:n]) or "none"


def _summary(start, end, project: str | None) -> str:
    params = {"features": "projects,languages", **_range(start, end)}
    if project:
        params["filter_by_project"] = project
    d = _get("/stats", params)["data"]
    when = f"{start or 'the beginning'} to {end or 'today'}" if (start or end) else "all time"
    lines = [
        f"hackatime, {when}" + (f", project {project}" if project else "") + f": {_dur(d.get('total_seconds', 0))} total, "
        f"{_dur(d.get('daily_average', 0))} daily average, current streak {d.get('streak', 0)} day(s)",
        f"projects: {_top(d.get('projects'), 10)}",
        f"languages: {_top(d.get('languages'), 8)}",
    ]
    return "\n".join(lines)


def _projects(query: str | None) -> str:
    data = _get("/stats", {"features": "projects"})["data"]
    projects = sorted(data.get("projects") or [], key=lambda p: -p["total_seconds"])
    if query:
        projects = [p for p in projects if query.lower() in p["name"].lower()]
    if not projects:
        return f"no hackatime projects matching {query!r}" if query else "no hackatime projects"
    lines = [f"- {p['name']}: {_dur(p['total_seconds'])}" for p in projects[:MAX_PROJECTS]]
    if len(projects) > MAX_PROJECTS:
        lines.append(f"...and {len(projects) - MAX_PROJECTS} more (search with query to narrow it down)")
    return f"{len(projects)} project(s), all-time time each:\n" + "\n".join(lines)


def _project(name: str, start, end) -> str:
    if not name:
        return "which project? give its name (the projects action lists them)"
    # without a start this endpoint only covers a recent stretch, not all time
    p = _get(f"/project/{urllib.parse.quote(name, safe='')}", _range(start or ALL_TIME, end))
    when = f" ({start or 'the beginning'} to {end or 'today'})" if (start or end) else " (all time)"
    first, last = (p.get("first_heartbeat") or "")[:10], (p.get("last_heartbeat") or "")[:10]
    return (
        f"{p['name']}{when}: {_dur(p.get('total_seconds', 0))}, worked on from {first or '?'} to {last or '?'}"
        f"{', archived' if p.get('archived') else ''}\n"
        f"repo: {p.get('repo_url') or 'none linked'}\n"
        f"languages: {', '.join((p.get('languages') or [])[:15]) or 'none'}"
    )


def _spans(start: datetime.date, end: datetime.date, project: str | None) -> list[tuple[datetime.datetime, datetime.datetime]]:
    params = _range(start, end)
    if project:
        params["project"] = project
    return [
        (
            datetime.datetime.fromtimestamp(s["start_time"], AMSTERDAM_TZ),
            datetime.datetime.fromtimestamp(s["start_time"] + s["duration"], AMSTERDAM_TZ),
        )
        for s in _get("/heartbeats/spans", params).get("spans") or []
    ]


def _days(start, end, project: str | None) -> str:
    today = datetime.datetime.now(AMSTERDAM_TZ).date()
    end = end or today
    start = start or end - datetime.timedelta(days=6)
    if (end - start).days >= MAX_DAYS:
        return f"that's more than {MAX_DAYS} days for a day-by-day list; use summary for long ranges, or a shorter range"
    totals: dict = {}
    monthly = (end - start).days > 92  # a day per line gets too long, group by month instead
    for begin, finish in _spans(start, end, project):
        key = begin.strftime("%Y-%m") if monthly else begin.date().isoformat()
        totals[key] = totals.get(key, 0) + (finish - begin).total_seconds()
    if not totals:
        return f"no coding time between {start} and {end}" + (f" on {project}" if project else "")
    best = max(totals, key=totals.get)
    lines = [f"- {k}: {_dur(v)}" for k, v in sorted(totals.items())]
    return (
        f"coding time per {'month' if monthly else 'day'}, {start} to {end}"
        + (f", project {project}" if project else "")
        + f" (days with nothing are left out; most: {best}, {_dur(totals[best])}):\n"
        + "\n".join(lines)
    )


def _sessions(day: datetime.date, project: str | None) -> str:
    spans = sorted(_spans(day, day, project))
    if not spans:
        return f"no coding on {day}" + (f" on {project}" if project else "")
    sessions = [list(spans[0])]
    for begin, finish in spans[1:]:
        if begin - sessions[-1][1] <= SESSION_GAP:
            sessions[-1][1] = max(sessions[-1][1], finish)
        else:
            sessions.append([begin, finish])
    total = sum((f - b).total_seconds() for b, f in spans)
    lines = [f"- {b:%H:%M}-{f:%H:%M}" for b, f in sessions]
    return f"coding sessions on {day} ({_dur(total)} of actual coding" + (f", project {project}" if project else "") + "):\n" + "\n".join(lines)


# his daily rhythm (which days, which hours) is for him only; totals and projects are public
OWNER_ONLY = ("days", "sessions")


def query(args: dict, is_owner: bool = False) -> str:
    """the `hackatime` tool's backend. returns plain text or an error string.
    is_owner: the real sender is matthias (days/sessions show when he codes)."""
    if not configured():
        return "hackatime isn't configured (HACKATIME_USERNAME missing)"
    action = args.get("action") or "summary"
    if action in OWNER_ONLY and not is_owner:
        return f"only matthias can see {action} (they show when he codes); totals, projects and summaries are open to anyone"
    project = (args.get("project") or "").strip() or None
    today = datetime.datetime.now(AMSTERDAM_TZ).date()
    try:
        start = _day(args.get("start_date"))
        end = _day(args.get("end_date"))
        if action == "today":
            return _summary(today, today, project)
        if action == "summary":
            return _summary(start, end, project)
        if action == "projects":
            return _projects(args.get("query"))
        if action == "project":
            return _project(project or args.get("query") or "", start, end)
        if action == "days":
            return _days(start, end, project)
        if action == "sessions":
            return _sessions(start or today, project)
    except ValueError:
        return "dates have to be YYYY-MM-DD"
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return "not found on hackatime (check the project name; the projects action lists them)"
        if exc.code == 403:
            return "his hackatime stats aren't public and HACKATIME_API_KEY isn't set or is wrong"
        logger.exception("hackatime %s failed", action)
        return f"hackatime {action} failed: {exc}"
    except Exception as exc:  # noqa: BLE001
        logger.exception("hackatime %s failed", action)
        return f"hackatime {action} failed: {exc}"
    return f"unknown hackatime action: {action}"
