// dashboard-v17.js — v17 (Reboot Deep-Dive, 100 Hz STATS) dashboard logic.
// Loads dashboard/data/v17-only.json (produced by scripts/v17-extract-matrix.py
// + post-processing). Renders T3 (production target) as the headline view +
// a full instance × TPS matrix table.
// Vanilla JS, no Chart.js dependency — boxplots are hand-drawn SVG.

const SCENARIOS = [
  { key: 'blue-green', label: 'Blue/Green',   sub: 'T3: 5 clusters × 1 round = 5 (v17 BG 5/5 success)',  color: 'var(--green)',  cls: 'green' },
  { key: 'failover',   label: 'Failover',     sub: 'T3: 5 clusters × 1 round = 5',  color: 'var(--orange)', cls: 'orange' },
  { key: 'reboot',     label: 'Reboot',       sub: 'T3: 5 measurements (cluster auto-failover, 100 Hz STATS)',   color: 'var(--cyan)',   cls: 'cyan' },
];

const CLUSTERS = ['test-v11-1', 'test-v11-2', 'test-v11-3', 'test-v11-4', 'test-v11-5'];

function fmtMs(ms) {
  if (ms == null || ms === '' || ms === '—') return '—';
  if (ms === 0) return '0 ms';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms/1000).toFixed(2)} s`;
}

function verdict(scenarioKey, st) {
  const med = st.median || 0;
  // Targets calibrated for v16 T3 (8X @ 4000 TPS, production target)
  const targets = {
    'blue-green': { ok: 4000, warn: 6000 },   // BG 3.4s in T3 = good
    'failover':   { ok: 12000, warn: 15000 }, // FO 11s at 4000 TPS
    'reboot':     { ok: 50,    warn: 200 },   // RB 10-30ms (r7g.2xlarge reader)
  };
  const t = targets[scenarioKey] || { ok: 5000, warn: 10000 };
  if (med <= t.ok)   return { label: 'good', cls: 'good' };
  if (med <= t.warn) return { label: 'ok',   cls: 'warn' };
  return { label: 'slow', cls: 'bad' };
}

async function load() {
  let data;
  try {
    const resp = await fetch('data/v17-only.json', { cache: 'no-store' });
    if (!resp.ok) throw new Error(resp.statusText);
    data = await resp.json();
  } catch (e) {
    document.getElementById('empty').style.display = 'block';
    return;
  }

  document.getElementById('content').style.display = 'block';
  document.getElementById('genAt').textContent = `Generated ${data.generatedAt || '—'}`;

  renderHero(data);
  renderHighlights(data);
  renderYaml(data);
  renderBoxplots(data);
  renderStatsTable(data);
  renderPerClusterTable(data);
  renderRoundsTable(data);
  renderMatrixView(data);
}

function renderHero(data) {
  const hero = document.getElementById('hero');
  hero.innerHTML = '';
  for (const sc of SCENARIOS) {
    const sd = (data.scenarios || {})[sc.key];
    const st = (sd && sd.writeStats) || {};
    const med = st.median;
    const v = verdict(sc.key, st);
    const card = document.createElement('div');
    card.className = 'card';
    card.innerHTML = `
      <div class="scenario-label">${sc.label}</div>
      <div class="num ${sc.cls}">${med != null ? fmtMs(med) : '—'}</div>
      <div class="meta">
        median · n=${st.n || 0} · stdev ${fmtMs(st.stdev || 0)}
        <span class="pill ${v.cls}" style="margin-left:0.5rem">${v.label}</span>
      </div>`;
    hero.appendChild(card);
  }
}

function renderHighlights(data) {
  const el = document.getElementById('highlights');
  el.innerHTML = '';
  const items = (data.config && data.config.highlights) || [];
  for (const h of items) {
    const div = document.createElement('div');
    div.className = 'highlight';
    div.innerHTML = `
      <div class="name">${h.name}</div>
      <div class="value">${h.value}</div>
      <div class="rationale">${h.rationale}</div>`;
    el.appendChild(div);
  }
}

function renderYaml(data) {
  const box = document.getElementById('yamlBox');
  box.textContent = (data.config && data.config.yaml) || '(yaml not loaded)';
}

// ─────────────── Box plot SVG ───────────────
function renderBoxplots(data) {
  const cont = document.getElementById('boxplots');
  cont.innerHTML = '';
  for (const sc of SCENARIOS) {
    const sd = (data.scenarios || {})[sc.key];
    const samples = (sd && sd.samples) || [];
    const st = (sd && sd.writeStats) || {};
    const cell = document.createElement('div');
    cell.className = 'boxplot-cell';
    cell.style.borderLeft = `3px solid ${sc.color}`;
    cell.innerHTML = `
      <h3 style="color:${sc.color}">${sc.label}</h3>
      <div class="sublabel">${sc.sub} · n=${st.n || 0}</div>
      <div class="svg-host"></div>`;
    const host = cell.querySelector('.svg-host');
    host.appendChild(makeBoxplotSvg(samples, st, sc.color));
    cont.appendChild(cell);
  }
}

function makeBoxplotSvg(samples, st, color) {
  const W = 240, H = 220, PAD_T = 20, PAD_B = 28, PAD_L = 50, PAD_R = 16;
  const innerW = W - PAD_L - PAD_R;
  const innerH = H - PAD_T - PAD_B;
  const svgNS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(svgNS, 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');

  const n = samples.length;
  if (n === 0) {
    const text = document.createElementNS(svgNS, 'text');
    text.setAttribute('x', W/2);
    text.setAttribute('y', H/2);
    text.setAttribute('text-anchor', 'middle');
    text.setAttribute('fill', '#86868b');
    text.setAttribute('font-size', '12');
    text.textContent = 'no data';
    svg.appendChild(text);
    return svg;
  }

  const yMin = 0;
  const yMax = Math.max(st.max || 0, ...samples) * 1.1 || 1;
  const yScale = v => PAD_T + innerH - (v - yMin) / (yMax - yMin) * innerH;

  const axisColor = '#1f1f24';
  const labelColor = '#86868b';
  const grid = document.createElementNS(svgNS, 'g');
  grid.setAttribute('stroke', axisColor);
  for (let i = 0; i <= 4; i++) {
    const v = yMax * i / 4;
    const y = yScale(v);
    const line = document.createElementNS(svgNS, 'line');
    line.setAttribute('x1', PAD_L);
    line.setAttribute('x2', W - PAD_R);
    line.setAttribute('y1', y);
    line.setAttribute('y2', y);
    line.setAttribute('stroke-width', '0.5');
    grid.appendChild(line);
    const lbl = document.createElementNS(svgNS, 'text');
    lbl.setAttribute('x', PAD_L - 4);
    lbl.setAttribute('y', y + 3.5);
    lbl.setAttribute('text-anchor', 'end');
    lbl.setAttribute('fill', labelColor);
    lbl.setAttribute('font-size', '10');
    lbl.setAttribute('font-family', 'monospace');
    lbl.textContent = v >= 1000 ? `${(v/1000).toFixed(1)}s` : `${Math.round(v)}ms`;
    grid.appendChild(lbl);
  }
  svg.appendChild(grid);

  const cx = PAD_L + innerW / 2;
  const boxW = 60;
  const minY = yScale(st.min || 0);
  const maxY = yScale(st.max || 0);
  const q1Y = yScale(st.q1 || 0);
  const q3Y = yScale(st.q3 || 0);
  const medY = yScale(st.median || 0);

  const wt = document.createElementNS(svgNS, 'line');
  wt.setAttribute('x1', cx); wt.setAttribute('x2', cx);
  wt.setAttribute('y1', maxY); wt.setAttribute('y2', q3Y);
  wt.setAttribute('stroke', color); wt.setAttribute('stroke-width', '1.5');
  svg.appendChild(wt);
  const wb = document.createElementNS(svgNS, 'line');
  wb.setAttribute('x1', cx); wb.setAttribute('x2', cx);
  wb.setAttribute('y1', q1Y); wb.setAttribute('y2', minY);
  wb.setAttribute('stroke', color); wb.setAttribute('stroke-width', '1.5');
  svg.appendChild(wb);
  for (const yv of [minY, maxY]) {
    const cap = document.createElementNS(svgNS, 'line');
    cap.setAttribute('x1', cx - 12); cap.setAttribute('x2', cx + 12);
    cap.setAttribute('y1', yv); cap.setAttribute('y2', yv);
    cap.setAttribute('stroke', color); cap.setAttribute('stroke-width', '1.5');
    svg.appendChild(cap);
  }
  const rect = document.createElementNS(svgNS, 'rect');
  rect.setAttribute('x', cx - boxW/2);
  rect.setAttribute('y', q3Y);
  rect.setAttribute('width', boxW);
  rect.setAttribute('height', Math.max(2, q1Y - q3Y));
  rect.setAttribute('fill', color);
  rect.setAttribute('fill-opacity', '0.18');
  rect.setAttribute('stroke', color);
  rect.setAttribute('stroke-width', '1.5');
  svg.appendChild(rect);
  const ml = document.createElementNS(svgNS, 'line');
  ml.setAttribute('x1', cx - boxW/2);
  ml.setAttribute('x2', cx + boxW/2);
  ml.setAttribute('y1', medY); ml.setAttribute('y2', medY);
  ml.setAttribute('stroke', color); ml.setAttribute('stroke-width', '3');
  svg.appendChild(ml);

  for (const s of samples) {
    const c = document.createElementNS(svgNS, 'circle');
    const jitter = (Math.random() - 0.5) * (boxW * 0.45);
    c.setAttribute('cx', cx + jitter);
    c.setAttribute('cy', yScale(s));
    c.setAttribute('r', 3);
    c.setAttribute('fill', color);
    c.setAttribute('fill-opacity', '0.9');
    c.setAttribute('stroke', '#000');
    c.setAttribute('stroke-width', '0.5');
    svg.appendChild(c);
  }

  const annot = document.createElementNS(svgNS, 'text');
  annot.setAttribute('x', cx + boxW/2 + 6);
  annot.setAttribute('y', medY + 3);
  annot.setAttribute('fill', color);
  annot.setAttribute('font-size', '10');
  annot.setAttribute('font-family', 'monospace');
  annot.setAttribute('font-weight', '700');
  annot.textContent = `med ${fmtMs(st.median || 0)}`;
  svg.appendChild(annot);

  return svg;
}

// ─────────────── Stats table ───────────────
function renderStatsTable(data) {
  const tbody = document.querySelector('#statsTable tbody');
  tbody.innerHTML = '';
  for (const sc of SCENARIOS) {
    const sd = (data.scenarios || {})[sc.key];
    const st = (sd && sd.writeStats) || {};
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="scenario">${sc.label}</td>
      <td class="num">${st.n || 0}</td>
      <td class="num">${fmtMs(st.min)}</td>
      <td class="num">${fmtMs(st.q1)}</td>
      <td class="num">${fmtMs(st.median)}</td>
      <td class="num">${fmtMs(st.mean)}</td>
      <td class="num">${fmtMs(st.q3)}</td>
      <td class="num">${fmtMs(st.p95)}</td>
      <td class="num">${fmtMs(st.max)}</td>
      <td class="num">${fmtMs(st.stdev)}</td>`;
    tbody.appendChild(tr);
  }
}

