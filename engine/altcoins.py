"""ALTCOINS: an asymmetric-bet scanner for small coins with real use, plus meme coins.

Pipeline, all from free keyless sources:
  1. Universe: CoinGecko markets beyond the top 250 (ranks ~250-1000) plus the
     meme, DePIN, AI and real-world-asset categories, kept to small caps that still trade.
  2. Product check: DefiLlama protocols (a token whose protocol holds TVL has a live product).
  3. Pre-score on market data, then pull CoinGecko detail (description, categories,
     community links, GitHub activity, exchange listings) for the best candidates.
     Details are cached for a day in data/alt/details.json to stay inside rate limits.
  4. Headlines from CoinGape, Changelly, Superhero and Google News that mention a candidate.
  5. Optional AI due diligence (ANTHROPIC_API_KEY): Claude searches the web for each
     top candidate's products, partnerships, staking and community activity.
     Cached for three days per coin in data/alt/ai.json, a few new coins per run.

Telegram, Discord and X have no free read access, so the page links to each
coin's channels and shows what CoinGecko reports about them instead.
"""

import json
import math
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from . import fetch_news
from .fetch_crypto import CG, CG_HEADERS
from .http import get_json

ROOT = Path(__file__).resolve().parent.parent
CFG_FILE = ROOT / "data" / "altcoins.json"
CACHE_DIR = ROOT / "data" / "alt"
DETAILS = CACHE_DIR / "details.json"
AI_CACHE = CACHE_DIR / "ai.json"

FEEDS = [
    ("CoinGape", "https://coingape.com/feed/"),
    ("Changelly", "https://changelly.com/blog/feed/"),
    ("Superhero", "https://www.superhero.com.au/feed/"),
    ("Google News", fetch_news.gnews("altcoin partnership OR mainnet OR listing")),
    ("Google News", fetch_news.gnews("crypto DePIN OR RWA token launch")),
]

UTILITY_CATS = re.compile(
    r"depin|infrastructure|oracle|storage|compute|artificial intelligence|\bai\b|real world assets|rwa|"
    r"payments?|privacy|layer 1|layer 2|interoperability|data availability|gaming|metaverse|identity|"
    r"decentralized exchange|lending|derivatives|liquid staking|bridge|desci|social", re.I)
MEME_CATS = re.compile(r"\bmeme\b", re.I)
STABLE_CATS = re.compile(r"stablecoin|wrapped|bridged|tokenized gold|liquid staking tokens?\b(?! governance)", re.I)

PLATFORM_RX = re.compile(r"\b(platform|protocol|marketplace|network|ecosystem|app|dapp|exchange|wallet)\b", re.I)
TOKEN_USE_RX = re.compile(r"\b(fees?|gas|govern|pay(?:ment)?s?|access|utility|rewards?|collateral|burn)", re.I)
PARTNER_RX = re.compile(r"\bpartner(?:s|ship|ships|ed)?\b|\bintegrat(?:es|ed|ion)\b|\bbacked by\b|\bcollaborat", re.I)
STAKING_RX = re.compile(r"\bstak(?:e|ing|ed|ers?)\b", re.I)
SERVER_RX = re.compile(r"\b(gpus?|compute|computing|servers?|nodes?|storage|bandwidth|hosting|validators?|rpc|vpn|cloud)\b", re.I)
LIVE_RX = re.compile(r"\b(mainnet|live|launched|users|app store|google play|active users|v\d)\b", re.I)


def config():
    return json.loads(CFG_FILE.read_text())


def _load(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _save(path, obj):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, sort_keys=True, default=str))


def _now():
    return datetime.now(timezone.utc)


def _age_hours(ts):
    try:
        return (_now() - datetime.fromisoformat(ts)).total_seconds() / 3600
    except (TypeError, ValueError):
        return 1e9


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


# ------------------------------------------------------------------ fetch

