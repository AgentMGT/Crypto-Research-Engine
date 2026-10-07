"""Build the site from synthetic data, with no network access.

Used to check the templates and signal maths offline (CI uses the real fetchers).
Run: python tools/demo_build.py
"""

import math
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import build, fetch_crypto, fetch_tao, signals  # noqa: E402

random.seed(7)

NAMES = [
    ("bitcoin", "BTC", "Bitcoin"), ("ethereum", "ETH", "Ethereum"), ("tether", "USDT", "Tether"),
    ("solana", "SOL", "Solana"), ("bnb", "BNB", "BNB"), ("ripple", "XRP", "XRP"),
    ("usd-coin", "USDC", "USD Coin"), ("cardano", "ADA", "Cardano"), ("dogecoin", "DOGE", "Dogecoin"),
    ("bittensor", "TAO", "Bittensor"), ("chainlink", "LINK", "Chainlink"), ("avalanche-2", "AVAX", "Avalanche"),
    ("sui", "SUI", "Sui"), ("render-token", "RENDER", "Render"), ("near", "NEAR", "NEAR Protocol"),
    ("arbitrum", "ARB", "Arbitrum"), ("injective-protocol", "INJ", "Injective"), ("aave", "AAVE", "Aave"),
    ("fetch-ai", "FET", "Artificial Superintelligence"), ("hyperliquid", "HYPE", "Hyperliquid"),
]


def walk(start, n, drift, vol):
    out, p = [], start
    for _ in range(n):
        p *= 1 + random.gauss(drift, vol)
        out.append(p)
    return out


def fake_crypto():
    markets = []
    for i, (cid, sym, name) in enumerate(NAMES):
        price = 10 ** random.uniform(-1, 4.8) if sym not in ("USDT", "USDC") else 1.0
        spark = walk(price, 168, random.gauss(0.0004, 0.0008), 0.012)
        price = spark[-1]
        markets.append({
            "id": cid, "symbol": sym.lower(), "name": name, "current_price": price,
            "market_cap": price * random.uniform(1e6, 2e8), "market_cap_rank": i + 1,
            "total_volume": price * random.uniform(1e5, 4e7),
            "image": "", "sparkline_in_7d": {"price": spark},
            "price_change_percentage_1h_in_currency": random.gauss(0, 0.8),
            "price_change_percentage_24h_in_currency": random.gauss(0, 4),
            "price_change_percentage_7d_in_currency": (spark[-1] / spark[0] - 1) * 100,
            "price_change_percentage_30d_in_currency": random.gauss(4, 20),
        })
    return {
        "markets": markets,
        "global": {"total_market_cap": {"usd": 3.1e12}, "total_volume": {"usd": 1.4e11},
                   "market_cap_percentage": {"btc": 57.2, "eth": 11.4}, "market_cap_change_percentage_24h_usd": 1.8},
        "trending": [{"symbol": s.lower(), "market_cap_rank": i + 1} for i, (_, s, _) in enumerate(NAMES[:7])],
        "fear_greed": [{"value": str(max(5, min(95, int(55 + 18 * math.sin(d / 5)))))} for d in range(30)],
        "defi": {"chains": [{"name": n, "tvl": t} for n, t in
                            [("Ethereum", 6.4e10), ("Solana", 1.1e10), ("Base", 4.2e9), ("BSC", 5.6e9), ("Arbitrum", 3.1e9)]],
                 "stablecoin_supply": 3.05e11},
        "macro": [{"symbol": s, "name": n, "price": p, "chg_1d": random.gauss(0, 0.6),
                   "chg_1m": random.gauss(0.5, 3), "spark": walk(p, 22, 0.001, 0.008)}
                  for s, n, p in [("^GSPC", "S&P 500", 6120), ("^IXIC", "Nasdaq", 20300), ("^VIX", "VIX", 16.4),
                                  ("DX-Y.NYB", "US Dollar (DXY)", 103.2), ("^TNX", "US 10Y yield", 4.21),
                                  ("GC=F", "Gold", 2710), ("CL=F", "WTI Oil", 71.4), ("COIN", "Coinbase (COIN)", 268),
                                  ("MSTR", "Strategy (MSTR)", 352), ("IBIT", "BlackRock BTC ETF (IBIT)", 54.8)]],
        "errors": [],
    }


SUBNET_NAMES = ["apex", "targon", "templar", "chutes", "gradients", "celium", "dojo", "ridges", "nineteen",
                "sportstensor", "proprietary-trading", "zeus", "deval", "patrol", "bitmind", "score",
                "subvortex", "omron", "precog", "graphite"]


def fake_tao():
    subs = []
    for i in range(1, 76):
        price = 10 ** random.uniform(-3.4, -1.1)
        supply = random.uniform(2e5, 9e6)
        liq = random.uniform(300, 60000)
        subs.append({
            "netuid": i,
            "name": SUBNET_NAMES[i - 1] if i <= len(SUBNET_NAMES) else f"Subnet {i}",
            "price_tao": price,
            "moving_price_tao": price * random.uniform(0.7, 1.35),
            "tao_liquidity": liq,
            "alpha_in_pool": liq / price,
            "alpha_supply": supply,
            "mcap_tao": price * supply,
            "tao_emission_per_block": random.uniform(0.0001, 0.08),
            "volume_total_tao": random.uniform(1e3, 2e6),
            "ema_tao_flow": random.gauss(0, 0.05),
            "age_days": random.uniform(5, 900),
            "neurons": random.randint(32, 256),
        })
    return {
        "chain": {"block": 6_241_883, "subnets": subs},
        "tao": {"price": 412.5, "mcap": 3.9e9, "volume": 1.8e8, "chg_24h": 2.4, "chg_7d": -5.1,
                "chg_30d": 12.8, "ath": 757.0, "circulating": 9_410_000, "max_supply": 21_000_000,
                "history": walk(380, 90, 0.001, 0.03)},
        "errors": ["demo build: synthetic data, no live sources"],
    }


fetch_crypto.fetch_all = fake_crypto
fetch_tao.fetch_all = fake_tao

# Two past snapshots so the "since last refresh" and 1d/7d columns have something to compare.
build.HIST.mkdir(parents=True, exist_ok=True)
e = build.env()
ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
build.OUT.mkdir(parents=True, exist_ok=True)
build.DATA.mkdir(parents=True, exist_ok=True)
for _ in range(3):
    build.build_crypto(e, ts)
    build.build_tao(e, ts)
main_signals = None
build.main()
