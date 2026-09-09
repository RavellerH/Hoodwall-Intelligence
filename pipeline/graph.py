"""Wallet relationship graph: nodes, edges, clusters and flow.

This is the Bubblemaps-style layer. Two kinds of edge, deliberately kept
distinguishable rather than blended into one "relatedness" number:

  transfer    - wallet A actually sent value to wallet B on-chain. Evidence.
                Requires enriched events, so coverage is limited by the API
                budget and masked wallets can never participate.
  behavioural - A and B share something we know: the same entity, the same
                narrative, the same token, the same funding source.
                Free, total coverage, but correlation - not proven flow.

Mixing them into a single score would let inference masquerade as evidence,
so every edge carries its `kind` all the way to the UI.

Clustering uses label propagation: deterministic given sorted iteration,
no dependencies, and it degrades sensibly when the graph is sparse (which
it will be until on-chain enrichment has run).
"""
from collections import defaultdict

from . import config

# Relative pull of each behavioural signal. An entity link is near-certain
# (you asserted it); sharing a narrative is weak evidence on its own.
BEHAVIOURAL_WEIGHTS = {
    "entity": 1.0,
    "funding": 0.8,
    "token": 0.5,
    "narrative": 0.3,
    "source": 0.15,
}

# Below this combined weight a behavioural edge is noise and is dropped -
# otherwise every wallet from one feed links to every other.
MIN_BEHAVIOURAL_WEIGHT = 0.25

# How hard a shared attribute is damped as more wallets share it. The
# exponent is deliberately mild (0.25, not 0.5): a token bought by ten
# wallets in one window is a strong coordination signal and must survive,
# while a feed shared by seventy still falls below the threshold because its
# base weight is low. Damping alone should not have to separate a broad
# attribute from a selective one - that is what BEHAVIOURAL_WEIGHTS is for.
BUCKET_DAMPING_EXPONENT = 0.25

# Cap on behavioural edges emitted per wallet, keeping the layout readable.
MAX_EDGES_PER_NODE = 12

LABEL_PROPAGATION_ROUNDS = 20


def _node_id(chain, address_or_mask):
    return f"{chain}:{address_or_mask}"


def build_nodes(kb, hl_scores, evm_scores):
    """One node per knowledge-base wallet, enriched with observed data."""
    nodes = {}
    for wallet in kb.wallets.values():
        address = wallet.get("address")
        node = {
            "id": wallet["key"],
            "chain": wallet["chain"],
            "address": address,
            "display": wallet["display"],
            "resolved": wallet["resolved"],
            "entity": wallet.get("entity"),
            "handle": wallet.get("handle"),
            "labels": list(wallet.get("labels", [])),
            "narratives": list(wallet.get("narratives", [])),
            "tokens": list(wallet.get("tokens", [])),
            "source": wallet.get("source"),
            # value drives node radius; 0 until something is observed
            "value_usd": 0.0,
            "score": None,
            "tier": None,
            "pnl": None,
            "kind": "wallet",
        }

        hl = hl_scores.get(address) if address else None
        if hl:
            features = hl["features"]
            node["value_usd"] = features["equity"]
            node["score"] = hl["smart_score"]
            node["tier"] = hl["tier"]
            node["pnl"] = {
                "unrealized": features["unrealized_pnl"],
                "realized": features["realized_pnl"],
                "win_rate": features["win_rate"],
                "source": "hyperliquid",
            }
            node["kind"] = "perps"

        evm = evm_scores.get(address) if address else None
        if evm:
            node["score"] = evm["smart_score"]
            node["tier"] = evm["tier"]
            node.setdefault("volume_native", evm["features"]["volume_native"])

        nodes[node["id"]] = node
    return nodes


