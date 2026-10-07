"""Headlines from free RSS feeds, plus a CLARITY Act tracker.

Every source is optional: a feed that fails is listed in `errors` and the rest
still render. Headlines are tagged by topic (regulation, CLARITY Act, ETFs...)
and by the coins they mention, so the page can filter and link them to charts.
"""

import json
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from urllib.parse import quote_plus
from xml.etree import ElementTree as ET

import requests

from .http import get_json

ROOT = Path(__file__).resolve().parent.parent
UA = {"User-Agent": "Mozilla/5.0 (compatible; crypto-research-engine/0.1)"}
MAX_AGE = timedelta(days=7)


def gnews(q):
    return f"https://news.google.com/rss/search?q={quote_plus(q)}+when:7d&hl=en-US&gl=US&ceid=US:en"


# (source label, url, edition) — edition "crypto" feeds the main page, "tao" the Bittensor page.
FEEDS = [
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/", "crypto"),
    ("Cointelegraph", "https://cointelegraph.com/rss", "crypto"),
    ("The Block", "https://www.theblock.co/rss.xml", "crypto"),
    ("Decrypt", "https://decrypt.co/feed", "crypto"),
    ("Blockworks", "https://blockworks.co/feed", "crypto"),
    ("Google News", gnews('"CLARITY Act" crypto'), "crypto"),
    ("Google News", gnews("crypto market structure bill Senate"), "crypto"),
    ("Google News", gnews("SEC OR CFTC crypto regulation"), "crypto"),
    ("Google News", gnews("Bittensor OR \"TAO subnet\""), "tao"),
]

# Topic tags. Order matters only for display.
TOPICS = [
    ("CLARITY Act", r"\bclarity act\b|\bmarket[- ]structure (?:bill|legislation|law)\b|\bh\.?r\.? ?3633\b"),
    ("Regulation", r"\bsec\b|\bcftc\b|\bregulat|\bcongress|\bsenate\b|\bhouse (?:vote|passes|committee)|\blawmakers?\b|\bbill\b|\btreasury\b|\bgensler|\batkins\b|\bwarren\b|\blummis\b|\btillis\b|\bmica\b|\blegislat"),
    ("Stablecoins", r"\bstablecoins?\b|\bgenius act\b|\busdt\b|\busdc\b|\btether\b"),
    ("ETFs", r"\betfs?\b|\betps?\b"),
    ("Hacks & enforcement", r"\bhack(?:ed|er|s)?\b|\bexploit|\bdrain(?:ed)?\b|\blawsuit|\bsued\b|\bcharged\b|\bsettle"),
    ("Macro", r"\bfed\b|\bfomc\b|\binflation\b|\bcpi\b|\brate (?:cut|hike)|\btariffs?\b|\bpowell\b"),
]
TOPIC_RE = [(name, re.compile(pat, re.I)) for name, pat in TOPICS]

# Symbols that are ordinary English words or too ambiguous to match on their own.
AMBIGUOUS = {"ONE", "SUN", "GAS", "KEY", "HOT", "BIG", "AI", "OM", "S", "IP", "ME", "TON", "PEOPLE",
             "BONK", "JUP", "MOVE", "ZRO", "W", "T", "G", "TRUMP", "HYPE", "PENGU", "FUN", "ACT"}

CLARITY_FILE = ROOT / "data" / "clarity.json"


# ------------------------------------------------------------------ parsing

def _text(el, *names):
    for n in names:
        found = el.find(n)
        if found is not None and (found.text or "").strip():
            return found.text.strip()
    return ""


def _clean(html):
    s = re.sub(r"<[^>]+>", " ", unescape(html or ""))
    return re.sub(r"\s+", " ", s).strip()


def _when(s):
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def parse_feed(xml, source):
    """RSS 2.0 or Atom -> [{title, link, source, published, summary}]."""
    root = ET.fromstring(xml)
    atom = "{http://www.w3.org/2005/Atom}"
    out = []
    items = root.iter("item")
    entries = list(root.iter(f"{atom}entry"))
    for it in items:
        title = _clean(_text(it, "title"))
        src = source
        # Google News appends " - Publisher" and carries it in <source>.
        pub = it.find("source")
        if pub is not None and (pub.text or "").strip():
            src = pub.text.strip()
            if title.endswith(" - " + src):
                title = title[: -len(src) - 3].strip()
        out.append({
            "title": title,
            "link": _text(it, "link"),
            "source": src,
            "published": _when(_text(it, "pubDate", "{http://purl.org/dc/elements/1.1/}date")),
            "summary": "" if source == "Google News" else _clean(_text(it, "description"))[:240],
        })
    for it in entries:
        link = it.find(f"{atom}link")
        out.append({
            "title": _clean(_text(it, f"{atom}title")),
            "link": link.get("href") if link is not None else "",
            "source": source,
            "published": _when(_text(it, f"{atom}published", f"{atom}updated")),
            "summary": _clean(_text(it, f"{atom}summary", f"{atom}content"))[:240],
        })
    return [i for i in out if i["title"] and i["link"]]


