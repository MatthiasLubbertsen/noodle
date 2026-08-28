import logging

import config

# shared, mutable runtime state. set by bot.app.build_app() at startup.
# keeping it here avoids circular imports between the smaller modules.
logger = logging.getLogger("noodle")

app = None
client = None
AUTH_USER_ID = None
BOT_ID = None
MENTION_IDS: set = set()
SYSTEM_PROMPT = ""

# short-term per-conversation memory (in-memory, resets on restart)
MEMORY: dict = {}
# threads noodle has joined (by replying) so it keeps answering in them
PARTICIPATING_THREADS: set = set()
