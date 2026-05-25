#!/usr/bin/env python3
"""
v16-extract-matrix.py — aggregate v16 matrix sweep measurements.

Walks e2e-results/v17-* directories (each = one matrix run × scenario × round
× cluster) and produces a single dashboard JSON sliceable by:
  - Run ID (M1, M2, M3, M4, T2, T3)
  - Scenario (blue-green / failover / reboot)
  - Writer instance class (r7g.large / 2xlarge / 4xlarge / 8xlarge)
  - TPS tier (1280 / 2560 / 4000)
  - Cluster (test-v11-1 .. -5)

Output: dashboard/data/v17-matrix.json
"""
from __future__ import annotations

import datetime as dt
import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO_ROOT / "e2e-results"
OUT_FILE = REPO_ROOT / "dashboard/data/v17-matrix.json"


def percentile(xs: list[int], p: float) -> int:
    if not xs:
        return 0
    xs = sorted(xs)
    if len(xs) == 1:
        return int(xs[0])
    k = (len(xs) - 1) * (p / 100)
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    frac = k - lo
    return int(xs[lo] + (xs[hi] - xs[lo]) * frac)


def stats(xs: list[int]) -> dict:
    if not xs:
        return {"n": 0}
    s = sorted(xs)
    return {
        "n": len(s),
        "min": int(s[0]),
        "max": int(s[-1]),
        "mean": int(st.mean(s)),
        "median": int(st.median(s)),
        "p50": percentile(s, 50),
        "p75": percentile(s, 75),
        "p90": percentile(s, 90),
        "p95": percentile(s, 95),
        "p99": percentile(s, 99),
        "q1": percentile(s, 25),
        "q3": percentile(s, 75),
        "stdev": int(st.pstdev(s)) if len(s) > 1 else 0,
    }


def load_meta_and_gap(round_dir: Path) -> dict | None:
    """Walk into round_dir/{cluster}_{config}/ and load meta+gap. Returns None if invalid."""
    sub_dirs = [d for d in round_dir.iterdir() if d.is_dir()]
    if not sub_dirs:
        return None
    sub = sub_dirs[0]  # there's exactly one per round
    meta_f = sub / "meta.json"
    gap_f = sub / "stats-gap.json"
    if not (meta_f.exists() and gap_f.exists()):
        return None
    try:
        meta = json.loads(meta_f.read_text())
        gap = json.loads(gap_f.read_text())
        return {
            "meta": meta,
            "gap": gap,
            "directory": str(round_dir.relative_to(REPO_ROOT)),
        }
    except Exception:
        return None


