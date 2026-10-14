import json
import logging
import urllib.error
import urllib.request

import config

logger = logging.getLogger("noodle")


def _get(path: str) -> dict | None:
    url = config.FREEDOM_API_BASE.rstrip("/") + path
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {config.FREEDOM_API_KEY}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        logger.exception("freedom GET %s failed", path)
        return None


def list_devices() -> list[dict] | None:
    """GET /api/devices - the freedom account's registered devices, or None
    on failure. the account is SHARED with matthias's dad, so this is used
    to resolve specific device names to ids rather than ever targeting
    "every device" on it.
    """
    data = _get("/api/devices")
    if data is None:
        return None
    return data.get("devices")


def start_session(duration_minutes: int, device_ids: list[int]) -> dict | None:
    """start a freedom.to focus session: block everything, on exactly the
    given device ids, for duration_minutes - via matthias's self-hosted
    freedom api (github.com/MatthiasLubbertsen/freedom, deployed at
    FREEDOM_API_BASE). NEVER pass "all_devices" here - the account is
    shared with matthias's dad, so that would block his devices too.

    returns the parsed json response (either `{"session": {...}}` on
    success or `{"error": {"code", "message"}}` on a rejection the api
    itself reported), or None if the request couldn't be made at all
    (network failure, timeout, bad response) - never raises.
    """
    url = config.FREEDOM_API_BASE.rstrip("/") + "/api/sessions"
    payload = json.dumps(
        {
            "duration_minutes": duration_minutes,
            "block_everything": True,
            "device_ids": device_ids,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {config.FREEDOM_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            body = {}
        error = body.get("error") or {}
        logger.warning(
            "freedom session start failed: %s %s", exc.code, error.get("message")
        )
        return {"error": {"code": error.get("code", str(exc.code)), "message": error.get("message")}}
    except Exception:  # noqa: BLE001
        logger.exception("freedom session start failed")
        return None
