// Formats all of Spool Stock's own code, so it reads the same everywhere.
//
//   npm run format         rewrite the files in place
//   npm run format:check   list files that would change, and exit 1 if there are any
//
// What it covers:
//   - JavaScript files (the tests, tools, the Windows wrapper, the service worker): Prettier.
//   - The <script> and <style> blocks inside the app and Spool Reader. The HTML around them is left alone,
//     as is everything inside template strings, because whitespace there ends up on the page.
//   - The tab pages kept inside the Orca plugins (the PAGE = r"""...""" string in each Python file).
//   - Python: `ruff format`, when ruff is installed (pip install ruff). Settings are in ruff.toml.
//
// Vendored libraries (site/vendor) and generated files (site/catalogue.json) are never touched.
import {readFile, writeFile, readdir} from "node:fs/promises";
import {execFileSync} from "node:child_process";
import path from "node:path";
import {fileURLToPath} from "node:url";
import prettier from "prettier";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const check = process.argv.includes("--check");
const options = await prettier.resolveConfig(path.join(root, "package.json"));

const JS_FOLDERS = ["test", "tools", "wrappers/windows"];
const JS_FILES = ["site/config.js", "site/sw.js"];
const HTML_FILES = ["site/index.html", "reader/spool-reader.html"];
const PLUGIN_FILES = ["orca-plugin/spoolstock/spool_stock.py", "orca-plugin/probe/spoolstock_probe.py"];
const PYTHON = ["orca-plugin", "test"];

// Prettier can need a second pass on very dense code before its output settles, so run it until nothing changes.
async function settle(text, parser) {
  for (let pass = 0; pass < 4; pass++) {
    const next = await prettier.format(text, {...options, parser});
    if (next === text) break;
    text = next;
  }
  return text;
}
const js = text => settle(text, "babel");
const css = text => settle(text, "css");

/** Formats every inline <script> and <style> block of an HTML document, leaving the markup as it is. */
async function formatHtml(html) {
  const parts = [];
  let last = 0;
  for (const m of html.matchAll(/(<script>|<style>)([\s\S]*?)(<\/script>|<\/style>)/g)) {
    const body = m[1] === "<style>" ? await css(m[2]) : await js(m[2]);
    parts.push(html.slice(last, m.index), m[1], "\n", body, m[3]);
    last = m.index + m[0].length;
  }
  parts.push(html.slice(last));
  return parts.join("");
}

/** Formats the tab page a plugin keeps in its PAGE raw string. The Python around it is ruff's job. */
async function formatPluginPage(source) {
  const open = 'PAGE = r"""';
  const start = source.indexOf(open);
  if (start < 0) return source;
  const from = start + open.length;
  const to = source.indexOf('"""', from);
  const page = await formatHtml(source.slice(from, to));
  if (page.includes('"""')) throw new Error("The tab page can't contain three double quotes in a row");
  return source.slice(0, from) + page + source.slice(to);
}

const changed = [];
async function rewrite(file, format) {
  const full = path.join(root, file);
  const before = await readFile(full, "utf8");
  const after = await format(before);
  if (after === before) return;
  changed.push(file);
  if (!check) await writeFile(full, after, "utf8");
}

for (const folder of JS_FOLDERS)
  for (const name of await readdir(path.join(root, folder)))
    if (/\.(js|mjs)$/.test(name)) await rewrite(path.join(folder, name), js);
for (const file of JS_FILES) await rewrite(file, js);
for (const file of HTML_FILES) await rewrite(file, formatHtml);

// Python first, then the pages inside the plugins, so each tool only sees its own part.
// pip often puts ruff somewhere that isn't on the PATH on Windows, so fall back to running it through Python.
let ruff = true;
const ruffArgs = ["format", ...(check ? ["--check"] : []), ...PYTHON];
const runRuff = () => {
  try {
    return execFileSync("ruff", ruffArgs, {cwd: root, stdio: "pipe"});
  } catch (e) {
    if (e.code !== "ENOENT") throw e;
    const py = process.platform === "win32" ? "python" : "python3";
    try {
      return execFileSync(py, ["-m", "ruff", ...ruffArgs], {cwd: root, stdio: "pipe"});
    } catch (e2) {
      if (e2.code === "ENOENT" || /No module named ruff/.test(String(e2.stderr)))
        throw Object.assign(e2, {code: "ENOENT"});
      throw e2;
    }
  }
};
try {
  runRuff();
} catch (e) {
  if (e.code === "ENOENT") ruff = false;
  else if (check) {
    // ruff names each file it would change on a line like "   --> test/plugin.py:788:4"
    const files = [...String(e.stdout).matchAll(/^\s*--> (.+?):\d+:\d+$/gm)].map(m => m[1]);
    changed.push(...(files.length ? files : ["Python files (run ruff format --check to see which)"]));
  } else throw e;
}
for (const file of PLUGIN_FILES) await rewrite(file, formatPluginPage);

if (!ruff) console.log("ruff isn't installed, so the Python files were left as they are (pip install ruff).");
if (check && changed.length) {
  console.log("These need formatting (npm run format):\n  " + [...new Set(changed)].join("\n  "));
  process.exit(1);
}
console.log(
  changed.length ? `Formatted ${changed.length} file${changed.length === 1 ? "" : "s"}.` : "Everything is formatted.",
);