def main():
    # Group rounds by (run_label, scenario) and by (writer_instance, tps, scenario)
    by_run = defaultdict(list)         # run_label → [round_data, ...]
    by_run_scenario = defaultdict(list)  # (run_label, scenario) → [...]
    run_metadata = {}                    # run_label → {writer, reader, client, tps}

    # All v17-* run dirs
    pattern = "v17-*-*"  # e.g. v17-M3-r7g4xl-tps1280-blue-green-test-v11-3-r1_2026...
    for round_dir in sorted(RESULTS_DIR.glob(pattern)):
        if not round_dir.is_dir():
            continue
        info = load_meta_and_gap(round_dir)
        if info is None:
            continue
        meta = info["meta"]
        run_label = meta.get("runLabel")
        if not run_label:
            continue

        scenario = meta.get("scenario", "?")
        wmax = info["gap"]["summary"].get("writeMaxMs", 0)
        rmax = info["gap"]["summary"].get("readMaxMs", 0)

        # v16 meta-write bug: meta.json's "tps" field was hard-coded to "1280" for
        # all runs. Reconstruct real workload TPS from "config" (e.g. "v17-tps4000"
        # → "4000") which IS reliably written.
        cfg = meta.get("config", "")
        real_tps = "1280"  # default
        if "tps" in cfg:
            real_tps = cfg.split("tps")[-1] or "1280"

        round_record = {
            "cluster": meta.get("cluster"),
            "round": meta.get("round"),
            "writeMaxMs": wmax,
            "readMaxMs": rmax,
            "runId": meta.get("runId"),
            "directory": info["directory"],
            "writerInstance": meta.get("writerInstance"),
            "readerInstance": meta.get("readerInstance"),
            "clientInstance": meta.get("clientInstance"),
            "tps": real_tps,
        }
        by_run[run_label].append(round_record)
        by_run_scenario[(run_label, scenario)].append(round_record)

        if run_label not in run_metadata:
            run_metadata[run_label] = {
                "writer_instance": meta.get("writerInstance"),
                "reader_instance": meta.get("readerInstance"),
                "client_instance": meta.get("clientInstance"),
                "tps_config": meta.get("config"),
                "tps": real_tps,
            }

    # Build per-run summaries (3 scenarios each)
    runs_summary = {}
    for run_label, rounds in by_run.items():
        per_scenario = {}
        for scenario in ("blue-green", "failover", "reboot"):
            scen_rounds = by_run_scenario.get((run_label, scenario), [])
            samples = [r["writeMaxMs"] for r in scen_rounds]
            read_samples = [r["readMaxMs"] for r in scen_rounds]
            per_scenario[scenario] = {
                "scenario": scenario,
                "rounds": sorted(scen_rounds, key=lambda r: (r["cluster"] or "", r["round"] or 0)),
                "samples": samples,
                "readSamples": read_samples,
                "writeStats": stats(samples),
                "readStats": stats(read_samples),
            }
        runs_summary[run_label] = {
            "label": run_label,
            "metadata": run_metadata.get(run_label, {}),
            "scenarios": per_scenario,
            "total_measurements": sum(
                per_scenario[s]["writeStats"].get("n", 0)
                for s in ("blue-green", "failover", "reboot")
            ),
        }

    # ── Cross-run aggregates ──
    # Instance sweep at TPS=1280
    instance_sweep = {}
    for run_label, rs in runs_summary.items():
        if rs["metadata"].get("tps") != "1280":
            continue
        wic = rs["metadata"].get("writer_instance")
        if not wic:
            continue
        instance_sweep[wic] = {
            "run_label": run_label,
            "writer_instance": wic,
            "scenarios": {
                s: rs["scenarios"][s]["writeStats"]
                for s in ("blue-green", "failover", "reboot")
            },
        }

    # TPS sweep at writer=r7g.8xlarge
    tps_sweep = {}
    for run_label, rs in runs_summary.items():
        if rs["metadata"].get("writer_instance") != "r7g.8xlarge":
            continue
        tps = rs["metadata"].get("tps", "?")
        tps_sweep[tps] = {
            "run_label": run_label,
            "tps": tps,
            "scenarios": {
                s: rs["scenarios"][s]["writeStats"]
                for s in ("blue-green", "failover", "reboot")
            },
        }

    out = {
        "schema": "v17-matrix-1",
        "experiment": "v17-reboot-deepdive-revalidation",
        "generatedAt": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "runs": runs_summary,
        "instance_sweep_at_1280_tps": instance_sweep,
        "tps_sweep_at_r7g8xl": tps_sweep,
    }

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(out, indent=2))
    print(f"\nWrote {OUT_FILE}", file=sys.stderr)
    print(f"  runs_found:                     {len(runs_summary)}", file=sys.stderr)
    print(f"  instance_sweep_at_1280:         {sorted(instance_sweep.keys())}", file=sys.stderr)
    print(f"  tps_sweep_at_r7g8xl:            {sorted(tps_sweep.keys())}", file=sys.stderr)
    for run_label, rs in sorted(runs_summary.items()):
        meds = [rs["scenarios"][s]["writeStats"].get("median", 0) for s in ("blue-green", "failover", "reboot")]
        ns = [rs["scenarios"][s]["writeStats"].get("n", 0) for s in ("blue-green", "failover", "reboot")]
        print(f"  {run_label:30s}  BG={meds[0]:>6}ms (n={ns[0]})  "
              f"FO={meds[1]:>6}ms (n={ns[1]})  "
              f"RB={meds[2]:>6}ms (n={ns[2]})", file=sys.stderr)

    # ── CSV exports ──
    # CSV 1: aggregate percentiles (1 row per run × scenario, 18 rows total)
    import csv
    PCSV = REPO_ROOT / "dashboard/data/v17-matrix-percentiles.csv"
    PCSV.parent.mkdir(parents=True, exist_ok=True)
    with PCSV.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "run_id", "run_label", "scenario",
            "writer_instance", "reader_instance", "client_instance",
            "tps", "tps_config",
            "n", "min_ms",
            "p50_ms", "p75_ms", "p90_ms", "p95_ms", "p99_ms", "max_ms",
            "mean_ms", "stdev_ms",
        ])
        # Order: M1, M2, M3, M4, T2, T3 then within each: BG, FO, RB
        ORDER = [
            ("M1", "v17-M1-r7glarge-tps1280"),
            ("M2", "v17-M2-r7g2xl-tps1280"),
            ("M3", "v17-M3-r7g4xl-tps1280"),
            ("M4", "v17-M4-r7g8xl-tps1280"),
            ("T2", "v17-T2-r7g8xl-tps2560"),
            ("T3", "v17-T3-r7g8xl-tps4000"),
        ]
        for run_id, run_label in ORDER:
            rs = runs_summary.get(run_label)
            if not rs:
                continue
            meta = rs["metadata"]
            for sc_key in ("blue-green", "failover", "reboot"):
                ws = rs["scenarios"][sc_key]["writeStats"]
                w.writerow([
                    run_id, run_label, sc_key,
                    meta.get("writer_instance", ""),
                    meta.get("reader_instance", ""),
                    meta.get("client_instance", ""),
                    meta.get("tps", ""),
                    meta.get("tps_config", ""),
                    ws.get("n", 0),
                    ws.get("min", 0),
                    ws.get("p50", 0),
                    ws.get("p75", 0),
                    ws.get("p90", 0),
                    ws.get("p95", 0),
                    ws.get("p99", 0),
                    ws.get("max", 0),
                    ws.get("mean", 0),
                    ws.get("stdev", 0),
                ])
    print(f"  Wrote CSV: {PCSV}", file=sys.stderr)

    # CSV 2: raw measurements (1 row per cluster × scenario, 88 rows)
    RCSV = REPO_ROOT / "dashboard/data/v17-raw-measurements.csv"
    with RCSV.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "run_id", "run_label", "scenario",
            "cluster", "round",
            "writer_instance", "reader_instance", "client_instance",
            "tps", "tps_config",
            "writeMaxMs", "readMaxMs",
            "result_dir",
        ])
        for run_id, run_label in ORDER:
            rs = runs_summary.get(run_label)
            if not rs:
                continue
            meta = rs["metadata"]
            for sc_key in ("blue-green", "failover", "reboot"):
                for r in rs["scenarios"][sc_key]["rounds"]:
                    w.writerow([
                        run_id, run_label, sc_key,
                        r.get("cluster", ""),
                        r.get("round", ""),
                        meta.get("writer_instance", ""),
                        meta.get("reader_instance", ""),
                        meta.get("client_instance", ""),
                        meta.get("tps", ""),
                        meta.get("tps_config", ""),
                        r.get("writeMaxMs", 0),
                        r.get("readMaxMs", 0),
                        r.get("directory", ""),
                    ])
    print(f"  Wrote CSV: {RCSV}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
