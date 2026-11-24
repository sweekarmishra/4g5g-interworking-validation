#!/usr/bin/env python3
"""Simulated 4G EPC + 5G core with N26 interworking, behind one HTTP port.

A *behavioural* model used to exercise a validation framework without lab
equipment. It models what the network does to a UE's sessions when it moves
between EPS (LTE/EPC) and 5GS (NR/5GC):
  - connected-mode handover over N26   (TS 23.502 4.11.1.2)
  - idle-mode move: TAU / mobility registration (TS 23.502 4.11.1.3)
  - EPS fallback for IMS voice         (TS 23.502 4.13.6.1)
  - PDU session <-> PDN connection and 5QI <-> QCI mapping (TS 23.501 5.17.2, 5.7.4)

Payloads are simplified, transport is HTTP/1.1, and RAN behaviour (RRC) is out
of scope. Latency figures are model values, not measurements.

Writes a log in the line format understood by tools/log_tracker.py.

Standalone:   python3 interworking_core.py --port 8080 --log core_test_logs.txt
Fault modes:  --fault n26_down    N26 context transfer fails
              --fault no_qci1     EPS dedicated bearer (QCI 1) setup fails
"""
import argparse
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ALLOWED_PLMN = {("001", "01")}
BARRED = {"imsi-001019999999999"}
DNNS = {"internet", "ims"}
SSTS = {1, 2}          # sst 2 = slice not supported in EPS, so not transferable
SUPI_RE = re.compile(r"^imsi-(\d{15})$")
RAT_TO_CORE = {"LTE": "EPC", "NR": "5GC"}


