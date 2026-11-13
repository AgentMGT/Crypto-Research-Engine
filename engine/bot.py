"""Paper trading bot. On each refresh it trades two simulated accounts by fixed rules:
a crypto account (USD) on the crypto signals, and a Bittensor account (TAO) that follows
the trading plan. With ANTHROPIC_API_KEY set, Claude reviews every proposed new entry and
can skip it, with a written reason; exits (stops, targets, plan exits) are never vetoed.

Nothing here talks to an exchange or a wallet. State lives in data/bot/state.json and is
committed with the history snapshots; the site reads a copy as bot.json.
"""

import json
import math
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "data" / "bot.json"
STATE = ROOT / "data" / "bot" / "state.json"
MODEL = "claude-opus-5-5"
KEEP_TRADES, KEEP_EQUITY, KEEP_TICKS = 600, 500, 60
# Structured copy of this tick's fills, for engine/live.py to mirror. Reset on every run.
ORDERS = []


def now_ms():
    return int(time.time() * 1000)


def config():
    return json.loads(CONFIG.read_text())


def load_state(cfg):
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except ValueError:
            pass
    t = now_ms()
    return {
        "v": 1, "created": t, "last_tick": None, "ticks": [],
        "crypto": {"start": cfg["crypto"]["start_usd"], "cash": cfg["crypto"]["start_usd"], "positions": [], "trades": [], "equity": [], "next_id": 1},
        "tao": {"start": cfg["tao"]["start_tao"], "cash": cfg["tao"]["start_tao"], "positions": {}, "trades": [], "equity": []},
    }


# ------------------------------------------------------------------ crypto account

def _slip(notional, vol24):
    return min(0.2, 10 * notional / vol24) if vol24 else 0.02


def crypto_equity(acct, prices):
    return acct["cash"] + sum(p["qty"] * prices.get(p["id"], p["entry"]) for p in acct["positions"])


def _path_since(coin, hours):
    """4-hour closes since the last tick, oldest first, ending at the current price."""
    spark = coin.get("spark") or []
    k = max(1, min(len(spark), math.ceil(hours / 4)))
    return (spark[-k:] if spark else []) + [coin["price"]]


def daily_swing(coin):
    """The coin's typical daily move in %, from the 7-day 4-hour closes (mean absolute daily change)."""
    daily = (coin.get("spark") or [])[5::6]
    moves = [abs(b / a - 1) * 100 for a, b in zip(daily, daily[1:]) if a]
    return sum(moves) / len(moves) if moves else None


def _avg(xs):
    xs = [x for x in xs or [] if x]
    return sum(xs) / len(xs) if xs else None


def regime_confirmed(ticks, regime, allowed, hours, t):
    """True when the current regime and every bot reading in the last `hours` are in `allowed`,
    with at least two earlier readings in that window. Stops one brief Neutral spell inside a
    downtrend from unlocking the trend setup."""
    if regime not in allowed:
        return False
    recent = [x.get("regimes", {}).get("crypto") for x in ticks if t - x["t"] <= hours * 3600000]
    recent = [r for r in recent if r]
    return len(recent) >= 2 and all(r in allowed for r in recent)


def trend_breaker(acct, rk, t):
    """Reason the trend setup is paused, or None: a run of stops in a row, or a bad week."""
    n, last = 0, None
    for x in reversed(acct["trades"]):
        if x["type"] != "Close" or not x["setup"].startswith("Trend"):
            continue
        if x["reason"] != "Stop-loss":
            break
        n += 1
        last = last or x["t"]
    if n >= rk["breaker_losses_in_row"] and t - last < rk["breaker_pause_hours"] * 3600000:
        return f"{n} trend stop-losses in a row; trend entries pause for {rk['breaker_pause_hours']}h after the last one"
    week = [e for e in acct["equity"] if t - e["t"] >= 7 * 86400000]
    if week and acct["equity"]:
        ch = (acct["equity"][-1]["v"] / week[-1]["v"] - 1) * 100
        if ch <= -rk["breaker_week_loss_pct"]:
            return f"Account is {ch:.1f}% over 7 days, past the -{rk['breaker_week_loss_pct']}% limit; trend entries pause."
    return None


