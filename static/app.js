// Client-side sort + filter for the screener tables. No dependencies.
(function () {
  var table = document.getElementById("screener");
  if (!table) return;
  var tbody = table.tBodies[0];
  var rows = Array.prototype.slice.call(tbody.rows);

  function val(row, i) {
    var cell = row.cells[i];
    if (!cell) return "";
    var v = cell.getAttribute("data-v");
    return v !== null ? parseFloat(v) : cell.textContent.trim();
  }

  Array.prototype.forEach.call(table.tHead.rows[0].cells, function (th, i) {
    var desc = true;
    th.addEventListener("click", function () {
      rows.sort(function (a, b) {
        var x = val(a, i), y = val(b, i);
        if (typeof x === "number" && typeof y === "number") return desc ? y - x : x - y;
        return desc ? String(y).localeCompare(String(x)) : String(x).localeCompare(String(y));
      });
      desc = !desc;
      rows.forEach(function (r) { tbody.appendChild(r); });
    });
  });

  var q = document.getElementById("q");
  if (q) {
    q.addEventListener("input", function () {
      var term = q.value.trim().toLowerCase();
      rows.forEach(function (r) {
        r.style.display = !term || (r.getAttribute("data-q") || "").toLowerCase().indexOf(term) !== -1 ? "" : "none";
      });
    });
  }
})();

