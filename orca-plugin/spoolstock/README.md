# Spool Stock plugin for OrcaSlicer

Takes each print's filament off the right spools in Spool Stock, for Bambu printers in LAN mode. Needs OrcaSlicer 2.5 or later.

## How it works

1. When a printer accepts a job, it says which AMS tray or the external spool each filament comes from.
2. Straight away, the plugin reads the job file on the printer for the grams of each filament. If the printer is busy, it tries again once printing is under way.
3. It looks up which spool is in that tray using **Setup > Printers** in Spool Stock: AMS 1 is the printer's first unit, tray 1 is space 1 of the storage linked to it, and the external spool is the storage and space you chose.
4. When the print **finishes**, the grams come off those spools straight away. Each spool gets the print in its list, and **Setup > Orca plugin** in the app lists it with an **Undo** button.
5. When a print **fails or is cancelled**, it waits under **Needs you** in Orca's Spool Stock tab. It shows how far the printer got, with that percentage filled in, and the spool it used. Change anything that's wrong, then press **Take it off**, or **Skip**.

## Stock and presets

The tab has four views: **Prints** (the printers, prints waiting for you and recent results), **Stock**, **Presets** and **Cameras**.

- **Stock** shows every spool that isn't used up: its colour, a bar for what's left, and its storage and space. Search by code, brand, material, colour or storage, show only low spools (150 g or less, as in the app), and group by storage, material or brand. A spool a printer feeds from has that printer's name on it. It refreshes about once a minute, or press **Refresh**.
- **Presets** adds a **Spool Stock** group to Orca's filament list, with one preset for each filament you own. Ones Orca already has a profile for build on it unchanged. The rest build on Orca's closest generic profile with the temperatures from your stickers, the same as **Setup > Orca presets** in the app. Press **Add them to Orca** once. After that the group follows your stock: Spool Stock sends the list whenever your filaments change, and the plugin picks it up within a minute or two, or straight away with **Update now**. **Remove them from Orca** takes the group out again.

The presets live in Orca's own folder as a local preset bundle, `user\<your preset folder>\_local\spool-stock\`. Orca 2.5 builds that can reload a bundle while open show the change straight away. Older ones need a restart, and the tab says so. Copies of the same presets sent before by the Windows app are moved to `spoolstock\old-presets\`, so nothing shows twice.

## Cameras

**Cameras** plays RTSP cameras, such as Tapo ones, inside Orca. There's nothing else to install: the plugin connects to each camera and passes its video to the tab as it comes, without re-encoding it.

1. For a Tapo camera, make a Camera Account first: in the Tapo app, open the camera's settings, then **Advanced Settings**, then **Camera Account**.
2. In **Cameras**, give the camera a name, paste its link and pick the printer it watches. The link is `rtsp://username:password@CAMERA-IP/stream2`. A space, `@` or `/` in the username or password is fine as it is.
3. Press **Add**. The picture appears in a few seconds, with the printer's current print under it.

`stream2` is the lighter stream and plenty for watching prints. `stream1` is full quality. Cameras only stream while the Cameras view is open, and let go about 10 seconds after you leave it. Only H.264 video can be shown. The links, with their passwords, stay in `cameras.json` on this PC and never go to Spool Stock or the log.

## Low spool warnings

As soon as a job is sent, the plugin also checks each spool it will use. If one has less left than the print needs, or only just enough, Orca shows a warning and the tab shows it on that printer, so you can swap the spool before it runs out.

A finished print also waits under **Needs you** if a tray has no spool in Spool Stock, or the printer isn't set up there, so you can pick the spool yourself.

It works for prints sent from Orca, Bambu Studio or anything else, as the tray and the grams come from the printer. If Orca was closed when a job started, the plugin can still match a one-colour print by the tray the printer fed from. Anything else from that job waits for you.

## Install

1. In Spool Stock, open **Setup > Orca plugin** and press **Download the plugin**.
2. In Orca, open **File > Plugins**, press **Install local plugin** and choose `spool_stock.py`. Tick **Activate**. A **Spool Stock** tab appears.
3. If the Spool Stock Probe is installed, deactivate it. Each printer only takes a few connections at once.
4. Back in Spool Stock, press **Copy sign-in code**. Paste it into the tab and press **Sign in**.
5. In the tab, add each printer with the same name as in Spool Stock, its IP address and its access code (on the printer: **Settings > Network**). Orca asks whether to allow the connection: choose **Yes**.

The tab shows each printer's Spool Stock match, and while printing, which spool each filament is coming off. It follows Orca's light or dark mode.

## Privacy

- Printer IPs and access codes stay in `%APPDATA%\OrcaSlicer\spoolstock\printers.json` on this PC. They're never sent to Spool Stock.
- The sign-in is kept in `account.json` in the same folder. It can only reach your own stock. Changing your Spool Stock password cancels it.
- The only thing sent to a printer is Bambu's standard request for its status. The job file is only read.
- Camera links and passwords stay in `cameras.json` in the same folder.
- The log, `spoolstock.log`, never contains access codes, camera passwords or the sign-in.

## If Orca blocks the connection

Orca asks permission before a plugin connects to anything. If you said No, press **Connect** on a printer in the tab and choose **Yes**. If Orca only allows the connection to Spool Stock from the tab itself, the plugin notices and sends from there instead. Keep Orca open, as it does now when printing.

Tests: `python3 test/plugin.py` runs the plugin against a fake printer and a fake Spool Stock.
