"""post the 50 days updates noodle missed (it wasn't deployed on day 1, or a recap failed to
link), one per day, into the goal thread and the channel (`reply_broadcast`), in order.

for each day of the challenge so far it takes the LAST bulleted @matthias-day recap of that
day from #matthias and posts ":50days: Day X/50" + its link, exactly like the live feature.
days that already have a ":50days: Day X/50" post in the thread (by anyone) are skipped,
so it is safe to run again. needs FIFTY_DAYS_THREAD in .env.

run from the project root (docker: `docker compose exec noodle python -m scripts.backfill_fifty_days`):

    python -m scripts.backfill_fifty_days --dry-run   # show what it would post
    python -m scripts.backfill_fifty_days             # post it
"""
import datetime
import logging
import sys

from slack_sdk import WebClient

import config
from bot import fifty_days
from bot.log import setup_logging
from bot.matthias_day import message_recap
from bot.slack_text import _matthias_day_pinged
from bot.tz import AMSTERDAM_TZ

logger = logging.getLogger("noodle")

# dec 31 is day 50, so the challenge starts on nov 12 of the year it ends
START = datetime.date(datetime.date.today().year, 12, 31) - datetime.timedelta(days=49)


def recaps_by_day(client: WebClient) -> dict[int, dict]:
    """{day number: the day's last bulleted recap message}, from #matthias since START."""
    oldest = datetime.datetime.combine(START, datetime.time(0), AMSTERDAM_TZ).timestamp()
    found: dict[int, dict] = {}
    cursor = None
    while True:
        resp = client.conversations_history(
            channel=config.MATTHIAS_DAY_CHANNEL_ID, oldest=str(oldest), cursor=cursor, limit=200
        )
        if not resp.get("ok"):
            raise RuntimeError(f"conversations_history failed: {resp.get('error')}")
        for msg in resp.get("messages", []):
            text = msg.get("text", "") or ""
            if msg.get("subtype") or not _matthias_day_pinged(text):
                continue
            if not message_recap(text, msg.get("blocks")):
                continue  # a "^^" re-ping, not a recap
            day = fifty_days.day_number(
                datetime.datetime.fromtimestamp(float(msg["ts"]), AMSTERDAM_TZ).date()
            )
            if day is None:
                continue
            if day not in found or float(msg["ts"]) > float(found[day]["ts"]):
                found[day] = msg
        cursor = (resp.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            return found


def main() -> None:
    setup_logging()
    dry = "--dry-run" in sys.argv[1:]
    where = fifty_days.target()
    if not where:
        sys.exit("FIFTY_DAYS_THREAD is not set (or unreadable), nothing to do")
    client = WebClient(token=config.SLACK_USER_TOKEN)
    done = fifty_days.days_already_posted(client, where)
    recaps = recaps_by_day(client)
    posted = 0
    for day in sorted(recaps):
        if day in done:
            logger.info("50 days: day %d already posted, skipping", day)
            continue
        ts = recaps[day]["ts"]
        link = client.chat_getPermalink(channel=config.MATTHIAS_DAY_CHANNEL_ID, message_ts=ts)["permalink"]
        if dry:
            logger.info("50 days (dry run): would post %r", fifty_days.day_text(day, link))
            continue
        fifty_days.post_day(client, where, day, link)
        logger.info("50 days: posted day %d (%s)", day, link)
        posted += 1
    missing = [d for d in range(1, (datetime.date.today() - START).days + 2) if d not in recaps and d not in done]
    if missing:
        logger.info("50 days: no recap found for day(s) %s, nothing to post for them", missing)
    logger.info("50 days backfill done: %d posted", posted)


if __name__ == "__main__":
    main()
