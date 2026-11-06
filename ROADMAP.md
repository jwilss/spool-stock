# Spool Stock roadmap

A filament inventory built around QR stickers. One website, wrapped as an Android app and a Windows exe, with a separate Claude artifact for photo work.

## Files

- `site/`: the app. `index.html` holds nearly everything. `config.js` holds the addresses and Firebase details.
- `reader/spool-reader.html`: copy of the Spool Reader artifact.
- `wrappers/android/`: Capacitor shell around the site.
- `wrappers/windows/`: Electron shell around the site.
- `.github/workflows/`: `site.yml` publishes the site, `apps.yml` builds the APK and exe.
- `firestore.rules`: keeps each account to its own data.
- `docs/`: images for the README, made with `test/shots.js`.
- `test/`: `run.js` for the app, `reader.js` for Spool Reader. Both use stand-ins for Firebase and Claude.
- `SETUP.md`: first time setup.

## How it fits together

- Data lives in Firestore under `users/<uid>/`: `spools`, `locations`, `brands`, `profiles`, `config/main`.
- Writes land in the phone's local copy first and sync when there's signal, so the app never waits on them.
- Sticker QR codes hold the site address plus a code: `#s0012` for a spool, `#loc-top-shelf` for a storage.
- The Scan page runs a live camera: scan a spool, then a storage. A phone's normal camera also works through the link.
- A storage is one record with one sticker. `kind` picks the defaults from `STORE_TYPES`. `spaces` and `capacity` say whether spools sit in numbered spaces or loose, `group` keeps storages together, and `warn` lists materials to flag.
- A spool in a storage with spaces also has a `slot`. Older accounts with one record per slot are folded into one storage once, on first load.
- Spool Reader keeps its own list and hands spools over as a `spool-stock-import` JSON file. Spool Stock hands its stock back as a `spool-stock-list` file.
- Print settings start from a typical table per material, then the label, then your edits, remembered per filament.
- Logos are found once and stored per manufacturer in your account: Simple Icons for exact vector matches, Wikimedia Commons as choices, or your own upload. All are turned solid black on transparent. No API keys.
- Stickers are black except for the colour box: solid, triangles for split colours, or a left to right blend for gradients.

## Done

- [x] Stock view with each storage drawn with its spools, rings showing colour and weight left
- [x] Add by hand, or import from Spool Reader
- [x] Sticker PDF for A4 sheets, remembering the next free sticker
- [x] Storage stickers
- [x] Live camera scanning, plus photo and typed code as fallbacks
- [x] Storage built from scratch, with types, groups, spaces, limits and material warnings
- [x] One storage with numbered spaces, swapping when full, and moving between spaces
- [x] Reorder storages
- [x] Undo, and a warning when a spool wants hotter than a dryer gives
- [x] Works with no signal
- [x] CSV and JSON download
- [x] Spool Reader: batch label reading, logo cropping, identify against stock
- [x] Automatic logo lookup
- [x] Black stickers with split and gradient colour boxes

## Check on first use

- [ ] First run of both GitHub Actions workflows
- [ ] Camera inside the APK
- [ ] Sticker alignment on a test sheet
- [ ] Stickers scanning once stuck to a spool
- [ ] Real label photos in Spool Reader
- [ ] Logo lookup against the real services
- [ ] Colour box on real printed stickers

## Next

- [ ] Restore from a JSON backup
- [ ] Low stock shopping list
- [ ] Save PDFs from the Android app
- [ ] Open sticker links straight in the app instead of the browser

## Ideas

- [ ] Filament used per print, from printer history if a safe route exists
- [ ] Thermal label printer output
- [ ] NFC tags as a one tap alternative to QR
