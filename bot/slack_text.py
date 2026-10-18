import logging
import re

import config
from bot import state

logger = logging.getLogger("noodle")


def _is_dm(event: dict) -> bool:
    return event.get("channel_type") == "im" or str(event.get("channel", "")).startswith("D")


def _mention_in_text(text: str) -> bool:
    return any(f"<@{mid}>" in text for mid in state.MENTION_IDS)


def _matthias_day_pinged(text: str) -> bool:
    """did this message OPEN with an @matthias-day ping - the real daily
    recap, not just someone mentioning the group/ping in casual
    conversation? (e.g. "can i copy ur matthias-day idea for my channel" is
    NOT a ping, even though the text "matthias-day" appears in it - a real
    ping always starts with the mention, so anchoring the match to the
    start of the message is what tells the two apart.)
    """
    if not text:
        return False
    stripped = text.lstrip()
    if config.MATTHIAS_DAY_GROUP_ID and stripped.startswith(
        f"<!subteam^{config.MATTHIAS_DAY_GROUP_ID}"
    ):
        return True
    # also catches a plain typed "@matthias-day"/"matthias-day" that slack
    # didn't expand into a real mention - still only at the start.
    if re.match(r"@?matthias-day\b", stripped, re.IGNORECASE):
        return True
    # rare slack glitch: the mention sometimes renders as a literal "@…"
    # (a collapsed/broken autocomplete) instead of expanding at all - only
    # counted as a ping when immediately followed by "today", to avoid
    # matching an unrelated message that happens to start with "@someone".
    return bool(re.match(r"@…\s*today\b", stripped, re.IGNORECASE))


def _clean_text(text: str) -> str:
    # drop our OWN mention entirely (e.g. "@noodle") so the model never parrots
    # its own ping back.
    for mid in state.MENTION_IDS:
        text = re.sub(rf"<@{mid}(?:\|[A-Z0-9]+)?>", "", text)
    # IMPORTANT: leave every other slack mention exactly as slack sends it
    # (e.g. <#C0C78SG9L|hq> and <@U09UE480JHH|bob>). slack ONLY renders a real
    # mention when the full <...> form is used, so the model copies the id into
    # <#CHANNELID> / <@USERID> when it replies. we do NOT reformat mentions here.
    return re.sub(r"\s{2,}", " ", text).strip()


def _clean_reply(text: str) -> str:
    # never forward any chain-of-thought / reasoning to slack
    if not text:
        return ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<reasoning>.*?</reasoning>", "", text, flags=re.DOTALL | re.IGNORECASE)
    return _tidy_links(_no_dashes(text.strip()))


_URL = re.compile(r"<(https?://[^|>\s]+)(?:\|([^>]*))?>|(?<![<|/\w])(https?://[^\s<>|`]+)")


def _tidy_links(text: str) -> str:
    """every url goes out as slack's rich text link without the scheme in the visible part
    (`<https://go.example.com/docs|go.example.com/docs>`), never as a bare url, and a stray
    space before sentence punctuation after it ("link . it'll") is closed up. code spans are
    left alone."""

    def link(m: re.Match) -> str:
        if m.group(1):  # already <url> or <url|label>
            url, label = m.group(1), m.group(2)
            return f"<{url}|{label}>" if label else f"<{url}|{_shown(url)}>"
        url = m.group(3)
        trail = ""
        while url and url[-1] in ".,!?;:)":
            trail = url[-1] + trail
            url = url[:-1]
        return f"<{url}|{_shown(url)}>{trail}"

    parts = re.split(r"(`[^`]*`)", text)
    parts = [p if p.startswith("`") else _URL.sub(link, p) for p in parts]
    text = "".join(parts)
    return re.sub(r"(?<=>)[ \t]+(?=[.,!?](?:\s|$))", "", text)


def _shown(url: str) -> str:
    return re.sub(r"^https?://", "", url).rstrip("/")


def _no_dashes(text: str) -> str:
    """noodle never writes em dashes (or en dashes): the persona prompt says so, and this
    is the backstop for when the model does anyway. a dash between numbers is a range
    ("9\u201315" -> "9-15"), a dash at the end or start of a line just goes away, anything
    else reads as a comma. line breaks are never touched (they separate slack messages)."""
    dash = "[\u2013\u2014\u2015]"
    text = re.sub(rf"(?<=\d)[ \t]*{dash}[ \t]*(?=\d)", "-", text)
    text = re.sub(rf"[ \t]*{dash}[ \t]*(?=\n|$)", "", text)
    text = re.sub(rf"(?m)^[ \t]*{dash}[ \t]*", "", text)
    return re.sub(rf"[ \t]*{dash}[ \t]*", ", ", text)


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
