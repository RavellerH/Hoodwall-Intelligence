/* Hoodwall terminal.
   Vanilla ES2020, no build step, no CDN. All shaping happens in
   pipeline/publish.py; this file renders, filters and navigates.

   Interaction model is keyboard-first on purpose: 1-6 switch views, arrows
   move the selection, ENTER opens detail, "/" focuses the command line. */
'use strict';

const VIEWS = ['DASH', 'WAL', 'ENT', 'NAR', 'CHN', 'SRC'];

const state = {
  kb: null, meta: null, scored: [],
  view: 'DASH',
  chains: new Set(),      // empty = all
  query: '',
  sort: {}, selected: {}, // per view
};

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/* A label is either curated (you asserted it) or observed (an adapter
   measured it). Showing which is which keeps assertion apart from evidence. */
function labelTag(l) {
  const name = typeof l === 'string' ? l : l.name;
  const observed = typeof l === 'object' && l.source === 'observed';
  const title = (typeof l === 'object' && l.evidence) ? ` title="${esc(l.evidence)}"` : '';
  return `<span class="tag${observed ? ' obs' : ''}"${title}>${esc(name)}</span>`;
}

const fmt = (n, d = 0) => (n === null || n === undefined || Number.isNaN(Number(n)))
  ? '—' : Number(n).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });

