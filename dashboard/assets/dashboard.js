// Dashboard logic — loads data/runs.json (produced by compare-runs.py)
// and renders charts + tables. Vanilla JS, no build step.

const COLORS = {
  baseline: '#ff453a',
  v1:       '#ff9f0a',
  v2:       '#ffd60a',
  v3:       '#a98cf2',
  v4:       '#30d158',
  v5:       '#2997ff',
  v6:       '#bf5af2',
  v7:       '#5e5ce6',
  default:  '#86868b',
};

function colorFor(cfg) {
  for (const [key, c] of Object.entries(COLORS)) {
    if (cfg.startsWith(key)) return c;
  }
  return COLORS.default;
}

function fmtMs(ms) {
  if (ms == null || ms === 0) return '—';
  return (ms / 1000).toFixed(2) + 's';
}

function verdict(writeMs) {
  if (writeMs === 0)        return { label: 'no fail',  cls: 'good' };
  if (writeMs <= 5_000)     return { label: 'good',     cls: 'good' };
  if (writeMs <= 10_000)    return { label: 'ok',       cls: 'warn' };
  if (writeMs <= 20_000)    return { label: 'slow',     cls: 'warn' };
  return                          { label: 'BAD',      cls: 'bad' };
}

async function load() {
  let data;
  try {
    const resp = await fetch('data/runs.json', { cache: 'no-store' });
    if (!resp.ok) throw new Error(resp.statusText);
    data = await resp.json();
  } catch (e) {
    document.getElementById('empty').style.display = 'block';
    return;
  }

  document.getElementById('content').style.display = 'block';
  document.getElementById('genAt').textContent = data.generatedAt || '—';

  renderHero(data);
  renderByConfigChart(data);
  renderStatsTable(data);
  renderComboChart(data);
  renderRunsTable(data);
}

function renderHero(data) {
  // Compute simple aggregates: total runs, configs covered, best/worst max
  const allWrite = [];
  for (const r of data.runs) {
    for (const l of (r.logs || [])) {
      if (l.writeMaxMs > 0) allWrite.push(l.writeMaxMs);
    }
  }
  allWrite.sort((a, b) => a - b);
  const min = allWrite[0] || 0;
  const max = allWrite[allWrite.length - 1] || 0;
  const median = allWrite.length ? allWrite[Math.floor(allWrite.length / 2)] : 0;

  const runs = data.runs.length;
  const configs = Object.keys(data.statsByConfig || {}).length;

  const hero = document.getElementById('hero');
  hero.innerHTML = '';
  const cards = [
    { label: 'Test runs',  num: runs,                     cls: '',        sub: configs + ' configs' },
    { label: 'Best write', num: fmtMs(min),               cls: 'green',   sub: 'min observed' },
    { label: 'Median',     num: fmtMs(median),            cls: 'orange',  sub: 'across all logs' },
    { label: 'Worst',      num: fmtMs(max),               cls: 'red',     sub: 'max observed' },
  ];
  for (const c of cards) {
    const el = document.createElement('div');
    el.className = 'card';
    el.innerHTML = `
      <div class="label">${c.label}</div>
      <div class="num ${c.cls}">${c.num}</div>
      <div class="sub">${c.sub}</div>`;
    hero.appendChild(el);
  }
}

