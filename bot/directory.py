import json
import logging
import urllib.error
import urllib.parse
import urllib.request

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


def lookup_user(user_id: str) -> str:
    """look up a slack user's profile by id. no auth needed."""
    data = _get_json(f"/user/{user_id}")
    if not data:
        return f"no user info found for {user_id}"
    user = (data.get("data") or {}).get("user") or {}
    if not user:
        return f"no user info found for {user_id}"
    bits = [
        f"user {user_id}: @{user.get('name', '?')}",
        f"(display: {user.get('display_name') or '?'}, "
        f"real: {user.get('real_name') or '?'})",
    ]
    if user.get("title"):
        bits.append(f"- {user['title']}")
    return " ".join(bits)


def lookup_channel(channel_id: str) -> str:
    """look up a slack channel's info by id. no auth needed."""
    data = _get_json(f"/channel/{channel_id}")
    if not data:
        return f"no channel info found for {channel_id}"
    name = data.get("name") or channel_id
    bits = [f"channel {channel_id}: #{name}"]
    if data.get("description"):
        bits.append(data["description"])
    if data.get("topic"):
        bits.append(f"topic: {data['topic']}")
    return " - ".join(bits)


def search_users(query: str) -> str:
    """search slack users by name/handle."""
    data = _get_json("/users/search", {"q": query})
    if not data:
        return f"user search for {query!r} failed"
    results = data.get("data") or []
    if not results:
        return f"no users found for {query!r}"
    lines = [f"users matching {query!r}:"]
    for u in results[:10]:
        lines.append(f"- {u.get('id')}: @{u.get('name')} "
                     f"(display: {u.get('display_name') or '?'})")
    return "\n".join(lines)
