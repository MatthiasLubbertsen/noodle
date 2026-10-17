// build-time only (the runtime container has no network): download the pyodide
// package wheels this sandbox preloads into node_modules/pyodide, next to the
// lock file, so loadPackage() resolves them from local disk.
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const PYODIDE_DIR = path.join(__dirname, "node_modules", "pyodide");
const lock = JSON.parse(fs.readFileSync(path.join(PYODIDE_DIR, "pyodide-lock.json"), "utf8"));
const version = require(path.join(PYODIDE_DIR, "package.json")).version;
const CDN = `https://cdn.jsdelivr.net/pyodide/v${version}/full/`;

// keep in sync with PRELOAD in child.js
const WANTED = ["numpy", "pandas", "matplotlib", "scipy", "sympy"];

function closure(names, seen = new Set()) {
  for (const n of names) {
    const key = n.toLowerCase();
    if (seen.has(key)) continue;
    const pkg = lock.packages[key];
    if (!pkg) throw new Error(`package not in pyodide lock: ${n}`);
    seen.add(key);
    closure(pkg.depends || [], seen);
  }
  return seen;
}

(async () => {
  const names = [...closure(WANTED)];
  console.log(`fetching ${names.length} packages for pyodide ${version}: ${names.join(", ")}`);
  for (const n of names) {
    const pkg = lock.packages[n];
    const dest = path.join(PYODIDE_DIR, pkg.file_name);
    if (fs.existsSync(dest)) continue;
    const res = await fetch(CDN + pkg.file_name);
    if (!res.ok) throw new Error(`${pkg.file_name}: HTTP ${res.status}`);
    const buf = Buffer.from(await res.arrayBuffer());
    const sha = crypto.createHash("sha256").update(buf).digest("hex");
    if (pkg.sha256 && sha !== pkg.sha256) throw new Error(`${pkg.file_name}: checksum mismatch`);
    fs.writeFileSync(dest, buf);
  }
  console.log("done");
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
