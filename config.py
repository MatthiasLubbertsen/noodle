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

# --- channels ---
# comma separated channel ids where noodle replies to mentions.
# leave empty to disable channel replies, or set to "*" to allow any channel.
ALLOWED_CHANNELS_RAW = os.getenv("ALLOWED_CHANNELS", "")
ALLOWED_CHANNELS = [c.strip() for c in ALLOWED_CHANNELS_RAW.split(",") if c.strip()]
ALLOW_CHANNEL_WILDCARD = "*" in ALLOWED_CHANNELS

# --- chunking / pacing ---
CHUNK_DELAY_SECONDS = float(os.getenv("CHUNK_DELAY_SECONDS", "0.5"))
MAX_FRAGMENT_CHARS = int(os.getenv("MAX_FRAGMENT_CHARS", "1500"))

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
# optional - if set, matched by id (<!subteam^ID>); the label "matthias-day" is
# always matched too as a fallback.
MATTHIAS_DAY_GROUP_ID = os.getenv("MATTHIAS_DAY_GROUP_ID", "")

# --- paths ---
SYSTEM_PROMPT_PATH = BASE_DIR / "prompts" / "system_prompt.md"
LOG_DIR = BASE_DIR / "logs"
DATA_DIR = BASE_DIR / "data"
NEWS_SEEN_PATH = DATA_DIR / "hackclub_news_seen.json"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
