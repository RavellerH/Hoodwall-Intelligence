/* Force-directed wallet relationship map.
   Hand-rolled rather than pulling in d3: the whole dashboard is
   dependency-free, N is small enough that naive O(N^2) repulsion is fine
   (96 nodes = ~9k pair calculations per frame), and this keeps full control
   over matching the light/dark design system rather than a canvas that
   looks bolted onto the page.

   Two edge kinds are drawn differently on purpose. A transfer edge is
   evidence (solid, arrowed, thickness by value); a behavioural edge is
   inference (faint, dashed). Blending them would let inference read as
   proof.

   Colors are theme-aware: the caller reads the page's current CSS custom
   properties and passes them in as `opts.palette`, so the graph matches
   light or dark mode instead of carrying its own fixed palette. */
'use strict';

const DEFAULT_PALETTE = {
  transfer: '#4c5fea', behavioural: '#c7cbd6', text: '#565f70',
  nodes: ['#4c5fea', '#0ea5a4', '#7c3aed', '#e0293f', '#b8860b',
          '#2563eb', '#16a34a', '#db2777', '#0891b2', '#65a30d'],
};

function createGraph(canvas, data, opts = {}) {
  const ctx = canvas.getContext('2d');
  const onSelect = opts.onSelect || (() => {});
  const palette = { ...DEFAULT_PALETTE, ...(opts.palette || {}) };

  const nodes = data.nodes.map((n, i) => ({
    ...n,
    // Seed on a circle rather than at random: the layout converges faster
    // and, being deterministic, the same graph always looks the same.
    x: Math.cos((i / data.nodes.length) * Math.PI * 2) * 220 + (i % 7) * 3,
    y: Math.sin((i / data.nodes.length) * Math.PI * 2) * 220 + (i % 5) * 3,
    vx: 0, vy: 0, degree: 0,
  }));
  const index = new Map(nodes.map((n) => [n.id, n]));

  const edges = data.edges
    .map((e) => ({ ...e, s: index.get(e.source), t: index.get(e.target) }))
    .filter((e) => e.s && e.t);
  for (const e of edges) { e.s.degree++; e.t.degree++; }

  const clusterColor = new Map();
  (data.clusters || []).forEach((c, i) => clusterColor.set(c.id, palette.nodes[i % palette.nodes.length]));

  const maxValue = Math.max(1, ...nodes.map((n) => n.value_usd || 0));
  const maxDegree = Math.max(1, ...nodes.map((n) => n.degree));

  function radius(n) {
    // Value drives size where known; degree is the fallback so wallets with
    // no observed balance are not all invisible dots.
    const byValue = (n.value_usd || 0) / maxValue;
    const byDegree = n.degree / maxDegree;
    return 3.5 + Math.sqrt(Math.max(byValue, byDegree * 0.55)) * 15;
  }

  const view = { x: 0, y: 0, k: 1 };
  let hover = null, selected = null, dragging = null, panning = null;
  let alpha = 1, running = true;
  // The camera tracks the layout until the user takes control. Fitting only
  // once, after the simulation settles, leaves nodes off-screen for the
  // ~10s the layout takes to converge.
  let userMoved = false;

  /** Centre and scale the view so the whole graph is visible. */
  function fit(padding = 60) {
    if (!nodes.length) return;
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const n of nodes) {
      const r = radius(n);
      minX = Math.min(minX, n.x - r); maxX = Math.max(maxX, n.x + r);
      minY = Math.min(minY, n.y - r); maxY = Math.max(maxY, n.y + r);
    }
    const w = canvas.clientWidth - padding * 2;
    const h = canvas.clientHeight - padding * 2;
    const spanX = Math.max(1, maxX - minX), spanY = Math.max(1, maxY - minY);
    view.k = Math.max(0.25, Math.min(2.2, Math.min(w / spanX, h / spanY)));
    view.x = -(minX + maxX) / 2;
    view.y = -(minY + maxY) / 2;
  }

  /* ---------- physics ---------- */
  function step() {
    const REPULSION = 5200, SPRING = 0.012, CENTER = 0.0016, DAMPING = 0.86;

    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j];
        let dx = a.x - b.x, dy = a.y - b.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 0.01) { dx = (Math.random() - 0.5) * 0.1; dy = (Math.random() - 0.5) * 0.1; d2 = 0.01; }
        if (d2 > 260000) continue;                 // far apart: ignore
        const force = REPULSION / d2;
        const d = Math.sqrt(d2);
        const fx = (dx / d) * force, fy = (dy / d) * force;
        a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy;
      }
    }

    for (const e of edges) {
      const dx = e.t.x - e.s.x, dy = e.t.y - e.s.y;
      const d = Math.hypot(dx, dy) || 0.01;
      // Transfer edges pull harder, so proven relationships dominate layout.
      const strength = SPRING * e.weight * (e.kind === 'transfer' ? 1.8 : 1);
      const target = e.kind === 'transfer' ? 90 : 150;
      const force = (d - target) * strength;
      const fx = (dx / d) * force, fy = (dy / d) * force;
      e.s.vx += fx; e.s.vy += fy; e.t.vx -= fx; e.t.vy -= fy;
    }

    for (const n of nodes) {
      n.vx -= n.x * CENTER; n.vy -= n.y * CENTER;
      if (n === dragging) { n.vx = n.vy = 0; continue; }
      n.vx *= DAMPING; n.vy *= DAMPING;
      n.x += n.vx * alpha; n.y += n.vy * alpha;
    }
    alpha *= 0.985;
    if (alpha < 0.02) running = false;
  }

  /* ---------- rendering ---------- */
  function toScreen(n) {
    return { x: (n.x + view.x) * view.k + canvas.clientWidth / 2,
             y: (n.y + view.y) * view.k + canvas.clientHeight / 2 };
  }

  function neighboursOf(node) {
    const set = new Set();
    if (!node) return set;
    for (const e of edges) {
      if (e.s === node) set.add(e.t);
      if (e.t === node) set.add(e.s);
    }
    return set;
  }

  function draw() {
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
      canvas.width = w * dpr; canvas.height = h * dpr;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    const focus = hover || selected;
    const near = neighboursOf(focus);

    for (const e of edges) {
      const a = toScreen(e.s), b = toScreen(e.t);
      const involved = !focus || e.s === focus || e.t === focus;
      const transfer = e.kind === 'transfer';

      ctx.save();
      ctx.globalAlpha = involved ? (transfer ? 0.9 : 0.55) : 0.06;
      ctx.strokeStyle = transfer ? palette.transfer : palette.behavioural;
      ctx.lineWidth = transfer
        ? Math.min(4, 0.6 + Math.log10(1 + (e.value || 0)) * 0.9)
        : 1;
      if (!transfer) ctx.setLineDash([3, 4]);
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();

      // Arrowhead only on evidence edges, and only when legible.
      if (transfer && involved && view.k > 0.55) {
        const angle = Math.atan2(b.y - a.y, b.x - a.x);
        const r = radius(e.t) * view.k + 3;
        const tipX = b.x - Math.cos(angle) * r, tipY = b.y - Math.sin(angle) * r;
        ctx.setLineDash([]);
        ctx.fillStyle = palette.transfer;
        ctx.beginPath();
        ctx.moveTo(tipX, tipY);
        ctx.lineTo(tipX - Math.cos(angle - 0.4) * 7, tipY - Math.sin(angle - 0.4) * 7);
        ctx.lineTo(tipX - Math.cos(angle + 0.4) * 7, tipY - Math.sin(angle + 0.4) * 7);
        ctx.closePath(); ctx.fill();
      }
      ctx.restore();
    }

    for (const n of nodes) {
      const p = toScreen(n);
      const r = Math.max(2, radius(n) * view.k);
      const dim = focus && n !== focus && !near.has(n);
      const color = clusterColor.get(n.cluster) || palette.behavioural;

      ctx.save();
      ctx.globalAlpha = dim ? 0.15 : 1;
      ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.globalAlpha = dim ? 0.12 : 0.75;
      ctx.fill();

      ctx.globalAlpha = dim ? 0.2 : 1;
      ctx.lineWidth = n === selected ? 2.5 : 1;
      // Unresolved (masked) wallets get a dashed ring: they cannot be
      // enriched or alerted on, and that limitation should be visible.
      ctx.setLineDash(n.resolved ? [] : [2, 2]);
      ctx.strokeStyle = n === selected ? palette.transfer : color;
      ctx.stroke();
      ctx.restore();

      if (!dim && (view.k > 1.15 || r > 11 || n === focus)) {
        ctx.save();
        ctx.globalAlpha = 0.95;
        ctx.fillStyle = palette.text;
        ctx.font = '600 10px -apple-system, "Segoe UI", sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(n.entity || n.display, p.x, p.y + r + 11);
        ctx.restore();
      }
    }
  }

  function frame() {
    if (running) {
      step();
      if (!userMoved) fit();
    }
    draw();
    requestAnimationFrame(frame);
  }

  /* ---------- interaction ---------- */
  function pick(clientX, clientY) {
    const rect = canvas.getBoundingClientRect();
    const mx = clientX - rect.left, my = clientY - rect.top;
    let best = null, bestDist = Infinity;
    for (const n of nodes) {
      const p = toScreen(n);
      const d = Math.hypot(p.x - mx, p.y - my);
      const r = Math.max(6, radius(n) * view.k + 4);
      if (d < r && d < bestDist) { best = n; bestDist = d; }
    }
    return best;
  }

  canvas.addEventListener('mousemove', (e) => {
    if (panning) {
      userMoved = true;
      view.x += (e.clientX - panning.x) / view.k;
      view.y += (e.clientY - panning.y) / view.k;
      panning = { x: e.clientX, y: e.clientY };
      return;
    }
    if (dragging) {
      userMoved = true;
      const rect = canvas.getBoundingClientRect();
      dragging.x = (e.clientX - rect.left - canvas.clientWidth / 2) / view.k - view.x;
      dragging.y = (e.clientY - rect.top - canvas.clientHeight / 2) / view.k - view.y;
      alpha = Math.max(alpha, 0.35); running = true;
      return;
    }
    hover = pick(e.clientX, e.clientY);
    canvas.style.cursor = hover ? 'pointer' : 'grab';
  });

  canvas.addEventListener('mousedown', (e) => {
    const hit = pick(e.clientX, e.clientY);
    if (hit) { dragging = hit; selected = hit; onSelect(hit); }
    else panning = { x: e.clientX, y: e.clientY };
  });
  window.addEventListener('mouseup', () => { dragging = null; panning = null; });

  canvas.addEventListener('wheel', (e) => {
    e.preventDefault();
    userMoved = true;
    view.k = Math.max(0.25, Math.min(4, view.k * (e.deltaY < 0 ? 1.12 : 0.89)));
  }, { passive: false });

  frame();

  return {
    fit,
    reheat() { alpha = 1; running = true; userMoved = false; },
    focusNode(id) {
      const n = index.get(id);
      if (!n) return;
      userMoved = true;
      selected = n;
      view.x = -n.x; view.y = -n.y; view.k = Math.max(view.k, 1.4);
      onSelect(n);
    },
    legend() {
      return (data.clusters || []).map((c, i) => ({
        ...c, color: palette.nodes[i % palette.nodes.length],
      }));
    },
  };
}
