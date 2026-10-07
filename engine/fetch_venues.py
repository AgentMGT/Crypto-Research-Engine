"""Centralized and decentralized market scan (all free, keyless endpoints).

CEX: CoinGecko exchange volumes and its derivatives feed, which carries funding
     rate and open interest for perpetuals on Binance, Bybit, OKX, Bitget and others.
DEX: GeckoTerminal trending and newly created pools across every chain it indexes,
     DefiLlama spot-DEX and perp-DEX volume by protocol, and Hyperliquid perps
     (funding and open interest on an on-chain venue).
"""

from .fetch_crypto import CG, CG_HEADERS
from .http import get_json

GT = "https://api.geckoterminal.com/api/v2"
GT_HEADERS = {"Accept": "application/json;version=20230302"}


# ------------------------------------------------------------------ CEX

def cex_exchanges(n=15):
    rows = get_json(f"{CG}/exchanges", params={"per_page": n, "page": 1}, headers=CG_HEADERS)
    return [
        {
            "name": r["name"],
            "trust": r.get("trust_score"),
            "volume_btc": r.get("trade_volume_24h_btc_normalized") or r.get("trade_volume_24h_btc") or 0,
            "country": r.get("country"),
        }
        for r in rows
    ]


def cex_derivatives():
    """Raw perpetual tickers; aggregated per asset in signals.venue_signals."""
    rows = get_json(f"{CG}/derivatives", headers=CG_HEADERS, timeout=60)
    out = []
    for r in rows:
        if (r.get("contract_type") or "").lower() != "perpetual":
            continue
        out.append(
            {
                "venue": r.get("market"),
                "symbol": r.get("symbol"),
                "base": (r.get("index_id") or "").upper(),
                "price": _f(r.get("price")),
                "funding": _f(r.get("funding_rate")),  # percent per funding interval
                "oi_usd": _f(r.get("open_interest")),
                "volume_usd": _f(r.get("volume_24h")),
                "basis": _f(r.get("basis")),
            }
        )
    return out


# ------------------------------------------------------------------ DEX

def _pool(p, included):
    a = p["attributes"]
    rel = p.get("relationships") or {}
    net = ((rel.get("network") or {}).get("data") or {}).get("id") or p["id"].split("_")[0]
    dex_id = ((rel.get("dex") or {}).get("data") or {}).get("id")
    tx = ((a.get("transactions") or {}).get("h24")) or {}
    pc = a.get("price_change_percentage") or {}
    vol = a.get("volume_usd") or {}
    return {
        "name": a.get("name"),
        "address": a.get("address"),
        "network": net,
        "dex": (included.get(dex_id) or {}).get("name") or dex_id,
        "price_usd": _f(a.get("base_token_price_usd")),
        "liquidity": _f(a.get("reserve_in_usd")),
        "fdv": _f(a.get("fdv_usd")),
        "mcap": _f(a.get("market_cap_usd")),
        "vol_24h": _f(vol.get("h24")),
        "vol_1h": _f(vol.get("h1")),
        "ch_1h": _f(pc.get("h1")),
        "ch_24h": _f(pc.get("h24")),
        "buys": tx.get("buys") or 0,
        "sells": tx.get("sells") or 0,
        "buyers": tx.get("buyers") or 0,
        "sellers": tx.get("sellers") or 0,
        "created": a.get("pool_created_at"),
        "url": f"https://www.geckoterminal.com/{net}/pools/{a.get('address')}",
    }


def _pools(path, pages=2):
    out = []
    for page in range(1, pages + 1):
        d = get_json(f"{GT}/{path}", params={"include": "dex", "page": page}, headers=GT_HEADERS)
        included = {i["id"]: i.get("attributes") or {} for i in d.get("included") or []}
        out += [_pool(p, included) for p in d.get("data") or []]
    return out


def dex_trending():
    return _pools("networks/trending_pools", pages=3)


def dex_new():
    return _pools("networks/new_pools", pages=2)


def dex_volumes():
    params = {"excludeTotalDataChart": "true", "excludeTotalDataChartBreakdown": "true"}
    spot = get_json("https://api.llama.fi/overview/dexs", params=params)
    perps = get_json("https://api.llama.fi/overview/derivatives", params=params)

    def top(d, n=12):
        rows = sorted(d.get("protocols") or [], key=lambda p: p.get("total24h") or 0, reverse=True)[:n]
        return [
            {"name": p.get("displayName") or p.get("name"), "vol_24h": p.get("total24h") or 0,
             "ch_1d": p.get("change_1d"), "chains": (p.get("chains") or [])[:4]}
            for p in rows
        ]

    return {
        "spot_total_24h": spot.get("total24h"),
        "spot_change_1d": spot.get("change_1d"),
        "perp_total_24h": perps.get("total24h"),
        "perp_change_1d": perps.get("change_1d"),
        "spot_top": top(spot),
        "perp_top": top(perps),
    }


def hyperliquid():
    meta, ctxs = get_json("https://api.hyperliquid.xyz/info", post={"type": "metaAndAssetCtxs"})
    out = []
    for asset, c in zip(meta.get("universe") or [], ctxs):
        mark = _f(c.get("markPx"))
        prev = _f(c.get("prevDayPx"))
        oi = _f(c.get("openInterest"))
        out.append(
            {
                "venue": "Hyperliquid",
                "base": asset.get("name"),
                "price": mark,
                "ch_24h": (mark / prev - 1) * 100 if mark and prev else None,
                "funding": _f(c.get("funding")) * 100 if c.get("funding") is not None else None,  # % per hour
                "oi_usd": oi * mark if oi and mark else 0,
                "volume_usd": _f(c.get("dayNtlVlm")),
            }
        )
    return out


def _f(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def fetch_all():
    out, errors = {}, []
    for key, fn in [
        ("cex_exchanges", cex_exchanges),
        ("cex_perps", cex_derivatives),
        ("dex_trending", dex_trending),
        ("dex_new", dex_new),
        ("dex_volumes", dex_volumes),
        ("dex_perps", hyperliquid),
    ]:
        try:
            print(f"fetch {key}")
            out[key] = fn()
        except Exception as e:
            print(f"  FAILED {key}: {e}")
            errors.append(f"{key}: {e}")
    out["errors"] = errors
    return out
