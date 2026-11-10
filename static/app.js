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

    /* Light / dark switch */
    function syncSwitches() {
      var dark = doc.documentElement.getAttribute("data-theme") === "dark";
      Array.prototype.forEach.call(doc.querySelectorAll("[data-theme-toggle]"), function (b) {
        b.setAttribute("aria-checked", dark ? "true" : "false");
        b.setAttribute("title", dark ? "Switch to light mode" : "Switch to dark mode");
      });
      var meta = doc.querySelector('meta[name="theme-color"]');
      if (meta) meta.setAttribute("content", dark ? "#16161A" : "#FFFFFF");
    }
    syncSwitches();
    doc.addEventListener("click", function (e) {
      var b = e.target.closest("[data-theme-toggle]");
      if (!b) return;
      var next = doc.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
      doc.documentElement.classList.add("theme-anim");
      doc.documentElement.setAttribute("data-theme", next);
      try { localStorage.setItem("theme", next); } catch (err) {}
      syncSwitches();
      setTimeout(function () { doc.documentElement.classList.remove("theme-anim"); }, 350);
    });
    /* Follow the device if it changes and nobody has picked a theme on this device */
    if (window.matchMedia) {
      window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function (m) {
        var saved = null;
        try { saved = localStorage.getItem("theme"); } catch (err) {}
        if (!saved) { doc.documentElement.setAttribute("data-theme", m.matches ? "dark" : "light"); syncSwitches(); }
      });
    }

    /* Select boxes that apply as soon as you pick something */
    doc.addEventListener("change", function (e) {
      var el = e.target.closest("[data-autosubmit]");
      if (el && el.form) {
        // "Sent to agency" first asks for the date it was really sent
        if (el.dataset.askDate && el.value === el.dataset.askDate && el.form.querySelector("[data-sent-ask]")) {
          openSentAsk(el.form);
          return;
        }
        el.form.classList.add("is-saving");
        if (el.form.requestSubmit) el.form.requestSubmit(); else el.form.submit();
      }
    });

    function openSentAsk(form) {
      var box = form.querySelector("[data-sent-ask]"), input = box.querySelector("input[name=sent_on]");
      box.hidden = false;
      input.disabled = false;
      form.classList.add("asking");
      if (window.StudioDatePicker) window.StudioDatePicker.enhance(box);
      setTimeout(function () { (box.querySelector(".dp-field, input[name=sent_on]") || input).focus(); }, 0);
    }
    function closeSentAsk(form) {
      var box = form.querySelector("[data-sent-ask]");
      if (!box || box.hidden) return;
      box.hidden = true;
      box.querySelector("input[name=sent_on]").disabled = true;
      form.classList.remove("asking");
      var sel = form.querySelector("select[data-ask-date]");
      if (sel) Array.prototype.forEach.call(sel.options, function (o) { if (o.defaultSelected) sel.value = o.value; });
    }
    doc.addEventListener("click", function (e) {
      var c = e.target.closest("[data-sent-cancel]");
      if (c) { closeSentAsk(c.closest("form")); return; }
      var ed = e.target.closest("[data-sent-edit]");
      if (ed) {
        var f = ed.closest("form"), sel = f.querySelector("select[data-ask-date]");
        if (sel) sel.value = sel.dataset.askDate;
        openSentAsk(f);
      }
    });
    doc.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && e.target.closest && e.target.closest("[data-sent-ask]")) closeSentAsk(e.target.closest("form"));
    });

    /* Inline dates save when you leave the field or press Enter, not on every keystroke
       (a date being typed is "valid" after each digit) */
    function saveDate(el) {
      if (!el.form || el.value === el.defaultValue || el.form.classList.contains("is-saving")) return;
      if (el.value && (+el.value.slice(0, 4) < 2000 || !el.checkValidity())) return;
      el.form.classList.add("is-saving");
      if (el.form.requestSubmit) el.form.requestSubmit(); else el.form.submit();
    }
    doc.addEventListener("focusout", function (e) {
      if (e.target.matches && e.target.matches("[data-save-on-leave]")) saveDate(e.target);
    });
    doc.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && e.target.matches && e.target.matches("[data-save-on-leave]")) { e.preventDefault(); saveDate(e.target); }
      if (e.key === "Escape" && e.target.matches && e.target.matches("[data-save-on-leave]")) { e.target.value = e.target.defaultValue; e.target.blur(); }
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

    /* Photos: phone pictures are 3–10 MB. Shrink each one in the browser (longest side
       2000 px, JPEG) so uploads are quick on mobile data; the server re-checks and
       re-compresses anyway. */
    var LIMIT = 11500000;
    function shrink(file) {
      return new Promise(function (resolve) {
        if (!/^image\/(jpeg|png|webp)$/.test(file.type) || file.size < 700000 || !window.createImageBitmap) return resolve(file);
        createImageBitmap(file).then(function (bmp) {
          var max = 2000, s = Math.min(1, max / Math.max(bmp.width, bmp.height));
          var c = doc.createElement("canvas");
          c.width = Math.round(bmp.width * s); c.height = Math.round(bmp.height * s);
          c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
          c.toBlob(function (blob) {
            if (!blob || blob.size >= file.size) return resolve(file);
            resolve(new File([blob], file.name.replace(/\.\w+$/, "") + ".jpg", { type: "image/jpeg" }));
          }, "image/jpeg", 0.85);
        }).catch(function () { resolve(file); });
      });
    }
    function formBytes(form) {
      var total = 0;
      Array.prototype.forEach.call(form.querySelectorAll("input[type=file]"), function (i) {
        Array.prototype.forEach.call(i.files || [], function (f) { total += f.size; });
      });
      return total;
    }
    doc.addEventListener("change", function (e) {
      var input = e.target;
      if (!input.matches || !input.matches("input[type=file][accept^='image']") || !input.files.length || !window.DataTransfer) return;
      var form = input.form;
      if (form) form.classList.add("is-saving");
      Promise.all(Array.prototype.map.call(input.files, shrink)).then(function (files) {
        var dt = new DataTransfer();
        files.forEach(function (f) { dt.items.add(f); });
        input.files = dt.files;
        if (form) form.classList.remove("is-saving");
        var hint = input.parentNode.querySelector(".upload-note") || doc.createElement("span");
        hint.className = "help upload-note";
        var mb = form ? formBytes(form) / 1e6 : 0;
        hint.textContent = files.length + " photo" + (files.length > 1 ? "s" : "") + " ready (" + mb.toFixed(1) + " MB)" +
          (mb * 1e6 > LIMIT ? ". Too large to send in one go: pick fewer photos." : "");
        hint.style.color = mb * 1e6 > LIMIT ? "var(--bad)" : "";
        input.insertAdjacentElement("afterend", hint);
      });
    });
    doc.addEventListener("submit", function (e) {
      if (e.target.querySelector("input[type=file]") && formBytes(e.target) > LIMIT) {
        e.preventDefault(); e.stopImmediatePropagation();
        e.target.dataset.sent = "";
        window.alert("These photos are too large to send together. Remove a few and try again.");
      }
    }, true);

    /* Add / edit a person */
    var pf = doc.querySelector("[data-person-form]");
    if (pf) {
      var role = pf.querySelector("#id_role"), warn = pf.querySelector("[data-founder-warning]");
      var confirmRow = pf.querySelector('[data-field="confirm_founder"]');
      var original = pf.getAttribute("data-original-role");
      function founderCheck() {
        var risky = role && role.value === "admin" && original !== "admin";
        if (warn) warn.hidden = !risky;
        if (confirmRow) confirmRow.hidden = !risky;
        if (!risky && confirmRow) { var cb = confirmRow.querySelector("input"); if (cb) cb.checked = false; }
      }
      if (role) { role.addEventListener("change", founderCheck); founderCheck(); }
      pf.addEventListener("submit", function (e) {
        if (role && role.value === "admin" && original !== "admin") {
          var cb = confirmRow && confirmRow.querySelector("input");
          if (!cb || !cb.checked) { e.preventDefault(); e.stopImmediatePropagation(); pf.dataset.sent = "";
            if (warn) warn.scrollIntoView({ block: "center", behavior: "smooth" });
            window.alert("You've chosen Founder (full access). Tick the confirmation box if that's intended, or pick another role."); }
        }
      }, true);

      /* Login ID suggestion: Name_Surname, until you type your own */
      var first = pf.querySelector("#id_first_name"), last = pf.querySelector("#id_last_name"), uname = pf.querySelector("#id_username");
      function part(v) {
        return (v || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").trim().split(/\s+/)
          .map(function (w) { w = w.replace(/[^A-Za-z0-9]/g, ""); return w ? w[0].toUpperCase() + w.slice(1) : ""; })
          .filter(Boolean).join("_");
      }
      if (uname && first) {
        if (!pf.hasAttribute("data-new") || uname.value) uname.dataset.touched = "1";
        uname.addEventListener("input", function () { uname.dataset.touched = uname.value ? "1" : ""; });
        var suggest = function () {
          if (uname.dataset.touched) return;
          uname.value = [part(first.value), part(last && last.value)].filter(Boolean).join("_");
        };
        first.addEventListener("input", suggest);
        if (last) last.addEventListener("input", suggest);
      }

      /* Password: visible, with a generator */
      var pw = pf.querySelector("[data-password]");
      if (pw && window.crypto) {
        var row = doc.createElement("div"); row.className = "pw-row";
        pw.parentNode.insertBefore(row, pw); row.appendChild(pw);
        var gen = doc.createElement("button"); gen.type = "button"; gen.className = "btn"; gen.textContent = "Generate";
        row.appendChild(gen);
        gen.addEventListener("click", function () {
          var chars = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789", out = [];
          var buf = new Uint32Array(12); crypto.getRandomValues(buf);
          for (var i = 0; i < 12; i++) { out.push(chars[buf[i] % chars.length]); if (i === 3 || i === 7) out.push("-"); }
          pw.value = out.join("");
        });
      }
    }

    /* Person form: full-time vs contract fields */
    if (pf) {
      var emp = pf.querySelectorAll('input[name="employment"]'), basis = pf.querySelector("#id_contract_basis");
      var rowOf = function (n) { return pf.querySelector('[data-field="' + n + '"]'); };
      function empSync() {
        var checked = pf.querySelector('input[name="employment"]:checked');
        var contract = checked && checked.value === "contract";
        var sal = rowOf("monthly_salary"), cb = rowOf("contract_basis"), cr = rowOf("contract_rate");
        if (sal) sal.hidden = contract;
        if (cb) cb.hidden = !contract;
        if (cr) cr.hidden = !contract || !basis || basis.value === "project" || !basis.value;
        ["username", "password"].forEach(function (n) { var r = rowOf(n); if (r) r.hidden = contract; });
        var note = pf.querySelector("[data-contract-note]"); if (note) note.hidden = !contract;
      }
      Array.prototype.forEach.call(emp, function (r) { r.addEventListener("change", empSync); });
      if (basis) basis.addEventListener("change", empSync);
      empSync();
    }

    /* Today: copy a job + stage to the rows below */
    doc.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-copy-down]");
      if (!btn) return;
      var row = btn.closest("[data-task-row]"), rows = Array.prototype.slice.call(doc.querySelectorAll("[data-task-row]"));
      var src = row.querySelectorAll("select"), i = rows.indexOf(row), n = 0;
      rows.slice(i + 1).forEach(function (r) {
        var dst = r.querySelectorAll("select");
        for (var k = 0; k < src.length && k < dst.length; k++) dst[k].value = src[k].value;
        r.classList.add("flash"); setTimeout(function () { r.classList.remove("flash"); }, 700);
        n++;
      });
      btn.textContent = "Copied to " + n + " row" + (n === 1 ? "" : "s") + " ✓";
      setTimeout(function () { btn.textContent = "⤓ Copy to rows below"; }, 1800);
    });

    /* Today: add another job row / clear a row */
    /* Looked up per click (inside the clicked form), so job rows also work in pop-ups
       loaded later, such as logging a missed day from My month */
    var rowsBox, tpl, total;
    function scopeOf(el) {
      var s = (el && el.closest("form")) || doc;
      rowsBox = s.querySelector("[data-task-rows]"); tpl = s.querySelector("[data-task-template]");
      total = s.querySelector('input[name="form-TOTAL_FORMS"]');
    }
    function renumber() {
      Array.prototype.forEach.call(rowsBox.querySelectorAll("[data-task-row]"), function (r, i) {
        var n = r.querySelector("[data-task-num]"); if (n) n.textContent = i + 1;
      });
    }
    doc.addEventListener("click", function (e) {
      if (e.target.closest("[data-add-task]")) scopeOf(e.target);
      if (e.target.closest("[data-add-task]") && rowsBox && tpl && total) {
        var max = parseInt(rowsBox.getAttribute("data-max") || "12", 10), i = parseInt(total.value, 10);
        if (i >= max) { var lim = (e.target.closest("form") || doc).querySelector("[data-task-limit]"); if (lim) lim.hidden = false; return; }
        var html = tpl.innerHTML.replace(/__prefix__/g, i).replace(/__num__/g, i + 1);
        rowsBox.insertAdjacentHTML("beforeend", html);
        total.value = i + 1;
        renumber();
        var sel = rowsBox.lastElementChild.querySelector("select"); if (sel) sel.focus();
      }
      var clr = e.target.closest("[data-remove-task]");
      if (clr) {
        var row = clr.closest("[data-task-row]");
        Array.prototype.forEach.call(row.querySelectorAll("select, input"), function (el) { el.value = ""; });
      }
    });

    /* Planner: close the copy-down popover when clicking elsewhere */
    doc.addEventListener("click", function (e) {
      Array.prototype.forEach.call(doc.querySelectorAll("details.repeat[open]"), function (d) {
        if (!d.contains(e.target)) d.removeAttribute("open");
      });
    });

    /* New project: per-film rows follow the ticked films; show the agency's usual rate */
    var np = doc.querySelector("[data-new-project]");
    if (np) {
      var rates = {};
      try { rates = JSON.parse(np.getAttribute("data-rates") || "{}"); } catch (err) {}
      var agencySel = np.querySelector("#id_agency"), perFilm = np.querySelector("[data-per-film]");
      var sourceSel = np.querySelector("#id_source"), linkRow = np.querySelector('[data-field="cloud_link"]');
      function syncFilms() {
        var ticked = Array.prototype.map.call(np.querySelectorAll('input[name="deliverables"]:checked'), function (i) { return i.value; });
        var r = rates[agencySel && agencySel.value] || {}, any = false;
        Array.prototype.forEach.call(np.querySelectorAll("tr[data-film]"), function (tr) {
          var on = ticked.indexOf(tr.getAttribute("data-film")) >= 0;
          tr.hidden = !on; any = any || on;
          var amt = tr.querySelector('input[name$="_amount"]');
          if (amt) amt.placeholder = r[tr.getAttribute("data-film")] ? "usual " + r[tr.getAttribute("data-film")] : "amount";
        });
        if (perFilm) perFilm.hidden = !any;
      }
      function syncSource() { if (linkRow) linkRow.hidden = !(sourceSel && sourceSel.value === "cloud"); }
      np.addEventListener("change", function (e) {
        if (e.target.name === "deliverables" || e.target === agencySel) syncFilms();
        if (e.target === sourceSel) syncSource();
      });
      syncFilms(); syncSource();
    }

    /* Copy buttons */
    doc.addEventListener("click", function (e) {
      var b = e.target.closest("[data-copy]");
      if (!b) return;
      var src = doc.querySelector(b.getAttribute("data-copy"));
      if (!src) return;
      var text = src.value || src.textContent, label = b.textContent;
      function done() { b.textContent = "Copied ✓"; setTimeout(function () { b.textContent = label; }, 2000); }
      if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done);
      else { src.classList.remove("sr"); src.select(); doc.execCommand("copy"); src.classList.add("sr"); done(); }
    });

    /* Messages fade after a while (errors stay) */
    Array.prototype.forEach.call(doc.querySelectorAll(".msg:not(.error)"), function (m) {
      setTimeout(function () { m.classList.add("gone"); }, 6000);
    });
  });
})();

/* Team & attendance: keep the "not logged" date list fully visible even inside
   scrolling tables (it's positioned against the window, next to the name). */
(function () {
  "use strict";
  function place(host) {
    var tip = host.querySelector(".miss-tip");
    if (!tip) return;
    var r = host.getBoundingClientRect();
    tip.classList.add("fixed");
    var w = tip.offsetWidth, h = tip.offsetHeight;
    var left = Math.min(Math.max(8, r.left), window.innerWidth - w - 8);
    var top = r.bottom + 6;
    if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 6);
    tip.style.left = left + "px";
    tip.style.top = top + "px";
  }
  document.addEventListener("mouseover", function (e) {
    var host = e.target.closest && e.target.closest(".who-miss.has");
    if (host) place(host);
  });
  document.addEventListener("focusin", function (e) {
    var host = e.target.closest && e.target.closest(".who-miss.has");
    if (host) place(host);
  });
})();
