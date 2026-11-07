# /// script
# requires-python = ">=3.12"
# dependencies = []
#
# [tool.orcaslicer.plugin]
# id = "spoolstock-probe"
# name = "Spool Stock Probe"
# description = "Read-only test for Spool Stock: logs what Orca and your Bambu printers report about AMS slots, slicing and prints."
# author = "jwilss"
# version = "0.2.0"
# ///
"""Spool Stock Probe.

Read-only. It sends nothing to the printers except Bambu's standard "pushall" status request,
changes nothing in Orca or Spool Stock, and writes one log file:
    %APPDATA%/OrcaSlicer/spoolstock-probe/probe.log

It records:
  * what Orca's plugin interface offers (names only),
  * every Orca lifecycle event and its fields,
  * the filament slots of each slice (preset, colour, material, vendor, grams),
  * each printer's status over the local network (AMS trays, external spool, job, progress,
    and anything that looks like a tray mapping),
  * the sliced job file on the printer (per-filament grams from slice_info.config).
Access codes are never written to the log.
"""

import ftplib
import io
import json
import os
import re
import socket
import ssl
import struct
import sys
import threading
import time
import traceback
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import orca

NAME = "Spool Stock Probe"
VERSION = "0.2.0"
MQTT_PORT, FTP_PORT = 8883, 990
PUSHALL = '{"pushing": {"sequence_id": "0", "command": "pushall"}}'
LOG_MAX = 8 * 1024 * 1024
JOB_MAX = 96 * 1024 * 1024


# ---------- files ----------

def _data_dir() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if parent.name == "OrcaSlicer":
            return parent / "spoolstock-probe"
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "OrcaSlicer" / "spoolstock-probe"


DATA = _data_dir()
LOG = DATA / "probe.log"
SETTINGS = DATA / "printers.json"
_log_lock = threading.Lock()
_secrets: set[str] = set()
_recent: list[str] = []


def _redact(text: str) -> str:
    for s in _secrets:
        if s:
            text = text.replace(s, "********")
    return text


def log(kind: str, **data) -> None:
    """One JSON line per entry. Never raises."""
    try:
        line = json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind, **data}, default=str, ensure_ascii=False)
        line = _redact(line)
        with _log_lock:
            DATA.mkdir(parents=True, exist_ok=True)
            if LOG.exists() and LOG.stat().st_size > LOG_MAX:
                LOG.replace(LOG.with_suffix(".old.log"))
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            _recent.append(f"{time.strftime('%H:%M:%S')} {kind}")
            del _recent[:-40]
    except Exception:
        pass


def load_printers() -> list[dict]:
    try:
        items = json.loads(SETTINGS.read_text(encoding="utf-8"))
        for p in items:
            if p.get("code"):
                _secrets.add(p["code"])
        return items
    except Exception:
        return []


def save_printers(items: list[dict]) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(items, indent=2), encoding="utf-8")
    try:
        os.chmod(SETTINGS, 0o600)
    except OSError:
        pass


# ---------- what Orca offers ----------

def _names(obj) -> list[str]:
    try:
        return sorted(n for n in dir(obj) if not n.startswith("_"))
    except Exception:
        return []


def _fields(ctx) -> dict:
    out = {}
    for n in _names(ctx):
        try:
            v = getattr(ctx, n)
            if callable(v):
                continue
            out[n] = v if isinstance(v, (int, float, bool, type(None))) else str(v)[:500]
        except Exception as e:
            out[n] = f"<{type(e).__name__}>"
    return out


def survey_api() -> dict:
    """Names of everything the plugin interface exposes, a few levels down. No values."""
    seen = {}
    def walk(obj, path, depth):
        if depth > 3 or path in seen:
            return
        names = _names(obj)
        seen[path] = names
        for n in names:
            try:
                child = getattr(obj, n)
            except Exception:
                continue
            if type(child).__name__ in ("module",) or (hasattr(child, "__dict__") and not callable(child) and depth < 3):
                walk(child, f"{path}.{n}", depth + 1)
    walk(orca, "orca", 0)
    return seen


