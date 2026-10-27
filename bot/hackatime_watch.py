import json
import logging

import config
from bot import hackatime
from bot.oneshot import complete, remember_facts

logger = logging.getLogger("noodle")

# every full hour matthias's all-time hackatime total for a project crosses gets a plain
# line in LOGS_CHANNEL_ID; every 5 hours also gets a persona-written post in his channel


def _load() -> dict | None:
    """{project: hours already reported}, or None on the very first run (no state yet)."""
    if not config.HACKATIME_SEEN_PATH.exists():
        return None
    try:
        return json.loads(config.HACKATIME_SEEN_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        logger.exception("failed to load hackatime seen-state, starting fresh")
        return None


def _save(seen: dict) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.HACKATIME_SEEN_PATH.write_text(json.dumps(seen), encoding="utf-8")


def new_milestones() -> list[tuple[str, int, int]]:
    """[(project, hours before, hours now)] for projects that crossed a full hour since
    the last check, and marks them reported. like the github watcher, the first run
    ever only baselines (an existing 300h project shouldn't announce 300 hours)."""
    totals = hackatime.project_totals()
    seen = _load()
    if seen is None:
        _save({name: int(seconds // 3600) for name, seconds in totals.items()})
        logger.info("hackatime watcher: baselined %d project(s)", len(totals))
        return []
    crossed = []
    for name, seconds in totals.items():
        hours, before = int(seconds // 3600), seen.get(name, 0)
        if hours > before:
            crossed.append((name, before, hours))
            seen[name] = hours
        # hackatime can recount a project slightly lower; never re-announce an hour
    if crossed:
        _save(seen)
    return crossed


def log_line(name: str, before: int, hours: int) -> str:
    jump = f" (was {before}h at the last check)" if hours - before > 1 else ""
    return f"hackatime: matthias hit {hours}h on `{name}`{jump}"


def five_hour_mark(before: int, hours: int) -> int | None:
    """the highest multiple of 5 crossed, if any."""
    mark = hours // 5 * 5
    return mark if mark > before and mark > 0 else None


def compose_milestone(name: str, hours: int) -> str:
    repo = hackatime.project_repo(name)
    facts = f"project: {name}\nall-time coding time on it: {hours} hours (hackatime)" + (f"\nrepo: {repo}" if repo else "")
    remember_facts("hackatime milestone", facts)
    text = complete(
        f"matthias just passed {hours} hours of coding on one of his projects. announce it in "
        "his own slack channel in your own voice: one or two short sentences, proud with a "
        "little teasing, no @mentions. keep the repo link exactly as given if there is one. "
        "use ONLY these facts.\n\nfacts:\n" + facts,
        max_tokens=120,
    )
    if text and (not repo or repo.removeprefix("https://") in text):
        return text
    return f"matthias just passed {hours}h on {name}!" + (f" {repo}" if repo else "")
