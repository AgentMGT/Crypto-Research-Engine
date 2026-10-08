"""Bittensor (TAO) + subnet data, read straight from the public Finney chain.

No API key needed: the official `bittensor` SDK (v11) connects to the public
RPC endpoints. TAO's USD price and history come from CoinGecko.
"""

import asyncio

from .fetch_crypto import CG, CG_HEADERS
from .http import get_json

RAO = 1e9
FIX32 = 2**32  # I96F32 fixed point (moving price)


def _int(v):
    if v is None:
        return 0
    if isinstance(v, dict):  # {"bits": n} fixed point or wrapped balance
        v = v.get("bits", next(iter(v.values()), 0))
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _fixed(v):
    """FixedI128 / I96F32 -> float."""
    if isinstance(v, dict) and "bits" in v:
        return int(v["bits"]) / FIX32
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _ema_flow(v):
    """SubnetEmaTaoFlow is (last_block, I64F64 ema in rao) -> ema in TAO per block.

    I64F64 has 64 fractional bits, not the 32 of the I96F32 prices, so `_fixed`
    alone overstates it by 2**32 (live runs showed flows of ~1e12 TAO/day).
    """
    x = v[1] if isinstance(v, (list, tuple)) and len(v) == 2 else v
    if isinstance(x, dict) and "bits" in x:
        return int(x["bits"]) / 2 ** 64 / RAO
    return _fixed(x) / RAO


async def _chain_subnets():
    from bittensor.client import Client
    from bittensor._generated import storage

    S = storage.SubtensorModule
    async with Client("finney") as client:
        (names, prices, tao_in, alpha_in, alpha_out, moving, tao_em, volume, ema_flow, reg_at, n_neurons, block) = (
            await asyncio.gather(
                client.read("subnet_names"),
                client.read("alpha_prices"),
                client.query_map(S.SubnetTAO),
                client.query_map(S.SubnetAlphaIn),
                client.query_map(S.SubnetAlphaOut),
                client.query_map(S.SubnetMovingPrice),
                client.query_map(S.SubnetTaoInEmission),
                client.query_map(S.SubnetVolume),
                client.query_map(S.SubnetEmaTaoFlow),
                client.query_map(S.NetworkRegisteredAt),
                client.query_map(S.SubnetworkN),
                client.query(("System", "Number")),
            )
        )

    m = lambda rows, f=_int: {int(k): f(v) for k, v in rows}  # noqa: E731
    tao_in, alpha_in, alpha_out = m(tao_in), m(alpha_in), m(alpha_out)
    moving, tao_em, volume = m(moving, _fixed), m(tao_em), m(volume)
    ema_flow, reg_at, n_neurons = m(ema_flow, _ema_flow), m(reg_at), m(n_neurons)
    block = _int(block)

    subnets = []
    for netuid in sorted(set(tao_in) | set(prices)):
        if netuid == 0:
            continue  # root has no alpha pool
        price = prices.get(netuid) or ((tao_in.get(netuid, 0) / alpha_in[netuid]) if alpha_in.get(netuid) else 0)
        supply = (alpha_in.get(netuid, 0) + alpha_out.get(netuid, 0)) / RAO
        subnets.append(
            {
                "netuid": netuid,
                "name": names.get(netuid) or f"Subnet {netuid}",
                "price_tao": price,
                "moving_price_tao": moving.get(netuid, 0.0),
                "tao_liquidity": tao_in.get(netuid, 0) / RAO,
                "alpha_in_pool": alpha_in.get(netuid, 0) / RAO,
                "alpha_supply": supply,
                "mcap_tao": price * supply,
                "tao_emission_per_block": tao_em.get(netuid, 0) / RAO,
                "volume_total_tao": volume.get(netuid, 0) / RAO,
                "ema_tao_flow": ema_flow.get(netuid, 0.0),
                "age_days": max(0, (block - reg_at.get(netuid, block)) * 12 / 86400) if block else None,
                "neurons": n_neurons.get(netuid, 0),
            }
        )
    return {"block": block, "subnets": subnets}


def tao_market():
    coin = get_json(
        f"{CG}/coins/bittensor",
        params={"localization": "false", "tickers": "false", "community_data": "false", "developer_data": "false"},
        headers=CG_HEADERS,
    )
    md = coin["market_data"]
    chart = get_json(f"{CG}/coins/bittensor/market_chart", params={"vs_currency": "usd", "days": 90, "interval": "daily"}, headers=CG_HEADERS)
    return {
        "price": md["current_price"]["usd"],
        "mcap": md["market_cap"]["usd"],
        "volume": md["total_volume"]["usd"],
        "chg_24h": md.get("price_change_percentage_24h") or 0,
        "chg_7d": md.get("price_change_percentage_7d") or 0,
        "chg_30d": md.get("price_change_percentage_30d") or 0,
        "ath": md["ath"]["usd"],
        "circulating": md.get("circulating_supply"),
        "max_supply": md.get("max_supply"),
        "history": [p[1] for p in chart.get("prices", [])],
    }


DEX_HINTS = ("uniswap", "raydium", "orca", "meteora", "jupiter", "aerodrome", "velodrome", "pancakeswap",
             "sushi", "curve", "balancer", "camelot", "trader_joe", "hydradx", "osmosis", "swap", "dex")


def _is_dex(market):
    ident = f"{market.get('identifier') or ''} {market.get('name') or ''}".lower()
    return any(h in ident for h in DEX_HINTS)


def tao_venues():
    """Where TAO trades: CEX order books and DEX pools, from CoinGecko's ticker feed."""
    d = get_json(f"{CG}/coins/bittensor/tickers", params={"order": "volume_desc", "depth": "true"}, headers=CG_HEADERS)
    rows = []
    for t in d.get("tickers") or []:
        m = t.get("market") or {}
        rows.append(
            {
                "venue": m.get("name"),
                "kind": "DEX" if _is_dex(m) else "CEX",
                "pair": f"{t.get('base')}/{t.get('target')}",
                "price": (t.get("converted_last") or {}).get("usd"),
                "volume": (t.get("converted_volume") or {}).get("usd") or 0,
                "spread": t.get("bid_ask_spread_percentage"),
                "depth_2pct": (t.get("cost_to_move_up_usd") or 0) + (t.get("cost_to_move_down_usd") or 0),
                "trust": t.get("trust_score"),
                "stale": t.get("is_stale") or t.get("is_anomaly"),
            }
        )
    rows = [r for r in rows if not r["stale"] and r["volume"]]
    return sorted(rows, key=lambda r: -r["volume"])[:20]


def fetch_all():
    out, errors = {}, []
    try:
        print("fetch tao venues")
        out["venues"] = tao_venues()
    except Exception as e:
        print(f"  FAILED tao venues: {e}")
        errors.append(f"tao venues: {e}")
    try:
        print("fetch tao market")
        out["tao"] = tao_market()
    except Exception as e:
        print(f"  FAILED tao market: {e}")
        errors.append(f"tao market: {e}")
    try:
        print("fetch subnets from chain")
        out["chain"] = asyncio.run(asyncio.wait_for(_chain_subnets(), timeout=300))
    except Exception as e:
        print(f"  FAILED chain: {e}")
        errors.append(f"chain: {e}")
    out["errors"] = errors
    return out