function fmtUsd(n) {
  if (n === null || n === undefined) return '—';
  const v = Number(n), a = Math.abs(v);
  if (a >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
  if (a >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
  return v.toFixed(0);
}

/* ---------------- load ---------------- */

async function boot() {
  const bust = `?v=${Date.now()}`;
  try {
    const [kb, meta] = await Promise.all([
      fetch(`data/kb.json${bust}`).then((r) => r.json()),
      fetch(`data/meta.json${bust}`).then((r) => r.json()),
    ]);
    state.kb = kb;
    state.meta = meta;
  } catch (err) {
    $('panel').innerHTML = '<div class="empty">DATA UNAVAILABLE — the pipeline may not have run yet.</div>';
    $('ticker').textContent = 'NO DATA';
    return;
  }
  try {
    state.scored = await fetch(`data/wallets.json${bust}`).then((r) => r.json());
  } catch { state.scored = []; }

  renderChrome();
  setView('DASH');
  startClock();
}

function startClock() {
  const tick = () => {
    $('clock').textContent = new Date().toISOString().slice(11, 19) + ' UTC';
  };
  tick();
  setInterval(tick, 1000);
}

function renderChrome() {
  const s = state.kb.stats || {};
  $('ticker').textContent =
    `WALLETS ${s.wallets ?? 0}  ENTITIES ${s.entities ?? 0}  ` +
    `NARRATIVES ${s.narratives ?? 0}  CHAINS ${s.chains ?? 0}  ` +
    `UPDATED ${(state.meta.generated_at || '').slice(0, 16).replace('T', ' ')}`;

  const errs = (state.kb.errors || []).length;
  const health = $('kb-health');
  health.textContent = errs ? `KB ${errs} ERR` : 'KB OK';
  health.className = `health ${errs ? 'bad' : 'ok'}`;

  const byChain = s.wallets_by_chain || {};
  $('chainfilter').innerHTML = Object.keys(state.kb.chains)
    .filter((c) => byChain[c])
    .map((c) => `<button class="chip" data-chain="${esc(c)}" aria-pressed="false">
        ${esc(c.toUpperCase())}<span class="n">${byChain[c]}</span></button>`).join('');

  $('fkeys').innerHTML = [
    ['F1', 'HELP'], ['1', 'DASH'], ['2', 'WAL'], ['3', 'ENT'],
    ['4', 'NAR'], ['5', 'CHN'], ['6', 'SRC'], ['/', 'CMD'], ['ESC', 'CLEAR'],
  ].map(([k, l]) => `<button class="fkey" data-cmd="${l}"><b>${k}</b>${l}</button>`).join('');
}

/* ---------------- view routing ---------------- */

function setView(view) {
  if (!VIEWS.includes(view)) return false;
  state.view = view;
  document.querySelectorAll('.navitem').forEach(
    (el) => el.setAttribute('aria-current', String(el.dataset.view === view)));
  render();
  return true;
}

const TITLES = {
  DASH: 'DASH · OVERVIEW', WAL: 'WAL · TRACKED WALLETS',
  ENT: 'ENT · ENTITY CLUSTERS', NAR: 'NAR · NARRATIVES',
  CHN: 'CHN · CHAIN COVERAGE', SRC: 'SRC · SOURCES',
};

function render() {
  $('panel-title').textContent = TITLES[state.view] || state.view;
  const renderers = { DASH: viewDash, WAL: viewWallets, ENT: viewEntities,
                      NAR: viewNarratives, CHN: viewChains, SRC: viewSources };
  const count = renderers[state.view]();
  $('panel-count').textContent = count === null ? '' : `${count} ROW(S)`;
}

/* ---------------- filtering + sorting ---------------- */

function chainOk(chain) {
  return state.chains.size === 0 || state.chains.has(chain);
}

function matches(haystack) {
  if (!state.query) return true;
  return haystack.toLowerCase().includes(state.query.toLowerCase());
}

function sortRows(rows, view, fallbackKey) {
  const s = state.sort[view];
  const key = s?.key || fallbackKey;
  const dir = s?.dir === 'asc' ? 1 : -1;
  return rows.slice().sort((a, b) => {
    const x = a[key], y = b[key];
    if (x === null || x === undefined) return 1;
    if (y === null || y === undefined) return -1;
    if (typeof x === 'string') return dir * x.localeCompare(y) * -1;
    return dir * (x - y);
  });
}

function table(view, columns, rows, rowFn) {
  const s = state.sort[view];
  const head = columns.map((c) => {
    const sorted = s && s.key === c.key;
    const aria = sorted ? ` aria-sort="${s.dir === 'asc' ? 'ascending' : 'descending'}"` : '';
    return `<th class="${c.num ? 'num' : ''}" data-key="${esc(c.key)}"${aria}>${esc(c.label)}</th>`;
  }).join('');

  if (!rows.length) {
    $('panel').innerHTML = '<div class="empty">NO ROWS MATCH THE CURRENT FILTER.</div>';
    return 0;
  }
  $('panel').innerHTML =
    `<table><thead><tr>${head}</tr></thead><tbody>${rows.map(rowFn).join('')}</tbody></table>`;
  restoreSelection();
  return rows.length;
}

/* ---------------- views ---------------- */

function viewDash() {
  const s = state.kb.stats || {};
  const tiles = [
    ['WALLETS', s.wallets, `${s.resolved} resolved · ${s.unresolved} masked`],
    ['ENTITIES', s.entities, `${(state.kb.entities || []).filter((e) => e.cluster_size > 1).length} multi-wallet`],
    ['NARRATIVES', s.narratives, Object.entries(s.narratives_by_status || {})
      .map(([k, v]) => `${v} ${k}`).join(' · ')],
    ['CHAINS', s.chains, `${Object.keys(s.wallets_by_chain || {}).length} populated`],
    ['SOURCES', s.sources, 'reliability tracked'],
    ['SCORED', (state.scored || []).length, 'on-chain enriched'],
  ].map(([k, v, sub]) => `<div class="tile"><div class="v">${fmt(v)}</div>
      <div class="k">${k}</div><div class="sub">${esc(sub || '')}</div></div>`).join('');

  const bars = (obj, total) => Object.entries(obj || {})
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `<div class="bar"><span class="lbl">${esc(k)}</span>
        <span class="track"><span class="fill" style="width:${(v / total * 100).toFixed(1)}%"></span></span>
        <span class="n">${v}</span></div>`).join('');

  const maxChain = Math.max(1, ...Object.values(s.wallets_by_chain || {}));
  const maxLabel = Math.max(1, ...Object.values(s.wallets_by_label || {}));

  const clusters = (state.kb.entities || []).filter((e) => e.cluster_size > 0).slice(0, 8);

  $('panel').innerHTML = `
    <div class="tiles">${tiles}</div>
    <div class="section">WALLETS BY CHAIN</div><div class="bars">${bars(s.wallets_by_chain, maxChain)}</div>
    <div class="section">WALLETS BY LABEL</div><div class="bars">${bars(s.wallets_by_label, maxLabel)}</div>
    <div class="section">LARGEST ENTITY CLUSTERS</div>
    <div class="bars">${clusters.map((e) => `<div class="bar">
        <span class="lbl">${esc(e.name)}</span>
        <span class="track"><span class="fill" style="width:${(e.cluster_size / Math.max(1, clusters[0].cluster_size) * 100).toFixed(0)}%"></span></span>
        <span class="n">${e.cluster_size}</span></div>`).join('') || '<div class="dim" style="padding:0 10px">none</div>'}</div>`;
  return null;
}

function viewWallets() {
  const rows = (state.kb.wallets || []).filter((w) =>
    chainOk(w.chain) && matches(
      [w.address, w.masked, w.entity, w.handle, (w.labels || []).join(' '), w.source].join(' ')));

  return table('WAL', [
    { key: 'chain', label: 'CHAIN' },
    { key: 'display', label: 'ADDRESS' },
    { key: 'entity', label: 'ENTITY' },
    { key: 'labels', label: 'LABELS' },
    { key: 'confidence', label: 'CONF' },
    { key: 'score', label: 'SCORE', num: true },
    { key: 'source', label: 'SOURCE' },
  ], sortRows(rows, 'WAL', 'chain'), (w) => {
    const obs = w.observed;
    return `<tr data-key="${esc(w.key)}" data-kind="wallet">
      <td class="dim">${esc(w.chain.toUpperCase())}</td>
      <td class="${w.resolved ? 'addr' : 'masked'}">${esc(w.display)}${w.resolved ? '' : ' ◌'}</td>
      <td>${w.entity ? esc(w.entity) : '<span class="faint">—</span>'}</td>
      <td>${(w.labels || []).map(labelTag).join('') || '<span class="faint">—</span>'}</td>
      <td class="cf-${esc(w.confidence || 'reported')}">${esc((w.confidence || '—').toUpperCase())}</td>
      <td class="num">${obs ? `<b>${obs.smart_score.toFixed(0)}</b>` : '<span class="faint">—</span>'}</td>
      <td class="dim">${esc(w.source || '—')}</td></tr>`;
  });
}

function viewEntities() {
  const rows = (state.kb.entities || []).filter((e) =>
    matches([e.name, e.key, e.type, Object.values(e.handles || {}).join(' ')].join(' ')));

  return table('ENT', [
    { key: 'name', label: 'ENTITY' },
    { key: 'type', label: 'TYPE' },
    { key: 'cluster_size', label: 'WALLETS', num: true },
    { key: 'confidence', label: 'CONF' },
    { key: 'holdings', label: 'HOLDINGS' },
  ], sortRows(rows, 'ENT', 'cluster_size'), (e) => {
    const hold = (e.holdings || []).map((h) =>
      `${fmtUsd(h.amount)} ${esc(h.asset || '')}`).join(', ');
    return `<tr data-key="${esc(e.key)}" data-kind="entity">
      <td><b>${esc(e.name)}</b></td>
      <td class="dim">${esc(e.type.toUpperCase())}</td>
      <td class="num">${e.cluster_size || '<span class="faint">0</span>'}</td>
      <td class="cf-${esc(e.confidence)}">${esc(e.confidence.toUpperCase())}</td>
      <td class="dim">${hold || '<span class="faint">—</span>'}</td></tr>`;
  });
}

function viewNarratives() {
  const rows = (state.kb.narratives || []).filter((n) =>
    matches([n.title, n.key, (n.tokens || []).join(' '), (n.chains || []).join(' ')].join(' ')));

  return table('NAR', [
    { key: 'title', label: 'NARRATIVE' },
    { key: 'status', label: 'STATUS' },
    { key: 'conviction', label: 'CONV' },
    { key: 'chains', label: 'CHAINS' },
    { key: 'tokens', label: 'TOKENS', num: true },
    { key: 'opened', label: 'OPENED' },
  ], sortRows(rows, 'NAR', 'opened'), (n) => `
    <tr data-key="${esc(n.key)}" data-kind="narrative">
      <td><b>${esc(n.title)}</b></td>
      <td><span class="pill st-${esc(n.status)}">${esc(n.status.toUpperCase())}</span></td>
      <td class="dim">${esc((n.conviction || 'none').toUpperCase())}</td>
      <td class="dim">${esc((n.chains || []).join(',').toUpperCase()) || '—'}</td>
      <td class="num">${(n.tokens || []).length}</td>
      <td class="dim">${esc(n.opened || '—')}</td></tr>`);
}

function viewChains() {
  const byChain = state.kb.stats.wallets_by_chain || {};
  const rows = Object.entries(state.kb.chains).map(([key, c]) => ({
    key, name: c.name, family: c.family, enrichment: c.enrichment,
    wallets: byChain[key] || 0, native: c.native_symbol, explorer: c.explorer,
  }));

  return table('CHN', [
    { key: 'key', label: 'CHAIN' },
    { key: 'name', label: 'NAME' },
    { key: 'family', label: 'FAMILY' },
    { key: 'wallets', label: 'WALLETS', num: true },
    { key: 'enrichment', label: 'LIVE DATA' },
  ], sortRows(rows, 'CHN', 'wallets'), (c) => `
    <tr data-key="${esc(c.key)}" data-kind="chain">
      <td><b>${esc(c.key.toUpperCase())}</b></td>
      <td>${esc(c.name)}</td>
      <td class="dim">${esc(c.family.toUpperCase())}</td>
      <td class="num">${c.wallets || '<span class="faint">0</span>'}</td>
      <td class="${c.enrichment === 'none' ? 'faint' : 'pos'}">${esc(
        c.enrichment === 'none' ? 'KB ONLY' : c.enrichment.toUpperCase())}</td></tr>`);
}

function viewSources() {
  const rows = (state.kb.sources || []).filter((s) => matches([s.name, s.key].join(' ')));
  return table('SRC', [
    { key: 'name', label: 'SOURCE' },
    { key: 'type', label: 'TYPE' },
    { key: 'trust', label: 'TRUST' },
    { key: 'median', label: 'MEDIAN MULT', num: true },
  ], rows, (s) => {
    const med = s.reliability?.median_multiple;
    return `<tr data-key="${esc(s.key)}" data-kind="source">
      <td><b>${esc(s.name)}</b></td>
      <td class="dim">${esc(s.type || '—')}</td>
      <td class="${s.trust === 'low' ? 'neg' : 'dim'}">${esc((s.trust || '—').toUpperCase())}</td>
      <td class="num ${med !== undefined && med < 1 ? 'neg' : ''}">${
        med === undefined ? '—' : med.toFixed(2) + 'x'}</td></tr>`;
  });
}

/* ---------------- detail pane ---------------- */

function openDetail(kind, key) {
  const body = $('detail-body');
  const finders = {
    wallet: () => (state.kb.wallets || []).find((w) => w.key === key),
    entity: () => (state.kb.entities || []).find((e) => e.key === key),
    narrative: () => (state.kb.narratives || []).find((n) => n.key === key),
    chain: () => ({ key, ...state.kb.chains[key] }),
    source: () => (state.kb.sources || []).find((s) => s.key === key),
  };
  const item = finders[kind]?.();
  if (!item) return;

  $('detail-title').textContent = `${kind.toUpperCase()} DETAIL`;
  body.innerHTML = ({ wallet: detailWallet, entity: detailEntity,
                      narrative: detailNarrative, chain: detailChain,
                      source: detailSource })[kind](item);
  $('detail').classList.add('open');
}

function kv(pairs) {
  return `<dl class="kv">${pairs.filter(([, v]) => v !== null && v !== undefined && v !== '')
    .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join('')}</dl>`;
}

function detailWallet(w) {
  const chain = state.kb.chains[w.chain] || {};
  const obs = w.observed;
  const entity = w.entity && (state.kb.entities || []).find((e) => e.key === w.entity);
  return `
    <h3>IDENTITY</h3>
    ${kv([
      ['ADDRESS', `<span class="${w.resolved ? 'addr' : 'masked'}">${esc(w.address || w.masked)}</span>`],
      ['STATE', w.resolved ? '<span class="pos">RESOLVED</span>'
        : '<span class="masked">MASKED — awaiting resolution</span>'],
      ['CHAIN', esc(chain.name || w.chain)],
      ['ENTITY', entity ? `<b>${esc(entity.name)}</b>` : '<span class="faint">unassigned</span>'],
      ['HANDLE', w.handle ? esc(w.handle) : null],
      ['CONFIDENCE', `<span class="cf-${esc(w.confidence || 'reported')}">${esc((w.confidence || '—').toUpperCase())}</span>`],
      ['SOURCE', esc(w.source || '—')],
    ])}
    ${(w.labels || []).length ? `<h3>LABELS</h3>
      <div>${w.labels.map(labelTag).join('')}</div>
      ${w.labels.filter((l) => l.evidence).map((l) =>
        `<div class="note"><b>${esc(l.name)}</b> — ${esc(l.evidence)}</div>`).join('')}` : ''}
    ${w.notes ? `<h3>NOTES</h3><div class="note">${esc(w.notes)}</div>` : ''}
    ${obs ? observedBlock(obs) : `<h3>OBSERVED</h3>
      <p class="dim">No live data. ${chain.enrichment === 'none'
        ? `${esc(w.chain.toUpperCase())} has no enrichment adapter yet — this record is curated only.`
        : 'Not yet enriched.'}</p>`}
    ${(w.narratives || []).length ? `<h3>NARRATIVES</h3>${w.narratives.map((n) =>
      `<button class="linkbtn" data-goto="narrative:${esc(n)}">${esc(n)}</button>`).join('')}` : ''}
    ${w.address && chain.explorer ? `<a class="linkbtn" target="_blank" rel="noopener"
      href="${esc(chain.explorer)}/address/${esc(w.address)}">EXPLORER ↗</a>` : ''}`;
}

/* Observed data has two shapes. Perps accounts have no transaction history
   and EVM wallets have no positions, so they get different panels rather
   than a lowest-common-denominator one that misrepresents both. */
function observedBlock(obs) {
  return obs.kind === 'perps' ? observedPerps(obs) : observedEvm(obs);
}

function observedEvm(obs) {
  return `<h3>OBSERVED ON-CHAIN</h3>${kv([
    ['SMART SCORE', `<b>${obs.smart_score}</b> ${esc((obs.tier || '').toUpperCase())}`],
    ['TXS', fmt(obs.tx_count)],
    ['ACTIVE DAYS', fmt(obs.active_days)],
    ['VOLUME', fmt(obs.volume_native, 2)],
    ['LAST SEEN', esc((obs.last_seen || '').slice(0, 10))],
  ])}`;
}

function observedPerps(obs) {
  const pnlClass = (v) => (v > 0 ? 'pos' : v < 0 ? 'neg' : 'dim');
  const bias = obs.net_bias > 0.2 ? 'LONG' : obs.net_bias < -0.2 ? 'SHORT' : 'NEUTRAL';
  const positions = (obs.positions || []).slice(0, 8).map((p) => `
    <tr><td>${esc(p.coin)}</td>
        <td class="${p.direction === 'long' ? 'pos' : 'neg'}">${esc(p.direction.toUpperCase())}</td>
        <td class="num">$${fmtUsd(p.notional)}</td>
        <td class="num ${pnlClass(p.unrealized_pnl)}">${p.unrealized_pnl >= 0 ? '+' : ''}$${fmtUsd(p.unrealized_pnl)}</td>
        <td class="num dim">${p.leverage ? p.leverage + 'x' : '—'}</td></tr>`).join('');

  return `<h3>OBSERVED — PERPS</h3>${kv([
      ['SMART SCORE', `<b>${obs.smart_score}</b> ${esc((obs.tier || '').toUpperCase())}`],
      ['EQUITY', `$${fmtUsd(obs.equity)}`],
      ['NOTIONAL', `$${fmtUsd(obs.notional)}`],
      ['LEVERAGE', `<span class="${obs.leverage >= 20 ? 'neg' : obs.leverage >= 10 ? 'st-cooling' : 'pos'}">${(obs.leverage || 0).toFixed(1)}x</span>`],
      ['BIAS', `${bias} <span class="dim">(${(obs.net_bias || 0).toFixed(2)})</span>`],
      ['CONCENTRATION', `${((obs.concentration || 0) * 100).toFixed(0)}%`],
      ['UNREALIZED', `<span class="${pnlClass(obs.unrealized_pnl)}">$${fmtUsd(obs.unrealized_pnl)}</span>`],
      ['REALIZED', `<span class="${pnlClass(obs.realized_pnl)}">$${fmtUsd(obs.realized_pnl)}</span>`],
      ['WIN RATE', obs.win_rate === null || obs.win_rate === undefined
        ? '<span class="faint">no closes</span>' : `${(obs.win_rate * 100).toFixed(0)}%`],
      ['FILLS', fmt(obs.fill_count)],
      ['MARKETS', esc((obs.top_coins || []).join(', ')) || '—'],
    ])}
    ${positions ? `<h3>OPEN POSITIONS (${obs.position_count})</h3>
      <table class="prose"><thead><tr>
        <th>COIN</th><th>SIDE</th><th class="num">NOTIONAL</th>
        <th class="num">uPnL</th><th class="num">LEV</th></tr></thead>
      <tbody>${positions}</tbody></table>` : ''}
    ${obs.components ? `<h3>SCORE BREAKDOWN</h3>${Object.entries(obs.components)
      .sort((a, b) => b[1] - a[1]).map(([k, v]) => `<div class="bar">
        <span class="lbl">${esc(k.replace(/_/g, ' '))}</span>
        <span class="track"><span class="fill" style="width:${(v * 100).toFixed(0)}%"></span></span>
        <span class="n">${(v * 100).toFixed(0)}</span></div>`).join('')}` : ''}`;
}

function detailEntity(e) {
  const wallets = (state.kb.wallets || []).filter((w) => e.cluster.includes(w.key));
  return `
    <h3>${esc(e.name.toUpperCase())}</h3>
    ${kv([
      ['TYPE', esc(e.type.toUpperCase())],
      ['CONFIDENCE', `<span class="cf-${esc(e.confidence)}">${esc(e.confidence.toUpperCase())}</span>`],
      ['WALLETS', String(e.cluster_size)],
      ...Object.entries(e.handles || {}).map(([k, v]) => [k.toUpperCase(), esc(v)]),
    ])}
    ${e.notes ? `<h3>NOTES</h3><div class="note">${esc(e.notes)}</div>` : ''}
    ${(e.holdings || []).length ? `<h3>HOLDINGS</h3>${kv(e.holdings.map((h) =>
      [`${(h.asset || '').toUpperCase()}`,
       `${fmt(h.amount)} <span class="dim">(${esc(h.as_reported || '')})</span>`]))}
      <p class="dim" style="margin-top:6px">Entity-level holding: coins span many
      addresses, so no individual wallet is tracked.</p>` : ''}
    ${wallets.length ? `<h3>WALLET CLUSTER</h3>${wallets.map((w) =>
      `<div><button class="linkbtn" data-goto="wallet:${esc(w.key)}">
        <span class="dim">${esc(w.chain.toUpperCase())}</span>
        <span class="${w.resolved ? 'addr' : 'masked'}">${esc(w.display)}</span>
      </button></div>`).join('')}` : ''}`;
}

function detailNarrative(n) {
  const wallets = (state.kb.wallets || []).filter((w) => n.linked_wallets.includes(w.key));
  return `
    <h3>${esc(n.title.toUpperCase())}</h3>
    ${kv([
      ['STATUS', `<span class="pill st-${esc(n.status)}">${esc(n.status.toUpperCase())}</span>`],
      ['CONVICTION', esc((n.conviction || 'none').toUpperCase())],
      ['CHAINS', esc((n.chains || []).join(', ').toUpperCase()) || '—'],
      ['OPENED', esc(n.opened || '—')],
      ['TOKENS', String((n.tokens || []).length)],
    ])}
    ${(n.outcomes || []).length ? `<h3>MEASURED OUTCOMES</h3>${kv(n.outcomes.map((o) => [
      String(o.metric).toUpperCase().slice(0, 14),
      `<span class="${typeof o.value === 'number' && o.value < 1 ? 'neg' : 'pos'}">${
        esc(String(o.value))}</span> <span class="faint">${esc(o.date || '')}</span>`]))}` : ''}
    ${(n.tokens || []).length ? `<h3>TOKENS</h3>
      <div>${n.tokens.map((t) => `<span class="tag">${esc(t)}</span>`).join('')}</div>` : ''}
    ${(n.updates || []).length ? `<h3>UPDATES</h3>${n.updates.map((u) =>
      `<div class="note"><span class="faint">${esc(u.date || '')}</span><br>${esc(u.note || '')}</div>`).join('')}` : ''}
    ${wallets.length ? `<h3>POSITIONED WALLETS (${wallets.length})</h3>${wallets.slice(0, 25).map((w) =>
      `<div><button class="linkbtn" data-goto="wallet:${esc(w.key)}">${esc(w.display)}</button></div>`).join('')}` : ''}
    ${n.body ? `<h3>ANALYSIS</h3><div class="prose">${miniMarkdown(n.body)}</div>` : ''}`;
}

function detailChain(c) {
  return `<h3>${esc((c.name || c.key).toUpperCase())}</h3>
    ${kv([
      ['KEY', esc(c.key)], ['FAMILY', esc((c.family || '').toUpperCase())],
      ['CHAIN ID', c.chain_id ? String(c.chain_id) : null],
      ['NATIVE', esc(c.native_symbol || '—')],
      ['LIVE DATA', c.enrichment === 'none'
        ? '<span class="faint">KB ONLY</span>' : `<span class="pos">${esc(c.enrichment.toUpperCase())}</span>`],
    ])}
    ${c.notes ? `<h3>NOTES</h3><div class="note">${esc(c.notes)}</div>` : ''}
    ${c.explorer ? `<a class="linkbtn" target="_blank" rel="noopener" href="${esc(c.explorer)}">EXPLORER ↗</a>` : ''}`;
}

function detailSource(s) {
  const rel = s.reliability || {};
  return `<h3>${esc(s.name.toUpperCase())}</h3>
    ${kv([['TYPE', esc(s.type || '—')],
          ['TRUST', `<span class="${s.trust === 'low' ? 'neg' : 'dim'}">${esc((s.trust || '—').toUpperCase())}</span>`]])}
    ${s.description ? `<div class="note">${esc(s.description)}</div>` : ''}
    ${Object.keys(rel).length ? `<h3>MEASURED RELIABILITY</h3>${kv(Object.entries(rel).map(([k, v]) =>
      [k.toUpperCase().slice(0, 14), `<span class="${typeof v === 'number' && v < 1 && k !== 'window' ? 'neg' : ''}">${esc(String(v))}</span>`]))}` : ''}
    ${(s.caveats || []).length ? `<h3>CAVEATS</h3>${s.caveats.map((c) =>
      `<div class="note">${esc(c)}</div>`).join('')}` : ''}`;
}

/* Minimal markdown for narrative bodies: headings, tables, bold, code.
   A full parser is not worth the bytes for content we author ourselves. */
function miniMarkdown(md) {
  const lines = esc(md).split('\n');
  let html = '', inTable = false;
  for (const line of lines) {
    if (/^\|/.test(line)) {
      if (/^\|[\s:|-]+\|$/.test(line)) continue;       // separator row
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
  return html
    .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
    .replace(/`(.+?)`/g, '<code>$1</code>');
}

/* ---------------- selection + keyboard ---------------- */

function rows() { return [...document.querySelectorAll('#panel tbody tr')]; }

function select(index) {
  const all = rows();
  if (!all.length) return;
  const i = Math.max(0, Math.min(all.length - 1, index));
  all.forEach((r) => r.removeAttribute('aria-selected'));
  all[i].setAttribute('aria-selected', 'true');
  all[i].scrollIntoView({ block: 'nearest' });
  state.selected[state.view] = i;
  openDetail(all[i].dataset.kind, all[i].dataset.key);
}

function restoreSelection() {
  const i = state.selected[state.view];
  if (i !== undefined && rows().length) select(i);
}

document.addEventListener('keydown', (e) => {
  const typing = document.activeElement === $('cmd');

  if (e.key === '/' && !typing) { e.preventDefault(); $('cmd').focus(); return; }
  if (e.key === 'Escape') {
    $('cmd').value = ''; state.query = ''; $('cmdhint').textContent = '';
    $('detail').classList.remove('open'); $('cmd').blur(); render(); return;
  }
  if (typing) return;

  if (e.key >= '1' && e.key <= '6') { setView(VIEWS[+e.key - 1]); return; }
  if (e.key === 'ArrowDown' || e.key === 'j') {
    e.preventDefault(); select((state.selected[state.view] ?? -1) + 1); return;
  }
  if (e.key === 'ArrowUp' || e.key === 'k') {
    e.preventDefault(); select((state.selected[state.view] ?? 1) - 1); return;
  }
  if (e.key === 'Enter') {
    const sel = document.querySelector('#panel tbody tr[aria-selected="true"]');
    if (sel) openDetail(sel.dataset.kind, sel.dataset.key);
  }
});

/* ---------------- command line ---------------- */

const HELP = `
<h3>COMMANDS</h3>
<div class="prose">
<p><b>DASH WAL ENT NAR CHN SRC</b> — switch view (or press 1-6)</p>
<p><b>CHAIN &lt;name&gt;</b> — filter to one chain (CHAIN ALL to clear)</p>
<p><b>FIND &lt;text&gt;</b> — search the current view</p>
<p><b>HELP</b> — this panel · <b>ESC</b> — clear filters and close detail</p>
<h2>KEYS</h2>
<p><b>/</b> focus command · <b>↑↓</b> or <b>j k</b> move · <b>ENTER</b> open detail</p>
<h2>READING THE DATA</h2>
<p><span class="masked">◌ purple</span> = address known only in masked form
(e.g. 0x3475…3a12) and not yet resolved to a full address.</p>
<p><b>CONF</b> is how much the wallet-to-entity link is trusted:
<span class="cf-confirmed">CONFIRMED</span> (self-disclosed or provable),
<span class="cf-reported">REPORTED</span> (a third party asserts it),
<span class="cf-inferred">INFERRED</span> (our own clustering).</p>
<p><b>KB ONLY</b> in CHN means that chain has curated records but no live
enrichment adapter yet.</p>
</div>`;

function runCommand(raw) {
  const input = raw.trim();
  if (!input) { state.query = ''; render(); return; }
  const [head, ...rest] = input.split(/\s+/);
  const cmd = head.toUpperCase();
  const arg = rest.join(' ');

  if (cmd === 'HELP' || cmd === '?') {
    $('panel-title').textContent = 'HELP';
    $('panel').innerHTML = `<div style="padding:10px 12px">${HELP}</div>`;
    $('panel-count').textContent = '';
    return;
  }
  if (VIEWS.includes(cmd)) { state.query = ''; setView(cmd); $('cmdhint').textContent = ''; return; }
  if (cmd === 'CHAIN') {
    const name = arg.toLowerCase();
    if (!name || name === 'all') state.chains.clear();
    else if (state.kb.chains[name]) state.chains = new Set([name]);
    else { $('cmdhint').textContent = `UNKNOWN CHAIN: ${arg}`; return; }
    syncChips(); render(); $('cmdhint').textContent = '';
    return;
  }
  if (cmd === 'FIND') { state.query = arg; render(); return; }

  // Anything else is treated as a search — the common case.
  state.query = input;
  $('cmdhint').textContent = `FILTER: ${input}`;
  render();
}

function syncChips() {
  document.querySelectorAll('[data-chain]').forEach(
    (c) => c.setAttribute('aria-pressed', String(state.chains.has(c.dataset.chain))));
}

/* ---------------- events ---------------- */

$('cmd').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { runCommand($('cmd').value); }
});
$('cmd').addEventListener('input', (e) => {
  const v = e.target.value.trim();
  // Live-filter as you type, unless it looks like a command verb.
  if (!/^(HELP|CHAIN|FIND|DASH|WAL|ENT|NAR|CHN|SRC|\?)/i.test(v)) {
    state.query = v; render();
  }
});

$('nav').addEventListener('click', (e) => {
  const item = e.target.closest('[data-view]');
  if (item) { state.query = ''; $('cmd').value = ''; setView(item.dataset.view); return; }
  const chip = e.target.closest('[data-chain]');
  if (chip) {
    const c = chip.dataset.chain;
    state.chains.has(c) ? state.chains.delete(c) : state.chains.add(c);
    syncChips(); render();
  }
});

$('panel').addEventListener('click', (e) => {
  const tr = e.target.closest('tr[data-key]');
  if (tr) { select(rows().indexOf(tr)); return; }
  const th = e.target.closest('th[data-key]');
  if (th) {
    const cur = state.sort[state.view];
    state.sort[state.view] = (cur && cur.key === th.dataset.key)
      ? { key: cur.key, dir: cur.dir === 'asc' ? 'desc' : 'asc' }
      : { key: th.dataset.key, dir: 'desc' };
    render();
  }
});

document.addEventListener('click', (e) => {
  const goto = e.target.closest('[data-goto]');
  if (goto) {
    const [kind, ...keyParts] = goto.dataset.goto.split(':');
    openDetail(kind, keyParts.join(':'));
    return;
  }
  const fkey = e.target.closest('[data-cmd]');
  if (fkey) { $('cmd').value = ''; runCommand(fkey.dataset.cmd); }
});

boot();
