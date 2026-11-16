/* Hard drives <-> projects, matched by the couple's name. No inline JS (CSP).
   - [data-film-picker] (a drive's Contents box): type a couple, pick films -> chips;
     the picked film ids go in a hidden "films_link" input and are linked to the drive on save.
   - [data-footage-suggest] (a film's couple name): drives that look like they hold this
     couple are suggested; picking one sets "Footage on" (and ticks the folder's films). */
(function () {
  "use strict";
  var doc = document;

  function el(tag, cls, text) {
    var n = doc.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  /* A small dropdown under an input, with keyboard support. */
  function Dropdown(input, onPick) {
    var host = input.parentNode;
    host.classList.add("sg-host");
    var list = el("div", "sg-list");
    list.setAttribute("role", "listbox");
    list.hidden = true;
    input.insertAdjacentElement("afterend", list);
    input.setAttribute("aria-autocomplete", "list");
    var items = [], active = -1;

    function setActive(i) {
      active = i;
      items.forEach(function (it, k) { it.el.classList.toggle("on", k === i); it.el.setAttribute("aria-selected", k === i); });
      if (items[i]) items[i].el.scrollIntoView({ block: "nearest" });
    }
    var api = {
      list: list,
      show: function (rows) {          // rows: [{html node, value, disabled, head}]
        list.innerHTML = "";
        items = [];
        rows.forEach(function (r) {
          list.appendChild(r.node);
          if (r.head || r.disabled) return;
          r.node.setAttribute("role", "option");
          var it = { el: r.node, value: r.value };
          r.node.addEventListener("mousedown", function (e) { e.preventDefault(); onPick(it.value); });
          items.push(it);
        });
        list.hidden = !rows.length;
        active = -1;
      },
      hide: function () { list.hidden = true; active = -1; },
      open: function () { return !list.hidden; }
    };
    input.addEventListener("keydown", function (e) {
      if (!api.open()) return;
      if (e.key === "ArrowDown") { e.preventDefault(); setActive(Math.min(items.length - 1, active + 1)); }
      else if (e.key === "ArrowUp") { e.preventDefault(); setActive(Math.max(0, active - 1)); }
      else if (e.key === "Enter" && active >= 0) { e.preventDefault(); onPick(items[active].value); }
      else if (e.key === "Escape") { api.hide(); }
    });
    input.addEventListener("blur", function () { setTimeout(api.hide, 120); });
    return api;
  }

  function fetcher(url) {
    var timer, seq = 0;
    return function (q, done) {
      clearTimeout(timer);
      timer = setTimeout(function () {
        var mine = ++seq;
        fetch(url + "?q=" + encodeURIComponent(q), { credentials: "same-origin", headers: { "Accept": "application/json" } })
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (j) { if (j && mine === seq) done(j); })
          .catch(function () {});
      }, 160);
    };
  }

  /* ---------------- drive Contents: pick films ---------------- */
  function filmPicker(note) {
    var form = note.form;
    // the picker gets its own search box; the Contents box stays a free note
    var input = el("input", "input sg-search");
    input.type = "search";
    input.autocomplete = "off";
    input.placeholder = "🔎 Type a couple to add films, e.g. Amit Tanya";
    input.setAttribute("aria-label", "Find films to put on this drive");
    var wrap = el("div", "sg-picker");
    note.insertAdjacentElement("beforebegin", wrap);
    wrap.appendChild(input);
    var hidden = form.querySelector('input[name="films_link"]');
    if (!hidden) {
      hidden = el("input");
      hidden.type = "hidden";
      hidden.name = "films_link";
      form.appendChild(hidden);
    }
    var picked = [];
    try { picked = JSON.parse(note.getAttribute("data-initial") || "[]"); } catch (e) {}
    var chips = el("div", "sg-chips");
    chips.setAttribute("aria-label", "Films linked to this drive");
    input.insertAdjacentElement("beforebegin", chips);
    var get = fetcher(note.getAttribute("data-film-picker"));

    function sync() {
      hidden.value = picked.map(function (p) { return p.id; }).join(",");
      chips.innerHTML = "";
      picked.forEach(function (p, i) {
        var c = el("span", "sg-chip");
        c.appendChild(el("span", "", p.label));
        var x = el("button", "sg-x", "×");
        x.type = "button";
        x.setAttribute("aria-label", "Remove " + p.label);
        x.addEventListener("click", function () { picked.splice(i, 1); sync(); input.focus(); });
        c.appendChild(x);
        chips.appendChild(c);
      });
      chips.hidden = !picked.length;
    }
    function has(id) { return picked.some(function (p) { return p.id === id; }); }
    function add(films) {
      films.forEach(function (f) { if (!has(f.id)) picked.push({ id: f.id, label: f.label }); });
      sync();
      input.value = "";
      dd.hide();
      input.focus();
    }
    var dd = Dropdown(input, add);
    input.addEventListener("keydown", function (e) { if (e.key === "Enter") e.preventDefault(); });

    input.addEventListener("input", function () {
      var q = input.value.trim();
      if (q.length < 2) { dd.hide(); return; }
      get(q, function (j) {
        var groups = {}, order = [];
        j.films.forEach(function (f) {
          if (!groups[f.couple]) { groups[f.couple] = []; order.push(f.couple); }
          groups[f.couple].push(f);
        });
        var rows = [];
        order.forEach(function (c) {
          var fs = groups[c], free = fs.filter(function (f) { return !has(f.id); });
          var head = el("div", "sg-head", c);
          rows.push({ node: head, head: true });
          if (free.length > 1) {
            var all = el("div", "sg-item sg-all", "＋ All " + free.length + " films of " + c);
            rows.push({ node: all, value: free });
          }
          fs.forEach(function (f) {
            var n = el("div", "sg-item");
            n.appendChild(el("b", "", f.film));
            n.appendChild(el("span", "muted", " · " + [f.agency, f.status].filter(Boolean).join(" · ")));
            if (f.drive) n.appendChild(el("span", "tag" + (has(f.id) ? "" : " warn"), "on " + f.drive));
            if (has(f.id)) { n.classList.add("is-picked"); n.appendChild(el("span", "tag ok", "✓ picked")); }
            rows.push({ node: n, value: [f], disabled: has(f.id) });
          });
        });
        if (!rows.length) rows.push({ node: el("div", "sg-empty", "No films found for “" + q + "”. Add the project first, or write it in the note below."), head: true });
        dd.show(rows);
      });
    });
    sync();
  }

  /* ---------------- film couple: suggest the drive with its footage ---------------- */
  function footageSuggest(input) {
    var form = input.form;
    var source = form.querySelector('select[name="source"]');
    if (!source) return;
    var folder = form.querySelector('input[name="folder"]');
    if (!folder) {
      folder = el("input");
      folder.type = "hidden";
      folder.name = "folder";
      form.appendChild(folder);
    }
    var note = el("div", "sg-note");
    note.hidden = true;
    var get = fetcher(input.getAttribute("data-footage-suggest"));
    var dd = Dropdown(input, pick);
    dd.list.insertAdjacentElement("afterend", note);

    function pick(d) {
      var prev = source.value;
      var opt = source.querySelector('option[value="' + d.id + '"]');
      if (!opt) { opt = el("option", "", d.code); opt.value = String(d.id); source.appendChild(opt); }
      source.value = String(d.id);
      source.dispatchEvent(new Event("change", { bubbles: true }));
      folder.value = d.folder || "";
      var ag = form.querySelector('select[name="agency"]'), agPrev = ag ? ag.value : "";
      if (ag && !ag.value && d.agency_id) { ag.value = String(d.agency_id); ag.dispatchEvent(new Event("change", { bubbles: true })); }
      var ticked = [];
      var boxes = form.querySelectorAll('input[name="deliverables"]');
      if (d.films.length && !Array.prototype.some.call(boxes, function (b) { return b.checked; })) {
        Array.prototype.forEach.call(boxes, function (b) {
          if (d.films.indexOf(b.value) >= 0) { b.checked = true; b.dispatchEvent(new Event("change", { bubbles: true })); ticked.push(b.value); }
        });
      }
      note.innerHTML = "";
      note.appendChild(el("span", "", "✓ Footage on " + d.code + " (" + d.why + ")" + (ticked.length ? " · ticked " + ticked.join(", ") : "")));
      var undo = el("button", "linkish", "Undo");
      undo.type = "button";
      undo.addEventListener("click", function () {
        source.value = prev;
        source.dispatchEvent(new Event("change", { bubbles: true }));
        if (ag && ag.value !== agPrev) { ag.value = agPrev; ag.dispatchEvent(new Event("change", { bubbles: true })); }
        folder.value = "";
        ticked.forEach(function (v) {
          var b = form.querySelector('input[name="deliverables"][value="' + v + '"]');
          if (b) { b.checked = false; b.dispatchEvent(new Event("change", { bubbles: true })); }
        });
        note.hidden = true;
      });
      note.appendChild(undo);
      note.hidden = false;
      dd.hide();
    }

    input.addEventListener("input", function () {
      var q = input.value.trim();
      if (q.length < 3) { dd.hide(); return; }
      get(q, function (j) {
        var rows = [];
        if (j.drives.length) rows.push({ node: el("div", "sg-head", "Footage may be on…"), head: true });
        j.drives.forEach(function (d) {
          var n = el("div", "sg-item");
          n.appendChild(el("b", "mono", d.code));
          n.appendChild(el("span", "muted", " · " + [d.agency, d.status].filter(Boolean).join(" · ")));
          n.appendChild(el("div", "small muted", d.why + (d.films.length ? " · " + d.films.join(", ") : "")));
          if (String(d.id) === source.value) n.appendChild(el("span", "tag ok", "✓ chosen"));
          rows.push({ node: n, value: d });
        });
        dd.show(rows);
      });
    });
  }

  function init(root) {
    Array.prototype.forEach.call((root || doc).querySelectorAll("[data-film-picker]:not([data-sg])"), function (i) {
      i.setAttribute("data-sg", "1"); filmPicker(i);
    });
    Array.prototype.forEach.call((root || doc).querySelectorAll("[data-footage-suggest]:not([data-sg])"), function (i) {
      i.setAttribute("data-sg", "1"); footageSuggest(i);
    });
  }
  /* the drive viewer on editors' pages closes when clicking elsewhere or pressing Escape */
  doc.addEventListener("click", function (e) {
    Array.prototype.forEach.call(doc.querySelectorAll("details.drive-view[open], details.qc-edit[open]"), function (d) {
      if (!d.contains(e.target)) d.removeAttribute("open");
    });
  });
  doc.addEventListener("keydown", function (e) {
    if (e.key === "Escape") Array.prototype.forEach.call(doc.querySelectorAll("details.drive-view[open], details.qc-edit[open]"), function (d) { d.removeAttribute("open"); });
  });

  window.StudioSuggest = { init: init };
  if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", function () { init(); }); else init();
})();
