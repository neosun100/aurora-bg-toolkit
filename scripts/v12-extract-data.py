#!/usr/bin/env python3
"""
v12-extract-data.py — aggregate v12 measurements into dashboard JSON.

Walks e2e-results/ and selects rounds where meta.json["config"] starts with
"v12-". Combines stats-gap.json + meta.json into a single JSON for the
dashboard.

Output: dashboard/data/v12-only.json
"""
from __future__ import annotations

import datetime as dt
import json
import statistics as st
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO_ROOT / "e2e-results"
CONFIG_FILE = REPO_ROOT / "configs/v12-aggressive-timeouts.yaml"
OUT_FILE = REPO_ROOT / "dashboard/data/v12-only.json"

SCENARIOS = ["blue-green", "failover", "reboot"]

HIGHLIGHTS = [
    {"name": "Config", "value": "v12-aggressive-timeouts",
     "rationale": "Tests 3 timeout reductions vs v11 baseline"},
    {"name": "connectTimeout", "value": "500ms (vs v11 1000ms)",
     "rationale": "H1: faster TCP timeout → faster BG recovery"},
    {"name": "failureDetectionTime", "value": "3000ms (vs v11 6000ms)",
     "rationale": "H2: faster EFM2 detection → faster FO"},
    {"name": "socketTimeout", "value": "1500ms (vs v11 3000ms)",
     "rationale": "H3: faster stale connection release → faster RB"},
    {"name": "Workload", "value": "1280 ops/s, pool=50",
     "rationale": "Same as v11"},
    {"name": "Parallelism", "value": "5 clusters in parallel",
     "rationale": "Same as v11 (CDK + ThreadPoolExecutor)"},
]


def stats(samples: list[int]) -> dict:
    if not samples:
        return {"n": 0}
    s = sorted(samples)
    n = len(s)
    return {
        "n": n,
        "min": s[0],
        "max": s[-1],
        "median": s[n // 2] if n % 2 == 1 else (s[n // 2 - 1] + s[n // 2]) / 2,
        "mean": round(sum(s) / n, 1),
        "q1": s[max(0, n // 4)],
        "q3": s[min(n - 1, 3 * n // 4)],
        "p95": s[min(n - 1, int(0.95 * n))],
        "stdev": round(st.stdev(s), 1) if n > 1 else 0,
    }


def main():
    rounds_by_scenario = {sc: [] for sc in SCENARIOS}
    rounds_by_cluster = {}  # cluster -> {sc -> list of writeMaxMs}

    for round_dir in sorted(RESULTS_DIR.iterdir()):
        if not round_dir.is_dir():
            continue
        # Look in subdirs (test-v11-N_v12-aggressive-timeouts/)
        for sub in round_dir.iterdir():
            if not sub.is_dir():
                continue
            meta_f = sub / "meta.json"
            gap_f = sub / "stats-gap.json"
            if not meta_f.exists() or not gap_f.exists():
                continue
            try:
                meta = json.loads(meta_f.read_text())
                if not meta.get("config", "").startswith("v12"):
                    continue
                gap = json.loads(gap_f.read_text())
                summary = gap.get("summary", {})
                w_ms = summary.get("writeMaxMs")
                r_ms = summary.get("readMaxMs")
                if w_ms is None:
                    continue
                sc = meta["scenario"]
                if sc not in rounds_by_scenario:
                    continue
                cluster = meta.get("cluster", "unknown")
                round_num = meta.get("round", 0)
                rec = {
                    "cluster": cluster,
                    "round": round_num,
                    "writeMaxMs": w_ms,
                    "readMaxMs": r_ms,
                    "runId": meta.get("runId", ""),
                    "directory": str(round_dir.relative_to(REPO_ROOT)),
                    "statsCount": summary.get("statsCount", 0),
                    "detectedPeriodMs": summary.get("detectedPeriodMs", 0),
                }
                rounds_by_scenario[sc].append(rec)
                rounds_by_cluster.setdefault(cluster, {}).setdefault(sc, []).append(w_ms)
            except Exception as e:
                print(f"  skip {sub}: {e}")

    out = {
        "schema": "v12-only-1",
        "experiment": "v12-aggressive-timeouts",
        "generatedAt": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "config": {
            "name": "v12-aggressive-timeouts",
            "yamlPath": "configs/v12-aggressive-timeouts.yaml",
            "yaml": CONFIG_FILE.read_text() if CONFIG_FILE.exists() else "(yaml not found)",
            "highlights": HIGHLIGHTS,
        },
        "scenarios": {},
    }

    for sc in SCENARIOS:
        rounds = sorted(rounds_by_scenario[sc], key=lambda r: (r["cluster"], r["round"]))
        samples = [r["writeMaxMs"] for r in rounds if r["writeMaxMs"] is not None]
        readSamples = [r["readMaxMs"] for r in rounds if r["readMaxMs"] is not None]
        # Per-cluster stats
        byCluster = {}
        for cl, scmap in rounds_by_cluster.items():
            cls = scmap.get(sc, [])
            if cls:
                byCluster[cl] = stats(cls)
        out["scenarios"][sc] = {
            "scenario": sc,
            "rounds": rounds,
            "samples": samples,
            "readSamples": readSamples,
            "writeStats": stats(samples),
            "readStats": stats(readSamples),
            "byCluster": byCluster,
        }

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(out, indent=2))
    print(f"✓ wrote {OUT_FILE}")
    print()
    for sc in SCENARIOS:
        ws = out["scenarios"][sc]["writeStats"]
        print(f"  {sc}: n={ws.get('n',0)} median={ws.get('median','—')}ms max={ws.get('max','—')}ms stdev={ws.get('stdev','—')}ms")


if __name__ == "__main__":
    main()
