# noodle

a tiny, cute slack agent that lives in your workspace and chats through a
slack user account (not a bot app) using the slack bolt framework in socket
mode. it is powered by an openai compatible api (openrouter or any proxy) and
speaks in a warm, casual, lowercase style, like a good friend texting.

[![python](https://img.shields.io/badge/python-3.10%2B-3776ab?logo=python&logoColor=white)](https://www.python.org)
[![slack](https://img.shields.io/badge/slack-bolt%20%2B%20socket%20mode-4a154b?logo=slack&logoColor=white)](https://slack.dev/bolt-python/)
[![license](https://img.shields.io/badge/license-mit-green)](#license)
[![made with love](https://img.shields.io/badge/made%20with-love-ff69b4)](https://github.com/MatthiasLubbertsen/noodle)
[![persona](https://img.shields.io/badge/persona-cute%20friend-9b59b6)](#)

## what noodle can do

- chat in DMs (only with the one allowed user) and in allowed channels when you
  mention `noodle` or `@noodle`.
- keep talking inside threads it has joined, even without a fresh mention.
- chime into allowed channels on its own when a small "is this a good idea?"
  gate decides the message is meant for it (no mention needed), throttled so
  it won't jump into the same channel more than once every
  `UNPROMPTED_COOLDOWN_SECONDS`.
- DM you a reminder to send today's `@matthias-day` ping at `DAILY_PING_REMINDER`
  (default 19:00, Europe/Amsterdam), and reply with a "messages sent today"
  recap (never as a thread reply) whenever that usergroup actually gets
  pinged.
- DM you a tiny wakey-wakey message every morning at `DAILY_MORNING_DM`
  (default 08:00, Europe/Amsterdam), mentioning it only if today has a silly/
  named day (like watermelon day). if there are any new hack club news
  articles (news.hackclub.com) since the last check, that's sent as a
  second, separate message right after.
- ignore any message that starts with `# ` (a hash and a space), always,
  no matter what.
- watch one channel (`WATCHED_CHANNEL_ID`) for people joining/leaving: on
  join it adds them to the `@matthias-day` usergroup, logs it (one message,
  channel join + group add together) to `LOGS_CHANNEL_ID`, and welcomes them
  with an ephemeral message (visible only to them) mentioning them
  (`JOIN_EPHEMERAL_MESSAGE`). on leave it removes them from the usergroup if
  they were in it and logs that too, again as one message.
- search slack for old messages and fetch the real text of a single message by
  its link.
- look up users, channels, apps, emoji and commands by id or by name using the
  public flaron directory (the single `flaron` tool returns the full json), so it
  always mentions the right `<@USERID>` or `<#CHANNELID>`.
- remember the recent conversation per dm or thread, and never sends its own
  reasoning or tool output to slack.
- persist dms, threads, and direct pings/mentions (not ambient allowed-channel
  chatter it wasn't addressed in - that's not persisted yet) to a local
  sqlite db (`data/noodle_memory.db`), each turn embedded for semantic
  search. the daily ping reminder and morning dm(s) count as dm turns too.
  a `search_memory` tool lets noodle semantically search weeks of its own
  history when asked to recall something older than the live context window.

## how mentioning works

when noodle writes about a person or channel it uses slack's link syntax so the
client renders a real mention:

- a user: `<@USERID>` (never `@john`)
- a channel: `<#CHANNELID>` (never `#general`)

if it only knows a name, it calls `lookup_slack_user` / `lookup_slack_channel` /
`search_slack_users` to resolve the correct id first. the same idea works the
other way: paste a slack link and noodle can read what the message actually
says with `fetch_slack_message`.

## project layout

```
noodle/
  main.py              # thin entrypoint: builds the app and starts socket mode
  config.py            # loads .env and exposes all settings + paths
  bot/                 # the actual bot, split into small focused modules
    __init__.py        # shared runtime state (app, client, memory, identity)
    app.py             # builds the bolt app, resolves identity, starts socket mode
    handlers.py        # the message event: decide whether to reply, then process
    gate.py            # "should noodle reply here?" unprompted-reply gate
    llm.py             # talks to the model, runs the tool loop
    tools.py           # the tools the model can call (search, fetch, lookups)
    slack_text.py      # cleans incoming text, parses slack links, channel links
    directory.py       # flaron user/channel directory (no auth needed)
    memory.py          # per-conversation memory helpers (+ the stable dm key)
    history_store.py   # persistent + embedded memory (sqlite), semantic search
    embeddings.py      # calls the ai endpoint's /embeddings route
    chunk.py           # splits a reply into one slack message per paragraph
    stats.py           # "messages sent today" recap (via slack search)
    almanac.py         # silly/named days today (wikipedia)
    news.py            # hack club news rss + persisted "seen" state
    morning.py         # builds the morning greeting + separate news message
    scheduler.py       # daily background jobs (ping reminder, morning dm)
    tz.py              # shared Europe/Amsterdam timezone constant
    usergroups.py      # add/remove a user from a slack usergroup
    watch.py           # channel join/leave analytics + automations
    log.py             # logging setup
  prompts/
    system_prompt.md   # noodle's persona + reply style + tool instructions
  logs/                # created at runtime (noodle.log)
  data/                # created at runtime (news state, noodle_memory.db)
```

## running it locally

```bash
cd C:/Code/noodle
python -m venv .venv
.venv\Scripts\activate      # windows
pip install -r requirements.txt
python main.py
```

you should see `starting noodle in socket mode...` and `noodle online as ...`
in the console. then DM noodle from the `USER_ID` account to test the persona.

## running it with docker

```bash
docker compose up -d --build
```

the container reads `.env` next to the compose file. logs go to the console; the
code also tries to write `logs/noodle.log` and falls back to console only if
that is not writable.

## .env reference

| var | meaning |
| --- | --- |
| `SLACK_USER_TOKEN` | user token (xoxp-...) the app acts as |
| `SLACK_APP_TOKEN` | socket mode app token (xapp-...) |
| `USER_ID` | only slack user allowed to DM noodle |
| `OPENAI_API_KEY` | openai compatible api key (sk-or-...) |
| `AI_ENDPOINT` | openai compatible base; `/v1` is appended automatically |
| `MODEL` | model id, e.g. `gpt-4o-mini` or an openrouter model |
| `EMBEDDING_MODEL` | embeddings model for memory search, via `AI_ENDPOINT` + `/embeddings` |
| `ALLOWED_CHANNELS` | comma separated channel ids, or `*` for any |
| `CHUNK_DELAY_SECONDS` | pause between fragment messages |
| `UNPROMPTED_COOLDOWN_SECONDS` | min gap between unprompted replies in a channel |
| `DAILY_PING_REMINDER` | `HH:MM` time for the `@matthias-day` reminder dm (Europe/Amsterdam) |
| `DAILY_MORNING_DM` | `HH:MM` time for the morning wakeup dm (Europe/Amsterdam) |
| `MATTHIAS_DAY_GROUP_ID` | optional usergroup id for `@matthias-day` |
| `WATCHED_CHANNEL_ID` | channel to watch for joins/leaves; empty disables the feature |
| `LOGS_CHANNEL_ID` | where join/leave + usergroup changes get logged |
| `JOIN_EPHEMERAL_MESSAGE` | ephemeral nudge template, `{user}` = the new member |
| `LOG_LEVEL` | optional, default `INFO` |

every scheduled/dated thing noodle does runs in Europe/Amsterdam time,
hardcoded (not an env var).

## notes

- noodle only answers DMs from the slack user whose id is in `USER_ID`. any DM
  from another user is silently ignored. it never replies to its own messages.
- all code, comments and logs are written in english. the assistant persona
  itself speaks in a warm, casual, lowercase style. that is the bot
  character, not the codebase.
- tool calling needs a model that supports function calls. if the configured
  `MODEL` does not, the search/fetch/lookup tools simply will not trigger.
- the join/leave watcher needs `usergroups:read` + `usergroups:write` scopes
  on `SLACK_USER_TOKEN`, and the slack app must have the
  `member_joined_channel` / `member_left_channel` events subscribed (event
  subscriptions are configured in the slack app settings, not in this repo).
- memory search is built against hack club ai's embeddings proxy
  (`docs.ai.hackclub.com/api/embeddings.html`), hit at `AI_ENDPOINT +
  "/embeddings"` using `OPENAI_API_KEY`. the default `EMBEDDING_MODEL` is a
  free model there; it'll work against any other openai-compatible
  `/embeddings` endpoint at that same base too.

## license

MIT. have fun, be kind.
