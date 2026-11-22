"""fix what noodle got wrong about @matthias-day recaps posted after midnight (before
RECAP_DAY_CUTOFF, default 05:00). such a recap is about the day before, but older noodle
treated it as the new day. real incident: saturday's recap at 00:23 got "zero messages,
taking a sunday off" in #matthias and ":50days: Day 11/50" instead of 10.

it EDITS noodle's own messages in place (chat.update, noodle's token), nothing new is posted:
  1. #matthias: noodle's count reply right after a late ping is rewritten (model-written
     again, with the count of the day the recap is about). a reply that was already edited
     is left alone unless --force, so running it twice doesn't rewrite it twice.
  2. the 50 days goal thread: every ":50days: Day X/50" post by noodle is checked against
     the recap it links to; a wrong day number is corrected (the link stays).
  3. memory: the journal row of a late recap is re-dated to the day it is about, so
     get_journal and the weekly recap file it under the right day.

safe to re-run. run on the machine with noodle's .env and data/ (docker:
`docker compose exec noodle python -m scripts.fix_late_recaps --dry-run`, then without):

    python -m scripts.fix_late_recaps --dry-run         # show what it would change
    python -m scripts.fix_late_recaps                   # change it
    python -m scripts.fix_late_recaps --since 2026-11-01 --force
"""
import argparse
import datetime
import logging
import re

from openai import OpenAI
from slack_sdk import WebClient

import config
from bot import fifty_days, history_store, state
from bot.log import setup_logging
from bot.matthias_day import is_late, message_recap, recap_created_at, recap_date
from bot.prompt import load_prompt
from bot.slack_text import _matthias_day_pinged
from bot.tz import AMSTERDAM_TZ

logger = logging.getLogger("noodle")

# noodle's count reply goes out within seconds of the ping; anything later isn't it
REPLY_WINDOW_SECONDS = 600
# the recap link in a 50 days post, as day_text() writes it: "<https://...>"
_URL_RE = re.compile(r"<(https?://[^>|]+)")


