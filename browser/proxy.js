// the egress filter. chromium is started with --proxy-server pointing at this, so EVERY
// request it makes (pages, redirects, subresources, websockets) goes through here, and this
// resolves the name itself and connects to the address it checked. that closes the dns
// rebinding gap a request-interception check would leave (chromium resolving the name a
// second time after we looked), and it keeps the browser away from the docker network, the
// host and the lan: only public addresses are ever connected to.
const dns = require("dns").promises;
const http = require("http");
const net = require("net");

const blocked = new net.BlockList();
for (const [net4, bits] of [
  ["0.0.0.0", 8], // "this" network
  ["10.0.0.0", 8],
  ["100.64.0.0", 10], // carrier-grade nat (also tailscale)
  ["127.0.0.0", 8],
  ["169.254.0.0", 16], // link-local, cloud metadata
  ["172.16.0.0", 12],
  ["192.0.0.0", 24],
  ["192.168.0.0", 16],
  ["198.18.0.0", 15],
  ["224.0.0.0", 4], // multicast
  ["240.0.0.0", 4], // reserved + broadcast
]) blocked.addSubnet(net4, bits, "ipv4");
// ipv4-mapped ipv6 (::ffff:a.b.c.d) is judged by the ipv4 rules above: node's BlockList treats
// the two as the same address, so there is deliberately no ::ffff:0:0/96 rule (it would match
// every ipv4 address)
for (const [net6, bits] of [
  ["::", 128],
  ["::1", 128],
  ["64:ff9b::", 96], // nat64
  ["fc00::", 7], // unique local
  ["fe80::", 10], // link-local
  ["ff00::", 8], // multicast
  ["2001:db8::", 32], // documentation
]) blocked.addSubnet(net6, bits, "ipv6");

// ports that are never a web page: mail, ssh, databases, smb...
const DENIED_PORTS = new Set([21, 22, 23, 25, 110, 135, 139, 143, 445, 465, 587, 993, 995, 1433, 3306, 3389, 5432, 6379, 27017]);

function isBlockedAddress(ip) {
  const family = net.isIP(ip);
  if (!family) return true;
  return blocked.check(ip, family === 4 ? "ipv4" : "ipv6");
}

// resolve a host to ONE address that is allowed to be connected to; throws otherwise.
// if any of the answers is private the whole name is refused (a name mixing public and
// private records is exactly what an attacker would set up).
async function resolvePublic(host, allowPrivate) {
  const bare = host.replace(/^\[|\]$/g, "");
  const answers = net.isIP(bare) ? [{ address: bare }] : await dns.lookup(bare, { all: true });
  if (!answers.length) throw new Error("no address");
  if (!allowPrivate && answers.some((a) => isBlockedAddress(a.address))) throw new Error("address not allowed");
  return answers[0].address;
}

function splitHostPort(target, defaultPort) {
  const m = /^(\[[^\]]+\]|[^:]+)(?::(\d+))?$/.exec(target);
  if (!m) throw new Error("bad target");
  return { host: m[1], port: m[2] ? parseInt(m[2], 10) : defaultPort };
}

function createProxy({ allowPrivate = false, log = () => {} } = {}) {
  const server = http.createServer(async (req, res) => {
    // plain http request through the proxy (https goes through CONNECT below)
    let url;
    try {
      url = new URL(req.url);
      if (url.protocol !== "http:") throw new Error("only http");
      const port = url.port ? parseInt(url.port, 10) : 80;
      if (DENIED_PORTS.has(port)) throw new Error("port not allowed");
      const ip = await resolvePublic(url.hostname, allowPrivate);
      const headers = { ...req.headers };
      delete headers["proxy-connection"];
      const upstream = http.request({ host: ip, port, method: req.method, path: url.pathname + url.search, headers }, (up) => {
        res.writeHead(up.statusCode, up.headers);
        up.pipe(res);
      });
      upstream.setTimeout(30000, () => upstream.destroy());
      upstream.on("error", () => res.headersSent || res.writeHead(502).end());
      req.pipe(upstream);
    } catch (e) {
      log(`denied http ${req.url}: ${e.message}`);
      if (!res.headersSent) res.writeHead(403, { "Content-Type": "text/plain" });
      res.end("blocked by noodle's browser egress filter");
    }
  });

  server.on("connect", async (req, clientSocket, head) => {
    let upstream;
    try {
      const { host, port } = splitHostPort(req.url, 443);
      if (DENIED_PORTS.has(port)) throw new Error("port not allowed");
      const ip = await resolvePublic(host, allowPrivate);
      upstream = net.connect({ host: ip, port }, () => {
        upstream.setTimeout(0);
        clientSocket.write("HTTP/1.1 200 Connection Established\r\n\r\n");
        if (head && head.length) upstream.write(head);
        upstream.pipe(clientSocket);
        clientSocket.pipe(upstream);
      });
      upstream.setTimeout(15000, () => upstream.destroy()); // connect timeout only, cleared once the tunnel is up
      upstream.on("error", () => clientSocket.destroy());
      clientSocket.on("error", () => upstream.destroy());
      clientSocket.on("close", () => upstream.destroy());
      upstream.on("close", () => clientSocket.destroy());
    } catch (e) {
      log(`denied connect ${req.url}: ${e.message}`);
      clientSocket.end("HTTP/1.1 403 Forbidden\r\n\r\n");
    }
  });

  return server;
}

module.exports = { createProxy, isBlockedAddress };
