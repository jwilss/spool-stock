# /// script
# requires-python = ">=3.12"
# dependencies = []
#
# [tool.orcaslicer.plugin]
# id = "spool-stock"
# name = "Spool Stock"
# description = "Takes each print's filament off the right spools in Spool Stock, shows your stock and cameras, and keeps a Spool Stock group of your filaments in Orca's filament list."
# author = "jwilss"
# version = "1.3.0"
# ///
"""Spool Stock for OrcaSlicer 2.5.

Watches each Bambu printer on the local network. When a job starts, the printer says which AMS tray or
the external spool each filament comes from. The plugin reads the job file on the printer for the grams
per filament, and when the print ends takes them off the spools that Spool Stock has in those trays.

A finished print is taken off straight away, with Undo in the app. A failed or cancelled print waits in
the Spool Stock tab with how far it got, for you to confirm.

The tab also shows your stock, and can keep a Spool Stock group in Orca's filament list: one preset for each
filament you own, written as a local preset bundle and reloaded while Orca is open. Its Cameras view plays RTSP
cameras such as Tapo ones, relayed by the plugin, with nothing else to install.

The only thing sent to a printer is Bambu's standard request for its status. Printer IPs and access
codes stay in this plugin's folder on this PC and are never sent to Spool Stock or written to the log.
"""

import base64
import ftplib
import hashlib
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
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import orca

NAME = "Spool Stock"
VERSION = "1.3.0"
MQTT_PORT, FTP_PORT = 8883, 990
PUSHALL = '{"pushing": {"sequence_id": "0", "command": "pushall"}}'
TOKEN_URL = "https://securetoken.googleapis.com/v1/token"
FIRESTORE_URL = "https://firestore.googleapis.com/v1"
CLOUD_HOSTS = ("securetoken.googleapis.com", "firestore.googleapis.com")
LOG_MAX = 2 * 1024 * 1024
JOB_MAX = 96 * 1024 * 1024
CANCELLED = 50348044  # print_error the printer reports when a job is cancelled (0x0300400C)
ACTIVE = ("PREPARE", "RUNNING", "PAUSE", "SLICING")


# ---------- files ----------

def _data_dir() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if parent.name == "OrcaSlicer":
            return parent / "spoolstock"
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "OrcaSlicer" / "spoolstock"


DATA = _data_dir()
LOG = DATA / "spoolstock.log"
PRINTERS = DATA / "printers.json"
ACCOUNT = DATA / "account.json"
JOBS = DATA / "jobs.json"
PRESET_STATE = DATA / "presets.json"
_log_lock = threading.Lock()
_secrets: set[str] = set()


def _redact(text: str) -> str:
    for s in _secrets:
        if s and len(s) >= 4:
            text = text.replace(s, "********")
    return text


def log(kind: str, **data) -> None:
    """One JSON line per entry. Never raises."""
    try:
        line = json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind, **data}, default=str, ensure_ascii=False)
        with _log_lock:
            DATA.mkdir(parents=True, exist_ok=True)
            if LOG.exists() and LOG.stat().st_size > LOG_MAX:
                LOG.replace(LOG.with_suffix(".old.log"))
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(_redact(line) + "\n")
    except Exception:
        pass


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data, private: bool = False) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(path)
    if private:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def load_printers() -> list[dict]:
    items = _read_json(PRINTERS, [])
    for p in items:
        _secrets.add(p.get("code", ""))
    return items


def save_printers(items: list[dict]) -> None:
    _write_json(PRINTERS, items, private=True)


# ---------- jobs: what's printing, what's waiting to be taken off, what needs you ----------

class Store:
    """Kept in jobs.json so a restart of Orca mid-print loses nothing.

    active:  serial -> the job printing now
    outbox:  bookings ready to send to Spool Stock
    pending: prints waiting for you in the tab (failed, cancelled, or a tray or spool that couldn't be matched)
    recent:  the last few results, for the tab
    """

    def __init__(self):
        self.lock = threading.RLock()
        d = _read_json(JOBS, {})
        self.active: dict = d.get("active", {})
        self.outbox: list = d.get("outbox", [])
        self.pending: list = d.get("pending", [])
        self.recent: list = d.get("recent", [])

    def save(self):
        with self.lock:
            try:
                _write_json(JOBS, {"active": self.active, "outbox": self.outbox, "pending": self.pending, "recent": self.recent[:20]})
            except Exception as e:
                log("store.error", error=str(e))

    def note(self, text: str, key: str = ""):
        with self.lock:
            self.recent.insert(0, {"at": time.time(), "text": text, "key": key})
            del self.recent[20:]


STORE = Store()
_wake = threading.Event()


# ---------- Spool Stock (Firestore over its REST interface) ----------

class CloudError(Exception):
    def __init__(self, status: str, message: str, code: int = 0):
        super().__init__(f"{status}: {message}")
        self.status, self.code = status, code


def to_fs(v):
    if v is None:
        return {"nullValue": None}
    if isinstance(v, bool):
        return {"booleanValue": v}
    if isinstance(v, int):
        return {"integerValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, str):
        return {"stringValue": v}
    if isinstance(v, (list, tuple)):
        return {"arrayValue": {"values": [to_fs(x) for x in v]}}
    if isinstance(v, dict):
        return {"mapValue": {"fields": {k: to_fs(x) for k, x in v.items()}}}
    return {"stringValue": str(v)}


def from_fs(v: dict):
    if not isinstance(v, dict):
        return None
    if "integerValue" in v:
        return int(v["integerValue"])
    if "doubleValue" in v:
        return float(v["doubleValue"])
    if "stringValue" in v:
        return v["stringValue"]
    if "booleanValue" in v:
        return bool(v["booleanValue"])
    if "arrayValue" in v:
        return [from_fs(x) for x in (v["arrayValue"].get("values") or [])]
    if "mapValue" in v:
        return {k: from_fs(x) for k, x in (v["mapValue"].get("fields") or {}).items()}
    if "timestampValue" in v:
        return v["timestampValue"]
    return None


def decode_code(code: str) -> dict:
    """The sign-in code from Spool Stock's Setup page: SS1. then base64 JSON."""
    code = re.sub(r"\s+", "", code or "")
    if not code.startswith("SS1."):
        raise ValueError("That isn't a Spool Stock sign-in code. Copy it from Setup > Orca plugin.")
    body = code[4:]
    d = json.loads(base64.b64decode(body + "=" * (-len(body) % 4)).decode("utf-8"))
    if not all(d.get(k) for k in ("k", "p", "u", "t")):
        raise ValueError("That sign-in code is missing parts. Copy it again.")
    return d


class Cloud:
    def __init__(self):
        self.acct: dict = _read_json(ACCOUNT, {})
        self._secret_parts()
        self.id_token, self.expires = "", 0.0
        self.lock = threading.RLock()
        self.ui_mode = False       # Orca refused network calls from the background thread, so make them from the tab
        self.snap: dict | None = None
        self.snap_at = 0.0
        self.status = "Not signed in" if not self.acct else "Signed in"

    def _secret_parts(self):
        for k in ("t", "k"):
            _secrets.add(self.acct.get(k, ""))

    @property
    def signed_in(self) -> bool:
        return bool(self.acct.get("t"))

    def sign_in(self, code: str):
        d = decode_code(code)
        with self.lock:
            self.acct = {"k": d["k"], "p": d["p"], "u": d["u"], "e": d.get("e", ""), "t": d["t"]}
            self._secret_parts()
            self.id_token, self.snap = "", None
            _write_json(ACCOUNT, self.acct, private=True)
        self.status = "Signed in"

    def sign_out(self):
        with self.lock:
            self.acct, self.id_token, self.snap = {}, "", None
            try:
                ACCOUNT.unlink()
            except OSError:
                pass
        self.status = "Not signed in"

    def _http(self, method: str, url: str, body=None, form=None, auth=True):
        headers = {}
        data = None
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if auth:
            headers["Authorization"] = "Bearer " + self.token()
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read() or b"{}")
                err = err[0] if isinstance(err, list) and err else err
                err = err.get("error", err)
                status = err.get("status") or str(err.get("message", "")) if isinstance(err, dict) else str(err)
                message = err.get("message", "") if isinstance(err, dict) else ""
            except Exception:
                status, message = str(e.code), ""
            raise CloudError(status or str(e.code), message, e.code)
        except urllib.error.URLError as e:
            if isinstance(e.reason, PermissionError):
                raise e.reason
            raise

    def token(self) -> str:
        with self.lock:
            if self.id_token and time.time() < self.expires - 120:
                return self.id_token
            if not self.signed_in:
                raise CloudError("SIGNED_OUT", "not signed in")
            try:
                d = self._http("POST", f"{TOKEN_URL}?key={urllib.parse.quote(self.acct['k'])}",
                               form={"grant_type": "refresh_token", "refresh_token": self.acct["t"]}, auth=False)
            except CloudError as e:
                if e.code in (400, 401, 403):
                    self.status = "The sign-in code no longer works. Copy a new one from Spool Stock."
                    self.acct["t"] = ""
                    _write_json(ACCOUNT, self.acct, private=True)
                raise
            self.id_token = d.get("id_token", "")
            _secrets.add(self.id_token)
            self.expires = time.time() + int(d.get("expires_in", 3600))
            if d.get("refresh_token") and d["refresh_token"] != self.acct.get("t"):
                self.acct["t"] = d["refresh_token"]
                _secrets.add(d["refresh_token"])
                _write_json(ACCOUNT, self.acct, private=True)
            return self.id_token

    def _root(self) -> str:
        return f"projects/{self.acct['p']}/databases/(default)/documents"

    def _name(self, path: str) -> str:
        return f"{self._root()}/users/{self.acct['u']}/{path}"

    def docs(self, collection: str) -> dict:
        out, page = {}, ""
        while True:
            q = "?pageSize=300" + (f"&pageToken={urllib.parse.quote(page)}" if page else "")
            d = self._http("GET", f"{FIRESTORE_URL}/{self._name(collection)}{q}")
            for doc in d.get("documents", []):
                did = doc["name"].rsplit("/", 1)[-1]
                out[did] = {**{k: from_fs(v) for k, v in (doc.get("fields") or {}).items()}, "id": did}
            page = d.get("nextPageToken", "")
            if not page:
                return out

    def get(self, path: str):
        try:
            doc = self._http("GET", f"{FIRESTORE_URL}/{self._name(path)}")
        except CloudError as e:
            if e.code == 404:
                return None, ""
            raise
        return {k: from_fs(v) for k, v in (doc.get("fields") or {}).items()}, doc.get("updateTime", "")

    def commit(self, writes: list):
        return self._http("POST", f"{FIRESTORE_URL}/{self._root()}:commit", body={"writes": writes})

    def snapshot(self, max_age: float = 60) -> dict:
        """Printers, storages and spools from Spool Stock, reloaded when older than max_age seconds."""
        with self.lock:
            if self.snap is not None and time.time() - self.snap_at < max_age:
                return self.snap
            snap = {"printers": self.docs("printers"), "locs": self.docs("locations"), "spools": self.docs("spools"),
                    "orca": self.get("config/orca")[0]}
            self.snap, self.snap_at = snap, time.time()
            self.status = "Signed in" + (f" as {self.acct.get('e')}" if self.acct.get("e") else "")
            return snap


CLOUD = Cloud()


# ---------- which spool is in which tray, as the app works it out (trayToSpool in index.html) ----------

def _spaced(l) -> bool:
    return bool(l and l.get("spaces") and (l.get("capacity") or 0) > 0)


def _live_in(snap, loc_id) -> list:
    return [s for _, s in sorted(snap["spools"].items()) if s.get("status") != "used" and s.get("loc") == loc_id]


def _seated(snap, l) -> list:
    cap = int(l.get("capacity") or 0)
    seats, rest = [None] * cap, []
    for s in _live_in(snap, l["id"]):
        n = s.get("slot")
        if isinstance(n, (int, float)) and 1 <= n <= cap and seats[int(n) - 1] is None:
            seats[int(n) - 1] = s
        else:
            rest.append(s)
    for s in rest:
        if None in seats:
            seats[seats.index(None)] = s
    return seats


def _loose(snap, l) -> list:
    return sorted(_live_in(snap, l["id"]), key=lambda s: (s.get("pos") if s.get("pos") is not None else 1e9, s.get("addedAt") or 0, s["id"]))


def find_printer(snap, serial: str, name: str):
    ps = list(snap["printers"].values())
    return (next((p for p in ps if serial and (p.get("serial") or "").strip().upper() == serial.upper()), None)
            or next((p for p in ps if name and (p.get("name") or "").strip().casefold() == name.strip().casefold()), None))


def tray_to_spool(snap, printer, unit, tray):
    """unit is the AMS unit from 0, or "external". Returns the spool, or None."""
    if not printer:
        return None
    feeds = printer.get("feeds") or []
    f = next((f for f in feeds if (f.get("kind") == "external" if unit == "external" else f.get("kind") == "ams" and f.get("unit") == int(unit) + 1)), None)
    l = snap["locs"].get(f.get("loc")) if f else None
    if not l:
        return None
    if unit == "external":
        if _spaced(l):
            seats = _seated(snap, l)
            i = int(f.get("space") or 1) - 1
            return seats[i] if 0 <= i < len(seats) else None
        loose = _loose(snap, l)
        return loose[0] if loose else None
    if not _spaced(l):
        return None
    seats = _seated(snap, l)
    return seats[int(tray)] if 0 <= int(tray) < len(seats) else None


def spool_label(s: dict) -> str:
    left = s.get("remainG") if s.get("remainG") is not None else s.get("netG")
    parts = [s["id"].upper(), s.get("brand", ""), s.get("material", ""), s.get("colour", "")]
    return " ".join(x for x in parts if x).strip() + (f", {round(left)} g" if isinstance(left, (int, float)) else "")