def _markets(params):
    return get_json(f"{CG}/coins/markets", params={
        "vs_currency": "usd", "order": "market_cap_desc", "per_page": 250, "sparkline": "false",
        "price_change_percentage": "24h,7d,30d", **params}, headers=CG_HEADERS)


def universe(cfg, errors):
    """Small caps that still trade, tagged with the CoinGecko category pulls they came from."""
    rows, cats = {}, {}
    pulls = [({"page": p}, None) for p in cfg["market_pages"]] + [({"category": c, "page": 1}, label) for c, label in cfg["categories"].items()]
    for params, label in pulls:
        try:
            got = _markets(params)
        except RuntimeError as e:
            errors.append(f"CoinGecko {params}: {str(e)[:60]}")
            continue
        for c in got:
            if label:
                cats.setdefault(c["id"], set()).add(label)
            rows.setdefault(c["id"], c)
        time.sleep(cfg["sleep_seconds"])
    lo, hi = cfg["mcap_min_usd"], cfg["mcap_max_usd"]
    out = []
    for c in rows.values():
        mc, vol = c.get("market_cap") or 0, c.get("total_volume") or 0
        if not (lo <= mc <= hi) or vol < cfg["min_volume_usd"]:
            continue
        c["_cats"] = sorted(cats.get(c["id"], []))
        out.append(c)
    return out


def llama_protocols(errors):
    """gecko_id -> {tvl, category, name, url} for every DefiLlama protocol with a token."""
    try:
        got = get_json("https://api.llama.fi/protocols")
    except RuntimeError as e:
        errors.append(f"DefiLlama protocols: {str(e)[:60]}")
        return {}
    out = {}
    for p in got:
        gid = p.get("gecko_id")
        if gid:
            out[gid] = {"tvl": p.get("tvl") or 0, "category": p.get("category"), "name": p.get("name"),
                        "slug": p.get("slug"), "chains": (p.get("chains") or [])[:4]}
    return out


def coin_detail(cid):
    d = get_json(f"{CG}/coins/{cid}", params={
        "localization": "false", "tickers": "true", "market_data": "false",
        "community_data": "true", "developer_data": "true", "sparkline": "false"}, headers=CG_HEADERS)
    links = d.get("links") or {}
    tickers = d.get("tickers") or []
    cex = sorted({(t.get("market") or {}).get("name") for t in tickers
                  if (t.get("market") or {}).get("identifier") and not re.search(r"swap|dex|uniswap|pancake|raydium|orca|meteora|aerodrome|curve|sushi|jupiter|camelot|trader", (t.get("market") or {}).get("identifier", ""), re.I)} - {None})
    dex = sorted({(t.get("market") or {}).get("name") for t in tickers} - set(cex) - {None})
    comm, dev = d.get("community_data") or {}, d.get("developer_data") or {}
    tg = links.get("telegram_channel_identifier")
    chats = [u for u in (links.get("chat_url") or []) if u]
    return {
        "fetched": _now().isoformat(),
        "categories": [c for c in (d.get("categories") or []) if c],
        "description": re.sub(r"<[^>]+>", "", ((d.get("description") or {}).get("en") or ""))[:1500],
        "genesis": d.get("genesis_date"),
        "homepage": next((u for u in (links.get("homepage") or []) if u), None),
        "twitter": f"https://x.com/{links['twitter_screen_name']}" if links.get("twitter_screen_name") else None,
        "telegram": f"https://t.me/{tg}" if tg else None,
        "discord": next((u for u in chats if "discord" in u), None),
        "chat": next((u for u in chats if "discord" not in u), None),
        "reddit": links.get("subreddit_url") if (links.get("subreddit_url") or "").rstrip("/").count("/") > 3 else None,
        "github": next((u for u in ((links.get("repos_url") or {}).get("github") or []) if u), None),
        "explorer": next((u for u in (links.get("blockchain_site") or []) if u), None),
        "twitter_followers": comm.get("twitter_followers"),
        "telegram_users": comm.get("telegram_channel_user_count"),
        "watchers": d.get("watchlist_portfolio_users"),
        "sentiment_up": d.get("sentiment_votes_up_percentage"),
        "commits_4w": dev.get("commit_count_4_weeks"),
        "stars": dev.get("stars"),
        "cex": cex[:12], "cex_count": len(cex), "dex": dex[:6],
        "platforms": sorted((d.get("platforms") or {}).keys() - {""})[:4],
    }