def orca_printer_info() -> dict:
    """The current printer preset and anything about devices Orca's preset bundle shows."""
    out = {}
    try:
        bundle = orca.host.preset_bundle()
        out["bundle_api"] = _names(bundle)
        cur = bundle.current_printer_preset()
        out["printer_preset"] = getattr(cur, "name", "")
        for key in ("printer_model", "print_host", "printer_variant", "host_type", "nozzle_diameter"):
            try:
                out[key] = str(cur.config_value(key))[:200]
            except Exception as e:
                out[key] = f"<{type(e).__name__}>"
        hosts = []
        for p in bundle.printers:
            try:
                h = str(p.config_value("print_host") or "")
            except Exception:
                h = ""
            if h:
                hosts.append({"preset": getattr(p, "name", ""), "print_host": h[:200]})
        out["print_hosts"] = hosts
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def orca_conf_hints() -> dict:
    """Section names of OrcaSlicer.conf, and for device-looking sections their keys only."""
    out = {}
    try:
        conf = DATA.parent / "OrcaSlicer.conf"
        text = conf.read_text(encoding="utf-8")
        j = json.loads(text[: text.rfind("}") + 1])
        out["sections"] = sorted(j.keys())
        for sec, val in j.items():
            if isinstance(val, dict) and re.search(r"access|lan|ip|device|dev_|printer|machine", sec, re.I):
                out[sec] = {"keys": sorted(val.keys())[:50]}
        app = j.get("app", {})
        out["app_keys_device"] = sorted(k for k in app if re.search(r"access|lan|ip|device|dev_|printer|machine|user", k, re.I))[:80]
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


# ---------- printers over the local network ----------

def _mlen(n: int) -> bytes:
    out = bytearray()
    while True:
        d, n = n % 128, n // 128
        out.append(d | (128 if n else 0))
        if not n:
            return bytes(out)


