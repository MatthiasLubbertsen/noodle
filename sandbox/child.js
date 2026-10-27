// one-shot job executor. runs in its own node process, started by server.js
// with `node --permission` (no file writes, no child processes, no workers,
// no native addons), killed after exactly one job. the container around it has
// no network, no secrets and a read-only filesystem.
//
// protocol over node's IPC channel: parent sends {code, timeoutMs, files?}
// (files = [{path, data(base64)}] to put under /work first, bash only),
// child answers with one result object. python children load pyodide + the
// preloaded scientific packages first, then send {ready: true}.
const path = require("path");

// node's permission model disables process.binding(), which pyodide's emscripten
// runtime calls exactly once to read the fs open() flag constants. give it just
// that - nothing else can use process.binding() afterwards.
process.binding = (name) => {
  if (name === "constants") return { fs: require("fs").constants };
  throw new Error("process.binding is disabled");
};

const LIMITS = {
  stdout: 20000, // chars kept per stream
  stderr: 10000,
  files: 5,
  fileBytes: 8 * 1024 * 1024,
  totalFileBytes: 12 * 1024 * 1024,
};
// preloaded into every fresh interpreter. everything else in fetch-packages.js (scipy)
// is loaded on demand when the code imports it - no network involved, it is on local disk.
const PRELOAD = ["numpy", "pandas", "matplotlib", "sympy"];

function capped() {
  const parts = [];
  let size = 0;
  let truncated = false;
  return {
    push(line, max) {
      if (size >= max) return void (truncated = true);
      const room = max - size;
      parts.push(line.length > room ? line.slice(0, room) : line);
      size += Math.min(line.length, room) + 1;
      if (line.length > room) truncated = true;
    },
    text: () => parts.join("\n"),
    get truncated() {
      return truncated;
    },
  };
}

// ---------------------------------------------------------------- python
const PY_SETUP = `
import os, sys
os.environ["MPLBACKEND"] = "Agg"
os.makedirs("/out", exist_ok=True)
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
`;

// best effort only: pyodide can reach the node host through the "js" bridge,
// so these blocks are NOT the boundary (the permission model + container are).
const PY_LOCKDOWN = `
import sys
for _name in ("js", "pyodide_js"):
    sys.modules[_name] = None
`;

const PY_COLLECT = `
import io, base64, json, os
import matplotlib.pyplot as plt
_figs = []
for _n in plt.get_fignums()[:5]:
    _buf = io.BytesIO()
    plt.figure(_n).savefig(_buf, format="png", dpi=150, bbox_inches="tight")
    _figs.append(base64.b64encode(_buf.getvalue()).decode())
plt.close("all")
_files = []
for _name in sorted(os.listdir("/out"))[:5]:
    _p = os.path.join("/out", _name)
    if os.path.isfile(_p) and os.path.getsize(_p) <= ${LIMITS.fileBytes}:
        with open(_p, "rb") as _f:
            _files.append([_name, base64.b64encode(_f.read()).decode()])
json.dumps({"figures": _figs, "files": _files})
`;

