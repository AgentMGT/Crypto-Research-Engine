"""Live trading: mirrors the paper bot's fills with real orders, inside hard limits.

Every run the bot ticks, each new paper fill is turned into a live order for the same
asset. An account places REAL orders only when all three locks are open:

  1. data/live.json  -> <account>.armed is true
  2. environment     -> LIVE_TRADING == "on"   (a GitHub repository variable; the kill switch)
  3. secrets         -> the account's API credentials are present

Otherwise the account runs as a dry run: it sizes each order against its budget, checks
the limits, and logs what it would have placed, without touching an exchange or chain.

Limits per account: a total budget the bot may have deployed, a maximum order size, a
maximum number of positions, and a daily loss limit that halts new buys until cleared.
Exits always go through. New buys need the AI reviewer's approval when
require_ai_approval is set.

Crypto trades spot only, long only, through ccxt (default exchange: Coinbase). After each
buy it places a stop-loss order on the exchange, so the stop works between refreshes.

Bittensor stakes and unstakes subnet alpha through the bittensor SDK, signed by a
delegate key that the owner's coldkey has authorised as a Staking proxy. The delegate can
stake and unstake but cannot transfer funds, and the coldkey never touches this machine.
"""

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "data" / "live.json"
STATE = ROOT / "data" / "live" / "state.json"
KEEP_LOG = 500


def now_ms():
    return int(time.time() * 1000)


def config():
    return json.loads(CONFIG.read_text())


def load_state():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except ValueError:
            pass
    return {"v": 1, "crypto": {"positions": {}, "realized": 0.0, "day": None, "halted": None},
            "tao": {"positions": {}, "realized": 0.0, "day": None, "halted": None}, "log": [], "runs": []}


def save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    state["log"] = state["log"][-KEEP_LOG:]
    state["runs"] = state["runs"][-60:]
    STATE.write_text(json.dumps(state, default=str, separators=(",", ":")))


def _secrets(account):
    if account == "crypto":
        k, s = os.environ.get("LIVE_EXCHANGE_API_KEY"), os.environ.get("LIVE_EXCHANGE_SECRET")
        return {"apiKey": k, "secret": s, "password": os.environ.get("LIVE_EXCHANGE_PASSWORD")} if k and s else None
    m, owner = os.environ.get("LIVE_TAO_PROXY_MNEMONIC"), os.environ.get("LIVE_TAO_COLDKEY_SS58")
    return {"mnemonic": m, "owner": owner} if m and owner else None


def mode_for(account, cfg):
    """('live' | 'dry_run', [reasons it isn't live])."""
    why = []
    if not cfg[account].get("armed"):
        why.append("not armed in data/live.json")
    if os.environ.get("LIVE_TRADING", "").strip().lower() != "on":
        why.append("repository variable LIVE_TRADING is not 'on'")
    if not _secrets(account):
        why.append("API secrets not set")
    if account == "tao" and not cfg["tao"].get("validator_hotkey"):
        why.append("no validator hotkey chosen")
    return ("live" if not why else "dry_run"), why


def _log(state, t, account, mode, side, asset, status, **kw):
    entry = {"t": t, "account": account, "mode": mode, "side": side, "asset": asset, "status": status}
    entry.update({k: v for k, v in kw.items() if v is not None})
    state["log"].append(entry)
    print(f"  live[{account}/{mode}] {side} {asset}: {status}" + (f" ({kw.get('note')})" if kw.get("note") else ""))
    return entry


def _equity(acct, budget, marks):
    """Virtual ledger inside the budget: cash left of the budget plus positions at market."""
    cost = sum(p["cost"] for p in acct["positions"].values())
    value = sum(p["amount"] * marks.get(k, p["entry"]) for k, p in acct["positions"].items())
    return budget - cost + acct["realized"] + value, cost


