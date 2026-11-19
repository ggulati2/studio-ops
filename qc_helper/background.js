/* Red Wed QC Helper: opens a runner tab for each AutoCheck the portal asks for. */
chrome.runtime.onMessage.addListener(function (msg, sender, reply) {
  if (!msg || msg.type !== "autocheck:run" || !sender.tab) return;
  var job = msg.job || {};
  if (!job.id || !job.v2) { reply({ok: false, error: "No V2 link"}); return; }
  var key = "job:" + job.id;
  var store = {};
  store[key] = {job: job, portalTab: sender.tab.id};
  chrome.storage.local.set(store, function () {
    chrome.tabs.create({url: chrome.runtime.getURL("runner.html") + "#" + job.id, active: true,
                        windowId: sender.tab.windowId}, function () { reply({ok: true}); });
  });
  return true;
});
