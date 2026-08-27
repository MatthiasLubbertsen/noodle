import json
import logging
import re
import threading
import time
from pathlib import Path

from openai import APIConnectionError, OpenAI
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

import config

# --------------------------------------------------------------------------
# logging
# --------------------------------------------------------------------------
config.LOG_DIR.mkdir(exist_ok=True)
_log_handlers = [logging.StreamHandler()]
try:
    _log_handlers.append(
        logging.FileHandler(config.LOG_DIR / "noodle.log", encoding="utf-8")
    )
except OSError as exc:
    print(f"warning: cannot write log file, logging to console only: {exc}")
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=_log_handlers,
)
logger = logging.getLogger("noodle")

# --------------------------------------------------------------------------
# slack app (socket mode) + ai client
# --------------------------------------------------------------------------
app = App(token=config.SLACK_USER_TOKEN)
client = OpenAI(api_key=config.OPENAI_API_KEY, base_url=config.AI_ENDPOINT)

# resolve our own identity so we never reply to ourselves
auth = app.client.auth_test()
AUTH_USER_ID = auth.get("user_id")
BOT_ID = auth.get("bot_id")
MENTION_IDS = {mid for mid in (AUTH_USER_ID, BOT_ID) if mid}
# threads noodle has joined (by replying) so it keeps answering in them
PARTICIPATING_THREADS: set[str] = set()
# short-term conversation memory (in-memory, resets on restart)
MEMORY: dict[str, list[dict]] = {}
logger.info("noodle online as user=%s bot=%s", AUTH_USER_ID, BOT_ID)

# load persona
SYSTEM_PROMPT = config.SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _is_dm(event: dict) -> bool:
    return event.get("channel_type") == "im" or str(event.get("channel", "")).startswith("D")


def _mention_in_text(text: str) -> bool:
    return any(f"<@{mid}>" in text for mid in MENTION_IDS)


def _clean_text(text: str) -> str:
    # drop our OWN mention entirely (e.g. "@noodle") so the model never parrots
    # its own ping back. keep other user/channel mentions in slack's canonical
    # syntax (<@U123> / <#C123>) so it can build from:<@U123> / in:<#C123> queries.
    for mid in MENTION_IDS:
        text = re.sub(rf"<@{mid}(?:\|[A-Z0-9]+)?>", "", text)
    text = re.sub(r"<@([A-Z0-9]+)\|[^>]+>", r"<@\1>", text)
    text = re.sub(r"<#([A-Z0-9]+)\|[^>]+>", r"<#\1>", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def _clean_reply(text: str) -> str:
    # never forward any chain-of-thought / reasoning to slack
    if not text:
        return ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<reasoning>.*?</reasoning>", "", text, flags=re.DOTALL | re.IGNORECASE)
    return text.strip()


def _should_respond(event: dict):
    # ignore our own messages and system/bot noise -> prevents reply loops
    if event.get("bot_id") or event.get("subtype"):
        return False, None, None
    if event.get("user") == AUTH_USER_ID:
        return False, None, None

    user = event.get("user")
    text = event.get("text", "")
    channel = event.get("channel")
    thread_ts = event.get("thread_ts")

    if _is_dm(event):
        if user != config.USER_ID:
            logger.info("ignored DM from unauthorized user %s", user)
            return False, None, None
        return True, _clean_text(text), thread_ts

    mentioned = ("noodle" in text.lower()) or _mention_in_text(text)

    if mentioned:
        if (
            not config.ALLOW_CHANNEL_WILDCARD
            and channel not in config.ALLOWED_CHANNELS
        ):
            logger.info("ignored mention in non-allowed channel %s", channel)
            return False, None, None
        return True, _clean_text(text), thread_ts

    # keep talking inside threads noodle has joined, even without a mention
    if thread_ts and thread_ts in PARTICIPATING_THREADS:
        return True, _clean_text(text), thread_ts

    return False, None, None


def _chunk_response(text: str):
    # primary strategy: the model is told to put each fragment on its own line
    parts = [p.strip() for p in text.splitlines() if p.strip()]

    # fallback: if there are no line breaks, split on sentence boundaries
    if len(parts) <= 1:
        parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]

    # hard cap very long fragments so slack messages stay small
    result = []
    for part in parts:
        if len(part) <= config.MAX_FRAGMENT_CHARS:
            result.append(part)
            continue
        words = part.split(" ")
        current = ""
        for word in words:
            if len(current) + len(word) + 1 <= config.MAX_FRAGMENT_CHARS:
                current = (current + " " + word).strip()
            else:
                if current:
                    result.append(current)
                current = word
        if current:
            result.append(current)
    return result or [text.strip()]