def _day_check(acct, t, eq, max_loss, unit):
    """Track the day's starting equity; halt new buys when the day's loss passes the limit."""
    day = datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m-%d")
    if not acct.get("day") or acct["day"]["date"] != day:
        acct["day"] = {"date": day, "start": eq}
    loss = acct["day"]["start"] - eq
    if loss > max_loss and not acct.get("halted"):
        acct["halted"] = {"t": t, "reason": f"Daily loss {loss:.2f} {unit} passed the {max_loss} {unit} limit. New buys are paused until cleared."}
    return loss


# ------------------------------------------------------------------ crypto (ccxt)

class CryptoVenue:
    def __init__(self, cfg, creds):
        import ccxt
        self.cfg = cfg
        self.ex = getattr(ccxt, cfg["exchange"])({**{k: v for k, v in (creds or {}).items() if v}, "enableRateLimit": True})
        self.ex.load_markets()

    def market(self, symbol):
        for q in self.cfg["quote_currencies"]:
            m = f"{symbol.upper()}/{q}"
            if m in self.ex.markets and self.ex.markets[m].get("active", True) and self.ex.markets[m].get("spot", True):
                return m
        return None

    def ask(self, m):
        tk = self.ex.fetch_ticker(m)
        return tk.get("ask") or tk.get("last")

    def free(self, code):
        return float((self.ex.fetch_balance().get("free") or {}).get(code) or 0)

    def total(self, code):
        # Includes coins reserved by an open stop order, which "free" leaves out.
        return float((self.ex.fetch_balance().get("total") or {}).get(code) or 0)

    def buy(self, m, cost):
        if self.ex.has.get("createMarketBuyOrderWithCost"):
            o = self.ex.create_market_buy_order_with_cost(m, cost)
        else:
            o = self.ex.create_order(m, "market", "buy", self.ex.amount_to_precision(m, cost / self.ask(m)))
        try:
            o = self.ex.fetch_order(o["id"], m) or o
        except Exception:
            pass
        return o

    def stop(self, m, amount, stop_price):
        amt = self.ex.amount_to_precision(m, amount)
        limit = self.ex.price_to_precision(m, stop_price * 0.985)
        return self.ex.create_order(m, "limit", "sell", amt, limit, {"stopLossPrice": self.ex.price_to_precision(m, stop_price)})

    def cancel(self, oid, m):
        try:
            self.ex.cancel_order(oid, m)
        except Exception:
            pass

    def sell(self, m, amount):
        return self.ex.create_order(m, "market", "sell", self.ex.amount_to_precision(m, amount))


