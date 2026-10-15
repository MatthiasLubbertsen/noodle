import datetime
import json
import logging
import urllib.parse
import urllib.request

import config
from bot.httpjson import USER_AGENT
from bot.tz import AMSTERDAM_TZ

logger = logging.getLogger("noodle")

API = "https://api.github.com"


def configured() -> bool:
    return bool(config.GITHUB_TOKEN)


def _get(path: str, params: dict | None = None):
    url = API + path + (f"?{urllib.parse.urlencode(params)}" if params else "")
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {config.GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        return json.loads(resp.read().decode("utf-8"))


def html_url(api_url: str | None, repo: str) -> str:
    """notification subjects only carry api urls - turn them into browsable ones."""
    if not api_url:
        return f"https://github.com/{repo}"
    return (
        api_url.replace("https://api.github.com/repos/", "https://github.com/")
        .replace("/pulls/", "/pull/")
        .replace("/commits/", "/commit/")
    )


def notifications() -> list[dict]:
    """unread notifications (needs a classic token with the `notifications`
    scope - fine-grained tokens can't read this endpoint)."""
    return _get("/notifications", {"all": "false", "per_page": 50})


def commits_today() -> tuple[int, list[str]]:
    """(commit count, repos) for matthias's commits today, via commit search."""
    today = datetime.datetime.now(AMSTERDAM_TZ).strftime("%Y-%m-%d")
    data = _get(
        "/search/commits",
        {"q": f"author:{config.GITHUB_USER} author-date:{today}", "per_page": 100},
    )
    repos = sorted({i["repository"]["full_name"] for i in data.get("items", [])})
    return data.get("total_count", 0), repos


def _repo(raw: str) -> str:
    raw = (raw or "").strip()
    return raw if "/" in raw else f"{config.GITHUB_USER}/{raw}"


def query(action: str, repo: str | None = None, q: str | None = None, state: str = "open") -> str:
    """the `github` tool's backend. returns a plain-text summary or an error string."""
    if not configured():
        return "github isn't configured (GITHUB_TOKEN missing)"
    try:
        if action == "repo":
            r = _get(f"/repos/{_repo(repo)}")
            return (
                f"{r['full_name']} - {r.get('description') or 'no description'}\n"
                f"language {r.get('language')}, {r['stargazers_count']} stars, "
                f"{r['open_issues_count']} open issues+prs, last push {r['pushed_at']}, "
                f"{'private' if r['private'] else 'public'}, {r['html_url']}"
            )
        if action == "commits":
            items = _get(f"/repos/{_repo(repo)}/commits", {"per_page": 8})
            return "\n".join(
                f"- {c['sha'][:7]} {c['commit']['author']['date']} "
                f"{c['commit']['message'].splitlines()[0]} ({c['html_url']})"
                for c in items
            ) or "no commits"
        if action in ("issues", "pulls"):
            items = _get(f"/repos/{_repo(repo)}/{action}", {"state": state, "per_page": 10})
            if action == "issues":  # the issues endpoint also returns prs
                items = [i for i in items if "pull_request" not in i]
            return "\n".join(
                f"- #{i['number']} {i['title']} ({i['user']['login']}, {i['html_url']})" for i in items
            ) or f"no {state} {action}"
        if action == "search":
            data = _get("/search/issues", {"q": q or "", "per_page": 8})
            return "\n".join(
                f"- {i['repository_url'].split('repos/')[-1]}#{i['number']} {i['title']} ({i['html_url']})"
                for i in data.get("items", [])
            ) or "no results"
        if action == "notifications":
            items = notifications()
            return "\n".join(
                f"- [{n['repository']['full_name']}] {n['subject']['title']} "
                f"({n['reason']}, {html_url(n['subject'].get('url'), n['repository']['full_name'])})"
                for n in items[:15]
            ) or "no unread notifications"
        if action == "today":
            count, repos = commits_today()
            return f"{count} commit(s) today" + (f" in {', '.join(repos)}" if repos else "")
    except Exception as exc:  # noqa: BLE001
        logger.exception("github %s failed", action)
        return f"github {action} failed: {exc}"
    return f"unknown github action: {action}"
