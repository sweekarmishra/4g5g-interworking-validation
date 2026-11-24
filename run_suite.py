#!/usr/bin/env python3
"""Run the 4G-5G interworking validation suite and produce an HTML + JSON report.

Local demo (starts the built-in simulated network, nothing to install):
    python3 run_suite.py
    python3 run_suite.py --fault n26_down      # N26 context transfer fails
    python3 run_suite.py --fault no_qci1       # EPS voice bearer setup fails
    python3 run_suite.py --landslide-csv landslide/sample_landslide_export.csv

Against another endpoint that speaks the same simplified API:
    python3 run_suite.py --target http://HOST:PORT [--core-log path/to/core.log]
"""
import argparse
import html
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
for sub in ("", "sim", "tools"):
    sys.path.insert(0, os.path.join(HERE, sub))

import interworking_core  # noqa: E402
import landslide_report  # noqa: E402
import log_tracker  # noqa: E402
from test_cases import TESTS  # noqa: E402


class Client:
    def __init__(self, base):
        self.base, self.trace = base.rstrip("/"), []

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                code, raw = r.status, r.read()
        except urllib.error.HTTPError as e:
            code, raw = e.code, e.read()
        ms = (time.perf_counter() - t0) * 1000
        try:
            obj = json.loads(raw) if raw else None
        except ValueError:
            obj = raw.decode(errors="replace")
        self.trace.append({"method": method, "path": path, "request": body, "status": code,
                           "response": obj, "ms": round(ms, 1)})
        return code, obj


def run_all(base):
    results = []
    for t in TESTS:
        client, t0 = Client(base), time.perf_counter()
        status, msg, metrics = "PASS", "", None
        try:
            metrics = t["fn"](client)
        except AssertionError as e:
            status, msg = "FAIL", str(e)
        except Exception as e:
            status, msg = "ERROR", f"{type(e).__name__}: {e}"
        trace = client.trace if len(client.trace) <= 12 else client.trace[:6] + client.trace[-6:]
        results.append({"id": t["id"], "title": t["title"], "ref": t["ref"], "status": status, "message": msg,
                        "ms": round((time.perf_counter() - t0) * 1000, 1), "metrics": metrics,
                        "calls": len(client.trace), "trace": trace})
    return results


CSS = """
:root{--bg:#fff;--fg:#1c2330;--mut:#5b6678;--line:#dde2ea;--ok:#1a7f4b;--bad:#b3261e;--warn:#9a6700;--card:#f5f7fa}
@media (prefers-color-scheme:dark){:root{--bg:#12161d;--fg:#e6eaf0;--mut:#9aa6b8;--line:#2a3240;--ok:#4cc38a;--bad:#ff7b72;--warn:#e3b341;--card:#1a2029}}
body{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;margin:0;padding:24px 16px}
main{max-width:980px;margin:auto} h1{margin:0 0 4px;font-size:24px} h2{margin-top:32px;font-size:18px}
.sub{color:var(--mut);margin:0 0 20px}.kpis{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:8px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 16px;min-width:110px}
.kpi b{display:block;font-size:24px}.kpi span{color:var(--mut);font-size:13px}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--mut);font-weight:600;font-size:13px}tr.PASS td b{color:var(--ok)}tr.FAIL td b,tr.ERROR td b{color:var(--bad)}
tr.d td{padding-top:0}summary{cursor:pointer;color:var(--mut);font-size:13px}
pre{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:10px;overflow-x:auto;font-size:12px}
.msg{color:var(--bad);margin:6px 0}.fault{color:var(--warn);font-weight:600}.table-wrap{overflow-x:auto}
.note{color:var(--mut);font-size:13px}
"""


def kpi(value, label, color=""):
    style = f' style="color:var(--{color})"' if color else ""
    return f'<div class="kpi"><b{style}>{value}</b><span>{html.escape(label)}</span></div>'


