import logging

import config
from bot import state
from bot.almanac import fun_holidays_today
from bot.news import unread_articles
from bot.slack_text import _clean_reply

logger = logging.getLogger("noodle")


def _facts_for_prompt() -> str:
    lines = []

    holidays = fun_holidays_today()
    if holidays:
        lines.append("silly/named days today: " + "; ".join(holidays))
    else:
        lines.append("no silly/named days today")

    articles = unread_articles()
    if articles:
        titles = "; ".join(f'"{a["title"]}"' for a in articles[:5])
        lines.append(f"{len(articles)} new hack club news article(s): {titles}")
    else:
        lines.append("no new hack club news articles")

    return "\n".join(lines)


def build_wakeup_message() -> str:
    facts = _facts_for_prompt()
    prompt = (
        "it's morning, wake matthias up. write ONE short good-morning message "
        "in your own voice, 1-2 sentences, not a report. just say good morning, "
        "mention if today has a silly/named day (skip it if there isn't one, "
        "don't say 'no special day' or anything like that), and mention new "
        "hack club news articles only if there are any. that's it, nothing "
        "else, no extra facts, no history lessons, keep it tiny.\n\n"
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
        logger.exception("failed to build morning wakeup message")
        return "good morning! (i had trouble putting today's update together, sorry)"
