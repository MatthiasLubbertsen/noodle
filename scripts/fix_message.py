"""one-off: fix a message noodle sent by editing it in place, instead of deleting and resending.

edits the slack message (chat.update, as noodle's own account) and the matching memory
row(s) in data/noodle_memory.db (text rewritten, embedding recomputed). dry run by default.

    python -m scripts.fix_message --channel D0BQXEME7GD --ts 1793373300.123456 \
        --text "oh yes, we love github's reliability (when it works)." \
        --find "time to write your day"            # dry run: shows what would change
    ... --apply                                    # do it

--find is a phrase from the bad text: every assistant row containing it in the dm
conversation is rewritten to --text. the running bot keeps its live copy of the chat in
memory, so restart it afterwards (it reloads the dm history from the db on startup).
"""

import argparse
import json
import sqlite3

from slack_sdk import WebClient

import config
from bot.embeddings import embed_text
from bot.memory import dm_conv_key


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", required=True, help="channel id of the message (the dm)")
    parser.add_argument("--ts", required=True, help="ts of the slack message to edit")
    parser.add_argument("--text", required=True, help="the corrected text")
    parser.add_argument("--find", required=True, help="phrase identifying the bad memory row(s)")
    parser.add_argument("--apply", action="store_true", help="actually make the changes")
    args = parser.parse_args()

    conn = sqlite3.connect(config.MEMORY_DB_PATH)
    rows = conn.execute(
        "SELECT id, created_at, content FROM messages "
        "WHERE conv_key = ? AND role = 'assistant' AND content LIKE ?",
        (dm_conv_key(), f"%{args.find}%"),
    ).fetchall()
    print(f"slack: {args.channel} {args.ts} -> {args.text!r}")
    for row_id, created_at, content in rows:
        print(f"memory row {row_id} ({created_at}): {content!r} -> {args.text!r}")
    if not rows:
        print("no matching memory rows")
    if not args.apply:
        print("dry run, nothing changed (add --apply)")
        return

    WebClient(token=config.SLACK_USER_TOKEN).chat_update(
        channel=args.channel, ts=args.ts, text=args.text
    )
    embedding = embed_text(args.text)
    for row_id, _, _ in rows:
        conn.execute(
            "UPDATE messages SET content = ?, embedding = ? WHERE id = ?",
            (args.text, json.dumps(embedding) if embedding is not None else None, row_id),
        )
    conn.commit()
    print(f"updated the slack message and {len(rows)} memory row(s)")


if __name__ == "__main__":
    main()
