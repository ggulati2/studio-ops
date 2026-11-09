/* Planner: click a cell to plan it, drag a film to move it (Alt/Option to copy),
   keyboard with arrows / Enter / Delete. Saves in the background and re-draws only
   the changed cells. Toasts confirm each change, with Undo. No inline JS (CSP). */
(function () {
  "use strict";
  var doc = document;
  var root = doc.querySelector("[data-planner]");
  if (!root) return;
  var view = root.getAttribute("data-view");
  var url = root.getAttribute("data-url");
  var csrf = (root.querySelector("input[name=csrfmiddlewaretoken]") || {}).value;
  var optionsTpl = root.querySelector("template[data-plan-options]");
  var table = root.querySelector("table");

  function el(tag, cls, text) { var e = doc.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function cellOf(node) { return node && node.closest ? node.closest("td[data-cell]") : null; }
  function cellKey(td) { return td.getAttribute("data-u") + ":" + td.getAttribute("data-d"); }
  function findCell(key) {
    var p = key.split(":");
    return table.querySelector('td[data-cell][data-u="' + p[0] + '"][data-d="' + p[1] + '"]');
  }
  function fmtDay(iso) {
    var d = new Date(iso + "T00:00:00");
    return d.toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" });
  }

  /* ---------------------------------------------------------------- toasts */
  var stack = el("div", "toasts");
  stack.setAttribute("role", "status");
  stack.setAttribute("aria-live", "polite");
  doc.body.appendChild(stack);
  function toast(msg, opts) {
    opts = opts || {};
    var t = el("div", "toast" + (opts.error ? " error" : ""));
    t.appendChild(el("span", "", msg));
    if (opts.undo) {
      var b = el("button", "toast-undo", "Undo"); b.type = "button";
      b.addEventListener("click", function () { t.remove(); opts.undo(); });
      t.appendChild(b);
    }
    stack.appendChild(t);
    requestAnimationFrame(function () { t.classList.add("in"); });
    setTimeout(function () { t.classList.remove("in"); setTimeout(function () { t.remove(); }, 250); }, opts.error ? 7000 : 5000);
  }

  /* ---------------------------------------------------------------- saving */
  function post(data, busyCells) {
    var body = new URLSearchParams();
    body.append("view", view);
    Object.keys(data).forEach(function (k) {
      var v = data[k];
      if (Array.isArray(v)) v.forEach(function (x) { body.append(k, x); }); else body.append(k, v == null ? "" : v);
    });
    (busyCells || []).forEach(function (td) { if (td) td.classList.add("saving"); });
    return fetch(url, {
      method: "POST", credentials: "same-origin", body: body,
      headers: { "X-CSRFToken": csrf, "X-Requested-With": "fetch", "Accept": "application/json" }
    }).then(function (r) {
      if (r.status === 403) throw new Error("Your session has ended. Reload the page and sign in again.");
      if (!r.ok) throw new Error("Couldn't save (" + r.status + "). Check your connection and try again.");
      return r.json();
    }).then(function (res) {
      Object.keys(res.cells || {}).forEach(function (k) {
        var td = findCell(k);
        if (!td) return;
        td.innerHTML = res.cells[k].html;
        td.className = res.cells[k].cls;
        td.classList.add("flash");
        setTimeout(function () { td.classList.remove("flash"); }, 900);
      });
      if (view === "month") recountMonth();
      if (res.message) {
        toast(res.message, { error: !res.ok, undo: res.undo ? function () {
          var u = res.undo, d = Object.assign({}, u);
          post(d, [findCell(u.user + ":" + u.date)]);
        } : null });
      }
      return res;
    }).catch(function (err) {
      toast(err.message || "Couldn't save.", { error: true });
    }).then(function (res) {
      (busyCells || []).forEach(function (td) { if (td) td.classList.remove("saving"); });
      return res;
    });
  }

  /* Month view: keep the "Planned x / y" column right after edits */
  function recountMonth() {
    Array.prototype.forEach.call(table.tBodies[0].rows, function (tr) {
      var tds = tr.querySelectorAll("td[data-cell]");
      if (!tds.length) return;
      var planned = 0, work = 0;
      Array.prototype.forEach.call(tds, function (td) {
        var off = /\bk-(leave|holiday|off)\b/.test(td.className);
        if (!off) { work++; if (td.querySelector('[data-chip][data-slot="1"]')) planned++; }
      });
      var sum = tr.querySelector("td.sum");
      if (sum) sum.innerHTML = "<b>" + planned + '</b><span class="small muted">/' + work + "</span>" +
        (work - planned ? '<div class="small" style="color:var(--ok)">' + (work - planned) + " free</div>" : "");
    });
  }

  /* ---------------------------------------------------------------- picker popover */
  var pop = null, cur = null;   // cur = {td, slot}

  function buildPop() {
    pop = el("div", "pp");
    pop.setAttribute("role", "dialog");
    pop.hidden = true;
    pop.innerHTML =
      '<div class="pp-head"><div class="pp-title"></div><button type="button" class="pp-x" aria-label="Close">×</button></div>' +
      '<div class="pp-slots seg" role="tablist"><a href="#" role="tab" data-s="1">Main work</a><a href="#" role="tab" data-s="2">Alongside</a></div>' +
      '<div class="pp-current"></div>' +
      '<input class="input pp-search" type="search" placeholder="Search films, agencies, editors…" aria-label="Search" autocomplete="off">' +
      '<div class="pp-list" role="listbox"></div>' +
      '<div class="pp-foot"><div class="pp-repeat">Copy to next <input class="input" type="number" min="1" max="60" value="5" aria-label="Number of working days"> working days <button type="button" class="btn sm" data-repeat>Copy</button></div>' +
      '<button type="button" class="btn sm danger pp-clear" data-clear>Clear</button></div>';
    pop.querySelector(".pp-list").appendChild(optionsTpl.content.cloneNode(true));
    doc.body.appendChild(pop);
    pop.querySelector(".pp-x").addEventListener("click", function () { closePop(true); });
    pop.querySelector(".pp-slots").addEventListener("click", function (e) {
      var a = e.target.closest("a[data-s]");
      if (!a) return;
      e.preventDefault();
      if (a.classList.contains("disabled")) return;
      cur.slot = a.getAttribute("data-s");
      fillPop();
    });
    var search = pop.querySelector(".pp-search");
    search.addEventListener("input", filter);
    search.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
      else if (e.key === "Enter") { e.preventDefault(); var a = pop.querySelector(".po.active"); if (a) choose(a.getAttribute("data-v")); }
    });
    pop.querySelector(".pp-list").addEventListener("click", function (e) {
      var o = e.target.closest(".po");
      if (o) choose(o.getAttribute("data-v"));
    });
    pop.querySelector("[data-clear]").addEventListener("click", function () { choose(""); });
    pop.querySelector("[data-repeat]").addEventListener("click", function () {
      var n = parseInt(pop.querySelector(".pp-repeat input").value, 10) || 0;
      var v = currentValue(cur.td, cur.slot);
      if (!n || !v) return;
      var td = cur.td, slot = cur.slot;
      closePop(false);
      post({ action: "repeat", user: td.getAttribute("data-u"), date: td.getAttribute("data-d"), slot: slot, value: v, repeat: n }, [td]);
    });
    pop.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { e.preventDefault(); closePop(true); }
    });
  }

  function currentValue(td, slot) {
    var c = td.querySelector('[data-chip][data-slot="' + slot + '"]');
    return c ? c.getAttribute("data-v") : "";
  }

  function fillPop() {
    var td = cur.td, slot = cur.slot, uid = td.getAttribute("data-u");
    var hasMain = !!currentValue(td, "1");
    if (slot === "2" && !hasMain) slot = cur.slot = "1";
    pop.querySelector(".pp-title").innerHTML = "";
    pop.querySelector(".pp-title").appendChild(el("b", "", td.getAttribute("data-name")));
    pop.querySelector(".pp-title").appendChild(el("span", "", " · " + fmtDay(td.getAttribute("data-d"))));
    Array.prototype.forEach.call(pop.querySelectorAll(".pp-slots a"), function (a) {
      var s = a.getAttribute("data-s");
      a.classList.toggle("on", s === slot);
      a.setAttribute("aria-selected", s === slot ? "true" : "false");
      a.classList.toggle("disabled", s === "2" && !hasMain);
      a.title = s === "2" && !hasMain ? "Plan the main work first" : "";
    });
    var v = currentValue(td, slot);
    var currentBox = pop.querySelector(".pp-current");
    currentBox.innerHTML = "";
    if (v) {
      var chip = td.querySelector('[data-chip][data-slot="' + slot + '"]');
      currentBox.appendChild(el("span", "small muted", slot === "2" ? "Alongside now: " : "Planned now: "));
      currentBox.appendChild(el("b", "", chip ? chip.getAttribute("title").replace(/^Alongside: /, "") : v));
    }
    pop.querySelector("[data-clear]").hidden = !v;
    pop.querySelector(".pp-repeat").hidden = !v;
    // Their own films first
    var films = pop.querySelector('[data-group="films"]');
    if (films) {
      var opts = Array.prototype.slice.call(films.querySelectorAll(".po"));
      opts.sort(function (a, b) {
        var am = a.getAttribute("data-editor") === uid ? 0 : a.getAttribute("data-editor") ? 2 : 1;
        var bm = b.getAttribute("data-editor") === uid ? 0 : b.getAttribute("data-editor") ? 2 : 1;
        return am - bm || (+a.dataset.i || 0) - (+b.dataset.i || 0);
      });
      opts.forEach(function (o) {
        o.classList.toggle("mine", o.getAttribute("data-editor") === uid);
        films.appendChild(o);
      });
    }
    Array.prototype.forEach.call(pop.querySelectorAll(".po"), function (o) {
      o.classList.toggle("sel", o.getAttribute("data-v") === v);
      o.setAttribute("aria-selected", o.getAttribute("data-v") === v ? "true" : "false");
    });
    var s = pop.querySelector(".pp-search");
    s.value = "";
    filter();
  }

  function filter() {
    var q = pop.querySelector(".pp-search").value.trim().toLowerCase();
    var words = q.split(/\s+/).filter(Boolean);
    var first = null;
    Array.prototype.forEach.call(pop.querySelectorAll(".po-group"), function (g) {
      var any = false;
      Array.prototype.forEach.call(g.querySelectorAll(".po"), function (o) {
        var hay = (o.getAttribute("data-q") || "") + " " + o.textContent.toLowerCase();
        var ok = words.every(function (w) { return hay.indexOf(w) >= 0; });
        o.hidden = !ok;
        o.classList.remove("active");
        if (ok) { any = true; if (!first) first = o; }
      });
      g.hidden = !any;
    });
    var sel = pop.querySelector(".po.sel:not([hidden])");
    var act = q ? first : (sel || first);
    if (act) act.classList.add("active");
    var empty = pop.querySelector(".pp-empty");
    if (!first && !empty) { empty = el("div", "pp-empty", "Nothing matches."); pop.querySelector(".pp-list").appendChild(empty); }
    if (empty) empty.hidden = !!first;
    if (act && !q) act.scrollIntoView({ block: "nearest" });
  }

  function move(dir) {
    var vis = Array.prototype.filter.call(pop.querySelectorAll(".po"), function (o) { return !o.hidden; });
    if (!vis.length) return;
    var i = vis.indexOf(pop.querySelector(".po.active"));
    if (i >= 0) vis[i].classList.remove("active");
    i = Math.max(0, Math.min(vis.length - 1, i + dir));
    vis[i].classList.add("active");
    vis[i].scrollIntoView({ block: "nearest" });
  }

  function choose(v) {
    var td = cur.td, slot = cur.slot;
    var before = currentValue(td, slot);
    closePop(true);
    if (v === before) return;
    post({ action: "set", user: td.getAttribute("data-u"), date: td.getAttribute("data-d"), slot: slot, value: v }, [td]);
  }

  function place(td) {
    var r = td.getBoundingClientRect(), vw = window.innerWidth, vh = window.innerHeight;
    if (vw < 600) { pop.classList.add("sheet"); pop.style.left = pop.style.top = ""; return; }
    pop.classList.remove("sheet");
    var pw = pop.offsetWidth, ph = pop.offsetHeight;
    var left = r.left; if (left + pw > vw - 10) left = Math.max(10, r.right - pw);
    var top = r.bottom + 6; if (top + ph > vh - 10) top = Math.max(10, r.top - ph - 6);
    pop.style.left = (left + window.scrollX) + "px";
    pop.style.top = (top + window.scrollY) + "px";
  }

  function openPop(td, slot) {
    if (!td || td.classList.contains("locked")) {
      if (td) toast(td.getAttribute("data-name") + " is on leave that day.", { error: true });
      return;
    }
    if (!pop) {
      buildPop();
      Array.prototype.forEach.call(pop.querySelectorAll('[data-group="films"] .po'), function (o, i) { o.dataset.i = i; });
    }
    if (cur && cur.td) cur.td.classList.remove("picking");
    cur = { td: td, slot: slot || "1" };
    td.classList.add("picking");
    fillPop();
    pop.hidden = false;
    place(td);
    requestAnimationFrame(function () { pop.classList.add("in"); });
    var s = pop.querySelector(".pp-search");
    if (window.matchMedia("(pointer:fine)").matches) s.focus(); else pop.querySelector(".pp-x").focus();
  }

  function closePop(refocus) {
    if (!pop || pop.hidden) return;
    pop.classList.remove("in");
    pop.hidden = true;
    var td = cur && cur.td;
    if (td) td.classList.remove("picking");
    cur = null;
    if (refocus && td && doc.body.contains(td)) td.focus();
  }

  doc.addEventListener("mousedown", function (e) {
    if (pop && !pop.hidden && !pop.contains(e.target) && !cellOf(e.target)) closePop(false);
  });
  window.addEventListener("resize", function () { if (cur && pop && !pop.hidden) place(cur.td); });
  root.querySelector(".planner-scroll").addEventListener("scroll", function () { if (cur && pop && !pop.hidden && !pop.classList.contains("sheet")) place(cur.td); });

  /* ---------------------------------------------------------------- clicks + keyboard on cells */
  Array.prototype.forEach.call(table.querySelectorAll("td[data-cell]"), function (td) { td.tabIndex = -1; });
  var first = table.querySelector("td[data-cell]");
  if (first) first.tabIndex = 0;

  table.addEventListener("click", function (e) {
    if (justDragged) return;
    var td = cellOf(e.target);
    if (!td) return;
    var chip = e.target.closest("[data-chip]"), add = e.target.closest("[data-add]");
    var slot = chip ? chip.getAttribute("data-slot") : add ? add.getAttribute("data-slot") : "1";
    setFocus(td, false);
    openPop(td, slot);
  });

  function cells2d() {
    return Array.prototype.map.call(table.tBodies[0].rows, function (tr) {
      return Array.prototype.slice.call(tr.querySelectorAll("td[data-cell]"));
    });
  }
  function setFocus(td, focus) {
    Array.prototype.forEach.call(table.querySelectorAll('td[data-cell][tabindex="0"]'), function (x) { x.tabIndex = -1; });
    td.tabIndex = 0;
    if (focus) td.focus();
  }
  table.addEventListener("keydown", function (e) {
    var td = cellOf(e.target);
    if (!td || e.target !== td) return;
    var grid = cells2d(), r = -1, c = -1;
    grid.forEach(function (row, i) { var j = row.indexOf(td); if (j >= 0) { r = i; c = j; } });
    var d = { ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1] }[e.key];
    if (d) {
      e.preventDefault();
      var nr = Math.max(0, Math.min(grid.length - 1, r + d[0]));
      var row = grid[nr], nc = Math.max(0, Math.min(row.length - 1, c + d[1]));
      setFocus(row[nc], true);
      row[nc].scrollIntoView({ block: "nearest", inline: "nearest" });
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault(); openPop(td, "1");
    } else if ((e.key === "Delete" || e.key === "Backspace") && currentValue(td, "1")) {
      e.preventDefault();
      post({ action: "set", user: td.getAttribute("data-u"), date: td.getAttribute("data-d"), slot: "1", value: "" }, [td]);
    }
  });

  /* ---------------------------------------------------------------- drag a film to another cell */
  var justDragged = false;
  table.addEventListener("pointerdown", function (e) {
    var chip = e.target.closest("[data-chip]");
    if (!chip || e.button !== 0) return;
    var src = cellOf(chip), sx = e.clientX, sy = e.clientY, dragging = false, ghost = null, over = null;
    function begin() {
      dragging = true;
      closePop(false);
      ghost = chip.cloneNode(true);
      ghost.classList.add("chip-ghost");
      ghost.style.width = chip.offsetWidth + "px";
      doc.body.appendChild(ghost);
      chip.classList.add("dragging");
      root.classList.add("is-dragging");
    }
    function onMove(ev) {
      if (!dragging) {
        if (Math.abs(ev.clientX - sx) + Math.abs(ev.clientY - sy) < 6) return;
        begin();
      }
      ev.preventDefault();
      ghost.style.left = ev.clientX + 8 + "px";
      ghost.style.top = ev.clientY + 8 + "px";
      ghost.classList.toggle("copy", ev.altKey || ev.ctrlKey || ev.metaKey);
      var under = doc.elementFromPoint(ev.clientX, ev.clientY), td = cellOf(under);
      if (td !== over) {
        if (over) over.classList.remove("drop-ok", "drop-no");
        over = td;
        if (over && over !== src) over.classList.add(over.classList.contains("locked") ? "drop-no" : "drop-ok");
      }
      // auto-scroll the grid near its edges
      var sc = root.querySelector(".planner-scroll"), r = sc.getBoundingClientRect();
      if (ev.clientX > r.right - 40) sc.scrollLeft += 16; else if (ev.clientX < r.left + 40) sc.scrollLeft -= 16;
      if (ev.clientY > window.innerHeight - 40) window.scrollBy(0, 16); else if (ev.clientY < 40) window.scrollBy(0, -16);
    }
    function onUp(ev) {
      doc.removeEventListener("pointermove", onMove);
      doc.removeEventListener("pointerup", onUp);
      doc.removeEventListener("pointercancel", onUp);
      if (!dragging) return;
      ghost.remove();
      chip.classList.remove("dragging");
      root.classList.remove("is-dragging");
      if (over) over.classList.remove("drop-ok", "drop-no");
      justDragged = true; setTimeout(function () { justDragged = false; }, 50);
      if (!over || over === src || over.classList.contains("locked") || ev.type === "pointercancel") return;
      var copy = ev.altKey || ev.ctrlKey || ev.metaKey;
      post({ action: "move", from_user: src.getAttribute("data-u"), from_date: src.getAttribute("data-d"),
             from_slot: chip.getAttribute("data-slot"), to_user: over.getAttribute("data-u"),
             to_date: over.getAttribute("data-d"), copy: copy ? "1" : "0" }, [src, over]);
    }
    doc.addEventListener("pointermove", onMove);
    doc.addEventListener("pointerup", onUp);
    doc.addEventListener("pointercancel", onUp);
  });
  // stop the browser's own drag of buttons/text
  table.addEventListener("dragstart", function (e) { if (e.target.closest && e.target.closest("[data-chip]")) e.preventDefault(); });

  /* Keep today in view on the 4-week grid */
  var todayRow = table.querySelector("tr.is-today");
  if (todayRow && view === "weeks" && todayRow.getBoundingClientRect().top > window.innerHeight) {
    todayRow.scrollIntoView({ block: "center" });
  }
  var todayCol = table.querySelector("thead th.today");
  if (todayCol && view === "month") {
    var sc = root.querySelector(".planner-scroll");
    // only scroll when today would otherwise be off to the right
    if (todayCol.offsetLeft + todayCol.offsetWidth > sc.clientWidth) sc.scrollLeft = Math.max(0, todayCol.offsetLeft - 260);
  }
})();
