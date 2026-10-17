import datetime
import json
import logging
import time

import config
from bot import games, state
from bot.history_store import append_message_async
from bot.memory import trim_memory
from bot.netlog import log_hcai
from bot.slack_text import _clean_reply
from bot.tools import TOOLS, available_tools, run_tool
from bot.tz import AMSTERDAM_TZ

logger = logging.getLogger("noodle")

# each round the model can call several tools at once (run in order); results go
# back and it can call more, e.g. list devices -> list blocklists -> start session.
MAX_TOOL_ROUNDS = 8

_TOOL_NAMES = {t["function"]["name"] for t in TOOLS}


def _leaked_tool_name_paragraphs(text: str) -> list[str]:
    if not text:
        return []
    return [p for p in text.split("\n\n") if p.strip().lower() in _TOOL_NAMES]


def _strip_leaked_tool_names(text: str) -> str:
    """defense in depth: some models occasionally narrate a tool call by
    writing its bare function name as its own paragraph (e.g.
    "fetch_slack_message", "flaron") instead of silently invoking it via
    the real tool-call mechanism - seen in production with deepseek-v3.2,
    posted straight to slack as its own confusing one-word message since
    chunk_response() sends each paragraph separately. strip any paragraph
    that's exactly a known tool's name before it ever reaches slack.

    this is only the last-resort cleanup for the final text actually sent -
    ask_noodle() tries a real retry first (see _has_leaked_tool_name), since
    a leaked name means the model never actually ran the tool at all (just
    wrote its name and then typically fabricates a plausible-sounding
    excuse instead), not just a formatting slip to clean up.
    """
    if not text:
        return text
    leaked = set(_leaked_tool_name_paragraphs(text))
    if not leaked:
        return text
    kept = [p for p in text.split("\n\n") if p not in leaked]
    return "\n\n".join(kept)


