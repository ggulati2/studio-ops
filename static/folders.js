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