def run_crypto(state, cfg, orders, t, crypto_sig):
    c = cfg["crypto"]
    acct = state["crypto"]
    mode, why = mode_for("crypto", cfg)
    marks = {p["id"]: p["price"] for p in (crypto_sig or {}).get("coins", [])}
    venue = None
    if mode == "live":
        try:
            venue = CryptoVenue(c, _secrets("crypto"))
        except Exception as e:
            _log(state, t, "crypto", mode, "-", "-", "failed", note=f"couldn't connect to {c['exchange']}: {type(e).__name__}")
            return mode, why
    else:
        try:  # public market list, so the dry run can say whether the exchange lists the coin
            venue = CryptoVenue(c, None)
        except Exception:
            venue = None

    # Sync: a position the exchange stop already closed shows up as a missing balance.
    if mode == "live":
        for k, p in list(acct["positions"].items()):
            try:
                held = venue.total(p["base"])
                if held < p["amount"] * 0.1 and p.get("stop_order"):
                    pnl = p["amount"] * p["stop"] - p["cost"]
                    acct["realized"] += pnl
                    del acct["positions"][k]
                    _log(state, t, "crypto", mode, "sell", p["symbol"], "closed by exchange stop", pnl=round(pnl, 2), price=p["stop"])
            except Exception:
                pass

    eq, deployed = _equity(acct, c["budget_usd"], marks)
    loss = _day_check(acct, t, eq, c["max_daily_loss_usd"], "USD")

    for o in [x for x in orders if x["account"] == "crypto"]:
        sym, cid = o["symbol"], o["id"]
        m = venue.market(sym) if venue else None
        if o["side"] == "sell":
            p = acct["positions"].get(cid)
            if not p:
                continue  # the bot bought this before live trading started
            amount = p["amount"] * o["frac"]
            price = marks.get(cid, o.get("price"))
            if mode == "live":
                try:
                    if p.get("stop_order"):
                        venue.cancel(p["stop_order"], p["market"])
                    amount = min(amount, venue.free(p["base"]))
                    r = venue.sell(p["market"], amount)
                    price = r.get("average") or price
                    status, oid = "placed", r.get("id")
                except Exception as e:
                    _log(state, t, "crypto", mode, "sell", sym, "failed", note=type(e).__name__ + ": " + str(e)[:160])
                    continue
            else:
                status, oid = "would place", None
            pnl = amount * price - p["cost"] * o["frac"]
            acct["realized"] += pnl
            if o["frac"] >= 0.999:
                del acct["positions"][cid]
            else:
                p["amount"] -= amount
                p["cost"] *= 1 - o["frac"]
            _log(state, t, "crypto", mode, "sell", sym, status, reason=o["reason"], amount=amount, price=price, pnl=round(pnl, 2), order_id=oid)
            continue

        # Buys: every limit must pass.
        size = min(eq * o["size_pct"] / 100, c["max_order_usd"], c["budget_usd"] - deployed)
        skip = None
        if acct.get("halted"):
            skip = "paused: " + acct["halted"]["reason"]
        elif c.get("require_ai_approval") and not (o.get("ai") and o["ai"].get("verdict") == "take"):
            skip = "no AI approval (the AI reviewer needs the ANTHROPIC_API_KEY secret)"
        elif cid in acct["positions"]:
            skip = "already holds it"
        elif len(acct["positions"]) >= c["max_positions"]:
            skip = f"at the {c['max_positions']}-position limit"
        elif size < c["min_order_usd"]:
            skip = f"order would be ${size:,.2f}, under the ${c['min_order_usd']} minimum (budget used up)"
        elif venue and not m:
            skip = f"{c['exchange']} doesn't list {sym} against {'/'.join(c['quote_currencies'])}"
        if skip:
            _log(state, t, "crypto", mode, "buy", sym, "skipped", note=skip, size=round(max(size, 0), 2))
            continue
        price, oid, stop_id, amount = o["price"], None, None, None
        if mode == "live":
            try:
                ask = venue.ask(m)
                if abs(ask / o["price"] - 1) * 100 > c["max_price_drift_pct"]:
                    _log(state, t, "crypto", mode, "buy", sym, "skipped", note=f"exchange price {ask} is more than {c['max_price_drift_pct']}% from the signal price {o['price']}")
                    continue
                quote = m.split("/")[1]
                if venue.free(quote) < size:
                    _log(state, t, "crypto", mode, "buy", sym, "skipped", note=f"not enough {quote} on the exchange")
                    continue
                if venue.total(m.split("/")[0]) * ask > c["min_order_usd"]:
                    _log(state, t, "crypto", mode, "buy", sym, "skipped", note="the exchange account already holds this coin outside the bot")
                    continue
                r = venue.buy(m, size)
                price = r.get("average") or ask
                amount = r.get("filled") or size / price
                oid = r.get("id")
            except Exception as e:
                _log(state, t, "crypto", mode, "buy", sym, "failed", note=type(e).__name__ + ": " + str(e)[:160])
                continue
            stop_price = price * (1 - o["stop_pct"] / 100)
            if c.get("place_exchange_stop"):
                try:
                    stop_id = venue.stop(m, amount * 0.999, stop_price).get("id")
                except Exception as e:
                    _log(state, t, "crypto", mode, "stop", sym, "failed", note="no exchange stop; the bot still sells at the stop on each refresh (" + type(e).__name__ + ")")
            status = "placed"
        else:
            amount = size / price
            stop_price = price * (1 - o["stop_pct"] / 100)
            status = "would place"
        acct["positions"][cid] = {"symbol": sym, "market": m, "base": (m or sym + "/USD").split("/")[0], "amount": amount, "cost": size,
                                  "entry": price, "stop": stop_price, "stop_order": stop_id, "opened": t, "setup": o["setup"]}
        deployed += size
        _log(state, t, "crypto", mode, "buy", sym, status, setup=o["setup"], size=round(size, 2), price=price, amount=amount,
             stop=stop_price, order_id=oid, stop_order=stop_id, ai=(o.get("ai") or {}).get("reason"))
    acct["equity"] = round(_equity(acct, c["budget_usd"], marks)[0], 2)
    acct["loss_today"] = round(loss, 2)
    return mode, why


