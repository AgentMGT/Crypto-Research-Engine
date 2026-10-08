// Coin research page: search any coin, then profile it from CoinGecko, GeckoTerminal
// and this engine's latest snapshot (crypto-latest.json). Everything runs in the browser.
(function () {
  var CG = "https://api.coingecko.com/api/v3";
  var GT = "https://api.geckoterminal.com/api/v2";
  var DEX_HINTS = ["uniswap", "raydium", "orca", "meteora", "jupiter", "aerodrome", "velodrome", "pancakeswap",
    "sushi", "curve", "balancer", "camelot", "trader_joe", "hydradx", "osmosis", "swap", "dex"];

  var q = document.getElementById("rs-q");
  var list = document.getElementById("rs-suggest");
  var box = q.parentNode;
  var out = document.getElementById("rs-out");
  var status = document.getElementById("rs-status");
  var quick = document.getElementById("rs-quick");
  var snap = null;        // crypto-latest.json
  var active = -1;        // highlighted suggestion
  var items = [];         // current suggestions
  var searchTimer = null;
  var searchToken = 0;
  var loadToken = 0;

  // ------------------------------------------------------------ helpers
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function getJSON(url) {
    return fetch(url, { headers: { Accept: "application/json" } }).then(function (r) {
      if (r.status === 429) throw new Error("CoinGecko's free tier is rate-limiting this browser; wait a minute and try again");
      if (r.status === 404) throw new Error("not found");
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }
  function money(v) {
    if (v == null || isNaN(v)) return "–";
    var a = Math.abs(v);
    if (a >= 1e12) return "$" + (v / 1e12).toFixed(2) + "T";
    if (a >= 1e9) return "$" + (v / 1e9).toFixed(2) + "B";
    if (a >= 1e6) return "$" + (v / 1e6).toFixed(2) + "M";
    if (a >= 1e3) return "$" + (v / 1e3).toFixed(1) + "K";
    return price(v);
  }
  function price(v) {
    if (v == null || isNaN(v)) return "–";
    var a = Math.abs(v);
    var dp = a >= 1 ? 2 : a >= 0.01 ? 4 : a >= 0.0001 ? 6 : 8;
    return "$" + v.toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });
  }
  function num(v, dp) {
    if (v == null || isNaN(v)) return "–";
    return v.toLocaleString("en-US", { maximumFractionDigits: dp == null ? 0 : dp });
  }
  function big(v) {
    if (v == null || isNaN(v)) return "–";
    var a = Math.abs(v);
    if (a >= 1e12) return (v / 1e12).toFixed(2) + "T";
    if (a >= 1e9) return (v / 1e9).toFixed(2) + "B";
    if (a >= 1e6) return (v / 1e6).toFixed(2) + "M";
    if (a >= 1e3) return (v / 1e3).toFixed(1) + "K";
    return num(v, 2);
  }
  function pct(v, dp) {
    if (v == null || isNaN(v)) return '<span class="flat">–</span>';
    var c = v > 0 ? "up" : v < 0 ? "down" : "flat";
    return '<span class="' + c + '">' + (v > 0 ? "+" : "") + v.toFixed(dp == null ? 1 : dp) + "%</span>";
  }
  function chartAttr(spec) {
    return ' data-chart="' + esc(JSON.stringify(spec)) + '" tabindex="0" role="button"';
  }
  function isDex(m) {
    var s = ((m.identifier || "") + " " + (m.name || "")).toLowerCase();
    return DEX_HINTS.some(function (h) { return s.indexOf(h) >= 0; });
  }
  function stripHTML(html) {
    var d = new DOMParser().parseFromString(html || "", "text/html");
    return (d.body.textContent || "").trim();
  }
  function setStatus(text, kind) {
    status.textContent = text || "";
    status.className = "rs-status" + (kind ? " " + kind : "");
  }

  // ------------------------------------------------------------ snapshot
  var snapReady = fetch("crypto-latest.json").then(function (r) { return r.ok ? r.json() : null; })
    .catch(function () { return null; })
    .then(function (d) { snap = d; renderQuick(); return d; });

  function renderQuick() {
    var picks = [];
    if (snap && snap.coins) {
      picks = snap.coins.slice().sort(function (a, b) { return a.rank - b.rank; }).slice(0, 6);
      (snap.ideas || []).slice(0, 4).forEach(function (c) {
        if (!picks.some(function (p) { return p.id === c.id; })) picks.push(c);
      });
    } else {
      picks = [{ id: "bitcoin", symbol: "BTC" }, { id: "ethereum", symbol: "ETH" }, { id: "solana", symbol: "SOL" }, { id: "bittensor", symbol: "TAO" }];
    }
    quick.innerHTML = '<span>Try</span>' + picks.map(function (c) {
      return '<button type="button" data-id="' + esc(c.id) + '">' + esc((c.symbol || "").toUpperCase()) + "</button>";
    }).join("");
  }
  quick.addEventListener("click", function (e) {
    var b = e.target.closest("button[data-id]");
    if (b) choose(b.dataset.id);
  });

  // ------------------------------------------------------------ search box
  function localMatches(term) {
    if (!snap || !snap.coins) return [];
    var t = term.toLowerCase();
    return snap.coins.filter(function (c) {
      return c.symbol.toLowerCase() === t || c.name.toLowerCase().indexOf(t) === 0 || c.symbol.toLowerCase().indexOf(t) === 0;
    }).sort(function (a, b) {
      var ea = a.symbol.toLowerCase() === t ? 0 : 1, eb = b.symbol.toLowerCase() === t ? 0 : 1;
      return ea - eb || a.rank - b.rank;
    }).slice(0, 6).map(function (c) {
      return { id: c.id, name: c.name, symbol: c.symbol.toUpperCase(), rank: c.rank, thumb: c.image, tracked: true };
    });
  }

  function showSuggest(arr) {
    items = arr;
    active = arr.length ? 0 : -1;
    if (!arr.length) { list.hidden = true; box.setAttribute("aria-expanded", "false"); return; }
    list.innerHTML = arr.map(function (c, i) {
      return '<li role="option" id="rs-opt-' + i + '" data-i="' + i + '"' + (i === active ? ' aria-selected="true"' : "") + ">" +
        (c.thumb ? '<img src="' + esc(c.thumb) + '" alt="" width="20" height="20" loading="lazy">' : '<span class="ph"></span>') +
        '<span class="s">' + esc(c.symbol) + '</span><span class="n">' + esc(c.name) + "</span>" +
        (c.tracked ? '<span class="t">in screener</span>' : "") +
        '<span>' + (c.rank ? "#" + c.rank : "unranked") + "</span></li>";
    }).join("");
    list.hidden = false;
    box.setAttribute("aria-expanded", "true");
    q.setAttribute("aria-activedescendant", "rs-opt-0");
  }

  function highlight(i) {
    if (!items.length) return;
    active = (i + items.length) % items.length;
    list.querySelectorAll("li").forEach(function (li, k) { li.setAttribute("aria-selected", String(k === active)); });
    q.setAttribute("aria-activedescendant", "rs-opt-" + active);
    var li = list.children[active];
    if (li && li.scrollIntoView) li.scrollIntoView({ block: "nearest" });
  }

  q.addEventListener("input", function () {
    var term = q.value.trim();
    clearTimeout(searchTimer);
    if (term.length < 2) { showSuggest(term ? localMatches(term) : []); return; }
    var local = localMatches(term);
    showSuggest(local);
    var token = ++searchToken;
    searchTimer = setTimeout(function () {
      getJSON(CG + "/search?query=" + encodeURIComponent(term)).then(function (d) {
        if (token !== searchToken) return;
        var seen = {};
        local.forEach(function (c) { seen[c.id] = 1; });
        var remote = (d.coins || []).filter(function (c) { return !seen[c.id]; }).slice(0, 10).map(function (c) {
          return { id: c.id, name: c.name, symbol: (c.symbol || "").toUpperCase(), rank: c.market_cap_rank, thumb: c.thumb };
        });
        showSuggest(local.concat(remote));
      }).catch(function (e) {
        if (token === searchToken && !local.length) setStatus("Search failed: " + e.message + ".", "bad");
      });
    }, 300);
  });

  q.addEventListener("keydown", function (e) {
    if (e.key === "ArrowDown") { e.preventDefault(); highlight(active + 1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); highlight(active - 1); }
    else if (e.key === "Escape") { showSuggest([]); }
    else if (e.key === "Enter") {
      e.preventDefault();
      if (items[active]) choose(items[active].id);
      else if (q.value.trim()) choose(q.value.trim().toLowerCase());
    }
  });
  list.addEventListener("mousedown", function (e) {
    var li = e.target.closest("li[data-i]");
    if (li) { e.preventDefault(); choose(items[+li.dataset.i].id); }
  });
  q.addEventListener("blur", function () { setTimeout(function () { showSuggest([]); }, 120); });

  // ------------------------------------------------------------ load a coin
  function choose(id) {
    showSuggest([]);
    q.blur();
    try { history.replaceState(null, "", "?id=" + encodeURIComponent(id)); } catch (e) {}
    load(id);
  }

  function load(id, retried) {
    var token = ++loadToken;
    setStatus("Loading " + id + "…");
    out.innerHTML = "";
    var url = CG + "/coins/" + encodeURIComponent(id) +
      "?localization=false&tickers=true&market_data=true&community_data=false&developer_data=true&sparkline=false";
    Promise.all([getJSON(url), snapReady]).then(function (res) {
      if (token !== loadToken) return;
      var c = res[0];
      document.title = (c.symbol || "").toUpperCase() + " · " + c.name + " — research engine";
      q.value = c.name;
      setStatus("");
      out.innerHTML = render(c);
      pools(c, token);
    }).catch(function (e) {
      if (token !== loadToken) return;
      if (e.message === "not found" && !retried) {
        // Typed a ticker or name rather than an id: take the best search match.
        return getJSON(CG + "/search?query=" + encodeURIComponent(id)).then(function (d) {
          var hit = (d.coins || [])[0];
          if (token !== loadToken) return;
          if (hit) { try { history.replaceState(null, "", "?id=" + encodeURIComponent(hit.id)); } catch (x) {} load(hit.id, true); }
          else setStatus("No coin matches “" + id + "”.", "bad");
        }).catch(function (x) { setStatus("Couldn't load " + id + ": " + x.message + ".", "bad"); });
      }
      setStatus("Couldn't load " + id + ": " + e.message + ".", "bad");
    });
  }

  // ------------------------------------------------------------ analysis
  function venues(c) {
    var rows = (c.tickers || []).map(function (t) {
      var m = t.market || {};
      return {
        venue: m.name || m.identifier, dex: isDex(m),
        pair: (t.base || "").slice(0, 12) + "/" + (t.target || "").slice(0, 12),
        price: (t.converted_last || {}).usd, vol: (t.converted_volume || {}).usd || 0,
        spread: t.bid_ask_spread_percentage, trust: t.trust_score,
        flag: t.is_anomaly ? "anomaly" : t.is_stale ? "stale" : "", url: t.trade_url
      };
    }).filter(function (r) { return r.vol > 0 && !r.flag; });
    rows.sort(function (a, b) { return b.vol - a.vol; });
    var total = rows.reduce(function (s, r) { return s + r.vol; }, 0);
    var dex = rows.filter(function (r) { return r.dex; }).reduce(function (s, r) { return s + r.vol; }, 0);
    var byVenue = {};
    rows.forEach(function (r) { byVenue[r.venue] = (byVenue[r.venue] || 0) + r.vol; });
    var top = Object.keys(byVenue).sort(function (a, b) { return byVenue[b] - byVenue[a]; })[0];
    return { rows: rows, total: total, dexShare: total ? dex / total * 100 : null, top: top, topShare: total && top ? byVenue[top] / total * 100 : null, count: Object.keys(byVenue).length };
  }

  function engine(c) {
    if (!snap) return null;
    var coin = (snap.coins || []).filter(function (x) { return x.id === c.id; })[0] || null;
    var v = snap.venues || {};
    var perp = (v.perps || []).filter(function (p) { return p.cg_id === c.id; })[0] ||
      (v.perps || []).filter(function (p) { return (p.base || "").toUpperCase() === (c.symbol || "").toUpperCase(); })[0] || null;
    var nameRe = new RegExp("\\b(" + c.name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + ")\\b", "i");
    var symRe = (c.symbol || "").length >= 3 ? new RegExp("\\b" + c.symbol.toUpperCase() + "\\b") : null;
    var news = []
      .concat((snap.news || {}).crypto || [])
      .filter(function (n) {
        return (n.coins || []).some(function (x) { return x.id === c.id; }) || nameRe.test(n.title) || (symRe && symRe.test(n.title));
      }).slice(0, 12);
    return { coin: coin, perp: perp, news: news, updated: snap.updated };
  }

  function read(c, md, ven, eng) {
    var f = [];
    function add(kind, text) { f.push({ kind: kind, text: text }); }
    var mc = md.market_cap.usd, fdv = md.fully_diluted_valuation.usd, vol = md.total_volume.usd;
    var circ = md.circulating_supply, max = md.max_supply || md.total_supply;
    if (circ && max) {
      var share = circ / max * 100;
      if (share < 50) add("bad", "Only " + share.toFixed(0) + "% of supply is circulating. Future unlocks and emissions can weigh on price; check the vesting schedule.");
      else if (share < 80) add("warn", share.toFixed(0) + "% of supply is circulating, so some dilution is still ahead.");
      else add("good", share.toFixed(0) + "% of supply is already circulating, so dilution risk is low.");
    }
    if (mc && fdv && fdv / mc > 1.5) add(fdv / mc > 3 ? "bad" : "warn", "Fully diluted value is " + (fdv / mc).toFixed(1) + "× the market cap.");
    if (mc && vol) {
      var t = vol / mc * 100;
      if (t < 1) add("warn", "Thin trading: 24h volume is only " + t.toFixed(2) + "% of market cap.");
      else if (t > 40) add("warn", "Very heavy turnover (" + t.toFixed(0) + "% of market cap in 24h), typical of speculative or event-driven flows.");
      else add("good", "Healthy liquidity: 24h volume is " + t.toFixed(1) + "% of market cap.");
    }
    var ath = md.ath_change_percentage.usd;
    if (ath != null) {
      if (ath > -10) add("warn", "Trading within " + Math.abs(ath).toFixed(0) + "% of its all-time high.");
      else if (ath < -85) add("bad", "Down " + Math.abs(ath).toFixed(0) + "% from its all-time high; a long way from prior interest.");
      else if (ath < -60) add("warn", "Down " + Math.abs(ath).toFixed(0) + "% from its all-time high.");
    }
    var c7 = md.price_change_percentage_7d, c30 = md.price_change_percentage_30d;
    if (c7 != null && c30 != null) {
      if (c7 > 0 && c30 > 0) add("good", "Momentum is positive on both 7 and 30 days (" + c7.toFixed(1) + "%, " + c30.toFixed(1) + "%).");
      else if (c7 < 0 && c30 < 0) add("bad", "Momentum is negative on both 7 and 30 days (" + c7.toFixed(1) + "%, " + c30.toFixed(1) + "%).");
      else add("warn", "Short and medium-term momentum disagree (7d " + c7.toFixed(1) + "%, 30d " + c30.toFixed(1) + "%).");
    }
    if (ven.topShare != null && ven.count > 1 && ven.topShare > 50) add("warn", ven.top + " carries " + ven.topShare.toFixed(0) + "% of reported volume, so price discovery is concentrated in one venue.");
    if (ven.dexShare != null && ven.total) {
      if (ven.dexShare > 60) add("warn", ven.dexShare.toFixed(0) + "% of volume is on DEXs; on-chain liquidity matters more than exchange listings here.");
      else if (ven.dexShare < 5 && (c.platforms && Object.keys(c.platforms).filter(Boolean).length)) add("warn", "Almost all volume is on centralized exchanges.");
    }
    if (c.genesis_date) {
      var age = (Date.now() - Date.parse(c.genesis_date)) / 31557600000;
      if (age < 1) add("warn", "Young asset: launched " + c.genesis_date + ".");
    }
    if (eng && eng.perp) {
      var fr = eng.perp.funding_8h;
      if (fr != null && fr > 0.03) add("warn", "Perp funding is " + fr.toFixed(3) + "% per 8h; longs are crowded.");
      if (fr != null && fr < -0.01) add("good", "Perp funding is negative (" + fr.toFixed(3) + "% per 8h); shorts are paying, so a squeeze is possible.");
      if (eng.perp.oi_to_mcap != null && eng.perp.oi_to_mcap > 10) add("warn", "Perp open interest is " + eng.perp.oi_to_mcap.toFixed(0) + "% of market cap, so leverage could drive moves.");
    }
    if (c.categories && c.categories.some(function (x) { return /meme/i.test(x || ""); })) add("warn", "Classified as a meme coin; price is mostly driven by attention.");
    return f;
  }

  // ------------------------------------------------------------ render
  function card(k, v, n) {
    return '<div class="card"><div class="k">' + k + '</div><div class="v">' + v + "</div>" + (n ? '<div class="n">' + n + "</div>" : "") + "</div>";
  }

  function render(c) {
    var md = c.market_data || {};
    ["market_cap", "fully_diluted_valuation", "total_volume", "ath", "atl", "ath_change_percentage", "atl_change_percentage", "ath_date", "atl_date", "current_price"].forEach(function (k) { md[k] = md[k] || {}; });
    var sym = (c.symbol || "").toUpperCase();
    var ven = venues(c);
    var eng = engine(c);
    var flags = read(c, md, ven, eng);
    var spec = { t: "cg", id: c.id, label: sym + " · " + c.name };
    var links = c.links || {};
    var home = (links.homepage || []).filter(Boolean)[0];
    var explorer = (links.blockchain_site || []).filter(Boolean)[0];
    var x = links.twitter_screen_name;
    var gh = ((links.repos_url || {}).github || []).filter(Boolean)[0];
    var mc = md.market_cap.usd, fdv = md.fully_diluted_valuation.usd;
    var circ = md.circulating_supply, max = md.max_supply || md.total_supply;
    var h = "";

    h += '<div class="rs-head">' +
      (c.image && c.image.large ? '<img src="' + esc(c.image.large) + '" alt="" width="44" height="44">' : "") +
      '<div class="rs-title"><h2>' + esc(c.name) + ' <span class="sym">' + esc(sym) + "</span></h2>" +
      '<div class="rs-meta">' + (c.market_cap_rank ? "Rank #" + c.market_cap_rank : "Unranked") +
      ((c.categories || []).filter(Boolean).slice(0, 3).map(function (x) { return ' · ' + esc(x); }).join("")) + "</div></div>" +
      '<div class="rs-price"><div class="p">' + price(md.current_price.usd) + "</div>" +
      '<div class="ch">' + pct(md.price_change_percentage_1h_in_currency && md.price_change_percentage_1h_in_currency.usd) + " 1h · " +
      pct(md.price_change_percentage_24h) + " 24h</div></div></div>";

    h += '<div class="rs-actions"><button type="button" class="rs-btn primary"' + chartAttr(spec).replace(' tabindex="0" role="button"', "") + ">Open chart</button>" +
      '<a class="rs-btn" target="_blank" rel="noopener" href="https://www.coingecko.com/en/coins/' + esc(c.id) + '">CoinGecko ↗</a>' +
      (home ? '<a class="rs-btn" target="_blank" rel="noopener" href="' + esc(home) + '">Website ↗</a>' : "") +
      (explorer ? '<a class="rs-btn" target="_blank" rel="noopener" href="' + esc(explorer) + '">Explorer ↗</a>' : "") +
      (x ? '<a class="rs-btn" target="_blank" rel="noopener" href="https://x.com/' + esc(x) + '">X ↗</a>' : "") +
      (gh ? '<a class="rs-btn" target="_blank" rel="noopener" href="' + esc(gh) + '">GitHub ↗</a>' : "") + "</div>";

    h += '<div class="rs-changes">' + [["24h", md.price_change_percentage_24h], ["7d", md.price_change_percentage_7d],
      ["30d", md.price_change_percentage_30d], ["60d", md.price_change_percentage_60d], ["1y", md.price_change_percentage_1y]]
      .map(function (p) { return "<div><span>" + p[0] + "</span>" + pct(p[1]) + "</div>"; }).join("") + "</div>";

    h += '<div class="cards">' +
      card("Market cap", money(mc), fdv ? "FDV " + money(fdv) + (mc ? " · " + (fdv / mc).toFixed(2) + "×" : "") : "") +
      card("24h volume", money(md.total_volume.usd), mc ? (md.total_volume.usd / mc * 100).toFixed(1) + "% of market cap" : "") +
      card("Circulating supply", big(circ) + " " + esc(sym), max ? (circ / max * 100).toFixed(0) + "% of " + big(max) + (md.max_supply ? " max" : " total") : "No max supply") +
      card("All-time high", price(md.ath.usd), pct(md.ath_change_percentage.usd, 0) + " · " + (md.ath_date.usd || "").slice(0, 10)) +
      card("All-time low", price(md.atl.usd), pct(md.atl_change_percentage.usd, 0) + " · " + (md.atl_date.usd || "").slice(0, 10)) +
      card("Markets", num(ven.count), ven.dexShare != null ? ven.dexShare.toFixed(0) + "% of volume on DEXs" : "") +
      "</div>";

    if (flags.length) {
      h += '<h3 class="rs-h">Research read</h3><ul class="rs-flags">' + flags.map(function (f) {
        return '<li class="' + f.kind + '">' + esc(f.text) + "</li>";
      }).join("") + '</ul><p class="rs-foot">Mechanical checks over the figures above, not advice.</p>';
    }

    if (eng) {
      var e = eng.coin, p = eng.perp;
      h += '<h3 class="rs-h">This engine\'s signals <span class="faint">from the ' + esc(eng.updated || "latest") + " scan</span></h3>";
      if (!e && !p) h += '<p class="sub">Not in the screener (top ~250 by market cap) or the perps book this run.</p>';
      h += '<div class="cards">';
      if (e) {
        h += card("Screener score", String(e.score), "rank #" + e.rank) +
          card("RSI (4h)", num(e.rsi, 0), e.rsi > 70 ? "overbought" : e.rsi < 30 ? "oversold" : "neutral") +
          card("vs BTC, 7d", pct(e.rs7), "relative strength") +
          card("7d range position", e.range7 != null ? num(e.range7, 0) + "%" : "–", "0 = low, 100 = high");
      }
      if (p) {
        h += card("Perp funding (8h)", p.funding_8h != null ? p.funding_8h.toFixed(4) + "%" : "–", p.funding_ann != null ? num(p.funding_ann, 1) + "% annualized" : "") +
          card("Open interest", money(p.oi_usd), p.oi_to_mcap != null ? num(p.oi_to_mcap, 1) + "% of market cap" : (p.venues ? p.venues + " venues" : ""));
      }
      h += "</div>";
      var tags = ((e && e.tags) || []).concat((p && p.tags) || []).filter(function (t, i, a) { return a.indexOf(t) === i; });
      if (tags.length) h += '<div class="tags">' + tags.map(function (t) { return '<span class="tag">' + esc(t) + "</span>"; }).join("") + "</div>";
    }

    if (ven.rows.length) {
      h += '<h3 class="rs-h">Where it trades <span class="faint">top markets by 24h volume, ' + money(ven.total) + " total</span></h3>" +
        '<div class="tablewrap"><table><thead><tr><th>Venue</th><th class="l">Type</th><th class="l">Pair</th><th>Price</th><th>24h volume</th><th>Share</th><th>Spread</th></tr></thead><tbody>' +
        ven.rows.slice(0, 15).map(function (r) {
          return "<tr><td>" + (r.url ? '<a target="_blank" rel="noopener" href="' + esc(r.url) + '">' + esc(r.venue) + "</a>" : esc(r.venue)) +
            (r.trust === "green" ? "" : r.trust ? ' <span class="faint">(' + esc(r.trust) + " trust)</span>" : "") + "</td>" +
            '<td class="l"><span class="tag">' + (r.dex ? "DEX" : "CEX") + "</span></td><td class=\"l\">" + esc(r.pair) + '</td><td>' + price(r.price) +
            '</td><td>' + money(r.vol) + '</td><td>' + (r.vol / ven.total * 100).toFixed(1) + '%</td><td>' +
            (r.spread != null ? r.spread.toFixed(2) + "%" : "–") + "</td></tr>";
        }).join("") + "</tbody></table></div>";
    }

    h += '<div id="rs-pools"></div>';

    if (eng) {
      h += '<h3 class="rs-h">In the news <span class="faint">last 7 days</span></h3>';
      h += eng.news.length ? '<ul class="news compact">' + eng.news.map(function (n) {
        return '<li class="news-item"><a class="news-title" target="_blank" rel="noopener" href="' + esc(n.link) + '">' + esc(n.title) + "</a>" +
          '<div class="news-meta"><span>' + esc(n.source) + "</span><span>" + esc(n.age) + "</span>" +
          (n.topics || []).map(function (t) { return '<span class="tag topic' + (t === "CLARITY Act" ? " hot" : "") + '">' + esc(t) + "</span>"; }).join("") + "</div></li>";
      }).join("") + "</ul>" : '<p class="sub">No headlines mentioned ' + esc(c.name) + " in this run's feeds.</p>";
    }

    var desc = stripHTML((c.description || {}).en);
    var plats = Object.keys(c.platforms || {}).filter(function (k) { return k && c.platforms[k]; });
    h += '<h3 class="rs-h">About</h3><div class="rs-about">';
    if (desc) h += '<p class="rs-desc">' + esc(desc.length > 900 ? desc.slice(0, 900).replace(/\s+\S*$/, "") + "…" : desc) + "</p>";
    h += '<dl class="rs-facts">' +
      (c.genesis_date ? "<dt>Launched</dt><dd>" + esc(c.genesis_date) + "</dd>" : "") +
      (c.hashing_algorithm ? "<dt>Algorithm</dt><dd>" + esc(c.hashing_algorithm) + "</dd>" : "") +
      ((c.categories || []).filter(Boolean).length ? "<dt>Categories</dt><dd>" + esc(c.categories.filter(Boolean).join(", ")) + "</dd>" : "") +
      (plats.length ? "<dt>Contracts</dt><dd>" + plats.slice(0, 6).map(function (k) {
        return '<div class="addr"><span>' + esc(k) + "</span> <code>" + esc(c.platforms[k]) + "</code></div>";
      }).join("") + "</dd>" : "") +
      (c.developer_data && c.developer_data.commit_count_4_weeks != null && gh ? "<dt>Development</dt><dd>" + num(c.developer_data.commit_count_4_weeks) + " commits in 4 weeks · " + num(c.developer_data.stars) + " GitHub stars</dd>" : "") +
      (c.sentiment_votes_up_percentage != null ? "<dt>CoinGecko sentiment</dt><dd>" + num(c.sentiment_votes_up_percentage, 0) + "% of voters bullish</dd>" : "") +
      "</dl></div>";
    return h;
  }

  // On-chain pools from GeckoTerminal; loads after the main profile so it never blocks it.
  function pools(c, token) {
    var el = document.getElementById("rs-pools");
    if (!el) return;
    var addrs = Object.keys(c.platforms || {}).map(function (k) { return (c.platforms[k] || "").toLowerCase(); }).filter(Boolean);
    var sym = (c.symbol || "").toUpperCase();
    var term = addrs[0] || sym;
    getJSON(GT + "/search/pools?query=" + encodeURIComponent(term) + "&include=base_token,dex").then(function (d) {
      if (token !== loadToken) return;
      var inc = {};
      (d.included || []).forEach(function (x) { inc[x.type + ":" + x.id] = x.attributes || {}; });
      var rows = (d.data || []).map(function (p) {
        var a = p.attributes || {}, rel = p.relationships || {};
        var base = inc["token:" + ((rel.base_token || {}).data || {}).id] || {};
        var dex = inc["dex:" + ((rel.dex || {}).data || {}).id] || {};
        var net = ((rel.network || {}).data || {}).id || (p.id || "").split("_")[0];
        return {
          name: a.name, net: net, pool: a.address, dex: dex.name || ((rel.dex || {}).data || {}).id,
          baseSym: (base.symbol || "").toUpperCase(), baseAddr: (base.address || "").toLowerCase(),
          liq: +a.reserve_in_usd || 0, vol: +((a.volume_usd || {}).h24) || 0, ch: +((a.price_change_percentage || {}).h24),
          link: "https://www.geckoterminal.com/" + net + "/pools/" + a.address
        };
      }).filter(function (r) {
        return addrs.length ? addrs.indexOf(r.baseAddr) >= 0 : r.baseSym === sym;
      }).sort(function (a, b) { return b.liq - a.liq; }).slice(0, 10);
      if (!rows.length) return;
      el.innerHTML = '<h3 class="rs-h">On-chain pools <span class="faint">click a pool for its chart</span></h3>' +
        '<div class="tablewrap"><table><thead><tr><th>Pool</th><th class="l">DEX</th><th class="l">Network</th><th>Liquidity</th><th>24h volume</th><th>24h</th></tr></thead><tbody>' +
        rows.map(function (r) {
          return "<tr" + chartAttr({ t: "gt", net: r.net, pool: r.pool, link: r.link, label: r.name + " · " + r.dex }) + "><td>" + esc(r.name) +
            "</td><td class=\"l\">" + esc(r.dex) + "</td><td class=\"l\">" + esc(r.net) + '</td><td>' + money(r.liq) + '</td><td>' + money(r.vol) +
            '</td><td>' + pct(isNaN(r.ch) ? null : r.ch) + "</td></tr>";
        }).join("") + "</tbody></table></div>";
    }).catch(function () { /* pools are a bonus; the profile already rendered */ });
  }

  // "Open chart" is a <button>, which app.js's delegated handler handles via data-chart.
  var params = new URLSearchParams(location.search);
  var start = params.get("id") || params.get("q");
  if (start) load(start.toLowerCase()); else q.focus();
})();
