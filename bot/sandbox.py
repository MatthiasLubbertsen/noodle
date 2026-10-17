import json
import logging
import urllib.error
import urllib.request

import config
from bot import features

logger = logging.getLogger("noodle")


def configured() -> bool:
    return features.enabled("sandbox")


def run(language: str, code: str, timeout_s: int) -> dict | str:
    """run a snippet in the sandbox service. returns its json result
    ({ok, stdout, stderr, result, error, truncated, duration_ms, files}) or an
    error string. never raises. the service itself has no network and no
    secrets - see sandbox/server.js."""
    payload = json.dumps({"language": language, "code": code, "timeout_ms": timeout_s * 1000}).encode("utf-8")
    req = urllib.request.Request(
        config.SANDBOX_URL + "/run",
        data=payload,
        headers={"Authorization": f"Bearer {config.SANDBOX_TOKEN}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        # generous: the service may queue the job and warm a fresh interpreter first
        with urllib.request.urlopen(req, timeout=timeout_s + 90) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("error")
        except Exception:  # noqa: BLE001
            detail = None
        logger.warning("sandbox returned %s: %s", exc.code, detail)
        return f"the sandbox refused the run ({exc.code}): {detail or 'no details'}"
    except Exception as exc:  # noqa: BLE001
        logger.warning("sandbox request failed: %s", exc)
        return "couldn't reach the sandbox service, check that its container is up"
