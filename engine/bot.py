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


# ------------------------------------------------------------------ altcoin account

def alt_prices(alt, held_ids):
    """Price, volume and scan record for every coin the scan saw, plus a CoinGecko lookup for held
    coins it didn't (a winner that grew past the scan's cap band, or one that collapsed below it)."""
    seen = {}
    for v in (alt or {}).get("universe") or []:
        seen[v["id"]] = v
    for key in ("pregrad", "utility", "meme"):
        for v in (alt or {}).get(key) or []:
            seen[v["id"]] = {**seen.get(v["id"], {}), **v, "checked": True}
    missing = [i for i in held_ids if not (seen.get(i) or {}).get("price")]
    if missing:
        try:
            from .fetch_crypto import CG, CG_HEADERS
            from .http import get_json
            got = get_json(f"{CG}/simple/price", params={"ids": ",".join(missing), "vs_currencies": "usd", "include_24hr_vol": "true"},
                           headers=CG_HEADERS, retries=2)
            for i, x in (got or {}).items():
                if x.get("usd"):
                    seen[i] = {**seen.get(i, {"id": i}), "price": x["usd"], "volume": x.get("usd_24h_vol")}
        except Exception as e:
            print(f"  bot alt prices: {e}")
    return seen


def alt_equity(acct, seen):
    return acct["cash"] + sum(p["qty"] * ((seen.get(p["id"]) or {}).get("price") or p.get("mark") or p["entry"]) for p in acct["positions"])


def _alt_blocked(v, a):
    return [f for f in v.get("flags") or [] if any(f.startswith(b) for b in a["block_flags"])]


def alt_fit(v, a):
    """Marvin's five requirements for an altcoin, checked against the scan's evidence.
    Returns (missing, pluses): a coin is bought only when nothing is missing; pluses rank the ones that pass.
    An unknown (no data) counts as missing: the account only buys what the data shows."""
    f = a["fit"]
    ok = {c.get("key"): c.get("ok") for c in v.get("checks") or []}
    d = v.get("detail") or {}
    missing, pluses = [], []
    # 1. Low liquidity: little traded yet, but enough to get out.
    if not (ok.get("liquidity") and f["min_volume_usd"] <= (v.get("volume") or 0) <= f["max_volume_usd"]):
        missing.append("low liquidity")
    # 2. Solves a real-world problem, with a product that is live (not a promise, not a meme).
    if v.get("meme") or not (ok.get("use") and ok.get("live")):
        missing.append("real-world use")
    # 3. Hair-on-fire demand: usage growing fast (money locked up sharply), or a busy ecosystem the market hasn't priced.
    demand = []
    if (v.get("tvl") or 0) >= f["demand_min_tvl_usd"] and (v.get("tvl_ch30") or 0) >= f["demand_tvl_growth_30d_pct"]:
        demand.append(f"usage up {v['tvl_ch30']:.0f}% in 30 days")
    if ok.get("quiet_eco"):
        demand.append("busy ecosystem, little trading")
    if not demand:
        missing.append("strong demand")
    # 4. High exclusivity: scarce supply (most of it already circulating), no big unlocks hanging over it.
    if not ok.get("exclusive"):
        missing.append("exclusivity")
    # 5. Room to grow, including a major-exchange listing: tradable today, not yet on the big exchanges, already outrunning BTC.
    cex = [x.lower() for x in d.get("cex") or []]
    on_major = [m for m in f["major_exchanges"] if any(m in x for x in cex)]
    if not d or on_major or (d.get("cex_count") or 0) > f["max_cex_count"] or (v.get("rs30") is None) or v["rs30"] < f["min_rs30"]:
        missing.append("growth / listing room" + (f" (already on {', '.join(on_major)})" if on_major else ""))
    for key, label in (("small", "small cap"), ("platform", "powers a platform"), ("servers", "compute/servers"), ("staking", "staking"), ("partners", "partners")):
        if ok.get(key):
            pluses.append(label)
    if v.get("buying_up"):
        pluses.append("buying picking up")
    if (d.get("watchers") or 0) >= f["attention_watchers"]:
        pluses.append(f"{d['watchers']:,} watchlists")
    return missing, demand + pluses


