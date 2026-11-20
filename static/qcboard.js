/* QC status board: click a film's name to open its details right under its row (versions, events,
   AutoCheck, history), click again to fold it. Loaded in the background, no page reload; films
   you opened stay open when the board is redrawn after a pass / fail. No inline JS (CSP). */
(function () {
  "use strict";
  var doc = document, open = {}, cache = {};

  function cols(tr) { return tr.children.length || 1; }

  function detailRow(tr, pk) {
    var d = tr.nextElementSibling;
    if (d && d.getAttribute("data-qc-detail") === pk) return d;
    d = doc.createElement("tr");
    d.className = "qc-detail";
    d.setAttribute("data-qc-detail", pk);
    d.innerHTML = '<td colspan="' + cols(tr) + '"><div class="qc-fold"><div class="qc-fold-in"><div class="qc-fold-body">' +
                  '<span class="small muted qc-loading">Loading…</span></div></div></div></td>';
    tr.parentNode.insertBefore(d, tr.nextSibling);
    return d;
  }

  function fill(d, html) {
    var body = d.querySelector(".qc-fold-body");
    body.innerHTML = html;
    doc.dispatchEvent(new CustomEvent("studio:parts"));      // AutoCheck boxes inside come alive
  }

  function load(link, d, pk, quiet) {
    var url = link.getAttribute("data-url");
    if (cache[pk] && !quiet) fill(d, cache[pk]);
    return fetch(url, {credentials: "same-origin", headers: {"X-Requested-With": "fetch"}})
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
      .then(function (html) { cache[pk] = html; fill(d, html); })
      .catch(function () {
        if (!cache[pk]) fill(d, '<span class="small" style="color:var(--bad)">Couldn\'t load this film. <a href="' + link.getAttribute("href") + '">Open it here</a>.</span>');
      });
  }

  function setOpen(link, want, quiet) {
    var pk = link.getAttribute("data-qc-film"), tr = link.closest("tr");
    if (!tr) return;
    var d = detailRow(tr, pk);
    link.setAttribute("aria-expanded", want ? "true" : "false");
    tr.classList.toggle("qc-row-open", want);
    if (want) {
      open[pk] = true;
      load(link, d, pk, quiet);
      requestAnimationFrame(function () { requestAnimationFrame(function () { d.classList.add("is-open"); }); });
    } else {
      delete open[pk];
      d.classList.remove("is-open");
      setTimeout(function () { if (!d.classList.contains("is-open") && d.parentNode) d.parentNode.removeChild(d); }, 320);
    }
  }

  doc.addEventListener("click", function (e) {
    var link = e.target.closest && e.target.closest("a[data-qc-film]");
    if (!link || e.metaKey || e.ctrlKey || e.shiftKey || e.button) return;     // new tab: the full page
    e.preventDefault();
    setOpen(link, link.getAttribute("aria-expanded") !== "true");
  });

  // the board was redrawn (a pass / fail, a filter): open again what was open, without a flash
  function reopen() {
    Object.keys(open).forEach(function (pk) {
      var link = doc.querySelector('a[data-qc-film="' + pk + '"]');
      if (!link || link.getAttribute("aria-expanded") === "true") return;
      var tr = link.closest("tr"), d = detailRow(tr, pk);
      if (cache[pk]) fill(d, cache[pk]);
      d.classList.add("is-open", "no-anim");
      link.setAttribute("aria-expanded", "true");
      tr.classList.add("qc-row-open");
      load(link, d, pk, true);
      setTimeout(function () { d.classList.remove("no-anim"); }, 50);
    });
  }
  var busy = false;
  doc.addEventListener("studio:parts", function () {
    if (busy) return;
    busy = true;
    setTimeout(function () { reopen(); busy = false; }, 0);
  });
})();
