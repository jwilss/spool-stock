# Runs the Spool Stock Orca plugin against a stand-in Orca, a fake Bambu printer (status and job file)
# and a fake Spool Stock database. Run: python3 test/plugin.py
import base64, copy, http.server, importlib.util, io, json, os, queue, re, socket, ssl, struct, subprocess, sys, tempfile, threading, time, types, zipfile
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ok_all = True
def ok(c, m):
    global ok_all
    print(("PASS " if c else "FAIL ") + m)
    ok_all &= bool(c)

def wait(cond, secs=15):
    end = time.time() + secs
    while time.time() < end:
        if cond(): return True
        time.sleep(0.1)
    return False

tmp = Path(tempfile.mkdtemp())
os.environ["APPDATA"] = str(tmp)
(tmp / "OrcaSlicer").mkdir()
(tmp / "OrcaSlicer" / "OrcaSlicer.conf").write_text(json.dumps({"app": {"dark_color_mode": "0"}}) + "\n# MD5 checksum X\n")

# ---- stand-in orca ----
orca = types.ModuleType("orca")
class Base: pass
registered = []
orca.base = Base
orca.plugin = lambda cls: (cls().register_capabilities(), cls)[1]
orca.register_capability = registered.append
orca.request_permissions = lambda **k: None
orca.PluginType = types.SimpleNamespace(Pages="Pages")
class PagesBase:
    def __init__(self): self.sent = []
    def post_message(self, m): self.sent.append(m)
orca.pages = types.SimpleNamespace(PagesPluginCapabilityBase=PagesBase)
pushed = []
def push_notification(text, level): pushed.append((text, level))
class FakePreset:
    def __init__(self, name, file="", user=False): self.name, self.file, self._user = name, file, user
    def is_user(self): return self._user
class FakeColl:
    def __init__(self, presets): self.presets = presets
    def size(self): return len(self.presets)
    def preset(self, i): return self.presets[i]
    def find_preset(self, name): return next((p for p in self.presets if p.name == name), None)
SYSTEM = ["Generic PLA @System", "Generic PETG @System", "Generic PETG HF @System", "Bambu ASA @BBL X1C"]
FIL = FakeColl([FakePreset(n) for n in SYSTEM] + [FakePreset("My PLA", str(tmp / "OrcaSlicer" / "user" / "4242" / "filament" / "My PLA.json"), True)])
fake_bundle = types.SimpleNamespace(filaments=FIL, prints=FakeColl([]), printers=FakeColl([]))
reloads = []
def reload_local(bundle_id):
    if not (tmp / "OrcaSlicer" / "user" / "4242" / "_local" / bundle_id / "bundle_metadata.json").exists(): raise RuntimeError("Local bundle metadata is missing")
    reloads.append(bundle_id)
bundles_api = types.SimpleNamespace(reload_local=reload_local, list_ids=lambda: ["spool-stock"])
orca.host = types.SimpleNamespace(ui=types.SimpleNamespace(WarningNotificationLevel="warning", push_notification=push_notification),
                                  preset_bundle=lambda: fake_bundle, preset=types.SimpleNamespace(bundles=bundles_api))
sys.modules["orca"] = orca

# ---- TLS for the fake printer ----
SERIAL = "01P00C512345678"
cert, key = tmp / "c.pem", tmp / "k.pem"
subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert), "-days", "1", "-subj", "/CN=" + SERIAL], check=True, capture_output=True)
sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); sctx.load_cert_chain(cert, key)

def mstr(s): b = s.encode(); return struct.pack("!H", len(b)) + b
def mlen(n):
    out = bytearray()
    while True:
        d, n = n % 128, n // 128; out.append(d | (128 if n else 0))
        if not n: return bytes(out)
def read(conn):
    first = conn.recv(1)
    if not first: return None
    size, shift = 0, 0
    while True:
        b = conn.recv(1)[0]; size |= (b & 127) << shift; shift += 7
        if not b & 128: break
    body = b""
    while len(body) < size: body += conn.recv(size - len(body))
    return first[0], body

# ---- fake printer: MQTT ----
to_printer = queue.Queue()
seen = {}
msrv = socket.socket(); msrv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); msrv.bind(("127.0.0.1", 0)); msrv.listen(5)
current = [None]
def mqtt_conn(raw):
    try: conn = sctx.wrap_socket(raw, server_side=True)
    except Exception: return
    me = object()
    t, body = read(conn)
    i = 10 + struct.unpack("!H", body[10:12])[0] + 2
    ul = struct.unpack("!H", body[i:i+2])[0]; user = body[i+2:i+2+ul].decode(); i += 2 + ul
    seen["login"] = (user, body[i+2:i+2+struct.unpack("!H", body[i:i+2])[0]].decode())
    conn.sendall(b"\x20\x02\x00\x00")
    t, body = read(conn); seen["subscribe"] = body[4:4+struct.unpack("!H", body[2:4])[0]].decode()
    conn.settimeout(0.1); current[0] = me
    while current[0] is me:
        try:
            pk = read(conn)
            if pk and pk[0] >> 4 == 3: seen["pushall"] = True
        except socket.timeout: pass
        except Exception: return
        try: doc = to_printer.get_nowait()
        except queue.Empty: continue
        body = mstr(f"device/{SERIAL}/report") + json.dumps(doc).encode()
        conn.sendall(bytes([0x30]) + mlen(len(body)) + body)
def mqtt_server():
    while True:
        raw, _ = msrv.accept(); threading.Thread(target=mqtt_conn, args=(raw,), daemon=True).start()
threading.Thread(target=mqtt_server, daemon=True).start()

# ---- fake printer: implicit FTPS with the job file ----
def job_zip(fils):
    zb = io.BytesIO()
    with zipfile.ZipFile(zb, "w") as z:
        z.writestr("Metadata/slice_info.config", "<config><plate><metadata key=\"index\" value=\"1\"/>" + "".join(
            f'<filament id="{i}" type="{t}" color="{c}" used_m="1" used_g="{g}"/>' for i, t, c, g in fils) + "</plate></config>")
    return zb.getvalue()
