# Spool Stock Probe

A read-only OrcaSlicer 2.5 plugin used to find out what Orca and the printers report, before the real Spool Stock plugin is built.

It changes nothing in Orca, Spool Stock or the printers. The only thing it sends a printer is Bambu's standard request for its full status. It writes one log file:

`%APPDATA%\OrcaSlicer\spoolstock-probe\probe.log`

Access codes are kept in `printers.json` beside the log, on your PC only, and are never written to the log.

## Install

1. In Orca, open **File > Plugins**, press **Install local plugin** and choose `spoolstock_probe.py`. Or put the file in `%APPDATA%\OrcaSlicer\orca_plugins\spoolstock-probe\` and restart Orca.
2. Tick **Activate** for Spool Stock Probe. A **Spool Stock Probe** tab appears.
3. Slices are logged without anything else turned on: when Orca finishes writing the G-code, the probe reads the grams and filament presets from it. The **Slicing Pipeline Plugin** setting under the process settings' **Others** isn't needed.

## What the first run showed

From a two-colour print on a P1S with two AMS units, and an external-spool print on another P1S:

- **Which tray each filament used:** the printer's `project_file` message, and every status report after it, carry `ams_mapping` (`[0, 1]` means filament 1 from AMS 1 tray 1 and filament 2 from AMS 1 tray 2) and `ams_mapping2` (`{ams_id, slot_id}` per filament). These stay until the next job. External spool prints show `tray_now` 254.
- **Proof it happened:** `tray_now` moves 0, then 255 while changing, then 1, then 255 at the end.
- **Grams:** not in the printer's reports. The AMS shows `remain` as -1 even for Bambu RFID spools, so the grams come from the sliced file.
- **Orca's events:** `PrintJobStarted` and `PrintJobFinished` mean the upload, not the print, and carry the printer's serial in `device_id`. `PrintStateChanged` gives PREPARE, RUNNING, PAUSE, FINISH and IDLE with the serial in `name`, but only for the printer selected in Orca. `GCodeExportFinished` gives the path of the G-code Orca just wrote.
- **Job file:** reading it over FTPS while the printer was still preparing timed out, so 0.3.0 waits until layer 1 and tries again at the end, up to three times each.
- **The slicing step** never ran, so 0.3.0 reads the G-code from the export event instead.

## Use

1. In the tab, add each printer with its IP and access code (on the printer: Settings > Network). The serial is optional.
2. Orca asks whether to allow the connection. Choose Yes.
3. Slice and print as normal. Let one print finish on each printer, and cancel a short one if you can.
4. Press **Log a full snapshot now**, then **Open the log folder**, and send `probe.log`.

Tests: `python3 test/probe.py`.
