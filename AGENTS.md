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
    memory.py             # per-conversation memory helpers (+ the stable dm key)
    history_store.py      # persistent + embedded memory (sqlite), semantic search
    embeddings.py         # calls the ai endpoint's /embeddings route
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
  Dockerfile              # non-root `appuser`, see the data/logs volume note below
  docker-compose.yml      # bind-mounts ./data and ./logs - see "running it with docker"
  .env                    # secrets + tuning (already populated, do not commit)
  .gitignore              # ignores .env, logs, caches
  AGENTS.md               # this file
  README.md               # project readme (badges, run instructions)
  prompts/
    system_prompt.md      # noodle's persona + reply-style instructions
  logs/                   # created at runtime (noodle.log)
  data/                   # created at runtime (news state, noodle_memory.db)
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
    is jev (hack club ai's fast/cheap classifier model, see 4b-iv below), not
    a chat completion: it answers one yes/no question with a calibrated 0-1
    probability, and the gate requires > `REPLY_THRESHOLD` (0.6, biased above
    a coin flip to stay conservative). the instructions tell it to default to
    no: only answer yes if the message is clearly addressed to noodle
    specifically, or is a direct continuation of something noodle just said,
    and to explicitly ignore ordinary questions between other people. a
    request failure also defaults to no.
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

### 4b-iii. search_memory tool
- a `search_memory` tool (`bot/tools.py`) lets the model semantically search
  noodle's own persisted memory (see 4d below) for anything related to a
  topic, from however long ago, when asked to recall something that isn't in
  the live context window. embeds the query with `bot/embeddings.py`, then
  `history_store.search_similar()` does a brute-force cosine-similarity scan
  over every embedded row and returns the top 5, formatted as
  `[kind @ timestamp] <@USERID>: content` (or `noodle: content` for its own
  replies) - never posted raw, same as every other tool result.

### 4b-iv. the unprompted-reply gate runs on jev, not chat completions
- `bot/gate.py`'s `should_reply_unprompted()` calls hack club ai's jev /
  "system one" model (`docs.ai.hackclub.com/api/jev.html`,
  `POST AI_ENDPOINT + "/jev/systemone"`) instead of a normal chat completion.
  jev is built for exactly this kind of narrow, calibrated decision
  (classification/routing), and is faster and cheaper per call than an LLM
  request - billing there is input-tokens-only, output is free.
- the request sends one `state` string (the last ~6 turns of the
  conversation + the new incoming message) and one `noul`-type question
  (`should_reply`), which returns a 0-1 probability of "yes" rather than a
  coin-flip text answer. `GATE_INSTRUCTIONS` is the single prompt for that
  question - kept tight since it's the only thing billed, while `state`
  carries the actual content for free. `REPLY_THRESHOLD = 0.6` is the cutoff
  noodle requires before replying, biased a bit above 0.5 to stay
  conservative (matches the "default to no" rule above). any request
  failure (timeout, bad response shape, rate limit, etc) is logged and
  treated as "no".

### 4c. never send reasoning to slack
- noodle only ever forwards the model's final `content` to slack. any
  chain-of-thought / `<think:6124c78e>...</think:6124c78e>` blocks are stripped before chunking,
  and tool results are used as context only (never posted). reasoning, debug
  logs, or raw tool output are never sent to a channel or thread.

### 4d. memory: live context (in-memory) + persisted, embedded history
- **live context.** noodle keeps a per-conversation history (`state.MEMORY`,
  keyed by `bot.memory.conv_key()`) of the last ~12 user turns, including
  tool calls/results, so it can refer back to recent context within one
  conversation. on each message the user turn is appended, the model is
  called with `system + history`, and the assistant reply is stored back
  (`bot/llm.py`). this part is unchanged by persistence/search below - it's
  what actually goes into the prompt on the next turn.
- **what gets persisted.** every dm, thread (`thread:<ts>`), and direct
  ping/mention (`chan:<id>`, but only when noodle was actually addressed -
  see below) turn is written to `data/noodle_memory.db` (sqlite, stdlib
  `sqlite3`, no new dependency) via `bot/history_store.py`
  (`init_db()` / `append_message()` / `load_history()` / `search_similar()`).
  ambient allowed-channel chatter the unprompted-reply gate chimed into
  (noodle wasn't addressed) is explicitly NOT persisted yet.
- each row also carries `user_id`: the slack id of whoever actually sent a
  "user"-role message (matthias in a dm, or anyone else in a thread) -
  nothing else about them (no name, no profile data). `handlers.handle_message()`
  reads `event.get("user")` and threads it through `_process()` →
  `ask_noodle(..., user_id=...)` → `llm._remember()` → `append_message(...,
  user_id=...)`. "assistant"-role rows (noodle's own replies) always pass
  `user_id=None` - role already disambiguates those.
- `handlers._should_respond()` returns a 4th value, `persist`: `True`
  for dms, mentions, and thread continuations, `False` for the gate's
  unprompted replies. that flows through `_process()` → `ask_noodle(...,
  persist=...)` → `llm._remember()`, which tags the row's `kind`
  (`"dm"` / `"thread"` / `"mention"`) from the conv_key's prefix and skips
  writing entirely when `persist` is `False`.
- **embeddings + semantic search.** every persisted row also gets embedded
  (`bot/embeddings.py`, hack club ai's `/v1/embeddings` with `EMBEDDING_MODEL`
  - deliberately a cheap paid model, qwen3-embedding-8b, not a `:free` one;
  see 4b-iii and the `.env` reference) and the vector stored alongside it as
  json. this is what powers `search_memory`: a plain recency-ordered table
  wouldn't let noodle find something from weeks ago by topic, so this
  genuinely needs embeddings + similarity search, not just "give me the last
  N rows". `search_similar()` is a brute-force cosine-similarity scan over
  every embedded row (plenty fast for a personal bot's history - no vector
  index needed at this scale). embedding failures never break chat:
  `append_message()` just stores a `NULL` embedding and the row is skipped
  by search but still counts for live-context hydration.
- **dm continuity across restarts.** dms get one stable key regardless of
  slack's (occasionally recycled) dm channel id -
  `bot.memory.dm_conv_key()` → `f"dm:{USER_ID}"` - since there's only ever
  one authorized dm partner. `bot/scheduler.py`'s `_post_dm()` persists the
  `@matthias-day` ping reminder and the morning dm(s) the same way (kind
  `"dm"`, `user_id=None` since noodle sent them), live into
  `state.MEMORY[dm_conv_key()]` and to disk. on startup, `build_app()` calls
  `history_store.init_db()` then `load_history(dm_conv_key())` to
  repopulate `state.MEMORY` before the socket connection opens.
- channel/thread conversations are NOT similarly re-hydrated into live
  `state.MEMORY` on startup (and `state.PARTICIPATING_THREADS` isn't
  persisted either) - a restart still resets those for live back-and-forth,
  same as before. only dm live-continuity and the persisted/searchable
  history (all kinds) survive a restart.
- **this all depends on `data/` being a real bind mount, not just a
  container-local directory.** `docker-compose.yml` mounts `./data:/app/data`
  (and `./logs:/app/logs`) precisely so `noodle_memory.db` and
  `data/hackclub_news_seen.json` survive `docker compose up -d --build` -
  without it, rebuilding the container (the normal update flow) discards the
  container's whole writable layer, wiping everything in `data/` even though
  a plain restart of the same container would have been fine. see "running
  it with docker" in the README for the one-time `chmod 777 data logs` the
  non-root `appuser` needs the first time those host directories get created.

### 5. message chunking
- after the ai replies, `chunk_response()` splits it into one slack message
  per paragraph, on blank-line breaks only (the model rarely uses more than
  one paragraph, since it's told to write a single bigger message).
- a paragraph is NEVER cut mid-way, no matter how long - there is no
  character-based hard-split anymore. slack's own per-message limit is far
  bigger than anything the model realistically writes, so this is safe.
- each resulting block is sent as its own `chat_postMessage`, with a pause of
  `CHUNK_DELAY_SECONDS` (default 0.5s) between them if there is more than one,
  capped at 8 messages per reply (`handlers._process()`) so a pathological
  multi-paragraph reply can't spam a channel.

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
- `bot/morning.py` exposes two builders, sent as two separate messages by
  `scheduler._send_morning_dm()`:
  - `build_greeting_message()` asks the model (using the normal persona
    system prompt, one-off call, not the regular per-conversation `MEMORY`)
    for ONE tiny 1-2 sentence good-morning message covering only the
    good-morning + today's silly/named day (if any) — explicitly told to
    skip it entirely rather than announcing "nothing special today", and to
    never mention news. kept deliberately small.
  - `build_news_message()` is a separate, hand-written (not model-generated)
    message listing new hack club news articles, returning `None` when
    there's nothing new - in which case `_send_morning_dm()` sends only the
    greeting and skips the news message entirely.

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
  `user=<the person who just joined>` - visible only to them, nobody else in
  the channel sees it) welcomes them in, from `JOIN_EPHEMERAL_MESSAGE`, with
  `{user}` substituted for a real `<@USERID>` mention of them.
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
| `EMBEDDING_MODEL` | embeddings model for `search_memory`, via `AI_ENDPOINT` + `/embeddings` (default: a cheap paid model, not `:free`) |
| `ALLOWED_CHANNELS` | comma separated channel ids, or `*` for any |
| `CHUNK_DELAY_SECONDS` | pause between fragment messages |
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
- persisting + embedding ambient allowed-channel chatter (currently only
  dms/threads/direct pings are persisted/searchable).
- restoring thread/channel live continuity (`state.PARTICIPATING_THREADS`,
  per-channel `state.MEMORY`) across a restart - only dm live-context does
  that today; thread/channel history is persisted+searchable but not
  re-hydrated into live context on startup.
- streaming tokens into fragments for even snappier typing.
- slash commands or `app_mention` event handling.
