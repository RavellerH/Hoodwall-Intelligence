/* Hoodwall Intelligence dashboard.
   Plain ES2020, no build step and no CDN: GitHub Pages serves these three
   files exactly as committed. All shaping happens in pipeline/publish.py,
   so this file only filters, sorts and renders. */
'use strict';

const TIERS = ['elite', 'watch', 'candidate'];
const state = {
  wallets: [],
  meta: {},
  tiers: new Set(['elite', 'watch']),
  search: '',
  label: '',
  source: '',
  sort: 'smart_score',
  dir: 'desc',
};

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const short = (a) => `${a.slice(0, 10)}…${a.slice(-8)}`;
const tierColor = (t) => `var(--${t})`;

function num(v, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  const n = Number(v);
  if (Math.abs(n) >= 1000) return n.toLocaleString('en-US', { maximumFractionDigits: 0 });
  const s = n.toFixed(digits);
  // Strip trailing zeros only after a decimal point - a bare /0+$/ would
  // turn 180 into 18 and 100 into 1.
  return s.includes('.') ? s.replace(/0+$/, '').replace(/\.$/, '') : s;
}

function ago(days) {
  if (days === null || days === undefined) return '—';
  if (days < 1 / 24) return 'now';
  if (days < 1) return `${Math.round(days * 24)}h`;
  if (days < 30) return `${Math.round(days)}d`;
  if (days < 365) return `${Math.round(days / 30)}mo`;
  return `${(days / 365).toFixed(1)}y`;
}

/* --- load ------------------------------------------------------------- */

async function load() {
  try {
    // Cache-bust so a fresh Actions run is visible without a hard refresh.
    const bust = `?v=${Date.now()}`;
    const [wallets, meta] = await Promise.all([
      fetch(`data/wallets.json${bust}`).then((r) => r.json()),
      fetch(`data/meta.json${bust}`).then((r) => r.json()),
    ]);
    state.wallets = wallets;
    state.meta = meta;
  } catch (err) {
    $('empty').hidden = false;
    $('empty').textContent =
      'Could not load data/wallets.json — the pipeline may not have run yet.';
    $('generated').textContent = 'no data';
    return;
  }
  renderChrome();
  render();
}

/* --- static chrome ---------------------------------------------------- */

function renderChrome() {
  const m = state.meta;
  const c = m.counts || {};
  const t = m.tiers || {};

  $('generated').textContent = m.generated_at
    ? `updated ${new Date(m.generated_at).toLocaleString()}`
    : '';
  if (m.title) document.title = m.title;

  $('stats').innerHTML = [
    ['Candidates', c.candidates ?? 0, ''],
    ['Enriched', c.enriched ?? 0, ''],
    ['Elite', t.elite ?? 0, 'elite'],
    ['Watch', t.watch ?? 0, 'watch'],
    ['Candidate', t.candidate ?? 0, 'candidate'],
    ['Archived', t.archive ?? 0, ''],
  ].map(([label, value, cls]) => `
    <div class="stat">
      <div class="stat-value ${cls}">${value.toLocaleString()}</div>
      <div class="stat-label">${label}</div>
    </div>`).join('');

  const published = m.published_tiers || {};
  $('tier-filters').innerHTML = TIERS.map((tier) => `
    <button class="chip" type="button" data-tier="${tier}"
      aria-pressed="${state.tiers.has(tier)}">
      ${tier}<span class="count">${published[tier] ?? 0}</span>
    </button>`).join('');

  fillSelect('label-filter', Object.keys(m.labels || {}).sort(), 'All labels');
  fillSelect('source-filter', Object.keys(m.sources || {}).sort(), 'All sources');
}

function fillSelect(id, options, placeholder) {
  $(id).innerHTML = `<option value="">${placeholder}</option>` +
    options.map((o) => `<option value="${esc(o)}">${esc(o.replace(/_/g, ' '))}</option>`).join('');
}

/* --- filter + sort + render ------------------------------------------- */