def headlines(errors):
    items = []
    for source, url in FEEDS:
        try:
            raw = fetch_news._get(url)
            try:
                got = fetch_news.parse_feed(raw, source)
            except ET.ParseError:
                got = _loose_items(raw, source)  # some blog feeds carry broken HTML
        except RuntimeError as e:
            errors.append(f"{source} feed: {str(e)[:60]}")
            continue
        items += got
    return items


def _loose_items(raw, source):
    text = raw.decode("utf-8", "replace")
    out = []
    for m in re.finditer(r"<item>(.*?)</item>", text, re.S):
        body = m.group(1)
        t = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", body, re.S)
        l = re.search(r"<link>(.*?)</link>", body, re.S)
        d = re.search(r"<pubDate>(.*?)</pubDate>", body, re.S)
        if t and l:
            out.append({"title": fetch_news._clean(t.group(1)), "link": l.group(1).strip(), "source": source,
                        "published": fetch_news._when(d.group(1).strip() if d else None), "summary": ""})
    return out


# ------------------------------------------------------------------ scoring

def _pct(a, b):
    return (a / b - 1) * 100 if a and b else None


def market_view(c, llama, btc7, btc30, trending_ids):
    mc, vol = c.get("market_cap") or 0, c.get("total_volume") or 0
    fdv = c.get("fully_diluted_valuation") or None
    circ, maxs = c.get("circulating_supply") or 0, c.get("max_supply") or None
    ch7 = c.get("price_change_percentage_7d_in_currency") or 0
    ch30 = c.get("price_change_percentage_30d_in_currency") or 0
    lp = llama.get(c["id"])
    return {
        "id": c["id"], "symbol": (c.get("symbol") or "").upper(), "name": c.get("name"), "image": c.get("image"),
        "rank": c.get("market_cap_rank"), "price": c.get("current_price"), "mcap": mc, "fdv": fdv, "volume": vol,
        "fdv_ratio": (fdv / mc) if fdv and mc else None,
        "float_pct": (circ / maxs * 100) if maxs else None, "capped": bool(maxs),
        "turnover": vol / mc * 100 if mc else 0,
        "ch24": c.get("price_change_percentage_24h_in_currency"), "ch7": ch7, "ch30": ch30,
        "rs30": ch30 - btc30, "rs7": ch7 - btc7,
        "from_ath": c.get("ath_change_percentage"),
        "cats": c.get("_cats") or [],
        "tvl": lp["tvl"] if lp else None, "llama": lp,
        "trending": c["id"] in trending_ids,
    }


def pre_score(v, cfg):
    """Market-data-only score, used to pick who gets a detail lookup."""
    small = clamp(1 - math.log10(max(v["mcap"], 1) / cfg["mcap_min_usd"]) / math.log10(cfg["mcap_max_usd"] / cfg["mcap_min_usd"]))
    unlock = 1 if v["fdv_ratio"] is None else clamp((4 - v["fdv_ratio"]) / 3)
    mom = clamp((v["rs30"] + 20) / 60)
    product = 1 if (v["tvl"] or 0) >= cfg["live_tvl_usd"] else 0
    util = 1 if any(c != "Meme" for c in v["cats"]) else 0
    return 30 * small + 15 * unlock + 20 * mom + 20 * product + 10 * util + (5 if v["trending"] else 0)


