# Hoodwall Intelligence

Multi-chain wallet and narrative intelligence, served as a dashboard on
GitHub Pages — sidebar navigation, card overviews, readable tables, in the
visual language of Nansen / DropsTab / Token Terminal / CoinMarketCap
rather than a command-line terminal.

Two halves that meet in the dashboard:

- **The knowledge base** (`knowledge/`) — your curated wallets, entity
  clusters, narratives and sources across Robinhood Chain, Ethereum L2s,
  Hyperliquid, Solana and Bitcoin. Human-authored, schema-validated, never
  overwritten by automation.
- **The pipeline** — discovers and enriches wallets on-chain, scores them
  with a published deterministic formula, pulls free market data, and
  merges all of it onto your curated records.

Plus a **realtime alert relay** on Cloudflare Workers, because Actions cron
cannot go below 5 minutes. See [docs/realtime-alerts.md](docs/realtime-alerts.md).

**No server, no database, no LLM, no cost.** GitHub Actions is the cron
engine, the repo is the database, GitHub Pages is the front end.

## The dashboard

Mouse-first: a sidebar for navigation, a search bar that filters whatever
view is open, sortable tables, and a detail panel that slides in from the
right. Number keys `1`–`9` and `/` still work for anyone who prefers them,
but nothing requires the keyboard.

| Section | What it shows |
|---|---|
| Overview | KPI tiles plus breakdowns by chain, label and entity cluster |
| Wallets | Every tracked wallet, all chains, with score and confidence |
| Entities | Wallets grouped by the person or organization behind them |
| Narratives | Theses being tracked, with measured outcomes |
| **Relationship Map** | **Bubblemaps-style wallet graph, with real flow** |
| **Positions & P&L** | **Live equity, leverage, realized/unrealized P&L** |
| **Sentiment** | **A confidence-weighted read across five sources** |
| Chains | Coverage per chain and whether it has a live adapter |
| Sources | Feeds this system reads, with their measured reliability |

A live price ticker in the top bar (BTC, ETH, SOL, HYPE, ARB, OP) comes
from DefiLlama's free API — see [Market data](#market-data-defillama) below.

### The graph (Relationship Map)

Solid arrowed edges are **transfers** — value actually moved on-chain.
Faint dashed edges are **behavioural** — the wallets share an entity, token,
narrative or source. They are never blended, and the legend always states
what share of edges are evidence rather than inference. Nodes are sized by
value, coloured by cluster, and a dashed ring marks a masked address that
cannot gain transfer edges until resolved.

### Sentiment

Five sources, each reporting a score, a confidence and its evidence.
Confidence-weighted, so a source with no data contributes nothing instead of
dragging the reading toward neutral. Fully deterministic today, with one
clean seam where an LLM narrator can be dropped in later.

See [docs/graph-and-sentiment.md](docs/graph-and-sentiment.md).

A violet address in the wallet table marks one known only in masked form
(e.g. `0x3475…3a12`).

## Market data (DefiLlama)

[`docs/tool-landscape.md`](docs/tool-landscape.md) surveys the third-party
tools shared for this project (FOMO, Kaito, Bubblemaps, Artemis, GMGN, …).
Of that whole list, exactly one has a genuinely free, keyless API:
**DefiLlama**. Everything else is either a consumer app with no public data
API, or gates its API behind signup or payment — Kaito's free Yaps API in
particular was shut down in January 2026 after X revoked its access.

`pipeline/adapters/defillama.py` pulls chain TVL and major-cap token prices
with 24h change, at zero cost and no API key. This feeds the top-bar ticker
and, for the first time, gives `pipeline/sentiment.py`'s `price` signal real
data instead of an always-empty stub. Run it on its own with:

```bash
python run.py market
```

## Chain coverage

| Chain | Family | Live enrichment |
|---|---|---|
| Robinhood Chain, Ethereum, Arbitrum, Base, OP | EVM | Blockscout |
| Hyperliquid | EVM addresses, perps venue | **live** (public API, no key) |
| Solana | SVM (base58) | knowledge base only |
| Bitcoin | UTXO | knowledge base only (entity-level) |

Chains marked *knowledge base only* are fully tracked as curated records;
they simply have no live adapter yet.

Hyperliquid has its own adapter and its own scorer, because perps data is
positional rather than transactional — there is no counterparty diversity
or contract engagement to measure. It scores on capital scale, realized
performance, activity, diversification and **risk control** (leverage is
not penalized below 5x, since perps are leveraged by design, but an account
above 20x is one wick from liquidation). It needs no API key, so it works
immediately:

```bash
python run.py hyperliquid
```

### API budget

Blockscout's free tier is 100,000 credits/day at 5 req/s; most endpoints
cost 20 credits, so roughly **5,000 calls/day**. The pipeline runs hourly
rather than half-hourly for exactly this reason: one run costs about
60 wallets x 2 calls + ~22 discovery calls = 142, so 24 runs/day is ~3,400
calls, about 68% of the allowance. A 30-minute cadence would exceed the
free tier outright. Get a key at https://blockscout.com and store it as the
`BLOCKSCOUT_API_KEY` **secret** — never in a file.

