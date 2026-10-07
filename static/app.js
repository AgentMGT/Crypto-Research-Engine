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
