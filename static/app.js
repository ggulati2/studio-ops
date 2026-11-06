/* Studio Ops · small progressive enhancements. The app works without JS;
   this file only adds comfort (menus, auto-submit, confirmations, mobile tables).
   No inline scripts anywhere, so the Content-Security-Policy can forbid them. */
(function () {
  "use strict";
  var doc = document;

  function ready(fn) {
    if (doc.readyState !== "loading") fn(); else doc.addEventListener("DOMContentLoaded", fn);
  }

  ready(function () {
    doc.documentElement.classList.add("js");

    /* Select boxes that apply as soon as you pick something */
    doc.addEventListener("change", function (e) {
      var el = e.target.closest("[data-autosubmit]");
      if (el && el.form) {
        el.form.classList.add("is-saving");
        if (el.form.requestSubmit) el.form.requestSubmit(); else el.form.submit();
      }
    });

    /* Ask before anything that can't be undone */
    doc.addEventListener("click", function (e) {
      var c = e.target.closest("[data-confirm]");
      if (c && !window.confirm(c.getAttribute("data-confirm"))) { e.preventDefault(); e.stopPropagation(); return; }
      if (e.target.closest("[data-print]")) { e.preventDefault(); window.print(); return; }
      var back = e.target.closest("[data-back]");
      if (back && window.history.length > 1 && doc.referrer && new URL(doc.referrer).origin === location.origin) {
        e.preventDefault(); window.history.back();
      }
    });

    /* One submit per click: stops double invoices / double reviews on slow connections */
    doc.addEventListener("submit", function (e) {
      var f = e.target;
      if (f.method && f.method.toLowerCase() === "post") {
        if (f.dataset.sent === "1") { e.preventDefault(); return; }
        f.dataset.sent = "1";
        setTimeout(function () { f.dataset.sent = ""; }, 4000);
      }
    });

    /* Mobile / tablet menu drawer */
    var toggle = doc.querySelector("[data-nav-toggle]");
    var side = doc.getElementById("side");
    var scrim = doc.querySelector(".scrim");
    function setOpen(open) {
      if (!toggle || !side) return;
      doc.body.classList.toggle("nav-open", open);
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      if (open) { var first = side.querySelector("nav a, a, button"); if (first) first.focus(); }
      else toggle.focus({ preventScroll: true });
    }
    if (toggle && side) {
      toggle.addEventListener("click", function () { setOpen(!doc.body.classList.contains("nav-open")); });
      if (scrim) scrim.addEventListener("click", function () { setOpen(false); });
      doc.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && doc.body.classList.contains("nav-open")) setOpen(false);
      });
      window.matchMedia("(min-width: 1080px)").addEventListener("change", function (m) { if (m.matches) doc.body.classList.remove("nav-open"); });
    }

    /* Tables: on phones each row becomes a card; label every cell with its column name */
    Array.prototype.forEach.call(doc.querySelectorAll("main table"), function (t) {
      if (t.closest(".plan, .no-stack, .cal")) return;
      var heads = Array.prototype.map.call(t.querySelectorAll("thead th"), function (th) { return th.textContent.trim(); });
      if (!heads.length) return;
      t.classList.add("stack");
      Array.prototype.forEach.call(t.querySelectorAll("tbody tr, tfoot tr"), function (tr) {
        var i = 0;
        Array.prototype.forEach.call(tr.children, function (td) {
          if (heads[i] && !td.hasAttribute("data-label")) td.setAttribute("data-label", heads[i]);
          i += parseInt(td.getAttribute("colspan") || "1", 10);
        });
      });
    });

    /* Wide grids (planner, calendar): show a hint while there is more to scroll */
    Array.prototype.forEach.call(doc.querySelectorAll(".scroll"), function (s) {
      function upd() {
        s.classList.toggle("more-right", s.scrollLeft + s.clientWidth < s.scrollWidth - 4);
        s.classList.toggle("more-left", s.scrollLeft > 4);
      }
      s.addEventListener("scroll", upd, { passive: true });
      window.addEventListener("resize", upd);
      upd();
    });

    /* Messages fade after a while (errors stay) */
    Array.prototype.forEach.call(doc.querySelectorAll(".msg:not(.error)"), function (m) {
      setTimeout(function () { m.classList.add("gone"); }, 6000);
    });
  });
})();
