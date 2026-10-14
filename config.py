import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value


# --- slack ---
SLACK_USER_TOKEN = _require("SLACK_USER_TOKEN")
SLACK_APP_TOKEN = _require("SLACK_APP_TOKEN")

# --- security ---
# only this slack user id is allowed to talk to noodle via direct message
USER_ID = _require("USER_ID")

# --- ai / openrouter ---
OPENAI_API_KEY = _require("OPENAI_API_KEY")

# openrouter exposes an openai-compatible api at <base>/v1
AI_ENDPOINT = os.getenv("AI_ENDPOINT", "https://openrouter.ai/api")
if not AI_ENDPOINT.endswith("/v1"):
    AI_ENDPOINT = AI_ENDPOINT.rstrip("/") + "/v1"

MODEL = os.getenv("MODEL", "gpt-4o-mini")

# embeddings model for semantic memory search, via the ai endpoint's
# /embeddings route (see bot/embeddings.py). qwen3-embedding-8b: cheap
# (~$0.01/1M tokens), multilingual, 32k context - deliberately not a :free
# model (those come with retention/quality tradeoffs not worth it here).
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "qwen/qwen3-embedding-8b")

# --- channels ---
# comma separated channel ids where noodle replies to mentions.
# leave empty to disable channel replies, or set to "*" to allow any channel.
ALLOWED_CHANNELS_RAW = os.getenv("ALLOWED_CHANNELS", "")
ALLOWED_CHANNELS = [c.strip() for c in ALLOWED_CHANNELS_RAW.split(",") if c.strip()]
ALLOW_CHANNEL_WILDCARD = "*" in ALLOWED_CHANNELS

# --- chunking / pacing ---
CHUNK_DELAY_SECONDS = float(os.getenv("CHUNK_DELAY_SECONDS", "0.5"))

# minimum gap between unprompted (no-mention) replies in the same channel, so
# noodle doesn't chime into a busy channel over and over back to back.
UNPROMPTED_COOLDOWN_SECONDS = float(os.getenv("UNPROMPTED_COOLDOWN_SECONDS", "300"))

# --- daily DMs ---
# everything runs in Europe/Amsterdam time - not configurable, matthias lives there.
# "HH:MM" time noodle DMs USER_ID a reminder to send today's @matthias-day ping
DAILY_PING_REMINDER = os.getenv("DAILY_PING_REMINDER", "19:00")
# "HH:MM" time noodle DMs USER_ID the wakey-wakey / special-day / news digest
DAILY_MORNING_DM = os.getenv("DAILY_MORNING_DM", "08:00")

# the "@matthias-day" usergroup: pinging it triggers a "messages sent today" recap.
# matched by id (<!subteam^ID>); the label "matthias-day" is always matched
# too as a fallback (catches a plain, unexpanded "@matthias-day" typed as
# text). defaults to the real usergroup id - a real subteam mention never
# contains the literal text "matthias-day" (slack renders it as a bare
# <!subteam^ID>, no readable label), so without this id configured the
# fallback alone essentially never matches and the ping goes undetected.
MATTHIAS_DAY_GROUP_ID = os.getenv("MATTHIAS_DAY_GROUP_ID", "S0AK8LYJ8FR")

# the one channel matthias posts his daily @matthias-day recap in - used by
# bot/matthias_day.py (the live listener) and scripts/backfill_matthias_day.py.
MATTHIAS_DAY_CHANNEL_ID = os.getenv("MATTHIAS_DAY_CHANNEL_ID", "C0A9NJT2BCJ")

# --- freedom.to focus sessions ---
# matthias's self-hosted api for his freedom.to account
# (github.com/MatthiasLubbertsen/freedom) - see bot/freedom.py. empty key
# means the start_freedom_session tool just reports it's not configured,
# rather than this being a _require()d startup failure (it's optional).
FREEDOM_API_BASE = os.getenv("FREEDOM_API_BASE", "https://freedom.matthiaz.dev")
FREEDOM_API_KEY = os.getenv("FREEDOM_API_KEY", "")

# --- channel join/leave analytics ---
# the one channel noodle watches for member_joined_channel/member_left_channel.
# leave empty to disable this feature entirely.
WATCHED_CHANNEL_ID = os.getenv("WATCHED_CHANNEL_ID", "")
# where join/leave (+ @matthias-day group changes) get logged
LOGS_CHANNEL_ID = os.getenv("LOGS_CHANNEL_ID", "")
# ephemeral message posted in the watched channel when someone joins,
# visible only to the person who just joined (welcoming them in). "{user}"
# is replaced with a real <@USERID> mention of that person.
JOIN_EPHEMERAL_MESSAGE = os.getenv(
    "JOIN_EPHEMERAL_MESSAGE", "hey {user}, welcome! say hi and introduce yourself :)"
)

# --- paths ---
SYSTEM_PROMPT_PATH = BASE_DIR / "prompts" / "system_prompt.md"
LOG_DIR = BASE_DIR / "logs"
DATA_DIR = BASE_DIR / "data"
NEWS_SEEN_PATH = DATA_DIR / "hackclub_news_seen.json"
# persistent, embedded conversation history (dms, threads, direct pings) -
# sqlite, see bot/history_store.py.
MEMORY_DB_PATH = DATA_DIR / "noodle_memory.db"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