JOB = {"zip": job_zip([(1, "PETG", "#2850E0", "10.10"), (2, "PETG", "#0ACC38", "5.60")]), "name": "Bracket.gcode.3mf"}
fsrv = socket.socket(); fsrv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); fsrv.bind(("127.0.0.1", 0)); fsrv.listen(5)
ftp_log = []
def ftp_conn(raw):
    try: c = sctx.wrap_socket(raw, server_side=True)
    except Exception: return
    f = c.makefile("rb"); c.sendall(b"220 Bambu\r\n"); data_l = None
    def data_send(payload):
        d, _ = data_l.accept(); d = sctx.wrap_socket(d, server_side=True); d.sendall(payload)
        try: d.unwrap()
        except Exception: pass
        d.close()
    while True:
        line = f.readline().decode().strip()
        if not line: break
        cmd, _, arg = line.partition(" "); cmd = cmd.upper(); ftp_log.append(cmd)
        if cmd == "USER": c.sendall(b"331 ok\r\n")
        elif cmd == "PASS": c.sendall(b"230 ok\r\n" if arg == "SECRET99" else b"530 no\r\n")
        elif cmd in ("PBSZ", "PROT", "TYPE"): c.sendall(b"200 ok\r\n")
        elif cmd == "PASV":
            data_l = socket.socket(); data_l.bind(("127.0.0.1", 0)); data_l.listen(1); dp = data_l.getsockname()[1]
            c.sendall(f"227 Entering Passive Mode (127,0,0,1,{dp >> 8},{dp & 255})\r\n".encode())
        elif cmd == "NLST":
            c.sendall(b"150 here\r\n"); data_send((JOB["name"] + "\r\ncache\r\n").encode() if arg == "/" else b""); c.sendall(b"226 done\r\n")
        elif cmd == "RETR": c.sendall(b"150 here\r\n"); data_send(JOB["zip"]); c.sendall(b"226 done\r\n")
        elif cmd == "QUIT": c.sendall(b"221 bye\r\n"); break
        else: c.sendall(b"502 no\r\n")
    c.close()
def ftp_server():
    while True:
        raw, _ = fsrv.accept(); threading.Thread(target=ftp_conn, args=(raw,), daemon=True).start()
threading.Thread(target=ftp_server, daemon=True).start()

# ---- fake Spool Stock: Firebase token and Firestore REST ----
UID, PROJECT = "user1", "spool-stock"
ROOT = f"projects/{PROJECT}/databases/(default)/documents"
def enc(v):
    if isinstance(v, bool): return {"booleanValue": v}
    if isinstance(v, int): return {"integerValue": str(v)}
    if isinstance(v, float): return {"doubleValue": v}
    if isinstance(v, str): return {"stringValue": v}
    if isinstance(v, list): return {"arrayValue": {"values": [enc(x) for x in v]}}
    if isinstance(v, dict): return {"mapValue": {"fields": {k: enc(x) for k, x in v.items()}}}
    return {"nullValue": None}
def dec(v):
    if "integerValue" in v: return int(v["integerValue"])
    if "doubleValue" in v: return float(v["doubleValue"])
    if "stringValue" in v: return v["stringValue"]
    if "booleanValue" in v: return v["booleanValue"]
    if "arrayValue" in v: return [dec(x) for x in v["arrayValue"].get("values", [])]
    if "mapValue" in v: return {k: dec(x) for k, x in v["mapValue"].get("fields", {}).items()}
    return None
DB, VER = {}, [0]
def put(path, data):
    VER[0] += 1; DB[path] = ({k: enc(v) for k, v in data.items()}, f"2026-11-07T20:00:{VER[0]:02d}.000000Z")
def doc_of(path): return {k: dec(v) for k, v in DB[path][0].items()}
put(f"users/{UID}/printers/p1", {"name": "Betsy", "serial": SERIAL, "feeds": [{"kind": "ams", "unit": 1, "loc": "ams-1"}, {"kind": "external", "loc": "rack", "space": 1}]})
put(f"users/{UID}/printers/p2", {"name": "Alduin", "serial": "01P00C999999999", "feeds": [{"kind": "external", "loc": "space-pi", "space": 1}]})
put(f"users/{UID}/locations/ams-1", {"name": "AMS 1", "spaces": True, "capacity": 4})
put(f"users/{UID}/locations/rack", {"name": "Rack", "spaces": False, "capacity": 0})
put(f"users/{UID}/locations/space-pi", {"name": "Space Pi", "spaces": True, "capacity": 2})
put(f"users/{UID}/spools/s0001", {"loc": "ams-1", "slot": 1, "remainG": 800, "brand": "Sunlu", "material": "PETG", "colour": "Blue", "prints": []})
put(f"users/{UID}/spools/s0002", {"loc": "ams-1", "slot": 2, "netG": 1000, "brand": "Sunlu", "material": "PETG", "colour": "Green"})
put(f"users/{UID}/spools/s0003", {"loc": "ams-1", "slot": 3, "remainG": 50, "status": "used", "material": "PLA"})
put(f"users/{UID}/spools/s0004", {"loc": "rack", "pos": 2, "remainG": 300, "material": "PLA"})
put(f"users/{UID}/spools/s0005", {"loc": "rack", "pos": 1, "remainG": 400, "material": "PLA"})
put(f"users/{UID}/spools/s0006", {"loc": "space-pi", "slot": 1, "remainG": 900, "brand": "Bambu", "material": "ASA", "colour": "Green"})
REFRESH = "REFRESHTOKEN123"
calls = []
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def reply(self, code, obj):
        b = json.dumps(obj).encode(); self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def authed(self):
        if self.headers.get("Authorization") != "Bearer IDTOKEN1":
            self.reply(401, {"error": {"code": 401, "status": "UNAUTHENTICATED", "message": "no"}}); return False
        return True
    def do_POST(self):
        u = urlparse(self.path); body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        calls.append(("POST", u.path, threading.current_thread().name))
        if u.path == "/token":
            form = parse_qs(body.decode())
            if parse_qs(u.query).get("key") != ["APIKEY"] or form.get("refresh_token") != [REFRESH]:
                return self.reply(400, {"error": {"code": 400, "message": "INVALID_REFRESH_TOKEN", "status": "INVALID_ARGUMENT"}})
            return self.reply(200, {"id_token": "IDTOKEN1", "refresh_token": REFRESH, "expires_in": "3600", "user_id": UID})
        if not self.authed(): return
        if u.path == f"/v1/{ROOT}:commit":
            writes = json.loads(body)["writes"]
            for w in writes:
                path = w["update"]["name"].split(ROOT + "/", 1)[1]; pre = w.get("currentDocument", {})
                if pre.get("exists") is False and path in DB:
                    return self.reply(409, {"error": {"code": 409, "status": "ALREADY_EXISTS", "message": "exists"}})
                if "updateTime" in pre and (path not in DB or DB[path][1] != pre["updateTime"]):
                    return self.reply(400, {"error": {"code": 400, "status": "FAILED_PRECONDITION", "message": "changed"}})
            for w in writes:
                path = w["update"]["name"].split(ROOT + "/", 1)[1]
                fields = dict(DB[path][0]) if path in DB and "updateMask" in w else {}
                fields.update(w["update"]["fields"]); VER[0] += 1; DB[path] = (fields, f"2026-11-07T20:01:{VER[0]:02d}.000000Z")
            return self.reply(200, {"writeResults": [{} for _ in writes]})
        self.reply(404, {"error": {"code": 404, "status": "NOT_FOUND", "message": "?"}})
    def do_GET(self):
        u = urlparse(self.path); calls.append(("GET", u.path, threading.current_thread().name))
        if not self.authed(): return
        path = u.path.split(f"/v1/{ROOT}/", 1)[1]
        if path in DB:
            return self.reply(200, {"name": f"{ROOT}/{path}", "fields": DB[path][0], "updateTime": DB[path][1]})
        docs = [{"name": f"{ROOT}/{p}", "fields": f, "updateTime": t} for p, (f, t) in sorted(DB.items()) if p.rsplit("/", 1)[0] == path]
        if docs or path.count("/") == 2: return self.reply(200, {"documents": docs} if docs else {})
        self.reply(404, {"error": {"code": 404, "status": "NOT_FOUND", "message": "missing"}})
