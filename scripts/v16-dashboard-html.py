#!/usr/bin/env python3
"""
v16-dashboard-html.py — Generate a self-contained HTML dashboard.

Reads infra/state/matrix-progress.json + runs/*-progress.json and writes
a single HTML file that's uploaded to s3://abt-v16-state-{account}/dashboard.html
with public-read so the user can open it in any browser.

Called periodically by the matrix orchestrator (or invoked manually
from the runner EC2 / locally for debugging).

The HTML is fully self-contained (inline CSS, no external JS dependencies)
and auto-refreshes every 30s.
"""
from __future__ import annotations

import datetime
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = REPO_ROOT / "infra" / "state"
MATRIX_PROGRESS = STATE_DIR / "matrix-progress.json"

OUT_HTML = STATE_DIR / "dashboard.html"


def fmt_duration(seconds: int) -> str:
    if seconds is None:
        return "—"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def render_html(progress: dict, per_run: dict[str, dict]) -> str:
    """Produce the dashboard HTML."""
    runs = progress.get("runs", {})
    total = progress.get("total_runs", len(runs))
    done = sum(1 for r in runs.values() if r.get("status") == "done")
    running_count = sum(1 for r in runs.values() if r.get("status") == "running")
    failed = sum(1 for r in runs.values() if r.get("status") == "failed")
    pending = max(total - done - running_count - failed, 0)

    pct = int(100 * done / max(total, 1))

    # Compute elapsed
    elapsed_str = "—"
    try:
        s = datetime.datetime.fromisoformat(progress["started_at"].rstrip("Z"))
        e_str = progress.get("completed_at") or (datetime.datetime.utcnow().isoformat() + "Z")
        e = datetime.datetime.fromisoformat(e_str.rstrip("Z"))
        elapsed_str = fmt_duration(int((e - s).total_seconds()))
    except Exception:
        pass

    completed_at = progress.get("completed_at")

    # Per-run rows
    run_rows = []
    for run_id in sorted(runs.keys(), key=lambda k: runs[k].get("started_at", "9999")):
        r = runs[run_id]
        st = r.get("status", "pending")
        color_class = {
            "done": "ok", "running": "warn", "failed": "bad",
        }.get(st, "dim")

        # Per-run inner phase counts
        prun = per_run.get(r.get("label", run_id), {})
        phases = prun.get("phases", {}) if isinstance(prun, dict) else {}
        phase_done = sum(1 for p in phases.values() if p.get("status") == "done")
        phase_total = len(phases)
        phase_progress = f"{phase_done}/{phase_total}" if phase_total else "—"

        # Aggregate per-cluster writeMaxMs medians (rough)
        bg_meds, fo_meds, rb_meds = [], [], []
        for pname, pinfo in phases.items():
            wm = pinfo.get("writeMaxMs")
            if not isinstance(wm, (int, float)) or wm < 0:
                continue
            if "_BG_" in pname:
                bg_meds.append(wm)
            elif "_FO_" in pname:
                fo_meds.append(wm)
            elif "_RB_" in pname:
                rb_meds.append(wm)

        def median_str(xs):
            if not xs:
                return "—"
            xs_sorted = sorted(xs)
            mid = xs_sorted[len(xs_sorted) // 2]
            return f"{mid/1000:.2f}s" if mid >= 1000 else f"{mid}ms"

        bg_str, fo_str, rb_str = median_str(bg_meds), median_str(fo_meds), median_str(rb_meds)

        # Duration
        dur = r.get("ended_at") and r.get("started_at")
        dur_str = "—"
        try:
            if r.get("started_at"):
                s = datetime.datetime.fromisoformat(r["started_at"].rstrip("Z"))
                e = datetime.datetime.fromisoformat(
                    (r.get("ended_at") or datetime.datetime.utcnow().isoformat() + "Z").rstrip("Z"))
                dur_str = fmt_duration(int((e - s).total_seconds()))
        except Exception:
            pass

        run_rows.append(f"""
        <tr class="{color_class}">
          <td><code>{run_id}</code></td>
          <td><span class="pill {color_class}">{st}</span></td>
          <td>{r.get('writer_instance', '—')}</td>
          <td>{r.get('client_instance', '—')}</td>
          <td>{r.get('tps_config', '—')}</td>
          <td class="num">{phase_progress}</td>
          <td class="num">{bg_str}</td>
          <td class="num">{fo_str}</td>
          <td class="num">{rb_str}</td>
          <td class="num">{dur_str}</td>
        </tr>""")

    rows_html = "".join(run_rows) if run_rows else """
        <tr><td colspan="10" style="text-align:center;color:var(--dim);padding:2rem">
          (no runs yet — orchestrator may still be initializing)
        </td></tr>"""

    # Recent events
    events = progress.get("events", [])[-10:]
    evt_items = []
    for e in events:
        run_label = f"[{e.get('run')}]" if e.get("run") else ""
        evt_items.append(
            f"<li class='evt-{e.get('level', 'info')}'><code>{e['ts']}</code> "
            f"{run_label} {e['message']}</li>"
        )
    evt_html = "".join(evt_items) or "<li class='dim'>no events</li>"

    now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="30">
<title>Aurora BG Toolkit v16 — Matrix Sweep</title>
<style>
:root {{ --bg:#0a0a0f; --panel:#15151a; --panel2:#1d1d24; --fg:#f5f5f7;
         --dim:#86868b; --accent:#2997ff; --green:#30d158; --red:#ff453a;
         --orange:#ff9f0a; --purple:#bf5af2; --border:#2a2a32; }}
* {{ box-sizing: border-box }}
body {{ margin:0; padding:2rem; background:var(--bg); color:var(--fg);
        font-family:-apple-system,'SF Pro Display',system-ui,sans-serif }}
.wrap {{ max-width: 1280px; margin: 0 auto }}
h1 {{ font-size:1.8rem; margin:0 0 0.5rem;
      background:linear-gradient(135deg,var(--accent),var(--purple));
      -webkit-background-clip:text; -webkit-text-fill-color:transparent }}
.meta {{ color:var(--dim); font-size:0.9rem; margin-bottom:1.5rem }}
.cards {{ display:grid; grid-template-columns:repeat(4,1fr); gap:1rem; margin:1rem 0 }}
.card {{ background:var(--panel); border:1px solid var(--border); border-radius:10px;
         padding:1rem 1.2rem }}
.card .lbl {{ color:var(--dim); font-size:0.75rem; text-transform:uppercase;
              letter-spacing:0.05em }}
.card .val {{ font-size:2rem; font-weight:700; margin:0.3rem 0;
              font-variant-numeric:tabular-nums }}
.card.green .val {{ color:var(--green) }}
.card.orange .val {{ color:var(--orange) }}
.card.red .val {{ color:var(--red) }}
.card.dim .val {{ color:var(--dim) }}
.bar {{ background:var(--panel2); border-radius:8px; height:32px; margin:1rem 0;
        position:relative; overflow:hidden; border:1px solid var(--border) }}
.bar-fill {{ position:absolute; left:0; top:0; bottom:0; border-radius:8px;
             transition:width .5s }}
.bar-fill.done {{ background:var(--green); width:{int(100*done/max(total,1))}% }}
.bar-fill.running {{ background:var(--orange); left:{int(100*done/max(total,1))}%;
                     width:{int(100*running_count/max(total,1))}%; animation:pulse 1.5s infinite }}
@keyframes pulse {{ 0%,100% {{ opacity:1 }} 50% {{ opacity:0.6 }} }}
.bar-text {{ position:absolute; inset:0; display:flex; align-items:center;
             justify-content:center; font-weight:600; z-index:2; mix-blend-mode:difference }}
.panel {{ background:var(--panel); border:1px solid var(--border); border-radius:10px;
          padding:1.2rem; margin:1.5rem 0 }}
.panel h2 {{ font-size:1.1rem; margin:0 0 0.8rem; color:var(--accent) }}
table {{ width:100%; border-collapse:collapse; font-size:0.85rem }}
th, td {{ padding:0.5rem 0.65rem; text-align:left; border-bottom:1px solid var(--border) }}
th {{ color:var(--dim); font-weight:500; text-transform:uppercase;
      font-size:0.7rem; letter-spacing:0.05em }}
td.num {{ font-variant-numeric:tabular-nums; font-family:monospace }}
.pill {{ display:inline-block; padding:0.15rem 0.55rem; border-radius:999px;
         font-size:0.7rem; font-weight:600; text-transform:uppercase }}
.pill.ok {{ background:rgba(48,209,88,0.18); color:var(--green) }}
.pill.warn {{ background:rgba(255,159,10,0.18); color:var(--orange) }}
.pill.bad {{ background:rgba(255,69,58,0.18); color:var(--red) }}
.pill.dim {{ background:rgba(134,134,139,0.15); color:var(--dim) }}
ul.evt {{ list-style:none; padding:0; margin:0; font-size:0.85rem }}
ul.evt li {{ padding:0.3rem 0; border-bottom:1px solid var(--border) }}
ul.evt code {{ color:var(--dim); margin-right:0.5rem }}
.evt-error {{ color:var(--red) }}
.evt-warn {{ color:var(--orange) }}
.dim {{ color:var(--dim) }}
footer {{ margin-top:2rem; color:var(--dim); font-size:0.8rem; text-align:center }}
@media (max-width:700px) {{ .cards {{ grid-template-columns:repeat(2,1fr) }} }}
</style>
</head>
<body>
<div class="wrap">
<h1>Aurora BG Toolkit v16 — Matrix Sweep</h1>
<div class="meta">
  Started: {progress.get('started_at','?')} ·
  Elapsed: {elapsed_str} ·
  {'<span style="color:var(--green)">Completed: ' + completed_at + ' ✓</span>' if completed_at else '<span style="color:var(--orange)">Running</span>'} ·
  Auto-refresh every 30s
</div>

<div class="cards">
  <div class="card green"><div class="lbl">Done</div><div class="val">{done}</div></div>
  <div class="card orange"><div class="lbl">Running</div><div class="val">{running_count}</div></div>
  <div class="card dim"><div class="lbl">Pending</div><div class="val">{pending}</div></div>
  <div class="card {'red' if failed else 'dim'}"><div class="lbl">Failed</div><div class="val">{failed}</div></div>
</div>

<div class="bar">
  <div class="bar-fill done"></div>
  <div class="bar-fill running"></div>
  <div class="bar-text">{done} / {total} runs ({pct}%)</div>
</div>

<div class="panel">
  <h2>Per-run progress</h2>
  <table>
    <thead><tr>
      <th>ID</th><th>Status</th>
      <th>Writer</th><th>Client</th><th>TPS Config</th>
      <th class="num">Phases</th>
      <th class="num">BG med</th><th class="num">FO med</th><th class="num">RB med</th>
      <th class="num">Duration</th>
    </tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
</div>

<div class="panel">
  <h2>Recent events</h2>
  <ul class="evt">{evt_html}</ul>
</div>

<footer>
  Aurora BG Toolkit v16 · last refresh {now_str} · auto-refresh every 30s ·
  <a href="https://github.com/neosun100/aurora-bg-toolkit" style="color:var(--accent)">github.com/neosun100/aurora-bg-toolkit</a>
</footer>
</div>
</body>
</html>"""


def main():
    if not MATRIX_PROGRESS.exists():
        progress = {
            "started_at": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "runs": {}, "events": [],
            "total_runs": 0,
        }
    else:
        progress = json.loads(MATRIX_PROGRESS.read_text())

    # Load per-run progress.json files
    per_run = {}
    for pf in STATE_DIR.glob("*-progress.json"):
        if pf.name == "matrix-progress.json":
            continue
        try:
            label = pf.name.replace("-progress.json", "")
            per_run[label] = json.loads(pf.read_text())
        except Exception:
            pass

    html = render_html(progress, per_run)
    OUT_HTML.write_text(html)
    print(f"Wrote {OUT_HTML} ({len(html)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
