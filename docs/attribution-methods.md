# Attribution beyond a broken trail

`docs/flow-tracing.md` describes what works while value moves wallet to
wallet. This document is about what happens when it stops: the trail enters
a centralized exchange, or a mixer, or crosses a bridge. Those three break
the trail in *different* ways, and treating them as one problem — "the
trace failed" — throws away the case where the trail did not actually end.

## The three boundaries are not equivalent

| Boundary | What it does to the trail | Recoverable? |
|---|---|---|
| **CEX** | Concentrates identity. The deposit address is generated per user. | Often — the deposit address *is* the join key |
| **Bridge** | Moves value to another chain, usually 1:1 through a relayer. | Frequently — matched amount and time across two chains |
| **Mixer** | Destroys the link by design, in proportion to its anonymity set. | Rarely — only through operator error |

A CEX is the opposite of a dead end. It is the single strongest
common-ownership signal available, because an exchange assigns each user
their own deposit address and users reuse it for years.

---

## 1. Deposit-address registry (the highest-value mechanism)

`flow.py` already detects deposit-shaped addresses: funded by a seed,
forwards everything to one address it never receives from, never returns to
its funder. Today that detection lives in a local dict and is discarded when
the run ends, which means every trace starts from zero.

Persisting it inverts the trace direction:

> Once `0xDEP` is known to be Cupseyy's deposit address, **any** wallet that
> sends to `0xDEP` is the same exchange account — and almost certainly the
> same person.

That turns a boundary into a discovery engine. It is cheap (the data is
already produced), it accumulates (every run makes the next one better),
and it needs no new data source.

**Confidence.** Same exchange account is strong but not absolute: a shared
custodial account, or an address a user deposits on someone else's behalf,
both produce the same shape. It earns `probable`, not `confirmed`.

## 2. Deposit ↔ withdrawal correlation

The other side of a CEX: value leaves through a hot wallet with thousands
of counterparties, so there is no edge to walk. What remains is
correlation — a deposit of 3.847 ETH at 14:02 and a withdrawal of 3.845
from the hot wallet at 14:09 to an unfamiliar address.

This is only evidence when the pair is close to **unique** in its window.
An amount with unusual precision in a quiet window is a real signal; 1.0 ETH
at a busy hour is noise. So the rule is: score by amount distance and time
delta, and *discard the match entirely* if more than one candidate withdrawal
fits. Never let it reach `probable` on its own — it is a corroborating
signal, not a proof.

**Cost.** See the architecture section: doing this retrospectively over a
hot wallet's history is a volume problem, and the pipeline should watch
forward instead.

## 3. Behavioural fingerprinting

When the money trail is genuinely gone, stop tracing money. These survive
both a mixer and an exchange, because they are properties of the person and
their tooling rather than of the funds:

| Signal | Why it identifies |
|---|---|
| **Activity-hour histogram** | Timezone. People sleep, and they trade in the same waking hours. |
| **Timed token co-occurrence** | Buying the same illiquid token within minutes. For a memecoin trader this approaches transfer-grade evidence. |
| **Gas-price habit** | A manually set or unusually precise gas price, repeated. |
| **Router and venue preference** | Which DEX, which aggregator, which approval pattern — tool habits are sticky. |
| **Counterparty circle** | The small set of non-service EOAs both wallets deal with. People's friends do not change. |
| **Same address on several chains** | Already handled by `flow.probe()`; extremely common and often forgotten. |

`graph.py` already draws a `token` behavioural edge, but from the
hand-authored `tokens:` list on a KB record — not from timed on-chain
co-occurrence. The distinction matters: "both hold AZUKI" is weak, "both
bought AZUKI within four minutes of each other" is not.

## 4. Mixers, and saying so

A fixed-denomination mixer offers an information-theoretic guarantee: if a
thousand people deposit 10 ETH and a thousand withdraw 10 ETH, no analysis
links them. What is recoverable is operator error:

- depositing and withdrawing with the same address;
- a small anonymity set — an unusual denomination, or a quiet hour, leaving
  the deposit and withdrawal nearly paired;
- a distinctive gas price repeated on both sides;
- a burst that matches in multiplicity (5 × 10 ETH in, 5 × 10 ETH out);
- the withdrawal wallet later touching a rare contract or illiquid token
  the deposit wallet also touched.

When none of those apply, the correct output is **"the trail ends here,"**
not a low-confidence guess. A tracer that fabricates a link across a mixer
is worse than one that stops, because the fabrication is unfalsifiable.

Two practical notes. First, mixer interaction is itself intelligence and
deserves a label, independent of any linking. Second, for the traders this
repo actually tracks — memecoin and perps accounts — mixers are rare and
exchanges are constant, so effort belongs on the CEX side.

---

## Architecture: what fits, and what does not