def checks(v, d, cfg):
    """The criteria Marvin listed, each with what the data shows. ok: True / False / None (can't tell)."""
    d = d or {}
    desc = d.get("description") or ""
    cats = " ".join(d.get("categories") or []) + " " + " ".join(v["cats"])
    tvl = v["tvl"] or 0
    lp = v.get("llama") or {}
    out = []

    def add(key, label, ok, note):
        out.append({"key": key, "label": label, "ok": ok, "note": note})

    add("small", "Small, early cap", v["mcap"] <= cfg["small_cap_usd"],
        f"Market cap {_m(v['mcap'])}" + (f", FDV {_m(v['fdv'])}" if v["fdv"] else ""))
    add("liquidity", "Low liquidity (early)", v["volume"] <= cfg["low_liquidity_usd"],
        f"{_m(v['volume'])} traded in 24h")
    real = bool(UTILITY_CATS.search(cats)) and not MEME_CATS.search(cats)
    add("use", "Real-world use case", real or tvl >= cfg["live_tvl_usd"] or None,
        ", ".join((d.get("categories") or v["cats"])[:3]) or "No category listed")
    live_bits = []
    if tvl >= cfg["live_tvl_usd"]:
        live_bits.append(f"{_m(tvl)} locked in {lp.get('name')} ({lp.get('category')})")
    if (d.get("commits_4w") or 0) > 0:
        live_bits.append(f"{d['commits_4w']} GitHub commits in 4 weeks")
    if LIVE_RX.search(desc):
        live_bits.append("description mentions a live product")
    add("live", "Live products", True if live_bits else (None if not d else False), "; ".join(live_bits) or "No product evidence found")
    plat = bool(PLATFORM_RX.search(desc) and TOKEN_USE_RX.search(desc))
    add("platform", "Powers a platform", True if plat else (None if not d else False),
        "Token is used for fees, access or governance on a platform" if plat else "Not clear from the description")
    partner = bool(PARTNER_RX.search(desc))
    add("partners", "Active partnerships", True if partner else None,
        "Description mentions partners or integrations (verify they are current)" if partner else "Check announcements and X")
    stake = bool(STAKING_RX.search(desc) or re.search(r"staking", cats, re.I) or "staking" in (lp.get("category") or "").lower())
    add("staking", "Offers staking", True if stake else (None if not d else False),
        "Staking mentioned" if stake else "No staking found")
    srv = bool(SERVER_RX.search(desc) or re.search(r"depin|compute|storage", cats, re.I))
    add("servers", "Access to servers / compute", True if srv else (None if not d else False),
        "Nodes, compute, storage or bandwidth" if srv else "No infrastructure access found")
    scarce = (v["float_pct"] is not None and v["float_pct"] >= cfg["scarce_float_pct"]) or (v["fdv_ratio"] is not None and v["fdv_ratio"] <= 1.3)
    add("exclusive", "Scarcity / exclusivity", True if scarce else (False if v["fdv_ratio"] and v["fdv_ratio"] > 3 else None),
        (f"{v['float_pct']:.0f}% of capped supply circulating" if v["float_pct"] is not None else
         f"FDV is {v['fdv_ratio']:.1f}x market cap" if v["fdv_ratio"] else "Supply data missing"))
    socials = [k for k in ("twitter", "telegram", "discord") if d.get(k)]
    comm_note = ", ".join({"twitter": "X", "telegram": "Telegram", "discord": "Discord"}[k] for k in socials) or "no channels listed"
    if d.get("watchers"):
        comm_note += f"; {d['watchers']:,} CoinGecko watchlists"
    if d.get("telegram_users"):
        comm_note += f"; {d['telegram_users']:,} Telegram members"
    add("community", "Active community channels", True if len(socials) >= 2 else (None if not d else False), comm_note)
    return out


