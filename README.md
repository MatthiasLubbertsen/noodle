# noodle

a tiny, shy, cute slack agent that lives in your workspace and chats through a
slack user account (not a bot app) using the slack bolt framework in socket
mode. it is powered by an openai compatible api (openrouter or any proxy) and
speaks in a soft, lowercase, uwu flavored style.

[![python](https://img.shields.io/badge/python-3.10%2B-3776ab?logo=python&logoColor=white)](https://www.python.org)
[![slack](https://img.shields.io/badge/slack-bolt%20%2B%20socket%20mode-4a154b?logo=slack&logoColor=white)](https://slack.dev/bolt-python/)
[![license](https://img.shields.io/badge/license-mit-green)](#license)
[![made with love](https://img.shields.io/badge/made%20with-love-ff69b4)](https://github.com/MatthiasLubbertsen/noodle)
[![persona](https://img.shields.io/badge/persona-shy%20uwu-9b59b6)](#)

## what noodle can do

- chat in DMs (only with the one allowed user) and in allowed channels when you
  mention `noodle` or `@noodle`.
- keep talking inside threads it has joined, even without a fresh mention.
- chime into allowed channels on its own when a small "is this a good idea?"
  gate decides the message is meant for it (no mention needed).
- search slack for old messages and fetch the real text of a single message by
  its link.
- look up users, channels, apps, emoji and commands by id or by name using the
  public flaron directory (the single `flaron` tool returns the full json), so it
  always mentions the right `<@USERID>` or `<#CHANNELID>`.
- remember the recent conversation per dm or thread (in memory, resets on
  restart) and never sends its own reasoning or tool output to slack.

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
    memory.py          # per-conversation memory helpers
    chunk.py           # splits replies into small slack messages
    log.py             # logging setup
  prompts/
    system_prompt.md   # noodle's persona + reply style + tool instructions
  logs/                # created at runtime (noodle.log)
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
| `ALLOWED_CHANNELS` | comma separated channel ids, or `*` for any |
| `CHUNK_DELAY_SECONDS` | pause between fragment messages |
| `MAX_FRAGMENT_CHARS` | max length of a single fragment |
| `LOG_LEVEL` | optional, default `INFO` |

## notes

- noodle only answers DMs from the slack user whose id is in `USER_ID`. any DM
  from another user is silently ignored. it never replies to its own messages.
- all code, comments and logs are written in english. the assistant persona
  itself speaks in a playful, lowercase, uwuified style. that is the bot
  character, not the codebase.
- tool calling needs a model that supports function calls. if the configured
  `MODEL` does not, the search/fetch/lookup tools simply will not trigger.

## license

MIT. have fun, be kind.
