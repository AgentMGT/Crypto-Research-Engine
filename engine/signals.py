"""Rule-based research signals. These are screens to research further,
not trade instructions."""

import math

STABLES = {
    "usdt", "usdc", "dai", "usde", "fdusd", "tusd", "usdd", "pyusd", "usds", "busd", "gusd", "frax", "lusd",
    "usd0", "usdb", "rlusd", "susde", "susds", "usdtb", "bfusd", "usd1", "eurc", "usdf", "usdg", "buidl",
}
WRAPPED = {"wbtc", "weth", "steth", "wsteth", "weeth", "cbbtc", "reth", "wbeth", "lbtc", "meth", "ezeth", "rseth", "solvbtc", "jitosol", "msol", "bnsol", "wtrx", "clbtc", "tbtc", "wbnb"}


def pct(a, b):
    return (a / b - 1) * 100 if a and b else None


def rsi(series, period=14):
    if len(series) <= period:
        return None
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = series[i] - series[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    ag, al = gains / period, losses / period
    for i in range(period + 1, len(series)):
        d = series[i] - series[i - 1]
        ag = (ag * (period - 1) + max(d, 0)) / period
        al = (al * (period - 1) + max(-d, 0)) / period
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


def resample(series, step):
    return series[step - 1 :: step] if len(series) >= step else series


def clamp(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


# ---------------------------------------------------------------- crypto

def crypto_signals(data, history):
    markets = data.get("markets") or []
    btc = next((c for c in markets if c["id"] == "bitcoin"), None)
    btc7 = (btc or {}).get("price_change_percentage_7d_in_currency") or 0
    btc30 = (btc or {}).get("price_change_percentage_30d_in_currency") or 0
    prev = history[-1]["coins"] if history else {}

    coins = []
    for c in markets:
        sym = (c.get("symbol") or "").lower()
        if sym in STABLES or sym in WRAPPED or not c.get("current_price"):
            continue
        spark = ((c.get("sparkline_in_7d") or {}).get("price")) or []
        c4h = resample(spark, 4)
        r = rsi(c4h)
        hi, lo = (max(spark), min(spark)) if spark else (None, None)
        rng = ((c["current_price"] - lo) / (hi - lo) * 100) if spark and hi > lo else None
        ch24 = c.get("price_change_percentage_24h_in_currency") or 0
        ch7 = c.get("price_change_percentage_7d_in_currency") or 0
        ch30 = c.get("price_change_percentage_30d_in_currency") or 0
        turnover = (c.get("total_volume") or 0) / c["market_cap"] * 100 if c.get("market_cap") else 0
        rs7, rs30 = ch7 - btc7, ch30 - btc30

        # Composite: relative strength + trend position + participation, minus overextension.
        score = (
            0.35 * clamp(rs7 / 20)
            + 0.25 * clamp(rs30 / 40)
            + 0.15 * clamp(((rng or 50) - 50) / 50)
            + 0.15 * clamp(math.log10(max(turnover, 0.1) / 5))
            - (0.2 if r and r > 80 else 0)
        )
        tags = []
        if r is not None and r < 30:
            tags.append("Oversold")
        if r is not None and r > 75:
            tags.append("Overbought")
        if rng is not None and rng > 92 and rs7 > 0:
            tags.append("Breaking 7d high")
        if rs7 > 10 and rs30 > 15:
            tags.append("Momentum leader")
        if turnover > 25:
            tags.append("Unusual volume")
        if ch7 < -15 and rs7 < -10:
            tags.append("Heavy selling")
        if r is not None and r < 35 and rs30 > 10:
            tags.append("Pullback in uptrend")

        coins.append(
            {
                "id": c["id"],
                "symbol": sym.upper(),
                "name": c["name"],
                "image": c.get("image"),
                "rank": c.get("market_cap_rank"),
                "price": c["current_price"],
                "mcap": c.get("market_cap") or 0,
                "volume": c.get("total_volume") or 0,
                "ch1h": c.get("price_change_percentage_1h_in_currency"),
                "ch24": ch24,
                "ch7": ch7,
                "ch30": ch30,
                "since_last": pct(c["current_price"], (prev.get(c["id"]) or {}).get("price")),
                "rsi": r,
                "range7": rng,
                "turnover": turnover,
                "rs7": rs7,
                "score": round(score * 100),
                "tags": tags,
                "spark": c4h,
            }
        )

    ideas = []
    for c in sorted(coins, key=lambda x: -x["score"]):
        if any(t in c["tags"] for t in ("Momentum leader", "Breaking 7d high", "Pullback in uptrend")) and "Overbought" not in c["tags"]:
            ideas.append({**c, "kind": "long-bias"})
    for c in sorted(coins, key=lambda x: x["rsi"] or 50):
        if "Oversold" in c["tags"] and c["rank"] and c["rank"] <= 150:
            ideas.append({**c, "kind": "mean-reversion"})
    for c in sorted(coins, key=lambda x: x["score"]):
        if "Heavy selling" in c["tags"]:
            ideas.append({**c, "kind": "avoid / short-bias"})

    return {"coins": coins, "ideas": ideas[:18], "regime": regime(data, btc)}


def regime(data, btc):
    g = data.get("global") or {}
    fg = data.get("fear_greed") or []
    macro = {m["symbol"]: m for m in data.get("macro") or []}
    fg_now = int(fg[0]["value"]) if fg else None
    fg_week = int(fg[7]["value"]) if len(fg) > 7 else None
    points = 0
    reasons = []
    if fg_now is not None:
        if fg_now >= 60:
            points += 1
            reasons.append(f"Fear & Greed at {fg_now} (greed)")
        elif fg_now <= 35:
            points -= 1
            reasons.append(f"Fear & Greed at {fg_now} (fear)")
    if btc:
        b7 = btc.get("price_change_percentage_7d_in_currency") or 0
        b30 = btc.get("price_change_percentage_30d_in_currency") or 0
        if b7 > 3 and b30 > 0:
            points += 1
            reasons.append(f"BTC trending up ({b7:+.1f}% 7d, {b30:+.1f}% 30d)")
        elif b7 < -3 and b30 < 0:
            points -= 1
            reasons.append(f"BTC trending down ({b7:+.1f}% 7d, {b30:+.1f}% 30d)")
    vix = macro.get("^VIX")
    if vix:
        if vix["price"] > 25:
            points -= 1
            reasons.append(f"VIX elevated at {vix['price']:.1f}")
        elif vix["price"] < 15:
            points += 1
            reasons.append(f"VIX calm at {vix['price']:.1f}")
    dxy = macro.get("DX-Y.NYB")
    if dxy and abs(dxy["chg_1m"]) > 1.5:
        points += -1 if dxy["chg_1m"] > 0 else 1
        reasons.append(f"Dollar {'strengthening' if dxy['chg_1m'] > 0 else 'weakening'} ({dxy['chg_1m']:+.1f}% 1m)")
    label = "Risk-on" if points >= 2 else "Risk-off" if points <= -2 else "Neutral"
    return {
        "label": label,
        "points": points,
        "reasons": reasons,
        "fear_greed": fg_now,
        "fear_greed_week_ago": fg_week,
        "fear_greed_series": [int(x["value"]) for x in reversed(fg)],
        "btc_dominance": (g.get("market_cap_percentage") or {}).get("btc"),
        "eth_dominance": (g.get("market_cap_percentage") or {}).get("eth"),
        "total_mcap": (g.get("total_market_cap") or {}).get("usd"),
        "total_volume": (g.get("total_volume") or {}).get("usd"),
        "mcap_change_24h": g.get("market_cap_change_percentage_24h_usd"),
    }


# ---------------------------------------------------------------- TAO subnets

def _ago(history, runs):
    """Snapshot roughly `runs` refreshes back (3 refreshes per day)."""
    if not history:
        return {}
    return history[max(0, len(history) - runs)]["subnets"]


def tao_signals(data, history):
    chain = data.get("chain") or {}
    subnets = chain.get("subnets") or []
    total_em = sum(s["tao_emission_per_block"] for s in subnets) or 1
    prev, d1, d7 = _ago(history, 1), _ago(history, 3), _ago(history, 21)
    tao_usd = (data.get("tao") or {}).get("price") or 0

    rows = []
    for s in subnets:
        k = str(s["netuid"])
        em_share = s["tao_emission_per_block"] / total_em * 100
        daily_em_tao = s["tao_emission_per_block"] * 7200  # ~7200 blocks/day
        em_to_mcap = (daily_em_tao * 365 / s["mcap_tao"] * 100) if s["mcap_tao"] else None
        prem = pct(s["price_tao"], s["moving_price_tao"])
        ch_last = pct(s["price_tao"], (prev.get(k) or {}).get("price"))
        ch_1d = pct(s["price_tao"], (d1.get(k) or {}).get("price"))
        ch_7d = pct(s["price_tao"], (d7.get(k) or {}).get("price"))
        vol_1d = s["volume_total_tao"] - (d1.get(k) or {}).get("volume", s["volume_total_tao"]) if d1 else None
        liq_1d = pct(s["tao_liquidity"], (d1.get(k) or {}).get("liq"))

        score = (
            0.30 * clamp((prem or 0) / 10)
            + 0.25 * clamp(s["ema_tao_flow"] * 7200 / 200)  # daily EMA net flow vs 200 TAO
            + 0.20 * clamp(((em_to_mcap or 0) - 5) / 15)
            + 0.15 * clamp((ch_7d or 0) / 40)
            + 0.10 * clamp(math.log10(max(s["tao_liquidity"], 1) / 3000))
        )
        tags = []
        if s["ema_tao_flow"] > 0 and (prem or 0) > 2:
            tags.append("Inflows + above EMA")
        if s["ema_tao_flow"] < 0:
            tags.append("Net outflows")
        if em_to_mcap and em_to_mcap > 15 and s["ema_tao_flow"] >= 0:
            tags.append("Cheap vs emissions")
        if (prem or 0) < -8:
            tags.append("Far below EMA")
        if s["tao_liquidity"] < 1000:
            tags.append("Thin liquidity")
        if s["age_days"] is not None and s["age_days"] < 30:
            tags.append("New subnet")
        rows.append(
            {
                **s,
                "price_usd": s["price_tao"] * tao_usd,
                "mcap_usd": s["mcap_tao"] * tao_usd,
                "em_share": em_share,
                "daily_em_tao": daily_em_tao,
                "em_to_mcap": em_to_mcap,
                "ema_premium": prem,
                "daily_flow_tao": s["ema_tao_flow"] * 7200,
                "since_last": ch_last,
                "ch_1d": ch_1d,
                "ch_7d": ch_7d,
                "vol_1d": vol_1d,
                "liq_1d": liq_1d,
                "score": round(score * 100),
                "tags": tags,
            }
        )

    ideas = []
    for r in sorted(rows, key=lambda x: -x["score"]):
        if ("Inflows + above EMA" in r["tags"] or "Cheap vs emissions" in r["tags"]) and "Thin liquidity" not in r["tags"]:
            ideas.append({**r, "kind": "accumulate / research"})
    for r in sorted(rows, key=lambda x: x["daily_flow_tao"]):
        if "Net outflows" in r["tags"] and r["em_share"] > 0.5:
            ideas.append({**r, "kind": "caution"})

    eco = {
        "subnet_count": len(rows),
        "total_alpha_mcap_tao": sum(r["mcap_tao"] for r in rows),
        "total_pool_tao": sum(r["tao_liquidity"] for r in rows),
        "net_daily_flow_tao": sum(r["daily_flow_tao"] for r in rows),
        "inflow_count": sum(1 for r in rows if r["ema_tao_flow"] > 0),
        "top5_em_share": sum(sorted((r["em_share"] for r in rows), reverse=True)[:5]),
        "block": chain.get("block"),
    }
    return {"subnets": rows, "ideas": ideas[:14], "eco": eco}


# ---------------------------------------------------------------- snapshots for history

def crypto_snapshot(sig, ts):
    return {"ts": ts, "coins": {c["id"]: {"price": c["price"], "mcap": c["mcap"]} for c in sig["coins"]}}


def tao_snapshot(sig, ts, tao_price):
    return {
        "ts": ts,
        "tao_price": tao_price,
        "subnets": {
            str(s["netuid"]): {"price": s["price_tao"], "volume": s["volume_total_tao"], "liq": s["tao_liquidity"],
                               "flow": s["daily_flow_tao"], "em": s["em_share"]}
            for s in sig["subnets"]
        },
    }


# ---------------------------------------------------------------- CEX + DEX venues

FUNDING_HOT = 0.05    # % per 8h: longs paying ~55% annualised
FUNDING_COLD = -0.02  # % per 8h: shorts paying


def perp_book(cex_perps, dex_perps):
    """Per-asset perp positioning across CEX venues, with Hyperliquid alongside."""
    agg = {}
    for p in cex_perps or []:
        base = (p.get("base") or "").upper()
        if not base or p.get("funding") is None or not p.get("oi_usd"):
            continue
        a = agg.setdefault(base, {"base": base, "oi": 0.0, "vol": 0.0, "fw": 0.0, "venues": set()})
        a["oi"] += p["oi_usd"]
        a["vol"] += p.get("volume_usd") or 0
        a["fw"] += p["funding"] * p["oi_usd"]
        a["venues"].add(p.get("venue"))
    hl = {(p.get("base") or "").upper(): p for p in dex_perps or []}
    rows = []
    for base, a in agg.items():
        f8 = a["fw"] / a["oi"] if a["oi"] else None
        h = hl.get(base)
        hl8 = h["funding"] * 8 if h and h.get("funding") is not None else None
        tags = []
        if f8 is not None and f8 > FUNDING_HOT:
            tags.append("Crowded longs")
        if f8 is not None and f8 < FUNDING_COLD:
            tags.append("Shorts paying")
        if f8 is not None and hl8 is not None and abs(f8 - hl8) > 0.03:
            tags.append("CEX/DEX funding gap")
        rows.append({
            "base": base, "oi_usd": a["oi"], "volume_usd": a["vol"], "venues": len(a["venues"]),
            "funding_8h": f8, "funding_ann": f8 * 3 * 365 if f8 is not None else None,
            "hl_funding_8h": hl8, "hl_oi_usd": h.get("oi_usd") if h else None, "tags": tags,
        })
    # Assets that only trade as perps on Hyperliquid still matter for an on-chain read.
    for base, h in hl.items():
        if base not in agg and h.get("oi_usd", 0) > 5e6 and h.get("funding") is not None:
            hl8 = h["funding"] * 8
            rows.append({
                "base": base, "oi_usd": 0, "volume_usd": 0, "venues": 0, "funding_8h": None,
                "funding_ann": None, "hl_funding_8h": hl8, "hl_oi_usd": h["oi_usd"],
                "tags": ["DEX-only perp"] + (["Crowded longs"] if hl8 > FUNDING_HOT else []),
            })
    return sorted(rows, key=lambda r: -((r["oi_usd"] or 0) + (r["hl_oi_usd"] or 0)))


def dex_pool_signals(pools, min_liq=50_000):
    seen, rows = set(), []
    for p in pools or []:
        key = (p["network"], p["address"])
        if key in seen or not p.get("liquidity") or p["liquidity"] < min_liq:
            continue
        seen.add(key)
        tx = (p["buys"] or 0) + (p["sells"] or 0)
        buy_ratio = p["buys"] / tx if tx else None
        turnover = (p["vol_24h"] or 0) / p["liquidity"]
        tags = []
        if p["liquidity"] < 150_000:
            tags.append("Thin liquidity")
        if buy_ratio is not None and buy_ratio > 0.6 and p["buyers"] > p["sellers"]:
            tags.append("Buy pressure")
        if buy_ratio is not None and buy_ratio < 0.4:
            tags.append("Sell pressure")
        if turnover > 8:
            tags.append("Churn > 8x liquidity")
        if p.get("created") and p["created"][:10] >= _days_ago(2):
            tags.append("New pool")
        score = (
            0.35 * clamp(((buy_ratio or 0.5) - 0.5) * 5)
            + 0.25 * clamp(math.log10(max(p["liquidity"], 1) / 500_000))
            + 0.25 * clamp((p["ch_24h"] or 0) / 50)
            - (0.25 if turnover > 8 else 0)
        )
        rows.append({**p, "buy_ratio": buy_ratio, "turnover": turnover, "score": round(score * 100), "tags": tags})
    return rows


def _days_ago(n):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")


def venue_signals(venues, global_stats, coins):
    perps = perp_book(venues.get("cex_perps"), venues.get("dex_perps"))
    vols = venues.get("dex_volumes") or {}
    cex_spot = ((global_stats or {}).get("total_volume") or {}).get("usd")
    dex_spot = vols.get("spot_total_24h")
    by_sym = {c["symbol"]: c for c in coins or []}
    for r in perps:
        c = by_sym.get(r["base"])
        r["cg_id"] = c["id"] if c else None
        if c and c["mcap"]:
            r["oi_to_mcap"] = ((r["oi_usd"] or 0) + (r["hl_oi_usd"] or 0)) / c["mcap"] * 100
            if r["oi_to_mcap"] > 8:
                r["tags"].append("Leverage heavy")
            c["tags"] = c["tags"] + [t for t in r["tags"] if t in ("Crowded longs", "Shorts paying", "Leverage heavy")]
        else:
            r["oi_to_mcap"] = None
    return {
        "exchanges": venues.get("cex_exchanges") or [],
        "perps": perps[:40],
        "dex_trending": sorted(dex_pool_signals(venues.get("dex_trending")), key=lambda p: -p["score"])[:25],
        "dex_new": sorted(dex_pool_signals(venues.get("dex_new"), min_liq=25_000), key=lambda p: -(p["vol_24h"] or 0))[:15],
        "dex_volumes": vols,
        # CoinGecko's global volume already includes the DEX pairs it tracks, so this is a share of the whole.
        "dex_share": min(dex_spot / cex_spot * 100, 100) if dex_spot and cex_spot else None,
        "total_oi": sum((r["oi_usd"] or 0) + (r["hl_oi_usd"] or 0) for r in perps),
        "crowded": [r for r in perps if "Crowded longs" in r["tags"]][:8],
        "squeeze": [r for r in perps if "Shorts paying" in r["tags"]][:8],
    }
