# The relationship graph and sentiment engine

## Why two kinds of edge

The graph answers "which wallets are connected?" — but there are two very
different reasons to believe two wallets are connected, and blending them
would let inference masquerade as proof. So they never merge:

| Edge | Meaning | Drawn as | Cost |
|---|---|---|---|
| **transfer** | Wallet A actually sent value to wallet B on-chain | Solid, arrowed, thickness by value | Needs enrichment; limited by API budget |
| **behavioural** | A and B share an attribute we know about | Faint, dashed | Free, total coverage |

The MAP legend always states what share of edges are evidence. With no
enrichment run yet that figure is **0%** — the graph is entirely inference,
and says so rather than looking authoritative.

Masked wallets (`0x3475…3a12`) get a dashed ring. They can never gain a
transfer edge until they are resolved to a full address.

## Behavioural edge weights

```
entity     1.00   you asserted these wallets are the same person
funding    0.80   same first funding source
token      0.50   bought the same token
narrative  0.30   linked to the same thesis
source     0.15   came from the same feed
```

Each shared attribute is damped by how many wallets share it:

```
damping = 1 / (bucket_size - 1) ** 0.25
```

The exponent is deliberately mild. A token bought by ten wallets inside one
window is a strong coordination signal and must survive damping; a feed
shared by seventy still falls below the cut because its *base weight* is
low. Damping should not have to do the job that the weights already do.

Edges below a combined weight of `0.25` are dropped, and each node keeps at
most its 12 strongest, so the layout stays readable.

## Clustering

Label propagation over the combined graph, with transfer edges pulling 1.5×
harder than behavioural ones. Deterministic: nodes are visited in sorted
order and ties break on the smallest label, so identical input always yields
identical clusters.

A cluster is named after an entity only when that entity accounts for at
least 30% (and 2+) of its members. Naming twenty-two wallets after the one
member that happens to be attributed would be misleading, so those get
`cluster-N`.

## Sentiment

Five sources, each returning a score (−1 bearish … +1 bullish), a
confidence, and evidence citing real numbers.

| Source | Weight | What it reads |
|---|---|---|
| `flow` | 1.00 | Net accumulation vs distribution across tracked wallets |
| `positioning` | 0.80 | Equity-weighted directional bias of perps books |
| `price` | 0.60 | Volume-weighted 24h momentum |
| `feed` | 0.30 | Lexicon reading of ingested alert text |
| `social` | 0.30 | External chatter |

The composite is **confidence-weighted**, so a source with no data
contributes nothing rather than dragging the reading toward neutral. Three
silent sources cannot dilute one strong signal — there is a test for this.

Two deliberate design choices:

- **Flow counts wallets, not just value.** Fifty wallets quietly
  accumulating is a different signal from one whale doing the same size, and
  value alone would hide that.
- **An unreliable source loses confidence, never its sign.** The tracked
  feed's median call was 0.23x, so it is damped — but not inverted.
  Claiming to know the sign of a bad signal is its own overreach.

### Current coverage

| Source | Status |
|---|---|
| flow | Works once EVM enrichment has run |
| positioning | **Works now** — Hyperliquid needs no API key |
| feed | Works once Telegram ingestion is configured |
| price | Needs a price adapter (DexScreener is free) |
| social | **Needs a paid plan** — X's API is not free at useful volume |

`social_signal()` returns zero confidence and says so, rather than implying
coverage that does not exist. Wiring a real adapter means returning a list
of `{text, weight}` from wherever you get posts; nothing else changes.

## The LLM slot

Everything above is deterministic. `sentiment.narrate()` is the single seam
where an LLM narrator would go: it receives exactly the computed signals and
returns prose. Until then the summary is assembled from the signals
themselves, which means it can never overstate what the data says.
