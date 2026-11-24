"""Test case library: 4G-5G interworking and mobility validation.

A test is an ID, title, 3GPP reference and a function that drives the simulated
network through a Client and raises AssertionError on mismatch. A test may return
a dict of metrics, which the report shows as KPIs.
"""
import itertools

TESTS = []
_n = itertools.count(1)
PLMN = {"mcc": "001", "mnc": "01"}

R_HO = "TS 23.502 4.11.1.2"
R_IDLE = "TS 23.502 4.11.1.3"
R_FBK = "TS 23.502 4.13.6.1"
R_MAP = "TS 23.501 5.17.2"


def tc(tc_id, title, ref):
    def deco(fn):
        TESTS.append({"id": tc_id, "title": title, "ref": ref, "fn": fn})
        return fn
    return deco


def eq(actual, expected, what):
    if actual != expected:
        raise AssertionError(f"{what}: expected {expected!r}, got {actual!r}")


def new_supi():
    return f"imsi-00101{next(_n):010d}"


def ue_5g(c, sessions=()):
    supi = new_supi()
    code, _ = c.call("POST", "/5gc/register", {"supi": supi, "plmnId": PLMN})
    eq(code, 201, "setup 5G registration")
    for sid, dnn, sst in sessions:
        code, _ = c.call("POST", "/5gc/pdu-session", {"supi": supi, "pduSessionId": sid, "dnn": dnn, "sst": sst})
        eq(code, 201, f"setup PDU session {sid}")
    return supi


def ue_4g(c):
    supi = new_supi()
    code, _ = c.call("POST", "/epc/attach", {"supi": supi, "plmnId": PLMN})
    eq(code, 201, "setup 4G attach")
    return supi


def get(c, supi):
    code, obj = c.call("GET", f"/ue/{supi}")
    eq(code, 200, "UE state query")
    return obj


def ips(v):
    return {s["id"]: s["ip"] for s in v["sessions"]}


def qis(v):
    return {s["id"]: sorted(f["qi"] for f in s["flows"]) for s in v["sessions"]}


def ho(c, supi, target):
    return c.call("POST", "/mobility/handover", {"supi": supi, "targetRat": target})


def idle(c, supi, target):
    return c.call("POST", "/mobility/idle", {"supi": supi, "targetRat": target})


def call(c, supi):
    return c.call("POST", "/ims/call", {"supi": supi})


# ---------------- Baseline ----------------
@tc("TC-BASE-01", "4G attach creates default bearer (QCI 9) and IP", "TS 23.401 5.3.2")
def _(c):
    v = get(c, ue_4g(c))
    eq((v["core"], v["rat"]), ("EPC", "LTE"), "core / RAT")
    eq(qis(v), {1: [9]}, "default bearer QCI")


@tc("TC-BASE-02", "5G registration creates default PDU session (5QI 9) and IP", "TS 23.502 4.2.2.2.2")
def _(c):
    v = get(c, ue_5g(c))
    eq((v["core"], v["rat"]), ("5GC", "NR"), "core / RAT")
    eq(qis(v), {1: [9]}, "default flow 5QI")


@tc("TC-BASE-03", "Barred subscriber is rejected on 4G attach", "TS 23.401 5.3.2")
def _(c):
    code, obj = c.call("POST", "/epc/attach", {"supi": "imsi-001019999999999", "plmnId": PLMN})
    eq(code, 403, "status")
    eq(obj["cause"], "AUTHENTICATION_FAILURE", "cause")


# ---------------- Idle-mode mobility ----------------
@tc("TC-IDL-01", "5GS to EPS idle move (TAU) keeps IP and sessions", R_IDLE)
def _(c):
    supi = ue_5g(c, [(2, "ims", 1)])
    before = get(c, supi)
    code, obj = idle(c, supi, "LTE")
    eq(code, 200, "status")
    eq(obj["result"], "TAU_SUCCESS", "result")
    after = get(c, supi)
    eq(after["core"], "EPC", "core")
    eq(ips(after), ips(before), "session IPs preserved")
    eq(sorted(ips(after)), [1, 2], "session IDs")
    if not after["guti"].startswith("4g-guti"):
        raise AssertionError(f"mapped GUTI not assigned: {after['guti']}")


