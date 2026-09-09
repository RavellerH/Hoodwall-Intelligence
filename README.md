# Hoodwall Intelligence

Multi-chain wallet and narrative intelligence, served as a Bloomberg-style
terminal on GitHub Pages.

Two halves that meet in the terminal:

- **The knowledge base** (`knowledge/`) — your curated wallets, entity
  clusters, narratives and sources across Robinhood Chain, Ethereum L2s,
  Hyperliquid, Solana and Bitcoin. Human-authored, schema-validated, never
  overwritten by automation.
- **The pipeline** — discovers and enriches wallets on-chain, scores them
  with a published deterministic formula, and merges those observations
  onto your curated records.

Plus a **realtime alert relay** on Cloudflare Workers, because Actions cron
cannot go below 5 minutes. See [docs/realtime-alerts.md](docs/realtime-alerts.md).

**No server, no database, no LLM, no cost.** GitHub Actions is the cron
engine, the repo is the database, GitHub Pages is the front end.

## The terminal

Keyboard-first, amber-on-black, information-dense.

| Key | View |
|---|---|
| `1` | DASH — coverage overview |
| `2` | WAL — every tracked wallet, all chains |
| `3` | ENT — entity clusters (wallets grouped by person/org) |
| `4` | NAR — narratives with measured outcomes |
| `5` | **MAP — wallet relationship graph** (Bubblemaps-style, with flow) |
| `6` | **PNL — positions, equity, realized/unrealized P&L** |
| `7` | **SENT — sentiment across five sources** |
| `8` | CHN — chain coverage and which have live data |
| `9` | SRC — sources and their measured reliability |
| `/` | command line (`HELP`, `CHAIN sol`, `FIND cupsey`) |
| `↑↓` / `j k` | move · `ENTER` open detail · `ESC` clear |

### The graph (MAP)

Solid arrowed edges are **transfers** — value actually moved on-chain.
Faint dashed edges are **behavioural** — the wallets share an entity, token,
narrative or source. They are never blended, and the legend always states
what share of edges are evidence rather than inference. Nodes are sized by
value, coloured by cluster, and a dashed ring marks a masked address that
cannot gain transfer edges until resolved.

### Sentiment (SENT)

Five sources, each reporting a score, a confidence and its evidence.
Confidence-weighted, so a source with no data contributes nothing instead of
dragging the reading toward neutral. Fully deterministic today, with one
clean seam where an LLM narrator can be dropped in later.

See [docs/graph-and-sentiment.md](docs/graph-and-sentiment.md).

Purple `◌` marks an address known only in masked form.

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
  chain.py                  Blockscout client (retry/backoff)
  masks.py                  resolves truncated addresses like 0x3475…3a12
  features.py               behavioural feature extraction
  scoring.py                the Smart Score formula and label rules
  enrich.py / score.py / publish.py / digest.py
  sources/hood.py | telegram.py | discovery.py
site/                       the dashboard (published to Pages)
data/                       the JSON store (committed by Actions)
docs/signal-analysis.md     analysis of the vendor feed this ingests
tests/                      scorer and mask-resolution tests
```

## Analysis

[`docs/signal-analysis.md`](docs/signal-analysis.md) documents why the
vendor's "SMART MONEY" tag is not used as a score input: across its own 74
tagged wallets, reported win rate and reported PnL correlate at **r =
−0.001**, and the median tracked call is **down 77%**.

Not investment advice.
