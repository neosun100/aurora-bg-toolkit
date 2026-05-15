#!/usr/bin/env python3
"""
analyze-stats-gap.py — measure downtime as zero-throughput windows in STATS lines.

This is the most reliable downtime metric for wrapper 4.0+ where application-
layer exceptions are masked by the bg plugin's enhanced switchover handling.

Two parsing modes:
  1. Timestamped logs (preferred): "2026-05-15 15:20:00.000 ... STATS write_ok=N"
     Window durations come from real timestamps.
  2. Plain logs (fallback): "[bg-stats-reporter] ... STATS write_ok=N"
     Window durations come from counting STATS lines (the reporter runs at
     1Hz, so 1 STATS line ≈ 1 second). Less precise but functional.

The script auto-detects which mode to use.

Usage:
    analyze-stats-gap.py <log-file>
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

TIMESTAMPED_RE = re.compile(
    r"^\[?(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\]?"
    r".*?STATS\b.*?write_ok=(?P<wok>\d+).*?read_ok=(?P<rok>\d+)"
)
PLAIN_RE = re.compile(
    r"STATS\b.*?write_ok=(?P<wok>\d+).*?read_ok=(?P<rok>\d+)"
)
TS_FMT = "%Y-%m-%d %H:%M:%S.%f"


def parse_stats(path: Path):
    """Returns (samples, mode) where samples is a list of (ts_or_index, wok, rok)
    and mode is "timestamped" or "indexed"."""
    timestamped = []
    indexed = []
    with path.open("r", errors="replace") as f:
        for line in f:
            m = TIMESTAMPED_RE.search(line)
            if m:
                timestamped.append((
                    dt.datetime.strptime(m["ts"], TS_FMT),
                    int(m["wok"]),
                    int(m["rok"]),
                ))
                continue
            # Fallback: any STATS line (no timestamp)
            m2 = PLAIN_RE.search(line)
            if m2:
                indexed.append((
                    len(indexed),  # use line index as 1-second proxy
                    int(m2["wok"]),
                    int(m2["rok"]),
                ))
    if timestamped:
        return timestamped, "timestamped"
    return indexed, "indexed"


def gaps(samples, key_idx, label, mode):
    """Find runs of samples where sample[key_idx] == 0; return list of windows.

    In timestamped mode, durations are computed from actual timestamps.
    In indexed mode, each sample = 1 second (the reporter is 1Hz)."""
    out = []
    streak_start_key = None
    last_key = None
    for s in samples:
        key = s[0]
        v = s[key_idx]
        if v == 0:
            if streak_start_key is None:
                streak_start_key = key
            last_key = key
        else:
            if streak_start_key is not None and last_key is not None:
                if mode == "timestamped":
                    duration_ms = int((last_key - streak_start_key).total_seconds() * 1000)
                    start_iso = streak_start_key.isoformat(timespec="milliseconds")
                    end_iso = last_key.isoformat(timespec="milliseconds")
                else:
                    duration_ms = (last_key - streak_start_key + 1) * 1000
                    start_iso = f"sample#{streak_start_key}"
                    end_iso = f"sample#{last_key}"
                # Filter trivial 0-duration single-point gaps for the timestamped case
                if duration_ms > 0 or mode == "indexed":
                    out.append({
                        "kind": label,
                        "start": start_iso,
                        "end": end_iso,
                        "durationMs": duration_ms,
                    })
            streak_start_key = None
            last_key = None
    # Trailing streak
    if streak_start_key is not None and last_key is not None:
        if mode == "timestamped":
            duration_ms = int((last_key - streak_start_key).total_seconds() * 1000)
        else:
            duration_ms = (last_key - streak_start_key + 1) * 1000
        if duration_ms > 0:
            out.append({"kind": label, "start": str(streak_start_key), "end": str(last_key), "durationMs": duration_ms})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log", type=Path)
    args = ap.parse_args()

    samples, mode = parse_stats(args.log)
    if not samples:
        print(json.dumps({"error": "no STATS lines found", "log": str(args.log)}))
        return 1

    write_gaps = gaps(samples, 1, "WRITE_GAP", mode)
    read_gaps = gaps(samples, 2, "READ_GAP", mode)
    out = {
        "schema": 2,
        "log": str(args.log),
        "parseMode": mode,
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