hs = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=hs.serve_forever, daemon=True).start()
HTTP = f"http://127.0.0.1:{hs.server_address[1]}"

# ---- load the plugin ----
spec = importlib.util.spec_from_file_location("spool_stock", Path(__file__).resolve().parent.parent / "orca-plugin" / "spoolstock" / "spool_stock.py")
plug = importlib.util.module_from_spec(spec); spec.loader.exec_module(plug)
plug.MQTT_PORT, plug.FTP_PORT = msrv.getsockname()[1], fsrv.getsockname()[1]
plug.TOKEN_URL, plug.FIRESTORE_URL, plug.CLOUD_HOSTS = HTTP + "/token", HTTP + "/v1", ()

ok(len(registered) == 1, "registers the Spool Stock tab")
page = registered[0]()
html = page.get_ui()
ok("Spool Stock" in html and "window.orca" in html, "tab page builds")
js = re.search(r"<script>(.*)</script>", html, re.S).group(1)
(tmp / "page.js").write_text(js)
ok(subprocess.run(["node", "--check", str(tmp / "page.js")], capture_output=True).returncode == 0, "tab script is valid JavaScript")

# ---- unit checks: trays to spools ----
job = {"use_ams": True, "mapping": [0, 1], "mapping2": [{"ams_id": 0, "slot_id": 0}, {"ams_id": 0, "slot_id": 1}]}
ok(plug.tray_of(job, 1, 0) == (0, 0) and plug.tray_of(job, 2, 1) == (0, 1), "two filaments to AMS 1 trays 1 and 2")
ok(plug.tray_of({"use_ams": False, "mapping": [-1], "mapping2": [{"ams_id": 255, "slot_id": 0}]}, 1, 0) == ("external", None), "external spool job")
ok(plug.tray_of({"use_ams": True, "mapping": [5]}, 1, 0) == (1, 1), "old style mapping: 5 is AMS 2 tray 2")
ok(plug.tray_of({"use_ams": True, "mapping": [-1, 2]}, 1, 0) is None, "an unmapped filament is unknown, not guessed")
ok(plug.tray_of({"mapping": None, "trays_seen": ["1", "1"], "fil_count": 1}, 1, 0) == (0, 1), "joined part way: one filament, one tray fed")
ok(plug.tray_of({"mapping": None, "trays_seen": ["0", "1"], "fil_count": 2}, 1, 0) is None, "joined part way with two trays: left for you")
snap = {"printers": {k.rsplit("/", 1)[1]: {**doc_of(k), "id": k.rsplit("/", 1)[1]} for k in DB if "/printers/" in k},
        "locs": {k.rsplit("/", 1)[1]: {**doc_of(k), "id": k.rsplit("/", 1)[1]} for k in DB if "/locations/" in k},
        "spools": {k.rsplit("/", 1)[1]: {**doc_of(k), "id": k.rsplit("/", 1)[1]} for k in DB if "/spools/" in k}}
betsy = plug.find_printer(snap, SERIAL, "")
ok(betsy and betsy["name"] == "Betsy" and plug.find_printer(snap, "", "alduin")["name"] == "Alduin", "printer found by serial, or by name")
ok(plug.tray_to_spool(snap, betsy, 0, 1)["id"] == "s0002", "AMS 1 tray 2 is the spool in space 2")
ok(plug.tray_to_spool(snap, betsy, 0, 2) is None, "a used-up spool isn't matched")
ok(plug.tray_to_spool(snap, betsy, "external", None)["id"] == "s0005", "external spool from a loose storage is its first in order")
ok(plug.tray_to_spool(snap, plug.find_printer(snap, "", "Alduin"), "external", None)["id"] == "s0006", "external spool from a space")

# ---- start, add the printer, sign in ----
st = plug.handle({"type": "ready"})
ok(st["printers"] == [] and not st["signedIn"], "starts with no printers and signed out")
st = plug.handle({"type": "add", "name": "Betsy", "ip": "127.0.0.1", "code": "SECRET99"})
ok(wait(lambda: seen.get("subscribe") == f"device/{SERIAL}/report"), "connects, reads the serial from the certificate and subscribes to that printer")
ok(seen.get("login") == ("bblp", "SECRET99") and wait(lambda: seen.get("pushall")), "logs in with the access code and asks for status")
st = plug.handle({"type": "signin", "code": "nonsense"})
ok("isn't a Spool Stock sign-in code" in st["hint"], "a wrong code is explained")
code = "SS1." + base64.b64encode(json.dumps({"k": "APIKEY", "p": PROJECT, "u": UID, "e": "jack@example.com", "t": REFRESH}).encode()).decode()
plug.handle({"type": "signin", "code": code})
ok(wait(lambda: plug.CLOUD.snap is not None), "signs in and loads printers, storages and spools")
st = plug.handle({"type": "poll"})
ok(st["signedIn"] and st["printers"][0]["linked"] == "Betsy" and "jack@example.com" in st["cloud"], "printer linked to Spool Stock's Betsy")

# ---- a two colour print that finishes ----
def status(**p): return {"print": {"command": "push_status", **p}}
to_printer.put(status(gcode_state="FINISH", mc_percent=100, subtask_name="Old", ams={"tray_now": "255"}))
time.sleep(0.4)
to_printer.put({"print": {"command": "project_file", "subtask_name": "Bracket", "file": "Bracket.gcode.3mf", "param": "Metadata/plate_1.gcode",
                          "ams_mapping": [0, 1], "ams_mapping2": [{"ams_id": 0, "slot_id": 0}, {"ams_id": 0, "slot_id": 1}], "use_ams": True, "result": "success"}})
to_printer.put(status(gcode_state="FINISH", mc_percent=100))   # stale state straight after the job is sent
ok(wait(lambda: plug.STORE.active.get(SERIAL, {}).get("name") == "Bracket"), "job recorded with its tray mapping when the printer accepts it")
time.sleep(0.4)
ok(plug.STORE.active[SERIAL]["state"] == "sent", "the previous print's FINISH doesn't end the new job")
for d in (status(gcode_state="IDLE"), status(gcode_state="PREPARE"), status(gcode_state="RUNNING", mc_percent=0, layer_num=0, total_layer_num=20),
          status(gcode_state="RUNNING", mc_percent=40, layer_num=1, total_layer_num=20, ams={"tray_now": "0"})):
    to_printer.put(d)
