#!/usr/bin/env bash
# v11-status.sh — render the v11 master orchestrator's progress.
#
# Reads infra/state/v11-progress.json and prints:
#   * Current phase
#   * Setup phase status (CDK deploy, EC2 provision, etc.)
#   * 5-cluster parallel test progress (each cluster: 2 BG + 2 FO + 2 RB)
#   * Aggregated stats across all clusters
#   * Recent log tail
#
# Usage: bash scripts/v11-status.sh
#        bash scripts/v11-status.sh --watch    # auto-refresh every 30s

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR="$REPO_ROOT/infra/state"
PROGRESS="$STATE_DIR/v11-progress.json"
LOG_FILE="$STATE_DIR/v11-master.log"

WATCH=0
[[ "${1:-}" == "--watch" ]] && WATCH=1

render() {
    clear 2>/dev/null || true
    if [[ ! -f "$PROGRESS" ]]; then
        echo "No state at $PROGRESS"
        echo "Launch with: nohup python3 infra/orchestrate-v11.py > /tmp/v11-launch.log 2>&1 &"
        return
    fi

    REPO_ROOT="$REPO_ROOT" python3 - <<'PYEOF'
import json, os, datetime
from pathlib import Path

REPO_ROOT = Path(os.environ['REPO_ROOT'])
PROGRESS = REPO_ROOT / 'infra/state/v11-progress.json'
LOG_FILE = REPO_ROOT / 'infra/state/v11-master.log'

GRN = '\033[0;32m'; RED = '\033[0;31m'; YEL = '\033[0;33m'; BLU = '\033[0;34m'
CYA = '\033[0;36m'; GRY = '\033[0;37m'; BLD = '\033[1m'; NC = '\033[0m'

d = json.load(open(PROGRESS))

print(f"{BLD}{'='*78}{NC}")
print(f"{BLD} v11-cdk-parallel orchestrator status{NC}")
print(f"{BLD}{'='*78}{NC}")
print(f"  experiment: {d.get('experiment')}")
print(f"  started:    {d.get('started_at')}")
phases = d.get('phases', {})
running = [n for n,p in phases.items() if p.get('status')=='running']
print(f"  current:    {BLD}{', '.join(running) or '(idle/done)'}{NC}")
print()

# Phase counts — total = 6 setup phases + 30 measurement phases + 3 wrap-up = 39
counts = {'done':0, 'running':0, 'pending':0, 'failed':0}
for n,p in phases.items():
    s = p.get('status', 'pending')
    counts[s] = counts.get(s, 0) + 1
TOTAL = 6 + 30 + 3  # 6 setup + 30 measurements + 3 wrap-up
print(f"{BLD}Phase progress{NC}")
print(f"  done    {GRN}{counts['done']:3d}{NC} / {TOTAL}")
print(f"  running {BLU}{counts['running']:3d}{NC}")
print(f"  pending {GRY}{counts['pending']:3d}{NC}")
print(f"  failed  {RED}{counts['failed']:3d}{NC}")
print()

# Setup
print(f"{BLD}Setup{NC}")
for ph in ['PRECHECK', 'BUILD', 'CDK_BOOTSTRAP', 'CDK_DEPLOY', 'COLLECT_OUTPUTS', 'EC2_PROVISION']:
    info = phases.get(ph, {})
    s = info.get('status', '─')
    color = GRN if s=='done' else (BLU if s=='running' else (RED if s=='failed' else GRY))
    dur = info.get('duration_s', '')
    dur_str = f' ({dur}s)' if dur else ''
    print(f"  {color}●{NC} {ph:18s} {s}{dur_str}")
print()

# Per-cluster grid
def render_cluster(cid):
    print(f"{BLD}{cid}{NC}  (2 BG + 2 FO + 2 RB)")
    for sc, prefix in [('BG', 'BG'), ('FO', 'FO'), ('RB', 'RB')]:
        line = f"  {sc}:"
        for r in (1, 2):
            ph = f'TEST_{cid}_{prefix}_R{r}'
            info = phases.get(ph, {})
            s = info.get('status', 'pending')
            wmax = info.get('writeMaxMs', '')
            color = GRN if s=='done' else (BLU if s=='running' else (RED if s=='failed' else GRY))
            mark = '●' if s=='done' else ('▶' if s=='running' else ('✗' if s=='failed' else '○'))
            line += f"  R{r}:{color}{mark}{NC} {wmax!s:>5}ms"
        print(line)
    print()

for i in range(1, 6):
    render_cluster(f'test-v11-{i}')

# Wrap-up
print(f"{BLD}Wrap-up{NC}")
for ph in ['ANALYZE', 'REPORT', 'CDK_DESTROY', 'TEST_PARALLEL']:
    info = phases.get(ph, {})
    s = info.get('status', '─')
    color = GRN if s=='done' else (BLU if s=='running' else (RED if s=='failed' else GRY))
    dur = info.get('duration_s', '')
    dur_str = f' ({dur}s)' if dur else ''
    print(f"  {color}●{NC} {ph:18s} {s}{dur_str}")
print()

# Aggregate stats
import statistics as st
samples_by_scenario = {'BG': [], 'FO': [], 'RB': []}
for n,p in phases.items():
    if not n.startswith('TEST_test-v11-'): continue
    if p.get('status') != 'done': continue
    parts = n.split('_')
    if len(parts) < 4: continue
    sc = parts[3].rstrip('_R0123456789')
    sc = parts[3]
    wmax = p.get('writeMaxMs')
    if isinstance(wmax, int) and wmax >= 0:
        for s in samples_by_scenario:
            if f'_{s}_' in n:
                samples_by_scenario[s].append(wmax)
                break

if any(samples_by_scenario.values()):
    print(f"{BLD}Aggregated writeMaxMs across all clusters{NC}")
    for sc, samples in samples_by_scenario.items():
        if not samples: continue
        med = int(st.median(samples)) if samples else 0
        mx = max(samples) if samples else 0
        print(f"  {sc}: n={len(samples):2d}  median={med:>6}ms  max={mx:>6}ms")
    print()

# Errors
errors = d.get('errors', [])
if errors:
    print(f"{BLD}{RED}Errors{NC}")
    for e in errors[-5:]:
        print(f"  {e['ts']} [{e['phase']}] {e['error'][:100]}")
    print()

# Tail log
if LOG_FILE.exists():
    print(f"{BLD}Recent log tail{NC}")
    lines = open(LOG_FILE).readlines()
    for l in lines[-5:]:
        print(f"  {GRY}{l.rstrip()}{NC}")
PYEOF
}

if [[ "$WATCH" == "1" ]]; then
    while true; do render; sleep 30; done
else
    render
fi
