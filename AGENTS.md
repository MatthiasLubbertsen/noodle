# AGENTS.md — noodle project guide

noodle is a slack agent that runs on a **slack user account** (not a bot app)
using the **slack bolt** framework in **socket mode**. it is powered by an
openai-compatible api (configured to **openrouter**) using a configurable
model (defaults described in `.env`).

all code, comments, and logs are written in english. the assistant persona
itself speaks in a warm, casual, lowercase style — that is the bot
character, not the codebase.

## project layout

```
noodle/
  main.py                 # thin entrypoint: builds the app, starts socket mode
  config.py               # loads .env, exposes settings + paths
  bot/                    # the bot, split into small focused modules
    __init__.py           # package marker
    state.py              # shared runtime state (app, client, memory, identity)
    app.py                # builds bolt app, resolves identity, starts socket mode
    handlers.py           # message event: decide whether to reply, then process
    gate.py               # "should noodle reply here?" unprompted-reply gate
    llm.py                # talks to the model, runs the tool loop
    tools.py              # tools the model can call (search, fetch, lookups)
    slack_text.py         # cleans incoming text, parses slack links, channel links
    directory.py          # flaron user/channel directory (no auth needed)
    memory.py             # per-conversation memory helpers
    chunk.py              # splits replies into small slack messages
    stats.py              # "messages sent today" recap (via slack search)
    almanac.py            # silly/named days today (wikipedia)
    news.py               # hack club news rss + persisted "seen" state
    morning.py            # builds the tiny morning wakeup message
    scheduler.py          # daily background jobs (ping reminder, morning dm)
    tz.py                 # shared Europe/Amsterdam timezone constant
    usergroups.py         # add/remove a user from a slack usergroup
    watch.py              # channel join/leave analytics + automations
    log.py                # logging setup
  requirements.txt        # python dependencies
  .env                    # secrets + tuning (already populated, do not commit)
  .gitignore              # ignores .env, logs, caches
  AGENTS.md               # this file
  README.md               # project readme (badges, run instructions)
  prompts/
    system_prompt.md      # noodle's persona + reply-style instructions
  logs/                   # created at runtime (noodle.log)
  data/                   # created at runtime (hackclub_news_seen.json)
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
- a message whose text starts with `# ` (a literal hash + space) is ALWAYS
  ignored first, before anything else (mentions, DMs, the `@matthias-day`
  ping, the gate) — see `handlers._is_ignored()`.
- responds when:
  - it is a **direct message (DM)**, or
  - a message in an **allowed channel** mentions "noodle" (case-insensitive)
    or `@noodle` (the app/bot user id), or
  - a message arrives in a **thread noodle has joined** (it keeps answering
    inside threads it has replied to, even without a fresh mention), or
  - a message arrives in an **allowed channel with no mention** and the
    response gate (`bot/gate.py`) decides it is a good idea to reply. the gate
    defaults to "no": it only says yes if the message is clearly addressed to
    noodle specifically, or is a direct continuation of something noodle just
    said, and explicitly ignores ordinary questions between other people. it
    also defaults to "no" on error.
  - even when the gate says yes, noodle won't chime in unprompted in the same
    channel more than once every `UNPROMPTED_COOLDOWN_SECONDS` (default 300s),
    tracked in `state.LAST_UNPROMPTED_REPLY`. mentions and joined threads
    bypass this cooldown entirely.
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
- style rules: lowercase ONLY (no capitals); warm, casual, friendly, like a
  real friend texting; no baby-talk speech quirks (no `uwu`/`owo`/`:3`, no
  turning `r`/`l` into `w`, no `_giggles_`-style cute actions); they/them.
