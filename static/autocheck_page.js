/* AutoCheck page: pick any film (search as you type), its events and latest versions fill in by
   themselves; Run hands it to the QC Helper (autocheck.js does the rest). No inline JS (CSP). */
(function () {
  "use strict";
  var doc = document;
  function init() {
    var box = doc.querySelector(".ac-any-box");
    if (!box || box.dataset.pageReady) return;
    box.dataset.pageReady = "1";
    var sel = box.querySelector("[data-film-select]"), search = box.querySelector("[data-film-search]"),
        ev = box.querySelector("[data-event-select]"), v1 = box.querySelector("[name=v1_link]"), v2 = box.querySelector("[name=v2_link]"),
        note = box.querySelector("[data-links-note]"), l1 = box.querySelector("[data-v1-label]"), l2 = box.querySelector("[data-v2-label]");
    var all = Array.prototype.slice.call(sel.options, 1).map(function (o) { return {v: o.value, t: o.textContent, low: o.textContent.toLowerCase()}; });
    var keepEvent = null;

    search.addEventListener("input", function () {
      var words = search.value.toLowerCase().split(/\s+/).filter(Boolean), cur = sel.value;
      while (sel.options.length > 1) sel.remove(1);
      var hits = all.filter(function (f) { return words.every(function (w) { return f.low.indexOf(w) >= 0; }); });
      hits.slice(0, 300).forEach(function (f) { var o = doc.createElement("option"); o.value = f.v; o.textContent = f.t; sel.appendChild(o); });
      if (hits.some(function (f) { return f.v === cur; })) sel.value = cur;
      else if (hits.length === 1) { sel.value = hits[0].v; links(); }
    });

    function links() {
      var p = sel.value;
      if (!p) { box.setAttribute("data-start", ""); ev.disabled = true; return; }
      var url = box.getAttribute("data-links") + "?p=" + encodeURIComponent(p) + (ev.value && !ev.disabled ? "&event=" + encodeURIComponent(ev.value) : "");
      note.textContent = "Looking up its versions…";
      fetch(url, {credentials: "same-origin"}).then(function (r) { return r.json(); }).then(function (d) {
        box.setAttribute("data-start", d.start);
        if (ev.dataset.film !== p) {                 // a new film: its events
          ev.dataset.film = p;
          while (ev.options.length > 1) ev.remove(1);
          d.events.forEach(function (e) { var o = doc.createElement("option"); o.value = e.id; o.textContent = e.name + " · " + e.status; ev.appendChild(o); });
          ev.disabled = !d.events.length;
          if (keepEvent) { ev.value = keepEvent; keepEvent = null; if (ev.value) return links(); }
        }
        v1.value = d.v1; v2.value = d.v2;
        l1.textContent = d.v1_label ? "· " + d.v1_label : ""; l2.textContent = d.v2_label ? "· " + d.v2_label : "";
        note.textContent = d.note || (d.v1 ? "Checks " + d.v2_label + " against " + d.v1_label + "'s comments, plus a hygiene pass." : "Only one version so far: a hygiene pass on it.");
      }).catch(function () { note.textContent = "Couldn't look up its versions. Paste the links."; });
    }
    sel.addEventListener("change", function () { ev.value = ""; links(); });
    ev.addEventListener("change", links);
    box.querySelector(".ac-form").addEventListener("submit", function (e) {
      if (!box.getAttribute("data-start")) { e.preventDefault(); e.stopImmediatePropagation(); sel.focus(); if (window.StudioToast) window.StudioToast("Pick the film first.", {level: "error"}); }
    }, true);
    // "Check again" in the list: fill the form with that film / event and scroll up
    doc.addEventListener("click", function (e) {
      var a = e.target.closest && e.target.closest("[data-ac-again]");
      if (!a) return;
      e.preventDefault();
      search.value = ""; search.dispatchEvent(new Event("input"));
      sel.value = a.getAttribute("data-ac-again"); keepEvent = a.getAttribute("data-event") || null; ev.dataset.film = "";
      links();
      box.scrollIntoView({behavior: "smooth", block: "start"});
    });
    if (sel.value) links();
  }
  if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", init); else init();
})();
