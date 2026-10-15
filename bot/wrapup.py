import logging

import config
from bot import github, lastfm
from bot.oneshot import complete
from bot.stats import messages_sent_today

logger = logging.getLogger("noodle")


def build_wrapup_message() -> str | None:
    """a tiny end-of-day wrap-up from whatever numbers are available, or None
    if there are none. deliberately never drafts or mentions the
    @matthias-day recap - matthias writes that himself."""
    facts = []
    sent = messages_sent_today(config.USER_ID)
    if sent >= 0:
        facts.append(f"slack messages sent today: {sent}")
    if github.configured():
        try:
            count, repos = github.commits_today()
            facts.append(
                f"github commits today: {count}" + (f" (in {', '.join(repos)})" if repos else "")
            )
        except Exception:  # noqa: BLE001
            logger.exception("wrap-up: github commit count failed")
    tracks = lastfm.scrobbles_today()
    if tracks is not None:
        facts.append(f"tracks played today: {tracks}")
    if not facts:
        return None

    text = complete(
        "it's the end of the day. write ONE short wrap-up message for matthias in "
        "your own voice, 2-3 sentences max, using only the facts below. be warm, "
        "not a report; skip any stat that is zero or boring. do NOT mention, "
        "remind about or draft his @matthias-day recap - he writes that himself.\n\n"
        "facts:\n" + "\n".join(facts),
        max_tokens=200,
    )
    return text or "end of day wrap-up:\n" + "\n".join(f"- {f}" for f in facts)