function renderByConfigChart(data) {
  const cfgs = Object.keys(data.statsByConfig || {}).sort();
  const samplesByCfg = {};
  for (const r of data.runs) {
    const cfg = r.config || 'unknown';
    samplesByCfg[cfg] = samplesByCfg[cfg] || [];
    for (const l of (r.logs || [])) {
      if (l.writeMaxMs > 0) samplesByCfg[cfg].push(l.writeMaxMs);
    }
  }

  // Render each config's samples as a separate dataset on the same chart,
  // using sample index as x-axis. Helps spot outliers.
  const datasets = cfgs.map(cfg => ({
    label: cfg,
    data: (samplesByCfg[cfg] || []).map((v, i) => ({ x: i + 1, y: v / 1000 })),
    borderColor: colorFor(cfg),
    backgroundColor: colorFor(cfg),
    pointRadius: 5,
    pointHoverRadius: 7,
    showLine: false,
  }));

  new Chart(document.getElementById('byConfigChart'), {
    type: 'scatter',
    data: { datasets },
    options: {
      responsive: true,
      plugins: {
        legend: { labels: { color: '#86868b' } },
        tooltip: {
          callbacks: {
            label: ctx => `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(2)}s`,
          },
        },
      },
      scales: {
        x: {
          title: { display: true, text: 'sample #', color: '#86868b' },
          ticks: { color: '#86868b' },
          grid:  { color: '#1f1f24' },
        },
        y: {
          title: { display: true, text: 'write downtime (s)', color: '#86868b' },
          ticks: { color: '#86868b' },
          grid:  { color: '#1f1f24' },
          beginAtZero: true,
        },
      },
    },
  });
}

function renderStatsTable(data) {
  const tbody = document.querySelector('#statsTable tbody');
  tbody.innerHTML = '';
  for (const [cfg, s] of Object.entries(data.statsByConfig || {})) {
    const w = s.write || {};
    if (!w.count) continue;
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="cfg">${cfg}</td>
      <td class="num">${w.count}</td>
      <td class="num">${fmtMs(w.min)}</td>
      <td class="num">${fmtMs(w.median)}</td>
      <td class="num">${fmtMs(w.mean)}</td>
      <td class="num">${fmtMs(w.p95)}</td>
      <td class="num">${fmtMs(w.max)}</td>
      <td class="num">${fmtMs(w.stdev)}</td>`;
    tbody.appendChild(tr);
  }
}

function renderComboChart(data) {
  const combos = Object.keys(data.statsByCombo || {}).sort();
  const labels = combos;
  const minData = combos.map(c => (data.statsByCombo[c].min || 0) / 1000);
  const medData = combos.map(c => (data.statsByCombo[c].median || 0) / 1000);
  const maxData = combos.map(c => (data.statsByCombo[c].max || 0) / 1000);

  new Chart(document.getElementById('comboChart'), {
    type: 'bar',
    data: {
      labels,
      datasets: [
        { label: 'min',    data: minData, backgroundColor: '#30d158' },
        { label: 'median', data: medData, backgroundColor: '#2997ff' },
        { label: 'max',    data: maxData, backgroundColor: '#ff453a' },
      ],
    },
    options: {
      responsive: true,
      plugins: { legend: { labels: { color: '#86868b' } } },
      scales: {
        x: { ticks: { color: '#86868b' }, grid: { color: '#1f1f24' } },
        y: {
          title: { display: true, text: 'seconds', color: '#86868b' },
          ticks: { color: '#86868b' }, grid: { color: '#1f1f24' },
          beginAtZero: true,
        },
      },
    },
  });
}

function renderRunsTable(data) {
  const tbody = document.querySelector('#runsTable tbody');
  tbody.innerHTML = '';
  for (const r of data.runs) {
    let writeMax = 0;
    let readMax = 0;
    for (const l of (r.logs || [])) {
      if (l.writeMaxMs > writeMax) writeMax = l.writeMaxMs;
      if (l.readMaxMs  > readMax)  readMax  = l.readMaxMs;
    }
    const v = verdict(writeMax);
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><code style="font-size:0.85rem;color:#86868b">${r.id}</code></td>
      <td class="cfg">${r.config || '—'}</td>
      <td>${r.scenario || '—'}</td>
      <td class="num">${r.round || '—'}</td>
      <td class="num">${fmtMs(writeMax)}</td>
      <td class="num">${fmtMs(readMax)}</td>
      <td><span class="pill ${v.cls}">${v.label}</span></td>`;
    tbody.appendChild(tr);
  }
}

document.addEventListener('DOMContentLoaded', load);
