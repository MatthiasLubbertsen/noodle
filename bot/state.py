import logging
import threading

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
# last time (unix seconds) noodle sent an unprompted (no-mention) reply, per
# channel id, so it doesn't chime in over and over back to back
LAST_UNPROMPTED_REPLY: dict = {}
# one lock per conv_key, so messages to the same dm/thread/channel are
# handled one at a time, in order - without this, several messages arriving
# close together each spawn their own thread and race on the same
# state.MEMORY[conv_key] list, corrupting ordering and making replies come
# out jumbled/delayed. see handlers._get_conv_lock().
CONV_LOCKS: dict = {}
CONV_LOCKS_GUARD = threading.Lock()

# the last thing noodle said in each conversation: conv_key -> (unix ts, text). gives the
# model a clear anchor for short replies ("sad", "lol") - they're about THIS message.
LAST_REPLY: dict = {}
