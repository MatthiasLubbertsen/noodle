import datetime
import json
import logging
import sqlite3

import config
from bot.embeddings import embed_text
from bot.tz import AMSTERDAM_TZ

logger = logging.getLogger("noodle")


def init_db() -> None:
    """create the persistent memory db/table if they don't exist yet.

    also runs a one-time migration from the old dm-only "dm_messages" table
    (pre-semantic-search) into the new general "messages" table, if found.
    """
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
                embedding TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_conv_key ON messages (conv_key, id)"
        )
        old_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='dm_messages'"
        ).fetchone()
        if old_table:
            conn.execute(
                "INSERT INTO messages (conv_key, kind, role, content, embedding, created_at) "
                "SELECT conv_key, 'dm', role, content, NULL, created_at FROM dm_messages"
            )
            conn.execute("DROP TABLE dm_messages")
            logger.info("migrated old dm_messages rows into the messages table")
        conn.commit()
    finally:
        conn.close()


def append_message(conv_key: str, kind: str, role: str, content: str) -> None:
    """persist one turn (and its embedding, if that succeeds).

    never raises - a storage or embedding hiccup shouldn't break chat.
    """
    if not content:
        return
    embedding = embed_text(content)
    embedding_json = json.dumps(embedding) if embedding is not None else None
    try:
        conn = sqlite3.connect(config.MEMORY_DB_PATH)
        try:
            conn.execute(
                "INSERT INTO messages (conv_key, kind, role, content, embedding, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    conv_key,
                    kind,
                    role,
                    content,
                    embedding_json,
                    datetime.datetime.now(AMSTERDAM_TZ).isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        logger.exception("failed to persist message (conv_key=%s role=%s)", conv_key, role)


def load_history(conv_key: str, limit: int = 48) -> list[dict]:
    """load the most recent persisted turns for one conversation, oldest first."""
    try:
        conn = sqlite3.connect(config.MEMORY_DB_PATH)
        try:
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE conv_key = ? "
                "ORDER BY id DESC LIMIT ?",
                (conv_key, limit),
            ).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        logger.exception("failed to load persisted history (conv_key=%s)", conv_key)
        return []
    rows.reverse()
    return [{"role": role, "content": content} for role, content in rows]


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
                "SELECT kind, role, content, embedding, created_at FROM messages "
                "WHERE embedding IS NOT NULL"
            ).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        logger.exception("failed to query persisted memory for search")
        return []

    scored = []
    for kind, role, content, embedding_json, created_at in rows:
        try:
            vector = json.loads(embedding_json)
        except (TypeError, ValueError):
            continue
        score = _cosine(query_embedding, vector)
        scored.append((score, kind, role, content, created_at))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [
        {"score": score, "kind": kind, "role": role, "content": content, "created_at": created_at}
        for score, kind, role, content, created_at in scored[:limit]
    ]
