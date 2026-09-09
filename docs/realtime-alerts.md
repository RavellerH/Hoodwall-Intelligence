# Realtime alerts on free infrastructure

## Why not GitHub Actions

Actions is the pipeline's cron engine, but it cannot do realtime:

- The minimum schedule is **5 minutes**.
- Scheduled runs are **best-effort** and routinely delayed 5–20 minutes
  under load; GitHub does not guarantee execution time.
- Schedules are **disabled after 60 days** without repository activity.

So alerting runs on a separate free layer and the pipeline keeps doing
deep analysis on its own cadence.

## Architecture

```
EVM (RH Chain, ETH L2s) ──> Alchemy Address Activity webhook ─┐
Solana ─────────────────> Helius webhook ─────────────────────┤
                                                              ├─> Cloudflare ──> Telegram
Hyperliquid ────────────> Worker cron (60s) polls info API ───┤     Worker
Bitcoin ────────────────> Worker cron (60s) polls mempool ────┘        │
                                                                       └─> repository_dispatch
                                                                           ──> Actions deep scan
```

Push-based chains alert in **seconds**; polled chains within **60 seconds**.

## Free-tier budget

| Service | Free allowance | What we use it for | Headroom |
|---|---|---|---|
| Cloudflare Workers | 100k req/day, 1-min cron, WebSockets included | The relay itself | ~1,440 cron invocations/day plus webhooks |
| Cloudflare KV | 100k reads/day, 1k writes/day | Watchlist + last-seen state | Writes are the binding limit — see below |
| Alchemy Notify | 5 webhooks, 100k addresses each, 30M CU/mo | EVM address activity | ~40 CU/event ≈ **750k events/month** |
| Helius | 1 webhook, free monthly credits | Solana address activity | Fine for a curated watchlist |
| Hyperliquid | Public API, **no key** | `clearinghouseState` polling | Rate limits are address-based |
| mempool.space | Public API, ~10 req/s | Bitcoin address stats | Undocumented ceiling; keep the list small |
| Telegram Bot | Unlimited for personal use | Delivery | — |

**Total: $0/month.**

### The real constraint

Cloudflare KV allows **1,000 writes/day** on free. The Worker writes one key
per polled address per run, so a 1-minute cron over *N* addresses costs
`1440 × N` writes/day — meaning **one polled address already exceeds the
limit**.

The Worker therefore writes only when a value actually changes, and the
polled lists are capped (20 Hyperliquid, 15 Bitcoin) to stay inside the
50-subrequest-per-invocation ceiling. If you need more polled addresses,
raise the cron interval to 5 minutes (`*/5 * * * *`) rather than adding
addresses at 1-minute cadence. Push-based chains have no such limit, which
is why EVM and Solana should carry as much of the watchlist as possible.

## Setup

1. **Install wrangler and create the KV namespace**
   ```bash
   npm install -g wrangler
   cd alerts && wrangler login
   wrangler kv namespace create WATCHLIST     # paste the id into wrangler.toml
   ```

2. **Set secrets**
   ```bash
   wrangler secret put TELEGRAM_BOT_TOKEN
   wrangler secret put TELEGRAM_CHAT_ID
   wrangler secret put WATCHLIST_TOKEN        # any long random string
   wrangler secret put ALCHEMY_SIGNING_KEY    # from the Alchemy webhook page
   wrangler secret put HELIUS_AUTH_HEADER     # optional
   wrangler secret put GITHUB_TOKEN           # optional, for deep-scan dispatch
   wrangler secret put GITHUB_REPO            # e.g. RavellerH/Hoodwall-Intelligence
   ```

3. **Deploy**
   ```bash
   wrangler deploy      # prints https://hoodwall-alerts.<subdomain>.workers.dev
   ```

4. **Push the watchlist from the knowledge base**
   ```bash
   python scripts/kb_watchlist.py --push https://hoodwall-alerts.<sub>.workers.dev \
     --token "$WATCHLIST_TOKEN"
   ```

5. **Point the providers at the Worker**
   - Alchemy → Notify → Address Activity → webhook URL `.../hook/alchemy`
   - Helius → Webhooks → `.../hook/helius`

6. **Check it**
   ```bash
   curl https://hoodwall-alerts.<sub>.workers.dev/health
   ```

## Security

The webhook endpoints are public URLs, so an unauthenticated endpoint would
be an open alert-injection channel. `/hook/alchemy` verifies the HMAC-SHA256
signature with a constant-time compare and rejects anything unverifiable;
`/hook/helius` checks the shared Authorization header; `/watchlist` requires
a bearer token. Never commit any of these values — they are `wrangler
secret` entries, not `wrangler.toml` fields.

## Masked wallets never alert

An address known only in masked form (`0x3475…3a12`) cannot be watched — a
provider needs the full address. `kb_watchlist.py` therefore skips them,
which is why the exported EVM list can be far smaller than the wallet count
in the terminal. Resolve them first (see the mask resolution note in the
main README).
