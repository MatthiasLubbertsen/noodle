// `node test.js` (set BROWSER_EXECUTABLE if chromium isn't where playwright expects it).
// checks the egress filter's address rules, then drives a real session against a local page.
const assert = require("assert");
const http = require("http");
const { createProxy, isBlockedAddress } = require("./proxy");
const { Session, checkUrl } = require("./session");

for (const ip of ["127.0.0.1", "10.1.2.3", "172.17.0.2", "192.168.1.1", "169.254.169.254", "100.64.0.1", "::1", "fd00::1", "fe80::1", "::ffff:127.0.0.1", "0.0.0.0"]) {
  assert(isBlockedAddress(ip), `${ip} should be blocked`);
}
for (const ip of ["1.1.1.1", "8.8.8.8", "93.184.216.34", "2606:4700:4700::1111"]) assert(!isBlockedAddress(ip), `${ip} should be allowed`);
assert.throws(() => checkUrl("file:///etc/passwd"));
assert.throws(() => checkUrl("javascript:alert(1)"));
assert.strictEqual(checkUrl("example.com"), "https://example.com/");
console.log("address + url rules ok");

const page = `<!doctype html><title>hello</title><body><h1>noodle test</h1>
<a href="/two">go to two</a><input placeholder="your name"><input type="password" placeholder="secret">
<button onclick="document.title='clicked'">press me</button>`;
const site = http.createServer((req, res) => {
  res.setHeader("Content-Type", "text/html");
  res.end(req.url === "/two" ? "<title>two</title><p>second page</p>" : page);
});

(async () => {
  await new Promise((r) => site.listen(0, "127.0.0.1", r));
  const target = `http://127.0.0.1:${site.address().port}/`;

  // 1) the real filter refuses a loopback target
  const strict = createProxy();
  await new Promise((r) => strict.listen(3199, "127.0.0.1", r));
  const blockedSession = new Session({ proxyPort: 3199, executablePath: process.env.BROWSER_EXECUTABLE });
  const denied = await blockedSession.act({ action: "open", url: target });
  assert.strictEqual(denied.ok, false, "loopback must be refused: " + JSON.stringify(denied));
  await blockedSession.close();
  strict.close();
  console.log("loopback refused ok");

  // 2) with the filter relaxed, the session itself works
  const lax = createProxy({ allowPrivate: true });
  await new Promise((r) => lax.listen(3198, "127.0.0.1", r));
  const s = new Session({ proxyPort: 3198, executablePath: process.env.BROWSER_EXECUTABLE });
  let r = await s.act({ action: "open", url: target });
  assert(r.ok && r.title === "hello" && r.text.includes("noodle test"), JSON.stringify(r));
  const link = r.elements.find((e) => e.label === "go to two");
  const pw = r.elements.find((e) => e.type === "password");
  const name = r.elements.find((e) => e.placeholder === undefined && e.type === "text");
  assert(link && pw && name, JSON.stringify(r.elements));
  r = await s.act({ action: "type", index: pw.i, text: "x" });
  assert(!r.ok && /password/.test(r.error), "password typing must be refused");
  r = await s.act({ action: "type", index: name.i, text: "matthias" });
  assert(r.ok);
  r = await s.act({ action: "click", index: r.elements.find((e) => e.label === "press me").i });
  assert(r.ok && r.title === "clicked", JSON.stringify(r));
  r = await s.act({ action: "click", index: r.elements.find((e) => e.label === "go to two").i });
  assert(r.ok && r.title === "two" && r.text.includes("second page"), JSON.stringify(r));
  r = await s.act({ action: "back" });
  assert(r.ok && r.title === "clicked" || r.title === "hello");
  r = await s.act({ action: "screenshot" });
  assert(r.ok && r.screenshot && r.screenshot.data.length > 1000);
  r = await s.act({ action: "open", url: "file:///etc/passwd" });
  assert(!r.ok && /http/.test(r.error));
  r = await s.act({ action: "click", index: 999 });
  assert(!r.ok);
  await s.close();
  lax.close();
  site.close();
  console.log("session ok");
  process.exit(0);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