ok(wait(lambda: plug.STORE.active.get(SERIAL, {}).get("fils")), "grams read from the job file once printing is under way")
st = plug.handle({"type": "poll"})
tr = st["printers"][0]["job"]["trays"]
ok([t["spool"][:5] for t in tr] == ["S0001", "S0002"] and tr[0]["g"] == 10.1, "tab shows which spool each filament is coming off")
to_printer.put(status(gcode_state="RUNNING", mc_percent=80, layer_num=15, total_layer_num=20, ams={"tray_now": "1"}))
to_printer.put(status(gcode_state="FINISH", mc_percent=100, layer_num=20, total_layer_num=20))
ok(wait(lambda: plug.STORE.recent and "Bracket" in plug.STORE.recent[0]["text"]), "finished print taken off without asking")
s1, s2 = doc_of(f"users/{UID}/spools/s0001"), doc_of(f"users/{UID}/spools/s0002")
ok(s1["remainG"] == 790 and s2["remainG"] == 994, "grams taken off both spools (800 - 10.1, and 1000 - 5.6 from the full weight)")
ok(s1["prints"][0]["name"] == "Bracket" and s1["prints"][0]["g"] == 10.1, "print added to the spool's list")
jobs = [doc_of(k) for k in DB if "/jobs/" in k]
ok(len(jobs) == 1 and jobs[0]["printer"] == "Betsy" and {i["spool"] for i in jobs[0]["items"]} == {"s0001", "s0002"} and jobs[0]["at"] == s1["prints"][0]["at"],
   "a record for Undo in the app, matching the spool's print entry")
ok(doc_of(f"users/{UID}/spools/s0001")["brand"] == "Sunlu", "only the weight and print list change on the spool")

# ---- the same booking again is refused ----
again = dict(plug.STORE.recent[0]); entry = {"key": jobs and [k for k in DB if "/jobs/" in k][0].rsplit("/", 1)[1], "printer": "Betsy", "serial": SERIAL,
         "name": "Bracket", "plate": 1, "pct": 100, "at": 1, "items": [{"fil": 1, "g": 10.1, "tray": [0, 0], "spool": "s0001"}]}
with plug.STORE.lock: plug.STORE.outbox.append(entry)
plug._wake.set()
ok(wait(lambda: "already taken off" in plug.STORE.recent[0]["text"]) and doc_of(f"users/{UID}/spools/s0001")["remainG"] == 790, "the same print is never taken off twice")

# ---- a cancelled print waits for you ----
JOB["zip"], JOB["name"] = job_zip([(1, "PETG", "#2850E0", "20.00")]), "Lid.gcode.3mf"
to_printer.put({"print": {"command": "project_file", "subtask_name": "Lid", "file": "Lid.gcode.3mf", "param": "Metadata/plate_1.gcode",
                          "ams_mapping": [1], "ams_mapping2": [{"ams_id": 0, "slot_id": 1}], "use_ams": True, "result": "success"}})
for d in (status(gcode_state="PREPARE"), status(gcode_state="RUNNING", mc_percent=43, layer_num=12, total_layer_num=80, ams={"tray_now": "1"}),
          status(gcode_state="FAILED", mc_percent=43, print_error=plug.CANCELLED)):
    to_printer.put(d)
ok(wait(lambda: plug.STORE.pending), "cancelled print waits in the tab")
st = plug.handle({"type": "poll"})
pe = st["pending"][0]
ok(pe["why"] == "Cancelled" and pe["percent"] == 43 and pe["layer"] == 12 and pe["layers"] == 80, "says how far it got, from the printer")
ok(pe["items"][0]["spool"] == "s0002" and pe["items"][0]["trayName"] == "AMS 1 tray 2" and pe["items"][0]["g"] == 20.0, "suggests the spool in the tray it used")
ok(doc_of(f"users/{UID}/spools/s0002")["remainG"] == 994, "nothing taken off before you confirm")
plug.handle({"type": "confirm", "key": pe["key"], "pct": 43, "items": [{"spool": "s0002", "g": 20.0}]})
ok(wait(lambda: doc_of(f"users/{UID}/spools/s0002")["remainG"] == 985), "confirmed at 43%: 8.6 g taken off")
ok(not plug.STORE.pending and "8.6 g off S0002" in plug.STORE.recent[0]["text"], "tab says what was taken off")

# ---- a spool without enough left is flagged as the job starts ----
saved = DB[f"users/{UID}/spools/s0001"]
put(f"users/{UID}/spools/s0001", {**doc_of(f"users/{UID}/spools/s0001"), "remainG": 12})
plug.CLOUD.snap = None
JOB["zip"], JOB["name"] = job_zip([(1, "PETG", "#2850E0", "20.00")]), "Big.gcode.3mf"
to_printer.put({"print": {"command": "project_file", "subtask_name": "Big", "file": "Big.gcode.3mf", "param": "Metadata/plate_1.gcode",
                          "ams_mapping": [0], "ams_mapping2": [{"ams_id": 0, "slot_id": 0}], "use_ams": True, "result": "success"}})
ok(wait(lambda: plug.STORE.active.get(SERIAL, {}).get("warnings")), "job file read as soon as the job is sent, before printing starts")
st = plug.handle({"type": "poll"})
w = st["printers"][0]["job"]["warnings"]
ok(len(w) == 1 and "needs 20 g from S0001" in w[0] and "about 12 g left" in w[0], "warns that the spool in the tray has less left than the print needs")
ok(pushed and "Betsy" in pushed[-1][0] and "S0001" in pushed[-1][0] and pushed[-1][1] == "warning", "the warning pops up in Orca as a notification")
ok("about 12 g left" in plug.STORE.recent[0]["text"], "and stays in the tab's recent list")
DB[f"users/{UID}/spools/s0001"] = saved
plug.CLOUD.snap = None

# ---- Orca blocks the background thread: the tab sends it instead ----
real = plug.urllib.request.urlopen
def guarded(*a, **k):
    if threading.current_thread() is not threading.main_thread():
        raise plug.urllib.error.URLError(PermissionError("Plugin attempted audited operation without permission"))
    return real(*a, **k)
plug.urllib.request.urlopen = guarded
plug.CLOUD.snap = None
JOB["zip"], JOB["name"] = job_zip([(1, "PETG", "#2850E0", "3.00")]), "Clip.gcode.3mf"
to_printer.put({"print": {"command": "project_file", "subtask_name": "Clip", "file": "Clip.gcode.3mf", "param": "Metadata/plate_1.gcode",
                          "ams_mapping": [0], "use_ams": True, "result": "success"}})
