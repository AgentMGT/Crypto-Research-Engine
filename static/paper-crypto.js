// Crypto paper trading: any CoinGecko coin, long or short, with stops, targets and
// strategy tags. Live prices while the page is open; the account lives in localStorage.
(function () {
  var KEY = "paper-crypto-v1";
  var CG = "https://api.coingecko.com/api/v3";
  var $ = function (id) { return document.getElementById(id); };
  if (!$("pc-main")) return;
  var acct = null, prices = {}, coin = null, dir = "long", snapCoins = [], items = [], active = -1, searchTimer = null, token = 0;

  // ------------------------------------------------------------ helpers
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function n(v, dp) {
    if (v == null || isNaN(v)) return "–";
    return Number(v).toLocaleString("en-US", { minimumFractionDigits: dp == null ? 2 : dp, maximumFractionDigits: dp == null ? 2 : dp });
  }
  function usd(v) {
    if (v == null || isNaN(v)) return "–";
    var a = Math.abs(v), dp = a >= 1 ? 2 : a >= 0.01 ? 4 : a >= 0.0001 ? 6 : 8;
    return (v < 0 ? "-$" : "$") + n(a, dp);
  }
  function sgn(v, suffix, dp) {
    if (v == null || isNaN(v)) return '<span class="flat">–</span>';
    var c = v > 1e-9 ? "up" : v < -1e-9 ? "down" : "flat";
    return '<span class="' + c + '">' + (v > 0 ? "+" : v < 0 ? "-" : "") + (suffix === "$" ? "$" + n(Math.abs(v)) : n(Math.abs(v), dp == null ? 1 : dp) + suffix) + "</span>";
  }
  function getJSON(url) {
    return fetch(url, { headers: { Accept: "application/json" } }).then(function (r) {
      if (r.status === 429) throw new Error("CoinGecko is rate-limiting this browser; prices will retry in a minute");
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }
  function save() { try { localStorage.setItem(KEY, JSON.stringify(acct)); } catch (e) { alert("Couldn't save: your browser blocked storage."); } }
  function load() { try { return JSON.parse(localStorage.getItem(KEY) || "null"); } catch (e) { return null; } }
  function stamp(ms) { return new Date(ms).toISOString().slice(0, 16).replace("T", " ") + " UTC"; }
  function fee() { return (acct.fee != null ? acct.fee : 0.1) / 100; }
  function slip(id, notional) {
    var v = (prices[id] || {}).usd_24h_vol;
    return v ? Math.min(0.2, 10 * notional / v) : 0.005;
  }
  function setStatus(t) { $("pc-status").innerHTML = t; }

  // ------------------------------------------------------------ prices
  function ids() {
    var s = {};
    acct.lots.forEach(function (l) { s[l.id] = 1; });
    if (coin) s[coin.id] = 1;
    return Object.keys(s);
  }
  function poll() {
    var list = ids();
    if (!list.length) { setStatus("Pick a coin to see its live price."); return Promise.resolve(); }
    return getJSON(CG + "/simple/price?ids=" + list.map(encodeURIComponent).join(",") + "&vs_currencies=usd&include_24hr_vol=true&include_24hr_change=true")
      .then(function (d) {
        Object.keys(d).forEach(function (k) { prices[k] = { usd: d[k].usd, usd_24h_vol: d[k].usd_24h_vol, usd_24h_change: d[k].usd_24h_change, t: Date.now() }; });
        checkTriggersLive();
        sampleEquity();
        acct.lastCheck = Date.now();
        save();
        setStatus("Live prices from CoinGecko · updated " + new Date().toLocaleTimeString() + " · refreshes every minute");
        render();
      })
      .catch(function (e) { setStatus("Prices: " + esc(e.message) + "."); });
  }

  // ------------------------------------------------------------ accounting
  function lotValue(l, p) {
    p = p != null ? p : (prices[l.id] || {}).usd;
    if (p == null) return l.cost;
    return l.dir === "long" ? l.qty * p : l.cost + l.qty * (l.entry - p);
  }
  function equity() { return acct.cash + acct.lots.reduce(function (t, l) { return t + lotValue(l); }, 0); }

  function openLot() {
    var size = parseFloat($("pc-amt").value), p = (prices[coin.id] || {}).usd;
    if (!(size > 0) || p == null || size > acct.cash + 1e-9) return;
    var s = slip(coin.id, size), f = size * fee();
    var fill = dir === "long" ? p * (1 + s) : p * (1 - s);
    var sl = parseFloat($("pc-sl").value), tp = parseFloat($("pc-tp").value);
    var lot = {
      lid: acct.nextLid++, id: coin.id, symbol: coin.symbol, name: coin.name, thumb: coin.thumb, dir: dir,
      qty: (size - f) / fill, entry: fill, cost: size, opened: Date.now(),
      sl: sl > 0 ? (dir === "long" ? fill * (1 - sl / 100) : fill * (1 + sl / 100)) : null,
      tp: tp > 0 ? (dir === "long" ? fill * (1 + tp / 100) : fill * (1 - tp / 100)) : null,
      strategy: $("pc-strat").value.trim() || "Untagged", note: $("pc-note").value.trim(),
      slPct: sl > 0 ? sl : null, tpPct: tp > 0 ? tp : null, eqAtOpen: equity()
    };
    acct.cash -= size;
    acct.lots.push(lot);
    acct.trades.unshift({ type: "Open", t: lot.opened, lid: lot.lid, id: lot.id, symbol: lot.symbol, dir: dir, size: size, price: fill, slip: s * 100, fee: f, strategy: lot.strategy, note: lot.note });
    $("pc-amt").value = ""; $("pc-note").value = "";
    sampleEquity(true); save(); render();
  }

  // Close `frac` of a lot at market, or at a trigger level.
  function closeLot(lid, frac, reason, levelPrice, when) {
    var l = acct.lots.filter(function (x) { return x.lid === lid; })[0];
    if (!l) return;
    var p = levelPrice != null ? levelPrice : (prices[l.id] || {}).usd;
    if (p == null) { alert("No live price for " + l.symbol + " yet."); return; }
    var q = l.qty * frac, part = l.cost * frac, s = slip(l.id, q * p);
    var fill = l.dir === "long" ? p * (1 - s) : p * (1 + s);
    var gross = l.dir === "long" ? q * fill : part + q * (l.entry - fill);
    var f = q * fill * fee(), proceeds = Math.max(0, gross - f);
    acct.cash += proceeds;
    var pnl = proceeds - part;
    acct.trades.unshift({
      type: "Close", t: when || Date.now(), lid: l.lid, id: l.id, symbol: l.symbol, dir: l.dir, size: proceeds, price: fill, slip: s * 100, fee: f,
      pnl: pnl, pnlPct: pnl / part * 100, held: (when || Date.now()) - l.opened, reason: reason, strategy: l.strategy, note: l.note, entry: l.entry,
      opened: l.opened, frac: frac, cost: part, slPct: l.slPct, tpPct: l.tpPct, sizePct: l.eqAtOpen ? l.cost / frac / l.eqAtOpen * 100 : null
    });
    l.qty -= q; l.cost -= part;
    if (frac >= 0.9999 || l.qty <= 1e-12) acct.lots = acct.lots.filter(function (x) { return x.lid !== lid; });
    sampleEquity(true);
  }

  function hit(l, p) {
    if (l.dir === "long") {
      if (l.sl != null && p <= l.sl) return ["Stop", l.sl];
      if (l.tp != null && p >= l.tp) return ["Target", l.tp];
    } else {
      if (l.sl != null && p >= l.sl) return ["Stop", l.sl];
      if (l.tp != null && p <= l.tp) return ["Target", l.tp];
    }
    return null;
  }
  function checkTriggersLive() {
    acct.lots.slice().forEach(function (l) {
      var p = (prices[l.id] || {}).usd, h = p != null && hit(l, p);
      if (h) closeLot(l.lid, 1, h[0], h[0] === "Stop" && (l.dir === "long" ? p < h[1] : p > h[1]) ? p : h[1]);
    });
  }
  // While you were away: replay price history since the last check for lots with stops or targets.
  function replay() {
    var since = acct.lastCheck || Date.now();
    var lots = acct.lots.filter(function (l) { return l.sl != null || l.tp != null; });
    var byCoin = {};
    lots.forEach(function (l) { (byCoin[l.id] = byCoin[l.id] || []).push(l); });
    var coins = Object.keys(byCoin);
    if (!coins.length || Date.now() - since < 5 * 60000) return Promise.resolve(0);
    var days = Math.min(90, Math.max(1, Math.ceil((Date.now() - since) / 86400000)));
    var filled = 0;
    return coins.reduce(function (chain, id) {
      return chain.then(function () {
        return getJSON(CG + "/coins/" + encodeURIComponent(id) + "/market_chart?vs_currency=usd&days=" + days).then(function (d) {
          (d.prices || []).forEach(function (pt) {
            byCoin[id].forEach(function (l) {
              if (pt[0] <= Math.max(since, l.opened) || !acct.lots.some(function (x) { return x.lid === l.lid; })) return;
              var h = hit(l, pt[1]);
              if (h) {
                // A gap through a stop fills at the worse bar price, not the level.
                var gap = h[0] === "Stop" && (l.dir === "long" ? pt[1] < h[1] : pt[1] > h[1]);
                closeLot(l.lid, 1, h[0] + " (while away)", gap ? pt[1] : h[1], pt[0]);
                filled++;
              }
            });
          });
        }).catch(function () {});
      });
    }, Promise.resolve()).then(function () { return filled; });
  }

  function sampleEquity(force) {
    var e = equity(), last = acct.equity[acct.equity.length - 1];
    if (!force && last && Date.now() - last.t < 30 * 60000) { last.v = e; return; }
    acct.equity.push({ t: Date.now(), v: e });
    if (acct.equity.length > 2000) acct.equity.splice(0, acct.equity.length - 2000);
  }

  // ------------------------------------------------------------ search
  fetch("crypto-latest.json").then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
    snapCoins = ((d || {}).coins || []).map(function (c) { return { id: c.id, name: c.name, symbol: c.symbol.toUpperCase(), rank: c.rank, thumb: c.image }; });
  }).catch(function () {});

  function suggest(arr) {
    items = arr; active = arr.length ? 0 : -1;
    var list = $("pc-suggest");
    if (!arr.length) { list.hidden = true; return; }
    list.innerHTML = arr.map(function (c, i) {
      return '<li role="option" data-i="' + i + '"' + (i === 0 ? ' aria-selected="true"' : "") + ">" +
        (c.thumb ? '<img src="' + esc(c.thumb) + '" alt="" width="20" height="20">' : '<span class="ph"></span>') +
        '<span class="s">' + esc(c.symbol) + '</span><span class="n">' + esc(c.name) + '</span><span class="r">' + (c.rank ? "#" + c.rank : "") + "</span></li>";
    }).join("");
    list.hidden = false;
  }
  $("pc-q").addEventListener("input", function () {
    var term = this.value.trim(), t = term.toLowerCase();
    clearTimeout(searchTimer);
    var local = !t ? [] : snapCoins.filter(function (c) { return c.symbol.toLowerCase().indexOf(t) === 0 || c.name.toLowerCase().indexOf(t) === 0; })
      .sort(function (a, b) { return (a.symbol.toLowerCase() === t ? 0 : 1) - (b.symbol.toLowerCase() === t ? 0 : 1) || a.rank - b.rank; }).slice(0, 6);
    suggest(local);
    if (term.length < 2) return;
    var my = ++token;
    searchTimer = setTimeout(function () {
      getJSON(CG + "/search?query=" + encodeURIComponent(term)).then(function (d) {
        if (my !== token) return;
        var seen = {};
        local.forEach(function (c) { seen[c.id] = 1; });
        suggest(local.concat((d.coins || []).filter(function (c) { return !seen[c.id]; }).slice(0, 8).map(function (c) {
          return { id: c.id, name: c.name, symbol: (c.symbol || "").toUpperCase(), rank: c.market_cap_rank, thumb: c.thumb };
        })));
      }).catch(function () {});
    }, 300);
  });
  $("pc-q").addEventListener("keydown", function (e) {
    var list = $("pc-suggest");
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!items.length) return;
      active = (active + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      list.querySelectorAll("li").forEach(function (li, i) { li.setAttribute("aria-selected", String(i === active)); });
    } else if (e.key === "Enter") { e.preventDefault(); if (items[active]) pick(items[active]); }
    else if (e.key === "Escape") suggest([]);
  });
  $("pc-suggest").addEventListener("mousedown", function (e) {
    var li = e.target.closest("li[data-i]");
    if (li) { e.preventDefault(); pick(items[+li.dataset.i]); }
  });
  $("pc-q").addEventListener("blur", function () { setTimeout(function () { suggest([]); }, 120); });

  function pick(c) {
    coin = c;
    $("pc-q").value = c.name + " (" + c.symbol + ")";
    suggest([]);
    render();
    poll();
  }

  // ------------------------------------------------------------ render
  function card(k, v, sub) {
    return '<div class="card"><div class="k">' + k + '</div><div class="v">' + v + "</div>" + (sub ? '<div class="n">' + sub + "</div>" : "") + "</div>";
  }
  function stats() {
    var closes = acct.trades.filter(function (t) { return t.type === "Close"; });
    var by = {};
    closes.forEach(function (t) {
      var s = by[t.strategy] || (by[t.strategy] = { name: t.strategy, n: 0, wins: 0, pnl: 0, gw: 0, gl: 0, held: 0 });
      s.n++; s.pnl += t.pnl; s.held += t.held || 0;
      if (t.pnl > 0) { s.wins++; s.gw += t.pnl; } else s.gl += -t.pnl;
    });
    acct.lots.forEach(function (l) { if (!by[l.strategy]) by[l.strategy] = { name: l.strategy, n: 0, wins: 0, pnl: 0, gw: 0, gl: 0, held: 0 }; });
    return Object.keys(by).map(function (k) { return by[k]; }).sort(function (a, b) { return b.pnl - a.pnl; });
  }

  function render() {
    $("pc-setup").hidden = !!acct;
    $("pc-main").hidden = !acct;
    if (!acct) return;
    $("pc-fee-txt").textContent = n(acct.fee, 2) + "%";
    var eq = equity(), closes = acct.trades.filter(function (t) { return t.type === "Close"; });
    var wins = closes.filter(function (t) { return t.pnl > 0; }).length;
    var realized = closes.reduce(function (x, t) { return x + t.pnl; }, 0);
    var open = acct.lots.reduce(function (x, l) { return x + lotValue(l) - l.cost; }, 0);
    var peak = acct.equity.reduce(function (m, e) { return Math.max(m, e.v); }, acct.start);
    $("pc-cards").innerHTML =
      card("Account value", usd(eq), sgn((eq / acct.start - 1) * 100, "%") + " since start") +
      card("Cash", usd(acct.cash), n(acct.cash / eq * 100, 0) + "% of account") +
      card("Open P&L", sgn(open, "$"), acct.lots.length + " open position" + (acct.lots.length === 1 ? "" : "s")) +
      card("Realized P&L", sgn(realized, "$"), closes.length ? wins + " of " + closes.length + " closes won" : "no closed trades yet") +
      card("Drawdown", n((eq / peak - 1) * 100, 1) + "%", "from peak " + usd(peak));

    // Ticket
    var p = coin && prices[coin.id];
    $("pc-coin").innerHTML = coin ? "<b>" + esc(coin.symbol) + "</b> " + esc(coin.name) + (p ? " · " + usd(p.usd) + " " + sgn(p.usd_24h_change, "%") + " 24h · 24h volume " + usd(p.usd_24h_vol) : " · loading price…") +
      ' · <a href="research.html?id=' + encodeURIComponent(coin.id) + '">research</a>' : '<span class="faint">Search for a coin above.</span>';
    $("pc-quick").innerHTML = [1, 5, 10, 25].map(function (x) { return '<button type="button" data-v="' + (eq * x / 100).toFixed(2) + '">' + x + "%</button>"; }).join("");
    var size = parseFloat($("pc-amt").value), cs = [], prev = "";
    if (coin && p && size > 0) {
      var s = slip(coin.id, size), fill = dir === "long" ? p.usd * (1 + s) : p.usd * (1 - s);
      prev = (dir === "long" ? "Buy" : "Short") + " about <b>" + n((size - size * fee()) / fill, 6) + " " + esc(coin.symbol) + "</b> at " + usd(fill) + " (slippage " + n(s * 100, 2) + "%, fee " + usd(size * fee()) + ").";
      if (size > acct.cash + 1e-9) cs.push(["block", "Not enough cash: " + usd(acct.cash) + " available."]);
      if (size > eq * 0.25) cs.push(["warn", "This is " + n(size / eq * 100, 0) + "% of your account in one position."]);
      if (!(parseFloat($("pc-sl").value) > 0)) cs.push(["warn", "No stop-loss set. Decide where you're wrong before you enter."]);
      if (!$("pc-strat").value.trim()) cs.push(["warn", "No strategy tag, so this trade won't count toward a strategy's results."]);
      if (s > 0.02) cs.push(["warn", "Slippage is " + n(s * 100, 1) + "%: this coin is thin for that size."]);
      if (!cs.length) cs.push(["ok", "Sized, stopped and tagged."]);
    }
    $("pc-preview").innerHTML = prev;
    $("pc-checks").innerHTML = cs.map(function (c) { return '<div class="pp-chk ' + c[0] + '">' + (c[0] === "ok" ? "✓ " : c[0] === "warn" ? "! " : "✕ ") + esc(c[1]) + "</div>"; }).join("");
    var blocked = !(coin && p && size > 0) || cs.some(function (c) { return c[0] === "block"; });
    $("pc-submit").disabled = blocked;
    $("pc-submit").textContent = dir === "long" ? "Open long" : "Open short";
    $("pc-strats").innerHTML = stats().map(function (s) { return '<option value="' + esc(s.name) + '">'; }).join("");

    // Positions
    $("pc-positions").innerHTML = !acct.lots.length ? '<p class="sub">No open positions.</p>' :
      '<div class="tablewrap"><table><thead><tr><th class="l">Coin</th><th class="l">Side</th><th>Size</th><th>Entry</th><th>Price</th><th>P&amp;L</th><th>Stop</th><th>Target</th><th class="l">Strategy</th><th class="l">Close</th></tr></thead><tbody>' +
      acct.lots.map(function (l) {
        var price = (prices[l.id] || {}).usd, v = lotValue(l), pnl = v - l.cost;
        return '<tr><td class="l"><b>' + esc(l.symbol) + '</b><div class="why">' + esc(stamp(l.opened).slice(0, 10)) + '</div></td><td class="l"><span class="tag ' + (l.dir === "long" ? "setup" : "st-exit-avoid") + '">' + (l.dir === "long" ? "Long" : "Short") + "</span></td>" +
          "<td>" + usd(v) + "</td><td>" + usd(l.entry) + "</td><td>" + usd(price) + "</td><td>" + sgn(pnl, "$") + "<br>" + sgn(pnl / l.cost * 100, "%") + "</td>" +
          "<td>" + (l.sl != null ? usd(l.sl) : "–") + "</td><td>" + (l.tp != null ? usd(l.tp) : "–") + '</td><td class="l">' + esc(l.strategy) + '</td><td class="l pc-close">' +
          '<button type="button" data-lid="' + l.lid + '" data-f="0.5">½</button><button type="button" data-lid="' + l.lid + '" data-f="1">All</button></td></tr>';
      }).join("") + "</tbody></table></div>";

    // Strategies
    var st = stats();
    $("pc-strategies").innerHTML = !st.length ? '<p class="sub">Tag trades with a strategy and close them to see results here.</p>' :
      '<div class="tablewrap"><table><thead><tr><th class="l">Strategy</th><th>Closed</th><th>Win rate</th><th>Total P&amp;L</th><th>Avg win</th><th>Avg loss</th><th>Profit factor</th><th>Avg hold</th><th>Open now</th></tr></thead><tbody>' +
      st.map(function (s) {
        var losses = s.n - s.wins, openN = acct.lots.filter(function (l) { return l.strategy === s.name; }).length;
        return '<tr><td class="l"><b>' + esc(s.name) + "</b></td><td>" + s.n + "</td><td>" + (s.n ? n(s.wins / s.n * 100, 0) + "%" : "–") + "</td><td>" + sgn(s.pnl, "$") +
          "</td><td>" + (s.wins ? usd(s.gw / s.wins) : "–") + "</td><td>" + (losses ? usd(-s.gl / losses) : "–") + "</td><td>" + (s.gl ? n(s.gw / s.gl, 2) : s.gw ? "∞" : "–") +
          "</td><td>" + (s.n ? n(s.held / s.n / 3600000, 1) + "h" : "–") + "</td><td>" + openN + "</td></tr>";
      }).join("") + "</tbody></table></div>";

    // Equity
    var pts = acct.equity.slice(-300);
    if (pts.length < 2) $("pc-equity").innerHTML = '<p class="sub">The curve fills in as you trade and come back.</p>';
    else {
      var w = 600, h = 140, vals = pts.map(function (q) { return q.v; }).concat([acct.start]);
      var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals), r = (hi - lo) || 1;
      var t0 = pts[0].t, t1 = pts[pts.length - 1].t, span = (t1 - t0) || 1;
      var base = h - 6 - (acct.start - lo) / r * (h - 12);
      $("pc-equity").innerHTML = '<svg viewBox="0 0 ' + w + " " + h + '" preserveAspectRatio="none" class="pp-eq ' + (pts[pts.length - 1].v >= acct.start ? "up" : "down") + '" role="img" aria-label="Account value over time">' +
        '<line x1="0" x2="' + w + '" y1="' + base + '" y2="' + base + '" class="base"/><polyline fill="none" stroke="currentColor" stroke-width="2" vector-effect="non-scaling-stroke" points="' +
        pts.map(function (q) { return ((q.t - t0) / span * w).toFixed(1) + "," + (h - 6 - (q.v - lo) / r * (h - 12)).toFixed(1); }).join(" ") + '"/></svg>' +
        '<div class="pp-eq-axis"><span>' + stamp(t0) + "</span><span>" + usd(lo) + " – " + usd(hi) + " · dashed line = start</span><span>" + stamp(t1) + "</span></div>";
    }

    // Journal
    $("pc-journal").innerHTML = !acct.trades.length ? '<p class="sub">Your opens and closes will appear here.</p>' :
      '<div class="tablewrap"><table><thead><tr><th class="l">When</th><th class="l">Action</th><th class="l">Coin</th><th>Amount</th><th>Fill</th><th>Slippage</th><th>P&amp;L</th><th class="l">Strategy</th><th class="l">Note</th></tr></thead><tbody>' +
      acct.trades.slice(0, 200).map(function (t) {
        var act = t.type === "Open" ? (t.dir === "long" ? "Buy (open long)" : "Sell (open short)") : (t.dir === "long" ? "Sell" : "Buy to cover") + " · " + t.reason;
        return '<tr><td class="l">' + stamp(t.t) + '</td><td class="l">' + esc(act) + '</td><td class="l">' + esc(t.symbol) + "</td><td>" + usd(t.size) + "</td><td>" + usd(t.price) + "</td><td>" + n(t.slip, 2) + "%</td><td>" +
          (t.pnl != null ? sgn(t.pnl, "$") + " (" + sgn(t.pnlPct, "%") + ")" : "–") + '</td><td class="l">' + esc(t.strategy) + '</td><td class="l pp-note">' + esc(t.note) + "</td></tr>";
      }).join("") + "</tbody></table></div>";
  }

  // ------------------------------------------------------------ events
  $("pc-start-btn").addEventListener("click", function () {
    var v = parseFloat($("pc-start").value);
    if (!(v > 0)) return;
    acct = { v: 1, kind: "crypto", created: Date.now(), start: v, cash: v, fee: 0.1, lots: [], trades: [], equity: [{ t: Date.now(), v: v }], lastCheck: Date.now(), nextLid: 1 };
    save(); render();
  });
  $("pc-reset").addEventListener("click", function () {
    if (!acct || !confirm("Erase the crypto practice account, its positions and journal? Export first if you want a copy.")) return;
    acct = null;
    try { localStorage.removeItem(KEY); } catch (e) {}
    render();
  });
  $("pc-export").addEventListener("click", function () {
    if (!acct) return;
    var a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([JSON.stringify(acct, null, 1)], { type: "application/json" }));
    a.download = "paper-crypto-" + new Date().toISOString().slice(0, 10) + ".json";
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
  });
  $("pc-import").addEventListener("change", function () {
    var f = this.files[0];
    if (!f) return;
    f.text().then(function (txt) {
      var d = JSON.parse(txt);
      if (!d || d.kind !== "crypto" || typeof d.cash !== "number" || !d.lots) throw new Error("not a crypto practice account export");
      if (acct && !confirm("Replace the current crypto practice account with the imported one?")) return;
      acct = d; save(); render(); poll();
    }).catch(function (e) { alert("Couldn't import: " + e.message); });
    this.value = "";
  });
  document.querySelector("#pc-ticket .pp-side").addEventListener("click", function (e) {
    var b = e.target.closest("button[data-dir]");
    if (!b) return;
    dir = b.dataset.dir;
    this.querySelectorAll("button").forEach(function (x) { x.classList.toggle("on", x === b); });
    render();
  });
  $("pc-quick").addEventListener("click", function (e) {
    var b = e.target.closest("button[data-v]");
    if (b) { $("pc-amt").value = b.dataset.v; render(); }
  });
  ["pc-amt", "pc-sl", "pc-tp", "pc-strat"].forEach(function (id) { $(id).addEventListener("input", render); });
  $("pc-submit").addEventListener("click", function () {
    var b = this; b.disabled = true;
    poll().then(openLot);
  });
  $("pc-positions").addEventListener("click", function (e) {
    var b = e.target.closest("button[data-lid]");
    if (!b) return;
    // Fill at a fresh price, not one up to a minute old.
    b.disabled = true;
    poll().then(function () {
      if (!acct.lots.some(function (l) { return l.lid === +b.dataset.lid; })) return; // a stop or target already closed it
      closeLot(+b.dataset.lid, +b.dataset.f, "Manual");
      save(); render();
    });
  });

  // ------------------------------------------------------------ start
  acct = load();
  render();
  if (acct) {
    setStatus("Checking stops and targets since your last visit…");
    replay().then(function (filled) {
      if (filled) { save(); }
      return poll().then(function () {
        if (filled) setStatus($("pc-status").innerHTML + " · <b>" + filled + " stop/target fill" + (filled > 1 ? "s" : "") + " while you were away</b> (see journal)");
      });
    });
  } else setStatus("Start an account to begin.");
  var want = new URLSearchParams(location.search).get("coin");
  if (want) getJSON(CG + "/coins/" + encodeURIComponent(want) + "?localization=false&tickers=false&market_data=false&community_data=false&developer_data=false")
    .then(function (c) { pick({ id: c.id, name: c.name, symbol: (c.symbol || "").toUpperCase(), thumb: (c.image || {}).thumb }); }).catch(function () {});
  setInterval(function () { if (acct && !document.hidden) poll(); }, 60000);
  document.addEventListener("visibilitychange", function () { if (acct && !document.hidden) poll(); });
})();
