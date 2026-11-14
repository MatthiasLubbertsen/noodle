import datetime

import config
from bot.httpjson import get_json
from bot.tz import AMSTERDAM_TZ

# zermelo = the school timetable. read-only, matthias's own student token (ZERMELO_TOKEN, made by
# exchanging a one-time "koppel app" code at /api/v3/oauth/token) against his school's portal
# (ZERMELO_SCHOOL, the part before .zportal.nl). the school is PRIVATE like his location: it
# lives only in .env, never in code, docs, logs or a message.

# zermelo only knows the school's own abbreviations. these are the ones in his timetable, with
# their real (dutch) names. unknown codes are shown as they are.
SUBJECTS = {
    "ak": "aardrijkskunde",
    "bi": "biologie",
    "bo": "bewegingsonderwijs",
    "du": "duits",
    "ec": "economie",
    "en": "engels",
    "gd": "godsdienst",
    "gs": "geschiedenis",
    "ha": "handvaardigheid",
    "mu": "muziek",
    "na": "natuurkunde",
    "ne": "nederlands",
    "oo": "onderzoek & ontwerpen",
    "sk": "scheikunde",
    "te": "tekenen",
    "wi": "wiskunde",
    "coach": "coaching",
    "mt": "mentoruur",
    "lu": "leesuur",
    "act": "activiteit",
    "exc": "excursie",
}


def configured() -> bool:
    return bool(config.ZERMELO_SCHOOL and config.ZERMELO_TOKEN)


def subject_name(code: str) -> str:
    """the real (dutch) name for a zermelo subject code ("ak" -> "aardrijkskunde"). a "tts"
    prefix is a test: "ttswi" -> "toets wiskunde"."""
    key = (code or "").strip().lower()
    if key in SUBJECTS:
        return SUBJECTS[key]
    if key.startswith("tts") and key[3:] in SUBJECTS:
        return f"toets {SUBJECTS[key[3:]]}"
    return (code or "").strip() or "something"


def subjects_label(appointment: dict) -> str:
    names = [subject_name(code) for code in appointment.get("subjects") or []]
    return " + ".join(dict.fromkeys(names)) or "something"


def appointments(start: datetime.datetime, end: datetime.datetime) -> list[dict]:
    """his current (valid) appointments between two moments, cancelled ones included. raises on
    any failure, so a dead api never reads as "nothing is cancelled"."""
    body = get_json(
        f"https://{config.ZERMELO_SCHOOL}.zportal.nl/api/v3/appointments",
        params={
            "user": "~me",
            "start": int(start.timestamp()),
            "end": int(end.timestamp()),
            "valid": "true",
            "fields": "appointmentInstance,start,end,startTimeSlot,subjects,type,cancelled,valid,hidden,"
                      "changeDescription,remark,schedulerRemark,locations,teachers,moved,modified,new",
        },
        headers={"Authorization": f"Bearer {config.ZERMELO_TOKEN}"},
        timeout=15,
    )
    return body["response"]["data"]


def window(now: datetime.datetime | None = None) -> tuple[datetime.datetime, datetime.datetime]:
    """start of today until the end of tomorrow, amsterdam time."""
    now = now or datetime.datetime.now(AMSTERDAM_TZ)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + datetime.timedelta(days=2)


# ---------------------------------------------------------------------------------------------
# the `timetable` tool. matthias only (checked in tools.py); everything below is read-only.

ACTIONS = ("day", "week", "next", "changes", "exams", "stats")
_WORDS = {"today": 0, "vandaag": 0, "tomorrow": 1, "morgen": 1, "yesterday": -1, "gisteren": -1}


def _local(ts: int) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(ts, AMSTERDAM_TZ)


def _midnight(date: datetime.date) -> datetime.datetime:
    return datetime.datetime.combine(date, datetime.time(), AMSTERDAM_TZ)


def _date(value, now: datetime.datetime) -> datetime.date:
    """YYYY-MM-DD, today/tomorrow/yesterday, or nothing (= today)."""
    text = str(value or "").strip().lower()
    if not text:
        return now.date()
    if text in _WORDS:
        return now.date() + datetime.timedelta(days=_WORDS[text])
    return datetime.date.fromisoformat(text)


def _on(rows: list[dict]) -> list[dict]:
    """the appointments that actually take place, in order."""
    return sorted((r for r in rows if r.get("valid") and not r.get("cancelled") and not r.get("hidden")),
                  key=lambda r: r["start"])


def _times(r: dict) -> str:
    hour = f"hour {r['startTimeSlot']} " if r.get("startTimeSlot") else ""
    return f"{hour}{_local(r['start']):%H:%M}-{_local(r['end']):%H:%M}"