for d in (status(gcode_state="RUNNING", mc_percent=50, layer_num=3, total_layer_num=6), status(gcode_state="FINISH", mc_percent=100)):
    to_printer.put(d)
ok(wait(lambda: plug.CLOUD.ui_mode), "notices Orca refusing background network calls")
for _ in range(30):
    plug.handle({"type": "poll"}); time.sleep(0.2)
    if doc_of(f"users/{UID}/spools/s0001")["remainG"] == 787: break
ok(doc_of(f"users/{UID}/spools/s0001")["remainG"] == 787, "the tab sends it from Orca's main thread instead")
plug.urllib.request.urlopen = real

# ---- a print the plugin joined part way through ----
plug.disconnect_all(); plug._allowed.clear()
with plug.STORE.lock: plug.STORE.active.clear()
to_printer.queue.clear() if hasattr(to_printer, "queue") else None
seen.pop("pushall", None)
plug.handle({"type": "connect"})
wait(lambda: seen.get("pushall"))
JOB["zip"], JOB["name"] = job_zip([(1, "PLA", "#FFFFFF", "4.00")]), "Hook.gcode.3mf"
to_printer.put(status(gcode_state="RUNNING", subtask_name="Hook", gcode_file="Hook.gcode.3mf", mc_percent=60, layer_num=5, total_layer_num=10, ams={"tray_now": "0"}))
to_printer.put(status(gcode_state="FINISH", mc_percent=100, subtask_name="Hook"))
ok(wait(lambda: plug.handle({"type": "poll"}) and "Hook" in plug.STORE.recent[0]["text"]), "joined part way: one filament fed from one tray is matched and taken off")
ok(doc_of(f"users/{UID}/spools/s0001")["remainG"] == 783, "4 g off the spool in that tray")

# ---- Orca's theme ----
plug._theme["at"] = 0
ok(plug.handle({"type": "poll"})["dark"] is False, "light when Orca is in light mode")
(tmp / "OrcaSlicer" / "OrcaSlicer.conf").write_text(json.dumps({"app": {"dark_color_mode": "1"}}) + "\n# MD5 checksum X\n")
plug._theme["at"] = 0
ok(plug.handle({"type": "poll"})["dark"] is True, "dark when Orca is in dark mode")
ok('data-theme=dark' in html and "prefers-color-scheme:dark" in html, "tab has both colour schemes")

# ---- the Stock view ----
st = plug.handle({"type": "refresh"})
sv = st["stock"]["spools"]
ok([x["id"] for x in sv] == ["s0001", "s0002", "s0005", "s0004", "s0006"], "stock lists every spool not used up, in storage and space order: " + ",".join(x["id"] for x in sv))
by = {x["id"]: x for x in sv}
ok(by["s0001"]["where"] == "AMS 1, space 1" and by["s0005"]["where"] == "Rack" and by["s0006"]["where"] == "Space Pi, space 1", "says which storage and space each is in")
ok(by["s0001"]["printer"] == "Betsy" and by["s0005"]["printer"] == "Betsy" and by["s0006"]["printer"] == "Alduin", "marks spools a printer feeds from")
ok(by["s0002"]["left"] == 985 and by["s0002"]["net"] == 1000 and by["s0002"]["low"] is False, "weight left and full weight for the bar")
put(f"users/{UID}/spools/s0007", {"remainG": 90, "netG": 1000, "brand": "eSUN", "material": "PLA+", "colours": ["#ff0000", "#00ff00"], "pattern": "split"})
st = plug.handle({"type": "refresh"})
s7 = next(x for x in st["stock"]["spools"] if x["id"] == "s0007")
ok(s7["low"] and s7["where"] == "Not in a storage" and s7["cols"] == ["#ff0000", "#00ff00"] and s7["pattern"] == "split" and st["stock"]["spools"][-1]["id"] == "s0007",
   "a low spool with no storage comes last, with its split colours")

# ---- presets: a Spool Stock group in Orca's filament list ----
def item(name, label, own, parents, extra=None):
    p = {"type": "filament", "name": name, "from": "User", "instantiation": "true", "inherits": parents[0], "version": "2.4.0.0", "filament_settings_id": [name], "filament_vendor": [label.split()[0]], **(extra or {})}
    return {"name": name, "label": label, "own": own, "parents": parents, "text": json.dumps(p)}
feed = [item("Bambu ASA (Spool Stock)", "Bambu ASA", True, ["Bambu ASA @System", "Bambu ASA @BBL X1C"]),
        item("Sunlu PETG (Spool Stock)", "Sunlu PETG", False, ["Generic PETG @System", "Generic PLA @System"], {"nozzle_temperature": ["240"], "compatible_printers": ["X"], "printhost_apikey": "k"}),
        item("Odd PEEK (Spool Stock)", "Odd PEEK", False, ["Generic PEEK @System"]),
        item("../evil", "Evil", False, ["Generic PLA @System"])]
ok(plug.handle({"type": "poll"})["presets"]["have"] is False, "no list from the app yet: says to open Spool Stock once")
put(f"users/{UID}/config/orca", {"v": 1, "sig": "a1", "at": 1, "items": feed})
old = tmp / "OrcaSlicer" / "user" / "4242" / "filament" / "Sunlu PETG (Spool Stock).json"
old.parent.mkdir(parents=True, exist_ok=True)
old.write_text(json.dumps({"type": "filament", "name": "Sunlu PETG (Spool Stock)", "from": "User", "inherits": "Generic PETG @System", "filament_settings_id": ["Sunlu PETG (Spool Stock)"]}))
mine = tmp / "OrcaSlicer" / "user" / "4242" / "filament" / "My PLA.json"; mine.write_text(json.dumps({"type": "filament", "name": "My PLA", "from": "User"}))
st = plug.handle({"type": "refresh"})
ok(st["presets"]["have"] and not st["presets"]["on"] and not reloads, "the list arrives but nothing is written until you turn it on")
st = plug.handle({"type": "presets_on"})
bdir = tmp / "OrcaSlicer" / "user" / "4242" / "_local" / "spool-stock"
files = sorted(f.name for f in (bdir / "filament").iterdir())
ok(json.loads((bdir / "bundle_metadata.json").read_text())["id"] == "spool-stock", "writes a local bundle in Orca's active user folder, found from a user preset")
ok(files == ["Bambu ASA (Spool Stock).json", "Sunlu PETG (Spool Stock).json"], "one preset per filament: " + ", ".join(files))
asa, petg = (json.loads((bdir / "filament" / f).read_text()) for f in files)
ok(asa["inherits"] == "Bambu ASA @BBL X1C" and set(asa) == {"type", "name", "from", "instantiation", "inherits", "version", "filament_settings_id", "filament_vendor"},
   "Orca's own profile used unchanged, picking a version this Orca has")
