import datetime
import logging
import sqlite3

import config
from bot.tz import AMSTERDAM_TZ

logger = logging.getLogger("noodle")


def init_db() -> None:
    """create the persistent memory db/table if they don't exist yet."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.MEMORY_DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dm_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conv_key TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_dm_messages_conv_key "
            "ON dm_messages (conv_key, id)"
        )
        conn.commit()
    finally:
        conn.close()


def append_message(conv_key: str, role: str, content: str) -> None:
    """persist one dm turn. never raises - a storage hiccup shouldn't break chat."""
    if not content:
        return
    try:
        conn = sqlite3.connect(config.MEMORY_DB_PATH)
        try:
            conn.execute(
                "INSERT INTO dm_messages (conv_key, role, content, created_at) "
                "VALUES (?, ?, ?, ?)",
                (conv_key, role, content, datetime.datetime.now(AMSTERDAM_TZ).isoformat()),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        logger.exception("failed to persist dm message (conv_key=%s role=%s)", conv_key, role)


def load_history(conv_key: str, limit: int = 48) -> list[dict]:
    """load the most recent persisted turns for a conversation, oldest first."""
    try:
        conn = sqlite3.connect(config.MEMORY_DB_PATH)
        try:
            rows = conn.execute(
                "SELECT role, content FROM dm_messages WHERE conv_key = ? "
                "ORDER BY id DESC LIMIT ?",
                (conv_key, limit),
            ).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        logger.exception("failed to load persisted dm history (conv_key=%s)", conv_key)
        return []
    rows.reverse()
    return [{"role": role, "content": content} for role, content in rows]