def open_risk(acct, prices):
    """USD lost if every open position hit its stop from here. Runners with a stop above entry count only the drop to the stop."""
    return sum(max(0.0, p["qty"] * (prices.get(p["id"], p["entry"]) - p["stop"])) for p in acct["positions"])


def crypto_step(acct, sig, cfg, t, hours, ticks):
    c = cfg["crypto"]
    rk = c.get("risk") or {}
    fee = c["fee_pct"] / 100
    coins = {x["id"]: x for x in sig["coins"]}
    prices = {k: v["price"] for k, v in coins.items()}
    regime = (sig.get("regime") or {}).get("label", "Neutral")
    actions, proposals = [], []
    acct.setdefault("watch", {})

    def close(p, price, reason, rule, frac=1.0):
        coin = coins.get(p["id"]) or {}
        qty, cost = p["qty"] * frac, p["cost"] * frac
        s = _slip(qty * price, coin.get("volume"))
        fill = price * (1 - s)
        proceeds = qty * fill * (1 - fee)
        pnl = proceeds - cost
        acct["cash"] += proceeds
        hi, lo = p.get("hi", p["entry"]), p.get("lo", p["entry"])
        tr = {"type": "Close", "t": t, "pid": p["pid"], "id": p["id"], "symbol": p["symbol"], "setup": p["setup"], "dir": "long",
              "price": fill, "size": proceeds, "pnl": pnl, "pnl_pct": pnl / cost * 100, "cost": cost, "opened": p["opened"],
              "held": t - p["opened"], "reason": reason, "rule": rule, "stop_pct": p["stop_pct"], "size_pct": p["size_pct"], "entry_reason": p["reason"],
              # Best and worst price reached while open, as % from entry: shows whether targets are too tight or stops too close.
              "mfe_pct": (hi / p["entry"] - 1) * 100, "mae_pct": (lo / p["entry"] - 1) * 100}
        if frac < 0.999:
            tr["partial"] = frac
            p["qty"] -= qty
            p["cost"] -= cost
        else:
            acct["positions"].remove(p)
        acct["trades"].append(tr)
        actions.append(f"Sold {'half of ' if frac < 0.999 else ''}{p['symbol']} ({reason}): {pnl:+,.2f} USD")
        ORDERS.append({"account": "crypto", "side": "sell", "id": p["id"], "symbol": p["symbol"], "frac": frac, "reason": reason, "price": price,
                       "stop": p["stop"] if frac < 0.999 else None})

    # Exits: stop and target along the price path since the last tick, then time and signal exits.
    for p in list(acct["positions"]):
        coin = coins.get(p["id"])
        if not coin:
            continue
        key = p.get("key") or next((k for k, s in c["setups"].items() if s["label"] == p["setup"]), "")
        run = (c["setups"].get(key) or {}).get("runner")
        if run and "trail_pct" not in p:  # positions opened before trailing existed
            p["trail_pct"] = min(run["trail_max_pct"], max(run["trail_min_pct"], run["trail_swing_mult"] * (daily_swing(coin) or p["stop_pct"] / 2)))
        stop_before, hit = p["stop"], None
        for px in _path_since(coin, hours):
            p["hi"], p["lo"] = max(p.get("hi", p["entry"]), px), min(p.get("lo", p["entry"]), px)
            if px <= p["stop"]:
                fill = min(px, p["stop"]) if px < p["stop"] * 0.97 else p["stop"]
                hit = (fill, "Trailing stop", "Price fell to the trailing stop after the first target.") if p.get("runner") else \
                      (fill, "Stop-loss", f"Price fell to the {p['stop_pct']}% stop.")
                break
            if not p.get("runner") and px >= p["target"]:
                if not run:
                    hit = (p["target"], "Take-profit", f"Price reached the {p['target_pct']}% target.")
                    break
                # Bank part at the target, then let the rest run under a trailing stop that never drops below entry.
                p["runner"] = True
                p["stop"] = max(p["stop"], p["entry"])
                close(p, p["target"], "Take-profit (half)", f"Price reached the {p['target_pct']}% target; the rest runs with a {p['trail_pct']:.0f}% trailing stop.", run["take_frac"])
            if p.get("runner"):
                p["stop"] = max(p["stop"], p["entry"], p["hi"] * (1 - p["trail_pct"] / 100))
        if hit:
            close(p, hit[0], hit[1], hit[2])
            continue
        if p not in acct["positions"]:
            continue
        hold = run["max_hold_days"] if p.get("runner") and run else p["max_hold_days"]
        if t - p["opened"] > hold * 86400000:
            close(p, coin["price"], "Time stop", f"Held longer than {hold} days without hitting stop or target.")
            continue
        bad = [x for x in c["exit_tags"] if x in (coin.get("tags") or [])]
        if bad:
            close(p, coin["price"], "Signal exit", f"Signal turned: {', '.join(bad)}.")
            continue
        if p.get("runner") and p["stop"] > stop_before * 1.001:
            ORDERS.append({"account": "crypto", "side": "restop", "id": p["id"], "symbol": p["symbol"], "stop": p["stop"], "price": coin["price"]})

    # Entries: rank candidates by setup, propose up to max_new_per_tick.
    held = {p["id"] for p in acct["positions"]}
    # Cool-down: no re-entry into a coin the bot sold recently.
    held |= {x["id"] for x in acct["trades"] if x["type"] == "Close" and t - x["t"] < c.get("cooldown_hours", 24) * 3600000}
    eq = crypto_equity(acct, prices)
    room = c["max_positions"] - len(acct["positions"])
    n_trend = sum(1 for p in acct["positions"] if p["setup"].startswith("Trend"))
    breaker = trend_breaker(acct, rk, t) if rk else None
    blocked = {}  # setup key -> why it can't enter this refresh, for the tick log
    cands, watch = [], {}
    for coin in sig["coins"]:
        if coin["id"] in held or not coin.get("rank") or coin["rank"] > c["max_rank"] or (coin.get("volume") or 0) < c["min_volume_usd"]:
            continue
        tags = coin.get("tags") or []
        if any(x in tags for x in c.get("exclude_tags", [])):
            continue
        for key, s in c["setups"].items():
            if regime not in s["regimes"] or any(x in tags for x in s.get("tags_none", [])):
                continue
            if key == "trend":
                if coin["score"] < s["min_score"] or not any(x in tags for x in s["tags_any"]):
                    continue
                rs = s.get("require") or {}
                avg7 = _avg(coin.get("spark"))
                # Strength against Bitcoin and an intact trend of its own, not a bounce inside a downtrend.
                if ((coin.get("rs7") or 0) < rs.get("rs7_min", -1e9) or (coin.get("rs30") or 0) < rs.get("rs30_min", -1e9)
                        or (coin.get("ch30") or 0) < rs.get("ch30_min", -1e9) or (rs.get("above_7d_avg") and avg7 and coin["price"] < avg7)):
                    continue
                if s.get("regime_confirm_hours") and not regime_confirmed(ticks, regime, s["regimes"], s["regime_confirm_hours"], t):
                    blocked[key] = f"regime not {'/'.join(s['regimes'])} for {s['regime_confirm_hours']}h straight"
                    continue
                if breaker:
                    blocked[key] = breaker
                    continue
                if n_trend >= rk.get("max_trend_positions", 99):
                    blocked[key] = f"already {n_trend} trend positions"
                    continue
                why = f"Score {coin['score']}, tags {', '.join(tags)}; {coin['ch7']:+.1f}% 7d vs BTC {coin['rs7']:+.1f} pts, {coin['ch30']:+.1f}% 30d."
                rank = -coin["score"]
                # Confirmation: the first refresh that qualifies only puts the coin on watch. It is bought on a later
                # refresh only if it still qualifies and has held at or above the price it was flagged at.
                if s.get("confirm"):
                    w = acct["watch"].get(coin["id"])
                    if not w or coin["price"] < w["price"]:
                        watch[coin["id"]] = {"t": t, "price": coin["price"], "symbol": coin["symbol"]}
                        continue
                    watch[coin["id"]] = w
                    why += f" Held above its flag price ${w['price']:.4g} since the last refresh."
            else:
                if coin["rank"] > s.get("max_rank", 999) or coin.get("rsi") is None or coin["rsi"] > s["max_rsi"]:
                    continue
                why = f"4h RSI {coin['rsi']:.0f}, rank #{coin['rank']}, {coin['ch7']:+.1f}% 7d: oversold large cap."
                rank = 1000 + coin["rsi"]
            cands.append((rank, key, s, coin, why))
            break
    # Watch entries last one refresh: a coin that stops qualifying drops off.
    acct["watch"] = watch
    cands.sort(key=lambda x: x[0])
    risk_now = open_risk(acct, prices)
    for _, key, s, coin, why in cands[: max(0, min(room, c["max_new_per_tick"]))]:
        size = eq * s["size_pct"] / 100
        # Volatility sizing: with a fixed stop width, a coin whose normal daily swing is large next to the stop
        # gets stopped by noise more often, so its bet is cut in proportion (never below 25%).
        sw = daily_swing(coin)
        if s.get("vol_size") and sw and sw > s["stop_pct"] * s["vol_size"]["swing_vs_stop"]:
            size *= max(0.25, s["stop_pct"] * s["vol_size"]["swing_vs_stop"] / sw)
            why += f" Size cut: daily swing {sw:.1f}% is large for a {s['stop_pct']}% stop."
        risk = size * s["stop_pct"] / 100
        if rk.get("max_open_risk_pct") and risk_now + risk > eq * rk["max_open_risk_pct"] / 100:
            blocked[key] = f"open risk would pass {rk['max_open_risk_pct']}% of the account"
            continue
        if size > acct["cash"] or size <= 0:
            continue
        risk_now += risk
        proposals.append({"account": "crypto", "asset": coin["symbol"], "setup": s["label"], "size": round(size, 2), "unit": "USD", "rule_reason": why,
                          "context": {k: coin.get(k) for k in ("name", "rank", "price", "ch24", "ch7", "ch30", "rsi", "range7", "turnover", "score", "tags")},
                          "_do": (key, s, coin, why, size)})
    for k, v in blocked.items():
        actions.append(f"{c['setups'][k]['label']}: no entries ({v}).")
    if watch:
        actions.append("Watching for confirmation next refresh: " + ", ".join(w["symbol"] for w in watch.values()) + ".")
    return actions, proposals, regime