# --------------------------------------------------------------------------
# slack search tool (exposed to the model as a function call)
# --------------------------------------------------------------------------
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_slack_messages",
                "description": (
                "search slack for messages across channels, dms and threads. "
                "build a query with slack's modifiers: from:<@USERID> to filter "
                "by a user (e.g. from:<@U09UE480JHH>), in:<#CHANNELID> or "
                "in:#channel-name to filter by channel, and wrap exact phrases in "
                "double quotes. example: from:<@U09UE480JHH> \"i want to cheese\""
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "the slack search query, e.g. from:U12345 \"i want to cheese\"",
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
]


def _conv_key(event: dict) -> str:
    thread_ts = event.get("thread_ts")
    if thread_ts:
        return f"thread:{thread_ts}"
    return f"chan:{event.get('channel')}"


def _trim_memory(history: list[dict], max_groups: int = 12) -> None:
    # keep history grouped by user turns so tool_call/tool pairs stay intact
    groups: list[list[dict]] = []
    current: list[dict] | None = None
    for m in history:
        if m["role"] == "user":
            current = [m]
            groups.append(current)
        elif current is not None:
            current.append(m)
        else:
            groups.append([m])
    while len(groups) > max_groups:
        groups.pop(0)
    history[:] = [m for g in groups for m in g]


def _resolve_from_handle(query: str):
    # slack search 'from:' often matches on the @handle rather than the U-id,
    # so rewrite from:Uxxxx -> from:@handle when we can resolve it
    m = re.search(r"from:(U[A-Z0-9]+)", query)
    if not m:
        return None
    uid = m.group(1)
    try:
        info = app.client.users_info(user=uid)
        if not info.get("ok"):
            return None
        user = info.get("user", {}) or {}
        handle = user.get("name") or (user.get("profile") or {}).get("display_name")
        if not handle:
            return None
        return re.sub(r"from:" + re.escape(uid), f"from:@{handle}", query)
    except Exception:
        logger.exception("users_info failed for %s", uid)
        return None


def _search_slack_messages(query: str, user_prompt: str = "") -> str:
    first2 = " ".join(user_prompt.split()[:2])
    logger.debug("search query=%r (user: %r) searching_as=%s", query, first2, AUTH_USER_ID)
    try:
        resp = app.client.search_messages(query=query, count=5)
        if not resp.get("ok"):
            logger.warning("search FAILED error=%s", resp.get("error"))
            return f"slack search error: {resp.get('error')}"
        matches = (resp.get("messages") or {}).get("matches", [])
        logger.info("search %r -> %d matches", query, len(matches))
        if not matches:
            # slack search 'from:' sometimes matches better on the @handle than
            # the internal user id; try resolving the id to a handle.
            handle_q = _resolve_from_handle(query)
            if handle_q:
                logger.debug("search retry (from-handle) %r", handle_q)
                try:
                    resp_h = app.client.search_messages(query=handle_q, count=5)
                    if resp_h.get("ok"):
                        matches = (resp_h.get("messages") or {}).get("matches", [])
                except Exception:
                    logger.exception("from-handle search failed")
        if not matches:
            # one broader retry: drop the from: filter and any quotes
            broader = re.sub(r"from:\S+\s*", "", query).strip().strip('"').strip()
            if broader and broader != query:
                logger.debug("search retry (broader) %r", broader)
                try:
                    resp2 = app.client.search_messages(query=broader, count=5)
                    if resp2.get("ok"):
                        matches = (resp2.get("messages") or {}).get("matches", [])
                except Exception:
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
    except Exception as exc:
        logger.exception("slack search failed (user: %r)", first2)
        return f"slack search failed: {exc}"


def _parse_slack_ref(ref: str):
    # message permalink: archives/Cxxxx/p<10 digits><6 digits>
    m = re.search(r"archives/(C[0-9A-Z]+)/p(\d{10})(\d{1,6})", ref)
    if m:
        return m.group(1), f"{m.group(2)}.{m.group(3)}"
    # a channel mention pasted directly: <#Cxxxx> or <#Cxxxx|name>
    m_c = re.search(r"<#(C[0-9A-Z]+)", ref)
    if m_c:
        return m_c.group(1), None
    # bare channel id
    m_b = re.match(r"(C[0-9A-Z]+)$", ref.strip())
    if m_b:
        return m_b.group(1), None
    # query-string form: ?thread_ts=1493223429.243531&cid=C0C78SG9L
    m2 = re.search(r"cid=([C0-9A-Z]+)", ref)
    m3 = re.search(r"thread_ts=(\d+\.\d+)", ref)
    if m2 and m3:
        return m2.group(1), m3.group(1)
    # raw "C123:1493223429.243531"
    m4 = re.match(r"(C[0-9A-Z]+):(\d+\.\d+)", ref.strip())
    if m4:
        return m4.group(1), m4.group(2)
    # channel url without a specific message: archives/Cxxxx
    m5 = re.search(r"archives/(C[0-9A-Z]+)(?!/\w)", ref)
    if m5:
        return m5.group(1), None
    return None, None


