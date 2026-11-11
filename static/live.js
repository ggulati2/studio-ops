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
    return fetch(form.getAttribute("action") || form.action, {
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
      /* Other pieces of the page the server re-drew (e.g. a couple block and its summary lines) */
      (res.swap || []).forEach(function (sw) {
        var el = doc.querySelector(sw.sel);
        if (!el) return;
        if (sw.mode === "outer") {
          var t = doc.createElement("template");
          t.innerHTML = sw.html.trim();
          var n = t.content.firstElementChild;
          if (!n) return;
          el.parentNode.replaceChild(n, el);
          enhance(n);
          n.classList.add("row-flash");
          setTimeout(function () { n.classList.remove("row-flash"); }, 1100);
        } else {
          el.innerHTML = sw.html;
        }
      });
      if (res.refresh) {
        if (window.StudioLive) window.StudioLive.reload(); else setTimeout(function () { location.reload(); }, 600);
      }
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
    var body;
    if (e.submitter && e.submitter.name) {     // which button was pressed (e.g. Add vs Decline)
      body = new FormData(form);
      body.append(e.submitter.name, e.submitter.value);
    }
    send(form, body).catch(function (err) {
      form.classList.remove("is-saving");
      toast(err.message || "Couldn't save.", { level: "error" });
    });
  });

  /* ---------------------------------------------------------------- Team: pod per person */
  function podPost(form, data) {
    var fd = new FormData();
    fd.append("csrfmiddlewaretoken", csrfOf(form));
    Object.keys(data).forEach(function (k) { fd.append(k, data[k]); });
    form.classList.add("is-saving");
    return fetch(form.action, { method: "POST", body: fd, credentials: "same-origin",
      headers: { "X-Requested-With": "fetch", "X-CSRFToken": csrfOf(form), "Accept": "application/json" } })
      .then(function (r) {
        if (r.status === 403) throw new Error("Your session has ended. Reload the page and sign in again.");
        if (!r.ok) throw new Error("Couldn't save (" + r.status + ").");
        return r.json();
      })
      .then(function (res) {
        form.classList.remove("is-saving");
        syncPodOptions(res.pods);
        var sel = form.querySelector("[data-pod-select]");
        if (res.ok) {
          sel.value = res.pod_id == null ? "" : String(res.pod_id);
          sel.setAttribute("data-current", sel.value);
          sel.classList.toggle("empty", !sel.value);
          var row = form.closest("tr");
          if (row && res.message) { row.classList.add("row-flash"); setTimeout(function () { row.classList.remove("row-flash"); }, 1100); }
          closeNew(form);
        } else {
          sel.value = sel.getAttribute("data-current") || "";
        }
        if (res.message) toast(res.message, { level: res.ok ? "success" : "error",
          undo: res.ok && res.undo ? function () { podPost(form, res.undo); } : null });
        return res;
      })
      .catch(function (err) {
        form.classList.remove("is-saving");
        var sel = form.querySelector("[data-pod-select]");
        sel.value = sel.getAttribute("data-current") || "";
        toast(err.message, { level: "error" });
      });
  }
  /* A pod created on one row appears in every row's list straight away */
  function syncPodOptions(pods) {
    if (!pods) return;
    Array.prototype.forEach.call(doc.querySelectorAll("[data-pod-select]"), function (sel) {
      var keep = sel.value, newOpt = sel.querySelector('option[value="__new__"]');
      Array.prototype.forEach.call(sel.querySelectorAll("option"), function (o) {
        if (o.value && o.value !== "__new__") o.remove();
      });
      pods.forEach(function (p) {
        var o = doc.createElement("option"); o.value = p.id; o.textContent = p.name;
        sel.insertBefore(o, newOpt);
      });
      sel.value = keep;
    });
  }
  function openNew(form) {
    var box = form.querySelector("[data-pod-new]"), sel = form.querySelector("[data-pod-select]");
    sel.hidden = true; box.hidden = false;
    var inp = box.querySelector("input"); inp.value = ""; inp.focus();
  }
  function closeNew(form) {
    var box = form.querySelector("[data-pod-new]"), sel = form.querySelector("[data-pod-select]");
    if (!box || box.hidden) return;
    box.hidden = true; sel.hidden = false;
    sel.value = sel.getAttribute("data-current") || "";
  }
  Array.prototype.forEach.call(doc.querySelectorAll("[data-pod-select]"), function (sel) { sel.setAttribute("data-current", sel.value); });
  doc.addEventListener("change", function (e) {
    var sel = e.target.closest && e.target.closest("[data-pod-select]");
    if (!sel) return;
    var form = sel.form;
    if (sel.value === "__new__") { openNew(form); return; }
    podPost(form, { pod: sel.value });
  });
  doc.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form.matches || !form.matches("form[data-pod-form]") || !window.fetch) return;
    e.preventDefault();
    var box = form.querySelector("[data-pod-new]");
    if (box && !box.hidden) {
      var name = box.querySelector("input").value.trim();
      if (!name) { box.querySelector("input").focus(); return; }
      podPost(form, { pod: "__new__", new_pod: name });
    } else {
      podPost(form, { pod: form.querySelector("[data-pod-select]").value });
    }
  });
  doc.addEventListener("click", function (e) {
    var c = e.target.closest("[data-pod-cancel]");
    if (c) closeNew(c.closest("form"));
  });
  doc.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && e.target.closest && e.target.closest("[data-pod-new]")) closeNew(e.target.closest("form"));
  });

  /* ---------------------------------------------------------------- live filters */
  var filterForm = doc.querySelector("form[data-live-filter]");
  if (!filterForm || !window.fetch || !window.DOMParser) return;
  var statusDefault = filterForm.hasAttribute("data-status-default") ? filterForm.getAttribute("data-status-default") : "open";
  var pending = null;

  /* A thin progress line at the top; content only dims if the answer takes a moment */
  var bar = doc.createElement("div");
  bar.className = "live-bar";
  bar.setAttribute("aria-hidden", "true");
  doc.body.appendChild(bar);
  var dimTimer = null;

  function syncAttrs(el, from) {
    Array.prototype.slice.call(el.attributes).forEach(function (a) { if (!from.hasAttribute(a.name)) el.removeAttribute(a.name); });
    Array.prototype.forEach.call(from.attributes, function (a) { el.setAttribute(a.name, a.value); });
  }

  /* Keep the filter fields in step with a URL (chips, Back/Forward) */
  function syncForm(url) {
    var params = new URL(url, location.href).searchParams;
    Array.prototype.forEach.call(filterForm.elements, function (el) {
      if (!el.name || el.type === "submit" || el.tagName === "BUTTON") return;
      var v = params.get(el.name) || "";
      if (el.name === "status" && !params.has("status")) v = statusDefault;
      if (el.tagName === "SELECT") {
        el.value = v;
        if (el.selectedIndex < 0) el.selectedIndex = 0;
      } else if (el !== doc.activeElement) {
        el.value = v;
      }
    });
  }

  function load(url, push) {
    if (pending) pending.abort();
    var mine = pending = window.AbortController ? new AbortController() : null;
    var parts = doc.querySelectorAll("[data-live-part]");
    bar.className = "live-bar is-on";
    clearTimeout(dimTimer);
    dimTimer = setTimeout(function () {
      Array.prototype.forEach.call(parts, function (p) { p.classList.add("live-loading"); });
    }, 160);
    if (push) history.pushState({ live: true }, "", url);     // the address changes at once, like a real link
    return fetch(url, { credentials: "same-origin", signal: mine && mine.signal, headers: { "X-Requested-With": "fetch-page" } })
      .then(function (r) { if (!r.ok || r.redirected && r.url.indexOf("/login") >= 0) throw new Error("reload"); return r.text(); })
      .then(function (html) {
        var next = new DOMParser().parseFromString(html, "text/html");
        clearTimeout(dimTimer);
        Array.prototype.forEach.call(doc.querySelectorAll("[data-live-part]"), function (p) {
          var n = next.querySelector('[data-live-part="' + p.getAttribute("data-live-part") + '"]');
          p.classList.remove("live-loading");
          if (!n) return;
          if (p.innerHTML === n.innerHTML) return;          // nothing changed here: leave it alone (no flicker)
          // tables.js inserts its toolbar just before the table's scroll box; drop the old one
          if (p.getAttribute("data-live-part") === "table") {
            var tb = p.previousElementSibling;
            if (tb && tb.classList.contains("cols-bar")) tb.remove();
          }
          syncAttrs(p, n);              // list and grouped views use different wrappers
          p.innerHTML = n.innerHTML;
          enhance(p);
          if (p.getAttribute("data-live-part") === "table") {
            p.classList.remove("live-in");
            void p.offsetWidth;
            p.classList.add("live-in");
          }
        });
        if (window.StudioTables) window.StudioTables.init();
        doc.dispatchEvent(new CustomEvent("studio:parts"));
        filterForm.classList.remove("is-saving");
        if (!push) history.replaceState({ live: true }, "", url);
        bar.className = "live-bar is-done";
      })
      .catch(function (err) { if (err && err.name === "AbortError") return; location.href = url; })
      .then(function () { if (pending === mine) pending = null; });
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
    var a = e.target.closest('[data-live-part="chips"] a, [data-live-part="clear"] a, [data-live-part="viewswitch"] a');
    if (!a || e.metaKey || e.ctrlKey || e.shiftKey || e.button) return;
    e.preventDefault();
    // keep the filter form in step with the chip's status
    var url = new URL(a.href, location.href);
    var st = filterForm.querySelector('input[name="status"]');
    if (st) st.value = url.searchParams.has("status") ? url.searchParams.get("status") : statusDefault;
    var vw = filterForm.querySelector('input[name="view"]');
    if (vw) vw.value = url.searchParams.get("view") || "";
    // show the choice straight away; the list follows a moment later
    var group = a.closest("[data-live-part]");
    if (group && !group.matches('[data-live-part="clear"]')) {
      Array.prototype.forEach.call(group.querySelectorAll("a"), function (x) { x.classList.toggle("on", x === a); });
    }
    if (a.closest('[data-live-part="clear"]')) {
      Array.prototype.forEach.call(filterForm.querySelectorAll("select, input[type=search]"), function (el) {
        if (el.tagName === "SELECT") el.selectedIndex = 0; else el.value = "";
      });
    }
    load(url.pathname + url.search, true);
  });
  /* Back / Forward: re-draw in place too */
  window.addEventListener("popstate", function () {
    syncForm(location.href);
    load(location.pathname + location.search, false);
  });
  window.StudioLive = { reload: function () { return load(location.pathname + location.search, false); } };
})();
