/* Saving without page reloads.
   - <form data-live> posts in the background; the server answers JSON
     {ok, messages:[{level,text}], row, pk, undo, remove:[selectors]}. The row is
     re-drawn in place (keeping the user's column order), and a toast confirms the
     change with Undo.
   - <form data-live-filter> (GET) and links inside [data-live-part="chips"] fetch the
     page and swap only the [data-live-part] blocks, so filtering is instant too.
   Falls back to a normal submit if anything goes wrong. No inline JS (CSP). */
(function () {
  "use strict";
  var doc = document;

  /* ---------------------------------------------------------------- toasts */
  var stack = null;
  function toast(msg, opts) {
    opts = opts || {};
    if (!stack) {
      stack = doc.createElement("div");
      stack.className = "toasts";
      stack.setAttribute("role", "status");
      stack.setAttribute("aria-live", "polite");
      doc.body.appendChild(stack);
    }
    var t = doc.createElement("div");
    t.className = "toast" + (opts.level === "error" ? " error" : opts.level === "warning" ? " warning" : "");
    var span = doc.createElement("span"); span.textContent = msg; t.appendChild(span);
    if (opts.undo) {
      var b = doc.createElement("button"); b.type = "button"; b.className = "toast-undo"; b.textContent = "Undo";
      b.addEventListener("click", function () { t.remove(); opts.undo(); });
      t.appendChild(b);
    }
    stack.appendChild(t);
    requestAnimationFrame(function () { t.classList.add("in"); });
    setTimeout(function () { t.classList.remove("in"); setTimeout(function () { t.remove(); }, 250); },
      opts.level === "error" ? 8000 : opts.undo ? 7000 : 4500);
  }
  window.StudioToast = toast;

  function csrfOf(form) {
    var i = form.querySelector("input[name=csrfmiddlewaretoken]") || doc.querySelector("input[name=csrfmiddlewaretoken]");
    return i ? i.value : "";
  }

  /* Re-enhance a freshly inserted piece of page */
  function enhance(node) {
    if (window.StudioDatePicker) window.StudioDatePicker.enhance(node);
  }

  /* Put a new row's cells in the same order as the table's (possibly re-arranged) header */
  function alignRow(tr) {
    var table = tr.closest("table");
    var head = table && table.tHead && table.tHead.rows[0];
    if (!head) return;
    var byCol = {};
    Array.prototype.forEach.call(tr.cells, function (c) { if (c.dataset.col) byCol[c.dataset.col] = c; });
    Array.prototype.forEach.call(head.cells, function (th) { var c = byCol[th.dataset.col]; if (c) tr.appendChild(c); });
  }

  function swapRow(pk, html) {
    var old = doc.querySelector('tr[data-pk="' + pk + '"]');
    if (!old) return null;
    var tb = doc.createElement("tbody");
    tb.innerHTML = html.trim();
    var tr = tb.querySelector("tr");
    if (!tr) return null;
    old.parentNode.replaceChild(tr, old);
    alignRow(tr);
    enhance(tr);
    tr.classList.add("row-flash");
    setTimeout(function () { tr.classList.remove("row-flash"); }, 1100);
    return tr;
  }

  function send(form, data) {
    var body = data || new FormData(form);
    form.classList.add("is-saving");
    var row = form.closest("tr");
    if (row) row.classList.add("row-saving");
    return fetch(form.action, {
      method: "POST", credentials: "same-origin", body: body,
      headers: { "X-Requested-With": "fetch", "X-CSRFToken": csrfOf(form), "Accept": "application/json" }
    }).then(function (r) {
      if (r.status === 403) throw new Error("Your session has ended. Reload the page and sign in again.");
      if (!r.ok) throw new Error("Couldn't save (" + r.status + "). Check your connection and try again.");
      return r.json();
    }).then(function (res) {
      (res.remove || []).forEach(function (sel) {
        Array.prototype.forEach.call(doc.querySelectorAll(sel), function (n) {
          n.classList.add("fade-out");
          setTimeout(function () {
            var box = n.closest('[data-live-part="queue"]');
            n.remove();
            if (box && !box.querySelector("[data-queue]")) box.innerHTML = "";
            else if (box) { var h = box.querySelector("h2"); if (h) h.textContent = h.textContent.replace(/\d+/, function (n) { return Math.max(0, +n - 1); }); }
          }, 250);
        });
      });
      var tr = res.row ? swapRow(res.pk, res.row) : null;
      if (!tr && row) row.classList.remove("row-saving");
      var msgs = res.messages || [];
      msgs.forEach(function (m, i) {
        toast(m.text, { level: m.level, undo: (i === 0 && res.undo) ? function () { undo(res.undo_url || form.action, res.undo); } : null });
      });
      form.classList.remove("is-saving");
      return res;
    });
  }

  function undo(action, values) {
    var fd = new FormData();
    Object.keys(values).forEach(function (k) { fd.append(k, values[k]); });
    var f = doc.createElement("form");
    f.action = action;
    send(f, fd).catch(function (err) { toast(err.message, { level: "error" }); });
  }

  doc.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form.matches || !form.matches("form[data-live]") || !window.fetch) return;
    e.preventDefault();
    send(form).catch(function (err) {
      form.classList.remove("is-saving");
      toast(err.message || "Couldn't save.", { level: "error" });
    });
  });

  /* ---------------------------------------------------------------- live filters */
  var filterForm = doc.querySelector("form[data-live-filter]");
  if (!filterForm || !window.fetch || !window.DOMParser) return;
  var pending = null;

  function load(url, push) {
    if (pending) pending.abort();
    pending = window.AbortController ? new AbortController() : null;
    var parts = doc.querySelectorAll("[data-live-part]");
    Array.prototype.forEach.call(parts, function (p) { p.classList.add("live-loading"); });
    return fetch(url, { credentials: "same-origin", signal: pending && pending.signal, headers: { "X-Requested-With": "fetch-page" } })
      .then(function (r) { if (!r.ok || r.redirected && r.url.indexOf("/login") >= 0) throw new Error("reload"); return r.text(); })
      .then(function (html) {
        var next = new DOMParser().parseFromString(html, "text/html");
        Array.prototype.forEach.call(doc.querySelectorAll("[data-live-part]"), function (p) {
          var n = next.querySelector('[data-live-part="' + p.getAttribute("data-live-part") + '"]');
          if (!n) return;
          // tables.js inserts its toolbar just before the table's scroll box; drop the old one
          if (p.getAttribute("data-live-part") === "table") {
            var bar = p.previousElementSibling;
            if (bar && bar.classList.contains("cols-bar")) bar.remove();
          }
          p.innerHTML = n.innerHTML;
          p.classList.remove("live-loading");
          enhance(p);
        });
        if (window.StudioTables) window.StudioTables.init();
        filterForm.classList.remove("is-saving");
        if (push) history.pushState({ live: true }, "", url);
        else history.replaceState({ live: true }, "", url);
      })
      .catch(function (err) { if (err && err.name === "AbortError") return; location.href = url; })
      .then(function () { pending = null; });
  }

  function formUrl() {
    var fd = new FormData(filterForm), qs = new URLSearchParams();
    fd.forEach(function (v, k) { if (v !== "" || k === "status") qs.append(k, v); });
    return location.pathname + "?" + qs.toString();
  }

  filterForm.addEventListener("submit", function (e) { e.preventDefault(); load(formUrl(), true); });
  var q = filterForm.querySelector("[data-live-search]"), timer = null;
  if (q) q.addEventListener("input", function () {
    clearTimeout(timer);
    timer = setTimeout(function () { load(formUrl(), false); }, 280);
  });
  /* "Show more": append the next batch of rows instead of re-drawing the list */
  doc.addEventListener("click", function (e) {
    var a = e.target.closest("[data-live-more]");
    if (!a || e.metaKey || e.ctrlKey || e.shiftKey || e.button) return;
    e.preventDefault();
    if (a.classList.contains("is-loading")) return;
    a.classList.add("is-loading");
    a.textContent = "Loading…";
    var url = new URL(a.href, location.href);
    fetch(url.pathname + url.search, { credentials: "same-origin", headers: { "X-Requested-With": "fetch-page" } })
      .then(function (r) { if (!r.ok) throw new Error(); return r.text(); })
      .then(function (html) {
        var next = new DOMParser().parseFromString(html, "text/html");
        var body = doc.querySelector('[data-live-part="table"] tbody');
        var added = 0;
        Array.prototype.forEach.call(next.querySelectorAll('[data-live-part="table"] tbody tr[data-pk]'), function (tr) {
          if (body.querySelector('tr[data-pk="' + tr.getAttribute("data-pk") + '"]')) return;
          var row = doc.importNode(tr, true);
          body.appendChild(row);
          alignRow(row);
          added++;
        });
        ["more", "summary"].forEach(function (k) {
          var mine = doc.querySelector('[data-live-part="' + k + '"]'), theirs = next.querySelector('[data-live-part="' + k + '"]');
          if (mine && theirs) mine.innerHTML = theirs.innerHTML;
        });
        enhance(body);
        history.replaceState({ live: true }, "", url.pathname + url.search);
        toast(added + " more film" + (added === 1 ? "" : "s") + " shown.");
      })
      .catch(function () { location.href = a.href; });
  });

  doc.addEventListener("click", function (e) {
    var a = e.target.closest('[data-live-part="chips"] a, [data-live-part="clear"] a');
    if (!a || e.metaKey || e.ctrlKey || e.shiftKey || e.button) return;
    e.preventDefault();
    // keep the filter form in step with the chip's status
    var url = new URL(a.href, location.href);
    var st = filterForm.querySelector('input[name="status"]');
    if (st) st.value = url.searchParams.get("status") || "";
    if (a.closest('[data-live-part="clear"]')) {
      Array.prototype.forEach.call(filterForm.querySelectorAll("select, input[type=search]"), function (el) {
        if (el.tagName === "SELECT") el.selectedIndex = 0; else el.value = "";
      });
    }
    load(url.pathname + url.search, true);
  });
  window.addEventListener("popstate", function () { location.reload(); });
})();
