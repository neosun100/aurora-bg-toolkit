#!/usr/bin/env bash
# v10-status.sh — render the v10 master orchestrator's progress as a
# human-friendly status report.
#
# Reads infra/state/v10-progress.json and prints:
#   * Current phase
#   * Phase counts (done / running / pending / failed)
#   * Per-round measurements (writeMaxMs / readMaxMs) so far
#   * Recent log tail
#   * ETA based on average phase duration
#
# Usage: bash scripts/v10-status.sh
#        bash scripts/v10-status.sh --watch    # auto-refresh every 30s

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR="$REPO_ROOT/infra/state"
PROGRESS="$STATE_DIR/v10-progress.json"
LOG_FILE="$STATE_DIR/v10-master.log"

WATCH=0
[[ "${1:-}" == "--watch" ]] && WATCH=1

render() {
    clear 2>/dev/null || true

    if [[ ! -f "$PROGRESS" ]]; then
        echo "No state file at $PROGRESS"
        echo "The v10 master orchestrator hasn't started yet."
        echo
        echo "To launch:"
        echo "  nohup bash infra/orchestrate-v10-master.sh > /tmp/v10-launch.log 2>&1 &"
        return
    fi

    python3 - <<'PYEOF'
import json, datetime, os, sys
from pathlib import Path

REPO_ROOT = Path(os.environ.get('REPO_ROOT', '.'))
PROGRESS = REPO_ROOT / 'infra/state/v10-progress.json'
LOG_FILE = REPO_ROOT / 'infra/state/v10-master.log'

GREEN  = '\033[0;32m'
RED    = '\033[0;31m'
YELLOW = '\033[0;33m'
BLUE   = '\033[0;34m'
CYAN   = '\033[0;36m'
GREY   = '\033[0;37m'
BOLD   = '\033[1m'
NC     = '\033[0m'

d = json.load(open(PROGRESS))

# Header
print(f"{BOLD}{'='*70}{NC}")
print(f"{BOLD} v10-production master orchestrator status{NC}")
print(f"{BOLD}{'='*70}{NC}")
print(f"  experiment: {d.get('experiment')}")
print(f"  config:     {d.get('config_file')}")
print(f"  started:    {d.get('started_at')}")
print(f"  current:    {BOLD}{d.get('current_phase') or '(idle/done)'}{NC}")
print()

# Phase counts
phases = d.get('phases', {})
counts = {'done': 0, 'running': 0, 'pending': 0, 'failed': 0}
for p, info in phases.items():
    s = info.get('status', 'pending')
    counts[s] = counts.get(s, 0) + 1
EXPECTED_TOTAL = 6 + 30 + 3  # setup(6) + measurements(30) + analyze/report/teardown(3)
print(f"{BOLD}Phase progress{NC}")
print(f"  done    {GREEN}{counts['done']:3d}{NC} / {EXPECTED_TOTAL}")
print(f"  running {BLUE}{counts['running']:3d}{NC}")
print(f"  pending {GREY}{counts['pending']:3d}{NC}")
print(f"  failed  {RED}{counts['failed']:3d}{NC}")
print()

# Setup phases status
print(f"{BOLD}Setup{NC}")
for ph in ['PRECHECK', 'BUILD', 'BOOTSTRAP', 'CLUSTER_CREATE', 'BG_PREREQS', 'EC2_SETUP']:
    info = phases.get(ph, {})
    s = info.get('status', '─')
    color = GREEN if s == 'done' else (BLUE if s == 'running' else (RED if s == 'failed' else GREY))
    dur = info.get('duration_s', '')
    dur_str = f" ({dur}s)" if dur else ""
    print(f"  {color}●{NC} {ph:18s} {s}{dur_str}")
print()

# Per-scenario measurement table
def render_scenario(label, prefix):
    print(f"{BOLD}{label}{NC}  (10 rounds)")
    print(f"  Round  {'Status':10s}  {'writeMaxMs':>10s}  {'readMaxMs':>10s}")
    write_samples = []
    read_samples = []
    for r in range(1, 11):
        ph = phases.get(f"{prefix}{r}", {})
        s = ph.get('status', 'pending')
        wmax = ph.get('writeMaxMs', '')
        rmax = ph.get('readMaxMs', '')
        if isinstance(wmax, int) and wmax > 0: write_samples.append(wmax)
        if isinstance(rmax, int) and rmax > 0: read_samples.append(rmax)
        color = GREEN if s == 'done' else (BLUE if s == 'running' else (RED if s == 'failed' else GREY))
        print(f"  R{r:<5d} {color}{s:10s}{NC}  {wmax!s:>10}  {rmax!s:>10}")
    if write_samples:
        import statistics as st
        wmin, wmed, wmax = min(write_samples), int(st.median(write_samples)), max(write_samples)
        print(f"  {GREY}── stats (n={len(write_samples)}): write min={wmin}ms median={wmed}ms max={wmax}ms{NC}")
    print()

render_scenario("Blue/Green",  "TEST_BG_R")
render_scenario("Failover",    "TEST_FO_R")
render_scenario("Reboot",      "TEST_RB_R")

# Wrap-up
print(f"{BOLD}Wrap-up{NC}")
for ph in ['ANALYZE', 'REPORT', 'TEARDOWN']:
    info = phases.get(ph, {})
    s = info.get('status', '─')
    color = GREEN if s == 'done' else (BLUE if s == 'running' else (RED if s == 'failed' else GREY))
    dur = info.get('duration_s', '')
    dur_str = f" ({dur}s)" if dur else ""
    print(f"  {color}●{NC} {ph:18s} {s}{dur_str}")
print()

# Errors
errors = d.get('errors', [])
if errors:
    print(f"{BOLD}{RED}Errors{NC}")
    for e in errors[-5:]:
        print(f"  {e['timestamp']} [{e['phase']}] {e['error'][:120]}")
    print()

# ETA: average phase duration × remaining done-needed
done_phases = [p for p in phases.values() if p.get('status') == 'done' and p.get('duration_s')]
if done_phases and counts['done'] < EXPECTED_TOTAL:
    avg_dur = sum(p['duration_s'] for p in done_phases) / len(done_phases)
    remaining = EXPECTED_TOTAL - counts['done']
    # BG rounds dominate (~7-15min each); use that as estimate for measurement phases
    bg_done = [phases.get(f"TEST_BG_R{r}", {}).get('duration_s') for r in range(1,11)]
    bg_done = [d for d in bg_done if d]
    if bg_done:
        avg_bg = sum(bg_done) / len(bg_done)
        bg_remaining = sum(1 for r in range(1,11) if phases.get(f"TEST_BG_R{r}", {}).get('status') != 'done')
        eta_s = bg_remaining * avg_bg + (remaining - bg_remaining) * avg_dur
    else:
        eta_s = remaining * avg_dur
    eta_min = int(eta_s / 60)
    print(f"{CYAN}ETA: ~{eta_min} min remaining{NC} (estimated)")
    print()

# Recent log tail
if LOG_FILE.exists():
    print(f"{BOLD}Recent log tail{NC} (last 5 lines of {LOG_FILE})")
    try:
        with open(LOG_FILE) as f:
            lines = f.readlines()
        for line in lines[-5:]:
            print(f"  {GREY}{line.rstrip()}{NC}")
    except Exception:
        pass
PYEOF
}

export REPO_ROOT
if [[ "$WATCH" == "1" ]]; then
    while true; do
        render
        sleep 30
    done
else
    render
fi
