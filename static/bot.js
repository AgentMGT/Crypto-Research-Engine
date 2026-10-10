// Paper trading bot tab: renders the bot's two accounts from bot.json (written by engine/bot.py).
(function () {
  var $ = function (id) { return document.getElementById(id); };
  if (!$("bt-main")) return;

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function n(v, dp) {
    if (v == null || isNaN(v) || !isFinite(v)) return "–";
    return Number(v).toLocaleString("en-US", { minimumFractionDigits: dp == null ? 2 : dp, maximumFractionDigits: dp == null ? 2 : dp });
  }
  function money(v, u) { return v == null || isNaN(v) ? "–" : u === "$" ? (v < 0 ? "-$" : "$") + n(Math.abs(v)) : n(v, 3) + " τ"; }
  function sgn(v, u) {
    if (v == null || isNaN(v)) return '<span class="flat">–</span>';
    return '<span class="' + (v > 0 ? "up" : v < 0 ? "down" : "flat") + '">' + (v > 0 ? "+" : "") + (u === "%" ? n(v, 1) + "%" : money(v, u)) + "</span>";
  }
  function px(v) { return v == null ? "–" : v >= 100 ? n(v, 2) : v >= 1 ? n(v, 4) : n(v, 7); }
  function stamp(ms) { return new Date(ms).toISOString().slice(0, 16).replace("T", " "); }
  function card(k, v, sub) { return '<div class="card"><div class="k">' + k + '</div><div class="v">' + v + "</div>" + (sub ? '<div class="n">' + sub + "</div>" : "") + "</div>"; }
  function aiCell(ai) {
    if (!ai) return '<span class="faint">rules only</span>';
    return '<span class="tag ' + (ai.verdict === "take" ? "jr-done" : "st-exit-avoid") + '">AI ' + esc(ai.verdict) + " · " + ai.confidence + "/5</span> " + esc(ai.reason);
  }

  function equitySvg(pts, start, u) {
    if (!pts || pts.length < 2) return '<p class="sub">The curve fills in after a few runs.</p>';
    var w = 600, h = 140, vs = pts.map(function (p) { return p.v; }).concat([start]);
    var lo = Math.min.apply(null, vs), hi = Math.max.apply(null, vs), t0 = pts[0].t, t1 = pts[pts.length - 1].t;
    var x = function (t) { return (t - t0) / Math.max(1, t1 - t0) * w; }, y = function (v) { return h - 6 - (v - lo) / Math.max(1e-9, hi - lo) * (h - 12); };
    var up = pts[pts.length - 1].v >= start;
    return '<svg viewBox="0 0 ' + w + " " + h + '" preserveAspectRatio="none" class="pp-eq ' + (up ? "up" : "down") + '" role="img" aria-label="Bot account value over time">' +
      '<line class="base" x1="0" x2="' + w + '" y1="' + y(start) + '" y2="' + y(start) + '"/>' +
      '<polyline fill="none" stroke="currentColor" stroke-width="2" vector-effect="non-scaling-stroke" points="' + pts.map(function (p) { return x(p.t).toFixed(1) + "," + y(p.v).toFixed(1); }).join(" ") + '"/></svg>' +
      '<div class="pp-eq-axis"><span>' + stamp(t0) + "</span><span>" + money(lo, u) + " – " + money(hi, u) + " · dashed line = start</span><span>" + stamp(t1) + "</span></div>";
  }

  function stats(closes) {
    var w = closes.filter(function (t) { return t.pnl > 0; });
    return { n: closes.length, wins: w.length, total: closes.reduce(function (a, t) { return a + t.pnl; }, 0) };
  }

  function render(d) {
    if (!d || !d.ticks || !d.ticks.length) { $("bt-empty").hidden = false; $("bt-status").textContent = ""; return; }
    $("bt-main").hidden = false;
    var tick = d.ticks[d.ticks.length - 1];
    $("bt-status").innerHTML = "Last run <b>" + esc(tick.ts) + "</b> · " + d.ticks.length + " runs logged · AI reviewer " +
      (tick.ai ? "<b>on</b>" : "<b>off</b> (no Anthropic key on the site, so it trades on rules alone)");

    // Latest run
    $("bt-run-when").textContent = tick.ts + (tick.regimes ? " · crypto " + (tick.regimes.crypto || "–") + " · Bittensor plan " + (tick.regimes.tao || "–") : "");
    $("bt-run").innerHTML =
      (tick.ai_note ? '<p class="bt-note"><b>AI note:</b> ' + esc(tick.ai_note) + "</p>" : "") +
      (tick.actions.length ? "<ul class=\"bt-list\">" + tick.actions.map(function (a) { return "<li>" + esc(a) + "</li>"; }).join("") + "</ul>" : '<p class="sub" style="margin:0">No trades this run: nothing met the rules.</p>') +
      (tick.skipped.length ? "<h4>Skipped by the AI reviewer</h4><ul class=\"bt-list\">" + tick.skipped.map(function (s) {
        return "<li><b>" + esc(s.asset) + "</b> (" + esc(s.setup) + ", " + n(s.size, s.unit === "USD" ? 0 : 3) + " " + esc(s.unit) + "): " + esc(s.ai.reason) + "</li>";
      }).join("") + "</ul>" : "");

    // Crypto account
    var c = d.crypto, cOpen = c.positions.reduce(function (a, p) { return a + p.qty * (p.mark || p.entry) - p.cost; }, 0);
    var cEq = c.equity.length ? c.equity[c.equity.length - 1].v : c.start, cs = stats(c.trades.filter(function (t) { return t.type === "Close"; }));
    $("bt-c-cards").innerHTML = card("Account value", money(cEq, "$"), sgn((cEq / c.start - 1) * 100, "%") + " since start") +
      card("Open P&amp;L", sgn(cOpen, "$"), c.positions.length + " open") +
      card("Realized P&amp;L", sgn(cs.total, "$"), cs.n ? cs.wins + " of " + cs.n + " won" : "no closed trades yet");
    $("bt-c-pos").innerHTML = c.positions.length ? '<div class="tablewrap"><table><thead><tr><th class="l">Coin</th><th class="l">Setup</th><th>Entry</th><th>Now</th><th>P&amp;L</th><th>Stop</th><th>Target</th><th class="l">Opened</th></tr></thead><tbody>' +
      c.positions.map(function (p) {
        var now = p.mark || p.entry;
        return '<tr><td class="l"><b>' + esc(p.symbol) + '</b></td><td class="l">' + esc(p.setup) + "</td><td>$" + px(p.entry) + "</td><td>$" + px(now) + "</td><td>" + sgn((now / p.entry - 1) * 100, "%") +
          "</td><td>$" + px(p.stop) + "</td><td>$" + px(p.target) + '</td><td class="l">' + stamp(p.opened).slice(0, 10) + "</td></tr>";
      }).join("") + "</tbody></table></div>" : '<p class="sub">No open positions.</p>';
    $("bt-c-eq").innerHTML = equitySvg(c.equity, c.start, "$");

    // Bittensor account
    var t = d.tao, ids = Object.keys(t.positions);
    var tOpen = ids.reduce(function (a, k) { var p = t.positions[k]; return a + p.alpha * (p.mark || p.cost / p.alpha) - p.cost; }, 0);
    var tEq = t.equity.length ? t.equity[t.equity.length - 1].v : t.start, ts = stats(t.trades.filter(function (x) { return x.side === "Sell"; }));
    $("bt-t-cards").innerHTML = card("Account value", money(tEq, "τ"), sgn((tEq / t.start - 1) * 100, "%") + " since start") +
      card("Open P&amp;L", sgn(tOpen, "τ"), ids.length + " subnets") +
      card("Realized P&amp;L", sgn(ts.total, "τ"), ts.n ? ts.wins + " of " + ts.n + " won" : "no closed trades yet");
    $("bt-t-pos").innerHTML = ids.length ? '<div class="tablewrap"><table><thead><tr><th class="l">Subnet</th><th class="l">Setup</th><th>Tranches</th><th>Cost (τ)</th><th>Value (τ)</th><th>P&amp;L</th><th class="l">Opened</th></tr></thead><tbody>' +
      ids.map(function (k) {
        var p = t.positions[k], val = p.alpha * (p.mark || p.cost / p.alpha);
        return '<tr><td class="l"><b>SN' + k + "</b> " + esc(p.name || "") + '</td><td class="l">' + esc(p.setup || "") + "</td><td>" + p.tranches + "</td><td>" + n(p.cost, 3) + "</td><td>" + n(val, 3) +
          "</td><td>" + sgn((val / p.cost - 1) * 100, "%") + '</td><td class="l">' + stamp(p.opened).slice(0, 10) + "</td></tr>";
      }).join("") + "</tbody></table></div>" : '<p class="sub">No open positions.</p>';
    $("bt-t-eq").innerHTML = equitySvg(t.equity, t.start, "τ");

    // Trade log, both accounts
    var log = c.trades.map(function (x) {
      return { t: x.t, acct: "Crypto", act: x.type === "Open" ? "Buy" : "Sell · " + x.reason, asset: x.symbol, setup: x.setup, size: money(x.size, "$"),
        pnl: x.type === "Close" ? sgn(x.pnl, "$") + " " + sgn(x.pnl_pct, "%") : "", why: x.type === "Open" ? x.reason : x.rule, ai: x.ai, open: x.type === "Open" };
    }).concat(t.trades.map(function (x) {
      return { t: x.t, acct: "Bittensor", act: x.side === "Buy" ? "Buy · tranche " + x.tranche : "Sell · " + x.reason, asset: "SN" + x.netuid + " " + x.name, setup: x.setup || "",
        size: money(x.tao, "τ"), pnl: x.side === "Sell" ? sgn(x.pnl, "τ") + " " + sgn(x.pnl_pct, "%") : "", why: x.side === "Buy" ? x.reason : x.rule, ai: x.ai, open: x.side === "Buy" };
    })).sort(function (a, b) { return b.t - a.t; });
    $("bt-trades").innerHTML = log.length ? '<div class="tablewrap"><table class="bt-log"><thead><tr><th class="l">When (UTC)</th><th class="l">Account</th><th class="l">Order</th><th class="l">Asset</th><th>Size</th><th>P&amp;L</th><th class="l">Why</th><th class="l">AI reviewer</th></tr></thead><tbody>' +
      log.slice(0, 300).map(function (r) {
        return '<tr><td class="l">' + stamp(r.t) + '</td><td class="l">' + r.acct + '</td><td class="l">' + esc(r.act) + '</td><td class="l"><b>' + esc(r.asset) + "</b><div class=\"why\">" + esc(r.setup) + "</div></td><td>" + r.size + "</td><td>" + r.pnl +
          '</td><td class="l pp-note">' + esc(r.why) + '</td><td class="l pp-note">' + (r.open ? aiCell(r.ai) : '<span class="faint">exits never need approval</span>') + "</td></tr>";
      }).join("") + "</tbody></table></div>" : '<p class="sub">No trades yet.</p>';
  }

  // ------------------------------------------------------------ live trading status
  function renderLive(lv) {
    var box = $("lv-panel");
    if (!lv || !lv.crypto) {
      box.innerHTML = '<p class="sub" style="margin:0">Live trading is set up but <b>off</b>. Nothing has run yet; the first dry run happens on the next refresh.</p>';
      return;
    }
    var L = lv.limits || {};
    function acct(key, title, u) {
      var a = lv[key] || {}, lim = L[key] || {}, live = a.mode === "live";
      var badge = a.halted ? '<span class="tag st-exit-avoid">Paused</span>' : live ? '<span class="tag jr-done">LIVE</span>' : '<span class="tag">Off · dry run</span>';
      var budget = key === "crypto" ? lim.budget_usd : lim.budget_tao, dloss = key === "crypto" ? lim.max_daily_loss_usd : lim.max_daily_loss_tao;
      var pos = Object.keys(a.positions || {});
      return '<div class="lv-acct"><div class="lv-head"><b>' + title + "</b> " + badge + "</div>" +
        '<div class="lv-grid"><div><span class="faint">Budget</span><br>' + money(budget, u) + '</div><div><span class="faint">Value now</span><br>' + money(a.equity != null ? a.equity : budget, u) +
        '</div><div><span class="faint">Today</span><br>' + (a.loss_today ? sgn(-a.loss_today, u) : money(0, u)) + ' <span class="faint">(limit -' + money(dloss, u) + ")</span></div>" +
        '<div><span class="faint">Positions</span><br>' + pos.length + "</div></div>" +
        (a.halted ? '<p class="lv-why down">' + esc(a.halted.reason) + "</p>" : "") +
        (!live && a.why_not_live && a.why_not_live.length ? '<p class="lv-why">Not live because: ' + esc(a.why_not_live.join("; ")) + ".</p>" : "") +
        (key === "crypto" ? '<p class="lv-why">Exchange: ' + esc(lim.exchange || "–") + " · max order " + money(lim.max_order_usd, "$") + " · up to " + lim.max_positions + " positions · stop-loss placed on the exchange</p>"
          : '<p class="lv-why">Stakes through a Staking proxy (can stake and unstake, cannot transfer) · max order ' + money(lim.max_order_tao, "τ") + "</p>") + "</div>";
    }
    var log = (lv.log || []).slice(-60).reverse();
    box.innerHTML = '<div class="lv-accts">' + acct("crypto", "Crypto", "$") + acct("tao", "Bittensor", "τ") + "</div>" +
      '<p class="sub">While an account is off, it runs as a dry run: each time the bot trades, it works out the real order it would place against its budget and limits, and logs it below. Nothing reaches an exchange or the chain.</p>' +
      (log.length ? '<div class="tablewrap"><table><thead><tr><th class="l">When (UTC)</th><th class="l">Account</th><th class="l">Order</th><th class="l">Asset</th><th>Size</th><th class="l">Result</th></tr></thead><tbody>' +
        log.map(function (e) {
          var u = e.account === "crypto" ? "$" : "τ";
          var st = e.status === "placed" ? '<span class="tag jr-done">placed</span>' : e.status === "would place" ? '<span class="tag">would place</span>' : e.status === "failed" ? '<span class="tag st-exit-avoid">failed</span>' : '<span class="tag st-trim">' + esc(e.status) + "</span>";
          return '<tr><td class="l">' + stamp(e.t) + '</td><td class="l">' + (e.account === "crypto" ? "Crypto" : "Bittensor") + '</td><td class="l">' + esc(e.side) + '</td><td class="l"><b>' + esc(e.asset) + "</b></td><td>" +
            (e.size != null ? money(e.size, u) : e.pnl != null ? sgn(e.pnl, u) : "–") + '</td><td class="l pp-note">' + st + " " + esc(e.note || e.reason || "") + "</td></tr>";
        }).join("") + "</tbody></table></div>" : '<p class="sub">No orders yet.</p>');
  }
  fetch("live.json?t=" + Date.now()).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; }).then(renderLive);

  Trades.ready.then(render);
})();