def build_transfer_edges(nodes, events):
    """Edges from observed on-chain value movement between tracked wallets.

    Only counterparties that are themselves tracked become edges; the rest
    are external and would explode the graph without adding signal.
    """
    by_address = {n["address"]: n["id"] for n in nodes.values() if n.get("address")}
    aggregated = defaultdict(lambda: {"value": 0.0, "count": 0, "directions": set()})

    for event in events.values():
        source = event.get("wallet_address")
        counterparty = event.get("counterparty")
        if not source or not counterparty or source == counterparty:
            continue
        source_id = by_address.get(source)
        target_id = by_address.get(counterparty)
        if not source_id or not target_id:
            continue

        # Direction is from the perspective of the wallet whose history this
        # event came from, so normalize before aggregating.
        if event.get("direction") == "in":
            pair = (target_id, source_id)
        else:
            pair = (source_id, target_id)

        entry = aggregated[pair]
        entry["value"] += float(event.get("value_native") or 0)
        entry["count"] += 1
        entry["directions"].add(event.get("direction") or "unknown")

    edges = []
    for (source_id, target_id), entry in sorted(aggregated.items()):
        edges.append({
            "source": source_id,
            "target": target_id,
            "kind": "transfer",
            "weight": 1.0,
            "value": round(entry["value"], 6),
            "tx_count": entry["count"],
            "directed": True,
            "evidence": f"{entry['count']} transfer(s), "
                        f"{entry['value']:.4f} {config.NATIVE_SYMBOL}",
        })
    return edges


def build_behavioural_edges(nodes, kb):
    """Edges from shared attributes. Free, and covers masked wallets too."""
    groups = defaultdict(lambda: defaultdict(list))

    for node in nodes.values():
        if node.get("entity"):
            groups["entity"][node["entity"]].append(node["id"])
        if node.get("source"):
            groups["source"][node["source"]].append(node["id"])
        for narrative in node.get("narratives", []):
            groups["narrative"][narrative].append(node["id"])
        # Direct token co-occurrence is the strongest behavioural signal:
        # two wallets buying the same micro-cap in one window is much more
        # than two wallets merely appearing in the same feed.
        for token in node.get("tokens", []):
            groups["token"][token].append(node["id"])

    pair_weights = defaultdict(float)
    pair_reasons = defaultdict(list)

    for kind, buckets in groups.items():
        weight = BEHAVIOURAL_WEIGHTS.get(kind, 0.1)
        for bucket_key, members in buckets.items():
            unique = sorted(set(members))
            # A bucket containing most of the graph carries almost no
            # information (every wallet shares one feed), so damp large ones.
            size = len(unique)
            if size < 2:
                continue
            damping = 1.0 / max(1.0, (size - 1) ** BUCKET_DAMPING_EXPONENT)
            for i, a in enumerate(unique):
                for b in unique[i + 1:]:
                    pair = (a, b)
                    pair_weights[pair] += weight * damping
                    if len(pair_reasons[pair]) < 3:
                        pair_reasons[pair].append(f"{kind}:{bucket_key}")

    edges = []
    for (a, b), weight in sorted(pair_weights.items()):
        if weight < MIN_BEHAVIOURAL_WEIGHT:
            continue
        edges.append({
            "source": a, "target": b, "kind": "behavioural",
            "weight": round(min(1.0, weight), 4),
            "directed": False,
            "reasons": pair_reasons[(a, b)],
            "evidence": ", ".join(pair_reasons[(a, b)]),
        })

    return _prune(edges)


def _prune(edges):
    """Keep only each node's strongest edges, so the layout stays readable."""
    per_node = defaultdict(list)
    for edge in edges:
        per_node[edge["source"]].append(edge)
        per_node[edge["target"]].append(edge)

    keep = set()
    for node_id, node_edges in per_node.items():
        node_edges.sort(key=lambda e: -e["weight"])
        for edge in node_edges[:MAX_EDGES_PER_NODE]:
            keep.add(id(edge))
    return [e for e in edges if id(e) in keep]


