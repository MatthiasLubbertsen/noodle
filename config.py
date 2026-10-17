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

# "HH:MM" time noodle DMs a short end-of-day wrap-up (slack messages, commits,
# tracks played). it never drafts the @matthias-day recap - matthias writes that.
DAILY_WRAPUP_DM = os.getenv("DAILY_WRAPUP_DM", "21:30")
# weekly jobs, "<mon|tue|wed|thu|fri|sat|sun> HH:MM" (amsterdam time):
# a recap of the week's @matthias-day journal entries...
WEEKLY_JOURNAL = os.getenv("WEEKLY_JOURNAL", "sun 18:00")
# ...and a digest of the busiest channels noodle is in
WEEKLY_DIGEST = os.getenv("WEEKLY_DIGEST", "mon 09:00")
# ...and a music insights recap from last.fm (needs LASTFM_*)
WEEKLY_MUSIC = os.getenv("WEEKLY_MUSIC", "sun 18:30")

# --- proactivity guardrails ---
# unprompted pings (the github watcher) are held during quiet hours
# ("HH:MM-HH:MM", may wrap past midnight) and capped per day. fixed-time dms
# (morning, wrap-up, weekly) are exempt.
QUIET_HOURS = os.getenv("QUIET_HOURS", "23:00-07:30")
PROACTIVE_DAILY_CAP = int(os.getenv("PROACTIVE_DAILY_CAP", "10"))

# --- weather (open-meteo, no key needed) ---
# PRIVATE (this is where matthias lives): set these in .env only, never
# hardcode a default here. the get_weather tool's default when no place is given
WEATHER_DEFAULT_LOCATION = os.getenv("WEATHER_DEFAULT_LOCATION", "").strip()
# places covered by the morning dm
WEATHER_LOCATIONS = [
    n.strip() for n in os.getenv("WEATHER_LOCATIONS", "").split(",") if n.strip()
]

# --- last.fm (now playing; spotify can scrobble to it, no oauth app needed) ---
# both optional - unset just disables the now_playing tool / wrap-up line
LASTFM_API_KEY = os.getenv("LASTFM_API_KEY", "")
LASTFM_USER = os.getenv("LASTFM_USER", "")

# --- github (tool + notification watcher) ---
# a CLASSIC personal access token (scopes: repo, notifications) - the
# notifications api doesn't work with fine-grained tokens. optional: unset
# disables the tool, the watcher and the wrap-up commit count.
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_USER = os.getenv("GITHUB_USER", "MatthiasLubbertsen")
# how often the watcher checks for new notifications
GITHUB_POLL_SECONDS = int(os.getenv("GITHUB_POLL_SECONDS", "300"))

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
# comma-separated device NAMES (exactly as shown in freedom's dashboard) -
# ONLY these may ever be blocked. the freedom account is SHARED with
# matthias's dad, so "every device on the account" is never safe here - it
# would block his dad's devices too. resolved to ids at call time via
# GET /api/devices, so renaming a device in freedom just means updating
# this list, not redeploying code.
FREEDOM_DEVICE_NAMES = [
    n.strip() for n in os.getenv("FREEDOM_DEVICE_NAMES", "Matthias Windows,mopa5m").split(",") if n.strip()
]

# --- matthias's OWN slack user token (xoxp-..., scope search:read) ---
# used for exactly ONE thing: counting how many messages matthias sent today
# (bot/stats.py), so private channels and dms are counted too - noodle's own
# token can only see what noodle has joined. only the total count is ever read.
# NEVER use this anywhere else (not for reading messages, not for posting, not
# for any tool). unset = the count falls back to noodle's own, narrower search.
MATTHIAS_USER_TOKEN = os.getenv("MATTHIAS_USER_TOKEN", "")

# --- code sandbox (python + javascript in wasm, separate container) ---
# base url of the sandbox service and its shared secret. NO TOKEN = NO SANDBOX: the run_code
# tool is not offered to the model at all (see bot/features.py) and the sandbox container idles.
SANDBOX_URL = os.getenv("SANDBOX_URL", "http://sandbox:8080").rstrip("/")
SANDBOX_TOKEN = os.getenv("SANDBOX_TOKEN", "")

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
GITHUB_SEEN_PATH = DATA_DIR / "github_seen.json"
PROACTIVE_STATE_PATH = DATA_DIR / "proactive_state.json"
GAMES_STATE_PATH = DATA_DIR / "games.json"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