def _status(r: dict) -> str:
    """what changed about a lesson, in a few words (plus zermelo's own dutch sentence)."""
    if r.get("cancelled"):
        word = "DROPPED OUT"
    elif r.get("moved") or r.get("modified"):
        word = "CHANGED"
    elif r.get("new"):
        word = "NEW"
    else:
        return ""
    why = (r.get("changeDescription") or "").strip()
    return f" [{word}{': ' + why if why else ''}]"


def _describe(r: dict) -> str:
    parts = [subjects_label(r)]
    if r.get("type") not in (None, "lesson"):
        parts[0] += f" ({r['type']})"
    note = (r.get("remark") or r.get("schedulerRemark") or "").strip()
    if note:
        parts.append(f'"{note}"')
    if r.get("locations"):
        parts.append("room " + "/".join(r["locations"]))
    if r.get("teachers"):
        parts.append("teacher " + "/".join(r["teachers"]))
    return f"{_times(r)}: " + ", ".join(parts) + _status(r)


def _span(seconds: float) -> str:
    minutes = max(0, round(seconds / 60))
    hours, minutes = divmod(minutes, 60)
    if hours >= 48:
        return f"{hours // 24} days"
    return f"{hours}h {minutes}min" if hours else f"{minutes} min"


def _day_summary(rows: list[dict]) -> str:
    on = _on(rows)
    if not on:
        return "everything dropped out: a free day" if rows else "nothing on: a free day"
    text = f"starts {_local(on[0]['start']):%H:%M}, ends {_local(max(r['end'] for r in on)):%H:%M}, {len(on)} on"
    slots = sorted({r["startTimeSlot"] for r in on if r.get("startTimeSlot")})
    gaps = [str(n) for n in range(slots[0], slots[-1]) if n not in slots] if slots else []
    if gaps:
        text += f", free in between: hour {', '.join(gaps)}"
    dropped = sum(1 for r in rows if r.get("cancelled"))
    return text + (f", {dropped} dropped out" if dropped else "")


def _day_title(date: datetime.date, now: datetime.datetime) -> str:
    word = {0: "today, ", 1: "tomorrow, ", -1: "yesterday, "}.get((date - now.date()).days, "")
    return f"{word}{date:%A %d %B}".lower()


def _by_day(rows: list[dict]) -> dict:
    """date -> that day's current rows (cancelled ones included), in order."""
    days: dict = {}
    current = (r for r in rows if r.get("valid") and not r.get("hidden"))
    for r in sorted(current, key=lambda r: (r["start"], bool(r.get("cancelled")))):
        days.setdefault(_local(r["start"]).date(), []).append(r)
    return days


def _day(args: dict, now: datetime.datetime) -> str:
    date = _date(args.get("date"), now)
    rows = _by_day(appointments(_midnight(date), _midnight(date + datetime.timedelta(days=1)))).get(date, [])
    return "\n".join([f"{_day_title(date, now)}: {_day_summary(rows)}"] + [f"- {_describe(r)}" for r in rows])


def _week(args: dict, now: datetime.datetime) -> str:
    date = _date(args.get("date"), now)
    monday = date - datetime.timedelta(days=date.weekday())
    days = _by_day(appointments(_midnight(monday), _midnight(monday + datetime.timedelta(days=7))))
    lines = [f"week of monday {monday:%d %B}".lower()]
    for offset in range(5):
        day = monday + datetime.timedelta(days=offset)
        rows = days.get(day, [])
        lines.append(f"{_day_title(day, now)}: {_day_summary(rows)}")
        lines += [f"- {_describe(r)}" for r in rows]
    return "\n".join(lines)


def _next(now: datetime.datetime) -> str:
    rows = appointments(_midnight(now.date()), _midnight(now.date() + datetime.timedelta(days=10)))
    on, ts = _on(rows), now.timestamp()
    lines = [f"it is {now:%A %H:%M}".lower()]
    current = [r for r in on if r["start"] <= ts < r["end"]]
    for r in current:
        lines.append(f"now: {_describe(r)} (ends in {_span(r['end'] - ts)})")
    upcoming = [r for r in on if r["start"] > ts]
    if not upcoming:
        return "\n".join(lines + ["nothing on the timetable in the next 10 days"])
    first = upcoming[0]
    day = _local(first["start"]).date()
    lines.append(f"next ({_day_title(day, now)}): {_describe(first)} (starts in {_span(first['start'] - ts)})")
    today = [r for r in on if _local(r["start"]).date() == now.date() and r["end"] > ts]
    if today:
        end = max(r["end"] for r in today)
        lines.append(f"school is out today at {_local(end):%H:%M} (in {_span(end - ts)}), "
                     f"{len(today)} still to go, the current one included")
    else:
        lines.append("nothing (more) today")
    return "\n".join(lines)


