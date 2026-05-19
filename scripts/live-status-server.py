#!/usr/bin/env python3
"""Live status dashboard for aurora-bg-toolkit orchestrator.

Serves a single-page HTML dashboard on localhost:9999 that auto-refreshes
every 10 seconds, showing real-time progress of v11/v12 experiments.

Usage:
    python3 scripts/live-status-server.py &
    open http://localhost:9999
"""
import json
import http.server
import os
from pathlib import Path
from datetime import datetime, timezone

PORT = int(os.environ.get("STATUS_PORT", "9999"))
REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = REPO_ROOT / "infra" / "state"

HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="10">
<title>Aurora BG Toolkit — Live Status</title>
<style>
:root{--bg:#000;--panel:#0d0d0f;--fg:#f5f5f7;--dim:#86868b;--green:#30d158;--red:#ff453a;--orange:#ff9f0a;--cyan:#5ac8fa;--accent:#2997ff;--border:#1f1f24}
*{box-sizing:border-box}
body{margin:0;padding:2rem;background:var(--bg);color:var(--fg);font-family:-apple-system,system-ui,sans-serif}
h1{font-size:1.8rem;margin:0 0 0.5rem;background:linear-gradient(135deg,var(--accent),#bf5af2);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.meta{color:var(--dim);font-size:0.85rem;margin-bottom:1.5rem}
.progress-bar{background:var(--panel);border-radius:8px;height:32px;overflow:hidden;margin:1rem 0;border:1px solid var(--border)}
.progress-fill{height:100%;border-radius:8px;display:flex;align-items:center;padding:0 12px;font-size:0.8rem;font-weight:600;transition:width 0.5s}
.fill-done{background:var(--green)}
.fill-running{background:var(--orange);animation:pulse 1.5s infinite}
.fill-failed{background:var(--red)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.7}}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:1rem;margin:1.5rem 0}
.card{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:1.2rem}
.card h3{margin:0 0 0.5rem;font-size:1rem;color:var(--accent)}
.card .num{font-size:2rem;font-weight:700;font-variant-numeric:tabular-nums}
.card .num.green{color:var(--green)}
.card .num.orange{color:var(--orange)}
.card .num.red{color:var(--red)}
.phases{margin:1.5rem 0}
.phase{display:flex;align-items:center;gap:0.75rem;padding:0.4rem 0;border-bottom:1px solid var(--border);font-size:0.85rem}
.phase:last-child{border-bottom:none}
.dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.dot.done{background:var(--green)}
.dot.running{background:var(--orange);animation:pulse 1s infinite}
.dot.failed{background:var(--red)}
.dot.pending{background:var(--border)}
.phase .name{flex:1;font-family:monospace}
.phase .val{color:var(--dim);font-family:monospace;font-size:0.8rem}
.cluster-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:0.5rem;margin:1rem 0}
.cluster-cell{background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:0.6rem;text-align:center;font-size:0.75rem}
.cluster-cell .cname{color:var(--dim);margin-bottom:0.3rem}
.cluster-cell .cval{font-weight:700;font-variant-numeric:tabular-nums}
.cluster-cell .cval.done{color:var(--green)}
.cluster-cell .cval.running{color:var(--orange)}
.cluster-cell .cval.failed{color:var(--red)}
footer{margin-top:2rem;color:var(--dim);font-size:0.8rem;text-align:center}
.empty{text-align:center;padding:4rem;color:var(--dim)}
@media(max-width:600px){.cluster-grid{grid-template-columns:repeat(2,1fr)}}
</style>
</head>
<body>
{BODY}
<footer>Auto-refresh every 10s · <code>localhost:{PORT}</code> · {NOW}</footer>
</body>
</html>"""


def load_progress(prefix: str) -> dict | None:
    f = STATE_DIR / f"{prefix}-progress.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text())
    except Exception:
        return None


def render_experiment(data: dict, prefix: str) -> str:
    phases = data.get("phases", {})
    total = len(phases)
    done = sum(1 for p in phases.values() if p.get("status") == "done")
    running = sum(1 for p in phases.values() if p.get("status") == "running")
    failed = sum(1 for p in phases.values() if p.get("status") == "failed")
    pct = int(done / total * 100) if total else 0

    experiment = data.get("experiment", prefix)
    started = data.get("started_at", "")

    # Progress bar
    bar_cls = "fill-done" if not running else "fill-running"
    if failed and not running:
        bar_cls = "fill-failed"
    bar = f'<div class="progress-bar"><div class="progress-fill {bar_cls}" style="width:{max(pct,2)}%">{done}/{total} phases ({pct}%)</div></div>'

    # Summary cards
    cards = f"""<div class="grid">