ok(petg["inherits"] == "Generic PETG @System" and petg["nozzle_temperature"] == ["240"] and "compatible_printers" not in petg and "printhost_apikey" not in petg,
   "a generic based preset keeps the temperatures and nothing that would limit or reach a printer")
ok(reloads == ["spool-stock"] and st["presets"]["on"] and st["presets"]["how"] == "live", "Orca reloads the group straight away")
ok(st["presets"]["skipped"] == ["Odd PEEK", "Evil"], "says which couldn't be added: " + ", ".join(st["presets"]["skipped"]))
ok(not old.exists() and (tmp / "OrcaSlicer" / "spoolstock" / "old-presets" / old.name).exists() and mine.exists() and st["presets"]["moved"] == 1,
   "the Windows app's copy is moved aside so it doesn't show twice, and your own presets are left alone")
reloads.clear()
plug.handle({"type": "presets_now"})
ok(reloads == [], "nothing changed, so no reload")
put(f"users/{UID}/config/orca", {"v": 1, "sig": "a2", "at": 2, "items": feed[:1]})
plug.CLOUD.snapshot(max_age=0); plug._preset_try["at"] = 0
st = plug.handle({"type": "poll"})
ok(sorted(f.name for f in (bdir / "filament").iterdir()) == ["Bambu ASA (Spool Stock).json"] and reloads == ["spool-stock"], "updates by itself when your filaments change")
ok(st["presets"]["behind"] is False and st["presets"]["count"] == 1, "and shows it's up to date")
bundles_api.reload_local = None
put(f"users/{UID}/config/orca", {"v": 1, "sig": "a3", "at": 3, "items": feed[:2]})
st = plug.handle({"type": "presets_now"})
ok(st["presets"]["how"] == "restart" and "Restart Orca" in st["hint"], "an Orca without the live reload is told to restart")
logtext = (tmp / "OrcaSlicer" / "spoolstock" / "spoolstock.log").read_text(encoding="utf-8")
ok('"presets.sync"' in logtext and '"reload": "restart"' in logtext, "the log says how each update went")
bundles_api.reload_local = reload_local
st = plug.handle({"type": "presets_off"})
ok(not list((bdir / "filament").iterdir()) and reloads[-1] == "spool-stock" and not st["presets"]["on"], "turning it off empties the group and reloads")

# ---- cameras: a fake Tapo streaming real H.264 over RTSP, with Digest sign-in ----
import hashlib
h264 = tmp / "cam.h264"
subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15", "-t", "4", "-c:v", "libx264", "-profile:v", "baseline",
                "-x264-params", "keyint=15:min-keyint=15:slices=1:scenecut=0", "-pix_fmt", "yuv420p", "-f", "h264", str(h264)], check=True)
raw = h264.read_bytes()
nals = [n.lstrip(b"\x00") for n in re.split(b"\x00\x00\x01", raw) if n.strip(b"\x00")]
nals = [n[:-1] if n.endswith(b"\x00") else n for n in nals]
AUS, cur = [], []
for n in nals:
    cur.append(n)
    if n[0] & 0x1F in (1, 5): AUS.append(cur); cur = []
SPS = next(n for n in nals if n[0] & 0x1F == 7); PPS = next(n for n in nals if n[0] & 0x1F == 8)
CAM = {"user": "printer cam", "pass": "pa@ss/1", "realm": "TP-LINK IP-CAM", "nonce": "f00dcafe", "log": [], "playing": 0}
def md5(x): return hashlib.md5(x.encode()).hexdigest()
def rtp(seq, ts, pay, mark): return struct.pack(">BBHII", 0x80, (0x80 if mark else 0) | 96, seq & 0xFFFF, ts & 0xFFFFFFFF, 0x1234) + pay
def cam_conn(c):
    lock = threading.Lock(); buf = b""; playing = threading.Event()
    def send(b):
        with lock: c.sendall(b)
    def stream():
        seq, ts = 0, 0xFFFFF000   # starts near the wrap, to check it's handled
        while playing.is_set():
            for au in AUS:
                if not playing.is_set(): break
                pkts = []
                params = [n for n in au if n[0] & 0x1F in (7, 8)]
                if params: pkts.append(bytes([24]) + b"".join(struct.pack(">H", len(n)) + n for n in params))
                for n in (n for n in au if n[0] & 0x1F not in (7, 8)):
                    if len(n) <= 1200: pkts.append(n)
                    else:
                        body, hdr = n[1:], n[0]
                        parts = [body[i:i + 1200] for i in range(0, len(body), 1200)]
                        for i, part in enumerate(parts):
                            pkts.append(bytes([(hdr & 0xE0) | 28, (0x80 if i == 0 else 0) | (0x40 if i == len(parts) - 1 else 0) | (hdr & 0x1F)]) + part)
                for i, pk in enumerate(pkts):
                    r = rtp(seq, ts, pk, i == len(pkts) - 1); seq += 1
                    try: send(b"$\x00" + struct.pack(">H", len(r)) + r)
                    except OSError: return
                ts += 6000; time.sleep(1 / 15)
    try:
        while True:
            while b"\r\n\r\n" not in buf:
                d = c.recv(4096)
                if not d: return
                buf += d
            head, buf = buf.split(b"\r\n\r\n", 1)
            lines = head.decode().split("\r\n"); method, url = lines[0].split()[:2]
            h = {l.split(":", 1)[0].lower(): l.split(":", 1)[1].strip() for l in lines[1:] if ":" in l}
            CAM["log"].append((method, url, h.get("authorization", "")))
            cseq = h.get("cseq", "0")
            auth = h.get("authorization", "")
            want = md5(f"{md5(CAM['user'] + ':' + CAM['realm'] + ':' + CAM['pass'])}:{CAM['nonce']}:{md5(method + ':' + url)}")
            if method not in ("OPTIONS", "TEARDOWN") and f'response="{want}"' not in auth:
                send(f'RTSP/1.0 401 Unauthorized\r\nCSeq: {cseq}\r\nWWW-Authenticate: Basic realm="{CAM["realm"]}"\r\nWWW-Authenticate: Digest realm="{CAM["realm"]}", nonce="{CAM["nonce"]}"\r\n\r\n'.encode()); continue
            port = c.getsockname()[1]
            if method == "DESCRIBE":
                if not url.endswith("/stream2"): send(f"RTSP/1.0 404 Not Found\r\nCSeq: {cseq}\r\n\r\n".encode()); continue
                sdp = (f"v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\ns=Session streamed by \"TP-LINK RTSP Server\"\r\nt=0 0\r\nm=audio 0 RTP/AVP 8\r\na=rtpmap:8 PCMA/8000\r\na=control:track2\r\n"
                       f"m=video 0 RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\na=fmtp:96 packetization-mode=1; profile-level-id=42C01E; sprop-parameter-sets={base64.b64encode(SPS).decode()},{base64.b64encode(PPS).decode()}\r\na=control:track1\r\n")
                send(f"RTSP/1.0 200 OK\r\nCSeq: {cseq}\r\nContent-Base: rtsp://127.0.0.1:{port}/stream2/\r\nContent-Type: application/sdp\r\nContent-Length: {len(sdp)}\r\n\r\n{sdp}".encode())
            elif method == "SETUP":
                ok_track = url == f"rtsp://127.0.0.1:{port}/stream2/track1" and "interleaved=0-1" in h.get("transport", "")
                send(f"RTSP/1.0 {'200 OK' if ok_track else '461 Unsupported Transport'}\r\nCSeq: {cseq}\r\nTransport: RTP/AVP/TCP;unicast;interleaved=0-1\r\nSession: 7788;timeout=60\r\n\r\n".encode())
            elif method == "PLAY":
                send(f"RTSP/1.0 200 OK\r\nCSeq: {cseq}\r\nSession: 7788\r\n\r\n".encode()); playing.set(); CAM["playing"] += 1
                threading.Thread(target=stream, daemon=True).start()
            elif method == "TEARDOWN":
                playing.clear(); send(f"RTSP/1.0 200 OK\r\nCSeq: {cseq}\r\n\r\n".encode()); return
            else:
                send(f"RTSP/1.0 200 OK\r\nCSeq: {cseq}\r\nSession: 7788\r\n\r\n".encode())
    except OSError: pass
    finally: playing.clear(); c.close()
