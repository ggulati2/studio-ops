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
      // the avatar's initials ("RF") are a line of their own: not part of the comment
      var avatar = cell.querySelector("[data-testid=avatar-container]");
      var skip = [author ? author.textContent.trim() : "", avatar ? (avatar.innerText || "").trim() : "", tc, "Reply", "Edited"];
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

/* Post one comment on this file at `sec` seconds. Returns what Frame.io shows.
   The player has to really be at that moment first: a spot Frame.io hasn't loaded yet takes a
   few seconds, so wait for it, nudge it (a blink of play) and, if needed, click the timeline. */
async function fioPost(sec, text) {
  var sleep = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };
  var pad = function (n) { return String(n).padStart(2, "0"); };
  var hms = function (s) { return pad(Math.floor(s / 3600)) + ":" + pad(Math.floor(s % 3600 / 60)) + ":" + pad(Math.floor(s % 60)); };
  var tcSec = function (tc) { var p = (tc || "").split(":").map(Number); return p.length >= 3 ? p[0] * 3600 + p[1] * 60 + p[2] : NaN; };
  var comp = document.querySelector("[data-testid=create-comment-comment-composer]");
  if (!comp) return {ok: false, error: "comment box not found"};
  var vids = Array.prototype.slice.call(document.querySelectorAll("video"));
  var main = vids.slice().sort(function (a, b) { return b.clientWidth * b.clientHeight - a.clientWidth * a.clientHeight; })[0];
  var at = function () { return main ? main.currentTime : NaN; };
  var seek = function () { vids.forEach(function (v) { v.pause(); v.currentTime = sec + 0.04; }); };
  var settled = async function (ms) {     // the player is at `sec` with a picture ready
    for (var w = 0; w < ms / 250; w++) {
      await sleep(250);
      if (main && !main.seeking && main.readyState >= 2 && Math.abs(at() - sec) < 0.6) return true;
    }
    return false;
  };
  var timeline = function () {            // Frame.io's own scrubber, clicked where `sec` is
    var bar = document.querySelector("[role=slider][aria-valuemax]");
    if (!bar || !main || !isFinite(main.duration)) return false;
    var r = bar.getBoundingClientRect(), x = r.left + r.width * Math.min(1, Math.max(0, sec / main.duration)), y = r.top + r.height / 2;
    ["pointerdown", "mousedown", "pointerup", "mouseup", "click"].forEach(function (type) {
      var Ev = type.indexOf("pointer") === 0 ? PointerEvent : MouseEvent;
      bar.dispatchEvent(new Ev(type, {bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0}));
    });
    return true;
  };
  var shown = "", ok = false;
  for (var attempt = 0; attempt < 5 && !ok; attempt++) {
    if (attempt >= 2) timeline();
    seek();
    var there = await settled(attempt ? 8000 : 4000);
    if (!there && main) {                 // stuck on the old spot: seek during a blink of play, then stop there
      try { await main.play(); } catch (e) { /* autoplay blocked: carry on */ }
      main.currentTime = sec + 0.04; await sleep(400);
      main.pause(); main.currentTime = sec + 0.04;
      await settled(4000);
    }
    var tcEl = document.querySelector("[data-testid=timecode]") || document.querySelector("[data-testid=time-display]");
    if (tcEl) { tcEl.click(); await sleep(250); document.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", bubbles: true})); await sleep(150); }
    comp.focus(); comp.click(); await sleep(400);
    var box = document.querySelector("[data-testid=create-comment-composer-container]") || document;
    var pre = Array.prototype.slice.call(box.querySelectorAll("*")).find(function (e) {
      return e.childElementCount === 0 && /^\d\d:\d\d:\d\d:\d\d$/.test((e.textContent || "").trim());
    });
    shown = pre ? pre.textContent.trim() : "";
    ok = Math.abs(tcSec(shown) - sec) <= 1.01;     // a frame or two either side is the same moment
  }
  if (!ok) return {ok: false, error: "couldn't move the player to " + hms(sec) + " (it stayed at " + (shown.slice(0, 8) || "an unknown time") + ")"};
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
