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
            0.30 * clamp((prem or 0) / 30)
            + 0.25 * clamp(s["ema_tao_flow"] * 7200 / 500)  # daily EMA net flow vs 500 TAO
            + 0.20 * clamp(((em_to_mcap or 0) - 50) / 100)
            + 0.15 * clamp((ch_7d or 0) / 40)
            + 0.10 * clamp(math.log10(max(s["tao_liquidity"], 1) / 3000))
        )
        tags = []
        if s["ema_tao_flow"] > 0 and (prem or 0) > 5:
            tags.append("Inflows + above EMA")
        if s["ema_tao_flow"] < 0:
            tags.append("Net outflows")
        if em_to_mcap and em_to_mcap > 150 and s["ema_tao_flow"] >= 0:
            tags.append("Cheap vs emissions")
        if (prem or 0) < -20:
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
            str(s["netuid"]): {"price": s["price_tao"], "volume": s["volume_total_tao"], "liq": s["tao_liquidity"]}
            for s in sig["subnets"]
        },
    }
