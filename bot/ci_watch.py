import datetime
import json
import logging

import config
from bot import github
from bot.oneshot import complete, remember_facts

logger = logging.getLogger("noodle")

# failed github actions runs on his own repos (any branch, but not bots like dependabot), reported once
# per run. github's own `ci_activity` notification says the same thing with no detail and only if his
# notification settings send it, so github_watch ignores those and this replaces them.
MAX_REPOS = 15
MAX_LINES = 8
MAX_REMEMBERED = 400
MAX_AGE = datetime.timedelta(hours=24)  # a repo that just entered the recent list doesn't dump old failures


def _load() -> list | None:
    """run ids already reported, or None on the very first run (no state file yet)."""
    if not config.CI_SEEN_PATH.exists():
        return None
    try:
        return json.loads(config.CI_SEEN_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        logger.exception("failed to load ci seen-state, starting fresh")
        return None


def _save(ids: list) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.CI_SEEN_PATH.write_text(json.dumps(ids[-MAX_REMEMBERED:]), encoding="utf-8")


def _first_line(text: str | None) -> str:
    lines = (text or "").splitlines()
    return lines[0][:100] if lines else ""


def _recent(run: dict, now: datetime.datetime) -> bool:
    try:
        made = datetime.datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return True
    return now - made <= MAX_AGE


def new_failures() -> list[dict]:
    """failed runs not yet reported: {id, repo, workflow, branch, sha, message, actor, url}. the first
    run ever only baselines (what already failed is not news)."""
    now = datetime.datetime.now(datetime.timezone.utc)
    found = []
    for repo in github.recent_repos(MAX_REPOS):
        try:
            runs = github.failed_runs(repo)
        except Exception:  # noqa: BLE001  - one repo without actions (or a 404) must not stop the rest
            logger.debug("ci watcher: couldn't read runs of %s", repo, exc_info=True)
            continue
        for r in runs:
            actor = (r.get("actor") or {}).get("login", "")
            if actor.endswith("[bot]") or not _recent(r, now):
                continue
            found.append({
                "id": r["id"], "repo": repo, "workflow": r.get("name") or "workflow",
                "branch": r.get("head_branch") or "?", "sha": (r.get("head_sha") or "")[:7],
                "message": _first_line((r.get("head_commit") or {}).get("message")),
                "actor": actor, "url": r["html_url"],
            })
    seen = _load()
    if seen is None:
        _save([f["id"] for f in found])
        logger.info("ci watcher: baselined %d existing failed run(s)", len(found))
        return []
    return [f for f in found if f["id"] not in seen]


def mark_seen(items: list[dict]) -> None:
    _save((_load() or []) + [f["id"] for f in items])


def format_facts(items: list[dict]) -> str:
    lines = []
    for f in items[:MAX_LINES]:
        where = github.failed_step(f["repo"], f["id"])
        commit = f'commit {f["sha"]}' + (f' "{f["message"]}"' if f["message"] else "")
        lines.append(
            f'- {f["repo"]}: "{f["workflow"]}" failed on {f["branch"]} ({commit}, by {f["actor"] or "someone"})'
            f'{"; " + where if where else ""} {f["url"]}'
        )
    if len(items) > MAX_LINES:
        lines.append(f"...and {len(items) - MAX_LINES} more")
    return "\n".join(lines)


def compose_message(items: list[dict]) -> str:
    """persona-written from the facts, links kept verbatim; plain facts if the model call fails."""
    facts = format_facts(items)
    remember_facts("failed ci runs", facts)
    text = complete(
        "ci just failed on some of matthias's repos. tell him in your own voice, short and casual: "
        "which repo and branch, and what broke (the failed job/step if the facts have it). "
        "keep every link exactly as given. use ONLY these facts.\n\nfacts:\n" + facts,
        max_tokens=300,
    )
    urls = [w for line in facts.splitlines() for w in line.split() if w.startswith("https://")]
    if text and all(u in text for u in urls):
        return text
    return "ci failed:\n" + facts
