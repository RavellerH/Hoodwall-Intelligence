# The knowledge base

`knowledge/` is the human-authored half of the system. You assert what a
wallet is and why it matters; the pipeline attaches what it can observe
on-chain. **The pipeline never writes to `knowledge/`** — an automated run
can never overwrite your analysis.

```
knowledge/
  chains.yml          chain registry (8 chains across evm / svm / utxo)
  wallets/<chain>.yml tracked wallets
  entities/<key>.yml  people and organizations that control wallets
  narratives/<key>.md thesis + evidence + measured outcomes
  sources/<key>.yml   feeds, and their measured reliability
  inbox/              scratch space for raw pasted feed text
```

## Three ways to add records

**1. Import a spreadsheet**
```bash
python scripts/kb_import.py analysis.xlsx          # dry run
python scripts/kb_import.py analysis.xlsx --write
```
Recognized sheets: `Smart Money Wallets`, `Memecoin Smart Money`,
`Hyperliquid Clusters`, `Hyperliquid Top Traders`, `Named Individuals`,
`Notable Named Wallets`, `BTC Institutional`, `BTC Whale Entities`.

**2. Paste raw feed text**
```bash
cp alert.txt knowledge/inbox/
python scripts/kb_intake.py knowledge/inbox/alert.txt
```
Extracts full and masked addresses, handles and tokens into a **draft** for
review. Drafts never land in `wallets/` automatically.

**3. Edit YAML directly** in the GitHub web UI. CI validates every change.

Always finish with:
```bash
python scripts/kb_validate.py
```

## Record shapes

### Wallet
```yaml
- chain: robinhood            # must exist in chains.yml
  address: "0x6acc…ac31"      # OR masked: "0x3475…3a12"
  entity: bizyugo             # links into entities/
  handle: "@bizyugo"
  labels: [smart_money, whale]
  confidence: confirmed       # confirmed | reported | inferred
  conviction: medium          # your own conviction: none|low|medium|high
  narratives: [robinhood-launchpad-wave]
  source: intel-hood-vantis
  notes: Free text.
```

Either `address` or `masked` is required. A masked sighting is worth
recording before it can be resolved — dropping it loses the sighting.

### Entity — the wallet cluster
An entity groups wallets under one person or organization. **Cluster
membership is derived, not stored twice**: a wallet names its entity, and
the entity's cluster is assembled from that. Recording the relationship in
both places would guarantee they drift apart.

```yaml
key: bizyugo
name: bizyugo
type: individual        # individual|fund|exchange|etf|treasury|bot|unknown
confidence: confirmed
handles: {twitter: "@bizyugo"}
```

Entities can hold assets **without any address** — a Bitcoin ETF's coins
span many addresses that are not individually interesting:

```yaml
key: blackrock
name: BlackRock
type: etf
holdings:
  - {chain: bitcoin, asset: BTC, amount: 761801, as_reported: "761,801 BTC"}
```

### Narrative
Frontmatter plus prose. Outcomes are what make a narrative auditable
rather than a story.

```yaml
---
key: robinhood-launchpad-wave
title: Robinhood Chain launchpad wave
status: cooling          # emerging|active|cooling|dead|invalidated
conviction: low
chains: [robinhood]
tokens: [CHAD, HOODLAND]
opened: 2026-09-08
outcomes:
  - {date: 2026-09-08, metric: median_multiple, value: 0.23}
updates:
  - {date: 2026-09-08, note: First window captured.}
---
## Thesis
...prose...
```

### Source
Records a feed **and its measured track record**, so the KB can weight a
source rather than trust it. See `sources/intel-hood-vantis.yml`: that feed
reports a median call outcome of 0.23x and a win-rate/PnL correlation of
−0.001, which is why its tags are treated as claims to verify, never as
score inputs.

## Validation

Errors name the file, the record and the likely fix, because these files
are edited by hand and the message is the only feedback you get:

```
wallets/solana.yml #3: chain='solna' is not in chains.yml (did you mean 'solana'?)
wallets/robinhood.yml #7: needs either 'address' (full) or 'masked'
narratives/wave.md: outcomes[0]: missing 'value'
```

Dangling references (a wallet pointing at a deleted narrative) are errors
too — that silently removes the wallet from the narrative's page, which
looks like data loss rather than a typo.
