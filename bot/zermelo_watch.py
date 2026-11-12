import datetime
import json
import logging
import re

import config
from bot import zermelo
from bot.oneshot import complete, remember_facts
from bot.tz import AMSTERDAM_TZ

logger = logging.getLogger("noodle")

# dms matthias when a lesson (or exam/activity) of TODAY or TOMORROW drops out of his timetable.
# zermelo keeps a cancelled lesson as a current row with cancelled=true ("Les vervalt"), so every
# poll reads today + tomorrow and reports each cancelled row it hasn't reported yet. there is no
# first-run baseline on purpose: a lesson that was cancelled days ago is still news the moment its
# day comes into view (tomorrow), and that is exactly when he wants to hear it.
# only cancellations: a moved lesson or another room is not reported.


def _key(appointment: dict) -> str:
    # appointmentInstance stays the same across versions of one lesson, id does not
    return f"{appointment.get('appointmentInstance')}:{appointment.get('start')}"


def _load() -> dict:
    """reported key -> start timestamp."""
    try:
        data = json.loads(config.ZERMELO_SEEN_PATH.read_text(encoding="utf-8"))
        return {str(k): int(v) for k, v in data.get("reported", {}).items()}
    except FileNotFoundError:
        return {}
    except Exception:
        logger.exception("failed to load zermelo seen-state, starting fresh")
        return {}


def _save(reported: dict) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.ZERMELO_SEEN_PATH.write_text(json.dumps({"reported": reported}), encoding="utf-8")


def dropouts(rows: list[dict], now: datetime.datetime, reported: dict) -> list[dict]:
    """the cancelled appointments not reported yet, soonest first. ones that are already over
    are skipped (nothing to do with that news anymore)."""
    out = [
        r for r in rows
        if r.get("valid") and r.get("cancelled") and not r.get("hidden")
        and r.get("end", 0) > now.timestamp() and _key(r) not in reported
    ]
    return sorted(out, key=lambda r: (r["start"], _key(r)))


def new_dropouts(now: datetime.datetime | None = None) -> tuple[list[dict], list[dict]]:
    """(new dropouts, every appointment of today + tomorrow). raises if zermelo can't be read."""
    now = now or datetime.datetime.now(AMSTERDAM_TZ)
    rows = zermelo.appointments(*zermelo.window(now))
    return dropouts(rows, now, _load()), rows


def mark_reported(items: list[dict], now: datetime.datetime | None = None) -> None:
    now = now or datetime.datetime.now(AMSTERDAM_TZ)
    oldest = zermelo.window(now)[0].timestamp()
    reported = {k: v for k, v in _load().items() if v >= oldest}  # past days are never asked about again
    reported.update({_key(r): int(r["start"]) for r in items})
    _save(reported)


def _local(ts: int) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(ts, AMSTERDAM_TZ)


def _day(ts: int, now: datetime.datetime) -> str:
    day = _local(ts)
    offset = (day.date() - now.date()).days
    word = {0: "today", 1: "tomorrow"}.get(offset, day.strftime("%A").lower())
    return f"{word} ({day.strftime('%A %d %B').lower()})"


def _slot(r: dict) -> str:
    hour = f"hour {r['startTimeSlot']}, " if r.get("startTimeSlot") else ""
    return f"{hour}{_local(r['start']):%H:%M}-{_local(r['end']):%H:%M}"


def _line(r: dict) -> str:
    kind = "" if r.get("type") in (None, "lesson") else f" ({r['type']})"
    return f"{_slot(r)}: {zermelo.subjects_label(r)}{kind}"


def format_facts(items: list[dict], rows: list[dict], now: datetime.datetime) -> str:
    """what dropped out, per day, plus what is still on that day (so "you start at 9:55 now" or
    "the whole day is gone" can be said without guessing)."""
    lines = []
    for date in sorted({_local(r["start"]).date() for r in items}):
        same_day = [r for r in rows if _local(r["start"]).date() == date]
        dropped = [r for r in items if _local(r["start"]).date() == date]
        lines.append(f"{_day(dropped[0]['start'], now)}, dropped out:")
        for r in dropped:
            why = (r.get("changeDescription") or r.get("remark") or "").strip()
            lines.append(f"- {_line(r)}" + (f" [zermelo: {why}]" if why else ""))
        left = sorted(
            (r for r in same_day if r.get("valid") and not r.get("cancelled") and not r.get("hidden")),
            key=lambda r: r["start"],
        )
        if left:
            lines.append("still on that day: " + "; ".join(_line(r) for r in left))
        else:
            lines.append("still on that day: nothing, the whole day is free")
    return "\n".join(lines)


def _plain_message(items: list[dict], now: datetime.datetime) -> str:
    return "\n".join(
        f"{zermelo.subjects_label(r)} dropped out {_day(r['start'], now).split(' (')[0]} ({_slot(r)})"
        for r in items
    )


def compose_message(items: list[dict], rows: list[dict], now: datetime.datetime | None = None) -> str:
    now = now or datetime.datetime.now(AMSTERDAM_TZ)
    facts = format_facts(items, rows, now)
    remember_facts("school timetable", facts)
    text = complete(
        "something just dropped out of matthias's school timetable (from zermelo). tell him in your own "
        "voice, short and casual: which lesson(s), which day and which hour/time. if it changes when his "
        "day starts or ends, or leaves a gap, say that too. never name his school. "
        f"it is now {now:%A %H:%M}. use ONLY these facts.\n\nfacts:\n" + facts,
        max_tokens=250,
    )
    names = {zermelo.subject_name(code).lower() for r in items for code in r.get("subjects") or []}
    if text and all(name in text.lower() for name in names):
        return re.sub(r"\n\s*\n", "\n", text)  # one slack message: a blank line would start a new one
    return _plain_message(items, now)
