import logging

import config
from bot import state
from bot.almanac import fun_holidays_today, history_blurb_today
from bot.news import unread_articles
from bot.slack_text import _clean_reply

logger = logging.getLogger("noodle")


def _facts_for_prompt() -> str:
    lines = []

    holidays = fun_holidays_today()
    if holidays:
        lines.append("named/quirky days today: " + "; ".join(holidays))
    else:
        lines.append("no notable named days found for today")

    history = history_blurb_today()
    if history:
        lines.append(f"on this day in history: {history}")

    articles = unread_articles()
    if articles:
        listed = "; ".join(f'"{a["title"]}" ({a["link"]})' for a in articles[:5])
        lines.append(
            f"there are {len(articles)} new hack club news article(s) since last "
            f"time: {listed}"
        )
    else:
        lines.append("no new hack club news articles since last time")

    return "\n".join(lines)


def build_wakeup_message() -> str:
    facts = _facts_for_prompt()
    prompt = (
        "it's 08:00 and time to wake matthias up with a good-morning message. "
        "weave the facts below into ONE warm, cool little message in your own "
        "voice and words, like you're texting a friend, not a bulleted report. "
        "mention whether today is a fun/named day (or say it's a normal day if "
        "not), casually drop the on-this-day-in-history bit if there is one, and "
        "let him know about any new hack club news articles (only if there are "
        "any - don't mention news at all if there's nothing new). keep it "
        "natural and not too long.\n\n"
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
            max_tokens=400,
        )
        text = _clean_reply(resp.choices[0].message.content or "")
        return text or "good morning! (couldn't put today's update together, sorry)"
    except Exception:  # noqa: BLE001
        logger.exception("failed to build morning wakeup message")
        return "good morning! (i had trouble putting today's update together, sorry)"