def crypto_open(acct, cfg, t, key, s, coin, why, size, ai):
    fee = cfg["crypto"]["fee_pct"] / 100
    sl = _slip(size, coin.get("volume"))
    fill = coin["price"] * (1 + sl)
    qty = size * (1 - fee) / fill
    eq_before = acct["cash"]
    eq = crypto_equity(acct, {coin["id"]: coin["price"]})
    acct["cash"] -= size
    size_pct = size / eq * 100 if eq else s["size_pct"]
    run = s.get("runner")
    sw = daily_swing(coin) or s["stop_pct"] / 2
    p = {"pid": acct["next_id"], "id": coin["id"], "symbol": coin["symbol"], "setup": s["label"], "key": key, "qty": qty, "entry": fill, "cost": size,
         "opened": t, "stop": fill * (1 - s["stop_pct"] / 100), "target": fill * (1 + s["target_pct"] / 100), "stop_pct": s["stop_pct"],
         "target_pct": s["target_pct"], "max_hold_days": s["max_hold_days"], "size_pct": round(size_pct, 2), "reason": why, "ai": ai,
         "hi": fill, "lo": fill, "swing_pct": sw}
    if run:
        p["trail_pct"] = min(run["trail_max_pct"], max(run["trail_min_pct"], run["trail_swing_mult"] * sw))
    acct["next_id"] += 1
    acct["positions"].append(p)
    acct["trades"].append({"type": "Open", "t": t, "pid": p["pid"], "id": coin["id"], "symbol": coin["symbol"], "setup": s["label"], "dir": "long",
                           "price": fill, "size": size, "slip": sl * 100, "reason": why, "ai": ai, "cash_before": eq_before})
    ORDERS.append({"account": "crypto", "side": "buy", "id": coin["id"], "symbol": coin["symbol"], "setup": s["label"], "size_pct": round(size_pct, 2),
                   "stop_pct": s["stop_pct"], "target_pct": s["target_pct"], "price": coin["price"], "volume": coin.get("volume"), "ai": ai})
    return f"Bought {coin['symbol']} ({s['label']}): {size:,.0f} USD"


