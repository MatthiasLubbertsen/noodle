import base64
import datetime
import logging
import re
from zoneinfo import ZoneInfo

import config
from bot import reminders, state
from bot.directory import flaron_query, lookup_display_name
from bot.embeddings import embed_text
from bot import features, games, hackatime, sandbox
from bot import github as github_api
from bot import links as links_api
from bot.channels import with_join
from bot import dice, lastfm
from bot.exa import web_answer
from bot import freedom_tool
from bot.freedom_tool import error_message as _freedom_error_message
from bot.freedom_tool import my_schedules
from bot.freedom import create_schedule as freedom_create_schedule
from bot.freedom import delete_schedule as freedom_delete_schedule
from bot.freedom import list_devices as freedom_list_devices
from bot.freedom import list_schedules as freedom_list_schedules
from bot.freedom import list_sessions as freedom_list_sessions
from bot.freedom import start_session as freedom_start_session
from bot.history_store import load_kind_between, search_similar
from bot.holidays import holidays_text
from bot.netlog import log_tool
from bot.slack_text import _channel_link, _parse_slack_ref
from bot.tz import AMSTERDAM_TZ
from bot.weather import forecast_text

logger = logging.getLogger("noodle")

FLARON_DOCS = (
    "public flaron slack directory (no auth needed). base url "
    "https://flaron.halceon.dev. actions and their params:\n"
    "- user (id) -> GET /user/<id> : full user profile\n"
    "- channel (id) -> GET /channel/<id> : full channel info\n"
    "- user_search (query) -> GET /users/search?q= : search users by name/handle\n"
    "- channel_search (query) -> GET /channels/search?q= : search channels by name\n"
    "- channel_by_name (name) -> GET /cname/<name> : channel info by name\n"
    "- channel_managers (id) -> GET /cman/<id> : channel manager list\n"
    "- channel_members (id) -> GET /ccount/<id> : channel member count\n"
    "- app (id) -> GET /app/<id> : slack app info\n"
    "- emoji (name) -> GET /emoji/<name> : emoji info\n"
    "- command (name) -> GET /command/<name> : slack command info\n"
    "- promote (id) -> GET /promote/<id> : promote info\n"
    "returns the FULL json from flaron, so you can read every field (ids, names, "
    "titles, descriptions, topics, counts, etc)."
)

