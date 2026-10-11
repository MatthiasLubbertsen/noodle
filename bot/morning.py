import logging

import config
from bot import state
from bot.almanac import fun_holidays_today
from bot.news import unread_articles
from bot.slack_text import _clean_reply

logger = logging.getLogger("noodle")


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
    """a separate message listing new hack club news articles, or None if none."""
    articles = unread_articles()
    if not articles:
        return None
    count = len(articles)
    plural = "article" if count == 1 else "articles"
    lines = [f"oh and {count} new hack club news {plural} dropped:"]
    lines += [f"- {a['title']}: {a['link']}" for a in articles[:5]]
    return "\n".join(lines)
