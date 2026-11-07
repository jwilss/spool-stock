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

From a two-colour PETG print on a P1S fed by two AMS units, then PETG from the AMS on that printer and ASA from the external spool on another P1S at the same time:

- **Which tray each filament used:** only in the printer's `project_file` message, sent once when the job starts. It carries `ams_mapping` (`[0, 1]` means filament 1 from AMS 1 tray 1 and filament 2 from AMS 1 tray 2) and `ams_mapping2` (`{ams_id, slot_id}` per filament). The status reports never include it, so a plugin that connects after the job starts misses it and has to go by `tray_now`. External spool prints show `tray_now` 254.
- **Proof it happened:** `tray_now` moves 0, then 255 while changing, then 1, then 255 at the end.
- **Grams:** not in the printer's reports. The AMS shows `remain` as -1 even for Bambu RFID spools, so the grams come from the sliced file.
- **Orca's events:** `PrintJobStarted` and `PrintJobFinished` mean the upload, not the print, and carry the printer's serial in `device_id`. `PrintStateChanged` gives PREPARE, RUNNING, PAUSE, FINISH and IDLE with the serial in `name`, but only for the printer selected in Orca. `GCodeExportFinished` gives the path of the G-code Orca just wrote.
- **Job file:** 0.2 and 0.3.0 timed out because Bambu's file server starts encryption before it says hello, and the probe waited for the hello first. 0.3.1 fixed that and it now takes about 3 seconds. The file sits in the printer's top folder under the job's name, and is overwritten by the next job with the same name. Its `slice_info.config` gives each filament's type, colour, Bambu filament code and grams (`used_g`), so this is where the grams come from.
- **External spool:** a job from the external spool sends `ams_mapping` `[-1]`, `ams_mapping2` `{ams_id: 255}` and `use_ams` false.
- **Two printers at once:** both reports came through side by side with no trouble. Orca's own print states follow only the printer selected in Orca, switching when you send to the other one.
- **Which slice was sent:** Orca reuses one temporary G-code path and overwrites it on every slice, so the slice that was sent is the last one exported before `PrintJobStarted`, and it has to be read straight away.
- **Orca's G-code:** the slicing step never ran, and opening the exported G-code from the export event was refused by Orca's permission check. Not needed, as the job file on the printer has the grams.

## Use

1. In the tab, add each printer with its IP and access code (on the printer: Settings > Network). The serial is optional.
2. Orca asks whether to allow the connection. Choose Yes.
3. Slice and print as normal. Let one print finish on each printer, and cancel a short one if you can.
4. Press **Log a full snapshot now**, then **Open the log folder**, and send `probe.log`.

Tests: `python3 test/probe.py`.
