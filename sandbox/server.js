// the runner service. noodle's bot talks to this over an internal docker network
// (no published port); it runs python (pyodide) and javascript (quickjs) snippets
// in throwaway node processes and returns stdout / result / output files.
//
//   POST /run   {language: "python"|"javascript"|"bash", code, timeout_ms?,
//                files?: {name: text}, workspace?: key, reset?: bool}     (bearer auth)
//   GET  /health
//
// isolation layers, innermost first: wasm (no syscalls) -> node --permission (no
// file writes, no child processes, no workers) -> one process per job, killed
// after it -> container (non-root, read-only fs, no network, no secrets, caps
// dropped, memory/pids limits). see ../AGENTS.md for the full picture.
const http = require("http");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { fork } = require("child_process");

const PORT = parseInt(process.env.SANDBOX_PORT || "8080", 10);
const TOKEN = process.env.SANDBOX_TOKEN || "";
const DEFAULT_TIMEOUT_MS = 20000;
const MAX_TIMEOUT_MS = 60000;
const MAX_CODE = 200 * 1024;
const MAX_BODY = 1.5 * 1024 * 1024; // code (MAX_CODE) + bash input files (1MB), checked separately
const MAX_RSS_MB = parseInt(process.env.SANDBOX_MAX_RSS_MB || "900", 10);
const READY_TIMEOUT_MS = 120000;
const CHILD = path.join(__dirname, "child.js");

// no token (or one shorter than 16 chars) = the sandbox is switched off: the service still
// answers /health but never runs anything and never loads python, so it idles at almost no
// memory instead of crash-looping. noodle doesn't offer run_code without the same token.
const DISABLED = TOKEN.length < 16;
if (DISABLED) console.log("SANDBOX_TOKEN not set (needs 16+ characters): sandbox is disabled, idling");

function spawnChild(language) {
  const proc = fork(CHILD, [language], {
    cwd: __dirname,
    env: {}, // nothing from this process leaks into the job
    execArgv: ["--permission", `--allow-fs-read=${__dirname}`, "--max-old-space-size=512"],
    stdio: ["ignore", "ignore", "ignore", "ipc"],
  });
  const child = { proc, language, exited: false, isReady: false, rejectReady: null };
  child.ready = new Promise((resolve, reject) => {
    child.rejectReady = reject;
    const timer = setTimeout(() => reject(new Error("interpreter start timed out")), READY_TIMEOUT_MS);
    proc.once("message", (m) => {
      clearTimeout(timer);
      if (m && m.ready) {
        child.isReady = true;
        resolve();
      }
      else reject(new Error((m && m.fatal) || "interpreter failed to start"));
    });
    proc.once("exit", () => {
      clearTimeout(timer);
      reject(new Error("interpreter exited during start"));
    });
  });
  child.ready.catch(() => {}); // handled by whoever awaits it
  proc.once("exit", () => (child.exited = true));
  return child;
}

function kill(child) {
  if (child && !child.exited) {
    try {
      child.proc.kill("SIGKILL");
    } catch (_) {}
  }
}

function rssMb(pid) {
  try {
    const m = /VmRSS:\s+(\d+) kB/.exec(fs.readFileSync(`/proc/${pid}/status`, "utf8"));
    return m ? parseInt(m[1], 10) / 1024 : 0;
  } catch (_) {
    return 0;
  }
}

// ---- python: one warm, pristine interpreter; each job consumes it and a fresh one warms up behind it
let warmPython = null;
function warmUp() {
  warmPython = spawnChild("python");
  warmPython.ready.catch((e) => {
    console.error("python warm-up failed:", e.message);
    setTimeout(warmUp, 5000);
  });
}

// at most `max` jobs of a kind run at once, `maxWaiting` more may queue, the rest get a 429
function makeLimiter(max, maxWaiting) {
  let running = 0;
  const queue = [];
  return function limited(fn) {
    return new Promise((resolve, reject) => {
      const go = () => {
        running++;
        fn().then(resolve, reject).finally(() => {
          running--;
          const next = queue.shift();
          if (next) next();
        });
      };
      if (running < max) return go();
      if (queue.length >= maxWaiting) return reject(Object.assign(new Error("sandbox is busy, try again in a moment"), { status: 429 }));
      queue.push(go);
    });
  };
}
const withPythonLock = makeLimiter(1, 2);
const withBashLock = makeLimiter(2, 2);

