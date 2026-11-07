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
- `test/`: `run.js` for the app, `reader.js` for Spool Reader. Both use stand-ins for Firebase and Claude.
- `SETUP.md`: first time setup.

## How it fits together

- Data lives in Firestore under `users/<uid>/`: `spools`, `locations`, `brands`, `profiles`, `config/main`.
- Writes land in the phone's local copy first and sync when there's signal, so the app never waits on them.
- Sticker QR codes hold the site address plus a code: `#s0012` for a spool, `#loc-top-shelf` for a storage.
- The Scan page runs a live camera: scan a spool, then a storage. A phone's normal camera also works through the link.
- A storage is one record with one sticker. `kind` picks the defaults from `STORE_TYPES`. `spaces` and `capacity` say whether spools sit in numbered spaces or loose, `group` keeps storages together, and `warn` lists materials to flag.
- A spool in a storage with spaces also has a `slot`. In a loose storage it has a `pos`, which sets its order, and a moved-in spool goes to the end. Older accounts with one record per slot are folded into one storage once, on first load.
- Spool Reader keeps its own list and hands spools over as a `spool-stock-import` JSON file. Spool Stock hands its stock back as a `spool-stock-list` file.
- Print settings come from your own saved settings first, then the catalogue, then a typical table per material. Drying starts from that table, as the catalogue has none, unless `catalogue-extra.json` gives a value. Your edits are remembered per filament.
- The catalogue lists manufacturers and their product names. It's fetched once and kept for offline use. Saved names join the list for that manufacturer. A filament's other names (`a`, one or a list) are searchable too. Searches treat HS as high speed, HF as high flow, CF as carbon fibre and GF as glass fibre.
- `site.yml` rebuilds the catalogue from SpoolmanDB on every publish and once a week. If that fails, the committed copy is used.
- The Open Filament Database (MIT) is merged in for every brand. Where a filament matches by name, its manufacturer ranges, drying, spool weight and colours fill in what SpoolmanDB has. Filaments and manufacturers SpoolmanDB lacks are added. Names are matched on their words, so CF and carbon fiber, or a missing "(NFC)", still match. That gives about 195 manufacturers and 2,200 filaments. `catalogue-extra.json` is applied last, so hand checked values win.
- Logos are found once and stored per manufacturer in your account: Simple Icons for exact vector matches, Wikimedia Commons as choices, or your own upload. All are turned solid black on transparent. No API keys.
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
- [x] Log a print from an Orca or Bambu Studio sliced file (.gcode.3mf) or G-code, matching filaments to spools, with a percentage for failed prints, a print list per spool and Undo
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
- [ ] Log a print with a real Orca export from Alduin and Betsy
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
- [ ] Filament used per print, from printer history if a safe route exists
- [ ] Thermal label printer output
- [ ] NFC tags as a one tap alternative to QR
