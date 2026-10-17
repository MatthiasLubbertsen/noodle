// one-shot job executor. runs in its own node process, started by server.js
// with `node --permission` (no file writes, no child processes, no workers,
// no native addons), killed after exactly one job. the container around it has
// no network, no secrets and a read-only filesystem.
//
// protocol over node's IPC channel: parent sends {language, code, timeoutMs},
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

// ---------------------------------------------------------------- main
const language = process.argv[2];
(async () => {
  if (language === "python") await loadPython();
  process.send({ ready: true });
  process.once("message", async (job) => {
    let result;
    try {
      result = language === "python" ? await runPython(job.code) : await runJavaScript(job.code, job.timeoutMs);
    } catch (e) {
      result = { stdout: "", stderr: "", result: null, error: `runner error: ${e && e.message}`, files: [], truncated: false };
    }
    process.send({ done: result }, () => process.exit(0));
  });
})().catch((e) => {
  process.send({ fatal: String(e && e.message ? e.message : e) }, () => process.exit(1));
});
