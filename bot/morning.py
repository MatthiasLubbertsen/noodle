import logging
import re

import config
from bot import state
from bot.almanac import fun_holidays_today
from bot.news import unread_articles
from bot.oneshot import complete, remember_facts
from bot.slack_text import _clean_reply
from bot.weather import morning_weather_facts

logger = logging.getLogger("noodle")

# the weather and news messages are separate model calls that go out right after the
# greeting message. told to, they don't greet - but if one still opens with
# "good morning (matthias)" anyway, cut it, so he is never greeted twice.
_GREETING_PREFIX = re.compile(r"^\s*(?:good\s+morning|morning|gm)\b[\s,!.:;-]*(?:matthias\b[\s,!.:;-]*)?", re.IGNORECASE)


def _without_greeting(text: str) -> str:
    # may come back empty (the reply was only a greeting) - callers then use their plain fallback
    return _GREETING_PREFIX.sub("", text, count=1).strip()


def build_greeting_message() -> str:
    """good morning + today's silly/named day, if any. ONE tiny message - no news."""
    holidays = fun_holidays_today()
    facts = (
        "silly/named days today: " + "; ".join(holidays)
        if holidays
        else "no silly/named days today"
    )
    prompt = (
        "it's morning, wake matthias up. write ONE short good-morning message "
        "in your own voice, 1-2 sentences, not a report. just say good morning "
        "and mention if today has a silly/named day (skip it if there isn't "
        "one, don't say 'no special day' or anything like that). that's it, "
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
        return text or "good morning!"
    except Exception:  # noqa: BLE001
        logger.exception("failed to build morning greeting message")
        return "good morning! (i had trouble putting today's update together, sorry)"


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