@tc("TC-IDL-02", "EPS to 5GS idle move (mobility registration) keeps IP", R_IDLE)
def _(c):
    supi = ue_4g(c)
    before = get(c, supi)
    code, obj = idle(c, supi, "NR")
    eq(code, 200, "status")
    eq(obj["result"], "MOBILITY_REGISTRATION_SUCCESS", "result")
    after = get(c, supi)
    eq((after["core"], after["rat"]), ("5GC", "NR"), "core / RAT")
    eq(ips(after), ips(before), "session IPs preserved")


@tc("TC-IDL-03", "Idle move for an unknown UE returns 404", R_IDLE)
def _(c):
    eq(idle(c, new_supi(), "LTE")[0], 404, "status")


@tc("TC-IDL-04", "Slice-specific session is released when moving to EPS", R_IDLE)
def _(c):
    supi = ue_5g(c, [(3, "internet", 2)])
    code, obj = idle(c, supi, "LTE")
    eq(code, 200, "status")
    eq(obj["released"], [3], "released sessions")
    eq(sorted(ips(get(c, supi))), [1], "remaining sessions")


# ---------------- Connected-mode handover ----------------
@tc("TC-HO-01", "5GS to EPS handover preserves IP and maps 5QI 9 to QCI 9", R_HO)
def _(c):
    supi = ue_5g(c)
    before = get(c, supi)
    code, obj = ho(c, supi, "LTE")
    eq(code, 200, "status")
    after = get(c, supi)
    eq((after["core"], after["rat"]), ("EPC", "LTE"), "core / RAT")
    eq(ips(after), ips(before), "session IPs preserved")
    eq(qis(after), {1: [9]}, "QCI after handover")
    if obj["interruptionMs"] >= 100:
        raise AssertionError(f"interruption {obj['interruptionMs']} ms exceeds 100 ms")


@tc("TC-HO-02", "EPS to 5GS handover preserves IP and maps QCI 9 to 5QI 9", R_HO)
def _(c):
    supi = ue_4g(c)
    before = get(c, supi)
    code, obj = ho(c, supi, "NR")
    eq(code, 200, "status")
    after = get(c, supi)
    eq((after["core"], after["rat"]), ("5GC", "NR"), "core / RAT")
    eq(ips(after), ips(before), "session IPs preserved")
    eq(qis(after), {1: [9]}, "5QI after handover")
    if obj["interruptionMs"] >= 100:
        raise AssertionError(f"interruption {obj['interruptionMs']} ms exceeds 100 ms")


@tc("TC-HO-03", "IMS session keeps its QoS (5QI 5 <-> QCI 5) across handover", R_MAP)
def _(c):
    supi = ue_5g(c, [(2, "ims", 1)])
    eq(ho(c, supi, "LTE")[0], 200, "to EPS")
    eq(qis(get(c, supi))[2], [5], "IMS QCI in EPS")
    eq(ho(c, supi, "NR")[0], 200, "back to 5GS")
    eq(qis(get(c, supi))[2], [5], "IMS 5QI in 5GS")


@tc("TC-HO-04", "Handover transfers internet and IMS sessions, releases slice session", R_HO)
def _(c):
    supi = ue_5g(c, [(2, "ims", 1), (3, "internet", 2)])
    code, obj = ho(c, supi, "LTE")
    eq(code, 200, "status")
    eq(obj["transferred"], [1, 2], "transferred")
    eq(obj["released"], [3], "released")


@tc("TC-HO-05", "Handover to the current RAT is rejected (409)", R_HO)
def _(c):
    code, obj = ho(c, ue_5g(c), "NR")
    eq(code, 409, "status")
    eq(obj["cause"], "ALREADY_ON_TARGET_RAT", "cause")


@tc("TC-HO-06", "Handover for an unknown UE returns 404", R_HO)
def _(c):
    eq(ho(c, new_supi(), "LTE")[0], 404, "status")


