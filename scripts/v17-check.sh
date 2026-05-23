#!/usr/bin/env bash
# v16-check.sh — Pull matrix sweep progress from S3 and render to terminal.
#
# Works from any machine with AWS CLI access. Doesn't depend on local
# orchestrator state — the source of truth is s3://abt-v17-state-{account}/.
#
# Usage:
#   bash scripts/v16-check.sh             # one-shot
#   bash scripts/v16-check.sh --watch     # auto-refresh every 30s

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AWS_PROFILE="${AWS_PROFILE:-jiasunm-neo}"
AWS_REGION="${AWS_REGION:-us-east-1}"
export AWS_PROFILE AWS_REGION

WATCH=0
OPEN_DASHBOARD=0
case "${1:-}" in
    --watch) WATCH=1 ;;
    --open)  OPEN_DASHBOARD=1 ;;
esac

# Locate the bucket (set explicitly or derived from account)
if [[ -z "${ABT_STATE_BUCKET:-}" ]]; then
    ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo "")"
    if [[ -z "$ACCOUNT_ID" ]]; then
        echo "Error: cannot determine AWS account. Check AWS_PROFILE."
        exit 1
    fi
    ABT_STATE_BUCKET="abt-v17-state-$ACCOUNT_ID"
fi

if [[ "$OPEN_DASHBOARD" == "1" ]]; then
    LOCAL_HTML="/tmp/abt-v16-dashboard.html"
    if aws s3 cp "s3://$ABT_STATE_BUCKET/dashboard.html" "$LOCAL_HTML" --quiet 2>/dev/null; then
        open "$LOCAL_HTML" 2>/dev/null || xdg-open "$LOCAL_HTML" 2>/dev/null || echo "Saved: $LOCAL_HTML"
        echo "✓ Dashboard opened: $LOCAL_HTML"
    else
        echo "✗ dashboard.html not yet in s3://$ABT_STATE_BUCKET/"
        echo "  (orchestrator generates it after first phase completes)"
        exit 2
    fi
    exit 0
fi

