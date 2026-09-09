/* Hoodwall Intelligence dashboard.
   Plain ES2020, no build step, no CDN. Mouse-first: sidebar navigation,
   clickable table headers, a search bar that filters the active view.
   Keyboard shortcuts (1-9, /, Esc) still work for anyone used to them, but
   nothing requires the keyboard - a click gets you everywhere. */
'use strict';

const VIEWS = ['DASH', 'WAL', 'ENT', 'NAR', 'MAP', 'PNL', 'SENT', 'CHN', 'SRC'];

const state = {
  kb: null, meta: null, scored: [], graph: null, sentiment: null,
  view: 'DASH', chains: new Set(), query: '',
  sort: {}, selected: {}, graphInstance: null,
};

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const fmt = (n, d = 0) => (n === null || n === undefined || Number.isNaN(Number(n)))
  ? '—' : Number(n).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });

function fmtUsd(n) {
  if (n === null || n === undefined) return '—';
  const v = Number(n), a = Math.abs(v);
  if (a >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
  if (a >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
  if (a >= 1) return v.toFixed(2);
  return v.toPrecision(3);
}

const short = (a) => (a && a.length > 14 ? `${a.slice(0, 8)}…${a.slice(-6)}` : a || '');

/* A label is either curated (asserted) or observed (measured by an
   adapter). The color distinguishes assertion from evidence. */
function labelTag(l) {
  const name = typeof l === 'string' ? l : l.name;
  const observed = typeof l === 'object' && l.source === 'observed';
  const title = (typeof l === 'object' && l.evidence) ? ` title="${esc(l.evidence)}"` : '';
  return `<span class="tag${observed ? ' obs' : ''}"${title}>${esc(name)}</span>`;
}

/* ---------------- chain avatars ---------------- */
// A small fixed palette so the same chain always gets the same color,
// letting the eye scan a table by color the way CMC/Nansen coin icons do.
const CHAIN_COLOR = {
  robinhood: '#4c5fea', ethereum: '#627eea', arbitrum: '#28a0f0', base: '#0052ff',
  optimism: '#ff0420', hyperliquid: '#0a8f6b', solana: '#9945ff', bitcoin: '#f7931a',
};
function chainAvatar(chain) {
  const color = CHAIN_COLOR[chain] || '#6b7280';
  const letter = (chain || '?').slice(0, 2).toUpperCase();
  return `<span class="avatar" style="background:${color}">${esc(letter)}</span>`;
}
function entityAvatar(name) {
  const letter = (name || '?').trim().slice(0, 1).toUpperCase();
  let hash = 0;
  for (const ch of (name || '')) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  const palette = ['#4c5fea', '#7c3aed', '#0ea5a4', '#e0293f', '#b8860b', '#2563eb', '#16a34a'];
  return `<span class="avatar" style="background:${palette[hash % palette.length]}">${esc(letter)}</span>`;
}

/* ---------------- boot ---------------- */

async function boot() {
  applyStoredTheme();
  const bust = `?v=${Date.now()}`;
  try {
    const [kb, meta] = await Promise.all([
      fetch(`data/kb.json${bust}`).then((r) => r.json()),
      fetch(`data/meta.json${bust}`).then((r) => r.json()),
    ]);
    state.kb = kb; state.meta = meta;
  } catch {
    $('panel').innerHTML = `<div class="empty"><b>No data yet.</b><br>
      The pipeline hasn't published anything — run it from Actions, or
      locally with <code>python run.py all</code>.</div>`;
    $('kb-health').textContent = ''; renderIcons();
    return;
  }
  try { state.scored = await fetch(`data/wallets.json${bust}`).then((r) => r.json()); }
  catch { state.scored = []; }
  try { state.graph = await fetch(`data/graph.json${bust}`).then((r) => r.json()); }
  catch { state.graph = null; }
  try { state.sentiment = await fetch(`data/sentiment.json${bust}`).then((r) => r.json()); }
  catch { state.sentiment = null; }

  renderIcons();
  renderChrome();
  setView('DASH');
}

function renderChrome() {
  const m = state.meta;
  if (m.title) document.title = m.title;
  $('updated').textContent = m.generated_at
    ? `Updated ${new Date(m.generated_at).toLocaleString()}` : '';

  const errs = (state.kb.errors || []).length;
  const health = $('kb-health');
  health.textContent = errs ? `${errs} KB issue${errs > 1 ? 's' : ''}` : 'Live';
  health.className = `health ${errs ? 'bad' : 'ok'}`;

  $('ticker').innerHTML = (m.market || []).map((t) => {
    const up = t.change_24h === null || t.change_24h === undefined ? null : t.change_24h >= 0;
    const chg = up === null ? '' :
      `<span class="chg ${up ? 'pos' : 'neg'}">${up ? '▲' : '▼'} ${Math.abs(t.change_24h).toFixed(1)}%</span>`;
    return `<span class="tick"><span class="sym">${esc(t.symbol)}</span>
      <span class="px">$${fmtUsd(t.price_usd)}</span> ${chg}</span>`;
  }).join('') || '<span class="tick dim">Market data not configured yet</span>';
}

/* ---------------- theme ---------------- */

function applyStoredTheme() {
  let stored = null;
  try { stored = localStorage.getItem('hoodwall-theme'); } catch { /* private mode */ }
  if (stored === 'light' || stored === 'dark') document.documentElement.dataset.theme = stored;
}
function toggleTheme() {
  const current = document.documentElement.dataset.theme ||
    (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  const next = current === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem('hoodwall-theme', next); } catch { /* private mode */ }
}

/* ---------------- inline icon set ----------------
   No icon font / CDN: a tiny hand-drawn stroke set as data-URI masks,
   applied via CSS custom properties so both the sidebar and the topbar
   can share them without duplicating markup. */
const ICONS = {
  grid: 'M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z',
  wallet: 'M4 7a2 2 0 0 1 2-2h11a1 1 0 0 1 1 1v2M4 7v10a2 2 0 0 0 2 2h13a1 1 0 0 0 1-1v-4M4 7l2-3h9M15 13h4v3h-4a1.5 1.5 0 0 1 0-3z',
  users: 'M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM3 20c0-3.3 2.7-6 6-6s6 2.7 6 6M16.5 8a2.5 2.5 0 1 0 0-5M18 20c0-2.5-1.5-4.7-3.7-5.6',
  doc: 'M6 3h9l4 4v13a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zM14 3v5h5M8 12h8M8 16h8M8 8h3',
  share: 'M6 12a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zM18 7.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zM18 19a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zM8.2 10.8l7.6-3.6M8.2 13.2l7.6 4.4',
  dollar: 'M12 2v20M17 6.5c0-1.9-2.2-3.5-5-3.5s-5 1.4-5 3.5 2.2 3 5 3.5 5 1.6 5 3.5-2.2 3.5-5 3.5-5-1.6-5-3.5',
  pulse: 'M3 12h4l2-7 4 14 2-7h6',
  link: 'M9 15l6-6M8.5 8.5l-2 2a3.5 3.5 0 0 0 5 5l2-2M15.5 15.5l2-2a3.5 3.5 0 0 0-5-5l-2 2',
  stack: 'M12 3l9 5-9 5-9-5 9-5zM3 13l9 5 9-5M3 18l9 5 9-5',
  sun: 'M12 5V3M12 21v-2M5 12H3M21 12h-2M6.3 6.3 4.9 4.9M19.1 19.1l-1.4-1.4M6.3 17.7l-1.4 1.4M19.1 4.9l-1.4 1.4M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z',
  moon: 'M20 14.5A8.5 8.5 0 1 1 9.5 4a7 7 0 0 0 10.5 10.5z',
  menu: 'M3 6h18M3 12h18M3 18h18',
  search: 'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3',
  x: 'M6 6l12 12M18 6 6 18',
  cursor: 'M5 3l14 6-6 2-2 6-6-14z',
  chevron: 'M6 9l6 6 6-6',
};
function svgDataUri(path) {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" ` +
    `stroke="black" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="${path}"/></svg>`;
  return `url("data:image/svg+xml,${encodeURIComponent(svg)}")`;
}
function renderIcons() {
  document.querySelectorAll('[data-icon]').forEach((el) => {
    const name = el.dataset.icon;
    if (ICONS[name]) el.style.setProperty('--icon', svgDataUri(ICONS[name]));
  });
  const root = document.documentElement.style;
  root.setProperty('--icon-sun', svgDataUri(ICONS.sun));
  root.setProperty('--icon-moon', svgDataUri(ICONS.moon));
  root.setProperty('--icon-menu', svgDataUri(ICONS.menu));
  root.setProperty('--icon-search', svgDataUri(ICONS.search));
  root.setProperty('--icon-x', svgDataUri(ICONS.x));
  root.setProperty('--icon-cursor', svgDataUri(ICONS.cursor));
  root.setProperty('--icon-chevron', svgDataUri(ICONS.chevron));
}

/* ---------------- view routing ---------------- */

const TITLES = {
  DASH: ['Overview', 'What the pipeline is tracking right now, at a glance.'],
  WAL: ['Wallets', 'Every tracked wallet across all chains.'],
  ENT: ['Entities', 'Wallets grouped by the person or organization behind them.'],
  NAR: ['Narratives', 'Theses being tracked, with their measured outcomes.'],
  MAP: ['Relationship Map', 'Solid = on-chain transfer (evidence). Dashed = shared attribute (inference).'],
  PNL: ['Positions & P&L', 'Live equity, leverage and realized/unrealized P&L, where observed.'],
  SENT: ['Sentiment', 'A confidence-weighted read across five independent sources.'],
  CHN: ['Chains', 'Coverage per chain and whether it has a live data adapter.'],
  SRC: ['Sources', 'Feeds this system reads, with their measured reliability.'],
};

function setView(view) {
  if (!VIEWS.includes(view)) return false;
  state.view = view;
  document.querySelectorAll('.navitem').forEach(
    (el) => el.setAttribute('aria-current', String(el.dataset.view === view)));
  const [title, sub] = TITLES[view] || [view, ''];
  $('page-title').textContent = title;
  $('page-sub').textContent = sub;
  closeSidebar();
  render();
  return true;
}

function render() {
  const renderers = { DASH: viewDash, WAL: viewWallets, ENT: viewEntities,
                      NAR: viewNarratives, MAP: viewMap, PNL: viewPnl,
                      SENT: viewSentiment, CHN: viewChains, SRC: viewSources };
  $('page-actions').innerHTML = '';
  renderers[state.view]();
}

/* ---------------- filtering + sorting ---------------- */

function chainOk(chain) { return state.chains.size === 0 || state.chains.has(chain); }
function matches(haystack) {
  if (!state.query) return true;
  return haystack.toLowerCase().includes(state.query.toLowerCase());
}
function sortRows(rows, view, fallbackKey, defaultDir = 'desc') {
  const s = state.sort[view];
  const key = s?.key || fallbackKey;
  const dir = (s?.dir || defaultDir) === 'asc' ? 1 : -1;
  return rows.slice().sort((a, b) => {
    const x = a[key], y = b[key];
    if (x === null || x === undefined) return 1;
    if (y === null || y === undefined) return -1;
    if (typeof x === 'string') return dir * x.localeCompare(y) * -1;
    return dir * (x - y);
  });
}

function chainFilterRow() {
  const byChain = state.kb.stats.wallets_by_chain || {};
  const chips = Object.keys(state.kb.chains).filter((c) => byChain[c]).map((c) => `
    <button class="chip" data-chain="${esc(c)}" aria-pressed="${state.chains.has(c)}">
      ${esc((state.kb.chains[c].name || c))}<span class="n">${byChain[c]}</span></button>`).join('');
  return `<div class="filters" id="chainfilter">${chips}
    <button class="ghost-btn" id="clear-filters" type="button">Reset</button></div>`;
}

function table(view, columns, rows, rowFn, opts = {}) {
  const s = state.sort[view];
  const head = columns.map((c) => {
    const sorted = s && s.key === c.key;
    const aria = sorted ? ` aria-sort="${s.dir === 'asc' ? 'ascending' : 'descending'}"` : '';
    return `<th class="${c.num ? 'num' : ''}" data-key="${esc(c.key)}"${aria}>${esc(c.label)}</th>`;
  }).join('');

  const prefix = opts.prefix || '';
  if (!rows.length) {
    $('panel').innerHTML = prefix + `<div class="empty">
      <b>No rows match.</b><br>${esc(opts.emptyHint || 'Try clearing filters or the search.')}</div>`;
    return;
  }
  $('panel').innerHTML = prefix +
    `<div class="table-wrap"><table><thead><tr>${head}</tr></thead>
     <tbody>${rows.map(rowFn).join('')}</tbody></table></div>`;
  restoreSelection();
}

/* ---------------- DASH ---------------- */

function viewDash() {
  const s = state.kb.stats || {};
  const sent = state.sentiment?.overall;

  const tiles = [
    ['Wallets', s.wallets, `${s.resolved} resolved · ${s.unresolved} masked`, ''],
    ['Entities', s.entities, `${(state.kb.entities || []).filter((e) => e.cluster_size > 1).length} multi-wallet clusters`, ''],
    ['Narratives', s.narratives, Object.entries(s.narratives_by_status || {}).map(([k, v]) => `${v} ${k}`).join(' · '), ''],
    ['Chains tracked', s.chains, `${Object.keys(s.wallets_by_chain || {}).length} with wallets`, ''],
    ['Scored on-chain', (state.scored || []).length, 'live enrichment', ''],
    ['Sentiment', sent ? sent.label : '—', sent ? `confidence ${sent.confidence.toFixed(2)}` : 'no data yet',
      sent ? (sent.label === 'bullish' ? 'green' : sent.label === 'bearish' ? 'red' : '') : ''],
  ].map(([k, v, sub, cls]) => `<div class="tile"><div class="k">${esc(k)}</div>
      <div class="v ${cls}">${typeof v === 'number' ? fmt(v) : esc(String(v))}</div>
      <div class="sub">${esc(sub || '')}</div></div>`).join('');

  const bars = (obj, total) => Object.entries(obj || {}).sort((a, b) => b[1] - a[1]).map(([k, v]) => `
    <div class="bar"><span class="lbl">${esc(k.replace(/_/g, ' '))}</span>
      <span class="track"><span class="fill" style="width:${(v / total * 100).toFixed(1)}%"></span></span>
      <span class="n">${v}</span></div>`).join('') || '<p class="dim" style="font-size:12.5px">No data yet.</p>';

  const maxChain = Math.max(1, ...Object.values(s.wallets_by_chain || {}));
  const maxLabel = Math.max(1, ...Object.values(s.wallets_by_label || {}));
  const clusters = (state.kb.entities || []).filter((e) => e.cluster_size > 0).slice(0, 6);
  const maxCluster = Math.max(1, clusters[0]?.cluster_size || 1);

  const gstats = state.graph?.stats;
  const coverage = gstats ? Math.round((gstats.transfer_coverage || 0) * 100) : null;

  $('panel').innerHTML = `
    <div class="tiles">${tiles}</div>
    <div class="grid-2">
      <div class="card"><div class="card-head"><h3>Wallets by chain</h3></div>
        <div class="card-body">${bars(s.wallets_by_chain, maxChain)}</div></div>
      <div class="card"><div class="card-head"><h3>Wallets by label</h3></div>
        <div class="card-body">${bars(s.wallets_by_label, maxLabel)}</div></div>
      <div class="card"><div class="card-head"><h3>Largest entity clusters</h3></div>
        <div class="card-body">${clusters.length ? clusters.map((e) => `
          <div class="bar"><span class="lbl">${esc(e.name)}</span>
            <span class="track"><span class="fill" style="width:${(e.cluster_size / maxCluster * 100).toFixed(0)}%"></span></span>
            <span class="n">${e.cluster_size}</span></div>`).join('') : '<p class="dim" style="font-size:12.5px">None yet.</p>'}</div></div>
      <div class="card"><div class="card-head"><h3>Relationship graph</h3></div>
        <div class="card-body">
          ${gstats ? `<div class="kv" style="margin-bottom:4px">
            <dt>Nodes</dt><dd>${gstats.nodes}</dd>
            <dt>Transfer edges (evidence)</dt><dd>${gstats.transfer_edges}</dd>
            <dt>Behavioural edges (inferred)</dt><dd>${gstats.behavioural_edges}</dd>
            <dt>Clusters</dt><dd>${gstats.clusters}</dd>
            <dt>Evidence share</dt><dd>${coverage}%</dd></div>
            <button class="linkbtn" data-goto-view="MAP">Open the map →</button>`
            : '<p class="dim" style="font-size:12.5px">Not built yet.</p>'}
        </div></div>
    </div>`;
}

/* ---------------- WAL ---------------- */

function viewWallets() {
  $('page-actions').innerHTML = '';
  const rows = (state.kb.wallets || []).filter((w) =>
    chainOk(w.chain) && matches(
      [w.address, w.masked, w.entity, w.handle, (w.labels || []).map((l) => l.name || l).join(' '), w.source].join(' ')));

  table('WAL', [
    { key: 'chain', label: 'Chain' }, { key: 'display', label: 'Address' },
    { key: 'entity', label: 'Entity' }, { key: 'labels', label: 'Labels' },
    { key: 'confidence', label: 'Confidence' }, { key: 'score', label: 'Score', num: true },
    { key: 'source', label: 'Source' },
  ], sortRows(rows, 'WAL', 'chain', 'asc'), (w) => {
    const obs = w.observed;
    const score = obs ? obs.smart_score : null;
    const tierClass = obs ? obs.tier : '';
    return `<tr data-key="${esc(w.key)}" data-kind="wallet">
      <td>${chainAvatar(w.chain)}</td>
      <td><span class="${w.resolved ? 'addr' : 'addr masked'}">${esc(w.display)}</span>
        ${!w.resolved ? '<div class="sub-line">masked</div>' : ''}</td>
      <td>${w.entity ? esc(w.entity) : '<span class="dim">—</span>'}</td>
      <td>${(w.labels || []).slice(0, 3).map(labelTag).join('') || '<span class="dim">—</span>'}</td>
      <td><span class="pill cf-${esc(w.confidence || 'reported')}">${esc(w.confidence || '—')}</span></td>
      <td class="num">${score === null ? '<span class="dim">—</span>' : `
        <span class="score-cell"><span class="score-num">${score.toFixed(0)}</span>
        <span class="score-bar"><i style="width:${score}%;background:var(--${tierClass === 'elite' ? 'green' : tierClass === 'watch' ? 'blue' : tierClass === 'candidate' ? 'amber' : 'gray'})"></i></span></span>`}</td>
      <td class="dim">${esc(w.source || '—')}</td></tr>`;
  }, { prefix: chainFilterRow() });
}

/* ---------------- ENT ---------------- */

function viewEntities() {
  const rows = (state.kb.entities || []).filter((e) =>
    matches([e.name, e.key, e.type, Object.values(e.handles || {}).join(' ')].join(' ')));

  table('ENT', [
    { key: 'name', label: 'Entity' }, { key: 'type', label: 'Type' },
    { key: 'cluster_size', label: 'Wallets', num: true },
    { key: 'confidence', label: 'Confidence' }, { key: 'holdings', label: 'Holdings' },
  ], sortRows(rows, 'ENT', 'cluster_size'), (e) => {
    const hold = (e.holdings || []).map((h) => `${fmtUsd(h.amount)} ${esc(h.asset || '')}`).join(', ');
    return `<tr data-key="${esc(e.key)}" data-kind="entity">
      <td><div class="rowmain">${entityAvatar(e.name)}<b>${esc(e.name)}</b></div></td>
      <td class="dim">${esc(e.type)}</td>
      <td class="num">${e.cluster_size || '<span class="dim">0</span>'}</td>
      <td><span class="pill cf-${esc(e.confidence)}">${esc(e.confidence)}</span></td>
      <td class="dim">${hold || '—'}</td></tr>`;
  });
}

/* ---------------- NAR ---------------- */

function viewNarratives() {
  const rows = (state.kb.narratives || []).filter((n) =>
    matches([n.title, n.key, (n.tokens || []).join(' '), (n.chains || []).join(' ')].join(' ')));

  table('NAR', [
    { key: 'title', label: 'Narrative' }, { key: 'status', label: 'Status' },
    { key: 'conviction', label: 'Conviction' }, { key: 'chains', label: 'Chains' },
    { key: 'tokens', label: 'Tokens', num: true }, { key: 'opened', label: 'Opened' },
  ], sortRows(rows, 'NAR', 'opened', 'asc'), (n) => `
    <tr data-key="${esc(n.key)}" data-kind="narrative">
      <td><b>${esc(n.title)}</b></td>
      <td><span class="pill st-${esc(n.status)}">${esc(n.status)}</span></td>
      <td class="dim">${esc(n.conviction || 'none')}</td>
      <td class="dim">${esc((n.chains || []).join(', ')) || '—'}</td>
      <td class="num">${(n.tokens || []).length}</td>
      <td class="dim">${esc(n.opened || '—')}</td></tr>`);
}

/* ---------------- MAP ---------------- */

function viewMap() {
  const g = state.graph;
  if (!g || !g.nodes || !g.nodes.length) {
    $('panel').innerHTML = '<div class="empty"><b>No graph yet.</b><br>Run the pipeline to build it.</div>';
    return;
  }
  const st = g.stats || {};
  const coverage = ((st.transfer_coverage || 0) * 100).toFixed(0);

  $('panel').innerHTML = `
    <div class="maplegend">
      <span><i class="ln"></i> Transfer (${st.transfer_edges || 0}) — value moved on-chain</span>
      <span><i class="ln dash"></i> Behavioural (${st.behavioural_edges || 0}) — shared attribute</span>
      <span class="dim">${st.clusters || 0} clusters · ${coverage}% of edges are evidence</span>
      <span class="spacer"></span>
      <span class="dim">drag to pan · scroll to zoom · click a node</span>
    </div>
    <div class="mapwrap"><canvas id="mapcanvas"></canvas></div>
    <div class="clusterbar" id="clusterbar"></div>`;

  const canvas = $('mapcanvas');
  const styles = getComputedStyle(document.documentElement);
  const palette = {
    bg: styles.getPropertyValue('--surface').trim() || '#fff',
    transfer: styles.getPropertyValue('--brand').trim() || '#4c5fea',
    behavioural: styles.getPropertyValue('--border-strong').trim() || '#ccc',
    text: styles.getPropertyValue('--text-2').trim() || '#555',
    nodes: ['#4c5fea', '#0ea5a4', '#7c3aed', '#e0293f', '#b8860b', '#2563eb',
            '#16a34a', '#db2777', '#0891b2', '#65a30d'],
  };
  state.graphInstance = createGraph(canvas, g, { onSelect: (n) => openDetail('wallet', n.id), palette });

  $('clusterbar').innerHTML = state.graphInstance.legend().slice(0, 14).map((c) => `
    <button class="cchip" data-cluster="${esc(c.members[0])}">
      <i style="background:${c.color}"></i>${esc(c.name)}<span class="n">${c.size}</span></button>`).join('');
}

/* ---------------- PNL ---------------- */

function viewPnl() {
  const rows = (state.kb.wallets || [])
    .filter((w) => w.observed && chainOk(w.chain))
    .filter((w) => matches([w.address, w.entity, w.handle].join(' ')))
    .map((w) => {
      const o = w.observed;
      return { ...w, equity: o.equity ?? null, unrealized: o.unrealized_pnl ?? null,
        realized: o.realized_pnl ?? null, win_rate: o.win_rate ?? null,
        leverage: o.leverage ?? null, positions: o.position_count ?? 0, score: o.smart_score };
    });

  if (!rows.length) {
    $('panel').innerHTML = `<div class="empty"><b>No live position data yet.</b><br>
      Run <code>python run.py hyperliquid</code> (no key needed), or set
      <code>BLOCKSCOUT_API_KEY</code> for EVM chains.</div>`;
    return;
  }

  const total = rows.reduce((a, r) => a + (r.equity || 0), 0);
  const unreal = rows.reduce((a, r) => a + (r.unrealized || 0), 0);
  const real = rows.reduce((a, r) => a + (r.realized || 0), 0);
  const tiles = `<div class="tiles">
    <div class="tile"><div class="k">Tracked equity</div><div class="v">$${fmtUsd(total)}</div></div>
    <div class="tile"><div class="k">Unrealized</div><div class="v ${unreal >= 0 ? 'green' : 'red'}">${unreal >= 0 ? '+' : '-'}$${fmtUsd(Math.abs(unreal))}</div></div>
    <div class="tile"><div class="k">Realized</div><div class="v ${real >= 0 ? 'green' : 'red'}">${real >= 0 ? '+' : '-'}$${fmtUsd(Math.abs(real))}</div></div>
    <div class="tile"><div class="k">Positions tracked</div><div class="v">${rows.length}</div></div>
  </div>`;

  const sorted = sortRows(rows, 'PNL', 'equity');
  $('panel').innerHTML = tiles + `<div class="table-wrap"><table><thead><tr>
      <th data-key="display">Wallet</th><th data-key="entity">Entity</th>
      <th class="num" data-key="equity">Equity</th><th class="num" data-key="unrealized">Unrealized</th>
      <th class="num" data-key="realized">Realized</th><th class="num" data-key="win_rate">Win rate</th>
      <th class="num" data-key="leverage">Leverage</th><th class="num" data-key="positions">Positions</th>
      <th class="num" data-key="score">Score</th></tr></thead><tbody>
    ${sorted.map((r) => `<tr data-key="${esc(r.key)}" data-kind="wallet">
      <td><div class="rowmain">${chainAvatar(r.chain)}<span class="addr">${esc(r.display)}</span></div></td>
      <td>${r.entity ? esc(r.entity) : '<span class="dim">—</span>'}</td>
      <td class="num">$${fmtUsd(r.equity)}</td>
      <td class="num ${r.unrealized >= 0 ? 'pos' : 'neg'}">${r.unrealized >= 0 ? '+' : '-'}$${fmtUsd(Math.abs(r.unrealized || 0))}</td>
      <td class="num ${r.realized >= 0 ? 'pos' : 'neg'}">${r.realized >= 0 ? '+' : '-'}$${fmtUsd(Math.abs(r.realized || 0))}</td>
      <td class="num">${r.win_rate === null ? '<span class="dim">—</span>' : (r.win_rate * 100).toFixed(0) + '%'}</td>
      <td class="num ${r.leverage >= 20 ? 'neg' : ''}">${r.leverage ? r.leverage.toFixed(1) + 'x' : '—'}</td>
      <td class="num dim">${r.positions}</td>
      <td class="num"><b>${r.score === null ? '—' : r.score.toFixed(0)}</b></td></tr>`).join('')}
    </tbody></table></div>`;
  restoreSelection();
}

/* ---------------- SENT ---------------- */

function viewSentiment() {
  const s = state.sentiment;
  if (!s || !s.overall) {
    $('panel').innerHTML = '<div class="empty"><b>No sentiment data yet.</b><br>Run the pipeline.</div>';
    return;
  }
  const o = s.overall;
  const cls = o.label === 'bullish' ? 'pos' : o.label === 'bearish' ? 'neg' : 'dim';
  const pct = ((o.score + 1) / 2) * 100;
  const gauge = `<div class="gauge"><div class="gauge-mid"></div>
      <div class="gauge-fill ${o.score >= 0 ? 'pos' : 'neg'}"
           style="left:${Math.min(50, pct)}%;width:${Math.abs(pct - 50)}%"></div></div>`;

  const signals = o.signals.map((sig) => `
    <div class="sigrow">
      <span class="siglabel">${esc(sig.source)}</span>
      <span class="sigscore ${sig.confidence < 0.05 ? 'dim' : sig.score > 0 ? 'pos' : sig.score < 0 ? 'neg' : 'dim'}">
        ${sig.confidence < 0.05 ? 'No data' : (sig.score >= 0 ? '+' : '') + sig.score.toFixed(2)}</span>
      <span class="sigconf">conf ${sig.confidence.toFixed(2)}</span>
      <span class="sigev">${esc(sig.evidence)}</span></div>`).join('');

  const narratives = Object.entries(s.by_narrative || {}).map(([key, n]) => `
    <tr data-key="${esc(key)}" data-kind="narrative">
      <td><b>${esc(key)}</b></td>
      <td><span class="pill ${n.label === 'bullish' ? 'elite' : n.label === 'bearish' ? 'archive' : 'watch'}">${esc(n.label)}</span></td>
      <td class="num">${n.score >= 0 ? '+' : ''}${n.score.toFixed(2)}</td>
      <td class="num dim">${n.confidence.toFixed(2)}</td>
      <td class="dim">${esc((n.reporting_sources || []).join(', ')) || 'none'}</td></tr>`).join('');

  $('panel').innerHTML = `
    <div class="card" style="margin-bottom:18px"><div class="card-body">
      <div class="sentbig ${cls}">${esc(o.label)}<span class="sentnum">${o.score >= 0 ? '+' : ''}${o.score.toFixed(2)}</span></div>
      ${gauge}
      <p class="dim" style="font-size:12.5px;margin:10px 0 0">confidence ${o.confidence.toFixed(2)}
        · ${o.reporting_sources.length}/${o.signals.length} sources reporting</p>
      <p class="note" style="margin-top:12px">${esc(o.narrative)}</p>
    </div></div>
    <div class="card" style="margin-bottom:18px">
      <div class="card-head"><h3>Signals</h3></div>
      <div class="card-body">${signals}</div>
    </div>
    ${narratives ? `<div class="table-wrap"><table><thead><tr>
        <th>Narrative</th><th>Read</th><th class="num">Score</th><th class="num">Confidence</th><th>Sources</th>
      </tr></thead><tbody>${narratives}</tbody></table></div>` : ''}`;
}

/* ---------------- CHN ---------------- */

function viewChains() {
  const byChain = state.kb.stats.wallets_by_chain || {};
  const marketByChain = Object.fromEntries((state.meta.market || []).map((m) => [m.chain, m]));
  const rows = Object.entries(state.kb.chains).map(([key, c]) => ({
    key, name: c.name, family: c.family, enrichment: c.enrichment,
    wallets: byChain[key] || 0, native: c.native_symbol,
    tvl: marketByChain[key]?.price_usd !== undefined ? null : null, // reserved
  }));

  table('CHN', [
    { key: 'name', label: 'Chain' }, { key: 'family', label: 'Family' },
    { key: 'wallets', label: 'Wallets', num: true }, { key: 'enrichment', label: 'Live data' },
  ], sortRows(rows, 'CHN', 'wallets'), (c) => `
    <tr data-key="${esc(c.key)}" data-kind="chain">
      <td><div class="rowmain">${chainAvatar(c.key)}<b>${esc(c.name)}</b></div></td>
      <td class="dim">${esc(c.family.toUpperCase())}</td>
      <td class="num">${c.wallets || '<span class="dim">0</span>'}</td>
      <td>${c.enrichment === 'none'
        ? '<span class="pill archive">KB only</span>'
        : `<span class="pill elite">${esc(c.enrichment)}</span>`}</td></tr>`);
}

/* ---------------- SRC ---------------- */

function viewSources() {
  const rows = (state.kb.sources || []).filter((s) => matches([s.name, s.key].join(' ')));
  table('SRC', [
    { key: 'name', label: 'Source' }, { key: 'type', label: 'Type' },
    { key: 'trust', label: 'Trust' }, { key: 'median', label: 'Median multiple', num: true },
  ], rows, (s) => {
    const med = s.reliability?.median_multiple;
    return `<tr data-key="${esc(s.key)}" data-kind="source">
      <td><b>${esc(s.name)}</b></td><td class="dim">${esc(s.type || '—')}</td>
      <td><span class="pill ${s.trust === 'low' ? 'archive' : 'watch'}">${esc(s.trust || '—')}</span></td>
      <td class="num ${med !== undefined && med < 1 ? 'neg' : ''}">${med === undefined ? '—' : med.toFixed(2) + 'x'}</td></tr>`;
  });
}

/* ---------------- detail panel ---------------- */

function kv(pairs) {
  return `<dl class="kv">${pairs.filter(([, v]) => v !== null && v !== undefined && v !== '')
    .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join('')}</dl>`;
}

function openDetail(kind, key) {
  const finders = {
    wallet: () => (state.kb.wallets || []).find((w) => w.key === key),
    entity: () => (state.kb.entities || []).find((e) => e.key === key),
    narrative: () => (state.kb.narratives || []).find((n) => n.key === key),
    chain: () => ({ key, ...state.kb.chains[key] }),
    source: () => (state.kb.sources || []).find((s) => s.key === key),
  };
  const item = finders[kind]?.();
  if (!item) return;

  $('detail-title').textContent = { wallet: 'Wallet', entity: 'Entity', narrative: 'Narrative',
    chain: 'Chain', source: 'Source' }[kind];
  $('detail-body').innerHTML = ({ wallet: detailWallet, entity: detailEntity,
    narrative: detailNarrative, chain: detailChain, source: detailSource })[kind](item);
  $('detail').classList.add('open');
  $('scrim').toggleAttribute('data-show', true);
  $('scrim').hidden = false;
}
function closeDetail() {
  $('detail').classList.remove('open');
  $('scrim').hidden = true;
}

function detailWallet(w) {
  const chain = state.kb.chains[w.chain] || {};
  const obs = w.observed;
  const entity = w.entity && (state.kb.entities || []).find((e) => e.key === w.entity);
  return `
    <div class="dhead">${chainAvatar(w.chain)}
      <div><div class="name">${esc(w.display)}</div><div class="sub">${esc(chain.name || w.chain)}</div></div></div>
    ${obs ? `<div class="score-hero"><span class="num">${obs.smart_score.toFixed(0)}</span>
      <span class="pill ${obs.tier}">${esc(obs.tier)}</span></div>` : ''}
    <h3>Identity</h3>
    ${kv([
      ['Full address', `<span class="${w.resolved ? 'addr' : 'addr masked'}">${esc(w.address || w.masked)}</span>`],
      ['State', w.resolved ? '<span class="pill elite">Resolved</span>' : '<span class="pill archive">Masked — unresolved</span>'],
      ['Entity', entity ? `<b>${esc(entity.name)}</b>` : '<span class="dim">unassigned</span>'],
      ['Handle', w.handle ? esc(w.handle) : null],
      ['Confidence', `<span class="pill cf-${esc(w.confidence || 'reported')}">${esc(w.confidence || '—')}</span>`],
      ['Source', esc(w.source || '—')],
    ])}
    ${(w.labels || []).length ? `<h3>Labels</h3><div>${w.labels.map(labelTag).join('')}</div>
      ${w.labels.filter((l) => l.evidence).map((l) => `<div class="evidence-row">
        <div class="evidence-name">${esc(l.name)}</div><div class="evidence-text">${esc(l.evidence)}</div></div>`).join('')}` : ''}
    ${w.notes ? `<h3>Notes</h3><div class="note">${esc(w.notes)}</div>` : ''}
    ${obs ? (obs.kind === 'perps' ? observedPerps(obs) : observedEvm(obs)) : `<h3>Observed</h3>
      <p class="dim" style="font-size:12.5px">${chain.enrichment === 'none'
        ? `${esc(chain.name || w.chain)} has no live enrichment adapter yet — this record is curated only.`
        : 'Not yet enriched.'}</p>`}
    ${(w.narratives || []).length ? `<h3>Narratives</h3>${w.narratives.map((n) =>
      `<button class="linkbtn" data-goto="narrative:${esc(n)}">${esc(n)}</button>`).join('')}` : ''}
    ${w.address && chain.explorer ? `<a class="linkbtn" target="_blank" rel="noopener"
      href="${esc(chain.explorer)}/address/${esc(w.address)}">View on explorer ↗</a>` : ''}`;
}

function observedEvm(obs) {
  return `<h3>Observed on-chain</h3>${kv([
    ['Transactions', fmt(obs.tx_count)], ['Active days', fmt(obs.active_days)],
    ['Volume', fmt(obs.volume_native, 2)], ['Last seen', esc((obs.last_seen || '').slice(0, 10))],
  ])}`;
}
function observedPerps(obs) {
  const pnlClass = (v) => (v > 0 ? 'pos' : v < 0 ? 'neg' : 'dim');
  const bias = obs.net_bias > 0.2 ? 'Long' : obs.net_bias < -0.2 ? 'Short' : 'Neutral';
  const positions = (obs.positions || []).slice(0, 8).map((p) => `
    <tr><td>${esc(p.coin)}</td><td class="${p.direction === 'long' ? 'pos' : 'neg'}">${esc(p.direction)}</td>
      <td class="num">$${fmtUsd(p.notional)}</td>
      <td class="num ${pnlClass(p.unrealized_pnl)}">${p.unrealized_pnl >= 0 ? '+' : ''}$${fmtUsd(p.unrealized_pnl)}</td>
      <td class="num">${p.leverage ? p.leverage + 'x' : '—'}</td></tr>`).join('');
  return `<h3>Observed — perps</h3>${kv([
      ['Equity', `$${fmtUsd(obs.equity)}`], ['Notional', `$${fmtUsd(obs.notional)}`],
      ['Leverage', `<span class="${obs.leverage >= 20 ? 'neg' : 'pos'}">${(obs.leverage || 0).toFixed(1)}x</span>`],
      ['Bias', `${bias} (${(obs.net_bias || 0).toFixed(2)})`],
      ['Unrealized', `<span class="${pnlClass(obs.unrealized_pnl)}">$${fmtUsd(obs.unrealized_pnl)}</span>`],
      ['Realized', `<span class="${pnlClass(obs.realized_pnl)}">$${fmtUsd(obs.realized_pnl)}</span>`],
      ['Win rate', obs.win_rate === null || obs.win_rate === undefined ? '<span class="dim">no closes</span>' : `${(obs.win_rate * 100).toFixed(0)}%`],
      ['Fills', fmt(obs.fill_count)],
    ])}
    ${positions ? `<h3>Open positions (${obs.position_count})</h3>
      <table class="mini-table"><thead><tr><th>Coin</th><th>Side</th>
        <th class="num">Notional</th><th class="num">uPnL</th><th class="num">Lev</th></tr></thead>
      <tbody>${positions}</tbody></table>` : ''}`;
}

function detailEntity(e) {
  const wallets = (state.kb.wallets || []).filter((w) => e.cluster.includes(w.key));
  return `
    <div class="dhead">${entityAvatar(e.name)}
      <div><div class="name">${esc(e.name)}</div><div class="sub">${esc(e.type)}</div></div></div>
    ${kv([
      ['Confidence', `<span class="pill cf-${esc(e.confidence)}">${esc(e.confidence)}</span>`],
      ['Wallets', String(e.cluster_size)],
      ...Object.entries(e.handles || {}).map(([k, v]) => [k, esc(v)]),
    ])}
    ${e.notes ? `<h3>Notes</h3><div class="note">${esc(e.notes)}</div>` : ''}
    ${(e.holdings || []).length ? `<h3>Holdings</h3>${kv(e.holdings.map((h) =>
      [(h.asset || '').toUpperCase(), `${fmt(h.amount)} <span class="dim">(${esc(h.as_reported || '')})</span>`]))}
      <p class="dim" style="font-size:11.5px;margin-top:6px">Entity-level holding — coins span many addresses.</p>` : ''}
    ${wallets.length ? `<h3>Wallet cluster</h3>${wallets.map((w) => `<div style="margin-bottom:4px">
        <button class="linkbtn" data-goto="wallet:${esc(w.key)}">${chainAvatar(w.chain)}
          <span class="${w.resolved ? 'addr' : 'addr masked'}">${esc(w.display)}</span></button></div>`).join('')}` : ''}`;
}

function detailNarrative(n) {
  const wallets = (state.kb.wallets || []).filter((w) => n.linked_wallets.includes(w.key));
  return `
    <div class="dhead"><span class="avatar" style="background:var(--brand)">${esc((n.title || '?').slice(0, 1))}</span>
      <div><div class="name">${esc(n.title)}</div><div class="sub">${esc(n.status)}</div></div></div>
    ${kv([
      ['Status', `<span class="pill st-${esc(n.status)}">${esc(n.status)}</span>`],
      ['Conviction', esc(n.conviction || 'none')],
      ['Chains', esc((n.chains || []).join(', ')) || '—'],
      ['Opened', esc(n.opened || '—')], ['Tokens', String((n.tokens || []).length)],
    ])}
    ${(n.outcomes || []).length ? `<h3>Measured outcomes</h3>${kv(n.outcomes.map((o) => [
      String(o.metric), `<span class="${typeof o.value === 'number' && o.value < 1 ? 'neg' : 'pos'}">${
        esc(String(o.value))}</span> <span class="dim">${esc(o.date || '')}</span>`]))}` : ''}
    ${(n.tokens || []).length ? `<h3>Tokens</h3><div>${n.tokens.map((t) => `<span class="tag">${esc(t)}</span>`).join('')}</div>` : ''}
    ${(n.updates || []).length ? `<h3>Updates</h3>${n.updates.map((u) =>
      `<div class="note"><span class="dim">${esc(u.date || '')}</span><br>${esc(u.note || '')}</div>`).join('')}` : ''}
    ${wallets.length ? `<h3>Positioned wallets (${wallets.length})</h3>${wallets.slice(0, 25).map((w) =>
      `<div><button class="linkbtn" data-goto="wallet:${esc(w.key)}">${esc(w.display)}</button></div>`).join('')}` : ''}
    ${n.body ? `<h3>Analysis</h3><div class="prose">${miniMarkdown(n.body)}</div>` : ''}`;
}

function detailChain(c) {
  return `<div class="dhead">${chainAvatar(c.key)}
      <div><div class="name">${esc(c.name || c.key)}</div><div class="sub">${esc((c.family || '').toUpperCase())}</div></div></div>
    ${kv([
      ['Chain ID', c.chain_id ? String(c.chain_id) : null], ['Native asset', esc(c.native_symbol || '—')],
      ['Live data', c.enrichment === 'none' ? '<span class="pill archive">KB only</span>'
        : `<span class="pill elite">${esc(c.enrichment)}</span>`],
    ])}
    ${c.notes ? `<h3>Notes</h3><div class="note">${esc(c.notes)}</div>` : ''}
    ${c.explorer ? `<a class="linkbtn" target="_blank" rel="noopener" href="${esc(c.explorer)}">Explorer ↗</a>` : ''}`;
}

function detailSource(s) {
  const rel = s.reliability || {};
  return `<div class="dhead"><span class="avatar" style="background:var(--gray)">${esc((s.name || '?').slice(0, 1))}</span>
      <div><div class="name">${esc(s.name)}</div><div class="sub">${esc(s.type || '—')}</div></div></div>
    ${kv([['Trust', `<span class="pill ${s.trust === 'low' ? 'archive' : 'watch'}">${esc(s.trust || '—')}</span>`]])}
    ${s.description ? `<div class="note">${esc(s.description)}</div>` : ''}
    ${Object.keys(rel).length ? `<h3>Measured reliability</h3>${kv(Object.entries(rel).map(([k, v]) =>
      [k, `<span class="${typeof v === 'number' && v < 1 && k !== 'window' ? 'neg' : ''}">${esc(String(v))}</span>`]))}` : ''}
    ${(s.caveats || []).length ? `<h3>Caveats</h3>${s.caveats.map((c) => `<div class="note">${esc(c)}</div>`).join('')}` : ''}`;
}

function miniMarkdown(md) {
  const lines = esc(md).split('\n');
  let html = '', inTable = false;
  for (const line of lines) {
    if (/^\|/.test(line)) {
      if (/^\|[\s:|-]+\|$/.test(line)) continue;
      const cells = line.split('|').slice(1, -1);
      if (!inTable) { html += '<table>'; inTable = true; }
      html += '<tr>' + cells.map((c) => `<td>${c.trim()}</td>`).join('') + '</tr>';
      continue;
    }
    if (inTable) { html += '</table>'; inTable = false; }
    if (/^## /.test(line)) html += `<h2>${line.slice(3)}</h2>`;
    else if (/^\s*[-*] /.test(line)) html += `<div>• ${line.replace(/^\s*[-*] /, '')}</div>`;
    else if (line.trim()) html += `<p>${line}</p>`;
  }
  if (inTable) html += '</table>';
  return html.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/`(.+?)`/g, '<code>$1</code>');
}

/* ---------------- selection ---------------- */

function rows() { return [...document.querySelectorAll('#panel tbody tr')]; }
function select(index) {
  const all = rows();
  if (!all.length) return;
  const i = Math.max(0, Math.min(all.length - 1, index));
  all.forEach((r) => r.removeAttribute('aria-selected'));
  all[i].setAttribute('aria-selected', 'true');
  all[i].scrollIntoView({ block: 'nearest' });
  state.selected[state.view] = i;
}
function restoreSelection() {
  const i = state.selected[state.view];
  if (i !== undefined && rows().length) select(i);
}

/* ---------------- sidebar (mobile) ---------------- */

function closeSidebar() { $('sidebar').classList.remove('open'); }

/* ---------------- events ---------------- */

$('search').addEventListener('input', (e) => { state.query = e.target.value; render(); });

$('nav').addEventListener('click', (e) => {
  const item = e.target.closest('[data-view]');
  if (item) { $('search').value = ''; state.query = ''; setView(item.dataset.view); }
});

document.addEventListener('click', (e) => {
  const tr = e.target.closest('#panel tbody tr[data-key]');
  if (tr) { select(rows().indexOf(tr)); openDetail(tr.dataset.kind, tr.dataset.key); return; }

  const th = e.target.closest('#panel thead th[data-key]');
  if (th) {
    const cur = state.sort[state.view];
    state.sort[state.view] = (cur && cur.key === th.dataset.key)
      ? { key: cur.key, dir: cur.dir === 'asc' ? 'desc' : 'asc' } : { key: th.dataset.key, dir: 'desc' };
    render(); return;
  }

  const chip = e.target.closest('#chainfilter [data-chain]');
  if (chip) {
    const c = chip.dataset.chain;
    state.chains.has(c) ? state.chains.delete(c) : state.chains.add(c);
    render(); return;
  }
  if (e.target.closest('#clear-filters')) { state.chains.clear(); render(); return; }

  const cluster = e.target.closest('[data-cluster]');
  if (cluster && state.graphInstance) { state.graphInstance.focusNode(cluster.dataset.cluster); return; }

  const gotoView = e.target.closest('[data-goto-view]');
  if (gotoView) { setView(gotoView.dataset.gotoView); return; }

  const goto = e.target.closest('[data-goto]');
  if (goto) {
    const [kind, ...rest] = goto.dataset.goto.split(':');
    openDetail(kind, rest.join(':')); return;
  }

  if (e.target.closest('#detail-close') || e.target === $('scrim')) { closeDetail(); return; }
  if (e.target.closest('#theme-toggle')) { toggleTheme(); return; }
  if (e.target.closest('#menu-btn')) { $('sidebar').classList.toggle('open'); return; }
});

document.addEventListener('keydown', (e) => {
  const typing = document.activeElement === $('search');
  if (e.key === '/' && !typing) { e.preventDefault(); $('search').focus(); return; }
  if (e.key === 'Escape') {
    if (typing) $('search').blur();
    closeDetail(); closeSidebar(); return;
  }
  if (typing) return;
  if (e.key >= '1' && e.key <= '9') { setView(VIEWS[+e.key - 1]); return; }
  if (e.key === 'ArrowDown') { e.preventDefault(); select((state.selected[state.view] ?? -1) + 1); return; }
  if (e.key === 'ArrowUp') { e.preventDefault(); select((state.selected[state.view] ?? 1) - 1); return; }
  if (e.key === 'Enter') {
    const sel = document.querySelector('#panel tbody tr[aria-selected="true"]');
    if (sel) openDetail(sel.dataset.kind, sel.dataset.key);
  }
});

boot();