def flags(v, d, cfg):
    d = d or {}
    out = []
    if v["volume"] < cfg["thin_volume_usd"]:
        out.append("Very thin trading: hard to exit")
    if v["fdv_ratio"] and v["fdv_ratio"] > 4:
        out.append(f"Big unlocks ahead: FDV {v['fdv_ratio']:.1f}x market cap")
    if v["ch30"] is not None and v["ch30"] < -50:
        out.append(f"Down {abs(v['ch30']):.0f}% in 30 days")
    if d and not any(d.get(k) for k in ("twitter", "telegram", "discord")):
        out.append("No community channels listed")
    if d and d.get("cex_count", 0) <= 1 and not d.get("dex"):
        out.append("Barely listed anywhere")
    if d.get("genesis"):
        try:
            age = (_now().date() - datetime.fromisoformat(d["genesis"]).date()).days
            if age < 90:
                out.append(f"Only {age} days old")
        except ValueError:
            pass
    return out


def score(v, d, chk, news_n, meme):
    ok = {c["key"]: c["ok"] for c in chk}
    small = 1 if ok.get("small") else 0.4
    momentum = clamp((v["rs30"] + 20) / 60) * 0.6 + clamp((v["rs7"] + 10) / 30) * 0.4
    watchers = clamp(math.log10(max((d or {}).get("watchers") or 1, 1)) / 5)
    community = (sum(1 for k in ("twitter", "telegram", "discord") if (d or {}).get(k)) / 3) * 0.5 + watchers * 0.5
    attention = clamp(news_n / 3) * 0.6 + (0.4 if v["trending"] else 0)
    if meme:
        s = 40 * community + 30 * momentum + 15 * clamp(math.log10(max(v["volume"], 1) / 1e5) / 2) + 15 * small
    else:
        util = sum(1 for k, w in (("use", 1), ("live", 2), ("platform", 1), ("staking", 1), ("servers", 1), ("partners", 1)) for _ in range(w) if ok.get(k)) / 7
        scarce = 1 if ok.get("exclusive") else (0 if ok.get("exclusive") is False else 0.5)
        s = 35 * util + 15 * small + 10 * scarce + 15 * community + 15 * momentum + 10 * attention
    return round(s)


def _m(x):
    if x is None:
        return "–"
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(x) >= div:
            return f"${x / div:.1f}{suf}"
    return f"${x:,.0f}"


# ------------------------------------------------------------------ AI due diligence

AI_SYSTEM = """You do due diligence on small crypto tokens for an investor who makes small, \
asymmetric bets. For the token given, search the web and check, citing what you find:
- Does the token power a real platform or product, and is it live (users, app, mainnet)?
- Real-world use case: what problem does it solve, and for whom?
- Active partnerships: named, recent (last 6 months), and verifiable. Ignore vague "partnerships".
- Staking, node or server access: what holders can actually do with it.
- Exclusivity: is there little competition, a moat, or a capped supply?
- Community: recent activity on X, Telegram or Discord, as far as search shows it.
- Red flags: team anonymity, unlocks, exploits, delistings, lawsuits, dead socials.
Be skeptical and concrete. Never invent a partnership, product or number. If you can't verify \
something, say so. Research only: no buy or sell instructions, no price targets.

Finish with one JSON object on its own, and nothing after it:
{"verdict": "strong" | "promising" | "weak" | "avoid",
 "summary": "2-3 sentences",
 "platform": "...", "partnerships": "...", "staking": "...", "community": "...",
 "red_flags": ["..."],
 "sources": [{"title": "...", "url": "..."}]}"""


