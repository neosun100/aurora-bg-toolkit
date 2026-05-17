#!/usr/bin/env python3
"""
Synthesize analysis.json for v9 cells from existing meta.json + stats-gap.json.

Why: v9 ran analyze-stats-gap.py (better signal under wrapper 4.x) but did NOT
run the older analyze-logs.py (which produces analysis.json that compare-runs
expects). Rather than re-run the slower analyze-logs.py against 132 logs, we
adapt the stats-gap output into the same JSON shape compare-runs.py needs.

Usage:
    synthesize-v9-analysis.py e2e-results/
"""
from __future__ import annotations
import json
import sys
from pathlib import Path


def wrapper_version_from_jar(jar: str) -> str:
    if "w401" in jar: return "4.0.1"
    if "w400" in jar: return "4.0.0"
    if "w330" in jar: return "3.3.0"
    return "unknown"


def main(root: Path) -> int:
    cells = list(root.rglob("meta.json"))
    written = 0
    skipped = 0
    for meta_path in cells:
        gap_path = meta_path.with_name("stats-gap.json")
        analysis_path = meta_path.with_name("analysis.json")
        if not gap_path.exists():
            skipped += 1
            continue
        if analysis_path.exists():
            # respect existing — historical runs already had analyze-logs.py
            continue
        meta = json.loads(meta_path.read_text())
        gap = json.loads(gap_path.read_text())
        write_max = gap["summary"]["writeMaxMs"]
        read_max = gap["summary"]["readMaxMs"]
        windows = []
        for w in gap.get("writeGaps", []):
            windows.append({
                "kind": "WRITE_DOWN",
                "start": w["start"],
                "end": w["end"],
                "durationMs": w["durationMs"],
            })
        analysis = {
            "schema": 1,
            "generatedAt": "2026-05-17T05:40:00+08:00",
            "run": {
                "runId": meta.get("runId", meta_path.parent.name),
                "config": meta.get("config", "unknown"),
                "scenario": meta.get("scenario"),
                "round": meta.get("round"),
            },
            "summary": {
                "logCount": 1,
                "totalEvents": gap.get("statsCount", 0),
            },
            "logs": [{
                "logFile": str(gap_path.parent / "ec2_wrapper.log"),
                "platform": "ec2",
                "wrapperVersion": wrapper_version_from_jar(meta.get("wrapperJar", "")),
                "eventCount": gap.get("statsCount", 0),
                "downtimes": {
                    "writeMaxMs": write_max,
                    "readMaxMs": read_max,
                },
                "windows": windows,
            }],
        }
        analysis_path.write_text(json.dumps(analysis, indent=2))
        written += 1
    print(f"Synthesized {written} analysis.json (skipped {skipped} without stats-gap.json)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: synthesize-v9-analysis.py <e2e-results-root>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(Path(sys.argv[1])))
