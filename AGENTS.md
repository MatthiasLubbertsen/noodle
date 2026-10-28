# AGENTS.md — noodle project guide

noodle is a slack agent that runs on its **own, dedicated slack account** (a
user-type account, not a bot app) using the **slack bolt** framework in
**socket mode**.

> **important, easy to get wrong: noodle is NOT matthias's account.** noodle
> has its own slack account with its own identity (`state.AUTH_USER_ID`), and
> `SLACK_USER_TOKEN` is that account's token. matthias is a *different*
> person/account, identified only by `USER_ID`. "user token" / "user account"
> in this repo means the *type* of slack token (xoxp, as opposed to a bot
> token), never "matthias's login". consequences: anything noodle posts comes
> from noodle, not from matthias; noodle's search, history and channel access
> are limited to what the noodle account itself can see and has joined;
> matthias is just whoever messages it (the only authorized dm partner). don't
> write code, docs or replies that treat noodle's messages as sent by
> matthias, or that assume noodle can see everything matthias can.

it is powered by an it is powered by an
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
    exa.py                # calls the ai endpoint's /exa/answer + /exa/contents routes (web_search, fetch_url)
    webfetch.py           # fetch_url: guarded direct GET, html to text, exa fallback
    chunk.py              # splits replies into small slack messages
    rtm.py                # presence (green dot) + typing indicator via the web client websocket
    stats.py              # "messages sent today" recap (via slack search)
    almanac.py            # silly/named days today (wikipedia)
    news.py               # hack club news rss + persisted "seen" state
    morning.py            # builds the tiny morning wakeup message
    scheduler.py          # daily background jobs (ping reminder, morning dm)
    tz.py                 # shared Europe/Amsterdam timezone constant
    usergroups.py         # add/remove a user from a slack usergroup
    watch.py              # channel join/leave analytics + automations
    matthias_day.py       # clean + persist @matthias-day recap pings into memory
    reminders.py          # scheduled reminders: sqlite store + recurrence math
    freedom.py            # calls matthias's self-hosted freedom.to focus-session api
    freedom_tool.py       # the full `freedom` tool (devices, blocklists, sessions, schedules)
    httpjson.py           # tiny shared GET-json helper (one retry) for the apis below
    weather.py            # open-meteo forecasts (morning dm + get_weather tool)
    lastfm.py             # last.fm now playing / tracks today (now_playing tool)
    github.py             # github rest calls (github tool, commit count)
    links.py              # link shortener api calls (links tool)
    github_watch.py       # new-notification diffing + persisted "seen" state
    proactive.py          # quiet hours + daily cap for unprompted pings
    oneshot.py            # one-off persona completion for scheduled messages
    wrapup.py             # end-of-day wrap-up dm
    games.py              # dm games (tictactoe, connect4, hangman, wordle, mastermind)
    sandbox.py            # client for the code sandbox service (run_code tool)
    features.py           # which optional features are on: decided only by whether their credentials are set
    prompt.py             # loads the system prompt minus the sections of features that are off
    weekly.py             # weekly journal + music recaps
    log.py                # logging setup
    netlog.py             # concise "called hcai .../called tool ..." log lines
  scripts/
    backfill_matthias_day.py  # one-time: backfill historical pings into memory
  sandbox/                # the code sandbox: its OWN container (node + wasm), see 7g
    server.js             # http runner: pool, timeouts, memory watchdog, auth
    child.js              # one-shot job executor (pyodide python / quickjs javascript)
    fetch-packages.js     # build-time download of pyodide's package wheels
    Dockerfile
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
    inside threads it has replied to, even without a fresh mention) - this
    also covers a thread that only exists because someone replied to
    noodle's own standalone (non-threaded) message: `handlers._process()`
    registers the `ts` of every message noodle posts with no `thread_ts` of
    its own into `state.PARTICIPATING_THREADS` too (not just the triggering
    event's `thread_ts`), since slack lets anyone "reply in thread" to ANY
    message, turning its `ts` into a brand new `thread_ts` noodle couldn't
    have anticipated otherwise. real bug this fixed: a mention-triggered
    reply went out as a fresh top-level message (the triggering ping
    wasn't itself threaded), someone replied in a thread off of *that*
    message, and noodle silently ignored it for an hour since that
    thread's `thread_ts` (noodle's own message's `ts`) was never in
    `PARTICIPATING_THREADS` under the old code, or
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
  real friend texting, with a light dry/sarcastic streak (never mean, never
  every sentence, dropped entirely when someone is upset or needs real help);
  replies are kept SHORT (one or two sentences by default, longer only when asked); no baby-talk speech quirks (no `uwu`/`owo`/`:3`, no
  turning `r`/`l` into `w`, no `_giggles_`-style cute actions); they/them.
- the prompt tells the model to write its reply as one short chat message
  instead of splitting every thought onto its own line. a hard cap (`[:8]` fragments)
  still prevents the bot from spamming a channel if it ever writes an
  unusually long, multi-paragraph reply.
- the "using tools" section opens with a blanket rule: only call a tool when
  it's actually needed for that specific reply, never speculatively or out
  of habit. every tool call (`tool_choice="auto"` in `bot/llm.py`, so calling
  is always the model's choice, never forced) costs real money and real
  latency, and over-eager tool use is what caused the flaron mis-ping bug
  described in 4b-ii.
- the "using tools" section also tells the model that a tool often returns
  far more than is needed (e.g. flaron's full user profile - timezone,
  title, nda/verification status...) and to only share what's actually
  relevant to the question, not recite every field back. this exists
  because the model once answered "who's U097VQ3MX35?" by dumping the
  entire flaron response instead of just saying who the person is.

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
- **both the tool's own `description` field and `prompts/system_prompt.md`
  explicitly say not to use flaron speculatively** - only when a human
  clearly wants a specific real person/channel referenced or tagged, never
  to check whether some ordinary word (a technical term, a feature/model
  name) might coincidentally match a real user. this exists because the
  model once did exactly that: it explained "jev" (the classifier model
  used in 4b-iv) by looking the word up via flaron, found an unrelated real
  slack bot that happened to be named something like
  `JevClaudeCodexT3codeAgentProMax`, and pinged it in its reply. the fix is
  prompt-level (there's no code-level way to block a specific tool call
  pattern without breaking legitimate lookups), so it reduces but doesn't
  100% guarantee this can't recur.

### 4a. tool reference (all of them)
every tool lives in `bot/tools.py` (definitions in `TOOLS`, dispatch in
`run_tool()`); the `freedom` tool's logic is in `bot/freedom_tool.py`. "matthias
only" is enforced in code against the real slack sender id, never left to the
model. details for each are in the numbered sections below (4b-* search/fetch/
memory/web/freedom, 7b reminders + freedom scheduling, 7c-7e the rest).

| tool | what it does | who can use it |
| --- | --- | --- |
| `search_slack_messages` | search slack as noodle's own account (only what it can see) | anyone |
| `fetch_slack_message` | read the real text of one message from its link; silently joins a public channel it can't read yet, but only when matthias asked | anyone |
| `flaron` | look up users, channels, apps, emoji, commands in the public flaron directory | anyone |
| `search_memory` | semantic (vector) search of noodle's persisted memory: dms, threads, pings, journal, daily facts | matthias only |
| `web_search` | short cited answer from the live web (exa via hack club ai) | anyone |
| `fetch_url` | read one web page from its url as plain text; direct fetch that refuses private addresses, exa fallback for js pages (x/reddit usually fail); result labelled untrusted data | matthias only |
| `get_weather` | forecast for any place; no place = matthias's own location, which outside a dm is only ever reported as "current location" | anyone |
| `now_playing` | what matthias is listening to, via last.fm scrobbles | matthias only |
| `music_stats` | his top artists / tracks / albums over a period (7 days to all time), via last.fm | matthias only |
| `run_code` | run python (numpy, pandas, matplotlib, scipy, sympy) or javascript in an isolated wasm sandbox: calculations, data crunching, plots (sent to the dm as png), file conversions; no network | matthias only, dms only |
| `play_game` | tictactoe, connect4, hangman, wordle, mastermind in a dm, only when he asks; the engine plays and keeps state, the board is attached automatically | matthias only, dms only |
| `github` | repo, commits, issues, pulls, search, notifications, commits today | matthias only |
| `links` | create/list/get/update/delete his short links + click stats (link shortener api) | matthias only |
| `get_journal` | his `@matthias-day` recaps for a date range (plain date filter, not vector search) | matthias only |
| `summarize_thread` | reads a whole thread (link or current thread) so the model can tl;dr it; same auto-join rule as `fetch_slack_message` | anyone |
| `freedom` | the full freedom.to toolbox: devices, blocklists, sessions, schedules, history (13 actions); only ever touches matthias's own devices | matthias only |
| `start_freedom_session` | simple "block everything on my devices for N minutes" | matthias only |
| `schedule_freedom_session` | hand a later or recurring session to freedom's own scheduler | matthias only |
| `schedule_reminder` | remind him later (one-off or repeating) in a dm, channel or thread | matthias only |
| `list_scheduled` | list scheduled reminders and freedom sessions | matthias only |
| `cancel_scheduled` | cancel one reminder or freedom session by id | matthias only |

**no credential, no tool.** eight of these need a credential, and without it they are
not just broken but absent: `bot/features.py` decides from the env alone, `tools.available_tools()`
drops them from the list the model is sent, `prompt.load_prompt()` removes their sections of the
system prompt (wrapped in `<!-- if:NAME -->...<!-- endif:NAME -->` in `prompts/system_prompt.md`)
and fills the "never write a tool's name" list with only the tools that exist, and the background
jobs that need them don't start. so the model never offers or promises something that isn't there.
- `GITHUB_TOKEN` -> `github` (and the notification watcher)
- `LASTFM_API_KEY` + `LASTFM_USER` -> `now_playing`, `music_stats`
- `FREEDOM_API_KEY` -> `freedom`, `start_freedom_session`, `schedule_freedom_session`
- `LINKS_API_BASE` + `LINKS_API_KEY` (both) -> `links`
- `SANDBOX_TOKEN` (16+ chars) -> `run_code`
everything else (search, flaron, memory, weather, journal, threads, reminders, games, web search)
needs nothing extra. **adding a tool that needs a credential?** add it to `features.py`
(`TOOL_FEATURE` + `enabled()`) and wrap its prompt section in an `if:` marker.

**adding or changing a tool?** update all of these together: the `TOOLS` entry
and `run_tool()` dispatch in `bot/tools.py`, the "using tools" section and the
leaked-name list in `prompts/system_prompt.md` (`_TOOL_NAMES` in `bot/llm.py`
is derived from `TOOLS`, so that one is automatic), the matthias-only line in
`llm._runtime_context()` if it's restricted, this table, and the README table.

### 4b. tools (slack search)
- noodle can search slack using its own user token via the `search_messages`
  web api, exposed to the model as an openai-style **function/tool call**
  (`search_slack_messages`). the model decides when to call it; results are fed
  back as context and are **never** posted raw to slack.
- search runs **as noodle's own account** (logged as `searching_as=...`),
  not as matthias. it can only find messages that account is allowed to see
  (channels it has joined, its own dms), so the `SLACK_USER_TOKEN` account
  has to be in the channels you want searched.
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
  over every embedded row and returns the top 5 - never posted raw, same as
  every other tool result.
- persisted rows only ever store a raw `user_id` (see 4d), which on its own
  doesn't tell the model anything - so `_search_memory()` resolves each
  unique `user_id` in the results to a display name via
  `directory.lookup_display_name()` (one flaron `/user/<id>` lookup per
  unique id in the batch, cached locally in the function call so a person
  showing up in several results only costs one lookup) and formats each line
  as `[kind @ timestamp] Name (<@USERID>): content` - giving the model both
  a human-readable name (so it actually knows who it's quoting) and the real
  `<@USERID>` mention (so it can still tag them if it wants to). falls back
  to just `<@USERID>` if flaron has no name for that id, and to `noodle` /
  `someone` for assistant rows / rows with no stored `user_id`.

### 4b-iv-a. web_search tool (exa)
- a `web_search` tool (`bot/exa.py`, `run_tool()` in `bot/tools.py`) calls
  hack club ai's exa proxy (`docs.ai.hackclub.com/api/exa.html`,
  `POST AI_ENDPOINT + "/exa/answer"`) with a natural-language question and
  gets back a short answer grounded in live web results, plus up to 3 source
  urls appended as `sources: ...`. same auth/base-url pattern as
  embeddings/jev. its description and the system prompt both say to use it
  ONLY for current/external info noodle doesn't already know - never for
  casual chat or things answerable from the conversation itself.

### 4b-v. start_freedom_session tool (matthias-only, enforced server-side)
- a `start_freedom_session` tool (`bot/freedom.py`, `_start_freedom_session()`
  in `bot/tools.py`) starts a focus session on matthias's
  [freedom.to](https://freedom.to) account (website/app blocker) via his
  self-hosted api (`github.com/MatthiasLubbertsen/freedom`, deployed at
  `FREEDOM_API_BASE`, default `https://freedom.matthiaz.dev`). `POST
  /api/sessions` with `{"duration_minutes": N, "block_everything": true,
  "device_ids": [...]}` and `Authorization: Bearer FREEDOM_API_KEY` - blocks
  everything, for `duration_minutes` (1-1440). no blocklist selection is
  exposed - this is a simple "block me for X minutes" button, not the full
  freedom api (which also supports specific blocklists, scheduled/recurring
  sessions, ending sessions, device/blocklist listing - see the repo's
  `docs/freedom-endpoints.md` and `web/openapi.yaml` if any of that gets
  added later).
- **the freedom account is SHARED with matthias's dad, so this never uses
  `all_devices: true`** (the first version of this tool did, and would have
  blocked his dad's devices too). instead, `_start_freedom_session()` calls
  `freedom.list_devices()` (`GET /api/devices`) every time and resolves
  `config.FREEDOM_DEVICE_NAMES` (a comma-separated allowlist of device
  names exactly as shown in freedom's dashboard, default `"Matthias
  Windows,mopa5m"`) to their current ids, then passes only those as
  `device_ids`. if none of those names match a registered device (renamed,
  typo'd env var, api hiccup), it refuses outright rather than falling back
  to blocking everything on the account - guessing wrong here means
  blocking someone else's device, which is worse than just not starting
  the session.
- **this only works when the actual slack message is from `USER_ID`, checked
  in `_start_freedom_session()` itself, not left to the model's judgment** -
  the tool's own description tells the model not to call it for anyone else
  or "on matthias's behalf", but that's a prompt-level nudge, not the real
  guard. `handlers.handle_message()` → `ask_noodle(..., user_id=...)` →
  `llm.py`'s tool loop → `run_tool(..., user_id=user_id)` threads the real
  sender id of the message that triggered this reply all the way through,
  and `_start_freedom_session()` refuses (`"only matthias can start a
  freedom session"`) unless it equals `config.USER_ID` - this is what makes
  it safe to allow the tool in public channels too (`ALLOWED_CHANNELS`),
  not just dms: a DM already can't reach this tool from anyone but
  `USER_ID` (section 3's security gate), but a public channel has no such
  restriction, so the authorization has to happen here instead.
- errors from freedom's own api come back as `{"error": {"code",
  "message"}}`; the tool translates the ones matthias is likely to actually
  hit into plain language (`freedom_reauth_required` → run `freedomd login`
  again on the server; `premium_required` → that needs a paid freedom
  feature; `no_devices` → no devices registered) and falls back to the raw
  message for anything else. `FREEDOM_API_KEY` is optional in `.env` (no
  `_require()`) - if it's unset, the tool just reports it isn't configured
  instead of noodle failing to start.

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
- every successful gate check folds its actual outcome into that same
  `log_hcai("jev", ...)` line, instead of a separate one: `called hcai jev:
  <first 4 words>: 200 (yes, p=0.82)` / `... 200 (no, p=0.20)`. this is what
  makes it possible to tell from the logs alone whether noodle stayed quiet
  because the gate said no, vs. some other reason entirely.

### 4c-i. never write em dashes
- the persona prompt forbids em/en dashes, and `slack_text._clean_reply()` (applied to
  every model-written message: replies, scheduled dms, the morning greeting) enforces it
  with `_no_dashes()`: a dash between numbers becomes a hyphen ("9-15"), a dash at the
  start or end of a line disappears, any other becomes a comma. line breaks are never
  touched, since they separate slack messages. the prompt rule alone wasn't enough,
  the model kept using them.

### 4c. never send reasoning to slack
- noodle only ever forwards the model's final `content` to slack. any
  chain-of-thought / `<think:6124c78e>...</think:6124c78e>` blocks are stripped before chunking,
  and tool results are used as context only (never posted). reasoning, debug
  logs, or raw tool output are never sent to a channel or thread.
- **this also covers a model occasionally narrating a tool call by literally
  writing the tool's bare function name as its own paragraph** (e.g.
  `fetch_slack_message`, `flaron`) instead of silently invoking it through
  the real tool-calling mechanism - seen live with `deepseek-v3.2`: a reply
  with a stray one-word paragraph like that gets chunked by
  `chunk_response()` into its own confusing standalone slack message (real
  example: noodle posted the literal word "fetch_slack_message" as a
  message in `#matthias`, visible to everyone).
- a leaked tool name means the tool was never actually called at all - the
  model just wrote its name and, in the real incident, followed it with a
  fabricated excuse ("oh no, the link you gave me is a bit mangled") rather
  than an actual attempt, so the whole reply after the leak isn't
  trustworthy either. `ask_noodle()`'s tool loop checks every tool-call-free
  response for a leaked name (`_leaked_tool_name_paragraphs()`) and, the
  first time it sees one, retries the SAME turn once (re-sends the
  unchanged `messages`/history) instead of accepting that response - giving
  the model a real second chance to actually call the tool, which is what
  fixes the underlying problem, not just the visible symptom. only if the
  retry ALSO leaks (or any other turn just doesn't call tools) does
  `_strip_leaked_tool_names()` fall back to quietly removing the leaked
  paragraph from whatever text does get sent - a last-resort cosmetic
  cleanup, applied everywhere `_clean_reply()` is, alongside the "using
  tools" section's explicit instruction not to write a tool's name in a
  reply at all.

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
  (`init_db()` / `append_message()` / `append_message_async()` /
  `load_history()` / `search_similar()`). ambient allowed-channel chatter the
  unprompted-reply gate chimed into (noodle wasn't addressed) is explicitly
  NOT persisted yet.
- **persisting is asynchronous.** `append_message()` embeds the content
  before writing it (a network call to `bot/embeddings.py`), so calling it
  inline would add that round trip to every single reply's latency -
  `llm._remember()` and `scheduler._post_dm()` both call
  `append_message_async()` instead, which fires `append_message()` off in a
  daemon thread and returns immediately. nothing in the reply path waits on
  the write or the embedding call. this was a real bug: early on,
  persistence ran inline and visibly slowed down every reply.
- **rows are stamped when the message is SENT, not when its embedding finishes.**
  `append_message_async()` sets `created_at` before spawning the thread and
  `load_history()` orders by it (`julianday(created_at)`, then id). before, the
  stamp/insert happened after the variable-length embedding call, so a slow one
  could land after a later message and history reloaded after a restart came
  back out of order.
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
  by search but still counts for live-context hydration. `bot/embeddings.py`
  retries once after a transient failure (timeout/connection reset) before
  giving up, with a 30s timeout (up from 15s - a full day's recap is a lot
  more text than a typical chat message). a caller can instead pass
  `append_message(..., require_embedding=True)` to skip the insert entirely
  on failure rather than storing a dead, never-searchable row - used by
  `bot/matthias_day.py`'s `persist_ping()`, since those rows only exist to
  be searched (never hydrated into live context) and, combined with the
  permalink dedup, this means a failed day just gets retried cleanly on the
  next backfill run instead of silently and permanently lost.
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

### 4e. messages to the same conversation are serialized
- `handlers.handle_message()` spawns a new thread per incoming slack event
  (so the socket connection is never blocked), but several messages arriving
  close together for the SAME conv_key used to race each other: each thread
  read/appended `state.MEMORY[key]` independently, so one reply's chat
  completion could run against a stale snapshot of history, assistant/user
  turns could land out of order, and replies came back jumbled and delayed -
  it visibly looked like noodle was "talking to itself" when really it was
  several independent replies to different messages all finishing around the
  same time, in the wrong order.
- fixed with one `threading.Lock` per conv_key (`state.CONV_LOCKS`, created
  on demand behind `state.CONV_LOCKS_GUARD` by `handlers._get_conv_lock()`).
  `handlers._process()` acquires that lock before doing any work
  (`_process_locked()` is the actual body) - so messages to the same
  dm/thread/channel are handled one at a time, in the order their threads
  happen to acquire the lock, each seeing the previous one's finished
  history. different conv_keys still run fully in parallel.

### 4f. logging
- `bot/log.py`'s `setup_logging()` attaches a `_QuietLibraryFilter` to every
  log handler, dropping any sub-`WARNING` record whose logger name starts
  with `httpx`, `httpcore`, or `openai` - this is what actually silences
  the `openai` sdk's http transport's own verbose per-request line
  (`HTTP Request: POST .../chat/completions "HTTP/1.1 200 OK"`), which it
  logs at `INFO` for every single call. a plain
  `logging.getLogger("httpx").setLevel(WARNING)` is kept too (belt and
  suspenders) but isn't actually sufficient by itself: in production this
  line showed up under a differently-named logger (`httpx2`, likely a
  proxy/wrapper registering its own named instance), and `setLevel()` only
  configures the exact logger object named `"httpx"` - a different name is
  a different logger entirely and keeps logging at its own default level.
  a filter on the handler catches every record that reaches it by prefix,
  regardless of which exact name it was logged under.
- `bot/netlog.py` gives two small helpers for the `noodle` logger instead:
  - `log_hcai(endpoint, result, detail="")` -> one line per outbound call to
    a hack club ai proxy endpoint, e.g. `called hcai chat: who is that: 200`,
    `called hcai jev: lol true: 200`, `called hcai embeddings: error`.
    `detail` is the first 4 words of whatever prompt/query triggered the
    call, so the log line says what the call was actually for. used by
    `bot/llm.py` (chat completions, via a `_call_chat()` helper built on
    `.with_raw_response.create()` so the real http status code is
    available), `bot/gate.py` (jev), `bot/embeddings.py`, and `bot/exa.py`.
  - `log_tool(name, detail, result)` -> one line per tool call the model
    makes, e.g. `called tool flaron: C0A9NJT2BCJ: success`,
    `called tool web_search: google revenue: success`,
    `called tool search_memory: no matches`. used by every function in
    `bot/tools.py` and by `bot/directory.py`'s `flaron_query()` (via a
    local `_run()` closure wrapping each `_get_json()` call, so every
    flaron action logs the same way without repeating the log call at
    every branch).

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
  `MATTHIAS_DAY_GROUP_ID`, and always by the literal text "matthias-day" as
  a fallback), `handlers.handle_message()` replies with that recap **as a
  new top-level channel message, never a thread reply**
  (`_send_daily_stats_reply()`). this check runs before the normal
  mention/gate logic, is not subject to `ALLOWED_CHANNELS` or the
  unprompted-reply cooldown, and is skipped by the `# `-prefix ignore rule
  like everything else.
- **this detection depends on `MATTHIAS_DAY_GROUP_ID` matching the real
  usergroup id.** a real subteam ping renders as a bare `<!subteam^ID>` with
  no readable label - unlike a user mention, slack does NOT store the
  group's name inline - so the literal-text fallback ("matthias-day"
  appearing somewhere in the message) essentially never matches a real
  ping; it only catches the rare case where matthias types `@matthias-day`
  as plain text and slack doesn't expand it into a real mention. `config.py`
  defaults `MATTHIAS_DAY_GROUP_ID` to the actual usergroup id (`S0AK8LYJ8FR`),
  confirmed against real pings in `#matthias` - harmless hardening, but
  **not actually the cause of the stats-recap bug below** (matthias's `.env`
  already had this set correctly).
- **the real bug: `handlers._is_noise()` was silently dropping the ping
  before its text was ever even checked.** `_is_noise()` treats any event
  with `bot_id` or `subtype` set as noise (to avoid reply loops / reacting
  to other bots) - confirmed cause: matthias posts the daily ping from a
  website that calls `chat.postMessage` with his own user token, but
  without `as_user=true`, which makes slack tag the message bot-style
  (`bot_id`/`subtype: "bot_message"`) even though it's genuinely him, not
  some other app. `_is_noise()` doesn't check who the message is actually
  from, so it ate every one of these. `handle_message()` now computes
  `is_matthias_day_ping` **before** the noise check - true when the text
  matches a ping AND either `event["user"] == USER_ID` (the normal case) or
  the message landed in `MATTHIAS_DAY_CHANNEL_ID` (in case that posting
  method also drops the real `user` field, leaving only `bot_id`) - and if
  `_is_noise()` would have dropped it, responds anyway, logging the actual
  `bot_id`/`subtype` values first rather than silently eating it. the
  channel check is intentionally the looser of the two conditions (matches
  on channel alone, no user check), since this channel is matthias's own
  personal daily-update channel - an acceptable tradeoff for a personal
  single-user bot.
- `bot/scheduler.py` runs a background thread (started from `build_app()`)
  that DMs `USER_ID` a plain reminder to go send that ping, every day at
  `DAILY_PING_REMINDER` (default 19:00 Europe/Amsterdam) — this reminder DM
  itself does NOT contain the stats recap, it just nudges matthias to do the
  ping himself.

### 6b. @matthias-day pings are also persisted as a searchable daily journal
- every ping also gets cleaned and persisted into memory
  (`bot/matthias_day.py`'s `persist_ping()`, called from
  `handlers.handle_message()` in its own background thread, right alongside
  the stats-reply thread) - this makes matthias's own daily recaps
  semantically searchable via `search_memory` (4b-iii) months later, not
  just visible in the slack channel itself.
- `clean_ping_text()` strips three things off the raw message before it's
  stored: the ping itself (a real `<!subteam^ID>` mention, an unexpanded
  literal `@matthias-day`/`matthias-day`, or - rarely - a `@…` collapsed
  mention slack sometimes renders when the autocomplete glitches, only
  counted when immediately followed by "today"), the `today:` / `today` /
  `Today i did:` lead-in right after it, and - for newer pings that have
  it - the trailing signature: the whole last line containing `join this
  usergroup with`, which covers both the usual `please talk in the thread
  below! • join this usergroup with ...` and one-off variants like the
  vacation `I'm ooo, so I likely won't be reading reactions ... • join this
  usergroup with ...`. `_normalize_bullets()` then turns every
  markdown-style `-`/`*` list line into slack's own bullets - `•` at the
  top level, `◦` when indented (keeping the indentation) - since some days
  were posted as `-` lists and others with real `•`/`◦`, so the journal
  reads consistently regardless of how it was posted. what's
  left is just the day's actual bullet-point content. these patterns were
  derived from reading real pings spanning the entire history (first ones
  in march, a batch from june, the most recent ones in october), not
  guessed - the format drifted slightly over time (capitalization, the
  colon, and the trailing signature only exists on pings sent after
  thread-replies got enabled), hence matching all of them with tolerant
  regexes rather than one exact string.
- **a real daily recap always contains at least one bullet line - a
  re-ping without one is never stored itself, but the recap it points at
  is.** before matthias had his own posting ui, sometimes the group ping
  just didn't fire, so he'd post the full bulleted recap and then send a
  short follow-up re-ping to actually notify - things like
  `<!subteam^ID> ^^`, `<!subteam^ID> see above :)`, `... yeah this one
  Slack :sob-wx:`, `... :rac_ded:`, `... ^^ this only happens on mobile`.
  these open with a real ping but have no content, so `persist_ping()`
  never stores them as a "day". instead, `_recover_recap_before()` treats
  the re-ping as a pointer: it fetches the ~10 messages right before it
  (`conversations_history`, `latest=<re-ping ts>`) and persists
  matthias's nearest bulleted message (dated + permalinked to *that*
  message, not the re-ping). it skips other people's messages and
  further re-pings (sometimes he re-pinged twice), and stops at the first
  of his messages that's neither, or anything over 12h older than the
  re-ping, so it can't grab an unrelated bulleted message from days
  before. if that recap already pinged correctly and got stored on its
  own, the permalink dedup makes this a no-op - so it's what saves a
  recap whose own ping never registered at all (e.g. a mention that
  didn't expand, or no ping in it at all), without risking duplicates.
  runs the same way live and in the backfill.
- **one real day actually recovered: a `@…` glitched mention.** may 25's
  recap opened with a literal `@…` instead of a real mention (the
  autocomplete broke), so it was never detected as a ping at all by the
  original matching logic - matthias then tried to re-ping the group
  (`<!subteam^ID> ^^ slack hates me forgive me if i double ping agian`),
  which IS a real ping but has no bullet content of its own. the `@… today`
  pattern above recovers the original message so this day's real content
  makes it into the journal instead of being silently dropped.
- each row is stored with `kind="matthias_day"`, `role="user"`,
  `user_id=USER_ID`, under its own `conv_key` (`matthias_day:<channel>` -
  separate from the dm/thread/mention conv_keys, since these aren't live
  back-and-forth conversation turns). critically, `created_at` is set to
  the **message's own original timestamp** (converted to Europe/Amsterdam),
  not "now" - so a backfilled entry from march shows up dated march, not
  dated whenever the backfill script happened to run.
- a `permalink` column (added via a small `ALTER TABLE` migration in
  `history_store.init_db()`, so it's safe on a db created before this
  existed) stores a real link back to the original slack message, fetched
  via `chat.getPermalink`. `search_memory` includes it in its output
  whenever present, so noodle can hand back a real link, and matthias can
  open the original message himself instead of trusting a paraphrase.
- `history_store.append_message()` now takes optional `created_at` and
  `permalink` params, and - when `permalink` is given - skips the insert
  entirely if that exact permalink is already stored. this is what makes
  **the backfill script safe to re-run**: every row it inserts carries a
  permalink, so running it twice (or running it once the live listener has
  already captured today's ping) never creates a duplicate.
- **`scripts/backfill_matthias_day.py`** is the one-time backfill: run
  manually via `python -m scripts.backfill_matthias_day`. it walks the
  *entire* history of `MATTHIAS_DAY_CHANNEL_ID` via `conversations_history`
  (cursor-paginated, not slack's search index - a plain channel history
  scan is complete and deterministic, which matters for a one-time backfill
  that has to catch everything), filters for messages `_matthias_day_pinged()`
  recognizes, and calls `persist_ping()` on each one. it's a standalone
  script (its own `slack_sdk.WebClient`, no socket mode connection) meant to
  be run once, by hand, after deploying this feature - not wired into
  `build_app()`'s startup.
- because of the permalink dedup, a plain re-run never touches rows that
  are already stored - so rows saved by an older version of the cleaning
  rules (e.g. a junk `^^` re-ping row, un-normalized `-` bullets, an
  un-stripped signature) stay as they were. `--rebuild`
  (`python -m scripts.backfill_matthias_day --rebuild`) first deletes every
  `kind="matthias_day"` row (`history_store.delete_kind()`) and then
  re-saves the whole journal with the current rules. other memory (dms,
  threads, mentions) is untouched.
- a ping whose permalink can't be fetched (`chat.getPermalink` failing,
  rate limits) is skipped rather than stored without one - the permalink
  is the only thing that dedups re-runs, so a link-less row would be
  inserted again on every backfill. same idea as `require_embedding`:
  any failure leaves no trace, and simply re-running the backfill fills
  in whatever was missed.

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

### 7b. scheduled reminders + freedom sessions, and who/when awareness
- `schedule_reminder` (`bot/tools.py`, store in `bot/reminders.py`) lets
  matthias schedule a reminder message for later, one-off or repeating
  (`daily`, `weekdays`, `weekly`, `monthly`, `every_n_minutes`, min 5). rows
  live in the `reminders` table of `data/noodle_memory.db` (survive restarts;
  created by `reminders.init_table()` in `build_app()`).
- default destination is where it was asked: `handlers.handle_message()`
  passes `origin` (`channel`, `thread_ts`, `is_dm`) through `ask_noodle()` ->
  `run_tool()`. dms stay dms, a channel/thread stays there; an explicit
  `destination` ("dm" or an allowed channel id) overrides it.
- `scheduler._run_reminder_poller()` polls every 15s. `reminders.pop_due()`
  advances (or deactivates) a reminder BEFORE it fires, so a crash drops a
  firing instead of repeating it; recurring ones skip occurrences missed
  while offline, and a late one is sent with a note. `scheduler._deliver()`
  posts + remembers the message (live + persisted) like the morning dm.
- **freedom sessions are NOT scheduled by noodle** - noodle never waits for
  the time and starts it then. `schedule_freedom_session` hands it to
  freedom's own scheduler (`bot/freedom.py`): a one-off is `POST /api/sessions`
  with `start_time` ("HH:MM" in the freedom account's timezone, read from
  `GET /api/sessions`; freedom only supports the next 24h, so anything
  further is refused), recurring is `POST /api/schedules` with `days`
  (only `daily`/`weekdays`/`weekly`; monthly/every-n-minutes aren't
  possible). both need freedom premium. same device allowlist as
  `start_freedom_session` (`_my_freedom_device_ids()`), never all devices.
- `list_scheduled` shows reminders (`#3`) plus freedom's upcoming/recurring
  sessions (`freedom:123`), `cancel_scheduled` removes either. freedom's
  schedule list is for the whole SHARED account, so both only ever show or
  delete schedules whose devices are all matthias's own, and a running
  session can't be cancelled. all of these are matthias-only, checked
  server-side against the real sender id.
- the last message noodle sent in each conversation (`state.LAST_REPLY`, set by
  `ask_noodle()` and by `scheduler._deliver()`) is put in the context with how long
  ago it was, plus a rule that a short reply ("sad", "lol") is about THAT message
  (real incident: "sad" in reply to the weather was answered as if it were about
  yesterday's game).
- awareness: `llm._runtime_context()` appends the current amsterdam time, who
  is talking (matthias = verified, from slack's sender id; anyone else =
  explicitly not matthias), the location, and that noodle itself sends the
  morning dm and the @matthias-day reminder. in channels/threads, live
  history turns are prefixed `<@USERID>:` (persisted text is unchanged).
  short-term memory is `state.MEMORY` (last ~12 turns).

### 7c. weather, music, github, wrap-ups, weekly jobs, thread tl;drs
all of these are optional and fail soft (a missing key or a dead api just
skips that piece, never breaks startup or a dm).
- **everything new goes through the model and into memory.** every message
  below is persona-written from facts (never a templated line; a plain-text
  fallback is only used if the model call fails or drops a link/channel
  mention), delivered through `_deliver()` (so the message itself is embedded
  and persisted as a dm turn), AND the raw facts behind it are saved as their
  own `kind="daily_facts"` rows (`oneshot.remember_facts()`, conv_key
  `facts:<date>`) so `search_memory` can find e.g. a day's weather, tracks
  played or commit count even if the message left a detail out.
- **morning dm** (`scheduler._send_morning_dm`): greeting, then a separate
  model-written weather message (`morning.build_weather_message()`, facts from
  `weather.morning_weather_facts()`, open-meteo, no key, places from
  `WEATHER_LOCATIONS`, set in `.env` only), then news if any.
  `get_weather` with no place uses `WEATHER_DEFAULT_LOCATION` (also `.env`). calendar is NOT included: matthias can't create the google
  oauth app it needs.
  the weather and news messages are separate model calls that follow the
  greeting, so their prompts say not to greet, they must be a single paragraph
  (blank lines would split one reply into several slack messages), and
  `morning._without_greeting()` strips a leading "good morning (matthias)" if the
  model adds one anyway (a reply that is only a greeting falls back to the plain
  facts) - this fixed a double "good morning" in the morning dm.
- **tools** (`bot/tools.py`): `get_weather` (any place; none = `WEATHER_DEFAULT_LOCATION`), `now_playing` (last.fm, since
  matthias can't create more spotify apps - spotify scrobbling to last.fm
  needs no oauth), `github` (repo/commits/issues/pulls/search/notifications/
  today; **matthias-only, checked server-side**, because his token sees
  private repos), `get_journal` (his `@matthias-day` recaps for a date range - a plain
  sql date filter on the `matthias_day` rows, NOT vector search; `search_memory`
  is the vector/topic lookup) and `summarize_thread` (returns a
  thread transcript, from a permalink or the current thread, for the model to
  tl;dr). notes/`remember_this` tools were deliberately NOT built: every dm,
  thread and ping is already persisted and embedded.
- **end-of-day wrap-up** (`wrapup.py`, `DAILY_WRAPUP_DM`, default 21:30): a
  short persona message from slack message count, github commits today and
  last.fm tracks today. it NEVER drafts or mentions the `@matthias-day`
  recap - matthias writes that himself.
- **weekly** (`weekly.py`, `_run_weekly()` in the scheduler): a recap of the
  last 7 days of `matthias_day` rows built only from those rows
  (`WEEKLY_JOURNAL`, default `sun 18:00`). there is no busiest-channels
  digest anymore (removed). like all
  scheduled dms they go through `_deliver()`, so they are saved to memory and
  searchable.
- **github watcher** (`github_watch.py`, started only if `GITHUB_TOKEN` is
  set): polls `/notifications` every `GITHUB_POLL_SECONDS`, DMs one message
  per poll for notifications not yet reported (ignores `subscribed`), state in
  `data/github_seen.json`. first run ever baselines what is already unread and
  reports nothing. needs a CLASSIC token (`repo` + `notifications`); fine-
  grained tokens can't read notifications.
- **proactivity guardrails** (`proactive.py`): the watcher is the only
  unprompted ping, so it's held during `QUIET_HOURS` (default 23:00-07:30) and
  capped at `PROACTIVE_DAILY_CAP` dms/day (state in
  `data/proactive_state.json`). held notifications stay unreported and go out
  on the next allowed poll. fixed-time dms are exempt.

### 7d. the `freedom` tool (full freedom api / mcp surface)
`bot/freedom_tool.py` exposes everything freedom's mcp has as ONE tool with an
`action`: `auth_status`, `list_devices`, `refresh_devices`, `list_blocklists`,
`list_sessions`, `session_history`, `start_session`, `end_session`,
`end_all_sessions`, `list_schedules`, `create_schedule`, `update_schedule`,
`delete_schedule`. start/create/update take `devices` and `blocklists` by name
or id (so "block only mopa5m with the social blocklist" works),
`block_everything`/`block_apps`, `start_time`/`end_time`/`duration_minutes`,
`days`. the simple `start_freedom_session` / `schedule_freedom_session` /
`list_scheduled` / `cancel_scheduled` tools still exist for the plain cases.
- **safety, enforced in code, because the account is shared with his dad:**
  matthias-only; never `all_devices`; any requested device outside
  `FREEDOM_DEVICE_NAMES` is REFUSED (not silently dropped); noodle only lists,
  ends, edits or deletes sessions/schedules whose devices are ALL his;
  `end_all_sessions` ends his running sessions one by one rather than calling
  freedom's account-wide end-all; history is filtered to his devices; session
  stats are not exposed (freedom only gives them account-wide). blocklists are
  account-wide, so `list_blocklists` shows all of them (names, counts, domains).
- no devices given = all of matthias's devices; no blocklists/apps given =
  block everything; blocklists given = only those. `use_end_session_credit` and
  `confirm_permanent` are only to be set when he explicitly says so.
- **multiple tool calls:** `llm.ask_noodle()` runs up to `MAX_TOOL_ROUNDS` (8)
  rounds; each round may contain several tool calls (run in order) and their
  results go back to the model, so chains like list devices -> list blocklists
  -> start session all happen before the reply. only the final text is sent.

### 7e. privacy: weather location, memory access, channel joining
- **matthias's location is private.** `WEATHER_LOCATIONS` and
  `WEATHER_DEFAULT_LOCATION` come only from `.env` (no defaults in code, docs
  or prompts). the morning weather goes to his dm only. `get_weather` outside a
  dm (`origin["is_dm"]` false) never gives the model or anyone the place name
  for his own location: the forecast is labelled "current location" (this also
  applies if the model passes one of his configured places by name). a place
  someone else names is looked up normally. the system prompt also forbids
  naming/guessing where he lives outside a dm.
- **`search_memory` and `get_journal` are matthias-only**, checked in
  `bot/tools.py` against the real sender id (memory holds his dms with noodle).
  `github` and `freedom` already were.
- **joining channels** (`bot/channels.py`, nothing is ever posted): at startup
  `ensure_joined()` joins the configured channels it isn't in
  (`MATTHIAS_DAY_CHANNEL_ID`, `WATCHED_CHANNEL_ID`, `LOGS_CHANNEL_ID`, explicit
  `ALLOWED_CHANNELS` ids; not `*`). also, `fetch_slack_message` and
  `summarize_thread` auto-join a public channel they can't read yet and retry,
  but only when matthias asked. needs the `channels:join` scope; private
  channels still need an invite (logged as a warning). slack itself shows a
  standard "noodle joined" line.

### 7f. no computer messages, music insights, games, matthias's own token
- **every message noodle writes to matthias (or a channel) goes through the
  model** - the daily message count (`stats.build_daily_summary`, also the
  channel reply to the `@matthias-day` ping), the ping reminder dm, the news
  message, fired reminders (`scheduler._fire_reminder`), the weather, wrap-up,
  weekly dms and github notifications. a plain-text fallback exists only for
  when the model call itself fails or drops something that must be verbatim (a
  link, a mention). NOT converted on purpose: the two error fallbacks in
  `handlers.py` (the model is the thing that's down), the machine-log lines in
  `LOGS_CHANNEL_ID`, and the `JOIN_EPHEMERAL_MESSAGE` template (matthias's own
  words, from `.env`).
- **`MATTHIAS_USER_TOKEN`** is matthias's own xoxp token (`search:read`). it is
  used for ONE thing only: `stats._counting_client()` counts today's messages
  with it so private channels and dms are included, and only the total
  (`count=1`, `messages.total`) is ever read. it is not stored in `state`, not
  passed to any tool, never used to read or post anything. unset = the count
  falls back to noodle's own narrower search. **do not use it anywhere else.**
- **music insights** (`lastfm.top()`, `lastfm.scrobbles_between()`): the
  `music_stats` tool (top artists/tracks/albums for 7day..overall) and a weekly
  model-written recap (`weekly.build_music_recap()`, `WEEKLY_MUSIC`, default
  `sun 18:30`: this week vs last week plays, top artists/tracks; facts also
  saved to memory). `now_playing` and `music_stats` are matthias-only.
- **games** (`bot/games.py`, `play_game` tool): tictactoe, connect4, hangman,
  wordle, mastermind. matthias-only AND dm-only, enforced in code, and noodle
  never starts one itself (the prompt forbids suggesting one too). the engine
  owns rules, state (`data/games.json`, survives restarts, plus a win/loss
  record) and noodle's moves (tictactoe: minimax with easy/normal/hard;
  connect4: alpha-beta search at depth 1/3/5); the model only talks. the board
  is drawn by the engine out of emoji (the same width in every client and font:
  ascii grids in a code block drifted out of alignment because slack's code
  blocks aren't always monospace; tictactoe uses keycap digits + ❌/⭕, connect4
  🔴🟡⚪, mastermind coloured pegs, wordle 🟩🟨⬛, hangman hearts + the word in
  inline code) and attached to the reply by
  `llm._with_board()` instead of trusting the model to reproduce it - it is not
  stored in memory. wordle accepts any 5 letters (no dictionary check) and
  hangman/wordle words come from built-in lists in `games.py`.
- **the engine drives, the model narrates.** a cheap model doesn't reliably call
  `play_game` (real incident: it said "you took the middle right, i went center"
  with no tool call, so the board never changed). so `games.auto()` runs in
  `llm.ask_noodle()` BEFORE the model: in matthias's dm, a clear start request
  ("can we play tic tac toe", "play again", optional easy/hard; needs a game name
  or "again", a wish to play and no refusal word) or a clear move while a game
  is running (`6`, `let's do 6`, `square 6 please` - the message must be
  essentially only the move, so "i'm 3 days late" isn't square 3; one letter for
  hangman, 5 letters for wordle, `rgby` for mastermind) is applied by the engine,
  the board is attached, and the model gets a "game engine update" note in its
  system context to react to. a chatty message counts too when the move is at its
  start or end ("this will end in a draw lol. 7"), but not a number in the middle
  of a sentence or after "at"/"have" ("i'm 3 days late", "see you at 9"). on those
  turns the model is given NO `play_game` tool (real incident: it repeated the move
  the engine had just made, got "square 2 is already taken" / "no game is running"
  back and told him that), the note carries his full message so it answers what he
  said as a person first, and tictactoe results spell out the squares in words ("top
  right", "center") plus the full board state, because the model mapped square
  numbers to places wrongly ("i blocked the top right").
  anything else still goes through the model + tool.
  game names are matched tolerantly (`games.find_game()`: regex variants plus a
  fuzzy compare for the long names, so "tick tack to", "tik tak toe", "conect
  four" work; wordle/hangman stay regex-only so "world" isn't read as "wordle").
  **safety net:** if, in his dm, a reply narrates a game turn ("you're ❌, i'm
  ⭕", "pick a square", "your move") but no game tool put a board on the turn,
  `llm.ask_noodle()` throws that draft away and forces ONE `play_game` call
  (`tool_choice`, falling back to auto if the provider rejects it) with a note
  saying nothing actually happened - real incident: "hey noods let's play tick
  tack to" matched nothing and the model invented a game start.

### 7g. the code sandbox (`run_code`)
python 3.13 (pyodide: numpy, pandas, matplotlib, scipy, sympy) and javascript
(quickjs, ES2020) run as WebAssembly in a separate container. matthias-only and
dm-only, enforced in `tools._run_code()`; text output goes back to the model as
tool context, files (matplotlib figures are collected automatically as png, plus
anything the code writes to `/out/`, max 5 files) are uploaded straight into the
dm with `files_upload_v2` (needs the `files:write` scope on noodle's token).
- **layers, innermost first.** (1) wasm: no syscalls of its own. (2) every job
  runs in its own node process started with `node --permission`: no file
  writes, no reads outside `/app`, no child processes, no workers (verified:
  even with pyodide's `js` bridge to the node host fully open, `execSync`,
  `writeFileSync`, reading `/etc/passwd` and spawning workers are all denied;
  the one gap is raw sockets, which node's permission model doesn't cover - the
  container closes it). the job process gets an empty env, and `child.js` blanks
  `process.binding` after pyodide's one use of it. (3) one process per job, killed
  right after it: a fresh pristine interpreter every time, nothing leaks between
  runs. (4) the container: non-root (uid 1000), read-only root fs, a 64m tmpfs,
  all capabilities dropped, `no-new-privileges`, pids/memory/cpu limits, NO
  secrets (its only env var is `SANDBOX_TOKEN`; the rest of `.env` is never
  passed) and NO internet - its only network is the compose `sandbox` network
  (`internal: true`) shared with noodle alone, nothing published to the host,
  no traefik in front. (5) a watchdog in `server.js`: wall-clock kill
  (default 20s, max 60s), RSS limit (900MB, polled every 200ms), 20k/10k char
  stdout/stderr caps, 200kb code limit, one python job at a time (2 may wait,
  more get a 429).
- **limits worth knowing.** pyodide is not an airtight boundary by itself (its
  python<->js bridge reaches the node host), which is exactly why layers 2-4
  exist; `child.js` also blocks `import js` best-effort, but that is a
  speed bump, not the wall. a python job costs a ~7s (up to ~15s on a slow
  host) warm-up for the NEXT job because a fresh interpreter loads in the
  background after every run, so two python runs in a row wait. scipy is
  loaded on demand when imported (it's on disk, no network); anything not
  bundled raises a normal `ModuleNotFoundError`. wasm32 caps a python process
  at ~2GB. javascript has no `require`/`fetch`/timers. installing packages
  is impossible by design.
- **turning it on and off: only `SANDBOX_TOKEN`.** set 16+ random characters in `.env`
  (`openssl rand -hex 24`) and noodle offers `run_code` and the container runs jobs; remove it
  and `run_code` disappears from what the model sees (see `features.py`) and the container
  idles: `sandbox/server.js` with no token just answers `/health` (`disabled: true`), refuses
  `/run` with a 503 and never loads python, so it uses almost no memory and doesn't crash-loop.
  compose hands the token to both containers and sets `SANDBOX_URL=http://sandbox:8080` for noodle.
  the first `docker compose up -d --build` downloads pyodide's wheels (~50MB) at build time;
  the running containers never need the internet. on an unprivileged LXC (nest) check that
  `docker stats` shows the memory limit is actually enforced - if cgroup limits aren't
  delegated, the in-process watchdog and node's heap limit are what's left. not yet built or
  run in a real container: the runner itself is tested locally, the image is not.
- **what it can and can't do** (kept in sync in three places: the `run_code`
  tool description, the "using tools" section of the system prompt, and the
  failure hints from `tools._code_hints()` - change all three together). CAN:
  python + stdlib + numpy/pandas/matplotlib/scipy/sympy (and their deps),
  javascript ES2020, plots and `/out/` files sent to the dm. CANNOT: any network
  (requests, downloads, apis, git, pip/npm), other packages (sklearn, torch,
  requests, opencv...), shell/subprocess/threads, read any file or data of
  noodle's or slack's (input has to be inside the code - passing files in is a
  possible later feature), state between runs, stdin, interactive/gui plots,
  js `require`/`import`/`fetch`/timers/dom, more than 60s, ~900MB memory, 20k
  chars of output, 5 output files of 8MB each. a failed run adds hints for
  missing modules, no network, js-only gaps, missing files, timeouts and memory.
- **later (not built):** a real shell tier (git, tests, conversions) would be a
  second container behind a forward proxy with a domain allowlist (squid on the
  internal network); this tier needs no network at all.

### 7h. no invented tool calls on game moves, reminders must be asked for
- real incident: while matthias only sent tic-tac-toe moves ("5", "8"), the model called
  `flaron` (with the example id from the prompt, which is matthias's own id), `web_search`
  and `schedule_reminder` ("test reminder for matthias", picked up from an old offer in its
  history), and the reminder was later dm'd to him. nobody else was involved.
- on engine-handled game turns `llm.ask_noodle()` now passes NO tools at all (not just no
  `play_game`): the model only narrates.
- `tools._schedule_reminder()` refuses unless the sender's message itself matches
  `_REMINDER_ASK` (remind, ping me, every day, in 10 min...), enforced in code.
- `handlers` logs `incoming from=<user> channel=<id> ts=<ts>` for every message noodle acts
  on, so "who sent this" can be answered from the logs.

### 7i. link formatting, and "again" is not a rematch
- `slack_text._clean_reply()` (via `_tidy_links()`) puts every url a model writes into slack's
  rich text form without the scheme in the visible part (`<https://go.example.com/x|go.example.com/x>`),
  never a bare url, and closes a stray space between a link and the punctuation after it. code
  spans are left alone.
- real incident: "Try again, I forgot to build" in his dm matched `games.auto()` as "play again"
  (`again` is both a wish-to-play word and the rematch word) and a tictactoe board was attached to
  an unrelated reply. with no game named, "again" now only starts the last game together with a
  real play word (`play`, `rematch`, `new game`...) or as the whole message ("again!").
- an embeddings 502 in the logs is the ai endpoint failing upstream; `embeddings.py` retries once
  and a row that still fails is stored without an embedding (chat is unaffected).

### 7j. the `fetch_url` tool (`bot/webfetch.py`)
reads one web page as text. matthias-only, checked in `tools._fetch_url()` against the real sender
id (not dm-only: a link he drops in a channel can be read there; add the `origin["is_dm"]` check
from `_run_code` if that should change). it needs no credential, so it is always offered.
- **two tiers.** (1) a direct GET by noodle itself, html turned into text with a stdlib
  `html.parser` extractor (drops script/style/nav/footer/forms/cookie-ish blocks, prefers
  `<main>`/`<article>`, falls back to the meta description for js shells); json/plain text come
  back as is. (2) exa's `/exa/contents` via hack club ai (`exa.page_contents()`), used when the
  direct fetch fails, is blocked (403/429...), isn't text (pdf), or is under `THIN_CHARS` of text.
  x/twitter/reddit/instagram/facebook/tiktok/linkedin (`EXA_FIRST_HOSTS`) go to exa first, direct
  stays as the last resort. **tested against the real api: exa does NOT read x.com or reddit** (it
  answers 200 with `results: []` and `statuses[].error.tag = SOURCE_NOT_AVAILABLE`, which
  `exa.page_contents()` surfaces as the failure reason; free, `costDollars` 0). an old tweet can still
  come through the direct tier; reddit gives 403. exa does handle js-only pages and pdfs. if x/reddit
  matter, add small adapters (fxtwitter json for x, reddit `.json`), not built. check the logs
  (`called tool fetch_url: <host>: exa|direct|failed`).
- **private-address guard, for the direct tier.** only http/https on ports 80/443, no `user:pass@`,
  no `localhost`/`.local`/`.internal`/single-label hosts; the name is resolved and EVERY address
  must be public (`ipaddress.is_global`, plus 6to4, nat64, teredo, cgnat, ipv4-mapped), which covers
  loopback, rfc1918 (the docker host and the `sandbox` container), link-local/cloud metadata
  (169.254.169.254) and the odd integer/hex spellings. the connection then goes to the validated
  IP itself (`_Pinned`/`_PinnedTLS`, tls name = original host) so dns rebinding can't swap the
  answer, redirects are followed by hand (max 5) and re-validated every hop, body capped at 3MB
  (also after gunzip) and one 20s deadline covers redirects AND the body (`read1` loop, so a slow
  drip can't hold the thread). a cut-off page says so in its text. a refusal (`Blocked`) is final and is never retried through exa. the url is
  also checked before it goes to exa, so internal urls never leave noodle.
- **untrusted content.** the result is wrapped in `=== untrusted web page content ===` markers
  with a line saying it is data, not instructions (the end marker is stripped from the page text so
  it can't close the block early; any case/spacing of the markers is stripped, and title/url are
  flattened to one line the same way); the tool description and the system prompt say the same.
- **size.** default 15000 characters, max 40000 (`max_chars`); a longer page says which `start`
  value reads on.

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

### 8b. presence + typing indicator (`bot/rtm.py`)
bolt/socket mode can't show the green dot or "typing...", the real web client does both over
its own (undocumented) websocket. with `SLACK_WEB_TOKEN` (xoxc) + `SLACK_WEB_COOKIE` (the `d`
cookie), both of **noodle's own account**, `start_rtm()` (from `build_app()`) calls the web api
`client.getWebSocketURL`, connects with `?token=` + `cookie: d=`, pings every 40s, follows
`reconnect_url`, and reconnects on its own: being connected = online. `rtm.typing(channel,
thread_ts)` is a context manager sending `{"type":"typing"}` every 2.5s while it runs;
`handlers._process_locked()` wraps `ask_noodle()` in it, so it shows during slow tool calls.
the web session is used ONLY for the websocket (ping + typing): every message noodle sends still goes through the normal bolt token. if `getWebSocketURL` or the handshake fails with an auth error (`invalid_auth`, `token_expired`, 401/403...), `_report_expired()` dms matthias once per outage (persona-written, via `_post_dm`), then retries every 5 minutes; a successful connect resets the flag. without the credentials (`features.enabled("typing")`) everything is a silent no-op. all
failures are logged and never break replies.

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
| `SLACK_USER_TOKEN` | token (xoxp-...) of noodle's own dedicated slack account (not matthias's) |
| `SLACK_APP_TOKEN` | socket mode app token (xapp-...) |
| `SLACK_WEB_TOKEN` / `SLACK_WEB_COOKIE` | optional: noodle's web session (`xoxc-` token + `d` cookie) for presence + typing, see 8b. both unset = off |
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
| `MATTHIAS_DAY_GROUP_ID` | usergroup id for `@matthias-day` (defaults to the real id, `S0AK8LYJ8FR`) |
| `MATTHIAS_DAY_CHANNEL_ID` | channel the daily recap pings are posted in (defaults to `#matthias`, `C0A9NJT2BCJ`) |
| `FREEDOM_API_BASE` | base url of matthias's self-hosted freedom api (defaults to `https://freedom.matthiaz.dev`) |
| `FREEDOM_API_KEY` | bearer key for that api; optional - unset just disables `start_freedom_session` |
| `FREEDOM_DEVICE_NAMES` | comma separated device names (as shown in freedom's dashboard) allowed to be blocked - the account is shared, so this is never "all devices" (defaults to `Matthias Windows,mopa5m`) |
| `DAILY_WRAPUP_DM` | `HH:MM`, end-of-day wrap-up dm (default `21:30`) |
| `WEEKLY_JOURNAL` / `WEEKLY_MUSIC` | `<mon..sun> HH:MM`, weekly journal recap (`sun 18:00`) / music recap (`sun 18:30`) |
| `LINKS_API_BASE` / `LINKS_API_KEY` | link shortener api base url (no default, e.g. `https://go.example.com/api/v1`) + its `lnk_...` key; either unset = `links` tool off |
| `SANDBOX_TOKEN` | shared secret between noodle and the code sandbox (16+ random chars); compose sets `SANDBOX_URL` itself. **unset = sandbox off**: `run_code` is not offered to the model and the container idles |
| `MATTHIAS_USER_TOKEN` | matthias's OWN xoxp token (`search:read`), used only to count today's messages incl. private channels - nothing else |
| `QUIET_HOURS` / `PROACTIVE_DAILY_CAP` | no unprompted pings in `HH:MM-HH:MM` (default `23:00-07:30`), max per day (default 10) |
| `WEATHER_LOCATIONS` / `WEATHER_DEFAULT_LOCATION` | PRIVATE, no defaults in code: morning dm places (comma separated) / get_weather default place |
| `LASTFM_API_KEY` / `LASTFM_USER` | last.fm key + username; optional, enables `now_playing` |
| `GITHUB_TOKEN` / `GITHUB_USER` / `GITHUB_POLL_SECONDS` | classic token (`repo`, `notifications`); optional, enables the github tool + watcher |
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
