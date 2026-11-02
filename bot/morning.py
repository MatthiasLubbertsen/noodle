import logging
import re

import datetime

import config
from bot import state
from bot.almanac import fun_holidays_today
from bot.holidays import today_nl
from bot.interests import is_avoided
from bot.news import unread_articles
from bot.oneshot import complete, remember_facts
from bot.slack_text import _clean_reply
from bot.tz import AMSTERDAM_TZ
from bot.weather import morning_weather_facts

logger = logging.getLogger("noodle")

# the weather and news messages are separate model calls that go out right after the
# greeting message. told to, they don't greet - but if one still opens with
# "good morning (matthias)" anyway, cut it, so he is never greeted twice.
_GREETING_PREFIX = re.compile(r"^\s*(?:good\s+morning|morning|gm)\b[\s,!.:;-]*(?:matthias\b[\s,!.:;-]*)?", re.IGNORECASE)


def _without_greeting(text: str) -> str:
    # may come back empty (the reply was only a greeting) - callers then use their plain fallback
    return _GREETING_PREFIX.sub("", text, count=1).strip()


def _day_part(now: datetime.datetime | None = None) -> str:
    """morning / afternoon / evening / night, for the hour in amsterdam. the dm can go out
    whenever matthias first comes online, so it must not assume it's morning."""
    hour = (now or datetime.datetime.now(AMSTERDAM_TZ)).hour
    return "night" if hour < 5 else "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"


def _greeting_word(now: datetime.datetime | None = None) -> str:
    part = _day_part(now)
    return "hey" if part == "night" else f"good {part}"


def build_greeting_message() -> str:
    """good morning + today's silly/named day, if any. ONE tiny message - no news."""
    holidays = fun_holidays_today()
    facts = (
        "silly/named days today: " + "; ".join(holidays)
        if holidays
        else "no silly/named days today"
    )
    dutch_holiday = today_nl()
    facts += "\ndutch holiday today: " + (dutch_holiday or "none")
    now = datetime.datetime.now(AMSTERDAM_TZ)
    prompt = (
        f"it's {now.strftime('%H:%M')} ({_day_part(now)}) and matthias just came online for the "
        "first time today (or it's his usual time to hear from you). write ONE short greeting "
        "message in your own voice, 1-2 sentences, not a report, that fits this time of day: "
        f"say \"{_greeting_word(now)}\" or similar, and NEVER say morning unless it is "
        "actually morning. mention if today has a silly/named day (skip it if there isn't "
        "one, don't say 'no special day' or anything like that). a dutch holiday "
        "matters more than a silly day, so lead with it if the facts list one, and say "
        "whether it's a day off. NEVER mention a holiday or special day that isn't in "
        "the facts below, and never invent or guess one from the date. matthias does "
        "not do halloween or anything about death or spooky stuff: never mention "
        "halloween, even if it's the date. that's it, "
        "nothing else - no news, no extra facts, keep it tiny.\n\n"
        f"facts:\n{facts}"
    )
    try:
        resp = state.client.chat.completions.create(
            model=config.MODEL,
            messages=[
                {"role": "system", "content": state.SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.9,
            max_tokens=120,
        )
        text = _clean_reply(resp.choices[0].message.content or "")
        if is_avoided(text):  # safety net: the model mentioned something he avoids anyway
            logger.warning("greeting mentioned an avoided topic, using the plain greeting")
            return f"{_greeting_word()}!"
        return text or f"{_greeting_word()}!"
    except Exception:  # noqa: BLE001
        logger.exception("failed to build morning greeting message")
        return f"{_greeting_word()}! (i had trouble putting today's update together, sorry)"


def build_news_message() -> str | None:
    """a separate persona-written message about new hack club news articles
    (links kept verbatim), or None if there are none."""
    articles = unread_articles()
    if not articles:
        return None
    facts = "\n".join(f"- {a['title']}: {a['link']}" for a in articles[:5])
    remember_facts("hack club news", facts)
    text = complete(
        f"{len(articles)} new hack club news article(s) dropped. tell matthias in your "
        "own voice, short and casual: what each is about (from the title) with its "
        "link exactly as given. this goes out right after your good-morning message, "
        "so don't greet him or say good morning - start straight with the news. "
        "use ONLY these facts.\n\nfacts:\n" + facts,
        max_tokens=300,
    )
    if text:
        text = _without_greeting(text)
    if text and all(a["link"] in text for a in articles[:5]):
        return text
    return "new hack club news:\n" + facts


def build_weather_message() -> str | None:
    """a short persona-written weather update (from open-meteo facts) for the
    morning dm, or None if the forecast couldn't be fetched. the raw facts are
    also saved to memory."""
    facts = morning_weather_facts()
    if not facts:
        return None
    remember_facts("weather forecast", facts)
    text = complete(
        "tell matthias today's weather in your own voice: 1-3 short sentences in ONE "
        "paragraph covering the places below, with the actual temperatures and any "
        "rain. if the places are about the same, say so once instead of repeating "
        "yourself. this goes out right after your separate good-morning message, so "
        "do NOT greet him or say good morning - start straight with the weather. "
        "use ONLY these facts.\n\nfacts:\n" + facts,
        max_tokens=200,
    )
    if text:
        # one paragraph = one slack message (blank lines would split it into several)
        text = re.sub(r"\n\s*\n", " ", _without_greeting(text))
    return text or "weather today:\n" + facts
