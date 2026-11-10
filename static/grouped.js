/* Projects, grouped by agency and couple.
   - A couple's films load only when it is opened (one small request), so the page
     stays light however many films there are.
   - Which agencies and couples are open is remembered in this browser, also across
     filter changes.
   - "Not the same" on a look-alike hint hides it for good in this browser.
   No inline JS (CSP). */
(function () {
  "use strict";
  var doc = document;
  var KEY = "grp:open", DUPS = "grp:notdup", MAX_RESTORE = 10;

  function read(k) { try { return JSON.parse(localStorage.getItem(k)) || []; } catch (e) { return []; } }
  function write(k, v) { try { localStorage.setItem(k, JSON.stringify(v.slice(-200))); } catch (e) {} }

  function root() { return doc.querySelector("[data-groups]"); }

  /* Films of a couple are fetched as soon as the pointer rests on it (or it gets focus),
     so by the time it is clicked open they are usually already here. */
  var cache = {};
  function url(d) { return d.getAttribute("data-src") + location.search; }
  function get(u) {
    if (!cache[u]) {
      cache[u] = fetch(u, { credentials: "same-origin", headers: { "X-Requested-With": "fetch-page" } })
        .then(function (r) {
          if (!r.ok || (r.redirected && r.url.indexOf("/login") >= 0)) throw new Error(String(r.status));
          return r.text();
        });
      cache[u].catch(function () { delete cache[u]; });
    }
    return cache[u];
  }
  function prefetch(e) {
    var s = e.target.closest && e.target.closest(".grp-couple > summary");
    if (!s) return;
    var d = s.parentNode;
    if (!d.open && d.querySelector(":scope > .grp-body[data-lazy]")) get(url(d));
  }
  doc.addEventListener("pointerover", prefetch);
  doc.addEventListener("focusin", prefetch);
  /* Anything saved may change what a couple shows: forget fetched copies */
  doc.addEventListener("submit", function () { cache = {}; }, true);

  function loadCouple(d) {
    var body = d.querySelector(":scope > .grp-body[data-lazy]");
    if (!body || d.dataset.loading) return;
    d.dataset.loading = "1";
    get(url(d))
      .then(function (html) {
        var t = doc.createElement("template");
        t.innerHTML = html.trim();
        var n = t.content.firstElementChild;
        if (!n || !d.parentNode) return;
        // keep the opened <details> (no jump): only its body changes
        var nb = n.querySelector(":scope > .grp-body");
        body.removeAttribute("data-lazy");
        body.innerHTML = nb ? nb.innerHTML : "";
        body.classList.add("live-in");
        delete d.dataset.loading;
        if (window.StudioDatePicker) window.StudioDatePicker.enhance(body);
      })
      .catch(function () {
        delete d.dataset.loading;
        body.innerHTML = '<p class="small" style="color:var(--bad)">Couldn’t load these films. Close and open again to retry.</p>';
      });
  }

  function remember(d) {
    var k = d.getAttribute("data-key");
    if (!k) return;
    var open = read(KEY).filter(function (x) { return x !== k; });
    if (d.open) open.push(k);
    write(KEY, open);
  }

  /* toggle doesn't bubble: listen in the capture phase */
  doc.addEventListener("toggle", function (e) {
    var d = e.target;
    if (!d.matches || !d.matches(".grp-agency, .grp-couple")) return;
    if (d.dataset.restoring) { delete d.dataset.restoring; } else remember(d);
    if (d.open && d.matches(".grp-couple")) loadCouple(d);
  }, true);

  function restore() {
    var r = root();
    if (!r) return;
    var open = read(KEY), couples = 0;
    open.forEach(function (k) {
      var d = r.querySelector('[data-key="' + k + '"]');
      if (!d || d.open) return;
      if (d.matches(".grp-couple") && ++couples > MAX_RESTORE) return;
      d.dataset.restoring = "1";
      d.open = true;
    });
    var gone = read(DUPS);
    Array.prototype.forEach.call(r.querySelectorAll("[data-dup]"), function (x) {
      if (gone.indexOf(x.getAttribute("data-dup")) >= 0) x.remove();
    });
  }

  doc.addEventListener("click", function (e) {
    var b = e.target.closest("[data-dup-dismiss]");
    if (!b) return;
    var row = b.closest("[data-dup]");
    var gone = read(DUPS);
    gone.push(row.getAttribute("data-dup"));
    write(DUPS, gone);
    row.classList.add("fade-out");
    setTimeout(function () { row.remove(); }, 250);
  });

  /* Filters re-draw the list: open again what was open */
  doc.addEventListener("studio:parts", function () { cache = {}; restore(); });
  if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", restore); else restore();
})();