@tc("TC-HO-07", "Invalid target RAT is rejected (400)", R_HO)
def _(c):
    code, obj = ho(c, ue_5g(c), "GSM")
    eq(code, 400, "status")
    eq(obj["cause"], "INVALID_TARGET_RAT", "cause")


@tc("TC-HO-08", "Ping-pong stability: 50 round trips keep IP, all succeed", R_HO)
def _(c):
    supi = ue_5g(c, [(2, "ims", 1)])
    base_ips = ips(get(c, supi))
    times, ok, total = [], 0, 0
    for i in range(50):
        for target in ("LTE", "NR"):
            total += 1
            code, obj = ho(c, supi, target)
            eq(code, 200, f"cycle {i + 1} to {target}")
            ok += 1
            times.append(obj["interruptionMs"])
    eq(ips(get(c, supi)), base_ips, "session IPs after 50 round trips")
    return {"Handovers attempted": total, "Handover success rate": f"{100.0 * ok / total:.1f}%",
            "Avg interruption (model)": f"{sum(times) / len(times):.0f} ms",
            "Max interruption (model)": f"{max(times)} ms"}


# ---------------- EPS fallback for IMS voice ----------------
@tc("TC-FBK-01", "Voice call from 5GS triggers EPS fallback with QCI 1 bearer", R_FBK)
def _(c):
    supi = ue_5g(c)
    before = get(c, supi)
    code, obj = call(c, supi)
    eq(code, 201, "status")
    eq(obj["epsFallback"], True, "epsFallback")
    v = get(c, supi)
    eq((v["core"], v["rat"]), ("EPC", "LTE"), "core / RAT after fallback")
    ims = next(s for s in v["sessions"] if s["dnn"] == "ims")
    eq(sorted(f["qi"] for f in ims["flows"]), [1, 5], "IMS bearers (QCI 1 + QCI 5)")
    eq(ips(v)[1], ips(before)[1], "internet session IP preserved")


@tc("TC-FBK-02", "Call release removes the QCI 1 bearer", R_FBK)
def _(c):
    supi = ue_5g(c)
    eq(call(c, supi)[0], 201, "call setup")
    eq(c.call("DELETE", f"/ims/call/{supi}")[0], 204, "release status")
    ims = next(s for s in get(c, supi)["sessions"] if s["dnn"] == "ims")
    eq(sorted(f["qi"] for f in ims["flows"]), [5], "IMS bearers after release")


@tc("TC-FBK-03", "Voice call on LTE needs no fallback (VoLTE)", R_FBK)
def _(c):
    supi = ue_4g(c)
    code, obj = call(c, supi)
    eq(code, 201, "status")
    eq(obj["epsFallback"], False, "epsFallback")


@tc("TC-FBK-04", "Voice call for an unknown UE returns 404", R_FBK)
def _(c):
    eq(call(c, new_supi())[0], 404, "status")


@tc("TC-FBK-05", "Return to 5GS after the call keeps sessions and IPs", R_FBK)
def _(c):
    supi = ue_5g(c)
    eq(call(c, supi)[0], 201, "call setup")
    eq(c.call("DELETE", f"/ims/call/{supi}")[0], 204, "call release")
    mid = get(c, supi)
    code, _o = idle(c, supi, "NR")
    eq(code, 200, "return status")
    after = get(c, supi)
    eq(after["core"], "5GC", "core")
    eq(ips(after), ips(mid), "session IPs preserved")


@tc("TC-FBK-06", "Handover during a call keeps the QCI 1 / 5QI 1 voice flow", R_MAP)
def _(c):
    supi = ue_5g(c)
    eq(call(c, supi)[0], 201, "call setup")
    eq(ho(c, supi, "NR")[0], 200, "handover back to 5GS")
    ims = next(s for s in get(c, supi)["sessions"] if s["dnn"] == "ims")
    eq(sorted(f["qi"] for f in ims["flows"]), [1, 5], "IMS flows after handover")