# ------------------------------------------------------------------ Bittensor (SDK, Staking proxy)

class _Wallet:
    """Wallet-shaped holder for the delegate keypair: it signs as itself, on behalf of the owner."""
    def __init__(self, kp):
        self.coldkey = self.coldkeypub = self.hotkey = kp


async def _tao_exec(intents, cfg, creds):
    import bittensor as bt
    from bittensor.sp_core import Keypair
    kp = Keypair.create_from_mnemonic(creds["mnemonic"])
    wallet = _Wallet(kp)
    t = cfg["tao"]
    policy = bt.Policy(max_spend_tao=t["max_order_tao"], max_fee_tao=0.01, allowed_netuids=[i[1] for i in intents if i[1] is not None] or None)
    out = []

    async def stake_of(client, netuid):
        st = await client.read("stake", coldkey_ss58=creds["owner"], hotkey_ss58=t["validator_hotkey"], netuid=netuid)
        for attr in ("alpha", "tao"):  # stake on a subnet is an alpha Balance; .tao refuses it
            try:
                return float(getattr(st, attr))
            except Exception:
                continue
        return float(st or 0)

    async with bt.Client("finney") as client:
        for kind, netuid, amount in intents:
            try:
                before = await stake_of(client, netuid)
                if kind == "buy":
                    intent = bt.AddStake(hotkey_ss58=t["validator_hotkey"], netuid=netuid, amount_tao=amount,
                                         slippage_protection=True, rate_tolerance=t["rate_tolerance_pct"] / 100)
                else:
                    intent = bt.RemoveStake(hotkey_ss58=t["validator_hotkey"], netuid=netuid, amount_alpha=amount,
                                            slippage_protection=True, rate_tolerance=t["rate_tolerance_pct"] / 100)
                r = await client.execute(intent, wallet, policy=policy, proxy_for=creds["owner"], proxy_type="Staking")
                if not r.success:
                    out.append(("failed", r.message))
                    continue
                # The alpha actually received or sent, from the stake before and after.
                out.append(("ok", r.extrinsic_id, abs(await stake_of(client, netuid) - before)))
            except Exception as e:
                out.append(("failed", type(e).__name__ + ": " + str(e)[:160]))
    return out


