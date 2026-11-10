/* My month: fill a "Not logged" day in a pop-up, without leaving the page.
   Click the day (or "Log them now") → the form loads into a <dialog> → Save posts in
   the background → the day's square, the month totals and the reminder bar update.
   "Save & next day" moves straight on to the next missed day. No inline JS (CSP). */
(function () {
  "use strict";
  var doc = document;
  var dlg = doc.querySelector("[data-quickday]");
  if (!dlg || !window.fetch) return;
  var body = dlg.querySelector("[data-qd-body]");
  var toast = window.StudioToast || function () {};
  var current = null, opener = null;

  function missingDays() {
    return Array.prototype.map.call(doc.querySelectorAll("[data-quicklog]"), function (a) { return a; });
  }
  function nextAfter(day) {
    var list = missingDays();
    for (var i = 0; i < list.length; i++) if (list[i].getAttribute("data-day") > day) return list[i];
    return list[0] && list[0].getAttribute("data-day") !== day ? list[0] : null;
  }

  function wire() {
    var form = body.querySelector("[data-quickday-form]");
    if (!form) return;
    // Show the times / jobs only for the day types that need them
    var NEEDS_TIMES = ["office", "wfh", "half", "downtime"], NEEDS_JOBS = ["office", "wfh", "half"];
    function sync() {
      var t = (form.querySelector('input[name="day_type"]:checked') || {}).value || "";
      var times = form.querySelector("[data-qd-times]"), jobs = form.querySelector("[data-qd-jobs]");
      times.hidden = NEEDS_TIMES.indexOf(t) < 0;
      jobs.hidden = NEEDS_TIMES.indexOf(t) < 0;
      var star = jobs.querySelector(".req");
      if (star) star.hidden = NEEDS_JOBS.indexOf(t) < 0;
      hours();
    }
    function hours() {
      var a = form.querySelector('input[name="time_in"]').value, b = form.querySelector('input[name="time_out"]').value;
      var out = form.querySelector("[data-qd-hours]");
      if (!a || !b) { out.textContent = ""; return; }
      var m = function (s) { var p = s.split(":"); return +p[0] * 60 + +p[1]; };
      var mins = m(b) - m(a); if (mins <= 0) mins += 1440;
      var h = mins / 60, brk = parseFloat(dlg.getAttribute("data-break") || "60") / 60;
      var prod = h > 5 ? h - brk : h;
      out.textContent = fmt(h) + " h in office · " + fmt(prod) + " h productive";
    }
    function fmt(x) { return (Math.round(x * 4) / 4).toString(); }
    form.addEventListener("change", function (e) {
      if (e.target.name === "day_type") sync();
      if (e.target.name === "time_in" || e.target.name === "time_out") hours();
    });
    form.addEventListener("input", function (e) { if (e.target.type === "time") hours(); });
    sync();
    var nextBtn = form.querySelector("[data-qd-next]");
    if (nextBtn) nextBtn.hidden = !nextAfter(current);
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var then = e.submitter && e.submitter.value === "next" ? "next" : "close";
      save(form, then);
    });
    // Focus the first thing to fill: the first error, else the day type
    var err = form.querySelector(".err, .msg.error");
    var focusEl = err ? (err.closest(".field, fieldset, .qd-sec") || form).querySelector("input, select") : form.querySelector('input[name="day_type"]:checked') || form.querySelector("input, select");
    if (focusEl) setTimeout(function () { focusEl.focus(); }, 30);
  }

  function load(url, day) {
    current = day;
    body.innerHTML = '<div class="qd-loading"><span class="spin" aria-hidden="true"></span>Loading…</div>';
    if (!dlg.open) dlg.showModal();
    dlg.classList.add("in");
    return fetch(url, { credentials: "same-origin", headers: { "X-Requested-With": "fetch-page" } })
      .then(function (r) { if (!r.ok) throw new Error(); return r.text(); })
      .then(function (html) {
        var parsed = new DOMParser().parseFromString(html, "text/html");
        var form = parsed.querySelector("[data-quickday-form]");
        if (!form) throw new Error();
        body.innerHTML = "";
        body.appendChild(doc.importNode(form, true));
        wire();
      })
      .catch(function () { location.href = url; });
  }

  function save(form, then) {
    var btns = form.querySelectorAll("button");
    Array.prototype.forEach.call(btns, function (b) { b.disabled = true; });
    form.classList.add("is-saving");
    fetch(form.action, {
      method: "POST", body: new FormData(form), credentials: "same-origin",
      headers: { "X-Requested-With": "fetch", "Accept": "application/json" }
    }).then(function (r) {
      if (r.status === 403) throw new Error("Your session has ended. Reload the page and sign in again.");
      if (!r.ok) throw new Error("Couldn't save (" + r.status + "). Check your connection and try again.");
      return r.json();
    }).then(function (res) {
      if (!res.ok) {
        if (res.html) { body.innerHTML = res.html; wire(); }
        else toast(res.error || "Couldn't save.", { level: "error" });
        Array.prototype.forEach.call(btns, function (b) { b.disabled = false; });
        return;
      }
      // Swap the day's square
      var old = doc.querySelector('.cal [data-day="' + res.date + '"]');
      if (old) {
        var t = doc.createElement("div"); t.innerHTML = res.cell.trim();
        var cell = t.firstElementChild; old.parentNode.replaceChild(cell, old);
        cell.classList.add("just-logged");
        setTimeout(function () { cell.classList.remove("just-logged"); }, 1400);
      }
      var k = doc.querySelector("[data-month-kpis]"); if (k) k.innerHTML = res.kpis;
      updateBar();
      toast(res.message);
      var nxt = then === "next" ? nextAfter(res.date) : null;
      if (nxt) { load(nxt.getAttribute("data-quicklog"), nxt.getAttribute("data-day")); }
      else close();
    }).catch(function (err) {
      Array.prototype.forEach.call(btns, function (b) { b.disabled = false; });
      form.classList.remove("is-saving");
      toast(err.message || "Couldn't save.", { level: "error" });
    });
  }

  function updateBar() {
    var n = missingDays().length, bar = doc.querySelector("[data-missing-bar]");
    if (!bar) return;
    bar.hidden = !n;
    bar.querySelector("[data-missing-count]").textContent = n;
    bar.querySelector("[data-missing-s]").textContent = n === 1 ? "" : "s";
    bar.querySelector("[data-log-first]").textContent = n > 1 ? "Log them now" : "Log it now";
  }

  function close() {
    dlg.classList.remove("in");
    if (dlg.open) dlg.close();
  }
  dlg.addEventListener("close", function () {
    body.innerHTML = "";
    current = null;
    if (opener && doc.body.contains(opener)) opener.focus();
  });
  dlg.addEventListener("click", function (e) {
    if (e.target.closest("[data-qd-close]")) { close(); return; }
    // click on the backdrop closes
    if (e.target === dlg) close();
  });

  doc.addEventListener("click", function (e) {
    var a = e.target.closest("[data-quicklog]");
    var first = e.target.closest("[data-log-first]");
    if (first) { a = missingDays()[0]; if (!a) return; }
    if (!a || e.metaKey || e.ctrlKey || e.shiftKey) return;
    e.preventDefault();
    opener = a;
    load(a.getAttribute("data-quicklog"), a.getAttribute("data-day"));
  });
})();