# ------------------------------------------------------------------ Bittensor account

def _pool(s):
    T, A = s.get("tao_liquidity") or 0, s.get("alpha_in_pool") or 0
    if not (T > 0 and A > 0):
        T = 1e12
        A = T / s["price_tao"]
    return T, A


def tao_equity(acct, subs):
    return acct["cash"] + sum(p["alpha"] * subs[int(k)]["price_tao"] for k, p in acct["positions"].items() if int(k) in subs)


def tao_step(acct, tsig, cfg, t, ts):
    c = cfg["tao"]
    fee = c["fee_pct"] / 100
    plan = tsig.get("plan") or {}
    P, regime = plan.get("params") or {}, plan.get("regime") or {}
    subs = {s["netuid"]: s for s in tsig["subnets"]}
    rows = {r["netuid"]: r for r in plan.get("rows") or []}
    actions, proposals = [], []

    def sell(k, frac, reason, rule):
        p, s = acct["positions"][k], subs[int(k)]
        T, A = _pool(s)
        alpha = p["alpha"] * frac
        gross = T * alpha / (A + alpha)
        net = gross * (1 - fee)
        cost = p["cost"] * frac
        pnl = net - cost
        acct["cash"] += net
        acct["trades"].append({"side": "Sell", "t": t, "netuid": int(k), "name": s["name"], "alpha": alpha, "tao": net, "price": net / alpha,
                               "pnl": pnl, "pnl_pct": pnl / cost * 100, "cost": cost, "opened": p["opened"], "reason": reason, "rule": rule,
                               "setup": p.get("setup"), "frac": frac})
        if frac >= 0.999:
            del acct["positions"][k]
        else:
            p["alpha"] -= alpha
            p["cost"] -= cost
            p["trimmed"] = t
        actions.append(f"Sold {'all' if frac >= 0.999 else 'a third'} of SN{k} ({reason}): {pnl:+.3f} τ")
        ORDERS.append({"account": "tao", "side": "sell", "netuid": int(k), "name": s["name"], "frac": frac, "reason": reason, "price": s["price_tao"]})

    stop = P.get("stop_loss_pct", 30)
    for k in list(acct["positions"]):
        s, r, p = subs.get(int(k)), rows.get(int(k)), acct["positions"][k]
        if not s:
            continue
        avg = p["cost"] / p["alpha"]
        gain = (s["price_tao"] / avg - 1) * 100
        if r and r["status"] == "Exit / avoid":
            sell(k, 1.0, "Plan exit", r["short"])
        elif s["price_tao"] <= avg * (1 - stop / 100):
            sell(k, 1.0, "Stop-loss", f"Price is {gain:.0f}% from the average entry; the plan's stop is {stop}%.")
        elif p.get("profit_trimmed") and c.get("breakeven_after_profit_trim") and s["price_tao"] <= avg:
            sell(k, 1.0, "Break-even stop", "Back to the average entry after the profit trim; the rest is sold so a winner can't turn into a loss.")
        elif c.get("profit_trim_pct") and not p.get("profit_trimmed") and gain >= c["profit_trim_pct"]:
            p["profit_trimmed"] = t
            sell(k, 1 / 3, "Profit trim", f"Up {gain:.0f}% from the average entry, past the +{c['profit_trim_pct']}% profit trim; the stop on the rest moves to break-even.")
        elif r and r["status"] == "Trim" and (not p.get("trimmed") or t - p["trimmed"] > c["retrim_days"] * 86400000):
            sell(k, 1 / 3, "Plan trim", r["short"])

    eq = tao_equity(acct, subs)
    basket = eq - acct["cash"]
    basket_max = eq * (regime.get("basket_cap_pct") or 20) / 100
    n_new = 0
    for r in plan.get("entries") or []:
        if n_new >= c["max_new_per_tick"]:
            break
        k, s = str(r["netuid"]), subs.get(r["netuid"])
        if not s:
            continue
        if any(x["side"] == "Sell" and x["netuid"] == r["netuid"] and t - x["t"] < c.get("cooldown_hours", 24) * 3600000 for x in acct["trades"]):
            continue
        p = acct["positions"].get(k)
        pos_max = min(eq * P.get("position_max_pct", 5) / 100, s["tao_liquidity"] * P.get("max_pool_share_pct", 1) / 100)
        tranche = pos_max / P.get("tranches", 3)
        if p and (p["tranches"] >= P.get("tranches", 3) or p.get("last_buy") == ts):
            continue
        if not p and len(acct["positions"]) >= P.get("max_positions", 8):
            continue
        if (p["cost"] if p else 0) + tranche > pos_max * 1.001 or basket + tranche > basket_max or tranche > acct["cash"] or tranche <= 0:
            continue
        basket += tranche
        n_new += 1
        proposals.append({"account": "tao", "asset": f"SN{r['netuid']} {r['name']}", "setup": r["setup"], "size": round(tranche, 4), "unit": "TAO",
                          "rule_reason": " ".join(r["reasons"]), "tranche": (p["tranches"] + 1) if p else 1,
                          "context": {k2: r.get(k2) for k2 in ("ema_premium", "daily_flow_tao", "flow_rising", "em_rank", "em_to_mcap", "pool_tao", "ch_7d")},
                          "_do": (k, tranche, r)})
    return actions, proposals, regime.get("title")


