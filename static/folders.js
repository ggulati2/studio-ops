/* Instant filtering of folder lists (drive page, Projects inbox): type to search,
   click a status to narrow down. Works on content that live.js redraws too. No inline JS (CSP). */
(function () {
  "use strict";
  var doc = document;

  function norm(s) { return (s || "").toLowerCase().replace(/[_\-.\\/]+/g, " ").replace(/\s+/g, " ").trim(); }

  function apply(box) {
    var qEl = box.querySelector("[data-ff-q]");
    var words = norm(qEl && qEl.value).split(" ").filter(Boolean);
    var on = box.querySelector("[data-ff-status].on");
    var status = on ? on.getAttribute("data-ff-status") : "";
    var dEl = box.querySelector("[data-ff-drive]");
    var drive = dEl ? dEl.value : "";
    var shown = 0, items = box.querySelectorAll("[data-ff-item]");
    Array.prototype.forEach.call(items, function (it) {
      if (!it.dataset.ffText) it.dataset.ffText = norm(it.textContent + " " + (it.getAttribute("data-ff-extra") || ""));
      var ok = (!status || it.getAttribute("data-status") === status) &&
               (!drive || it.getAttribute("data-drive") === drive) &&
               words.every(function (w) { return it.dataset.ffText.indexOf(w) >= 0; });
      it.hidden = !ok;
      if (ok) shown++;
    });
    var out = box.querySelector("[data-ff-count]");
    if (out) out.textContent = shown === items.length ? items.length + " shown" : shown + " of " + items.length + " shown";
    var none = box.querySelector("[data-ff-none]");
    if (none) none.hidden = shown > 0 || !items.length;
  }

  doc.addEventListener("input", function (e) {
    var box = e.target.closest && e.target.closest("[data-ff]");
    if (box && e.target.matches("[data-ff-q]")) apply(box);
  });
  doc.addEventListener("change", function (e) {
    var box = e.target.closest && e.target.closest("[data-ff]");
    if (box && e.target.matches("[data-ff-drive]")) apply(box);
  });
  doc.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-ff-status]");
    if (!b) return;
    var box = b.closest("[data-ff]");
    Array.prototype.forEach.call(box.querySelectorAll("[data-ff-status]"), function (x) {
      x.classList.toggle("on", x === b);
      x.setAttribute("aria-pressed", x === b ? "true" : "false");
    });
    apply(box);
  });
  doc.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-ff-tickall]");
    if (!b) return;
    var box = b.closest("[data-ff]");
    var boxes = Array.prototype.filter.call(box.querySelectorAll("[data-ff-item]"), function (it) { return !it.hidden; })
      .map(function (it) { return it.querySelector('input[name="ids"]'); }).filter(Boolean);
    var to = boxes.some(function (c) { return !c.checked; });
    boxes.forEach(function (c) { c.checked = to; });
    b.textContent = to ? "Untick all" : "Tick all shown";
  });
  /* List view: hovering a drive shows its photos next to the thumbnail (fixed position, so the
     table's scroll box doesn't clip it) */
  var shown = null;
  function hidePop() { if (shown) { shown.classList.remove("show"); shown = null; } }
  doc.addEventListener("mouseover", function (e) {
    var row = e.target.closest && e.target.closest(".drive-row");
    var pop = row && row.querySelector(".ph-pop");
    if (pop === shown) return;
    hidePop();
    if (!pop) return;
    var t = row.querySelector(".ph-thumb").getBoundingClientRect();
    pop.classList.add("show");
    var r = pop.getBoundingClientRect();
    var top = Math.max(8, Math.min(t.top + t.height / 2 - r.height / 2, window.innerHeight - r.height - 8));
    var left = Math.min(t.right + 10, window.innerWidth - r.width - 8);
    pop.style.left = left + "px";
    pop.style.top = top + "px";
    shown = pop;
  });
  window.addEventListener("scroll", hidePop, true);

  /* Small confirm pop-overs (<details>): Cancel and clicking elsewhere close them */
  doc.addEventListener("click", function (e) {
    var c = e.target.closest && e.target.closest("[data-close-details]");
    if (c) { c.closest("details").open = false; return; }
    Array.prototype.forEach.call(doc.querySelectorAll("details.dc-del[open], details.ph-del[open]"), function (d) {
      if (!d.contains(e.target)) d.open = false;
    });
  });
  doc.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && e.target.matches && e.target.matches("[data-ff-q]") && e.target.value) {
      e.target.value = "";
      apply(e.target.closest("[data-ff]"));
    }
  });
  /* Redrawn content (after adding folders, a decision, or a filter change): keep the current filter */
  function all() { Array.prototype.forEach.call(doc.querySelectorAll("[data-ff]"), apply); }
  new MutationObserver(function (muts) {
    var hit = muts.some(function (m) {
      return Array.prototype.some.call(m.addedNodes, function (n) {
        return n.nodeType === 1 && (n.matches("[data-ff], [data-ff-item]") || n.querySelector("[data-ff-item]"));
      });
    });
    if (hit) all();
  }).observe(doc.body, { childList: true, subtree: true });
  if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", all); else all();
})();
