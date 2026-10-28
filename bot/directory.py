import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from bot.netlog import log_tool
from bot.state import logger

BASE_URL = "https://flaron.halceon.dev"


def _get_json(path: str, params: dict | None = None):
    url = BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "noodle/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        logger.warning("flaron %s -> HTTP %s", path, exc.code)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("flaron %s failed: %s", path, exc)
        return None


def _dump(data, label: str) -> str:
    if data is None:
        return f"no flaron data found for {label}"
    return json.dumps(data, indent=2, ensure_ascii=False)


def lookup_display_name(user_id: str) -> str | None:
    """resolve a slack user id to a short display name via the public flaron
    directory (e.g. for search_memory results, which only ever store a raw
    user_id). returns None if the lookup fails or no name is found - never
    raises.
    """
    if not user_id:
        return None
    data = _get_json(f"/user/{user_id}")
    if not data:
        return None
    user = (data.get("data") or {}).get("user") or {}
    return user.get("display_name") or user.get("real_name") or user.get("name") or None


def lookup_channel_name(channel_id: str) -> str | None:
    """resolve a slack channel id to its name via the public flaron directory (the fallback
    when slack's own conversations.info fails). returns None if not found - never raises."""
    if not channel_id:
        return None
    data = _get_json(f"/channel/{channel_id}")
    if not data:
        return None
    channel = (data.get("data") or {}).get("channel") or {}
    return channel.get("name") or None


def flaron_query(action: str, id: str | None = None, name: str | None = None,
                 query: str | None = None) -> str:
    """call any public (no-auth) flaron endpoint and return the full json.

    actions:
      user              -> GET /user/<id>            (needs id)
      channel           -> GET /channel/<id>         (needs id)
      user_search       -> GET /users/search?q=      (needs query)
      channel_search    -> GET /channels/search?q=   (needs query)
      channel_by_name   -> GET /cname/<name>         (needs name)
      channel_managers  -> GET /cman/<id>            (needs id)
      channel_members   -> GET /ccount/<id>          (needs id)
      app               -> GET /app/<id>             (needs id)
      emoji             -> GET /emoji/<name>          (needs name)
      command           -> GET /command/<name>       (needs name)
      promote           -> GET /promote/<id>         (needs id)
    """
    action = (action or "").lower()
    detail = id or name or query or action

    def _run(path, params=None):
        data = _get_json(path, params)
        log_tool("flaron", detail, "success" if data is not None else "not found")
        return data

    if action == "user":
        return _dump(_run(f"/user/{id}"), f"user {id}")
    if action == "channel":
        return _dump(_run(f"/channel/{id}"), f"channel {id}")
    if action == "user_search":
        return _dump(_run("/users/search", {"q": query}), f"users matching {query!r}")
    if action == "channel_search":
        return _dump(_run("/channels/search", {"q": query}), f"channels matching {query!r}")
    if action == "channel_by_name":
        return _dump(_run(f"/cname/{urllib.parse.quote(name or '')}"), f"channel named {name!r}")
    if action == "channel_managers":
        return _dump(_run(f"/cman/{id}"), f"managers of {id}")
    if action == "channel_members":
        return _dump(_run(f"/ccount/{id}"), f"member count of {id}")
    if action == "app":
        return _dump(_run(f"/app/{id}"), f"app {id}")
    if action == "emoji":
        return _dump(_run(f"/emoji/{urllib.parse.quote(name or '')}"), f"emoji {name!r}")
    if action == "command":
        return _dump(_run(f"/command/{urllib.parse.quote(name or '')}"), f"command {name!r}")
    if action == "promote":
        return _dump(_run(f"/promote/{id}"), f"promote {id}")
    log_tool("flaron", detail, "unknown action")
    return f"unknown flaron action: {action!r} (try user, channel, user_search, channel_search, channel_by_name, channel_managers, channel_members, app, emoji, command, promote)"
