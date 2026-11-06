// Visits each brand's home page once and saves anything that looks like its logo.
// Run by the "Fetch brand logos" GitHub Action. Results go in a temporary download, never into the repo.
import {mkdir, writeFile} from "node:fs/promises";
import path from "node:path";

const SITES = {
  "SUNLU": "https://store.sunlu.com/", "Overture": "https://overture3d.com/", "JAYO": "https://jayo3d.com/",
  "Kingroon": "https://kingroon.com/", "Fiberlogy": "https://fiberlogy.com/", "Extrudr": "https://www.extrudr.com/",
  "ColorFabb": "https://colorfabb.com/", "Spectrum": "https://spectrumfilaments.com/", "3DJake": "https://www.3djake.com/",
  "Geeetech": "https://www.geeetech.com/", "Eryone": "https://eryone3d.com/", "AMOLEN": "https://amolen.com/",
  "Siraya Tech": "https://siraya.tech/", "Fillamentum": "https://fillamentum.com/", "Recreus": "https://recreus.com/",
  "NinjaTek": "https://ninjatek.com/", "Das Filament": "https://www.dasfilament.de/", "Azurefilm": "https://azurefilm.com/",
  "Rosa3D": "https://rosa3d.pl/", "Devil Design": "https://devildesign.com/",
};
const OUT = process.argv[2] || "logos";
const BASE = process.env.SITES_BASE; // only used by the local test
const UA = "Mozilla/5.0 (compatible; SpoolStockLogoFetch/1.0)";
const EXT = {"image/svg+xml":"svg","image/png":"png","image/webp":"webp","image/jpeg":"jpg","image/gif":"gif","image/x-icon":"ico","image/vnd.microsoft.icon":"ico"};

const attr = (tag, name) => { const m = new RegExp(`\\b${name}\\s*=\\s*("([^"]*)"|'([^']*)')`, "i").exec(tag); return m ? (m[2] ?? m[3]).replace(/&amp;/g, "&") : ""; };
const biggest = set => set.split(",").map(s => s.trim().split(/\s+/)).map(([u, w]) => [u, parseFloat(w) || 0]).sort((a, b) => b[1] - a[1])[0]?.[0] || "";
// shop images are often served small, so ask for the original
const full = u => u.replace(/([?&])(width|w|height|h|size)=\d+&?/gi, "$1").replace(/[?&]$/, "").replace(/_(\d+x\d*|x\d+)(\.[a-z]+)/i, "$2");

export function candidates(html, base) {
  const found = [];
  const add = (u, score, why) => { if (!u || u.startsWith("data:")) return; try { found.push({url: full(new URL(u, base).href), score, why}); } catch {} };
  const head = html.slice(0, Math.max(html.search(/<\/header>/i), 0) || 40000);
  for (const m of html.matchAll(/<img\b[^>]*>/gi)) {
    const t = m[0], hay = (attr(t, "class") + " " + attr(t, "alt") + " " + attr(t, "id") + " " + attr(t, "src") + " " + attr(t, "data-src")).toLowerCase();
    if (!/logo|brand/.test(hay) || /payment|visa|paypal|footer|partner/.test(hay)) continue;
    const u = biggest(attr(t, "srcset") || attr(t, "data-srcset")) || attr(t, "src") || attr(t, "data-src");
    add(u, (/\.svg/i.test(u) ? 100 : 60) + (head.includes(t) ? 20 : 0), "image marked logo");
  }
  for (const m of html.matchAll(/"logo"\s*:\s*(?:\{[^}]*?"url"\s*:\s*)?"([^"]+)"/gi)) add(m[1].replace(/\\\//g, "/"), 70, "structured data");
  for (const m of html.matchAll(/<link\b[^>]*>/gi)) {
    const t = m[0], rel = attr(t, "rel").toLowerCase(), u = attr(t, "href");
    if (/icon/.test(rel) && /svg/i.test(u + attr(t, "type"))) add(u, 75, "svg icon");
    else if (/apple-touch-icon/.test(rel)) add(u, 30, "app icon");
  }
  const seen = new Set();
  return found.sort((a, b) => b.score - a.score).filter(f => !seen.has(f.url) && seen.add(f.url)).slice(0, 5);
}
// logos drawn straight into the page rather than linked as a file
export function inlineSvgs(html) {
  const out = [];
  for (const m of html.matchAll(/<svg\b[^>]*>[\s\S]*?<\/svg>/gi)) {
    const before = html.slice(Math.max(0, m.index - 300), m.index).toLowerCase(), open = m[0].slice(0, 300).toLowerCase();
    if (/logo/.test(open) || /class="[^"]*logo[^"]*"[^<]*(<[^>]*>\s*)*$/.test(before)) out.push(m[0].includes("xmlns") ? m[0] : m[0].replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"'));
    if (out.length === 2) break;
  }
  return out;
}
async function get(url) {
  const c = new AbortController(), t = setTimeout(() => c.abort(), 20000);
  try { return await fetch(url, {headers: {"user-agent": UA, accept: "text/html,image/*,*/*"}, redirect: "follow", signal: c.signal}); }
  finally { clearTimeout(t); }
}
async function main() {
  const report = [];
  for (const [brand, site] of Object.entries(SITES)) {
    const dir = path.join(OUT, brand.replace(/[^a-z0-9]+/gi, "-"));
    try {
      const url = BASE ? BASE + "/" + brand.replace(/\s/g, "") + ".html" : site;
      const r = await get(url);
      if (!r.ok) { report.push(`${brand}: home page refused (${r.status})`); continue; }
      const html = await r.text(); let n = 0;
      await mkdir(dir, {recursive: true});
      for (const svg of inlineSvgs(html)) await writeFile(path.join(dir, `inline-${++n}.svg`), svg);
      for (const c of candidates(html, r.url)) {
        try {
          const f = await get(c.url); if (!f.ok) continue;
          const type = (f.headers.get("content-type") || "").split(";")[0].trim(), ext = EXT[type] || (/\.(svg|png|webp|jpe?g)/i.exec(c.url)?.[1]);
          if (!ext) continue;
          await writeFile(path.join(dir, `logo-${++n}.${ext}`), Buffer.from(await f.arrayBuffer()));
        } catch {}
      }
      report.push(`${brand}: ${n ? n + " found" : "nothing found"}`);
    } catch (e) { report.push(`${brand}: couldn't reach the site (${e.name})`); }
  }
  await mkdir(OUT, {recursive: true});
  await writeFile(path.join(OUT, "report.txt"), report.join("\n") + "\n");
  console.log(report.join("\n"));
}
if (process.argv[1] && import.meta.url.endsWith(path.basename(process.argv[1]))) main();
