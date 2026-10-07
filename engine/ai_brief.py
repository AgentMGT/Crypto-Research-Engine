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
- You have no news feed: do not speculate about causes you cannot see in the data.
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
    if edition == "crypto":
        return {
            "regime": signals["regime"],
            "top_by_score": [
                {k: c[k] for k in ("symbol", "name", "rank", "price", "ch24", "ch7", "ch30", "rsi", "range7", "turnover", "score", "tags")}
                for c in sorted(signals["coins"], key=lambda x: -x["score"])[:25]
            ],
            "worst_by_score": [
                {k: c[k] for k in ("symbol", "name", "ch24", "ch7", "ch30", "rsi", "score", "tags")}
                for c in sorted(signals["coins"], key=lambda x: x["score"])[:10]
            ],
        }
    return {
        "tao": {k: signals["tao"][k] for k in ("price", "mcap", "chg_24h", "chg_7d", "chg_30d") if k in signals.get("tao", {})},
        "ecosystem": signals["eco"],
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