def alt_step(acct, alt, cfg, t, regime):
    a = cfg["alt"]
    fee = a["fee_pct"] / 100
    held_ids = [p["id"] for p in acct["positions"]]
    seen = alt_prices(alt, held_ids)
    fresh = bool(alt) and not alt.get("stale")
    actions, proposals = [], []

    def sell(p, frac, price, reason, rule):
        v = seen.get(p["id"]) or {}
        qty, cost = p["qty"] * frac, p["cost"] * frac
        s = _slip(qty * price, v.get("volume"))
        fill = price * (1 - s)
        proceeds = qty * fill * (1 - fee)
        pnl = proceeds - cost
        acct["cash"] += proceeds
        tr = {"type": "Close", "t": t, "pid": p["pid"], "id": p["id"], "symbol": p["symbol"], "setup": p["setup"], "dir": "long",
              "price": fill, "size": proceeds, "pnl": pnl, "pnl_pct": pnl / cost * 100, "cost": cost, "opened": p["opened"],
              "held": t - p["opened"], "reason": reason, "rule": rule, "size_pct": p["size_pct"], "entry_reason": p["reason"],
              "multiple": price / p["entry"], "mfe_pct": (p.get("hi", p["entry"]) / p["entry"] - 1) * 100,
              "mae_pct": (p.get("lo", p["entry"]) / p["entry"] - 1) * 100}
        if frac < 0.999:
            tr["partial"] = frac
            p["qty"] -= qty
            p["cost"] -= cost
        else:
            acct["positions"].remove(p)
        acct["trades"].append(tr)
        actions.append(f"Sold {'all' if frac >= 0.999 else f'{frac:.0%}'} of {p['symbol']} ({reason}): {pnl:+,.2f} USD")

    # Exits: take-profit ladder, thesis breaks, meme volume drying up, no price at all.
    for p in list(acct["positions"]):
        v = seen.get(p["id"]) or {}
        price = v.get("price")
        if not price:
            p["no_price"] = p.get("no_price", 0) + 1
            if p["no_price"] >= a["no_price_ticks"]:
                sell(p, 1.0, (p.get("mark") or p["entry"]) * 0.5, "No price",
                     f"No price for {p['no_price']} refreshes in a row (possible delisting); written down to half the last price.")
            continue
        p["no_price"], p["mark"] = 0, price
        p["hi"], p["lo"] = max(p.get("hi", p["entry"]), price), min(p.get("lo", p["entry"]), price)
        mult = price / p["entry"]
        # Take-profit ladder: at 2x sell half (the stake back), then a quarter of what's left at 5x and at 10x.
        while p["tp_done"] < len(a["take_profits"]) and mult >= a["take_profits"][p["tp_done"]][0]:
            x, frac = a["take_profits"][p["tp_done"]]
            p["tp_done"] += 1
            sell(p, frac, price, f"Take-profit {x:g}x", f"Price reached {x:g}x the average entry; sold {frac:.0%} of the position.")
        if p not in acct["positions"]:
            continue
        te = a["thesis_exit"]
        why = []
        if v.get("checked") and v.get("score") is not None and v["score"] < te["score_below"]:
            why.append(f"scan score fell to {v['score']} (entry {p['entry_score']}, exit below {te['score_below']})")
        bad = [f for f in v.get("flags") or [] if any(f.startswith(b) for b in te["flags"])]
        if bad:
            why.append("; ".join(bad))
        liq = ((v.get("pool") or {}).get("total_liq") or (v.get("pool") or {}).get("liquidity"))
        if liq and p.get("entry_liq") and liq < p["entry_liq"] * (1 - te["pool_liq_drop_pct"] / 100):
            why.append(f"DEX liquidity down {(1 - liq / p['entry_liq']) * 100:.0f}% since entry (liquidity pulled)")
        if why:
            sell(p, 1.0, price, "Thesis exit", "Thesis broke: " + "; ".join(why) + ".")
            continue
        if p["meme"] and p.get("entry_volume") and v.get("volume") is not None:
            if v["volume"] < p["entry_volume"] * a["meme_volume_dry_pct"] / 100:
                p.setdefault("dry_since", t)
                if t - p["dry_since"] > a["meme_volume_dry_days"] * 86400000:
                    sell(p, 1.0, price, "Volume dried up", f"Meme coin volume under {a['meme_volume_dry_pct']}% of its entry level for {a['meme_volume_dry_days']} days.")
                    continue
            else:
                p.pop("dry_since", None)

    if not fresh:
        actions.append("Altcoin scan didn't refresh this run: no new altcoin buys.")
        return actions, proposals, seen

    # Entries: second halves first (same plan, a few days later, story intact), then new coins.
    eq = alt_equity(acct, seen)
    for p in acct["positions"]:
        v = seen.get(p["id"]) or {}
        if p["halves"] >= 2 or p["tp_done"] or t - p["last_buy"] < a["second_half_after_days"] * 86400000 or not v.get("checked"):
            continue
        if v.get("score") is None or v["score"] < p["entry_score"] - a["second_half_max_score_drop"] or _alt_blocked(v, a):
            if t - p["last_buy"] > a["second_half_window_days"] * 86400000:
                p["halves"] = 2  # window passed with the story weaker: skip the second half for good
                actions.append(f"Skipped the second half of {p['symbol']}: the story weakened (score {v.get('score')} vs {p['entry_score']} at entry).")
            continue
        size = min(p["first_size"], acct["cash"])
        if size <= 0:
            continue
        proposals.append({"account": "alt", "asset": p["symbol"], "setup": p["setup"] + " · second half", "size": round(size, 2), "unit": "USD",
                          "rule_reason": f"Second half, {(t - p['last_buy']) / 86400000:.0f} days after the first; score {v['score']} (entry {p['entry_score']}).",
                          "context": {k: v.get(k) for k in ("name", "price", "mcap", "volume", "ch7", "ch30", "score", "passed_pct", "flags", "meme")},
                          "_do": ("second", p, v, size, f"Second half, score {v['score']} (entry {p['entry_score']}).")})

    held = {p["id"] for p in acct["positions"]}
    held |= {x["id"] for x in acct["trades"] if x["type"] == "Close" and t - x["t"] < a["cooldown_days"] * 86400000}
    n_meme = sum(1 for p in acct["positions"] if p["meme"])
    room = a["max_positions"] - len(acct["positions"])
    cands, near = [], []
    lists = (("pregrad", "Pre-graduation"), ("utility", "Utility")) + ((("meme", "Meme"),) if a.get("allow_meme") else ())
    for key, label in lists:
        for v in (alt.get(key) or [])[: a["top_n"]]:
            # The original rules first: top of its list, score, checklist, volume, no red flags.
            if v["id"] in held or not v.get("price") or (v.get("volume") or 0) < a["min_volume_usd"]:
                continue
            if (v.get("score") or 0) < a["min_score"] or (v.get("passed_pct") or 0) < a["min_pass_pct"] or _alt_blocked(v, a):
                continue
            held.add(v["id"])
            if a.get("fit"):
                miss, plus = alt_fit(v, a)
                if miss:
                    if len(miss) == 1:
                        near.append(f"{v['symbol']} (missing {miss[0]})")
                    continue
                v = {**v, "_plus": plus}
            cands.append((key, label, v))
    # Best evidence first: most pluses, then the scan score.
    cands.sort(key=lambda c: (-len(c[2].get("_plus") or []), -(c[2].get("score") or 0)))
    n_new = 0
    for key, label, v in cands:
        if n_new >= min(room, a["max_new_per_tick"]):
            break
        if v.get("meme") and n_meme >= a["max_meme_positions"]:
            continue
        size = eq * a["position_pct"]["meme" if v.get("meme") else "utility"] / 100 / 2  # first of two halves
        if size > acct["cash"] or size <= 0:
            continue
        passed = [c["label"] for c in v.get("checks") or [] if c.get("ok")]
        why = (f"{label}{' (meme)' if v.get('meme') and key != 'meme' else ''}: scan score {v['score']}, passes {v['passed_pct']}% of the checklist"
               + (f" ({', '.join(passed[:4])})" if passed else "") + f"; mcap ${v.get('mcap', 0) / 1e6:,.1f}M, 24h volume ${v.get('volume', 0) / 1e6:,.2f}M.")
        if v.get("_plus"):
            why += " Fits all five requirements; extra evidence: " + ", ".join(v["_plus"]) + "."
        if key == "pregrad" and v.get("buying_why"):
            why += " Buying picking up: " + "; ".join(v["buying_why"][:2]) + "."
        n_new += 1
        n_meme += 1 if v.get("meme") else 0
        proposals.append({"account": "alt", "asset": v["symbol"], "setup": f"Altcoin: {label.lower()}", "size": round(size, 2), "unit": "USD", "rule_reason": why,
                          "context": {k: v.get(k) for k in ("name", "price", "mcap", "fdv_ratio", "volume", "turnover", "ch7", "ch30", "score",
                                                            "passed_pct", "flags", "meme", "buying_why")} | {"regime": regime},
                          "_do": ("new", label, v, size, why)})
    if not cands and a.get("fit"):
        actions.append("Altcoins: no coin met all five requirements this run." + (f" Closest: {'; '.join(near[:5])}." if near else ""))
    return actions, proposals, seen


