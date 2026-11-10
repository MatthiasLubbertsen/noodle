import json
import logging
import os
import urllib.request

import config
from bot.httpjson import USER_AGENT
from bot.oneshot import complete, remember_facts

logger = logging.getLogger("noodle")

# release notes: the image is built by .github/workflows/docker.yml ONLY for a push to main (a
# merged pr is a push to main) or a tag, with the commit baked in as GIT_SHA. so "this container
# started with a sha it hasn't announced yet" means exactly: a new build was pushed, pulled and
# `docker compose up -d`'ed. a plain restart keeps the sha, a local `up --build` has no sha.
MAX_COMMITS = 15
MAX_FILES = 25
MAX_MESSAGE_CHARS = 500

PROMPT = (
    "you were just redeployed with a new version of yourself. tell matthias what changed, in your "
    "own voice, like a friend texting him 'hey i got updated': one short message, 2-4 sentences, "
    "no list, no headings. say what you can do or do differently now, in plain terms (the "
    "behaviour he'll notice, not file names, commit hashes, branch names or pr numbers). group "
    "related commits into one point and skip pure housekeeping (tests, lint, docs, refactors "
    "nobody can see) unless that's all there is, then just say it was an under-the-hood tidy-up. "
    "don't invent anything the changes don't say, and don't promise features that aren't in them. "
    "the changes are commit messages written by a developer for developers: they use jargon "
    "(case-insensitive, fallback, name check, regex, retry...) that is about the code, not about "
    "you or him. never guess what a vague word refers to: a 'name' or 'check' in a commit is "
    "not your name or something people type to you, and a 'fallback' is what you send when your "
    "own model call fails. this limits the FACTS only, never your personality: stay fully in your "
    "own casual, dry voice, just don't invent details. only state details the commit text says "
    "outright; when a change is too technical to explain without guessing, say in your own words "
    "that it's a small under-the-hood fix and stop. "
    "don't greet him and don't ask a question at the end.\n\nchanges:\n"
)


def build_sha() -> str:
    """the commit this image was built from, "" when unknown (local build, no GIT_SHA)."""
    sha = os.getenv("NOODLE_SHA", "").strip().lower()
    return sha if len(sha) >= 7 and all(c in "0123456789abcdef" for c in sha) else ""


def _get(path: str):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    if config.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {config.GITHUB_TOKEN}"
    req = urllib.request.Request(f"https://api.github.com/repos/{config.GITHUB_REPO}{path}", headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _load() -> dict:
    """{"sha": last build seen, "pending": the sha before it while its note is still unsent}."""
    try:
        data = json.loads(config.RELEASE_SEEN_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        logger.exception("failed to load release seen-state, starting fresh")
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.RELEASE_SEEN_PATH.write_text(json.dumps(data), encoding="utf-8")


def check() -> tuple[str, str] | None:
    """(old sha, new sha) when a build hasn't been announced yet, else None. the new sha is saved
    first, with the old one as `pending` until clear_pending(), so a restart while the note is held
    for quiet hours still sends it, and a crash after clear_pending() never sends it twice. the very
    first run only baselines."""
    sha = build_sha()
    if not sha:
        return None
    data = _load()
    seen = data.get("sha")
    if seen != sha:
        if not seen:
            _save({"sha": sha})
            logger.info("release watch: baselined at %s", sha[:7])
            return None
        data = {"sha": sha, "pending": seen}
        _save(data)
    pending = data.get("pending")
    return (pending, sha) if pending else None


def clear_pending() -> None:
    """called right before the dm goes out: from here on the note counts as sent."""
    _save({"sha": _load().get("sha", build_sha())})


def _format(commits: list[dict], files: list[dict]) -> str:
    lines = []
    for c in commits[-MAX_COMMITS:]:
        msg = (c.get("commit", {}).get("message") or "").strip()
        if msg:
            lines.append("- " + msg[:MAX_MESSAGE_CHARS].replace("\n\n", "\n  ").replace("\n", "\n  "))
    if len(commits) > MAX_COMMITS:
        lines.insert(0, f"(only the newest {MAX_COMMITS} of {len(commits)} commits)")
    names = [f.get("filename", "") for f in files[:MAX_FILES]]
    if names:
        lines.append("files touched: " + ", ".join(names) + (" ..." if len(files) > MAX_FILES else ""))
    return "\n".join(lines)


def changes(old: str, new: str) -> str:
    """what changed between two builds, from github's compare api; falls back to the newest
    commit alone when the old sha is unknown to github (force push). "" if nothing is readable."""
    try:
        data = _get(f"/compare/{old}...{new}")
        text = _format(data.get("commits") or [], data.get("files") or [])
        if text:
            return text
    except Exception:  # noqa: BLE001
        logger.exception("release watch: compare %s...%s failed", old[:7], new[:7])
    try:
        return _format([_get(f"/commits/{new}")], [])
    except Exception:  # noqa: BLE001
        logger.exception("release watch: could not read commit %s", new[:7])
        return ""


def compose_message(old: str, new: str) -> str | None:
    """the persona-written release note, or None when there's nothing readable to say (no dm
    then: a made-up or empty release note is worse than none)."""
    diff = changes(old, new)
    if not diff:
        return None
    remember_facts("release notes", f"deployed {old[:7]} -> {new[:7]}\n{diff}")
    text = complete(PROMPT + diff, max_tokens=250)
    return text or "i just got updated, but i can't put into words what changed. check the commits."
