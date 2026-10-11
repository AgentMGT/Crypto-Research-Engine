// ALTCOINS page: tabs, filters, and "My checks" per coin, saved in this browser.
(function () {
  var KEY = "alt-checks-v1";
  var store = {};
  try { store = JSON.parse(localStorage.getItem(KEY) || "{}") || {}; } catch (e) { store = {}; }
  function save() { try { localStorage.setItem(KEY, JSON.stringify(store)); } catch (e) { /* private mode */ } }

  // Tabs
  var tabs = document.querySelectorAll(".alt-tabs [data-tab]");
  function show(name) {
    tabs.forEach(function (b) {
      var on = b.getAttribute("data-tab") === name;
      b.setAttribute("aria-selected", on ? "true" : "false");
      var panel = document.getElementById("tab-" + b.getAttribute("data-tab"));
      if (panel) panel.hidden = !on;
    });
    try { localStorage.setItem("alt-tab", name); } catch (e) { /* ignore */ }
  }
  tabs.forEach(function (b) { b.addEventListener("click", function () { show(b.getAttribute("data-tab")); }); });
  var saved = null;
  try { saved = localStorage.getItem("alt-tab"); } catch (e) { /* ignore */ }
  if (saved && document.getElementById("tab-" + saved)) show(saved);

  // My checks
  function count(card) {
    var s = store[card.getAttribute("data-id")] || {};
    var n = Object.keys(s.c || {}).filter(function (k) { return s.c[k]; }).length;
    var el = card.querySelector("[data-n]");
    if (el) el.textContent = n ? n + "/8" : "";
    return n + (s.n ? 1 : 0);
  }
  document.querySelectorAll(".alt-card").forEach(function (card) {
    var id = card.getAttribute("data-id");
    var s = store[id] || {};
    card.querySelectorAll("[data-check]").forEach(function (box) {
      box.checked = !!(s.c && s.c[box.getAttribute("data-check")]);
      box.addEventListener("change", function () {
        var cur = store[id] || (store[id] = {});
        cur.c = cur.c || {};
        cur.c[box.getAttribute("data-check")] = box.checked;
        cur.t = new Date().toISOString();
        save(); count(card);
      });
    });
    var notes = card.querySelector("[data-notes]");
    if (notes) {
      notes.value = s.n || "";
      notes.addEventListener("input", function () {
        var cur = store[id] || (store[id] = {});
        cur.n = notes.value; cur.t = new Date().toISOString();
        save(); count(card);
      });
    }
    count(card);
  });

  // Filters apply to cards and table rows in every tab.
  var q = document.getElementById("alt-q");
  var strong = document.getElementById("alt-strong");
  var mine = document.getElementById("alt-mine");
  function apply() {
    var term = (q && q.value || "").trim().toLowerCase();
    document.querySelectorAll(".alt-card, #tab-dex tbody tr, #alt-all tbody tr").forEach(function (el) {
      var ok = !term || (el.getAttribute("data-q") || "").toLowerCase().indexOf(term) >= 0;
      if (el.classList.contains("alt-card")) {
        if (strong && strong.checked) ok = ok && Number(el.getAttribute("data-passed")) >= 60;
        if (mine && mine.checked) ok = ok && count(el) > 0;
      }
      el.hidden = !ok;
    });
  }
  [q, strong, mine].forEach(function (el) { if (el) el.addEventListener("input", apply); });
  [strong, mine].forEach(function (el) { if (el) el.addEventListener("change", apply); });
})();
