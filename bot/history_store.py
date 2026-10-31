import datetime
import json
import logging
import sqlite3
import threading

import config
from bot.embeddings import embed_text
from bot.tz import AMSTERDAM_TZ

logger = logging.getLogger("noodle")


def init_db() -> None:
    """create the persistent memory db/table if they don't exist yet."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.MEMORY_DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conv_key TEXT NOT NULL,
                kind TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                user_id TEXT,
                embedding TEXT,
                created_at TEXT NOT NULL,
                permalink TEXT
            )
            """
        )
        # lightweight migration for dbs created before "permalink" existed
        existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
        if "permalink" not in existing_cols:
            conn.execute("ALTER TABLE messages ADD COLUMN permalink TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_conv_key ON messages (conv_key, id)"
        )
        conn.commit()
    finally:
        conn.close()


def append_message(
    conv_key: str,
    kind: str,
    role: str,
    content: str,
    user_id: str | None = None,
    created_at: str | None = None,
    permalink: str | None = None,
    require_embedding: bool = False,
) -> None:
    """persist one turn (and its embedding, if that succeeds).

    user_id is the slack id of whoever actually said this - only meaningful
    for "user" turns (noodle's own "assistant" turns pass None, since role
    already disambiguates those). created_at overrides the recorded
    timestamp - used for backfilling a message to the day it actually
    happened instead of now(). permalink is an optional link back to the
    original slack message (e.g. for @matthias-day ping entries, see
    bot/matthias_day.py); if given and already stored, this is a no-op, so
    a backfill script can be re-run safely without duplicating rows.
    require_embedding skips the insert entirely if embedding fails, instead
    of storing a row with a NULL embedding - for rows that exist ONLY to be
    semantically searched (e.g. @matthias-day entries, never hydrated into
    live context), a dead, never-searchable row is worse than no row: with
    require_embedding + a permalink, a failed attempt leaves no trace, so
    re-running the backfill actually retries it instead of silently
    skipping it forever (a stored permalink is what the dedup check above
    keys on). never raises - a storage or embedding hiccup shouldn't break
    chat.
    """
    if not content:
        return
    try:
        conn = sqlite3.connect(config.MEMORY_DB_PATH)
        try:
            if permalink and conn.execute(
                "SELECT 1 FROM messages WHERE permalink = ? LIMIT 1", (permalink,)
            ).fetchone():
                return
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        logger.exception("failed to check for a duplicate permalink")

    embedding = embed_text(content)
    if embedding is None and require_embedding:
        logger.warning(
            "skipping persist (embedding failed) for conv_key=%s - safe to retry later",
            conv_key,
        )
        return
    embedding_json = json.dumps(embedding) if embedding is not None else None
    timestamp = created_at or datetime.datetime.now(AMSTERDAM_TZ).isoformat()
    try:
        conn = sqlite3.connect(config.MEMORY_DB_PATH)
        try:
            conn.execute(
                "INSERT INTO messages (conv_key, kind, role, content, user_id, embedding, created_at, permalink) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (conv_key, kind, role, content, user_id, embedding_json, timestamp, permalink),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        logger.exception("failed to persist message (conv_key=%s role=%s)", conv_key, role)


def delete_kind(kind: str) -> int:
    """delete every row of one kind, returning how many were removed - used
    by scripts/backfill_matthias_day.py --rebuild to start the journal
    over from scratch.
    """
    conn = sqlite3.connect(config.MEMORY_DB_PATH)
    try:
        cur = conn.execute("DELETE FROM messages WHERE kind = ?", (kind,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def delete_containing(text: str) -> int:
    """delete every row whose content contains `text` (case-insensitive), returning how
    many were removed - used by scripts/purge_morning.py to forget a message."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    conn = sqlite3.connect(config.MEMORY_DB_PATH)
    try:
        cur = conn.execute(
            "DELETE FROM messages WHERE content LIKE ? ESCAPE '\\'", (f"%{escaped}%",)
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def append_message_async(
    conv_key: str, kind: str, role: str, content: str, user_id: str | None = None
) -> None:
    """fire-and-forget append_message() in the background.

    append_message() embeds the content, which is a network call - doing it
    inline would add that round trip to every single reply's latency. this
    runs it off the critical path instead; nothing waits on it.
    """
    # stamp the time NOW: the row is inserted only after the (variable-length) embedding
    # call finishes, so stamping it there made rows land out of order - a reply could
    # end up before the question it answered when history was reloaded after a restart.
    sent_at = datetime.datetime.now(AMSTERDAM_TZ).isoformat()
    threading.Thread(
        target=append_message,
        args=(conv_key, kind, role, content),
        kwargs={"user_id": user_id, "created_at": sent_at},
        daemon=True,
    ).start()


def load_history(conv_key: str, limit: int = 48) -> list[dict]:
    """load the most recent persisted turns for one conversation, oldest first."""
    try:
        conn = sqlite3.connect(config.MEMORY_DB_PATH)
        try:
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE conv_key = ? "
                "ORDER BY julianday(created_at) DESC, id DESC LIMIT ?",
                (conv_key, limit),
            ).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        logger.exception("failed to load persisted history (conv_key=%s)", conv_key)
        return []
    rows.reverse()
    return [{"role": role, "content": content} for role, content in rows]


def load_kind_between(kind: str, start_date: str, end_date: str) -> list[dict]:
    """rows of one kind whose local date is in [start_date, end_date]
    (inclusive, 'YYYY-MM-DD'), oldest first. created_at is stored as an
    amsterdam iso timestamp, so comparing its date prefix is a local-date
    compare and the cet/cest offset can't skew it."""
    try:
        conn = sqlite3.connect(config.MEMORY_DB_PATH)
        try:
            rows = conn.execute(
                "SELECT created_at, content, permalink FROM messages WHERE kind = ? "
                "AND substr(created_at, 1, 10) >= ? AND substr(created_at, 1, 10) <= ? "
                "ORDER BY created_at",
                (kind, start_date, end_date),
            ).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        logger.exception("failed to load %s rows between %s and %s", kind, start_date, end_date)
        return []
    return [{"created_at": c, "content": t, "permalink": p} for c, t, p in rows]


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if not norm_a or not norm_b:
        return 0.0
    return dot / (norm_a * norm_b)


def search_similar(query_embedding: list[float], limit: int = 5) -> list[dict]:
    """brute-force semantic search over every embedded message.

    fine for a personal bot's history (thousands of short messages, not
    millions) - no vector index needed, just a linear cosine-similarity scan.
    """
    try:
        conn = sqlite3.connect(config.MEMORY_DB_PATH)
        try:
            rows = conn.execute(
                "SELECT kind, role, content, user_id, embedding, created_at, permalink FROM messages "
                "WHERE embedding IS NOT NULL"
            ).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        logger.exception("failed to query persisted memory for search")
        return []

    scored = []
    for kind, role, content, user_id, embedding_json, created_at, permalink in rows:
        try:
            vector = json.loads(embedding_json)
        except (TypeError, ValueError):
            continue
        score = _cosine(query_embedding, vector)
        scored.append((score, kind, role, content, user_id, created_at, permalink))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [
        {
            "score": score,
            "kind": kind,
            "role": role,
            "content": content,
            "user_id": user_id,
            "created_at": created_at,
            "permalink": permalink,
        }
        for score, kind, role, content, user_id, created_at, permalink in scored[:limit]
    ]
