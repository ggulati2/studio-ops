/* Red Wed QC Helper: bridge between the Studio Ops portal page and the extension.
   The portal posts {source:"redwed-portal"} messages; results come back as {source:"redwed-helper"}. */
(function () {
  "use strict";
  var VERSION = chrome.runtime.getManifest().version;
  function mark() { document.documentElement.setAttribute("data-qc-helper", VERSION); }
  function toPage(m) { m.source = "redwed-helper"; window.postMessage(m, location.origin); }
  mark();
  document.addEventListener("DOMContentLoaded", mark);
  window.addEventListener("message", function (e) {
    if (e.source !== window || !e.data || e.data.source !== "redwed-portal") return;
    var d = e.data;
    if (d.type === "autocheck:run") {
      chrome.runtime.sendMessage({type: "autocheck:run", job: d.job}, function (r) {
        toPage({type: "autocheck:accepted", id: d.job.id, ok: !!(r && r.ok), error: r && r.error,
                auto: !!d.job.auto, feedback: !!d.job.feedback, film: d.job.film});
      });
    } else if (d.type === "autocheck:ack") {
      chrome.runtime.sendMessage({type: "autocheck:ack", id: d.id});
    } else if (d.type === "ping") {
      toPage({type: "pong", version: VERSION});
    }
  });
  chrome.runtime.onMessage.addListener(function (msg) {
    if (msg && msg.source === "redwed-helper") window.postMessage(msg, location.origin);
  });
  // results that finished while Studio Ops wasn't open: hand them over to be saved
  window.addEventListener("load", function () {
    setTimeout(function () {
      chrome.runtime.sendMessage({type: "autocheck:pending", origin: location.origin}, function (r) {
        void chrome.runtime.lastError;
        ((r && r.results) || []).forEach(toPage);
      });
    }, 1500);
  });
})();
