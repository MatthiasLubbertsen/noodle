import json
import logging
import urllib.error
import urllib.parse
import urllib.request

import config
from bot.httpjson import USER_AGENT

logger = logging.getLogger("noodle")


def _call(method: str, path: str, params: dict | None = None, payload: dict | None = None):
    """one request to the link shortener api. returns the parsed json (`{}` for an
    empty body like a 204), `{"error": {"code", "message"}}` for a rejection the api
    itself reported, or None if the request couldn't be made at all - never raises."""
    url = config.LINKS_API_BASE.rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    headers = {"Authorization": f"Bearer {config.LINKS_API_KEY}", "User-Agent": USER_AGENT}
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
            error = json.loads(exc.read().decode("utf-8")).get("error") or {}
        except Exception:  # noqa: BLE001
            error = {}
        logger.warning("links %s %s failed: %s %s", method, path, exc.code, error.get("message"))
        return {"error": {"code": error.get("code", str(exc.code)), "message": error.get("message")}}
    except Exception:  # noqa: BLE001
        logger.exception("links %s %s failed", method, path)
        return None


def list_links(search=None, domain=None, limit=20):
    return _call("GET", "/links", {"search": search, "domain": domain, "limit": limit})


def get_link(link_id: str):
    return _call("GET", f"/links/{urllib.parse.quote(link_id, safe='')}")


def create_link(payload: dict):
    return _call("POST", "/links", payload=payload)


def update_link(link_id: str, payload: dict):
    return _call("PATCH", f"/links/{urllib.parse.quote(link_id, safe='')}", payload=payload)


def delete_link(link_id: str):
    return _call("DELETE", f"/links/{urllib.parse.quote(link_id, safe='')}")


def link_stats(link_id: str, days: int):
    return _call("GET", f"/links/{urllib.parse.quote(link_id, safe='')}/stats", {"days": days})


def account_stats(days: int, limit: int):
    return _call("GET", "/stats", {"days": days, "limit": limit})


def list_domains():
    return _call("GET", "/domains")
