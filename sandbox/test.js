// `node --test sandbox/test.js` (from the repo root) or `npm test` in sandbox/.
// starts the real service on a private port and drives the bash tier over http.
const test = require("node:test");
const assert = require("node:assert");
const { spawn } = require("node:child_process");
const path = require("node:path");

const TOKEN = "test-token-0123456789abcdef";
const PORT = 18000 + Math.floor(Math.random() * 1000);
let server;

async function post(body, token = TOKEN) {
  const res = await fetch(`http://127.0.0.1:${PORT}/run`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return { status: res.status, ...(await res.json()) };
}
const sh = (code, extra = {}) => post({ language: "bash", code, ...extra });

test.before(async () => {
  server = spawn(process.execPath, [path.join(__dirname, "server.js")], {
    env: { SANDBOX_TOKEN: TOKEN, SANDBOX_PORT: String(PORT), PATH: process.env.PATH },
    stdio: "ignore",
  });
  for (let i = 0; i < 100; i++) {
    try {
      if ((await fetch(`http://127.0.0.1:${PORT}/health`)).ok) return;
    } catch (_) {}
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error("sandbox did not start");
});
test.after(() => server && server.kill("SIGKILL"));

test("auth is required", async () => {
  assert.strictEqual((await sh("echo hi")).status, 200); // sanity: right token works
  assert.strictEqual((await post({ language: "bash", code: "echo hi" }, "wrong-token-wrong-token")).status, 401);
});

test("pipes, jq and csv tools work on input files", async () => {
  const r = await sh("jq -r .name cfg.json; xan count data.csv | tr -d ' '; echo hi > /out/hi.txt", {
    reset: true,
    files: { "cfg.json": '{"name":"noodle"}', "data.csv": "a,b\n1,2\n3,4\n" },
  });
  assert.strictEqual(r.ok, true, JSON.stringify(r));
  assert.strictEqual(r.stdout, "noodle\n2");
  assert.deepStrictEqual(r.files.map((f) => f.name), ["hi.txt"]);
});

test("/work persists between runs until reset", async () => {
  await sh("echo keep > notes.txt", { reset: true, workspace: "t1" });
  assert.strictEqual((await sh("cat notes.txt", { workspace: "t1" })).stdout, "keep");
  assert.strictEqual((await sh("cat notes.txt", { workspace: "other" })).stdout, "");
  const r = await sh("ls; cat notes.txt", { workspace: "t1", reset: true });
  assert.strictEqual(r.stdout, "");
});

test("a non-zero exit code is reported", async () => {
  const r = await sh("grep nothing /dev/null");
  assert.strictEqual(r.ok, false);
  assert.strictEqual(r.exit_code, 1);
});

test("there is no real host to reach", async () => {
  const r = await sh("cat /etc/passwd; cat /proc/self/environ; ls /app; echo \"[$SANDBOX_TOKEN][$PATH]\"; env");
  assert.ok(!/root:/.test(r.stdout + r.stderr), "host /etc/passwd leaked");
  assert.ok(!r.stdout.includes(TOKEN), "token leaked");
  assert.ok(/No such file/.test(r.stderr));
  assert.ok(!/server\.js/.test(r.stdout));
});

test("no real processes, no network, no real binaries", async () => {
  const r = await sh("node -e 1; curl http://1.1.1.1; git status; apt list; sh -c 'id'; /bin/sh -c id; ssh x");
  assert.ok(!/uid=/.test(r.stdout), "ran a real process");
  assert.ok(/uses js-exec instead of node/.test(r.stderr), r.stderr); // `node` is only a pointer to js-exec
  assert.ok(/curl: command not found/.test(r.stderr), r.stderr);
  assert.ok(/git: command not found/.test(r.stderr), r.stderr);
});

test("python3, js-exec and sqlite3 work inside the shell, with no network and no host", async () => {
  const r = await sh(
    [
      'python3 -c "print(sum(range(10)))"',
      'js-exec -c "console.log([1,2,3].map(x=>x*2).join())"',
      'sqlite3 :memory: "select 40+2"',
      "python3 - <<'PY'",
      "import os, socket, subprocess",
      "print(sorted(os.listdir('/')))",
      "for name, f in [('net', lambda: socket.create_connection(('1.1.1.1', 80), 2)), ('exec', lambda: subprocess.run(['id'], capture_output=True)), ('host', lambda: open('/etc/passwd').read())]:",
      "    try:",
      "        f(); print(name, 'ALLOWED')",
      "    except Exception as e:",
      "        print(name, 'denied')",
      "PY",
    ].join("\n"),
    { timeout_ms: 30000 }
  );
  assert.strictEqual(r.ok, true, JSON.stringify(r));
  const lines = r.stdout.split("\n");
  assert.deepStrictEqual(lines.slice(0, 3), ["45", "2,4,6", "42"]);
  assert.ok(!/ALLOWED/.test(r.stdout), r.stdout);
  assert.ok(/net denied/.test(r.stdout) && /host denied/.test(r.stdout), r.stdout);
});

test("a repo fetched by noodle's own code lands in /work and persists", async () => {
  const mk = (path, text) => ({ path, data: Buffer.from(text).toString("base64") });
  const r = await sh("ls demo; cat demo/src/a.txt", {
    reset: true,
    repo_files: [mk("demo/README.md", "hi"), mk("demo/src/a.txt", "from the repo")],
  });
  assert.strictEqual(r.ok, true, JSON.stringify(r));
  assert.strictEqual(r.stdout, "README.md\nsrc\nfrom the repo");
  assert.strictEqual((await sh("wc -c < demo/README.md")).stdout.trim(), "2");
  // a second fetch replaces the old copy instead of merging into it
  const r2 = await sh("ls demo", { repo_files: [mk("demo/new.txt", "x")] });
  assert.strictEqual(r2.stdout, "new.txt");
  for (const bad of [[mk("../x", "y")], [mk("/etc/x", "y"), { path: "demo/b", data: 5 }]]) {
    assert.strictEqual((await sh("ls", { repo_files: bad })).ok, false);
  }
});

test("runaway scripts are stopped", async () => {
  const loop = await sh("while true; do :; done", { timeout_ms: 3000 });
  assert.strictEqual(loop.ok, false);
  const rec = await sh(":(){ :|:& };:");
  assert.strictEqual(rec.ok, false);
  const t0 = Date.now();
  const slow = await sh("sleep 30", { timeout_ms: 1000 });
  assert.strictEqual(slow.ok, false);
  assert.ok(Date.now() - t0 < 6000, "wall clock kill took too long");
});

test("huge output and files are capped", async () => {
  const r = await sh("yes aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa | head -n 200000");
  assert.ok(r.stdout.length <= 20000);
  assert.strictEqual(r.truncated, true);
  const f = await sh("for i in 1 2 3 4 5 6 7 8; do echo $i > /out/f$i.txt; done");
  assert.ok(f.files.length <= 5);
});

test("input file names cannot escape /work", async () => {
  for (const name of ["../x", "/etc/x", "a/../../x", ""]) {
    const r = await sh("ls", { files: { [name]: "y" } });
    if (name === "/etc/x") assert.ok(r.ok, "absolute names are made relative to /work"); // lands in /work/etc/x
    else assert.strictEqual(r.ok, false, `accepted ${JSON.stringify(name)}`);
  }
  const big = await sh("ls", { files: { a: "x".repeat(1024 * 1024 + 1) } });
  assert.strictEqual(big.ok, false);
});

test("workers inherit the permission model (the probe the server itself runs at startup)", async () => {
  const probe = `
    const { Worker } = require("worker_threads");
    const w = new Worker(\`
      const fs = require("fs"); const r = [];
      for (const f of [() => fs.writeFileSync("/tmp/noodle-pwn", "x"), () => fs.readFileSync("/etc/passwd"), () => require("child_process").execSync("id")]) {
        try { f(); r.push("allowed"); } catch (e) { r.push("denied"); } }
      require("worker_threads").parentPort.postMessage(r.join());\`, { eval: true, env: {} });
    w.on("message", (m) => console.log(m));`;
  const out = await new Promise((resolve) => {
    const p = spawn(process.execPath, ["--permission", `--allow-fs-read=${__dirname}`, "--allow-worker", "--disable-warning=SecurityWarning", "-e", probe], { env: {} });
    let s = "";
    p.stdout.on("data", (d) => (s += d));
    p.on("exit", () => resolve(s));
  });
  assert.strictEqual(out.trim(), "denied,denied,denied");
});

test("the job process is under the node permission model", async () => {
  // what a bug in the shell that reached host js would still hit: this is the same
  // flag set server.js uses for every job, checked from the outside
  const probe = `
    const t = {
      write: () => require("fs").writeFileSync("/tmp/pwn", "x"),
      spawn: () => require("child_process").execSync("id"),
      worker: () => new (require("worker_threads").Worker)("1", { eval: true }),
    };
    for (const [k, f] of Object.entries(t)) { try { f(); console.log(k + ":allowed"); } catch (e) { console.log(k + ":denied"); } }`;
  const out = await new Promise((resolve) => {
    const p = spawn(process.execPath, ["--permission", `--allow-fs-read=${__dirname}`, "-e", probe], { env: {} });
    let s = "";
    p.stdout.on("data", (d) => (s += d));
    p.on("exit", () => resolve(s));
  });
  assert.strictEqual(out.trim(), "write:denied\nspawn:denied\nworker:denied");
});
