// Runs every test suite and prints one line per suite, then any failures in full.
//
//   npm test               all suites
//   npm test -- app plugin only the named ones
//
// Each suite prints PASS or FAIL lines and exits non-zero if anything failed. Details of what each one needs
// (Playwright, ffmpeg, the camera feed file) are in CLAUDE.md.
import {spawnSync} from "node:child_process";
import path from "node:path";
import {fileURLToPath} from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SUITES = {
  app: ["node", "test/run.js"],
  reader: ["node", "test/reader.js"],
  orca: ["node", "test/orca.js"],
  plugin: ["python3", "test/plugin.py"],
  probe: ["python3", "test/probe.py"],
  format: ["node", "tools/format.mjs", "--check"],
};

const wanted = process.argv.slice(2);
const names = wanted.length ? wanted : Object.keys(SUITES);
let failed = false;
for (const name of names) {
  const cmd = SUITES[name];
  if (!cmd) {
    console.log(`No suite called ${name}. Choose from: ${Object.keys(SUITES).join(", ")}`);
    process.exit(2);
  }
  const started = Date.now();
  const run = spawnSync(cmd[0], cmd.slice(1), {cwd: root, encoding: "utf8", maxBuffer: 64e6});
  const out = (run.stdout || "") + (run.stderr || "");
  const passes = (out.match(/^PASS /gm) || []).length;
  const fails = out.split("\n").filter(l => l.startsWith("FAIL "));
  const bad = run.status !== 0 || fails.length > 0;
  const secs = Math.round((Date.now() - started) / 1000);
  console.log(`${bad ? "FAIL" : "ok  "}  ${name.padEnd(7)} ${String(passes).padStart(4)} passed  ${secs} s`);
  if (bad) {
    failed = true;
    // Show the failures, or the end of the output when the suite stopped without saying why.
    console.log((fails.length ? fails : out.trim().split("\n").slice(-15)).map(l => "      " + l).join("\n"));
  }
}
process.exit(failed ? 1 : 0);
