"""Ollama integration: text generation, chat, embeddings, wallet categorization."""
import json
import re

import requests

import config

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)

REQUEST_TIMEOUT = 120


def generate(prompt: str, system: str = None, temperature: float = 0.1):
    """Generate text using Ollama."""
    payload = {
        "model": config.OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": 2048},
    }
    if system:
        payload["system"] = system

    response = requests.post(f"{config.OLLAMA_BASE_URL}/api/generate", json=payload, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()["response"]


def chat(messages: list, temperature: float = 0.1):
    """Chat with Ollama (pass full message history for multi-turn context)."""
    payload = {
        "model": config.OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": 2048},
    }

    response = requests.post(f"{config.OLLAMA_BASE_URL}/api/chat", json=payload, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()["message"]["content"]


def embed(text: str):
    """Generate embeddings using Ollama."""
    payload = {"model": config.OLLAMA_EMBED_MODEL, "prompt": text}
    response = requests.post(f"{config.OLLAMA_BASE_URL}/api/embeddings", json=payload, timeout=60)
    response.raise_for_status()
    return response.json()["embedding"]


CATEGORIZE_SYSTEM_PROMPT = """You are a blockchain wallet analyst. Categorize wallets based on their on-chain behavior.

Categories (a wallet can have multiple):
- smart_money: Repeated profitable trades, early entry, good timing
- whale: Large positions relative to token liquidity
- accumulator: Sustained net buying over time
- distributor: Sustained net selling over time
- trading_bot: High frequency, regular timing, machine-like patterns
- mev_bot: Arbitrage, multi-swap transactions, profit after gas
- lp_mm: Repeated liquidity adds/removals
- bridge_flow: Repeated bridge/CEX interactions
- fresh_emerging: New wallet with coherent early activity
- noise: Dust, spam, no meaningful activity

Respond with ONLY a JSON object, no other text, in this exact format:
{"labels": [{"name": "smart_money", "confidence": 0.8}], "evidence": ["Executed 14 swaps in 9 days"], "smart_score": 72, "tier": "watch"}
"""


def categorize_wallet(wallet_data: dict, events: list) -> dict:
    """Use the local LLM to categorize a wallet based on its activity."""
    prompt = f"""Analyze this wallet's activity:

Wallet: {wallet_data['address']}
Type: {wallet_data.get('address_type', 'unknown')}
Total transactions: {len(events)}
Recent events:
{json.dumps(events[:10], indent=2)}

Categorize this wallet and calculate a smart score (0-100).
"""

    response = chat([
        {"role": "system", "content": CATEGORIZE_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ])

    try:
        cleaned = _JSON_FENCE_RE.sub("", response.strip())
        return json.loads(cleaned)
    except (json.JSONDecodeError, KeyError):
        # Local models occasionally wrap or malform the JSON; degrade gracefully.
        return {
            "labels": [],
            "evidence": ["LLM parsing failed"],
            "smart_score": 0,
            "tier": "candidate",
        }
