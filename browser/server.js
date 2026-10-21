// the browser service. noodle's bot talks to this over an internal docker network (no
// published port); it drives one headless chromium session and returns pages as text.
//
//   POST /act   {action: open|read|click|type|back|screenshot|close, url?, index?, text?,
//                submit?, offset?, full_page?}  (bearer auth)
//   GET  /health
//
// layers: chromium only talks through the egress filter in proxy.js (public addresses
// only) -> incognito context, no downloads/permissions -> container (non-root, read-only
// fs, no caps, no secrets, own network that can't reach noodle) -> idle + memory limits.
// see ../AGENTS.md (7j).
const http = require("http");
const crypto = require("crypto");
const { createProxy } = require("./proxy");
const { Session } = require("./session");

const PORT = parseInt(process.env.BROWSER_PORT || "8080", 10);
const PROXY_PORT = parseInt(process.env.BROWSER_PROXY_PORT || "3128", 10);
const TOKEN = process.env.BROWSER_TOKEN || "";
const MAX_BODY = 20 * 1024;
const ACTION_DEADLINE_MS = 60000;

// no token (or one shorter than 16 chars) = switched off: only /health answers and chromium is
// never started, so it idles at almost no memory. noodle doesn't offer the tool without the token.
const DISABLED = TOKEN.length < 16;
if (DISABLED) console.log("BROWSER_TOKEN not set (needs 16+ characters): browser is disabled, idling");

const proxy = createProxy({ log: (m) => console.log(m) });
const session = new Session({ proxyPort: PROXY_PORT, executablePath: process.env.BROWSER_EXECUTABLE });

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
        reject(Object.assign(new Error("request too large"), { status: 413 }));
        req.destroy();
      } else chunks.push(c);
    });
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

// a hung page must never wedge the service: past the deadline the session is killed and restarted
function withDeadline(promise) {
  let timer;
  const deadline = new Promise((_, reject) => {
    timer = setTimeout(() => {
      session.close();
      reject(Object.assign(new Error("that took too long, the browser was reset"), { status: 504 }));
    }, ACTION_DEADLINE_MS);
  });
  return Promise.race([promise, deadline]).finally(() => clearTimeout(timer));
}

const server = http.createServer(async (req, res) => {
  try {
    if (req.method === "GET" && req.url === "/health") return send(res, 200, DISABLED ? { ok: true, disabled: true } : { ok: true });
    if (req.method !== "POST" || req.url !== "/act") return send(res, 404, { error: "not found" });
    if (DISABLED) return send(res, 503, { error: "the browser is disabled (no BROWSER_TOKEN set)" });
    if (!authorized(req)) return send(res, 401, { error: "unauthorized" });

    let body;
    try {
      body = JSON.parse(await readBody(req));
    } catch (e) {
      return send(res, e.status || 400, { error: e.status ? e.message : "bad json" });
    }
    const t0 = Date.now();
    const result = await withDeadline(session.act(body || {}));
    console.log(`act ${body.action} -> ${result.ok ? "ok" : "error"} in ${Date.now() - t0}ms`);
    return send(res, 200, { duration_ms: Date.now() - t0, ...result });
  } catch (e) {
    return send(res, e.status || 500, { error: e.message || "internal error" });
  }
});

// the egress filter listens on loopback only: chromium (same container) is its one client
if (!DISABLED) proxy.listen(PROXY_PORT, "127.0.0.1", () => console.log(`egress filter on 127.0.0.1:${PROXY_PORT}`));
server.listen(PORT, "0.0.0.0", () => console.log(`browser listening on :${PORT}`));
for (const sig of ["SIGTERM", "SIGINT"]) {
  process.on(sig, async () => {
    await session.close();
    process.exit(0);
  });
}
