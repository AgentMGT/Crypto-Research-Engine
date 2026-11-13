// Pre-listing tab: search a coin, see its native (DEX) markets and where it's listed, check it against
// the pre-listing rules (data/bot.json "prelist", embedded in the page), and paper-trade it against a
// pool. The practice account lives in this browser (localStorage); the bot's account comes from bot.json.
(function () {
  var $ = function (id) { return document.getElementById(id); };
  if (!$("pl-q")) return;
  var CG = "https://api.coingecko.com/api/v3";
  var GT = "https://api.geckoterminal.com/api/v2";
  var KEY = "paper-prelist-v1";
  var CFG = JSON.parse($("pl-cfg").textContent || "{}");
  var P = CFG.prelist || {}, E = P.entry || {}, FIT = CFG.fit || null;
  var MAJORS = { binance: "Binance", coinbase: "Coinbase", gdax: "Coinbase", okx: "OKX", okex: "OKX", bybit: "Bybit", upbit: "Upbit", kraken: "Kraken" };
  var DEX_RX = /swap|dex|uniswap|pancake|raydium|orca|meteora|aerodrome|velodrome|curve|sushi|jupiter|camelot|trader|balancer|pump/i;
  // CoinGecko platform id -> GeckoTerminal network id
  var NET = { "ethereum": "eth", "binance-smart-chain": "bsc", "solana": "solana", "base": "base", "arbitrum-one": "arbitrum",
    "polygon-pos": "polygon_pos", "avalanche": "avax", "optimistic-ethereum": "optimism", "sui": "sui-network", "the-open-network": "ton",
    "tron": "tron", "blast": "blast", "linea": "linea", "mantle": "mantle", "zksync": "zksync", "sonic": "sonic", "berachain": "berachain" };

  var scan = null, scanById = {}, bot = null, btc30 = null, cur = null, acct = null, marks = {}, searchTimer = null, items = [], active = -1, loadToken = 0;

  // ------------------------------------------------------------ helpers
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function n(v, dp) {
    if (v == null || isNaN(v) || !isFinite(v)) return "–";
    return Number(v).toLocaleString("en-US", { minimumFractionDigits: dp == null ? 2 : dp, maximumFractionDigits: dp == null ? 2 : dp });
  }
  function usd(v) {
    if (v == null || isNaN(v)) return "–";
    var a = Math.abs(v), dp = a >= 1 ? 2 : a >= 0.01 ? 4 : a >= 0.0001 ? 6 : 9;
    return (v < 0 ? "-$" : "$") + n(a, dp);
  }
  function big(v) {
    if (v == null || isNaN(v)) return "–";
    var a = Math.abs(v);
    return a >= 1e9 ? "$" + n(v / 1e9, 2) + "B" : a >= 1e6 ? "$" + n(v / 1e6, 2) + "M" : a >= 1e3 ? "$" + n(v / 1e3, 0) + "K" : "$" + n(v, 0);
  }
  function sgn(v, u) {
    if (v == null || isNaN(v)) return '<span class="flat">–</span>';
    var c = v > 1e-9 ? "up" : v < -1e-9 ? "down" : "flat";
    return '<span class="' + c + '">' + (v > 0 ? "+" : v < 0 ? "-" : "") + (u === "$" ? "$" + n(Math.abs(v)) : n(Math.abs(v), 1) + "%") + "</span>";
  }
  function stamp(ms) { return new Date(ms).toISOString().slice(0, 16).replace("T", " "); }
  function getJSON(url) {
    return fetch(url, { headers: { Accept: "application/json" } }).then(function (r) {
      if (r.status === 429) throw new Error("rate-limited by the data source; wait a minute and try again");
      if (r.status === 404) throw new Error("not found");
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }
  function setStatus(t, bad) { $("pl-status").textContent = t || ""; $("pl-status").className = "rs-status" + (bad ? " bad" : ""); }
  function card(k, v, sub) { return '<div class="card"><div class="k">' + k + '</div><div class="v">' + v + "</div>" + (sub ? '<div class="n">' + sub + "</div>" : "") + "</div>"; }

  // ------------------------------------------------------------ rules (same logic as engine/bot.py prelist_check)
  function signals(v) {
    var d = v.detail || {}, pool = v.pool || {}, cex = (d.cex || []).join(" | ").toLowerCase(), out = [];
    var t2 = (E.tier2_exchanges || []).filter(function (x) { return cex.indexOf(x) >= 0; });
    if (t2.length) out.push("already on " + t2.slice(0, 3).map(function (x) { return x.charAt(0).toUpperCase() + x.slice(1); }).join(", "));
    if ((v.mcap || 0) >= E.signal_min_mcap_usd) out.push("market cap " + big(v.mcap));
    if ((d.watchers || 0) >= E.signal_watchers) out.push(n(d.watchers, 0) + " watchlists");
    if (v.news_n || (v.news && v.news.length)) out.push("in the news");
    if (v.trending) out.push("trending on CoinGecko");
    if (v.buying_up) out.push("buying picking up");
    if ((pool.liq_change_pct || 0) >= E.signal_liq_growth_pct) out.push("DEX liquidity up " + n(pool.liq_change_pct, 0) + "% in a day");
    if ((d.commits_4w || 0) >= E.signal_commits_4w) out.push(d.commits_4w + " commits in 4 weeks");
    return out;
  }
  function fitMissing(v) {
    // Marvin's five altcoin requirements (engine/bot.py alt_fit); null when the scan hasn't checked the coin.
    if (!FIT || !v.checks) return null;
    var ok = {}; v.checks.forEach(function (c) { ok[c.key] = c.ok; });
    var d = v.detail || {}, miss = [];
    if (!(ok.liquidity && FIT.min_volume_usd <= (v.volume || 0) && (v.volume || 0) <= FIT.max_volume_usd)) miss.push("low liquidity");
    if (v.meme || !(ok.use && ok.live)) miss.push("real-world use");
    var demand = ((v.tvl || 0) >= FIT.demand_min_tvl_usd && (v.tvl_ch30 || 0) >= FIT.demand_tvl_growth_30d_pct) || ok.quiet_eco;
    if (!demand) miss.push("strong demand");
    if (!ok.exclusive) miss.push("exclusivity");
    var cex = (d.cex || []).map(function (x) { return x.toLowerCase(); });
    var onMaj = (FIT.major_exchanges || []).some(function (m) { return cex.some(function (x) { return x.indexOf(m) >= 0; }); });
    if (onMaj || (d.cex_count || 0) > FIT.max_cex_count || v.rs30 == null || v.rs30 < FIT.min_rs30) miss.push("growth / listing room");
    return miss;
  }
  function blocked(v) {
    return (v.flags || []).filter(function (f) { return (P.block_flags || []).some(function (b) { return f.indexOf(b) === 0; }); });
  }
  function check(v) {
    var pool = v.pool || {}, d = v.detail, rows = [];
    function row(label, ok, note) { rows.push({ label: label, ok: ok, note: note }); }
    var liq = pool.liquidity;
    row("Native market size", liq == null ? null : liq >= E.pool_min_usd && liq <= E.pool_max_usd && (pool.total_liq || 0) >= E.total_liq_min_usd,
      liq == null ? "No DEX pool found" : "Deepest pool " + big(liq) + ", " + big(pool.total_liq) + " across " + (pool.pools || 1) + " pool(s); the rule wants " + big(E.pool_min_usd) + "–" + big(E.pool_max_usd));
    var maj = v.on_major || [];
    row("Not yet on a major", !d ? null : !maj.length && (d.cex_count || 0) <= E.max_cex_count,
      !d ? "Listings not checked" : maj.length ? "Already on " + maj.join(", ") : (d.cex_count || 0) + " centralized exchange(s)" + (d.cex && d.cex.length ? ": " + d.cex.slice(0, 6).join(", ") : ""));
    var bs = pool.buy_share_6h, acc = pool.vol_accel, churn = pool.total_liq ? (pool.vol24 || 0) / pool.total_liq : null;
    row("Organic buying", bs == null || acc == null || churn == null ? null : bs >= E.min_buy_share_6h && acc >= E.min_vol_accel && churn <= E.max_churn,
      bs == null ? "No recent trade counts" : n(bs * 100, 0) + "% buys in 6h (needs " + n(E.min_buy_share_6h * 100, 0) + "%), activity " + n(acc, 2) + "× its daily pace, volume " + n(churn, 1) + "× liquidity (max " + E.max_churn + "×)");
    var sig = signals(v);
    row("Listing signals", sig.length >= E.min_listing_signals, sig.length + " of " + E.min_listing_signals + " needed" + (sig.length ? ": " + sig.join("; ") : ""));
    var fm = fitMissing(v), bl = blocked(v);
    row("Fundamentals", v.score == null ? null : v.score >= E.min_score && !bl.length && (fm == null || fm.length <= E.max_fit_missing),
      v.score == null ? "Not in the scan yet, so score and checklist are unknown: check the project yourself" :
        "Scan score " + v.score + " (needs " + E.min_score + ")" + (bl.length ? "; red flags: " + bl.join("; ") : "") +
        (fm ? "; your five requirements: " + (fm.length ? fm.length + " missing (" + fm.join(", ") + ")" : "all met") : ""));
    return rows;
  }
  function verdict(v, rows) {
    if ((v.on_major || []).length) return { cls: "bad", text: "Already listed on " + v.on_major.join(", ") + ": past the pre-listing stage." };
    var fail = rows.filter(function (r) { return r.ok === false; }).length, unk = rows.filter(function (r) { return r.ok == null; }).length;
    if (!fail && !unk) return { cls: "good", text: "Meets every pre-listing rule." };
    var parts = [];
    if (fail) parts.push("Fails " + fail + " rule" + (fail > 1 ? "s" : ""));
    if (unk) parts.push((fail ? "can't tell on " : "Can't tell on ") + unk + (fail ? "" : " rule" + (unk > 1 ? "s" : "")));
    return { cls: fail ? "bad" : "warn", text: parts.join(", ") + ". The bot wouldn't buy it." };
  }
  function checklist(rows) {
    return '<ul class="rs-flags">' + rows.map(function (r) {
      var cls = r.ok === true ? "good" : r.ok === false ? "bad" : "warn", mark = r.ok === true ? "✓" : r.ok === false ? "✗" : "?";
      return '<li class="' + cls + '"><b>' + mark + " " + esc(r.label) + "</b> · " + esc(r.note) + "</li>";
    }).join("") + "</ul>";
  }

  // ------------------------------------------------------------ data
  var scanReady = fetch("alt-latest.json?t=" + Date.now()).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; }).then(function (d) {
    scan = d;
    ["universe", "utility", "meme", "pregrad", "prelisting"].forEach(function (k) {
      ((d || {})[k] || []).forEach(function (v) { scanById[v.id] = Object.assign({}, scanById[v.id] || {}, v); });
    });
    renderCands();
  });
  fetch("bot.json?t=" + Date.now()).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; }).then(function (d) { bot = d; renderBot(); });

  function btc() {
    if (btc30 != null) return Promise.resolve(btc30);
    return getJSON(CG + "/coins/markets?vs_currency=usd&ids=bitcoin&price_change_percentage=30d").then(function (d) {
      btc30 = (d[0] || {}).price_change_percentage_30d_in_currency || 0; return btc30;
    }).catch(function () { return null; });
  }

  function poolsFor(c) {
    var plats = Object.keys(c.platforms || {}).filter(function (k) { return c.platforms[k] && NET[k]; }).slice(0, 3);
    return Promise.all(plats.map(function (k) {
      var net = NET[k], addr = c.platforms[k];
      return getJSON(GT + "/networks/" + net + "/tokens/" + addr + "/pools?include=dex").then(function (d) {
        var inc = {};
        (d.included || []).forEach(function (x) { inc[x.type + ":" + x.id] = x.attributes || {}; });
        return (d.data || []).map(function (p) {
          var a = p.attributes || {}, rel = p.relationships || {}, baseId = (((rel.base_token || {}).data) || {}).id || "";
          var isBase = baseId.toLowerCase() === (net + "_" + addr).toLowerCase();
          var tx6 = (a.transactions || {}).h6 || {}, vol = a.volume_usd || {};
          return { net: net, token: addr, pool: a.address, name: a.name, dex: (inc["dex:" + (((rel.dex || {}).data) || {}).id] || {}).name || (((rel.dex || {}).data) || {}).id,
            price: +(isBase ? a.base_token_price_usd : a.quote_token_price_usd), liq: +a.reserve_in_usd || 0, vol24: +vol.h24 || 0, vol6: +vol.h6 || 0,
            b6: tx6.buys || 0, s6: tx6.sells || 0, isBase: isBase, link: "https://www.geckoterminal.com/" + net + "/pools/" + a.address };
        });
      }).catch(function () { return []; });
    })).then(function (lists) {
      return [].concat.apply([], lists).filter(function (p) { return p.liq > 0 && p.price > 0; }).sort(function (a, b) { return b.liq - a.liq; });
    });
  }

  function view(c, pools, b30) {
    // Shape the live data like a scan record so the same rules apply; scan-only facts (score, checklist, news) merge in.
    var md = c.market_data || {}, s = scanById[c.id] || {};
    var cex = [], maj = {};
    (c.tickers || []).forEach(function (t) {
      var m = t.market || {}, key = ((m.identifier || "") + " " + (m.name || "")).toLowerCase();
      if (!m.name || DEX_RX.test(key) || t.is_stale || t.is_anomaly) return;
      if (cex.indexOf(m.name) < 0) cex.push(m.name);
      Object.keys(MAJORS).forEach(function (k) { if (key.indexOf(k) >= 0) maj[MAJORS[k]] = 1; });
    });
    var tot = pools.reduce(function (a, p) { return a + p.liq; }, 0), v24 = pools.reduce(function (a, p) { return a + p.vol24; }, 0), v6 = pools.reduce(function (a, p) { return a + p.vol6; }, 0);
    var b6 = pools.reduce(function (a, p) { return a + p.b6; }, 0), s6 = pools.reduce(function (a, p) { return a + p.s6; }, 0);
    var best = pools[0];
    var pool = best ? { liquidity: best.liq, total_liq: tot, pools: pools.length, vol24: v24, buy_share_6h: b6 + s6 ? b6 / (b6 + s6) : null,
      vol_accel: v24 ? (v6 / 6) / (v24 / 24) : null, liq_change_pct: (s.pool || {}).liq_change_pct } : null;
    var c30 = (md.price_change_percentage_30d_in_currency || {}).usd;
    return {
      id: c.id, symbol: (c.symbol || "").toUpperCase(), name: c.name, image: (c.image || {}).small, price: (md.current_price || {}).usd,
      mcap: (md.market_cap || {}).usd, volume: (md.total_volume || {}).usd, rs30: c30 != null && b30 != null ? c30 - b30 : s.rs30,
      detail: { cex: cex, cex_count: cex.length, watchers: c.watchlist_portfolio_users, commits_4w: (c.developer_data || {}).commit_count_4_weeks },
      on_major: Object.keys(maj), pool: pool, news_n: s.news_n || (s.news || []).length, trending: s.trending,
      buying_up: s.buying_up != null ? s.buying_up : !!(pool && pool.buy_share_6h >= 0.55 && pool.vol_accel >= 1.2),
      score: s.score, flags: s.flags, checks: s.checks, meme: s.meme, tvl: s.tvl, tvl_ch30: s.tvl_ch30, ch24: (md.price_change_percentage_24h || null)
    };
  }

  // ------------------------------------------------------------ search
  var q = $("pl-q"), list = $("pl-suggest");
  function showSuggest(arr) {
    items = arr; active = -1;
    list.hidden = !arr.length;
    q.parentNode.setAttribute("aria-expanded", String(!!arr.length));
    list.innerHTML = arr.map(function (c, i) {
      return '<li role="option" data-i="' + i + '" aria-selected="false">' + (c.thumb ? '<img src="' + esc(c.thumb) + '" alt="">' : '<span class="ph"></span>') +
        '<span class="s">' + esc(c.symbol) + '</span><span class="n">' + esc(c.name) + "</span>" + (c.rank ? '<span class="r">#' + c.rank + "</span>" : "") + "</li>";
    }).join("");
  }
  q.addEventListener("input", function () {
    clearTimeout(searchTimer);
    var term = q.value.trim();
    if (term.length < 2) { showSuggest([]); return; }
    searchTimer = setTimeout(function () {
      getJSON(CG + "/search?query=" + encodeURIComponent(term)).then(function (d) {
        if (q.value.trim() !== term) return;
        showSuggest((d.coins || []).slice(0, 10).map(function (c) { return { id: c.id, symbol: (c.symbol || "").toUpperCase(), name: c.name, thumb: c.thumb, rank: c.market_cap_rank }; }));
      }).catch(function (e) { setStatus("Search failed: " + e.message, true); });
    }, 250);
  });
  q.addEventListener("keydown", function (e) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!items.length) return;
      active = (active + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      list.querySelectorAll("li").forEach(function (li, k) { li.setAttribute("aria-selected", String(k === active)); });
    } else if (e.key === "Enter") {
      e.preventDefault();
      var pick = items[active >= 0 ? active : 0];
      if (pick) choose(pick.id); else if (q.value.trim()) choose(q.value.trim().toLowerCase());
    } else if (e.key === "Escape") showSuggest([]);
  });
  list.addEventListener("mousedown", function (e) {
    var li = e.target.closest("li");
    if (li) { e.preventDefault(); choose(items[+li.getAttribute("data-i")].id); }
  });
  q.addEventListener("blur", function () { setTimeout(function () { showSuggest([]); }, 120); });

  function choose(id) {
    showSuggest([]);
    q.blur();
    try { history.replaceState(null, "", "?id=" + encodeURIComponent(id)); } catch (e) {}
    load(id);
  }

  function load(id) {
    var token = ++loadToken;
    setStatus("Loading " + id + " and its native markets…");
    $("pl-out").innerHTML = "";
    var url = CG + "/coins/" + encodeURIComponent(id) + "?localization=false&tickers=true&market_data=true&community_data=false&developer_data=true&sparkline=false";
    Promise.all([getJSON(url), btc(), scanReady]).then(function (res) {
      if (token !== loadToken) return;
      var c = res[0];
      q.value = c.name;
      return poolsFor(c).then(function (pools) {
        if (token !== loadToken) return;
        cur = { coin: c, pools: pools, v: view(c, pools, res[1]), sel: 0 };
        setStatus(pools.length ? "" : "No DEX pools found on the networks this coin is deployed to.");
        renderCoin();
      });
    }).catch(function (e) {
      if (token !== loadToken) return;
      setStatus("Couldn't load " + id + ": " + e.message + ".", true);
    });
  }

  function renderCoin() {
    var v = cur.v, rows = check(v), vd = verdict(v, rows), pools = cur.pools;
    var tradable = pools.length && !(v.on_major || []).length;
    $("pl-out").innerHTML =
      '<div class="rs-head">' + (v.image ? '<img src="' + esc(v.image) + '" width="40" height="40" alt="">' : "") +
        '<div class="rs-title"><h2>' + esc(v.name) + ' <span class="sym">' + esc(v.symbol) + '</span></h2><div class="rs-meta">Market cap ' + big(v.mcap) + " · 24h volume " + big(v.volume) +
        ' · <a href="research.html?id=' + encodeURIComponent(v.id) + '">full research</a></div></div>' +
        '<div class="rs-price"><div class="p">' + usd(v.price) + '</div><div class="ch">' + sgn(v.ch24) + " 24h</div></div></div>" +
      '<ul class="rs-flags"><li class="' + vd.cls + '"><b>' + esc(vd.text) + "</b></li></ul>" +
      '<h3 class="rs-h">Pre-listing checklist <span class="faint">the same five rules the bot uses</span></h3>' + checklist(rows) +
      '<h3 class="rs-h">Native markets <span class="faint">' + (pools.length ? "pick the pool to trade against" : "none found") + "</span></h3>" +
      (pools.length ? '<div class="tablewrap"><table><thead><tr><th class="l">Trade</th><th class="l">Pool</th><th class="l">DEX</th><th class="l">Network</th><th>Price</th><th>Liquidity</th><th>24h volume</th><th>Buys 6h</th></tr></thead><tbody>' +
        pools.slice(0, 12).map(function (p, i) {
          var bs = p.b6 + p.s6 ? p.b6 / (p.b6 + p.s6) * 100 : null;
          return '<tr><td class="l"><input type="radio" name="pl-pool" value="' + i + '"' + (i === cur.sel ? " checked" : "") + ' aria-label="Trade against ' + esc(p.name) + '"></td><td class="l"><a href="' + esc(p.link) + '" target="_blank" rel="noopener">' + esc(p.name) + "</a></td><td class=\"l\">" + esc(p.dex) +
            '</td><td class="l">' + esc(p.net) + "</td><td>" + usd(p.price) + "</td><td>" + big(p.liq) + "</td><td>" + big(p.vol24) + "</td><td>" + (bs == null ? "–" : n(bs, 0) + "%") + "</td></tr>";
        }).join("") + "</tbody></table></div>" : "") +
      (pools.length ? '<div class="pp-panel" style="margin-top:10px"><h3>Practice trade</h3>' +
        (tradable ? "" : '<p class="sub">Already on a major exchange: you can still practice, but it isn\'t a pre-listing trade.</p>') +
        '<div class="rs-actions" style="align-items:center"><label>Amount (USD) <input id="pl-amt" type="number" min="10" step="10" value="' + Math.round(Math.min((acctNow().cash || 0) * (P.position_pct || 5) / 100, (pools[cur.sel] || {}).liq * (P.max_pool_share_pct || 1) / 100) || 100) +
        '" style="width:110px;font:inherit;padding:5px 8px"></label><button type="button" class="rs-btn primary" id="pl-buy">Buy ' + esc(v.symbol) + '</button><span class="faint" id="pl-est"></span></div>' +
        '<p class="faint" style="margin:0">Fills at the pool\'s live price plus its price impact and a ' + (P.fee_pct || 0.4) + '% fee. The rules suggest ' + (P.position_pct || 5) + "% of the account and no more than " + (P.max_pool_share_pct || 1) + "% of the pool.</p></div>" : "");
    var est = function () {
      var p = pools[cur.sel], amt = +($("pl-amt") || {}).value || 0;
      if (p && $("pl-est")) $("pl-est").textContent = "≈ " + n(Math.min(30, amt / (p.liq / 2) * 100), 2) + "% price impact, " + n(amt / p.liq * 100, 2) + "% of the pool";
    };
    $("pl-out").querySelectorAll("input[name=pl-pool]").forEach(function (r) { r.addEventListener("change", function () { cur.sel = +r.value; est(); }); });
    if ($("pl-amt")) { $("pl-amt").addEventListener("input", est); est(); }
    if ($("pl-buy")) $("pl-buy").addEventListener("click", function () { buy(pools[cur.sel], +$("pl-amt").value, rows); });
  }

  // ------------------------------------------------------------ practice account (this browser)
  function acctNow() {
    if (acct) return acct;
    try { acct = JSON.parse(localStorage.getItem(KEY) || "null"); } catch (e) { acct = null; }
    if (!acct) acct = { v: 1, start: 10000, cash: 10000, lots: [], trades: [], next: 1, created: Date.now() };
    return acct;
  }
  function save() { try { localStorage.setItem(KEY, JSON.stringify(acct)); } catch (e) { setStatus("Couldn't save: this browser blocked storage, so trades won't persist.", true); } }
  function livePool(net, addr, token) {
    return getJSON(GT + "/networks/" + net + "/pools/" + addr).then(function (d) {
      var a = (d.data || {}).attributes || {}, baseId = ((((d.data || {}).relationships || {}).base_token || {}).data || {}).id || "";
      var isBase = baseId.toLowerCase() === (net + "_" + token).toLowerCase();
      return { price: +(isBase ? a.base_token_price_usd : a.quote_token_price_usd), liq: +a.reserve_in_usd || 0 };
    });
  }
  function buy(p, amt, rows) {
    var a = acctNow();
    if (!p || !(amt > 0)) return;
    if (amt > a.cash) { setStatus("Not enough practice cash: " + usd(a.cash) + " left.", true); return; }
    $("pl-buy").disabled = true;
    livePool(p.net, p.pool, p.token).then(function (lp) {
      var imp = Math.min(0.3, amt / (lp.liq / 2)), fill = lp.price * (1 + imp), qty = amt * (1 - (P.fee_pct || 0.4) / 100) / fill;
      var passed = rows.filter(function (r) { return r.ok === true; }).length;
      var lot = { pid: a.next++, id: cur.v.id, symbol: cur.v.symbol, name: cur.v.name, net: p.net, pool: p.pool, token: p.token, poolName: p.name + " · " + p.dex,
        qty: qty, entry: fill, cost: amt, opened: Date.now(), rules: passed + "/5", entryLiq: lp.liq, onMajor: cur.v.on_major || [] };
      a.cash -= amt;
      a.lots.push(lot);
      a.trades.push({ type: "Open", t: lot.opened, pid: lot.pid, id: lot.id, symbol: lot.symbol, setup: "Pre-listing (manual)", price: fill, size: amt, slip: imp * 100,
        reason: "Rules passed " + lot.rules + " · " + lot.poolName });
      save();
      marks[lot.token] = lp.price;
      setStatus("Bought " + n(qty, qty > 100 ? 0 : 4) + " " + lot.symbol + " at " + usd(fill) + " (" + n(imp * 100, 2) + "% price impact).");
      renderAcct();
    }).catch(function (e) { setStatus("Couldn't get the pool's live price: " + e.message + ".", true); })
      .then(function () { if ($("pl-buy")) $("pl-buy").disabled = false; });
  }
  function sell(pid, frac) {
    var a = acctNow(), lot = a.lots.filter(function (l) { return l.pid === pid; })[0];
    if (!lot) return;
    livePool(lot.net, lot.pool, lot.token).then(function (lp) {
      var qty = lot.qty * frac, cost = lot.cost * frac, imp = Math.min(0.3, qty * lp.price / (lp.liq / 2));
      var fill = lp.price * (1 - imp), proceeds = qty * fill * (1 - (P.fee_pct || 0.4) / 100), pnl = proceeds - cost, now = Date.now();
      a.cash += proceeds;
      a.trades.push({ type: "Close", t: now, pid: lot.pid, id: lot.id, symbol: lot.symbol, setup: "Pre-listing (manual)", price: fill, size: proceeds, pnl: pnl,
        pnl_pct: pnl / cost * 100, cost: cost, opened: lot.opened, held: now - lot.opened, reason: frac < 0.999 ? "Sold half" : "Sold", rule: "Manual · " + lot.poolName,
        entry_reason: "Rules passed " + lot.rules, partial: frac < 0.999 ? frac : undefined });
      if (frac < 0.999) { lot.qty -= qty; lot.cost -= cost; } else a.lots = a.lots.filter(function (l) { return l !== lot; });
      save();
      marks[lot.token] = lp.price;
      setStatus("Sold " + (frac < 0.999 ? "half of " : "") + lot.symbol + " at " + usd(fill) + ": " + (pnl >= 0 ? "+" : "-") + "$" + n(Math.abs(pnl)) + ".");
      renderAcct();
    }).catch(function (e) { setStatus("Couldn't get the pool's live price to sell: " + e.message + ".", true); });
  }
  function refreshMarks() {
    var a = acctNow(), byNet = {};
    a.lots.forEach(function (l) { (byNet[l.net] = byNet[l.net] || {})[l.token] = 1; });
    return Promise.all(Object.keys(byNet).map(function (net) {
      var toks = Object.keys(byNet[net]).slice(0, 30);
      return getJSON(GT + "/simple/networks/" + net + "/token_price/" + toks.join(",")).then(function (d) {
        var tp = (((d || {}).data || {}).attributes || {}).token_prices || {};
        Object.keys(tp).forEach(function (k) {
          toks.forEach(function (t) { if (t.toLowerCase() === k.toLowerCase()) marks[t] = +tp[k]; });
        });
      }).catch(function () {});
    })).then(renderAcct);
  }
  function renderAcct() {
    var a = acctNow(), closes = a.trades.filter(function (t) { return t.type === "Close"; });
    var value = a.cash + a.lots.reduce(function (s, l) { return s + l.qty * (marks[l.token] || l.entry); }, 0);
    var open = a.lots.reduce(function (s, l) { return s + l.qty * (marks[l.token] || l.entry) - l.cost; }, 0);
    var real = closes.reduce(function (s, t) { return s + t.pnl; }, 0), wins = closes.filter(function (t) { return t.pnl > 0; }).length;
    $("pl-acct").innerHTML = '<div class="cards">' + card("Account value", usd(value), sgn((value / a.start - 1) * 100) + " since start") +
      card("Cash", usd(a.cash), a.lots.length + " open") + card("Open P&amp;L", sgn(open, "$"), "at live pool prices") +
      card("Realized P&amp;L", sgn(real, "$"), closes.length ? wins + " of " + closes.length + " won" : "no closed trades yet") + "</div>" +
      (a.lots.length ? '<div class="tablewrap" style="margin-top:10px"><table><thead><tr><th class="l">Coin</th><th class="l">Pool</th><th>Entry</th><th>Now</th><th>P&amp;L</th><th>Cost</th><th class="l">Rules at entry</th><th class="l">Opened</th><th class="l"></th></tr></thead><tbody>' +
        a.lots.map(function (l) {
          var now = marks[l.token] || l.entry;
          return '<tr><td class="l"><b><a href="?id=' + encodeURIComponent(l.id) + '">' + esc(l.symbol) + '</a></b></td><td class="l">' + esc(l.poolName) + "</td><td>" + usd(l.entry) + "</td><td>" + usd(now) +
            "</td><td>" + sgn((now / l.entry - 1) * 100) + "</td><td>" + usd(l.cost) + '</td><td class="l">' + esc(l.rules) + '</td><td class="l">' + stamp(l.opened).slice(0, 10) +
            '</td><td class="l"><button type="button" class="rs-btn" data-sell="' + l.pid + '" data-frac="0.5">Sell half</button> <button type="button" class="rs-btn" data-sell="' + l.pid + '" data-frac="1">Sell all</button></td></tr>';
        }).join("") + "</tbody></table></div>" : '<p class="sub">No open positions. Search a coin above and buy against one of its pools.</p>') +
      (closes.length ? '<h3 class="rs-h">Closed trades</h3><div class="tablewrap"><table><thead><tr><th class="l">Closed (UTC)</th><th class="l">Coin</th><th>P&amp;L</th><th>Held</th><th class="l">Entry</th></tr></thead><tbody>' +
        closes.slice().reverse().slice(0, 50).map(function (t) {
          return '<tr><td class="l">' + stamp(t.t) + '</td><td class="l"><b>' + esc(t.symbol) + "</b>" + (t.partial ? ' <span class="faint">half</span>' : "") + "</td><td>" + sgn(t.pnl, "$") + " " + sgn(t.pnl_pct) +
            "</td><td>" + n(t.held / 86400000, 1) + 'd</td><td class="l pp-note">' + esc(t.entry_reason) + "</td></tr>";
        }).join("") + "</tbody></table></div>" : "") +
      '<p class="faint" style="margin:10px 0 0">Starts with $' + n(a.start, 0) + ' of practice money. Closed trades also appear in the Journal and Trade review on the Practice page. <button type="button" class="rs-btn" id="pl-reset">Reset account</button></p>';
    $("pl-acct").querySelectorAll("[data-sell]").forEach(function (b) {
      b.addEventListener("click", function () { b.disabled = true; sell(+b.getAttribute("data-sell"), +b.getAttribute("data-frac")); });
    });
    $("pl-reset").addEventListener("click", function () {
      if (!window.confirm("Reset the pre-listing practice account? This clears its positions and history in this browser.")) return;
      acct = null; try { localStorage.removeItem(KEY); } catch (e) {}
      renderAcct();
    });
  }

  // ------------------------------------------------------------ candidates and the bot
  function renderCands() {
    var rows = ((scan || {}).prelisting || []).map(function (v) { return { v: v, rows: check(v), sig: signals(v) }; });
    if (!scan) { $("pl-cands").innerHTML = '<p class="sub" style="margin:0">The altcoin scan hasn\'t produced a list yet; it fills in on the next refresh.</p>'; return; }
    if (!rows.length) { $("pl-cands").innerHTML = '<p class="sub" style="margin:0">No coins in the latest scan have a native market in range without a major listing. The list grows as the scan checks more coins in detail.</p>'; return; }
    rows.sort(function (a, b) {
      var pa = a.rows.filter(function (r) { return r.ok === true; }).length, pb = b.rows.filter(function (r) { return r.ok === true; }).length;
      return pb - pa || (b.v.score || 0) - (a.v.score || 0);
    });
    $("pl-cands").innerHTML = '<p class="sub" style="margin:0 0 8px">' + rows.length + " coins from the scan of " + esc(scan.updated || "the last refresh") + ". Click one to load it above.</p>" +
      '<div class="tablewrap"><table><thead><tr><th class="l">Coin</th><th>Rules</th><th>Pool</th><th>Buys 6h</th><th>Score</th><th class="l">Missing</th><th class="l">Listing signals</th></tr></thead><tbody>' +
      rows.map(function (r) {
        var v = r.v, pass = r.rows.filter(function (x) { return x.ok === true; }).length, miss = r.rows.filter(function (x) { return x.ok !== true; }).map(function (x) { return x.label; });
        return '<tr><td class="l"><b><a href="?id=' + encodeURIComponent(v.id) + '" data-load="' + esc(v.id) + '">' + esc(v.symbol) + '</a></b> <span class="faint">' + esc(v.name) + "</span></td><td>" +
          (pass === 5 ? '<span class="tag jr-done">5/5</span>' : pass + "/5") + "</td><td>" + big((v.pool || {}).liquidity) + "</td><td>" +
          ((v.pool || {}).buy_share_6h != null ? n(v.pool.buy_share_6h * 100, 0) + "%" : "–") + "</td><td>" + (v.score == null ? "–" : v.score) +
          '</td><td class="l pp-note">' + esc(miss.join(", ") || "nothing") + '</td><td class="l pp-note">' + esc(r.sig.slice(0, 3).join("; ")) + "</td></tr>";
      }).join("") + "</tbody></table></div>";
    $("pl-cands").querySelectorAll("[data-load]").forEach(function (a) {
      a.addEventListener("click", function (e) { e.preventDefault(); choose(a.getAttribute("data-load")); window.scrollTo({ top: 0, behavior: "smooth" }); });
    });
  }
  function renderBot() {
    var b = (bot || {}).prelist;
    if (!b) { $("pl-bot").innerHTML = '<p class="sub" style="margin:0">The bot starts its pre-listing account on its next run.</p>'; return; }
    var eq = b.equity && b.equity.length ? b.equity[b.equity.length - 1].v : b.start, closes = b.trades.filter(function (t) { return t.type === "Close"; });
    var real = closes.reduce(function (s, t) { return s + t.pnl; }, 0), listed = closes.filter(function (t) { return t.reason === "Listed on a major"; }).length;
    var last = (bot.ticks || []).slice(-1)[0] || {}, note = (last.actions || []).filter(function (a) { return a.indexOf("Pre-listing") === 0; })[0];
    $("pl-bot").innerHTML = '<div class="cards">' + card("Account value", usd(eq), sgn((eq / b.start - 1) * 100) + " since start") + card("Open", b.positions.length + " coins", "") +
      card("Realized P&amp;L", sgn(real, "$"), closes.length ? closes.filter(function (t) { return t.pnl > 0; }).length + " of " + closes.length + " won" : "no closed trades yet") +
      card("Listings caught", String(listed), "sold into a major listing") + "</div>" +
      (b.positions.length ? '<div class="tablewrap" style="margin-top:10px"><table><thead><tr><th class="l">Coin</th><th>Entry</th><th>Now</th><th>P&amp;L</th><th class="l">Status</th><th class="l">Opened</th></tr></thead><tbody>' +
        b.positions.map(function (p) {
          var now = p.mark || p.entry;
          return '<tr><td class="l"><b><a href="?id=' + encodeURIComponent(p.id) + '">' + esc(p.symbol) + "</a></b></td><td>" + usd(p.entry) + "</td><td>" + usd(now) + "</td><td>" + sgn((now / p.entry - 1) * 100) +
            '</td><td class="l">' + (p.listed ? "Listed on " + esc((p.listed.on || []).join(", ")) + ", trailing" : "Waiting for a listing") + '</td><td class="l">' + stamp(p.opened).slice(0, 10) + "</td></tr>";
        }).join("") + "</tbody></table></div>" : "") +
      (note ? '<p class="faint" style="margin:10px 0 0">Last run: ' + esc(note) + "</p>" : "") +
      '<p class="faint" style="margin:6px 0 0">Full trade log on the <a href="paper.html#bot">Practice page\'s Bot tab</a>.</p>';
  }

  renderAcct();
  if (acctNow().lots.length) refreshMarks();
  setInterval(function () { if (!document.hidden && acctNow().lots.length) refreshMarks(); }, 60000);
  var start = new URLSearchParams(location.search).get("id");
  if (start) load(start.toLowerCase());
})();
