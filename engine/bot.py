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


def crypto_step(acct, sig, cfg, t, hours):
    c = cfg["crypto"]
    fee = c["fee_pct"] / 100
    coins = {x["id"]: x for x in sig["coins"]}
    prices = {k: v["price"] for k, v in coins.items()}
    regime = (sig.get("regime") or {}).get("label", "Neutral")
    actions, proposals = [], []

    def close(p, price, reason, rule):
        coin = coins.get(p["id"]) or {}
        gross = p["qty"] * price
        s = _slip(gross, coin.get("volume"))
        fill = price * (1 - s)
        proceeds = p["qty"] * fill * (1 - fee)
        pnl = proceeds - p["cost"]
        acct["cash"] += proceeds
        acct["positions"].remove(p)
        tr = {"type": "Close", "t": t, "pid": p["pid"], "id": p["id"], "symbol": p["symbol"], "setup": p["setup"], "dir": "long",
              "price": fill, "size": proceeds, "pnl": pnl, "pnl_pct": pnl / p["cost"] * 100, "cost": p["cost"], "opened": p["opened"],
              "held": t - p["opened"], "reason": reason, "rule": rule, "stop_pct": p["stop_pct"], "size_pct": p["size_pct"], "entry_reason": p["reason"]}
        acct["trades"].append(tr)
        actions.append(f"Sold {p['symbol']} ({reason}): {pnl:+,.2f} USD")
        ORDERS.append({"account": "crypto", "side": "sell", "id": p["id"], "symbol": p["symbol"], "frac": 1.0, "reason": reason, "price": price})

    # Exits: stop and target along the price path since the last tick, then time and signal exits.
    for p in list(acct["positions"]):
        coin = coins.get(p["id"])
        if not coin:
            continue
        hit = None
        for px in _path_since(coin, hours):
            if px <= p["stop"]:
                hit = (min(px, p["stop"]) if px < p["stop"] * 0.97 else p["stop"], "Stop-loss", f"Price fell to the {p['stop_pct']}% stop.")
                break
            if px >= p["target"]:
                hit = (p["target"], "Take-profit", f"Price reached the {p['target_pct']}% target.")
                break
        if hit:
            close(p, hit[0], hit[1], hit[2])
            continue
        if t - p["opened"] > p["max_hold_days"] * 86400000:
            close(p, coin["price"], "Time stop", f"Held longer than {p['max_hold_days']} days without hitting stop or target.")
            continue
        bad = [x for x in c["exit_tags"] if x in (coin.get("tags") or [])]
        if bad:
            close(p, coin["price"], "Signal exit", f"Signal turned: {', '.join(bad)}.")

    # Entries: rank candidates by setup, propose up to max_new_per_tick.
    held = {p["id"] for p in acct["positions"]}
    # Cool-down: no re-entry into a coin the bot sold recently.
    held |= {x["id"] for x in acct["trades"] if x["type"] == "Close" and t - x["t"] < c.get("cooldown_hours", 24) * 3600000}
    eq = crypto_equity(acct, prices)
    room = c["max_positions"] - len(acct["positions"])
    cands = []
    for coin in sig["coins"]:
        if coin["id"] in held or not coin.get("rank") or coin["rank"] > c["max_rank"] or (coin.get("volume") or 0) < c["min_volume_usd"]:
            continue
        tags = coin.get("tags") or []
        for key, s in c["setups"].items():
            if regime not in s["regimes"] or any(x in tags for x in s.get("tags_none", [])):
                continue
            if key == "trend":
                if coin["score"] < s["min_score"] or not any(x in tags for x in s["tags_any"]):
                    continue
                why = f"Score {coin['score']}, tags {', '.join(tags)}; {coin['ch7']:+.1f}% 7d vs BTC {coin['rs7']:+.1f} pts."
                rank = -coin["score"]
            else:
                if coin["rank"] > s.get("max_rank", 999) or coin.get("rsi") is None or coin["rsi"] > s["max_rsi"]:
                    continue
                why = f"4h RSI {coin['rsi']:.0f}, rank #{coin['rank']}, {coin['ch7']:+.1f}% 7d: oversold large cap."
                rank = 1000 + coin["rsi"]
            cands.append((rank, key, s, coin, why))
            break
    cands.sort(key=lambda x: x[0])
    for _, key, s, coin, why in cands[: max(0, min(room, c["max_new_per_tick"]))]:
        size = eq * s["size_pct"] / 100
        if size > acct["cash"] or size <= 0:
            continue
        proposals.append({"account": "crypto", "asset": coin["symbol"], "setup": s["label"], "size": round(size, 2), "unit": "USD", "rule_reason": why,
                          "context": {k: coin.get(k) for k in ("name", "rank", "price", "ch24", "ch7", "ch30", "rsi", "range7", "turnover", "score", "tags")},
                          "_do": (key, s, coin, why, size)})
    return actions, proposals, regime


def crypto_open(acct, cfg, t, key, s, coin, why, size, ai):
    fee = cfg["crypto"]["fee_pct"] / 100
    sl = _slip(size, coin.get("volume"))
    fill = coin["price"] * (1 + sl)
    qty = size * (1 - fee) / fill
    eq_before = acct["cash"]
    acct["cash"] -= size
    p = {"pid": acct["next_id"], "id": coin["id"], "symbol": coin["symbol"], "setup": s["label"], "qty": qty, "entry": fill, "cost": size,
         "opened": t, "stop": fill * (1 - s["stop_pct"] / 100), "target": fill * (1 + s["target_pct"] / 100), "stop_pct": s["stop_pct"],
         "target_pct": s["target_pct"], "max_hold_days": s["max_hold_days"], "size_pct": s["size_pct"], "reason": why, "ai": ai}
    acct["next_id"] += 1
    acct["positions"].append(p)
    acct["trades"].append({"type": "Open", "t": t, "pid": p["pid"], "id": coin["id"], "symbol": coin["symbol"], "setup": s["label"], "dir": "long",
                           "price": fill, "size": size, "slip": sl * 100, "reason": why, "ai": ai, "cash_before": eq_before})
    ORDERS.append({"account": "crypto", "side": "buy", "id": coin["id"], "symbol": coin["symbol"], "setup": s["label"], "size_pct": s["size_pct"],
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
        if r and r["status"] == "Exit / avoid":
            sell(k, 1.0, "Plan exit", r["short"])
        elif s["price_tao"] <= avg * (1 - stop / 100):
            sell(k, 1.0, "Stop-loss", f"Price is {(s['price_tao'] / avg - 1) * 100:.0f}% from the average entry; the plan's stop is {stop}%.")
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
        a, p, regimes["crypto"] = crypto_step(state["crypto"], crypto_sig, cfg, t, hours)
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
