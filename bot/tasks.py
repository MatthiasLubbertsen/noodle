import calendar
import datetime
import logging
import sqlite3
import threading
import time

import config
from bot.tz import AMSTERDAM_TZ

logger = logging.getLogger("noodle")

ACTIONS = ("remind", "freedom_session")
REPEATS = ("none", "every_n_minutes", "daily", "weekdays", "weekly", "monthly")
MIN_INTERVAL_MINUTES = 5
MAX_ACTIVE_TASKS = 50

# all writes go through one lock - the poller thread and tool calls (one
# thread per slack message) both touch this table.
_LOCK = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.MEMORY_DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_table() -> None:
    """create the scheduled_tasks table (same sqlite file as memory)."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS scheduled_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action TEXT NOT NULL,
                    text TEXT,
                    duration_minutes INTEGER,
                    channel TEXT NOT NULL,
                    thread_ts TEXT,
                    repeat TEXT NOT NULL,
                    interval_minutes INTEGER,
                    anchor TEXT NOT NULL,
                    next_run_ts REAL NOT NULL,
                    created_by TEXT,
                    created_at TEXT NOT NULL,
                    last_run_ts REAL,
                    run_count INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_due ON scheduled_tasks (active, next_run_ts)"
            )
            conn.commit()
        finally:
            conn.close()


def parse_local(value: str) -> datetime.datetime | None:
    """parse 'YYYY-MM-DD HH:MM' (or with a T) as Europe/Amsterdam wall time."""
    value = (value or "").strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.datetime.strptime(value, fmt).replace(tzinfo=AMSTERDAM_TZ)
        except ValueError:
            continue
    return None


def next_after(
    anchor: datetime.datetime, repeat: str, interval_minutes: int | None,
    after: datetime.datetime,
) -> datetime.datetime | None:
    """first occurrence strictly after `after`, or None for a one-shot.

    daily/weekdays/weekly/monthly are wall-clock in Amsterdam (so "9:00"
    stays 9:00 across dst changes) and keyed off the anchor (the first
    occurrence), which is what keeps e.g. "monthly on the 31st" from
    drifting down to the 30th for good after one short month.
    """
    if repeat == "every_n_minutes":
        if not interval_minutes:
            return None
        step = datetime.timedelta(minutes=interval_minutes)
        # real elapsed time, not wall-clock: do the math in utc
        a = anchor.astimezone(datetime.timezone.utc)
        n = after.astimezone(datetime.timezone.utc)
        if n < a:
            return anchor
        k = int((n - a) / step) + 1
        return (a + k * step).astimezone(AMSTERDAM_TZ)

    after_local = after.astimezone(AMSTERDAM_TZ)
    if repeat in ("daily", "weekdays", "weekly"):
        start = max(after_local.date(), anchor.date())
        for i in range(400):
            d = start + datetime.timedelta(days=i)
            if repeat == "weekdays" and d.weekday() >= 5:
                continue
            if repeat == "weekly" and d.weekday() != anchor.weekday():
                continue
            cand = datetime.datetime.combine(d, anchor.time(), tzinfo=AMSTERDAM_TZ)
            if cand > after:
                return cand
        return None

    if repeat == "monthly":
        start = max(after_local.date(), anchor.date())
        y, m = start.year, start.month
        for _ in range(60):
            day = min(anchor.day, calendar.monthrange(y, m)[1])
            cand = datetime.datetime.combine(
                datetime.date(y, m, day), anchor.time(), tzinfo=AMSTERDAM_TZ
            )
            if cand > after:
                return cand
            m += 1
            if m > 12:
                y, m = y + 1, 1
        return None

    return None


def add_task(
    action: str,
    text: str | None,
    duration_minutes: int | None,
    channel: str,
    thread_ts: str | None,
    repeat: str,
    interval_minutes: int | None,
    first: datetime.datetime,
    created_by: str,
) -> dict | None:
    """insert a task. returns {"id", "next_run"} or None if at the cap."""
    now = datetime.datetime.now(AMSTERDAM_TZ)
    next_run = first if first > now else next_after(first, repeat, interval_minutes, now)
    if next_run is None:
        return None
    with _LOCK:
        conn = _connect()
        try:
            active = conn.execute(
                "SELECT COUNT(*) FROM scheduled_tasks WHERE active = 1"
            ).fetchone()[0]
            if active >= MAX_ACTIVE_TASKS:
                return None
            cur = conn.execute(
                "INSERT INTO scheduled_tasks (action, text, duration_minutes, channel, "
                "thread_ts, repeat, interval_minutes, anchor, next_run_ts, created_by, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    action, text, duration_minutes, channel, thread_ts, repeat,
                    interval_minutes, first.isoformat(), next_run.timestamp(),
                    created_by, now.isoformat(),
                ),
            )
            conn.commit()
            return {"id": cur.lastrowid, "next_run": next_run}
        finally:
            conn.close()


def list_active() -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM scheduled_tasks WHERE active = 1 ORDER BY next_run_ts"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_task(task_id: int) -> bool:
    with _LOCK:
        conn = _connect()
        try:
            cur = conn.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def pop_due(now_ts: float | None = None) -> list[dict]:
    """return every due active task as it was, and advance them in the db.

    advancing (or deactivating a one-shot) happens BEFORE the caller runs
    anything, so a crash mid-run drops one firing instead of re-firing the
    same reminder on every restart. recurring tasks skip any occurrences
    missed while noodle was down and resume at the next future one.
    """
    now_ts = time.time() if now_ts is None else now_ts
    now = datetime.datetime.fromtimestamp(now_ts, AMSTERDAM_TZ)
    with _LOCK:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT * FROM scheduled_tasks WHERE active = 1 AND next_run_ts <= ? "
                "ORDER BY next_run_ts",
                (now_ts,),
            ).fetchall()
            due = [dict(r) for r in rows]
            for t in due:
                nxt = next_after(
                    datetime.datetime.fromisoformat(t["anchor"]),
                    t["repeat"], t["interval_minutes"], now,
                )
                if nxt is None:
                    conn.execute(
                        "UPDATE scheduled_tasks SET active = 0, last_run_ts = ?, "
                        "run_count = run_count + 1 WHERE id = ?",
                        (now_ts, t["id"]),
                    )
                else:
                    conn.execute(
                        "UPDATE scheduled_tasks SET next_run_ts = ?, last_run_ts = ?, "
                        "run_count = run_count + 1 WHERE id = ?",
                        (nxt.timestamp(), now_ts, t["id"]),
                    )
            conn.commit()
            return due
        finally:
            conn.close()


def describe_destination(task: dict) -> str:
    if task["channel"] == config.USER_ID:
        return "dms"
    where = f"<#{task['channel']}>"
    return f"{where} (in a thread)" if task.get("thread_ts") else where


def describe(task: dict) -> str:
    when = datetime.datetime.fromtimestamp(task["next_run_ts"], AMSTERDAM_TZ)
    if task["action"] == "freedom_session":
        what = f"{task['duration_minutes']}-minute freedom session"
    else:
        what = f"remind: {task['text']}"
    repeat = task["repeat"]
    if repeat == "none":
        rep = "one-off"
    elif repeat == "every_n_minutes":
        rep = f"repeats every {task['interval_minutes']} minutes"
    else:
        rep = f"repeats {repeat}"
    return (
        f"#{task['id']} {what} | next: {when.strftime('%a %Y-%m-%d %H:%M')} "
        f"(amsterdam) | {rep} | in {describe_destination(task)}"
    )
