"""fix or forget a message noodle sent: edit it (or delete it) in slack and in memory, optionally
send a fresh morning dm.

run on the machine that has noodle's .env and data/ (stop noodle first, or restart it
afterwards: its live in-memory history still holds the old text until a restart):

    python -m scripts.purge_morning --edit "D0BQXEME7GD:1793426420.316859=good morning matthias."

--edit CHANNEL:TS=TEXT  rewrite one of noodle's messages in place, in slack and in memory (repeatable)
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
    parser.add_argument("--edit", action="append", default=[], metavar="CHANNEL:TS=TEXT")
    parser.add_argument("--ts", action="append", default=[], metavar="CHANNEL:TS")
    parser.add_argument("--like", action="append", default=[], metavar="TEXT")
    parser.add_argument("--resend", action="store_true")
    args = parser.parse_args()
    setup_logging()

    client = WebClient(token=config.SLACK_USER_TOKEN)  # noodle's own token: it can delete its own messages
    history_store.init_db()
    for item in args.edit:
        target, _, new = item.partition("=")
        channel, _, ts = target.partition(":")
        try:
            msgs = client.conversations_history(channel=channel, latest=ts, inclusive=True, limit=1)["messages"]
            old = msgs[0]["text"] if msgs and msgs[0].get("ts") == ts else None
            if old is None:
                logger.error("message %s not found, not editing it", target)
                continue
            client.chat_update(channel=channel, ts=ts, text=new)
            logger.info("edited slack message %s", target)
            logger.info("updated %d memory row(s)", history_store.replace_content(old, new))
        except Exception:  # noqa: BLE001
            logger.exception("could not edit %s", target)

    for item in args.ts:
        channel, _, ts = item.partition(":")
        try:
            client.chat_delete(channel=channel, ts=ts)
            logger.info("deleted slack message %s", item)
        except Exception:  # noqa: BLE001
            logger.exception("could not delete slack message %s", item)

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
