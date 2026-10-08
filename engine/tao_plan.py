"""Rules-based trading plan for TAO and subnet alpha, evaluated on every refresh.

Research output only: it says which subnets meet the plan's entry, trim and exit
rules right now, and how large a position the rules allow. Nothing here places
trades. Parameters live in data/tao_plan.json.
"""

import json
from pathlib import Path

PARAMS_FILE = Path(__file__).resolve().parent.parent / "data" / "tao_plan.json"

DEFAULTS = {
    "allocation_pct": {"core_tao": 60, "subnet_basket": 30, "reserve": 10},
    "basket_cap_pct": {"risk_on": 30, "neutral": 20, "risk_off": 10},
    "max_positions": 8, "position_max_pct": 5, "tranches": 3, "max_pool_share_pct": 1.0,
    "min_pool_tao": 5000, "min_age_days": 60, "prune_zone_pct": 15,
    "entry_max_premium": 8, "leader_min_premium": -3, "value_min_premium": -10, "value_max_premium": 2,
    "value_min_em_to_mcap": 15, "leader_top_em_rank": 30, "trim_premium": 15, "trim_7d": 40, "stop_loss_pct": 30,
    "basket_drawdown_halve_pct": 25, "funding_hot_8h": 0.05,
}


def params():
    p = dict(DEFAULTS)
    try:
        p.update({k: v for k, v in json.loads(PARAMS_FILE.read_text()).items() if not k.startswith("_")})
    except (OSError, ValueError):
        pass
    return p


def _pc(v):
    """Signed percent with one decimal, without a "-0.0"."""
    return f"{(v if abs(v) >= 0.05 else 0):+.1f}"


def _ma(series, n):
    pts = [x for x in (series or []) if x]
    return sum(pts[-n:]) / len(pts[-n:]) if len(pts) >= n else None


def tao_regime(tao, eco, perps, p):
    """Core TAO stance from trend, network flow and perp funding."""
    hist = tao.get("history") or []
    price = tao.get("price")
    ma30, ma90 = _ma(hist, 30), _ma(hist, 90) or _ma(hist, len(hist) or 1)
    reasons, score = [], 0
    if price and ma30 and ma90:
        if price > ma30 > ma90:
            score += 2; reasons.append(f"Uptrend: price is above its 30-day average (${ma30:,.0f}), which is above the 90-day (${ma90:,.0f}).")
        elif price < ma30 < ma90:
            score -= 2; reasons.append(f"Downtrend: price is below its 30-day average (${ma30:,.0f}), which is below the 90-day (${ma90:,.0f}).")
        else:
            reasons.append(f"Mixed trend: 30-day average ${ma30:,.0f}, 90-day ${ma90:,.0f}.")
    flow = (eco or {}).get("net_daily_flow_tao")
    if flow is not None:
        if flow > 0:
            score += 1; reasons.append(f"Net TAO is flowing into subnets ({flow:,.0f} τ/day across the network).")
        else:
            score -= 1; reasons.append(f"Net TAO is leaving subnets ({flow:,.0f} τ/day across the network).")
    f8 = next((r.get("funding_8h") for r in perps or [] if r.get("funding_8h") is not None), None)
    hot = f8 is not None and f8 > p["funding_hot_8h"]
    if hot:
        score -= 1; reasons.append(f"Perp funding is hot ({f8:.3f}% per 8h): leveraged longs are crowded, so don't chase.")
    elif f8 is not None and f8 < 0:
        score += 1; reasons.append(f"Perp funding is negative ({f8:.3f}% per 8h): shorts are paying, a better backdrop for adds.")

    label = "risk_on" if score >= 2 else "risk_off" if score <= -2 else "neutral"
    stance = {
        "risk_on": "Hold the full core. Add on pullbacks toward the 30-day average; the subnet basket can run at its full cap.",
        "neutral": "Hold the core and add only on the regular DCA schedule. Keep the subnet basket below its full cap.",
        "risk_off": "No new core adds above the DCA schedule. Hold the reserve, keep the subnet basket small and favour exits over entries.",
    }[label]
    return {
        "label": label, "title": {"risk_on": "Risk-on", "neutral": "Neutral", "risk_off": "Risk-off"}[label],
        "stance": stance, "reasons": reasons, "basket_cap_pct": p["basket_cap_pct"][label],
        "ma30": ma30, "ma90": ma90, "funding_8h": f8, "hot": hot,
    }


