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
    # turn user mentions <@U123> into plain ids so the model can use from:U123
    text = re.sub(r"<@([A-Z0-9]+)>", r"\1", text)
    # turn channel mentions <#C123|name> / <#C123> into plain ids for in:C123
    text = re.sub(r"<#([A-Z0-9]+)\|[^>]+>", r"\1", text)
    text = re.sub(r"<#([A-Z0-9]+)>", r"\1", text)
    return text.strip()


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
                "build a good slack search query: use from:<user> to filter by a "
                "user (a user id like U123 works), in:<channel> to filter by "
                "channel, and wrap exact phrases in double quotes. "
                "example: from:U12345 \"i want to cheese\""
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
    }
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


def _search_slack_messages(query: str, user_prompt: str = "") -> str:
    first2 = " ".join(user_prompt.split()[:2])
    logger.info(
        "search_slack_messages query=%r (user prompt: %r) searching_as=%s",
        query, first2, AUTH_USER_ID,
    )
    try:
        resp = app.client.search_messages(query=query, count=5)
        if not resp.get("ok"):
            logger.warning(
                "search_slack_messages FAILED ok=%s error=%s (user: %r) searching_as=%s",
                resp.get("ok"), resp.get("error"), first2, AUTH_USER_ID,
            )
            return f"slack search error: {resp.get('error')}"
        matches = (resp.get("messages") or {}).get("matches", [])
        logger.info(
            "search_slack_messages got %d matches (user: %r) searching_as=%s",
            len(matches), first2, AUTH_USER_ID,
        )
        if not matches:
            return "no slack messages found for that query"
        for i, m in enumerate(matches[:3]):
            chan = m.get("channel", {}) or {}
            cid = chan.get("id") if isinstance(chan, dict) else chan
            logger.info(
                "  match %d: channel=%s user=%s ts=%s text=%r",
                i, cid, m.get("user"), m.get("ts"),
                (m.get("text") or "")[:120],
            )
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


def _ask_noodle(conv_key: str, user_text: str) -> str:
    user_text = user_text or "hello"
    first2 = " ".join(user_text.split()[:2])
    logger.info("ai request for user prompt: %r", first2)
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
            result = _search_slack_messages(
                args.get("query", ""), user_prompt=user_text
            )
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
