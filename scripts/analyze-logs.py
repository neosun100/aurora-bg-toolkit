#!/usr/bin/env python3
"""
Parse Aurora BG Toolkit log files and extract downtime windows.

Mirrors the Java LogParser — same regexes, same window-derivation logic, same
trailing-streak handling. Kept in Python so the analysis pipeline runs without
needing the JVM.

Usage:
    analyze-logs.py <log-dir> [-o OUTPUT_JSON]

A log dir conventionally looks like:
    test-04_v4-current_20260516_120000/
        ec2_wrapper3.log
        ec2_wrapper4.log
        eks_wrapper3.log
        eks_wrapper4.log
        meta.json   (optional — written by run-test.sh)

The default output is <log-dir>/analysis.json.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Iterable

# ─────────────────────────────────────────────────────────────────────────────
# Parsing primitives
# ─────────────────────────────────────────────────────────────────────────────

# Two timestamp formats are supported:
#   1. SLF4J simple-logger default:  2026-05-14 13:37:30.123
#   2. Legacy V0..V4 println style:  [2026-05-14 13:37:30.123]
TS_RE = r"\[?(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\]?"

FAIL_RE = re.compile(
    TS_RE + r".*?\b(?P<op>WRITE_FAIL|READ_FAIL)\b.*?\bconsecutive=(?P<count>\d+)"
)
RECOVERED_RE = re.compile(
    TS_RE + r".*?\b(?P<op>WRITE_RECOVERED|READ_RECOVERED)\b.*?after (?P<count>\d+) consecutive failures"
)
TS_FORMATS = ["%Y-%m-%d %H:%M:%S.%f"]


def parse_ts(s: str) -> dt.datetime:
    # SLF4J prints microseconds as 3 digits; Python's %f wants 6 digits, but
    # strptime tolerates 3-6 digits silently in practice.
    for f in TS_FORMATS:
        try:
            return dt.datetime.strptime(s, f)
        except ValueError:
            continue
    raise ValueError(f"unparseable timestamp: {s}")


@dataclasses.dataclass
class Event:
    ts: dt.datetime
    kind: str          # WRITE_FAIL / READ_FAIL / WRITE_RECOVERED / READ_RECOVERED
    consecutive: int


@dataclasses.dataclass
class Window:
    kind: str          # WRITE_FAIL or READ_FAIL (the streak's domain)
    start: dt.datetime
    end: dt.datetime
    duration_ms: int

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "start": self.start.isoformat(timespec="milliseconds"),
            "end": self.end.isoformat(timespec="milliseconds"),
            "durationMs": self.duration_ms,
        }


def parse_line(line: str) -> Event | None:
    m = FAIL_RE.search(line)
    if m:
        return Event(parse_ts(m["ts"]), m["op"], int(m["count"]))
    m = RECOVERED_RE.search(line)
    if m:
        return Event(parse_ts(m["ts"]), m["op"], int(m["count"]))
    return None


def parse_file(path: Path) -> list[Event]:
    events: list[Event] = []
    with path.open("r", errors="replace") as f:
        for line in f:
            ev = parse_line(line)
            if ev is not None:
                events.append(ev)
    return events


def compute_windows(events: Iterable[Event]) -> list[Window]:
    out: list[Window] = []
    write_start: dt.datetime | None = None
    write_last: dt.datetime | None = None
    read_start: dt.datetime | None = None
    read_last: dt.datetime | None = None

    for e in events:
        if e.kind == "WRITE_FAIL":
            if write_start is None:
                write_start = e.ts
            write_last = e.ts
        elif e.kind == "READ_FAIL":
            if read_start is None:
                read_start = e.ts
            read_last = e.ts
        elif e.kind == "WRITE_RECOVERED" and write_start is not None:
            ms = int((e.ts - write_start).total_seconds() * 1000)
            out.append(Window("WRITE_FAIL", write_start, e.ts, ms))
            write_start = None
            write_last = None
        elif e.kind == "READ_RECOVERED" and read_start is not None:
            ms = int((e.ts - read_start).total_seconds() * 1000)
            out.append(Window("READ_FAIL", read_start, e.ts, ms))
            read_start = None
            read_last = None

    # Trailing streaks: bound by the last fail seen.
    if write_start and write_last and write_last != write_start:
        ms = int((write_last - write_start).total_seconds() * 1000)
        out.append(Window("WRITE_FAIL", write_start, write_last, ms))
    if read_start and read_last and read_last != read_start:
        ms = int((read_last - read_start).total_seconds() * 1000)
        out.append(Window("READ_FAIL", read_start, read_last, ms))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Discovery
# ─────────────────────────────────────────────────────────────────────────────

# A log filename like "ec2_wrapper3.log" tells us platform + wrapper version
LOGNAME_RE = re.compile(r"(?P<platform>ec2|eks)_wrapper(?P<wrapper>\d+)\.log")


def detect_log_metadata(filename: str) -> dict[str, str] | None:
    m = LOGNAME_RE.match(filename)
    if not m:
        return None
    wrapper_short = m.group("wrapper")
    wrapper_long = {"3": "3.3.0", "4": "4.0.0", "5": "4.0.1"}.get(wrapper_short, wrapper_short)
    return {
        "platform": m.group("platform"),
        "wrapperVersion": wrapper_long,
    }


def detect_run_metadata(run_dir: Path) -> dict[str, str | int]:
    """
    Try to infer run metadata from:
      1. meta.json in the run dir (preferred, written by run-test.sh)
      2. Otherwise, fall back to parsing the directory name. We expect names
         like 'test-04_v4-current_20260516_120000' or 'test-04_v4_20260514_172418'.
    """
    meta_file = run_dir / "meta.json"
    if meta_file.exists():
        return json.loads(meta_file.read_text())

    name = run_dir.name
    parts = name.split("_")
    out: dict[str, str | int] = {"runId": name}
    if len(parts) >= 4:
        out["clusterTag"] = parts[0]                # test-04
        out["config"] = parts[1]                     # v4-current  /  v4
        out["scenarioStartedAt"] = "_".join(parts[-2:])
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────


def analyze(run_dir: Path) -> dict:
    if not run_dir.is_dir():
        raise SystemExit(f"not a directory: {run_dir}")

    run_meta = detect_run_metadata(run_dir)
    logs: list[dict] = []
    for log_file in sorted(run_dir.glob("*.log")):
        log_meta = detect_log_metadata(log_file.name)
        if log_meta is None:
            print(f"  skipping (unrecognized filename): {log_file.name}", file=sys.stderr)
            continue
        events = parse_file(log_file)
        windows = compute_windows(events)
        logs.append({
            "logFile": log_file.name,
            "platform": log_meta["platform"],
            "wrapperVersion": log_meta["wrapperVersion"],
            "events": [
                {"ts": e.ts.isoformat(timespec="milliseconds"),
                 "kind": e.kind,
                 "consecutive": e.consecutive}
                for e in events
            ],
            "eventCount": len(events),
            "windows": [w.as_dict() for w in windows],
            "downtimes": {
                "writeMaxMs": max((w.duration_ms for w in windows if w.kind == "WRITE_FAIL"), default=0),
                "readMaxMs":  max((w.duration_ms for w in windows if w.kind == "READ_FAIL"),  default=0),
            },
        })

    return {
        "schema": 1,
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "run": run_meta,
        "logs": logs,
        "summary": {
            "logCount": len(logs),
            "totalEvents": sum(l["eventCount"] for l in logs),
            "writeMaxMs": max((l["downtimes"]["writeMaxMs"] for l in logs), default=0),
            "readMaxMs":  max((l["downtimes"]["readMaxMs"]  for l in logs), default=0),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir", type=Path, help="run directory containing *.log files")
    ap.add_argument("-o", "--output", type=Path, default=None,
                    help="output JSON path (default: <run_dir>/analysis.json)")
    args = ap.parse_args()

    result = analyze(args.run_dir)
    output = args.output or (args.run_dir / "analysis.json")
    output.write_text(json.dumps(result, indent=2))
    print(f"Wrote {output}", file=sys.stderr)

    summary = result["summary"]
    print(f"  log_count={summary['logCount']}  total_events={summary['totalEvents']}")
    print(f"  write_max={summary['writeMaxMs']}ms  read_max={summary['readMaxMs']}ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