def detect_clusters(nodes, edges):
    """Label propagation over the combined graph.

    Deterministic: nodes are visited in sorted order and ties break on the
    lexicographically smallest label, so the same graph always yields the
    same clusters.
    """
    adjacency = defaultdict(list)
    for edge in edges:
        # A confirmed transfer pulls harder than an inferred association.
        weight = edge["weight"] * (1.5 if edge["kind"] == "transfer" else 1.0)
        adjacency[edge["source"]].append((edge["target"], weight))
        adjacency[edge["target"]].append((edge["source"], weight))

    labels = {node_id: node_id for node_id in nodes}

    for _ in range(LABEL_PROPAGATION_ROUNDS):
        changed = False
        for node_id in sorted(nodes):
            neighbours = adjacency.get(node_id)
            if not neighbours:
                continue
            tally = defaultdict(float)
            for neighbour, weight in neighbours:
                tally[labels[neighbour]] += weight
            best = min(sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))[:1])
            if best and best[0] != labels[node_id]:
                labels[node_id] = best[0]
                changed = True
        if not changed:
            break

    clusters = defaultdict(list)
    for node_id, label in labels.items():
        clusters[label].append(node_id)

    out = []
    for label, members in sorted(clusters.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if len(members) < 2:
            continue
        member_nodes = [nodes[m] for m in members]
        entity_counts = defaultdict(int)
        for member_node in member_nodes:
            if member_node.get("entity"):
                entity_counts[member_node["entity"]] += 1
        entities = sorted(entity_counts, key=lambda e: (-entity_counts[e], e))
        chains = sorted({n["chain"] for n in member_nodes})
        out.append({
            "id": f"cluster-{len(out) + 1}",
            "seed": label,
            "members": sorted(members),
            "size": len(members),
            "entities": entities,
            "chains": chains,
            "value_usd": round(sum(n.get("value_usd") or 0 for n in member_nodes), 2),
            # Name after an entity only when that entity accounts for a
            # meaningful share of the cluster. Labelling 22 wallets after
            # the one member that happens to be attributed is misleading.
            "name": (entities[0]
                     if entities and entity_counts[entities[0]] >= max(2, 0.3 * len(members))
                     else f"cluster-{len(out) + 1}"),
            "entity_counts": dict(entity_counts),
        })

    for cluster in out:
        for member in cluster["members"]:
            nodes[member]["cluster"] = cluster["id"]
    return out


def compute_flow(nodes, edges, clusters):
    """Net value flow per node and between clusters."""
    inflow = defaultdict(float)
    outflow = defaultdict(float)
    for edge in edges:
        if edge["kind"] != "transfer":
            continue
        outflow[edge["source"]] += edge["value"]
        inflow[edge["target"]] += edge["value"]

    for node_id, node in nodes.items():
        node["inflow"] = round(inflow.get(node_id, 0.0), 6)
        node["outflow"] = round(outflow.get(node_id, 0.0), 6)
        node["net_flow"] = round(node["inflow"] - node["outflow"], 6)

    cluster_of = {m: c["id"] for c in clusters for m in c["members"]}
    between = defaultdict(float)
    for edge in edges:
        if edge["kind"] != "transfer":
            continue
        a, b = cluster_of.get(edge["source"]), cluster_of.get(edge["target"])
        if a and b and a != b:
            between[(a, b)] += edge["value"]

    return [{"source": a, "target": b, "value": round(v, 6)}
            for (a, b), v in sorted(between.items(), key=lambda kv: -kv[1])]


def build(kb, events, hl_scores, evm_scores):
    """Assemble the full graph payload."""
    nodes = build_nodes(kb, hl_scores, evm_scores)
    transfer_edges = build_transfer_edges(nodes, events)
    behavioural_edges = build_behavioural_edges(nodes, kb)
    edges = transfer_edges + behavioural_edges

    clusters = detect_clusters(nodes, edges)
    cluster_flow = compute_flow(nodes, edges, clusters)

    return {
        "nodes": sorted(nodes.values(), key=lambda n: n["id"]),
        "edges": edges,
        "clusters": clusters,
        "cluster_flow": cluster_flow,
        "stats": {
            "nodes": len(nodes),
            "edges": len(edges),
            "transfer_edges": len(transfer_edges),
            "behavioural_edges": len(behavioural_edges),
            "clusters": len(clusters),
            "clustered_nodes": sum(c["size"] for c in clusters),
            # Honest coverage signal: how much of the graph rests on evidence
            # rather than inference.
            "transfer_coverage": round(
                len(transfer_edges) / len(edges), 4) if edges else 0.0,
        },
    }
