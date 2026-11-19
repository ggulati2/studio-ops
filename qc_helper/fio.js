/* Red Wed QC Helper: small functions run inside Frame.io's share page (via chrome.scripting).
   Each must be self-contained: it is serialised and injected on its own. */

/* Wait for the player, load the HD stream once, and report where the media lives. */
async function fioPrepare() {
  var sleep = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };
  for (var i = 0; i < 60 && !document.querySelector("video"); i++) await sleep(500);
  if (!document.querySelector("video")) return {error: "The Frame.io player didn't load. Is the link right and open to view?"};
  await sleep(1500);
  var find = function () {
    var R = performance.getEntriesByType("resource").map(function (r) { return r.name; });
    return {
      v: R.find(function (n) { return /\/h264_[a-z0-9_]+\/\d+\.m4s/.test(n); }),
      a: R.find(function (n) { return /\/aac_[a-z0-9_]+\/\d+\.m4s/.test(n); }),
      p: R.find(function (n) { return /video_h264_\d+\.mp4/.test(n); })
    };
  };
  try { performance.setResourceTimingBufferSize(5000); } catch (e) { /* older Chrome */ }
  var f = find();
  if (!f.v || !f.a) {
    var play = document.querySelector("[data-testid=overlay-play-button]") || document.querySelector("[data-testid=play-button]");
    if (play) play.click();
    for (var k = 0; k < 30 && !(f.v && f.a); k++) { await sleep(500); f = find(); }
    document.querySelectorAll("video").forEach(function (v) { v.pause(); });
  }
  // the audio sits next to the video under the same token
  if (f.v && !f.a) f.a = f.v.replace(/\/h264_[a-z0-9_]+\/(\d+\.m4s)/, "/aac_cha2/$1");
  var vids = Array.prototype.slice.call(document.querySelectorAll("video"));
  var dur = Math.max.apply(null, vids.map(function (v) { return isFinite(v.duration) ? v.duration : 0; }));
  var hd = (f.v || "").match(/\/(h264_[a-z0-9_]+)\//);
  return {
    title: document.title.replace(/ - Frame\.io$/, ""),
    duration: dur,
    proxy: f.p || null,
    vBase: f.v ? f.v.replace(/\/\d+\.m4s.*$/, "/") : null,
    aBase: f.a ? f.a.replace(/\/\d+\.m4s.*$/, "/") : null,
    quality: hd ? hd[1] : null,
    error: (!f.v || !f.a) ? "Couldn't reach the video stream on Frame.io." : null
  };
}