class Net:
    def __init__(self, logpath=None, fault=None):
        self.lock = threading.Lock()
        self.logpath, self.fault = logpath, fault
        self.ues = {}
        self.ip_n = 0
        if logpath:
            open(logpath, "w").close()

    # ---- logging ----
    def log(self, level, node, event, supi=None, detail=""):
        if not self.logpath:
            return
        ident = ""
        m = SUPI_RE.match(supi or "")
        if m:
            d = m.group(1)
            ident = f" IMSI={d} MCC={d[:3]} MNC={d[3:5]}"
        with open(self.logpath, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{level}] {node} {event}{ident} {detail}".rstrip() + "\n")

    # ---- helpers ----
    def new_ip(self):
        self.ip_n += 1
        return f"10.50.{self.ip_n // 250}.{self.ip_n % 250 + 2}"

    def view(self, ue):
        epc = ue["core"] == "EPC"
        sessions = []
        for s in sorted(ue["sessions"].values(), key=lambda x: x["id"]):
            d = {"id": s["id"], "dnn": s["dnn"], "sst": s["sst"], "ip": s["ip"],
                 "flows": [{"qi": f["qi"], "kind": f["kind"], "qiType": "QCI" if epc else "5QI"} for f in s["flows"]]}
            if epc:
                d["ebi"] = 4 + s["id"]
            sessions.append(d)
        return {"supi": ue["supi"], "core": ue["core"], "rat": ue["rat"], "guti": ue["guti"],
                "inCall": ue["call"], "sessions": sessions}

    def _guti(self, ue):
        ue["gen"] += 1
        return f"{'5g' if ue['core'] == '5GC' else '4g'}-guti-{ue['supi'][-6:]}-{ue['gen']}"

    def _move(self, ue, target):
        """Transfer the UE's sessions to the target core. Returns (moved, released)."""
        to_core = RAT_TO_CORE[target]
        moved, released = [], []
        for sid, s in list(ue["sessions"].items()):
            if to_core == "EPC" and s["sst"] not in (None, 1):
                released.append(sid)           # slice not available in EPS
                del ue["sessions"][sid]
            else:
                moved.append(sid)
                s["sst"] = None if to_core == "EPC" else (s["sst"] or 1)
        ue["core"], ue["rat"] = to_core, target
        ue["guti"] = self._guti(ue)
        return moved, sorted(released)

    def _admit(self, body, core):
        """Common admission checks. Returns (supi, error_tuple|None)."""
        body = body or {}
        supi, plmn = body.get("supi", ""), body.get("plmnId", {})
        node, bad_event = ("AMF", "REGISTRATION_REJECT") if core == "5GC" else ("MME", "ATTACH_REJECT")
        if not SUPI_RE.match(supi):
            self.log("ERROR", node, bad_event, detail="invalid identity format")
            return supi, (400, {"cause": "INVALID_SUPI"})
        if (plmn.get("mcc"), plmn.get("mnc")) not in ALLOWED_PLMN:
            self.log("ERROR", node, bad_event, supi, "PLMN not allowed")
            return supi, (403, {"cause": "PLMN_NOT_ALLOWED"})
        if supi in BARRED:
            self.log("ERROR", "HSS" if core == "EPC" else "AUSF", "AUTH_FAILURE", supi, "authentication rejected")
            return supi, (403, {"cause": "AUTHENTICATION_FAILURE"})
        if supi in self.ues:
            return supi, (409, {"cause": "ALREADY_REGISTERED"})
        return supi, None

    def _new_ue(self, supi, core):
        ue = {"supi": supi, "core": core, "rat": "LTE" if core == "EPC" else "NR", "gen": 0,
              "call": False, "sessions": {}}
        ue["guti"] = self._guti(ue)
        ue["sessions"][1] = {"id": 1, "dnn": "internet", "sst": None if core == "EPC" else 1,
                             "ip": self.new_ip(), "flows": [{"qi": 9, "kind": "default"}]}
        self.ues[supi] = ue
        return ue

    def _ensure_ims(self, ue):
        for s in ue["sessions"].values():
            if s["dnn"] == "ims":
                return s
        sid = next(i for i in range(2, 16) if i not in ue["sessions"])
        s = {"id": sid, "dnn": "ims", "sst": None if ue["core"] == "EPC" else 1, "ip": self.new_ip(),
             "flows": [{"qi": 5, "kind": "default"}]}
        ue["sessions"][sid] = s
        return s

    # ---- dispatch ----
    def handle(self, method, path, body):
        with self.lock:
            if method == "POST" and path == "/epc/attach":
                return self.attach(body)
            if method == "POST" and path == "/5gc/register":
                return self.register(body)
            if method == "POST" and path == "/5gc/pdu-session":
                return self.pdu_session(body)
            if method == "POST" and path == "/mobility/handover":
                return self.handover(body)
            if method == "POST" and path == "/mobility/idle":
                return self.idle(body)
            if method == "POST" and path == "/ims/call":
                return self.call_start(body)
            m = re.match(r"^/ims/call/([\w-]+)$", path)
            if method == "DELETE" and m:
                return self.call_end(m.group(1))
            m = re.match(r"^/ue/([\w-]+)$", path)
            if method == "GET" and m:
                ue = self.ues.get(m.group(1))
                return (200, self.view(ue)) if ue else (404, {"cause": "UE_NOT_FOUND"})
            return 404, {"cause": "RESOURCE_NOT_FOUND"}

    # ---- procedures ----
    def attach(self, body):
        supi, err = self._admit(body, "EPC")
        if err:
            return err
        ue = self._new_ue(supi, "EPC")
        self.log("INFO", "MME", "ATTACH_SUCCESS", supi, "default bearer QCI 9")
        return 201, self.view(ue)

    def register(self, body):
        supi, err = self._admit(body, "5GC")
        if err:
            return err
        ue = self._new_ue(supi, "5GC")
        self.log("INFO", "AMF", "REGISTRATION_SUCCESS", supi, "default PDU session 5QI 9")
        return 201, self.view(ue)

    def pdu_session(self, body):
        body = body or {}
        supi, sid, dnn, sst = body.get("supi"), body.get("pduSessionId"), body.get("dnn"), body.get("sst")
        ue = self.ues.get(supi)

        def fail(code, cause):
            self.log("ERROR", "SMF", "PDU_SESSION_FAILURE", supi, cause)
            return code, {"cause": cause}
        if not ue:
            return fail(404, "UE_NOT_FOUND")
        if ue["core"] != "5GC":
            return fail(409, "UE_NOT_IN_5GC")
        if not isinstance(sid, int) or not 1 <= sid <= 15:
            return fail(400, "INVALID_PDU_SESSION_ID")
        if dnn not in DNNS:
            return fail(403, "DNN_NOT_SUPPORTED")
        if sst not in SSTS:
            return fail(403, "SNSSAI_NOT_ALLOWED")
        if sid in ue["sessions"]:
            return fail(409, "PDU_SESSION_ALREADY_EXISTS")
        ue["sessions"][sid] = {"id": sid, "dnn": dnn, "sst": sst, "ip": self.new_ip(),
                               "flows": [{"qi": 5 if dnn == "ims" else 9, "kind": "default"}]}
        self.log("INFO", "SMF", "PDU_SESSION_SUCCESS", supi, f"id={sid} dnn={dnn} sst={sst}")
        return 201, self.view(ue)

    def _mobility_common(self, body):
        body = body or {}
        supi, target = body.get("supi"), body.get("targetRat")
        ue = self.ues.get(supi)
        if not ue:
            return supi, target, None, (404, {"cause": "UE_NOT_FOUND"})
        if target not in RAT_TO_CORE:
            return supi, target, ue, (400, {"cause": "INVALID_TARGET_RAT"})
        if ue["rat"] == target:
            return supi, target, ue, (409, {"cause": "ALREADY_ON_TARGET_RAT"})
        return supi, target, ue, None

    def handover(self, body):
        supi, target, ue, err = self._mobility_common(body)
        if err:
            return err
        src = ue["core"]
        node = "AMF" if src == "5GC" else "MME"
        if self.fault == "n26_down":
            self.log("ERROR", node, "HANDOVER_FAILURE", supi, "N26 context transfer timeout")
            return 500, {"cause": "N26_CONTEXT_TRANSFER_FAILURE"}
        moved, released = self._move(ue, target)
        ms = (45 if src == "5GC" else 40) + 6 * len(moved)    # model value
        self.log("INFO", node, "HANDOVER_SUCCESS", supi, f"{src}->{ue['core']} moved={moved} interruption={ms}ms")
        return 200, {"result": "HANDOVER_SUCCESS", "from": src, "to": ue["core"], "interruptionMs": ms,
                     "transferred": moved, "released": released, "ue": self.view(ue)}

    def idle(self, body):
        supi, target, ue, err = self._mobility_common(body)
        if err:
            return err
        ok, bad, node = (("TAU_SUCCESS", "TAU_REJECT", "MME") if target == "LTE"
                         else ("MOBILITY_REGISTRATION_SUCCESS", "REGISTRATION_REJECT", "AMF"))
        if self.fault == "n26_down":
            self.log("ERROR", node, bad, supi, "UE context not retrievable from old core")
            return 403, {"cause": "UE_CONTEXT_NOT_FOUND_AT_OLD_CORE"}
        src = ue["core"]
        moved, released = self._move(ue, target)
        self.log("INFO", node, ok, supi, f"{src}->{ue['core']} moved={moved}")
        return 200, {"result": ok, "from": src, "to": ue["core"], "transferred": moved,
                     "released": released, "ue": self.view(ue)}

    def call_start(self, body):
        supi = (body or {}).get("supi")
        ue = self.ues.get(supi)
        if not ue:
            return 404, {"cause": "UE_NOT_FOUND"}
        if ue["call"]:
            return 409, {"cause": "CALL_ALREADY_ACTIVE"}
        fallback = ue["core"] == "5GC"
        if fallback and self.fault == "n26_down":
            self.log("ERROR", "AMF", "EPS_FALLBACK_FAILURE", supi, "N26 context transfer timeout")
            return 500, {"cause": "EPS_FALLBACK_FAILURE"}
        if self.fault == "no_qci1":
            self.log("ERROR", "PGW", "EPS_BEARER_SETUP_FAILURE", supi, "dedicated bearer QCI 1 rejected")
            return 500, {"cause": "EPS_BEARER_SETUP_FAILURE"}
        if fallback:
            self._move(ue, "LTE")
            self.log("INFO", "AMF", "EPS_FALLBACK_SUCCESS", supi, "voice call moved UE to LTE")
        self._ensure_ims(ue)["flows"].append({"qi": 1, "kind": "dedicated"})
        ue["call"] = True
        self.log("INFO", "IMS", "CALL_SETUP_SUCCESS", supi, f"fallback={fallback}")
        return 201, {"epsFallback": fallback, "ue": self.view(ue)}

    def call_end(self, supi):
        ue = self.ues.get(supi)
        if not ue or not ue["call"]:
            return 404, {"cause": "NO_ACTIVE_CALL"}
        for s in ue["sessions"].values():
            s["flows"] = [f for f in s["flows"] if f["qi"] != 1]
        ue["call"] = False
        self.log("INFO", "IMS", "CALL_RELEASE_SUCCESS", supi)
        return 204, None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _go(self, method):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            return self._send(400, {"cause": "MALFORMED_JSON"})
        self._send(*self.server.net.handle(method, self.path.split("?")[0], body))

    def _send(self, code, obj):
        data = b"" if obj is None else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = lambda self: self._go("GET")
    do_POST = lambda self: self._go("POST")
    do_DELETE = lambda self: self._go("DELETE")


def start_server(port=0, logpath=None, fault=None):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.net = Net(logpath, fault)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--log", default="core_test_logs.txt")
    ap.add_argument("--fault", choices=["n26_down", "no_qci1"])
    a = ap.parse_args()
    _s, url = start_server(a.port, a.log, a.fault)
    print(f"Simulated EPC+5GC on {url} (log {a.log}, fault {a.fault})")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
