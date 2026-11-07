# /// script
# requires-python = ">=3.12"
# dependencies = []
#
# [tool.orcaslicer.plugin]
# id = "spool-stock"
# name = "Spool Stock"
# description = "Takes each print's filament off the right spools in Spool Stock, from the AMS trays and external spool your Bambu printers used."
# author = "jwilss"
# version = "1.1.0"
# ///
"""Spool Stock for OrcaSlicer 2.5.

Watches each Bambu printer on the local network. When a job starts, the printer says which AMS tray or
the external spool each filament comes from. The plugin reads the job file on the printer for the grams
per filament, and when the print ends takes them off the spools that Spool Stock has in those trays.

A finished print is taken off straight away, with Undo in the app. A failed or cancelled print waits in
the Spool Stock tab with how far it got, for you to confirm.

The only thing sent to a printer is Bambu's standard request for its status. Printer IPs and access
codes stay in this plugin's folder on this PC and are never sent to Spool Stock or written to the log.
"""

import base64
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
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import orca

NAME = "Spool Stock"
VERSION = "1.1.0"
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
            snap = {"printers": self.docs("printers"), "locs": self.docs("locations"), "spools": self.docs("spools")}
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
            "spools": _spool_options(snap), "log": str(LOG), "dark": orca_dark()}


PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><style>
:root{color-scheme:light;--fg:#262e30;--bg:#fff;--card:#fff;--line:#d6d6d6;--go:#009688;--warn:#c76b00;--warnbg:#fff6e8}
:root[data-theme=dark]{color-scheme:dark;--fg:#efeff0;--bg:#2d2d31;--card:#36363c;--line:#4c4c54;--warn:#e7a23c;--warnbg:#3b3122}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){color-scheme:dark;--fg:#efeff0;--bg:#2d2d31;--card:#36363c;--line:#4c4c54;--warn:#e7a23c;--warnbg:#3b3122}}
body{font:14px/1.45 system-ui,"Segoe UI",sans-serif;margin:0;padding:16px;color:var(--fg);background:var(--bg)}
h1{font-size:20px;margin:0 0 2px}h2{font-size:15px;margin:20px 0 6px;text-transform:uppercase;letter-spacing:.04em;opacity:.75}
.card{border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin:8px 0;background:var(--card)}
.muted{opacity:.7;font-size:13px}.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
input,select,textarea{font:inherit;padding:5px 6px;border:1px solid var(--line);border-radius:5px;background:var(--bg);color:inherit}
input{width:150px}input.pct{width:60px}select{max-width:100%}textarea{width:100%;box-sizing:border-box;height:60px}
button{font:inherit;padding:6px 12px;border:1px solid var(--line);border-radius:5px;background:transparent;color:inherit;cursor:pointer}
button.go{background:var(--go);border-color:var(--go);color:#fff}
.sw{display:inline-block;width:14px;height:14px;border:1px solid #0006;border-radius:3px;vertical-align:-2px}
table{border-collapse:collapse;width:100%;font-size:13px;margin-top:6px}td{border-top:1px solid var(--line);padding:4px 6px;vertical-align:middle}
.warn{border-color:var(--warn)}.alert{background:var(--warnbg);border-left:3px solid var(--warn);padding:6px 8px;margin-top:6px;border-radius:4px}
</style></head><body>
<h1>Spool Stock</h1>
<p class="muted">Takes each print's filament off the spools in the trays it used. <span id="ver"></span></p>
<div id="hint" class="muted"></div>
<h2>Account</h2><div id="acct"></div>
<div id="pendwrap"><h2>Needs you</h2><div id="pending"></div></div>
<h2>Printers</h2><div id="printers"></div>
<div class="card"><b>Add a printer</b><div class="row" style="margin-top:6px">
<input id="n" placeholder="Name, as in Spool Stock"><input id="ip" placeholder="IP address"><input id="code" placeholder="Access code" type="password">
<button id="add">Add</button></div><p class="muted">On the printer: Settings, then Network, for the IP address and access code. LAN mode must be on.</p></div>
<h2>Recent</h2><div id="recent" class="card muted"></div>
<p><button id="openlog">Open the log folder</button></p>
<script>
var o=window.orca,last={},spools=[];function send(m){o.postMessage(m)}
function h(s){return String(s==null?"":s).replace(/[&<>"]/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]})}
function sw(c){return '<span class="sw" style="background:'+h(c||"#0000")+'"></span>'}
function set(id,html,key){if(last[id]===key)return;last[id]=key;document.getElementById(id).innerHTML=html}
o.onMessage(function(d){if(!d||d.type!=="state")return;spools=d.spools||[];
document.documentElement.dataset.theme=d.dark===true?"dark":d.dark===false?"light":"";
document.getElementById("ver").textContent="Version "+d.version+".";
document.getElementById("hint").textContent=d.hint||"";
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
});
document.body.addEventListener("click",function(ev){var b=ev.target.closest("[data-a]");if(!b)return;var a=b.dataset.a;
 if(a==="signin")send({type:"signin",code:document.getElementById("codein").value});
 else if(a==="signout")send({type:"signout"});
 else if(a==="connect")send({type:"connect"});
 else if(a==="remove")send({type:"remove",index:+b.dataset.i});
 else if(a==="confirm"||a==="skip"){var c=b.closest("[data-key]"),items=[];
  c.querySelectorAll("tr").forEach(function(tr){var s=tr.querySelector("select"),g=tr.querySelector("input.g");if(s)items.push({spool:s.value,g:parseFloat(g.value)||0})});
  send({type:a,key:c.dataset.key,pct:parseFloat(c.querySelector("input.pct").value)||0,items:items});last.pending=null}});
document.getElementById("add").onclick=function(){send({type:"add",name:document.getElementById("n").value.trim(),ip:document.getElementById("ip").value.trim(),code:document.getElementById("code").value.trim()});document.getElementById("code").value=""};
document.getElementById("openlog").onclick=function(){send({type:"openlog"})};
send({type:"ready"});setInterval(function(){send({type:"poll"})},2000);
</script></body></html>"""


def handle(d: dict) -> dict:
    """One message from the tab. Runs on Orca's main thread."""
    global _started
    t = (d or {}).get("type")
    hint = ""
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