def run_tao(state, cfg, orders, t, tao_sig):
    c = cfg["tao"]
    acct = state["tao"]
    mode, why = mode_for("tao", cfg)
    subs = {s["netuid"]: s for s in (tao_sig or {}).get("subnets", [])}
    marks = {str(k): s["price_tao"] for k, s in subs.items()}
    eq, deployed = _equity(acct, c["budget_tao"], marks)
    loss = _day_check(acct, t, eq, c["max_daily_loss_tao"], "τ")
    creds = _secrets("tao")

    plan = []  # (order, intent tuple, size)
    for o in [x for x in orders if x["account"] == "tao"]:
        k, name = str(o["netuid"]), f"SN{o['netuid']} {o.get('name', '')}".strip()
        if o["side"] == "sell":
            p = acct["positions"].get(k)
            if not p:
                continue
            alpha = p["amount"] * o["frac"]
            plan.append((o, ("sell", o["netuid"], alpha), alpha))
            continue
        tranche = c["budget_tao"] * c["position_max_pct"] / 100 / 3
        size = min(tranche, c["max_order_tao"], c["budget_tao"] - deployed)
        held_cost = (acct["positions"].get(k) or {}).get("cost", 0)
        skip = None
        if acct.get("halted"):
            skip = "paused: " + acct["halted"]["reason"]
        elif c.get("require_ai_approval") and not (o.get("ai") and o["ai"].get("verdict") == "take"):
            skip = "no AI approval (the AI reviewer needs the ANTHROPIC_API_KEY secret)"
        elif held_cost + size > c["budget_tao"] * c["position_max_pct"] / 100 * 1.001:
            skip = f"position would pass {c['position_max_pct']}% of the budget"
        elif size < c["min_order_tao"]:
            skip = f"order would be {size:.3f} τ, under the {c['min_order_tao']} τ minimum (budget used up)"
        if skip:
            _log(state, t, "tao", mode, "buy", name, "skipped", note=skip, size=round(max(size, 0), 4))
            continue
        plan.append((o, ("buy", o["netuid"], size), size))
        deployed += size

    results = [None] * len(plan)
    if mode == "live" and plan:
        try:
            results = asyncio.run(_tao_exec([x[1] for x in plan], cfg, creds))
        except Exception as e:
            results = [("failed", type(e).__name__ + ": " + str(e)[:160])] * len(plan)

    for (o, (kind, netuid, amount), size), res in zip(plan, results):
        k, name = str(netuid), f"SN{netuid} {o.get('name', '')}".strip()
        price = subs.get(netuid, {}).get("price_tao") or o.get("price")
        if res and res[0] != "ok":
            _log(state, t, "tao", mode, kind, name, "failed", note=res[1])
            continue
        status, oid = ("placed", res[1]) if res else ("would place", None)
        if kind == "buy":
            p = acct["positions"].setdefault(k, {"symbol": name, "amount": 0.0, "cost": 0.0, "entry": price, "opened": t, "setup": o.get("setup")})
            p["amount"] += (res[2] if res and len(res) > 2 and res[2] else size / price)
            p["cost"] += size
            p["entry"] = p["cost"] / p["amount"]
            _log(state, t, "tao", mode, "buy", name, status, setup=o.get("setup"), size=round(size, 4), price=price, order_id=oid,
                 ai=(o.get("ai") or {}).get("reason"))
        else:
            p = acct["positions"][k]
            proceeds = amount * price
            pnl = proceeds - p["cost"] * o["frac"]
            acct["realized"] += pnl
            if o["frac"] >= 0.999:
                del acct["positions"][k]
            else:
                p["amount"] -= amount
                p["cost"] *= 1 - o["frac"]
            _log(state, t, "tao", mode, "sell", name, status, reason=o.get("reason"), amount=amount, price=price, pnl=round(pnl, 4), order_id=oid)
    acct["equity"] = round(_equity(acct, c["budget_tao"], marks)[0], 4)
    acct["loss_today"] = round(loss, 4)
    return mode, why


# ------------------------------------------------------------------ entry point

def run(bot_state, crypto_sig, tao_sig):
    """Called after the paper bot ticks. bot_state is None when the bot didn't tick this run."""
    if not bot_state or not bot_state.get("ticks"):
        return None
    cfg = config()
    state = load_state()
    tick = bot_state["ticks"][-1]
    if state.get("last_tick") == tick["t"]:
        return state  # already mirrored this tick
    orders, t = tick.get("orders") or [], tick["t"]
    summary = {"t": t, "ts": tick.get("ts")}
    for account, fn, sig in (("crypto", run_crypto, crypto_sig), ("tao", run_tao, tao_sig)):
        try:
            mode, why = fn(state, cfg, orders, t, sig)
        except Exception as e:
            mode, why = "error", [type(e).__name__ + ": " + str(e)[:160]]
        summary[account] = {"mode": mode, "why_not_live": why}
        state[account]["mode"], state[account]["why_not_live"] = mode, why
    state["runs"].append(summary)
    state["last_tick"] = t
    state["limits"] = {k: {kk: vv for kk, vv in v.items() if kk not in ("validator_hotkey",)} for k, v in cfg.items() if not k.startswith("_")}
    save_state(state)
    return state
