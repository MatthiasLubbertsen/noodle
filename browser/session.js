// one browsing session: a single incognito chromium context with one active page, driven
// by small actions (open / read / click / type / back / screenshot / close). pages are
// returned as text plus a numbered list of the things you can click or type into, so a
// model without vision can still use them; screenshots are an extra, sent to the dm.
const { chromium } = require("playwright-core");

const NAV_TIMEOUT_MS = 25000;
const ACTION_TIMEOUT_MS = 8000;
const TEXT_PAGE = 6000; // characters of page text per read
const MAX_ELEMENTS = 80;
const IDLE_CLOSE_MS = 5 * 60 * 1000;
const MAX_RSS_MB = 1400;
const ALLOWED_SCHEMES = new Set(["http:", "https:", "data:", "blob:", "about:"]);

function checkUrl(raw) {
  let url;
  try {
    url = new URL(/^[a-z][a-z0-9+.-]*:/i.test(raw) ? raw : `https://${raw}`);
  } catch (_) {
    throw new Error("that doesn't look like a url");
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") throw new Error("only http and https pages can be opened");
  return url.toString();
}

// runs inside the page: tags every visible interactive element with data-nid and describes it
function snapshotInPage(max) {
  for (const old of document.querySelectorAll("[data-nid]")) old.removeAttribute("data-nid");
  const sel = "a[href],button,input,select,textarea,summary,[role=button],[role=link],[role=tab],[role=menuitem],[onclick]";
  const out = [];
  for (const el of document.querySelectorAll(sel)) {
    if (out.length >= max) break;
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    if (r.width < 2 || r.height < 2 || st.visibility === "hidden" || st.display === "none" || el.disabled) continue;
    if (el.tagName === "INPUT" && el.type === "hidden") continue;
    const label = (
      el.getAttribute("aria-label") ||
      (el.innerText || "").trim() ||
      el.value ||
      el.placeholder ||
      el.title ||
      el.getAttribute("alt") ||
      el.name ||
      ""
    )
      .replace(/\s+/g, " ")
      .slice(0, 70);
    const id = out.length + 1;
    el.setAttribute("data-nid", String(id));
    const tag = el.tagName.toLowerCase();
    const item = { i: id, tag, label };
    if (tag === "input") item.type = el.type || "text";
    if (tag === "a") {
      try {
        const u = new URL(el.href);
        item.href = (u.host + u.pathname).slice(0, 80);
      } catch (_) {}
    }
    out.push(item);
  }
  return out;
}

class Session {
  constructor({ proxyPort, executablePath }) {
    this.proxyPort = proxyPort;
    this.executablePath = executablePath;
    this.browser = null;
    this.context = null;
    this.page = null;
    this.idleTimer = null;
    this.chain = Promise.resolve(); // one action at a time
  }

  // serialize every action: one page, one user
  act(body) {
    const run = this.chain.then(() => this._act(body));
    this.chain = run.catch(() => {});
    return run;
  }

  async _start() {
    if (this.browser) return;
    this.browser = await chromium.launch({
      executablePath: this.executablePath || undefined,
      headless: true,
      // the container (non-root, no caps, read-only fs) is the sandbox boundary; chromium's own
      // needs user namespaces the locked-down container doesn't have
      chromiumSandbox: false,
      args: [
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--no-first-run",
        "--disable-extensions",
        "--disable-background-networking",
        "--disable-sync",
        "--mute-audio",
        // everything, even loopback, goes through the egress filter
        "--proxy-bypass-list=<-loopback>",
        "--js-flags=--max-old-space-size=512",
      ],
      proxy: { server: `http://127.0.0.1:${this.proxyPort}` },
    });
    this.browser.on("disconnected", () => this._reset());
    // incognito: no cookies, no storage, nothing survives close(). no downloads, no
    // permission prompts (camera, location, notifications...), no service workers.
    this.context = await this.browser.newContext({
      viewport: { width: 1280, height: 800 },
      acceptDownloads: false,
      permissions: [],
      serviceWorkers: "block",
      ignoreHTTPSErrors: false,
      locale: "en-US",
    });
    this.context.setDefaultTimeout(ACTION_TIMEOUT_MS);
    // belt and suspenders next to the url check: nothing but web schemes leaves the browser
    await this.context.route("**/*", (route) => {
      let proto = "";
      try {
        proto = new URL(route.request().url()).protocol;
      } catch (_) {}
      return ALLOWED_SCHEMES.has(proto) ? route.continue() : route.abort();
    });
    this.context.on("page", (p) => this._adopt(p)); // links that open a new tab
    await this._adopt(await this.context.newPage());
  }

  async _adopt(page) {
    this.page = page;
    page.on("dialog", (d) => d.dismiss().catch(() => {}));
    page.on("download", (d) => d.cancel().catch(() => {}));
    page.on("close", () => {
      if (this.page === page) this.page = this.context ? this.context.pages().slice(-1)[0] || null : null;
    });
  }

  _reset() {
    clearTimeout(this.idleTimer);
    this.browser = this.context = this.page = null;
  }

  async close() {
    const browser = this.browser;
    this._reset();
    if (browser) await browser.close().catch(() => {});
  }

  _touch() {
    clearTimeout(this.idleTimer);
    this.idleTimer = setTimeout(() => this.close(), IDLE_CLOSE_MS);
  }

  async _rssTooHigh() {
    // chromium's process tree can balloon on a heavy page; the container limit is the hard wall,
    // this just ends the session cleanly before the oom killer takes the whole service down
    try {
      const fs = require("fs");
      const pid = this.browser && this.browser.process && this.browser.process() && this.browser.process().pid;
      if (!pid) return false;
      const m = /VmRSS:\s+(\d+) kB/.exec(fs.readFileSync(`/proc/${pid}/status`, "utf8"));
      return !!m && parseInt(m[1], 10) / 1024 > MAX_RSS_MB;
    } catch (_) {
      return false;
    }
  }

  async _act(body) {
    const { action } = body;
    if (action === "close") {
      await this.close();
      return { ok: true, closed: true };
    }
    if (!["open", "read", "click", "type", "back", "screenshot"].includes(action)) throw Object.assign(new Error("unknown action"), { status: 400 });
    if (action !== "open" && !this.page) throw Object.assign(new Error("no page is open, open a url first"), { status: 409 });

    await this._start();
    this._touch();
    const page = this.page;
    let shot = null;
    let note = "";

    try {
      if (action === "open") {
        const url = checkUrl(String(body.url || ""));
        const resp = await page.goto(url, { waitUntil: "domcontentloaded", timeout: NAV_TIMEOUT_MS });
        await page.waitForLoadState("networkidle", { timeout: 3000 }).catch(() => {});
        if (resp && resp.status() >= 400) note = `the server answered ${resp.status()}.`;
        // plain-http denials from proxy.js arrive as a 403 page; https ones fail the navigation itself
        if (resp && resp.status() === 403 && (await page.evaluate(() => document.body.innerText)).startsWith("blocked by noodle's browser egress filter")) {
          throw new Error("that address isn't allowed (private or local network addresses are blocked)");
        }
      } else if (action === "back") {
        await page.goBack({ waitUntil: "domcontentloaded", timeout: NAV_TIMEOUT_MS });
      } else if (action === "click" || action === "type") {
        const loc = page.locator(`[data-nid="${parseInt(body.index, 10)}"]`);
        if (!(await loc.count())) throw new Error("no element with that number on the current page, read the page again for fresh numbers");
        if (action === "click") {
          await loc.first().click({ timeout: ACTION_TIMEOUT_MS });
        } else {
          const type = (await loc.first().getAttribute("type")) || "";
          if (type.toLowerCase() === "password") throw new Error("typing into password fields isn't allowed");
          await loc.first().fill(String(body.text ?? ""), { timeout: ACTION_TIMEOUT_MS });
          if (body.submit) await loc.first().press("Enter");
        }
        await page.waitForLoadState("domcontentloaded", { timeout: 5000 }).catch(() => {});
        await page.waitForLoadState("networkidle", { timeout: 2500 }).catch(() => {});
      } else if (action === "screenshot") {
        const png = await page.screenshot({ type: "png", fullPage: !!body.full_page, timeout: 15000 });
        shot = { name: "screenshot.png", data: png.toString("base64") };
      }
    } catch (e) {
      // a failed navigation or click is a normal result for the model to react to, not a 500
      const error = String(e.message || e).split("\n")[0].slice(0, 300);
      const blockedByFilter = /ERR_TUNNEL_CONNECTION_FAILED|ERR_PROXY_CONNECTION_FAILED/.test(error);
      return {
        ok: false,
        error: blockedByFilter ? "couldn't connect: the address is blocked (private/local network) or unreachable" : error,
        ...(await this._view(body, null)),
      };
    }

    const view = await this._view(body, shot);
    if (note) view.note = note;
    if (await this._rssTooHigh()) {
      await this.close();
      view.note = (view.note ? view.note + " " : "") + "that page was heavy, so the browser session was closed to free memory.";
    }
    return { ok: true, ...view };
  }

  // what the model gets to see: url, title, a page of text and the numbered elements
  async _view(body, shot) {
    const page = this.page;
    if (!page) return { url: "", title: "", text: "", elements: [] };
    try {
      const [title, text, elements] = await Promise.all([
        page.title().catch(() => ""),
        page.evaluate(() => (document.body ? document.body.innerText : "")).catch(() => ""),
        page.evaluate(snapshotInPage, MAX_ELEMENTS).catch(() => []),
      ]);
      const clean = text.replace(/\n{3,}/g, "\n\n").trim();
      const offset = Math.max(0, parseInt(body.offset, 10) || 0);
      const chunk = clean.slice(offset, offset + TEXT_PAGE);
      return {
        url: page.url(),
        title: title.slice(0, 200),
        text: chunk,
        text_offset: offset,
        text_total: clean.length,
        more_text: offset + TEXT_PAGE < clean.length,
        elements,
        ...(shot ? { screenshot: shot } : {}),
      };
    } catch (_) {
      return { url: page.url(), title: "", text: "", elements: [] };
    }
  }
}

module.exports = { Session, checkUrl };