// ─────────────── Per-cluster breakdown table (NEW for v11) ───────────────
function renderPerClusterTable(data) {
  const host = document.getElementById('perCluster');
  if (!host) return;
  // Build a 5x3 grid: clusters on Y, scenarios on X.
  // For each cell we compute median + n + max from the rounds.
  const html = [];
  html.push('<table class="data-table">');
  html.push('<thead><tr><th>Cluster</th>');
  for (const sc of SCENARIOS) {
    html.push(`<th class="num" style="color:${sc.color}">${sc.label}<br/><small style="color:var(--dim);font-weight:400">median · n · max</small></th>`);
  }
  html.push('</tr></thead><tbody>');
  for (const cid of CLUSTERS) {
    html.push(`<tr><td><code>${cid}</code></td>`);
    for (const sc of SCENARIOS) {
      const sd = (data.scenarios || {})[sc.key];
      const rounds = (sd && sd.rounds || []).filter(r => r.cluster === cid);
      const samples = rounds.map(r => r.writeMaxMs).filter(v => v != null);
      if (samples.length === 0) {
        html.push('<td class="num">—</td>');
        continue;
      }
      const sorted = [...samples].sort((a, b) => a - b);
      const median = sorted.length % 2 === 1
        ? sorted[(sorted.length - 1) / 2]
        : (sorted[sorted.length / 2 - 1] + sorted[sorted.length / 2]) / 2;
      const max = Math.max(...samples);
      html.push(`<td class="num">
        <strong>${fmtMs(median)}</strong> · ${samples.length} · ${fmtMs(max)}
      </td>`);
    }
    html.push('</tr>');
  }
  html.push('</tbody></table>');
  host.innerHTML = html.join('');
}

