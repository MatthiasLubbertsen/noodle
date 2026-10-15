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


def _call(method: str, path: str, payload: dict | None = None) -> dict | None:
    """one request to the freedom api. returns the parsed json (`{}` for an
    empty body like a 204), `{"error": {"code", "message"}}` for a rejection
    the api itself reported, or None if the request couldn't be made at all
    (network failure, timeout, bad response) - never raises.
    """
    url = config.FREEDOM_API_BASE.rstrip("/") + path
    headers = {"Authorization": f"Bearer {config.FREEDOM_API_KEY}"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body.strip() else {}
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            body = {}
        error = body.get("error") or {}
        logger.warning(
            "freedom %s %s failed: %s %s", method, path, exc.code, error.get("message")
        )
        return {"error": {"code": error.get("code", str(exc.code)), "message": error.get("message")}}
    except Exception:  # noqa: BLE001
        logger.exception("freedom %s %s failed", method, path)
        return None


def start_session(
    duration_minutes: int, device_ids: list[int], start_time: str | None = None
) -> dict | None:
    """start a freedom.to focus session: block everything, on exactly the
    given device ids, for duration_minutes - via matthias's self-hosted
    freedom api (github.com/MatthiasLubbertsen/freedom, deployed at
    FREEDOM_API_BASE). NEVER pass "all_devices" here - the account is
    shared with matthias's dad, so that would block his devices too.

    with start_time ("HH:MM" in the freedom account's timezone, premium)
    freedom itself starts it at that time today, or tomorrow if it has
    passed - noodle does NOT have to be running or waiting then. returns
    `{"session": {...}}` / `{"error": {...}}` / None, see _call().
    """
    payload = {
        "duration_minutes": duration_minutes,
        "block_everything": True,
        "device_ids": device_ids,
    }
    if start_time:
        payload["start_time"] = start_time
    return _call("POST", "/api/sessions", payload)


def create_schedule(
    name: str, start_time: str, days: list[str], duration_minutes: int,
    device_ids: list[int],
) -> dict | None:
    """POST /api/schedules: a recurring session (premium), run by freedom
    itself on those days at start_time ("HH:MM", freedom account timezone).
    """
    return _call(
        "POST",
        "/api/schedules",
        {
            "name": name,
            "start_time": start_time,
            "duration_minutes": duration_minutes,
            "days": days,
            "block_everything": True,
            "device_ids": device_ids,
        },
    )


def list_sessions() -> dict | None:
    """GET /api/sessions -> {"timezone", "active": [...], "upcoming": [...]}"""
    return _call("GET", "/api/sessions")


def list_schedules() -> dict | None:
    """GET /api/schedules -> {"timezone", "schedules": [...]} (recurring,
    upcoming and running - for the whole SHARED account, so callers must
    filter to matthias's own devices)."""
    return _call("GET", "/api/schedules")


def delete_schedule(schedule_id: int) -> dict | None:
    """DELETE /api/schedules/{id} - a recurring schedule or an upcoming
    one-off session."""
    return _call("DELETE", f"/api/schedules/{int(schedule_id)}")