def _changes(args: dict, now: datetime.datetime) -> str:
    days = min(max(int(args.get("days") or 7), 1), 60)
    rows = appointments(_midnight(now.date()), _midnight(now.date() + datetime.timedelta(days=days)))
    lines = []
    for day, items in _by_day(rows).items():
        changed = [r for r in items if _status(r)]
        if changed:
            lines.append(f"{_day_title(day, now)}:")
            lines += [f"- {_describe(r)}" for r in changed]
    return "\n".join(lines) or f"no changes in the next {days} days"


def _is_test(r: dict) -> bool:
    return r.get("type") == "exam" or any(str(c).lower().startswith("tts") for c in r.get("subjects") or [])


def _exams(args: dict, now: datetime.datetime) -> str:
    days = min(max(int(args.get("days") or 60), 1), 365)
    rows = appointments(now, _midnight(now.date() + datetime.timedelta(days=days)))
    lines = [
        f"- {_day_title(_local(r['start']).date(), now)} (in {_span(r['start'] - now.timestamp())}): {_describe(r)}"
        for r in _on(rows) if _is_test(r)
    ]
    return "\n".join(lines) or f"no tests on the timetable in the next {days} days"


def _stats(args: dict, now: datetime.datetime) -> str:
    """lessons planned (and their share of all lessons), dropped out and hours that went on, grouped
    per subject, week, weekday or hour of the day. default: per subject, this school year so far."""
    year_start = datetime.date(now.year if now.month >= 8 else now.year - 1, 8, 1)
    start = _date(args.get("start_date"), now) if args.get("start_date") else year_start
    end = _date(args.get("end_date"), now)
    if end < start:
        return "end_date is before start_date"
    rows = [r for r in appointments(_midnight(start), _midnight(end + datetime.timedelta(days=1)))
            if r.get("valid") and not r.get("hidden") and r.get("type") == "lesson"]
    if not rows:
        return f"no lessons between {start} and {end}"
    by = str(args.get("by") or "subject").strip().lower()
    if by not in STATS_BY:
        return f"unknown `by`, use one of: {', '.join(STATS_BY)}"
    per: dict = {}  # sort key -> [label, planned, dropped out, hours that went on]
    for r in rows:
        when = _local(r["start"])
        if by == "week":
            monday = when.date() - datetime.timedelta(days=when.weekday())
            key, label = monday, f"week {monday.isocalendar().week} (from {monday:%d %b})".lower()
        elif by == "weekday":
            key, label = when.weekday(), f"{when:%A}".lower()
        elif by == "hour":
            key, label = r.get("startTimeSlot") or 0, f"hour {r.get('startTimeSlot') or '?'}"
        else:
            key = label = subjects_label(r)
        entry = per.setdefault(key, [label, 0, 0, 0.0])
        entry[1] += 1
        if r.get("cancelled"):
            entry[2] += 1
        else:
            entry[3] += (r["end"] - r["start"]) / 3600
    total, dropped = len(rows), sum(e[2] for e in per.values())
    lines = [f"{start} to {end}, per {by}: {total} lessons planned, {dropped} dropped out "
             f"({_pct(dropped, total)}), {_hours(sum(e[3] for e in per.values()))} of lessons that went on"]
    # subjects: biggest first. weeks, weekdays and hours: in their own order
    order = sorted(per, key=lambda k: (-per[k][1], k)) if by == "subject" else sorted(per)
    for key in order:
        label, planned, gone, hours = per[key]
        lines.append(f"- {label}: {planned} planned ({_pct(planned, total)} of all lessons), "
                     f"{gone} dropped out ({_pct(gone, planned)}), {_hours(hours)} on")
    return "\n".join(lines)


STATS_BY = ("subject", "week", "weekday", "hour")


def _pct(part: float, whole: float) -> str:
    return f"{round(part * 100 / whole)}%" if whole else "0%"


def _hours(hours: float) -> str:
    return f"{hours:.1f}".rstrip("0").rstrip(".") + "h"


def query(args: dict, now: datetime.datetime | None = None) -> str:
    now = now or datetime.datetime.now(AMSTERDAM_TZ)
    action = str(args.get("action") or "day").strip().lower()
    try:
        if action == "day":
            return _day(args, now)
        if action == "week":
            return _week(args, now)
        if action == "next":
            return _next(now)
        if action == "changes":
            return _changes(args, now)
        if action == "exams":
            return _exams(args, now)
        if action == "stats":
            return _stats(args, now)
    except ValueError:
        return "dates must be YYYY-MM-DD (or today / tomorrow), days a number"
    except Exception as exc:  # noqa: BLE001
        return f"zermelo could not be read right now ({type(exc).__name__})"
    return f"unknown action, use one of: {', '.join(ACTIONS)}"