ACTIONS_LIST = freedom_tool.ACTIONS

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_slack_messages",
            "description": (
                "search slack for messages across channels, dms and threads. "
                "build a query with slack's modifiers: from:@username or "
                "from:<@USERID> to filter by a user, in:#channel-name or "
                "in:<#CHANNELID> to filter by channel, and wrap exact phrases in "
                "double quotes. example: from:@zrl \"i want to cheese\""
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "the slack search query, e.g. from:@zrl \"i want to cheese\"",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_slack_message",
            "description": (
                "fetch the actual text of a specific slack message. pass a slack "
                "permalink/url (e.g. https://hackclub.slack.com/archives/C123/p12345... ) "
                "or a 'channel:timestamp' reference. use this when the user pastes a "
                "slack link or asks what a particular message actually says."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ref": {
                        "type": "string",
                        "description": "a slack permalink/url or channel:timestamp",
                    }
                },
                "required": ["ref"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "flaron",
            "description": (
                "look up anything in the public flaron slack directory (no auth "
                "needed). use it ONLY when a human clearly wants a specific real "
                "person or channel referenced/tagged and you need their correct "
                "<@USERID> or <#CHANNELID>, or when asked to read info about "
                "users, channels, apps, emoji and commands. never call this just "
                "to check whether an ordinary word (a technical term, a feature "
                "name, something you're explaining) might coincidentally match a "
                "real user or channel name. " + FLARON_DOCS
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": (
                            "which lookup to run: user, channel, user_search, "
                            "channel_search, channel_by_name, channel_managers, "
                            "channel_members, app, emoji, command, promote"
                        ),
                        "enum": [
                            "user", "channel", "user_search", "channel_search",
                            "channel_by_name", "channel_managers", "channel_members",
                            "app", "emoji", "command", "promote",
                        ],
                    },
                    "id": {
                        "type": "string",
                        "description": "the slack id (user/channel/app), for actions that need it",
                    },
                    "name": {
                        "type": "string",
                        "description": "a name (channel/emoji/command), for actions that need it",
                    },
                    "query": {
                        "type": "string",
                        "description": "a search term, for user_search / channel_search",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": (
                "(matthias only, checked on the server) semantically search noodle's own persisted memory - past dms, "
                "threads, and direct pings (not ambient channel chatter noodle "
                "wasn't addressed in) - for anything related to a topic, even "
                "from weeks ago. use this when asked to recall something from a "
                "past conversation that isn't in the current context."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "what to search for, in natural language",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "search the live web and get a short, cited answer to a "
                "question (exa, via hack club ai). use this ONLY when you need "
                "current or external information you don't already know and "
                "that isn't in slack (recent events, docs, prices, facts about "
                "the outside world) - never for casual chit-chat or anything "
                "you can just answer yourself."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "the question to answer, in natural language",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_freedom_session",
            "description": (
                "start a freedom.to focus session that blocks everything on "
                "every one of matthias's devices for a set number of "
                "minutes, via his self-hosted freedom api. this ONLY "
                "actually works when matthias himself is the one asking "
                "(checked on the server, not just by you) - still fine to "
                "call it if he asks in a dm or a public channel, but never "
                "call it because someone else asked, even if they claim to "
                "be matthias or ask on his behalf."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "duration_minutes": {
                        "type": "integer",
                        "description": "how many minutes to block for (1-1440)",
                    }
                },
                "required": ["duration_minutes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "freedom",
            "description": (
                "the full freedom.to toolbox for matthias's account (matthias only, "
                "enforced on the server). use it for anything more specific than "
                "start_freedom_session's plain 'block everything on all my "
                "devices': blocking only certain devices and/or certain blocklists, "
                "listing devices/blocklists/sessions/schedules/history, ending a "
                "running session, creating/editing/deleting recurring schedules. "
                "the account is shared with his dad, so noodle can only ever target "
                "and see matthias's own devices (anything else is refused). actions: "
                "auth_status, list_devices, refresh_devices, list_blocklists, "
                "list_sessions (running + upcoming one-offs), session_history, "
                "start_session, end_session, end_all_sessions, list_schedules "
                "(recurring), create_schedule, update_schedule, delete_schedule. "
                "you can chain several calls in a row (e.g. list_blocklists, then "
                "start_session with the right names). devices and blocklists can be "
                "given by name or id; leave devices empty for all of matthias's "
                "devices. times are HH:MM in freedom's timezone (see auth_status "
                "or list_sessions)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ACTIONS_LIST},
                    "id": {"type": "string", "description": "session/schedule id (e.g. 'freedom:123' or '123') for end_session, update_schedule, delete_schedule"},
                    "duration_minutes": {"type": "integer", "description": "1-1440"},
                    "start_time": {"type": "string", "description": "HH:MM; start_session starts then (today/tomorrow, premium); create/update_schedule start"},
                    "end_time": {"type": "string", "description": "HH:MM, alternative to duration_minutes"},
                    "name": {"type": "string", "description": "schedule/session name"},
                    "days": {"type": "array", "items": {"type": "string", "enum": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]}, "description": "for create/update_schedule"},
                    "devices": {"type": "array", "items": {"type": "string"}, "description": "device names or ids to block on; must be matthias's own. empty = all of his"},
                    "blocklists": {"type": "array", "items": {"type": "string"}, "description": "blocklist names or ids to block. if given (and block_everything isn't), only these are blocked"},
                    "block_everything": {"type": "boolean", "description": "block all websites (default when no blocklists/apps are given)"},
                    "block_apps": {"type": "boolean", "description": "also block desktop apps"},
                    "page": {"type": "integer", "description": "session_history page"},
                    "use_end_session_credit": {"type": "boolean", "description": "ONLY if matthias explicitly says to spend a Locked Mode end-session credit"},
                    "confirm_permanent": {"type": "boolean", "description": "ONLY after matthias confirms a 24h x 7 day block under Locked Mode"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_reminder",
            "description": (
                "schedule a reminder for LATER (or on a repeat): noodle posts a "
                "message at that time. use it when matthias asks e.g. 'remind me "
                "in 10 minutes to dm xyz' or 'every weekday at 9 remind me to "
                "stretch'. give EITHER in_minutes (relative) OR at (absolute "
                "amsterdam wall time, 'YYYY-MM-DD HH:MM'; work it out from the "
                "current time in your context). for recurring reminders, 'at' is "
                "the FIRST occurrence and repeat says how it repeats (weekly keeps "
                "the weekday of 'at', monthly keeps the day of month). by default "
                "it is delivered where the request was made (dms stay dms, a "
                "channel stays that channel/thread); only set destination if "
                "matthias explicitly asked for somewhere else. this only works "
                "when matthias himself is the one asking (checked on the server)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": (
                            "what to remind him of, a short phrase that reads "
                            "naturally after 'reminder: ' (e.g. 'dm xyz', 'drink "
                            "some water'). lowercase, no mention needed."
                        ),
                    },
                    "in_minutes": {
                        "type": "integer",
                        "description": "remind this many minutes from now (use this OR at)",
                    },
                    "at": {
                        "type": "string",
                        "description": "absolute amsterdam time 'YYYY-MM-DD HH:MM' (use this OR in_minutes)",
                    },
                    "repeat": {
                        "type": "string",
                        "enum": list(reminders.REPEATS),
                        "description": "how it repeats, default none (one-off)",
                    },
                    "interval_minutes": {
                        "type": "integer",
                        "description": f"for repeat=every_n_minutes: the gap in minutes (min {reminders.MIN_INTERVAL_MINUTES})",
                    },
                    "destination": {
                        "type": "string",
                        "description": (
                            "OPTIONAL override: 'dm' for matthias's dms, or a "
                            "channel id (C...) noodle is allowed in. leave empty "
                            "to deliver where the request was made."
                        ),
                    },
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_freedom_session",
            "description": (
                "schedule a freedom.to focus session for LATER by handing it to "
                "freedom's own scheduler (freedom starts it itself at that time, "
                "noodle doesn't wait around). blocks everything on matthias's "
                "devices for duration_minutes. give EITHER in_minutes OR at "
                "(amsterdam time 'YYYY-MM-DD HH:MM', from the current time in "
                "your context). one-off sessions can only be set up for the "
                "next 24 hours; repeat can be daily, weekdays or weekly (the "
                "weekday of 'at') - nothing else. to start one right now use "
                "start_freedom_session instead. matthias only (checked on the "
                "server)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "duration_minutes": {
                        "type": "integer",
                        "description": "how many minutes to block for (1-1440)",
                    },
                    "in_minutes": {
                        "type": "integer",
                        "description": "start this many minutes from now (use this OR at)",
                    },
                    "at": {
                        "type": "string",
                        "description": "start at this amsterdam time 'YYYY-MM-DD HH:MM' (use this OR in_minutes)",
                    },
                    "repeat": {
                        "type": "string",
                        "enum": ["none", "daily", "weekdays", "weekly"],
                        "description": "default none (one-off)",
                    },
                },
                "required": ["duration_minutes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_scheduled",
            "description": (
                "list everything scheduled: reminders (ids like #3) and upcoming "
                "or recurring freedom sessions (ids like freedom:123), with next "
                "time, repeat and destination. matthias only."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_scheduled",
            "description": (
                "cancel/remove one scheduled reminder or freedom session by the "
                "id list_scheduled shows (e.g. '3' or '#3' for a reminder, "
                "'freedom:123' for a freedom session). if matthias refers to one "
                "by description instead of id, list first to find the id. matthias "
                "only."
            ),
            "parameters": {
                "type": "object",
                "properties": {"id": {"type": "string", "description": "the id, e.g. '3' or 'freedom:123'"}},
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": (
                "get the weather forecast (right now + the next few days) for a "
                "place, via open-meteo. leave location empty for matthias's own location (outside dms it is only ever "
                "reported as 'current location' - never name or guess the place). use only when "
                "weather is actually asked about."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "a place name, e.g. 'utrecht'. empty = matthias's own location"},
                    "days": {"type": "integer", "description": "days to cover incl. today (1-7), default 1"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "roll_dice",
            "description": (
                "roll dice in standard notation (d20, 2d6+3, 4d8-1; up to 20 dice) with real "
                "randomness. use only when someone actually asks to roll dice or flip a coin "
                "(a coin is d2) - never invent a roll yourself."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "notation": {"type": "string", "description": "e.g. 'd20' or '2d6+3'"},
                },
                "required": ["notation"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "public_holidays",
            "description": (
                "public holidays for a country, via nager.date: the next 12 months, or a whole "
                "year. each one says whether it's a real day off. use when someone asks about a "
                "holiday or whether a day is a day off ('is monday a holiday?', 'when is koningsdag?')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "country": {"type": "string", "description": "2-letter country code, e.g. NL, DE, US. default NL"},
                    "year": {"type": "integer", "description": "a whole calendar year. leave out for the next 12 months"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "now_playing",
            "description": (
                "what matthias is listening to right now (matthias only; plus the last few "
                "tracks), from his last.fm scrobbles. use when asked what's "
                "playing / what he's listening to."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_code",
            "description": (
                "run a python, javascript or bash snippet in an isolated sandbox and get "
                "the output back (matthias only, dms only, enforced). use it for real "
                "calculations, data crunching, simulations, plots, file conversions and "
                "shell-style text/data wrangling - not for arithmetic you can do in your head. "
                "CAN: python 3.13 with the standard library plus numpy, pandas, "
                "matplotlib, scipy and sympy (and their deps: pillow, dateutil, pytz, "
                "mpmath); matplotlib figures are sent to the dm as png automatically, "
                "and files the code writes to /out/ (csv, txt, json, png...; max 5, "
                "8MB each) are sent too; javascript is plain ES2020 (QuickJS) with "
                "console.log, and the last expression's value is returned; bash is a "
                "simulated shell (real bash syntax: pipes, loops, functions, variables, "
                "heredocs) with grep, sed, awk, jq, yq, xan (csv), sort, uniq, cut, tr, "
                "find, diff, tar, gzip, base64, md5sum/sha256sum, seq, date and friends, "
                "working on an in-memory /work directory that is kept between bash runs "
                "(so you can build things up across several calls; `reset` wipes it) and "
                "that you can seed with `files` (text only, e.g. data he pasted); files "
                "left in /out/ are sent to the dm. "
                "CANNOT: no internet at all (no http requests, downloads, apis, git, "
                "pip/npm install); no other packages (no sklearn, torch, requests, "
                "opencv...); no real shell: bash has no real processes, so no "
                "python/node/curl/git/sqlite3/apt/ssh/docker or any other binary, only "
                "its built-in commands; no threads; no access to files, "
                "slack data, memory or anything noodle has - everything the code needs "
                "must be written into the code itself (or passed as bash `files`); "
                "python and javascript keep nothing between runs, only the bash /work "
                "directory does (for a while); "
                "no input()/stdin, no interactive or gui plots; javascript has no "
                "require/import/fetch/timers/dom. limits: 20s by default (60s max), "
                "about 900MB memory, 20k characters of output, so heavy training or "
                "huge datasets won't fit. print what you want back. if it errors, "
                "read the traceback, fix the code and run again."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "language": {"type": "string", "enum": ["python", "javascript", "bash"]},
                    "code": {"type": "string", "description": "the full program or shell script to run"},
                    "timeout_seconds": {"type": "integer", "description": "1-60, default 20"},
                    "files": {
                        "type": "object",
                        "description": "bash only: text files to put in /work before running, as {\"name.csv\": \"contents\"} (max 50 files, 1MB total)",
                        "additionalProperties": {"type": "string"},
                    },
                    "reset": {"type": "boolean", "description": "bash only: wipe the /work directory first and start from nothing"},
                },
                "required": ["language", "code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "play_game",
            "description": (
                "play a small game with matthias in a dm: tictactoe, connect4, "
                "hangman, wordle, mastermind. ONLY when HE asks to play - never "
                "start or suggest a game yourself. matthias + dms only (enforced). "
                "actions: list, rules, start (game, optional difficulty "
                "easy/normal/hard for tictactoe/connect4), move (his move: a "
                "square 1-9 for tictactoe, a column 1-7 for connect4, a letter or "
                "word for hangman, a 5-letter word for wordle, 4 letters like RGBY "
                "for mastermind), show (re-send the board), quit. the engine keeps "
                "the state and plays noodle's moves; the board is attached to your "
                "reply automatically, so never draw or retype it - just talk about "
                "what happened."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": games.ACTIONS},
                    "game": {"type": "string", "enum": list(games.GAMES), "description": "for start / rules"},
                    "move": {"type": "string", "description": "his move, for action=move"},
                    "difficulty": {"type": "string", "enum": ["easy", "normal", "hard"], "description": "for start, default normal"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "music_stats",
            "description": (
                "matthias's listening stats from last.fm (matthias only): his top "
                "artists, tracks or albums over a period. use for 'what have i been "
                "listening to this month', 'top artists this year' and the like."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["artists", "tracks", "albums"], "description": "default artists"},
                    "period": {"type": "string", "enum": ["7day", "1month", "3month", "6month", "12month", "overall"], "description": "default 7day"},
                    "limit": {"type": "integer", "description": "how many, default 10"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github",
            "description": (
                "look things up on github with matthias's account (matthias only, checked on "
                "the server - it can see his private repos). read-only. repo may be 'name' (his "
                "own) or 'owner/name'. actions:\n"
                "- repos: his repos, most recently pushed first\n"
                "- repo: one repo's summary; branches; releases (latest 5); actions (latest ci "
                "workflow runs and whether they passed)\n"
                "- readme: the repo's readme. tree: every file path in the repo (path = only "
                "under that folder). file: read one file (path), or list a folder. ref = a branch, "
                "tag or commit for readme/tree/file\n"
                "- commits: recent commits (optional path = only that file, ref = branch). commit: "
                "one commit's message and changed files (ref = the sha)\n"
                "- issues / pulls: open ones (state=closed|all for others). issue: one issue or pr "
                "by number, with its description, comments and (for a pr) changed files\n"
                "- stars: a repo's star count and newest stargazers; no repo = stars across all "
                "his repos. starred: repos he starred, newest first\n"
                "- search (issues/prs), search_repos, code_search (code in files, e.g. 'foo "
                "repo:owner/name'): github search syntax in q\n"
                "- user: a github profile (q = login, default his own)\n"
                "- notifications (his unread notifications), today (his commit count today)"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "repos", "repo", "branches", "releases", "actions", "readme", "tree", "file",
                            "commits", "commit", "issues", "pulls", "issue", "stars", "starred",
                            "search", "search_repos", "code_search", "user", "notifications", "today",
                        ],
                    },
                    "repo": {"type": "string", "description": "'name' (his own) or 'owner/name'"},
                    "path": {"type": "string", "description": "a file or folder path in the repo, for file/tree/commits"},
                    "ref": {"type": "string", "description": "branch, tag or commit sha (commit: the sha)"},
                    "number": {"type": "integer", "description": "issue or pr number, for issue"},
                    "q": {"type": "string", "description": "search query, or a login for user"},
                    "state": {"type": "string", "enum": ["open", "closed", "all"], "description": "for issues/pulls, default open"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hackatime",
            "description": (
                "matthias's coding time from hackatime (hack club's coding time tracker), his "
                "whole history back to the first day. public: anyone may ask. actions: summary "
                "(total, daily average, streak, top projects and languages; all time, or a "
                "start_date..end_date range, optionally one project), today (same for today), "
                "projects (every project ever with its all-time time; query = part of a name to "
                "search), project (one project: time, first/last day worked on, repo, languages; "
                "optional range), days (time per day for a range, per month if longer than ~3 "
                "months, max a year; default last 7 days; optional project) and sessions (when he "
                "coded on one day: start_date, default today) - those two show his daily "
                "rhythm, so they are matthias only, checked on the server. dates are "
                "YYYY-MM-DD, amsterdam days."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["summary", "today", "projects", "project", "days", "sessions"]},
                    "start_date": {"type": "string", "description": "YYYY-MM-DD, inclusive"},
                    "end_date": {"type": "string", "description": "YYYY-MM-DD, inclusive"},
                    "project": {"type": "string", "description": "exact hackatime project name"},
                    "query": {"type": "string", "description": "for projects: part of a project name"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "links",
            "description": (
                "manage matthias's short links on his own link shortener (matthias "
                "only, checked on the server). actions: create (needs url; key = the "
                "short path, random if omitted), list (optionally search = part of a "
                "key or destination), get, update (only the fields you send change; "
                "send clear_expiration / clear_password to remove those), delete "
                "(permanent, with its click history: only when he clearly asked), "
                "stats (one link's clicks, or account-wide totals + top links if no "
                "link is given), domains. a link is picked with id, or key (+ domain "
                "if he has several). always give him the full shortUrl."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "list", "get", "update", "delete", "stats", "domains"],
                    },
                    "id": {"type": "string", "description": "link id"},
                    "key": {"type": "string", "description": "short path, e.g. 'docs'"},
                    "domain": {"type": "string", "description": "only needed with more than one domain"},
                    "new_key": {"type": "string", "description": "for update: rename the short path"},
                    "url": {"type": "string", "description": "destination (http/https)"},
                    "search": {"type": "string", "description": "for list"},
                    "expiration_date": {"type": "string", "description": "ISO 8601 date-time"},
                    "expiration_url": {"type": "string", "description": "where to go after expiry"},
                    "password": {"type": "string"},
                    "clear_expiration": {"type": "boolean"},
                    "clear_password": {"type": "boolean"},
                    "is_cloaked": {"type": "boolean"},
                    "days": {"type": "integer", "description": "stats period, default 30"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_journal",
            "description": (
                "(matthias only) read matthias's own @matthias-day daily recaps for a date range, "
                "in order, with links to the originals. use for 'what did i do "
                "in march' / 'what was last week like' style questions where a "
                "date range matters (search_memory is for topic lookups)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "YYYY-MM-DD, inclusive"},
                    "end_date": {"type": "string", "description": "YYYY-MM-DD, inclusive (default: same as start)"},
                },
                "required": ["start_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "summarize_thread",
            "description": (
                "read a whole slack thread so you can give a tldr. pass a "
                "permalink to any message in it, or leave ref empty to use the "
                "thread you're currently in. returns the transcript; you write "
                "the summary."
            ),
            "parameters": {
                "type": "object",
                "properties": {"ref": {"type": "string", "description": "a slack permalink, or empty for the current thread"}},
            },
        },
    },
]


def available_tools() -> list[dict]:
    """TOOLS minus the ones whose credentials aren't set (bot/features.py), so the model is
    never offered, and never claims it can use, something that isn't there."""
    return [t for t in TOOLS if features.tool_enabled(t["function"]["name"])]


def _resolve_from_handle(query: str):
    # slack search 'from:' often matches on the @handle rather than the U-id,
    # so rewrite from:Uxxxx -> from:@handle when we can resolve it
    m = re.search(r"from:(U[A-Z0-9]+)", query)
    if not m:
        return None
    uid = m.group(1)
    try:
        info = state.app.client.users_info(user=uid)
        if not info.get("ok"):
            return None
        user = info.get("user", {}) or {}
        handle = user.get("name") or (user.get("profile") or {}).get("display_name")
        if not handle:
            return None
        return re.sub(r"from:" + re.escape(uid), f"from:@{handle}", query)
    except Exception:  # noqa: BLE001
        logger.exception("users_info failed for %s", uid)
        return None


def _search_slack_messages(query: str, user_prompt: str = "") -> str:
    first2 = " ".join(user_prompt.split()[:2])
    logger.debug("search query=%r (user: %r) searching_as=%s", query, first2, state.AUTH_USER_ID)
    try:
        resp = state.app.client.search_messages(query=query, count=5)
        if not resp.get("ok"):
            logger.warning("search FAILED error=%s", resp.get("error"))
            log_tool("search_slack_messages", query, "failed")
            return f"slack search error: {resp.get('error')}"
        matches = (resp.get("messages") or {}).get("matches", [])
        logger.info("search %r -> %d matches", query, len(matches))
        if not matches:
            handle_q = _resolve_from_handle(query)
            if handle_q:
                logger.debug("search retry (from-handle) %r", handle_q)
                try:
                    resp_h = state.app.client.search_messages(query=handle_q, count=5)
                    if resp_h.get("ok"):
                        matches = (resp_h.get("messages") or {}).get("matches", [])
                except Exception:  # noqa: BLE001
                    logger.exception("from-handle search failed")
        if not matches:
            broader = re.sub(r"from:\S+\s*", "", query).strip().strip('"').strip()
            if broader and broader != query:
                logger.debug("search retry (broader) %r", broader)
                try:
                    resp2 = state.app.client.search_messages(query=broader, count=5)
                    if resp2.get("ok"):
                        matches = (resp2.get("messages") or {}).get("matches", [])
                except Exception:  # noqa: BLE001
                    logger.exception("broader search failed")
        if not matches:
            log_tool("search_slack_messages", query, "no matches")
            return "no slack messages found for that query"
        logger.debug("top match: %r", (matches[0].get("text") or "")[:120])
        lines = []
        for m in matches:
            body = (m.get("text") or "").replace("\n", " ")
            user = m.get("user", "unknown")
            channel = m.get("channel", {}) or {}
            cid = channel.get("id") if isinstance(channel, dict) else channel
            cname = channel.get("name") if isinstance(channel, dict) else None
            chan_link = (
                f"<#{cid}|{cname}>" if cname else (f"<#{cid}>" if cid else "dm")
            )
            ts = m.get("ts", "")
            lines.append(f"- in {chan_link} from {user} at {ts}: {body}")
        log_tool("search_slack_messages", query, f"{len(matches)} matches")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        logger.exception("slack search failed (user: %r)", first2)
        log_tool("search_slack_messages", query, "failed")
        return f"slack search failed: {exc}"


def _fetch_slack_message(ref: str, user_id: str | None = None) -> str:
    cid, ts = _parse_slack_ref(ref)
    if not cid:
        log_tool("fetch_slack_message", ref, "bad ref")
        return "could not parse a slack channel/timestamp from that reference"
    if not ts:
        log_tool("fetch_slack_message", ref, "no ts")
        return (
            f"that looks like channel <#{cid}> but no specific message was given; "
            f"try a search with in:<#{cid}>"
        )
    logger.info("fetch %s %s", cid, ts)
    try:
        join_ok = _is_matthias(user_id)  # only his requests may make noodle join a channel
        resp = with_join(cid, join_ok, lambda: state.app.client.conversations_history(
            channel=cid, latest=ts, inclusive=True, limit=1
        ))
        msgs = (resp.get("messages") or []) if resp.get("ok") else []
        if not msgs:
            resp2 = with_join(cid, join_ok, lambda: state.app.client.conversations_replies(channel=cid, ts=ts, limit=1))
            msgs = (resp2.get("messages") or []) if resp2.get("ok") else []
        if not msgs:
            log_tool("fetch_slack_message", ref, "not found")
            return f"no message found at {_channel_link(cid)} {ts}"
        msg = msgs[0]
        text = msg.get("text", "")
        user = msg.get("user", "unknown")
        logger.debug("fetched from %s at %s: %r", user, ts, text[:160])
        log_tool("fetch_slack_message", ref, "success")
        return f"message in {_channel_link(cid)} from {user} at {ts}: {text}"
    except Exception as exc:  # noqa: BLE001
        logger.exception("fetch_slack_message failed")
        log_tool("fetch_slack_message", ref, "failed")
        return f"fetch failed: {exc}"


def _search_memory(query: str, user_id: str | None) -> str:
    # memory holds matthias's dms with noodle, his threads and his journal -
    # anyone else (e.g. in a public channel) must never be able to read it,
    # so this is checked here on the real sender id, not left to the model.
    if not _is_matthias(user_id):
        log_tool("search_memory", "", "unauthorized")
        return "only matthias can search noodle's memory - it holds his private dms."
    query = (query or "").strip()
    if not query:
        return "no search query given"
    vector = embed_text(query)
    if vector is None:
        log_tool("search_memory", query, "embed failed")
        return "memory search failed (couldn't embed the query, check logs)"
    results = search_similar(vector, limit=5)
    if not results:
        log_tool("search_memory", query, "no matches")
        return "no matching memories found"
    name_cache: dict = {}
    lines = []
    for r in results:
        if r["role"] == "assistant":
            who = "noodle"
        elif r["user_id"]:
            uid = r["user_id"]
            if uid not in name_cache:
                name_cache[uid] = lookup_display_name(uid)
            name = name_cache[uid]
            # keep the real <@USERID> mention too, so noodle can still tag
            # them if it wants to - the name is just so it knows who that is
            who = f"{name} (<@{uid}>)" if name else f"<@{uid}>"
        else:
            who = "someone"
        link = f" ({r['permalink']})" if r.get("permalink") else ""
        lines.append(f"- [{r['kind']} @ {r['created_at']}] {who}: {r['content']}{link}")
    log_tool("search_memory", query, f"{len(results)} matches")
    return "\n".join(lines)


def _web_search(query: str) -> str:
    query = (query or "").strip()
    if not query:
        return "no search query given"
    answer = web_answer(query)
    if answer is None:
        log_tool("web_search", query, "failed")
        return "web search failed, check logs"
    log_tool("web_search", query, "success")
    return answer


_CURRENT_LOCATION = "current location"


def _get_weather(location: str | None, days, origin: dict | None) -> str:
    """matthias's home/usual places are private: outside a dm they are never
    named (to the model or anyone else) - the forecast is labelled "current
    location" instead. a place someone else names is just looked up normally."""
    asked = (location or "").strip() or config.WEATHER_DEFAULT_LOCATION
    if not asked:
        return "no default location is configured, so i need a place name."
    private = {p.lower() for p in [*config.WEATHER_LOCATIONS, config.WEATHER_DEFAULT_LOCATION] if p}
    in_dm = bool(origin and origin.get("is_dm"))
    hide = not in_dm and asked.lower() in private
    log_tool("get_weather", _CURRENT_LOCATION if hide else asked, "success")
    return forecast_text(asked, days or 1, label=_CURRENT_LOCATION if hide else None)


def _now_playing(user_id: str | None) -> str:
    if not _is_matthias(user_id):
        log_tool("now_playing", "", "unauthorized")
        return "only matthias can see what he's listening to."
    result = lastfm.now_playing()
    log_tool("now_playing", "", "success" if not result.startswith("last.fm") else "failed")
    return result


def _music_stats(args: dict, user_id: str | None) -> str:
    if not _is_matthias(user_id):
        log_tool("music_stats", "", "unauthorized")
        return "only matthias can see his listening stats."
    if not lastfm.configured():
        return "last.fm isn't configured (LASTFM_API_KEY / LASTFM_USER missing)"
    kind = args.get("kind") or "artists"
    period = args.get("period") or "7day"
    if kind not in ("artists", "tracks", "albums") or period not in lastfm.PERIODS:
        return f"kind must be artists/tracks/albums and period one of {', '.join(lastfm.PERIODS)}"
    lines = lastfm.top(kind, period, int(args.get("limit") or 10))
    log_tool("music_stats", f"{kind} {period}", "failed" if lines is None else "success")
    if lines is None:
        return "last.fm lookup failed, check logs"
    return f"top {kind} ({period}):\n" + "\n".join(f"{i}. {l}" for i, l in enumerate(lines, 1))


def _github(args: dict, user_id: str | None) -> str:
    if not _is_matthias(user_id):
        return "only matthias can use the github tool"
    action = args.get("action", "")
    result = github_api.query(
        action, repo=args.get("repo"), q=args.get("q"), state=args.get("state") or "open",
        path=args.get("path"), ref=args.get("ref"), number=args.get("number"),
    )
    log_tool("github", f"{action} {args.get('repo') or args.get('q') or ''} {args.get('path') or ''}".strip(), "done")
    return result


def _fmt_link(link: dict) -> str:
    bits = [f"{link.get('shortUrl')} -> {link.get('url')}", f"id {link.get('id')}", f"{link.get('clicks', 0)} clicks"]
    if link.get("expirationDate"):
        bits.append(f"expires {link['expirationDate']}")
    if link.get("hasPassword"):
        bits.append("password protected")
    if link.get("isCloaked"):
        bits.append("cloaked")
    if link.get("archived"):
        bits.append("archived")
    return ", ".join(bits)


def _resolve_link_id(args: dict):
    """(id, None) or (None, error text) for the link the model means."""
    if args.get("id"):
        return str(args["id"]), None
    key = (args.get("key") or "").strip().strip("/")
    if not key:
        return None, "need a link id or key"
    found = links_api.list_links(search=key, domain=args.get("domain"), limit=200)
    if found is None or "error" in found:
        return None, "couldn't reach the link shortener, check logs."
    matches = [l for l in found.get("data", []) if l.get("key") == key]
    if not matches:
        return None, f"no link with key '{key}'"
    if len(matches) > 1:
        return None, "several domains have that key, say which domain: " + ", ".join(m["domain"] for m in matches)
    return matches[0]["id"], None


def _links(args: dict, user_id: str | None) -> str:
    if not _is_matthias(user_id):
        return "only matthias can use the links tool"
    action = args.get("action", "")
    detail = args.get("key") or args.get("id") or args.get("url") or args.get("search") or ""
    result = _links_action(action, args)
    log_tool("links", f"{action} {detail}".strip(), "failed" if result.startswith("couldn't") else "done")
    return result


def _links_action(action: str, args: dict) -> str:
    def failed(res) -> str | None:
        if res is None:
            return "couldn't reach the link shortener, check logs."
        if "error" in res:
            err = res["error"]
            return f"couldn't do that: {err.get('message') or err.get('code')}"
        return None

    if action == "domains":
        res = links_api.list_domains()
        return failed(res) or "domains: " + ", ".join(d["domain"] for d in res.get("data", []))
    if action == "list":
        res = links_api.list_links(search=args.get("search"), domain=args.get("domain"))
        if err := failed(res):
            return err
        rows = res.get("data", [])
        if not rows:
            return "no links found"
        return f"{len(rows)} of {res.get('total')} links:\n" + "\n".join(_fmt_link(l) for l in rows)
    if action == "stats":
        days = int(args.get("days") or 30)
        if args.get("id") or args.get("key"):
            link_id, err = _resolve_link_id(args)
            if err:
                return err
            res = links_api.link_stats(link_id, days)
            if err := failed(res):
                return err
            return (
                f"{_fmt_link(res['link'])}\n{res['clicksInPeriod']} clicks in the last {days} days "
                f"({res['totalClicks']} total). daily: "
                + ", ".join(f"{d['date']}: {d['clicks']}" for d in res.get("daily", []) if d["clicks"])
            )
        res = links_api.account_stats(days, 10)
        if err := failed(res):
            return err
        return (
            f"{res['totalLinks']} links, {res['totalClicks']} clicks total, {res['clicksInPeriod']} in the "
            f"last {days} days. top links:\n" + "\n".join(_fmt_link(l) for l in res.get("topLinks", []))
        )
    if action == "create":
        if not args.get("url"):
            return "need a destination url to create a link"
        res = links_api.create_link(_link_payload(args))
        return failed(res) or "created: " + _fmt_link(res)
    if action in ("get", "update", "delete"):
        link_id, err = _resolve_link_id(args)
        if err:
            return err
        if action == "get":
            res = links_api.get_link(link_id)
            return failed(res) or _fmt_link(res)
        if action == "update":
            payload = _link_payload(args)
            # key/domain pick the link here, so they only change it via new_key
            payload.pop("key", None)
            payload.pop("domain", None)
            if args.get("new_key"):
                payload["key"] = args["new_key"]
            if not payload:
                return "nothing to change, say which fields to update"
            res = links_api.update_link(link_id, payload)
            return failed(res) or "updated: " + _fmt_link(res)
        res = links_api.delete_link(link_id)
        return failed(res) or f"deleted link {link_id}"
    return f"unknown links action: {action}"


def _link_payload(args: dict) -> dict:
    payload = {}
    for arg, field in (
        ("url", "url"),
        ("domain", "domain"),
        ("key", "key"),
        ("expiration_date", "expirationDate"),
        ("expiration_url", "expirationUrl"),
        ("password", "password"),
        ("is_cloaked", "isCloaked"),
    ):
        if args.get(arg) is not None:
            payload[field] = args[arg]
    if args.get("clear_expiration"):
        payload["expirationDate"] = None
        payload["expirationUrl"] = None
    if args.get("clear_password"):
        payload["password"] = None
    return payload


def _get_journal(start_date, end_date, user_id: str | None) -> str:
    if not _is_matthias(user_id):
        log_tool("get_journal", "", "unauthorized")
        return "only matthias can read his journal."
    try:
        start = datetime.date.fromisoformat((start_date or "").strip())
        end = datetime.date.fromisoformat((end_date or start_date).strip())
    except ValueError:
        return "dates must be YYYY-MM-DD"
    entries = load_kind_between("matthias_day", start.isoformat(), end.isoformat())
    log_tool("get_journal", f"{start} to {end}", f"{len(entries)} entries")
    if not entries:
        return f"no journal entries between {start} and {end}"
    out = []
    for e in entries[:31]:
        link = f" ({e['permalink']})" if e.get("permalink") else ""
        out.append(f"[{e['created_at'][:10]}]{link}\n{e['content']}")
    return "\n\n".join(out)


_THREAD_MAX_CHARS = 14000


def _summarize_thread(ref: str, origin: dict | None, user_id: str | None = None) -> str:
    join_ok = _is_matthias(user_id)
    ref = (ref or "").strip()
    if ref:
        cid, ts = _parse_slack_ref(ref)
    elif origin and origin.get("thread_ts"):
        cid, ts = origin["channel"], origin["thread_ts"]
    else:
        return "no thread given - ask for a link to a message in the thread"
    if not cid or not ts:
        log_tool("summarize_thread", ref, "bad ref")
        return "could not parse a slack channel/timestamp from that reference"
    try:
        resp = with_join(
            cid, join_ok,
            lambda: state.app.client.conversations_replies(channel=cid, ts=ts, limit=200),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("summarize_thread failed for %s %s", cid, ts)
        log_tool("summarize_thread", ref or ts, "failed")
        return f"couldn't read that thread: {exc}"
    msgs = resp.get("messages") or []
    if not msgs:
        log_tool("summarize_thread", ref or ts, "empty")
        return "that thread has no messages (or i can't see it)"
    lines = [f"<@{m.get('user', 'unknown')}>: {(m.get('text') or '').strip()}" for m in msgs]
    text = "\n".join(lines)
    if len(text) > _THREAD_MAX_CHARS:
        text = text[:_THREAD_MAX_CHARS] + "\n[...thread truncated]"
    log_tool("summarize_thread", ref or ts, f"{len(msgs)} messages")
    return f"thread in {_channel_link(cid)} ({len(msgs)} messages):\n{text}"


_CODE_OUTPUT_CAP = 6000


def _code_hints(result: dict, language: str) -> list[str]:
    """short nudges appended to a failed run, so the model explains the real
    limit instead of guessing - the same limits the tool description lists."""
    text = f"{result.get('error') or ''}\n{result.get('stderr') or ''}"
    hints = []
    if "ModuleNotFoundError" in text or "No module named" in text:
        hints.append(
            "hint: that module isn't in the sandbox. it only has the standard library plus numpy, "
            "pandas, matplotlib, scipy and sympy, and nothing can be installed. rewrite it without "
            "that module, or tell matthias plainly it can't be done here."
        )
    if re.search(r"urlopen|ConnectionError|socket|fetch|NetworkError|Name or service|URLError", text, re.I):
        hints.append("hint: the sandbox has no internet at all, so anything that needs the network can't work. ask him to paste the data instead.")
    if re.search(r"(require|import|fetch|setTimeout|setInterval|process|window|document) is not defined|ReferenceError", text) and language == "javascript":
        hints.append("hint: javascript here is bare ES2020 - no require/import/fetch/timers/dom/node apis.")
    if language == "bash" and re.search(r"command not found|not available", text):
        hints.append(
            "hint: that command doesn't exist in the simulated shell, which only has built-in commands "
            "(grep sed awk jq yq xan sort uniq cut tr find diff tar gzip base64 and similar). no python/node/curl/git/"
            "sqlite3 or other binaries. use the python or javascript option for that, or say it can't be done."
        )
    if re.search(r"FileNotFoundError|No such file", text):
        hints.append("hint: the sandbox can't see any files of his or noodle's - the data has to be inside the code. only /out/ is for writing results.")
    if result.get("timed_out"):
        hints.append("hint: it hit the time limit. try smaller sizes or a smarter method, or say it's too heavy for the sandbox.")
    if result.get("out_of_memory"):
        hints.append("hint: it ran out of memory (about 900MB). use smaller data, or say it's too big for the sandbox.")
    return hints


def _run_code(args: dict, user_id: str | None, origin: dict | None) -> str:
    """run a python/javascript snippet or bash script in the sandbox (matthias, dms only).
    text goes back to the model as tool context; files (plots, csvs) are
    uploaded straight into the dm."""
    if not _is_matthias(user_id):
        log_tool("run_code", "", "unauthorized")
        return "only matthias can run code."
    if not (origin and origin.get("is_dm")):
        log_tool("run_code", "", "not a dm")
        return "code only runs in dms with matthias, not here."
    if not sandbox.configured():
        return "the code sandbox is switched off."
    language = (args.get("language") or "").lower()
    code = args.get("code") or ""
    if language not in ("python", "javascript", "bash"):
        return "language must be python, javascript or bash."
    if not code.strip():
        return "no code given."
    try:
        timeout_s = max(1, min(int(args.get("timeout_seconds") or 20), 60))
    except (TypeError, ValueError):
        timeout_s = 20

    files = args.get("files")
    if files is not None and not (isinstance(files, dict) and all(isinstance(v, str) for v in files.values())):
        return "files must be an object of {name: text}."
    result = sandbox.run(language, code, timeout_s, files=files if language == "bash" else None, reset=bool(args.get("reset")))
    if isinstance(result, str):
        log_tool("run_code", f"{language} {len(code)} chars", "failed")
        return result

    sent, failed = [], []
    for f in result.get("files") or []:
        try:
            state.app.client.files_upload_v2(
                channel=origin["channel"],
                file=base64.b64decode(f["data"]),
                filename=f["name"],
                title=f["name"],
            )
            sent.append(f["name"])
        except Exception:  # noqa: BLE001
            logger.exception("uploading sandbox file %s failed", f.get("name"))
            failed.append(f["name"])

    parts = []
    for label, key in (("stdout", "stdout"), ("stderr", "stderr"), ("result (value of the last expression)", "result"), ("error", "error")):
        if result.get(key):
            parts.append(f"{label}:\n{result[key]}")
    if result.get("truncated"):
        parts.append("(output was cut off, it was too long)")
    parts.extend(_code_hints(result, language))
    if sent:
        parts.append(f"sent to the dm as files: {', '.join(sent)} - he can see them, don't describe them as attached to your text.")
    if failed:
        parts.append(f"couldn't upload: {', '.join(failed)}")
    parts.append(f"ran in {result.get('duration_ms', '?')} ms")
    log_tool("run_code", f"{language} {len(code)} chars", "ok" if result.get("ok") else "error")
    return "\n\n".join(parts)[:_CODE_OUTPUT_CAP]


def _is_matthias(user_id: str | None) -> bool:
    return user_id == config.USER_ID


def _resolve_destination(destination, origin: dict | None):
    """returns (channel, thread_ts) or an error string.

    default is wherever the request was made: dms stay dms, a channel
    (and the thread, if it was in one) stays there. an explicit override
    is only accepted for 'dm' or a channel noodle is allowed to talk in.
    """
    destination = (destination or "").strip()
    if not destination or destination.lower() in ("here", "same", "default"):
        if not origin or origin.get("is_dm") or not origin.get("channel"):
            return config.USER_ID, None
        return origin["channel"], origin.get("thread_ts")
    if destination.lower() in ("dm", "dms"):
        return config.USER_ID, None
    m = re.search(r"\b([CG][A-Z0-9]{6,})\b", destination.upper())
    if not m:
        return "couldn't tell where to send that - destination should be 'dm' or a channel id."
    channel = m.group(1)
    if not (config.ALLOW_CHANNEL_WILDCARD or channel in config.ALLOWED_CHANNELS):
        return f"i'm not allowed to post in <#{channel}>, so can't schedule things there."
    return channel, None


def _parse_when(args: dict, repeat: str):
    """returns (first occurrence as an amsterdam datetime, now) or an error string."""
    now = datetime.datetime.now(AMSTERDAM_TZ)
    if args.get("in_minutes") not in (None, ""):
        try:
            minutes = int(args["in_minutes"])
        except (TypeError, ValueError):
            return "in_minutes has to be a whole number."
        if minutes < 1:
            return "in_minutes has to be at least 1."
        return (now + datetime.timedelta(minutes=minutes)).replace(second=0, microsecond=0), now
    if args.get("at"):
        first = reminders.parse_local(args["at"])
        if first is None:
            return "couldn't read that time, use 'YYYY-MM-DD HH:MM' (amsterdam time)."
        if first <= now and repeat == "none":
            return f"that time is already in the past (it's {now.strftime('%Y-%m-%d %H:%M')} in amsterdam right now)."
        return first, now
    return "need either in_minutes or at to know when."


_REMINDER_ASK = re.compile(
    r"remind|reminder|ping me|nudge|notify|alert me|tell me (in|at|every|when)|wake me"
    r"|schedule|every (day|week|month|\d+)|in \d+ ?(min|m\b|h\b|hour|day)",
    re.I,
)


def _schedule_reminder(args: dict, user_id: str | None, origin: dict | None,
                       user_prompt: str = "") -> str:
    # same server-side guard as start_freedom_session: the real slack
    # sender id decides, not the model's (or a channel message's) say-so.
    if not _is_matthias(user_id):
        log_tool("schedule_reminder", "", "unauthorized")
        return "only matthias can schedule things - not doing this for anyone else."
    # the model once scheduled a "test reminder" on its own while he was just sending
    # tic-tac-toe moves: his message has to actually ask for one.
    if not _REMINDER_ASK.search(user_prompt or ""):
        log_tool("schedule_reminder", "", "refused: not asked for")
        return "he didn't ask for a reminder in this message, so nothing was scheduled. don't mention this."
    text = (args.get("text") or "").strip()
    if not text:
        return "need the text of what to remind about."

    repeat = args.get("repeat") or "none"
    if repeat not in reminders.REPEATS:
        return f"unknown repeat {repeat!r}, use one of: {', '.join(reminders.REPEATS)}."
    interval = None
    if repeat == "every_n_minutes":
        try:
            interval = int(args.get("interval_minutes"))
        except (TypeError, ValueError):
            return "need interval_minutes for an every_n_minutes repeat."
        if interval < reminders.MIN_INTERVAL_MINUTES:
            return f"the shortest repeat gap is {reminders.MIN_INTERVAL_MINUTES} minutes."

    when = _parse_when(args, repeat)
    if isinstance(when, str):
        return when
    first, _now = when

    dest = _resolve_destination(args.get("destination"), origin)
    if isinstance(dest, str):
        return dest
    channel, thread_ts = dest

    created = reminders.add_reminder(
        text, channel, thread_ts, repeat, interval, first, user_id
    )
    if created is None:
        log_tool("schedule_reminder", text, "failed")
        return (
            f"couldn't schedule that (already at the limit of {reminders.MAX_ACTIVE_REMINDERS} "
            "active reminders, or that schedule never fires again)."
        )
    row = {
        "id": created["id"], "text": text, "channel": channel, "thread_ts": thread_ts,
        "repeat": repeat, "interval_minutes": interval,
        "next_run_ts": created["next_run"].timestamp(),
    }
    log_tool("schedule_reminder", f"#{created['id']}", "success")
    return "scheduled and saved: " + reminders.describe(row)


_FREEDOM_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _my_freedom_device_ids():
    """ids of matthias's OWN freedom devices, or an error string.

    the freedom account is SHARED with matthias's dad - resolve ONLY the
    devices named in config.FREEDOM_DEVICE_NAMES, every time, rather than
    ever targeting "all devices on the account". refuses if none match.
    """
    devices = freedom_list_devices()
    if devices is None:
        return "couldn't reach freedom to look up devices, check logs."
    wanted = {n.lower() for n in config.FREEDOM_DEVICE_NAMES}
    device_ids = [d["id"] for d in devices if (d.get("name") or "").strip().lower() in wanted]
    if not device_ids:
        return (
            "couldn't find matthias's own devices in freedom by name "
            f"({', '.join(config.FREEDOM_DEVICE_NAMES)}) - not doing anything, "
            "since the account is shared and guessing could block "
            "someone else's device."
        )
    return device_ids


def _start_freedom_session(duration_minutes, user_id: str | None) -> str:
    # authorization is enforced HERE, not left to the model's judgment -
    # a public channel request could claim to be "on matthias's behalf",
    # but only the actual message sender's real slack id counts.
    if user_id != config.USER_ID:
        log_tool("start_freedom_session", f"{duration_minutes}m", "unauthorized")
        return "only matthias can start a freedom session - not doing this for anyone else."
    if not config.FREEDOM_API_KEY:
        log_tool("start_freedom_session", f"{duration_minutes}m", "not configured")
        return "freedom isn't configured on noodle's end (no FREEDOM_API_KEY set), can't start a session."
    try:
        minutes = int(duration_minutes)
    except (TypeError, ValueError):
        return "need a valid number of minutes to start a freedom session."
    if not 1 <= minutes <= 1440:
        return "freedom sessions have to be between 1 and 1440 minutes (24 hours)."

    device_ids = _my_freedom_device_ids()
    if isinstance(device_ids, str):
        log_tool("start_freedom_session", f"{minutes}m", "devices")
        return device_ids

    result = freedom_start_session(minutes, device_ids)
    if result is None:
        log_tool("start_freedom_session", f"{minutes}m", "failed")
        return "couldn't reach freedom to start the session, check logs."
    error = result.get("error")
    if error:
        log_tool("start_freedom_session", f"{minutes}m", f"error:{error.get('code')}")
        return _freedom_error_message(error)
    session = (result.get("session") or {})
    log_tool("start_freedom_session", f"{minutes}m", "success")
    return f"started a {minutes}-minute freedom session, blocking everything on matthias's devices (session id {session.get('id')})."


def _schedule_freedom_session(args: dict, user_id: str | None) -> str:
    """hand a later/recurring session to FREEDOM's own scheduler (one-off:
    POST /api/sessions with start_time; recurring: POST /api/schedules) -
    freedom starts it itself, noodle never has to be online or waiting."""
    if not _is_matthias(user_id):
        log_tool("schedule_freedom_session", "", "unauthorized")
        return "only matthias can schedule a freedom session - not doing this for anyone else."
    if not config.FREEDOM_API_KEY:
        return "freedom isn't configured on noodle's end (no FREEDOM_API_KEY set), can't schedule a session."
    try:
        minutes = int(args.get("duration_minutes"))
    except (TypeError, ValueError):
        return "need a valid number of minutes for the freedom session."
    if not 1 <= minutes <= 1440:
        return "freedom sessions have to be between 1 and 1440 minutes (24 hours)."
    repeat = args.get("repeat") or "none"
    if repeat not in ("none", "daily", "weekdays", "weekly"):
        return "freedom can only repeat sessions daily, weekdays or weekly."

    when = _parse_when(args, repeat)
    if isinstance(when, str):
        return when
    first, now = when

    if repeat == "none":
        delta = first - now
        if delta < datetime.timedelta(minutes=2):
            return "that's basically now - ask me to start it right now instead."
        if delta >= datetime.timedelta(hours=24):
            return (
                "freedom can only schedule a one-off session for the next 24 hours "
                "(later today or tomorrow), so i can't set that one up yet - ask me "
                "again closer to the time, or make it a repeating one."
            )

    device_ids = _my_freedom_device_ids()
    if isinstance(device_ids, str):
        log_tool("schedule_freedom_session", f"{minutes}m", "devices")
        return device_ids

    # freedom takes HH:MM in ITS account timezone, which may not be amsterdam
    tz_name = (freedom_list_sessions() or {}).get("timezone")
    try:
        tz = ZoneInfo(tz_name) if tz_name else AMSTERDAM_TZ
    except Exception:  # noqa: BLE001
        tz = AMSTERDAM_TZ
    local = first.astimezone(tz)
    start_time = local.strftime("%H:%M")

    if repeat == "none":
        result = freedom_start_session(minutes, device_ids, start_time=start_time)
        key = "session"
    else:
        days = {
            "daily": _FREEDOM_DAYS,
            "weekdays": _FREEDOM_DAYS[:5],
            "weekly": [_FREEDOM_DAYS[local.weekday()]],
        }[repeat]
        result = freedom_create_schedule(
            f"noodle {minutes}m block", start_time, days, minutes, device_ids
        )
        key = "schedule"
    if result is None:
        log_tool("schedule_freedom_session", f"{minutes}m", "failed")
        return "couldn't reach freedom to schedule the session, check logs."
    error = result.get("error")
    if error:
        log_tool("schedule_freedom_session", f"{minutes}m", f"error:{error.get('code')}")
        return _freedom_error_message(error)
    item = result.get(key) or {}
    log_tool("schedule_freedom_session", f"{minutes}m", "success")
    when_text = first.astimezone(AMSTERDAM_TZ).strftime("%a %H:%M")
    rep = "one-off" if repeat == "none" else f"repeating {repeat}"
    return (
        f"scheduled a {minutes}-minute freedom session ({rep}) for {when_text} "
        f"amsterdam time, blocking everything on matthias's devices (freedom:{item.get('id')}). "
        "freedom itself will start it, noodle doesn't need to be online then."
    )


def _describe_freedom(s: dict, tz: str) -> str:
    mins = s.get("duration_minutes") or (s.get("duration_seconds") or 0) // 60
    if s.get("recurring"):
        rep = "repeats " + ",".join(s.get("days") or [])
    else:
        rep = "one-off"
    parts = [f"freedom:{s['id']} {mins}-minute freedom session", f"starts {s.get('start_time')} ({tz or 'freedom tz'})", rep]
    if s.get("active"):
        parts.append("running right now")
    elif s.get("seconds_until_start") is not None:
        parts.append(f"in ~{int(s['seconds_until_start']) // 60} min")
    return " | ".join(parts)


def _list_scheduled(user_id: str | None) -> str:
    if not _is_matthias(user_id):
        log_tool("list_scheduled", "", "unauthorized")
        return "only matthias can see what's scheduled."
    lines = [reminders.describe(r) for r in reminders.list_active()]
    if config.FREEDOM_API_KEY:
        device_ids = _my_freedom_device_ids()
        if isinstance(device_ids, str):
            lines.append(f"(couldn't list freedom sessions: {device_ids})")
        else:
            sessions, tz = my_schedules(device_ids)
            lines += [_describe_freedom(s, tz) for s in sessions.values()]
    log_tool("list_scheduled", "", f"{len(lines)} items")
    return "\n".join(lines) if lines else "nothing is scheduled right now."


def _cancel_scheduled(raw_id, user_id: str | None) -> str:
    if not _is_matthias(user_id):
        log_tool("cancel_scheduled", str(raw_id), "unauthorized")
        return "only matthias can cancel scheduled things."
    raw = str(raw_id or "").strip().lower().lstrip("#")
    if raw.startswith("freedom:"):
        try:
            fid = int(raw.split(":", 1)[1])
        except ValueError:
            return "that freedom id doesn't look right, expected freedom:<number>."
        if not config.FREEDOM_API_KEY:
            return "freedom isn't configured on noodle's end (no FREEDOM_API_KEY set)."
        device_ids = _my_freedom_device_ids()
        if isinstance(device_ids, str):
            return device_ids
        # only ever touch sessions that are matthias's (shared account)
        sessions, _tz = my_schedules(device_ids)
        target = sessions.get(fid)
        if target is None:
            log_tool("cancel_scheduled", raw, "not found")
            return f"no freedom session {raw} among matthias's scheduled ones."
        if target.get("active"):
            log_tool("cancel_scheduled", raw, "running")
            return "that freedom session is running right now, i can't cancel one that's already started."
        result = freedom_delete_schedule(fid)
        if result is None:
            return "couldn't reach freedom to cancel that, check logs."
        if result.get("error"):
            log_tool("cancel_scheduled", raw, f"error:{result['error'].get('code')}")
            return _freedom_error_message(result["error"])
        log_tool("cancel_scheduled", raw, "success")
        return f"cancelled freedom session {raw}."
    try:
        rid = int(raw)
    except ValueError:
        return "need the id of what to cancel, like 3 or freedom:123."
    if reminders.delete_reminder(rid):
        log_tool("cancel_scheduled", f"#{rid}", "success")
        return f"cancelled and removed reminder #{rid}."
    log_tool("cancel_scheduled", f"#{rid}", "not found")
    return f"no reminder with id #{rid}."


def run_tool(name: str, args: dict, user_prompt: str = "", user_id: str | None = None,
             origin: dict | None = None) -> str:
    if name == "search_slack_messages":
        return _search_slack_messages(args.get("query", ""), user_prompt=user_prompt)
    if name == "fetch_slack_message":
        return _fetch_slack_message(args.get("ref", ""), user_id)
    if name == "flaron":
        return flaron_query(
            args.get("action", ""),
            id=args.get("id"),
            name=args.get("name"),
            query=args.get("query"),
        )
    if name == "search_memory":
        return _search_memory(args.get("query", ""), user_id)
    if name == "web_search":
        return _web_search(args.get("query", ""))
    if name == "start_freedom_session":
        return _start_freedom_session(args.get("duration_minutes"), user_id=user_id)
    if name == "freedom":
        return freedom_tool.run(args, user_id)
    if name == "schedule_reminder":
        return _schedule_reminder(args, user_id=user_id, origin=origin, user_prompt=user_prompt)
    if name == "schedule_freedom_session":
        return _schedule_freedom_session(args, user_id=user_id)
    if name == "list_scheduled":
        return _list_scheduled(user_id)
    if name == "cancel_scheduled":
        return _cancel_scheduled(args.get("id"), user_id)
    if name == "get_weather":
        return _get_weather(args.get("location"), args.get("days"), origin)
    if name == "roll_dice":
        result = dice.roll(args.get("notation", ""))
        log_tool("roll_dice", args.get("notation", ""), "success")
        return result
    if name == "public_holidays":
        country = args.get("country") or "NL"
        log_tool("public_holidays", f"{country} {args.get('year') or 'next 12 months'}", "run")
        return holidays_text(country, args.get("year"))
    if name == "now_playing":
        return _now_playing(user_id)
    if name == "run_code":
        return _run_code(args, user_id, origin)
    if name == "play_game":
        log_tool("play_game", args.get("action", ""), "run")
        return games.play(args, user_id, origin)
    if name == "music_stats":
        return _music_stats(args, user_id)
    if name == "github":
        return _github(args, user_id)
    if name == "links":
        return _links(args, user_id)
    if name == "hackatime":
        log_tool("hackatime", f"{args.get('action', '')} {args.get('project') or args.get('query') or ''}".strip(), "run")
        return hackatime.query(args, is_owner=_is_matthias(user_id))
    if name == "get_journal":
        return _get_journal(args.get("start_date"), args.get("end_date"), user_id)
    if name == "summarize_thread":
        return _summarize_thread(args.get("ref", ""), origin, user_id)
    return f"unknown tool: {name}"
