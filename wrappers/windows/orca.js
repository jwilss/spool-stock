// Writes Spool Stock's filament presets into OrcaSlicer's user preset folder.
// Only JSON filament presets marked as user presets are accepted, and only into that one folder.
const fs = require("fs");
const path = require("path");

function orcaDir(appData) {
  const base = path.join(appData, "OrcaSlicer");
  if (!fs.existsSync(base)) return {error: "Couldn't find OrcaSlicer on this PC. Open Orca once, then try again."};
  let folder = "default";
  try {
    const conf = JSON.parse(fs.readFileSync(path.join(base, "OrcaSlicer.conf"), "utf8"));
    if (conf && conf.app && conf.app.preset_folder) folder = String(conf.app.preset_folder);
  } catch {}
  if (!/^[\w.-]+$/.test(folder) || folder === "." || folder === "..") folder = "default";
  return {dir: path.join(base, "user", folder, "filament")};
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

function writePresets(appData, files) {
  if (!Array.isArray(files) || !files.length || files.length > 500) return {error: "Nothing to send."};
  for (const f of files) { const bad = check(f); if (bad) return {error: bad}; }
  const where = orcaDir(appData);
  if (where.error) return where;
  fs.mkdirSync(where.dir, {recursive: true});
  for (const f of files) fs.writeFileSync(path.join(where.dir, f.name), f.text, "utf8");
  return {dir: where.dir, written: files.length};
}

module.exports = {orcaDir, writePresets, check};