def render_html(results, target, fault, log_summary, ls):
    counts = {s: sum(r["status"] == s for r in results) for s in ("PASS", "FAIL", "ERROR")}
    total, bad = len(results), counts["FAIL"] + counts["ERROR"]
    rows = []
    for r in results:
        trace = "\n".join(f"{x['method']} {x['path']}  ->  {x['status']}\n  request : {json.dumps(x['request'])}\n"
                          f"  response: {json.dumps(x['response'])}" for x in r["trace"])
        note = f"(showing first and last 6 of {r['calls']} calls)\n" if r["calls"] > len(r["trace"]) else ""
        detail = (f"<p class='msg'>{html.escape(r['message'])}</p>" if r["message"] else "") + \
                 f"<pre>{html.escape(note + trace)}</pre>"
        rows.append(f"<tr class='{r['status']}'><td>{r['id']}</td><td>{html.escape(r['title'])}</td>"
                    f"<td>{html.escape(r['ref'])}</td><td><b>{r['status']}</b></td></tr>"
                    f"<tr class='d'><td colspan='4'><details><summary>Message trace</summary>{detail}</details></td></tr>")
    metric_html = ""
    for r in results:
        if r.get("metrics"):
            metric_html += (f"<h2>Interworking KPIs <span class='note'>({r['id']}, simulated network)</span></h2>"
                            f"<div class='kpis'>{''.join(kpi(v, k) for k, v in r['metrics'].items())}</div>")
    ls_html = ""
    if ls:
        checks = "".join(f"<tr class='{'PASS' if ok else 'FAIL'}'><td>{html.escape(n)}</td><td>{v}</td>"
                         f"<td><b>{'PASS' if ok else 'FAIL'}</b></td></tr>" for n, ok, v in ls["checks"])
        ls_html = (f"<h2>Spirent Landslide results</h2><div class='kpis'>"
                   f"{kpi(ls['attempts'], 'attempts')}{kpi(ls['success_rate_pct'], 'success rate %')}"
                   f"{kpi(ls['avg_time_ms'], 'avg time (ms)')}"
                   f"{kpi('PASS' if ls['passed'] else 'FAIL', 'against criteria', 'ok' if ls['passed'] else 'bad')}</div>"
                   f"<div class='table-wrap'><table><tr><th>Criterion</th><th>Measured</th><th>Result</th></tr>{checks}</table></div>"
                   "<p class='note'>Source: exported results file, parsed with the column mapping in landslide/landslide_columns.json.</p>")
    log_html = f"<h2>Core log analysis (Log Tracker)</h2><pre>{html.escape(log_summary)}</pre>" if log_summary else ""
    banner = f"<p class='fault'>Fault injected: {html.escape(fault)}</p>" if fault else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>4G-5G Interworking Validation Report</title>
<style>{CSS}</style></head><body><main>
<h1>4G-5G Interworking Validation Report</h1>
<p class="sub">Target: {html.escape(target)} &middot; {time.strftime('%Y-%m-%d %H:%M:%S')} &middot; simulated network unless a real target was given</p>
{banner}<div class="kpis">{kpi(total, 'test cases')}{kpi(counts['PASS'], 'passed', 'ok')}{kpi(bad, 'failed / error', 'bad' if bad else '')}
{kpi(f"{100.0 * counts['PASS'] / total:.0f}%", 'pass rate')}</div>
{metric_html}<h2>Results</h2><div class="table-wrap"><table>
<tr><th>ID</th><th>Test case</th><th>3GPP reference</th><th>Result</th></tr>{''.join(rows)}</table></div>
{ls_html}{log_html}</main></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target")
    ap.add_argument("--fault", choices=["n26_down", "no_qci1"])
    ap.add_argument("--core-log")
    ap.add_argument("--landslide-csv")
    ap.add_argument("--out", default=os.path.join(HERE, "reports"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    core_log = a.core_log
    if a.target:
        base = a.target
    else:
        core_log = core_log or os.path.join(a.out, "core_test_logs.txt")
        _srv, base = interworking_core.start_server(0, core_log, a.fault)
    print(f"Target: {base}")
    results = run_all(base)

    log_summary = ""
    if core_log and os.path.exists(core_log):
        log_tracker.write_report(log_tracker.parse(core_log), os.path.join(a.out, "summary_report.txt"))
        log_summary = open(os.path.join(a.out, "summary_report.txt")).read()
    ls = landslide_report.analyze(a.landslide_csv) if a.landslide_csv else None

    json.dump(results, open(os.path.join(a.out, "results.json"), "w"), indent=2)
    open(os.path.join(a.out, "report.html"), "w").write(render_html(results, base, a.fault, log_summary, ls))

    for r in results:
        print(f"{r['status']:<5} {r['id']:<10} {r['title']}" + (f"\n        -> {r['message']}" if r["message"] else ""))
        if r.get("metrics"):
            print("        KPIs: " + ", ".join(f"{k}={v}" for k, v in r["metrics"].items()))
    if ls:
        print("\nLandslide export:\n" + landslide_report.to_text(ls))
    bad = sum(r["status"] != "PASS" for r in results)
    print(f"\n{len(results) - bad}/{len(results)} passed. Report: {os.path.join(a.out, 'report.html')}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