def _mstr(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack("!H", len(b)) + b


def _pkt(first: int, body: bytes) -> bytes:
    return bytes([first]) + _mlen(len(body)) + body


def _recv_exact(sock, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except socket.timeout:
            continue
        if not chunk:
            raise ConnectionError("printer closed the connection")
        buf += chunk
    return buf


def _read_packet(sock):
    try:
        first = sock.recv(1)
    except socket.timeout:
        return None
    if not first:
        raise ConnectionError("printer closed the connection")
    size, shift = 0, 0
    while True:
        b = _recv_exact(sock, 1)[0]
        size |= (b & 127) << shift
        shift += 7
        if not b & 128:
            break
    return first[0], (_recv_exact(sock, size) if size else b"")


def _merge(base: dict, new: dict) -> None:
    for k, v in new.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v


def summarise(status: dict) -> dict:
    """The parts of a Bambu status report that matter for spools."""
    p = status.get("print", {}) if isinstance(status.get("print"), dict) else {}
    ams = p.get("ams") or {}
    trays = []
    for unit in ams.get("ams") or []:
        for tray in unit.get("tray") or []:
            trays.append({
                "ams": unit.get("id"), "tray": tray.get("id"),
                "type": tray.get("tray_type"), "colour": tray.get("tray_color"),
                "info_idx": tray.get("tray_info_idx"), "sub_brand": tray.get("tray_sub_brands"),
                "remain": tray.get("remain"), "rfid": bool(tray.get("tag_uid") and tray.get("tag_uid") != "0000000000000000"),
                "nozzle_min": tray.get("nozzle_temp_min"), "nozzle_max": tray.get("nozzle_temp_max"),
            })
    vt = p.get("vt_tray") or {}
    mapping = {k: v for k, v in p.items() if "map" in k.lower()}
    return {
        "state": p.get("gcode_state"), "percent": p.get("mc_percent"), "layer": p.get("layer_num"),
        "layers": p.get("total_layer_num"), "job": p.get("subtask_name"), "file": p.get("gcode_file"),
        "task_id": p.get("task_id"), "subtask_id": p.get("subtask_id"),
        "tray_now": ams.get("tray_now"), "tray_pre": ams.get("tray_pre"), "tray_tar": ams.get("tray_tar"),
        "ams_exist_bits": ams.get("ams_exist_bits"), "trays": trays,
        "external": {"type": vt.get("tray_type"), "colour": vt.get("tray_color"), "info_idx": vt.get("tray_info_idx"), "remain": vt.get("remain")} if vt else None,
        "mapping_fields": mapping,
    }


class Link:
    """A read-only status connection to one printer."""

    def __init__(self, printer: dict):
        self.p = printer
        self.status: dict = {}
        self.state = "connecting"
        self.serial = printer.get("serial") or ""
        self.stop = threading.Event()
        self.last_sum: dict = {}
        self.jobs_checked: set[str] = set()
        threading.Thread(target=self.run, daemon=True, name="probe-" + printer.get("name", "printer")).start()

    def run(self):
        delay = 3
        while not self.stop.is_set():
            try:
                self.session()
                delay = 3
            except AuthError as e:
                self.state = "access code not accepted. Remove and add it again."
                log("printer.refused", printer=self.p.get("name"), error=str(e))
                return
            except PermissionError as e:
                self.state = "Orca blocked the connection. Press Connect."
                _allowed.discard(self.p.get("ip"))
                log("printer.blocked", printer=self.p.get("name"), error=str(e))
                self.stop.set()
                return
            except Exception as e:
                self.state = f"retrying ({type(e).__name__}: {e})"
                log("printer.error", printer=self.p.get("name"), error=f"{type(e).__name__}: {e}")
                self.stop.wait(delay)
                delay = min(delay * 2, 60)

    def session(self):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ip, code = self.p["ip"], self.p["code"]
        with socket.create_connection((ip, MQTT_PORT), timeout=10) as raw, ctx.wrap_socket(raw, server_hostname=ip) as s:
            s.settimeout(5)
            body = _mstr("MQTT") + b"\x04\xc2" + struct.pack("!H", 60) + _mstr("spoolstock-probe-" + os.urandom(3).hex()) + _mstr("bblp") + _mstr(code)
            s.sendall(_pkt(0x10, body))
            ack = _read_packet(s)
            if not ack or ack[0] >> 4 != 2:
                raise ConnectionError("no answer to the login")
            if ack[1][1] in (4, 5):
                raise AuthError("access code not accepted")
            if ack[1][1] != 0:
                raise ConnectionError(f"login refused ({ack[1][1]})")
            s.sendall(_pkt(0x82, struct.pack("!H", 1) + _mstr(f"device/{self.serial or '+'}/report") + b"\x00"))
            self.state = "connected"
            log("printer.connected", printer=self.p.get("name"), ip=ip, serial_known=bool(self.serial))
            asked, ping, full_logged = False, time.monotonic(), False
            last_full = 0.0
            while not self.stop.is_set():
                if self.serial and not asked:
                    s.sendall(_pkt(0x30, _mstr(f"device/{self.serial}/request") + PUSHALL.encode()))
                    asked = True
                pk = _read_packet(s)
                if pk and pk[0] >> 4 == 3:
                    b = pk[1]
                    n = struct.unpack("!H", b[:2])[0]
                    topic = b[2:2 + n].decode("utf-8", "replace")
                    if not self.serial and topic.count("/") >= 2:
                        self.serial = topic.split("/")[1]
                        self.p["serial"] = self.serial
                        log("printer.serial_found", printer=self.p.get("name"), serial=self.serial)
                    try:
                        doc = json.loads(b[2 + n:])
                    except ValueError:
                        continue
                    self.on_report(doc, first=not full_logged)
                    full_logged = True
                    if time.monotonic() - last_full > 600:
                        log("printer.full_status", printer=self.p.get("name"), status=self.status)
                        last_full = time.monotonic()
                if time.monotonic() - ping > 30:
                    s.sendall(b"\xc0\x00")
                    ping = time.monotonic()

    def on_report(self, doc: dict, first: bool):
        if not isinstance(doc, dict):
            return
        p = doc.get("print")
        if isinstance(p, dict) and p.get("command") not in (None, "push_status"):
            log("printer.message", printer=self.p.get("name"), command=p.get("command"), data=p)
        _merge(self.status, doc)
        s = summarise(self.status)
        if first:
            log("printer.first_report", printer=self.p.get("name"), report=doc)
        changed = {k: v for k, v in s.items() if self.last_sum.get(k) != v}
        if changed:
            log("printer.change", printer=self.p.get("name"), changed=changed)
        old_state = self.last_sum.get("state")
        self.last_sum = s
        if s.get("state") != old_state:
            log("printer.full_status", printer=self.p.get("name"), why=f"state {old_state} -> {s.get('state')}", status=self.status)
        if s.get("state") in ("RUNNING", "PREPARE", "FINISH", "FAILED") and s.get("file"):
            key = f"{s.get('task_id')}|{s.get('file')}"
            if key not in self.jobs_checked:
                self.jobs_checked.add(key)
                threading.Thread(target=self.check_job, args=(s,), daemon=True).start()

    def check_job(self, s: dict):
        """Find the job's .3mf on the printer and log its per-filament grams. Read-only."""
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            class Ftps(ftplib.FTP_TLS):
                def connect(self, host="", port=0, timeout=-999, source_address=None):
                    r = super().connect(host, port, timeout, source_address)
                    self.sock = self.context.wrap_socket(self.sock, server_hostname=self.host)
                    self.file = self.sock.makefile("r", encoding=self.encoding)
                    return r

                def ntransfercmd(self, cmd, rest=None):
                    conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
                    conn = self.context.wrap_socket(conn, server_hostname=self.host, session=self.sock.session)
                    return conn, size

            ftp = Ftps(context=ctx, timeout=20)
            ftp.connect(self.p["ip"], FTP_PORT)
            ftp.login("bblp", self.p["code"])
            ftp.prot_p()
            listing = {}
            for folder in ("/", "/cache", "/model"):
                try:
                    listing[folder] = ftp.nlst(folder)[:200]
                except ftplib.all_errors as e:
                    listing[folder] = f"<{e}>"
            log("printer.files", printer=self.p.get("name"), job_file=s.get("file"), job=s.get("job"), listing=listing)
            want = [x for x in (s.get("file"), s.get("job")) if x]
            names = [n for v in listing.values() if isinstance(v, list) for n in v]
            hit = None
            for w in want:
                base = os.path.basename(str(w)).replace(".gcode", "")
                for n in names:
                    if n.endswith(".3mf") and base and base in n:
                        hit = n if n.startswith("/") else "/" + n.lstrip("/")
                        break
                if hit:
                    break
            if not hit:
                log("printer.job_file_not_found", printer=self.p.get("name"), wanted=want)
                ftp.quit()
                return
            buf = io.BytesIO()
            size = [0]
            def keep(chunk):
                size[0] += len(chunk)
                if size[0] > JOB_MAX:
                    raise ValueError("job file too large")
                buf.write(chunk)
            ftp.retrbinary(f"RETR {hit}", keep)
            ftp.quit()
            z = zipfile.ZipFile(buf)
            entries = z.namelist()
            info = next((e for e in entries if e.endswith("slice_info.config")), None)
            plates = []
            if info:
                root = ET.fromstring(z.read(info))
                for pl in root.iter("plate"):
                    meta = {m.get("key"): m.get("value") for m in pl.findall("metadata")}
                    plates.append({"meta": meta, "filaments": [f.attrib for f in pl.findall("filament")]})
            log("printer.job_file", printer=self.p.get("name"), path=hit, entries=entries[:60], plates=plates)
        except Exception as e:
            log("printer.job_file_error", printer=self.p.get("name"), error=f"{type(e).__name__}: {e}")


_links: dict[str, Link] = {}
_allowed: set[str] = set()


class AuthError(Exception):
    pass


def authorise(ip: str) -> str:
    """Reach the printer once from Orca's main thread.

    Orca only shows its "allow this plugin to connect?" prompt for network calls made on its main
    (UI) thread. The same call from a background thread is refused without asking. So each printer
    is touched here first, on both of the ports used later, and the background link reuses the grant.
    Returns "" when allowed, or what went wrong.
    """
    problems = []
    for port in (MQTT_PORT, FTP_PORT):
        try:
            socket.getaddrinfo(ip, port, 0, socket.SOCK_STREAM)
            with socket.create_connection((ip, port), timeout=4):
                pass
        except PermissionError as e:
            log("permission.denied", ip=ip, port=port, error=str(e))
            return "Orca didn't allow the connection. Press Connect and choose Yes."
        except OSError as e:
            problems.append(f"port {port}: {e}")
    log("permission.checked", ip=ip, problems=problems)
    if any(f"port {MQTT_PORT}" in x for x in problems):
        return f"Couldn't reach the printer ({problems[0]}). Check the IP, and that the printer is on."
    _allowed.add(ip)
    return ""


def connect_all(on_ui_thread: bool = False) -> list[str]:
    """Start a link for each saved printer. Call from the UI thread so Orca can ask permission."""
    notes = []
    for p in load_printers():
        key = p.get("name") or p.get("ip")
        if not (p.get("ip") and p.get("code")) or (key in _links and not _links[key].stop.is_set()):
            continue
        if on_ui_thread and p["ip"] not in _allowed:
            why = authorise(p["ip"])
            if why:
                notes.append(f"{key}: {why}")
                continue
        if p["ip"] in _allowed:
            _links[key] = Link(p)
    return notes


def disconnect_all() -> None:
    for link in _links.values():
        link.stop.set()
    _links.clear()


# ---------- slicing ----------

def grams_from_gcode(path: str) -> list[float]:
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - 300_000))
            tail = f.read().decode("utf-8", "replace").splitlines()
        for line in reversed(tail):
            if line.startswith("; filament used [g] ="):
                return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", line.split("=", 1)[1])]
    except Exception as e:
        log("slice.read_error", error=str(e))
    return []