function visible() {
  const q = state.search.trim().toLowerCase();
  const rows = state.wallets.filter((w) => {
    if (!state.tiers.has(w.tier)) return false;
    if (q && !w.address.toLowerCase().includes(q)) return false;
    if (state.source && w.source !== state.source) return false;
    if (state.label && !w.labels.some((l) => l.name === state.label)) return false;
    return true;
  });

  const key = state.sort;
  const sign = state.dir === 'desc' ? -1 : 1;
  rows.sort((a, b) => {
    let x = a[key], y = b[key];
    // Nulls (a wallet with no recorded activity) always sort last.
    if (x === null || x === undefined) return 1;
    if (y === null || y === undefined) return -1;
    if (typeof x === 'string') return sign * x.localeCompare(y);
    return sign * (x - y);
  });
  return rows;
}

function render() {
  const rows = visible();
  $('tbody').innerHTML = rows.map((w) => `
    <tr tabindex="0" data-address="${esc(w.address)}">
      <td class="addr">${esc(short(w.address))}</td>
      <td class="numeric">
        <span class="score-cell">
          <span class="score-num">${w.smart_score.toFixed(0)}</span>
          <span class="score-bar"><i style="width:${w.smart_score}%;background:${tierColor(w.tier)}"></i></span>
        </span>
      </td>
      <td>${w.labels.length
        ? w.labels.slice(0, 3).map((l) => `<span class="tag">${esc(l.name.replace(/_/g, ' '))}</span>`).join('')
        : '<span class="dim">—</span>'}</td>
      <td class="numeric">${num(w.tx_count, 0)}</td>
      <td class="numeric">${num(w.distinct_counterparties, 0)}</td>
      <td class="numeric">${num(w.active_days, 0)}</td>
      <td class="numeric">${num(w.volume_native)}</td>
      <td class="numeric dim">${ago(w.recency_days)}</td>
      <td class="dim">${esc(w.source)}</td>
    </tr>`).join('');

  $('empty').hidden = rows.length > 0;
  if (!rows.length) $('empty').textContent = 'No wallets match these filters.';

  document.querySelectorAll('th.sortable').forEach((th) => {
    if (th.dataset.sort === state.sort) {
      th.setAttribute('aria-sort', state.dir === 'desc' ? 'descending' : 'ascending');
    } else {
      th.removeAttribute('aria-sort');
    }
  });
}

/* --- detail drawer ---------------------------------------------------- */

