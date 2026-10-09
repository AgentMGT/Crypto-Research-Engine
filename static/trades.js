// Shared trade data for the journal and the review: turns the two practice accounts (paper-crypto-v1,
// paper-tao-v1) and trades logged by hand (trade-log-v1) into one closed-trade shape. Journal notes
// (tags, lesson, emotion and so on) live in paper-reviews-v1, keyed by each trade's key.
window.Trades = (function () {
  var LOG = "trade-log-v1", NOTES = "paper-reviews-v1";
  function read(k) { try { return JSON.parse(localStorage.getItem(k) || "null"); } catch (e) { return null; } }
  function write(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
  function parseStamp(s) { return typeof s === "number" ? s : Date.parse(String(s).replace(" UTC", "Z").replace(" ", "T")); }

  // A logged trade: { id, asset, dir, venue, opened, closed, entry, exit, qty, fees, stop, target, strategy, note }
  // Prices and fees are in US dollars; closed and exit are empty while the trade is open.
  function logged(e) {
    var cost = e.entry * e.qty, fees = e.fees || 0, open = !(e.exit > 0);
    var pnl = open ? null : (e.dir === "short" ? (e.entry - e.exit) : (e.exit - e.entry)) * e.qty - fees;
    var risk = e.stop > 0 ? Math.abs(e.entry - e.stop) * e.qty : null;
    return {
      key: "l-" + e.id, acct: "logged", unit: "$", asset: e.asset, dir: e.dir, strategy: e.strategy || "Untagged", venue: e.venue,
      opened: e.opened, closed: open ? null : (e.closed || e.opened), pnl: pnl, pnlPct: open ? null : pnl / cost * 100, cost: cost,
      held: open || !e.opened ? null : (e.closed || e.opened) - e.opened, exit: open ? "Open" : "Closed",
      slPct: e.stop > 0 ? Math.abs(e.entry - e.stop) / e.entry * 100 : null, tpPct: e.target > 0 ? Math.abs(e.target - e.entry) / e.entry * 100 : null,
      sizePct: null, r: risk && !open ? pnl / risk : null, note: e.note, offPlan: [], open: open, raw: e
    };
  }

  function load(which) {
    if (which === "bot-crypto" || which === "bot-tao") return loadBot(which);
    if (which === "crypto") {
      var a = read("paper-crypto-v1");
      if (!a) return null;
      var opens = a.trades.filter(function (t) { return t.type === "Open"; });
      var closes = a.trades.filter(function (t) { return t.type === "Close"; }).map(function (t) {
        var risk = t.slPct && t.cost ? t.cost * t.slPct / 100 : null;
        return {
          key: "c-" + t.lid + "-" + t.t, acct: "crypto", unit: "$", asset: t.symbol, dir: t.dir, strategy: t.strategy || "Untagged",
          opened: t.opened || (t.t - (t.held || 0)), closed: t.t, pnl: t.pnl, pnlPct: t.pnlPct, cost: t.cost, held: t.held,
          exit: t.reason, slPct: t.slPct, tpPct: t.tpPct, sizePct: t.sizePct, r: risk ? t.pnl / risk : null, note: t.note, offPlan: []
        };
      });
      return { name: "crypto", unit: "$", start: a.start, trades: closes.sort(function (x, y) { return x.closed - y.closed; }),
        opens: opens.map(function (o) { return { t: o.t, size: o.size, asset: o.symbol }; }) };
    }
    if (which === "logged") {
      var log = read(LOG) || [];
      if (!log.length) return null;
      var all = log.map(logged);
      return { name: "logged", unit: "$", start: null,
        trades: all.filter(function (t) { return !t.open; }).sort(function (x, y) { return x.closed - y.closed; }),
        openTrades: all.filter(function (t) { return t.open; }),
        opens: log.map(function (e) { return { t: e.opened, size: e.entry * e.qty, asset: e.asset }; }) };
    }
    var b = read("paper-tao-v1");
    if (!b) return null;
    var buys = b.trades.filter(function (t) { return t.side === "Buy"; });
    var sells = b.trades.filter(function (t) { return t.side === "Sell"; }).map(function (t) {
      var closed = parseStamp(t.t), opened = t.opened ? parseStamp(t.opened) : null;
      var mine = buys.filter(function (x) { return x.netuid === t.netuid && parseStamp(x.t) <= closed && (!opened || parseStamp(x.t) >= opened - 60000); });
      var setup = (mine[mine.length - 1] || {}).planStatus || "TAO plan";
      var off = t.offPlan.concat.apply(t.offPlan, mine.map(function (x) { return x.offPlan; }));
      return {
        key: "t-" + t.t + "-" + t.netuid, acct: "tao", unit: "τ", asset: "SN" + t.netuid + " " + t.name, dir: "long", strategy: setup,
        opened: opened, closed: closed, pnl: t.pnl, pnlPct: t.pnlPct, cost: t.cost, held: opened ? closed - opened : null,
        exit: t.planStatus, sizePct: (mine[0] || {}).sizePct, r: null, note: t.note, offPlan: off
      };
    });
    return { name: "tao", unit: "τ", start: b.start, trades: sells.sort(function (x, y) { return x.closed - y.closed; }),
      opens: buys.map(function (o) { return { t: parseStamp(o.t), size: o.tao, asset: "SN" + o.netuid }; }) };
  }

  // The paper trading bot's accounts come from the site's bot.json, written on each refresh.
  var bot = null;
  var ready = fetch("bot.json?t=" + Date.now()).then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) { bot = d; return d; }).catch(function () { return null; });

  function loadBot(which) {
    if (!bot) return null;
    if (which === "bot-crypto") {
      var a = bot.crypto;
      var closes = a.trades.filter(function (t) { return t.type === "Close"; }).map(function (t) {
        return {
          key: "bc-" + t.pid + "-" + t.t, acct: which, unit: "$", asset: t.symbol, dir: "long", strategy: t.setup,
          opened: t.opened, closed: t.t, pnl: t.pnl, pnlPct: t.pnl_pct, cost: t.cost, held: t.held, exit: t.reason,
          slPct: t.stop_pct, sizePct: t.size_pct, r: t.stop_pct ? t.pnl / (t.cost * t.stop_pct / 100) : null, note: t.entry_reason, offPlan: [], bot: true
        };
      });
      return { name: which, unit: "$", start: a.start, trades: closes.sort(function (x, y) { return x.closed - y.closed; }),
        opens: a.trades.filter(function (t) { return t.type === "Open"; }).map(function (o) { return { t: o.t, size: o.size, asset: o.symbol }; }) };
    }
    var b = bot.tao;
    var sells = b.trades.filter(function (t) { return t.side === "Sell"; }).map(function (t) {
      return {
        key: "bt-" + t.t + "-" + t.netuid, acct: which, unit: "τ", asset: "SN" + t.netuid + " " + t.name, dir: "long", strategy: t.setup || "TAO plan",
        opened: t.opened, closed: t.t, pnl: t.pnl, pnlPct: t.pnl_pct, cost: t.cost, held: t.opened ? t.t - t.opened : null,
        exit: t.reason, sizePct: null, r: null, note: t.rule, offPlan: [], bot: true
      };
    });
    return { name: which, unit: "τ", start: b.start, trades: sells.sort(function (x, y) { return x.closed - y.closed; }),
      opens: b.trades.filter(function (t) { return t.side === "Buy"; }).map(function (o) { return { t: o.t, size: o.tao, asset: "SN" + o.netuid }; }) };
  }

  return {
    ready: ready, bot: function () { return bot; },
    LOG: LOG, NOTES: NOTES, read: read, write: write, load: load,
    log: function () { return read(LOG) || []; },
    saveLog: function (v) { write(LOG, v); },
    notes: function () { return read(NOTES) || {}; },
    saveNotes: function (v) { write(NOTES, v); }
  };
})();
