"""forget a message noodle sent: delete it from slack, delete it from memory, optionally
send a fresh morning dm.

run on the machine that has noodle's .env and data/ (stop noodle first, or restart it
afterwards: its live in-memory history still holds the old text until a restart):

    python -m scripts.purge_morning --ts D0BQXEME7GD:1793426420.316859 --like "happy halloween" --resend

--ts CHANNEL:TS   a noodle message to delete from slack (repeatable)
--like TEXT       delete every persisted memory row containing this text (repeatable)
--resend          then send a new morning dm (greeting, weather, news), using today's rules
"""
import argparse
import logging
import time

from openai import OpenAI
from slack_sdk import WebClient

import config
from bot import history_store, state
from bot.log import setup_logging
from bot.prompt import load_prompt

logger = logging.getLogger("noodle")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ts", action="append", default=[], metavar="CHANNEL:TS")
    parser.add_argument("--like", action="append", default=[], metavar="TEXT")
    parser.add_argument("--resend", action="store_true")
    args = parser.parse_args()
    setup_logging()

    client = WebClient(token=config.SLACK_USER_TOKEN)  # noodle's own token: it can delete its own messages
    for item in args.ts:
        channel, _, ts = item.partition(":")
        try:
            client.chat_delete(channel=channel, ts=ts)
            logger.info("deleted slack message %s", item)
        except Exception:  # noqa: BLE001
            logger.exception("could not delete slack message %s", item)

    history_store.init_db()
    for text in args.like:
        logger.info("removed %d memory row(s) containing %r", history_store.delete_containing(text), text)

    if args.resend:
        from bot.scheduler import _send_morning_dm  # imported late: it pulls in the whole bot

        state.app = type("App", (), {"client": client})()
        state.client = OpenAI(api_key=config.OPENAI_API_KEY, base_url=config.AI_ENDPOINT)
        state.SYSTEM_PROMPT = load_prompt()
        _send_morning_dm()
        time.sleep(15)  # memory writes (and their embeddings) run in background threads


if __name__ == "__main__":
    main()
