#!/usr/bin/env python3
"""
v11-extract-data.py — aggregate v11 measurements into dashboard JSON.

Walks e2e-results/v11-*-test-v11-N-rR_*/test-v11-N_v11-final/ and combines
stats-gap.json + meta.json into a single JSON consumable by the v11 dashboard
view (which can use the same dashboard-v10.js since the schema matches).

Output: dashboard/data/v11-only.json

In addition to the per-scenario stats, includes per-cluster breakdown to
detect 5-cluster contention (i.e. is one cluster slower than others?).
"""
from __future__ import annotations

import datetime as dt
import json
import re
import statistics as st
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO_ROOT / "e2e-results"
CONFIG_FILE = REPO_ROOT / "configs/v11-final.yaml"
OUT_FILE = REPO_ROOT / "dashboard/data/v11-only.json"

SCENARIOS = ["blue-green", "failover", "reboot"]

HIGHLIGHTS = [
    ("Infrastructure",         "AWS CDK (Python)",       "v11 difference: full IaC, no bash"),
    ("Parallelism",            "5 clusters, 5 threads",  "BG provisioning runs in parallel — half the wall time"),
    ("JVM DNS TTL",            "5 s",                     "v9 H1 winner — drops Reboot 5s → 0s"),
    ("connectTimeout",         "1000 ms",                "v4-current; v9 H3 (lower) regresses Failover"),
    ("socketTimeout",          "3000 ms",                "v4-current; balanced query timeout"),
    ("failureDetectionTime",   "6000 ms",                "v4-current; tighter than wrapper default (30s)"),
    ("HikariCP pool",          "50 / 50",                "production-grade"),
    ("Workload",               "1280 ops/s × 5 cluster",  "5× parallel = 6400 ops/s aggregate"),
    ("STATS reporter",         "10 Hz",                  "±100ms precision"),
    ("wrapper",                "4.0.1",                  "latest stable"),
]


def percentile(xs, p):
    if not xs: return 0
    xs = sorted(xs)
    if len(xs) == 1: return xs[0]
    k = (len(xs) - 1) * (p / 100)
    lo = int(k); hi = min(lo + 1, len(xs) - 1); frac = k - lo
    return xs[lo] + (xs[hi] - xs[lo]) * frac


def stats(xs):
    if not xs: return {"n": 0}
    s = sorted(xs)
    return {
        "n": len(s),
        "min": s[0], "max": s[-1],
        "mean": int(st.mean(s)), "median": int(st.median(s)),
        "p95": int(percentile(s, 95)),
        "stdev": int(st.pstdev(s)) if len(s) > 1 else 0,
        "q1": int(percentile(s, 25)),
        "q3": int(percentile(s, 75)),
    }


def collect_one(scenario: str) -> dict:
    """Walk e2e-results/v11-{scenario}-test-v11-N-rR_*/test-v11-N_v11-final/."""
    rounds = []
    samples = []
    read_samples = []
    by_cluster = {}  # cluster_id -> [writeMs]
    seen_keys = set()  # de-dup by (cluster, round) — pick newest

    cell_dirs = sorted(
        RESULTS_DIR.glob(f"v11-{scenario}-test-v11-*/test-v11-*_v11-final"),
        reverse=True,
    )
    for cell in cell_dirs:
        gap = cell / "stats-gap.json"
        meta = cell / "meta.json"
        if not gap.exists() or not meta.exists():
            continue
        m = json.loads(meta.read_text())
        cluster = m.get("cluster") or m.get("runId", "").split("_")[0]
        rno = m.get("round")
        key = (cluster, rno)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        g = json.loads(gap.read_text())
        wmax = g["summary"]["writeMaxMs"]
        rmax = g["summary"]["readMaxMs"]
        rounds.append({
            "cluster": cluster, "round": rno,
            "writeMaxMs": wmax, "readMaxMs": rmax,
            "runId": m.get("runId"),
            "directory": str(cell.relative_to(REPO_ROOT)),
            "statsCount": g.get("statsCount"),
            "detectedPeriodMs": g.get("detectedPeriodMs"),
        })
        samples.append(wmax)
        read_samples.append(rmax)
        by_cluster.setdefault(cluster, []).append(wmax)

    rounds.sort(key=lambda x: (x["cluster"] or "", x["round"] or 0))

    return {
        "scenario": scenario,
        "rounds": rounds,
        "samples": samples,
        "readSamples": read_samples,
        "writeStats": stats(samples),
        "readStats": stats(read_samples),
        "byCluster": {c: stats(s) for c, s in by_cluster.items()},
    }


def main() -> int:
    yaml_text = CONFIG_FILE.read_text() if CONFIG_FILE.exists() else "# v11-final.yaml not found"
    by_scenario = {}
    for sc in SCENARIOS:
        by_scenario[sc] = collect_one(sc)
        n = by_scenario[sc]["writeStats"].get("n", 0)
        med = by_scenario[sc]["writeStats"].get("median", "—")
        print(f"  {sc:12s} n={n:2d}  median writeMaxMs={med}", file=sys.stderr)

    out = {
        "schema": "v11-cdk-1",
        "experiment": "v11-cdk-parallel",
        "generatedAt": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "config": {
            "name": "v11-final",
            "yamlPath": str(CONFIG_FILE.relative_to(REPO_ROOT)),
            "yaml": yaml_text,
            "highlights": [{"name": n, "value": v, "rationale": r} for n, v, r in HIGHLIGHTS],
        },
        "scenarios": by_scenario,
    }

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(out, indent=2))
    print(f"\nWrote {OUT_FILE}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