def alt_open(acct, cfg, t, kind, x, v, size, why, ai, seen, regime):
    a = cfg["alt"]
    fee = a["fee_pct"] / 100
    sl = _slip(size, v.get("volume"))
    fill = v["price"] * (1 + sl)
    qty = size * (1 - fee) / fill
    eq = alt_equity(acct, seen)
    acct["cash"] -= size
    if kind == "second":
        p = x
        p["entry"] = (p["entry"] * p["qty"] + fill * qty) / (p["qty"] + qty)  # average entry, for the take-profit multiples
        p["qty"] += qty
        p["cost"] += size
        p["halves"], p["last_buy"] = 2, t
        p["size_pct"] = round(p["size_pct"] + size / eq * 100, 2)
        setup = p["setup"]
    else:
        label = x
        pool = v.get("pool") or {}
        setup = f"Altcoin: {label.lower()}"
        p = {"pid": acct["next_id"], "id": v["id"], "symbol": v["symbol"], "name": v.get("name"), "setup": setup, "meme": bool(v.get("meme")),
             "qty": qty, "entry": fill, "cost": size, "first_size": size, "opened": t, "last_buy": t, "halves": 1, "tp_done": 0,
             "size_pct": round(size / eq * 100, 2), "entry_score": v.get("score"), "entry_volume": v.get("volume"),
             "entry_liq": pool.get("total_liq") or pool.get("liquidity"), "entry_regime": regime, "mark": v["price"], "hi": fill, "lo": fill,
             "reason": why, "ai": ai}
        acct["next_id"] += 1
        acct["positions"].append(p)
    acct["trades"].append({"type": "Open", "t": t, "pid": p["pid"], "id": p["id"], "symbol": p["symbol"], "setup": setup, "dir": "long",
                           "price": fill, "size": size, "slip": sl * 100, "reason": why, "ai": ai, "regime": regime})
    return p, f"Bought {p['symbol']} ({setup}{', second half' if kind == 'second' else ''}): {size:,.0f} USD"


