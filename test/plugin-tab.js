// Loads the Orca plugin's tab page in Chromium with a stand-in for Orca's bridge, feeds it a state from
// test/plugin.py, and checks the Stock and Presets views. Run by test/plugin.py.
// Usage: node test/plugin-tab.js tab.html state.json [screenshot folder]
const fs = require("fs");
const path = require("path");
let chromium;
try { ({chromium} = require("playwright")); } catch { ({chromium} = require(require("child_process").execSync("npm root -g").toString().trim() + "/playwright")); }

const [html, statePath, shots] = process.argv.slice(2);
const state = JSON.parse(fs.readFileSync(statePath, "utf8"));
let failed = false;
const ok = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) failed = true; };

(async () => {
  const browser = await chromium.launch();
  for (const dark of [false, true]) {
    const p = await browser.newPage({viewport: {width: 760, height: 900}});
    const errors = [];
    p.on("pageerror", e => errors.push(String(e)));
    await p.addInitScript(() => {
      window.__sent = [];
      window.orca = {postMessage: m => window.__sent.push(m), onMessage: f => { window.__on = f; }};
    });
    await p.goto("file://" + path.resolve(html));
    await p.evaluate(s => window.__on(s), {...state, dark});
    if (!dark) {
      ok(await p.isVisible("#v-prints") && !(await p.isVisible("#v-stock")), "opens on Prints");
      await p.click("nav [data-v=stock]");
      const n = await p.$$eval("#stock .sp", e => e.length);
      ok(n === state.stock.spools.length, "Stock shows a card per spool: " + n);
      ok(await p.$$eval("#stock h3", e => e.length) >= 3, "grouped by storage");
      await p.fill("#q", "betsy");
      ok(await p.$$eval("#stock .sp", e => e.length) === state.stock.spools.filter(s => s.printer === "Betsy").length, "finds spools by the printer feeding from them");
      await p.fill("#q", "");
      await p.check("#lowonly");
      ok(await p.$$eval("#stock .sp", e => e.length) === state.stock.spools.filter(s => s.low).length && /1 low/.test(await p.textContent("#stockinfo")), "Low only shows the low spools");
      await p.uncheck("#lowonly");
      await p.selectOption("#grp", "mat");
      const heads = await p.$$eval("#stock h3", e => e.map(x => x.childNodes[0].textContent.trim()));
      ok(heads.join() === [...heads].sort((a, b) => a.localeCompare(b)).join() && heads.includes("PETG"), "grouped by material, in order: " + heads.join(", "));
      const bar = await p.$eval("#stock .sp .bar i", e => e.style.width);
      ok(/^\d+%$/.test(bar), "weight bar drawn: " + bar);
      await p.click("[data-a=refresh]");
      ok((await p.evaluate(() => window.__sent.map(m => m.type))).includes("refresh"), "Refresh asks for fresh stock");
      await p.click("nav [data-v=presets]");
      ok(/Spool Stock group/.test(await p.textContent("#presets")) && await p.$$eval("#presets .list div", e => e.length) === state.presets.feed.length, "Presets lists your filaments");
      await p.click("[data-a=presets_now]");
      ok((await p.evaluate(() => window.__sent.map(m => m.type))).includes("presets_now"), "Update now is sent");
      await p.reload();
      await p.evaluate(s => window.__on(s), {...state, dark});
      ok(await p.isVisible("#v-presets") && await p.inputValue("#grp") === "mat", "remembers the view and grouping");
    } else {
      await p.click("nav [data-v=stock]");
    }
    ok(!errors.length, (dark ? "dark" : "light") + ": no page errors" + (errors.length ? ": " + errors.join("; ") : ""));
    if (shots) {
      await p.click("nav [data-v=stock]");
      await p.screenshot({path: path.join(shots, `tab-stock-${dark ? "dark" : "light"}.png`), fullPage: true});
      await p.click("nav [data-v=presets]");
      await p.screenshot({path: path.join(shots, `tab-presets-${dark ? "dark" : "light"}.png`), fullPage: true});
    }
    await p.close();
  }
  await browser.close();
  process.exit(failed ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
