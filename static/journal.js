// Trade journal: lists every closed practice trade plus hand-logged trades (static/trades.js), and keeps
// per-trade notes (emotion, execution rating, tags, what happened, lesson) that the Trade review reads.
(function () {
  var $ = function (id) { return document.getElementById(id); };
  if (!$("jr-list")) return;
  var TAGS = ["Followed plan", "Good entry", "Good exit", "FOMO", "Early exit", "Late exit", "Oversized", "No stop", "Revenge", "Moved stop"];
  var GOOD_TAGS = ["Followed plan", "Good entry", "Good exit"];
  var MOODS = ["Calm", "Confident", "Excited", "Anxious", "FOMO", "Bored", "Frustrated"];
  var ACCT = { crypto: "Crypto practice", tao: "Bittensor practice", logged: "Logged" };
  var rows = [], editing = null, openKeys = {};

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
  function money(v, u) { return v == null || isNaN(v) ? "–" : u === "$" ? (v < 0 ? "-$" : "$") + n(Math.abs(v)) : n(v, 3) + " τ"; }
  function sgnMoney(v, u) {
    if (v == null || isNaN(v)) return '<span class="flat">–</span>';
    return '<span class="' + (v > 0 ? "up" : v < 0 ? "down" : "flat") + '">' + (v > 0 ? "+" : "") + money(v, u) + "</span>";
  }
  function stamp(ms) { return ms ? new Date(ms).toISOString().slice(0, 16).replace("T", " ") : "–"; }
  function fmtHrs(ms) { if (ms == null) return "–"; var h = ms / 3600000; return h < 48 ? n(h, 1) + "h" : n(h / 24, 1) + "d"; }
  function localInput(ms) { if (!ms) return ""; var d = new Date(ms); return new Date(ms - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16); }
  function fromInput(v) { var t = v ? new Date(v).getTime() : NaN; return isNaN(t) ? null : t; }
  function num(id) { var v = parseFloat($(id).value); return isNaN(v) ? null : v; }
  function hasNotes(r) { return !!(r && ((r.tags || []).length || r.lesson || r.emotion || r.happened || r.execution)); }

  // ------------------------------------------------------------ data
  function collect() {
    var out = [];
    ["crypto", "tao", "logged"].forEach(function (k) {
      var d = Trades.load(k);
      if (!d) return;
      out = out.concat(d.trades, d.openTrades || []);
    });
    return out.sort(function (a, b) { return (b.open ? 1 : 0) - (a.open ? 1 : 0) || (b.closed || b.opened) - (a.closed || a.opened); });
  }

  function filtered() {
    var acct = $("jr-acct").value, res = $("jr-result").value, st = $("jr-strat").value, q = $("jr-q").value.trim().toLowerCase(), notes = Trades.notes();
    return rows.filter(function (t) {
      var nt = notes[t.key] || {};
      if (acct !== "all" && t.acct !== acct) return false;
      if (st !== "all" && t.strategy !== st) return false;
      if (res === "win" && !(t.pnl > 0)) return false;
      if (res === "loss" && !(t.pnl <= 0 && !t.open)) return false;
      if (res === "open" && !t.open) return false;
      if (res === "todo" && (t.open || hasNotes(nt))) return false;
      if (q) {
        var hay = [t.asset, t.strategy, t.note, t.venue, nt.lesson, nt.happened, nt.emotion, (nt.tags || []).join(" ")].join(" ").toLowerCase();
        if (hay.indexOf(q) < 0) return false;
      }
      return true;
    });
  }

  // ------------------------------------------------------------ render
  function card(k, v, sub) { return '<div class="card"><div class="k">' + k + '</div><div class="v">' + v + "</div>" + (sub ? '<div class="n">' + sub + "</div>" : "") + "</div>"; }

  function render() {
    rows = collect();
    var strats = {};
    rows.forEach(function (t) { strats[t.strategy] = 1; });
    var sel = $("jr-strat"), keep = sel.value;
    sel.innerHTML = '<option value="all">All strategies</option>' + Object.keys(strats).sort().map(function (s) { return '<option value="' + esc(s) + '">' + esc(s) + "</option>"; }).join("");
    sel.value = strats[keep] ? keep : "all";
    $("jr-strats").innerHTML = Object.keys(strats).sort().map(function (s) { return '<option value="' + esc(s) + '">'; }).join("");
    renderList();
  }

  function renderList() {
    var list = filtered(), notes = Trades.notes();
    var closed = list.filter(function (t) { return !t.open; });
    var usd = closed.filter(function (t) { return t.unit === "$"; }), tao = closed.filter(function (t) { return t.unit === "τ"; });
    var sum = function (a) { return a.reduce(function (x, t) { return x + t.pnl; }, 0); };
    var done = closed.filter(function (t) { return hasNotes(notes[t.key]); }).length;
    var lessons = closed.filter(function (t) { return (notes[t.key] || {}).lesson; }).length;
    $("jr-cards").innerHTML = rows.length ?
      card("Trades shown", list.length, closed.length + " closed" + (list.length - closed.length ? " · " + (list.length - closed.length) + " open" : "")) +
      card("Net result ($)", sgnMoney(usd.length ? sum(usd) : null, "$"), usd.length + " dollar trades") +
      (tao.length ? card("Net result (τ)", sgnMoney(sum(tao), "τ"), tao.length + " Bittensor trades") : "") +
      card("Journaled", closed.length ? n(done / closed.length * 100, 0) + "%" : "–", done + " of " + closed.length + " closed trades") +
      card("Lessons written", lessons, lessons ? "" : "add one to every trade") : "";

    if (!rows.length) {
      $("jr-list").innerHTML = '<div class="pp-panel"><p class="sub" style="margin:0">Nothing in the journal yet. Close a practice trade in the Crypto or Bittensor tab, or use <b>Log a trade</b> for one you made elsewhere.</p></div>';
      return;
    }
    if (!list.length) { $("jr-list").innerHTML = '<p class="sub">No trades match these filters.</p>'; return; }
    $("jr-list").innerHTML = list.slice(0, 300).map(function (t) { return entry(t, notes[t.key] || {}); }).join("") +
      (list.length > 300 ? '<p class="sub">Showing the latest 300. Narrow the filters or export CSV for the rest.</p>' : "");
  }

  function chip(label, on, cls, attr) {
    return '<button type="button" class="' + (on ? "on " : "") + cls + '" ' + attr + '="' + esc(label) + '" aria-pressed="' + on + '">' + esc(label) + "</button>";
  }

  function entry(t, nt) {
    var badge = t.open ? '<span class="tag">Open</span>' : hasNotes(nt) ? '<span class="tag jr-done">Journaled</span>' : '<span class="tag st-trim">Add notes</span>';
    var facts = [
      ["Account", ACCT[t.acct] + (t.venue ? " · " + esc(t.venue) : "")],
      ["Opened", stamp(t.opened)], ["Closed", t.open ? "still open" : stamp(t.closed)],
      ["Held", fmtHrs(t.held)], ["Size", money(t.cost, t.unit) + (t.sizePct != null ? " (" + n(t.sizePct, 1) + "% of account)" : "")],
      ["Stop", t.slPct ? n(t.slPct, 1) + "% away" : t.acct === "tao" ? "plan stop" : "none"],
      ["Exit", esc(t.exit || "–")]
    ];
    if (t.acct === "logged") facts.splice(4, 0, ["Entry / exit", "$" + n(t.raw.entry, t.raw.entry < 1 ? 6 : 2) + " → " + (t.open ? "–" : "$" + n(t.raw.exit, t.raw.exit < 1 ? 6 : 2)) + " × " + n(t.raw.qty, 4)]);
    if (t.offPlan && t.offPlan.length) facts.push(["Off plan", esc(t.offPlan.join("; "))]);
    return '<details class="jr-entry" data-key="' + esc(t.key) + '"' + (openKeys[t.key] ? " open" : "") + "><summary>" +
      '<span class="jr-when">' + stamp(t.open ? t.opened : t.closed).slice(0, 10) + "</span>" +
      '<span class="jr-asset"><b>' + esc(t.asset) + "</b>" + (t.dir === "short" ? ' <span class="tag st-exit-avoid">Short</span>' : "") + "</span>" +
      '<span class="jr-strat">' + esc(t.strategy) + "</span>" +
      '<span class="jr-pnl">' + (t.open ? '<span class="flat">open</span>' : sgnMoney(t.pnl, t.unit) + (t.pnlPct != null ? ' <span class="faint">' + n(t.pnlPct, 1) + "%</span>" : "")) + (t.r != null ? ' <span class="faint">' + n(t.r, 2) + "R</span>" : "") + "</span>" +
      '<span class="jr-badge">' + badge + "</span></summary>" +
      '<div class="jr-body"><div class="jr-facts"><dl>' + facts.map(function (f) { return "<dt>" + f[0] + "</dt><dd>" + f[1] + "</dd>"; }).join("") + "</dl>" +
      (t.note ? '<p class="jr-why"><b>Why I took it:</b> ' + esc(t.note) + "</p>" : "") +
      (t.acct === "logged" ? '<div class="jr-actions"><button type="button" class="rs-btn" data-edit="' + esc(t.raw.id) + '">' + (t.open ? "Close or edit" : "Edit") + '</button><button type="button" class="rs-btn" data-del="' + esc(t.raw.id) + '">Delete</button></div>' : "") +
      '</div><div class="jr-notes">' +
      '<div class="pp-l">How you felt before entering</div><div class="rv-tags" data-group="emotion">' + MOODS.map(function (m) { return chip(m, nt.emotion === m, "mood", "data-mood"); }).join("") + "</div>" +
      '<div class="pp-l">How well you followed your process</div><div class="rv-tags jr-exec" data-group="execution">' + [1, 2, 3, 4, 5].map(function (x) { return chip(String(x), nt.execution === x, x >= 4 ? "good" : x <= 2 ? "bad" : "mid", "data-exec"); }).join("") + '<span class="faint">1 = ignored it · 5 = textbook</span></div>' +
      '<div class="pp-l">What went right or wrong</div><div class="rv-tags">' + TAGS.map(function (x) { return chip(x, (nt.tags || []).indexOf(x) >= 0, GOOD_TAGS.indexOf(x) >= 0 ? "good" : "bad", "data-tag"); }).join("") + "</div>" +
      '<label class="pp-l">What happened</label><textarea class="jr-text" data-field="happened" rows="3" maxlength="1500" placeholder="How the trade played out, what you saw, what you did and why">' + esc(nt.happened || "") + "</textarea>" +
      '<label class="pp-l">Lesson</label><input type="text" class="jr-text" data-field="lesson" maxlength="240" placeholder="One thing to keep or change next time" value="' + esc(nt.lesson || "") + '">' +
      '<div class="jr-saved faint" aria-live="polite"></div>' +
      "</div></div></details>";
  }

  // ------------------------------------------------------------ notes
  function update(key, fn, el) {
    var all = Trades.notes(), nt = all[key] || (all[key] = { tags: [], lesson: "" });
    nt.tags = nt.tags || [];
    fn(nt);
    Trades.saveNotes(all);
    var box = el.closest(".jr-entry");
    var t = rows.filter(function (r) { return r.key === key; })[0];
    if (t && !t.open) box.querySelector(".jr-badge").innerHTML = hasNotes(nt) ? '<span class="tag jr-done">Journaled</span>' : '<span class="tag st-trim">Add notes</span>';
    var s = box.querySelector(".jr-saved");
    s.textContent = "Saved";
    clearTimeout(s._t); s._t = setTimeout(function () { s.textContent = ""; }, 1500);
  }

  $("jr-list").addEventListener("click", function (e) {
    var b = e.target.closest("button");
    if (!b) return;
    var box = b.closest(".jr-entry"), key = box && box.dataset.key;
    if (b.dataset.edit) return openForm(b.dataset.edit);
    if (b.dataset.del) {
      if (!confirm("Delete this logged trade and its notes?")) return;
      Trades.saveLog(Trades.log().filter(function (x) { return String(x.id) !== b.dataset.del; }));
      var all = Trades.notes(); delete all[key]; Trades.saveNotes(all);
      return render();
    }
    var group = b.parentNode;
    if (b.dataset.mood) {
      update(key, function (nt) { nt.emotion = nt.emotion === b.dataset.mood ? "" : b.dataset.mood; }, b);
    } else if (b.dataset.exec) {
      update(key, function (nt) { var v = +b.dataset.exec; nt.execution = nt.execution === v ? null : v; }, b);
    } else if (b.dataset.tag) {
      update(key, function (nt) { var i = nt.tags.indexOf(b.dataset.tag); if (i >= 0) nt.tags.splice(i, 1); else nt.tags.push(b.dataset.tag); }, b);
      b.classList.toggle("on"); b.setAttribute("aria-pressed", String(b.classList.contains("on")));
      return;
    } else return;
    // single-choice groups
    var wasOn = b.classList.contains("on");
    group.querySelectorAll("button").forEach(function (x) { x.classList.remove("on"); x.setAttribute("aria-pressed", "false"); });
    if (!wasOn) { b.classList.add("on"); b.setAttribute("aria-pressed", "true"); }
  });
  $("jr-list").addEventListener("change", function (e) {
    var f = e.target.dataset.field;
    if (!f) return;
    var v = e.target.value.trim();
    update(e.target.closest(".jr-entry").dataset.key, function (nt) { nt[f] = v; }, e.target);
  });
  $("jr-list").addEventListener("toggle", function (e) {
    var d = e.target;
    if (d.classList && d.classList.contains("jr-entry")) openKeys[d.dataset.key] = d.open;
  }, true);
  ["jr-acct", "jr-result", "jr-strat"].forEach(function (id) { $(id).addEventListener("change", renderList); });
  $("jr-q").addEventListener("input", renderList);

  // ------------------------------------------------------------ log a trade
  var FIELDS = ["asset", "dir", "venue", "strat", "opened", "entry", "qty", "fees", "stop", "target", "closed", "exit", "note"];
  function openForm(id) {
    var e = id ? Trades.log().filter(function (x) { return String(x.id) === String(id); })[0] : null;
    editing = e ? e.id : null;
    $("jr-form-title").textContent = e ? "Edit logged trade" : "Log a trade";
    $("jf-asset").value = e ? e.asset : ""; $("jf-dir").value = e ? e.dir : "long"; $("jf-venue").value = e ? e.venue || "" : "";
    $("jf-strat").value = e ? e.strategy || "" : ""; $("jf-opened").value = localInput(e ? e.opened : Date.now());
    $("jf-entry").value = e ? e.entry : ""; $("jf-qty").value = e ? e.qty : ""; $("jf-fees").value = e ? e.fees || 0 : 0;
    $("jf-stop").value = e && e.stop ? e.stop : ""; $("jf-target").value = e && e.target ? e.target : "";
    $("jf-closed").value = e && e.closed ? localInput(e.closed) : ""; $("jf-exit").value = e && e.exit ? e.exit : "";
    $("jf-note").value = e ? e.note || "" : "";
    $("jr-form").hidden = false; preview();
    $("jr-form").scrollIntoView({ block: "start", behavior: "smooth" });
    $("jf-asset").focus({ preventScroll: true });
  }
  function formEntry() {
    var exit = num("jf-exit"), closed = fromInput($("jf-closed").value);
    if (exit > 0 && !closed) closed = Date.now();
    return {
      id: editing || Date.now(), asset: $("jf-asset").value.trim().toUpperCase(), dir: $("jf-dir").value, venue: $("jf-venue").value.trim(),
      strategy: $("jf-strat").value.trim(), opened: fromInput($("jf-opened").value), entry: num("jf-entry"), qty: num("jf-qty"), fees: num("jf-fees") || 0,
      stop: num("jf-stop"), target: num("jf-target"), closed: exit > 0 ? closed : null, exit: exit > 0 ? exit : null, note: $("jf-note").value.trim()
    };
  }
  function problems(e) {
    var p = [];
    if (!e.asset) p.push("Add the asset.");
    if (!e.opened) p.push("Add when it was opened.");
    if (!(e.entry > 0)) p.push("Add the entry price.");
    if (!(e.qty > 0)) p.push("Add the quantity.");
    if (e.closed && e.opened && e.closed < e.opened) p.push("Closed is before opened.");
    if (e.stop > 0 && e.entry > 0 && (e.dir === "long" ? e.stop >= e.entry : e.stop <= e.entry)) p.push("The stop is on the wrong side of the entry for a " + e.dir + ".");
    return p;
  }
  function preview() {
    var e = formEntry(), p = problems(e);
    if (p.length) { $("jf-preview").innerHTML = '<span class="faint">' + esc(p[0]) + "</span>"; return; }
    var cost = e.entry * e.qty, risk = e.stop > 0 ? Math.abs(e.entry - e.stop) * e.qty : null, txt = "Position <b>$" + n(cost) + "</b>";
    if (risk) txt += " · risking <b>$" + n(risk) + "</b> to the stop";
    if (e.exit > 0) {
      var pnl = (e.dir === "short" ? e.entry - e.exit : e.exit - e.entry) * e.qty - e.fees;
      txt += " · result " + sgnMoney(pnl, "$") + " (" + n(pnl / cost * 100, 1) + "%" + (risk ? ", " + n(pnl / risk, 2) + "R" : "") + ")";
    } else txt += " · open";
    $("jf-preview").innerHTML = txt;
  }
  FIELDS.forEach(function (f) { $("jf-" + f).addEventListener("input", preview); });
  $("jr-new").addEventListener("click", function () { openForm(null); });
  $("jf-cancel").addEventListener("click", function () { $("jr-form").hidden = true; editing = null; });
  $("jr-form").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var e = formEntry(), p = problems(e);
    if (p.length) { $("jf-preview").innerHTML = '<span class="down">' + esc(p.join(" ")) + "</span>"; return; }
    var log = Trades.log().filter(function (x) { return x.id !== e.id; });
    log.push(e);
    Trades.saveLog(log);
    $("jr-form").hidden = true; editing = null;
    openKeys["l-" + e.id] = true;
    render();
  });

  // ------------------------------------------------------------ export / backup
  function download(name, type, text) {
    var a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: type }));
    a.download = name; a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
  }
  $("jr-csv").addEventListener("click", function () {
    var notes = Trades.notes(), cell = function (v) { v = v == null ? "" : String(v); return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; };
    var head = ["account", "asset", "direction", "strategy", "venue", "opened_utc", "closed_utc", "status", "pnl", "unit", "pnl_pct", "r", "held_hours", "exit", "stop_pct", "why", "emotion", "execution", "tags", "what_happened", "lesson"];
    var lines = filtered().map(function (t) {
      var nt = notes[t.key] || {};
      return [ACCT[t.acct], t.asset, t.dir, t.strategy, t.venue, stamp(t.opened), t.open ? "" : stamp(t.closed), t.open ? "open" : "closed",
        t.pnl != null ? t.pnl.toFixed(t.unit === "$" ? 2 : 4) : "", t.unit === "$" ? "USD" : "TAO", t.pnlPct != null ? t.pnlPct.toFixed(2) : "",
        t.r != null ? t.r.toFixed(2) : "", t.held != null ? (t.held / 3600000).toFixed(1) : "", t.exit, t.slPct ? t.slPct.toFixed(2) : "", t.note,
        nt.emotion, nt.execution, (nt.tags || []).join("; "), nt.happened, nt.lesson].map(cell).join(",");
    });
    download("trade-journal-" + new Date().toISOString().slice(0, 10) + ".csv", "text/csv", head.join(",") + "\n" + lines.join("\n") + "\n");
  });
  $("jr-export").addEventListener("click", function () {
    download("trade-journal-backup-" + new Date().toISOString().slice(0, 10) + ".json", "application/json",
      JSON.stringify({ v: 1, kind: "journal", saved: new Date().toISOString(), log: Trades.log(), notes: Trades.notes() }, null, 1));
  });
  $("jr-import").addEventListener("change", function () {
    var f = this.files[0];
    if (!f) return;
    f.text().then(function (txt) {
      var d = JSON.parse(txt);
      if (!d || d.kind !== "journal" || !Array.isArray(d.log) || typeof d.notes !== "object") throw new Error("not a journal backup");
      var log = Trades.log(), ids = {};
      d.log.forEach(function (x) { ids[x.id] = 1; });
      Trades.saveLog(log.filter(function (x) { return !ids[x.id]; }).concat(d.log));
      var notes = Trades.notes();
      Object.keys(d.notes).forEach(function (k) { notes[k] = d.notes[k]; });
      Trades.saveNotes(notes);
      render();
      alert("Restored " + d.log.length + " logged trades and notes for " + Object.keys(d.notes).length + " trades.");
    }).catch(function (e) { alert("Couldn't restore: " + e.message); });
    this.value = "";
  });

  document.addEventListener("journal:show", render);
  render();
})();