# ------------------------------------------------------------------ pre-listing account
# Buys coins that trade on their own DEX pools ("native markets") before a major exchange lists them,
# and sells into the listing. Rules live in data/bot.json under "prelist".

MAJOR_KEYS = {"binance": "Binance", "coinbase": "Coinbase", "gdax": "Coinbase", "okx": "OKX", "okex": "OKX",
              "bybit": "Bybit", "upbit": "Upbit", "kraken": "Kraken"}


def majors_now(ids):
    """{coin id: [major exchanges listing it]} from each coin's CoinGecko tickers; coins whose lookup fails are left out."""
    out = {}
    try:
        from .fetch_crypto import CG, CG_HEADERS
        from .http import get_json
    except Exception:
        return out
    for cid in ids:
        try:
            got = get_json(f"{CG}/coins/{cid}/tickers", params={"page": 1}, headers=CG_HEADERS, retries=2)
        except Exception as e:
            print(f"  bot prelist tickers {cid}: {e}")
            continue
        names = set()
        for tk in (got or {}).get("tickers") or []:
            m = tk.get("market") or {}
            key = f"{m.get('identifier') or ''} {m.get('name') or ''}".lower()
            names |= {label for k, label in MAJOR_KEYS.items() if k in key}
        out[cid] = sorted(names)
    return out


