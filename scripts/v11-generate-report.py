#!/usr/bin/env python3
"""
v11-generate-report.py — write the final v11 production report to disk.

Reads dashboard/data/v11-only.json (must exist; run v11-extract-data.py first)
and writes docs/REPORTS/2026-05-17-v11-cdk-parallel.md.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA = REPO_ROOT / "dashboard/data/v11-only.json"
OUT = REPO_ROOT / "docs/REPORTS/2026-05-17-v11-cdk-parallel.md"


def fmt_ms(x):
    if x in (None, "", "—") or not isinstance(x, (int, float)):
        return "—"
    if x == 0:
        return "0 ms"
    if x < 1000:
        return f"{int(x)} ms"
    return f"{x/1000:.2f} s"


def render(d: dict) -> str:
    sc = d["scenarios"]
    bg = sc["blue-green"]["writeStats"]
    fo = sc["failover"]["writeStats"]
    rb = sc["reboot"]["writeStats"]
    cfg = d["config"]
    yaml_text = cfg["yaml"]

    out = []
    out.append("# v11-CDK-Parallel Final Report — 2026-05-17")
    out.append("")
    out.append(f"> **Experiment**: v11-cdk-parallel  ")
    out.append(f"> **Generated**: {d.get('generatedAt')}  ")
    out.append(f"> **Infrastructure**: AWS CDK (Python) — full IaC  ")
    out.append(f"> **Parallelism**: 5 clusters in parallel  ")
    out.append(f"> **N measurements**: BG={bg.get('n',0)}, Failover={fo.get('n',0)}, Reboot={rb.get('n',0)}  ")
    out.append("")
    out.append("---")
    out.append("")
    out.append("## Executive summary")
    out.append("")
    out.append("v11 is v10's production reference configuration **re-run on a fully")
    out.append("CDK-managed infrastructure** with **5 clusters in parallel**. The")
    out.append("workload, JDBC config, JVM flags, and analyzer are unchanged from v10")
    out.append("— only the orchestration path differs.")
    out.append("")
    out.append("### Aggregated stats")
    out.append("")
    out.append("| Scenario      | N   | min      | median    | mean      | p95       | max       | stdev     |")
    out.append("|---------------|-----|----------|-----------|-----------|-----------|-----------|-----------|")
    for label, st in (("Blue/Green", bg), ("Failover", fo), ("Reboot", rb)):
        out.append(
            f"| {label:13s} | {st.get('n',0):3d} | {fmt_ms(st.get('min',0)):8s} | "
            f"{fmt_ms(st.get('median',0)):9s} | {fmt_ms(st.get('mean',0)):9s} | "
            f"{fmt_ms(st.get('p95',0)):9s} | {fmt_ms(st.get('max',0)):9s} | "
            f"{fmt_ms(st.get('stdev',0)):9s} |"
        )
    out.append("")

    # Per-cluster breakdown
    out.append("## Per-cluster breakdown (5-cluster parallel)")
    out.append("")
    out.append("Detection of cluster contention: do all 5 clusters land in the same")
    out.append("statistical envelope, or is one slower?")
    out.append("")
    for label, key in (("Blue/Green", "blue-green"), ("Failover", "failover"), ("Reboot", "reboot")):
        by_c = sc[key].get("byCluster", {})
        if not by_c: continue
        out.append(f"### {label} per cluster")
        out.append("")
        out.append("| Cluster      | N | min | median | max | stdev |")
        out.append("|--------------|---|-----|--------|-----|-------|")
        for cid in sorted(by_c.keys()):
            s = by_c[cid]
            if s.get("n", 0) == 0: continue
            out.append(
                f"| {cid:12s} | {s.get('n',0)} | "
                f"{fmt_ms(s.get('min'))} | {fmt_ms(s.get('median'))} | "
                f"{fmt_ms(s.get('max'))} | {fmt_ms(s.get('stdev'))} |"
            )
        out.append("")

    # v11 vs v10 comparison
    out.append("## v11 vs v10 comparison (sanity check)")
    out.append("")
    out.append("v10 reference numbers (production-load, single cluster, bash):")
    out.append("- BG: median 5.05 s, max 21 s, stdev 6.17 s")
    out.append("- Failover: median 7.75 s, max 14.8 s, stdev 3.69 s")
    out.append("- Reboot: median 100 ms, max 2.6 s, stdev 1.19 s")
    out.append("")
    out.append("v11 numbers (production-load, 5-cluster parallel, CDK):")
    out.append(f"- BG: median {fmt_ms(bg.get('median'))}, max {fmt_ms(bg.get('max'))}, stdev {fmt_ms(bg.get('stdev'))}")
    out.append(f"- Failover: median {fmt_ms(fo.get('median'))}, max {fmt_ms(fo.get('max'))}, stdev {fmt_ms(fo.get('stdev'))}")
    out.append(f"- Reboot: median {fmt_ms(rb.get('median'))}, max {fmt_ms(rb.get('max'))}, stdev {fmt_ms(rb.get('stdev'))}")
    out.append("")

    # Test environment
    out.append("## Test environment")
    out.append("")
    out.append("- Aurora MySQL 8.0.mysql_aurora.3.10.4 × 5 (test-v11-1..5)")
    out.append("- Each cluster: db.r7g.large writer + db.t3.medium reader, aurora-iopt1, port 4488")
    out.append("- Region: us-east-1, default VPC, public subnets")
    out.append("- Single c6i.2xlarge EC2 runner (8 vCPU / 16 GiB) running 5 java processes in parallel")
    out.append("- Workload (per cluster): 64 threads × 50ms × R:I:U=9:2:1 ≈ 1280 ops/s")
    out.append("- Aggregate workload: ~6400 ops/s across 5 clusters")
    out.append("- Connection pool: HikariCP `maximumPoolSize=50, minimumIdle=50`")
    out.append("- JVM: `-Dnetworkaddress.cache.ttl=5 -Xmx2g`")
    out.append("- STATS reporter: 10 Hz (±100ms precision)")
    out.append("- Wrapper: aws-advanced-jdbc-wrapper 4.0.1")
    out.append("- Plugins: `[failover2, efm2, bg]`")
    out.append("")

    # Per-round measurements
    for label, key in (("Blue/Green", "blue-green"), ("Failover", "failover"), ("Reboot", "reboot")):
        rounds = sc[key].get("rounds", [])
        if not rounds: continue
        out.append(f"## {label} — per-round measurements")
        out.append("")
        out.append("| Cluster | Round | writeMaxMs | readMaxMs | runId |")
        out.append("|---------|-------|-----------:|----------:|-------|")
        for r in rounds:
            out.append(
                f"| {r.get('cluster','')} | {r.get('round','')} | "
                f"{fmt_ms(r.get('writeMaxMs'))} | {fmt_ms(r.get('readMaxMs'))} | "
                f"`{r.get('runId','')}` |"
            )
        out.append("")

    # Inline yaml + how to reproduce
    out.append("## How to reproduce (full IaC)")
    out.append("")
    out.append("```bash")
    out.append("git clone https://github.com/neosun100/aurora-bg-toolkit.git")
    out.append("cd aurora-bg-toolkit")
    out.append("# one-time CDK bootstrap (per AWS account/region):")
    out.append("cd infra/cdk && uv venv .venv && uv pip install -r requirements.txt && cdk bootstrap && cd ../..")
    out.append("# end-to-end run (~3.5h wall, ~$8 AWS):")
    out.append("nohup python3 infra/orchestrate-v11.py > /tmp/v11-launch.log 2>&1 &")
    out.append("# watch progress:")
    out.append("bash scripts/v11-status.sh --watch")
    out.append("```")
    out.append("")

    # Yaml
    out.append("## Production configuration (canonical)")
    out.append("")
    out.append("```yaml")
    out.append(yaml_text.rstrip())
    out.append("```")
    out.append("")

    # Final
    out.append("## Final recommendation")
    out.append("")
    out.append("Use **`configs/v11-final.yaml`** + `infra/orchestrate-v11.py` (CDK)")
    out.append("for any new measurement campaign on Aurora MySQL. v11 is the")
    out.append("recommended production reference path; v10 (bash + single cluster)")
    out.append("remains as the reference implementation.")
    out.append("")

    out.append("---")
    out.append("")
    out.append(f"*Auto-generated from `dashboard/data/v11-only.json` ({d.get('generatedAt')}).*")
    out.append("")
    return "\n".join(out)


def main() -> int:
    if not DATA.exists():
        print(f"missing {DATA}; run scripts/v11-extract-data.py first", file=sys.stderr)
        return 1
    d = json.loads(DATA.read_text())
    text = render(d)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text)
    print(f"wrote {OUT} ({len(text)} chars)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
