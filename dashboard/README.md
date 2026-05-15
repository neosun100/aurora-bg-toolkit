# Dashboard

Single-file static HTML dashboard for Aurora BG Toolkit results.

## How to view

Open `index.html` directly in any browser. **No build step, no server.**

```bash
open dashboard/index.html         # macOS
xdg-open dashboard/index.html     # Linux
```

If `data/runs.json` doesn't exist yet, the page shows a friendly "no data
loaded yet" message with the command to generate it.

## How to populate

After running E2E tests:

```bash
# 1. Per-run analysis (one analysis.json per run dir)
for d in e2e-results/test-*; do
  python3 scripts/analyze-logs.py "$d"
done

# 2. Aggregate into dashboard data
python3 scripts/compare-runs.py e2e-results/ -o dashboard/data/runs.json

# 3. Refresh the page
```

## Layout

- **Hero strip** — total runs, best / median / worst write downtime
- **Downtime by configuration** — scatter chart of all sample points, grouped by config color
- **Statistics table** — per-config N / min / median / mean / P95 / max / stdev
- **Platform × wrapper combo bars** — does EC2 vs EKS or wrapper 3.3 vs 4.0 matter?
- **Per-run detail table** — every run with verdict pill (good/ok/slow/BAD)
- **Anatomy section** — short explanation of the 30s+ TCP-hang root cause

## Contents

```
dashboard/
├── index.html         # the only HTML file
├── data/
│   └── runs.json      # produced by scripts/compare-runs.py (gitignored)
├── assets/
│   ├── chart.umd.min.js    # Chart.js v4.4.0 (vendored offline)
│   └── dashboard.js        # the page logic (vanilla JS)
└── README.md          # this file
```

## Sharing

The dashboard is just static files. To share with a customer:

```bash
zip -r aurora-bg-toolkit-dashboard.zip dashboard/
```

Send the zip; recipient unzips and opens `index.html`. Works offline.

## Why static HTML and not React?

Reproducibility. We need this dashboard to render the same way three years
from now without "build chain rotted" surprises. Vanilla JS + a single
vendored Chart.js bundle has zero supply-chain risk and no dependency
on Node tooling.
