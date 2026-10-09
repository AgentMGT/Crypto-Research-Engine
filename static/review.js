// Trade review: reads the practice journals (paper-crypto-v1, paper-tao-v1), runs rule-based
// checks, keeps per-trade tags and lessons, and asks Claude for a review with the user's own key.
(function () {
  var $ = function (id) { return document.getElementById(id); };
  if (!$("rv-main")) return;
  var REVIEWS = "paper-reviews-v1", AI = "paper-ai-reviews-v1", KEYSTORE = "anthropic-key";
  var SDK_URL = "https://cdn.jsdelivr.net/npm/@anthropic-ai/sdk@0.128.0/+esm";
  var MODEL = "claude-opus-5-5";
  var TAGS = ["Followed plan", "Good entry", "Good exit", "FOMO", "Early exit", "Late exit", "Oversized", "No stop", "Revenge", "Moved stop"];
  var GOOD_TAGS = ["Followed plan", "Good entry", "Good exit"];
  var acctName = "crypto";
  try { acctName = localStorage.getItem("review-acct") || "crypto"; } catch (e) {}
  var data = null;

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
  function money(v, u) {
    if (v == null || isNaN(v)) return "–";
    return u === "$" ? (v < 0 ? "-$" : "$") + n(Math.abs(v)) : n(v, 3) + " τ";
  }
  function sgnMoney(v, u) {
    if (v == null || isNaN(v)) return '<span class="flat">–</span>';
    return '<span class="' + (v > 0 ? "up" : v < 0 ? "down" : "flat") + '">' + (v > 0 ? "+" : "") + money(v, u) + "</span>";
  }
  function read(k) { try { return JSON.parse(localStorage.getItem(k) || "null"); } catch (e) { return null; } }
  function write(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
  function hrs(ms) { return ms == null ? null : ms / 3600000; }
  function fmtHrs(h) { return h == null ? "–" : h < 48 ? n(h, 1) + "h" : n(h / 24, 1) + "d"; }
  function parseStamp(s) { return typeof s === "number" ? s : Date.parse(String(s).replace(" UTC", "Z").replace(" ", "T")); }
  function day(ms) { return new Date(ms).toISOString().slice(0, 16).replace("T", " "); }

  var load = Trades.load;

  // ------------------------------------------------------------ stats and findings
  function stats(tr) {
    var w = tr.filter(function (t) { return t.pnl > 0; }), l = tr.filter(function (t) { return t.pnl <= 0; });
    var sum = function (a, f) { return a.reduce(function (x, t) { return x + f(t); }, 0); };
    var gw = sum(w, function (t) { return t.pnl; }), gl = -sum(l, function (t) { return t.pnl; });
    var streak = 0, worst = 0;
    tr.forEach(function (t) { streak = t.pnl <= 0 ? streak + 1 : 0; worst = Math.max(worst, streak); });
    var avgH = function (a) { var h = a.filter(function (t) { return t.held != null; }); return h.length ? hrs(sum(h, function (t) { return t.held; }) / h.length) : null; };
    var rs = tr.filter(function (t) { return t.r != null; });
    return {
      n: tr.length, wins: w.length, winRate: tr.length ? w.length / tr.length * 100 : null, total: gw - gl,
      avgWin: w.length ? gw / w.length : null, avgLoss: l.length ? -gl / l.length : null,
      expectancy: tr.length ? (gw - gl) / tr.length : null, pf: gl ? gw / gl : (gw ? Infinity : null),
      best: tr.length ? Math.max.apply(null, tr.map(function (t) { return t.pnl; })) : null,
      worstTrade: tr.length ? Math.min.apply(null, tr.map(function (t) { return t.pnl; })) : null,
      holdWin: avgH(w), holdLoss: avgH(l), maxLossStreak: worst,
      avgR: rs.length ? sum(rs, function (t) { return t.r; }) / rs.length : null, rCount: rs.length
    };
  }
  function group(tr, f) {
    var g = {};
    tr.forEach(function (t) { (g[f(t)] = g[f(t)] || []).push(t); });
    return Object.keys(g).map(function (k) { return { name: k, s: stats(g[k]) }; }).sort(function (a, b) { return b.s.total - a.s.total; });
  }

  function findings(d, s, reviews) {
    var f = [], tr = d.trades, u = d.unit;
    function add(k, t) { f.push({ kind: k, text: t }); }
    if (s.n < 20) add("warn", "Only " + s.n + " closed trade" + (s.n === 1 ? "" : "s") + ". Results this small are mostly noise; judge your process, not the P&L, until you have 20 to 30.");
    if (s.pf != null) {
      if (s.pf < 1) add("bad", "Losing overall: profit factor " + n(s.pf, 2) + " (you lose " + money(-s.expectancy, u) + " per trade on average).");
      else if (s.pf >= 1.5) add("good", "Profit factor " + (isFinite(s.pf) ? n(s.pf, 2) : "∞") + ": winners pay for losers with room to spare.");
      else add("warn", "Profit factor " + n(s.pf, 2) + ": positive but thin. Costs and one bad trade can erase it.");
    }
    if (s.avgWin != null && s.avgLoss != null && Math.abs(s.avgLoss) > s.avgWin && s.winRate < 60)
      add("bad", "Average loss (" + money(s.avgLoss, u) + ") is bigger than average win (" + money(s.avgWin, u) + ") with a " + n(s.winRate, 0) + "% win rate. That combination loses over time.");
    if (s.holdWin != null && s.holdLoss != null && s.holdLoss > s.holdWin * 1.5)
      add("bad", "You hold losers " + n(s.holdLoss / s.holdWin, 1) + "× longer than winners (" + fmtHrs(s.holdLoss) + " vs " + fmtHrs(s.holdWin) + "). Classic sign of hoping a loser comes back and banking winners too early.");
    if (s.maxLossStreak >= 4) add("warn", "Longest losing streak: " + s.maxLossStreak + " trades. Decide in advance when you pause after a streak.");
    if (d.name === "crypto") {
      var noStop = tr.filter(function (t) { return !t.slPct; }).length;
      if (noStop / s.n > 0.3) add("bad", noStop + " of " + s.n + " trades had no stop-loss. Without a stop you can't size by risk or measure R.");
      else if (s.n) add("good", "Stops on " + (s.n - noStop) + " of " + s.n + " trades.");
      var blown = tr.filter(function (t) { return t.slPct && t.pnlPct < -t.slPct * 1.5; });
      if (blown.length) add("warn", blown.length + " loss" + (blown.length > 1 ? "es" : "") + " ran well past the planned stop (more than 1.5× the stop distance), from gaps or slippage on thin coins.");
      var early = tr.filter(function (t) { return t.exit === "Manual" && t.pnl > 0 && t.tpPct && t.pnlPct < t.tpPct / 2; });
      if (early.length >= 2) add("warn", early.length + " winners were closed by hand at less than half their target. If the setup was still valid, that's leaving money on the table.");
      if (s.avgR != null) add(s.avgR > 0 ? "good" : "bad", "Average result " + n(s.avgR, 2) + "R across " + s.rCount + " trades with a stop (R = the amount you planned to risk).");
    }
    if (d.name === "tao") {
      var off = tr.filter(function (t) { return t.offPlan.length; });
      var on = tr.filter(function (t) { return !t.offPlan.length; });
      add(off.length / s.n > 0.25 ? "bad" : "good", (s.n - off.length) + " of " + s.n + " closed trades stayed on plan from entry to exit.");
      if (off.length && on.length) {
        var so = stats(off), sn = stats(on);
        add(so.total < sn.total ? "good" : "warn", "On-plan trades made " + money(sn.total, u) + " (" + n(sn.winRate, 0) + "% won); off-plan trades made " + money(so.total, u) + " (" + n(so.winRate, 0) + "% won)." + (so.total < sn.total ? " The plan is earning its keep." : " Off-plan trades did better so far; check whether that's skill or luck before changing the rules."));
      }
    }
    var big = tr.filter(function (t) { return t.sizePct > 25; });
    if (big.length) add("warn", big.length + " trade" + (big.length > 1 ? "s were" : " was") + " more than 25% of the account. One bad outcome there sets you back a long way.");
    // Revenge trading: a bigger position opened within 2 hours of a losing close.
    var rev = 0;
    tr.forEach(function (t) {
      if (t.pnl >= 0) return;
      var nx = d.opens.filter(function (o) { return o.t > t.closed && o.t - t.closed < 7200000; })[0];
      if (nx && t.cost && nx.size > t.cost * 1.2) rev++;
    });
    if (rev) add("bad", rev + " time" + (rev > 1 ? "s" : "") + " you opened a bigger position within 2 hours of a loss. That's often revenge trading.");
    var g = group(tr, function (t) { return t.strategy; }).filter(function (x) { return x.s.n >= 3; });
    if (g.length >= 2) {
      add("good", "Best strategy so far: " + g[0].name + " (" + sgnText(g[0].s.total, u) + " over " + g[0].s.n + " trades).");
      var last = g[g.length - 1];
      if (last.s.total < 0) add("warn", "Weakest strategy: " + last.name + " (" + sgnText(last.s.total, u) + " over " + last.s.n + " trades). Consider pausing it until you know why.");
    }
    var tagCount = {};
    tr.forEach(function (t) { ((reviews[t.key] || {}).tags || []).forEach(function (x) { tagCount[x] = (tagCount[x] || 0) + 1; }); });
    var bad = Object.keys(tagCount).filter(function (x) { return GOOD_TAGS.indexOf(x) < 0; }).sort(function (a, b) { return tagCount[b] - tagCount[a]; });
    if (bad.length) add("warn", "Your most common self-tagged mistake: " + bad[0] + " (" + tagCount[bad[0]] + " trade" + (tagCount[bad[0]] > 1 ? "s" : "") + ").");
    // Mood before entry, from the journal: compare results when calm vs. not.
    var moods = group(tr.filter(function (t) { return (reviews[t.key] || {}).emotion; }), function (t) { return reviews[t.key].emotion; });
    if (moods.length >= 2) {
      var worstMood = moods[moods.length - 1], bestMood = moods[0], low = function (m) { return m === "FOMO" ? m : m.toLowerCase(); };
      if (worstMood.s.n >= 2 && worstMood.s.total < 0) add("warn", "Trades entered feeling " + low(worstMood.name) + " made " + sgnText(worstMood.s.total, u) + " over " + worstMood.s.n +
        " trades, against " + sgnText(bestMood.s.total, u) + " when " + low(bestMood.name) + ". Check how you feel before you click.");
    }
    var reviewed = tr.filter(function (t) { var r = reviews[t.key]; return r && ((r.tags || []).length || r.lesson || r.emotion || r.happened); }).length;
    if (s.n && reviewed < s.n) add("warn", reviewed + " of " + s.n + " trades have your own review. Tag the rest below or in the Journal; the AI review is much better with them.");
    return f;
  }
  function sgnText(v, u) { return (v > 0 ? "+" : "") + money(v, u); }

  // ------------------------------------------------------------ render
  function card(k, v, sub) {
    return '<div class="card"><div class="k">' + k + '</div><div class="v">' + v + "</div>" + (sub ? '<div class="n">' + sub + "</div>" : "") + "</div>";
  }
  function groupTable(rows, u, label) {
    return '<div class="tablewrap"><table><thead><tr><th class="l">' + label + "</th><th>Trades</th><th>Win rate</th><th>Total</th><th>Expectancy</th><th>Profit factor</th></tr></thead><tbody>" +
      rows.map(function (g) {
        return '<tr><td class="l">' + esc(g.name) + "</td><td>" + g.s.n + "</td><td>" + n(g.s.winRate, 0) + "%</td><td>" + sgnMoney(g.s.total, u) + "</td><td>" + sgnMoney(g.s.expectancy, u) +
          "</td><td>" + (g.s.pf == null ? "–" : isFinite(g.s.pf) ? n(g.s.pf, 2) : "∞") + "</td></tr>";
      }).join("") + "</tbody></table></div>";
  }

  function render() {
    data = load(acctName);
    var tr = data ? data.trades : [];
    $("rv-empty").hidden = !!tr.length;
    $("rv-main").hidden = !tr.length;
    $("rv-range").textContent = tr.length ? tr.length + " closed trades, " + day(tr[0].closed).slice(0, 10) + " to " + day(tr[tr.length - 1].closed).slice(0, 10) : "";
    if (!tr.length) return;
    var u = data.unit, s = stats(tr), reviews = read(REVIEWS) || {};
    $("rv-cards").innerHTML =
      card("Net result", sgnMoney(s.total, u), s.n + " closed trades") +
      card("Win rate", n(s.winRate, 0) + "%", s.wins + " won · " + (s.n - s.wins) + " lost") +
      card("Avg win / avg loss", money(s.avgWin, u) + " / " + money(s.avgLoss, u), s.avgWin && s.avgLoss ? "ratio " + n(s.avgWin / Math.abs(s.avgLoss), 2) : "") +
      card("Expectancy", sgnMoney(s.expectancy, u), "per trade" + (s.avgR != null ? " · " + n(s.avgR, 2) + "R" : "")) +
      card("Profit factor", s.pf == null ? "–" : isFinite(s.pf) ? n(s.pf, 2) : "∞", "gross wins ÷ gross losses") +
      card("Hold time", fmtHrs(s.holdWin) + " / " + fmtHrs(s.holdLoss), "winners / losers");
    $("rv-findings").innerHTML = findings(data, s, reviews).map(function (x) { return '<li class="' + x.kind + '">' + esc(x.text) + "</li>"; }).join("");
    $("rv-by-strategy").innerHTML = groupTable(group(tr, function (t) { return t.strategy; }), u, "Strategy");
    $("rv-by-asset").innerHTML = groupTable(group(tr, function (t) { return t.asset; }), u, "Asset");

    $("rv-trades").innerHTML = '<div class="tablewrap"><table class="rv-table"><thead><tr><th class="l">Closed</th><th class="l">Asset</th><th class="l">Strategy</th><th>P&amp;L</th><th>R</th><th>Held</th><th class="l">Exit</th><th class="l">Flags</th><th class="l">Your review</th></tr></thead><tbody>' +
      tr.slice().reverse().map(function (t) {
        var rv = reviews[t.key] || {}, flags = autoFlags(t);
        return '<tr data-key="' + esc(t.key) + '"><td class="l">' + day(t.closed) + '</td><td class="l"><b>' + esc(t.asset) + "</b> " + (t.dir === "short" ? '<span class="tag st-exit-avoid">Short</span>' : "") +
          '</td><td class="l">' + esc(t.strategy) + "</td><td>" + sgnMoney(t.pnl, t.unit) + "<br>" + (t.pnlPct != null ? n(t.pnlPct, 1) + "%" : "") + "</td><td>" + (t.r != null ? n(t.r, 2) : "–") +
          "</td><td>" + fmtHrs(hrs(t.held)) + '</td><td class="l">' + esc(t.exit || "") + '</td><td class="l">' + flags.map(function (x) { return '<span class="tag st-trim">' + esc(x) + "</span>"; }).join(" ") +
          '</td><td class="l rv-cell"><div class="rv-tags">' + TAGS.map(function (x) {
            var on = (rv.tags || []).indexOf(x) >= 0;
            return '<button type="button" class="' + (on ? "on " : "") + (GOOD_TAGS.indexOf(x) >= 0 ? "good" : "bad") + '" data-tag="' + esc(x) + '" aria-pressed="' + on + '">' + esc(x) + "</button>";
          }).join("") + '</div><input type="text" class="rv-lesson" maxlength="240" placeholder="Lesson from this trade" value="' + esc(rv.lesson || "") + '">' +
          (t.note ? '<div class="why">Your note at entry: ' + esc(t.note) + "</div>" : "") + "</td></tr>";
      }).join("") + "</tbody></table></div>";
    renderAI();
  }

  function autoFlags(t) {
    var f = [];
    if (t.acct !== "tao" && !t.slPct) f.push("No stop");
    if (t.sizePct > 25) f.push("Oversized");
    if (t.offPlan && t.offPlan.length) f.push("Off plan");
    if (t.slPct && t.pnlPct < -t.slPct * 1.5) f.push("Past stop");
    return f;
  }

  // ------------------------------------------------------------ per-trade reviews
  $("rv-trades").addEventListener("click", function (e) {
    var b = e.target.closest("button[data-tag]");
    if (!b) return;
    var key = b.closest("tr").dataset.key, all = read(REVIEWS) || {}, rv = all[key] || (all[key] = { tags: [], lesson: "" });
    var i = rv.tags.indexOf(b.dataset.tag);
    if (i >= 0) rv.tags.splice(i, 1); else rv.tags.push(b.dataset.tag);
    write(REVIEWS, all);
    b.classList.toggle("on", i < 0);
    b.setAttribute("aria-pressed", String(i < 0));
    var s = stats(data.trades);
    $("rv-findings").innerHTML = findings(data, s, all).map(function (x) { return '<li class="' + x.kind + '">' + esc(x.text) + "</li>"; }).join("");
  });
  $("rv-trades").addEventListener("change", function (e) {
    if (!e.target.classList.contains("rv-lesson")) return;
    var key = e.target.closest("tr").dataset.key, all = read(REVIEWS) || {}, rv = all[key] || (all[key] = { tags: [], lesson: "" });
    rv.lesson = e.target.value.trim();
    write(REVIEWS, all);
  });

  // ------------------------------------------------------------ AI review
  var SCHEMA = {
    type: "object", additionalProperties: false,
    required: ["headline", "grade", "summary", "strengths", "mistakes", "rules_to_add", "trades_to_revisit", "next_steps"],
    properties: {
      headline: { type: "string" },
      grade: { type: "string", enum: ["A", "B", "C", "D", "F", "Too early"] },
      summary: { type: "string" },
      strengths: { type: "array", items: { type: "string" } },
      mistakes: { type: "array", items: { type: "object", additionalProperties: false, required: ["pattern", "evidence", "fix"],
        properties: { pattern: { type: "string" }, evidence: { type: "string" }, fix: { type: "string" } } } },
      rules_to_add: { type: "array", items: { type: "string" } },
      trades_to_revisit: { type: "array", items: { type: "object", additionalProperties: false, required: ["trade", "why"],
        properties: { trade: { type: "string" }, why: { type: "string" } } } },
      next_steps: { type: "array", items: { type: "string" } }
    }
  };
  var SYSTEM = "You are a trading coach reviewing a trader's practice (paper) trades. You get their summary statistics, " +
    "the rule-based findings, results by strategy and asset, and each closed trade with their own tags and lessons. " +
    "Judge process over outcome: a well-executed losing trade is fine, a lucky winner that broke the rules is not. " +
    "Ground every point in the data given; quote trade dates and assets as evidence. With fewer than 20 trades, say the results are mostly noise " +
    "and focus on process; use the grade \"Too early\" if there are fewer than 10. Be direct and specific; no generic trading advice. " +
    "This is education on practice trades: no price predictions and no recommendations to buy or sell any real asset. " +
    "Return at most 4 strengths, 5 mistakes, 4 rules to add, 5 trades to revisit and 4 next steps.";

  function payload() {
    var tr = data.trades, reviews = read(REVIEWS) || {}, s = stats(tr);
    return {
      account: { crypto: "Crypto paper account (USD), long and short, any coin", tao: "Bittensor subnet paper account (TAO), trading a rules-based plan",
        logged: "Trades the trader logged by hand from outside the simulator (USD)" }[data.name],
      units: data.unit === "$" ? "USD" : "TAO", starting_balance: data.start,
      stats: s, journal_fields: "my_emotion is how I felt before entering; my_execution is my own 1-5 rating of how well I followed my process", findings: findings(data, s, reviews).map(function (x) { return x.kind + ": " + x.text; }),
      by_strategy: group(tr, function (t) { return t.strategy; }), by_asset: group(tr, function (t) { return t.asset; }),
      trades: tr.slice(-150).map(function (t) {
        var rv = reviews[t.key] || {};
        return { closed: day(t.closed), asset: t.asset, dir: t.dir, strategy: t.strategy, pnl: +n(t.pnl, 4).replace(/,/g, ""), pnl_pct: t.pnlPct != null ? +t.pnlPct.toFixed(2) : null,
          r: t.r != null ? +t.r.toFixed(2) : null, held_hours: t.held != null ? +hrs(t.held).toFixed(1) : null, exit: t.exit, stop_pct: t.slPct || null,
          size_pct_of_account: t.sizePct != null ? +t.sizePct.toFixed(1) : null, off_plan: t.offPlan.length ? t.offPlan : undefined,
          entry_note: t.note || undefined, my_tags: (rv.tags || []).length ? rv.tags : undefined, my_lesson: rv.lesson || undefined,
          my_emotion: rv.emotion || undefined, my_execution: rv.execution || undefined, what_happened: rv.happened || undefined };
      })
    };
  }

  function renderAI() {
    var all = read(AI) || {}, list = all[acctName] || [], last = list[0];
    var key = null; try { key = localStorage.getItem(KEYSTORE); } catch (e) {}
    $("rv-ai-run").textContent = key ? "Run AI review" : "Run AI review (needs a key)";
    if (!last) { $("rv-ai-out").innerHTML = '<p class="sub">No AI review yet for this account.</p>'; return; }
    var r = last.result;
    $("rv-ai-out").innerHTML =
      '<div class="rv-ai-head"><span class="pill">' + esc(r.grade) + "</span><b>" + esc(r.headline) + '</b><span class="faint">' + esc(last.t) + " · " + esc(last.model) + " · " + last.n + " trades</span></div>" +
      "<p>" + esc(r.summary) + "</p>" +
      (r.strengths.length ? "<h4>Keep doing</h4><ul>" + r.strengths.map(function (x) { return "<li>" + esc(x) + "</li>"; }).join("") + "</ul>" : "") +
      (r.mistakes.length ? "<h4>Fix</h4><ul>" + r.mistakes.map(function (x) { return "<li><b>" + esc(x.pattern) + ".</b> " + esc(x.evidence) + ' <span class="rv-fix">Fix: ' + esc(x.fix) + "</span></li>"; }).join("") + "</ul>" : "") +
      (r.rules_to_add.length ? "<h4>Rules to add to your plan</h4><ul>" + r.rules_to_add.map(function (x) { return "<li>" + esc(x) + "</li>"; }).join("") + "</ul>" : "") +
      (r.trades_to_revisit.length ? "<h4>Trades to revisit</h4><ul>" + r.trades_to_revisit.map(function (x) { return "<li><b>" + esc(x.trade) + ":</b> " + esc(x.why) + "</li>"; }).join("") + "</ul>" : "") +
      (r.next_steps.length ? "<h4>Next steps</h4><ul>" + r.next_steps.map(function (x) { return "<li>" + esc(x) + "</li>"; }).join("") + "</ul>" : "") +
      (list.length > 1 ? '<p class="faint">' + (list.length - 1) + " earlier review" + (list.length > 2 ? "s" : "") + " saved in this browser.</p>" : "");
  }

  function status(t, bad) { $("rv-ai-status").innerHTML = t ? '<span class="' + (bad ? "down" : "") + '">' + t + "</span>" : ""; }

  $("rv-ai-run").addEventListener("click", function () {
    var key = null; try { key = localStorage.getItem(KEYSTORE); } catch (e) {}
    if (!key) { $("rv-key").hidden = false; $("rv-key-input").focus(); status("Add your Anthropic API key first, or use Copy for Claude chat."); return; }
    var btn = this, body = payload();
    btn.disabled = true;
    status("Reviewing " + data.trades.length + " trades… this can take a minute.");
    import(SDK_URL).then(function (mod) {
      var Anthropic = mod.default || mod.Anthropic;
      var client = new Anthropic({ apiKey: key, dangerouslyAllowBrowser: true });
      var req = {
        model: MODEL, max_tokens: 32000, system: SYSTEM,
        output_config: { effort: "high", format: { type: "json_schema", schema: SCHEMA } },
        messages: [{ role: "user", content: "Review these practice trades.\n\n" + JSON.stringify(body) }]
      };
      function run(withFallback) {
        var r = withFallback ? Object.assign({ betas: ["server-side-fallback-2026-07-01"], fallbacks: "default" }, req) : req;
        return (withFallback ? client.beta.messages : client.messages).stream(r).finalMessage();
      }
      return run(true).catch(function (e) {
        // Older SDK builds or accounts without the fallback beta: retry plain.
        if (e instanceof Anthropic.BadRequestError && /fallback/i.test(e.message)) return run(false);
        throw e;
      }).then(function (msg) {
        if (msg.stop_reason === "refusal") throw new Error("Claude declined this request" + (msg.stop_details && msg.stop_details.explanation ? ": " + msg.stop_details.explanation : ""));
        if (msg.stop_reason === "max_tokens") throw new Error("the review ran out of room; try again");
        var text = msg.content.filter(function (b) { return b.type === "text"; }).map(function (b) { return b.text; }).join("");
        var result = JSON.parse(text);
        var all = read(AI) || {};
        all[acctName] = [{ t: day(Date.now()) + " UTC", model: msg.model || MODEL, n: data.trades.length, result: result }].concat(all[acctName] || []).slice(0, 10);
        write(AI, all);
        status("");
        renderAI();
      }).catch(function (e) {
        var m = e instanceof Anthropic.AuthenticationError ? "the API key was rejected. Check it under API key."
          : e instanceof Anthropic.RateLimitError ? "rate limited by the API; wait a minute and try again."
          : e instanceof Anthropic.APIConnectionError ? "couldn't reach Anthropic's API from this browser."
          : e.message;
        status("AI review failed: " + esc(m), true);
      });
    }).catch(function (e) {
      status("Couldn't load the Anthropic SDK (" + esc(e.message) + "). Use Copy for Claude chat instead.", true);
    }).then(function () { btn.disabled = false; });
  });

  $("rv-ai-copy").addEventListener("click", function () {
    var text = SYSTEM + "\n\nReply in plain prose with these sections: headline and grade, summary, keep doing, fix (pattern, evidence, fix), rules to add, trades to revisit, next steps.\n\nMy practice trades:\n" + JSON.stringify(payload(), null, 1);
    var done = function () { status("Copied. Paste it into a new Claude chat."); };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, fallback);
    else fallback();
    function fallback() {
      var ta = document.createElement("textarea");
      ta.value = text; document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); done(); } catch (e) { status("Couldn't copy automatically.", true); }
      ta.remove();
    }
  });
  $("rv-ai-key").addEventListener("click", function () { $("rv-key").hidden = !$("rv-key").hidden; });
  $("rv-key-save").addEventListener("click", function () {
    var v = $("rv-key-input").value.trim();
    if (!/^sk-ant-/.test(v)) { status("That doesn't look like an Anthropic API key (they start with sk-ant-).", true); return; }
    try { localStorage.setItem(KEYSTORE, v); } catch (e) {}
    $("rv-key-input").value = ""; $("rv-key").hidden = true;
    status("Key saved in this browser."); renderAI();
  });
  $("rv-key-clear").addEventListener("click", function () {
    try { localStorage.removeItem(KEYSTORE); } catch (e) {}
    status("Key removed from this browser."); renderAI();
  });

  document.querySelector(".rv-acct").addEventListener("click", function (e) {
    var b = e.target.closest("button[data-acct]");
    if (!b) return;
    acctName = b.dataset.acct;
    try { localStorage.setItem("review-acct", acctName); } catch (e) {}
    this.querySelectorAll("button").forEach(function (x) { x.classList.toggle("on", x === b); });
    status(""); render();
  });
  document.querySelectorAll(".rv-acct button").forEach(function (x) { x.classList.toggle("on", x.dataset.acct === acctName); });
  document.addEventListener("review:show", render);
  render();
})();