- the prompt tells the model to write its reply as one bigger, normal chat
  message (a paragraph, or a couple if there's a lot to say) instead of
  splitting every thought onto its own line. a hard cap (`[:8]` fragments)
  still prevents the bot from spamming a channel if it ever writes an
  unusually long, multi-paragraph reply.

### 4b-ii. flaron directory lookups
- a single `flaron` tool lets noodle query the public flaron slack directory
  (no auth needed) and read the FULL json it returns. supported actions:
  `user`, `channel`, `user_search`, `channel_search`, `channel_by_name`,
  `channel_managers`, `channel_members`, `app`, `emoji`, `command`, `promote`.
  see `bot/directory.py` and the openapi at `flaron.halceon.dev/openapi.json`.
- the model uses this to turn a plain name into the correct `<@USERID>` /
  `<#CHANNELID>` and to read any info about users/channels/apps/emoji/commands
  (see persona rule in `prompts/system_prompt.md`).
- incoming channel/user mentions are passed through to the model untouched
  (as slack sends them, e.g. `<#C0C78SG9L|hq>` / `<@U09UE480JHH|bob>`). the model
  is told to copy the id into `<#CHANNELID>` / `<@USERID>` when it replies, since
  slack only renders a real mention with the full `<...>` form.

### 4b. tools (slack search)
- noodle can search slack using its own user token via the `search_messages`
  web api, exposed to the model as an openai-style **function/tool call**
  (`search_slack_messages`). the model decides when to call it; results are fed
  back as context and are **never** posted raw to slack.
- search runs **as the token's user** (logged as `searching_as=...`). it can
  only find messages that user is allowed to see, so the `SLACK_USER_TOKEN`
  must belong to the same user that can see the messages you want searched.
- requires the slack user token to have the `search:read` scope.
- tool-calling needs a model that supports function calls; if the configured
  `MODEL` does not, the search tool simply will not trigger.

### 4b-i. fetch_slack_message tool
- a second tool lets noodle read the **actual text** of one specific message
  by its permalink/url or `channel:timestamp`, using `conversations.history`
  (falling back to `conversations.replies` for thread replies).
- this is how noodle can answer "what does this message say?" when given a
  slack link, instead of only seeing search snippets/links.
- requires `channels:history` (or `groups:history`/`im:history`) on the token,
  and the bot user must be a member of that channel.
- verbose logs: each search logs its query, the first two words of the user's
  prompt, `searching_as`, the match count, and a snippet of the top matches.

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
- after the ai replies, `chunk_response()` keeps the reply as one block,
  splitting only on blank-line paragraph breaks (the model rarely uses more
  than one paragraph, since it's told to write a single bigger message).
- each resulting block is sent as its own `chat_postMessage`, with a pause of
  `CHUNK_DELAY_SECONDS` (default 0.5s) between them if there is more than one.
- long paragraphs are further hard-split at `MAX_FRAGMENT_CHARS` (default
  1500) so a single slack message never gets unreasonably huge.

### 6. @matthias-day ping + stats recap
- `bot/stats.py` builds a "messages sent today" recap for `USER_ID`, counting
  matches via `search_messages(query="from:<@USER_ID> on:<today>")` (using the
  `messages.total` field from the slack search response; "today" is always
  Europe/Amsterdam, `bot/tz.py`).
- whenever a message pings the `@matthias-day` usergroup (matched by
  `MATTHIAS_DAY_GROUP_ID` if set, and always by the literal text
  "matthias-day" as a fallback), `handlers.handle_message()` replies with
  that recap **as a new top-level channel message, never a thread reply**
  (`_send_daily_stats_reply()`). this check runs before the normal
  mention/gate logic, is not subject to `ALLOWED_CHANNELS` or the
  unprompted-reply cooldown, and is skipped by the `# `-prefix ignore rule
  like everything else.
- `bot/scheduler.py` runs a background thread (started from `build_app()`)
  that DMs `USER_ID` a plain reminder to go send that ping, every day at
  `DAILY_PING_REMINDER` (default 19:00 Europe/Amsterdam) — this reminder DM
  itself does NOT contain the stats recap, it just nudges matthias to do the
  ping himself.

### 7. morning wakeup dm
- also from `bot/scheduler.py`, a second daily background job DMs `USER_ID`
  at `DAILY_MORNING_DM` (default 08:00 Europe/Amsterdam).
- `bot/almanac.py` calls wikipedia's public `onthisday/holidays` REST api (no
  auth) for silly/named days today (e.g. "Roald Dahl Day", "Watermelon Day")
  — entries whose text starts with "Christian feast day" are filtered out
  since they exist for nearly every date and would drown out the fun ones.
- `bot/news.py` fetches the hack club news rss feed
  (`https://news.hackclub.com/feed.xml`) and diffs it against a persisted set
  of previously-seen article guids at `data/hackclub_news_seen.json` (created
  automatically; survives restarts). on the very first run ever (no state
  file yet) every article currently in the feed is marked seen and nothing is
  reported, since matthias said he'd already read everything available at
  setup time — only articles published after that count as "new".
- `bot/morning.py` gathers those facts and asks the model (using the normal
  persona system prompt, one-off call, not the regular per-conversation
  `MEMORY`) to write ONE tiny 1-2 sentence good-morning message — explicitly
  told to skip anything with nothing to say (no silly day, no new articles)
  rather than announcing "nothing special today". kept deliberately small so
  it doesn't turn into a multi-paragraph report.

### 8. channel join/leave analytics
- `application.event("member_joined_channel")` / `"member_left_channel")`
  (wired in `build_app()`) call into `bot/watch.py`. both handlers ignore
  everything outside `WATCHED_CHANNEL_ID` and ignore noodle's own
  join/leave. if `WATCHED_CHANNEL_ID` is unset the feature is a no-op.
- on join: `bot/usergroups.py` (`ensure_member()`) adds the person to
  `MATTHIAS_DAY_GROUP_ID` (fetches current members via
  `usergroups.users.list`, appends, then `usergroups.users.update` with the
  full new list - that's how the slack api works, it replaces the whole
  membership, there's no "append one user" call). ONE combined message
  ("`<@user> joined <#channel> and got added to the @matthias-day ping
  group.`") is logged to `LOGS_CHANNEL_ID`, covering both the channel join
  and the group add. then an ephemeral message (`chat_postEphemeral`,
  visible only to `USER_ID`, i.e. matthias himself - nobody else in the
  channel sees it) is posted in the watched channel from
  `JOIN_EPHEMERAL_MESSAGE`, with `{user}` substituted for a real `<@USERID>`
  mention of the person who joined, nudging matthias to say hi.
- on leave: `ensure_not_member()` removes the person from the usergroup only
  if they were actually in it (refuses to ever empty a usergroup down to
  zero members - slack's api isn't meant for that). again ONE combined
  message is logged, mentioning the removal only if it actually happened.
- needs `usergroups:read` + `usergroups:write` scopes on `SLACK_USER_TOKEN`,
  and the slack app must have `member_joined_channel` /
  `member_left_channel` subscribed as events (workspace/app-level config,
  not something this repo controls).

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
| `UNPROMPTED_COOLDOWN_SECONDS` | min gap between unprompted replies in one channel |
| `DAILY_PING_REMINDER` | `HH:MM`, when the `@matthias-day` reminder dm goes out |
| `DAILY_MORNING_DM` | `HH:MM`, when the morning wakeup dm goes out |
| `MATTHIAS_DAY_GROUP_ID` | optional usergroup id for `@matthias-day` |
| `WATCHED_CHANNEL_ID` | channel watched for joins/leaves; empty disables it |
| `LOGS_CHANNEL_ID` | where join/leave + usergroup changes get logged |
| `JOIN_EPHEMERAL_MESSAGE` | ephemeral nudge template, `{user}` = the new member |
| `LOG_LEVEL` | optional, default `INFO` |

everything time-based (both daily dms, "today" in the stats/news checks)
runs in `Europe/Amsterdam` — hardcoded in `bot/tz.py`, not an env var. only
the hour/minute of each dm is configurable, via `_parse_hhmm()` in
`bot/scheduler.py`.

## extension points (not yet built)
- per-channel/conversation memory (currently single-turn).
- streaming tokens into fragments for even snappier typing.
- slash commands or `app_mention` event handling.
