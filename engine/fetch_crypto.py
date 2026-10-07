"""Crypto + broader-market data from free public APIs (no keys required).

Sources: CoinGecko (prices, global, trending), alternative.me (Fear & Greed),
DefiLlama (chain TVL, stablecoin supply), Yahoo Finance chart API (macro).
An optional COINGECKO_API_KEY (free "demo" key) raises rate limits.
"""

import os

from .http import get_json

CG = "https://api.coingecko.com/api/v3"
CG_HEADERS = {"x-cg-demo-api-key": os.environ["COINGECKO_API_KEY"]} if os.environ.get("COINGECKO_API_KEY") else {}

# Macro dashboard: Yahoo symbol -> display name
MACRO = {
    "^GSPC": "S&P 500",
    "^IXIC": "Nasdaq",
    "^VIX": "VIX",
    "DX-Y.NYB": "US Dollar (DXY)",
    "^TNX": "US 10Y yield",
    "GC=F": "Gold",
    "CL=F": "WTI Oil",
    "COIN": "Coinbase (COIN)",
    "MSTR": "Strategy (MSTR)",
    "IBIT": "BlackRock BTC ETF (IBIT)",
}


def coins_markets(pages=2, per_page=125):
    rows = []
    for page in range(1, pages + 1):
        rows += get_json(
            f"{CG}/coins/markets",
            params={
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": per_page,
                "page": page,
                "sparkline": "true",
                "price_change_percentage": "1h,24h,7d,30d",
            },
            headers=CG_HEADERS,
        )
    return rows


def global_stats():
    return get_json(f"{CG}/global", headers=CG_HEADERS)["data"]


def trending():
    data = get_json(f"{CG}/search/trending", headers=CG_HEADERS)
    return [c["item"] for c in data.get("coins", [])]


def fear_greed(days=30):
    return get_json("https://api.alternative.me/fng/", params={"limit": days})["data"]


def defi():
    chains = get_json("https://api.llama.fi/v2/chains")
    chains = sorted(chains, key=lambda c: c.get("tvl") or 0, reverse=True)[:15]
    stables = get_json("https://stablecoins.llama.fi/stablecoins", params={"includePrices": "false"})
    total_stables = sum(
        (s.get("circulating") or {}).get("peggedUSD", 0) or 0 for s in stables.get("peggedAssets", [])
    )
    return {"chains": [{"name": c["name"], "tvl": c.get("tvl") or 0} for c in chains], "stablecoin_supply": total_stables}


def macro():
    out = []
    for sym, name in MACRO.items():
        try:
            d = get_json(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                params={"range": "1mo", "interval": "1d"},
            )["chart"]["result"][0]
            closes = [c for c in d["indicators"]["quote"][0]["close"] if c is not None]
            if len(closes) < 2:
                continue
            out.append(
                {
                    "symbol": sym,
                    "name": name,
                    "price": closes[-1],
                    "chg_1d": (closes[-1] / closes[-2] - 1) * 100,
                    "chg_1m": (closes[-1] / closes[0] - 1) * 100,
                    "spark": closes,
                }
            )
        except Exception as e:  # one bad symbol shouldn't sink the page
            print(f"  macro {sym}: {e}")
    return out


def fetch_all():
    """Each source is independent; a failure leaves that section empty."""
    out, errors = {}, []
    for key, fn in [
        ("markets", coins_markets),
        ("global", global_stats),
        ("trending", trending),
        ("fear_greed", fear_greed),
        ("defi", defi),
        ("macro", macro),
    ]:
        try:
            print(f"fetch {key}")
            out[key] = fn()
        except Exception as e:
            print(f"  FAILED {key}: {e}")
            errors.append(f"{key}: {e}")
    out["errors"] = errors
    return out