function failure(error, extra = {}) {
  return { stdout: "", stderr: "", result: null, error, files: [], truncated: false, ...extra };
}

async function execute(child, job, timeoutMs, graceMs = 0) {
  // child is ready; run the one job with a hard wall-clock kill + memory watchdog
  return new Promise((resolve) => {
    let settled = false;
    const finish = (res) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      clearInterval(watchdog);
      kill(child);
      resolve(res);
    };
    const timer = setTimeout(() => finish(failure(`timed out after ${Math.round(timeoutMs / 1000)}s`, { timed_out: true })), timeoutMs + graceMs);
    const watchdog = setInterval(() => {
      if (rssMb(child.proc.pid) > MAX_RSS_MB) finish(failure("ran out of memory", { out_of_memory: true }));
    }, 200);
    child.proc.on("message", (m) => {
      if (m && m.done) finish(m.done);
      else if (m && m.fatal) finish(failure(`runner error: ${m.fatal}`));
    });
    child.proc.once("exit", () => finish(failure("the interpreter crashed")));
    child.proc.send({ ...job, timeoutMs });
  });
}

async function runPython(code, timeoutMs) {
  return withPythonLock(async () => {
    if (!warmPython) warmUp();
    const child = warmPython;
    warmPython = null;
    try {
      await child.ready;
    } catch (e) {
      kill(child);
      warmUp();
      return failure(`python isn't available right now (${e.message})`);
    }
    try {
      return await execute(child, { code }, timeoutMs);
    } finally {
      kill(child);
      warmUp(); // fresh interpreter for the next job, so nothing leaks between runs
    }
  });
}

async function runJavaScript(code, timeoutMs) {
  const child = spawnChild("javascript");
  try {
    await child.ready;
  } catch (e) {
    kill(child);
    return failure(`javascript isn't available right now (${e.message})`);
  }
  return execute(child, { code }, timeoutMs, 2000); // quickjs interrupts itself at timeoutMs first
}

// ---- bash: a throwaway process per job, plus a small persistent workspace
// the shell's /work directory survives between runs: the child returns its files, they are kept
// here (in memory only, never on disk) and handed to the next job. a fresh process each time
// means no interpreter state leaks - only files do. bounded: 4 workspaces, 4MB / 200 files each,
// dropped after 30 idle minutes.
const WORKSPACE_TTL_MS = 30 * 60 * 1000;
const MAX_WORKSPACES = 4;
const MAX_INPUT_FILES = 50;
const MAX_INPUT_BYTES = 1024 * 1024;
const workspaces = new Map(); // key -> {files: [{path, data}], touched}

function workspaceFor(key) {
  const now = Date.now();
  for (const [k, w] of workspaces) if (now - w.touched > WORKSPACE_TTL_MS) workspaces.delete(k);
  return workspaces.get(key);
}

function safeRelPath(name) {
  if (typeof name !== "string") return null;
  const norm = path.posix.normalize(name.replace(/\\/g, "/").replace(/^\/+/, ""));
  if (!norm || norm === "." || norm.startsWith("../") || norm === ".." || norm.length > 200 || norm.includes("\0")) return null;
  return norm;
}

