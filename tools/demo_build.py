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

from engine import build, fetch_crypto, fetch_news, fetch_tao, fetch_venues, signals  # noqa: E402

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
        "trending": [{"id": cid, "name": n, "symbol": s.lower(), "market_cap_rank": i + 1} for i, (cid, s, n) in enumerate(NAMES[:7])],
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
            "moving_price_tao": price * random.uniform(0.94, 1.06),
            "tao_liquidity": liq,
            "alpha_in_pool": liq / price,
            "alpha_supply": supply,
            "mcap_tao": price * supply,
            "tao_emission_per_block": random.uniform(0.0001, 0.08),
            "volume_total_tao": random.uniform(1e3, 2e6),
            "ema_tao_flow": random.gauss(0, 0.02),
            "age_days": random.uniform(5, 900),
            "neurons": random.randint(32, 256),
        })
    return {
        "chain": {"block": 6_241_883, "subnets": subs},
        "tao": {"price": 412.5, "mcap": 3.9e9, "volume": 1.8e8, "chg_24h": 2.4, "chg_7d": -5.1,
                "chg_30d": 12.8, "ath": 757.0, "circulating": 9_410_000, "max_supply": 21_000_000,
                "history": walk(380, 90, 0.001, 0.03)},
        "venues": [
            {"venue": v, "kind": k, "pair": pr, "price": 412.5 * random.uniform(0.998, 1.002), "volume": vol,
             "spread": random.uniform(0.01, 0.4), "depth_2pct": vol * random.uniform(0.005, 0.03), "trust": "green", "stale": False}
            for v, k, pr, vol in [("Binance", "CEX", "TAO/USDT", 6.1e7), ("Upbit", "CEX", "TAO/KRW", 2.4e7), ("Bybit", "CEX", "TAO/USDT", 1.1e7),
                                  ("Coinbase Exchange", "CEX", "TAO/USD", 9.2e6), ("Kraken", "CEX", "TAO/USD", 3.3e6),
                                  ("Uniswap V3 (Ethereum)", "DEX", "WTAO/WETH", 1.2e6), ("Raydium", "DEX", "TAO/SOL", 4.0e5)]
        ],
        "errors": ["demo build: synthetic data, no live sources"],
    }


CHAINS = [("solana", "Raydium"), ("base", "Aerodrome"), ("eth", "Uniswap V3"), ("bsc", "PancakeSwap V3"), ("arbitrum", "Camelot")]


def fake_pool(i, new=False):
    net, dex = random.choice(CHAINS)
    liq = 10 ** random.uniform(4.5, 7.2)
    buys, sells = random.randint(200, 9000), random.randint(200, 9000)
    return {
        "name": f"TOKEN{i} / {'SOL' if net == 'solana' else 'WETH'}", "address": f"0x{i:040x}", "network": net, "dex": dex,
        "price_usd": 10 ** random.uniform(-6, 0), "liquidity": liq, "fdv": liq * random.uniform(3, 40), "mcap": None,
        "vol_24h": liq * random.uniform(0.3, 12), "vol_1h": liq * random.uniform(0.01, 0.6),
        "ch_1h": random.gauss(0, 6), "ch_24h": random.gauss(10, 45),
        "buys": buys, "sells": sells, "buyers": int(buys * 0.6), "sellers": int(sells * 0.6),
        "created": "2026-10-06T09:14:00Z" if new else "2026-08-21T12:00:00Z",
        "url": "https://www.geckoterminal.com",
    }


