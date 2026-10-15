import json
import logging

import config
from bot import github

logger = logging.getLogger("noodle")

# "subscribed" = merely watching a repo; far too noisy to dm about
IGNORED_REASONS = {"subscribed"}
MAX_LINES = 8


def _load_seen() -> dict | None:
    """{thread id: updated_at already dmed/baselined}, or None on the very
    first run (no state file yet)."""
    if not config.GITHUB_SEEN_PATH.exists():
        return None
    try:
        return json.loads(config.GITHUB_SEEN_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        logger.exception("failed to load github seen-state, starting fresh")
        return None


def _save_seen(seen: dict) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.GITHUB_SEEN_PATH.write_text(json.dumps(seen), encoding="utf-8")


def new_notifications() -> list[dict]:
    """unread notifications not yet reported. like the hack club news feed,
    the first run ever just baselines everything currently unread and
    reports nothing."""
    items = github.notifications()
    seen = _load_seen()
    if seen is None:
        _save_seen({n["id"]: n["updated_at"] for n in items})
        logger.info("github watcher: baselined %d existing notification(s)", len(items))
        return []
    return [
        n for n in items
        if n["reason"] not in IGNORED_REASONS and seen.get(n["id"]) != n["updated_at"]
    ]


def mark_seen(items: list[dict]) -> None:
    seen = _load_seen() or {}
    for n in items:
        seen[n["id"]] = n["updated_at"]
    _save_seen(seen)


def format_message(items: list[dict]) -> str:
    plural = "notification" if len(items) == 1 else "notifications"
    lines = [f"github: {len(items)} new {plural}"]
    for n in items[:MAX_LINES]:
        repo = n["repository"]["full_name"]
        url = github.html_url(n["subject"].get("url"), repo)
        reason = n["reason"].replace("_", " ")
        lines.append(f"- {repo}: {n['subject']['title']} ({reason}) {url}")
    if len(items) > MAX_LINES:
        lines.append(f"...and {len(items) - MAX_LINES} more")
    return "\n".join(lines)