def evaluate(subnets, prev, p):
    """Plan status for every subnet: entry setup, hold, trim, exit or avoid, with reasons."""
    live = [s for s in subnets if s["netuid"] != 0]
    by_em = sorted(live, key=lambda s: -s["em_share"])
    em_rank = {s["netuid"]: i + 1 for i, s in enumerate(by_em)}
    # Pruning removes the subnets the market prices lowest, so treat the bottom slice as a no-go zone.
    by_price = sorted(live, key=lambda s: s.get("moving_price_tao") or s["price_tao"])
    prune_n = max(1, round(len(by_price) * p["prune_zone_pct"] / 100))
    prune = {s["netuid"] for s in by_price[:prune_n]}

    rows = []
    for s in live:
        prem = s.get("ema_premium") or 0
        flow = s.get("daily_flow_tao") or 0
        pf = ((prev or {}).get(str(s["netuid"])) or {}).get("flow")
        rising = pf is not None and flow > pf
        e2m = s.get("em_to_mcap") or 0
        avoid, exits, trims, why = [], [], [], []

        if s["em_share"] <= 0:
            exits.append("Earning no emissions (negative flow, below the emission gate, or halted).")
        if flow <= 0:
            exits.append(f"Net outflows ({flow:,.0f} τ/day): flow-based emissions stop while this lasts.")
        if s["netuid"] in prune:
            exits.append(f"In the bottom {p['prune_zone_pct']}% by price, where pruning happens.")
        if s["tao_liquidity"] < p["min_pool_tao"]:
            avoid.append(f"Pool is only {s['tao_liquidity']:,.0f} τ; the plan needs {p['min_pool_tao']:,} τ to exit cleanly.")
        if s.get("age_days") is not None and s["age_days"] < p["min_age_days"]:
            avoid.append(f"Only {s['age_days']:.0f} days old; the plan waits {p['min_age_days']} days for a track record.")
        if prem > p["trim_premium"]:
            trims.append(f"{prem:+.0f}% above its moving price, stretched past the +{p['trim_premium']}% trim line.")
        if (s.get("ch_7d") or 0) > p["trim_7d"]:
            trims.append(f"Up {s['ch_7d']:.0f}% in 7 days, past the +{p['trim_7d']}% trim line.")

        setup = None
        if not exits and not avoid:
            if (em_rank[s["netuid"]] <= p["leader_top_em_rank"] and p["leader_min_premium"] <= prem <= p["entry_max_premium"]
                    and (s.get("ch_7d") is None or s["ch_7d"] > 0)):
                setup = "A · Flow leader"
                why.append(f"Top {p['leader_top_em_rank']} by emissions (#{em_rank[s['netuid']]}) with inflows of {flow:,.0f} τ/day.")
                why.append(f"Price is {_pc(prem)}% vs its moving price, inside the entry band ({p['leader_min_premium']}% to +{p['entry_max_premium']}%).")
            elif e2m >= p["value_min_em_to_mcap"] and p["value_min_premium"] <= prem <= p["value_max_premium"]:
                setup = "B · Value with inflows"
                why.append(f"Emissions run at {e2m:.0f}% of market cap a year, a high yield for holders.")
                why.append(f"Price is {_pc(prem)}% vs its moving price, still near or below it, and flows are positive.")
            if setup and rising:
                why.append("Flow is rising since the last refresh.")
            if setup and trims:
                setup, why = None, []  # never open into a stretched move

        if exits:
            status = "Exit / avoid"
        elif avoid:
            status = "Avoid"
        elif trims:
            status = "Trim"
        elif setup:
            status = "Entry"
        else:
            status = "Hold / watch"

        price = s["price_tao"]
        if setup:
            short = (f"#{em_rank[s['netuid']]} by emissions" if setup.startswith("A") else f"yield {e2m:,.0f}%/yr") + \
                f" · {_pc(prem)}% vs EMA · inflows{' rising' if rising else ''}"
        else:
            short = (exits + avoid + trims or ["Meets the hold rules; no entry setup right now."])[0]
        rows.append({
            "netuid": s["netuid"], "name": s["name"], "status": status, "setup": setup, "short": short,
            "reasons": why or trims or exits or avoid or ["Meets the hold rules; no entry setup right now."],
            "warnings": (exits + avoid + trims) if setup is None else [],
            "price_tao": price, "ema_premium": s.get("ema_premium"), "daily_flow_tao": flow, "flow_rising": rising,
            "em_share": s["em_share"], "em_rank": em_rank[s["netuid"]], "em_to_mcap": s.get("em_to_mcap"),
            "pool_tao": s["tao_liquidity"], "ch_7d": s.get("ch_7d"),
            # Position cap from pool depth: buying x τ into a pool holding T τ moves price by about x/T.
            "pool_cap_tao": s["tao_liquidity"] * p["max_pool_share_pct"] / 100,
            "stop_tao": price * (1 - p["stop_loss_pct"] / 100),
            "trim_tao": (s.get("moving_price_tao") or price) * (1 + p["trim_premium"] / 100),
        })

    order = {"Entry": 0, "Trim": 1, "Hold / watch": 2, "Exit / avoid": 3, "Avoid": 4}
    rows.sort(key=lambda r: (order[r["status"]], -(r["daily_flow_tao"] or 0)))
    entries = [r for r in rows if r["status"] == "Entry"]
    # Rank entries: leaders first, then by flow, and keep only as many as the plan allows positions.
    entries.sort(key=lambda r: (0 if r["setup"].startswith("A") else 1, -(r["daily_flow_tao"] or 0)))
    return rows, entries[: p["max_positions"]]


def build(sig, tao, history):
    p = params()
    prev = history[-2]["subnets"] if history and len(history) >= 2 else {}
    regime = tao_regime(tao or {}, sig.get("eco"), sig.get("tao_perps"), p)
    rows, entries = evaluate(sig["subnets"], prev, p)
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"params": p, "regime": regime, "rows": rows, "entries": entries, "counts": counts}
