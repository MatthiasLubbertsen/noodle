import base64
import datetime
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

import config
from bot import features
from bot.httpjson import USER_AGENT
from bot.tz import AMSTERDAM_TZ

logger = logging.getLogger("noodle")

API = "https://api.github.com"


def configured() -> bool:
    return features.enabled("github")


def _get(path: str, params: dict | None = None, accept: str = "application/vnd.github+json"):
    url = API + path + (f"?{urllib.parse.urlencode(params)}" if params else "")
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {config.GITHUB_TOKEN}",
            "Accept": accept,
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


MAX_FILE_CHARS = 12000
MAX_TREE_PATHS = 300


def _text(content: dict) -> str:
    """the decoded text of a contents-api file, cut to MAX_FILE_CHARS."""
    if content.get("encoding") != "base64" or not content.get("content"):
        return f"(too big to read through the api, {content.get('size', 0)} bytes: {content.get('html_url')})"
    try:
        text = base64.b64decode(content["content"]).decode("utf-8")
    except UnicodeDecodeError:
        return f"(binary file, {content.get('size', 0)} bytes: {content.get('html_url')})"
    if len(text) > MAX_FILE_CHARS:
        text = text[:MAX_FILE_CHARS] + f"\n...(cut off, {len(text)} chars total: {content.get('html_url')})"
    return text


def _stars(repo: str | None) -> str:
    if not repo:
        # his own repos (incl. private), most starred first
        repos = _get("/user/repos", {"affiliation": "owner", "per_page": 100, "sort": "pushed"})
        repos = sorted(repos, key=lambda r: -r["stargazers_count"])
        total = sum(r["stargazers_count"] for r in repos)
        lines = [f"- {r['full_name']}: {r['stargazers_count']}" for r in repos if r["stargazers_count"]][:20]
        return f"{total} stars across his {len(repos)} repos (100 most recently pushed):\n" + ("\n".join(lines) or "none starred yet")
    r = _get(f"/repos/{_repo(repo)}")
    count = r["stargazers_count"]
    recent = []
    if count:
        # stargazers come oldest first, so read the last page for the newest ones
        last_page = (count - 1) // 10 + 1
        try:
            page = _get(
                f"/repos/{r['full_name']}/stargazers", {"per_page": 10, "page": last_page},
                accept="application/vnd.github.star+json",
            )
            recent = [f"{s['user']['login']} ({s['starred_at'][:10]})" for s in reversed(page)]
        except urllib.error.HTTPError:
            logger.warning("couldn't list stargazers of %s, giving just the count", r["full_name"])
    return f"{r['full_name']} has {count} stars" + (f"; newest: {', '.join(recent)}" if recent else "")


def _issue(repo: str, number) -> str:
    i = _get(f"/repos/{repo}/issues/{int(number)}")
    kind = "pr" if "pull_request" in i else "issue"
    lines = [
        f"{kind} #{i['number']} {i['title']} by {i['user']['login']}, {i['state']}, opened {i['created_at'][:10]}, {i['html_url']}",
        (i.get("body") or "(no description)")[:3000],
    ]
    if kind == "pr":
        pr = _get(f"/repos/{repo}/pulls/{int(number)}")
        lines.append(
            f"{'merged' if pr.get('merged') else 'not merged'}, {pr['head']['ref']} -> {pr['base']['ref']}, "
            f"+{pr['additions']} -{pr['deletions']} in {pr['changed_files']} file(s)"
        )
        files = _get(f"/repos/{repo}/pulls/{int(number)}/files", {"per_page": 30})
        lines += [f"  {f['filename']} (+{f['additions']} -{f['deletions']})" for f in files]
    comments = []
    if i.get("comments"):
        # comments come oldest first, so read the last page to show the newest ones
        last_page = (i["comments"] - 1) // 100 + 1
        comments = _get(f"/repos/{repo}/issues/{int(number)}/comments", {"per_page": 100, "page": last_page})
    for c in comments[-10:]:
        lines.append(f"comment by {c['user']['login']} ({c['created_at'][:10]}): {c['body'][:800]}")
    return "\n".join(lines)


def _commit(repo: str, ref: str) -> str:
    c = _get(f"/repos/{repo}/commits/{urllib.parse.quote(ref, safe='')}")
    stats = c.get("stats") or {}
    lines = [
        f"{c['sha'][:7]} by {c['commit']['author']['name']} on {c['commit']['author']['date'][:10]}, "
        f"+{stats.get('additions', 0)} -{stats.get('deletions', 0)}, {c['html_url']}",
        c["commit"]["message"][:2000],
    ]
    lines += [f"  {f['filename']} ({f['status']}, +{f['additions']} -{f['deletions']})" for f in (c.get("files") or [])[:30]]
    return "\n".join(lines)