See [docs/knowledge-base.md](docs/knowledge-base.md) for how to add records.

## How it works

```
                    ┌──────────────── GitHub Actions (cron, every 30 min) ───────────────┐
                    │                                                                    │
 hood.vantis.sh ───▶│  ingest ──▶ enrich ──▶ score ──▶ publish                            │
 Telegram channel ─▶│    │          │          │          │                               │
 Blockscout ───────▶│    │          │          │          │                               │
                    │    ▼          ▼          ▼          ▼                               │
                    │  data/candidates.json  data/scores.json   site/data/*.json          │
                    │           (committed back to the repo = the database)               │
                    └────────────────────────────────┬───────────────────────────────────┘
                                                     ▼
                                          GitHub Pages (static dashboard)
```

| Stage | What it does |
|---|---|
| `ingest` | Scrapes hood.vantis.sh, polls the Telegram channel, walks recent Blockscout blocks, then resolves masked addresses |
| `enrich` | Pulls each candidate's on-chain profile and recent transactions |
| `score` | Computes the Smart Score and behavioural labels — pure function, no network |
| `publish` | Writes the JSON the dashboard reads |

Run any stage locally: `python run.py ingest|enrich|score|publish|all`

## The Smart Score

The score is a **weighted formula, not a model output** — so every number
decomposes into its inputs, and the dashboard shows that breakdown for
each wallet.

| Component | Weight | Measures |
|---|---|---|
| Activity scale | 0.25 | Volume of transactions (log-scaled) |
| Sustained presence | 0.20 | Active days and day-to-day density |
| Counterparty breadth | 0.20 | Distinct counterparties, and diversity per transaction |
| Protocol engagement | 0.15 | Contract interaction vs plain transfers |
| Economic weight | 0.15 | Native value actually moved |
| Recency | 0.05 | Exponential decay, 14-day half-life |

Multiplicative penalties apply for a high revert rate (×0.70), activity
concentrated on a single counterparty (×0.70), and many transactions
moving no value (×0.75). Wallets with fewer than 3 events are capped at 20.

Tiers: **elite** ≥80, **watch** ≥65, **candidate** ≥45, else archived.

Tuning belongs in `SATURATION` in `pipeline/scoring.py` — the point at
which a component earns full credit — rather than in the weights.

Labels (`smart_money`, `whale`, `trading_bot`, `mev_bot`, `accumulator`,
`distributor`, `lp_mm`, `fresh_emerging`, `noise`) are rule-based and each
carries the evidence string that triggered it.

## Money-flow tracing

One address is a starting point. `pipeline/flow.py` walks the value that
moved out of it and scores every counterparty as "same owner", with the
evidence attached — sole funding, round-trips, exclusivity, sweeps — while
throwing out the three things that make naive clustering useless: contracts,
high-degree services, and CEX deposit addresses (which are exclusive to one
user without belonging to them).

```bash
python scripts/trace_flow.py                    # trace knowledge/watchlist.yml
python scripts/trace_flow.py --seed 0x... --depth 1
python scripts/trace_flow.py --write-kb         # promote probable links
```

Submitted addresses are recorded in `knowledge/watchlist.yml`, not in
`knowledge/wallets/`, because a bare `0x` address carries no chain — the
tracer probes every readable EVM chain and only then files the record.
Links it infers are written with `confidence: inferred` and never overwrite
a hand-authored record. Full method and its limits:
[docs/flow-tracing.md](docs/flow-tracing.md).

Deposit addresses are remembered in `data/deposits.json`, which inverts the
exchange boundary: any wallet funding a deposit address we already attribute
is the same exchange account, so the registry reaches wallets no forward
walk could. What survives a mixer, an exchange or a bridge — and what
honestly does not — is worked through in
[docs/attribution-methods.md](docs/attribution-methods.md).

## Setup

### 1. Enable GitHub Pages
Repository **Settings → Pages → Source: GitHub Actions**. Without this the
pipeline runs but the deploy step fails.

### 2. Repository variables
**Settings → Secrets and variables → Actions → Variables:**

| Variable | Example |
|---|---|
| `HOOD_BASE_URL` | `https://hood.vantis.sh` |
| `BLOCKSCOUT_BASE` | `https://api.blockscout.com/4663/api/v2` (default; usually leave unset) |
| `TG_SOURCE` | `@channelname` |
| `SITE_URL` | `https://<user>.github.io/Hoodwall-Intelligence/` |

### 3. Secrets

`BLOCKSCOUT_API_KEY` is required — without it enrichment cannot run (see
*Chain API access* below). The Telegram secrets are optional; those sources
skip themselves if unset.

| Secret | For |
|---|---|
| `BLOCKSCOUT_API_KEY` | **Required** for on-chain enrichment — free key from https://blockscout.com |
| `TG_API_ID`, `TG_API_HASH` | Telegram API, from https://my.telegram.org |
| `TG_SESSION_STRING` | Telegram auth (see below) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Daily digest, from @BotFather |