const MIME = { png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", svg: "image/svg+xml", csv: "text/csv", txt: "text/plain", json: "application/json", pdf: "application/pdf" };

function cleanTraceback(message) {
  // drop pyodide's own internal frames, keep the user's part of the traceback
  const i = message.indexOf('File "<exec>"');
  if (i === -1) return message.trim();
  return "Traceback (most recent call last):\n  " + message.slice(i).trim();
}

let pyodide = null;
async function loadPython() {
  const { loadPyodide } = require("pyodide");
  const dir = path.dirname(require.resolve("pyodide/package.json"));
  pyodide = await loadPyodide({ indexURL: dir + path.sep });
  await pyodide.loadPackage(PRELOAD);
  await pyodide.runPythonAsync(PY_SETUP);
}

async function runPython(code) {
  const out = capped();
  const err = capped();
  pyodide.setStdout({ batched: (s) => out.push(s, LIMITS.stdout) });
  pyodide.setStderr({ batched: (s) => err.push(s, LIMITS.stderr) });
  let result = null;
  let error = null;
  try {
    await pyodide.loadPackagesFromImports(code, { messageCallback: () => {}, errorCallback: () => {} });
  } catch (e) {
    // a package that isn't bundled: the user's own import will raise a clear ModuleNotFoundError
  }
  await pyodide.runPythonAsync(PY_LOCKDOWN);
  const globals = pyodide.globals.get("dict")();
  try {
    const value = await pyodide.runPythonAsync(code, { globals });
    if (value !== undefined && value !== null) {
      result = String(value).slice(0, LIMITS.stdout);
      if (typeof value.destroy === "function") value.destroy();
    }
  } catch (e) {
    error = cleanTraceback(String(e && e.message ? e.message : e));
  }
  const files = [];
  try {
    const collected = JSON.parse(await pyodide.runPythonAsync(PY_COLLECT.replace("${LIMITS.fileBytes}", String(LIMITS.fileBytes))));
    let total = 0;
    collected.figures.forEach((b64, i) => files.push({ name: `figure${i + 1}.png`, data: b64 }));
    for (const [name, b64] of collected.files) files.push({ name, data: b64 });
    for (const f of files) {
      total += Buffer.byteLength(f.data, "base64");
      f.mime = MIME[f.name.split(".").pop().toLowerCase()] || "application/octet-stream";
    }
    if (total > LIMITS.totalFileBytes || files.length > LIMITS.files * 2) {
      files.length = 0;
      error = (error ? error + "\n" : "") + "output files were too big to return";
    }
  } catch (e) {
    error = (error ? error + "\n" : "") + "couldn't collect output files";
  }
  return { stdout: out.text(), stderr: err.text(), result, error, files, truncated: out.truncated || err.truncated };
}

// ------------------------------------------------------------ javascript
async function runJavaScript(code, timeoutMs) {
  const { getQuickJS, shouldInterruptAfterDeadline } = require("quickjs-emscripten");
  const QuickJS = await getQuickJS();
  const runtime = QuickJS.newRuntime();
  runtime.setMemoryLimit(128 * 1024 * 1024);
  runtime.setMaxStackSize(1024 * 1024);
  runtime.setInterruptHandler(shouldInterruptAfterDeadline(Date.now() + timeoutMs));
  const vm = runtime.newContext();
  const out = capped();
  const err = capped();

  const fmt = (v) => (typeof v === "string" ? v : JSON.stringify(v, null, 0) ?? String(v));
  const consoleObj = vm.newObject();
  for (const [name, sink, max] of [["log", out, LIMITS.stdout], ["info", out, LIMITS.stdout], ["warn", err, LIMITS.stderr], ["error", err, LIMITS.stderr]]) {
    const fn = vm.newFunction(name, (...args) => sink.push(args.map((a) => fmt(vm.dump(a))).join(" "), max));
    vm.setProp(consoleObj, name, fn);
    fn.dispose();
  }
  vm.setProp(vm.global, "console", consoleObj);
  consoleObj.dispose();

  let result = null;
  let error = null;
  const res = vm.evalCode(code, "script.js");
  if (res.error) {
    const e = vm.dump(res.error);
    error = e && e.message ? `${e.name || "Error"}: ${e.message}${e.stack ? "\n" + e.stack : ""}` : fmt(e);
    res.error.dispose();
  } else {
    const v = vm.dump(res.value);
    if (v !== undefined) result = fmt(v).slice(0, LIMITS.stdout);
    res.value.dispose();
  }
  // let promise callbacks (then/await-less async code) run
  const jobs = runtime.executePendingJobs();
  if (jobs.error) jobs.error.dispose();
  vm.dispose();
  runtime.dispose();
  return { stdout: out.text(), stderr: err.text(), result, error, files: [], truncated: out.truncated || err.truncated };
}

// ------------------------------------------------------------------ bash
// a simulated shell (just-bash): bash syntax + ~80 commands (grep sed awk jq sort tar gzip
// xan yq diff find ...) re-implemented in javascript on an in-memory filesystem. there is no
// exec(): nothing here can start a real process, read a real file or open a socket - there
// IS no real process to start. it is JS in this node process though, not a vm, so the
// real boundary against a bug in it is what surrounds it: `node --permission` (no fs writes,
// no child processes, no workers), then the container (no network, no secrets, read-only fs).
// network, python3, js-exec and sqlite3 stay off: each is extra surface (python/js-exec/sqlite
// run in node workers, which the permission model denies on purpose).
const WORK = "/work";
const WORKSPACE = { files: 200, bytes: 4 * 1024 * 1024 };

async function runBash(job) {
  const { Bash, InMemoryFs } = await import("just-bash");
  const fs = new InMemoryFs(undefined, { maxTotalBytes: 32 * 1024 * 1024 });
  await fs.mkdir(WORK, { recursive: true });
  await fs.mkdir("/out", { recursive: true });
  for (const f of job.files || []) {
    const abs = path.posix.join(WORK, f.path);
    await fs.mkdir(path.posix.dirname(abs), { recursive: true });
    await fs.writeFile(abs, Buffer.from(f.data, "base64"));
  }
  const bash = new Bash({
    fs,
    cwd: WORK,
    env: { HOME: WORK, USER: "sandbox" },
    executionLimitProfile: "hardened",
    executionLimits: {
      maxExecutionTimeMs: job.timeoutMs,
      maxOutputSize: 2 * 1024 * 1024,
      maxFileSystemBytes: 32 * 1024 * 1024,
    },
    defenseInDepth: true,
  });
  const out = capped();
  const err = capped();
  let exitCode = null;
  let error = null;
  try {
    const r = await bash.exec(job.code, { signal: AbortSignal.timeout(job.timeoutMs), rawScript: true });
    exitCode = r.exitCode;
    for (const line of String(r.stdout).split("\n")) out.push(line, LIMITS.stdout);
    for (const line of String(r.stderr).split("\n")) err.push(line, LIMITS.stderr);
    if (exitCode !== 0) error = `exit code ${exitCode}`;
  } catch (e) {
    error = `shell error: ${e && e.message ? e.message : e}`;
  }

  // deliverables: whatever the script left in /out
  const files = [];
  let total = 0;
  const all = fs.getAllPaths();
  for (const p of all.filter((x) => x.startsWith("/out/")).sort()) {
    if (files.length >= LIMITS.files) break;
    const st = await fs.stat(p).catch(() => null);
    if (!st || !st.isFile || st.size > LIMITS.fileBytes) continue;
    const name = p.slice(5);
    if (name.includes("/")) continue;
    const buf = Buffer.from(await fs.readFileBuffer(p));
    total += buf.length;
    files.push({ name, data: buf.toString("base64"), mime: MIME[name.split(".").pop().toLowerCase()] || "application/octet-stream" });
  }
  if (total > LIMITS.totalFileBytes) {
    files.length = 0;
    error = (error ? error + "\n" : "") + "output files were too big to return";
  }

  // the workspace: everything under /work, handed back so the next run can start from it
  let workspace = [];
  let workspaceBytes = 0;
  let workspaceDropped = false;
  for (const p of all.filter((x) => x.startsWith(WORK + "/")).sort()) {
    const st = await fs.stat(p).catch(() => null);
    if (!st || !st.isFile) continue;
    const buf = Buffer.from(await fs.readFileBuffer(p));
    workspaceBytes += buf.length;
    if (workspace.length >= WORKSPACE.files || workspaceBytes > WORKSPACE.bytes) {
      workspaceDropped = true;
      break;
    }
    workspace.push({ path: p.slice(WORK.length + 1), data: buf.toString("base64") });
  }
  if (workspaceDropped) workspace = null; // too big to keep: the parent keeps the previous one

  return {
    stdout: out.text().replace(/\n+$/, ""),
    stderr: err.text().replace(/\n+$/, ""),
    result: null,
    error,
    exit_code: exitCode,
    files,
    workspace,
    workspace_dropped: workspaceDropped,
    truncated: out.truncated || err.truncated,
  };
}

// ---------------------------------------------------------------- main
const language = process.argv[2];
(async () => {
  if (language === "python") await loadPython();
  process.send({ ready: true });
  process.once("message", async (job) => {
    let result;
    try {
      if (language === "python") result = await runPython(job.code);
      else if (language === "bash") result = await runBash(job);
      else result = await runJavaScript(job.code, job.timeoutMs);
    } catch (e) {
      result = { stdout: "", stderr: "", result: null, error: `runner error: ${e && e.message}`, files: [], truncated: false };
    }
    process.send({ done: result }, () => process.exit(0));
  });
})().catch((e) => {
  process.send({ fatal: String(e && e.message ? e.message : e) }, () => process.exit(1));
});
