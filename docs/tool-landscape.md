# External tool landscape

Reference list of third-party tools for multi-chain attention, wallet
tracking, copy trading, execution, bridging, and research — kept here so
the list survives outside chat. Not all of these need to be used; pick per
need (finding attention, tracking a wallet, moving chain, or trading
directly).

For each tool: what it does, and — where relevant — how it relates to what
Hoodwall already does or doesn't do, so gaps are visible at a glance.

> Not investment advice. Third-party tools, unaudited by us — verify
> before connecting a wallet or API key to any of them.

## Attention

| Tool | What it does | Relation to Hoodwall |
|---|---|---|
| **FOMO** | Multichain, easy UX; flow across several chains from one place | Same category as Hoodwall's MAP flow view, but FOMO is live multi-chain out of the box — Hoodwall's live enrichment is currently Robinhood Chain (Blockscout) + Hyperliquid only |
| **[@KaitoAI](https://twitter.com/KaitoAI)** | Narrative and mindshare tracking on Crypto Twitter | Hoodwall's `SENT` (sentiment) view reads on-chain flow + feed text, not social mindshare — Kaito covers the CT-attention angle Hoodwall's `social` signal deliberately leaves at zero confidence (X's API isn't free at volume) |
| **wind.jokkimon.club** | Aggregates X, Telegram, FOMO, pump.fun feeds; realtime tab for tokens starting to see buy pressure | Overlaps with Hoodwall's Telegram ingestion + hood.vantis.sh scraper, but wind pulls more sources (X, pump.fun) that Hoodwall doesn't touch |

## Copy / smart money

| Tool | What it does | Relation to Hoodwall |
|---|---|---|
| **CopyFOMO** | Auto-mirrors a FOMO-tracked trader once their tx lands on-chain | Execution layer Hoodwall doesn't have — Hoodwall tracks and alerts, never trades |
| **[@CieloFinance](https://twitter.com/CieloFinance)** | Wallet tracker + trading agents; multi-buy agent auto-buys when several tracked wallets enter the same token | Closest analog to Hoodwall's entity clustering (`ENT` view) + behavioural graph edges (`token:` co-occurrence in `MAP`) — Cielo acts on the signal, Hoodwall only surfaces it |
| **Mirrorly** | Self-custody copy trading; leaders sourced from Hyperliquid + CEX smart money | Hoodwall's Hyperliquid adapter (`pipeline/adapters/hyperliquid.py`) reads the same leaderboard-style data but scores it, doesn't copy it |
| **[@mobyagent](https://twitter.com/mobyagent)** | Smart-money **cohort** tracking, not single-wallet | Same idea as Hoodwall's entity clusters — a cohort is what a cluster becomes once behavioural edges connect wallets sharing tokens/entity/source |

## Trading

| Tool | What it does |
|---|---|
| **[@gmgnai](https://twitter.com/gmgnai)** | Discovery + smart money + trading + Telegram in one app; wide chain coverage |
| **[@BasedBot](https://twitter.com/BasedBot)** | Usually fast to support new chains |
| **[@covetrade](https://twitter.com/covetrade)** | Trade several chains from one USDC balance |

## Bridge

| Tool | What it does |
|---|---|
| **[@RelayProtocol](https://twitter.com/RelayProtocol)** | Bridging; fast, commonly used |

## Wallet

| Tool | What it does |
|---|---|
| **[@wallet](https://twitter.com/wallet)** | Simple UX for multichain, broad chain support |

## Research

| Tool | What it does | Relation to Hoodwall |
|---|---|---|
| **DefiLlama** | TVL, fees, yields, bridge flow | Not something Hoodwall tracks — could feed the `price`/flow sentiment signal later (see `pipeline/sentiment.py: price_signal`, currently unconfigured) |
| **Artemis** | Per-chain activity, useful when volume is rotating between chains | Same "where is volume going" question Hoodwall's chain-coverage view (`CHN`) answers narrowly (which chains have live wallet data), not broadly (aggregate chain activity) |
| **Bubblemaps** | Holder cluster visualization before entering a hyped token | This is the direct model for Hoodwall's `MAP` view — see `docs/graph-and-sentiment.md`. Difference: Bubblemaps clusters by co-spend/holder overlap; Hoodwall clusters by known entity + token co-occurrence + actual transfers, and keeps evidence (transfer edges) visually distinct from inference (behavioural edges) |
| **[@getmoni_io](https://twitter.com/getmoni_io)** | Early project discovery + smart-follower / social graph | Social-graph angle Hoodwall doesn't have — Hoodwall's entity graph is wallet-based, not follower-based |
| **mintgo.fun** | NFT mint dashboard (EVM): live/upcoming mints, floor/volume, contract, multi-wallet eligibility | No NFT coverage in Hoodwall at all — out of scope so far (memecoin + large-cap wallet/narrative tracking only) |

## Reading this list against Hoodwall

Roughly, where Hoodwall sits versus buying one of these:

- **Attention / social mindshare** (Kaito, wind, Moni) — Hoodwall does not compete here. Its `social` sentiment source is a documented zero-confidence stub because X's API isn't free at volume (see `docs/graph-and-sentiment.md`).
- **Copy trading / execution** (CopyFOMO, Cielo, Mirrorly, covetrade) — out of scope by design. Hoodwall tracks, scores, clusters, and alerts; it never places a trade.
- **Wallet clustering / flow** (Bubblemaps, Cielo, Moby) — this is Hoodwall's actual center of gravity (`MAP`, `ENT` views), and the one area with a genuine differentiator: transfer edges (evidence) are never blended with behavioural edges (inference) — most of these tools present a single "related wallets" score without that distinction.
- **Multi-chain discovery** (FOMO, GMGN, BasedBot) — Hoodwall's live enrichment is narrow (Robinhood Chain + Hyperliquid); these tools are broader out of the box. Widening Hoodwall's chain coverage would mean building the adapters documented as `enrichment: none` in `knowledge/chains.yml`.
- **Research / macro flow** (DefiLlama, Artemis) — potential free inputs to `pipeline/sentiment.py`'s `price_signal`, which is currently unconfigured pending a price adapter.
