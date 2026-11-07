# Runs the Orca probe plugin against a stand-in Orca and a fake Bambu printer. Run: python3 test/probe.py
import importlib.util, json, os, socket, ssl, struct, subprocess, sys, tempfile, threading, time, types
from pathlib import Path

ok_all = True
def ok(c, m):
    global ok_all
    print(("PASS " if c else "FAIL ") + m)
    ok_all &= bool(c)

tmp = Path(tempfile.mkdtemp())
os.environ["APPDATA"] = str(tmp)
(tmp / "OrcaSlicer").mkdir()
(tmp / "OrcaSlicer" / "OrcaSlicer.conf").write_text(json.dumps({"app": {"preset_folder": "default", "user_access_code": "x"}, "access_code": {"01P00A123": "SECRET99"}, "filaments": {}}) + "\n# MD5 checksum X\n")

# stand-in orca module
orca = types.ModuleType("orca")
class Base: pass
registered = []
orca.base = Base
orca.plugin = lambda cls: (cls().register_capabilities(), cls)[1]
orca.register_capability = registered.append
orca.request_permissions = lambda **k: None
orca.PluginType = types.SimpleNamespace(Pages="Pages")
orca.ExecutionResult = types.SimpleNamespace(success=lambda *a: "ok")
class PagesBase:
    def __init__(self): self.sent = []
    def post_message(self, m): self.sent.append(m)
class SlicingBase: pass
orca.pages = types.SimpleNamespace(PagesPluginCapabilityBase=PagesBase)
orca.slicing = types.SimpleNamespace(SlicingPipelineCapabilityBase=SlicingBase)
class Preset:
    def __init__(self, name, cfg): self.name, self.cfg = name, cfg
    def config_value(self, k): return self.cfg.get(k, "")
bundle = types.SimpleNamespace(current_printer_preset=lambda: Preset("Bambu Lab P1S 0.4 nozzle", {"printer_model": "Bambu Lab P1S", "print_host": "127.0.0.1"}),
                               printers=[Preset("Bambu Lab P1S 0.4 nozzle", {"print_host": "127.0.0.1"})])
orca.host = types.SimpleNamespace(app_info=lambda: {"version": "2.5.0"}, preset_bundle=lambda: bundle, ui=types.SimpleNamespace())
sys.modules["orca"] = orca

# fake printer: MQTT over TLS
cert, key = tmp / "c.pem", tmp / "k.pem"
subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert), "-days", "1", "-subj", "/CN=printer"], check=True, capture_output=True)
srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); srv.bind(("127.0.0.1", 0)); srv.listen(1)
port = srv.getsockname()[1]
sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); sctx.load_cert_chain(cert, key)
seen = {}
def mstr(s): b = s.encode(); return struct.pack("!H", len(b)) + b
def mlen(n):
    out = bytearray()
    while True:
        d, n = n % 128, n // 128; out.append(d | (128 if n else 0))
        if not n: return bytes(out)
def publish(conn, topic, doc):
    body = mstr(topic) + json.dumps(doc).encode(); conn.sendall(bytes([0x30]) + mlen(len(body)) + body)
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
status = {"print": {"command": "push_status", "gcode_state": "RUNNING", "mc_percent": 12, "layer_num": 5, "total_layer_num": 80,
          "subtask_name": "Bracket", "gcode_file": "/cache/Bracket.gcode.3mf", "task_id": "77",
          "ams": {"tray_now": "2", "ams": [{"id": "0", "tray": [{"id": "0", "tray_type": "PLA", "tray_color": "C8102EFF", "remain": 80, "tag_uid": "0000000000000000"},
                                                                {"id": "2", "tray_type": "PETG", "tray_color": "F4F4F2FF", "remain": -1}]}]},
          "vt_tray": {"tray_type": "PLA", "tray_color": "00843DFF"}, "ams_mapping": [0, 2]}}
def broker():
    raw, _ = srv.accept()
    conn = sctx.wrap_socket(raw, server_side=True)
    t, body = read(conn)
    i = 10 + struct.unpack("!H", body[10:12])[0] + 2   # after protocol + flags + keepalive + client id
    user_len = struct.unpack("!H", body[i:i+2])[0]; user = body[i+2:i+2+user_len].decode(); i += 2 + user_len
    pw = body[i+2:i+2+struct.unpack("!H", body[i:i+2])[0]].decode()
    seen["login"] = (user, pw)
    conn.sendall(b"\x20\x02\x00\x00")
    t, body = read(conn); seen["subscribe"] = body[4:4+struct.unpack("!H", body[2:4])[0]].decode()
    publish(conn, "device/01P00A123/report", status)
    conn.settimeout(10)
    try:
        while True:
            pk = read(conn)
            if pk is None: break
            if pk[0] >> 4 == 3:
                n = struct.unpack("!H", pk[1][:2])[0]; seen["request"] = (pk[1][2:2+n].decode(), pk[1][2+n:].decode())
                publish(conn, "device/01P00A123/report", {"print": {"command": "push_status", "gcode_state": "FINISH", "mc_percent": 100}})
    except Exception:
        pass
