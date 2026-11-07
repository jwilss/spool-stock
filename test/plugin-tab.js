// Loads the Orca plugin's tab page in Chromium with a stand-in for Orca's bridge, feeds it a state from
// test/plugin.py, and checks the Stock and Presets views. Run by test/plugin.py.
// Usage: node test/plugin-tab.js tab.html state.json camera-replies.json|"" [screenshot folder]
const fs = require("fs");
const path = require("path");
let chromium;
try { ({chromium} = require("playwright")); } catch { ({chromium} = require(require("child_process").execSync("npm root -g").toString().trim() + "/playwright")); }

const [html, statePath, camPath, shots] = process.argv.slice(2);
const state = JSON.parse(fs.readFileSync(statePath, "utf8"));
const camReplies = camPath ? JSON.parse(fs.readFileSync(camPath, "utf8")) : [];
let failed = false;
const ok = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) failed = true; };

(async () => {
  const browser = await chromium.launch();
  for (const dark of [false, true]) {
    const p = await browser.newPage({viewport: {width: 760, height: 900}});
    const errors = [];
    p.on("pageerror", e => errors.push(String(e)));
    await p.addInitScript(replies => {
      window.__sent = [];
      let i = 0;
      // Plays back what the plugin sent for a real camera stream, one reply per request, as the plugin would
      window.orca = {postMessage: m => {
        window.__sent.push(m);
        if (m.type === "cams" && window.__on) {
          const r = replies[Math.min(i++, replies.length - 1)];
          const out = {type: "cams", cams: m.want.map(w => r && i <= replies.length ? {...r, id: w.id} : {id: w.id, status: "Live", gen: r ? r.gen : 0})};
          setTimeout(() => window.__on(out), 20);
        }
      }, onMessage: f => { window.__on = f; }};
    }, camReplies);
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
      if (camReplies.length) {
        await p.click("nav [data-v=cameras]");
        ok(await p.$$eval("#camgrid .cam", e => e.length) === 1 && /Betsy/.test(await p.textContent("#camgrid .camjob")), "Cameras shows a tile per camera, with its printer");
        let played = null;
        try {
          await p.waitForFunction(() => { const v = document.querySelector("#camgrid video"); return v && v.currentTime > 0.5 && v.videoWidth > 0; }, null, {timeout: 15000});
          played = await p.$eval("#camgrid video", v => ({w: v.videoWidth, h: v.videoHeight, t: v.currentTime}));
        } catch (e) { played = await p.$eval("#camgrid video", v => ({w: v.videoWidth, t: v.currentTime, err: v.error && v.error.message})); }
        ok(played && played.w === 320 && played.h === 240 && played.t > 0.5, "the camera's video plays in the tab: " + JSON.stringify(played));
        ok(await p.$eval("#camgrid .camst", e => e.hidden), "the status bar clears once the picture is up");
        ok((await p.evaluate(() => window.__sent.filter(m => m.type === "cams").length)) >= 3, "asks for more video while the page is open");
        await p.fill("#cname", "Alduin cam"); await p.fill("#curl", "rtsp://printer cam:pw@192.168.1.51/stream2"); await p.click("#cadd");
        const add = await p.evaluate(() => window.__sent.find(m => m.type === "cam_add"));
        ok(add && add.name === "Alduin cam" && add.url.endsWith("/stream2") && (await p.inputValue("#curl")) === "", "Add sends the camera and clears the link box");
        await p.click("#camgrid [data-a=cam_remove]");
        ok(!(await p.evaluate(() => window.__sent.some(m => m.type === "cam_remove"))) && /Remove it/.test(await p.textContent("#camgrid [data-a=cam_remove]")), "Remove asks again before it removes");
        await p.click("#camgrid [data-a=cam_remove]");
        ok(await p.evaluate(() => window.__sent.some(m => m.type === "cam_remove" && m.id === "c1")), "and removes on the second press");
        if (shots) await p.screenshot({path: path.join(shots, "tab-cameras-light.png"), fullPage: true});
        await p.click("nav [data-v=presets]");
      }
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