/* Read every comment (the list only shows some at a time, so scroll through it). */
async function fioComments() {
  var sleep = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };
  for (var i = 0; i < 30 && !document.querySelector("[data-testid=comment-tab-container]"); i++) await sleep(500);
  await sleep(800);
  var got = {};
  var grab = function () {
    document.querySelectorAll("[data-testid^=comment-cell-]").forEach(function (cell) {
      var id = cell.getAttribute("data-testid").replace("comment-cell-", "");
      if (got[id]) return;
      var tcEl = cell.querySelector("[data-testid=comment-prefix-timestamp]");
      var tc = tcEl ? (tcEl.textContent || "").trim() : "";
      if (!/^\d\d:\d\d:\d\d:\d\d$/.test(tc)) {
        var leaf = Array.prototype.slice.call(cell.querySelectorAll("*")).find(function (e) {
          return e.childElementCount === 0 && /^\d\d:\d\d:\d\d:\d\d$/.test((e.textContent || "").trim());
        });
        tc = leaf ? leaf.textContent.trim() : "";
      }
      var author = cell.querySelector("[data-testid=comment-author-name]");
      var num = ((cell.innerText || "").match(/#(\d+)/) || [])[1];
      var lines = (cell.innerText || "").split("\n").map(function (s) { return s.replace(/\ufeff/g, "").trim(); }).filter(Boolean);
      var skip = [author ? author.textContent.trim() : "", tc, "Reply", "Edited"];
      var body = lines.filter(function (l) {
        return skip.indexOf(l) < 0 && !/^#\d+$/.test(l) && !/^(\d+[smhdw]|Just now|\d+ ?(min|mins|hours?|days?) ago)$/i.test(l)
          && !/^(Read|Seen|Viewed) by\b/i.test(l) && l.length > 1;
      }).join(" ").replace(/\s*(Read|Seen|Viewed) by [^.]*$/i, "");
      got[id] = {id: id, n: num ? +num : null, tc: tc, text: body, author: author ? author.textContent.trim() : ""};
    });
  };
  grab();
  var first = document.querySelector("[data-testid^=comment-cell-]");
  var sc = first;
  while (sc && !(sc.scrollHeight > sc.clientHeight + 20 && getComputedStyle(sc).overflowY !== "visible")) sc = sc.parentElement;
  if (sc) {
    sc.scrollTop = 0; await sleep(300); grab();
    for (var k = 0; k < 400; k++) {
      var before = sc.scrollTop;
      sc.scrollTop += Math.max(150, sc.clientHeight * 0.7);
      await sleep(220); grab();
      if (sc.scrollTop === before) break;
    }
  }
  return Object.keys(got).map(function (k) { return got[k]; }).filter(function (c) { return c.tc; });
}

/* Post one comment on this file at `sec` seconds. Returns what Frame.io shows. */
async function fioPost(sec, text) {
  var sleep = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };
  var pad = function (n) { return String(n).padStart(2, "0"); };
  var want = pad(Math.floor(sec / 3600)) + ":" + pad(Math.floor(sec % 3600 / 60)) + ":" + pad(Math.floor(sec % 60));
  var comp = document.querySelector("[data-testid=create-comment-comment-composer]");
  if (!comp) return {ok: false, error: "comment box not found"};
  for (var attempt = 0; attempt < 3; attempt++) {
    document.querySelectorAll("video").forEach(function (v) { v.pause(); v.currentTime = sec; });
    await sleep(900);
    var tcEl = document.querySelector("[data-testid=timecode]") || document.querySelector("[data-testid=time-display]");
    if (tcEl) { tcEl.click(); await sleep(250); document.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", bubbles: true})); await sleep(150); }
    comp.focus(); comp.click(); await sleep(400);
    var box = document.querySelector("[data-testid=create-comment-composer-container]") || document;
    var pre = Array.prototype.slice.call(box.querySelectorAll("*")).find(function (e) {
      return e.childElementCount === 0 && /^\d\d:\d\d:\d\d:\d\d$/.test((e.textContent || "").trim());
    });
    var shown = pre ? pre.textContent.trim() : "";
    if (shown.slice(0, 8) === want) break;
  }
  if (shown.slice(0, 8) !== want) return {ok: false, error: "couldn't set the time (showed " + shown + ")"};
  // clear anything left in the box, then type through the editor's own input events
  var sel = getSelection(), r = document.createRange();
  r.selectNodeContents(comp); sel.removeAllRanges(); sel.addRange(r);
  comp.dispatchEvent(new InputEvent("beforeinput", {inputType: "deleteContentBackward", bubbles: true, cancelable: true, composed: true}));
  await sleep(200);
  comp.dispatchEvent(new InputEvent("beforeinput", {inputType: "insertText", data: text, bubbles: true, cancelable: true, composed: true}));
  await sleep(400);
  var send = document.querySelector("[data-testid=composer-submit-button]");
  if (!send || send.disabled) return {ok: false, error: "comment box didn't take the text"};
  var cellsBefore = document.querySelectorAll("[data-testid^=comment-cell-]").length;
  send.click();
  for (var w = 0; w < 20; w++) {
    await sleep(300);
    if ((comp.innerText || "").indexOf(text.slice(0, 20)) < 0) break;
  }
  var posted = (comp.innerText || "").indexOf(text.slice(0, 20)) < 0;
  return {ok: posted, at: shown, cells: [cellsBefore, document.querySelectorAll("[data-testid^=comment-cell-]").length]};
}