def _channel_link(cid: str) -> str:
    # resolve a channel id to a slack-renderable link (<#C123|name>)
    try:
        info = app.client.conversations_info(channel=cid)
        if info.get("ok"):
            ch = info.get("channel", {}) or {}
            if ch.get("is_im"):
                return "dm"
            name = ch.get("name")
            if name:
                return f"<#{cid}|{name}>"
    except Exception:
        logger.exception("conversations_info failed for %s", cid)
    return f"<#{cid}>"


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
        resp = app.client.conversations_history(
            channel=cid, latest=ts, inclusive=True, limit=1
        )
        msgs = (resp.get("messages") or []) if resp.get("ok") else []
        if not msgs:
            # maybe a reply inside a thread
            resp2 = app.client.conversations_replies(channel=cid, ts=ts, limit=1)
            msgs = (resp2.get("messages") or []) if resp2.get("ok") else []
        if not msgs:
            return f"no message found at {_channel_link(cid)} {ts}"
        msg = msgs[0]
        text = msg.get("text", "")
        user = msg.get("user", "unknown")
        logger.debug("fetched from %s at %s: %r", user, ts, text[:160])
        return f"message in {_channel_link(cid)} from {user} at {ts}: {text}"
    except Exception as exc:
        logger.exception("fetch_slack_message failed")
        return f"fetch failed: {exc}"


def _ask_noodle(conv_key: str, user_text: str) -> str:
    user_text = user_text or "hello"
    first2 = " ".join(user_text.split()[:2])
    logger.debug("ai request for user prompt: %r", first2)
    history = MEMORY.setdefault(conv_key, [])
    history.append({"role": "user", "content": user_text})

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history
    for _ in range(5):
        response = client.chat.completions.create(
            model=config.MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            temperature=0.9,
            max_tokens=1000,
        )
        message = response.choices[0].message
        if not message.tool_calls:
            final = _clean_reply(message.content or "")
            history.append({"role": "assistant", "content": final})
            _trim_memory(history)
            return final
        history.append(
            {
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in message.tool_calls
                ],
            }
        )
        for tc in message.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            name = tc.function.name
            if name == "search_slack_messages":
                result = _search_slack_messages(
                    args.get("query", ""), user_prompt=user_text
                )
            elif name == "fetch_slack_message":
                result = _fetch_slack_message(args.get("ref", ""))
            else:
                result = f"unknown tool: {name}"
            history.append(
                {"role": "tool", "tool_call_id": tc.id, "content": result}
            )
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history

    # safety net: answer without tools if we hit the round cap
    final_resp = client.chat.completions.create(
        model=config.MODEL,
        messages=messages,
        temperature=0.9,
        max_tokens=1000,
    )
    final = _clean_reply(final_resp.choices[0].message.content or "")
    history.append({"role": "assistant", "content": final})
    _trim_memory(history)
    return final


def _process(channel: str, prompt: str, thread_ts: str | None, conv_key: str) -> None:
    try:
        if thread_ts:
            # remember this thread so we keep answering in it
            PARTICIPATING_THREADS.add(thread_ts)
        reply = _ask_noodle(conv_key, prompt)
        # cap fragments so noodle never spams the channel
        fragments = _chunk_response(reply)[:8]
        for fragment in fragments:
            payload = {"channel": channel, "text": fragment}
            if thread_ts:
                payload["thread_ts"] = thread_ts
            app.client.chat_postMessage(**payload)
            time.sleep(config.CHUNK_DELAY_SECONDS)
    except APIConnectionError:
        logger.exception("ai backend unreachable (network/proxy issue)")
        try:
            app.client.chat_postMessage(
                channel=channel,
                text="i can't reach my brain right now, the network is wobbly :3",
            )
        except Exception:
            pass
    except Exception:
        logger.exception("failed to handle message in %s", channel)
        try:
            app.client.chat_postMessage(
                channel=channel, text="oops something went wobbly :3"
            )
        except Exception:
            pass


# --------------------------------------------------------------------------
# events
# --------------------------------------------------------------------------
@app.event("message")
def handle_message(event: dict) -> None:
    ok, prompt, thread_ts = _should_respond(event)
    if not ok:
        return
    channel = event.get("channel")
    key = _conv_key(event)
    # run the (slow) ai call + chunked sending off the socket thread
    threading.Thread(
        target=_process, args=(channel, prompt, thread_ts, key), daemon=True
    ).start()


# --------------------------------------------------------------------------
# entrypoint
# --------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info("starting noodle in socket mode...")
    SocketModeHandler(app, config.SLACK_APP_TOKEN).start()