def fake_venues():
    perps = []
    for _, sym, _ in NAMES:
        if sym in ("USDT", "USDC"):
            continue
        for venue in ("Binance (Futures)", "Bybit (Futures)", "OKX (Futures)", "Bitget Futures"):
            perps.append({"venue": venue, "symbol": f"{sym}USDT", "base": sym, "price": 1.0,
                          "funding": random.gauss(0.012, 0.025), "oi_usd": 10 ** random.uniform(7, 9.6),
                          "volume_usd": 10 ** random.uniform(7.5, 10), "basis": 0})
    hl = [{"venue": "Hyperliquid", "base": sym, "price": 1.0, "ch_24h": random.gauss(0, 4),
           "funding": random.gauss(0.0015, 0.003), "oi_usd": 10 ** random.uniform(6.5, 9), "volume_usd": 10 ** random.uniform(7, 9.5)}
          for _, sym, _ in NAMES if sym not in ("USDT", "USDC")] + [
          {"venue": "Hyperliquid", "base": "PUMP", "price": 0.004, "ch_24h": 8.1, "funding": 0.009, "oi_usd": 4.1e7, "volume_usd": 2.2e8}]
    return {
        "cex_exchanges": [{"id": n.lower().split()[0], "name": n, "trust": t, "volume_btc": v, "country": None} for n, t, v in
                          [("Binance", 10, 182000), ("Bybit", 10, 41000), ("Coinbase Exchange", 10, 38500), ("OKX", 10, 35200),
                           ("Upbit", 10, 22100), ("Bitget", 9, 19800), ("Gate", 9, 15400), ("Kraken", 10, 9300)]],
        "cex_perps": perps,
        "dex_trending": [fake_pool(i) for i in range(40)],
        "dex_new": [fake_pool(100 + i, new=True) for i in range(20)],
        "dex_volumes": {"spot_total_24h": 1.21e10, "spot_change_1d": 6.4, "perp_total_24h": 1.37e10, "perp_change_1d": -3.1,
                        "spot_top": [{"name": n, "slug": n.lower(), "vol_24h": v, "ch_1d": random.gauss(0, 12), "chains": []} for n, v in
                                     [("Uniswap", 3.1e9), ("PancakeSwap", 2.4e9), ("Raydium", 1.3e9), ("Aerodrome", 8.8e8), ("Meteora", 7.1e8), ("Orca", 5.2e8), ("Curve", 3.3e8)]],
                        "perp_top": [{"name": n, "slug": n.lower(), "vol_24h": v, "ch_1d": random.gauss(0, 12), "chains": []} for n, v in
                                     [("Hyperliquid", 8.4e9), ("Aster", 2.2e9), ("Lighter", 1.4e9), ("edgeX", 6.0e8), ("Jupiter Perps", 4.1e8)]]},
        "dex_perps": hl,
        "errors": [],
    }


HEADLINES = {
    "coindesk": ["Bitcoin holds $60K as ETF outflows slow", "Solana DEX volume tops Ethereum for third week",
                 "SEC chair Atkins signals token taxonomy guidance", "Chainlink adds CCIP lanes for tokenized funds"],
    "cointelegraph": ["Ethereum staking ETF sees record inflows", "Hacker drains $40M from cross-chain bridge",
                      "Fed rate cut odds rise after soft CPI print", "Tether mints $1B USDT on Tron"],
    "theblock": ["Senate leaders weigh lame-duck vote on CLARITY Act", "CFTC nominee backs spot crypto oversight"],
    "decrypt": ["Hyperliquid open interest hits new high", "XRP lawsuit appeal briefing set"],
    "blockworks": ["Market structure bill: what the failed cloture vote means for exchanges"],
    "CLARITY": ["Tillis motion to reconsider keeps CLARITY Act alive - Reuters",
                "Crypto PACs rethink midterm spending after CLARITY Act stalls - Bloomberg",
                "Lummis: market structure bill is dead for this Congress - Politico"],
    "Bittensor": ["Bittensor subnet emissions shift after dTAO update - CoinDesk",
                  "Grayscale files for Bittensor TAO trust conversion - The Block"],
}


def fake_feed(url):
    key = next((k for k in HEADLINES if k.lower() in url.lower()), None)
    google = "news.google.com" in url
    items = []
    for i, t in enumerate(HEADLINES.get(key, []) if key else []):
        when = datetime.now(timezone.utc).timestamp() - (i * 5 + random.random() * 30) * 3600
        pub = datetime.fromtimestamp(when, timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
        src = f"<source url='https://x'>{t.rsplit(' - ', 1)[1]}</source>" if google and " - " in t else ""
        items.append(f"<item><title>{t}</title><link>https://example.com/{key}/{i}</link><pubDate>{pub}</pubDate>{src}"
                     f"<description>&lt;p&gt;Synthetic summary for {t}.&lt;/p&gt;</description></item>")
    return f"<?xml version='1.0'?><rss><channel>{''.join(items)}</channel></rss>".encode()


fetch_news._get = fake_feed
fetch_news.get_json = lambda *a, **k: {"objects": [{"current_status_label": "Passed House",
                                                     "current_status_date": "2025-07-17",
                                                     "link": "https://www.govtrack.us/congress/bills/119/hr3633"}]}
fetch_crypto.fetch_all = fake_crypto
fetch_venues.fetch_all = fake_venues
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
