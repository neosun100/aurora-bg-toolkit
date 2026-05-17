#!/usr/bin/env python3
"""
v10-extract-data.py — aggregate v10 measurements into dashboard JSON.

Walks e2e-results/v10-{bg,failover,reboot}-{1..10}_*/test-v10_v10-final/
and combines stats-gap.json + meta.json into a single JSON consumable by
the v10-only dashboard view.

Output: dashboard/data/v10-only.json

Schema:
{
  "schema": "v10-only-1",
  "experiment": "v10-production",
  "generatedAt": "...",
  "config": {
      "name": "v10-final",
      "yaml": "<full yaml content>",
      "highlights": [
          ["DNS TTL",      "5s",      "winner from v9"],
          ["connectTimeout","1000ms",  "v4-current default"],
          ...
      ]
  },
  "scenarios": {
      "blue-green": {
          "n": 10, "min": ..., "median": ..., "p95": ..., "max": ...,
          "samples": [3800, 3900, ...],
          "rounds": [{"round":1, "writeMaxMs": 3800, "readMaxMs": 3800, "runId": "...", "directory": "..."}, ...]
      },
      "failover": {...},
      "reboot": {...}
  }
}
"""
from __future__ import annotations

import datetime as dt
import json
import statistics as st
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO_ROOT / "e2e-results"
CONFIG_FILE = REPO_ROOT / "configs/v10-final.yaml"
OUT_FILE = REPO_ROOT / "dashboard/data/v10-only.json"

SCENARIOS = [
    ("blue-green", "v10-bg-"),
    ("failover",   "v10-failover-"),
    ("reboot",     "v10-reboot-"),
]

# Highlights: 10 most user-relevant parameters from v10-final.yaml + their rationale
HIGHLIGHTS = [
    ("JVM DNS TTL",            "5 s",       "v9 H1 winner — drops Reboot 5s → 0s"),
    ("connectTimeout",         "1000 ms",   "v4-current; v9 H3 (lower) regresses Failover"),
    ("socketTimeout",          "3000 ms",   "v4-current; balanced query timeout"),
    ("failureDetectionTime",   "6000 ms",   "v4-current; tighter than wrapper default (30s)"),
    ("failureDetectionInterval","1000 ms",  "v4-current"),
    ("failureDetectionCount",  "3",         "v4-current; avoids false positives"),
    ("HikariCP pool max/min",  "50 / 50",   "production-grade; was 10/10 in low-load v4"),
    ("HikariCP maxLifetime",   "60 s",      "v4-current; v9 H5 (300s) showed no benefit"),
    ("connectionInitSql",      "SELECT 1",  "v4-current; v9 H2 (null) showed no benefit"),
    ("Workload load",          "1280 ops/s", "64 threads × 50ms (production)"),
    ("STATS reporter",         "10 Hz",     "±100ms precision (vs 1Hz ±500ms)"),
    ("wrapper version",        "4.0.1",     "latest stable; v9 H4 says no diff vs 4.0.0"),
]


def collect_one(scenario: str, prefix: str) -> dict:
    """Walk e2e-results/<prefix>R_TS/test-v10_v10-final/ and return aggregated stats."""
    rounds = []
    samples = []
    read_samples = []
    seen_rounds = set()
    # Sort newest first per round so we pick the latest re-run if any
    cell_dirs = sorted(RESULTS_DIR.glob(f"{prefix}*/test-v10_v10-final"), reverse=True)
    for cell in cell_dirs:
        gap = cell / "stats-gap.json"
        meta = cell / "meta.json"
        if not gap.exists() or not meta.exists():
            continue
        m = json.loads(meta.read_text())
        round_no = m.get("round")
        if round_no in seen_rounds:
            continue  # already have a newer one for this round
        seen_rounds.add(round_no)
        g = json.loads(gap.read_text())
        wmax = g["summary"]["writeMaxMs"]
        rmax = g["summary"]["readMaxMs"]
        rounds.append({
            "round": round_no,
            "writeMaxMs": wmax,
            "readMaxMs": rmax,
            "runId": m.get("runId"),
            "wrapperJar": m.get("wrapperJar"),
            "directory": str(cell.relative_to(REPO_ROOT)),
            "statsCount": g.get("statsCount", 0),
            "detectedPeriodMs": g.get("detectedPeriodMs"),
        })
        samples.append(wmax)
        read_samples.append(rmax)

    rounds.sort(key=lambda x: x["round"] or 0)

    def stats(xs):
        if not xs:
            return {"n": 0}
        s = sorted(xs)
        return {
            "n": len(s),
            "min": s[0],
            "max": s[-1],
            "mean": int(st.mean(s)),
            "median": int(st.median(s)),
            "p95": int(percentile(s, 95)),
            "stdev": int(st.pstdev(s)) if len(s) > 1 else 0,
            "q1": int(percentile(s, 25)),
            "q3": int(percentile(s, 75)),
        }

    return {
        "scenario": scenario,
        "rounds": rounds,
        "samples": samples,
        "readSamples": read_samples,
        "writeStats": stats(samples),
        "readStats": stats(read_samples),
    }


def percentile(sorted_xs, p):
    if not sorted_xs:
        return 0
    if len(sorted_xs) == 1:
        return sorted_xs[0]
    k = (len(sorted_xs) - 1) * (p / 100)
    lo = int(k)
    hi = min(lo + 1, len(sorted_xs) - 1)
    frac = k - lo
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * frac


def main() -> int:
    yaml_text = CONFIG_FILE.read_text() if CONFIG_FILE.exists() else "# v10-final.yaml not found"
    by_scenario = {}
    for sc, prefix in SCENARIOS:
        by_scenario[sc] = collect_one(sc, prefix)
        n = by_scenario[sc]["writeStats"].get("n", 0)
        med = by_scenario[sc]["writeStats"].get("median", "—")
        print(f"  {sc:12s} n={n:2d}  median writeMaxMs={med}", file=sys.stderr)

    out = {
        "schema": "v10-only-1",
        "experiment": "v10-production",
        "generatedAt": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "config": {
            "name": "v10-final",
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
