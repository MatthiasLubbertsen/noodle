import json
import logging
import urllib.error
import urllib.request

import config

logger = logging.getLogger("noodle")


def start_session(duration_minutes: int) -> dict | None:
    """start a freedom.to focus session: block everything, on every device,
    for duration_minutes - via matthias's self-hosted freedom api
    (github.com/MatthiasLubbertsen/freedom, deployed at FREEDOM_API_BASE).

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
            "all_devices": True,
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