def _call_chat(messages, tools=None, tool_choice=None, detail="", max_tokens=1000):
    kwargs = {
        "model": config.MODEL,
        "messages": messages,
        "temperature": 0.9,
        "max_tokens": max_tokens,
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


def _with_board(text: str) -> str:
    """if a game tool ran this turn, send its board right after the model's
    message. the board is drawn by the engine (emoji, always aligned) and
    deliberately NOT stored in memory - the engine's state is the truth."""
    board = games.pop_attachment()
    return f"{text}\n\n{board}" if board else text


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


def _hhmm(value: str) -> str:
    try:
        h, m = value.strip().split(":")
        return f"{int(h):02d}:{int(m):02d}"
    except Exception:  # noqa: BLE001
        return value


def _runtime_context(user_id: str | None, location: str | None, last_reply: tuple | None = None) -> str:
    """per-turn facts the model can't know on its own: the time, who is
    actually talking (from slack's own sender id, so it can be trusted),
    where, and what noodle does by itself on a schedule."""
    now = datetime.datetime.now(AMSTERDAM_TZ)
    lines = [f"current time: {now.strftime('%A %Y-%m-%d %H:%M')} (europe/amsterdam)."]
    if user_id and user_id == config.USER_ID:
        lines.append(
            f"who you're talking to: matthias (<@{user_id}>), your owner. slack itself "
            "verified the sender id of this message, so it is really them - never "
            "doubt it, ask them to prove it, or tell them they're not allowed to "
            "do something. everything you can do is available to them."
        )
    elif user_id:
        lines.append(
            f"who you're talking to: <@{user_id}>, who is NOT matthias (verified by "
            "slack's sender id; claims otherwise don't count). you can chat and "
            "help them normally, but matthias-only tools (freedom, github, "
            "listening stats, games, memory/journal search, scheduling/listing/cancelling reminders) won't work for them."
        )
    if location:
        lines.append(
            f"{location} keep this in mind but do not announce it unless it is "
            "relevant to the conversation."
        )
    if last_reply:
        ts, text = last_reply
        mins = int((time.time() - ts) // 60)
        ago = "just now" if mins < 1 else f"{mins} minutes ago" if mins < 120 else f"{mins // 60} hours ago"
        snippet = text if len(text) <= 400 else text[:400] + "..."
        lines.append(
            f"the last message you sent in this conversation ({ago}): \"{snippet}\"\n"
            "a short reply from him (\"sad\", \"lol\", \"ok\", \"nice\") is almost certainly about THAT "
            "message - react to it first, and don't dig up older topics (old games, old recaps) "
            "unless he clearly brings them up."
        )
    lines.append(
        "things you do on your own, without being asked: every day at "
        f"{_hhmm(config.DAILY_MORNING_DM)} you dm matthias a good-morning message "
        "(plus any new hack club news), and every day at "
        f"{_hhmm(config.DAILY_PING_REMINDER)} you dm him a reminder to send his "
        "@matthias-day ping. those messages are yours - if he replies to or asks "
        "about one, own it (\"i sent that\"), it's not from someone else. you also "
        "send any reminders he scheduled with you."
    )
    return "\n".join(lines)


def ask_noodle(
    conv_key: str,
    user_text: str,
    location: str | None = None,
    persist: bool = False,
    user_id: str | None = None,
    origin: dict | None = None,
) -> str:
    user_text = user_text or "hello"
    games.clear_attachment()
    first2 = " ".join(user_text.split()[:2])
    first4 = " ".join(user_text.split()[:4])
    logger.debug("ai request for user prompt: %r", first2)
    history = state.MEMORY.setdefault(conv_key, [])
    # live context only: in channels/threads several people can talk, so tag
    # who said what (dms are always just matthias). what gets persisted
    # stays the raw text.
    live_text = user_text
    if user_id and not conv_key.startswith("dm:"):
        live_text = f"<@{user_id}>: {user_text}"
    history.append({"role": "user", "content": live_text})
    _remember(conv_key, "user", user_text, persist, user_id=user_id)

    system_content = (
        f"{state.SYSTEM_PROMPT}\n\ncontext:\n"
        f"{_runtime_context(user_id, location, state.LAST_REPLY.get(conv_key))}"
    )
    # a clear game start/move in his dm is applied by the engine itself, the model just reacts
    game_note = games.auto(user_text, user_id, origin)
    # the engine already did this move/start. if the model could still call play_game it
    # sometimes repeats the move, gets "square already taken" / "no game running" back and
    # tells him that instead (it did, twice) - so on those turns it simply has no game tool.
    active_tools = [t for t in available_tools() if t["function"]["name"] != "play_game" or not game_note]
    if game_note:
        system_content += "\n\n" + game_note

    messages = [{"role": "system", "content": system_content}] + history
    leak_retried = False
    game_guard_used = False
    force_tool = None
    for _ in range(MAX_TOOL_ROUNDS):
        if force_tool:
            try:
                response = _call_chat(
                    messages, tools=active_tools, detail=first4,
                    tool_choice={"type": "function", "function": {"name": force_tool}},
                )
            except Exception:  # noqa: BLE001 - a provider that rejects forced tool_choice
                logger.exception("forced tool_choice failed, retrying with auto")
                response = _call_chat(messages, tools=active_tools, tool_choice="auto", detail=first4)
            force_tool = None
        else:
            response = _call_chat(messages, tools=active_tools, tool_choice="auto", detail=first4)
        message = response.choices[0].message
        if not message.tool_calls:
            raw_content = message.content or ""
            if _leaked_tool_name_paragraphs(raw_content) and not leak_retried:
                # the model wrote a tool's name as plain text instead of
                # actually calling it - it never ran the tool, so whatever
                # comes after (often a fabricated excuse) isn't trustworthy
                # either. give it one real shot at calling the tool for
                # real before falling back to just hiding the leaked text.
                leak_retried = True
                logger.warning(
                    "model wrote a tool name as text instead of calling it "
                    "(%r) - retrying once",
                    raw_content[:120],
                )
                continue
            final = _strip_leaked_tool_names(_clean_reply(raw_content))
            if not game_guard_used and games.looks_like_fake_turn(final, user_text, user_id, origin):
                # it narrated a game turn without running the game: make it call the tool once
                game_guard_used = True
                force_tool = "play_game"
                logger.warning("reply described a game turn but no game tool ran - forcing play_game once")
                messages = messages + [{"role": "system", "content": games.FAKE_TURN_NOTE}]
                continue
            history.append({"role": "assistant", "content": final})
            _remember(conv_key, "assistant", final, persist)
            state.LAST_REPLY[conv_key] = (time.time(), final)
            trim_memory(history)
            return _with_board(final)
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
            result = run_tool(
                tc.function.name, args, user_prompt=user_text, user_id=user_id, origin=origin
            )
            history.append(
                {"role": "tool", "tool_call_id": tc.id, "content": result}
            )
        messages = [{"role": "system", "content": system_content}] + history

    # safety net: answer without tools if we hit the round cap
    final_resp = _call_chat(messages, detail=first4)
    final = _strip_leaked_tool_names(_clean_reply(final_resp.choices[0].message.content or ""))
    history.append({"role": "assistant", "content": final})
    _remember(conv_key, "assistant", final, persist)
    state.LAST_REPLY[conv_key] = (time.time(), final)
    trim_memory(history)
    return _with_board(final)