function openDetail(address) {
  const w = state.wallets.find((x) => x.address === address);
  if (!w) return;

  const weights = state.meta.weights || {};
  // Points contributed = normalized component x its weight x 100. Showing
  // this is the whole point: every score decomposes into its inputs.
  const comps = Object.entries(w.components)
    .map(([name, value]) => ({ name, value, points: value * (weights[name] ?? 0) * 100 }))
    .sort((a, b) => b.points - a.points);

  const explorer = state.meta.explorer_url || '';
  const sym = state.meta.native_symbol || '';

  $('detail-body').innerHTML = `
    <h2>${esc(w.address)}</h2>
    <div class="headline">
      <span class="big" style="color:${tierColor(w.tier)}">${w.smart_score.toFixed(0)}</span>
      <span class="badge" style="color:${tierColor(w.tier)};border-color:${tierColor(w.tier)}">${esc(w.tier)}</span>
      <span class="dim">${esc(w.address_type)}</span>
    </div>

    <h3>Score breakdown</h3>
    ${comps.map((c) => `
      <div class="comp">
        <div class="comp-head">
          <span class="comp-name">${esc(c.name.replace(/_/g, ' '))}
            <span class="comp-weight">×${(weights[c.name] ?? 0).toFixed(2)}</span>
          </span>
          <span class="comp-pts">${c.points.toFixed(1)} pts</span>
        </div>
        <div class="comp-track"><div class="comp-fill" style="width:${(c.value * 100).toFixed(1)}%"></div></div>
      </div>`).join('')}
    ${w.penalties.length ? `
      <h3>Penalties</h3>
      ${w.penalties.map((p) => `<div class="penalty">×${p.factor} — ${esc(p.reason)}</div>`).join('')}` : ''}

    <h3>Evidence</h3>
    ${w.labels.length ? w.labels.map((l) => `
      <div class="evidence">
        <div class="evidence-name">${esc(l.name.replace(/_/g, ' '))}
          <span class="dim">(${l.confidence})</span></div>
        <div class="evidence-text">${esc(l.evidence)}</div>
      </div>`).join('') : '<p class="dim">No behavioural labels matched.</p>'}

    <h3>Activity</h3>
    <dl class="kv">
      <dt>Transactions</dt><dd>${num(w.tx_count, 0)}</dd>
      <dt>Distinct counterparties</dt><dd>${num(w.distinct_counterparties, 0)}</dd>
      <dt>Active days</dt><dd>${num(w.active_days, 0)}</dd>
      <dt>Contract ratio</dt><dd>${(w.contract_ratio * 100).toFixed(0)}%</dd>
      <dt>Volume</dt><dd>${num(w.volume_native)} ${esc(sym)}</dd>
      <dt>Net flow</dt><dd>${num(w.net_flow_native)} ${esc(sym)}</dd>
      <dt>Balance</dt><dd>${num(w.balance_native)} ${esc(sym)}</dd>
      <dt>Hour entropy</dt><dd>${num(w.hour_entropy)}</dd>
      <dt>Timing regularity (CV)</dt><dd>${num(w.gap_cv)}</dd>
      <dt>First seen</dt><dd>${w.first_seen ? new Date(w.first_seen).toLocaleDateString() : '—'}</dd>
      <dt>Last seen</dt><dd>${ago(w.recency_days)} ago</dd>
      <dt>Discovered via</dt><dd>${esc(w.source)}</dd>
    </dl>

    ${explorer ? `<a class="explorer" target="_blank" rel="noopener"
       href="${esc(explorer)}/address/${esc(w.address)}">View on explorer ↗</a>` : ''}`;

  $('detail').hidden = false;
  $('scrim').hidden = false;
  $('detail-close').focus();
}

function closeDetail() {
  $('detail').hidden = true;
  $('scrim').hidden = true;
}

/* --- events ----------------------------------------------------------- */

$('search').addEventListener('input', (e) => { state.search = e.target.value; render(); });
$('label-filter').addEventListener('change', (e) => { state.label = e.target.value; render(); });
$('source-filter').addEventListener('change', (e) => { state.source = e.target.value; render(); });

$('tier-filters').addEventListener('click', (e) => {
  const chip = e.target.closest('[data-tier]');
  if (!chip) return;
  const tier = chip.dataset.tier;
  if (state.tiers.has(tier)) state.tiers.delete(tier); else state.tiers.add(tier);
  chip.setAttribute('aria-pressed', state.tiers.has(tier));
  render();
});

document.querySelector('thead').addEventListener('click', (e) => {
  const th = e.target.closest('th.sortable');
  if (!th) return;
  const key = th.dataset.sort;
  // Re-clicking the active column flips direction; a new column starts
  // descending, which is what you want for every numeric column here.
  if (state.sort === key) {
    state.dir = state.dir === 'desc' ? 'asc' : 'desc';
  } else {
    state.sort = key;
    state.dir = key === 'address' || key === 'source' ? 'asc' : 'desc';
  }
  render();
});

$('tbody').addEventListener('click', (e) => {
  const tr = e.target.closest('tr[data-address]');
  if (tr) openDetail(tr.dataset.address);
});
$('tbody').addEventListener('keydown', (e) => {
  if (e.key !== 'Enter' && e.key !== ' ') return;
  const tr = e.target.closest('tr[data-address]');
  if (tr) { e.preventDefault(); openDetail(tr.dataset.address); }
});

$('detail-close').addEventListener('click', closeDetail);
$('scrim').addEventListener('click', closeDetail);
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeDetail(); });

$('reset').addEventListener('click', () => {
  state.search = ''; state.label = ''; state.source = '';
  state.tiers = new Set(['elite', 'watch']);
  $('search').value = ''; $('label-filter').value = ''; $('source-filter').value = '';
  document.querySelectorAll('[data-tier]').forEach(
    (c) => c.setAttribute('aria-pressed', state.tiers.has(c.dataset.tier)));
  render();
});

load();
