#!/usr/bin/env python3
"""
v10-generate-report.py — write the final v10 production report to disk.

Reads dashboard/data/v10-only.json (must exist; run v10-extract-data.py first)
and writes docs/REPORTS/2026-05-17-v10-production.md.

The output is a self-contained markdown document with:
  - Executive summary
  - Test environment + workload (full v10-final.yaml inlined)
  - Per-scenario statistics tables
  - Hypothesis verdicts (vs predictions in EXPERIMENT-V10-PLAN.md)
  - Methodological notes
  - Final production recommendation
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA = REPO_ROOT / "dashboard/data/v10-only.json"
OUT = REPO_ROOT / "docs/REPORTS/2026-05-17-v10-production.md"


def fmt_ms(x):
    if x in (None, "", "—") or not isinstance(x, (int, float)):
        return "—"
    if x == 0:
        return "0 ms"
    if x < 1000:
        return f"{int(x)} ms"
    return f"{x/1000:.2f} s"


def render(d: dict) -> str:
    s = d["scenarios"]
    bg = s["blue-green"]["writeStats"]
    fo = s["failover"]["writeStats"]
    rb = s["reboot"]["writeStats"]
    cfg = d["config"]
    yaml_text = cfg["yaml"]

    out = []
    out.append(f"# v10-Production Final Report — 2026-05-17")
    out.append("")
    out.append(f"> **Experiment**: v10-production  ")
    out.append(f"> **Generated**: {d.get('generatedAt')}  ")
    out.append(f"> **Config file**: `{cfg.get('yamlPath')}`  ")
    out.append(f"> **N measurements**: BG={bg.get('n',0)}, Failover={fo.get('n',0)}, Reboot={rb.get('n',0)}  ")
    out.append("")
    out.append("---")
    out.append("")
    out.append("## Executive summary")
    out.append("")
    out.append("v10 is the **production reference configuration**: `v4-current.yaml`'s")
    out.append("validated tuning parameters, run for the first time at production load")
    out.append("(1280 ops/s, pool=50) with mandatory JVM `-Dnetworkaddress.cache.ttl=5`.")
    out.append("")
    out.append("This experiment closes a gap discovered during the 2026-05-17 audit of v9:")
    out.append("v9's v4 control cells were measured at low load (40 ops/s, pool=10), not")
    out.append("the claimed production load. v10 fixes that and records the production-grade")
    out.append("numbers for the canonical recommended configuration.")
    out.append("")
    out.append(f"| Scenario      | N   | min      | median    | mean      | p95       | max       | stdev     |")
    out.append(f"|---------------|-----|----------|-----------|-----------|-----------|-----------|-----------|")
    for label, st in (("Blue/Green", bg), ("Failover", fo), ("Reboot", rb)):
        out.append(f"| {label:13s} | {st.get('n',0):3d} | {fmt_ms(st.get('min',0)):8s} | {fmt_ms(st.get('median',0)):9s} | {fmt_ms(st.get('mean',0)):9s} | {fmt_ms(st.get('p95',0)):9s} | {fmt_ms(st.get('max',0)):9s} | {fmt_ms(st.get('stdev',0)):9s} |")
    out.append("")

    # Hypothesis verdicts
    out.append("## Hypothesis verdicts")
    out.append("")
    out.append("| H  | Prediction (from v10 plan)                     | Measured                              | Verdict |")
    out.append("|----|-------------------------------------------------|---------------------------------------|---------|")
    h1_med = bg.get('median', 0)
    h1_stdev = bg.get('stdev', 0)
    h1_pass = (3500 <= h1_med <= 4500 and h1_stdev < 500)
    out.append(f"| H1 | BG median 3.5–4.5s, stdev<500ms                | median {fmt_ms(h1_med)}, stdev {fmt_ms(h1_stdev)} | {'✅' if h1_pass else '⚠️'}      |")
    h2_med = fo.get('median', 0); h2_max = fo.get('max', 0)
    h2_pass = (5000 <= h2_med <= 8000 and h2_max < 12000)
    out.append(f"| H2 | Failover median 5–8s, max<12s                  | median {fmt_ms(h2_med)}, max {fmt_ms(h2_max)} | {'✅' if h2_pass else '⚠️'}      |")
    h3_med = rb.get('median', 0)
    h3_pass = (h3_med < 500)
    out.append(f"| H3 | Reboot median <500ms                            | median {fmt_ms(h3_med)}                          | {'✅' if h3_pass else '⚠️'}      |")
    out.append("")

    # Test environment
    out.append("## Test environment")
    out.append("")
    out.append("- Aurora MySQL 8.0.mysql_aurora.3.10.4 (matches customer)")
    out.append("- db.r7g.large writer + db.t3.medium reader, aurora-iopt1 storage")
    out.append("- Region: us-east-1 single VPC")
    out.append("- Client: c6i.2xlarge EC2 (8 vCPU / 16 GiB) in same VPC")
    out.append("- Workload: 64 threads × 50ms × R:I:U=9:2:1 ≈ 1280 ops/s (production)")
    out.append("- Connection pool: HikariCP `maximumPoolSize=50, minimumIdle=50`")
    out.append("- JVM DNS TTL: 5s (mandatory)")
    out.append("- STATS reporter: 10 Hz (±100ms precision)")
    out.append("- Wrapper: aws-advanced-jdbc-wrapper 4.0.1")
    out.append("- Plugins: `[failover2, efm2, bg]`")
    out.append("")

    # Per-scenario tables
    for label, key in (("Blue/Green switchover", "blue-green"), ("Failover", "failover"), ("Reboot", "reboot")):
        rounds = d["scenarios"][key].get("rounds", [])
        st = d["scenarios"][key].get("writeStats", {})
        out.append(f"## {label} — per-round measurements")
        out.append("")
        if not rounds:
            out.append("_No completed rounds._")
            out.append("")
            continue
        out.append("| Round | writeMaxMs | readMaxMs | wrapper | runId | period |")
        out.append("|-------|-----------:|----------:|---------|-------|--------|")
        for r in rounds:
            out.append(f"| {r.get('round')} | {fmt_ms(r.get('writeMaxMs'))} | {fmt_ms(r.get('readMaxMs'))} | {r.get('wrapperJar','')} | `{r.get('runId','')}` | {r.get('detectedPeriodMs','')}ms |")
        out.append("")

    # Inline yaml
    out.append("## Production configuration (canonical)")
    out.append("")
    out.append("```yaml")
    out.append(yaml_text.rstrip())
    out.append("```")
    out.append("")
    out.append("Required JVM startup flags:")
    out.append("```")
    out.append("-Dnetworkaddress.cache.ttl=5")
    out.append("-Dnetworkaddress.cache.negative.ttl=2")
    out.append("--add-opens java.base/java.lang=ALL-UNNAMED")
    out.append("--add-opens java.base/java.lang.reflect=ALL-UNNAMED")
    out.append("```")
    out.append("")

    # Methodological notes
    out.append("## Methodological notes")
    out.append("")
    out.append("- Each round is a fully independent measurement (cluster pre-warmed for")
    out.append("  60–90s before the trigger; clients shut down 90–240s after).")
    out.append("- Blue/Green: each round requires its own fresh BG deployment (BG can")
    out.append("  only switch over once). Old BGs are deleted with `--delete-target`")
    out.append("  before the next provision to avoid quota issues.")
    out.append("- Downtime is computed as the longest contiguous gap of zero-throughput")
    out.append("  STATS lines (write_ok=0). At 10Hz this gives ±100ms precision.")
    out.append("- All AWS resources are torn down at experiment end; account audited")
    out.append("  empty after each run (zero ongoing cost).")
    out.append("")

    # Final recommendation
    out.append("## Final recommendation")
    out.append("")
    out.append("Use **`configs/v10-final.yaml`** + the JVM flags listed above for any")
    out.append("Aurora MySQL JDBC client at production load. This configuration:")
    out.append("")
    out.append(f"- Holds Blue/Green switchover downtime to **median {fmt_ms(bg.get('median',0))}** (RDS bg plugin's hardcoded floor)")
    out.append(f"- Failover at **median {fmt_ms(fo.get('median',0))}** (Aurora writer-reader role swap)")
    out.append(f"- Reboot at **median {fmt_ms(rb.get('median',0))}** (DNS TTL=5 wins again)")
    out.append("")
    out.append("To go below the BG floor (~4 s) you must wait for either an")
    out.append("`aws-advanced-jdbc-wrapper` major release or an Aurora engine update.")
    out.append("Client-side tuning has hit its ceiling.")
    out.append("")

    out.append("---")
    out.append("")
    out.append(f"*Auto-generated from `dashboard/data/v10-only.json` ({d.get('generatedAt')}).*")
    out.append("")
    return "\n".join(out)


def main() -> int:
    if not DATA.exists():
        print(f"missing {DATA}; run scripts/v10-extract-data.py first", file=sys.stderr)
        return 1
    d = json.loads(DATA.read_text())
    text = render(d)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text)
    print(f"wrote {OUT} ({len(text)} chars)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
