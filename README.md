# noodle

a tiny, cute slack agent that lives in your workspace and chats through its
own dedicated slack account (a user-type account, not a bot app, and not
anyone's personal login) using the slack bolt framework in socket
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
  gate decides the message is meant for it (no mention needed). that gate is
  jev (hack club ai's fast/cheap classifier model, not a full chat
  completion) answering a single yes/no question with a calibrated
  probability; a reply is also throttled so it won't jump into the same
  channel more than once every `UNPROMPTED_COOLDOWN_SECONDS`.
- DM you a reminder to send today's `@matthias-day` ping at `DAILY_PING_REMINDER`
  (default 19:00, Europe/Amsterdam), and reply with a "messages sent today"
  recap (never as a thread reply) whenever that usergroup actually gets
  pinged.
- DM you a tiny wakey-wakey message every morning at `DAILY_MORNING_DM`
  (default 08:00, Europe/Amsterdam), mentioning it only if today has a silly/
  named day (like watermelon day). if there are any new hack club news
  articles (news.hackclub.com) since the last check, that's sent as a
  second, separate message right after.
- add the weather (open-meteo) for your places to the morning dm, DM an
  end-of-day wrap-up, a weekly recap of your `@matthias-day` journal, and DM you new github notifications
  (quiet hours + a daily cap keep it polite).
- answer weather, last.fm now-playing, github and "what did i do in march"
  (journal) questions with tools, and tl;dr a slack thread from a link.
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
  search and tagged with the slack id of whoever said it (nothing else about
  them is stored). the daily ping reminder and morning dm(s) count as dm
  turns too. a `search_memory` tool lets noodle semantically search weeks of
  its own history when asked to recall something older than the live
  context window.
- search the live web for a short, cited answer with a `web_search` tool
  (exa, via hack club ai) - only called when it genuinely needs current or
  external info, never for casual chat.
- messages to the same dm/thread/channel are handled one at a time, in
  order, so several messages arriving close together don't race each other
  and come out jumbled or delayed.

## tools

these are the tools the model can call (it decides when; results are used as
context and never posted raw). "matthias only" is enforced in code against the
real slack sender id, so it is safe in public channels too.

| tool | what it does | who can use it |
| --- | --- | --- |
| `search_slack_messages` | search slack as noodle's own account (only what it can see) | anyone |
| `fetch_slack_message` | read the real text of one message from its link; silently joins a public channel it can't read yet, but only when matthias asked | anyone |
| `flaron` | look up users, channels, apps, emoji, commands in the public flaron directory | anyone |
| `search_memory` | semantic (vector) search of noodle's persisted memory: dms, threads, pings, journal, daily facts | matthias only |
| `web_search` | short cited answer from the live web (exa via hack club ai) | anyone |
| `get_weather` | forecast for any place; no place = matthias's own location, which outside a dm is only ever reported as "current location" | anyone |
| `now_playing` | what matthias is listening to, via last.fm scrobbles | matthias only |
| `music_stats` | his top artists / tracks / albums over a period (7 days to all time), via last.fm | matthias only |
| `run_code` | run python (numpy, pandas, matplotlib, scipy, sympy) or javascript in an isolated wasm sandbox: calculations, data crunching, plots (sent to the dm as png), file conversions; no network | matthias only, dms only |
| `play_game` | tictactoe, connect4, hangman, wordle, mastermind in a dm, only when he asks; the engine plays and keeps state, the board is attached automatically | matthias only, dms only |
| `github` | repo, commits, issues, pulls, search, notifications, commits today | matthias only |
| `links` | create, list, edit, delete and see click stats of his short links | matthias only |
| `get_journal` | his `@matthias-day` recaps for a date range (plain date filter, not vector search) | matthias only |
| `summarize_thread` | reads a whole thread (link or current thread) so the model can tl;dr it; same auto-join rule as `fetch_slack_message` | anyone |
| `freedom` | the full freedom.to toolbox: devices, blocklists, sessions, schedules, history (13 actions); only ever touches matthias's own devices | matthias only |
| `start_freedom_session` | simple "block everything on my devices for N minutes" | matthias only |
| `schedule_freedom_session` | hand a later or recurring session to freedom's own scheduler | matthias only |
| `schedule_reminder` | remind him later (one-off or repeating) in a dm, channel or thread | matthias only |
| `list_scheduled` | list scheduled reminders and freedom sessions | matthias only |
| `cancel_scheduled` | cancel one reminder or freedom session by id | matthias only |

## how mentioning works

when noodle writes about a person or channel it uses slack's link syntax so the
client renders a real mention:

- a user: `<@USERID>` (never `@john`)
- a channel: `<#CHANNELID>` (never `#general`)

if it only knows a name, it calls the `flaron` tool to resolve the correct id first. the same idea works the
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
    exa.py             # calls the ai endpoint's /exa/answer route (web_search)
    chunk.py           # splits a reply into one slack message per paragraph
    stats.py           # "messages sent today" recap (via slack search)
    almanac.py         # silly/named days today (wikipedia)
    news.py            # hack club news rss + persisted "seen" state
    morning.py         # builds the morning greeting + separate news message
    scheduler.py       # daily background jobs (ping reminder, morning dm)
    tz.py              # shared Europe/Amsterdam timezone constant
    usergroups.py      # add/remove a user from a slack usergroup
    watch.py           # channel join/leave analytics + automations
    matthias_day.py    # clean + persist @matthias-day recap pings into memory
    reminders.py       # scheduled reminders: sqlite store + recurrence math
    freedom.py         # calls matthias's self-hosted freedom.to api
    freedom_tool.py    # the full `freedom` tool (devices, blocklists, sessions, schedules)
    httpjson.py        # tiny shared GET-json helper for the apis below
    weather.py         # open-meteo forecasts (morning dm + get_weather)
    lastfm.py          # last.fm now playing / tracks today (now_playing)
    github.py          # github rest calls (github tool, commit count)
    links.py           # link shortener api calls (links tool)
    github_watch.py    # new-notification diffing + persisted "seen" state
    proactive.py       # quiet hours + daily cap for unprompted pings
    oneshot.py         # one-off persona completion + daily-facts memory rows
    wrapup.py          # end-of-day wrap-up dm
    games.py           # dm games (tictactoe, connect4, hangman, wordle, mastermind)
    sandbox.py         # client for the code sandbox (run_code tool)
    weekly.py          # weekly journal + music recaps
    channels.py        # silently join configured channels
    log.py             # logging setup
    netlog.py          # concise "called hcai .../called tool ..." log lines
  sandbox/             # the code sandbox: a separate container (node + wasm, no network)
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
mkdir -p data logs && chmod 777 data logs   # first time only, see note below
docker compose up -d --build
```

the container reads `.env` next to the compose file. `./data` and `./logs`
are bind-mounted into the container (`docker-compose.yml`) so persisted
state - `noodle_memory.db`, the hack club news "seen" state, log files -
survives `docker compose up -d --build`, not just a plain restart. without
that mount, rebuilding the container (which you'll do on every update)
recreates its filesystem from scratch and silently wipes all of that.

the `chmod 777` is needed because the container runs as a non-root
`appuser`: if `./data`/`./logs` don't exist yet, docker creates them on the
host owned by root, which the container user can't write into. skip this
step if those directories already exist with permissions the container can
write to.

`docker compose up` also starts a second container, `sandbox`, that runs the
`run_code` tool's python/javascript. it is on only if `SANDBOX_TOKEN` is set in
`.env`: without it noodle doesn't offer `run_code` at all and the container just
idles. it gets none of noodle's other secrets and has no internet: it only shares
a private internal network with noodle. the first build downloads about 50MB of
python packages; after that it never needs the network.

the same rule holds for every tool that needs a credential (github, last.fm,
freedom, the sandbox): no key in `.env`, no tool, and noodle doesn't know it
exists.

logs go to the console either way; the code also tries to write
`logs/noodle.log` and falls back to console only if that's not writable.

## .env reference

| var | meaning |
| --- | --- |
| `SLACK_USER_TOKEN` | token (xoxp-...) of noodle's own dedicated slack account |
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
| `SANDBOX_TOKEN` | shared secret between noodle and the code sandbox container (16+ random chars, e.g. `openssl rand -hex 24`). leave unset to switch the sandbox off |
| `MATTHIAS_USER_TOKEN` | matthias's OWN xoxp token (`search:read`), used only to count today's messages including private channels - nothing else; optional |
| `DAILY_WRAPUP_DM` | `HH:MM` end-of-day wrap-up dm (default `21:30`) |
| `WEEKLY_JOURNAL` / `WEEKLY_MUSIC` | `<mon..sun> HH:MM` weekly journal recap (`sun 18:00`) / music recap (`sun 18:30`) |
| `QUIET_HOURS` / `PROACTIVE_DAILY_CAP` | no unprompted pings in `HH:MM-HH:MM` (default `23:00-07:30`) / max per day (default 10) |
| `WEATHER_LOCATIONS` / `WEATHER_DEFAULT_LOCATION` | private, set in `.env` only: morning dm places (comma separated) / default place for `get_weather` |
| `LASTFM_API_KEY` / `LASTFM_USER` | optional, enables `now_playing` |
| `GITHUB_TOKEN` / `GITHUB_USER` / `GITHUB_POLL_SECONDS` | optional classic token (`repo`, `notifications`): enables the `github` tool and the notification watcher |
| `LINKS_API_BASE` / `LINKS_API_KEY` | link shortener api base url (e.g. `https://go.example.com/api/v1`, no default) + its `lnk_...` key; both optional, but the `links` tool is only offered when both are set |
| `FREEDOM_API_BASE` / `FREEDOM_API_KEY` / `FREEDOM_DEVICE_NAMES` | matthias's self-hosted freedom api; key optional (unset disables the freedom tools); device names are the only devices ever blocked |
| `MATTHIAS_DAY_GROUP_ID` | optional usergroup id for `@matthias-day` |
| `MATTHIAS_DAY_CHANNEL_ID` | channel the daily recap pings are posted in |
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
  "/embeddings"` using `OPENAI_API_KEY`. the default `EMBEDDING_MODEL`
  (qwen3-embedding-8b) is a cheap paid model, deliberately not a `:free`
  one; it'll work against any other openai-compatible `/embeddings`
  endpoint at that same base too.
- the unprompted-reply gate is built against hack club ai's jev/"system one"
  model (`docs.ai.hackclub.com/api/jev.html`), hit at `AI_ENDPOINT +
  "/jev/systemone"` - a purpose-built classifier, not a chat completion, so
  it's both faster and cheaper (billing is input-tokens-only there; output
  is free) than the old approach of asking a full chat model to answer
  "yes"/"no" in text.
- `web_search` is built against hack club ai's exa proxy
  (`docs.ai.hackclub.com/api/exa.html`), hit at `AI_ENDPOINT +
  "/exa/answer"` using `OPENAI_API_KEY` - same pattern as embeddings/jev.
- persisting a memory turn embeds it first (a network call), which runs in
  a background thread (`history_store.append_message_async()`) so it never
  adds latency to noodle's actual reply.

## license

MIT. have fun, be kind.