<div class="card"><h3>Done</h3><div class="num green">{done}</div></div>
<div class="card"><h3>Running</h3><div class="num orange">{running}</div></div>
<div class="card"><h3>Failed</h3><div class="num {'red' if failed else 'green'}">{failed}</div></div>
<div class="card"><h3>Total</h3><div class="num">{total}</div></div>
</div>"""

    # Setup phases
    setup_names = ["PRECHECK", "BUILD", "CDK_BOOTSTRAP", "CDK_DEPLOY", "COLLECT_OUTPUTS", "EC2_PROVISION"]
    setup_html = ""
    for name in setup_names:
        p = phases.get(name, {})
        st = p.get("status", "pending")
        dur = f"{p.get('duration_s', '')}s" if p.get("duration_s") else ""
        setup_html += f'<div class="phase"><span class="dot {st}"></span><span class="name">{name}</span><span class="val">{st} {dur}</span></div>'

    # Cluster grid (BG/FO/RB per cluster)
    cluster_html = '<div class="cluster-grid">'
    for i in range(1, 6):
        cid = f"test-v11-{i}"
        cells = []
        for sc, label in [("BG", "BG"), ("FO", "FO"), ("RB", "RB")]:
            for r in [1, 2]:
                pname = f"TEST_{cid}_{sc}_R{r}"
                p = phases.get(pname, {})
                st = p.get("status", "pending")
                ms = p.get("writeMaxMs", "")
                val = f"{ms}ms" if ms else st[:4]
                cells.append(f'<span class="cval {st}">{val}</span>')
        cluster_html += f'<div class="cluster-cell"><div class="cname">{cid}</div>{"  ".join(cells)}</div>'
    cluster_html += '</div>'

    # Wrap-up
    wrap_names = ["TEST_PARALLEL", "ANALYZE", "REPORT", "CDK_DESTROY"]
    wrap_html = ""
    for name in wrap_names:
        p = phases.get(name, {})
        st = p.get("status", "pending")
        dur = f"{p.get('duration_s', '')}s" if p.get("duration_s") else ""
        wrap_html += f'<div class="phase"><span class="dot {st}"></span><span class="name">{name}</span><span class="val">{st} {dur}</span></div>'

    # Errors
    errors = data.get("errors", [])
    err_html = ""
    if errors:
        err_html = '<div style="margin-top:1rem;padding:1rem;background:#1a0000;border:1px solid var(--red);border-radius:8px;font-size:0.8rem;color:var(--red)">'
        for e in errors[-3:]:
            err_html += f'<div>{e.get("phase","")}: {str(e.get("error",""))[:120]}</div>'
        err_html += '</div>'

    return f"""
<h2 style="color:var(--accent);margin-top:2rem">{experiment}</h2>
<div class="meta">Started: {started}</div>
{bar}
{cards}
<h3 style="color:var(--dim);margin-top:1.5rem">Setup</h3>
<div class="phases">{setup_html}</div>
<h3 style="color:var(--dim)">Clusters (BG·FO·RB × R1·R2)</h3>
{cluster_html}
<h3 style="color:var(--dim)">Wrap-up</h3>
<div class="phases">{wrap_html}</div>
{err_html}
"""


def render_page() -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    body = "<h1>Aurora BG Toolkit — Live Status</h1>"

    v11 = load_progress("v11")
    v12 = load_progress("v12")

    if not v11 and not v12:
        body += '<div class="empty"><p>No experiments running.</p><p>Start with: <code>python3 infra/orchestrate-v11.py</code></p></div>'
    else:
        if v11:
            body += render_experiment(v11, "v11")
        if v12:
            body += render_experiment(v12, "v12")

    return HTML_TEMPLATE.replace("{BODY}", body).replace("{PORT}", str(PORT)).replace("{NOW}", now)


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(render_page().encode())

    def log_message(self, format, *args):
        pass  # suppress access logs


if __name__ == "__main__":
    server = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Live status: http://localhost:{PORT}")
    print("Auto-refresh every 10s. Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