def prelist_signals(v, e):
    """Signs a major-exchange listing could be coming. The rules want several, not one."""
    d = v.get("detail") or {}
    pool = v.get("pool") or {}
    cex = " | ".join(d.get("cex") or []).lower()
    out = []
    tier2 = [x for x in e["tier2_exchanges"] if x in cex]
    if tier2:
        out.append("already on " + ", ".join(t.title() for t in tier2[:3]))
    if (v.get("mcap") or 0) >= e["signal_min_mcap_usd"]:
        out.append(f"market cap ${v['mcap'] / 1e6:,.0f}M")
    if (d.get("watchers") or 0) >= e["signal_watchers"]:
        out.append(f"{d['watchers']:,} watchlists")
    if v.get("news_n") or v.get("news"):
        out.append("in the news")
    if v.get("trending"):
        out.append("trending on CoinGecko")
    if v.get("buying_up"):
        out.append("buying picking up")
    if (pool.get("liq_change_pct") or 0) >= e["signal_liq_growth_pct"]:
        out.append(f"DEX liquidity up {pool['liq_change_pct']:.0f}% in a day")
    if (d.get("commits_4w") or 0) >= e["signal_commits_4w"]:
        out.append(f"{d['commits_4w']} commits in 4 weeks")
    return out


def prelist_check(v, cfg):
    """(missing, signals) for a pre-listing entry. Nothing missing = buy."""
    e = cfg["prelist"]["entry"]
    d = v.get("detail") or {}
    pool = v.get("pool") or {}
    missing = []
    # 1. A working native market: deep enough to get out, shallow enough that discovery hasn't happened.
    if not (e["pool_min_usd"] <= (pool.get("liquidity") or 0) <= e["pool_max_usd"]) or (pool.get("total_liq") or 0) < e["total_liq_min_usd"]:
        missing.append("native market size")
    # 2. Not listed on a major yet, and not already on every smaller exchange.
    if not d or v.get("on_major") or (d.get("cex_count") or 0) > e["max_cex_count"]:
        missing.append("not yet on a major")
    # 3. Organic demand in the pools: more buys than sells, activity holding up, not wash-traded churn.
    bs, acc = pool.get("buy_share_6h"), pool.get("vol_accel")
    churn = (pool.get("vol24") or 0) / pool["total_liq"] if pool.get("total_liq") else None
    if bs is None or bs < e["min_buy_share_6h"] or acc is None or acc < e["min_vol_accel"] or churn is None or churn > e["max_churn"]:
        missing.append("organic buying")
    # 4. Listing signals: several, not one.
    sig = prelist_signals(v, e)
    if len(sig) < e["min_listing_signals"]:
        missing.append(f"listing signals ({len(sig)} of {e['min_listing_signals']})")
    # 5. Fundamentals: scan score, no red flags, and most of Marvin's five requirements.
    if (v.get("score") or 0) < e["min_score"] or _alt_blocked(v, cfg["prelist"]):
        missing.append("score / red flags")
    elif cfg.get("alt", {}).get("fit"):
        miss, _ = alt_fit(v, cfg["alt"])
        if len(miss) > e["max_fit_missing"]:
            missing.append(f"fundamentals ({len(miss)} of 5 requirements missing)")
    return missing, sig


