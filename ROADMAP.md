# Spool Stock roadmap

A filament inventory built around QR stickers. One website, wrapped as an Android app and a Windows exe, with a separate Claude artifact for photo work.

## Files

- `site/`: the app. `index.html` holds nearly everything. `config.js` holds the addresses and Firebase details, with the API key added from a repository secret when the site publishes.
- `site/catalogue.json`: the filament catalogue, built by `tools/build-catalogue.mjs` from SpoolmanDB.
- `tools/catalogue-extra.json`: additions and corrections checked against manufacturers' own sites, applied every time the catalogue is built.
- `reader/spool-reader.html`: copy of the Spool Reader artifact.
- `wrappers/android/`: Capacitor shell around the site.
- `wrappers/windows/`: Electron shell around the site.
- `.github/workflows/`: `site.yml` adds the Firebase key from the `FIREBASE_API_KEY` secret, refreshes the catalogue and publishes the site. `apps.yml` builds the APK and exe.
- `firestore.rules`: keeps each account to its own data.
- `docs/`: images for the README, made with `test/shots.js`. The stickers image is the only one it doesn't make.
- `test/`: `run.js` for the app, `reader.js` for Spool Reader, `orca.js` for the Windows Orca bridge, `plugin.py` and `probe.py` for the Orca plugins (`plugin.py` also runs `plugin-tab.js`, which checks the tab's Stock and Presets views in Chromium). They use stand-ins for Firebase, Claude, Orca and the printers.
- `orca-plugin/spoolstock/`: the OrcaSlicer 2.5 plugin that takes each print off the spools it used.
- `orca-plugin/probe/`: the read-only test plugin used to find out what Orca and the printers report. Its README has what it found.
- `SETUP.md`: first time setup.
- `CLAUDE.md`: house rules, tests, publishing and agreed next steps, read automatically by Claude Code.

## How it fits together

- Data lives in Firestore under `users/<uid>/`: `spools`, `locations`, `brands`, `profiles`, `printers`, `jobs`, `config/main`.
- Writes land in the phone's local copy first and sync when there's signal, so the app never waits on them.
- Sticker QR codes hold the site address plus a code: `#s0012` for a spool, `#loc-top-shelf` for a storage.
- The Scan page runs a live camera: scan a spool, then a storage. A phone's normal camera also works through the link.
- A storage is one record with one sticker. `kind` picks the defaults from `STORE_TYPES`. `spaces` and `capacity` say whether spools sit in numbered spaces or loose, `group` keeps storages together, and `warn` lists materials to flag.
- A spool in a storage with spaces also has a `slot`. In a loose storage it has a `pos`, which sets its order, and a moved-in spool goes to the end. Older accounts with one record per slot are folded into one storage once, on first load.
- Spool Reader keeps its own list and hands spools over as a `spool-stock-import` JSON file. Spool Stock hands its stock back as a `spool-stock-list` file.
- Print settings come from your own saved settings first, then the catalogue, then a typical table per material. Drying starts from that table, as the catalogue has none, unless `catalogue-extra.json` gives a value. Your edits are remembered per filament.
- The catalogue lists manufacturers and their product names. It's fetched once and kept for offline use. Saved names join the list for that manufacturer. A filament's other names (`a`, one or a list) are searchable too. Searches treat HS as high speed, HF as high flow, CF as carbon fibre and GF as glass fibre.
- Catalogue lines that OrcaSlicer's filament library has a profile for carry its name (`o`), read from inside each profile file, as a few file names differ from the preset (`Bambu Support For PLA-PETG @System.json` holds `Bambu Support For PLA/PETG @System`; the printer versions are corrected the same way), and the library's generic profiles are listed in `orca.generic`. Owned filaments with an Orca profile use it as it is. The rest get a user preset that inherits a generic profile, with the app's temperatures. The Windows app's `preload.js` and `orca.js` let the site write those presets into `%APPDATA%\OrcaSlicer\user\<folder>\filament`, and set the `filaments` section of `OrcaSlicer.conf` to the exact Orca preset names the catalogue lists for your filaments (`orca.variants`, read from Orca's source, Bambu printer versions included, as newer Orca packs its profiles into one file per brand) (with a fresh MD5 checksum line) to every installed variant of your filaments, after backing it up to `OrcaSlicer.conf.spoolstock.bak`. It refuses while `orca-slicer.exe` is running, accepts only filament user presets, and only from the Spool Stock site.
- Printers live in `printers`: `{name, serial, feeds: [{kind: "ams", unit: 1, loc}, {kind: "external", loc, space}]}`. `SpoolStock.trayToSpool(printer, unit, tray)` turns the printer's own numbering (AMS unit and tray from 0, or "external") into the spool in that space. Printer IPs and access codes never go to Firebase.
- The app keeps `config/orca` up to date for the plugin: `{v, sig, at, items}`, one item per owned filament with `name`, `label`, `own`, `parents` (Orca profiles it can build on, in order) and `text` (the preset JSON, as the download zip has it). It's rewritten whenever its signature changes, once the catalogue and the live data have loaded.
- The plugin's Presets view writes those as a local preset bundle, `user/<folder>/_local/spool-stock/` with `bundle_metadata.json` and `filament/<name>.json`, which Orca shows as its own group. The folder comes from a user preset's file path, then `preset_folder` in `OrcaSlicer.conf`. Each preset inherits the first of its `parents` Orca has (`find_preset`), keeps only plain setting keys (nothing that limits printers or reaches one), and then `orca.host.preset.bundles.reload_local("spool-stock")` makes it live. Without that call Orca needs a restart. Same-named presets from the Windows app in `user/<folder>/filament` are moved to `spoolstock/old-presets`. State is in `spoolstock/presets.json`, and each run logs `presets.sync`.
- The Orca plugin (`spool_stock.py`, one Python file) keeps a status connection to each printer (MQTT on 8883, user `bblp` and the access code, subscribed to that printer's own topic, serial read from its certificate). The printer's `project_file` message gives `ams_mapping2` (`{ams_id, slot_id}` per filament, `ams_id` 255 for the external spool). As soon as the job is sent (and again once printing is under way, if that fails) it reads the job's `.3mf` from the printer over implicit FTPS on 990 and takes `used_g` per filament from `slice_info.config`. The same trays-to-spool rules as `trayToSpool` find the spools. A finished print is written in one Firestore commit: each spool's `remainG` and `prints`, checked against the spool's last update, plus `jobs/<serial>-<ms>`, which must not exist yet, so nothing is taken off twice. A failed (`gcode_state` FAILED) or cancelled (`print_error` 50348044) print waits in the tab with the printer's `mc_percent`. The app's Undo puts the grams back and removes the matching print entry.
- The plugin signs in with a code from Setup: `SS1.` and base64 JSON of the web API key, project, user ID and the browser's refresh token. It swaps that for ID tokens and uses Firestore's REST interface. Orca's audit permissions only prompt on its main thread, so the printers and Google's hosts are reached once from the tab first. If background calls are still refused, the tab's two-second poll does the sending. Its state (`jobs.json`) survives a restart of Orca mid-print.
- `site.yml` rebuilds the catalogue from SpoolmanDB, the Open Filament Database and OrcaSlicer's filament library on every publish and once a week. If that fails, the committed copy is used.
- The Open Filament Database (MIT) is merged in for every brand. Where a filament matches by name, its manufacturer ranges, drying, spool weight and colours fill in what SpoolmanDB has. Filaments and manufacturers SpoolmanDB lacks are added. Names are matched on their words, so CF and carbon fiber, or a missing "(NFC)", still match. That gives about 195 manufacturers and 2,200 filaments. `catalogue-extra.json` is applied last, so hand checked values win.
- Logos are found once and stored per manufacturer in your account: Simple Icons for exact vector matches, Wikimedia Commons as choices, or your own upload. All are turned solid black on transparent. No API keys.
- Spool rings are sized like a real 1 kg roll: the filament left is the area between the core and the outside, so a ring shrinks slowly at first and quickly near the core. Anything over 1 kg draws as full.
- Stickers are black except for the colour box: solid, triangles for split colours, or a left to right blend for gradients.
- The Firebase key isn't in the repo or its history. Browsers still need it, so it can be read in `config.js` on the live site. The key is restricted to Firebase services, sign-ups are off, and `firestore.rules` protects the data.

## Done

- [x] Stock view with each storage drawn with its spools, rings showing colour and weight left
- [x] Add by hand, or import from Spool Reader
- [x] Sticker PDF for A4 sheets, remembering the next free sticker
- [x] Storage stickers
- [x] Live camera scanning, plus photo and typed code as fallbacks
- [x] Storage built from scratch, with types, groups, spaces, limits and material warnings
- [x] One storage with numbered spaces, swapping when full, and moving between spaces
- [x] Reorder storages
- [x] Owned filaments in Orca: Orca's own profile where one matches, a generic-based preset with your temperatures where not, and Orca's filament list set to just your filaments from the Windows app, with backup and restore
- [x] Orca plugin: finished prints taken off the spools in the trays they used, failed and cancelled ones confirmed in Orca's tab with how far they got, and Undo in the app
- [x] Orca plugin warns as a job starts if a spool it uses has less left than the print needs, and follows Orca's light or dark mode
- [x] Stock view in the Orca plugin's tab: spool cards with colour, weight bar and storage, with search, low only, and grouping by storage, material or brand
- [x] Live preset sync: the plugin keeps a Spool Stock group in Orca's filament list from the app's list of owned filaments, reloaded while Orca is open
- [x] Log a print from an Orca or Bambu Studio sliced file (.gcode.3mf) or G-code, matching filaments to spools, with a percentage for failed prints, a print list per spool and Undo
- [x] Printers in the app, with what feeds each one (AMS units and the external spool mapped to storages and spaces)
- [x] Arrange the spools inside a storage: spaces for a unit or dryer, a set order for shelves and boxes
- [x] Add several storages at once, and remove all of them to start again
- [x] Undo, and a warning when a spool wants hotter than a dryer gives
- [x] Works with no signal
- [x] CSV and JSON download
- [x] Spool Reader: batch label reading, logo cropping, identify against stock
- [x] Automatic logo lookup
- [x] Black stickers with split and gradient colour boxes
- [x] Firebase key moved to a repository secret and cleared from the history
- [x] Square camera viewfinder that frames the QR code
- [x] Search manufacturers, then their own filament names, filling settings, spool weight and colours

## Check on first use

- [ ] Publish run with the `FIREBASE_API_KEY` secret, then sign in on the live site
- [ ] First run of Build apps
- [ ] Old `build-1` release and tag deleted
- [ ] Older account's slots folded into one storage on the real data
- [ ] Weekly catalogue refresh running in GitHub Actions
- [x] Run the Orca probe plugin through a print on Alduin and Betsy, and send the log
- [x] Probe 0.3.1 through a print on each printer: tray mapping, job file and grams all confirmed
- [ ] Orca plugin on the real printers: a finished print on each, and a cancelled one
- [ ] Orca's warning notification and dark mode in the plugin tab, on the real Orca
- [x] Plugin 1.2.0 on the real Orca: **Add them to Orca** loaded the Spool Stock group live, with 22 filaments, and moved 12 Windows app copies aside
- [ ] Bambu Lab Support for PLA/PETG in the Spool Stock group, after the catalogue fix
- [ ] Stock view on the real Orca
- [ ] Log a print with a real Orca export from Alduin and Betsy
- [ ] Import the Orca presets zip into Orca, and Send to Orca from the rebuilt Windows app, including the filament list
- [ ] Camera inside the APK
- [ ] Sticker alignment on a test sheet
- [ ] Stickers scanning once stuck to a spool
- [ ] Real label photos in Spool Reader
- [ ] Logo lookup against the real services
- [ ] Colour box on real printed stickers

## Next

- [ ] Restore from a JSON backup
- [ ] Low stock shopping list
- [ ] Add drying settings per filament, and fill catalogue gaps by sending additions to SpoolmanDB
- [ ] Save PDFs from the Android app
- [ ] Open sticker links straight in the app instead of the browser

## Ideas

- [ ] Names for spaces in a storage, such as Left and Right
- [ ] Firebase App Check to cut down junk requests
- [ ] Show prints waiting in Orca's tab in the app too
- [ ] Install the plugin from the Windows app
- [ ] Thermal label printer output
- [ ] NFC tags as a one tap alternative to QR
