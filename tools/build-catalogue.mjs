// Builds site/catalogue.json from a copy of SpoolmanDB (https://github.com/Donkie/SpoolmanDB).
// Run: node tools/build-catalogue.mjs <path to SpoolmanDB> [output file]
import {readdir, readFile, writeFile} from "node:fs/promises";
import path from "node:path";

const [from, to = "site/catalogue.json"] = process.argv.slice(2);
if (!from) { console.error("Give the path to a copy of SpoolmanDB"); process.exit(1); }

const tidy = s => s.replace(/[™®]/g, "").replace(/\s+/g, " ").trim();
const span = (range, one) => range?.length === 2 ? range : one != null ? [one, one] : undefined;
const hex = h => String(h).replace("#", "").slice(0, 6).toUpperCase();

function naming(name, material) {
  // Brands often give only the colour, or a word like "High Speed", so the material fills the gap
  const raw = tidy(name.replace("{color_name}", "")).replace(/\s*-$/, "");
  const old = /\(Formerly ([^)]+)\)/i.exec(raw)?.[1];
  const product = tidy(raw.replace(/\(Formerly [^)]+\)/i, ""));
  const named = product.toLowerCase().includes(material.toLowerCase());
  const label = !product ? material : named ? product : /\bfor$/i.test(product) ? `${product} ${material}` : `${material} ${product}`;
  return {label, old};
}
function finishOf(f, label) {
  if (/silk/i.test(label)) return "Silk";
  if (f.finish === "matte") return "Matte";
  if (f.finish === "glossy") return "Glossy";
  if (f.pattern === "marble") return "Marble";
  if (f.pattern === "sparkle") return "Sparkle";
  if (f.translucent) return "Translucent";
}
function colourOf(c, f) {
  const hexes = c.hexes?.length ? c.hexes.map(hex) : [hex(c.hex ?? "9AA5AB")];
  if (hexes.length === 1) return [tidy(c.name), hexes[0]];
  const way = c.multi_color_direction ?? f.multi_color_direction;
  return [tidy(c.name), hexes, way === "longitudinal" ? "gradient" : "split"];
}

const brands = {};
const folder = path.join(from, "filaments");
for (const file of (await readdir(folder)).filter(n => n.endsWith(".json")).sort()) {
  const {manufacturer, filaments} = JSON.parse(await readFile(path.join(folder, file), "utf8"));
  const lines = new Map();
  for (const f of filaments) {
    const {label, old} = naming(f.name, f.material), finish = finishOf(f, label);
    const key = label + "|" + (finish ?? "");
    const weight = f.weights.find(w => w.weight === 1000) ?? f.weights[0];
    if (!lines.has(key)) lines.set(key, {n: label, m: f.material, a: old, t: span(f.extruder_temp_range, f.extruder_temp), b: span(f.bed_temp_range, f.bed_temp), d: f.density, w: weight?.weight, s: weight?.spool_weight ?? undefined, f: finish, c: []});
    const line = lines.get(key);
    for (const c of f.colors) { const n = colourOf(c, f); if (!line.c.some(x => x[0] === n[0])) line.c.push(n); }
  }
  // The same name in two finishes gets the finish added, so each one can be picked
  const unique = new Map();
  for (const line of lines.values()) {
    const taken = [...lines.values()].filter(l => l.n === line.n).length > 1;
    const label = taken && line.f && !line.n.includes(line.f) ? `${line.n} ${line.f}` : line.n;
    const same = unique.get(label);
    if (same) same.c.push(...line.c.filter(c => !same.c.some(x => x[0] === c[0])));
    else unique.set(label, {...line, n: label});
  }
  brands[manufacturer] = [...unique.values()].sort((a, b) => a.n.localeCompare(b.n));
}

const licence = await readFile(path.join(from, "LICENSE"), "utf8");
const count = Object.values(brands).reduce((a, l) => a + l.length, 0);
await writeFile(to, JSON.stringify({source: "https://github.com/Donkie/SpoolmanDB", built: new Date().toISOString().slice(0, 10), notice: licence, brands}));
console.log(`${Object.keys(brands).length} manufacturers, ${count} filaments`);
