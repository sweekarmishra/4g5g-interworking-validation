"""Analyse a Spirent Landslide results export (CSV) against pass criteria.

IMPORTANT: Landslide column names differ by version and by how a report is
exported. This tool therefore reads the names from landslide/landslide_columns.json.
Edit that file to match the headers in YOUR export. If a column is missing, the
tool stops and lists the headers it found.

Usage:
    python3 tools/landslide_report.py export.csv
        [--columns landslide/landslide_columns.json]
        [--criteria landslide/landslide_criteria.json]
"""
import argparse
import csv
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def load_json(p):
    with open(p) as f:
        return json.load(f)


def num(x):
    return float(str(x).replace(",", "").replace("%", "").strip() or 0)


def analyze(csv_path, columns_path=None, criteria_path=None):
    cols = load_json(columns_path or os.path.join(ROOT, "landslide", "landslide_columns.json"))
    crit = load_json(criteria_path or os.path.join(ROOT, "landslide", "landslide_criteria.json"))
    with open(csv_path, newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit("CSV has no data rows")
    missing = [v for k, v in cols.items() if not k.startswith("_") and v not in rows[0]]
    if missing:
        raise SystemExit(f"Column(s) not found in CSV: {missing}\nHeaders found: {list(rows[0].keys())}\n"
                         "Edit landslide/landslide_columns.json to match.")
    attempts = sum(num(r[cols["attempts"]]) for r in rows)
    successes = sum(num(r[cols["successes"]]) for r in rows)
    setup = sorted(num(r[cols["avg_time_ms"]]) for r in rows)
    p95 = setup[max(0, math.ceil(0.95 * len(setup)) - 1)]
    out = {
        "intervals": len(rows), "attempts": int(attempts), "successes": int(successes),
        "failures": int(attempts - successes),
        "success_rate_pct": round(100.0 * successes / attempts, 3) if attempts else 0.0,
        "avg_time_ms": round(sum(setup) / len(setup), 1), "p95_of_interval_avgs_ms": p95,
        "max_interval_avg_ms": max(setup),
    }
    out["checks"] = [
        ("Success rate >= %.2f%%" % crit["min_success_rate_pct"], out["success_rate_pct"] >= crit["min_success_rate_pct"],
         f"{out['success_rate_pct']}%"),
        ("Average time <= %d ms" % crit["max_avg_time_ms"], out["avg_time_ms"] <= crit["max_avg_time_ms"],
         f"{out['avg_time_ms']} ms"),
        ("P95 of interval averages <= %d ms" % crit["max_p95_time_ms"], p95 <= crit["max_p95_time_ms"], f"{p95} ms"),
    ]
    out["passed"] = all(ok for _n, ok, _v in out["checks"])
    return out


def to_text(r):
    lines = [f"Intervals: {r['intervals']}   Attempts: {r['attempts']}   Successes: {r['successes']}   "
             f"Failures: {r['failures']}",
             f"Success rate: {r['success_rate_pct']}%   Avg time: {r['avg_time_ms']} ms   "
             f"P95 of interval averages: {r['p95_of_interval_avgs_ms']} ms", ""]
    lines += [f"  [{'PASS' if ok else 'FAIL'}] {name}  (measured {val})" for name, ok, val in r["checks"]]
    lines.append(f"\nOverall: {'PASS' if r['passed'] else 'FAIL'}")
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--columns")
    ap.add_argument("--criteria")
    a = ap.parse_args()
    res = analyze(a.csv, a.columns, a.criteria)
    print(to_text(res))
    sys.exit(0 if res["passed"] else 1)
