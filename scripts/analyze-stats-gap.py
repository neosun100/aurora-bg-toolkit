#!/usr/bin/env python3
"""
analyze-stats-gap.py — alternative downtime analysis based on STATS gaps.

While analyze-logs.py looks at WRITE_FAIL/RECOVERED markers (errors visible
to the application), this analyzer looks at the STATS lines emitted every
second by the workload reporter:

    STATS write_ok=N read_ok=N

A consecutive run of STATS lines where write_ok=0 (or read_ok=0) indicates
the workload was actually unable to make progress on that operation type,
even if no exception was thrown.

This is the more sensible measure for the WRAPPER 4.0+ enhanced mode where
bg plugin's SuspendConnectRouting silently parks new connections during
switchover instead of throwing exceptions visible to the workload.
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

STATS_RE = re.compile(
    r"^\[?(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\]?"
    r".*?STATS\b.*?write_ok=(?P<wok>\d+).*?read_ok=(?P<rok>\d+)"
)
TS_FMT = "%Y-%m-%d %H:%M:%S.%f"


def parse_stats(path: Path):
    out = []
    with path.open("r", errors="replace") as f:
        for line in f:
            m = STATS_RE.search(line)
            if m:
                out.append((
                    dt.datetime.strptime(m["ts"], TS_FMT),
                    int(m["wok"]),
                    int(m["rok"]),
                ))
    return out


def gaps(samples, key_idx, label):
    """Return list of (start_ts, end_ts, duration_s) where the metric was 0."""
    out = []
    in_gap_start = None
    last_ts = None
    for s in samples:
        ts = s[0]
        v = s[key_idx]
        if v == 0:
            if in_gap_start is None:
                in_gap_start = ts
            last_ts = ts
        else:
            if in_gap_start is not None and last_ts is not None and last_ts != in_gap_start:
                out.append({
                    "kind": label,
                    "start": in_gap_start.isoformat(timespec="milliseconds"),
                    "end": last_ts.isoformat(timespec="milliseconds"),
                    "durationMs": int((last_ts - in_gap_start).total_seconds() * 1000),
                })
            in_gap_start = None
            last_ts = None
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log", type=Path)
    args = ap.parse_args()

    samples = parse_stats(args.log)
    if not samples:
        print("no STATS lines found", file=sys.stderr)
        return 1

    write_gaps = gaps(samples, 1, "WRITE_GAP")
    read_gaps = gaps(samples, 2, "READ_GAP")
    out = {
        "schema": 1,
        "log": str(args.log),
        "statsCount": len(samples),
        "writeGaps": write_gaps,
        "readGaps": read_gaps,
        "summary": {
            "writeMaxMs": max((g["durationMs"] for g in write_gaps), default=0),
            "readMaxMs":  max((g["durationMs"] for g in read_gaps),  default=0),
            "writeGapCount": len(write_gaps),
            "readGapCount":  len(read_gaps),
        },
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