threading.Thread(target=broker, daemon=True).start()

spec = importlib.util.spec_from_file_location("probe", Path(__file__).resolve().parent.parent / "orca-plugin" / "probe" / "spoolstock_probe.py")
probe = importlib.util.module_from_spec(spec); spec.loader.exec_module(probe)
probe.MQTT_PORT = port
probe.FTP_PORT = 1  # nothing listens: the job file check must fail quietly
ok(len(registered) == 2, "registers a tab and a slicing step")
page = registered[0]()
html = page.get_ui()
ok("Spool Stock Probe" in html and "window.orca" in html, "tab page builds")
page.on_message({"type": "ready"})
ok(page.sent[-1]["suggest"] == "127.0.0.1", "printer IP suggested from Orca's printer preset")
page.on_message({"type": "add", "name": "Betsy", "ip": "127.0.0.1", "serial": "", "code": "SECRET99"})
for _ in range(50):
    time.sleep(0.2)
    if "request" in seen and probe._links.get("Betsy") and probe._links["Betsy"].last_sum.get("state") == "FINISH": break
ok(seen.get("login") == ("bblp", "SECRET99"), "logs in as bblp with the access code")
ok(seen.get("subscribe") == "device/+/report", "subscribes to reports, serial unknown at first")
ok(seen.get("request", ("", ""))[0] == "device/01P00A123/request" and "pushall" in seen["request"][1], "learns the serial and asks for full status")
link = probe._links["Betsy"]
s = link.last_sum
time.sleep(0.5)
ok(len(s["trays"]) == 2 and s["trays"][0]["type"] == "PLA" and s["external"]["type"] == "PLA", "AMS trays and external spool read")
ok(s["mapping_fields"].get("ams_mapping") == [0, 2], "tray mapping field captured")
page.on_lifecycle_event("LifecycleEvent.PrintJobStarted", types.SimpleNamespace(name="Bracket", device_id="01P00A123", code="Info"))
gc = tmp / "x.gcode"; gc.write_text("G1 X1\n; filament used [g] = 12.50, 3.20\n")
ctx = types.SimpleNamespace(step="Step.psGCodePostProcess", gcode_path=str(gc), config_value=lambda k: {"filament_settings_id": '"Bambu PLA Matte @BBL X1C";"Landu Rapid PETG (Spool Stock)"', "filament_colour": "#C8102E;#F4F4F2", "filament_type": "PLA;PETG"}.get(k, ""))
ok(registered[1]().execute(ctx) == "ok", "slicing step runs")
page.on_message({"type": "snapshot"})
time.sleep(0.5)
logtext = (tmp / "OrcaSlicer" / "spoolstock-probe" / "probe.log").read_text(encoding="utf-8")
kinds = [json.loads(l)["kind"] for l in logtext.splitlines()]
for k in ("probe.start", "orca.api", "orca.printer", "orca.conf", "printer.connected", "printer.serial_found", "printer.first_report", "printer.change", "printer.full_status", "orca.event", "slice.step", "printer.job_file_error"):
    ok(k in kinds, "logs " + k)
ok("SECRET99" not in logtext, "access code never written to the log")
sl = [json.loads(l) for l in logtext.splitlines() if json.loads(l)["kind"] == "slice.step"][0]
ok(sl["grams"] == [12.5, 3.2] and "Landu Rapid PETG" in sl["config"]["filament_settings_id"], "slice grams and presets logged")
conf = [json.loads(l) for l in logtext.splitlines() if json.loads(l)["kind"] == "orca.conf"][0]["hints"]
ok("access_code" in conf["sections"] and conf["access_code"]["keys"] == ["01P00A123"], "Orca's settings sections listed, keys only")
ok(oct(os.stat(tmp / "OrcaSlicer" / "spoolstock-probe" / "printers.json").st_mode)[-3:] in ("600", "666"), "saved printers file is private")
probe.disconnect_all()
sys.exit(0 if ok_all else 1)
