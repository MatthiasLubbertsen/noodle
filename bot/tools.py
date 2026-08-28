import logging
import re

from bot import state
from bot.directory import lookup_channel, lookup_user, search_users
from bot.slack_text import _channel_link, _parse_slack_ref

logger = logging.getLogger("noodle")


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_slack_messages",
            "description": (
                "search slack for messages across channels, dms and threads. "
                "build a query with slack's modifiers: from:@username or "
                "from:<@USERID> to filter by a user, in:#channel-name or "
                "in:<#CHANNELID> to filter by channel, and wrap exact phrases in "
                "double quotes. example: from:@zrl \"i want to cheese\""
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "the slack search query, e.g. from:@zrl \"i want to cheese\"",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_slack_message",
            "description": (
                "fetch the actual text of a specific slack message. pass a slack "
                "permalink/url (e.g. https://hackclub.slack.com/archives/C123/p12345... ) "
                "or a 'channel:timestamp' reference. use this when the user pastes a "
                "slack link or asks what a particular message actually says."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ref": {
                        "type": "string",
                        "description": "a slack permalink/url or channel:timestamp",
                    }
                },
                "required": ["ref"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_slack_user",
            "description": (
                "look up a slack user's profile by their user id (e.g. U123). "
                "returns their @username, display name and real name. use this to "
                "find the correct <@USERID> to mention when you only know a name."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "the slack user id, e.g. U123"}
                },
                "required": ["user_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_slack_channel",
            "description": (
                "look up a slack channel's info by channel id (e.g. C123). returns "
                "its #name, description and topic. use this to find the correct "
                "<#CHANNELID> to mention when you only know a name."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "channel_id": {"type": "string", "description": "the slack channel id, e.g. C123"}
                },
                "required": ["channel_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_slack_users",
            "description": (
                "search slack users by name or handle. returns matching user ids and "
                "@usernames. use this to turn a name like 'matthias' into a <@USERID>."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "the name or handle to search for"}
                },
                "required": ["query"],
            },
        },
    },
]


def _resolve_from_handle(query: str):
    # slack search 'from:' often matches on the @handle rather than the U-id,
    # so rewrite from:Uxxxx -> from:@handle when we can resolve it
    m = re.search(r"from:(U[A-Z0-9]+)", query)
    if not m:
        return None
    uid = m.group(1)
    try:
        info = state.app.client.users_info(user=uid)
        if not info.get("ok"):
            return None
        user = info.get("user", {}) or {}
        handle = user.get("name") or (user.get("profile") or {}).get("display_name")
        if not handle:
            return None
        return re.sub(r"from:" + re.escape(uid), f"from:@{handle}", query)
    except Exception:  # noqa: BLE001
        logger.exception("users_info failed for %s", uid)
        return None


def _search_slack_messages(query: str, user_prompt: str = "") -> str:
    first2 = " ".join(user_prompt.split()[:2])
    logger.debug("search query=%r (user: %r) searching_as=%s", query, first2, state.AUTH_USER_ID)
    try:
        resp = state.app.client.search_messages(query=query, count=5)
        if not resp.get("ok"):
            logger.warning("search FAILED error=%s", resp.get("error"))
            return f"slack search error: {resp.get('error')}"
        matches = (resp.get("messages") or {}).get("matches", [])
        logger.info("search %r -> %d matches", query, len(matches))
        if not matches:
            handle_q = _resolve_from_handle(query)
            if handle_q:
                logger.debug("search retry (from-handle) %r", handle_q)
                try:
                    resp_h = state.app.client.search_messages(query=handle_q, count=5)
                    if resp_h.get("ok"):
                        matches = (resp_h.get("messages") or {}).get("matches", [])
                except Exception:  # noqa: BLE001
                    logger.exception("from-handle search failed")
        if not matches:
            broader = re.sub(r"from:\S+\s*", "", query).strip().strip('"').strip()
            if broader and broader != query:
                logger.debug("search retry (broader) %r", broader)
                try:
                    resp2 = state.app.client.search_messages(query=broader, count=5)
                    if resp2.get("ok"):
                        matches = (resp2.get("messages") or {}).get("matches", [])
                except Exception:  # noqa: BLE001
                    logger.exception("broader search failed")
        if not matches:
            return "no slack messages found for that query"
        logger.debug("top match: %r", (matches[0].get("text") or "")[:120])
        lines = []
        for m in matches:
            body = (m.get("text") or "").replace("\n", " ")
            user = m.get("user", "unknown")
            channel = m.get("channel", {}) or {}
            cid = channel.get("id") if isinstance(channel, dict) else channel
            cname = channel.get("name") if isinstance(channel, dict) else None
            chan_link = (
                f"<#{cid}|{cname}>" if cname else (f"<#{cid}>" if cid else "dm")
            )
            ts = m.get("ts", "")
            lines.append(f"- in {chan_link} from {user} at {ts}: {body}")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        logger.exception("slack search failed (user: %r)", first2)
        return f"slack search failed: {exc}"


def _fetch_slack_message(ref: str) -> str:
    cid, ts = _parse_slack_ref(ref)
    if not cid:
        return "could not parse a slack channel/timestamp from that reference"
    if not ts:
        return (
            f"that looks like channel <#{cid}> but no specific message was given; "
            f"try a search with in:<#{cid}>"
        )
    logger.info("fetch %s %s", cid, ts)
    try:
        resp = state.app.client.conversations_history(
            channel=cid, latest=ts, inclusive=True, limit=1
        )
        msgs = (resp.get("messages") or []) if resp.get("ok") else []
        if not msgs:
            resp2 = state.app.client.conversations_replies(channel=cid, ts=ts, limit=1)
            msgs = (resp2.get("messages") or []) if resp2.get("ok") else []
        if not msgs:
            return f"no message found at {_channel_link(cid)} {ts}"
        msg = msgs[0]
        text = msg.get("text", "")
        user = msg.get("user", "unknown")
        logger.debug("fetched from %s at %s: %r", user, ts, text[:160])
        return f"message in {_channel_link(cid)} from {user} at {ts}: {text}"
    except Exception as exc:  # noqa: BLE001
        logger.exception("fetch_slack_message failed")
        return f"fetch failed: {exc}"


def run_tool(name: str, args: dict, user_prompt: str = "") -> str:
    if name == "search_slack_messages":
        return _search_slack_messages(args.get("query", ""), user_prompt=user_prompt)
    if name == "fetch_slack_message":
        return _fetch_slack_message(args.get("ref", ""))
    if name == "lookup_slack_user":
        return lookup_user(args.get("user_id", ""))
    if name == "lookup_slack_channel":
        return lookup_channel(args.get("channel_id", ""))
    if name == "search_slack_users":
        return search_users(args.get("query", ""))
    return f"unknown tool: {name}"
