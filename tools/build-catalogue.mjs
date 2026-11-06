// Builds site/catalogue.json from a copy of SpoolmanDB (https://github.com/Donkie/SpoolmanDB).
// Run: node tools/build-catalogue.mjs <path to SpoolmanDB> [output file] [path to Open Filament Database]
// With the Open Filament Database, manufacturer temperature ranges and drying replace SpoolmanDB's single
// values for exact name matches in the brands listed in OFD_BRANDS, and its colours are added to those lines.
// tools/catalogue-extra.json holds additions and corrections checked against manufacturers' own sites.
import {readdir, readFile, writeFile} from "node:fs/promises";
import path from "node:path";

const [from, to = "site/catalogue.json", ofd] = process.argv.slice(2);
const OFD_BRANDS = {"eSun": "esun_3d", "Bambu Lab": "bambu_lab", "Sunlu": "sunlu", "CC3D": "cc3d"};
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

const words = s => new Set((s.toLowerCase().replace(/\+/g, " plus ").replace(/high speed/g, "hs").replace(/-/g, " ").match(/[a-z0-9]+/g) ?? []).filter(w => w !== "filament"));
const same = (a, b) => a.size === b.size && [...a].every(w => b.has(w));
const both = (lo, hi) => lo != null && hi != null ? [lo, hi] : undefined;
let fromOfd = 0;
if (ofd) for (const [brand, folder] of Object.entries(OFD_BRANDS)) {
  const root = path.join(ofd, "data", folder), records = [];
  for (const mat of await readdir(root, {withFileTypes: true})) {
    if (!mat.isDirectory()) continue;
    const base = JSON.parse(await readFile(path.join(root, mat.name, "material.json"), "utf8").catch(() => "{}"));
    for (const line of await readdir(path.join(root, mat.name), {withFileTypes: true})) {
      if (!line.isDirectory()) continue;
      const j = JSON.parse(await readFile(path.join(root, mat.name, line.name, "filament.json"), "utf8").catch(() => "null"));
      if (!j || j.discontinued) continue;
      const colours = [];
      for (const c of await readdir(path.join(root, mat.name, line.name), {withFileTypes: true})) {
        if (!c.isDirectory()) continue;
        const vj = JSON.parse(await readFile(path.join(root, mat.name, line.name, c.name, "variant.json"), "utf8").catch(() => "null"));
        if (!vj?.name || !vj.color_hex || vj.discontinued) continue;
        const hexes = [].concat(vj.color_hex).map(hex).filter(h => /^[0-9A-F]{6}$/.test(h));
        if (!hexes.length) continue;
        colours.push(hexes.length === 1 ? [tidy(vj.name), hexes[0]] : [tidy(vj.name), hexes.slice(0, 6), vj.traits?.gradual_color_change ? "gradient" : "split"]);
      }
      const v = k => j[k] ?? base["default_" + k];
      records.push({colours, key: new Set([...words(j.name), ...words(mat.name)]), t: both(v("min_print_temperature"), v("max_print_temperature")), b: both(v("min_bed_temperature"), v("max_bed_temperature")), y: v("dry_temperature") ? [v("dry_temperature"), v("dry_time") ?? null] : undefined});
    }
  }
  for (const line of brands[brand] ?? []) {
    const key = new Set([...words(line.n), ...words(line.m)]), named = records.filter(r => same(r.key, key));
    if (named.length !== 1) continue;
    const r = named[0], known = new Set(line.c.map(c => c[0].toLowerCase()));
    for (const c of r.colours) if (!known.has(c[0].toLowerCase())) { line.c.push(c); known.add(c[0].toLowerCase()); }
    if (!(r.t && r.b && (r.t[0] < r.t[1] || r.b[0] < r.b[1]))) continue;
    Object.assign(line, {t: r.t, b: r.b}, r.y ? {y: r.y} : {}); fromOfd++;
  }
}
if (ofd) console.log(`${fromOfd} filaments given manufacturer ranges from the Open Filament Database`);

const extra = JSON.parse(await readFile(new URL("catalogue-extra.json", import.meta.url), "utf8"));
for (const [brand, adds] of Object.entries(extra.add)) for (const line of adds) {
  const list = brands[brand] ??= [];
  const same = list.find(l => l.n === line.n);
  if (same) Object.assign(same, line, {c: same.c}); else list.push(line);
  list.sort((a, b) => a.n.localeCompare(b.n));
}
for (const [brand, lines] of Object.entries(extra.change)) for (const [name, patch] of Object.entries(lines)) {
  const line = brands[brand]?.find(l => l.n === name);
  if (line) Object.assign(line, patch); else console.warn(`Correction not used: ${brand} ${name}`);
  brands[brand]?.sort((a, b) => a.n.localeCompare(b.n));
}

const licence = await readFile(path.join(from, "LICENSE"), "utf8");
const count = Object.values(brands).reduce((a, l) => a + l.length, 0);
await writeFile(to, JSON.stringify({source: "https://github.com/Donkie/SpoolmanDB", built: new Date().toISOString().slice(0, 10), notice: licence, also: ofd ? "https://github.com/OpenFilamentCollective/open-filament-database" : undefined, aliases: extra.aliases ?? {}, brands}));
console.log(`${Object.keys(brands).length} manufacturers, ${count} filaments`);
