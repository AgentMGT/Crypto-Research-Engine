"""Build the static site. Run: python -m engine.build [crypto|tao|all]"""

import json
import os
import shutil
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from html import escape

from jinja2 import Environment, FileSystemLoader, Undefined, select_autoescape
from markupsafe import Markup

from . import ai_brief, fetch_crypto, fetch_news, fetch_tao, fetch_venues, signals, tao_plan

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site"
DATA = ROOT / "data"
HIST = DATA / "history"
KEEP = 90  # ~30 days at 3 refreshes a day

_venues = None
_news = None

# Yahoo symbol -> TradingView symbol, for the macro rows' click-through charts.
TV_SYMBOLS = {
    "^GSPC": "SP:SPX", "^IXIC": "NASDAQ:IXIC", "^VIX": "TVC:VIX", "DX-Y.NYB": "TVC:DXY",
    "^TNX": "TVC:US10Y", "GC=F": "COMEX:GC1!", "CL=F": "NYMEX:CL1!",
    "COIN": "NASDAQ:COIN", "MSTR": "NASDAQ:MSTR", "IBIT": "NASDAQ:IBIT",
}


def venues():
    """CEX/DEX scan is shared by both editions, so fetch it once per run."""
    global _venues
    if _venues is None:
        print("== venue scan (CEX + DEX)")
        _venues = fetch_venues.fetch_all()
    return _venues


def news(coins=None):
    """Headlines are shared too; coin tagging uses the screener's list when it exists."""
    global _news
    if _news is None:
        print("== news")
        try:
            _news = fetch_news.fetch_all(sorted(coins or [], key=lambda c: c.get("rank") or 9999))
        except Exception as e:  # never let headlines take a page down
            traceback.print_exc()
            _news = {"crypto": [], "tao": [], "clarity": {}, "topics": [], "errors": [f"news: {e}"]}
    return _news


# ------------------------------------------------------------------ helpers

def load_history(edition):
    path = HIST / f"{edition}.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except ValueError:
            return []
    return []


def save_history(edition, history, snapshot):
    history = (history + [snapshot])[-KEEP:]
    HIST.mkdir(parents=True, exist_ok=True)
    (HIST / f"{edition}.json").write_text(json.dumps(history))
    return history


def _missing(v):
    return v is None or isinstance(v, Undefined)


def money(v, dp=2):
    if _missing(v):
        return "–"
    v = float(v)
    for cut, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= cut:
            return f"${v / cut:,.2f}{suf}"
    if abs(v) < 0.01:
        return f"${v:,.6f}".rstrip("0")
    return f"${v:,.{dp}f}"


def num(v, dp=2):
    return "–" if _missing(v) else f"{float(v):,.{dp}f}"


def signed(v, dp=1):
    return "–" if _missing(v) else f"{float(v):+,.{dp}f}%"


def cls(v):
    if _missing(v):
        return "flat"
    return "up" if v > 0 else "down" if v < 0 else "flat"


def spark(series, width=120, height=28):
    pts = [p for p in (series or []) if p is not None]
    if len(pts) < 2:
        return ""
    lo, hi = min(pts), max(pts)
    rng = (hi - lo) or 1
    step = width / (len(pts) - 1)
    coords = " ".join(f"{i * step:.1f},{height - (p - lo) / rng * height:.1f}" for i, p in enumerate(pts))
    trend = "up" if pts[-1] >= pts[0] else "down"
    return (
        f'<svg class="spark {trend}" viewBox="0 0 {width} {height}" preserveAspectRatio="none" '
        f'aria-hidden="true"><polyline points="{coords}" fill="none" stroke="currentColor" '
        f'stroke-width="1.5" vector-effect="non-scaling-stroke"/></svg>'
    )


def chart(spec):
    """Attributes that make an element open the chart panel (see static/app.js)."""
    spec = {k: v for k, v in spec.items() if v not in (None, "") and not isinstance(v, Undefined)}
    return Markup(f' data-chart="{escape(json.dumps(spec, default=str))}" tabindex="0" role="button"')