def prelist_step(acct, alt, cfg, t, regime):
    c = cfg["prelist"]
    x = c["exits"]
    fee = c["fee_pct"] / 100
    seen = alt_prices({**(alt or {}), "utility": ((alt or {}).get("utility") or []) + ((alt or {}).get("prelisting") or [])},
                      [p["id"] for p in acct["positions"]])
    listed = majors_now([p["id"] for p in acct["positions"] if not p.get("listed")])
    actions, proposals = [], []

    def impact(size, v, pool_liq):
        # Constant-product pool: trading x into a pool holding about L/2 on each side moves price by about x / (L/2).
        liq = pool_liq or ((v.get("pool") or {}).get("liquidity"))
        return min(0.3, size / (liq / 2)) if liq else _slip(size, v.get("volume"))

    def sell(p, frac, price, reason, rule):
        v = seen.get(p["id"]) or {}
        qty, cost = p["qty"] * frac, p["cost"] * frac
        fill = price * (1 - impact(qty * price, v, p.get("pool_liq")))
        proceeds = qty * fill * (1 - fee)
        pnl = proceeds - cost
        acct["cash"] += proceeds
        tr = {"type": "Close", "t": t, "pid": p["pid"], "id": p["id"], "symbol": p["symbol"], "setup": p["setup"], "dir": "long",
              "price": fill, "size": proceeds, "pnl": pnl, "pnl_pct": pnl / cost * 100, "cost": cost, "opened": p["opened"],
              "held": t - p["opened"], "reason": reason, "rule": rule, "stop_pct": x["stop_pct"], "size_pct": p["size_pct"],
              "entry_reason": p["reason"], "multiple": price / p["entry"], "listed": p.get("listed"),
              "mfe_pct": (p.get("hi", p["entry"]) / p["entry"] - 1) * 100, "mae_pct": (p.get("lo", p["entry"]) / p["entry"] - 1) * 100}
        if frac < 0.999:
            tr["partial"] = frac
            p["qty"] -= qty
            p["cost"] -= cost
        else:
            acct["positions"].remove(p)
        acct["trades"].append(tr)
        actions.append(f"Sold {'all' if frac >= 0.999 else f'{frac:.0%}'} of {p['symbol']} ({reason}): {pnl:+,.2f} USD")

    for p in list(acct["positions"]):
        v = seen.get(p["id"]) or {}
        price = v.get("price")
        if not price:
            p["no_price"] = p.get("no_price", 0) + 1
            if p["no_price"] >= c["no_price_ticks"]:
                sell(p, 1.0, (p.get("mark") or p["entry"]) * 0.5, "No price", f"No price for {p['no_price']} refreshes in a row; written down to half the last price.")
            continue
        p["no_price"], p["mark"] = 0, price
        p["hi"], p["lo"] = max(p.get("hi", p["entry"]), price), min(p.get("lo", p["entry"]), price)
        pool = v.get("pool") or {}
        if pool.get("liquidity"):
            p["pool_liq"] = pool["liquidity"]
        # The catalyst: a major exchange lists it. Sell into the news, trail the rest.
        if not p.get("listed") and listed.get(p["id"]):
            p["listed"] = {"t": t, "on": listed[p["id"]], "price": price}
            p["stop"] = max(p.get("stop", 0), price * (1 - x["listing_trail_pct"] / 100))
            sell(p, x["listing_sell_frac"], price, "Listed on a major", f"Listed on {', '.join(listed[p['id']])}: sold {x['listing_sell_frac']:.0%} into the news; the rest trails {x['listing_trail_pct']}% below its high.")
            continue
        if p.get("listed"):
            p["stop"] = max(p["stop"], p["hi"] * (1 - x["listing_trail_pct"] / 100))
            if price <= p["stop"]:
                sell(p, 1.0, price, "Trailing stop", f"Fell {x['listing_trail_pct']}% from its high after the listing.")
            continue
        if price <= p["entry"] * (1 - x["stop_pct"] / 100):
            sell(p, 1.0, price, "Stop-loss", f"Down {x['stop_pct']}% from entry before any listing.")
            continue
        if not p.get("tp_done") and price >= p["entry"] * x["take_profit_x"]:
            p["tp_done"] = True
            sell(p, x["take_profit_frac"], price, f"Take-profit {x['take_profit_x']:g}x", f"Reached {x['take_profit_x']:g}x before any listing; sold {x['take_profit_frac']:.0%}.")
            continue
        liq = pool.get("total_liq") or pool.get("liquidity")
        if liq and p.get("entry_liq") and liq < p["entry_liq"] * (1 - x["liq_drop_pct"] / 100):
            sell(p, 1.0, price, "Liquidity pulled", f"DEX liquidity down {(1 - liq / p['entry_liq']) * 100:.0f}% since entry.")
            continue
        bs = pool.get("buy_share_6h")
        if bs is not None:
            p["sell_ticks"] = p.get("sell_ticks", 0) + 1 if bs < x["sell_pressure_below"] else 0
            if p["sell_ticks"] >= x["sell_pressure_ticks"]:
                sell(p, 1.0, price, "Sell pressure", f"Sellers outnumbered buyers ({bs:.0%} buys) for {p['sell_ticks']} refreshes in a row.")
                continue
        bad = [f for f in v.get("flags") or [] if any(f.startswith(b) for b in x["red_flags"])]
        if bad:
            sell(p, 1.0, price, "Red flag", "; ".join(bad) + ".")
            continue
        if t - p["opened"] > x["max_hold_days"] * 86400000:
            sell(p, 1.0, price, "Time stop", f"No major listing within {x['max_hold_days']} days; the thesis was a listing soon.")

    if not alt or alt.get("stale"):
        actions.append("Pre-listing: scan didn't refresh this run, no new buys.")
        return actions, proposals, seen

    eq = alt_equity(acct, seen)
    held = {p["id"] for p in acct["positions"]} | {x2["id"] for x2 in acct["trades"] if x2["type"] == "Close" and t - x2["t"] < c["cooldown_days"] * 86400000}
    room = min(c["max_positions"] - len(acct["positions"]), c["max_new_per_tick"])
    near, n = [], 0
    for v in alt.get("prelisting") or []:
        if n >= room:
            break
        if v["id"] in held or not v.get("price"):
            continue
        missing, sig = prelist_check(v, cfg)
        if missing:
            if len(missing) == 1:
                near.append(f"{v['symbol']} (missing {missing[0]})")
            continue
        pool = v.get("pool") or {}
        size = min(eq * c["position_pct"] / 100, (pool.get("liquidity") or 0) * c["max_pool_share_pct"] / 100, acct["cash"])
        if size < c["min_order_usd"]:
            continue
        n += 1
        why = (f"Native market ${pool.get('liquidity', 0) / 1e3:,.0f}K on {pool.get('dex') or 'DEX'} ({pool.get('chain')}), "
               f"{(pool.get('buy_share_6h') or 0):.0%} buys in 6h; not on a major exchange ({(v.get('detail') or {}).get('cex_count') or 0} smaller ones). "
               f"Listing signals: {', '.join(sig)}. Scan score {v.get('score')}.")
        proposals.append({"account": "prelist", "asset": v["symbol"], "setup": "Pre-listing", "size": round(size, 2), "unit": "USD", "rule_reason": why,
                          "context": {k: v.get(k) for k in ("name", "price", "mcap", "volume", "ch7", "ch30", "score", "flags", "news")} |
                                     {"pool": {k: pool.get(k) for k in ("liquidity", "total_liq", "buy_share_6h", "vol_accel", "liq_change_pct", "dex", "chain")},
                                      "cex": (v.get("detail") or {}).get("cex"), "regime": regime},
                          "_do": (v, size, why)})
    if not proposals:
        actions.append("Pre-listing: no coin met every rule this run." + (f" Closest: {'; '.join(near[:5])}." if near else ""))
    return actions, proposals, seen


