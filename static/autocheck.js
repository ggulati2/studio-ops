/* AutoCheck on the QC dashboard: hands the job to the Red Wed QC Helper extension and saves
   what it reports. The extension marks the page with data-qc-helper="<version>". No inline JS (CSP). */
(function () {
  "use strict";
  var doc = document, active = {};

  function helperVersion() { return doc.documentElement.getAttribute("data-qc-helper"); }
  function older(a, b) {
    var x = String(a).split(".").map(Number), y = String(b).split(".").map(Number);
    for (var i = 0; i < 3; i++) { if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) < (y[i] || 0); }
    return false;
  }
  function csrf(box) { var i = box.querySelector("input[name=csrfmiddlewaretoken]"); return i ? i.value : ""; }
  function say(text, level) {
    if (window.StudioToast) window.StudioToast(text, {level: level || "info"});
  }

  function setup(box) {
    if (box.dataset.acReady) return;
    box.dataset.acReady = "1";
    var have = helperVersion(), need = box.getAttribute("data-helper-need");
    box.querySelector(".ac-helper-missing").hidden = !!have;
    if (have && need && older(have, need)) {
      box.querySelector(".ac-helper-old").hidden = false;
      box.querySelector("[data-ac-have]").textContent = have;
    }
    var form = box.querySelector(".ac-form");
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      if (!helperVersion()) { box.querySelector(".ac-helper-missing").hidden = false; return; }
      var btn = form.querySelector("[data-ac-run]");
      btn.disabled = true;
      fetch(box.getAttribute("data-start"), {method: "POST", credentials: "same-origin", body: new FormData(form),
                                             headers: {"X-CSRFToken": csrf(box)}})
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (!d.ok) { btn.disabled = false; say(d.error || "Couldn't start AutoCheck.", "error"); return; }
          active[d.job.id] = {box: box, url: "/qc/autocheck/" + d.job.id + "/update/", last: 0};
          box.querySelector(".ac-live").hidden = false;
          box.querySelector("[data-ac-status]").textContent = "Starting… a new tab opens for the check.";
          window.postMessage({source: "redwed-portal", type: "autocheck:run", job: d.job}, location.origin);
        })
        .catch(function () { btn.disabled = false; say("Couldn't start AutoCheck. Check your connection.", "error"); });
    });
  }

  function save(id, payload) {
    var a = active[id]; if (!a) return Promise.resolve();
    return fetch(a.url, {method: "POST", credentials: "same-origin", body: JSON.stringify(payload),
                         headers: {"Content-Type": "application/json", "X-CSRFToken": csrf(a.box)}}).catch(function () {});
  }

  window.addEventListener("message", function (e) {
    if (e.source !== window || !e.data || e.data.source !== "redwed-helper") return;
    var m = e.data, a = active[m.id];
    if (m.type === "pong") return;
    if (!a) return;
    var status = a.box.querySelector("[data-ac-status]"), bar = a.box.querySelector(".ac-bar");
    if (m.type === "autocheck:accepted" && !m.ok) {
      status.textContent = "The QC Helper couldn't start: " + (m.error || "unknown error");
      save(m.id, {type: "failed", error: m.error || "helper refused"});
    } else if (m.type === "autocheck:progress") {
      status.textContent = m.text;
      if (m.frac != null) bar.style.width = Math.round(m.frac * 100) + "%";
      if (Date.now() - a.last > 4000) { a.last = Date.now(); save(m.id, {type: "progress", text: m.text}); }
    } else if (m.type === "autocheck:done") {
      bar.style.width = "100%";
      status.textContent = "Done. " + m.posted + " comment(s) posted on V2.";
      save(m.id, {type: "done", report: m.report, posted: m.posted}).then(function () {
        say("AutoCheck finished: " + m.posted + " comment(s) posted on V2.", "success");
        delete active[m.id];
        if (window.StudioLive && window.StudioLive.reload) window.StudioLive.reload(); else location.reload();
      });
    } else if (m.type === "autocheck:failed") {
      status.textContent = "Stopped: " + m.error;
      save(m.id, {type: "failed", error: m.error}).then(function () { say("AutoCheck stopped: " + m.error, "error"); delete active[m.id]; });
    }
  });

  window.addEventListener("beforeunload", function (e) {
    if (Object.keys(active).length) { e.preventDefault(); e.returnValue = ""; }
  });

  function refresh() {
    var have = helperVersion();
    Array.prototype.forEach.call(doc.querySelectorAll("[data-autocheck]"), function (box) {
      var need = box.getAttribute("data-helper-need");
      box.querySelector(".ac-helper-missing").hidden = !!have;
      var old = box.querySelector(".ac-helper-old");
      old.hidden = !(have && need && older(have, need));
      if (!old.hidden) box.querySelector("[data-ac-have]").textContent = have;
    });
  }
  // the extension marks the page as it loads; ask it again in case that came late
  window.addEventListener("message", function (e) {
    if (e.source === window && e.data && e.data.source === "redwed-helper" && e.data.type === "pong") {
      if (e.data.version) doc.documentElement.setAttribute("data-qc-helper", e.data.version);
      refresh();
    }
  });
  function init(root) {
    Array.prototype.forEach.call((root || doc).querySelectorAll("[data-autocheck]"), setup);
    refresh();
    window.postMessage({source: "redwed-portal", type: "ping"}, location.origin);
    setTimeout(refresh, 600);
  }
  doc.addEventListener("studio:parts", function () { init(); });
  if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", function () { init(); }); else init();
})();