def ai_diligence(cands, cfg):
    cache = _load(AI_CACHE, {})
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not cfg.get("ai_due_diligence"):
        return cache  # off until switched on in data/altcoins.json (it spends API credit on web searches)
    if not key:
        print("  no ANTHROPIC_API_KEY, skipping AI due diligence")
        return cache
    try:
        import anthropic
    except ImportError:
        return cache
    client = anthropic.Anthropic()
    todo = [c for c in cands if _age_hours((cache.get(c["id"]) or {}).get("ts")) > cfg["ai_cache_hours"]][: cfg["ai_per_run"]]
    for c in todo:
        print(f"  AI due diligence: {c['symbol']}")
        d = c.get("detail") or {}
        prompt = (f"Token: {c['name']} ({c['symbol']}), CoinGecko id {c['id']}. Market cap {_m(c['mcap'])}, "
                  f"24h volume {_m(c['volume'])}. Website {d.get('homepage')}. X {d.get('twitter')}. "
                  f"Categories: {', '.join(d.get('categories') or c['cats'])}.\nDescription: {(d.get('description') or '')[:800]}")
        messages = [{"role": "user", "content": prompt}]
        try:
            for _ in range(3):  # server-side search loop can pause; resume up to twice
                msg = client.messages.create(
                    model=cfg["ai_model"], max_tokens=6000, system=AI_SYSTEM, messages=messages,
                    tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 6}])
                if msg.stop_reason != "pause_turn":
                    break
                messages = [messages[0], {"role": "assistant", "content": msg.content}]
        except Exception as e:
            print(f"  AI due diligence failed for {c['symbol']}: {e}")
            continue
        text = "".join(b.text for b in msg.content if b.type == "text")
        m = re.search(r"\{[^{}]*\"verdict\".*\}\s*$", text, re.S)
        try:
            out = json.loads(m.group(0)) if m else None
        except ValueError:
            out = None
        if out:
            out["ts"] = _now().isoformat()
            out["model"] = cfg["ai_model"]
            cache[c["id"]] = out
    _save(AI_CACHE, cache)
    return cache


# ------------------------------------------------------------------ entry point

