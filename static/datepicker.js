/* Studio Ops date picker.
   Replaces the browser's date / month pop-ups with one calendar that looks the same
   everywhere, works with mouse, touch and keyboard, and lets you type dates in plain
   words ("12 nov", "12/11", "tomorrow", "+3", "next mon").
   The original <input> stays in the form (as a hidden field with the same name and an
   ISO value), so the server sees exactly what it did before. No inline JS (CSP). */
(function () {
  "use strict";
  var doc = document;
  var MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  var MON = MONTHS.map(function (m) { return m.slice(0, 3); });
  var DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  var WD = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function iso(d) { return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()); }
  function isoMonth(d) { return d.getFullYear() + "-" + pad(d.getMonth() + 1); }
  function parseIso(s) {
    var m = /^(\d{4})-(\d{2})(?:-(\d{2}))?$/.exec(s || "");
    if (!m) return null;
    var d = new Date(+m[1], +m[2] - 1, m[3] ? +m[3] : 1);
    return isNaN(d) ? null : d;
  }
  function today() { var t = new Date(); return new Date(t.getFullYear(), t.getMonth(), t.getDate()); }
  function addDays(d, n) { return new Date(d.getFullYear(), d.getMonth(), d.getDate() + n); }
  function addMonths(d, n) {
    var t = new Date(d.getFullYear(), d.getMonth() + n, 1);
    var last = new Date(t.getFullYear(), t.getMonth() + 1, 0).getDate();
    t.setDate(Math.min(d.getDate(), last));
    return t;
  }
  function same(a, b) { return a && b && a.getTime() === b.getTime(); }

  function fmt(d, compact, month) {
    if (!d) return "";
    if (month) return MONTHS[d.getMonth()] + " " + d.getFullYear();
    if (compact) return d.getDate() + " " + MON[d.getMonth()] + (d.getFullYear() !== today().getFullYear() ? " " + d.getFullYear() : "");
    return WD[d.getDay()] + ", " + d.getDate() + " " + MON[d.getMonth()] + " " + d.getFullYear();
  }

  /* Typed dates, Indian order (day first). Returns a Date, null for empty, or false if unreadable. */
  function parseTyped(s, month) {
    s = (s || "").trim().toLowerCase().replace(/(\d)(st|nd|rd|th)\b/g, "$1").replace(/,/g, " ");
    if (!s) return null;
    var t = today(), m;
    if (!month) {
      if (s === "today" || s === "tod") return t;
      if (s === "tomorrow" || s === "tom" || s === "tmrw") return addDays(t, 1);
      if (s === "yesterday") return addDays(t, -1);
      if ((m = /^([+-])\s*(\d{1,3})\s*(d|days?|w|weeks?|m|months?)?$/.exec(s))) {
        var n = +m[2] * (m[1] === "-" ? -1 : 1), u = (m[3] || "d")[0];
        return u === "w" ? addDays(t, 7 * n) : u === "m" ? addMonths(t, n) : addDays(t, n);
      }
      if ((m = /^(next\s+)?(mon|tue|wed|thu|fri|sat|sun)[a-z]*$/.exec(s))) {
        var want = WD.map(function (w) { return w.toLowerCase(); }).indexOf(m[2]);
        return addDays(t, (want - t.getDay() + 7) % 7 || 7);     // the coming one, never today
      }
    }
    var iso1 = parseIso(s); if (iso1) return iso1;
    var monthIdx = function (w) { var i = MON.map(function (x) { return x.toLowerCase(); }).indexOf(w.slice(0, 3)); return i; };
    var y, mo, d;
    if (month) {
      if ((m = /^([a-z]+)\s*(\d{2,4})?$/.exec(s)) && monthIdx(m[1]) >= 0) { mo = monthIdx(m[1]); y = m[2]; }
      else if ((m = /^(\d{1,2})[\/.\- ](\d{2,4})$/.exec(s))) { mo = +m[1] - 1; y = m[2]; }
      else return false;
      y = y ? (+y < 100 ? 2000 + +y : +y) : t.getFullYear();
      return mo >= 0 && mo < 12 ? new Date(y, mo, 1) : false;
    }
    if ((m = /^(\d{1,2})\s*([a-z]+)\s*(\d{2,4})?$/.exec(s)) && monthIdx(m[2]) >= 0) { d = +m[1]; mo = monthIdx(m[2]); y = m[3]; }
    else if ((m = /^([a-z]+)\s*(\d{1,2})\s*(\d{4})?$/.exec(s)) && monthIdx(m[1]) >= 0) { d = +m[2]; mo = monthIdx(m[1]); y = m[3]; }
    else if ((m = /^(\d{1,2})[\/.\- ](\d{1,2})(?:[\/.\- ](\d{2,4}))?$/.exec(s))) { d = +m[1]; mo = +m[2] - 1; y = m[3]; }
    else if ((m = /^(\d{1,2})$/.exec(s))) { d = +m[1]; mo = t.getMonth(); }
    else return false;
    if (y) y = +y < 100 ? 2000 + +y : +y;
    else {
      y = t.getFullYear();
      // A day/month without a year means the next one coming up if it's well in the past
      if (new Date(y, mo, d) < addDays(t, -60)) y += 1;
    }
    var out = new Date(y, mo, d);
    return out.getMonth() === mo && out.getDate() === d ? out : false;
  }

  /* ------------------------------------------------------------------ popover */
  var pop, state = null;

  function el(tag, cls, text) { var e = doc.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }

  function buildPop() {
    pop = el("div", "dp-pop");
    pop.setAttribute("role", "dialog");
    pop.setAttribute("aria-modal", "false");
    pop.hidden = true;
    doc.body.appendChild(pop);
    pop.addEventListener("mousedown", function (e) { e.preventDefault(); });   // keep focus in the field
    pop.addEventListener("click", onPopClick);
    pop.addEventListener("keydown", onPopKey);
  }

  function bounds(w) {
    return { min: parseIso(w.hidden.getAttribute("min")), max: parseIso(w.hidden.getAttribute("max")) };
  }
  function allowed(w, d) {
    var b = bounds(w);
    if (w.month) {
      if (b.min && d < new Date(b.min.getFullYear(), b.min.getMonth(), 1)) return false;
      if (b.max && d > b.max) return false;
      return true;
    }
    return !((b.min && d < b.min) || (b.max && d > b.max));
  }

  function render() {
    var w = state.w, view = state.view, sel = parseIso(w.hidden.value), t = today();
    pop.innerHTML = "";
    pop.classList.toggle("dp-month", !!w.month || state.mode === "months");
    var cap = el("div", "dp-cap", (w.label || "").replace(/: choose a (date|month)$/, ""));
    pop.appendChild(cap);
    var head = el("div", "dp-head");
    var prev = el("button", "dp-nav", ""); prev.type = "button"; prev.setAttribute("data-dp", "prev");
    var next = el("button", "dp-nav", ""); next.type = "button"; next.setAttribute("data-dp", "next");
    var title = el("button", "dp-title"); title.type = "button"; title.setAttribute("data-dp", "mode");
    var monthsMode = w.month || state.mode === "months";
    prev.setAttribute("aria-label", monthsMode ? "Previous year" : "Previous month");
    next.setAttribute("aria-label", monthsMode ? "Next year" : "Next month");
    title.textContent = monthsMode ? String(view.getFullYear()) : MONTHS[view.getMonth()] + " " + view.getFullYear();
    title.setAttribute("aria-label", monthsMode ? "Year " + view.getFullYear() : "Pick month and year");
    if (w.month) title.disabled = true;
    head.appendChild(prev); head.appendChild(title); head.appendChild(next);
    pop.appendChild(head);

    var grid = el("div", monthsMode ? "dp-months" : "dp-grid");
    grid.setAttribute("role", "grid");
    if (monthsMode) {
      for (var i = 0; i < 12; i++) {
        var md = new Date(view.getFullYear(), i, 1);
        var b = el("button", "dp-m", MON[i]); b.type = "button"; b.setAttribute("data-m", i);
        if (sel && sel.getFullYear() === md.getFullYear() && sel.getMonth() === i) b.classList.add("sel");
        if (t.getFullYear() === md.getFullYear() && t.getMonth() === i) b.classList.add("now");
        if (w.month && !allowed(w, md)) b.disabled = true;
        b.tabIndex = (state.focus.getMonth() === i) ? 0 : -1;
        grid.appendChild(b);
      }
    } else {
      DAYS.forEach(function (d) { var h = el("span", "dp-wd", d.slice(0, 2)); h.setAttribute("aria-hidden", "true"); grid.appendChild(h); });
      var first = new Date(view.getFullYear(), view.getMonth(), 1);
      var start = addDays(first, -((first.getDay() + 6) % 7));
      for (var k = 0; k < 42; k++) {
        var d = addDays(start, k);
        if (k >= 35 && d.getMonth() !== view.getMonth()) break;
        var c = el("button", "dp-d", String(d.getDate())); c.type = "button";
        c.setAttribute("data-d", iso(d));
        c.setAttribute("aria-label", fmt(d));
        if (d.getMonth() !== view.getMonth()) c.classList.add("out");
        if (same(d, t)) { c.classList.add("now"); c.setAttribute("aria-current", "date"); }
        if (same(d, sel)) { c.classList.add("sel"); c.setAttribute("aria-pressed", "true"); }
        if (d.getDay() === 0) c.classList.add("sun");
        if (!allowed(w, d)) c.disabled = true;
        c.tabIndex = same(d, state.focus) ? 0 : -1;
        grid.appendChild(c);
      }
    }
    pop.appendChild(grid);

    var foot = el("div", "dp-foot");
    var quick = w.month ? [["This month", 0]] : [["Today", 0], ["Tomorrow", 1], ["+1 week", 7]];
    quick.forEach(function (q) {
      var target = w.month ? new Date(t.getFullYear(), t.getMonth(), 1) : addDays(t, q[1]);
      var b = el("button", "dp-chip", q[0]); b.type = "button";
      b.setAttribute("data-pick", w.month ? isoMonth(target) : iso(target));
      if (!allowed(w, target)) b.disabled = true;
      foot.appendChild(b);
    });
    if (!w.required && w.hidden.value) {
      var clr = el("button", "dp-chip dp-clear", "Clear"); clr.type = "button"; clr.setAttribute("data-pick", "");
      foot.appendChild(clr);
    }
    pop.appendChild(foot);
  }

  function place() {
    var r = state.w.field.getBoundingClientRect(), vw = window.innerWidth, vh = window.innerHeight;
    pop.classList.remove("sheet");
    if (vw < 560) { pop.classList.add("sheet"); pop.style.left = pop.style.top = ""; return; }
    var pw = pop.offsetWidth, ph = pop.offsetHeight;
    var left = Math.min(Math.max(8, r.left), vw - pw - 8);
    var below = r.bottom + 6, above = r.top - ph - 6;
    var top = (below + ph > vh - 8 && above > 8) ? above : below;
    pop.style.left = (left + window.scrollX) + "px";
    pop.style.top = (top + window.scrollY) + "px";
    pop.classList.toggle("up", top === above);
  }

  function open(w) {
    if (!pop) buildPop();
    if (state && state.w === w) return;
    close(false);
    var sel = parseIso(w.hidden.value) || parseTyped(w.field.value, w.month) || null;
    var base = sel || clampToBounds(w, today());
    state = { w: w, view: new Date(base.getFullYear(), base.getMonth(), 1), focus: base, mode: w.month ? "months" : "days" };
    pop.setAttribute("aria-label", w.label || "Choose a date");
    render();
    pop.hidden = false;
    place();
    requestAnimationFrame(function () { pop.classList.add("in"); });
    w.field.setAttribute("aria-expanded", "true");
    w.box.classList.add("open");
  }
  function clampToBounds(w, d) {
    var b = bounds(w);
    if (b.min && d < b.min) return b.min;
    if (b.max && d > b.max) return b.max;
    return d;
  }

  function close(refocus) {
    if (!state) return;
    var w = state.w;
    pop.classList.remove("in");
    pop.hidden = true;
    w.field.setAttribute("aria-expanded", "false");
    w.box.classList.remove("open");
    state = null;
    if (refocus) w.field.focus();
  }

  function focusCell() {
    var c = pop.querySelector('[tabindex="0"]');
    if (c) c.focus();
  }

  function onPopClick(e) {
    var b = e.target.closest("button");
    if (!b || b.disabled || !state) return;
    var w = state.w, act = b.getAttribute("data-dp");
    var monthsMode = w.month || state.mode === "months";
    if (act === "prev" || act === "next") {
      var dir = act === "prev" ? -1 : 1;
      state.view = monthsMode ? new Date(state.view.getFullYear() + dir, 0, 1) : addMonths(state.view, dir);
      state.focus = monthsMode ? new Date(state.view.getFullYear(), state.focus.getMonth(), 1) : new Date(state.view);
      render(); return;
    }
    if (act === "mode") { state.mode = "months"; state.focus = new Date(state.view); render(); focusCell(); return; }
    if (b.hasAttribute("data-m")) {
      var md = new Date(state.view.getFullYear(), +b.getAttribute("data-m"), 1);
      if (w.month) { pick(w, isoMonth(md)); return; }
      state.mode = "days"; state.view = md; state.focus = md; render(); focusCell(); return;
    }
    if (b.hasAttribute("data-d")) { pick(w, b.getAttribute("data-d")); return; }
    if (b.hasAttribute("data-pick")) { pick(w, b.getAttribute("data-pick")); }
  }

  function onPopKey(e) {
    if (!state) return;
    var w = state.w, monthsMode = w.month || state.mode === "months", f = state.focus, n = null;
    var k = e.key;
    if (k === "Escape") { e.preventDefault(); close(true); return; }
    if (k === "Tab") { close(false); return; }
    if (monthsMode) {
      var step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -3, ArrowDown: 3 }[k];
      if (step) n = new Date(f.getFullYear(), f.getMonth() + step, 1);
      if (k === "PageUp") n = new Date(f.getFullYear() - 1, f.getMonth(), 1);
      if (k === "PageDown") n = new Date(f.getFullYear() + 1, f.getMonth(), 1);
    } else {
      var s = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }[k];
      if (s) n = addDays(f, s);
      if (k === "PageUp") n = addMonths(f, e.shiftKey ? -12 : -1);
      if (k === "PageDown") n = addMonths(f, e.shiftKey ? 12 : 1);
      if (k === "Home") n = addDays(f, -((f.getDay() + 6) % 7));
      if (k === "End") n = addDays(f, 6 - ((f.getDay() + 6) % 7));
    }
    if (n) {
      e.preventDefault();
      state.focus = n;
      state.view = monthsMode ? new Date(n.getFullYear(), 0, 1) : new Date(n.getFullYear(), n.getMonth(), 1);
      render(); focusCell();
    }
  }

  /* ------------------------------------------------------------------ fields */
  function commit(w, value, fromPicker) {
    var before = w.hidden.value;
    w.hidden.value = value;
    w.field.value = fmt(parseIso(value), w.compact, w.month);
    w.field.setCustomValidity("");
    w.box.classList.remove("bad");
    if (value === before) return;
    w.hidden.dispatchEvent(new Event("input", { bubbles: true }));
    w.hidden.dispatchEvent(new Event("change", { bubbles: true }));
    // Inline cells (Projects list) save as soon as a date is chosen
    if (w.saveOnPick && w.hidden.form && value !== w.orig) {
      w.hidden.form.classList.add("is-saving");
      if (w.hidden.form.requestSubmit) w.hidden.form.requestSubmit(); else w.hidden.form.submit();
    }
  }

  function pick(w, value) {
    commit(w, value, true);
    close(!w.saveOnPick);
  }

  function readTyped(w) {
    var v = parseTyped(w.field.value, w.month);
    if (v === false) {
      w.field.setCustomValidity(w.month ? "Type a month like Nov 2026." : "Type a date like 12 Nov, 12/11/2026, tomorrow or +3.");
      w.box.classList.add("bad");
      return false;
    }
    if (v && !allowed(w, v)) {
      var b = bounds(w);
      w.field.setCustomValidity("Choose a date" + (b.min ? " from " + fmt(b.min, true) : "") + (b.max ? " up to " + fmt(b.max, true) : "") + ".");
      w.box.classList.add("bad");
      return false;
    }
    commit(w, v ? (w.month ? isoMonth(v) : iso(v)) : "", false);
    return true;
  }

  function enhance(input) {
    if (input.dataset.dp || input.closest("[data-native-date]")) return;
    var month = input.type === "month";
    var w = { month: month, required: input.required, compact: input.classList.contains("cell-input"),
              saveOnPick: input.hasAttribute("data-save-on-leave") };   // data-autosubmit fields submit from the change event
    var label = input.getAttribute("aria-label") || (input.labels && input.labels[0] && input.labels[0].textContent.replace(/\*\s*$/, "").trim()) || "";
    w.label = label ? label + (month ? ": choose a month" : ": choose a date") : (month ? "Choose a month" : "Choose a date");

    var box = el("span", "dp" + (w.compact ? " dp-compact" : ""));
    var field = el("input", input.className.replace(/\bcell-input\b/, "") + " dp-field" + (w.compact ? " cell-input" : ""));
    field.type = "text";
    if (input.id) { field.id = input.id; input.removeAttribute("id"); }   // keeps <label for> working
    field.autocomplete = "off";
    field.spellcheck = false;
    field.setAttribute("inputmode", "text");
    field.setAttribute("aria-haspopup", "dialog");
    field.setAttribute("aria-expanded", "false");
    if (label) field.setAttribute("aria-label", label);
    field.placeholder = input.getAttribute("placeholder") || (month ? "Month" : w.compact ? "—" : "Pick a date");
    if (input.required) field.required = true;
    if (input.disabled) field.disabled = true;
    ["aria-describedby", "aria-invalid", "style"].forEach(function (a) { if (input.hasAttribute(a)) field.setAttribute(a, input.getAttribute(a)); });
    input.removeAttribute("style");

    var btn = el("button", "dp-btn"); btn.type = "button"; btn.tabIndex = -1;
    btn.setAttribute("aria-label", w.label);
    btn.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="5" width="17" height="15.5" rx="3"/><path d="M3.5 10h17M8 3v4M16 3v4"/></svg>';

    input.parentNode.insertBefore(box, input);
    box.appendChild(field); box.appendChild(btn); box.appendChild(input);
    input.type = "hidden";
    input.dataset.dp = "1";
    w.orig = input.value;
    w.hidden = input; w.field = field; w.box = box;
    field.value = fmt(parseIso(input.value), w.compact, month);

    field.addEventListener("click", function () { open(w); });
    btn.addEventListener("click", function () { if (state && state.w === w) close(true); else { field.focus(); open(w); } });
    field.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown" || (e.key === "Down")) { e.preventDefault(); open(w); focusCell(); }
      else if (e.key === "Enter") { e.preventDefault(); if (readTyped(w)) { close(false); if (w.saveOnPick && w.hidden.form && w.hidden.value !== w.orig && !w.hidden.form.classList.contains("is-saving")) { w.hidden.form.classList.add("is-saving"); w.hidden.form.requestSubmit ? w.hidden.form.requestSubmit() : w.hidden.form.submit(); } } else field.reportValidity(); }
      else if (e.key === "Escape") { if (state && state.w === w) { e.preventDefault(); close(false); } else if (w.compact) { field.value = fmt(parseIso(w.orig), true, month); w.hidden.value = w.orig; field.blur(); } }
      else if (e.key === "Tab") close(false);
    });
    field.addEventListener("input", function () { w.box.classList.remove("bad"); field.setCustomValidity(""); });
    field.addEventListener("blur", function () {
      setTimeout(function () {
        if (state && state.w === w && pop.contains(doc.activeElement)) return;
        if (state && state.w === w) close(false);
        var typed = field.value, shown = fmt(parseIso(w.hidden.value), w.compact, month);
        if (typed !== shown) {
          if (readTyped(w) && w.saveOnPick && w.hidden.form && w.hidden.value !== w.orig && !w.hidden.form.classList.contains("is-saving")) {
            w.hidden.form.classList.add("is-saving");
            w.hidden.form.requestSubmit ? w.hidden.form.requestSubmit() : w.hidden.form.submit();
          }
        }
      }, 120);
    });
    // A typed date that couldn't be read stops the form being sent with the old value
    if (input.form) input.form.addEventListener("submit", function (e) {
      var shown = fmt(parseIso(w.hidden.value), w.compact, month);
      if (field.value !== shown && !readTyped(w)) { e.preventDefault(); field.reportValidity(); }
    });
  }

  function enhanceAll(root) {
    Array.prototype.forEach.call((root || doc).querySelectorAll('input[type="date"], input[type="month"]'), enhance);
  }

  doc.addEventListener("mousedown", function (e) {
    if (state && !pop.contains(e.target) && !state.w.box.contains(e.target)) close(false);
  });
  window.addEventListener("resize", function () { if (state) place(); });
  doc.addEventListener("scroll", function (e) {
    if (state && !pop.contains(e.target)) { if (window.innerWidth < 560) return; place(); }
  }, true);

  window.StudioDatePicker = { enhance: enhanceAll, parse: parseTyped };
  if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", function () { enhanceAll(); });
  else enhanceAll();
})();
