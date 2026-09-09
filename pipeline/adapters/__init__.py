"""Per-chain enrichment adapters.

Each adapter turns one chain's native data into features and a score. They
are deliberately NOT a shared interface over a single feature vector: a
perps trader's leverage and a UTXO holder's balance have no common shape,
and forcing them into the EVM transaction-history model would produce
numbers that look comparable but are not.
"""
