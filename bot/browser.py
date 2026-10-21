import json
import logging
import urllib.error
import urllib.request

import config
from bot import features

logger = logging.getLogger("noodle")


def configured() -> bool:
    return features.enabled("browser")


def act(payload: dict) -> dict | str:
    """send one action to the browser service. returns its json result
    ({ok, error?, url, title, text, elements, screenshot?, ...}) or an error string.
    never raises. the service has no secrets and can only reach public addresses -
    see browser/proxy.js."""
    req = urllib.request.Request(
        config.BROWSER_URL + "/act",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {config.BROWSER_TOKEN}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        # the service gives up on a hung page after 60s; allow a little more than that
        with urllib.request.urlopen(req, timeout=75) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("error")
        except Exception:  # noqa: BLE001
            detail = None
        logger.warning("browser returned %s: %s", exc.code, detail)
        return f"the browser refused that ({exc.code}): {detail or 'no details'}"
    except Exception as exc:  # noqa: BLE001
        logger.warning("browser request failed: %s", exc)
        return "couldn't reach the browser service, check that its container is up"