// ─────────────── Per-round table ───────────────
function renderRoundsTable(data) {
  const tbody = document.querySelector('#roundsTable tbody');
  const toggle = document.getElementById('roundsToggle');
  tbody.innerHTML = '';
  let total = 0;
  for (const sc of SCENARIOS) {
    const sd = (data.scenarios || {})[sc.key];
    const rounds = (sd && sd.rounds) || [];
    for (const r of rounds) {
      total++;
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td class="scenario">${sc.label}</td>
        <td><code>${r.cluster || ''}</code></td>
        <td class="num">${r.round}</td>
        <td class="num">${fmtMs(r.writeMaxMs)}</td>
        <td class="num">${fmtMs(r.readMaxMs)}</td>
        <td><code style="font-size:0.78rem;color:var(--dim)">${r.runId||''}</code></td>`;
      tbody.appendChild(tr);
    }
  }
  if (toggle) toggle.textContent = `Show all ${total} rounds`;
}

document.addEventListener('DOMContentLoaded', load);

// ─────────────── v16 NEW: Instance × TPS matrix view ───────────────
// Renders two tables: instance sweep at TPS=1280, TPS sweep at 8X.
// Inserted at the bottom of the dashboard, just below per-round table.
function renderMatrixView(data) {
  const matrix = data.matrix_summary || {};
  const inst = matrix.instance_sweep_at_1280 || {};
  const tps = matrix.tps_sweep_at_8X || {};

  // Find the per-round table to anchor below
  const anchor = document.getElementById('roundsTable') || document.getElementById('content');
  if (!anchor) return;
  const parent = anchor.parentElement;

  // Build container
  const wrap = document.createElement('section');
  wrap.style.cssText = 'margin-top: 3rem;';
  wrap.innerHTML = `
    <h2 style="margin-bottom: 0.4rem;">v16 Matrix Sweep</h2>
    <p style="color: var(--dim); margin: 0 0 1.5rem; font-size: 0.95rem;">
      Full instance × TPS sweep (6 runs × 5 clusters × 3 scenarios = 90 measurements).
      Reboot ≈ 0 ms across all rows because v16 uses cluster topology with reader replica:
      reboot writer triggers cluster auto-failover, AWS JDBC wrapper transparently follows
      cluster endpoint. <strong>Not</strong> an instrumentation bug — it's the realistic
      production behavior under HSK's planned topology.
    </p>

    <h3 style="margin: 1rem 0 0.6rem; font-size: 1.05rem;">Instance sweep @ 1280 TPS</h3>
    <table id="matrixInstTable" class="table" style="width:100%; margin-bottom: 2rem;">
      <thead>
        <tr>
          <th>Writer</th>
          <th class="num">BG median</th>
          <th class="num">BG max</th>
          <th class="num">FO median</th>
          <th class="num">FO max</th>
          <th class="num">RB median</th>
          <th class="num">RB max</th>
          <th class="num">n</th>
        </tr>
      </thead>
      <tbody id="matrixInstBody"></tbody>
    </table>

    <h3 style="margin: 1rem 0 0.6rem; font-size: 1.05rem;">TPS sweep @ r7g.8xlarge</h3>
    <table id="matrixTpsTable" class="table" style="width:100%; margin-bottom: 2rem;">
      <thead>
        <tr>
          <th>TPS</th>
          <th class="num">BG median</th>
          <th class="num">BG max</th>
          <th class="num">FO median</th>
          <th class="num">FO max</th>
          <th class="num">RB median</th>
          <th class="num">RB max</th>
          <th class="num">n (BG/FO/RB)</th>
        </tr>
      </thead>
      <tbody id="matrixTpsBody"></tbody>
    </table>
  `;
  parent.appendChild(wrap);

  // Instance sweep
  const instBody = document.getElementById('matrixInstBody');
  const order = ['r7g.large', 'r7g.2xlarge', 'r7g.4xlarge', 'r7g.8xlarge'];
  for (const wic of order) {
    const r = inst[wic];
    if (!r) continue;
    const bg = r.BG || {}, fo = r.FO || {}, rb = r.RB || {};
    const totN = (bg.n || 0) + (fo.n || 0) + (rb.n || 0);
    instBody.innerHTML += `
      <tr>
        <td><code>${wic}</code></td>
        <td class="num">${fmtMs(bg.median)}</td>
        <td class="num">${fmtMs(bg.max)}</td>
        <td class="num">${fmtMs(fo.median)}</td>
        <td class="num">${fmtMs(fo.max)}</td>
        <td class="num">${fmtMs(rb.median)}</td>
        <td class="num">${fmtMs(rb.max)}</td>
        <td class="num">${totN}</td>
      </tr>
    `;
  }

  // TPS sweep
  const tpsBody = document.getElementById('matrixTpsBody');
  const tpsOrder = ['1280', '2560', '4000'];
  for (const t of tpsOrder) {
    const r = tps[t];
    if (!r) continue;
    const bg = r.BG || {}, fo = r.FO || {}, rb = r.RB || {};
    const ns = `${bg.n || 0}/${fo.n || 0}/${rb.n || 0}`;
    const headlineCls = (t === '4000') ? ' style="background: rgba(255, 159, 10, 0.05);"' : '';
    tpsBody.innerHTML += `
      <tr${headlineCls}>
        <td><strong>${t}${t === '4000' ? ' ⭐' : ''}</strong></td>
        <td class="num">${fmtMs(bg.median)}</td>
        <td class="num">${fmtMs(bg.max)}</td>
        <td class="num">${fmtMs(fo.median)}</td>
        <td class="num">${fmtMs(fo.max)}</td>
        <td class="num">${fmtMs(rb.median)}</td>
        <td class="num">${fmtMs(rb.max)}</td>
        <td class="num">${ns}</td>
      </tr>
    `;
  }
}
