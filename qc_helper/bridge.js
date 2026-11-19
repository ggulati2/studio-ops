/* Red Wed QC Helper: bridge between the Studio Ops portal page and the extension.
   The portal posts {source:"redwed-portal"} messages; results come back as {source:"redwed-helper"}. */
(function () {
  "use strict";
  var VERSION = chrome.runtime.getManifest().version;
  function mark() { document.documentElement.setAttribute("data-qc-helper", VERSION); }
  mark();
  document.addEventListener("DOMContentLoaded", mark);
  window.addEventListener("message", function (e) {
    if (e.source !== window || !e.data || e.data.source !== "redwed-portal") return;
    if (e.data.type === "autocheck:run") {
      chrome.runtime.sendMessage({type: "autocheck:run", job: e.data.job}, function (r) {
        window.postMessage({source: "redwed-helper", type: "autocheck:accepted", id: e.data.job.id,
                            ok: !!(r && r.ok), error: r && r.error}, location.origin);
      });
    } else if (e.data.type === "ping") {
      window.postMessage({source: "redwed-helper", type: "pong", version: VERSION}, location.origin);
    }
  });
  chrome.runtime.onMessage.addListener(function (msg) {
    if (msg && msg.source === "redwed-helper") window.postMessage(msg, location.origin);
  });
})();
