#!/usr/bin/env python3
"""
Generate a Markdown report from one or more analysis.json files
produced by analyze-logs.py.

Usage:
    generate-report.py <analysis.json> [<analysis.json> ...] -o REPORT.md

When multiple analyses are given, the report contains a comparison table
across configs/runs.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any


def load_analyses(paths: list[Path]) -> list[dict]:
    out = []
    for p in paths:
        if not p.exists():
            raise SystemExit(f"missing: {p}")
        out.append(json.loads(p.read_text()))
    return out


def fmt_ms(ms: int) -> str:
    if ms == 0:
        return "—"
    return f"{ms / 1000:.2f}s"


def render(analyses: list[dict]) -> str:
    lines: list[str] = []
    lines.append("# Aurora BG Toolkit — Test Run Report")
    lines.append("")
    if analyses:
        lines.append(f"Generated: {analyses[0]['generatedAt']}")
        lines.append("")

    # ─── Top-level summary table ──────────────────────────────────────────
    lines.append("## Run summary")
    lines.append("")
    lines.append("| Run ID | Config | EC2 wrapper3 ↓write | EC2 wrapper4 ↓write | EKS wrapper3 ↓write | EKS wrapper4 ↓write |")
    lines.append("|---|---|---|---|---|---|")
    for a in analyses:
        run = a["run"]
        rid = run.get("runId", "?")
        cfg = run.get("config", "?")
        cells = {("ec2", "3.3.0"): "—", ("ec2", "4.0.0"): "—",
                 ("eks", "3.3.0"): "—", ("eks", "4.0.0"): "—"}
        for log in a["logs"]:
            key = (log["platform"], log["wrapperVersion"])
            if key in cells:
                cells[key] = fmt_ms(log["downtimes"]["writeMaxMs"])
        lines.append(
            f"| `{rid}` | {cfg} | {cells[('ec2','3.3.0')]} | {cells[('ec2','4.0.0')]} "
            f"| {cells[('eks','3.3.0')]} | {cells[('eks','4.0.0')]} |"
        )
    lines.append("")

    # ─── Per-run detail ────────────────────────────────────────────────────
    for a in analyses:
        run = a["run"]
        lines.append(f"## Detail: `{run.get('runId', '?')}`")
        lines.append("")
        if "config" in run:
            lines.append(f"- Config: `{run['config']}`")
        if "scenario" in run:
            lines.append(f"- Scenario: {run['scenario']}")
        if "round" in run:
            lines.append(f"- Round: {run['round']}")
        if "scenarioStartedAt" in run:
            lines.append(f"- Started at: {run['scenarioStartedAt']}")
        lines.append(f"- Logs found: {a['summary']['logCount']}")
        lines.append(f"- Total events: {a['summary']['totalEvents']}")
        lines.append("")
        lines.append("| Log file | Platform | Wrapper | Events | ↓write max | ↓read max | Windows |")
        lines.append("|---|---|---|---|---|---|---|")
        for log in a["logs"]:
            lines.append(
                f"| `{log['logFile']}` | {log['platform']} | {log['wrapperVersion']} "
                f"| {log['eventCount']} | {fmt_ms(log['downtimes']['writeMaxMs'])} "
                f"| {fmt_ms(log['downtimes']['readMaxMs'])} "
                f"| {len(log['windows'])} |"
            )
        lines.append("")

        # Windows detail (collapsed)
        for log in a["logs"]:
            if not log["windows"]:
                continue
            lines.append(f"<details><summary>{log['logFile']} — windows</summary>")
            lines.append("")
            lines.append("| Kind | Start | End | Duration |")
            lines.append("|---|---|---|---|")
            for w in log["windows"]:
                lines.append(
                    f"| {w['kind']} | {w['start']} | {w['end']} | {fmt_ms(w['durationMs'])} |"
                )
            lines.append("")
            lines.append("</details>")
            lines.append("")

    # ─── Cross-run statistics (if more than one) ──────────────────────────
    if len(analyses) > 1:
        lines.append("## Statistics across runs")
        lines.append("")

        # Group by config
        by_cfg: dict[str, list[int]] = {}
        for a in analyses:
            cfg = a["run"].get("config", "unknown")
            for log in a["logs"]:
                if log["downtimes"]["writeMaxMs"] > 0:
                    by_cfg.setdefault(cfg, []).append(log["downtimes"]["writeMaxMs"])

        lines.append("| Config | Runs | min | median | mean | max | stdev |")
        lines.append("|---|---|---|---|---|---|---|")
        for cfg, samples in sorted(by_cfg.items()):
            if not samples:
                continue
            lines.append(
                f"| {cfg} | {len(samples)} | {fmt_ms(min(samples))} "
                f"| {fmt_ms(int(statistics.median(samples)))} "
                f"| {fmt_ms(int(statistics.mean(samples)))} "
                f"| {fmt_ms(max(samples))} "
                f"| {fmt_ms(int(statistics.pstdev(samples))) if len(samples) > 1 else '—'} |"
            )
        lines.append("")

    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", type=Path, help="one or more analysis.json files")
    ap.add_argument("-o", "--output", type=Path, default=Path("REPORT.md"),
                    help="output Markdown file (default: REPORT.md)")
    args = ap.parse_args()

    analyses = load_analyses(args.inputs)
    text = render(analyses)
    args.output.write_text(text)
    print(f"Wrote {args.output} ({len(text)} chars across {len(analyses)} runs)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
