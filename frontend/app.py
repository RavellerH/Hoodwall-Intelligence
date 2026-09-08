"""Streamlit dashboard for browsing scored wallets."""
import json
import os
import sys

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from google_sheets import get_all_rows

st.set_page_config(page_title="Robinhood Wallet Monitor", layout="wide")
st.title("Robinhood Chain Wallet Monitor")


def _parse_json_field(value):
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return []


@st.cache_data(ttl=60)
def load_wallets():
    rows = get_all_rows(config.GOOGLE_SHEET_ID, "wallets_updated")
    if not rows or len(rows) <= 1:
        return pd.DataFrame()

    columns = ["address", "smart_score", "tier", "labels", "evidence", "updated_at"]
    df = pd.DataFrame(rows[1:], columns=columns)
    # wallets_updated is append-only (one row per score run) - keep the latest per address
    return df.drop_duplicates(subset="address", keep="last")


wallets_df = load_wallets()

if wallets_df.empty:
    st.warning("No wallets found yet. Run the enrichment and scoring scripts first.")
    st.stop()

wallets_df["smart_score"] = pd.to_numeric(wallets_df["smart_score"], errors="coerce").fillna(0)

st.sidebar.header("Filters")
tier_filter = st.sidebar.multiselect(
    "Tier", options=["elite", "watch", "candidate", "archive"], default=["elite", "watch"]
)
label_filter = st.sidebar.multiselect(
    "Label",
    options=[
        "smart_money", "whale", "accumulator", "distributor", "trading_bot",
        "mev_bot", "lp_mm", "bridge_flow", "fresh_emerging", "noise",
    ],
)

filtered = wallets_df[wallets_df["tier"].isin(tier_filter)].copy()
filtered["parsed_labels"] = filtered["labels"].apply(_parse_json_field)

if label_filter:
    filtered = filtered[
        filtered["parsed_labels"].apply(lambda labels: any(l["name"] in label_filter for l in labels))
    ]

filtered = filtered.sort_values("smart_score", ascending=False)

st.subheader(f"Wallets ({len(filtered)})")

display_df = filtered.copy()
display_df["smart_score"] = display_df["smart_score"].round(0).astype(int)
display_df["labels"] = display_df["parsed_labels"].apply(lambda labels: ", ".join(l["name"] for l in labels))

st.dataframe(display_df[["address", "smart_score", "tier", "labels"]], width="stretch", hide_index=True)

selected = st.selectbox("View wallet detail", options=filtered["address"].tolist())

if selected:
    wallet = filtered[filtered["address"] == selected].iloc[0]
    st.subheader(f"Wallet: `{selected}`")
    st.metric("Smart Score", int(wallet["smart_score"]))
    st.metric("Tier", wallet["tier"])

    st.write("**Labels:**")
    for label in wallet["parsed_labels"]:
        st.write(f"- {label['name']} (confidence: {label.get('confidence', 0):.2f})")

    st.write("**Evidence:**")
    for ev in _parse_json_field(wallet["evidence"]):
        st.write(f"- {ev}")