def tao_open(acct, tsig, cfg, t, ts, k, tranche, r, ai):
    fee = cfg["tao"]["fee_pct"] / 100
    s = next(x for x in tsig["subnets"] if x["netuid"] == int(k))
    T, A = _pool(s)
    net = tranche * (1 - fee)
    alpha = A * net / (T + net)
    acct["cash"] -= tranche
    p = acct["positions"].setdefault(k, {"alpha": 0.0, "cost": 0.0, "opened": t, "tranches": 0, "setup": r["setup"]})
    p["alpha"] += alpha
    p["cost"] += tranche
    p["tranches"] += 1
    p["last_buy"] = ts
    acct["trades"].append({"side": "Buy", "t": t, "netuid": int(k), "name": s["name"], "alpha": alpha, "tao": tranche, "price": tranche / alpha,
                           "slip": (tranche / alpha / s["price_tao"] - 1) * 100, "setup": r["setup"], "tranche": p["tranches"],
                           "reason": " ".join(r["reasons"]), "ai": ai})
    ORDERS.append({"account": "tao", "side": "buy", "netuid": int(k), "name": s["name"], "setup": r["setup"], "tranche": p["tranches"],
                   "price": s["price_tao"], "pool_tao": s.get("tao_liquidity"), "ai": ai})
    return f"Bought SN{k} tranche {p['tranches']} ({r['setup']}): {tranche:.3f} τ"


