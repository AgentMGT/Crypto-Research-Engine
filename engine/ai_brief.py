"""Optional AI commentary. Without ANTHROPIC_API_KEY the site still builds —
every page falls back to the rule-based signals alone.
"""

import json
import os

MODEL = "claude-opus-5-5"

SYSTEM = """You are the research analyst for a self-updating crypto research site that \
refreshes three times a day. You are given a JSON snapshot of market state and \
rule-based signals computed from it.

Write for an experienced investor who wants a fast, honest read. Rules:
- Ground every claim in the numbers provided. Never invent a number, a headline, or an event.
- You also get recent headlines (titles only). Use them as context for what the market may be \
reacting to, attribute them to their source, and never treat a headline as confirmed fact or \
infer details beyond its title. Don't speculate about causes that neither the data nor a headline shows.
- Say plainly when the data is mixed or thin.
- Research and risk framing only. No "buy X" instructions, no price targets, no leverage advice.

Return JSON only, matching this shape:
{"headline": "<= 90 chars",
 "summary": "2-3 sentences on the current regime",
 "watchlist": [{"name": "...", "why": "one sentence tied to the figures"}],
 "risks": ["..."],
 "questions": ["what a researcher should check next"]}
Keep watchlist to at most 5 entries, risks and questions to 3 each."""


def _payload(edition, signals):
    """Trim the snapshot to what the model needs — keeps the prompt small and cheap."""
    news = signals.get("news") or {}
    heads = lambda items, n: [{k: i.get(k) for k in ("title", "source", "ts", "topics")} for i in (items or [])[:n]]
    if edition == "crypto":
        return {
            "headlines": heads(news.get("crypto"), 25),
            "clarity_act": {"status": (news.get("clarity") or {}).get("status_note"),
                            "headlines": heads((news.get("clarity") or {}).get("headlines"), 6)},
            "regime": signals["regime"],
            "top_by_score": [
                {k: c[k] for k in ("symbol", "name", "rank", "price", "ch24", "ch7", "ch30", "rsi", "range7", "turnover", "score", "tags")}
                for c in sorted(signals["coins"], key=lambda x: -x["score"])[:25]
            ],
            "venues": {
                "dex_share_of_spot_pct": signals["venues"]["dex_share"],
                "total_perp_oi_usd": signals["venues"]["total_oi"],
                "crowded_longs": [{k: r[k] for k in ("base", "funding_8h", "oi_usd", "hl_funding_8h", "oi_to_mcap")} for r in signals["venues"]["crowded"]],
                "shorts_paying": [{k: r[k] for k in ("base", "funding_8h", "oi_usd", "hl_funding_8h", "oi_to_mcap")} for r in signals["venues"]["squeeze"]],
                "dex_trending": [{k: p[k] for k in ("name", "network", "dex", "liquidity", "vol_24h", "ch_24h", "buy_ratio", "tags")} for p in signals["venues"]["dex_trending"][:10]],
            } if signals.get("venues") else None,
            "worst_by_score": [
                {k: c[k] for k in ("symbol", "name", "ch24", "ch7", "ch30", "rsi", "score", "tags")}
                for c in sorted(signals["coins"], key=lambda x: x["score"])[:10]
            ],
        }
    return {
        "headlines": heads(news.get("tao"), 12),
        "trading_plan": {
            "regime": {k: (signals.get("plan") or {}).get("regime", {}).get(k) for k in ("title", "stance", "reasons", "basket_cap_pct")},
            "entry_candidates": [{k: r[k] for k in ("netuid", "name", "setup", "ema_premium", "daily_flow_tao", "em_to_mcap", "pool_tao")}
                                 for r in (signals.get("plan") or {}).get("entries", [])],
            "trim": [r["netuid"] for r in (signals.get("plan") or {}).get("rows", []) if r["status"] == "Trim"],
        },
        "tao": {k: signals["tao"][k] for k in ("price", "mcap", "chg_24h", "chg_7d", "chg_30d") if k in signals.get("tao", {})},
        "ecosystem": signals["eco"],
        "tao_perps": signals.get("tao_perps"),
        "tao_spot_venues": (signals.get("tao_venues") or [])[:8],
        "top_subnets": [
            {k: s[k] for k in ("netuid", "name", "price_tao", "ema_premium", "daily_flow_tao", "em_share", "em_to_mcap", "mcap_tao", "tao_liquidity", "ch_1d", "ch_7d", "score", "tags")}
            for s in sorted(signals["subnets"], key=lambda x: -x["score"])[:25]
        ],
        "weakest_subnets": [
            {k: s[k] for k in ("netuid", "name", "ema_premium", "daily_flow_tao", "em_share", "score", "tags")}
            for s in sorted(signals["subnets"], key=lambda x: x["score"])[:10]
        ],
    }


def brief(edition, signals):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("  no ANTHROPIC_API_KEY, skipping AI brief")
        return None
    try:
        import anthropic
    except ImportError:
        print("  anthropic SDK not installed, skipping AI brief")
        return None

    client = anthropic.Anthropic()
    label = "crypto and broader markets" if edition == "crypto" else "Bittensor (TAO) and its subnets"
    try:
        with client.messages.stream(
            model=MODEL,
            max_tokens=8000,
            system=SYSTEM,
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[
                {
                    "role": "user",
                    "content": f"Edition: {label}.\n\nSnapshot:\n{json.dumps(_payload(edition, signals), default=str)}",
                }
            ],
        ) as stream:
            msg = stream.get_final_message()
    except TypeError:
        # Older SDK without the fallbacks/betas parameters.
        with client.messages.stream(
            model=MODEL,
            max_tokens=8000,
            system=SYSTEM,
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[
                {
                    "role": "user",
                    "content": f"Edition: {label}.\n\nSnapshot:\n{json.dumps(_payload(edition, signals), default=str)}",
                }
            ],
        ) as stream:
            msg = stream.get_final_message()
    except Exception as e:
        print(f"  AI brief failed: {e}")
        return None

    if msg.stop_reason == "refusal":
        print("  AI brief declined by safety classifier")
        return None
    text = "".join(b.text for b in msg.content if b.type == "text")
    try:
        out = json.loads(text)
    except ValueError:
        print("  AI brief returned unparseable JSON")
        return None
    out["model"] = MODEL
    return out


SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["headline", "summary", "watchlist", "risks", "questions"],
    "properties": {
        "headline": {"type": "string"},
        "summary": {"type": "string"},
        "watchlist": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "why"],
                "properties": {"name": {"type": "string"}, "why": {"type": "string"}},
            },
        },
        "risks": {"type": "array", "items": {"type": "string"}},
        "questions": {"type": "array", "items": {"type": "string"}},
    },
}
