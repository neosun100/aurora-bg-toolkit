#!/usr/bin/env python3
"""
Aggregate multiple analysis.json files (each one round of one config) into a
single dashboard JSON consumable by dashboard/index.html, plus a multi-run
comparison summary.

Usage:
    compare-runs.py <root-dir> -o dashboard/data/runs.json

<root-dir> is expected to contain one analysis.json per subdirectory (i.e.
one per test run). Each subdirectory's name is used as the run ID.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


def discover_analyses(root: Path) -> list[tuple[Path, dict]]:
    out: list[tuple[Path, dict]] = []
    for p in sorted(root.rglob("analysis.json")):
        try:
            out.append((p, json.loads(p.read_text())))
        except json.JSONDecodeError as e:
            print(f"WARN: skipping invalid JSON {p}: {e}", file=sys.stderr)
    return out


def aggregate(analyses: list[tuple[Path, dict]]) -> dict[str, Any]:
    runs: list[dict] = []
    write_samples_by_cfg: dict[str, list[int]] = defaultdict(list)
    read_samples_by_cfg: dict[str, list[int]] = defaultdict(list)
    write_samples_by_combo: dict[str, list[int]] = defaultdict(list)

    for path, a in analyses:
        run_meta = a["run"]
        cfg = run_meta.get("config", "unknown")
        run_entry = {
            "id": run_meta.get("runId", path.parent.name),
            "config": cfg,
            "scenario": run_meta.get("scenario"),
            "round": run_meta.get("round"),
            "startedAt": run_meta.get("scenarioStartedAt"),
            "logs": [],
        }
        for log in a["logs"]:
            run_entry["logs"].append({
                "platform": log["platform"],
                "wrapperVersion": log["wrapperVersion"],
                "eventCount": log["eventCount"],
                "writeMaxMs": log["downtimes"]["writeMaxMs"],
                "readMaxMs": log["downtimes"]["readMaxMs"],
                "windows": log["windows"],
            })
            wm = log["downtimes"]["writeMaxMs"]
            rm = log["downtimes"]["readMaxMs"]
            if wm > 0:
                write_samples_by_cfg[cfg].append(wm)
                combo = f"{log['platform']}_w{log['wrapperVersion']}"
                write_samples_by_combo[combo].append(wm)
            if rm > 0:
                read_samples_by_cfg[cfg].append(rm)
        runs.append(run_entry)

    def stats(samples: list[int]) -> dict:
        if not samples:
            return {"count": 0}
        return {
            "count": len(samples),
            "min": min(samples),
            "median": int(statistics.median(samples)),
            "mean": int(statistics.mean(samples)),
            "max": max(samples),
            "p95": int(_percentile(samples, 95)),
            "stdev": int(statistics.pstdev(samples)) if len(samples) > 1 else 0,
        }

    return {
        "schema": 1,
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "runs": runs,
        "statsByConfig": {
            cfg: {"write": stats(samples), "read": stats(read_samples_by_cfg.get(cfg, []))}
            for cfg, samples in write_samples_by_cfg.items()
        },
        "statsByCombo": {
            combo: stats(samples)
            for combo, samples in write_samples_by_combo.items()
        },
    }


def _percentile(samples: list[int], p: float) -> float:
    """Simple percentile (linear interpolation, no numpy dependency)."""
    if not samples:
        return 0.0
    s = sorted(samples)
    if len(s) == 1:
        return float(s[0])
    k = (len(s) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    frac = k - lo
    return s[lo] + (s[hi] - s[lo]) * frac


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", type=Path, help="directory containing per-run subdirectories")
    ap.add_argument("-o", "--output", type=Path, default=Path("dashboard/data/runs.json"),
                    help="output JSON for the dashboard")
    args = ap.parse_args()

    analyses = discover_analyses(args.root)
    if not analyses:
        raise SystemExit(f"no analysis.json files found under {args.root}")
    print(f"Found {len(analyses)} analyses under {args.root}", file=sys.stderr)

    out = aggregate(analyses)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, indent=2))
    print(f"Wrote {args.output}", file=sys.stderr)

    print()
    print(f"Configs: {len(out['statsByConfig'])}")
    for cfg, s in sorted(out["statsByConfig"].items()):
        ws = s["write"]
        if ws["count"] == 0:
            continue
        print(f"  {cfg:32s}  N={ws['count']:3d}  "
              f"min={ws['min']:5d}ms  median={ws['median']:5d}ms  "
              f"max={ws['max']:5d}ms  p95={ws['p95']:5d}ms  stdev={ws['stdev']:5d}ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
