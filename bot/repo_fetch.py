"""fetches a github repo for the sandbox shell, done by this code and never by the model or the sandbox.

the sandbox has no network at all, so the one network step lives here: download github's tarball
of a repo (a snapshot, no git history), unpack it IN MEMORY (nothing is ever written to disk) and
hand the files to the sandbox, which puts them in /work/<repo>. only github.com is ever contacted,
the host is not something the model can choose."""
import base64
import io
import logging
import posixpath
import re
import tarfile
import urllib.error
import urllib.parse
import urllib.request

import config
from bot.httpjson import USER_AGENT

logger = logging.getLogger("noodle")

MAX_DOWNLOAD = 40 * 1024 * 1024  # compressed tarball
MAX_FILES = 4000
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 25 * 1024 * 1024
_ALLOWED_HOST = re.compile(r"(^|\.)github\.com$")
_NAME = r"[A-Za-z0-9_.-]{1,100}"
_SPEC = re.compile(rf"^(?:https?://github\.com/)?(?P<owner>{_NAME})/(?P<name>{_NAME}?)(?:\.git)?(?:/tree/(?P<tree>[^\s]+)|@(?P<at>[^\s]+))?/?$")


def parse_spec(spec: str) -> tuple[str, str, str | None] | None:
    """'owner/name', 'owner/name@ref' or a github.com url (optionally /tree/<ref>) -> (owner, name, ref)."""
    m = _SPEC.match((spec or "").strip())
    if not m or {m.group("owner"), m.group("name")} & {".", ".."}:
        return None
    ref = m.group("tree") or m.group("at")
    if ref and not re.fullmatch(r"[A-Za-z0-9_./-]{1,200}", ref):
        return None
    return m.group("owner"), m.group("name"), ref


class _GithubOnly(urllib.request.HTTPRedirectHandler):
    """the tarball endpoint redirects to codeload.github.com; follow that, nothing else (the
    token, if any, travels with the redirect, so it must never leave github)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _ALLOWED_HOST.search(urllib.parse.urlparse(newurl).hostname or ""):
            raise urllib.error.URLError(f"refusing redirect to {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download(owner: str, name: str, ref: str | None) -> bytes:
    path = f"/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(name)}/tarball"
    if ref:
        path += "/" + urllib.parse.quote(ref, safe="/")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    if config.GITHUB_TOKEN:  # his token: lets him clone his own private repos too
        headers["Authorization"] = f"Bearer {config.GITHUB_TOKEN}"
    req = urllib.request.Request("https://api.github.com" + path, headers=headers)
    opener = urllib.request.build_opener(_GithubOnly)
    with opener.open(req, timeout=30) as resp:
        data = resp.read(MAX_DOWNLOAD + 1)
    if len(data) > MAX_DOWNLOAD:
        raise ValueError(f"the repo is too big to fetch (over {MAX_DOWNLOAD // 1024 // 1024}MB packed)")
    return data


def _unpack(data: bytes, prefix: str) -> tuple[list[dict], dict]:
    """regular files only (no symlinks/devices/hardlinks), top folder stripped, paths normalised
    and kept inside the prefix, size caps, .git skipped. returns (files, stats)."""
    files, total, skipped_big, skipped_cap = [], 0, 0, 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar:
            if not member.isreg():
                continue
            parts = posixpath.normpath(member.name).split("/")[1:]  # drop "<owner>-<repo>-<sha>/"
            if not parts or ".." in parts or parts[0] == ".git" or any(p in ("", ".") for p in parts):
                continue
            if member.size > MAX_FILE_BYTES:
                skipped_big += 1
                continue
            if len(files) >= MAX_FILES or total + member.size > MAX_TOTAL_BYTES:
                skipped_cap += 1
                continue
            fh = tar.extractfile(member)
            if fh is None:
                continue
            body = fh.read(MAX_FILE_BYTES + 1)
            total += len(body)
            files.append({"path": f"{prefix}/{'/'.join(parts)}", "data": base64.b64encode(body).decode("ascii")})
    return files, {"files": len(files), "bytes": total, "skipped_big": skipped_big, "skipped_cap": skipped_cap}


def fetch(spec: str) -> dict | str:
    """-> {"dir", "files", "summary"} for the sandbox, or an error string. never raises."""
    parsed = parse_spec(spec)
    if not parsed:
        return "repo must look like owner/name, owner/name@branch, or a github.com url."
    owner, name, ref = parsed
    try:
        data = _download(owner, name, ref)
        files, stats = _unpack(data, name)
    except urllib.error.HTTPError as exc:
        logger.warning("repo fetch %s/%s failed: %s", owner, name, exc.code)
        if exc.code in (401, 403, 404):
            return f"couldn't fetch {owner}/{name}: not found, private, or rate limited ({exc.code})."
        return f"couldn't fetch {owner}/{name}: github returned {exc.code}."
    except Exception as exc:  # noqa: BLE001
        logger.warning("repo fetch %s/%s failed: %s", owner, name, exc)
        return f"couldn't fetch {owner}/{name}: {exc}"
    if not files:
        return f"{owner}/{name} came back empty."
    summary = f"cloned {owner}/{name}{'@' + ref if ref else ''} into /work/{name} ({stats['files']} files, {stats['bytes'] // 1024}KB, a snapshot without git history)"
    notes = []
    if stats["skipped_big"]:
        notes.append(f"{stats['skipped_big']} files over {MAX_FILE_BYTES // 1024 // 1024}MB skipped")
    if stats["skipped_cap"]:
        notes.append(f"{stats['skipped_cap']} files skipped, the size cap was hit")
    if notes:
        summary += "; " + ", ".join(notes)
    return {"dir": name, "files": files, "summary": summary}