def prelist_open(acct, cfg, t, v, size, why, ai, seen, regime):
    c = cfg["prelist"]
    pool = v.get("pool") or {}
    imp = min(0.3, size / (pool["liquidity"] / 2)) if pool.get("liquidity") else _slip(size, v.get("volume"))
    fill = v["price"] * (1 + imp)
    qty = size * (1 - c["fee_pct"] / 100) / fill
    eq = alt_equity(acct, seen)
    acct["cash"] -= size
    p = {"pid": acct["next_id"], "id": v["id"], "symbol": v["symbol"], "name": v.get("name"), "setup": "Pre-listing", "qty": qty, "entry": fill,
         "cost": size, "opened": t, "size_pct": round(size / eq * 100, 2), "reason": why, "ai": ai, "mark": v["price"], "hi": fill, "lo": fill,
         "entry_liq": pool.get("total_liq") or pool.get("liquidity"), "pool_liq": pool.get("liquidity"), "pool": {k: pool.get(k) for k in ("dex", "chain", "url", "pair")},
         "entry_score": v.get("score"), "entry_regime": regime, "stop": fill * (1 - c["exits"]["stop_pct"] / 100)}
    acct["next_id"] += 1
    acct["positions"].append(p)
    acct["trades"].append({"type": "Open", "t": t, "pid": p["pid"], "id": p["id"], "symbol": p["symbol"], "setup": "Pre-listing", "dir": "long",
                           "price": fill, "size": size, "slip": imp * 100, "reason": why, "ai": ai, "regime": regime})
    return f"Bought {p['symbol']} (Pre-listing): {size:,.0f} USD"