def series_from_history(history, key, field="price"):
    """Per-item price series from stored snapshots, as {id: [[unix_seconds, value], ...]}."""
    out = {}
    for snap in history:
        try:
            t = int(datetime.strptime(snap["ts"], "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc).timestamp())
        except (KeyError, ValueError):
            continue
        for k, v in (snap.get(key) or {}).items():
            if v.get(field) is not None:
                out.setdefault(k, []).append([t, v[field]])
    return out


def env():
    e = Environment(
        loader=FileSystemLoader(ROOT / "templates"),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    e.filters.update(money=money, num=num, signed=signed, cls=cls, spark=spark, chart=chart)
    # Repo the "Scan now" button triggers; Actions sets GITHUB_REPOSITORY.
    e.globals["repo"] = os.environ.get("GITHUB_REPOSITORY") or "AgentMGT/Crypto-Research-Engine"
    e.tests["contains"] = lambda seq, x: x in (seq or [])
    return e


# ------------------------------------------------------------------ editions

def build_crypto(e, ts):
    print("== crypto edition")
    data = fetch_crypto.fetch_all()
    history = load_history("crypto")
    sig = signals.crypto_signals(data, history)
    v = venues()
    sig["venues"] = signals.venue_signals(v, data.get("global"), sig["coins"])
    n = news(sig["coins"])
    sig["news"] = n
    data["errors"] = (data.get("errors") or []) + (v.get("errors") or []) + (n.get("errors") or [])
    if not sig["coins"]:
        print("  no market data; keeping previous page")
        return None
    history = save_history("crypto", history, signals.crypto_snapshot(sig, ts))
    ai = ai_brief.brief("crypto", sig)
    html = e.get_template("crypto.html").render(
        edition="crypto",
        tv_symbols=TV_SYMBOLS,
        updated=ts,
        sig=sig,
        data=data,
        ai=ai,
        history=history,
        errors=data.get("errors") or [],
    )
    (OUT / "index.html").write_text(html)
    (DATA / "crypto-latest.json").write_text(
        json.dumps({"updated": ts, "regime": sig["regime"], "ideas": sig["ideas"], "coins": sig["coins"], "venues": sig["venues"], "news": sig["news"], "ai": ai}, default=str)
    )
    return sig


def build_tao(e, ts):
    print("== bittensor edition")
    data = fetch_tao.fetch_all()
    history = load_history("tao")
    sig = signals.tao_signals(data, history)
    sig["tao"] = data.get("tao") or {}
    v = venues()
    sig["tao_venues"] = data.get("venues") or []
    sig["tao_perps"] = [r for r in signals.perp_book(v.get("cex_perps"), v.get("dex_perps")) if r["base"] == "TAO"]
    sig["tao_perp_venues"] = sorted(
        [p for p in (v.get("cex_perps") or []) + (v.get("dex_perps") or []) if (p.get("base") or "").upper() == "TAO" and p.get("oi_usd")],
        key=lambda p: -(p["oi_usd"] or 0),
    )[:12]
    sig["news"] = news()
    if not sig["subnets"]:
        print("  no chain data; keeping previous page")
        return None
    history = save_history("tao", history, signals.tao_snapshot(sig, ts, (data.get("tao") or {}).get("price")))
    sig["plan"] = tao_plan.build(sig, sig["tao"], history)
    ai = ai_brief.brief("tao", sig)
    html = e.get_template("tao.html").render(
        edition="tao",
        updated=ts,
        sig=sig,
        tao=sig["tao"],
        ai=ai,
        series={"subnets": series_from_history(history, "subnets"),
                "tao": [[t, v] for t, v in series_from_history(
                    [{"ts": h["ts"], "x": {"tao": {"price": h.get("tao_price")}}} for h in history], "x").get("tao", [])]},
        history=history,
        errors=data.get("errors") or [],
    )
    (OUT / "bittensor.html").write_text(html)
    (DATA / "tao-latest.json").write_text(
        json.dumps({"updated": ts, "eco": sig["eco"], "ideas": sig["ideas"], "subnets": sig["subnets"], "plan": sig["plan"], "ai": ai}, default=str)
    )
    return sig


def build_research(e, ts):
    """The coin research and paper trading pages are static; they load everything in the browser."""
    (OUT / "research.html").write_text(e.get_template("research.html").render(edition="research", updated=ts, errors=[]))
    (OUT / "paper.html").write_text(e.get_template("paper.html").render(edition="paper", updated=ts, errors=[]))


def main():
    which = (sys.argv[1] if len(sys.argv) > 1 else "all").lower()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    OUT.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    e = env()

    built = []
    for name, fn in (("crypto", build_crypto), ("tao", build_tao)):
        if which not in (name, "all"):
            continue
        try:
            built.append((name, fn(e, ts)))
        except Exception:
            # One broken edition must not take the other one down with it.
            traceback.print_exc()
            built.append((name, None))

    build_research(e, ts)
    for name in ("style.css", "app.js", "research.js", "paper.js", "paper-crypto.js", "review.js"):
        src = ROOT / "static" / name
        if src.exists():
            shutil.copy(src, OUT / name)
    for name in ("crypto-latest.json", "tao-latest.json"):
        src = DATA / name
        if src.exists():
            shutil.copy(src, OUT / name)

    ok = [n for n, s in built if s]
    print(f"built: {', '.join(ok) if ok else 'nothing'} -> {OUT}")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