def query(action: str, repo: str | None = None, q: str | None = None, state: str = "open",
          path: str | None = None, ref: str | None = None, number=None) -> str:
    """the `github` tool's backend. returns a plain-text summary or an error string."""
    if not configured():
        return "github isn't configured (GITHUB_TOKEN missing)"
    full = _repo(repo) if repo else None
    ref_param = {"ref": ref} if ref else None
    try:
        if action in ("repo", "commits", "issues", "pulls", "readme", "tree", "file", "issue",
                      "commit", "releases", "actions", "branches") and not full:
            return f"github {action} needs a repo"
        if action == "repo":
            r = _get(f"/repos/{full}")
            return (
                f"{r['full_name']} - {r.get('description') or 'no description'}\n"
                f"language {r.get('language')}, {r['stargazers_count']} stars, {r['forks_count']} forks, "
                f"{r['open_issues_count']} open issues+prs, default branch {r['default_branch']}, "
                f"last push {r['pushed_at']}, {'private' if r['private'] else 'public'}, {r['html_url']}"
            )
        if action == "repos":
            items = _get("/user/repos", {"affiliation": "owner", "sort": "pushed", "per_page": 30})
            return "\n".join(
                f"- {r['full_name']}{' (private)' if r['private'] else ''}: {r.get('description') or 'no description'} "
                f"({r['stargazers_count']} stars, pushed {r['pushed_at'][:10]})"
                for r in items
            ) or "no repos"
        if action == "commits":
            params = {"per_page": 8, **({"path": path} if path else {}), **({"sha": ref} if ref else {})}
            items = _get(f"/repos/{full}/commits", params)
            return "\n".join(
                f"- {c['sha'][:7]} {c['commit']['author']['date']} "
                f"{c['commit']['message'].splitlines()[0]} ({c['html_url']})"
                for c in items
            ) or "no commits"
        if action == "commit":
            return _commit(full, ref or "HEAD")
        if action in ("issues", "pulls"):
            items = _get(f"/repos/{full}/{action}", {"state": state, "per_page": 10})
            if action == "issues":  # the issues endpoint also returns prs
                items = [i for i in items if "pull_request" not in i]
            return "\n".join(
                f"- #{i['number']} {i['title']} ({i['user']['login']}, {i['html_url']})" for i in items
            ) or f"no {state} {action}"
        if action == "issue":
            if not number:
                return "github issue needs a number"
            return _issue(full, number)
        if action == "search":
            data = _get("/search/issues", {"q": q or "", "per_page": 8})
            return "\n".join(
                f"- {i['repository_url'].split('repos/')[-1]}#{i['number']} {i['title']} ({i['html_url']})"
                for i in data.get("items", [])
            ) or "no results"
        if action == "search_repos":
            data = _get("/search/repositories", {"q": q or "", "per_page": 10})
            return "\n".join(
                f"- {r['full_name']}: {r.get('description') or 'no description'} ({r['stargazers_count']} stars, {r['html_url']})"
                for r in data.get("items", [])
            ) or "no repos found"
        if action == "code_search":
            data = _get("/search/code", {"q": q or "", "per_page": 10})
            return "\n".join(
                f"- {i['repository']['full_name']}: {i['path']} ({i['html_url']})" for i in data.get("items", [])
            ) or "no code found"
        if action == "readme":
            return _text(_get(f"/repos/{full}/readme", ref_param))
        if action == "tree":
            branch = ref or _get(f"/repos/{full}")["default_branch"]
            tree = _get(f"/repos/{full}/git/trees/{urllib.parse.quote(branch, safe='')}", {"recursive": "1"})
            prefix = (path or "").strip("/")
            paths = [
                t["path"] + ("/" if t["type"] == "tree" else "") for t in tree.get("tree", [])
                if not prefix or t["path"] == prefix or t["path"].startswith(prefix + "/")
            ]
            more = len(paths) - MAX_TREE_PATHS
            cut = more > 0 or tree.get("truncated")
            return (f"{full}@{branch}" + (f" under {prefix}/" if prefix else "") + f", {len(paths)} path(s):\n"
                    + "\n".join(paths[:MAX_TREE_PATHS])
                    + (f"\n...(cut off; give a path to narrow it down)" if cut else ""))
        if action == "file":
            if not path:
                return "github file needs a path (use tree to find it)"
            content = _get(f"/repos/{full}/contents/{urllib.parse.quote(path.strip('/'))}", ref_param)
            if isinstance(content, list):  # a directory
                return f"{path} is a folder:\n" + "\n".join(
                    f"- {c['name']}{'/' if c['type'] == 'dir' else ''}" for c in content
                )
            return f"{content['path']} ({content['html_url']}):\n" + _text(content)
        if action == "stars":
            return _stars(full)
        if action == "starred":
            items = _get("/user/starred", {"sort": "created", "per_page": 15})
            return "repos he starred, newest first:\n" + "\n".join(
                f"- {r['full_name']}: {r.get('description') or 'no description'} ({r['stargazers_count']} stars)"
                for r in items
            )
        if action == "releases":
            items = _get(f"/repos/{full}/releases", {"per_page": 5})
            return "\n".join(
                f"- {r['tag_name']} {r.get('name') or ''} ({(r.get('published_at') or '')[:10]}, {r['html_url']})"
                + (f"\n  {(r.get('body') or '')[:500]}" if r.get("body") else "")
                for r in items
            ) or "no releases"
        if action == "actions":
            runs = _get(f"/repos/{full}/actions/runs", {"per_page": 8}).get("workflow_runs", [])
            return "\n".join(
                f"- {r['name']} on {r['head_branch']}: {r['conclusion'] or r['status']} "
                f"({r['created_at'][:16].replace('T', ' ')}, {r['html_url']})"
                for r in runs
            ) or "no workflow runs"
        if action == "branches":
            items = _get(f"/repos/{full}/branches", {"per_page": 50})
            return ", ".join(b["name"] for b in items) or "no branches"
        if action == "user":
            u = _get(f"/users/{urllib.parse.quote((q or config.GITHUB_USER).strip())}")
            return (
                f"{u['login']} ({u.get('name') or 'no name'}): {u.get('bio') or 'no bio'}\n"
                f"{u['public_repos']} public repos, {u['followers']} followers, {u['following']} following, "
                f"joined {u['created_at'][:10]}, {u['html_url']}"
            )
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
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return f"github {action}: not found (wrong name/path/number, or no access)"
        logger.exception("github %s failed", action)
        return f"github {action} failed: {exc}"
    except Exception as exc:  # noqa: BLE001
        logger.exception("github %s failed", action)
        return f"github {action} failed: {exc}"
    return f"unknown github action: {action}"