def build(crypto_sig, crypto_data):
    cfg = config()
    errors = []
    markets = (crypto_data or {}).get("markets") or []
    btc = next((c for c in markets if c.get("id") == "bitcoin"), {})
    btc7 = btc.get("price_change_percentage_7d_in_currency") or 0
    btc30 = btc.get("price_change_percentage_30d_in_currency") or 0
    trending_ids = {t.get("id") for t in (crypto_data or {}).get("trending") or []}

    raw = universe(cfg, errors)
    llama = llama_protocols(errors)
    views = []
    for c in raw:
        cats = " ".join(c.get("_cats") or [])
        if STABLE_CATS.search(cats) or re.search(r"usd|eur|wrapped|bridged|staked", c.get("name") or "", re.I):
            continue
        views.append(market_view(c, llama, btc7, btc30, trending_ids))
    for v in views:
        v["meme"] = "Meme" in v["cats"]
        v["pre"] = pre_score(v, cfg)

    # Headlines that name a candidate.
    items = headlines(errors)
    matchers = fetch_news.coin_matchers(sorted(views, key=lambda v: -v["pre"]), top=600)
    for v in views:
        v["news"] = []
    by_id = {v["id"]: v for v in views}
    seen = set()
    for it in items:
        key = fetch_news._norm(it["title"])
        if key in seen:
            continue
        seen.add(key)
        for c, rx in matchers:
            if rx.search(it["title"]):
                p = it.get("published")
                by_id[c["id"]]["news"].append({"title": it["title"], "link": it["link"], "source": it["source"],
                                              "ts": p.strftime("%Y-%m-%d") if p else ""})

    for v in views:
        v["pre"] += 10 * min(len(v["news"]), 2)  # in the news: check it sooner

    # Detail lookups for the best of each kind, reusing anything fetched in the last day.
    cache = {k: x for k, x in _load(DETAILS, {}).items() if _age_hours(x.get("fetched")) < cfg["detail_cache_hours"] * 3}
    util = sorted([v for v in views if not v["meme"]], key=lambda v: -v["pre"])[: cfg["detail_utility"]]
    memes = sorted([v for v in views if v["meme"]], key=lambda v: -v["pre"])[: cfg["detail_meme"]]
    fetched = 0
    # A quarter of each run's lookups go to meme coins so neither list starves the other.
    budget = {False: cfg["detail_per_run"] - cfg["detail_per_run"] // 4, True: cfg["detail_per_run"] // 4}
    for v in memes + util:
        fresh = _age_hours((cache.get(v["id"]) or {}).get("fetched")) < cfg["detail_cache_hours"]
        if not fresh and budget[v["meme"]] > 0:
            budget[v["meme"]] -= 1
            try:
                cache[v["id"]] = coin_detail(v["id"])
                fetched += 1
                time.sleep(cfg["sleep_seconds"])
            except RuntimeError as e:
                errors.append(f"detail {v['id']}: {str(e)[:50]}")
        v["detail"] = cache.get(v["id"])
    _save(DETAILS, cache)
    print(f"  altcoins: {len(views)} in universe, {fetched} detail lookups, {len(cache)} cached")

    for v in views:
        d = v.get("detail")
        if d and MEME_CATS.search(" ".join(d.get("categories") or [])):
            v["meme"] = True
        v["checks"] = checks(v, d, cfg)
        if v["meme"]:  # utility questions don't apply to meme coins
            v["checks"] = [c for c in v["checks"] if c["key"] in ("small", "liquidity", "exclusive", "community")]
        v["flags"] = flags(v, d, cfg)
        v["passed"] = sum(1 for c in v["checks"] if c["ok"])
        v["passed_pct"] = round(100 * v["passed"] / len(v["checks"]))
        v["score"] = score(v, d, v["checks"], len(v["news"]), v["meme"]) if d else round(v["pre"] * 0.6)
        v["news"] = v["news"][:4]

    ranked_util = sorted([v for v in views if not v["meme"] and v.get("detail")], key=lambda v: -v["score"])
    ranked_meme = sorted([v for v in views if v["meme"] and v.get("detail")], key=lambda v: -v["score"])
    ai = ai_diligence(ranked_util[: cfg["ai_top"]] + ranked_meme[:2], cfg)
    for v in views:
        v["ai"] = ai.get(v["id"])

    dex = []
    for p in ((crypto_sig or {}).get("venues") or {}).get("dex_new", []) + ((crypto_sig or {}).get("venues") or {}).get("dex_trending", []):
        if p.get("liquidity") and p["liquidity"] <= cfg["dex_max_liquidity_usd"] and "Sell pressure" not in p.get("tags", []):
            dex.append(p)
    seen_pools, dex_rows = set(), []
    for p in sorted(dex, key=lambda p: -(p.get("score") or 0)):
        if p["address"] not in seen_pools:
            seen_pools.add(p["address"])
            dex_rows.append(p)

    slim = lambda v: {k: v.get(k) for k in ("id", "symbol", "name", "image", "rank", "price", "mcap", "fdv", "volume", "fdv_ratio",
                                           "float_pct", "turnover", "ch24", "ch7", "ch30", "from_ath", "cats", "tvl", "trending",
                                           "meme", "score", "passed", "passed_pct", "checks", "flags", "news", "ai")} | {
        "detail": {k: (v.get("detail") or {}).get(k) for k in ("homepage", "twitter", "telegram", "discord", "chat", "reddit", "github",
                                                              "explorer", "categories", "watchers", "telegram_users", "commits_4w",
                                                              "cex", "cex_count", "dex", "platforms", "genesis")} if v.get("detail") else None,
        "llama_slug": (v.get("llama") or {}).get("slug")}
    return {
        "cfg": {k: cfg[k] for k in ("mcap_min_usd", "mcap_max_usd", "small_cap_usd", "low_liquidity_usd", "min_volume_usd", "plan")},
        "utility": [slim(v) for v in ranked_util[: cfg["show_utility"]]],
        "meme": [slim(v) for v in ranked_meme[: cfg["show_meme"]]],
        "universe": [{k: v.get(k) for k in ("id", "symbol", "name", "mcap", "volume", "fdv_ratio", "ch7", "ch30", "tvl", "meme", "score", "cats")}
                     for v in sorted(views, key=lambda v: -v["score"])],
        "dex": dex_rows[:20],
        "headline_count": len(seen),
        "ai_on": bool(os.environ.get("ANTHROPIC_API_KEY")) and bool(cfg.get("ai_due_diligence")),
        "errors": errors,
    }
