// Sends Spool Stock's filaments to OrcaSlicer:
// - writes filament presets into Orca's user preset folder (only JSON filament presets marked as user presets)
// - optionally sets Orca's filament list to just the given filaments, after backing up Orca's settings file
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const {execFileSync} = require("child_process");

const BACKUP = "OrcaSlicer.conf.spoolstock.bak";

function orcaRunning() {
  if (process.platform !== "win32") return false;
  try {
    const out = execFileSync("tasklist", ["/FI", "IMAGENAME eq orca-slicer.exe", "/NH"], {encoding: "utf8", windowsHide: true});
    return /orca-slicer\.exe/i.test(out);
  } catch { return false; }
}

function orcaBase(appData) {
  const base = path.join(appData, "OrcaSlicer");
  return fs.existsSync(base) ? base : null;
}

function orcaDir(appData) {
  const base = orcaBase(appData);
  if (!base) return {error: "Couldn't find OrcaSlicer on this PC. Open Orca once, then try again."};
  let folder = "default";
  const conf = readConf(base);
  if (conf && conf.json.app && conf.json.app.preset_folder) folder = String(conf.json.app.preset_folder);
  if (!/^[\w.-]+$/.test(folder) || folder === "." || folder === "..") folder = "default";
  return {dir: path.join(base, "user", folder, "filament")};
}

function readConf(base) {
  try {
    const text = fs.readFileSync(path.join(base, "OrcaSlicer.conf"), "utf8");
    const end = text.lastIndexOf("}");
    return {text, json: JSON.parse(text.slice(0, end + 1))};
  } catch { return null; }
}

// Orca saves its settings as JSON followed by an MD5 checksum line of that JSON.
function writeConf(base, json) {
  const body = JSON.stringify(json, null, 4);
  const md5 = crypto.createHash("md5").update(body).digest("hex").toUpperCase();
  fs.writeFileSync(path.join(base, "OrcaSlicer.conf"), body + "\n# MD5 checksum " + md5 + "\n", "utf8");
}

// Names of Orca's installed system filament presets, from its system folder.
function systemFilaments(base) {
  const out = [];
  const walk = dir => {
    for (const e of fs.readdirSync(dir, {withFileTypes: true})) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) walk(p);
      else if (e.name.endsWith(".json") && !/@base\.json$/.test(e.name) && /[\\/]filament[\\/]/.test(p)) out.push(e.name.slice(0, -5));
    }
  };
  try { walk(path.join(base, "system")); } catch {}
  return out;
}

function check(file) {
  if (!file || typeof file.name !== "string" || typeof file.text !== "string") return "Missing name or contents";
  if (!/^[^\\/:*?"<>|]{1,150}\.json$/.test(file.name) || file.name.includes("..")) return "Bad file name: " + file.name;
  if (file.text.length > 200000) return "Too large: " + file.name;
  let j;
  try { j = JSON.parse(file.text); } catch { return "Not JSON: " + file.name; }
  if (j.type !== "filament" || j.from !== "User" || !Array.isArray(j.filament_settings_id) || j.name + ".json" !== file.name) return "Not a filament preset: " + file.name;
  return null;
}

function writePresets(appData, files, opts = {}, running = orcaRunning) {
  files = files || [];
  const show = opts && Array.isArray(opts.show) ? opts.show : null;
  if (!Array.isArray(files) || files.length > 500) return {error: "Nothing to send."};
  if (!files.length && !show) return {error: "Nothing to send."};
  if (show && (show.length > 500 || show.some(n => typeof n !== "string" || !n || n.length > 200))) return {error: "Bad filament list."};
  for (const f of files) { const bad = check(f); if (bad) return {error: bad}; }
  const where = orcaDir(appData);
  if (where.error) return where;
  if (show && running()) return {error: "Close Orca first, so it doesn't overwrite its filament list when it closes."};
  fs.mkdirSync(where.dir, {recursive: true});
  for (const f of files) fs.writeFileSync(path.join(where.dir, f.name), f.text, "utf8");
  const result = {dir: where.dir, written: files.length};
  if (show) {
    const base = orcaBase(appData), conf = readConf(base);
    if (!conf) return {...result, error: "Sent the presets, but couldn't read Orca's settings to set its filament list."};
    const installed = systemFilaments(base), visible = {}, missing = [];
    for (const name of show) {
      const hits = installed.filter(n => n === name || n.startsWith(name + " @"));
      if (hits.length) hits.forEach(n => { visible[n] = "true"; }); else missing.push(name);
    }
    if (!fs.existsSync(path.join(base, BACKUP))) fs.writeFileSync(path.join(base, BACKUP), conf.text, "utf8");
    conf.json.filaments = visible;
    writeConf(base, conf.json);
    Object.assign(result, {shown: Object.keys(visible).length, missing});
  }
  return result;
}

function restoreList(appData, running = orcaRunning) {
  const base = orcaBase(appData);
  if (!base) return {error: "Couldn't find OrcaSlicer on this PC."};
  const backup = path.join(base, BACKUP);
  if (!fs.existsSync(backup)) return {error: "There's no saved list to restore."};
  if (running()) return {error: "Close Orca first, then restore."};
  const conf = readConf(base), old = (() => { try { const t = fs.readFileSync(backup, "utf8"); return JSON.parse(t.slice(0, t.lastIndexOf("}") + 1)); } catch { return null; } })();
  if (!conf || !old) return {error: "Couldn't read Orca's settings."};
  if (old.filaments) conf.json.filaments = old.filaments; else delete conf.json.filaments;
  writeConf(base, conf.json);
  fs.unlinkSync(backup);
  return {restored: true};
}

module.exports = {orcaDir, writePresets, restoreList, check, systemFilaments};