render_once() {
    clear 2>/dev/null || true
    local tmpfile
    tmpfile="$(mktemp)"
    trap "rm -f $tmpfile" EXIT

    if ! aws s3 cp "s3://$ABT_STATE_BUCKET/matrix-progress.json" "$tmpfile" --quiet 2>/dev/null; then
        echo "No matrix-progress.json in s3://$ABT_STATE_BUCKET/"
        echo
        echo "Possible reasons:"
        echo "  1. Matrix hasn't started yet (run: bash infra/launch-matrix.sh)"
        echo "  2. Wrong bucket. Set ABT_STATE_BUCKET explicitly."
        echo "  3. AWS credentials issue."
        return
    fi

    BUCKET="$ABT_STATE_BUCKET" python3 - "$tmpfile" <<'PYEOF'
import json
import os
import sys
import datetime

p = json.load(open(sys.argv[1]))

GRN = '\033[0;32m'; RED = '\033[0;31m'; YEL = '\033[0;33m'; BLU = '\033[0;34m'
CYA = '\033[0;36m'; GRY = '\033[0;37m'; BLD = '\033[1m'; DIM = '\033[2m'; NC = '\033[0m'

print(f"{BLD}{'='*78}{NC}")
print(f"{BLD} Aurora BG Toolkit v17 — Matrix Sweep status{NC}")
print(f"{BLD}{'='*78}{NC}")

started = p.get("started_at", "?")
completed = p.get("completed_at")
print(f"  started:    {started}")

# Elapsed
try:
    s = datetime.datetime.fromisoformat(started.rstrip("Z"))
    if completed:
        e = datetime.datetime.fromisoformat(completed.rstrip("Z"))
    else:
        e = datetime.datetime.utcnow()
    elapsed = e - s
    h, rem = divmod(int(elapsed.total_seconds()), 3600)
    m, s = divmod(rem, 60)
    print(f"  elapsed:    {h}h {m:02d}m {s:02d}s")
except Exception:
    pass

if completed:
    print(f"  {GRN}{BLD}completed:  {completed} ✓{NC}")
else:
    print(f"  status:     {YEL}running{NC}")
print()

runs = p.get("runs", {})
total = p.get("total_runs", len(runs))
done = sum(1 for r in runs.values() if r.get("status") == "done")
running = sum(1 for r in runs.values() if r.get("status") == "running")
failed = sum(1 for r in runs.values() if r.get("status") == "failed")

# Progress bar
done_blocks = int(20 * done / max(total, 1))
running_blocks = int(20 * running / max(total, 1))
bar = "▓" * done_blocks + "▒" * running_blocks + "░" * (20 - done_blocks - running_blocks)
print(f"  Progress:   [{GRN}{bar[:done_blocks]}{NC}{YEL}{bar[done_blocks:done_blocks+running_blocks]}{NC}{DIM}{bar[done_blocks+running_blocks:]}{NC}]  "
      f"{done}/{total} runs done")
if failed:
    print(f"              {RED}{failed} failed{NC}")
print()

# Per-run table
if runs:
    print(f"{BLD}Per-run status{NC}")
    print(f"  {'ID':<8s} {'Status':<8s} {'Writer':<14s} {'Client':<14s} {'TPS':<14s} {'Started':<20s} {'Duration':<10s}")
    print(f"  {GRY}{'-'*8} {'-'*8} {'-'*14} {'-'*14} {'-'*14} {'-'*20} {'-'*10}{NC}")
    for run_id in sorted(runs.keys(),
                         key=lambda k: runs[k].get("started_at", "9999")):
        r = runs[run_id]
        st = r.get("status", "?")
        color = (GRN if st == "done" else
                 YEL if st == "running" else
                 RED if st == "failed" else GRY)
        st_str = f"{color}{st:<8s}{NC}"
        writer = r.get("writer_instance", "?")[:14]
        client = r.get("client_instance", "?")[:14]
        tps = r.get("tps_config", "?")[:14]
        started_at = (r.get("started_at") or "")[:19]
        # Compute duration
        duration_str = ""
        if r.get("started_at") and (r.get("ended_at") or st == "running"):
            try:
                s = datetime.datetime.fromisoformat(r["started_at"].rstrip("Z"))
                e = datetime.datetime.fromisoformat(
                    (r.get("ended_at") or
                     datetime.datetime.utcnow().isoformat() + "Z").rstrip("Z"))
                d = int((e - s).total_seconds())
                hh, rem = divmod(d, 3600)
                mm = rem // 60
                duration_str = f"{hh}h{mm:02d}m" if hh else f"{mm}m"
            except Exception:
                pass
        print(f"  {run_id:<8s} {st_str} {writer:<14s} {client:<14s} {tps:<14s} {started_at:<20s} {duration_str:<10s}")
        if r.get("error"):
            print(f"           {DIM}error: {r['error'][:100]}{NC}")
    print()

# Recent events
events = p.get("events", [])[-5:]
if events:
    print(f"{BLD}Recent events{NC}")
    for e in events:
        lvl = e.get("level", "info")
        color = RED if lvl == "error" else (YEL if lvl == "warn" else GRY)
        run_str = f" [{e.get('run', '')}]" if e.get('run') else ""
        print(f"  {DIM}{e['ts']}{NC} {color}{lvl}{NC}{run_str}: {e['message'][:120]}")
    print()

bucket = os.environ.get("BUCKET", "abt-v17-state")
print(f"{DIM}Open dashboard:  bash scripts/v16-check.sh --open{NC}")
print(f"{DIM}Bucket:          s3://{bucket}/{NC}")
PYEOF
}

if [[ "$WATCH" == "1" ]]; then
    while true; do render_once; sleep 30; done
else
    render_once
fi