The repo's premise is *no server, no database, no cost*: JSON tables in
`data/`, committed by Actions, with `knowledge/` hand-authored and never
overwritten by automation. That premise holds for three of the four
mechanisms and breaks for one.

### Fits as-is

**The deposit registry** is a new keyed table and nothing more. It is small,
grows slowly, and keyed upserts are exactly the right shape. No change.

**Mixer and bridge labelling** needs a curated address list, which is
knowledge, not observation — so it belongs in `knowledge/`, not `data/`.

### Needs a change

**A curated infrastructure registry is missing, and everything wants it.**
`flow.py` classifies counterparties purely by heuristic: contract flag and
peer degree. That works, but it is guesswork about addresses whose identity
is often publicly known. A `knowledge/infrastructure.yml` — CEX hot wallets,
deposit sweeps, bridges, mixers, routers, each with a `role` — makes
classification authoritative where the answer is known and leaves the
heuristic for where it is not. It also gives discovered deposit addresses
somewhere to graduate to once a human confirms them.

This is the highest-leverage structural change, because all four mechanisms
improve with it and none of them is at its best without it.

**Event pruning destroys exactly what fingerprinting needs.**
`store.prune_events()` keeps the most recent 100 events per wallet, because
raw events are the fastest-growing table and unbounded growth would make
every commit enormous. That reasoning is sound for raw events and fatal for
behavioural analysis: an activity-hour histogram or a gas-price signature
wants *all* history, and the pipeline currently throws it away every run.

The fix is not to stop pruning. It is to **derive before discarding**: a
`fingerprints` table holding, per wallet, a 24-bucket hour histogram,
gas-price quantiles, router counts and token first-seen timestamps, updated
incrementally on each enrich. Aggregates are a few hundred bytes per wallet,
never need pruning, and *accumulate* — the system gets better at recognizing
a person the longer it runs, instead of forgetting at a fixed horizon. Raw
events can then be pruned harder, not less.

This is the deeper point: the pipeline currently discards information it
could be compounding.

**Retrospective correlation does not fit; forward watching does.**
Matching a deposit to a withdrawal means searching a hot wallet's history
around a timestamp. Blockscout's v2 address endpoint is newest-first cursor
pagination with no time filter; the legacy `txlist` endpoint does accept a
block range, so this is a volume problem rather than an impossible one — but
a CEX hot wallet emits thousands of transactions in any window worth
searching, and a scheduled free-tier job cannot page through that.

Inverting it fits the cron architecture exactly. When a seed deposits to a
CEX or a bridge, record the open question in a `pending_exits` table —
venue, amount, timestamp. Each scheduled run then examines only *new*
activity for a match and closes the entry. Retrospective search needs an
indexed source (Dune, Allium, a warehouse); forward watching needs only the
next page of recent history, which the pipeline is already fetching.

The cost is honest and worth stating: forward watching cannot answer
questions about the past. A deposit made before the watcher existed is not
recoverable this way.

### What should not change

Repo-as-database still holds. Every table proposed here is small and slow-
growing; none of them argues for a real database. The rule that automation
writes to `data/` and only a human promotes into `knowledge/` should hold
especially firmly here, because these mechanisms produce *inferences about
people* — the one category where an automated writer must never be trusted
with the authoritative record.

## The Hyperliquid correction

Flow tracing does not work on a Hyperliquid account: positions are internal
to the venue, so there is no transfer graph to walk. That is true, and it
led to the wrong conclusion — that the funding question was unanswerable
for those wallets.

It is not. A Hyperliquid account is funded by bridging USDC in from
**Arbitrum**, and the depositor there is the same address. So the venue has
no graph, but its on-ramp is an ordinary Blockscout chain that this project
already reads. `flow.on_ramp_chain()` encodes the redirect, and
`scripts/deposit_sources.py` applies it automatically: ask for the funding
of a `hyperliquid` wallet and Arbitrum is what gets inspected.

The venue's own ledger (`adapters.hyperliquid.ledger()`) is read as
corroboration — it knows when money entered and how much, but not who sent
it. Arbitrum knows who. Neither answer is complete alone.

The general lesson is worth keeping: **when a venue has no transfer graph,
look at its on-ramp.** The same shape applies to any chain or venue whose
balances arrive over a bridge.

## Build order

1. `knowledge/infrastructure.yml` + loader support — the foundation the rest leans on. **Done.**
2. Deposit registry and reverse lookup — largest gain, data already produced. **Done.**
2b. Funding sources per wallet, with the Hyperliquid on-ramp redirect — `scripts/deposit_sources.py`. **Done.**
3. `fingerprints` table, derived on enrich — unblocks behavioural matching.
4. `pending_exits` + forward watching — CEX withdrawal and bridge pairing.
5. Mixer boundary reporting and labelling — small, and honest about limits.