async function runBash(body, timeoutMs) {
  const key = typeof body.workspace === "string" && /^[\w:.-]{1,64}$/.test(body.workspace) ? body.workspace : "default";
  if (body.reset) workspaces.delete(key);
  const prior = workspaceFor(key);

  const merged = new Map((prior ? prior.files : []).map((f) => [f.path, f.data]));
  if (body.files && typeof body.files === "object") {
    const entries = Object.entries(body.files);
    if (entries.length > MAX_INPUT_FILES) return failure(`too many input files (max ${MAX_INPUT_FILES})`);
    let bytes = 0;
    for (const [name, text] of entries) {
      const rel = safeRelPath(name);
      if (!rel || typeof text !== "string") return failure(`bad input file name: ${String(name).slice(0, 60)}`);
      bytes += Buffer.byteLength(text);
      if (bytes > MAX_INPUT_BYTES) return failure("input files are too big (max 1MB in total)");
      merged.set(rel, Buffer.from(text, "utf8").toString("base64"));
    }
  }
  const files = [...merged].map(([p, data]) => ({ path: p, data }));

  const child = spawnChild("bash");
  try {
    await child.ready;
  } catch (e) {
    kill(child);
    return failure(`the shell isn't available right now (${e.message})`);
  }
  const result = await execute(child, { code: body.code, files }, timeoutMs, 2000); // the shell stops itself at timeoutMs first
  if (result.workspace) {
    if (!workspaces.has(key) && workspaces.size >= MAX_WORKSPACES) workspaces.delete(workspaces.keys().next().value);
    workspaces.set(key, { files: result.workspace, touched: Date.now() });
  } else if (prior) {
    prior.touched = Date.now(); // timed out / killed: keep what it had
  }
  delete result.workspace;
  return result;
}

// ---- http
function send(res, status, body) {
  const data = JSON.stringify(body);
  res.writeHead(status, { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(data) });
  res.end(data);
}

function authorized(req) {
  const given = Buffer.from((req.headers.authorization || "").replace(/^Bearer /, ""));
  const want = Buffer.from(TOKEN);
  return given.length === want.length && crypto.timingSafeEqual(given, want);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on("data", (c) => {
      size += c.length;
      if (size > MAX_BODY) {
        reject(Object.assign(new Error("code too large"), { status: 413 }));
        req.destroy();
      } else chunks.push(c);
    });
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

const server = http.createServer(async (req, res) => {
  try {
    if (req.method === "GET" && req.url === "/health") {
      if (DISABLED) return send(res, 200, { ok: true, disabled: true });
      return send(res, 200, { ok: true, python_ready: !!warmPython && !!warmPython.isReady && !warmPython.exited });
    }
    if (req.method !== "POST" || req.url !== "/run") return send(res, 404, { error: "not found" });
    if (DISABLED) return send(res, 503, { error: "the sandbox is disabled (no SANDBOX_TOKEN set)" });
    if (!authorized(req)) return send(res, 401, { error: "unauthorized" });

    let body;
    try {
      body = JSON.parse(await readBody(req));
    } catch (e) {
      return send(res, e.status || 400, { error: e.status ? e.message : "bad json" });
    }
    const { language, code } = body || {};
    if (!["python", "javascript", "bash"].includes(language)) return send(res, 400, { error: "language must be python, javascript or bash" });
    if (typeof code !== "string" || !code.trim()) return send(res, 400, { error: "code is required" });
    if (code.length > MAX_CODE) return send(res, 413, { error: "code too large" });
    const timeoutMs = Math.min(Math.max(parseInt(body.timeout_ms, 10) || DEFAULT_TIMEOUT_MS, 1000), MAX_TIMEOUT_MS);

    const t0 = Date.now();
    const result =
      language === "python"
        ? await runPython(code, timeoutMs)
        : language === "bash"
          ? await withBashLock(() => runBash(body, timeoutMs))
          : await runJavaScript(code, timeoutMs);
    const files = (result.files || []).map((f) => ({ ...f, size: Buffer.byteLength(f.data, "base64") }));
    console.log(`run ${language} ${code.length} chars -> ${result.error ? "error" : "ok"} in ${Date.now() - t0}ms (${files.length} files)`);
    return send(res, 200, { ok: !result.error, language, duration_ms: Date.now() - t0, ...result, files });
  } catch (e) {
    return send(res, e.status || 500, { error: e.message || "internal error" });
  }
});

server.listen(PORT, "0.0.0.0", () => console.log(`sandbox listening on :${PORT}`));
if (!DISABLED) warmUp();
for (const sig of ["SIGTERM", "SIGINT"]) {
  process.on(sig, () => {
    kill(warmPython);
    process.exit(0);
  });
}