// Click-to-chart: any element with data-chart opens a chart panel.
// Data is fetched from the browser at click time from free public APIs; nothing is pre-rendered.
(function () {
  var LWC_SRC = "https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js";
  var modal = document.getElementById("chart-modal");
  if (!modal) return;
  var area = document.getElementById("chart-area");
  var titleEl = document.getElementById("chart-title");
  var rangesEl = document.getElementById("chart-ranges");
  var noteEl = document.getElementById("chart-note");
  var linkEl = document.getElementById("chart-link");
  var cache = {};
  var chart = null;
  var current = null;
  var lastFocus = null;
  var seriesData = null;

  function readSeries() {
    if (seriesData) return seriesData;
    var el = document.getElementById("series-data");
    try { seriesData = el ? JSON.parse(el.textContent) : {}; } catch (e) { seriesData = {}; }
    return seriesData;
  }

  function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

  function loadLib() {
    if (window.LightweightCharts) return Promise.resolve();
    if (loadLib.p) return loadLib.p;
    loadLib.p = new Promise(function (ok, fail) {
      var s = document.createElement("script");
      s.src = LWC_SRC; s.onload = ok; s.onerror = function () { loadLib.p = null; fail(new Error("chart library failed to load")); };
      document.head.appendChild(s);
    });
    return loadLib.p;
  }

  function getJSON(url, body) {
    var key = url + (body ? JSON.stringify(body) : "");
    if (cache[key]) return cache[key];
    var opts = body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {};
    cache[key] = fetch(url, opts).then(function (r) {
      if (r.status === 429) throw new Error("rate limited by the data source, try again in a minute");
      if (!r.ok) throw new Error("data source returned " + r.status);
      return r.json();
    }).catch(function (e) { delete cache[key]; throw e; });
    return cache[key];
  }

  // Sort ascending by time and drop duplicate timestamps (the chart library requires both).
  function clean(rows) {
    rows.sort(function (a, b) { return a.time - b.time; });
    var out = [];
    rows.forEach(function (r) { if (!out.length || out[out.length - 1].time !== r.time) out.push(r); });
    return out;
  }

  var RANGES = {
    cg: [["1D", 1], ["7D", 7], ["30D", 30], ["90D", 90], ["1Y", 365]],
    gt: [["1D", 1], ["7D", 7], ["30D", 30], ["90D", 90], ["1Y", 365]],
    hl: [["1D", 1], ["7D", 7], ["30D", 30], ["90D", 90], ["1Y", 365]],
    cgx: [["7D", 7], ["30D", 30], ["90D", 90], ["1Y", 365]],
    llama: [["30D", 30], ["90D", 90], ["1Y", 365], ["All", 0]],
    sn: [["All", 0]],
    tv: []
  };

  var LOADERS = {
    cg: function (spec, days) {
      var base = "https://api.coingecko.com/api/v3/coins/" + encodeURIComponent(spec.id);
      return Promise.all([
        getJSON(base + "/ohlc?vs_currency=usd&days=" + days),
        getJSON(base + "/market_chart?vs_currency=usd&days=" + days).catch(function () { return {}; })
      ]).then(function (res) {
        return {
          candles: res[0].map(function (r) { return { time: Math.floor(r[0] / 1000), open: r[1], high: r[2], low: r[3], close: r[4] }; }),
          volume: (res[1].total_volumes || []).map(function (r) { return { time: Math.floor(r[0] / 1000), value: r[1] }; }),
          link: "https://www.coingecko.com/en/coins/" + spec.id,
          note: "Price in USD from CoinGecko, all venues aggregated."
        };
      });
    },
    gt: function (spec, days) {
      var tf = days <= 1 ? ["minute", 15, 96] : days <= 7 ? ["hour", 1, 168] : days <= 30 ? ["hour", 4, 180] : ["day", 1, days];
      var url = "https://api.geckoterminal.com/api/v2/networks/" + spec.net + "/pools/" + spec.pool +
        "/ohlcv/" + tf[0] + "?aggregate=" + tf[1] + "&limit=" + tf[2] + "&currency=usd";
      return getJSON(url).then(function (d) {
        var list = ((d.data || {}).attributes || {}).ohlcv_list || [];
        return {
          candles: list.map(function (r) { return { time: r[0], open: r[1], high: r[2], low: r[3], close: r[4] }; }),
          volume: list.map(function (r) { return { time: r[0], value: r[5] }; }),
          link: spec.link,
          note: "On-chain pool candles in USD from GeckoTerminal. Thin pools move on single trades."
        };
      });
    },
    hl: function (spec, days) {
      var interval = days <= 1 ? "15m" : days <= 7 ? "1h" : days <= 30 ? "4h" : "1d";
      var end = Date.now();
      return getJSON("https://api.hyperliquid.xyz/info", {
        type: "candleSnapshot", req: { coin: spec.coin, interval: interval, startTime: end - days * 864e5, endTime: end }
      }).then(function (rows) {
        return {
          candles: rows.map(function (r) { return { time: Math.floor(r.t / 1000), open: +r.o, high: +r.h, low: +r.l, close: +r.c }; }),
          volume: rows.map(function (r) { return { time: Math.floor(r.t / 1000), value: +r.v * +r.c }; }),
          link: "https://app.hyperliquid.xyz/trade/" + spec.coin,
          note: "Perpetual mark candles from Hyperliquid, an on-chain venue."
        };
      });
    },
    cgx: function (spec, days) {
      return getJSON("https://api.coingecko.com/api/v3/exchanges/" + spec.id + "/volume_chart?days=" + days).then(function (rows) {
        return {
          line: rows.map(function (r) { return { time: Math.floor(r[0] / 1000), value: +r[1] }; }),
          link: "https://www.coingecko.com/en/exchanges/" + spec.id,
          note: "Exchange trading volume in BTC, from CoinGecko."
        };
      });
    },
    llama: function (spec, days) {
      var kind = spec.kind || "dexs";
      return getJSON("https://api.llama.fi/summary/" + kind + "/" + spec.slug + "?excludeTotalDataChartBreakdown=true").then(function (d) {
        var rows = (d.totalDataChart || []).map(function (r) { return { time: r[0], value: r[1] }; });
        if (days) { var cut = Date.now() / 1000 - days * 86400; rows = rows.filter(function (r) { return r.time >= cut; }); }
        return { bars: rows, link: "https://defillama.com/protocol/" + spec.slug, note: "Daily volume in USD from DefiLlama." };
      });
    },
    sn: function (spec) {
      var rows = ((readSeries().subnets || {})[String(spec.netuid)] || []).map(function (r) { return { time: r[0], value: r[1] }; });
      return Promise.resolve({
        line: rows,
        link: "https://taostats.io/subnets/" + spec.netuid,
        note: rows.length < 2
          ? "Not enough history yet. This chart is built from the site's own on-chain snapshots, three a day, so it fills in over the coming days."
          : "Alpha price in TAO from this site's on-chain snapshots (" + rows.length + " so far, three a day)."
      });
    }
  };

  function fmtFor(values) {
    var max = 0;
    values.forEach(function (v) { if (v > max) max = v; });
    var precision = max >= 1 ? 2 : max >= 0.01 ? 4 : max >= 0.0001 ? 6 : 8;
    return { type: "price", precision: precision, minMove: Math.pow(10, -precision) };
  }

  function draw(res) {
    if (chart) { chart.remove(); chart = null; }
    area.innerHTML = "";
    var up = css("--up"), down = css("--down"), line = css("--line"), dim = css("--dim"), accent = css("--accent");
    chart = LightweightCharts.createChart(area, {
      autoSize: true,
      layout: { background: { type: "solid", color: css("--panel") }, textColor: dim, fontSize: 12 },
      grid: { vertLines: { color: line }, horzLines: { color: line } },
      rightPriceScale: { borderColor: line },
      timeScale: { borderColor: line, timeVisible: true, secondsVisible: false },
      crosshair: { mode: 0 }
    });
    var main;
    if (res.candles) {
      var c = clean(res.candles);
      if (c.length < 2) throw new Error("no price history returned for this range");
      main = chart.addCandlestickSeries({
        upColor: up, downColor: down, wickUpColor: up, wickDownColor: down, borderVisible: false,
        priceFormat: fmtFor(c.map(function (r) { return r.close; }))
      });
      main.setData(c);
      var v = clean((res.volume || []).filter(function (r) { return r.value != null; }));
      if (v.length) {
        var vol = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "", color: line });
        vol.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
        vol.setData(v);
        main.priceScale().applyOptions({ scaleMargins: { top: 0.08, bottom: 0.22 } });
      }
    } else if (res.bars) {
      var b = clean(res.bars);
      if (!b.length) throw new Error("no history returned");
      main = chart.addHistogramSeries({ color: accent, priceFormat: { type: "volume" } });
      main.setData(b);
    } else {
      var l = clean(res.line || []);
      if (l.length < 2) { chart.remove(); chart = null; return; }
      main = chart.addAreaSeries({
        lineColor: accent, topColor: accent + "55", bottomColor: accent + "05", lineWidth: 2,
        priceFormat: fmtFor(l.map(function (r) { return r.value; }))
      });
      main.setData(l);
    }
    chart.timeScale().fitContent();
  }

  function tradingView(spec) {
    if (chart) { chart.remove(); chart = null; }
    area.innerHTML = "";
    var box = document.createElement("div");
    box.className = "tradingview-widget-container";
    box.style.height = "100%";
    var inner = document.createElement("div");
    inner.className = "tradingview-widget-container__widget";
    inner.style.height = "100%";
    box.appendChild(inner);
    var s = document.createElement("script");
    s.src = "https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js";
    s.async = true;
    var dark = matchMedia("(prefers-color-scheme: dark)").matches;
    s.textContent = JSON.stringify({
      autosize: true, symbol: spec.sym, interval: "D", timezone: "Etc/UTC", theme: dark ? "dark" : "light",
      style: "1", locale: "en", allow_symbol_change: false, hide_side_toolbar: true, save_image: false
    });
    box.appendChild(s);
    area.appendChild(box);
    noteEl.textContent = "Chart by TradingView.";
    linkEl.href = "https://www.tradingview.com/symbols/" + spec.sym.replace(":", "-") + "/";
    linkEl.hidden = false;
  }

  function load(spec, days) {
    rangesEl.querySelectorAll("button").forEach(function (b) { b.setAttribute("aria-pressed", String(+b.dataset.days === days)); });
    noteEl.textContent = "Loading…";
    area.classList.add("loading");
    var token = current = {};
    Promise.all([loadLib(), LOADERS[spec.t](spec, days)]).then(function (res) {
      if (token !== current) return;
      var r = res[1];
      draw(r);
      noteEl.textContent = r.note || "";
      if (r.link) { linkEl.href = r.link; linkEl.hidden = false; }
    }).catch(function (e) {
      if (token !== current) return;
      if (chart) { chart.remove(); chart = null; }
      area.innerHTML = "";
      noteEl.textContent = "Couldn't load this chart: " + e.message + ".";
      var fallback = spec.link || (spec.t === "cg" && spec.id && "https://www.coingecko.com/en/coins/" + spec.id) ||
        (spec.t === "hl" && "https://app.hyperliquid.xyz/trade/" + spec.coin);
      if (fallback) { linkEl.href = fallback; linkEl.hidden = false; }
    }).then(function () { area.classList.remove("loading"); });
  }

  function open(spec, from) {
    lastFocus = from;
    titleEl.textContent = spec.label || "Chart";
    linkEl.hidden = true;
    rangesEl.innerHTML = "";
    modal.hidden = false;
    document.body.classList.add("modal-open");
    document.getElementById("chart-close").focus();
    if (spec.t === "tv") { tradingView(spec); return; }
    var ranges = RANGES[spec.t] || [];
    var start = ranges.length > 1 ? (ranges[1] || ranges[0])[1] : (ranges[0] || [0, 0])[1];
    ranges.forEach(function (r) {
      var b = document.createElement("button");
      b.type = "button"; b.textContent = r[0]; b.dataset.days = r[1];
      b.addEventListener("click", function () { load(spec, r[1]); });
      rangesEl.appendChild(b);
    });
    load(spec, start);
  }

  function close() {
    modal.hidden = true;
    current = null;
    document.body.classList.remove("modal-open");
    if (chart) { chart.remove(); chart = null; }
    area.innerHTML = "";
    if (lastFocus) lastFocus.focus();
  }

  function specOf(el) {
    try { return JSON.parse(el.getAttribute("data-chart")); } catch (e) { return null; }
  }

  document.addEventListener("click", function (ev) {
    if (ev.target.closest("a, input, th")) return;
    var el = ev.target.closest("[data-chart]");
    if (!el || modal.contains(el)) return;
    var spec = specOf(el);
    if (spec && (LOADERS[spec.t] || spec.t === "tv")) open(spec, el);
  });
  document.addEventListener("keydown", function (ev) {
    if (!modal.hidden && ev.key === "Escape") { close(); return; }
    if ((ev.key === "Enter" || ev.key === " ") && ev.target.matches && ev.target.matches("[data-chart]")) {
      ev.preventDefault();
      var spec = specOf(ev.target);
      if (spec) open(spec, ev.target);
    }
  });
  document.getElementById("chart-close").addEventListener("click", close);
  modal.addEventListener("click", function (ev) { if (ev.target === modal) close(); });
})();

// Headline topic filter.
(function () {
  document.querySelectorAll(".news-filter").forEach(function (bar) {
    var list = bar.nextElementSibling;
    if (!list) return;
    bar.addEventListener("click", function (e) {
      var b = e.target.closest("button");
      if (!b) return;
      bar.querySelectorAll("button").forEach(function (x) { x.classList.toggle("on", x === b); });
      var t = b.dataset.topic;
      list.querySelectorAll(".news-item").forEach(function (li) {
        li.hidden = !!t && li.dataset.topics.split("|").indexOf(t) < 0;
      });
    });
  });
})();
