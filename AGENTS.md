# AGENTS.md — noodle project guide

noodle is a slack agent that runs on a **slack user account** (not a bot app)
using the **slack bolt** framework in **socket mode**. it is powered by an
openai-compatible api (configured to **openrouter**) using a configurable
model (defaults described in `.env`).

all code, comments, and logs are written in english. the assistant persona
itself speaks in a playful, lowercase, "uwuified" style — that is the bot
character, not the codebase.

## project layout

```
noodle/
  main.py                 # entrypoint: bolt app, socket mode, event handling
  config.py               # loads .env, exposes settings + paths
  requirements.txt        # python dependencies
  .env                    # secrets + tuning (already populated, do not commit)
  .gitignore              # ignores .env, logs, caches
  AGENTS.md               # this file
  prompts/
    system_prompt.md      # noodle's persona + reply-style instructions
  logs/                   # created at runtime (noodle.log)
```

note: the project lives directly at `C:/Code/noodle` — there is intentionally
no nested `noodle/noodle` folder.

## how it works

### 1. slack connection
- uses `slack_bolt.App(token=SLACK_USER_TOKEN)` with a `SocketModeHandler`
  driven by `SLACK_APP_TOKEN`.
- no public http server needed; the connection is a websocket to slack.

### 2. event triggers
- listens to the generic `message` event.
- responds when:
  - it is a **direct message (DM)**, or
  - a message in an **allowed channel** mentions "noodle" (case-insensitive)
    or `@noodle` (the app/bot user id), or
  - a message arrives in a **thread noodle has joined** (it keeps answering
    inside threads it has replied to, even without a fresh mention).
- when replying inside a thread, noodle posts into that thread
  (`thread_ts`), so conversations stay grouped.
- allowed channels come from `ALLOWED_CHANNELS` in `.env` (comma separated).
  set it to `*` to allow mentions in any channel, or leave it empty to disable
  channel replies entirely (DMs still work).

### 3. security gate
- noodle only answers DMs from the slack user whose id is in `USER_ID`.
- any DM from another user is silently ignored.
- noodle never replies to its own messages (bot_id / its own auth user id are
  filtered) to prevent reply loops.

### 4. persona & system prompt
- the persona lives in `prompts/system_prompt.md` and is loaded at startup.
- style rules: lowercase ONLY (no capitals); `r`/`l` → `w` **only sometimes**,
  for flavor; `uwu`/`owo`/`<3`/`:3` used **sparingly** (not every line); periods
  are allowed on SOME sentences and two short sentences may share one message;
  cute actions use **underscores** (`_giggles_`) and appear **only sometimes**,
  optionally on the same line as the last sentence; shy, cute, they/them.
- the prompt tells the model to keep replies short (1-2 messages) and to put
  each short thought on its own line so the bot can chunk the reply. a hard cap
  (`[:8]` fragments) prevents the bot from spamming a channel.

### 4b. tools (slack search)
- noodle can search slack using its own user token via the `search_messages`
  web api, exposed to the model as an openai-style **function/tool call**
  (`search_slack_messages`). the model decides when to call it; results are fed
  back as context and are **never** posted raw to slack.
- requires the slack user token to have the `search:read` scope.
- tool-calling needs a model that supports function calls; if the configured
  `MODEL` does not, the search tool simply will not trigger.

### 4c. never send reasoning to slack
- noodle only ever forwards the model's final `content` to slack. any
  chain-of-thought / `<think:6124c78e>...</think:6124c78e>` blocks are stripped before chunking,
  and tool results are used as context only (never posted). reasoning, debug
  logs, or raw tool output are never sent to a channel or thread.

### 4d. short-term memory (in-memory)
- noodle keeps a per-conversation history (`MEMORY`, keyed by channel or
  thread) of the last ~12 user turns, including tool calls/results, so it can
  refer back to recent context. history is lost on restart (no persistence yet).
- on each message the user turn is appended, the model is called with
  `system + history`, and the assistant reply is stored back. long-term
  (persistent) memory is a planned future extension.

### 5. message chunking
- after the ai replies, `_chunk_response()` splits the text into small
  fragments: first by line breaks (the model is instructed to use them), with
  a sentence-boundary fallback if there are none.
- each fragment is sent as its own `chat_postMessage`, with a pause of
  `CHUNK_DELAY_SECONDS` (default 0.5s) between them, to simulate fast typing.
- long fragments are further hard-split at `MAX_FRAGMENT_CHARS`.

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

## .env reference

| var | meaning |
| --- | --- |
| `SLACK_USER_TOKEN` | user token (xoxp-...) the app acts as |
| `SLACK_APP_TOKEN` | socket mode app token (xapp-...) |
| `USER_ID` | only slack user allowed to DM noodle |
| `OPENAI_API_KEY` | openrouter api key (sk-or-...) |
| `AI_ENDPOINT` | openai-compatible base; `/v1` is appended automatically |
| `MODEL` | model id, e.g. `gpt-4o-mini` or an openrouter model |
| `ALLOWED_CHANNELS` | comma separated channel ids, or `*` for any |
| `CHUNK_DELAY_SECONDS` | pause between fragment messages |
| `MAX_FRAGMENT_CHARS` | max length of a single fragment |
| `LOG_LEVEL` | optional, default `INFO` |

## extension points (not yet built)
- per-channel/conversation memory (currently single-turn).
- streaming tokens into fragments for even snappier typing.
- slash commands or `app_mention` event handling.
