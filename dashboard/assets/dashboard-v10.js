// dashboard-v10.js — v10-only dashboard logic.
// Loads dashboard/data/v10-only.json (produced by scripts/v10-extract-data.py)
// and renders: hero strip, config panel + highlights, 3 SVG boxplots, stats
// table, per-round table.
// Vanilla JS, no Chart.js dependency — boxplots are hand-drawn SVG.

const SCENARIOS = [
  { key: 'blue-green', label: 'Blue/Green',   sub: '10 rounds × switchover',   color: 'var(--green)',  cls: 'green' },
  { key: 'failover',   label: 'Failover',     sub: '10 rounds × writer demote', color: 'var(--orange)', cls: 'orange' },
  { key: 'reboot',     label: 'Reboot',       sub: '10 rounds × writer reboot', color: 'var(--cyan)',   cls: 'cyan' },
];

function fmtMs(ms) {
  if (ms == null || ms === '' || ms === '—') return '—';
  if (ms === 0) return '0 ms';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms/1000).toFixed(2)} s`;
}

function verdict(scenarioKey, st) {
  const med = st.median || 0;
  // Targets from EXPERIMENT-V10-PLAN.md
  const targets = {
    'blue-green': { ok: 4500, warn: 6000 },   // BG floor ~4s, warn if >6s
    'failover':   { ok: 8000, warn: 12000 },
    'reboot':     { ok: 500, warn: 2000 },
  };
  const t = targets[scenarioKey] || { ok: 5000, warn: 10000 };
  if (med <= t.ok)   return { label: 'good', cls: 'good' };
  if (med <= t.warn) return { label: 'ok',   cls: 'warn' };
  return { label: 'slow', cls: 'bad' };
}

async function load() {
  let data;
  try {
    const resp = await fetch('data/v10-only.json', { cache: 'no-store' });
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
  renderRoundsTable(data);
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
// A horizontal layout: one box per cell. Range from 0 to max-of-all-data.
function renderBoxplots(data) {
  const cont = document.getElementById('boxplots');
  cont.innerHTML = '';
  // Find the global max for shared y-axis-ish scale (per scenario)
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
  // Layout: vertical box plot, axis on left, single box centered
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

  // Scale: 0 → max + 10% padding
  const yMin = 0;
  const yMax = Math.max(st.max || 0, ...samples) * 1.1 || 1;
  const yScale = v => PAD_T + innerH - (v - yMin) / (yMax - yMin) * innerH;

  // Axis (left) — show min, median, max as labels + light gridlines
  const axisColor = '#1f1f24';
  const labelColor = '#86868b';
  const grid = document.createElementNS(svgNS, 'g');
  grid.setAttribute('stroke', axisColor);
  // 5 ticks
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

  // Box: x-center is innerW/2 + PAD_L, width 60
  const cx = PAD_L + innerW / 2;
  const boxW = 60;
  const minY = yScale(st.min || 0);
  const maxY = yScale(st.max || 0);
  const q1Y = yScale(st.q1 || 0);
  const q3Y = yScale(st.q3 || 0);
  const medY = yScale(st.median || 0);

  // Whisker top (max) → Q3 top
  const wt = document.createElementNS(svgNS, 'line');
  wt.setAttribute('x1', cx); wt.setAttribute('x2', cx);
  wt.setAttribute('y1', maxY); wt.setAttribute('y2', q3Y);
  wt.setAttribute('stroke', color); wt.setAttribute('stroke-width', '1.5');
  svg.appendChild(wt);
  // Whisker bottom (Q1) → min
  const wb = document.createElementNS(svgNS, 'line');
  wb.setAttribute('x1', cx); wb.setAttribute('x2', cx);
  wb.setAttribute('y1', q1Y); wb.setAttribute('y2', minY);
  wb.setAttribute('stroke', color); wb.setAttribute('stroke-width', '1.5');
  svg.appendChild(wb);
  // Caps
  for (const yv of [minY, maxY]) {
    const cap = document.createElementNS(svgNS, 'line');
    cap.setAttribute('x1', cx - 12); cap.setAttribute('x2', cx + 12);
    cap.setAttribute('y1', yv); cap.setAttribute('y2', yv);
    cap.setAttribute('stroke', color); cap.setAttribute('stroke-width', '1.5');
    svg.appendChild(cap);
  }
  // Box (Q1..Q3 with median line)
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
  // Median line (thicker)
  const ml = document.createElementNS(svgNS, 'line');
  ml.setAttribute('x1', cx - boxW/2);
  ml.setAttribute('x2', cx + boxW/2);
  ml.setAttribute('y1', medY); ml.setAttribute('y2', medY);
  ml.setAttribute('stroke', color); ml.setAttribute('stroke-width', '3');
  svg.appendChild(ml);

  // Sample dots (jittered)
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

  // Median annotation
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
        <td class="num">${r.round}</td>
        <td class="num">${fmtMs(r.writeMaxMs)}</td>
        <td class="num">${fmtMs(r.readMaxMs)}</td>
        <td><code style="font-size:0.8rem;color:var(--dim)">${r.wrapperJar||''}</code></td>
        <td><code style="font-size:0.78rem;color:var(--dim)">${r.runId||''}</code></td>`;
      tbody.appendChild(tr);
    }
  }
  toggle.textContent = `Show all ${total} rounds`;
}

document.addEventListener('DOMContentLoaded', load);
