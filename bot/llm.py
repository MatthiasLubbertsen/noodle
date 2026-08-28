import json
import logging

import config
from bot import state
from bot.memory import trim_memory
from bot.slack_text import _clean_reply
from bot.tools import TOOLS, run_tool

logger = logging.getLogger("noodle")


def ask_noodle(conv_key: str, user_text: str, location: str | None = None) -> str:
    user_text = user_text or "hello"
    first2 = " ".join(user_text.split()[:2])
    logger.debug("ai request for user prompt: %r", first2)
    history = state.MEMORY.setdefault(conv_key, [])
    history.append({"role": "user", "content": user_text})

    system_content = state.SYSTEM_PROMPT
    if location:
        system_content = (
            f"{system_content}\n\n"
            f"context: {location} keep this in mind but do not announce it unless "
            "it is relevant to the conversation."
        )

    messages = [{"role": "system", "content": system_content}] + history
    for _ in range(5):
        response = state.client.chat.completions.create(
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
            result = run_tool(tc.function.name, args, user_prompt=user_text)
            history.append(
                {"role": "tool", "tool_call_id": tc.id, "content": result}
            )
        messages = [{"role": "system", "content": system_content}] + history

    # safety net: answer without tools if we hit the round cap
    final_resp = state.client.chat.completions.create(
        model=config.MODEL,
        messages=messages,
        temperature=0.9,
        max_tokens=1000,
    )
    final = _clean_reply(final_resp.choices[0].message.content or "")
    history.append({"role": "assistant", "content": final})
    trim_memory(history)
    return final
