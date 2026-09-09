# Signal analysis: the "Intel Hood by Vantis" feed

Analysis of the vendor alert data (58 token cards, 74 tagged wallets,
229 tracked calls; window ~2026-09-08 13:00–23:51 UTC) that this system is
designed to ingest. It exists to answer one question: **can the vendor's
"SMART MONEY" tag be used as a signal as-is?**

The answer is no, and that finding shapes the whole design.

## 1. The addresses are masked

All 74 wallets in the feed are published truncated — `0x3475…3a12` — which
reveals 8 of 40 hex digits.

This is the single most important engineering consequence in the dataset.
A conventional `0x[a-fA-F0-9]{40}` extractor finds **zero** addresses in
this feed. The obvious implementation of a Telegram ingestion source
silently collects nothing and looks like it is working.

The system handles this in `pipeline/masks.py`: masks are stored
unresolved, then matched against the address universe that on-chain
discovery builds. 8 hex digits is 32 bits of entropy, so against a
million known addresses a false match is expected roughly 0.02% of the
time. A mask matching exactly one known address is promoted to a
candidate; a mask matching two or more is recorded as `ambiguous` and
never guessed.

| Source | Wallets | Address form | Usable on-chain |
|---|---|---|---|
| Memecoin feed (`Smart Money Wallets`) | 74 | masked, 8/40 hex | only after resolution |
| Hyperliquid leaderboard | 20 | full 40 hex | yes — but wrong chain |
| Named individuals | 2 | identity, not address | no |
| BTC entities | 7 | entity names | no |

Of 103 catalogued entities, **20 are directly usable addresses, and all 20
are Hyperliquid/EVM-mainnet traders, not Robinhood Chain wallets.** They
are a useful reference set, not an input to this pipeline.

## 2. The vendor's own metrics do not agree with each other

Across the 74 tagged wallets:

| Relationship | Pearson r |
|---|---|
| Reported win rate vs reported realized PnL | **−0.001** |
| Feed appearances vs win rate | −0.066 |
| Feed appearances vs PnL | +0.175 |
| Buy size vs win rate | −0.097 |

A correlation of −0.001 between the two headline numbers means they carry
essentially no information about each other. Knowing a wallet's win rate
tells you nothing about whether it makes money, in the vendor's own data.

## 3. Recurring wallets are not better wallets

The intuitive read — a wallet the feed keeps flagging must be good — does
not survive contact with the numbers:

| Group | n | Median win rate | Median realized PnL |
|---|---|---|---|
| Recurring (2+ appearances) | 10 | 40.6% | $27,921 |
| One-off (1 appearance) | 64 | **44.5%** | $18,091 |

Recurring wallets have a *lower* median win rate. Appearing repeatedly
means the vendor keeps tagging them, not that they are right more often.

## 4. The roster is a selection artifact

71 of 74 wallets (96%) are reported profitable, and the top 5 wallets
account for 42% of all positive PnL. A population where almost everyone
wins is not a population — it is a filtered list. The vendor tags wallets
it has *already* scored as winners, so the label cannot be evidence that
the wallet is good; it is a restatement of the vendor's prior.

Win rates are also weaker than the label implies: median 44.3%, with 68%
of wallets below 50% and 41% below 40%.

## 5. The calls themselves lose money

From the feed's own 24-hour scoreboard (229 calls, quotes at 2026-09-08
17:04 UTC):

| Metric | Value |
|---|---|
| Median multiple across 228 usable quotes | **0.23x** |
| Ever reached ≥2x at any point | 64 / 229 (28%) |
| Still ≥2x now | 15 / 229 (6.6%) |
| Down more than 20% from call | 167 / 229 (73%) |
| At half the call price or worse | 137 / 229 (60%) |

The typical called token is **down ~77%**. The "10x from our call" cards
in the feed are survivorship-biased highlights drawn from that pool.

Median market cap at signal is $86,200 (range $23,700–$1,830,000), i.e.
these are micro-cap launches where a single exit moves the price.

## 6. What this means for the build

1. **Ingest the feed as claims, not as facts.** A vendor tag is an
   observation to be verified on-chain, never a score input. Nothing in
   `pipeline/scoring.py` reads a vendor label.
2. **Score independently.** The Smart Score is computed only from
   observed on-chain behaviour, which is why it is a published formula
   rather than a model output.
3. **The masked-address problem is the real integration work**, not the
   scoring. Without `masks.py` the richest source contributes nothing.
4. **Measure forward outcomes.** The most valuable thing the vendor data
   proves is that a feed can look authoritative while its median call
   loses 77%. Any scoring system — including this one — should be judged
   the same way, on tracked forward performance, not on how confident its
   labels sound.

> These figures are the vendor's own self-reported numbers, not
> independently audited. Nothing here is investment advice.
