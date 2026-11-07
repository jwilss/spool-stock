# Spool Stock Probe

A read-only OrcaSlicer 2.5 plugin used to find out what Orca and the printers report, before the real Spool Stock plugin is built.

It changes nothing in Orca, Spool Stock or the printers. The only thing it sends a printer is Bambu's standard request for its full status. It writes one log file:

`%APPDATA%\OrcaSlicer\spoolstock-probe\probe.log`

Access codes are kept in `printers.json` beside the log, on your PC only, and are never written to the log.

## Install

1. In Orca, open **File > Plugins**, press **Install local plugin** and choose `spoolstock_probe.py`. Or put the file in `%APPDATA%\OrcaSlicer\orca_plugins\spoolstock-probe\` and restart Orca.
2. Tick **Activate** for Spool Stock Probe. A **Spool Stock Probe** tab appears.
3. To log slices too, open the process settings, then **Others > Slicing Pipeline Plugin**, and turn on **Spool Stock Probe**.

## Use

1. In the tab, add each printer with its IP and access code (on the printer: Settings > Network). The serial is optional.
2. Orca asks whether to allow the connection. Choose Yes.
3. Slice and print as normal. Let one print finish on each printer, and cancel a short one if you can.
4. Press **Log a full snapshot now**, then **Open the log folder**, and send `probe.log`.

Tests: `python3 test/probe.py`.
