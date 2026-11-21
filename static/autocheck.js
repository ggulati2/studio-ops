/* AutoCheck on Studio Ops: hands checks to the Red Wed QC Helper extension and saves what it reports.
   - On the QC page: "Run AutoCheck" on an event (or the whole film) runs it now in this browser.
   - For QC people: checks queued when an editor sends a link are picked up by themselves, one at a
     time, whenever Studio Ops is open in a Chrome that has the helper.
   The extension marks the page with data-qc-helper="<version>". No inline JS (CSP). */
(function () {
  "use strict";
  var doc = document, active = {}, claiming = false, lastProgress = {};

  function meta(name) { var m = doc.querySelector('meta[name="' + name + '"]'); return m ? m.content : ""; }
  function helperVersion() { return doc.documentElement.getAttribute("data-qc-helper"); }
  function older(a, b) {
    var x = String(a).split(".").map(Number), y = String(b).split(".").map(Number);
    for (var i = 0; i < 3; i++) { if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) < (y[i] || 0); }
    return false;
  }
  function say(text, level) { if (window.StudioToast) window.StudioToast(text, {level: level || "info"}); }
  function post(url, body, json) {
    var h = {"X-CSRFToken": meta("ac-csrf")};
    if (json) h["Content-Type"] = "application/json";
    return fetch(url, {method: "POST", credentials: "same-origin", headers: h, body: json ? JSON.stringify(body) : body});
  }
  function strip(id) { return doc.querySelector('[data-ac-run-id="' + id + '"]') || (active[id] && active[id].box); }

  /* ---- run now, from an event's AutoCheck box */
  function setup(box) {
    if (box.dataset.acReady) return;
    box.dataset.acReady = "1";
    var form = box.querySelector(".ac-form");
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      if (!helperVersion()) { refresh(true); say("Install the Red Wed QC Helper first (see the note at the top).", "error"); return; }
      var btn = form.querySelector("[data-ac-run]");
      btn.disabled = true;
      post(box.getAttribute("data-start"), new FormData(form))
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (!d.ok) { btn.disabled = false; say(d.error || "Couldn't start AutoCheck.", "error"); return; }
          active[d.job.id] = {box: box};
          box.setAttribute("data-ac-run-id", d.job.id);
          showLive(d.job.id, "Starting… a new tab opens for the check.");
          window.postMessage({source: "redwed-portal", type: "autocheck:run", job: d.job}, location.origin);
        })
        .catch(function () { btn.disabled = false; say("Couldn't start AutoCheck. Check your connection.", "error"); });
    });
  }
  function showLive(id, text, frac) {
    var b = strip(id); if (!b) return;
    var live = b.querySelector(".ac-live"); if (!live) return;
    live.hidden = false;
    var st = b.querySelector("[data-ac-live-status]"); if (st && text) st.textContent = text;
    if (frac != null) b.querySelector(".ac-bar").style.width = Math.round(frac * 100) + "%";
    var old = b.querySelector("[data-ac-status]"); if (old) old.hidden = true;
  }

  /* ---- save what the helper reports (for any check this browser runs, even after moving page) */
  function save(id, payload) {
    return post("/qc/autocheck/" + id + "/update/", payload, true).then(function (r) { return r.ok; }).catch(function () { return false; });
  }
  function ack(id) { window.postMessage({source: "redwed-portal", type: "autocheck:ack", id: id}, location.origin); }

  window.addEventListener("message", function (e) {
    if (e.source !== window || !e.data || e.data.source !== "redwed-helper") return;
    var m = e.data;
    if (m.type === "pong") {
      if (m.version) doc.documentElement.setAttribute("data-qc-helper", m.version);
      refresh(); return;
    }
    if (!m.id) return;
    if (m.type === "autocheck:accepted") {
      if (!m.ok) {
        showLive(m.id, "The QC Helper couldn't start: " + (m.error || "unknown error"));
        save(m.id, {type: "failed", error: m.error || "helper refused"});
      } else if (m.auto) say("AutoCheck started on " + (m.film || "a film") + " (in its own window).", "info");
    } else if (m.type === "autocheck:progress") {
      showLive(m.id, m.text, m.frac);
      if (Date.now() - (lastProgress[m.id] || 0) > 4000) { lastProgress[m.id] = Date.now(); save(m.id, {type: "progress", text: m.text}); }
    } else if (m.type === "autocheck:done") {
      save(m.id, {type: "done", report: m.report, posted: m.posted}).then(function (ok) {
        if (!ok) return;                         // the helper keeps it and hands it over again next time
        ack(m.id);
        if (m.report && m.report.repost) {
          var st = doc.querySelector("[data-ac-repost-status]"), left = (m.report.posted || []).filter(function (x) { return !x.ok; }).length;
          if (st) st.textContent = left ? left + " still didn't post. Open the film on Frame.io and add " + (left > 1 ? "them" : "it") + " by hand." : "All posted.";
          say(m.posted + " comment(s) posted on Frame.io" + (left ? ", " + left + " still not." : "."), left ? "warning" : "success");
          if (!left) setTimeout(function () { location.reload(); }, 1200);
          delete active[m.id];
          return;
        }
        showLive(m.id, "Done: " + m.posted + " comment(s) posted on Frame.io.", 1);
        var b = strip(m.id);
        if (b && !b.querySelector("[data-ac-report]")) {
          var a = doc.createElement("a"); a.href = "/qc/autocheck/" + m.id + "/"; a.className = "small"; a.textContent = "Report →";
          a.setAttribute("data-ac-report", ""); b.querySelector(".ac-head").appendChild(a);
        }
        say("AutoCheck finished" + (m.film ? " on " + m.film : "") + ": " + m.posted + " comment(s) posted on Frame.io.", "success");
        delete active[m.id];
      });
    } else if (m.type === "autocheck:failed") {
      save(m.id, {type: "failed", error: m.error}).then(function (ok) {
        if (!ok) return;
        ack(m.id);
        showLive(m.id, "Stopped: " + m.error);
        say("AutoCheck stopped: " + m.error, "error");
        delete active[m.id];
      });
    }
  });

  /* ---- pick up checks queued when editors send links (QC people only) */
  function claim() {
    var url = meta("ac-claim");
    if (!url || !helperVersion() || claiming) return;
    claiming = true;
    post(url, new FormData())
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && d.job) window.postMessage({source: "redwed-portal", type: "autocheck:run", job: d.job}, location.origin);
      })
      .catch(function () {})
      .then(function () { claiming = false; });
  }

  function refresh(force) {
    var have = helperVersion();
    Array.prototype.forEach.call(doc.querySelectorAll(".ac-helper-missing"), function (w) { w.hidden = !!have && !force; });
    Array.prototype.forEach.call(doc.querySelectorAll(".ac-helper-old"), function (w) {
      var need = w.getAttribute("data-helper-need");
      w.hidden = !(have && need && older(have, need));
      var h = w.querySelector("[data-ac-have]"); if (h && have) h.textContent = have;
    });
  }
  /* report page: post again only the comments that didn't go up */
  doc.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-ac-repost]");
    if (!b) return;
    var box = b.closest("[data-ac-repost-box]"), st = box.querySelector("[data-ac-repost-status]");
    if (!helperVersion()) { refresh(true); say("Install the Red Wed QC Helper first.", "error"); return; }
    b.disabled = true; st.textContent = "Starting…";
    post(box.getAttribute("data-url"), new FormData())
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) { b.disabled = false; st.textContent = ""; say(d.error || "Couldn't start.", "error"); return; }
        active[d.job.id] = {box: box, repost: true};
        st.textContent = "Posting in a new tab…";
        window.postMessage({source: "redwed-portal", type: "autocheck:run", job: d.job}, location.origin);
      })
      .catch(function () { b.disabled = false; st.textContent = ""; say("Couldn't start. Check your connection.", "error"); });
  });

  function init() {
    Array.prototype.forEach.call(doc.querySelectorAll("[data-autocheck]"), setup);
    refresh();
    window.postMessage({source: "redwed-portal", type: "ping"}, location.origin);
    setTimeout(refresh, 600);
  }
  doc.addEventListener("studio:parts", init);
  if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", init); else init();
  if (meta("ac-claim")) {
    setTimeout(claim, 2500);                     // after the helper has answered the ping
    setInterval(claim, 60000);
    doc.addEventListener("visibilitychange", function () { if (!doc.hidden) setTimeout(claim, 1000); });
  }
})();
