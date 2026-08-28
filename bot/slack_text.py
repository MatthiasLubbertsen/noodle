import logging
import re

from bot import state

logger = logging.getLogger("noodle")


def _is_dm(event: dict) -> bool:
    return event.get("channel_type") == "im" or str(event.get("channel", "")).startswith("D")


def _mention_in_text(text: str) -> bool:
    return any(f"<@{mid}>" in text for mid in state.MENTION_IDS)


def _clean_text(text: str) -> str:
    # drop our OWN mention entirely (e.g. "@noodle") so the model never parrots
    # its own ping back.
    for mid in state.MENTION_IDS:
        text = re.sub(rf"<@{mid}(?:\|[A-Z0-9]+)?>", "", text)
    # render channel mentions as friendly #name so the model doesn't mangle raw
    # <#C123> tokens. slack search accepts in:#channel-name, so the id is not
    # needed in the prompt. keep the bare <#C123> form only when no name present.
    text = re.sub(r"<#([A-Z0-9]+)\|([^>]+)>", r"#\2", text)
    # render user mentions as @name when a label is present (from:@name works).
    text = re.sub(r"<@([A-Z0-9]+)\|([^>]+)>", r"@\2", text)
    # anything still wrapped in <...> (bare ids) gets unwrapped to avoid leakage
    text = re.sub(r"<([^>]+)>", r"\1", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def _clean_reply(text: str) -> str:
    # never forward any chain-of-thought / reasoning to slack
    if not text:
        return ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<reasoning>.*?</reasoning>", "", text, flags=re.DOTALL | re.IGNORECASE)
    return text.strip()


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
        info = state.app.client.conversations_info(channel=cid)
        if info.get("ok"):
            ch = info.get("channel", {}) or {}
            if ch.get("is_im"):
                return "dm"
            name = ch.get("name")
            if name:
                return f"<#{cid}|{name}>"
    except Exception:  # noqa: BLE001
        logger.exception("conversations_info failed for %s", cid)
    # fallback to the public flaron directory
    from bot import directory

    d = directory.lookup_channel(cid)
    if d and "no channel info" not in d:
        m = re.search(r"#(\S+)", d)
        if m:
            return f"<#{cid}|{m.group(1)}>"
    return f"<#{cid}>"
