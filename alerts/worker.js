/**
 * Hoodwall realtime alert relay — Cloudflare Worker.
 *
 * GitHub Actions cannot do realtime: its cron minimum is 5 minutes and
 * scheduled runs are routinely delayed 5-20 minutes under load. This Worker
 * is the realtime half of the system and stays inside free-tier limits
 * (100k requests/day, 1-minute cron, WebSockets included).
 *
 * Two inbound paths:
 *   POST /hook/alchemy  - EVM address activity (Alchemy Notify)
 *   POST /hook/helius   - Solana address activity (Helius webhooks)
 * Two polled paths (cron, 1 min):
 *   Hyperliquid  - clearinghouseState per watched address
 *   Bitcoin      - mempool.space address stats
 *
 * Outbound: Telegram immediately, plus an optional repository_dispatch so
 * the Actions pipeline can do deep enrichment on what the alert found.
 *
 * Secrets (wrangler secret put ...):
 *   TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
 *   ALCHEMY_SIGNING_KEY, HELIUS_AUTH_HEADER   (verify inbound webhooks)
 *   GITHUB_TOKEN, GITHUB_REPO                 (optional deep-scan trigger)
 * KV namespace: WATCHLIST (key "watchlist" = JSON from the knowledge base)
 */

const TELEGRAM_API = 'https://api.telegram.org';
const HL_INFO = 'https://api.hyperliquid.xyz/info';
const MEMPOOL_API = 'https://mempool.space/api';

/* ---------- helpers ---------- */

async function sendTelegram(env, text) {
  if (!env.TELEGRAM_BOT_TOKEN || !env.TELEGRAM_CHAT_ID) return false;
  const res = await fetch(`${TELEGRAM_API}/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      chat_id: env.TELEGRAM_CHAT_ID,
      text,
      parse_mode: 'Markdown',
      disable_web_page_preview: true,
    }),
  });
  return res.ok;
}

/** Constant-time compare so a signature check cannot be timed. */
function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

async function verifyAlchemy(env, rawBody, signature) {
  // An unauthenticated webhook endpoint is an open alert-injection channel,
  // so an unverifiable request is rejected rather than trusted.
  if (!env.ALCHEMY_SIGNING_KEY) return false;
  const key = await crypto.subtle.importKey(
    'raw', new TextEncoder().encode(env.ALCHEMY_SIGNING_KEY),
    { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const mac = await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(rawBody));
  const hex = [...new Uint8Array(mac)].map((b) => b.toString(16).padStart(2, '0')).join('');
  return timingSafeEqual(hex, (signature || '').toLowerCase());
}

async function getWatchlist(env) {
  try {
    const raw = await env.WATCHLIST.get('watchlist');
    return raw ? JSON.parse(raw) : { evm: [], solana: [], hyperliquid: [], bitcoin: [], meta: {} };
  } catch {
    return { evm: [], solana: [], hyperliquid: [], bitcoin: [], meta: {} };
  }
}

/** Look up curated context so an alert says WHO moved, not just an address. */
function describe(watchlist, address) {
  const meta = watchlist.meta?.[(address || '').toLowerCase()];
  if (!meta) return '';
  const bits = [];
  if (meta.entity) bits.push(`*${meta.entity}*`);
  if (meta.handle) bits.push(meta.handle);
  if (meta.labels?.length) bits.push(`_${meta.labels.join(', ')}_`);
  return bits.length ? `\n${bits.join(' · ')}` : '';
}

const short = (a) => (a && a.length > 14 ? `${a.slice(0, 8)}…${a.slice(-6)}` : a || '');

async function triggerDeepScan(env, reason, addresses) {
  if (!env.GITHUB_TOKEN || !env.GITHUB_REPO) return;
  await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/dispatches`, {
    method: 'POST',
    headers: {
      authorization: `Bearer ${env.GITHUB_TOKEN}`,
      accept: 'application/vnd.github+json',
      'content-type': 'application/json',
      'user-agent': 'hoodwall-alert-relay',
    },
    body: JSON.stringify({
      event_type: 'wallet-activity',
      client_payload: { reason, addresses: addresses.slice(0, 50) },
    }),
  });
}

/* ---------- inbound webhooks ---------- */

async function handleAlchemy(request, env) {
  const raw = await request.text();
  const signature = request.headers.get('x-alchemy-signature');
  if (!(await verifyAlchemy(env, raw, signature))) {
    return new Response('invalid signature', { status: 401 });
  }

  const payload = JSON.parse(raw);
  const activity = payload?.event?.activity || [];
  if (!activity.length) return new Response('ok');

  const watchlist = await getWatchlist(env);
  const network = payload?.event?.network || 'EVM';
  const touched = new Set();

  const lines = activity.slice(0, 10).map((a) => {
    touched.add((a.fromAddress || '').toLowerCase());
    touched.add((a.toAddress || '').toLowerCase());
    const asset = a.asset || 'ETH';
    const value = a.value !== undefined ? Number(a.value).toLocaleString('en-US',
      { maximumFractionDigits: 4 }) : '?';
    return `\`${short(a.fromAddress)}\` → \`${short(a.toAddress)}\`\n` +
           `${value} ${asset}${describe(watchlist, a.fromAddress)}`;
  });

  await sendTelegram(env,
    `🔔 *ACTIVITY* · ${network}\n\n${lines.join('\n\n')}` +
    (activity.length > 10 ? `\n\n_+${activity.length - 10} more events_` : ''));
  await triggerDeepScan(env, 'alchemy-activity', [...touched].filter(Boolean));
  return new Response('ok');
}

