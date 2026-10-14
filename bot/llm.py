import json
import logging

import config
from bot import state
from bot.history_store import append_message_async
from bot.memory import trim_memory
from bot.netlog import log_hcai
from bot.slack_text import _clean_reply
from bot.tools import TOOLS, run_tool

logger = logging.getLogger("noodle")


def _call_chat(messages, tools=None, tool_choice=None, detail=""):
    kwargs = {
        "model": config.MODEL,
        "messages": messages,
        "temperature": 0.9,
        "max_tokens": 1000,
    }
    if tools is not None:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = tool_choice
    try:
        raw = state.client.chat.completions.with_raw_response.create(**kwargs)
    except Exception as exc:  # noqa: BLE001
        log_hcai("chat", str(getattr(exc, "status_code", "error")), detail=detail)
        raise
    log_hcai("chat", str(raw.status_code), detail=detail)
    return raw.parse()


def _kind_for(conv_key: str) -> str:
    if conv_key.startswith("dm:"):
        return "dm"
    if conv_key.startswith("thread:"):
        return "thread"
    return "mention"


def _remember(
    conv_key: str, role: str, content: str, persist: bool, user_id: str | None = None
) -> None:
    # persist dms, threads, and direct pings - not ambient allowed-channel
    # chatter the unprompted-reply gate chimed into (persist=False for that).
    # async: embedding is a network call, and nothing here should block the
    # actual reply on it.
    if persist:
        append_message_async(conv_key, _kind_for(conv_key), role, content, user_id=user_id)


def ask_noodle(
    conv_key: str,
    user_text: str,
    location: str | None = None,
    persist: bool = False,
    user_id: str | None = None,
) -> str:
    user_text = user_text or "hello"
    first2 = " ".join(user_text.split()[:2])
    first4 = " ".join(user_text.split()[:4])
    logger.debug("ai request for user prompt: %r", first2)
    history = state.MEMORY.setdefault(conv_key, [])
    history.append({"role": "user", "content": user_text})
    _remember(conv_key, "user", user_text, persist, user_id=user_id)

    system_content = state.SYSTEM_PROMPT
    if location:
        system_content = (
            f"{system_content}\n\n"
            f"context: {location} keep this in mind but do not announce it unless "
            "it is relevant to the conversation."
        )

    messages = [{"role": "system", "content": system_content}] + history
    for _ in range(5):
        response = _call_chat(messages, tools=TOOLS, tool_choice="auto", detail=first4)
        message = response.choices[0].message
        if not message.tool_calls:
            final = _clean_reply(message.content or "")
            history.append({"role": "assistant", "content": final})
            _remember(conv_key, "assistant", final, persist)
            trim_memory(history)
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
            result = run_tool(tc.function.name, args, user_prompt=user_text, user_id=user_id)
            history.append(
                {"role": "tool", "tool_call_id": tc.id, "content": result}
            )
        messages = [{"role": "system", "content": system_content}] + history

    # safety net: answer without tools if we hit the round cap
    final_resp = _call_chat(messages, detail=first4)
    final = _clean_reply(final_resp.choices[0].message.content or "")
    history.append({"role": "assistant", "content": final})
    _remember(conv_key, "assistant", final, persist)
    trim_memory(history)
    return final