# ------------------------------------------------------------------ AI review of entries

SYSTEM = """You are the risk reviewer for a paper trading bot (simulated money only). \
Each refresh, fixed rules propose new entries. For each proposal decide "take" or "skip". \
Skip when the data shows a concrete problem the rule missed: the move looks exhausted or news-driven, \
liquidity is thin for the size, it duplicates exposure the bot already has, the regime argues against it, \
or the evidence is contradictory. Otherwise take it; the rules are the strategy, you are the check, so do \
not skip just because outcomes are uncertain. Ground every reason in the numbers given, in one or two sentences. \
confidence is 1 (coin flip) to 5 (clear). Also write a two-sentence note on what the bot is doing this refresh."""

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["note", "decisions"],
    "properties": {
        "note": {"type": "string"},
        "decisions": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "verdict", "reason", "confidence"],
            "properties": {"id": {"type": "integer"}, "verdict": {"type": "string", "enum": ["take", "skip"]},
                           "reason": {"type": "string"}, "confidence": {"type": "integer"}}}},
    },
}


def ai_review(proposals, context):
    """Returns ({index: decision}, note, model) or (None, None, None) without a key or on failure."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key or not proposals:
        return None, None, None
    try:
        import anthropic
    except ImportError:
        return None, None, None
    body = {"context": context, "proposals": [{"id": i, **{k: v for k, v in p.items() if not k.startswith("_")}} for i, p in enumerate(proposals)]}
    req = dict(model=MODEL, max_tokens=16000, system=SYSTEM,
               output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
               messages=[{"role": "user", "content": "Review these proposed paper trades.\n\n" + json.dumps(body, default=str)}])
    client = anthropic.Anthropic()
    try:
        try:
            with client.messages.stream(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **req) as st:
                msg = st.get_final_message()
        except TypeError:
            with client.messages.stream(**req) as st:
                msg = st.get_final_message()
    except Exception as e:
        print(f"  bot AI review failed: {e}")
        return None, None, None
    if msg.stop_reason in ("refusal", "max_tokens"):
        print(f"  bot AI review stopped: {msg.stop_reason}")
        return None, None, None
    try:
        out = json.loads("".join(b.text for b in msg.content if b.type == "text"))
    except ValueError:
        return None, None, None
    return {d["id"]: d for d in out["decisions"]}, out["note"], getattr(msg, "model", MODEL)


# ------------------------------------------------------------------ run

def run(crypto_sig, tao_sig, ts):
    cfg = config()
    state = load_state(cfg)
    t = now_ms()
    last = state.get("last_tick")
    gap = float(os.environ.get("BOT_MIN_HOURS", cfg["min_hours_between_ticks"]))
    if last and t - last < gap * 3600000:
        print(f"  bot: last tick {(t - last) / 3600000:.1f}h ago, waiting for {gap:g}h")
        return None
    hours = (t - last) / 3600000 if last else 4
    ORDERS.clear()
    actions, proposals, regimes = [], [], {}
    if crypto_sig and crypto_sig.get("coins"):
        a, p, regimes["crypto"] = crypto_step(state["crypto"], crypto_sig, cfg, t, hours, state.get("ticks") or [])
        actions += a
        proposals += p
    if tao_sig and tao_sig.get("subnets") and tao_sig.get("plan"):
        a, p, regimes["tao"] = tao_step(state["tao"], tao_sig, cfg, t, ts)
        actions += a
        proposals += p

    context = {
        "crypto_regime": (crypto_sig or {}).get("regime", {}).get("label") if crypto_sig else None,
        "crypto_regime_reasons": (crypto_sig or {}).get("regime", {}).get("reasons") if crypto_sig else None,
        "tao_plan_regime": ((tao_sig or {}).get("plan") or {}).get("regime", {}).get("title") if tao_sig else None,
        "crypto_holdings": [p["symbol"] for p in state["crypto"]["positions"]],
        "tao_holdings": [f"SN{k}" for k in state["tao"]["positions"]],
        "exits_this_refresh": actions,
        "headlines": [h.get("title") for h in ((crypto_sig or {}).get("news") or {}).get("crypto", [])[:15]],
    }
    decisions, note, model = ai_review(proposals, context)
    skipped = []
    for i, p in enumerate(proposals):
        d = (decisions or {}).get(i)
        ai = {"verdict": d["verdict"], "reason": d["reason"], "confidence": d["confidence"], "model": model} if d else None
        if ai and ai["verdict"] == "skip":
            skipped.append({"asset": p["asset"], "setup": p["setup"], "size": p["size"], "unit": p["unit"], "rule_reason": p["rule_reason"], "ai": ai})
            continue
        if p["account"] == "crypto":
            actions.append(crypto_open(state["crypto"], cfg, t, *p["_do"], ai))
        else:
            actions.append(tao_open(state["tao"], tao_sig, cfg, t, ts, *p["_do"], ai))

    if crypto_sig and crypto_sig.get("coins"):
        prices = {c["id"]: c["price"] for c in crypto_sig["coins"]}
        state["crypto"]["equity"].append({"t": t, "v": round(crypto_equity(state["crypto"], prices), 2)})
        for p in state["crypto"]["positions"]:
            p["mark"] = prices.get(p["id"], p.get("mark"))
    if tao_sig and tao_sig.get("subnets"):
        subs = {s["netuid"]: s for s in tao_sig["subnets"]}
        state["tao"]["equity"].append({"t": t, "v": round(tao_equity(state["tao"], subs), 4)})
        for k, p in state["tao"]["positions"].items():
            if int(k) in subs:
                p["mark"] = subs[int(k)]["price_tao"]
                p["name"] = subs[int(k)]["name"]
    for k in ("crypto", "tao"):
        state[k]["trades"] = state[k]["trades"][-KEEP_TRADES:]
        state[k]["equity"] = state[k]["equity"][-KEEP_EQUITY:]
    state["ticks"] = (state["ticks"] + [{"t": t, "ts": ts, "regimes": regimes, "actions": actions, "skipped": skipped,
                                         "ai_note": note, "ai_model": model, "ai": decisions is not None, "orders": list(ORDERS)}])[-KEEP_TICKS:]
    state["last_tick"] = t
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, default=str, separators=(",", ":")))
    print(f"  bot: {len(actions)} actions, {len(skipped)} skipped by AI" + ("" if decisions is not None else " (no AI review)"))
    return state