def _get(url, retries=2):
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=UA, timeout=20)
            r.raise_for_status()
            return r.content
        except requests.RequestException as e:
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"GET {url} failed: {last}")


# ------------------------------------------------------------------ tagging

def _norm(title):
    return re.sub(r"[^a-z0-9]+", "", title.lower())[:70]


def coin_matchers(coins, top=120):
    """Regexes for coin names and tickers, from the screener's coin list."""
    out = []
    for c in (coins or [])[:top]:
        pats = []
        name = (c.get("name") or "").strip()
        if len(name) >= 4:
            pats.append(r"\b" + re.escape(name) + r"\b")
        sym = (c.get("symbol") or "").upper()
        if len(sym) >= 3 and sym not in AMBIGUOUS:
            # Tickers are matched case-sensitively so "SOL" hits but "sol" doesn't.
            pats.append(r"(?-i:\b" + re.escape(sym) + r"\b)")
        if pats:
            out.append((c, re.compile("|".join(pats), re.I)))
    return out


def tag(item, matchers):
    text = f"{item['title']} {item.get('summary', '')}"
    item["topics"] = [name for name, rx in TOPIC_RE if rx.search(text)]
    if "CLARITY Act" in item["topics"] and "Regulation" not in item["topics"]:
        item["topics"].insert(1, "Regulation")
    item["coins"] = [{"id": c.get("id"), "symbol": (c.get("symbol") or "").upper(), "name": c.get("name")}
                     for c, rx in matchers if rx.search(item["title"])][:4]
    return item


# ------------------------------------------------------------------ CLARITY Act

def clarity(items, errors):
    """Milestones from data/clarity.json, live bill status from GovTrack, latest headlines."""
    base = {}
    try:
        base = json.loads(CLARITY_FILE.read_text())
    except (OSError, ValueError) as e:
        errors.append(f"clarity.json: {e}")
    bill = base.get("bill") or {}
    live = None
    if bill.get("congress") and bill.get("number"):
        try:
            d = get_json("https://www.govtrack.us/api/v2/bill",
                         params={"congress": bill["congress"], "bill_type": bill.get("govtrack_type", "house_bill"),
                                 "number": bill["number"]},
                         headers=UA, retries=2)
            obj = (d.get("objects") or [None])[0] if isinstance(d, dict) else None
            if obj:
                live = {
                    "status": obj.get("current_status_label") or obj.get("current_status"),
                    "date": obj.get("current_status_date"),
                    "description": _clean(obj.get("current_status_description") or ""),
                    "link": obj.get("link"),
                }
        except RuntimeError as e:
            errors.append(f"GovTrack: {str(e)[:80]}")
    headlines = [i for i in items if "CLARITY Act" in i["topics"]][:15]
    return {**base, "live": live, "headlines": headlines}


# ------------------------------------------------------------------ entry point

def fetch_all(coins=None):
    errors, seen, items = [], set(), []
    now = datetime.now(timezone.utc)
    matchers = coin_matchers(coins)
    for source, url, edition in FEEDS:
        try:
            got = parse_feed(_get(url), source)
        except (RuntimeError, ET.ParseError) as e:
            errors.append(f"{source} feed: {str(e)[:80]}")
            continue
        for it in got:
            key = _norm(it["title"])
            if key in seen:
                continue
            if it["published"] and now - it["published"] > MAX_AGE:
                continue
            seen.add(key)
            it["edition"] = edition
            items.append(tag(it, matchers))
        print(f"  {source}: {len(got)} items")

    oldest = datetime(1970, 1, 1, tzinfo=timezone.utc)
    items.sort(key=lambda i: i["published"] or oldest, reverse=True)
    for it in items:
        p = it.pop("published")
        it["ts"] = p.strftime("%Y-%m-%d %H:%M UTC") if p else ""
        it["age"] = _age(now - p) if p else ""

    crypto = [i for i in items if i["edition"] == "crypto"]
    tao = [i for i in items if i["edition"] == "tao" or re.search(r"(?i:\bbittensor\b)|\bTAO\b", i["title"])]
    return {
        "crypto": crypto[:80],
        "tao": tao[:30],
        "clarity": clarity(crypto, errors),
        "topics": [name for name, _ in TOPICS],
        "errors": errors,
    }


def _age(d):
    s = max(d.total_seconds(), 0)
    if s < 3600:
        return f"{int(s // 60)}m ago"
    if s < 86400:
        return f"{int(s // 3600)}h ago"
    return f"{int(s // 86400)}d ago"