# ---------- the tab ----------

PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
body{font:14px/1.45 system-ui,Segoe UI,sans-serif;margin:16px;color:var(--orca-text-color,#222);background:var(--orca-bg-color,#fff)}
h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:18px 0 6px}
.card{border:1px solid #8884;border-radius:6px;padding:10px;margin:8px 0}
input{padding:5px;margin:2px 4px 2px 0;width:150px}button{padding:6px 12px;margin:4px 6px 4px 0;cursor:pointer}
.muted{opacity:.7;font-size:13px}table{border-collapse:collapse;font-size:13px}td,th{border:1px solid #8884;padding:3px 6px;text-align:left}
.sw{display:inline-block;width:14px;height:14px;border:1px solid #0006;vertical-align:middle}
pre{white-space:pre-wrap;font-size:12px;max-height:220px;overflow:auto;background:#8881;padding:6px}
</style></head><body>
<h1>Spool Stock Probe</h1>
<p class="muted">Read-only test. It logs what Orca and your printers report, so the real plugin can be built on it. Nothing is changed. Log file: <span id="log"></span></p>
<h2>Printers</h2>
<div id="printers"></div>
<div class="card"><b>Add a printer</b><br>
<input id="n" placeholder="Name, e.g. Alduin"><input id="ip" placeholder="IP address"><input id="sn" placeholder="Serial (optional)"><input id="code" placeholder="Access code" type="password">
<br><button id="add">Add and connect</button><span id="hint" class="muted"></span></div>
<h2>Live</h2><div id="live"></div>
<h2>Recent log entries</h2><pre id="recent"></pre>
<button id="snap">Log a full snapshot now</button><button id="openlog">Open the log folder</button>
<script>
var o=window.orca;function send(m){o.postMessage(m)}
o.onMessage(function(d){if(!d||d.type!=="state")return;
document.getElementById("log").textContent=d.log;
document.getElementById("hint").textContent=d.hint||"";
if(d.suggest&&!document.getElementById("ip").value)document.getElementById("ip").value=d.suggest;
document.getElementById("printers").innerHTML=d.printers.map(function(p,i){return '<div class="card"><b>'+p.name+'</b> '+p.ip+' <span class="muted">'+(p.serial||"serial not known yet")+'</span><br>Status: '+p.state+' <button data-connect="1">Connect</button><button data-rm="'+i+'">Remove</button></div>'}).join("")||'<p class="muted">None yet.</p>';
document.getElementById("live").innerHTML=d.live.map(function(l){var s=l.summary||{};return '<div class="card"><b>'+l.name+'</b>: '+(s.state||"?")+(s.percent!=null?" "+s.percent+"%":"")+(s.job?" · "+s.job:"")+'<br>Feeding from tray: '+s.tray_now+'<table><tr><th>AMS</th><th>Tray</th><th>Type</th><th>Colour</th><th>Remain</th><th>RFID</th></tr>'+(s.trays||[]).map(function(t){return '<tr><td>'+t.ams+'</td><td>'+t.tray+'</td><td>'+(t.type||"")+'</td><td><span class="sw" style="background:#'+(t.colour||"0000").slice(0,6)+'"></span> '+(t.colour||"")+'</td><td>'+t.remain+'</td><td>'+(t.rfid?"yes":"")+'</td></tr>'}).join("")+'</table>'+(s.external?'External spool: '+(s.external.type||"")+' '+(s.external.colour||""):'')+'<br><span class="muted">Mapping fields: '+JSON.stringify(s.mapping_fields||{})+'</span></div>'}).join("")||'<p class="muted">Not connected.</p>';
document.getElementById("recent").textContent=d.recent.slice().reverse().join("\\n");
document.querySelectorAll("[data-rm]").forEach(function(b){b.onclick=function(){send({type:"remove",index:+b.dataset.rm})}});
document.querySelectorAll("[data-connect]").forEach(function(b){b.onclick=function(){send({type:"connect"})}})});
document.getElementById("add").onclick=function(){send({type:"add",name:document.getElementById("n").value.trim(),ip:document.getElementById("ip").value.trim(),serial:document.getElementById("sn").value.trim(),code:document.getElementById("code").value.trim()});document.getElementById("code").value=""};
document.getElementById("snap").onclick=function(){send({type:"snapshot"})};
document.getElementById("openlog").onclick=function(){send({type:"openlog"})};
send({type:"ready"});setInterval(function(){send({type:"poll"})},2000);
</script></body></html>"""


def _state(suggest: str = "", hint: str = "") -> dict:
    rows = []
    for p in load_printers():
        link = _links.get(p.get("name") or p.get("ip"))
        rows.append({"name": p.get("name", ""), "ip": p.get("ip", ""),
                     "serial": (link.serial if link else "") or p.get("serial", ""),
                     "state": link.state if link else "not connected"})
    return {"type": "state", "log": str(LOG), "suggest": suggest, "hint": hint, "printers": rows,
            "live": [{"name": k, "summary": v.last_sum} for k, v in _links.items()], "recent": list(_recent)}


def _suggest_ip() -> str:
    info = orca_printer_info()
    for h in [info.get("print_host", "")] + [x["print_host"] for x in info.get("print_hosts", [])]:
        m = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", str(h or ""))
        if m:
            return m.group(1)
    return ""


def _ask_permissions():
    """Declare threads, and the saved printers' addresses, so Orca can ask once when it loads."""
    req = getattr(orca, "request_permissions", None)
    if not callable(req):
        return
    try:
        req(threading=True)
    except Exception as e:
        log("permissions.request_failed", kind="threading", error=f"{type(e).__name__}: {e}")
    ips = [p["ip"] for p in load_printers() if p.get("ip")]
    if ips:
        try:
            req(network=ips)
            log("permissions.declared", network=ips)
        except Exception as e:
            log("permissions.request_failed", kind="network", error=f"{type(e).__name__}: {e}")


Pages = getattr(getattr(orca, "pages", None), "PagesPluginCapabilityBase", None)
Slicing = getattr(getattr(orca, "slicing", None), "SlicingPipelineCapabilityBase", None)


if Pages is not None:
    class ProbePage(Pages):
        def __init__(self):
            super().__init__()
            self._surveyed = False

        def get_name(self):
            return NAME

        def get_type(self):
            return orca.PluginType.Pages

        def get_ui(self):
            if not self._surveyed:
                self._surveyed = True
                info = {}
                try:
                    info = dict(orca.host.app_info())
                except Exception as e:
                    info = {"error": str(e)}
                log("probe.start", version=VERSION, python=sys.version, orca=info)
                log("orca.api", names=survey_api())
                log("orca.printer", info=orca_printer_info())
                log("orca.conf", hints=orca_conf_hints())
            return PAGE

        def on_message(self, arg0):
            try:
                d = json.loads(arg0) if isinstance(arg0, str) else arg0
                t = (d or {}).get("type")
                if t == "ready":
                    notes = connect_all(on_ui_thread=True)
                    self.post_message(_state(suggest=_suggest_ip(), hint=" ".join(notes)))
                elif t == "connect":
                    notes = connect_all(on_ui_thread=True)
                    self.post_message(_state(hint=" ".join(notes) or "Connecting."))
                elif t == "poll":
                    self.post_message(_state())
                elif t == "add":
                    if not (d.get("ip") and d.get("code")):
                        self.post_message(_state(hint="Needs at least the IP and access code."))
                        return
                    items = load_printers()
                    items.append({"name": d.get("name") or d["ip"], "ip": d["ip"], "serial": d.get("serial", ""), "code": d["code"]})
                    _secrets.add(d["code"])
                    save_printers(items)
                    log("printer.added", name=d.get("name"), ip=d["ip"], serial_given=bool(d.get("serial")))
                    notes = connect_all(on_ui_thread=True)
                    self.post_message(_state(hint=" ".join(notes) or "Added and connecting."))
                elif t == "remove":
                    items = load_printers()
                    i = int(d.get("index", -1))
                    if 0 <= i < len(items):
                        gone = items.pop(i)
                        save_printers(items)
                        link = _links.pop(gone.get("name") or gone.get("ip"), None)
                        if link:
                            link.stop.set()
                    self.post_message(_state())
                elif t == "snapshot":
                    for k, v in _links.items():
                        log("printer.full_status", printer=k, why="asked", status=v.status)
                    log("orca.printer", info=orca_printer_info())
                    self.post_message(_state(hint="Snapshot written to the log."))
                elif t == "openlog":
                    try:
                        os.startfile(str(DATA))  # Windows
                    except Exception:
                        self.post_message(_state(hint=f"Log folder: {DATA}"))
            except Exception:
                log("page.error", trace=traceback.format_exc())

        def on_lifecycle_event(self, event, ctx):
            log("orca.event", event=str(event), fields=_fields(ctx))

        def on_unload(self):
            disconnect_all()
            log("probe.unload")


if Slicing is not None:
    class ProbeSlice(Slicing):
        def get_name(self):
            return "Spool Stock Probe"

        def execute(self, ctx):
            try:
                step = str(getattr(ctx, "step", ""))
                entry = {"step": step, "fields": _fields(ctx)}
                if "GCodePostProcess" in step:
                    cfg = {}
                    for key in ("filament_settings_id", "filament_colour", "filament_type", "filament_vendor", "filament_ids",
                                "printer_model", "printer_settings_id", "print_host", "filament_map", "filament_map_mode"):
                        try:
                            cfg[key] = str(ctx.config_value(key))[:1000]
                        except Exception as e:
                            cfg[key] = f"<{type(e).__name__}>"
                    entry["config"] = cfg
                    entry["grams"] = grams_from_gcode(getattr(ctx, "gcode_path", "") or "")
                log("slice.step", **entry)
            except Exception:
                log("slice.error", trace=traceback.format_exc())
            return orca.ExecutionResult.success()


@orca.plugin
class SpoolStockProbe(orca.base):
    def register_capabilities(self):
        _ask_permissions()
        if Pages is not None:
            orca.register_capability(ProbePage)
        if Slicing is not None:
            orca.register_capability(ProbeSlice)
        log("probe.registered", pages=Pages is not None, slicing=Slicing is not None)
