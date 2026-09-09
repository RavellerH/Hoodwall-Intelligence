# Money-flow tracing

One address is a starting point, not an answer. The person behind it keeps
a funding wallet, a trading wallet, and usually one they would rather not
have connected to the first two. Those wallets are linked by the only thing
on a public chain that cannot be retracted: the value that moved between
them.

`pipeline/flow.py` walks that flow. `scripts/trace_flow.py` runs it.

```bash
python scripts/trace_flow.py                          # every watchlist seed
python scripts/trace_flow.py --seed 0xF64d... --depth 1
python scripts/trace_flow.py --chain base --chain arbitrum
python scripts/trace_flow.py --write-kb               # promote what it finds
```

Or run the **Flow trace** workflow from the Actions tab, which does the same
thing with the repo's Blockscout key and commits the report.

## The seed list

A wallet record in `knowledge/wallets/` must name a chain. A bare `0x`
address does not have one — the same 40 hex characters exist on every EVM
chain, and a handle that posts an address almost never says where it lives.

So submitted addresses land in **`knowledge/watchlist.yml`** first. Tracing
probes every Blockscout-readable EVM chain for each seed, and only once it
knows where the address is actually active does `--write-kb` promote it to
`knowledge/wallets/<chain>.yml`. Guessing the chain in order to file the
record would put an unverified fact in the knowledge base to satisfy a
schema, which is exactly backwards.

## Why counterparties are not clusters

Expanding one hop from a wallet and calling the result "their cluster"
produces a cluster of Uniswap. Almost every counterparty is a contract, an
exchange, a bridge, or simply another person. So each candidate is first
**classified**, and only then scored:

| Class | What it is | Fate |
|---|---|---|
| `contract` | Blockscout reports code at the address | dropped — not a wallet |
| `service` | more than `SERVICE_DEGREE` (60) distinct peers | dropped — router, hot wallet, MM |
| `deposit` | funded by a seed, forwards everything to one address it never receives from, never returns to its funder | dropped from the cluster, **kept as a signal** |
| `wallet` | everything else | scored |

The deposit case is the interesting one. A CEX deposit address is
*generated for one user*, so on funding exclusivity alone it looks exactly
like a sibling wallet — it is the single most common way an automated
cluster goes wrong. It is not the user's wallet. But two seeds depositing
to the **same** address means one exchange account, which is stronger
evidence of common ownership than any transfer pattern, so it is reported
separately as a `shared_deposit` finding.

## The signals

| Signal | Weight | Why it means "same owner" |
|---|---|---|
| `sole_funder` | 40 | The candidate's entire inbound history is this seed — a wallet that has never been funded by anyone else |
| `bidirectional` | 25 | Value moved both ways; payments rarely round-trip |
| `exclusive` | 20 | The candidate deals with no address outside this trace |
| `sweep` | 15 | A single transfer that emptied the sender — a migration, not a payment |
| `shared_funder` | 15 | Funded only by other seeds in the trace — a sibling |
| `same_window` | 5 | First and last contact within 24h — a one-off move, not a relationship |

**≥50 is `probable`**, 30–49 is `possible`, below that is not reported. The
weights are set so no single soft signal reaches `probable` alone: it takes
sole funding, or two independent mid-weight signals.

Every link carries its evidence strings, so a cluster can be argued with
rather than believed:

```
[ 85] probable base:0x1f2b…  <- 0xf64d…be30
      - value moved both ways (3 out / 2 in)
      - every inbound transfer came from 0xf64d…be30
```

## What the tracer refuses to claim

`sole_funder` and `exclusive` are assertions about a *complete* history.
Blockscout paginates, and `MAX_FLOW_PAGES` caps how much is read, so the
client tracks whether the last page carried a next-page cursor. If history
was truncated, those two signals are simply not awarded — a truncated read
can prove that a wallet *has* another funder, never that it has none. This
is why a fresh wallet with three transactions clusters confidently and a
five-year-old wallet does not.

Records written by `--write-kb` carry `confidence: inferred`, the schema's
own word for "our clustering heuristic said so". They are never written as
`confirmed`, and existing hand-authored records are never overwritten.

## Budgets

Fan-out is multiplicative, so tracing has hard ceilings of its own:

| Variable | Default | Caps |
|---|---|---|
| `MAX_FLOW_DEPTH` | 2 | hops from a seed |
| `MAX_FLOW_ADDRESSES` | 120 | addresses examined per run, whatever the depth |
| `MAX_FLOW_PAGES` | 3 | history pages per address (one API call each) |

Depth 2 expands only from wallets already judged `probable`. Expanding from
every counterparty instead makes the second hop the entire chain.

## How this differs from the relationship graph

`pipeline/graph.py` and this module answer different questions and are not
alternatives to each other.

The graph draws relationships **between wallets already in the store** —
transfer edges from enriched events, behavioural edges from shared
entities, narratives, tokens and funding — and clusters them for the UI.
Its universe is what the pipeline already knows.

Flow tracing goes the other way: it starts from one address and calls the
chain to find wallets **nobody has recorded yet**, then decides which of
them are the same owner. Its output is new addresses; the graph's output is
structure over known ones. A traced link written with `--write-kb` becomes
a wallet the graph can then draw.

## Limits worth knowing

- **Bridges break the trail.** Value that leaves through a bridge reappears
  on another chain with a different sender. Probing runs per chain; it does
  not stitch a bridge crossing back together.
- **Privacy tooling breaks it by design.** A mixer between two wallets
  leaves no edge to walk.
- **Balance history is not available** through this API, so `sweep` is
  approximated from transfer shape and weighted as a soft signal.
- **A link is a claim about wallets, not about people.** An address
  attributed to a handle by a third party stays `reported` until the owner
  signs a message or the chain proves it.
