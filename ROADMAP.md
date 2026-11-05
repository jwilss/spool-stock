# Spool Stock roadmap

Filament stock for Alduin (left P1S, fed by the Space Pi Plus) and Betsy (right P1S, fed by AMS 1 and AMS 2), plus the two tier rack. One website, wrapped as an Android app and a Windows exe, with a separate Claude artifact for photo work.

## Files

- `site/`: the app. `index.html` holds nearly everything. `config.js` holds the addresses and Firebase details.
- `reader/spool-reader.html`: copy of the Spool Reader artifact.
- `wrappers/android/`: Capacitor shell around the site.
- `wrappers/windows/`: Electron shell around the site.
- `.github/workflows/`: `site.yml` publishes the site, `apps.yml` builds the APK and exe.
- `firestore.rules`: keeps each account to its own data.
- `test/`: `run.js` for the app, `reader.js` for Spool Reader. Both use stand-ins for Firebase and Claude.
- `SETUP.md`: first time setup.

## How it fits together

- Data lives in Firestore under `users/<uid>/`: `spools`, `locations`, `brands`, `profiles`, `config/main`.
- Writes land in the phone's local copy first and sync when there's signal, so the app never waits on them.
- Sticker QR codes hold the site address plus a code: `#s0012` for a spool, `#loc-rack-top` for a storage.
- The Scan page runs a live camera: scan a spool, then a storage. A phone's normal camera also works through the link.
- Spool Reader keeps its own list and hands spools over as a `spool-stock-import` JSON file. Spool Stock hands its stock back as a `spool-stock-list` file.
- Print settings start from Bambu Lab's filament table, then the label, then your edits, remembered per filament.
- Logos come from your own photos or files.

## Done

- [x] Stock view with storages drawn as racks, rings showing colour and weight left
- [x] Add by hand, or import from Spool Reader
- [x] Sticker PDF for A4 sheets, remembering the next free sticker
- [x] Storage stickers
- [x] Live camera scanning, plus photo and typed code as fallbacks
- [x] Undo, swaps in AMS slots, TPU warning for the AMS, Space Pi 70 C warning
- [x] Works with no signal
- [x] CSV and JSON download
- [x] Spool Reader: batch label reading, logo cropping, identify against stock

## Check on first use

- [ ] Sign in and sync against the real Firebase project
- [ ] First run of both GitHub Actions workflows
- [ ] Camera inside the APK
- [ ] Sticker alignment on a test sheet
- [ ] Stickers scanning once stuck to a spool
- [ ] Real label photos in Spool Reader

## Next

- [ ] Rename an AMS group from Setup
- [ ] Restore from a JSON backup
- [ ] Low stock shopping list
- [ ] Reorder storages by dragging
- [ ] Save PDFs from the Android app
- [ ] Open sticker links straight in the app instead of the browser

## Ideas

- [ ] Filament used per print from Bambu's print history, if a safe route exists
- [ ] Thermal label printer output
- [ ] NFC tags as a one tap alternative to QR
