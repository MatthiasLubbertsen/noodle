import base64
import io
import json
import logging
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from datetime import datetime, timedelta, timezone

import config
from bot import features

logger = logging.getLogger("noodle")

_API = "https://api.github.com"
WORKFLOW_FILE = "sandbox.yml"
MAX_CODE_CHARS = 60000  # github caps the whole workflow_dispatch payload at ~65k
_SETUP_BUDGET_S = 240  # queueing + vm boot + package installs, on top of the code's own limit


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def configured() -> bool:
    return features.enabled("sandbox")


def _gh(method: str, path: str, body: dict | None = None, raw: bool = False, timeout: float = 30):
    """one github rest call as the sandbox token. returns parsed json (or None for an
    empty body); raw=True returns the bytes of an artifact zip, following github's
    redirect WITHOUT the token (the blob host doesn't need it and must not get it)."""
    headers = {
        "Authorization": f"Bearer {config.SANDBOX_GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "noodle-slack-bot",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{_API}/repos/{config.SANDBOX_REPO}{path}", data=data, headers=headers, method=method)
    if raw:
        try:
            urllib.request.build_opener(_NoRedirect).open(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code not in (301, 302, 303, 307, 308):
                raise
            with urllib.request.urlopen(exc.headers["Location"], timeout=120) as resp:
                return resp.read()
        raise RuntimeError("artifact download did not redirect")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = resp.read()
    return json.loads(payload) if payload else None


def _find_run(job_id: str, since: datetime, deadline: float) -> dict | None:
    """the dispatch call returns no run id, so find our run by the unique id in its run-name."""
    created = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    title = f"sandbox {job_id}"
    while time.time() < deadline:
        time.sleep(2)
        data = _gh("GET", f"/actions/workflows/{WORKFLOW_FILE}/runs?event=workflow_dispatch&per_page=30&created=%3E%3D{created}")
        for run in (data or {}).get("workflow_runs", []):
            if title in (run.get("display_title"), run.get("name")):
                return run
    return None


def _parse_artifact(blob: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = zf.namelist()
        result = json.loads(zf.read("result.json").decode("utf-8"))
        files = []
        for name in result.get("files") or []:
            member = f"files/{name}"
            if member in names:
                files.append({"name": name, "data": base64.b64encode(zf.read(member)).decode("ascii")})
        result["files"] = files
        return result


def run(language: str, code: str, timeout_s: int, packages: str = "") -> dict | str:
    """run a snippet on a fresh github actions vm (see .github/workflows/sandbox.yml) and
    return {ok, stdout, stderr, error, timed_out, truncated, duration_ms, files} or an error
    string. never raises. the vm is throwaway: it has the internet but none of noodle's
    secrets, and the run is deleted afterwards."""
    if len(code) > MAX_CODE_CHARS:
        return f"that code is too long for the sandbox ({len(code)} chars, max {MAX_CODE_CHARS})."
    job_id = uuid.uuid4().hex[:12]
    since = datetime.now(timezone.utc) - timedelta(seconds=30)  # clock skew with github
    deadline = time.time() + timeout_s + _SETUP_BUDGET_S
    run_id = None
    try:
        _gh("POST", f"/actions/workflows/{WORKFLOW_FILE}/dispatches", {
            "ref": config.SANDBOX_REF,
            "inputs": {
                "job_id": job_id,
                "language": language,
                "code": code,
                "packages": packages,
                "timeout_seconds": str(timeout_s),
            },
        })
        run_row = _find_run(job_id, since, deadline)
        if not run_row:
            return "the sandbox run never showed up on github, try again in a moment."
        run_id = run_row["id"]
        while run_row.get("status") != "completed":
            if time.time() > deadline:
                try:
                    _gh("POST", f"/actions/runs/{run_id}/cancel")
                except Exception:  # noqa: BLE001
                    pass
                return "the sandbox run took too long to start or finish, so it was cancelled."
            time.sleep(3)
            run_row = _gh("GET", f"/actions/runs/{run_id}")

        arts = (_gh("GET", f"/actions/runs/{run_id}/artifacts") or {}).get("artifacts", [])
        art = next((a for a in arts if a.get("name") == "result"), None)
        if not art:
            return f"the sandbox vm didn't produce a result (run {run_row.get('conclusion')}): {run_row.get('html_url')}"
        return _parse_artifact(_gh("GET", f"/actions/artifacts/{art['id']}/zip", raw=True))
    except urllib.error.HTTPError as exc:
        logger.warning("sandbox github call failed: %s %s", exc.code, exc.reason)
        hint = " (is SANDBOX_GITHUB_TOKEN allowed to run actions on SANDBOX_REPO, and is the workflow on SANDBOX_REF?)" if exc.code in (401, 403, 404, 422) else ""
        return f"github refused the sandbox request ({exc.code} {exc.reason}){hint}"
    except Exception as exc:  # noqa: BLE001
        logger.warning("sandbox request failed: %s", exc)
        return "couldn't run the sandbox on github actions right now."
    finally:
        if run_id:  # throwaway: don't leave his code lying around in the run list
            try:
                _gh("DELETE", f"/actions/runs/{run_id}")
            except Exception:  # noqa: BLE001
                logger.debug("could not delete sandbox run %s", run_id)