csrv = socket.socket(); csrv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); csrv.bind(("127.0.0.1", 0)); csrv.listen(5)
def cam_server():
    while True:
        c, _ = csrv.accept(); threading.Thread(target=cam_conn, args=(c,), daemon=True).start()
threading.Thread(target=cam_server, daemon=True).start()
CPORT = csrv.getsockname()[1]
plug.CAM_IDLE = 3

ok(plug.parse_rtsp("rtsp://printer cam:pa@ss/1@192.168.1.50/stream2") == {"user": "printer cam", "password": "pa@ss/1", "host": "192.168.1.50", "port": 554, "url": "rtsp://192.168.1.50/stream2"},
   "camera links with a space, @ and / in the sign-in still read right")
ok(plug.parse_rtsp("rtsp://printer%20cam:pw@10.0.0.2:8554/stream1")["user"] == "printer cam" and plug.parse_rtsp("http://x/") is None, "an encoded space works, and a web link is refused")
ok(plug.sps_size(SPS) == (320, 240), "picture size read from the camera's video settings")
qc = plug.Camera("q", "rtsp://u:p@h/stream2", "q"); qc.auth = ("digest", {"realm": "r", "nonce": "n", "qop": "auth,auth-int", "algorithm": "MD5"})
qa = qc._authorisation("PLAY", "rtsp://h/stream2"); qd = dict(re.findall(r'(\w+)="?([^",]*)"?', qa[7:]))
ok(qd.get("qop") == "auth" and qd.get("nc") == "00000001" and qd["response"] == md5(f"{md5('u:r:p')}:n:00000001:{qd['cnonce']}:auth:{md5('PLAY:rtsp://h/stream2')}"),
   "the stricter Digest sign-in (qop) works too")
st = plug.handle({"type": "cam_add", "name": "Betsy cam", "url": f"rtsp://printer cam:pa@ss/1@127.0.0.1:{CPORT}/stream2", "printer": "Betsy"})
cams = st["cameras"]
ok(len(cams) == 1 and cams[0]["printer"] == "Betsy" and cams[0]["host"] == "127.0.0.1" and "url" not in cams[0], "camera added, and the tab never gets its link or password")
cid = cams[0]["id"]
got, gen, seq, replies = [], 0, -1, []
end = time.time() + 6
while time.time() < end:
    r = plug.handle({"type": "cams", "want": [{"id": cid, "gen": gen, "seq": seq}]})["cams"][0]
    replies.append(r)
    if r.get("init"): gen, seq = r["gen"], -1; got = [base64.b64decode(r["init"])]
    if r.get("chunks") and r["gen"] == gen: got += [base64.b64decode(x) for x in r["chunks"]]; seq = r["seq"]
    time.sleep(0.3)