# ------------------------------------------------------------------ AI review of entries

SYSTEM = """You are the risk reviewer for a paper trading bot (simulated money only). \
Each refresh, fixed rules propose new entries. For each proposal decide "take" or "skip". \
Skip when the data shows a concrete problem the rule missed: the move looks exhausted or news-driven, \
liquidity is thin for the size, it duplicates exposure the bot already has, the regime argues against it, \
or the evidence is contradictory. Altcoin-account entries are deliberately small, high-risk bets on small caps sized so a total loss is acceptable: judge them on the scan's evidence (red flags, liquidity, a story that doesn't hold up), not on volatility or the chance of losing the stake. Pre-listing entries bet that a coin trading on its own DEX pools gets listed on a major exchange: skip when the pool data looks like wash trading or a pump, or nothing suggests a listing is plausible. Otherwise take it; the rules are the strategy, you are the check, so do \
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

def run(crypto_sig, tao_sig, ts, alt=None):
    cfg = config()
    state = load_state(cfg)
    for k in ("alt", "prelist"):
        if cfg.get(k) and k not in state:
            state[k] = {"start": cfg[k]["start_usd"], "cash": cfg[k]["start_usd"], "positions": [], "trades": [], "equity": [], "next_id": 1,
                        "created": now_ms()}
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
    seen = None
    if cfg.get("alt") and (alt or state["alt"]["positions"]):
        a, p, seen = alt_step(state["alt"], alt, cfg, t, regimes.get("crypto"))
        actions += a
        proposals += p
    seen_pl = None
    if cfg.get("prelist") and (alt or state["prelist"]["positions"]):
        a, p, seen_pl = prelist_step(state["prelist"], alt, cfg, t, regimes.get("crypto"))
        actions += a
        proposals += p

    context = {
        "crypto_regime": (crypto_sig or {}).get("regime", {}).get("label") if crypto_sig else None,
        "crypto_regime_reasons": (crypto_sig or {}).get("regime", {}).get("reasons") if crypto_sig else None,
        "tao_plan_regime": ((tao_sig or {}).get("plan") or {}).get("regime", {}).get("title") if tao_sig else None,
        "crypto_holdings": [p["symbol"] for p in state["crypto"]["positions"]],
        "tao_holdings": [f"SN{k}" for k in state["tao"]["positions"]],
        "alt_holdings": [p["symbol"] for p in (state.get("alt") or {}).get("positions", [])],
        "prelist_holdings": [p["symbol"] for p in (state.get("prelist") or {}).get("positions", [])],
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
        elif p["account"] == "prelist":
            actions.append(prelist_open(state["prelist"], cfg, t, *p["_do"], ai, seen_pl, regimes.get("crypto")))
        elif p["account"] == "alt":
            actions.append(alt_open(state["alt"], cfg, t, *p["_do"], ai, seen, regimes.get("crypto"))[1])
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
    if seen_pl is not None:
        state["prelist"]["equity"].append({"t": t, "v": round(alt_equity(state["prelist"], seen_pl), 2)})
    if seen is not None:
        state["alt"]["equity"].append({"t": t, "v": round(alt_equity(state["alt"], seen), 2)})
    for k in ("crypto", "tao", "alt", "prelist"):
        if k not in state:
            continue
        state[k]["trades"] = state[k]["trades"][-KEEP_TRADES:]
        state[k]["equity"] = state[k]["equity"][-KEEP_EQUITY:]
    state["ticks"] = (state["ticks"] + [{"t": t, "ts": ts, "regimes": regimes, "actions": actions, "skipped": skipped,
                                         "ai_note": note, "ai_model": model, "ai": decisions is not None, "orders": list(ORDERS)}])[-KEEP_TICKS:]
    state["last_tick"] = t
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, default=str, separators=(",", ":")))
    print(f"  bot: {len(actions)} actions, {len(skipped)} skipped by AI" + ("" if decisions is not None else " (no AI review)"))
    return state
