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
    # strip @-mentions so the model does not echo them
    for mid in MENTION_IDS:
        text = text.replace(f"<@{mid}>", "")
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
                "search slack for messages across channels, dms and threads "
                "using a free-text query. use this whenever the user asks about "
                "something that might have been said before, or wants you to look "
                "something up."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "the search query, e.g. 'deploy downtime last week'",
                    }
                },
                "required": ["query"],
            },
        },
    }
]


def _search_slack_messages(query: str) -> str:
    try:
        resp = app.client.search_messages(query=query, count=5)
        matches = (resp.get("messages") or {}).get("matches", []) if resp else []
        if not matches:
            return "no slack messages found for that query"
        lines = []
        for m in matches:
            body = (m.get("text") or "").replace("\n", " ")
            user = m.get("user", "unknown")
            channel = m.get("channel", {})
            chan = channel.get("name") if isinstance(channel, dict) else channel
            permalink = m.get("permalink", "")
            lines.append(f"- in #{chan} by {user}: {body} ({permalink})")
        return "\n".join(lines)
    except Exception as exc:
        logger.exception("slack search failed")
        return f"slack search failed: {exc}"


def _ask_noodle(user_text: str) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_text or "hello"},
    ]
    # tool-calling loop (max a few rounds so we never spin forever)
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
            return _clean_reply(message.content or "")
        # record the assistant turn (with its tool calls) for context
        messages.append(
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
            result = _search_slack_messages(args.get("query", ""))
            messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": result}
            )
    # safety net: produce a final answer without tools if we hit the cap
    response = client.chat.completions.create(
        model=config.MODEL,
        messages=messages,
        temperature=0.9,
        max_tokens=1000,
    )
    return _clean_reply(response.choices[0].message.content or "")


def _process(channel: str, prompt: str, thread_ts: str | None) -> None:
    try:
        if thread_ts:
            # remember this thread so we keep answering in it
            PARTICIPATING_THREADS.add(thread_ts)
        reply = _ask_noodle(prompt)
        for fragment in _chunk_response(reply):
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
    # run the (slow) ai call + chunked sending off the socket thread
    threading.Thread(
        target=_process, args=(channel, prompt, thread_ts), daemon=True
    ).start()


# --------------------------------------------------------------------------
# entrypoint
# --------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info("starting noodle in socket mode...")
    SocketModeHandler(app, config.SLACK_APP_TOKEN).start()
