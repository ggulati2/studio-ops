/* Red Wed QC Helper: opens a runner for each AutoCheck the portal asks for, and keeps finished
   results until the portal has saved them (so nothing is lost if Studio Ops was closed meanwhile). */
chrome.runtime.onMessage.addListener(function (msg, sender, reply) {
  if (!msg) return;
  if (msg.type === "autocheck:run" && sender.tab) {
    var job = msg.job || {};
    if (!job.id || !job.v2) { reply({ok: false, error: "No link to check"}); return; }
    var store = {};
    store["job:" + job.id] = {job: job, portalTab: sender.tab.id};
    chrome.storage.local.set(store, function () {
      var url = chrome.runtime.getURL("runner.html") + "#" + job.id;
      if (job.auto) {
        // queued by an editor's send: its own window, behind the one being worked in
        chrome.windows.create({url: url, focused: false, width: 1200, height: 860}, function () { reply({ok: true}); });
      } else {
        chrome.tabs.create({url: url, active: true, windowId: sender.tab.windowId}, function () { reply({ok: true}); });
      }
    });
    return true;
  }
  if (msg.type === "autocheck:pending") {
    chrome.storage.local.get(null, function (all) {
      var out = [];
      Object.keys(all).forEach(function (k) {
        if (k.indexOf("result:") === 0 && (!msg.origin || all[k].origin === msg.origin)) out.push(all[k].msg);
      });
      reply({results: out});
    });
    return true;
  }
  if (msg.type === "autocheck:ack" && msg.id) {
    chrome.storage.local.remove("result:" + msg.id);
  }
});
