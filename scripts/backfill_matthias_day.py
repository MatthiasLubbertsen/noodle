"""one-time backfill: find every historical @matthias-day ping in
config.MATTHIAS_DAY_CHANNEL_ID (#matthias) and persist each one into
noodle's memory (bot/history_store.py) via bot/matthias_day.py - cleaned
(ping + "today:" lead-in + trailing thread/join signature stripped),
timestamped to the day it actually happened (the message's own ts, not
now()), and linked back to the original slack message so it can be read
later.

run once, manually, from the project root (needs the same .env as the bot):

    python -m scripts.backfill_matthias_day

safe to re-run: bot.history_store.append_message() skips any permalink it
has already stored, so nothing gets duplicated.
"""
import logging

from slack_sdk import WebClient

import config
from bot.history_store import init_db
from bot.log import setup_logging
from bot.matthias_day import persist_ping
from bot.slack_text import _matthias_day_pinged

logger = logging.getLogger("noodle")


def _iter_matthias_day_messages(client: WebClient):
    """walk the whole channel history (oldest supported page size: 200,
    cursor-paginated) and yield every message that pings @matthias-day.

    uses conversations_history (not slack search) so the backfill is
    complete and deterministic - it doesn't depend on search index
    freshness or pagination quirks, and this only has to run once.
    """
    cursor = None
    while True:
        resp = client.conversations_history(
            channel=config.MATTHIAS_DAY_CHANNEL_ID, cursor=cursor, limit=200
        )
        if not resp.get("ok"):
            logger.error("conversations_history failed: %s", resp.get("error"))
            return
        for msg in resp.get("messages", []):
            if msg.get("subtype"):
                continue
            if _matthias_day_pinged(msg.get("text", "") or ""):
                yield msg
        cursor = (resp.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            return


def main() -> None:
    setup_logging()
    init_db()
    client = WebClient(token=config.SLACK_USER_TOKEN)
    count = 0
    for msg in _iter_matthias_day_messages(client):
        persist_ping(client, config.MATTHIAS_DAY_CHANNEL_ID, msg["ts"], msg.get("text", "") or "")
        count += 1
    logger.info("matthias-day backfill done: processed %d ping(s)", count)


if __name__ == "__main__":
    main()
