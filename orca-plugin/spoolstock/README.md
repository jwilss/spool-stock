# Spool Stock plugin for OrcaSlicer

Takes each print's filament off the right spools in Spool Stock, for Bambu printers in LAN mode. Needs OrcaSlicer 2.5 or later.

## How it works

1. When a printer accepts a job, it says which AMS tray or the external spool each filament comes from.
2. Once printing is under way, the plugin reads the job file on the printer for the grams of each filament.
3. It looks up which spool is in that tray using **Setup > Printers** in Spool Stock: AMS 1 is the printer's first unit, tray 1 is space 1 of the storage linked to it, and the external spool is the storage and space you chose.
4. When the print **finishes**, the grams come off those spools straight away. Each spool gets the print in its list, and **Setup > Orca plugin** in the app lists it with an **Undo** button.
5. When a print **fails or is cancelled**, it waits under **Needs you** in Orca's Spool Stock tab. It shows how far the printer got, with that percentage filled in, and the spool it used. Change anything that's wrong, then press **Take it off**, or **Skip**.

A finished print also waits under **Needs you** if a tray has no spool in Spool Stock, or the printer isn't set up there, so you can pick the spool yourself.

It works for prints sent from Orca, Bambu Studio or anything else, as the tray and the grams come from the printer. If Orca was closed when a job started, the plugin can still match a one-colour print by the tray the printer fed from. Anything else from that job waits for you.

## Install

1. In Spool Stock, open **Setup > Orca plugin** and press **Download the plugin**.
2. In Orca, open **File > Plugins**, press **Install local plugin** and choose `spool_stock.py`. Tick **Activate**. A **Spool Stock** tab appears.
3. If the Spool Stock Probe is installed, deactivate it. Each printer only takes a few connections at once.
4. Back in Spool Stock, press **Copy sign-in code**. Paste it into the tab and press **Sign in**.
5. In the tab, add each printer with the same name as in Spool Stock, its IP address and its access code (on the printer: **Settings > Network**). Orca asks whether to allow the connection: choose **Yes**.

The tab shows each printer's Spool Stock match, and while printing, which spool each filament is coming off.

## Privacy

- Printer IPs and access codes stay in `%APPDATA%\OrcaSlicer\spoolstock\printers.json` on this PC. They're never sent to Spool Stock.
- The sign-in is kept in `account.json` in the same folder. It can only reach your own stock. Changing your Spool Stock password cancels it.
- The only thing sent to a printer is Bambu's standard request for its status. The job file is only read.
- The log, `spoolstock.log`, never contains access codes or the sign-in.

## If Orca blocks the connection

Orca asks permission before a plugin connects to anything. If you said No, press **Connect** on a printer in the tab and choose **Yes**. If Orca only allows the connection to Spool Stock from the tab itself, the plugin notices and sends from there instead. Keep Orca open, as it does now when printing.

Tests: `python3 test/plugin.py` runs the plugin against a fake printer and a fake Spool Stock.
