// Builds site/catalogue.json from a copy of SpoolmanDB (https://github.com/Donkie/SpoolmanDB).
// Run: node tools/build-catalogue.mjs <path to SpoolmanDB> [output file] [path to Open Filament Database] [path to OrcaSlicer]
// With OrcaSlicer, each line that Orca's filament library has a profile for is linked to it (o), so Spool Stock can
// make Orca presets that build on it, and the library's generic profiles are listed for everything else.
// With the Open Filament Database, its manufacturer ranges, drying, spool weights and colours fill in
// matching lines, and filaments or manufacturers SpoolmanDB lacks are added.
// tools/catalogue-extra.json holds additions and corrections checked against manufacturers' own sites.
import {readdir, readFile, writeFile} from "node:fs/promises";
import path from "node:path";

const [from, to = "site/catalogue.json", ofd, orca] = process.argv.slice(2);
if (!from) { console.error("Give the path to a copy of SpoolmanDB"); process.exit(1); }

const tidy = s => s.replace(/[™®]/g, "").replace(/\s+/g, " ").trim();
const span = (range, one) => range?.length === 2 ? range : one != null ? [one, one] : undefined;
const hex = h => String(h).replace("#", "").slice(0, 6).toUpperCase();

function naming(name, material) {
  // Brands often give only the colour, or a word like "High Speed", so the material fills the gap
  const raw = tidy(name.replace("{color_name}", "").replace(/\(NFC\)/i, "")).replace(/\s*-$/, "");
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

const words = s => new Set((s.toLowerCase().replace(/carbon fib(er|re)/g, "cf").replace(/glass fib(er|re)/g, "gf").replace(/\+/g, " plus ").replace(/high speed/g, "hs").replace(/-/g, " ").match(/[a-z0-9]+/g) ?? []).filter(w => w !== "filament"));
const same = (a, b) => a.size === b.size && [...a].every(w => b.has(w));
const keyOf = line => new Set([...words(line.n), ...words(line.m)]);
const both = (lo, hi) => lo != null && hi != null ? [lo, hi] : undefined;
const brandKey = s => s.toLowerCase().replace(/[^a-z0-9]/g, "").replace(/(filaments?|3d)+$/, "");
const json = async f => JSON.parse(await readFile(f, "utf8").catch(() => "null"));
const dirs = async d => (await readdir(d, {withFileTypes: true}).catch(() => [])).filter(e => e.isDirectory()).map(e => e.name).sort();

// Open Filament Database: every brand. Ranges, drying and colours go into matching lines, and filaments
// or brands SpoolmanDB doesn't have are added.
const tally = {ranges: 0, lines: 0, brands: 0, colours: 0};
if (ofd) for (const folder of await dirs(path.join(ofd, "data"))) {
  const root = path.join(ofd, "data", folder), info = await json(path.join(root, "brand.json"));
  if (!info?.name) continue;
  const name = Object.keys(brands).find(b => brandKey(b) === brandKey(info.name) || brandKey(b) === brandKey(folder)) ?? tidy(info.name);
  if (!brands[name]) { brands[name] = []; tally.brands++; }
  const list = brands[name], records = [];
  for (const mat of await dirs(root)) {
    const base = await json(path.join(root, mat, "material.json")) ?? {}, material = base.material ?? mat;
    for (const id of await dirs(path.join(root, mat))) {
      const j = await json(path.join(root, mat, id, "filament.json"));
      if (!j?.name || j.discontinued) continue;
      const colours = [], weights = [];
      for (const v of await dirs(path.join(root, mat, id))) {
        const vj = await json(path.join(root, mat, id, v, "variant.json"));
        if (!vj?.name || vj.discontinued) continue;
        const sizes = ((await json(path.join(root, mat, id, v, "sizes.json"))) ?? []).filter(z => !z.discontinued && (z.diameter ?? 1.75) === 1.75);
        if (!sizes.length) continue;
        weights.push(...sizes);
        const hexes = [].concat(vj.color_hex ?? []).map(hex).filter(h => /^[0-9A-F]{6}$/.test(h));
        if (!hexes.length) continue;
        colours.push(hexes.length === 1 ? [tidy(vj.name), hexes[0]] : [tidy(vj.name), hexes.slice(0, 6), vj.traits?.gradual_color_change ? "gradient" : "split"]);
      }
      if (!weights.length) continue;
      const common = Object.entries(weights.reduce((a, z) => (a[z.filament_weight] = (a[z.filament_weight] ?? 0) + 1, a), {})).sort((a, b) => b[1] - a[1] || +b[0] - +a[0])[0][0];
      const size = weights.find(z => String(z.filament_weight) === common && z.empty_spool_weight) ?? weights.find(z => String(z.filament_weight) === common);
      const v = k => j[k] ?? base["default_" + k];
      const {label} = naming(j.name.replace(/\s*\(NFC\)/i, ""), material);
      records.push({n: label, m: material, t: both(v("min_print_temperature"), v("max_print_temperature")), b: both(v("min_bed_temperature"), v("max_bed_temperature")),
        y: v("dry_temperature") ? [v("dry_temperature"), v("dry_time") ?? null] : undefined, d: j.density, w: +common || undefined, s: size?.empty_spool_weight, colours,
        key: new Set([...words(label), ...words(material)])});
    }
  }
  for (const r of records) {
    const matched = list.filter(l => same(keyOf(l), r.key) || l.n.toLowerCase() === r.n.toLowerCase());
    if (matched.length > 1) continue;
    let line = matched[0];
    if (!line) {
      if (list.some(l => l.n.toLowerCase() === r.n.toLowerCase())) continue;
      const f = /silk/i.test(r.n) ? "Silk" : /matte/i.test(r.n) ? "Matte" : /marble/i.test(r.n) ? "Marble" : /sparkle|glitter|galaxy/i.test(r.n) ? "Sparkle" : /translucent|transparent/i.test(r.n) ? "Translucent" : undefined;
      line = {n: r.n, m: r.m, t: r.t, b: r.b, y: r.y, d: r.d, w: r.w, s: r.s, f, c: []};
      list.push(line); tally.lines++;
    } else {
      if (r.t && r.b && (r.t[0] < r.t[1] || r.b[0] < r.b[1]) && records.filter(x => same(x.key, r.key)).length === 1) { Object.assign(line, {t: r.t, b: r.b}, r.y ? {y: r.y} : {}); tally.ranges++; }
      if (line.s == null && r.s != null && line.w === r.w) line.s = r.s;
      line.d ??= r.d;
    }
    const known = new Set(line.c.map(c => c[0].toLowerCase()));
    for (const c of r.colours) if (!known.has(c[0].toLowerCase())) { line.c.push(c); known.add(c[0].toLowerCase()); tally.colours++; }
  }
  list.sort((a, b) => a.n.localeCompare(b.n));
}
if (ofd) console.log(`Open Filament Database: ${tally.ranges} ranges, ${tally.lines} filaments and ${tally.brands} manufacturers added, ${tally.colours} colours`);

const extra = JSON.parse(await readFile(new URL("catalogue-extra.json", import.meta.url), "utf8"));
for (const [brand, adds] of Object.entries(extra.add)) for (const line of adds) {
  const list = brands[brand] ??= [];
  const names = [line.n, ...[].concat(line.a ?? [])].map(n => n.toLowerCase());
  const twin = list.find(l => names.includes(l.n.toLowerCase())) ?? list.find(l => same(keyOf(l), keyOf(line)));
  if (twin) Object.assign(twin, line, {c: [...line.c, ...twin.c.filter(c => !line.c.some(x => x[0].toLowerCase() === c[0].toLowerCase()))]}); else list.push(line);
  list.sort((a, b) => a.n.localeCompare(b.n));
}
for (const [brand, lines] of Object.entries(extra.change)) for (const [name, patch] of Object.entries(lines)) {
  const line = brands[brand]?.find(l => l.n === name);
  const twin = patch.n && patch.n !== name && brands[brand]?.find(l => l.n === patch.n);
  if (line && twin) {
    // A rename onto a line that now exists merges the two
    for (const c of line.c) if (!twin.c.some(x => x[0].toLowerCase() === c[0].toLowerCase())) twin.c.push(c);
    Object.assign(twin, patch); brands[brand].splice(brands[brand].indexOf(line), 1);
  } else if (line) Object.assign(line, patch); else console.warn(`Correction not used: ${brand} ${name}`);
  brands[brand]?.sort((a, b) => a.n.localeCompare(b.n));
}

// OrcaSlicer's filament library: link lines to the brand's own Orca profile
const orcaInfo = {generic: []};
const ORCA_BRANDS = {"bambu": "Bambu Lab", "addnorth": "add:north"};
async function walk(dir) {
  const out = [];
  for (const e of await readdir(dir, {withFileTypes: true}).catch(() => [])) {
    if (e.isDirectory()) out.push(...(await walk(path.join(dir, e.name))).map(f => path.join(e.name, f)));
    else if (/ @System\.json$/.test(e.name)) out.push(e.name);
  }
  return out;
}
let linked = 0;
if (orca) {
  const lib = path.join(orca, "resources", "profiles", "OrcaFilamentLibrary", "filament");
  for (const rel of await walk(lib)) {
    const parts = rel.split(path.sep), name = parts.at(-1).replace(/\.json$/, "");
    if (parts.length === 1) { if (name.startsWith("Generic ")) orcaInfo.generic.push(name); continue; }
    const vendor = parts[0], brand = ORCA_BRANDS[brandKey(vendor)] ?? Object.keys(brands).find(b => brandKey(b) === brandKey(vendor));
    if (!brand) continue;
    let label = name.replace(/ @System$/, "").split(" ");
    if (brandKey(label[0]) === brandKey(vendor) || brandKey(vendor).startsWith(brandKey(label[0]))) label = label.slice(1);
    const w = words(label.join(" "));
    for (const line of brands[brand]) {
      if (line.o) continue;
      if (same(w, keyOf(line))) { line.o = name; linked++; }
    }
  }
  orcaInfo.generic.sort();
  console.log(`OrcaSlicer: ${linked} filaments linked to Orca profiles, ${orcaInfo.generic.length} generic profiles`);
}

const licence = await readFile(path.join(from, "LICENSE"), "utf8");
const count = Object.values(brands).reduce((a, l) => a + l.length, 0);
await writeFile(to, JSON.stringify({source: "https://github.com/Donkie/SpoolmanDB", built: new Date().toISOString().slice(0, 10), notice: licence, also: ofd ? "https://github.com/OpenFilamentCollective/open-filament-database" : undefined, aliases: extra.aliases ?? {}, orca: orcaInfo, brands}));
console.log(`${Object.keys(brands).length} manufacturers, ${count} filaments`);
