(function () {
  "use strict";
  function cellValue(cell, sortType) {
    if (!cell) return "";
    if (cell.dataset.sortValue) {
      var raw = cell.dataset.sortValue;
      if (sortType === "number") {
        var num = parseFloat(raw.replace(/[^0-9.\-]/g, ""));
        return isNaN(num) ? 0 : num;
      }
      if (sortType === "date") {
        var dts = Date.parse(raw.replace(" ", "T"));
        return isNaN(dts) ? raw.toLowerCase() : dts;
      }
      return raw.toLowerCase();
    }
    var text = (cell.textContent || "").trim();
    if (sortType === "number") {
      var n = parseFloat(text.replace(/[^0-9.\-]/g, ""));
      return isNaN(n) ? 0 : n;
    }
    if (sortType === "date") {
      var normalized = text.replace(" ", "T");
      var ts = Date.parse(normalized);
      return isNaN(ts) ? text.toLowerCase() : ts;
    }
    return text.toLowerCase();
  }
  function isSortableHeader(th) {
    if (!th || th.tagName !== "TH") return false;
    if (th.classList.contains("no-sort") || th.classList.contains("actions-col")) {
      return false;
    }
    if (th.querySelector("button, input, select, a.btn")) return false;
    var label = th.dataset.sortLabel || th.textContent || "";
    return label.trim().length > 0;
  }
  function ensureTableStructure(table) {
    var firstRow = table.querySelector("tr");
    if (!firstRow || !firstRow.querySelector("th")) return null;
    var thead = table.querySelector("thead");
    var tbody = table.querySelector("tbody");
    if (!thead) {
      thead = document.createElement("thead");
      thead.appendChild(firstRow);
      table.insertBefore(thead, table.firstChild);
    }
    if (!tbody) {
      tbody = document.createElement("tbody");
      var rows = Array.from(table.querySelectorAll("tr")).filter(function (row) {
        return row.parentElement !== thead;
      });
      rows.forEach(function (row) {
        tbody.appendChild(row);
      });
      table.appendChild(tbody);
    }
    return { thead: thead, tbody: tbody };
  }
  function initSortableTable(table) {
    if (table.dataset.sortableInit === "1") return;
    var parts = ensureTableStructure(table);
    if (!parts) return;
    var headerRow = parts.thead.querySelector("tr");
    var headers = Array.from(headerRow.querySelectorAll("th"));
    if (!headers.length) return;
    headers.forEach(function (th, colIndex) {
      if (!isSortableHeader(th)) return;
      var sortType = th.dataset.sort || "text";
      var labelText = (th.dataset.sortLabel || th.textContent || "").trim();
      th.classList.add("sortable-th");
      th.innerHTML = "";
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "th-sort-btn";
      btn.setAttribute("aria-label", "Sort by " + labelText);
      var labelSpan = document.createElement("span");
      labelSpan.className = "th-sort-label";
      labelSpan.textContent = labelText;
      var iconSpan = document.createElement("span");
      iconSpan.className = "th-sort-icon";
      iconSpan.setAttribute("aria-hidden", "true");
      iconSpan.textContent = "\u21C5";
      btn.appendChild(labelSpan);
      btn.appendChild(iconSpan);
      th.appendChild(btn);
      btn.addEventListener("click", function () {
        var rows = Array.from(parts.tbody.querySelectorAll("tr")).filter(function (row) {
          return !row.querySelector("td[colspan]");
        });
        if (rows.length < 2) return;
        var current = th.dataset.sortDir;
        var direction = current === "asc" ? "desc" : "asc";
        headers.forEach(function (other) {
          other.dataset.sortDir = "";
          var icon = other.querySelector(".th-sort-icon");
          if (icon) icon.textContent = "\u21C5";
          other.classList.remove("sort-asc", "sort-desc");
        });
        th.dataset.sortDir = direction;
        th.classList.add(direction === "asc" ? "sort-asc" : "sort-desc");
        iconSpan.textContent = direction === "asc" ? "\u2191" : "\u2193";
        rows.sort(function (a, b) {
          var av = cellValue(a.cells[colIndex], sortType);
          var bv = cellValue(b.cells[colIndex], sortType);
          var cmp = 0;
          if (av < bv) cmp = -1;
          else if (av > bv) cmp = 1;
          return direction === "asc" ? cmp : -cmp;
        });
        rows.forEach(function (row) {
          parts.tbody.appendChild(row);
        });
      });
    });
    table.dataset.sortableInit = "1";
  }
  function initAll() {
    document.querySelectorAll(".table-wrap table").forEach(initSortableTable);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAll);
  } else {
    initAll();
  }
})();
