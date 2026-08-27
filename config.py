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
MAX_FRAGMENT_CHARS = int(os.getenv("MAX_FRAGMENT_CHARS", "200"))

# --- paths ---
SYSTEM_PROMPT_PATH = BASE_DIR / "prompts" / "system_prompt.md"
LOG_DIR = BASE_DIR / "logs"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
