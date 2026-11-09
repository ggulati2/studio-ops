/* Movable and resizable columns for tables marked data-columns="<name>".
   Drag a header to move its column; drag a header's right edge to resize it
   (double-click the edge to fit). Alt + ←/→ moves the focused header's column.
   The layout is remembered per browser and can be reset. No inline JS (CSP). */
(function () {
  "use strict";
  var doc = document;
  var MIN = 56;

  function load(key) {
    try { return JSON.parse(localStorage.getItem("cols:" + key)) || {}; } catch (e) { return {}; }
  }
  function save(key, v) {
    try { localStorage.setItem("cols:" + key, JSON.stringify(v)); } catch (e) {}
  }
  function drop(key) { try { localStorage.removeItem("cols:" + key); } catch (e) {} }

  function setup(table) {
    if (table.dataset.colsReady) return;
    var key = table.getAttribute("data-columns");
    var headRow = table.tHead && table.tHead.rows[0];
    if (!headRow) return;
    table.dataset.colsReady = "1";
    var ths = Array.prototype.slice.call(headRow.cells);
    var n = ths.length;
    ths.forEach(function (th, i) {
      th.dataset.col = th.getAttribute("data-col") || String(i);
      th.classList.add("col-th");
      th.tabIndex = 0;
      th.setAttribute("aria-roledescription", "movable column header");
      th.title = "Drag to move · drag the edge to resize · Alt+←/→ to move";
      var grip = doc.createElement("span");
      grip.className = "col-grip";
      grip.setAttribute("aria-hidden", "true");
      th.appendChild(grip);
    });
    var keys = ths.map(function (th) { return th.dataset.col; });
    var saved = load(key);

    function rows() {
      return Array.prototype.slice.call(table.rows).filter(function (r) { return r.cells.length === n; });
    }
    function currentOrder() {
      return Array.prototype.map.call(headRow.cells, function (c) { return c.dataset.col; });
    }
    function applyOrder(order) {
      if (!order || order.length !== n || order.some(function (k) { return keys.indexOf(k) < 0; })) return;
      var pos = currentOrder();
      rows().forEach(function (r) {
        var cells = Array.prototype.slice.call(r.cells);
        // cells are in the same order as the header row right now
        var byKey = {};
        pos.forEach(function (k, i) { byKey[k] = cells[i]; });
        order.forEach(function (k) { r.appendChild(byKey[k]); });
      });
    }
    function freezeWidths() {
      if (table.classList.contains("cols-fixed")) return;
      var widths = Array.prototype.map.call(headRow.cells, function (c) { return c.getBoundingClientRect().width; });
      Array.prototype.forEach.call(headRow.cells, function (c, i) { c.style.width = Math.round(widths[i]) + "px"; });
      table.classList.add("cols-fixed");
      syncTableWidth();
    }
    function syncTableWidth() {
      var total = 0;
      Array.prototype.forEach.call(headRow.cells, function (c) { total += parseFloat(c.style.width) || c.getBoundingClientRect().width; });
      table.style.width = Math.round(total) + "px";
      table.style.minWidth = "0";
    }
    function applyWidths(widths) {
      if (!widths) return;
      freezeWidths();
      Array.prototype.forEach.call(headRow.cells, function (c) {
        var w = widths[c.dataset.col];
        if (w) c.style.width = Math.max(MIN, w) + "px";
      });
      syncTableWidth();
    }
    function persist() {
      var widths = null;
      if (table.classList.contains("cols-fixed")) {
        widths = {};
        Array.prototype.forEach.call(headRow.cells, function (c) { widths[c.dataset.col] = Math.round(parseFloat(c.style.width)); });
      }
      var order = currentOrder();
      var custom = order.join() !== keys.join() || widths;
      if (custom) save(key, { order: order, widths: widths }); else drop(key);
      resetBtn.hidden = !custom;
    }

    /* Toolbar: hint + reset */
    var bar = doc.createElement("div");
    bar.className = "cols-bar";
    bar.innerHTML = '<span class="small muted">Drag a column header to move it, or its edge to resize.</span>';
    var resetBtn = doc.createElement("button");
    resetBtn.type = "button"; resetBtn.className = "btn sm"; resetBtn.textContent = "Reset columns";
    resetBtn.hidden = true;
    bar.appendChild(resetBtn);
    var anchor = table.closest(".scroll") || table;
    anchor.parentNode.insertBefore(bar, anchor);
    resetBtn.addEventListener("click", function () {
      applyOrder(keys);
      table.classList.remove("cols-fixed");
      table.style.width = table.style.minWidth = "";
      Array.prototype.forEach.call(headRow.cells, function (c) { c.style.width = ""; });
      drop(key); resetBtn.hidden = true;
    });

    applyOrder(saved.order);
    applyWidths(saved.widths);
    resetBtn.hidden = !(saved.order || saved.widths);

    /* Resize */
    headRow.addEventListener("pointerdown", function (e) {
      var grip = e.target.closest(".col-grip");
      if (!grip) return;
      e.preventDefault(); e.stopPropagation();
      freezeWidths();
      var th = grip.parentNode, startX = e.clientX, startW = parseFloat(th.style.width);
      table.classList.add("cols-resizing");
      grip.setPointerCapture(e.pointerId);
      function move(ev) {
        th.style.width = Math.max(MIN, Math.round(startW + ev.clientX - startX)) + "px";
        syncTableWidth();
      }
      function up() {
        grip.removeEventListener("pointermove", move); grip.removeEventListener("pointerup", up); grip.removeEventListener("pointercancel", up);
        table.classList.remove("cols-resizing");
        persist();
      }
      grip.addEventListener("pointermove", move); grip.addEventListener("pointerup", up); grip.addEventListener("pointercancel", up);
    });
    headRow.addEventListener("dblclick", function (e) {
      var grip = e.target.closest(".col-grip");
      if (!grip) return;
      var th = grip.parentNode, idx = Array.prototype.indexOf.call(headRow.cells, th);
      freezeWidths();
      // Fit to the widest cell content in this column
      var widest = MIN;
      rows().forEach(function (r) {
        var c = r.cells[idx], probe = c.cloneNode(true);
        probe.style.cssText = "position:absolute;visibility:hidden;width:auto;white-space:nowrap";
        c.parentNode.appendChild(probe);
        widest = Math.max(widest, Math.ceil(probe.getBoundingClientRect().width) + 4);
        probe.remove();
      });
      th.style.width = Math.min(widest, 480) + "px";
      syncTableWidth(); persist();
    });

    /* Move by dragging a header */
    headRow.addEventListener("pointerdown", function (e) {
      if (e.button !== 0 || e.target.closest(".col-grip, a, button, input, select")) return;
      var th = e.target.closest("th");
      if (!th) return;
      var startX = e.clientX, dragging = false, ghost = null, marker = null, target = null;
      function begin() {
        dragging = true;
        table.classList.add("cols-moving");
        th.classList.add("col-dragging");
        var r = th.getBoundingClientRect();
        ghost = doc.createElement("div");
        ghost.className = "col-ghost";
        ghost.textContent = th.textContent.trim();
        ghost.style.width = r.width + "px";
        ghost.style.top = r.top + "px";
        doc.body.appendChild(ghost);
        marker = doc.createElement("div");
        marker.className = "col-marker";
        doc.body.appendChild(marker);
      }
      function move(ev) {
        if (!dragging) {
          if (Math.abs(ev.clientX - startX) < 6) return;
          begin();
        }
        ev.preventDefault();
        ghost.style.left = (ev.clientX - ghost.offsetWidth / 2) + "px";
        var cells = Array.prototype.slice.call(headRow.cells), tr = table.getBoundingClientRect();
        target = null;
        cells.forEach(function (c, i) {
          var r = c.getBoundingClientRect();
          if (ev.clientX >= r.left && ev.clientX < r.right) target = ev.clientX < r.left + r.width / 2 ? i : i + 1;
        });
        if (target === null) target = ev.clientX < tr.left ? 0 : cells.length;
        var edge = target < cells.length ? cells[target].getBoundingClientRect().left : cells[cells.length - 1].getBoundingClientRect().right;
        marker.style.left = (edge - 1) + "px";
        marker.style.top = tr.top + "px";
        marker.style.height = Math.min(tr.height, window.innerHeight - tr.top) + "px";
        // Scroll the wrapper when dragging near its edges
        var sc = table.closest(".scroll");
        if (sc) {
          var sr = sc.getBoundingClientRect();
          if (ev.clientX > sr.right - 40) sc.scrollLeft += 18;
          else if (ev.clientX < sr.left + 40) sc.scrollLeft -= 18;
        }
      }
      function up() {
        doc.removeEventListener("pointermove", move); doc.removeEventListener("pointerup", up); doc.removeEventListener("pointercancel", up);
        if (!dragging) return;
        table.classList.remove("cols-moving");
        th.classList.remove("col-dragging");
        if (ghost) ghost.remove();
        if (marker) marker.remove();
        var from = Array.prototype.indexOf.call(headRow.cells, th);
        if (target !== null && target !== from && target !== from + 1) {
          var order = currentOrder(), k = order.splice(from, 1)[0];
          order.splice(target > from ? target - 1 : target, 0, k);
          applyOrder(order);
          persist();
        }
        // Swallow the click that follows a drag
        var stop = function (ev) { ev.stopPropagation(); ev.preventDefault(); doc.removeEventListener("click", stop, true); };
        doc.addEventListener("click", stop, true);
        setTimeout(function () { doc.removeEventListener("click", stop, true); }, 400);
        setTimeout(function () { th.focus(); }, 0);
      }
      doc.addEventListener("pointermove", move); doc.addEventListener("pointerup", up); doc.addEventListener("pointercancel", up);
    });

    /* Keyboard: Alt + ←/→ */
    headRow.addEventListener("keydown", function (e) {
      if (!e.altKey || (e.key !== "ArrowLeft" && e.key !== "ArrowRight")) return;
      var th = e.target.closest("th");
      if (!th) return;
      e.preventDefault();
      var order = currentOrder(), i = order.indexOf(th.dataset.col), j = i + (e.key === "ArrowLeft" ? -1 : 1);
      if (j < 0 || j >= order.length) return;
      order.splice(j, 0, order.splice(i, 1)[0]);
      applyOrder(order); persist(); th.focus();
    });
  }

  function init() {
    if (!window.matchMedia("(min-width: 760px)").matches) return;
    Array.prototype.forEach.call(doc.querySelectorAll("table[data-columns]"), setup);
  }
  if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", init); else init();
  window.matchMedia("(min-width: 760px)").addEventListener("change", function (m) { if (m.matches) init(); });
})();