### 4. Telegram session
Actions cannot do an interactive login, so mint a session string once
locally and store it as `TG_SESSION_STRING`:

```bash
pip install telethon
TG_API_ID=... TG_API_HASH=... python scripts/mint_telegram_session.py
```

That string grants full read access to your Telegram account — treat it as
a password.

### 5. Run it
**Actions → Pipeline → Run workflow.** After that it runs every 30 minutes
on its own.

## What changed from the local version

| Local (v1) | Cloud (v2) | Why |
|---|---|---|
| Ollama scores wallets | Deterministic formula | No GPU in CI — and a 7B model inventing a 0–100 number was never reproducible or explainable |
| Google Sheets | JSON committed to the repo | No service account, no API quota; every run is a reviewable diff |
| Append-only rows, deduped by readers | Keyed upserts | Removes an entire class of "keep the latest row" bugs |
| Streamlit dashboard | Static HTML on Pages | Nothing to keep running |
| Screen OCR ingestion | Blockscout block-walking | There is no screen in CI, and reading the chain directly is strictly better |
| Always-on Telethon listener | Cursor-based polling | Nothing stays running between scheduled jobs |
| cron / Task Scheduler on your PC | GitHub Actions schedules | The PC no longer has to be on |

## Chain API access

Robinhood Chain (chain id **4663**) is an Arbitrum Orbit L2 explored by
Blockscout. Its per-instance host, `robinhoodchain.blockscout.com/api/v2`,
returns **403 Forbidden** to programmatic requests — chain data is served
through Blockscout's multichain Pro API instead:

```
https://api.blockscout.com/4663/api/v2/...
Authorization: Bearer $BLOCKSCOUT_API_KEY
```

Get a free key at https://blockscout.com and store it as the
`BLOCKSCOUT_API_KEY` secret. `BLOCKSCOUT_BASE` already defaults to the Pro
API, so no variable change is needed.

If enrichment still returns nothing, run **Actions → Diagnose chain API →
Run workflow**. It probes every plausible base URL and auth combination in
one job and prints which works, rather than requiring a scheduled run per
guess.

The client treats 401/403 as permanent and aborts the stage immediately
instead of retrying per wallet, and enrichment stops after 10 consecutive
failures and emits a workflow warning — so a broken endpoint fails fast and
visibly rather than burning three minutes and reporting success.

## Caveats

- **Scheduled workflows are best-effort.** GitHub queues them under load, so
  `*/30` means "about every half hour."
- **Actions disables schedules after 60 days without repo activity.** The
  pipeline's own commits count, so this only matters if it is already broken.
- **The JSON store grows.** Events are capped per wallet
  (`MAX_EVENTS_PER_WALLET`), and each run commits, so history accumulates.
- **Public repo = public data.** Raw Telegram text is excluded from
  published artifacts unless `PUBLISH_RAW_TEXT=true`.

## Repo layout

```
run.py                      CLI entrypoint; workflows call this
pipeline/
  config.py                 env-var configuration
  store.py                  JSON store (keyed upserts, atomic writes)
  chain.py                  Blockscout client (retry/backoff, multi-chain)
  flow.py                   money-flow tracing and same-owner scoring
  masks.py                  resolves truncated addresses like 0x3475…3a12
  features.py                behavioural feature extraction (EVM)
  scoring.py                  the Smart Score formula and label rules (EVM)
  graph.py                    the relationship graph: transfer + behavioural edges, clustering
  sentiment.py                 five-source confidence-weighted sentiment
  market.py                    DefiLlama chain TVL + price stage
  enrich.py / enrich_hl.py / score.py / publish.py / digest.py
  adapters/hyperliquid.py     perps feature extraction and scoring
  adapters/defillama.py       free market data client
  kb/                          knowledge-base loader, schema, address validation
  sources/scraper.py          generic vendor-site scraper (addresses + masks)
  sources/hood.py | bizyugoscan.py | telegram.py | discovery.py
knowledge/                  your curated wallets, entities, narratives, sources
site/                        the dashboard (published to Pages)
  index.html / dashboard.css / dashboard.js / graph.js
data/                       the JSON store (committed by Actions)
knowledge/watchlist.yml     submitted addresses awaiting a chain
knowledge/infrastructure.yml exchanges, bridges, mixers, routers - not wallets
docs/                       signal-analysis, knowledge-base, flow-tracing,
                             attribution-methods, graph-and-sentiment,
                             realtime-alerts, tool-landscape
tests/                      172 tests across pipeline, kb, graph, sentiment, adapters
```

## Analysis

[`docs/signal-analysis.md`](docs/signal-analysis.md) documents why the
vendor's "SMART MONEY" tag is not used as a score input: across its own 74
tagged wallets, reported win rate and reported PnL correlate at **r =
−0.001**, and the median tracked call is **down 77%**.

[`docs/tool-landscape.md`](docs/tool-landscape.md) catalogs third-party
attention, copy-trading, execution, bridge, wallet and research tools, and
notes where each overlaps with or differs from what Hoodwall does — useful
when deciding whether a gap here is worth building or better covered by an
existing tool.

Not investment advice.
