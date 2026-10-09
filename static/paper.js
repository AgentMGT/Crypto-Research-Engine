// Paper trading for the Bittensor plan. Fills use each subnet's constant-product pool
// from the latest refresh (tao-latest.json); the account lives in localStorage.
(function () {
  var KEY = "paper-tao-v1";
  var $ = function (id) { return document.getElementById(id); };
  var snap = null;      // {ts, subnets: {netuid: {...}}, plan: {netuid: row}, params, regime, taoUsd}
  var acct = null;
  var side = "buy";

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
  function px(v) { return v == null || isNaN(v) ? "–" : (v >= 0.01 ? n(v, 5) : n(v, 7)); }
  function usd(v) { return v == null || isNaN(v) ? "–" : "$" + n(v, v >= 1000 ? 0 : 2); }
  function sgn(v, dp) {
    if (v == null || isNaN(v)) return '<span class="flat">–</span>';
    var c = v > 0.00001 ? "up" : v < -0.00001 ? "down" : "flat";
    return '<span class="' + c + '">' + (v > 0 ? "+" : "") + n(v, dp == null ? 2 : dp) + "</span>";
  }
  function save() { try { localStorage.setItem(KEY, JSON.stringify(acct)); } catch (e) { alert("Couldn't save: your browser blocked storage."); } }
  function load() { try { return JSON.parse(localStorage.getItem(KEY) || "null"); } catch (e) { return null; } }
  function now() { return new Date().toISOString().slice(0, 16).replace("T", " ") + " UTC"; }
  function feeRate() { return (acct && acct.fee != null ? acct.fee : 0.05) / 100; }

  // ------------------------------------------------------------ market snapshot
  function loadSnap() {
    return fetch("tao-latest.json?t=" + Date.now()).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (d) {
      var subs = {}, plan = {}, taoUsd = null;
      (d.subnets || []).forEach(function (s) {
        subs[s.netuid] = s;
        if (!taoUsd && s.price_usd && s.price_tao) taoUsd = s.price_usd / s.price_tao;
      });
      ((d.plan || {}).rows || []).forEach(function (r) { plan[r.netuid] = r; });
      snap = { ts: d.updated, subnets: subs, plan: plan, params: (d.plan || {}).params || {}, regime: (d.plan || {}).regime || {}, taoUsd: taoUsd };
    });
  }

  // Pool for a subnet, including what this account's own orders did to it since the refresh.
  function pool(id) {
    var s = snap.subnets[id];
    if (!s) return null;
    var T = s.tao_liquidity, A = s.alpha_in_pool;
    if (!(T > 0 && A > 0)) { T = 1e12; A = T / s.price_tao; } // no pool data: fill at spot
    var imp = acct.impact && acct.impact.ts === snap.ts ? acct.impact.pools[id] : null;
    if (imp) { T += imp.dT; A += imp.dA; }
    return { T: T, A: A, spot: s.price_tao };
  }
  function quoteBuy(id, tao) {
    var p = pool(id), net = tao * (1 - feeRate());
    var alpha = p.A * net / (p.T + net);
    return { alpha: alpha, price: tao / alpha, slip: (tao / alpha / p.spot - 1) * 100, fee: tao - net, dT: net, dA: -alpha };
  }
  function quoteSell(id, alpha) {
    var p = pool(id), gross = p.T * alpha / (p.A + alpha), net = gross * (1 - feeRate());
    return { tao: net, price: net / alpha, slip: (net / alpha / p.spot - 1) * 100, fee: gross - net, dT: -gross, dA: alpha };
  }
  function markImpact(id, dT, dA) {
    if (!acct.impact || acct.impact.ts !== snap.ts) acct.impact = { ts: snap.ts, pools: {} };
    var p = acct.impact.pools[id] || (acct.impact.pools[id] = { dT: 0, dA: 0 });
    p.dT += dT; p.dA += dA;
  }

  // ------------------------------------------------------------ account maths
  function posValue(id) {
    var pos = acct.positions[id], s = snap.subnets[id];
    return pos && s ? pos.alpha * s.price_tao : 0;
  }
  function basketValue() { return Object.keys(acct.positions).reduce(function (t, id) { return t + posValue(id); }, 0); }
  function equity() { return acct.cash + basketValue(); }
  function limits(id) {
    var P = snap.params, eq = equity(), s = snap.subnets[id] || {};
    var posMax = Math.min(eq * (P.position_max_pct || 5) / 100, (s.tao_liquidity || 0) * (P.max_pool_share_pct || 1) / 100);
    return {
      posMax: posMax, tranche: posMax / (P.tranches || 3),
      basketMax: eq * (snap.regime.basket_cap_pct || 20) / 100,
      maxPos: P.max_positions || 8
    };
  }

  // Plan checks for an order. Each check: {level: "ok"|"warn"|"block", text}.
  function checks(id, sideNow, amt) {
    var out = [], P = snap.params, row = snap.plan[id], s = snap.subnets[id], pos = acct.positions[id];
    var L = limits(id);
    if (!s) return [{ level: "block", text: "This subnet isn't in the latest refresh." }];
    if (!(amt > 0)) return [];
    if (sideNow === "buy") {
      if (amt > acct.cash + 1e-9) out.push({ level: "block", text: "Not enough TAO: you have " + n(acct.cash) + " τ in cash." });
      if (!row) out.push({ level: "warn", text: "The plan doesn't cover this subnet (root or missing data)." });
      else if (row.status === "Entry") out.push({ level: "ok", text: "Plan entry setup: " + row.setup + "." });
      else out.push({ level: "warn", text: "Plan status is " + row.status + ": " + row.short });
      var held = pos ? pos.cost : 0;
      if (amt > L.tranche * 1.05) out.push({ level: "warn", text: "Bigger than one tranche (" + n(L.tranche) + " τ). The plan enters in " + (P.tranches || 3) + " steps." });
      else out.push({ level: "ok", text: "Within one tranche (" + n(L.tranche) + " τ)." });
      if (pos && pos.lastBuySnap === snap.ts) out.push({ level: "warn", text: "You already added to this position since the last refresh. The plan spaces tranches at least one refresh apart." });
      if (held + amt > L.posMax * 1.02) out.push({ level: "warn", text: "Position would reach " + n(held + amt) + " τ, above the plan's max of " + n(L.posMax) + " τ (the smaller of " + (P.position_max_pct || 5) + "% of your account and " + (P.max_pool_share_pct || 1) + "% of the pool)." });
      if (!pos && Object.keys(acct.positions).length >= L.maxPos) out.push({ level: "warn", text: "You'd hold more than " + L.maxPos + " positions." });
      if (basketValue() + amt > L.basketMax * 1.02) out.push({ level: "warn", text: "Subnet basket would be " + n((basketValue() + amt) / equity() * 100, 1) + "% of your account; the cap in a " + (snap.regime.title || "") + " regime is " + snap.regime.basket_cap_pct + "%." });
    } else {
      if (!pos) return [{ level: "block", text: "You don't hold this subnet." }];
      if (amt > pos.alpha * 1.0000001) out.push({ level: "block", text: "You only hold " + n(pos.alpha, 4) + " alpha." });
      var avg = pos.cost / pos.alpha, stopHit = s.price_tao <= avg * (1 - (P.stop_loss_pct || 30) / 100);
      if (row && row.status === "Exit / avoid") out.push({ level: "ok", text: "Plan exit signal: " + row.short });
      else if (stopHit) out.push({ level: "ok", text: "Stop hit: price is " + n((s.price_tao / avg - 1) * 100, 1) + "% from your average entry." });
      else if (row && row.status === "Trim") {
        var share = amt / pos.alpha;
        out.push({ level: share <= 0.4 ? "ok" : "warn", text: share <= 0.4 ? "Plan trim signal: selling about a third is on plan." : "Plan says trim a third, not " + n(share * 100, 0) + "%." });
      } else out.push({ level: "warn", text: "No exit, trim or stop signal on this subnet. Selling now is discretionary, so it counts as off plan." });
    }
    return out;
  }

  // ------------------------------------------------------------ orders
  function place() {
    var id = +$("pp-sn").value, amt = parseFloat($("pp-amt").value);
    var cs = checks(id, side, amt);
    if (!(amt > 0) || cs.some(function (c) { return c.level === "block"; })) return;
    var off = cs.filter(function (c) { return c.level === "warn"; }).map(function (c) { return c.text; });
    if (off.length && !confirm("This order breaks the plan:\n\n• " + off.join("\n• ") + "\n\nPlace it anyway? It will be marked off plan.")) return;
    var s = snap.subnets[id], row = snap.plan[id] || {}, t;
    if (side === "buy") {
      var q = quoteBuy(id, amt);
      var pos = acct.positions[id] || (acct.positions[id] = { alpha: 0, cost: 0, tranches: 0, opened: now(), name: s.name });
      pos.alpha += q.alpha; pos.cost += amt; pos.tranches += 1; pos.lastBuySnap = snap.ts;
      acct.cash -= amt;
      markImpact(id, q.dT, q.dA);
      t = { side: "Buy", tao: amt, alpha: q.alpha, price: q.price, slip: q.slip, fee: q.fee, sizePct: amt / equity() * 100 };
    } else {
      var p = acct.positions[id], qs = quoteSell(id, amt), costPart = p.cost * amt / p.alpha;
      p.alpha -= amt; p.cost -= costPart;
      if (p.alpha < 1e-9) delete acct.positions[id];
      acct.cash += qs.tao;
      markImpact(id, qs.dT, qs.dA);
      t = { side: "Sell", tao: qs.tao, alpha: amt, price: qs.price, slip: qs.slip, fee: qs.fee, pnl: qs.tao - costPart, pnlPct: (qs.tao / costPart - 1) * 100, opened: p.opened, cost: costPart };
    }
    t.t = now(); t.snap = snap.ts; t.netuid = id; t.name = s.name;
    t.planStatus = row.setup || row.status || "–"; t.offPlan = off; t.note = $("pp-note").value.trim();
    acct.trades.unshift(t);
    recordEquity();
    save();
    $("pp-amt").value = ""; $("pp-note").value = "";
    render();
  }

  function recordEquity() {
    var v = equity(), last = acct.equity[acct.equity.length - 1];
    if (last && last.snap === snap.ts) { last.value = v; last.taoUsd = snap.taoUsd; }
    else acct.equity.push({ snap: snap.ts, value: v, taoUsd: snap.taoUsd });
    var b = basketValue();
    acct.basketPeak = Math.max(acct.basketPeak || 0, b);
    if (b === 0) acct.basketPeak = 0;
  }

  // ------------------------------------------------------------ render
  function card(k, v, sub) {
    return '<div class="card"><div class="k">' + k + '</div><div class="v">' + v + "</div>" + (sub ? '<div class="n">' + sub + "</div>" : "") + "</div>";
  }

  function renderCards() {
    var eq = equity(), b = basketValue(), ret = (eq / acct.start - 1) * 100;
    var sells = acct.trades.filter(function (t) { return t.side === "Sell"; });
    var wins = sells.filter(function (t) { return t.pnl > 0; }).length;
    var realized = sells.reduce(function (x, t) { return x + (t.pnl || 0); }, 0);
    var on = acct.trades.filter(function (t) { return !t.offPlan.length; }).length;
    var peak = acct.equity.reduce(function (m, e) { return Math.max(m, e.value); }, acct.start);
    var dd = peak ? (eq / peak - 1) * 100 : 0;
    $("pp-cards").innerHTML =
      card("Account value", n(eq) + " τ", snap.taoUsd ? usd(eq * snap.taoUsd) : "") +
      card("Return", sgn(ret, 1) + "%", "from " + n(acct.start, 0) + " τ · drawdown " + n(dd, 1) + "%") +
      card("Cash", n(acct.cash) + " τ", n(acct.cash / eq * 100, 0) + "% of account") +
      card("Subnet basket", n(b) + " τ", n(b / eq * 100, 1) + "% of " + (snap.regime.basket_cap_pct || "–") + "% cap · " + Object.keys(acct.positions).length + "/" + (snap.params.max_positions || 8) + " positions") +
      card("Realized P&L", sgn(realized) + " τ", sells.length ? wins + " of " + sells.length + " sells won" : "no closed trades yet") +
      card("Plan adherence", acct.trades.length ? n(on / acct.trades.length * 100, 0) + "%" : "–", acct.trades.length ? on + " of " + acct.trades.length + " orders on plan" : "place an order to start");
  }

  function renderPositions() {
    var ids = Object.keys(acct.positions);
    if (!ids.length) { $("pp-positions").innerHTML = '<p class="sub">No open positions. Start with an entry candidate from the plan.</p>'; }
    else {
      $("pp-positions").innerHTML = '<div class="tablewrap"><table class="pp-pos"><thead><tr><th class="l">Subnet</th><th>Alpha</th><th>Avg cost</th><th>Price</th><th>Value (TAO)</th><th>P&amp;L (TAO)</th><th>P&amp;L</th><th class="l">Plan now</th></tr></thead><tbody>' +
        ids.map(function (id) {
          var p = acct.positions[id], s = snap.subnets[id] || {}, row = snap.plan[id] || {};
          var val = posValue(id), avg = p.cost / p.alpha;
          var tag = row.status === "Exit / avoid" ? "st-exit-avoid" : row.status === "Trim" ? "st-trim" : row.status === "Entry" ? "setup" : "";
          return '<tr data-id="' + id + '" tabindex="0" title="Click to sell"><td class="l"><b>SN' + id + "</b> " + esc(p.name) + '<div class="why">' + p.tranches + " tranche" + (p.tranches > 1 ? "s" : "") + " · since " + esc(p.opened.slice(0, 10)) + "</div></td>" +
            "<td>" + n(p.alpha, 3) + "</td><td>" + px(avg) + "</td><td>" + px(s.price_tao) + "</td><td>" + n(val) + "</td><td>" + sgn(val - p.cost) + "</td><td>" + sgn((val / p.cost - 1) * 100, 1) + "%</td>" +
            '<td class="l"><span class="tag ' + tag + '">' + esc(row.setup || row.status || "–") + "</span></td></tr>";
        }).join("") + "</tbody></table></div>";
    }
    // Alerts: plan signals on what you hold, stops and the basket circuit breaker.
    var P = snap.params, alerts = [];
    ids.forEach(function (id) {
      var p = acct.positions[id], s = snap.subnets[id] || {}, row = snap.plan[id] || {}, avg = p.cost / p.alpha;
      if (row.status === "Exit / avoid") alerts.push({ k: "bad", t: "SN" + id + " " + p.name + ": exit signal. " + row.short });
      else if (row.status === "Trim") alerts.push({ k: "warn", t: "SN" + id + " " + p.name + ": trim a third. " + row.short });
      if (s.price_tao <= avg * (1 - (P.stop_loss_pct || 30) / 100)) alerts.push({ k: "bad", t: "SN" + id + " " + p.name + ": stop hit, " + n((s.price_tao / avg - 1) * 100, 1) + "% from your average entry." });
    });
    var b = basketValue();
    if (acct.basketPeak && b < acct.basketPeak * (1 - (P.basket_drawdown_halve_pct || 25) / 100))
      alerts.push({ k: "bad", t: "Basket is " + n((b / acct.basketPeak - 1) * 100, 1) + "% below its peak: the circuit breaker says halve every position." });
    if (b > equity() * (snap.regime.basket_cap_pct || 20) / 100 * 1.02)
      alerts.push({ k: "warn", t: "Basket is above this run's " + snap.regime.basket_cap_pct + "% cap (" + (snap.regime.title || "") + " regime). Trim back toward it." });
    $("pp-alerts").innerHTML = alerts.length ? alerts.map(function (a) { return '<li class="' + a.k + '">' + esc(a.t) + "</li>"; }).join("")
      : '<li class="good">' + (ids.length ? "No plan signals on your positions this run." : "Nothing to manage yet.") + "</li>";
  }

  function renderEquity() {
    var pts = acct.equity.slice(-120);
    if (pts.length < 2) { $("pp-equity").innerHTML = '<p class="sub">The curve fills in as you come back after each refresh.</p>'; return; }
    var w = 600, h = 140, lo = Math.min.apply(null, pts.map(function (p) { return p.value; }).concat([acct.start])),
      hi = Math.max.apply(null, pts.map(function (p) { return p.value; }).concat([acct.start])), r = (hi - lo) || 1;
    var xy = pts.map(function (p, i) { return [i / (pts.length - 1) * w, h - 6 - (p.value - lo) / r * (h - 12)]; });
    var base = h - 6 - (acct.start - lo) / r * (h - 12);
    var up = pts[pts.length - 1].value >= acct.start;
    $("pp-equity").innerHTML = '<svg viewBox="0 0 ' + w + " " + h + '" preserveAspectRatio="none" class="pp-eq ' + (up ? "up" : "down") + '" role="img" aria-label="Account value over time">' +
      '<line x1="0" x2="' + w + '" y1="' + base + '" y2="' + base + '" class="base"/>' +
      '<polyline fill="none" stroke="currentColor" stroke-width="2" vector-effect="non-scaling-stroke" points="' + xy.map(function (p) { return p[0].toFixed(1) + "," + p[1].toFixed(1); }).join(" ") + '"/></svg>' +
      '<div class="pp-eq-axis"><span>' + esc(pts[0].snap) + "</span><span>" + n(lo) + " – " + n(hi) + " τ · dashed line = start</span><span>" + esc(pts[pts.length - 1].snap) + "</span></div>";
  }

  function renderJournal() {
    if (!acct.trades.length) { $("pp-journal").innerHTML = '<p class="sub">Your orders will appear here with the plan status at the time.</p>'; return; }
    $("pp-journal").innerHTML = '<div class="tablewrap"><table><thead><tr><th class="l">When</th><th class="l">Subnet</th><th class="l">Side</th><th>TAO</th><th>Alpha</th><th>Fill price</th><th>Slippage</th><th>P&amp;L</th><th class="l">Plan at the time</th><th class="l">Note</th></tr></thead><tbody>' +
      acct.trades.map(function (t) {
        return '<tr><td class="l">' + esc(t.t) + '</td><td class="l">SN' + t.netuid + " " + esc(t.name) + '</td><td class="l">' + t.side + "</td><td>" + n(t.tao) + "</td><td>" + n(t.alpha, 3) + "</td><td>" + px(t.price) +
          "</td><td>" + n(t.slip, 2) + "%</td><td>" + (t.pnl != null ? sgn(t.pnl) + " τ (" + n(t.pnlPct, 1) + "%)" : "–") + '</td><td class="l">' +
          (t.offPlan.length ? '<span class="tag st-exit-avoid" title="' + esc(t.offPlan.join(" ")) + '">Off plan</span> ' : '<span class="tag setup">On plan</span> ') + esc(t.planStatus) +
          '</td><td class="l pp-note">' + esc(t.note) + "</td></tr>";
      }).join("") + "</tbody></table></div>";
  }

  function fillSelect() {
    var sel = $("pp-sn"), keep = sel.value;
    var rows = Object.keys(snap.plan).map(function (k) { return snap.plan[k]; });
    var held = Object.keys(acct.positions).map(Number);
    function opt(r) { return '<option value="' + r.netuid + '">SN' + r.netuid + " " + esc(r.name) + " — " + esc(r.setup || r.status) + "</option>"; }
    var groups = [
      ["Your positions", rows.filter(function (r) { return held.indexOf(r.netuid) >= 0; })],
      ["Plan entry candidates", rows.filter(function (r) { return r.status === "Entry" && held.indexOf(r.netuid) < 0; })],
      ["All other subnets", rows.filter(function (r) { return r.status !== "Entry" && held.indexOf(r.netuid) < 0; }).sort(function (a, b) { return a.netuid - b.netuid; })]
    ];
    sel.innerHTML = groups.filter(function (g) { return g[1].length; }).map(function (g) {
      return '<optgroup label="' + g[0] + '">' + g[1].map(opt).join("") + "</optgroup>";
    }).join("");
    if (keep && sel.querySelector('option[value="' + keep + '"]')) sel.value = keep;
  }

  function renderTicket() {
    var id = +$("pp-sn").value, amt = parseFloat($("pp-amt").value), pos = acct.positions[id], L = limits(id);
    $("pp-amt-label").textContent = side === "buy" ? "Amount to spend (τ)" : "Alpha to sell" + (pos ? " (you hold " + n(pos.alpha, 4) + ")" : "");
    var q = side === "buy"
      ? [["1 tranche", Math.max(0, Math.min(L.tranche, L.posMax - (pos ? pos.cost : 0), acct.cash))], ["Plan max", Math.max(0, Math.min(L.posMax - (pos ? pos.cost : 0), acct.cash))]]
      : pos ? [["⅓", pos.alpha / 3], ["½", pos.alpha / 2], ["All", pos.alpha]] : [];
    $("pp-quick").innerHTML = q.map(function (x) { return '<button type="button" data-v="' + x[1] + '">' + x[0] + "</button>"; }).join("");
    var s = snap.subnets[id], prev = "";
    if (s && amt > 0) {
      if (side === "buy" && amt <= acct.cash) {
        var b = quoteBuy(id, amt);
        prev = "You get <b>" + n(b.alpha, 4) + " alpha</b> at " + px(b.price) + " τ each (spot " + px(s.price_tao) + "), slippage " + n(b.slip, 2) + "%, fee " + n(b.fee, 4) + " τ.";
      } else if (side === "sell" && pos && amt <= pos.alpha * 1.0000001) {
        var c = quoteSell(id, amt), part = pos.cost * amt / pos.alpha;
        prev = "You get <b>" + n(c.tao, 4) + " τ</b> at " + px(c.price) + " τ each, slippage " + n(c.slip, 2) + "%. P&amp;L on this part " + sgn(c.tao - part, 4) + " τ.";
      }
    }
    $("pp-preview").innerHTML = prev;
    var cs = checks(id, side, amt);
    $("pp-checks").innerHTML = cs.map(function (c) { return '<div class="pp-chk ' + c.level + '">' + (c.level === "ok" ? "✓ " : c.level === "warn" ? "! " : "✕ ") + esc(c.text) + "</div>"; }).join("");
    var blocked = !(amt > 0) || cs.some(function (c) { return c.level === "block"; });
    var warn = cs.some(function (c) { return c.level === "warn"; });
    $("pp-submit").disabled = blocked;
    $("pp-submit").textContent = blocked ? "Place order" : (side === "buy" ? "Buy" : "Sell") + (warn ? " (off plan)" : "");
    $("pp-submit").classList.toggle("warn", !blocked && warn);
  }

  function render() {
    $("pp-setup").hidden = !!acct;
    $("pp-main").hidden = !acct;
    $("pp-snap").innerHTML = "Prices from the <b>" + esc(snap.ts) + "</b> refresh · " + esc(snap.regime.title || "") + " regime" +
      (snap.taoUsd ? " · TAO " + usd(snap.taoUsd) : "") + ' · <a href="bittensor.html#plan">view the plan</a>';
    $("pp-fee-txt").textContent = n((acct ? acct.fee : 0.05), 2) + "%";
    if (!acct) return;
    fillSelect();
    renderCards(); renderPositions(); renderEquity(); renderJournal(); renderTicket();
  }

  // ------------------------------------------------------------ events
  $("pp-start-btn").addEventListener("click", function () {
    var v = parseFloat($("pp-start").value);
    if (!(v > 0)) return;
    acct = { v: 1, created: now(), start: v, cash: v, fee: 0.05, positions: {}, trades: [], equity: [], impact: null, basketPeak: 0 };
    recordEquity(); save(); render();
  });
  $("pp-reset").addEventListener("click", function () {
    if (!acct || !confirm("Erase this practice account, its positions and journal? Export first if you want a copy.")) return;
    acct = null;
    try { localStorage.removeItem(KEY); } catch (e) {}
    render();
  });
  $("pp-export").addEventListener("click", function () {
    if (!acct) return;
    var a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([JSON.stringify(acct, null, 1)], { type: "application/json" }));
    a.download = "paper-tao-" + new Date().toISOString().slice(0, 10) + ".json";
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
  });
  $("pp-import").addEventListener("change", function () {
    var f = this.files[0];
    if (!f) return;
    f.text().then(function (txt) {
      var d = JSON.parse(txt);
      if (!d || d.v !== 1 || typeof d.cash !== "number" || !d.positions || !d.trades) throw new Error("not a practice account export");
      if (acct && !confirm("Replace the current practice account with the imported one?")) return;
      acct = d; recordEquity(); save(); render();
    }).catch(function (e) { alert("Couldn't import: " + e.message); });
    this.value = "";
  });
  document.querySelector(".pp-side").addEventListener("click", function (e) {
    var b = e.target.closest("button[data-side]");
    if (!b) return;
    side = b.dataset.side;
    this.querySelectorAll("button").forEach(function (x) { x.classList.toggle("on", x === b); });
    $("pp-amt").value = "";
    renderTicket();
  });
  $("pp-quick").addEventListener("click", function (e) {
    var b = e.target.closest("button[data-v]");
    if (!b) return;
    var v = +b.dataset.v;
    $("pp-amt").value = side === "buy" ? (Math.floor(v * 100) / 100) : v;
    renderTicket();
  });
  $("pp-sn").addEventListener("change", function () { $("pp-amt").value = ""; renderTicket(); });
  $("pp-amt").addEventListener("input", renderTicket);
  $("pp-submit").addEventListener("click", place);
  $("pp-positions").addEventListener("click", function (e) {
    var tr = e.target.closest("tr[data-id]");
    if (!tr) return;
    $("pp-sn").value = tr.dataset.id;
    document.querySelector('.pp-side button[data-side="sell"]').click();
    $("pp-ticket").scrollIntoView({ behavior: "smooth", block: "start" });
  });

  // ------------------------------------------------------------ start
  loadSnap().then(function () {
    acct = load();
    if (acct) { recordEquity(); save(); }
    var want = new URLSearchParams(location.search).get("sn");
    render();
    if (acct && want && $("pp-sn").querySelector('option[value="' + want + '"]')) { $("pp-sn").value = want; renderTicket(); }
  }).catch(function (e) {
    $("pp-snap").textContent = "Couldn't load prices (" + e.message + "). Try again after the next refresh.";
  });
})();