async function handleHelius(request, env) {
  // Helius sends the shared secret in the Authorization header.
  if (env.HELIUS_AUTH_HEADER &&
      request.headers.get('authorization') !== env.HELIUS_AUTH_HEADER) {
    return new Response('unauthorized', { status: 401 });
  }
  const events = await request.json();
  const list = Array.isArray(events) ? events : [events];
  if (!list.length) return new Response('ok');

  const watchlist = await getWatchlist(env);
  const lines = list.slice(0, 8).map((e) => {
    const who = e.feePayer || (e.accountData?.[0]?.account) || 'unknown';
    return `\`${short(who)}\`\n${e.type || 'TRANSACTION'}` +
           `${e.description ? `\n_${String(e.description).slice(0, 140)}_` : ''}` +
           describe(watchlist, who);
  });
  await sendTelegram(env, `🔔 *ACTIVITY* · SOLANA\n\n${lines.join('\n\n')}`);
  return new Response('ok');
}

/* ---------- polled chains ---------- */

async function pollHyperliquid(env, watchlist) {
  const addresses = watchlist.hyperliquid || [];
  const alerts = [];
  // Batched to stay under the Worker's 50-subrequest ceiling per invocation.
  for (const address of addresses.slice(0, 20)) {
    try {
      const res = await fetch(HL_INFO, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ type: 'clearinghouseState', user: address }),
      });
      if (!res.ok) continue;
      const data = await res.json();
      const equity = Number(data?.marginSummary?.accountValue || 0);
      const key = `hl:${address}`;
      const prevRaw = await env.WATCHLIST.get(key);
      const prev = prevRaw ? Number(prevRaw) : null;
      await env.WATCHLIST.put(key, String(equity), { expirationTtl: 604800 });

      // Only a material move is worth a notification; small drift is noise.
      if (prev && prev > 0) {
        const change = (equity - prev) / prev;
        if (Math.abs(change) >= 0.10) {
          alerts.push(`\`${short(address)}\` equity ${change > 0 ? '▲' : '▼'} ` +
            `${(change * 100).toFixed(1)}%\n$${prev.toLocaleString('en-US',
              { maximumFractionDigits: 0 })} → $${equity.toLocaleString('en-US',
              { maximumFractionDigits: 0 })}${describe(watchlist, address)}`);
        }
      }
    } catch { /* one address failing must not stop the sweep */ }
  }
  if (alerts.length) {
    await sendTelegram(env, `⚡ *HYPERLIQUID*\n\n${alerts.join('\n\n')}`);
  }
  return alerts.length;
}

async function pollBitcoin(env, watchlist) {
  const addresses = watchlist.bitcoin || [];
  const alerts = [];
  for (const address of addresses.slice(0, 15)) {
    try {
      const res = await fetch(`${MEMPOOL_API}/address/${address}`);
      if (!res.ok) continue;
      const data = await res.json();
      const txCount = (data.chain_stats?.tx_count || 0) + (data.mempool_stats?.tx_count || 0);
      const key = `btc:${address}`;
      const prevRaw = await env.WATCHLIST.get(key);
      await env.WATCHLIST.put(key, String(txCount), { expirationTtl: 604800 });
      if (prevRaw && txCount > Number(prevRaw)) {
        const balance = ((data.chain_stats?.funded_txo_sum || 0) -
                         (data.chain_stats?.spent_txo_sum || 0)) / 1e8;
        alerts.push(`\`${short(address)}\`\n${txCount - Number(prevRaw)} new tx · ` +
          `balance ${balance.toFixed(4)} BTC${describe(watchlist, address)}`);
      }
    } catch { /* ignore individual failures */ }
  }
  if (alerts.length) await sendTelegram(env, `₿ *BITCOIN*\n\n${alerts.join('\n\n')}`);
  return alerts.length;
}

/* ---------- entrypoints ---------- */

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === '/health') {
      const watchlist = await getWatchlist(env);
      return Response.json({
        ok: true,
        watching: {
          evm: (watchlist.evm || []).length,
          solana: (watchlist.solana || []).length,
          hyperliquid: (watchlist.hyperliquid || []).length,
          bitcoin: (watchlist.bitcoin || []).length,
        },
      });
    }

    // Lets the Actions pipeline push a refreshed watchlist from the KB.
    if (url.pathname === '/watchlist' && request.method === 'POST') {
      if (request.headers.get('authorization') !== `Bearer ${env.WATCHLIST_TOKEN}`) {
        return new Response('unauthorized', { status: 401 });
      }
      const body = await request.text();
      await env.WATCHLIST.put('watchlist', body);
      return new Response('stored');
    }

    if (request.method !== 'POST') return new Response('hoodwall alert relay', { status: 200 });
    if (url.pathname === '/hook/alchemy') return handleAlchemy(request, env);
    if (url.pathname === '/hook/helius') return handleHelius(request, env);
    return new Response('not found', { status: 404 });
  },

  async scheduled(event, env, ctx) {
    const watchlist = await getWatchlist(env);
    ctx.waitUntil(Promise.all([
      pollHyperliquid(env, watchlist),
      pollBitcoin(env, watchlist),
    ]));
  },
};