ok(replies[-1]["status"] == "Live" and replies[-1]["mime"] == 'video/mp4; codecs="avc1.%02X%02X%02X"' % (SPS[1], SPS[2], SPS[3]), "camera goes live: " + replies[-1]["status"] + " " + replies[-1].get("mime", ""))
auth_used = [a for m, u, a in CAM["log"] if m == "DESCRIBE"]
ok(len(auth_used) == 2 and not auth_used[0] and auth_used[1].startswith("Digest ") and 'username="printer cam"' in auth_used[1], "signs in with Digest when the camera asks, over Basic")
mp4 = tmp / "cam.mp4"; mp4.write_bytes(b"".join(got))
pr = json.loads(subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-show_streams", "-of", "json", str(mp4)], capture_output=True, text=True).stdout or "{}")
vs = (pr.get("streams") or [{}])[0]
ok(vs.get("codec_name") == "h264" and vs.get("width") == 320 and vs.get("height") == 240 and int(vs.get("nb_read_frames", 0)) >= 45,
   f"what reaches the tab is playable video: {vs.get('codec_name')} {vs.get('width')}x{vs.get('height')}, {vs.get('nb_read_frames')} frames")
dec = subprocess.run(["ffmpeg", "-v", "error", "-i", str(mp4), "-f", "null", "-"], capture_output=True, text=True)
ok(dec.returncode == 0 and not dec.stderr.strip(), "decodes without errors" + (": " + dec.stderr[:200] if dec.stderr.strip() else ""))
(tmp / "cam-replies.json").write_text(json.dumps([r for r in replies if r.get("init") or r.get("chunks")]))
late = plug.handle({"type": "cams", "want": [{"id": cid, "gen": 0, "seq": -1}]})["cams"][0]
ok(late.get("init") and plug.mp4_init(SPS, PPS)[0] == base64.b64decode(late["init"]) and late["chunks"] and (base64.b64decode(late["chunks"][0])[4:8] == b"moof"),
   "a page that opens later gets the header and starts from a keyframe")
first = base64.b64decode(late["chunks"][0]); trun = first.index(b"trun")
ok(struct.unpack(">I", first[trun + 24:trun + 28])[0] == 0x02000000, "and that first frame is a keyframe")
playing_before = CAM["playing"]
time.sleep(plug.CAM_IDLE + 2.5)
ok(any(m == "TEARDOWN" for m, _, _ in CAM["log"]) and not plug._cams[cid].thread.is_alive(), "lets the camera go once the Cameras page stops asking")
r = plug.handle({"type": "cams", "want": [{"id": cid, "gen": gen, "seq": seq}]})["cams"][0]
ok(wait(lambda: plug.handle({"type": "cams", "want": [{"id": cid, "gen": gen, "seq": seq}]})["cams"][0].get("init")) and CAM["playing"] == playing_before + 1,
   "and reconnects with a fresh stream when it's opened again")
plug.handle({"type": "cam_add", "name": "Wrong", "url": f"rtsp://printer cam:nope@127.0.0.1:{CPORT}/stream2"})
wid = next(c["id"] for c in plug.cameras_view() if c["name"] == "Wrong")
ok(wait(lambda: "didn't accept the username and password" in plug.handle({"type": "cams", "want": [{"id": wid}]})["cams"][0]["status"], 8), "a wrong password is explained")
plug.handle({"type": "cam_add", "name": "Path", "url": f"rtsp://printer cam:pa@ss/1@127.0.0.1:{CPORT}/stream9"})
pid = next(c["id"] for c in plug.cameras_view() if c["name"] == "Path")
ok(wait(lambda: "no stream at that link" in plug.handle({"type": "cams", "want": [{"id": pid}]})["cams"][0]["status"], 8), "a wrong stream name is explained")
ok("isn't an RTSP link" in plug.handle({"type": "cam_add", "url": "http://192.168.1.50/stream2"})["hint"], "a web link is refused with an example")
plug.handle({"type": "cam_edit", "id": cid, "printer": "Alduin"})
ok(plug.cameras_view()[0]["printer"] == "Alduin", "a camera can be moved to another printer")
for x in (wid, pid): plug.handle({"type": "cam_remove", "id": x})
ok([c["id"] for c in plug.cameras_view()] == [cid] and wid not in plug._cams, "removing a camera stops it")
camfile = tmp / "OrcaSlicer" / "spoolstock" / "cameras.json"
ok(oct(os.stat(camfile).st_mode)[-3:] in ("600", "666"), "camera links are kept in a private file")

(tmp / "tab-state.json").write_text(json.dumps({**plug.handle({"type": "presets_now"}), "cameras": [{"id": "c1", "name": "Betsy cam", "printer": "Betsy", "host": "127.0.0.1"}]}))
(tmp / "tab.html").write_text(html)
r = subprocess.run(["node", str(Path(__file__).resolve().parent / "plugin-tab.js"), str(tmp / "tab.html"), str(tmp / "tab-state.json")] + ([str(tmp / "cam-replies.json")] if (tmp / "cam-replies.json").exists() else [""]) + ([os.environ["TAB_SHOTS"]] if os.environ.get("TAB_SHOTS") else []), capture_output=True, text=True)
print(r.stdout.strip()); ok(r.returncode == 0, "the tab's Stock and Presets views work in a browser" + (": " + r.stderr[-300:] if r.returncode else ""))

# ---- printers switched off for the night ----
def blackhole(ip, port):
    """A port that never answers, like a printer that's switched off: a full queue, so new connections hang."""
    l = socket.socket(); l.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); l.bind((ip, port)); l.listen(0)
    fill = []
    for _ in range(8):
        c = socket.socket(); c.setblocking(False)
        try: c.connect((ip, port))
        except BlockingIOError: pass
        fill.append(c)
    return l, fill
holes = [blackhole("127.0.0.4", plug.MQTT_PORT), blackhole("127.0.0.4", plug.FTP_PORT)]
pr_ = socket.socket(); pr_.settimeout(1.5)
try: pr_.connect(("127.0.0.4", plug.MQTT_PORT)); hangs = False
except socket.timeout: hangs = True
except OSError: hangs = False
pr_.close()
plug.disconnect_all(); plug._allowed.clear()
items = plug.load_printers() + [{"name": "Off", "ip": "127.0.0.4", "serial": "", "code": "SECRET99"}, {"name": "Night", "ip": "127.0.0.3", "serial": "", "code": "SECRET99"}]
plug.save_printers(items)
t0 = time.time(); plug.handle({"type": "ready"}); took = time.time() - t0
ok(took < 0.5, f"opening the tab with printers switched off doesn't hold Orca up: {took:.2f} s (the test address {'hangs like a switched off printer' if hangs else 'refuses'})")
t0 = time.time(); plug.handle({"type": "connect"}); took = time.time() - t0
ok(took < 0.5 and "Off" in plug._links and "Night" in plug._links, f"nor does Connect ({took:.2f} s), and both keep trying in the background")
row = lambda n: next(p for p in plug.handle({"type": "poll"})["printers"] if p["name"] == n)
ok(wait(lambda: "switched off" in row("Night")["state"], 10) and wait(lambda: "switched off" in row("Off")["state"], 12), "a printer that's off says so: " + row("Off")["state"])
night = socket.socket(); night.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); night.bind(("127.0.0.3", plug.MQTT_PORT)); night.listen(5)
threading.Thread(target=lambda: mqtt_conn(night.accept()[0]), daemon=True).start()
ok(wait(lambda: row("Night")["state"] == "connected", 25), "connects by itself when the printer is switched back on, with no Connect press")
logtext = (tmp / "OrcaSlicer" / "spoolstock" / "spoolstock.log").read_text(encoding="utf-8")
errs = [l for l in logtext.splitlines() if '"printer.error"' in l and '"Off"' in l]
ok(1 <= len(errs) <= 2 and '"printer.back"' in logtext, f"a night switched off doesn't fill the log ({len(errs)} lines for the printer that stayed off)")
off = plug._links["Off"]
t0 = time.time(); plug.disconnect_all(); off.thread.join(3)
ok(not off.thread.is_alive(), f"closing Orca stops a connection that's still waiting, in {time.time() - t0:.1f} s")
for l, fill in holes:
    for c in fill: c.close()
    l.close()

# ---- privacy ----
logtext = (tmp / "OrcaSlicer" / "spoolstock" / "spoolstock.log").read_text(encoding="utf-8")
ok("SECRET99" not in logtext and REFRESH not in logtext and "APIKEY" not in logtext and "IDTOKEN1" not in logtext and "pa@ss/1" not in logtext and "nope@" not in logtext,
   "access codes, camera passwords, sign-in and API key never written to the log")
ok('"camera.connected"' in logtext and '"camera.stopped"' in logtext, "the log says when a camera connects and stops")
acct = tmp / "OrcaSlicer" / "spoolstock" / "account.json"
ok(oct(os.stat(acct).st_mode)[-3:] in ("600", "666") and oct(os.stat(tmp / "OrcaSlicer" / "spoolstock" / "printers.json").st_mode)[-3:] in ("600", "666"), "saved sign-in and printers are private files")
ok(all(c[1] in ("/token",) or c[1].startswith(f"/v1/{ROOT}") for c in calls), "talks only to the sign-in and database addresses")
plug.handle({"type": "signout"})
ok(not acct.exists() and not plug.handle({"type": "poll"})["signedIn"], "sign out removes the saved sign-in")
plug.disconnect_all()
sys.exit(0 if ok_all else 1)