def _history(client: WebClient, channel: str, oldest: float) -> list[dict]:
    """every top-level message in `channel` since `oldest`, oldest first."""
    out: list[dict] = []
    cursor = None
    while True:
        resp = client.conversations_history(channel=channel, oldest=str(oldest), cursor=cursor, limit=200)
        if not resp.get("ok"):
            raise RuntimeError(f"conversations_history failed: {resp.get('error')}")
        out.extend(resp.get("messages", []))
        cursor = (resp.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            return sorted(out, key=lambda m: float(m["ts"]))


def late_pings(messages: list[dict]) -> list[dict]:
    """his @matthias-day pings that were posted after midnight, about the day before."""
    return [
        m for m in messages
        if m.get("user") == config.USER_ID
        and _matthias_day_pinged(m.get("text", "") or "")
        and is_late(m["ts"])
    ]


def count_reply(messages: list[dict], ping_ts: str, noodle_id: str) -> dict | None:
    """noodle's top-level count reply to the ping at `ping_ts`: its first message after it."""
    start = float(ping_ts)
    for m in messages:
        t = float(m["ts"])
        if t <= start:
            continue
        if t > start + REPLY_WINDOW_SECONDS:
            return None
        if m.get("user") == noodle_id and not m.get("thread_ts"):
            return m
    return None


def wrong_day_posts(client: WebClient, where: tuple[str, str], noodle_id: str) -> list[tuple[dict, int, int]]:
    """(post, day it says, day it should say) for noodle's 50 days posts whose linked recap
    is about another day."""
    out = []
    cursor = None
    while True:
        resp = client.conversations_replies(channel=where[0], ts=where[1], cursor=cursor, limit=200)
        for msg in resp.get("messages", []):
            if msg.get("user") != noodle_id:
                continue
            text = (msg.get("text") or "").strip()
            said = fifty_days._DAY_LINE_RE.match(text)
            link = fifty_days._LINK_RE.search(text)
            if not said or not link or not _URL_RE.search(text):
                continue
            recap_ts = f"{link.group(2)}.{link.group(3)}"
            right = fifty_days.day_number(recap_date(recap_ts))
            if right is not None and right != int(said.group(1)):
                out.append((msg, int(said.group(1)), right))
        cursor = (resp.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            return out


def fix_fifty_days(client: WebClient, noodle_id: str, dry: bool) -> int:
    where = fifty_days.target()
    if not where:
        logger.info("50 days: FIFTY_DAYS_THREAD not set, skipping")
        return 0
    already = fifty_days.days_already_posted(client, where)
    fixed = 0
    for msg, said, right in wrong_day_posts(client, where, noodle_id):
        url = _URL_RE.search(msg["text"]).group(1)
        if right in already:
            logger.warning("50 days: day %d is in the thread already too; fixing %s anyway", right, msg["ts"])
        if dry:
            logger.info("50 days (dry run): would change %s from day %d to day %d", msg["ts"], said, right)
            continue
        client.chat_update(channel=where[0], ts=msg["ts"], text=fifty_days.day_text(right, url))
        logger.info("50 days: changed %s from day %d to day %d", msg["ts"], said, right)
        fixed += 1
    return fixed


def fix_channel(client: WebClient, noodle_id: str, since: datetime.date, dry: bool, force: bool) -> tuple[int, int]:
    from bot.stats import build_daily_summary  # imported late: it needs state set up

    channel = config.MATTHIAS_DAY_CHANNEL_ID
    oldest = datetime.datetime.combine(since, datetime.time(0), AMSTERDAM_TZ).timestamp()
    messages = _history(client, channel, oldest)
    replies = redated = 0
    for ping in late_pings(messages):
        day = recap_date(ping["ts"])
        logger.info("late recap %s (posted %s, about %s)", ping["ts"],
                    datetime.datetime.fromtimestamp(float(ping["ts"]), AMSTERDAM_TZ).strftime("%Y-%m-%d %H:%M"), day)

        reply = count_reply(messages, ping["ts"], noodle_id)
        if reply is None:
            logger.info("  no count reply from noodle found")
        elif reply.get("edited") and not force:
            logger.info("  count reply %s was already edited, leaving it (use --force)", reply["ts"])
        elif dry:
            logger.info("  (dry run) would rewrite count reply %s: %r", reply["ts"], reply.get("text", "")[:80])
        else:
            new = build_daily_summary(config.USER_ID, ping["ts"])
            client.chat_update(channel=channel, ts=reply["ts"], text=new)
            logger.info("  rewrote count reply %s: %r", reply["ts"], new)
            replies += 1

        if message_recap(ping.get("text", "") or "", ping.get("blocks")):
            try:
                link = client.chat_getPermalink(channel=channel, message_ts=ping["ts"])["permalink"]
            except Exception:  # noqa: BLE001
                logger.exception("  no permalink for %s, journal date not checked", ping["ts"])
                continue
            if dry:
                logger.info("  (dry run) would date the journal entry %s", recap_created_at(ping["ts"]))
            else:
                n = history_store.set_created_at(link, recap_created_at(ping["ts"]))
                logger.info("  journal entry re-dated (%d row%s)", n, "" if n == 1 else "s")
                redated += n
    return replies, redated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="only show what would change")
    parser.add_argument("--force", action="store_true", help="also rewrite count replies that were already edited")
    parser.add_argument("--since", type=datetime.date.fromisoformat, default=datetime.date(2026, 1, 1),
                        metavar="YYYY-MM-DD", help="first day to look at in #matthias (default 2026-01-01)")
    args = parser.parse_args()
    setup_logging()

    client = WebClient(token=config.SLACK_USER_TOKEN)  # noodle's own token: it can edit its own messages
    noodle_id = client.auth_test()["user_id"]
    # what build_daily_summary needs: the slack client (message counts) and the model
    state.app = type("App", (), {"client": client})()
    state.client = OpenAI(api_key=config.OPENAI_API_KEY, base_url=config.AI_ENDPOINT)
    state.SYSTEM_PROMPT = load_prompt()
    history_store.init_db()

    replies, redated = fix_channel(client, noodle_id, args.since, args.dry_run, args.force)
    posts = fix_fifty_days(client, noodle_id, args.dry_run)
    logger.info("done%s: %d count repl%s rewritten, %d journal row%s re-dated, %d 50 days post%s fixed",
                " (dry run)" if args.dry_run else "",
                replies, "y" if replies == 1 else "ies", redated, "" if redated == 1 else "s",
                posts, "" if posts == 1 else "s")


if __name__ == "__main__":
    main()
