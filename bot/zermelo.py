import datetime

import config
from bot.httpjson import get_json
from bot.tz import AMSTERDAM_TZ

# zermelo = the school timetable. read-only, matthias's own student token (ZERMELO_TOKEN, made by
# exchanging a one-time "koppel app" code at /api/v3/oauth/token) against his school's portal
# (ZERMELO_SCHOOL, the part before .zportal.nl). the school is PRIVATE like his location: it
# lives only in .env, never in code, docs, logs or a message outside his dm.

# zermelo only knows the school's own abbreviations. these are the ones in his timetable.
# unknown codes are shown as they are.
SUBJECTS = {
    "ak": "geography",
    "bi": "biology",
    "bo": "PE",
    "du": "german",
    "ec": "economics",
    "en": "english",
    "gd": "religion",
    "gs": "history",
    "ha": "crafts",
    "mu": "music",
    "na": "physics",
    "ne": "dutch",
    "oo": "O&O (research & design)",
    "sk": "chemistry",
    "te": "drawing",
    "wi": "maths",
    "coach": "coaching",
    "mt": "mentor hour",
    "act": "activity",
    "exc": "excursion",
}


def configured() -> bool:
    return bool(config.ZERMELO_SCHOOL and config.ZERMELO_TOKEN)


def subject_name(code: str) -> str:
    """the real name for a zermelo subject code ("ak" -> "geography"). "ttswi"-style codes
    (a prefix + a subject) keep the prefix: "maths (tts)"."""
    key = (code or "").strip().lower()
    if key in SUBJECTS:
        return SUBJECTS[key]
    if key.startswith("tts") and key[3:] in SUBJECTS:
        return f"{SUBJECTS[key[3:]]} (tts)"
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
            "fields": "appointmentInstance,start,end,startTimeSlot,subjects,type,cancelled,"
                      "valid,hidden,changeDescription,remark",
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
