# Working on Spool Stock

Jack's filament inventory. Read `ROADMAP.md` first: it has the file map, how everything fits together, what's done, what still needs checking on real hardware, and what's next. `README.md`, `SETUP.md` and the READMEs under `orca-plugin/` cover the rest.

## House rules

- Commit as `jwilss` (`git -c user.name=jwilss -c user.email=jwilss@users.noreply.github.com commit ...`), never Jack's full name.
- UK spelling. No em dashes in docs, UI text or commit messages. Plain, direct wording, written for Jack rather than about him.
- No manufacturer logos in this repo. They live in each user's Firestore data.
- The Firebase API key is never committed. `site/config.js` keeps `__FIREBASE_API_KEY__`, and `site.yml` fills it from the `FIREBASE_API_KEY` secret.
- Printer IPs and access codes never go to Firebase or into a log. They stay in the Orca plugin's folder on Jack's PC.
- No API keys for logo lookups.
- Keep the app one HTML file with no framework.
- Update `ROADMAP.md` (and the README if it's user-facing) with each change, and tick off what's done.

## Tests

Run them before pushing. All should pass.

```
node test/run.js        # the app, in Chromium with a stand-in Firebase (needs playwright)
node test/reader.js     # Spool Reader
node test/orca.js       # the Windows app's Orca preset bridge
python3 test/plugin.py  # the Orca plugin, against a fake printer, printer file server and Firestore
python3 test/probe.py   # the probe plugin
```

`test/shots.js` remakes the README images in `docs/`.

## Publishing

Pushing to `main` publishes the site through `.github/workflows/site.yml` (only when `site/`, `orca-plugin/spoolstock/` or the workflow changes). It also copies the Orca plugin to the site as `spool_stock.py` and rebuilds the catalogue. Check the run succeeded with `gh run list --workflow site.yml`. The Android APK and Windows exe build from **Actions > Build apps**. Both apps load the live site, so most changes need no rebuild. Only `wrappers/` changes do.

## Jack's setup

- Two Bambu Lab P1S printers in LAN mode with developer mode, printing from OrcaSlicer 2.5 on Windows.
  - Betsy is fed by two AMS units.
  - Alduin is fed from the external spool, which sits in space 1 of his Space Pi dryer.
  - Serials, IPs and access codes aren't kept in this public repo. They're in Spool Stock (Setup > Printers) and the plugin's tab.
- The Orca plugin has full access in Orca, so its background threads can reach the network.
- Plugin data on his PC: `%APPDATA%\OrcaSlicer\spoolstock\` (`spoolstock.log`, `jobs.json`, `printers.json`, `account.json`).

## What the printers report (confirmed with the probe, Nov 2026)

- The tray for each filament comes only in the `project_file` message when a job is sent: `ams_mapping2` is `{ams_id, slot_id}` per filament, and `ams_id` 255 with `use_ams` false means the external spool. Status reports never repeat it.
- `tray_now` is the global tray (AMS unit times 4 plus tray), 254 for external, 255 for none.
- Grams per filament are only in the job's `.3mf` on the printer (`slice_info.config`, `used_g`), read over implicit FTPS on port 990. The AMS reports `remain` -1.
- Orca refuses plugins opening its own temporary G-code, and its `PrintStateChanged` events only cover the printer selected in Orca.
- `orca.host.ui.push_notification` exists (levels such as `WarningNotificationLevel`). The plugin calls it only from the tab's messages, on Orca's main thread, and logs which argument order worked (`notify.shown` in `spoolstock.log`). Dark mode comes from `dark_color_mode` in the `app` section of `OrcaSlicer.conf`.

## Next, agreed with Jack

Done: the low-filament warning and Orca's theme in the tab (plugin 1.1.0). Jack doesn't want a reorder or buy button.

1. Stock view inside the Orca tab: spool cards with colour, weight bar, filter and group.
2. Live preset sync from the plugin with Orca open (FilamentHub's approach: presets in `user/<folder>/_local/<bundle>/filament` with `bundle_metadata.json`, then `orca.host.preset.bundles.reload_local`). Unproven on his Orca, so probe it first.