def tray_name(t) -> str:
    if t is None:
        return "unknown tray"
    if t[0] == "external":
        return "external spool"
    return f"AMS {t[0] + 1} tray {t[1] + 1}"


def tray_of(job: dict, fil_id: int, index: int):
    """Which tray a filament came from: (unit, tray), ("external", None) or None."""
    if job.get("use_ams") is False:
        return ("external", None)
    m2, m = job.get("mapping2") or [], job.get("mapping") or []
    i = fil_id - 1
    if not (0 <= i < max(len(m2), len(m))):
        i = index
    if 0 <= i < len(m2) and isinstance(m2[i], dict):
        a, slot = int(m2[i].get("ams_id", -1)), int(m2[i].get("slot_id", 0))
        if a == 255:
            return ("external", None)
        if 0 <= a < 128:
            return (a, slot)
    if 0 <= i < len(m):
        v = int(m[i])
        if v == 254:
            return ("external", None)
        if 0 <= v < 128:
            return (v // 4, v % 4)
    if not m and not m2:
        # No mapping (the plugin joined after the job started): go by the trays the printer fed from
        seen = [t for t in job.get("trays_seen") or [] if t not in ("255", None)]
        if job.get("fil_count", 1) == 1 and len(set(seen)) == 1:
            v = int(seen[0])
            return ("external", None) if v == 254 else (v // 4, v % 4)
    return None


# ---------- the job file on the printer: grams per filament ----------

class Ftps(ftplib.FTP_TLS):
    """Implicit FTPS, as Bambu printers use on port 990: TLS starts before the printer says hello."""
    _plain = None

    @property
    def sock(self):
        return self._plain

    @sock.setter
    def sock(self, value):
        if value is not None and not isinstance(value, ssl.SSLSocket):
            value = self.context.wrap_socket(value, server_hostname=self.host)
        self._plain = value

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        conn = self.context.wrap_socket(conn, server_hostname=self.host, session=self.sock.session)
        return conn, size


def _insecure_ctx() -> ssl.SSLContext:
    # Bambu printers use their own certificate authority, so the certificate can't be checked against Windows'
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def read_job_file(ip: str, code: str, file: str, plate: int) -> list[dict]:
    """The filaments of one plate from the job's .3mf on the printer: id, type, colour, grams."""
    ftp = Ftps(context=_insecure_ctx(), timeout=30)
    ftp.connect(ip, FTP_PORT)
    try:
        ftp.login("bblp", code)
        ftp.prot_p()
        base = os.path.basename(file or "")
        names = []
        for folder in ("/", "/cache"):
            try:
                names += [n if n.startswith("/") else f"{folder.rstrip('/')}/{n}" for n in ftp.nlst(folder)]
            except ftplib.all_errors:
                pass
        hit = next((n for n in names if os.path.basename(n) == base), None) or \
            next((n for n in names if n.endswith(".3mf") and base.replace(".gcode.3mf", "") in n), None)
        if not hit:
            raise FileNotFoundError(f"{base} isn't on the printer")
        buf, size = io.BytesIO(), [0]

        def keep(chunk):
            size[0] += len(chunk)
            if size[0] > JOB_MAX:
                raise ValueError("job file too large")
            buf.write(chunk)
        ftp.retrbinary(f"RETR {hit}", keep)
    finally:
        try:
            ftp.quit()
        except Exception:
            pass
    z = zipfile.ZipFile(buf)
    info = next((e for e in z.namelist() if e.endswith("slice_info.config")), None)
    if not info:
        raise ValueError("the job file has no slice information")
    plates = []
    for pl in ET.fromstring(z.read(info)).iter("plate"):
        meta = {m.get("key"): m.get("value") for m in pl.findall("metadata")}
        fils = [{"id": int(f.get("id", "0") or 0), "type": f.get("type", ""), "colour": f.get("color", ""),
                 "g": round(float(f.get("used_g", "0") or 0), 2)} for f in pl.findall("filament")]
        plates.append((int(meta.get("index", "0") or 0), fils))
    chosen = next((f for i, f in plates if i == plate), None) or next((f for _, f in plates if f), [])
    return [f for f in chosen if f["g"] > 0]


# ---------- printers ----------

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


def serial_from_cert(der: bytes) -> str:
    """A Bambu printer's own certificate is named (CN) after its serial number."""
    i = 0
    while True:
        i = der.find(b"\x06\x03\x55\x04\x03", i)
        if i < 0:
            return ""
        j = i + 5
        if j + 2 <= len(der) and der[j + 1] < 128:
            name = der[j + 2:j + 2 + der[j + 1]].decode("utf-8", "replace")
            if re.fullmatch(r"[0-9A-Z]{12,20}", name):
                return name
        i = j


def _merge(base: dict, new: dict) -> None:
    for k, v in new.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v


class AuthError(Exception):
    pass


class Link:
    """A read-only status connection to one printer, and the job it's printing."""

    def __init__(self, printer: dict):
        self.p = printer
        self.status: dict = {}
        self.state = "connecting"
        self.serial = printer.get("serial") or ""
        self.stop = threading.Event()
        self.last_state = None
        self.fetching = False
        threading.Thread(target=self.run, daemon=True, name="spoolstock-" + printer.get("name", "printer")).start()

    @property
    def name(self) -> str:
        return self.p.get("name") or self.p.get("ip", "")

    def run(self):
        delay = 3
        while not self.stop.is_set():
            try:
                self.session()
                delay = 3
            except AuthError:
                self.state = "access code not accepted. Remove the printer and add it again."
                log("printer.refused", printer=self.name)
                return
            except PermissionError as e:
                self.state = "Orca blocked the connection. Press Connect."
                _allowed.discard(self.p.get("ip"))
                log("printer.blocked", printer=self.name, error=str(e))
                self.stop.set()
                return
            except Exception as e:
                self.state = f"reconnecting ({type(e).__name__}: {e})"
                log("printer.error", printer=self.name, error=f"{type(e).__name__}: {e}")
                self.stop.wait(delay)
                delay = min(delay * 2, 60)

    def session(self):
        ip, code = self.p["ip"], self.p["code"]
        with socket.create_connection((ip, MQTT_PORT), timeout=10) as raw, _insecure_ctx().wrap_socket(raw, server_hostname=ip) as s:
            s.settimeout(5)
            if not self.serial:
                self.serial = serial_from_cert(s.getpeercert(binary_form=True) or b"")
                if not self.serial:
                    raise ConnectionError("serial number unknown")
                self.p["serial"] = self.serial
                items = load_printers()
                for it in items:
                    if it.get("ip") == ip:
                        it["serial"] = self.serial
                save_printers(items)
            body = _mstr("MQTT") + b"\x04\xc2" + struct.pack("!H", 60) + _mstr("spoolstock-" + os.urandom(3).hex()) + _mstr("bblp") + _mstr(code)
            s.sendall(_pkt(0x10, body))
            ack = _read_packet(s)
            if not ack or ack[0] >> 4 != 2:
                raise ConnectionError("no answer to the login")
            if ack[1][1] in (4, 5):
                raise AuthError()
            if ack[1][1] != 0:
                raise ConnectionError(f"login refused ({ack[1][1]})")
            s.sendall(_pkt(0x82, struct.pack("!H", 1) + _mstr(f"device/{self.serial}/report") + b"\x00"))
            s.sendall(_pkt(0x30, _mstr(f"device/{self.serial}/request") + PUSHALL.encode()))
            self.state = "connected"
            log("printer.connected", printer=self.name, serial=self.serial)
            ping = time.monotonic()
            while not self.stop.is_set():
                pk = _read_packet(s)
                if pk and pk[0] >> 4 == 3:
                    b = pk[1]
                    n = struct.unpack("!H", b[:2])[0]
                    try:
                        doc = json.loads(b[2 + n:])
                    except ValueError:
                        doc = None
                    if isinstance(doc, dict):
                        try:
                            self.on_report(doc)
                        except Exception:
                            log("job.error", printer=self.name, trace=traceback.format_exc())
                if time.monotonic() - ping > 30:
                    s.sendall(b"\xc0\x00")
                    ping = time.monotonic()

    # --- the job ---

    def on_report(self, doc: dict):
        p = doc.get("print")
        if not isinstance(p, dict):
            return
        cmd = p.get("command")
        if cmd == "project_file":
            # Reports only carry what changed, so record the new job's name here or the last one lingers
            _merge(self.status, {"print": {"subtask_name": p.get("subtask_name") or "", "gcode_file": p.get("file") or ""}})
            self.start_job(p)
            return
        if cmd not in (None, "push_status"):
            return
        _merge(self.status, doc)
        s = self.status.get("print", {})
        state = s.get("gcode_state")
        old, self.last_state = self.last_state, state
        with STORE.lock:
            job = STORE.active.get(self.serial)
            fresh = p.get("subtask_name")
            if job and state in ACTIVE and fresh and job["state"] == "running" and fresh != job["name"]:
                # A different job started while the plugin wasn't watching
                job["state"], job["result"] = "ended", "unseen"
                threading.Thread(target=self.end, args=(job,), daemon=True).start()
                job = None
            if job is None and state in ACTIVE and s.get("subtask_name"):
                # Joined after the job started, or a job sent some other way: no tray mapping from the printer
                job = self.new_job(name=s.get("subtask_name"), file=s.get("gcode_file") or "", plate=1, late=True)
            if job is None:
                return
            if state in ACTIVE:
                if job["state"] == "sent":
                    job["state"] = "running"
                    STORE.save()
                if state == "RUNNING":
                    job["percent"] = s.get("mc_percent", job.get("percent", 0))
                    job["layer"], job["layers"] = s.get("layer_num", 0), s.get("total_layer_num", 0)
                tn = str((s.get("ams") or {}).get("tray_now", "255"))
                if tn not in ("255", "None") and tn not in job["trays_seen"]:
                    job["trays_seen"].append(tn)
                    STORE.save()
                if job.get("fils") is None and not self.fetching and (s.get("layer_num") or 0) >= 1:
                    self.fetching = True
                    threading.Thread(target=self.fetch, args=(job,), daemon=True).start()
            elif state in ("FINISH", "FAILED") and job["state"] == "running":
                job["state"] = "ended"
                job["result"] = "finished" if state == "FINISH" else ("cancelled" if s.get("print_error") == CANCELLED else "failed")
                job["ended_at"] = time.time()
                STORE.save()
                threading.Thread(target=self.end, args=(job,), daemon=True).start()

    def new_job(self, name: str, file: str, plate: int, late: bool = False, mapping=None, mapping2=None, use_ams=None) -> dict:
        now = time.time()
        job = {"key": f"{self.serial}-{int(now * 1000)}", "serial": self.serial, "printer": self.name, "name": name or "print",
               "file": file, "plate": plate, "mapping": mapping, "mapping2": mapping2, "use_ams": use_ams, "late": late,
               "started": now, "state": "sent", "percent": 0, "layer": 0, "layers": 0, "trays_seen": [], "fils": None}
        STORE.active[self.serial] = job
        STORE.save()
        log("job.start", printer=self.name, job=job["name"], file=file, plate=plate, mapping=mapping, mapping2=mapping2, use_ams=use_ams, late=late)
        return job

    def start_job(self, p: dict):
        if p.get("result") not in (None, "success"):
            return
        m = re.search(r"plate_(\d+)", str(p.get("param", "")))
        with STORE.lock:
            prev = STORE.active.get(self.serial)
            if prev and prev["state"] == "running":
                prev["state"] = "ended"
                prev["result"] = "unseen"
                threading.Thread(target=self.end, args=(prev,), daemon=True).start()
            self.new_job(name=p.get("subtask_name") or "", file=p.get("file") or os.path.basename(str(p.get("url", ""))),
                         plate=int(m.group(1)) if m else 1, mapping=p.get("ams_mapping"), mapping2=p.get("ams_mapping2"),
                         use_ams=p.get("use_ams"))
            job = STORE.active[self.serial]
            self.fetching = True
        # Read the grams straight away, so a spool that's too low is flagged before the print gets going
        threading.Thread(target=self.fetch, args=(job, 3), daemon=True).start()

    def fetch(self, job: dict, delay: float = 0) -> bool:
        """Read the grams from the job file, up to 3 tries, then check the spools have enough."""
        self.fetching = True
        try:
            if delay and self.stop.wait(delay):
                return False
            for attempt in range(1, 4):
                try:
                    fils = read_job_file(self.p["ip"], self.p["code"], job["file"], job["plate"])
                    with STORE.lock:
                        job["fils"] = fils
                        STORE.save()
                    log("job.grams", printer=self.name, job=job["name"], filaments=fils)
                    try:
                        check_low(job)
                    except PermissionError:
                        pass   # the tab tries again from Orca's main thread
                    except Exception as e:
                        job["checked_low"] = True
                        log("low.error", error=f"{type(e).__name__}: {e}")
                    return True
                except Exception as e:
                    log("job.file_error", printer=self.name, job=job["name"], attempt=attempt, error=f"{type(e).__name__}: {e}")
                    if self.stop.wait(15):
                        return False
            return False
        finally:
            self.fetching = False

    def end(self, job: dict):
        job["checked_low"] = True   # too late to warn
        until = time.monotonic() + 120
        while self.fetching and time.monotonic() < until:   # a read started during the print is still going
            time.sleep(0.5)
        if job.get("fils") is None:
            self.fetch(job)
        finish_job(job)


_notify_q: list[str] = []


def notify(text: str) -> None:
    """Show a warning in Orca. Queued here and shown from the tab's next message, on Orca's main thread."""
    _notify_q.append(text)


def _flush_notifications() -> None:
    ui = getattr(getattr(orca, "host", None), "ui", None)
    push = getattr(ui, "push_notification", None)
    level = getattr(ui, "WarningNotificationLevel", None)
    while _notify_q:
        text = _notify_q.pop(0)
        if not callable(push):
            continue
        tries = [(text, level), (level, text), (text,)] if level is not None else [(text,)]
        for args in tries:
            try:
                push(*args)
                log("notify.shown", form=len(args) if args[0] is text else "level first")
                break
            except TypeError:
                continue
            except Exception as e:
                log("notify.error", error=f"{type(e).__name__}: {e}")
                break


def check_low(job: dict) -> None:
    """Warn when a spool in a tray the job uses has less left than the job needs (with 10% to spare)."""
    if job.get("checked_low") or not job.get("fils") or not CLOUD.signed_in:
        return
    snap = CLOUD.snapshot(max_age=30)
    printer = find_printer(snap, job["serial"], job["printer"])
    fils, warnings = job["fils"], []
    for i, f in enumerate(fils):
        t = tray_of({**job, "fil_count": len(fils)}, f["id"], i)
        sp = tray_to_spool(snap, printer, t[0], t[1]) if (t and printer) else None
        left = (sp.get("remainG") if sp.get("remainG") is not None else sp.get("netG")) if sp else None
        if not isinstance(left, (int, float)) or left >= f["g"] * 1.1:
            continue
        need = round(f["g"])
        what = f"{sp['id'].upper()} ({' '.join(x for x in (sp.get('brand'), sp.get('material'), sp.get('colour')) if x)}) in {tray_name(t)}"
        warnings.append(f"{job['name']} needs {need} g from {what}, which has about {round(left)} g left."
                        if left < f["g"] else
                        f"{job['name']} needs {need} g from {what}, which has about {round(left)} g left. That's only just enough.")
    with STORE.lock:
        job["checked_low"], job["warnings"] = True, warnings
        for w in warnings:
            STORE.note(f"{job['printer']}: {w}", job["key"])
        STORE.save()
    for w in warnings:
        log("low.warning", printer=job["printer"], text=w)
        notify(f"Spool Stock, {job['printer']}: {w}")


def finish_job(job: dict):
    """A finished print with every tray known goes to the outbox. Anything else waits for you."""
    fils = job.get("fils") or []
    for i, f in enumerate(fils):
        f["tray"] = tray_of({**job, "fil_count": len(fils)}, f["id"], i)
    with STORE.lock:
        if STORE.active.get(job["serial"]) is job:
            del STORE.active[job["serial"]]
        entry = {"key": job["key"], "printer": job["printer"], "serial": job["serial"], "name": job["name"], "plate": job["plate"],
                 "result": job.get("result"), "percent": job.get("percent", 0), "layer": job.get("layer", 0),
                 "layers": job.get("layers", 0), "at": int(job.get("ended_at", time.time()) * 1000),
                 "items": [{"fil": f["id"], "type": f["type"], "colour": f["colour"], "g": f["g"], "tray": f["tray"]} for f in fils]}
        if job.get("result") == "finished" and fils and all(f["tray"] for f in fils):
            entry["pct"] = 100
            STORE.outbox.append(entry)
        else:
            entry["why"] = ("No grams: the job file couldn't be read from the printer." if not fils else
                            {"cancelled": "Cancelled", "failed": "Failed", "unseen": "The plugin didn't see this print end"}.get(job.get("result"), "Couldn't tell which tray it used"))
            STORE.pending.append(entry)
        STORE.save()
    log("job.end", printer=job["printer"], job=job["name"], result=job.get("result"), percent=job.get("percent"), items=entry["items"],
        waiting=entry in STORE.pending)
    _wake.set()


# ---------- taking it off in Spool Stock ----------

def resolve(entry: dict, snap: dict) -> list[str]:
    """Fill in the spool for each item from the trays. Returns what couldn't be matched."""
    printer = find_printer(snap, entry["serial"], entry["printer"])
    missing = []
    for it in entry["items"]:
        if it.get("spool"):
            continue
        t = it.get("tray")
        sp = tray_to_spool(snap, printer, t[0], t[1]) if t and printer else None
        if sp:
            it["spool"] = sp["id"]
        elif not printer:
            missing.append(f"{entry['printer']} isn't set up in Spool Stock (Setup > Printers, with serial {entry['serial']})")
        else:
            missing.append(f"No spool in {printer.get('name')} {tray_name(t)} in Spool Stock")
    return missing


def book(entry: dict) -> str:
    """Take an entry's grams off its spools, in one write with a record for Undo. Returns "booked" or "already"."""
    pct = max(1, min(100, int(entry.get("pct", 100))))
    use: dict[str, float] = {}
    for it in entry["items"]:
        if it.get("spool") and it.get("g"):
            use[it["spool"]] = use.get(it["spool"], 0) + float(it["g"]) * pct / 100
    if not use:
        return "nothing"
    at = int(entry.get("at") or time.time() * 1000)
    for attempt in range(3):
        writes = [{"update": {"name": CLOUD._name(f"jobs/{entry['key']}"), "fields": to_fs({
            "at": at, "printer": entry["printer"], "serial": entry["serial"], "job": entry["name"], "plate": entry["plate"],
            "pct": pct, "result": entry.get("result") or "", "source": "orca",
            "items": [{"spool": sid, "g": round(g, 1)} for sid, g in use.items()]})["mapValue"]["fields"]},
            "currentDocument": {"exists": False}}]
        for sid, g in use.items():
            doc, when = CLOUD.get(f"spools/{sid}")
            if doc is None:
                raise CloudError("NOT_FOUND", f"spool {sid.upper()} isn't in Spool Stock")
            have = doc.get("remainG") if doc.get("remainG") is not None else doc.get("netG") or 0
            prints = [{"at": at, "name": entry["name"], "g": round(g, 1)}] + list(doc.get("prints") or [])
            writes.append({"update": {"name": CLOUD._name(f"spools/{sid}"),
                                      "fields": {"remainG": to_fs(max(0, round(have - g))), "prints": to_fs(prints[:30])}},
                           "updateMask": {"fieldPaths": ["remainG", "prints"]},
                           "currentDocument": {"updateTime": when}})
        try:
            CLOUD.commit(writes)
            entry["items_booked"] = [{"spool": sid, "g": round(g, 1)} for sid, g in use.items()]
            return "booked"
        except CloudError as e:
            if e.status == "ALREADY_EXISTS" or e.code == 409:
                return "already"
            if e.status != "FAILED_PRECONDITION" or attempt == 2:
                raise
            time.sleep(1)
    return "booked"


_tick_lock = threading.Lock()


def tick():
    """Send what's in the outbox and keep the spool list fresh. Runs on the background thread, or from the tab
    when Orca only allows network calls there."""
    if not CLOUD.signed_in or not _tick_lock.acquire(blocking=False):
        return
    try:
        with STORE.lock:
            todo = list(STORE.outbox)
        snap = CLOUD.snapshot(max_age=0) if todo else None
        for entry in todo:
            missing = resolve(entry, snap)
            with STORE.lock:
                if missing:
                    entry["why"] = "; ".join(dict.fromkeys(missing))
                    STORE.outbox.remove(entry)
                    STORE.pending.append(entry)
                    STORE.save()
                    log("book.waiting", job=entry["name"], why=entry["why"])
                    continue
            result = book(entry)
            with STORE.lock:
                if entry in STORE.outbox:
                    STORE.outbox.remove(entry)
                done = ", ".join(f"{i['g']} g off {i['spool'].upper()}" for i in entry.get("items_booked", []))
                what = {"booked": done, "already": "already taken off", "nothing": "nothing to take off"}[result]
                STORE.note(f"{entry['printer']}: {entry['name']}, {what}", entry["key"])
                STORE.save()
            log("book.done", job=entry["name"], result=result, items=entry.get("items_booked"))
            CLOUD.snap = None
        CLOUD.snapshot(max_age=60)
    except PermissionError as e:
        if not CLOUD.ui_mode:
            log("cloud.background_blocked", error=str(e))
        CLOUD.ui_mode = True
        raise
    except CloudError as e:
        CLOUD.status = f"Spool Stock: {e}"
        log("cloud.error", error=str(e))
    except Exception as e:
        CLOUD.status = f"Spool Stock: {type(e).__name__}: {e}"
        log("cloud.error", error=traceback.format_exc())
    finally:
        _tick_lock.release()


def worker():
    while True:
        _wake.wait(20)
        _wake.clear()
        if CLOUD.ui_mode:
            continue
        try:
            tick()
        except PermissionError:
            pass


# ---------- Orca's permission to reach the network ----------

_links: dict[str, Link] = {}
_allowed: set[str] = set()
_started = False


def authorise(host: str, ports: tuple) -> str:
    """Reach a host once from Orca's main thread. Orca only asks "allow this plugin to connect?" for
    calls made there, and the background threads then reuse the grant. Returns "" when allowed."""
    problems = []
    for port in ports:
        try:
            socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
            with socket.create_connection((host, port), timeout=4):
                pass
        except PermissionError as e:
            log("permission.denied", host=host, port=port, error=str(e))
            return "Orca didn't allow the connection. Press Connect and choose Yes."
        except OSError as e:
            problems.append(f"port {port}: {e}")
    if problems and len(problems) == len(ports):
        return f"Couldn't reach {host} ({problems[0]})."
    _allowed.add(host)
    return ""


def connect_all() -> list[str]:
    """Start a link for each saved printer. Call from the tab, so Orca can ask permission."""
    notes = []
    for p in load_printers():
        key = p.get("name") or p.get("ip")
        if not (p.get("ip") and p.get("code")) or (key in _links and not _links[key].stop.is_set()):
            continue
        if p["ip"] not in _allowed:
            why = authorise(p["ip"], (MQTT_PORT, FTP_PORT))
            if why:
                notes.append(f"{key}: {why}")
                continue
        _links[key] = Link(p)
    if CLOUD.signed_in:
        for h in CLOUD_HOSTS:
            if h not in _allowed:
                why = authorise(h, (443,))
                if why:
                    notes.append(f"Spool Stock: {why}")
    return notes


def disconnect_all() -> None:
    for link in _links.values():
        link.stop.set()
    _links.clear()
    for cam in list(_cams.values()):
        cam.stop.set()
    _cams.clear()


# ---------- your stock, for the Stock view in the tab ----------

LOW_G = 150   # the same as the app's low stock mark
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _left(s: dict):
    v = s.get("remainG") if s.get("remainG") is not None else s.get("netG")
    return v if isinstance(v, (int, float)) else None


def stock_view(snap) -> dict | None:
    """Every spool not used up, with where it is, in the order the app shows storages."""
    if not snap:
        return None
    locs = snap["locs"]
    order = sorted(locs.values(), key=lambda l: (l.get("order") if isinstance(l.get("order"), (int, float)) else 1e9, l.get("name") or "", l["id"]))
    rank = {l["id"]: i for i, l in enumerate(order)}
    fed: dict = {}   # storage id -> [(printer name, space or None)]
    for p in snap["printers"].values():
        for f in p.get("feeds") or []:
            if f.get("loc"):
                fed.setdefault(f["loc"], []).append((p.get("name") or "", int(f.get("space") or 1) if f.get("kind") == "external" and _spaced(locs.get(f["loc"])) else None))
    places: dict = {}
    for l in order:
        for i, s in enumerate(_seated(snap, l) if _spaced(l) else _loose(snap, l)):
            if s:
                places[s["id"]] = (rank[l["id"]], i, (i + 1) if _spaced(l) else None)
    out = []
    for s in snap["spools"].values():
        if s.get("status") == "used":
            continue
        l = locs.get(s.get("loc"))
        r, i, space = places.get(s["id"], (len(order), 0, None))
        cols = [c for c in (s.get("colours") or []) if isinstance(c, str) and HEX.match(c)] or ([s["hex"]] if HEX.match(str(s.get("hex") or "")) else [])
        left, net = _left(s), s.get("netG") if isinstance(s.get("netG"), (int, float)) else None
        printer = ""
        if l:
            for name, sp in fed.get(l["id"], []):
                if sp is None or sp == space:
                    printer = name
        out.append({"id": s["id"], "code": s["id"].upper(), "brand": s.get("brand") or "", "material": s.get("material") or "",
                    "colour": s.get("colour") or "", "finish": s.get("finish") or "", "cols": cols,
                    "pattern": s.get("pattern") if len(cols) > 1 and s.get("pattern") in ("split", "gradient") else "solid",
                    "left": round(left) if left is not None else None, "net": round(net) if net else None,
                    "low": left is not None and left <= LOW_G,
                    "where": (l.get("name") or "") + (f", space {space}" if space else "") if l else "Not in a storage",
                    "group": (l.get("group") or "") if l else "", "locRank": r, "pos": i, "printer": printer})
    out.sort(key=lambda x: (x["locRank"], x["pos"], x["id"]))
    return {"spools": out, "at": time.strftime("%H:%M", time.localtime(CLOUD.snap_at)) if CLOUD.snap_at else ""}


# ---------- presets: a Spool Stock group in Orca's filament list ----------
#
# The app keeps a list of the filaments you own in config/orca, each with its preset and the Orca profiles it
# can build on. The plugin writes them as a local preset bundle in user/<folder>/_local/spool-stock/, which
# Orca shows as its own group in the filament list, and asks Orca to reload that bundle so they appear without
# a restart. Older Orca builds without the reload need a restart instead.

BUNDLE_ID, BUNDLE_NAME = "spool-stock", "Spool Stock"
_SAFE_NAME = re.compile(r'^[^\\/:*?"<>|\x00-\x1f]{1,150}$')
_SAFE_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_KEEP_OUT = ("compatible_printers", "compatible_prints", "compatible_printers_condition", "filament_start_gcode", "filament_end_gcode")


def _preset_state() -> dict:
    return _read_json(PRESET_STATE, {})


def _save_preset_state(d: dict) -> None:
    _write_json(PRESET_STATE, d)


def orca_dir() -> Path:
    return DATA.parent


def preset_folder() -> str:
    """Orca's active user preset folder: from a user preset's file where Orca shows one, then Orca's settings."""
    folder = ""
    try:
        bundle = orca.host.preset_bundle()
        for coll in (bundle.filaments, bundle.prints, bundle.printers):
            for i in range(coll.size()):
                pr = coll.preset(i)
                if pr.is_user():
                    parts = os.path.normpath(str(pr.file or "")).replace("\\", "/").split("/")
                    if "user" in parts and parts.index("user") + 1 < len(parts) - 1:
                        folder = parts[parts.index("user") + 1]
                        break
            if folder:
                break
    except Exception:
        pass
    if not folder:
        try:
            text = (orca_dir() / "OrcaSlicer.conf").read_text(encoding="utf-8")
            folder = str(json.loads(text[: text.rfind("}") + 1]).get("app", {}).get("preset_folder") or "")
        except Exception:
            folder = ""
    return folder if re.fullmatch(r"[\w.-]+", folder or "") and folder not in (".", "..") else "default"


def _finder():
    """A function telling whether Orca has a filament preset by that name, or None if Orca won't say."""
    try:
        fil = orca.host.preset_bundle().filaments
        find = getattr(fil, "find_preset", None)
        if callable(find):
            return lambda name: find(name) is not None
        names = set(fil.preset_names())
        return lambda name: name in names
    except Exception:
        return None


def build_presets(items: list, has=None) -> tuple[dict, list]:
    """Turn the app's list into {file name: preset}. Returns them and the labels of any that can't be built."""
    out, skipped = {}, []
    for it in items or []:
        try:
            p = json.loads(it.get("text") or "")
        except Exception:
            p = None
        name = (it.get("name") or "").strip()
        if not isinstance(p, dict) or not _SAFE_NAME.match(name) or name.startswith(".") or p.get("name") != name:
            skipped.append(it.get("label") or name or "?")
            continue
        parents = [x for x in (it.get("parents") or []) if isinstance(x, str) and x]
        parent = next((x for x in parents if has(x)), None) if has else (parents[0] if parents else None)
        if not parent:
            skipped.append(it.get("label") or name)
            continue
        preset = {"type": "filament", "name": name, "from": "User", "instantiation": "true", "inherits": parent,
                  "version": str(p.get("version") or "2.4.0.0"), "filament_settings_id": [name]}
        for k, v in p.items():
            if k in preset or k in _KEEP_OUT or k.startswith("printhost") or not _SAFE_KEY.match(k):
                continue
            if isinstance(v, list) and all(isinstance(x, str) and len(x) <= 200 for x in v) and len(v) <= 16:
                preset[k] = v
            elif isinstance(v, str) and len(v) <= 200:
                preset[k] = v
        out[name + ".json"] = preset
    return out, skipped


def _write_text_if_changed(path: Path, text: str) -> bool:
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return True


def reload_bundle() -> tuple[str, str]:
    """Ask Orca to reload the Spool Stock bundle. Returns ("live" or "restart" or "error", detail)."""
    bundles = getattr(getattr(getattr(orca, "host", None), "preset", None), "bundles", None)
    reload_local = getattr(bundles, "reload_local", None)
    if not callable(reload_local):
        return "restart", "this Orca can't reload presets while open"
    listed = None
    list_ids = getattr(bundles, "list_ids", None)
    if callable(list_ids):
        try:
            listed = list(list_ids())
        except Exception as e:
            log("presets.list_error", error=f"{type(e).__name__}: {e}")
    try:
        reload_local(BUNDLE_ID)
        return "live", f"bundles {listed}" if listed is not None else ""
    except Exception as e:
        return "error", f"{type(e).__name__}: {e}" + (f" (bundles {listed})" if listed is not None else "")


def sync_presets(feed: dict | None) -> dict:
    """Write the presets into Orca's Spool Stock bundle and reload it. Runs on Orca's main thread."""
    state = _preset_state()
    items = (feed or {}).get("items") or []
    folder = preset_folder()
    base = orca_dir() / "user" / folder / "_local" / BUNDLE_ID
    fdir = base / "filament"
    presets, skipped = build_presets(items, _finder())
    meta = {"id": BUNDLE_ID, "name": BUNDLE_NAME, "version": VERSION, "description": "The filaments you own in Spool Stock", "author": "Spool Stock"}
    changed = _write_text_if_changed(base / "bundle_metadata.json", json.dumps(meta, indent=4))
    for file, preset in presets.items():
        changed |= _write_text_if_changed(fdir / file, json.dumps(preset, indent=4))
    removed = 0
    if fdir.is_dir():
        for f in fdir.iterdir():
            if f.suffix in (".json", ".info") and f.with_suffix(".json").name not in presets:
                try:
                    f.unlink()
                    removed += f.suffix == ".json"
                    changed = True
                except OSError as e:
                    log("presets.remove_error", file=f.name, error=str(e))
    # Copies of the same presets sent before by the Windows app would show twice, so move them aside
    moved = 0
    old_dir = orca_dir() / "user" / folder / "filament"
    for file in presets:
        old = old_dir / file
        try:
            j = json.loads(old.read_text(encoding="utf-8")) if old.is_file() else None
        except Exception:
            j = None
        if isinstance(j, dict) and j.get("from") == "User" and j.get("name") == file[:-5] and file.endswith(" (Spool Stock).json"):
            keep = DATA / "old-presets" / file
            keep.parent.mkdir(parents=True, exist_ok=True)
            try:
                old.replace(keep)
                info = old.with_suffix(".info")
                if info.exists():
                    info.replace(keep.with_suffix(".info"))
                moved += 1
            except OSError as e:
                log("presets.move_error", file=file, error=str(e))
    how, detail = ("live", "nothing changed") if not changed and state.get("how") == "live" else reload_bundle()
    state.update({"on": True, "sig": (feed or {}).get("sig", ""), "folder": folder, "count": len(presets), "skipped": skipped,
                  "how": how, "detail": detail, "at": time.time(), "moved": moved + int(state.get("moved") or 0) if moved else state.get("moved", 0)})
    _save_preset_state(state)
    log("presets.sync", folder=folder, presets=len(presets), removed=removed, skipped=skipped, moved_old=moved, reload=how, detail=detail)
    return state


def presets_view(feed: dict | None) -> dict:
    st = _preset_state()
    items = (feed or {}).get("items") or []
    return {"on": bool(st.get("on")), "feed": [{"label": it.get("label") or it.get("name"), "own": bool(it.get("own")), "name": it.get("name")} for it in items],
            "have": feed is not None, "count": st.get("count", 0), "skipped": st.get("skipped") or [], "how": st.get("how", ""),
            "detail": st.get("detail", ""), "when": time.strftime("%d %b %H:%M", time.localtime(st["at"])) if st.get("at") else "",
            "behind": bool(st.get("on")) and feed is not None and feed.get("sig") != st.get("sig"), "moved": st.get("moved", 0)}


def remove_presets() -> None:
    """Turn the group off: remove the bundle and reload so it leaves Orca's list."""
    st = _preset_state()
    base = orca_dir() / "user" / (st.get("folder") or preset_folder()) / "_local" / BUNDLE_ID
    for f in (base / "filament").glob("*") if (base / "filament").is_dir() else []:
        try:
            f.unlink()
        except OSError:
            pass
    how, detail = reload_bundle()
    st.update({"on": False, "sig": "", "count": 0, "how": how, "detail": detail, "at": time.time()})
    _save_preset_state(st)
    log("presets.off", reload=how, detail=detail)


# ---------- cameras: Tapo and other RTSP cameras, played in the tab ----------
#
# Orca's tab is a web page, which can't play RTSP. The plugin connects to each camera itself (RTSP over TCP),
# takes the H.264 video out of the RTP packets and rewraps it as fragmented MP4, which the page plays with
# MediaSource. Nothing is decoded or re-encoded. A camera is only streamed while the Cameras view asks for it.
# Camera links (with their passwords) stay in cameras.json on this PC and are never logged.

CAMERAS = DATA / "cameras.json"
CAM_IDLE = 10          # seconds without the page asking before a camera is let go
CAM_KEEP = 300         # fragments kept for a page that's catching up


def load_cameras() -> list[dict]:
    items = _read_json(CAMERAS, [])
    for c in items:
        u = parse_rtsp(c.get("url", ""))
        if u:
            _secrets.add(u["password"])
            _secrets.add(c["url"])
    return items


def save_cameras(items: list[dict]) -> None:
    _write_json(CAMERAS, items, private=True)


def parse_rtsp(url: str) -> dict | None:
    """rtsp://user:password@host[:port]/path. Lenient: a space or @ in the name or password is fine."""
    m = re.match(r"^\s*rtsp://(.+)$", url or "", re.I)
    if not m:
        return None
    rest = m.group(1).strip()
    # the host comes after the last @, so an @ or / in the password still works
    at = rest.rfind("@")
    cred, after = (rest[:at], rest[at + 1:]) if at >= 0 else ("", rest)
    hostport, _, path = after.partition("/")
    user, _, password = cred.partition(":")
    host, _, port = hostport.partition(":")
    if not host or (port and not port.isdigit()):
        return None
    user, password = urllib.parse.unquote(user), urllib.parse.unquote(password)
    port_n = int(port or 554)
    return {"user": user, "password": password, "host": host, "port": port_n,
            "url": f"rtsp://{host}{':' + str(port_n) if port else ''}/{path}"}


# --- H.264 bits ---

def _unescape(nal: bytes) -> bytes:
    return nal.replace(b"\x00\x00\x03", b"\x00\x00")


class _Bits:
    def __init__(self, data: bytes):
        self.d, self.i = data, 0

    def u(self, n: int) -> int:
        v = 0
        for _ in range(n):
            v = (v << 1) | ((self.d[self.i >> 3] >> (7 - (self.i & 7))) & 1)
            self.i += 1
        return v

    def ue(self) -> int:
        z = 0
        while self.u(1) == 0:
            z += 1
            if z > 31:
                raise ValueError("bad exp-Golomb code")
        return (1 << z) - 1 + self.u(z)

    def se(self) -> int:
        v = self.ue()
        return (v + 1) // 2 if v & 1 else -(v // 2)


def sps_size(sps: bytes) -> tuple[int, int]:
    """Width and height from an H.264 sequence parameter set (NAL header included)."""
    b = _Bits(_unescape(sps[1:]))
    profile = b.u(8)
    b.u(16)
    b.ue()
    chroma = 1
    if profile in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135):
        chroma = b.ue()
        if chroma == 3:
            b.u(1)
        b.ue(); b.ue(); b.u(1)
        if b.u(1):
            for i in range(8 if chroma != 3 else 12):
                if b.u(1):
                    last, nxt = 8, 8
                    for _ in range(16 if i < 6 else 64):
                        if nxt:
                            nxt = (last + b.se() + 256) % 256
                        last = nxt or last
    b.ue()
    poc = b.ue()
    if poc == 0:
        b.ue()
    elif poc == 1:
        b.u(1); b.se(); b.se()
        for _ in range(b.ue()):
            b.se()
    b.ue(); b.u(1)
    w_mbs, h_maps = b.ue() + 1, b.ue() + 1
    frame_mbs_only = b.u(1)
    if not frame_mbs_only:
        b.u(1)
    b.u(1)
    l = r = t = bt = 0
    if b.u(1):
        l, r, t, bt = b.ue(), b.ue(), b.ue(), b.ue()
    cx, cy = (2, 2) if chroma == 1 else (2, 1) if chroma == 2 else (1, 1)
    return w_mbs * 16 - (l + r) * cx, (2 - frame_mbs_only) * h_maps * 16 - (t + bt) * cy * (2 - frame_mbs_only)


def _box(kind: bytes, *parts: bytes) -> bytes:
    body = b"".join(parts)
    return struct.pack(">I", 8 + len(body)) + kind + body


def _full(kind: bytes, version: int, flags: int, *parts: bytes) -> bytes:
    return _box(kind, struct.pack(">I", (version << 24) | flags), *parts)


_MATRIX = struct.pack(">9I", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)


def mp4_init(sps: bytes, pps: bytes) -> tuple[bytes, str]:
    """The MP4 header (ftyp and moov) for one H.264 track at 90 kHz, and the codec string for MediaSource."""
    w, h = sps_size(sps)
    avcc = _box(b"avcC", bytes([1, sps[1], sps[2], sps[3], 0xFF, 0xE1]), struct.pack(">H", len(sps)), sps,
                b"\x01", struct.pack(">H", len(pps)), pps)
    avc1 = _box(b"avc1", b"\x00" * 6, struct.pack(">H", 1), b"\x00" * 16, struct.pack(">HH", w, h),
                struct.pack(">II", 0x480000, 0x480000), b"\x00" * 4, struct.pack(">H", 1), b"\x00" * 32,
                struct.pack(">Hh", 0x18, -1), avcc)
    stbl = _box(b"stbl", _full(b"stsd", 0, 0, struct.pack(">I", 1), avc1), _full(b"stts", 0, 0, b"\x00" * 4),
                _full(b"stsc", 0, 0, b"\x00" * 4), _full(b"stsz", 0, 0, b"\x00" * 8), _full(b"stco", 0, 0, b"\x00" * 4))
    minf = _box(b"minf", _full(b"vmhd", 0, 1, b"\x00" * 8),
                _box(b"dinf", _full(b"dref", 0, 0, struct.pack(">I", 1), _full(b"url ", 0, 1))), stbl)
    mdia = _box(b"mdia", _full(b"mdhd", 0, 0, struct.pack(">IIII", 0, 0, 90000, 0), struct.pack(">HH", 0x55C4, 0)),
                _full(b"hdlr", 0, 0, b"\x00" * 4, b"vide", b"\x00" * 12, b"Video\x00"), minf)
    tkhd = _full(b"tkhd", 0, 3, struct.pack(">IIIII", 0, 0, 1, 0, 0), b"\x00" * 8, struct.pack(">hhhH", 0, 0, 0, 0),
                 _MATRIX, struct.pack(">II", w << 16, h << 16))
    mvhd = _full(b"mvhd", 0, 0, struct.pack(">IIII", 0, 0, 1000, 0), struct.pack(">IH", 0x10000, 0x100), b"\x00" * 10,
                 _MATRIX, b"\x00" * 24, struct.pack(">I", 2))
    moov = _box(b"moov", mvhd, _box(b"trak", tkhd, mdia),
                _box(b"mvex", _full(b"trex", 0, 0, struct.pack(">IIIII", 1, 1, 0, 0, 0))))
    ftyp = _box(b"ftyp", b"isom", struct.pack(">I", 0x200), b"isomiso6avc1mp41")
    return ftyp + moov, "avc1.%02X%02X%02X" % (sps[1], sps[2], sps[3])


def mp4_fragment(seq: int, start: int, duration: int, nals: list[bytes], key: bool) -> bytes:
    """One frame as moof and mdat. start and duration are in 90 kHz ticks."""
    data = b"".join(struct.pack(">I", len(n)) + n for n in nals)
    flags = 0x02000000 if key else 0x01010000

    def moof(offset):
        trun = _full(b"trun", 0, 0x000701, struct.pack(">Ii", 1, offset), struct.pack(">III", duration, len(data), flags))
        traf = _box(b"traf", _full(b"tfhd", 0, 0x020000, struct.pack(">I", 1)), _full(b"tfdt", 1, 0, struct.pack(">Q", start)), trun)
        return _box(b"moof", _full(b"mfhd", 0, 0, struct.pack(">I", seq)), traf)
    m = moof(0)
    m = moof(len(m) + 8)
    return m + _box(b"mdat", data)


# --- RTSP over TCP ---

class CamError(Exception):
    pass


class _Reader:
    def __init__(self, sock):
        self.s, self.buf = sock, b""

    def _more(self):
        chunk = self.s.recv(65536)
        if not chunk:
            raise ConnectionError("the camera closed the connection")
        self.buf += chunk

    def exact(self, n: int) -> bytes:
        while len(self.buf) < n:
            self._more()
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def until(self, mark: bytes) -> bytes:
        while mark not in self.buf:
            if len(self.buf) > 65536:
                raise CamError("the camera's reply was too long")
            self._more()
        i = self.buf.index(mark) + len(mark)
        out, self.buf = self.buf[:i], self.buf[i:]
        return out


class Camera:
    """One camera: connects when the page asks for it, and lets go once the page stops asking."""

    def __init__(self, cid: str, url: str, name: str):
        self.id, self.name, self.url = cid, name, url
        self.lock = threading.Lock()
        self.wanted = 0.0
        self.status = "Starting"
        self.gen = 0
        self.init = b""
        self.mime = ""
        self.frags: list[tuple[int, bytes]] = []   # (seq, fragment), kept from the last keyframe on
        self.seq = 0
        self.thread = None
        self.stop = threading.Event()

    def want(self):
        self.wanted = time.monotonic()
        if not (self.thread and self.thread.is_alive()):
            self.stop.clear()
            self.thread = threading.Thread(target=self.run, daemon=True, name="spoolstock-cam-" + self.id)
            self.thread.start()

    def idle(self) -> bool:
        return time.monotonic() - self.wanted > CAM_IDLE

    def take(self, gen: int, after: int) -> dict:
        """What the page hasn't had yet: the header when it's new to this stream, then any fragments."""
        with self.lock:
            out = {"id": self.id, "status": self.status, "gen": self.gen, "mime": self.mime}
            if not self.init:
                return out
            if gen != self.gen:
                out["init"] = base64.b64encode(self.init).decode()
                after = -1
            first_key = self.frags[0][0] if self.frags else 0
            if after < first_key - 1:
                after = -1   # too far behind: start again from the last keyframe
            new = [(s, f) for s, f in self.frags if s > after]
            out["chunks"] = [base64.b64encode(f).decode() for _, f in new]
            out["seq"] = new[-1][0] if new else after
            return out

    def run(self):
        delay = 2
        while not self.stop.is_set() and not self.idle():
            try:
                self.session()
                delay = 2
            except PermissionError as e:
                self.status = "Orca blocked the connection. Press Connect on the Prints page and choose Yes."
                _allowed.discard((parse_rtsp(self.url) or {}).get("host"))
                log("camera.blocked", camera=self.name, error=str(e))
                return
            except CamError as e:
                self.status = str(e)
                log("camera.error", camera=self.name, error=str(e))
                if self.stop.wait(15):
                    break
            except Exception as e:
                self.status = f"Reconnecting ({type(e).__name__}: {e})"
                log("camera.error", camera=self.name, error=f"{type(e).__name__}: {e}")
                if self.stop.wait(delay):
                    break
                delay = min(delay * 2, 30)
        if self.status in ("Live", "Starting", "Connecting"):
            self.status = "Paused"
        log("camera.stopped", camera=self.name)

    # the RTSP conversation

    def _request(self, s, rd, method: str, url: str, extra: dict | None = None) -> tuple[int, dict, bytes]:
        self.cseq += 1
        headers = {"CSeq": str(self.cseq), "User-Agent": "SpoolStock/" + VERSION, **(extra or {})}
        if self.session_id:
            headers["Session"] = self.session_id
        if self.auth:
            headers["Authorization"] = self._authorisation(method, url)
        s.sendall((f"{method} {url} RTSP/1.0\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers.items()) + "\r\n").encode())
        while True:
            first = rd.exact(1)
            if first == b"$":   # video already flowing ahead of the reply
                rd.exact(1)
                rd.exact(struct.unpack(">H", rd.exact(2))[0])
                continue
            head = (first + rd.until(b"\r\n\r\n")).decode("utf-8", "replace")
            lines = head.split("\r\n")
            code = int(lines[0].split()[1]) if len(lines[0].split()) > 1 else 0
            hdrs: dict = {}
            for line in lines[1:]:
                k, _, v = line.partition(":")
                if k:
                    hdrs.setdefault(k.strip().lower(), []).append(v.strip())
            body = rd.exact(int(hdrs.get("content-length", ["0"])[0] or 0))
            if hdrs.get("cseq", [""])[0] in ("", str(self.cseq)):
                return code, hdrs, body

    def _authorisation(self, method: str, url: str) -> str:
        kind, p = self.auth
        u = parse_rtsp(self.url)
        if kind == "basic":
            return "Basic " + base64.b64encode(f"{u['user']}:{u['password']}".encode()).decode()
        md5 = lambda x: hashlib.md5(x.encode()).hexdigest()
        ha1, ha2 = md5(f"{u['user']}:{p.get('realm', '')}:{u['password']}"), md5(f"{method}:{url}")
        head = f'Digest username="{u["user"]}", realm="{p.get("realm", "")}", nonce="{p.get("nonce", "")}", uri="{url}"'
        if "auth" in [q.strip() for q in p.get("qop", "").split(",")]:
            p["nc"] = p.get("nc", 0) + 1
            nc, cnonce = f"{p['nc']:08x}", p.setdefault("cnonce", os.urandom(8).hex())
            head += f', qop=auth, nc={nc}, cnonce="{cnonce}", response="{md5(f"{ha1}:{p["nonce"]}:{nc}:{cnonce}:auth:{ha2}")}"'
        else:
            head += f', response="{md5(f"{ha1}:{p.get("nonce", "")}:{ha2}")}"'
        if p.get("algorithm"):
            head += f', algorithm={p["algorithm"]}'
        if p.get("opaque"):
            head += f', opaque="{p["opaque"]}"'
        return head

    def session(self):
        u = parse_rtsp(self.url)
        if not u:
            raise CamError("That isn't an RTSP link. It should start rtsp://")
        self.status, self.cseq, self.session_id, self.auth = "Connecting", 0, "", None
        with self.lock:
            self.init, self.frags = b"", []   # a new connection starts a new stream for the page
        with socket.create_connection((u["host"], u["port"]), timeout=10) as s:
            s.settimeout(15)
            rd = _Reader(s)
            code, hdrs, body = self._request(s, rd, "DESCRIBE", u["url"], {"Accept": "application/sdp"})
            if code == 401:
                if not u["user"]:
                    raise CamError("The camera wants a username and password in the link.")
                chal = " ".join(hdrs.get("www-authenticate", []))
                digest = next((h for h in hdrs.get("www-authenticate", []) if h.lower().startswith("digest")), "")
                if digest:
                    self.auth = ("digest", dict(re.findall(r'(\w+)="([^"]*)"', digest)))
                elif "basic" in chal.lower():
                    self.auth = ("basic", {})
                code, hdrs, body = self._request(s, rd, "DESCRIBE", u["url"], {"Accept": "application/sdp"})
                if code == 401:
                    raise CamError("The camera didn't accept the username and password. Check the Camera Account in the Tapo app.")
            if code == 404:
                raise CamError("The camera has no stream at that link. Check the end of it, for example /stream2.")
            if code != 200:
                raise CamError(f"The camera refused the stream ({code}).")
            sdp = body.decode("utf-8", "replace")
            base = (hdrs.get("content-base") or [u["url"]])[0]
            video = sdp.split("m=video", 1)[1].split("\nm=", 1)[0] if "m=video" in sdp else ""
            if not video:
                raise CamError("The camera didn't offer any video.")
            codec = (re.search(r"a=rtpmap:\d+\s+([\w-]+)/", video) or [None, ""])[1].upper()
            if codec != "H264":
                raise CamError(f"The camera sends {codec or 'an unknown format'} video, and only H.264 can be shown here.")
            control = (re.search(r"a=control:\s*(\S+)", video) or [None, ""])[1]
            track = base if control in ("", "*") else control if control.lower().startswith("rtsp://") else base.rstrip("/") + "/" + control
            self.sps = self.pps = b""
            sprop = re.search(r"sprop-parameter-sets=([A-Za-z0-9+/=]+),([A-Za-z0-9+/=]+)", video)
            if sprop:
                try:
                    self.sps, self.pps = base64.b64decode(sprop.group(1)), base64.b64decode(sprop.group(2))
                except Exception:
                    pass
            code, hdrs, _ = self._request(s, rd, "SETUP", track, {"Transport": "RTP/AVP/TCP;unicast;interleaved=0-1"})
            if code != 200:
                raise CamError(f"The camera refused to send video over TCP ({code}).")
            self.session_id = (hdrs.get("session") or [""])[0].split(";")[0]
            chan = int((re.search(r"interleaved=(\d+)", (hdrs.get("transport") or [""])[0]) or [None, "0"])[1])
            code, _, _ = self._request(s, rd, "PLAY", base, {"Range": "npt=0.000-"})
            if code != 200:
                raise CamError(f"The camera wouldn't start the stream ({code}).")
            log("camera.connected", camera=self.name, host=u["host"])
            self.status = "Waiting for a picture"
            self.read_video(s, rd, chan, base)
            try:
                s.sendall((f"TEARDOWN {base} RTSP/1.0\r\nCSeq: {self.cseq + 1}\r\nSession: {self.session_id}\r\n\r\n").encode())
            except OSError:
                pass

    def read_video(self, s, rd, chan: int, base: str):
        nals: list[bytes] = []
        fu = None
        ts_now = None
        pending = None   # the last frame, held until the next one gives its duration
        t0 = None
        last_ts = 0
        unwrapped = 0
        ping = time.monotonic()
        while not self.stop.is_set() and not self.idle():
            if time.monotonic() - ping > 25:
                self.cseq += 1
                s.sendall((f"GET_PARAMETER {base} RTSP/1.0\r\nCSeq: {self.cseq}\r\nSession: {self.session_id}\r\n"
                           + (f"Authorization: {self._authorisation('GET_PARAMETER', base)}\r\n" if self.auth else "") + "\r\n").encode())
                ping = time.monotonic()
            first = rd.exact(1)
            if first != b"$":   # a reply to the keep-alive
                head = (first + rd.until(b"\r\n\r\n")).decode("utf-8", "replace")
                n = re.search(r"content-length:\s*(\d+)", head, re.I)
                rd.exact(int(n.group(1)) if n else 0)
                continue
            ch = rd.exact(1)[0]
            pkt = rd.exact(struct.unpack(">H", rd.exact(2))[0])
            if ch != chan or len(pkt) < 13:
                continue
            cc = pkt[0] & 0x0F
            marker = pkt[1] & 0x80
            ts = struct.unpack(">I", pkt[4:8])[0]
            off = 12 + cc * 4
            if pkt[0] & 0x10 and len(pkt) >= off + 4:   # header extension
                off += 4 + struct.unpack(">H", pkt[off + 2:off + 4])[0] * 4
            pay = pkt[off:len(pkt) - (pkt[-1] if pkt[0] & 0x20 else 0)]
            if not pay:
                continue
            if ts_now is not None and ts != ts_now and nals:
                pending = self._frame(nals, ts_now, pending)
                nals = []
            ts_now = ts
            t = pay[0] & 0x1F
            if 1 <= t <= 23:
                nals.append(pay)
            elif t == 24:   # STAP-A: several NALs in one packet
                i = 1
                while i + 2 <= len(pay):
                    n = struct.unpack(">H", pay[i:i + 2])[0]
                    nals.append(pay[i + 2:i + 2 + n])
                    i += 2 + n
            elif t == 28 and len(pay) > 2:   # FU-A: one NAL split over several packets
                if pay[1] & 0x80:
                    fu = bytearray([(pay[0] & 0xE0) | (pay[1] & 0x1F)]) + pay[2:]
                elif fu is not None:
                    fu += pay[2:]
                if pay[1] & 0x40 and fu is not None:
                    nals.append(bytes(fu))
                    fu = None
            if marker and nals:
                pending = self._frame(nals, ts, pending)
                nals = []
                ts_now = None

    def _frame(self, nals: list[bytes], ts: int, pending):
        """Collect one frame. Emits the frame before it, now that its duration is known."""
        key = False
        body = []
        for n in nals:
            t = n[0] & 0x1F
            if t == 7:
                self.sps = n
            elif t == 8:
                self.pps = n
            elif t != 9:
                body.append(n)
                key |= t == 5
        if not body:
            return pending
        if pending:
            p_ts, p_body, p_key = pending
            self._emit(p_body, p_key, p_ts, (ts - p_ts) & 0xFFFFFFFF)
        return (ts, body, key)

    def _emit(self, body, key, ts, duration):
        if duration <= 0 or duration > 90000 * 5:
            duration = 6000
        with self.lock:
            if not self.init:
                if not (key and self.sps and self.pps):
                    return   # wait for the first keyframe
                try:
                    self.init, self.mime = mp4_init(self.sps, self.pps)
                except Exception as e:
                    raise CamError(f"Couldn't read the camera's video settings ({e}).")
                self.mime = f'video/mp4; codecs="{self.mime}"'
                self.gen += 1
                self.frags, self.keys, self.base, self.ts_last = [], [], 0, ts
                log("camera.video", camera=self.name, size=sps_size(self.sps), codec=self.mime)
            self.base += (ts - self.ts_last) & 0xFFFFFFFF
            self.ts_last = ts
            self.seq += 1
            frag = mp4_fragment(self.seq, self.base, duration, body, key)
            if key:   # keep the last two keyframes' worth, so a page that's a little behind can catch up
                self.keys = (self.keys + [self.seq])[-2:]
                self.frags = [f for f in self.frags if f[0] >= self.keys[0]]
            self.frags.append((self.seq, frag))
            if len(self.frags) > CAM_KEEP:
                del self.frags[: len(self.frags) - CAM_KEEP]
            self.status = "Live"


_cams: dict[str, Camera] = {}
_cam_checked: dict[str, float] = {}


def camera(cid: str) -> Camera | None:
    c = next((c for c in load_cameras() if c.get("id") == cid), None)
    if not c:
        _cams.pop(cid, None)
        return None
    cam = _cams.get(cid)
    if not cam or cam.url != c["url"]:
        if cam:
            cam.stop.set()
        cam = _cams[cid] = Camera(cid, c["url"], c.get("name") or cid)
    return cam


def cameras_view() -> list[dict]:
    return [{"id": c["id"], "name": c.get("name") or "", "printer": c.get("printer") or "",
             "host": (parse_rtsp(c.get("url", "")) or {}).get("host", "")} for c in load_cameras()]


def watch_cameras(want: list) -> dict:
    """The page's request for video. Runs on Orca's main thread, so the first connection can be allowed."""
    out = []
    for w in want or []:
        cam = camera(str(w.get("id") or ""))
        if not cam:
            continue
        u = parse_rtsp(cam.url) or {}
        host = u.get("host")
        if host and host not in _allowed:
            # Reach it once from here so Orca can ask permission. Only every 30 s, as an unreachable camera
            # would otherwise hold up Orca on every request.
            if time.monotonic() - _cam_checked.get(host, -60) < 30:
                out.append({"id": cam.id, "status": cam.status if cam.status != "Starting" else "Checking the connection"})
                continue
            _cam_checked[host] = time.monotonic()
            why = authorise(host, (u["port"],))
            if why.startswith("Orca didn't allow"):
                out.append({"id": cam.id, "status": "Orca didn't allow the connection to this camera. Press Connect on the Prints page and choose Yes."})
                continue
            _allowed.add(host)
        cam.want()
        out.append(cam.take(int(w.get("gen") or 0), int(w.get("seq") if w.get("seq") is not None else -1)))
    return {"type": "cams", "cams": out}


# ---------- the tab ----------

def _spool_options(snap) -> list[dict]:
    if not snap:
        return []
    out = [{"id": s["id"], "label": spool_label(s), "mat": (s.get("material") or "").upper()}
           for s in snap["spools"].values() if s.get("status") != "used"]
    return sorted(out, key=lambda x: x["id"])


_theme = {"at": 0.0, "dark": None}


def orca_dark():
    """Whether Orca is in dark mode, from its settings file. None if it can't be read."""
    if time.time() - _theme["at"] < 20:
        return _theme["dark"]
    _theme["at"] = time.time()
    try:
        text = (DATA.parent / "OrcaSlicer.conf").read_text(encoding="utf-8")
        v = json.loads(text[: text.rfind("}") + 1]).get("app", {}).get("dark_color_mode")
        _theme["dark"] = None if v is None else str(v).strip().lower() in ("1", "true")
    except Exception:
        _theme["dark"] = None
    return _theme["dark"]


def _state(hint: str = "") -> dict:
    snap = CLOUD.snap
    rows = []
    for p in load_printers():
        link = _links.get(p.get("name") or p.get("ip"))
        serial = (link.serial if link else "") or p.get("serial", "")
        ss = find_printer(snap, serial, p.get("name", "")) if snap else None
        job = STORE.active.get(serial)
        trays = []
        if job:
            fils = job.get("fils") or []
            for i, f in enumerate(fils):
                t = tray_of({**job, "fil_count": len(fils)}, f["id"], i)
                sp = tray_to_spool(snap, ss, t[0], t[1]) if (t and ss and snap) else None
                trays.append({"colour": f["colour"], "type": f["type"], "g": f["g"], "tray": tray_name(t), "spool": spool_label(sp) if sp else ""})
        rows.append({"name": p.get("name", ""), "ip": p.get("ip", ""), "serial": serial, "state": link.state if link else "not connected",
                     "linked": (ss or {}).get("name", ""), "job": {"name": job["name"], "percent": job.get("percent", 0), "state": job["state"],
                                                                     "late": job.get("late"), "trays": trays,
                                                                     "warnings": job.get("warnings") or []} if job else None})
    with STORE.lock:
        pending = []
        for e in STORE.pending:
            ss = find_printer(snap, e["serial"], e["printer"]) if snap else None
            items = []
            for it in e["items"]:
                t = it.get("tray")
                guess = it.get("spool") or ((tray_to_spool(snap, ss, t[0], t[1]) or {}).get("id", "") if (t and ss) else "")
                items.append({**it, "spool": guess, "trayName": tray_name(t)})
            pending.append({**e, "items": items})
        recent = [{"when": time.strftime("%d %b %H:%M", time.localtime(r["at"])), "text": r["text"]} for r in STORE.recent[:10]]
        outbox = len(STORE.outbox)
    return {"type": "state", "version": VERSION, "hint": hint, "signedIn": CLOUD.signed_in, "account": CLOUD.acct.get("e", ""),
            "cloud": CLOUD.status, "printers": rows, "pending": pending, "recent": recent, "outbox": outbox,
            "spools": _spool_options(snap), "log": str(LOG), "dark": orca_dark(),
            "stock": stock_view(snap) if CLOUD.signed_in else None,
            "presets": presets_view(snap.get("orca")) if (CLOUD.signed_in and snap) else None,
            "cameras": cameras_view()}


PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><style>
:root{color-scheme:light;--fg:#262e30;--bg:#fff;--card:#fff;--line:#d6d6d6;--go:#009688;--warn:#c76b00;--warnbg:#fff6e8;--track:#ececec}
:root[data-theme=dark]{color-scheme:dark;--fg:#efeff0;--bg:#2d2d31;--card:#36363c;--line:#4c4c54;--warn:#e7a23c;--warnbg:#3b3122;--track:#47474e}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){color-scheme:dark;--fg:#efeff0;--bg:#2d2d31;--card:#36363c;--line:#4c4c54;--warn:#e7a23c;--warnbg:#3b3122;--track:#47474e}}
body{font:14px/1.45 system-ui,"Segoe UI",sans-serif;margin:0;padding:16px;color:var(--fg);background:var(--bg)}
h1{font-size:20px;margin:0 0 8px}h2{font-size:15px;margin:20px 0 6px;text-transform:uppercase;letter-spacing:.04em;opacity:.75}
h3{font-size:14px;margin:16px 0 4px;display:flex;gap:8px;align-items:baseline}h3 .muted{font-weight:normal}
nav{display:flex;gap:4px;border-bottom:1px solid var(--line);margin-bottom:10px}
nav button{border:0;border-bottom:2px solid transparent;border-radius:0;padding:6px 12px;opacity:.75}
nav button[aria-current]{border-bottom-color:var(--go);opacity:1;font-weight:600}
.card{border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin:8px 0;background:var(--card)}
.muted{opacity:.7;font-size:13px}.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
input,select,textarea{font:inherit;padding:5px 6px;border:1px solid var(--line);border-radius:5px;background:var(--bg);color:inherit}
input{width:150px}input.pct{width:60px}select{max-width:100%}textarea{width:100%;box-sizing:border-box;height:60px}
input[type=checkbox]{width:auto}input[type=search]{width:200px}
button{font:inherit;padding:6px 12px;border:1px solid var(--line);border-radius:5px;background:transparent;color:inherit;cursor:pointer}
button.go{background:var(--go);border-color:var(--go);color:#fff}
.sw{display:inline-block;width:14px;height:14px;border:1px solid #0006;border-radius:3px;vertical-align:-2px}
table{border-collapse:collapse;width:100%;font-size:13px;margin-top:6px}td{border-top:1px solid var(--line);padding:4px 6px;vertical-align:middle}
.warn{border-color:var(--warn)}.alert{background:var(--warnbg);border-left:3px solid var(--warn);padding:6px 8px;margin-top:6px;border-radius:4px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:8px}
.sp{display:grid;grid-template-columns:34px minmax(0,1fr);gap:10px;align-items:center;border:1px solid var(--line);border-radius:8px;padding:8px 10px;background:var(--card)}
.sp.low{border-color:var(--warn)}
.disc{width:34px;height:34px;border-radius:50%;border:1px solid #0005;box-sizing:border-box;position:relative}
.disc::after{content:"";position:absolute;inset:11px;border-radius:50%;background:var(--card);border:1px solid #0003}
.sp b{font-variant-numeric:tabular-nums}.sp .name{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar{height:6px;border-radius:3px;background:var(--track);overflow:hidden;margin:5px 0 2px}.bar i{display:block;height:100%;border-radius:3px}
.tag{display:inline-block;font-size:11px;padding:0 6px;border-radius:9px;border:1px solid var(--line);margin-left:4px}
.tag.lowt{border-color:var(--warn);color:var(--warn)}
.list div{padding:3px 0;border-top:1px solid var(--line)}.list div:first-child{border-top:0}
.camgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:10px;margin-bottom:10px}
.cam{border:1px solid var(--line);border-radius:8px;padding:8px;background:var(--card)}
.camhead{display:flex;gap:8px;align-items:center;margin-bottom:6px}.camhead b{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.camhead select,.camhead button{font-size:12px;padding:3px 6px}
.vid{position:relative;background:#000;aspect-ratio:16/9;border-radius:6px;overflow:hidden}
.vid video{width:100%;height:100%;object-fit:contain;display:block}
.camst{position:absolute;left:0;right:0;bottom:0;padding:6px 8px;background:#000b;color:#fff;font-size:12px}
.camjob{margin-top:5px}
input.wide{width:330px}
[hidden]{display:none!important}
</style></head><body>
<h1>Spool Stock</h1>
<nav><button data-v="prints" aria-current="page">Prints<span id="nb"></span></button><button data-v="stock">Stock</button><button data-v="presets">Presets</button><button data-v="cameras">Cameras</button></nav>
<div id="hint" class="muted"></div>
<section id="v-prints">
<p class="muted">Takes each print's filament off the spools in the trays it used. <span id="ver"></span></p>
<h2>Account</h2><div id="acct"></div>
<div id="pendwrap"><h2>Needs you</h2><div id="pending"></div></div>
<h2>Printers</h2><div id="printers"></div>
<div class="card"><b>Add a printer</b><div class="row" style="margin-top:6px">
<input id="n" placeholder="Name, as in Spool Stock"><input id="ip" placeholder="IP address"><input id="code" placeholder="Access code" type="password">
<button id="add">Add</button></div><p class="muted">On the printer: Settings, then Network, for the IP address and access code. LAN mode must be on.</p></div>
<h2>Recent</h2><div id="recent" class="card muted"></div>
<p><button id="openlog">Open the log folder</button></p>
</section>
<section id="v-stock" hidden>
<div class="row"><input id="q" type="search" placeholder="Find a spool"><select id="grp"><option value="loc">By storage</option><option value="mat">By material</option><option value="brand">By brand</option><option value="none">One list</option></select>
<label class="row" style="gap:4px"><input type="checkbox" id="lowonly">Low only</label><button data-a="refresh">Refresh</button></div>
<p id="stockinfo" class="muted"></p><div id="stock"></div>
</section>
<section id="v-presets" hidden><div id="presets"></div></section>
<section id="v-cameras" hidden>
<div id="camgrid" class="camgrid"></div><p id="camnone" class="muted">No cameras yet.</p>
<div class="card"><b>Add a camera</b><div class="row" style="margin-top:6px">
<input id="cname" placeholder="Name"><input id="curl" class="wide" placeholder="rtsp://name:password@192.168.1.50/stream2" autocomplete="off" spellcheck="false"><select id="cprinter"></select><button id="cadd">Add</button></div>
<p class="muted">For a Tapo camera: in the Tapo app, open the camera's settings, then Advanced Settings, then Camera Account, and make a username and password. The link is rtsp://username:password@the camera's IP/stream2. stream2 is lighter and plenty for watching prints, stream1 is full quality. The link stays on this PC.</p>
<p class="muted">Cameras only stream while this page is open.</p></div>
</section>
<script>
var o=window.orca,last={},spools=[],view="prints",D=null;function send(m){o.postMessage(m)}
function h(s){return String(s==null?"":s).replace(/[&<>"]/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]})}
function sw(c){return '<span class="sw" style="background:'+h(c||"#0000")+'"></span>'}
function set(id,html,key){if(last[id]===key)return;last[id]=key;document.getElementById(id).innerHTML=html}
function kept(k,v){try{if(v===undefined)return localStorage.getItem(k);localStorage.setItem(k,v)}catch(e){}return null}
function show(v){view=v;kept("ss.view",v);document.querySelectorAll("nav button").forEach(function(b){if(b.dataset.v===v)b.setAttribute("aria-current","page");else b.removeAttribute("aria-current")});
 ["prints","stock","presets","cameras"].forEach(function(x){document.getElementById("v-"+x).hidden=x!==v});
 if(v!=="cameras")Object.keys(CAMS).forEach(function(k){try{CAMS[k].video.pause()}catch(e){}});if(D)paint()}
var CAMS={},camAsk=0;
function b64(x){var s=atob(x),a=new Uint8Array(s.length);for(var i=0;i<s.length;i++)a[i]=s.charCodeAt(i);return a}
function camStatus(C,t){C.st.textContent=t||"";C.st.hidden=!t}
function camReset(C,mime){C.q=[];C.sb=null;C.mime=mime;
 if(!window.MediaSource||!MediaSource.isTypeSupported(mime)){C.bad="Orca's viewer can't play this camera's video ("+mime+").";return}
 C.bad="";var ms=new MediaSource();C.ms=ms;C.video.src=URL.createObjectURL(ms);
 ms.addEventListener("sourceopen",function(){if(C.ms!==ms)return;try{C.sb=ms.addSourceBuffer(mime);C.sb.addEventListener("updateend",function(){camLive(C);camPump(C)});camPump(C)}catch(e){C.bad="Couldn't start the video: "+e.message}},{once:true})}
function camPump(C){var sb=C.sb;if(!sb||sb.updating||!C.q.length)return;try{sb.appendBuffer(C.q.shift())}catch(e){C.gen=0;C.seq=-1}}
function camLive(C){var v=C.video,br=v.buffered,sb=C.sb;if(!br.length)return;var end=br.end(br.length-1);
 if(end-v.currentTime>1.5||v.currentTime<br.start(br.length-1))v.currentTime=Math.max(br.start(br.length-1),end-0.25);
 if(v.paused&&view==="cameras")v.play().catch(function(){});
 if(sb&&!sb.updating&&v.currentTime-br.start(0)>30){try{sb.remove(br.start(0),v.currentTime-10)}catch(e){}}}
function camTiles(){var list=D.cameras||[],grid=document.getElementById("camgrid"),names=D.printers.map(function(p){return p.name}).filter(Boolean);
 document.getElementById("camnone").hidden=!!list.length;
 var opts=function(sel){return '<option value="">No printer</option>'+names.concat(sel&&names.indexOf(sel)<0?[sel]:[]).map(function(n){return '<option'+(n===sel?" selected":"")+'>'+h(n)+'</option>'}).join("")};
 set("cprinter",opts(document.getElementById("cprinter").value),JSON.stringify(names));
 Object.keys(CAMS).forEach(function(id){if(!list.some(function(c){return c.id===id})){CAMS[id].el.remove();delete CAMS[id]}});
 list.forEach(function(c){var C=CAMS[c.id];
  if(!C){var el=document.createElement("div");el.className="cam";el.dataset.id=c.id;
   el.innerHTML='<div class="camhead"><b></b><select class="campr"></select><button data-a="cam_remove">Remove</button></div><div class="vid"><video muted autoplay playsinline></video><div class="camst">Connecting</div></div><div class="muted camjob"></div>';
   grid.appendChild(el);C=CAMS[c.id]={id:c.id,el:el,video:el.querySelector("video"),st:el.querySelector(".camst"),gen:0,seq:-1,q:[],bad:""};
   el.querySelector(".campr").addEventListener("change",function(ev){send({type:"cam_edit",id:c.id,printer:ev.target.value})})}
  C.el.querySelector("b").textContent=c.name;C.el.querySelector("b").title=c.name+" ("+c.host+")";
  var key=JSON.stringify([c.printer,names]);if(C.pk!==key){C.pk=key;C.el.querySelector(".campr").innerHTML=opts(c.printer)}
  var p=D.printers.filter(function(x){return x.name===c.printer})[0],j=p&&p.job;
  C.el.querySelector(".camjob").textContent=!c.printer?"":!p?c.printer+": not added under Prints":j?c.printer+": printing "+j.name+(j.percent?", "+j.percent+"%":""):c.printer+": "+p.state})}
function camPoll(){if(view!=="cameras"||!D||!(D.cameras||[]).length)return;if(camAsk&&Date.now()-camAsk<3000)return;camAsk=Date.now();
 send({type:"cams",want:Object.keys(CAMS).map(function(k){var C=CAMS[k];return {id:k,gen:C.gen,seq:C.seq}})})}
function camGot(d){camAsk=0;(d.cams||[]).forEach(function(r){var C=CAMS[r.id];if(!C)return;
 if(r.init){C.gen=r.gen;C.seq=-1;camReset(C,r.mime);if(!C.bad)C.q.push(b64(r.init))}
 if(r.chunks&&r.gen===C.gen&&!C.bad){r.chunks.forEach(function(x){C.q.push(b64(x))});if(r.seq!=null)C.seq=r.seq;if(C.q.length>200)C.q.splice(1,C.q.length-60)}
 camPump(C);camStatus(C,C.bad||(r.status==="Live"&&C.video.buffered.length?"":r.status==="Live"?"Starting the picture":r.status))})}
function fill(s){var c=s.cols.length?s.cols:["#9AA5AB"];if(c.length<2)return c[0];
 if(s.pattern==="gradient")return "linear-gradient(135deg,"+c.join(",")+")";
 var n=c.length;return "conic-gradient("+c.map(function(x,i){return x+" "+(i*360/n)+"deg "+((i+1)*360/n)+"deg"}).join(",")+")"}
function barCol(s){return s.cols.length?(s.pattern==="solid"?s.cols[0]:"linear-gradient(90deg,"+s.cols.join(",")+")"):"#9AA5AB"}
function card(s){var pc=s.net&&s.left!=null?Math.max(0,Math.min(100,Math.round(s.left*100/s.net))):100;
 return '<div class="sp'+(s.low?" low":"")+'" title="'+h(s.code+" "+s.brand+" "+s.material+" "+s.colour)+'"><span class="disc" style="background:'+h(fill(s))+'"></span><div style="min-width:0">'
 +'<div class="name"><b>'+h(s.code)+'</b> '+h([s.brand,s.material].filter(Boolean).join(" "))+'</div>'
 +'<div class="muted name">'+h([s.colour,s.where].filter(Boolean).join(", "))+'</div>'
 +'<div class="bar"><i style="width:'+pc+'%;background:'+h(barCol(s))+'"></i></div>'
 +'<div class="muted">'+(s.left!=null?s.left+" g left"+(s.net?" of "+s.net+" g":""):"Weight not known")+(s.low?'<span class="tag lowt">Low</span>':"")+(s.printer?'<span class="tag" title="'+h(s.printer)+' feeds from it">'+h(s.printer)+'</span>':"")+'</div></div></div>'}
function paintStock(){var st=D.stock;
 if(!D.signedIn){set("stock",'<p class="muted">Sign in on the Prints page to see your stock here.</p>',"out");set("stockinfo","","");return}
 if(!st){set("stock",'<p class="muted">Loading your stock.</p>',"wait");return}
 var q=document.getElementById("q").value.trim().toLowerCase(),g=document.getElementById("grp").value,lo=document.getElementById("lowonly").checked;
 var words=q.split(/\s+/).filter(Boolean),list=st.spools.filter(function(s){if(lo&&!s.low)return false;var hay=(s.code+" "+s.brand+" "+s.material+" "+s.colour+" "+s.finish+" "+s.where+" "+s.group+" "+s.printer).toLowerCase();return words.every(function(w){return hay.indexOf(w)>=0})});
 var kg=list.reduce(function(a,s){return a+(s.left||0)},0)/1000,low=list.filter(function(s){return s.low}).length;
 set("stockinfo",list.length+" spool"+(list.length===1?"":"s")+", "+kg.toFixed(1)+" kg"+(low?", "+low+" low":"")+(st.at?". Updated "+h(st.at)+".":""),[list.length,kg,low,st.at].join());
 var groups=[],at={};list.forEach(function(s){var k=g==="loc"?(s.group?s.group+": ":"")+s.where.replace(/, space \d+$/,""):g==="mat"?(s.material||"Unknown material"):g==="brand"?(s.brand||"Unknown brand"):"";
  if(!(k in at)){at[k]=groups.length;groups.push({k:k,s:[]})}groups[at[k]].s.push(s)});
 if(g==="mat"||g==="brand")groups.sort(function(a,b){return a.k.localeCompare(b.k)});
 var html=list.length?groups.map(function(gr){var t=gr.s.reduce(function(a,s){return a+(s.left||0)},0);
  return (gr.k?'<h3>'+h(gr.k)+' <span class="muted">'+gr.s.length+(gr.s.length===1?" spool, ":" spools, ")+(t>=1000?(t/1000).toFixed(1)+" kg":Math.round(t)+" g")+'</span></h3>':"")+'<div class="grid">'+gr.s.map(card).join("")+'</div>'}).join("")
  :'<p class="muted">'+(st.spools.length?"Nothing matches.":"No spools yet. Add them in Spool Stock.")+'</p>';
 set("stock",html,JSON.stringify([st.spools,q,g,lo]))}
function paintPresets(){var p=D.presets;
 if(!D.signedIn){set("presets",'<p class="muted">Sign in on the Prints page first.</p>',"out");return}
 var intro='<p>Adds a Spool Stock group to Orca\'s filament list, with one preset for each filament you own. Ones Orca already has a profile for use it unchanged. The rest build on Orca\'s closest generic profile, with the temperatures from your stickers. The group updates by itself as your stock changes.</p>';
 if(!p){set("presets",intro+'<p class="muted">Loading your stock.</p>',"wait");return}
 if(!p.have){set("presets",intro+'<p class="muted">Open Spool Stock on your phone or PC once, so it can send your filament list here.</p>',"none");return}
 var st=p.on?(p.behind?"Updating.":p.how==="live"?"In Orca's filament list under Spool Stock. Updated "+h(p.when)+".":p.how==="restart"?"Saved. Restart Orca to see them, as this version of Orca can't add them while it's open.":"Saved, but Orca didn't load them ("+h(p.detail)+"). Restart Orca to see them."):"";
 var html=intro+(p.on?'<div class="card'+(p.how==="error"?" warn":"")+'">'+st+'<div class="row" style="margin-top:6px"><button data-a="presets_now">Update now</button><button data-a="presets_off">Remove them from Orca</button></div></div>'
  :'<div class="row"><button class="go" data-a="presets_on">Add them to Orca</button></div>')
  +(p.skipped.length?'<div class="alert">Couldn\'t add '+h(p.skipped.join(", "))+', as Orca has none of the profiles they build on.</div>':"")
  +(p.moved?'<p class="muted">Moved '+p.moved+' older cop'+(p.moved===1?"y":"ies")+' from the Windows app to the old-presets folder, next to the log, so nothing shows twice. Restart Orca once to clear '+(p.moved===1?"it":"them")+' from the list.</p>':"")
  +'<h3>Your filaments <span class="muted">'+p.feed.length+'</span></h3><div class="card list">'+(p.feed.map(function(f){return '<div>'+h(f.label)+' <span class="muted">'+(f.own?"Orca's own profile":"on a generic profile, with your temperatures")+'</span></div>'}).join("")||'<div class="muted">No spools yet.</div>')+'</div>';
 set("presets",html,JSON.stringify(p))}
function paint(){if(view==="stock")paintStock();else if(view==="presets")paintPresets();camTiles()}
o.onMessage(function(d){if(d&&d.type==="cams"){camGot(d);return}if(!d||d.type!=="state")return;D=d;spools=d.spools||[];
document.documentElement.dataset.theme=d.dark===true?"dark":d.dark===false?"light":"";
document.getElementById("ver").textContent="Version "+d.version+".";
document.getElementById("hint").textContent=d.hint||"";
document.getElementById("nb").textContent=d.pending.length?" ("+d.pending.length+")":"";
set("acct",d.signedIn?'<div class="card">'+h(d.cloud)+(d.outbox?" "+d.outbox+" waiting to send.":"")+'<div class="row" style="margin-top:6px"><button data-a="signout">Sign out</button></div></div>'
 :'<div class="card'+(d.cloud&&d.cloud!=="Not signed in"?" warn":"")+'"><p>'+h(d.cloud)+'</p><p class="muted">In Spool Stock, open Setup, then Orca plugin, and press Copy sign-in code. Paste it here.</p><textarea id="codein"></textarea><div class="row"><button class="go" data-a="signin">Sign in</button></div></div>',
 JSON.stringify([d.signedIn,d.cloud,d.outbox]));
document.getElementById("pendwrap").style.display=d.pending.length?"":"none";
set("pending",d.pending.map(function(e){
 var stop=e.result==="finished"?"":(e.layers?" at "+e.percent+"%, layer "+e.layer+" of "+e.layers:" at "+e.percent+"%");
 return '<div class="card warn" data-key="'+h(e.key)+'"><b>'+h(e.printer)+': '+h(e.name)+'</b><br><span class="muted">'+h(e.why)+(e.result!=="finished"&&e.result?h(stop)+".":"")+'</span>'
 +'<div class="row" style="margin-top:6px">How much printed <input class="pct" type="number" min="0" max="100" value="'+(e.result==="finished"?100:Math.round(e.percent||0))+'">%</div>'
 +'<table>'+e.items.map(function(it,i){var opts='<option value="">Not from a spool in Spool Stock</option>'+spools.map(function(s){return '<option value="'+h(s.id)+'"'+(s.id===it.spool?" selected":"")+'>'+h(s.label)+'</option>'}).join("");
  return '<tr><td>'+sw(it.colour)+' '+h(it.type)+'</td><td>'+h(it.trayName)+'</td><td><input class="g pct" type="number" min="0" step="0.1" value="'+it.g+'"> g in full</td><td><select data-i="'+i+'">'+opts+'</select></td></tr>'}).join("")
 +(e.items.length?"":'<tr><td class="muted">No filament details. Skip this one, or log it in Spool Stock from the sliced file.</td></tr>')+'</table>'
 +'<div class="row" style="margin-top:8px"><button class="go" data-a="confirm">Take it off</button><button data-a="skip">Skip</button></div></div>'}).join(""),
 JSON.stringify([d.pending.map(function(e){return e.key}),spools.length]));
set("printers",d.printers.map(function(p,i){var j=p.job;
 return '<div class="card"><b>'+h(p.name)+'</b> <span class="muted">'+h(p.ip)+(p.serial?" · "+h(p.serial):"")+'</span><br>'+h(p.state)
 +(p.linked?' · <span class="muted">Spool Stock printer: '+h(p.linked)+'</span>':(d.signedIn?'<br><span class="muted">Not found in Spool Stock. Add a printer there called '+h(p.name)+(p.serial?', or with serial '+h(p.serial):"")+'.</span>':""))
 +(j&&j.warnings.length?j.warnings.map(function(w){return '<div class="alert">'+h(w)+'</div>'}).join(""):"")
 +(j?(j.warnings.length?'':'<br>')+'Printing '+h(j.name)+(j.percent?" ("+j.percent+"%)":"")+(j.late?' <span class="muted">(joined part way, so the trays come from what it fed)</span>':"")
   +(j.trays.length?'<table>'+j.trays.map(function(t){return '<tr><td>'+sw(t.colour)+' '+h(t.type)+'</td><td>'+t.g+' g</td><td>'+h(t.tray)+'</td><td>'+(t.spool?h(t.spool):'<span class="muted">no spool matched</span>')+'</td></tr>'}).join("")+'</table>':'<br><span class="muted">Grams come from the job file once printing starts.</span>'):"")
 +'<div class="row" style="margin-top:6px"><button data-a="connect">Connect</button><button data-a="remove" data-i="'+i+'">Remove</button></div></div>'}).join("")||'<p class="muted">None yet.</p>',JSON.stringify(d.printers));
set("recent",d.recent.map(function(r){return h(r.when)+": "+h(r.text)}).join("<br>")||"Nothing yet.",JSON.stringify(d.recent));
paint();
});
document.querySelector("nav").addEventListener("click",function(ev){var b=ev.target.closest("[data-v]");if(b)show(b.dataset.v)});
["q","grp","lowonly"].forEach(function(id){var el=document.getElementById(id);el.addEventListener(el.type==="checkbox"||el.tagName==="SELECT"?"change":"input",function(){kept("ss."+id,el.type==="checkbox"?(el.checked?"1":""):el.value);if(D)paintStock()})});
(function(){var g=kept("ss.grp");if(g)document.getElementById("grp").value=g;document.getElementById("lowonly").checked=kept("ss.lowonly")==="1";var v=kept("ss.view");if(v==="stock"||v==="presets"||v==="cameras")show(v)})();
document.getElementById("cadd").onclick=function(){var u=document.getElementById("curl");send({type:"cam_add",name:document.getElementById("cname").value.trim(),url:u.value.trim(),printer:document.getElementById("cprinter").value});u.value="";document.getElementById("cname").value=""};
setInterval(camPoll,300);
document.body.addEventListener("click",function(ev){var b=ev.target.closest("[data-a]");if(!b)return;var a=b.dataset.a;
 if(a==="signin")send({type:"signin",code:document.getElementById("codein").value});
 else if(a==="signout")send({type:"signout"});
 else if(a==="connect")send({type:"connect"});
 else if(a==="remove")send({type:"remove",index:+b.dataset.i});
 else if(a==="cam_remove"){if(b.dataset.sure){send({type:"cam_remove",id:b.closest(".cam").dataset.id})}else{b.dataset.sure="1";b.textContent="Remove it";setTimeout(function(){delete b.dataset.sure;b.textContent="Remove"},4000)}}
 else if(a==="refresh"||a==="presets_on"||a==="presets_now"||a==="presets_off"){if(a!=="refresh")last.presets=null;send({type:a})}
 else if(a==="confirm"||a==="skip"){var c=b.closest("[data-key]"),items=[];
  c.querySelectorAll("tr").forEach(function(tr){var s=tr.querySelector("select"),g=tr.querySelector("input.g");if(s)items.push({spool:s.value,g:parseFloat(g.value)||0})});
  send({type:a,key:c.dataset.key,pct:parseFloat(c.querySelector("input.pct").value)||0,items:items});last.pending=null}});
document.getElementById("add").onclick=function(){send({type:"add",name:document.getElementById("n").value.trim(),ip:document.getElementById("ip").value.trim(),code:document.getElementById("code").value.trim()});document.getElementById("code").value=""};
document.getElementById("openlog").onclick=function(){send({type:"openlog"})};
send({type:"ready"});setInterval(function(){send({type:"poll"})},2000);
</script></body></html>"""


_preset_try = {"at": 0.0}


def _fresh_snapshot() -> str:
    """Reload stock straight away, from the tab. Returns a hint if it couldn't."""
    if not CLOUD.signed_in:
        return "Sign in first."
    try:
        CLOUD.snapshot(max_age=0)
        return ""
    except PermissionError:
        CLOUD.ui_mode = True
        return "Orca blocked the connection to Spool Stock. Press Connect on a printer and choose Yes."
    except Exception as e:
        log("cloud.error", error=f"{type(e).__name__}: {e}")
        return f"Couldn't reach Spool Stock: {e}"


def _presets(force: bool = False) -> str:
    """Bring Orca's Spool Stock group up to date when it's on and the app's list has changed."""
    snap = CLOUD.snap
    feed = snap.get("orca") if snap else None
    st = _preset_state()
    if not (force or (st.get("on") and feed is not None and feed.get("sig") != st.get("sig"))):
        return ""
    if not force and time.time() - _preset_try["at"] < 60:
        return ""
    _preset_try["at"] = time.time()
    if feed is None:
        return "Open Spool Stock on your phone or PC once, so it can send your filament list here."
    try:
        r = sync_presets(feed)
    except Exception as e:
        log("presets.error", trace=traceback.format_exc())
        return f"Couldn't write the presets: {type(e).__name__}: {e}"
    return {"live": "", "restart": "Saved. Restart Orca to see them.", "error": "Saved, but Orca didn't load them. Restart Orca to see them."}[r["how"]] if force else ""


def handle(d: dict) -> dict:
    """One message from the tab. Runs on Orca's main thread."""
    global _started
    t = (d or {}).get("type")
    hint = ""
    if t == "cams":
        return watch_cameras(d.get("want") or [])
    _flush_notifications()
    if t == "ready":
        if not _started:
            _started = True
            log("plugin.start", version=VERSION, python=sys.version)
            threading.Thread(target=worker, daemon=True, name="spoolstock-worker").start()
        hint = " ".join(connect_all())
        _wake.set()
    elif t == "connect":
        hint = " ".join(connect_all()) or "Connecting."
    elif t == "poll":
        if CLOUD.ui_mode:
            try:
                tick()
                for job in list(STORE.active.values()):
                    if job.get("fils") and not job.get("checked_low"):
                        check_low(job)
            except PermissionError:
                hint = "Orca is blocking Spool Stock. Press Connect on a printer and choose Yes."
        hint = _presets() or hint
    elif t == "refresh":
        hint = _fresh_snapshot()
    elif t in ("presets_on", "presets_now"):
        hint = _fresh_snapshot() or _presets(force=True)
    elif t == "cam_add":
        u = parse_rtsp(d.get("url") or "")
        if not u:
            hint = "That isn't an RTSP link. It should look like rtsp://name:password@192.168.1.50/stream2"
        else:
            items = load_cameras()
            cid = "c" + os.urandom(3).hex()
            items.append({"id": cid, "name": (d.get("name") or "").strip() or u["host"], "url": d["url"].strip(), "printer": d.get("printer") or ""})
            _secrets.update({u["password"], d["url"].strip()})
            save_cameras(items)
            log("camera.added", name=items[-1]["name"], host=u["host"])
    elif t == "cam_edit":
        items = load_cameras()
        for c in items:
            if c["id"] == d.get("id"):
                if "printer" in d:
                    c["printer"] = d.get("printer") or ""
                if d.get("name"):
                    c["name"] = d["name"].strip()
        save_cameras(items)
    elif t == "cam_remove":
        items = [c for c in load_cameras() if c.get("id") != d.get("id")]
        save_cameras(items)
        cam = _cams.pop(d.get("id"), None)
        if cam:
            cam.stop.set()
    elif t == "presets_off":
        try:
            remove_presets()
        except Exception as e:
            log("presets.error", trace=traceback.format_exc())
            hint = f"Couldn't remove them: {e}"
    elif t == "signin":
        try:
            CLOUD.sign_in(d.get("code", ""))
            hint = " ".join(connect_all())
            CLOUD.snapshot(max_age=0) if CLOUD.ui_mode else _wake.set()
            log("account.signed_in", email=CLOUD.acct.get("e"))
        except PermissionError:
            CLOUD.ui_mode = True
            hint = "Orca blocked the connection to Spool Stock. Press Connect and choose Yes."
        except Exception as e:
            hint = str(e) if isinstance(e, ValueError) else f"Couldn't sign in: {e}"
    elif t == "signout":
        CLOUD.sign_out()
        log("account.signed_out")
    elif t == "add":
        if not (d.get("ip") and d.get("code")):
            hint = "Needs the IP address and access code."
        else:
            items = load_printers()
            items.append({"name": d.get("name") or d["ip"], "ip": d["ip"], "serial": "", "code": d["code"]})
            _secrets.add(d["code"])
            save_printers(items)
            log("printer.added", name=d.get("name"), ip=d["ip"])
            hint = " ".join(connect_all()) or "Added. Connecting."
    elif t == "remove":
        items = load_printers()
        i = int(d.get("index", -1))
        if 0 <= i < len(items):
            gone = items.pop(i)
            save_printers(items)
            link = _links.pop(gone.get("name") or gone.get("ip"), None)
            if link:
                link.stop.set()
    elif t in ("confirm", "skip"):
        with STORE.lock:
            entry = next((e for e in STORE.pending if e["key"] == d.get("key")), None)
            if entry:
                STORE.pending.remove(entry)
                if t == "skip":
                    STORE.note(f"{entry['printer']}: {entry['name']}, skipped", entry["key"])
                else:
                    sent = d.get("items") or []
                    items = []
                    for i, it in enumerate(entry["items"]):
                        got = sent[i] if i < len(sent) else {}
                        if got.get("spool"):
                            items.append({**it, "spool": got["spool"], "g": max(0.0, float(got.get("g") or 0))})
                    entry["items"], entry["pct"] = items, max(0, min(100, round(float(d.get("pct") or 0))))
                    if entry["pct"] == 0 or not items:
                        STORE.note(f"{entry['printer']}: {entry['name']}, nothing taken off", entry["key"])
                    else:
                        STORE.outbox.append(entry)
                STORE.save()
                log("pending." + t, job=entry["name"], pct=d.get("pct"))
        if CLOUD.ui_mode:
            try:
                tick()
            except PermissionError:
                pass
        else:
            _wake.set()
    elif t == "openlog":
        try:
            os.startfile(str(DATA))  # type: ignore[attr-defined]
        except Exception:
            hint = f"Log folder: {DATA}"
    return _state(hint)


Pages = getattr(getattr(orca, "pages", None), "PagesPluginCapabilityBase", None)

if Pages is not None:
    class SpoolStockPage(Pages):
        def get_name(self):
            return NAME

        def get_type(self):
            return orca.PluginType.Pages

        def get_ui(self):
            return PAGE

        def on_message(self, arg0):
            try:
                d = json.loads(arg0) if isinstance(arg0, str) else arg0
                self.post_message(handle(d or {}))
            except Exception:
                log("page.error", trace=traceback.format_exc())

        def on_lifecycle_event(self, event, ctx):
            pass

        def on_unload(self):
            disconnect_all()
            log("plugin.unload")


@orca.plugin
class SpoolStock(orca.base):
    def register_capabilities(self):
        req = getattr(orca, "request_permissions", None)
        if callable(req):
            try:
                req(threading=True)
            except Exception as e:
                log("permissions.request_failed", error=f"{type(e).__name__}: {e}")
        if Pages is not None:
            orca.register_capability(SpoolStockPage)
