// Checks the Windows app's Orca bridge. Run: node test/orca.js
const {writePresets, check} = require("../wrappers/windows/orca.js");
const fs = require("fs"), os = require("os"), path = require("path");
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "orca"));
const ok = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) process.exitCode = 1; };
const good = {name: "eSUN PETG (Spool Stock).json", text: JSON.stringify({type: "filament", from: "User", name: "eSUN PETG (Spool Stock)", filament_settings_id: ["eSUN PETG (Spool Stock)"]})};
ok(writePresets(tmp, [{name: "a.json", text: "{}"}]).error.includes("filament preset"), "rejects a non-preset");
ok(writePresets(tmp, [good]).error.includes("find OrcaSlicer"), "needs Orca installed");
fs.mkdirSync(path.join(tmp, "OrcaSlicer"));
let r = writePresets(tmp, [good]);
ok(r.written === 1 && fs.existsSync(path.join(tmp, "OrcaSlicer/user/default/filament", good.name)), "writes to the default preset folder");
fs.writeFileSync(path.join(tmp, "OrcaSlicer/OrcaSlicer.conf"), JSON.stringify({app: {preset_folder: "1234567"}}));
r = writePresets(tmp, [good]);
ok(r.dir.endsWith(path.join("user", "1234567", "filament")), "follows a signed-in Orca folder");
fs.writeFileSync(path.join(tmp, "OrcaSlicer/OrcaSlicer.conf"), JSON.stringify({app: {preset_folder: "../../evil"}}));
r = writePresets(tmp, [good]);
ok(r.dir.endsWith(path.join("user", "default", "filament")), "ignores an odd folder name");
ok(!!check({name: "../x.json", text: good.text}), "refuses paths that leave the folder");
ok(!!check({name: "x.json", text: good.text}), "file name must match the preset name");
