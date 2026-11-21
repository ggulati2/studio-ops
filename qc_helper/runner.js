/* Red Wed QC Helper: runs one AutoCheck.
   1. Open V1 and V2 on Frame.io, read V1's comments, find each file's video and audio streams.
   2. Quick pass (whole film, small preview, every half second) + audio, for both.
   3. Line V1 up with V2, then the HD pass (1080p) on each comment point, every cut and each shot.
   4. Verdict per V1 comment + new issues on V2 -> posted on V2 as [Hygiene] comments.
   5. Report back to Studio Ops, with what QC did on V1 with the last check's flags (kept,
      deleted, answered, or their own notes nearby): Studio Ops learns from that. */
(function () {
  "use strict";
  var AC = window.AC, jobId = location.hash.slice(1), job = null, portalTab = null, myWindow;
  var started = Date.now(), logEl = document.getElementById("log"), barEl = document.getElementById("bar");

  function log(text, frac) {
    var li = document.createElement("li"); li.textContent = text; logEl.appendChild(li);
    if (frac != null) barEl.style.width = Math.round(frac * 100) + "%";
    tell({type: "autocheck:progress", id: jobId, text: text, frac: frac});
  }
  function tell(msg) {
    msg.source = "redwed-helper";
    if (msg.type === "autocheck:done" || msg.type === "autocheck:failed") {
      // kept until Studio Ops confirms it saved it (the portal tab may have been closed or moved on)
      msg.film = job && job.film;
      var keep = {};
      keep["result:" + jobId] = {msg: msg, origin: job && job.portal ? new URL(job.portal).origin : ""};
      chrome.storage.local.set(keep);
    }
    if (portalTab != null) chrome.tabs.sendMessage(portalTab, msg, function () { void chrome.runtime.lastError; });
  }
  function inTab(tabId, func, args) {
    return chrome.scripting.executeScript({target: {tabId: tabId}, func: func, args: args || [], world: "MAIN"})
      .then(function (r) { return r && r[0] ? r[0].result : null; });
  }
  function openTab(url, label) {
    return new Promise(function (res, rej) {
      chrome.tabs.create({url: url, active: false, windowId: myWindow}, function (tab) {
        var done = false;
        var finish = function (ok, why) {
          if (done) return; done = true; chrome.tabs.onUpdated.removeListener(check);
          if (ok) res(tab.id); else { chrome.tabs.remove(tab.id, function () { void chrome.runtime.lastError; }); rej(new Error(why)); }
        };
        var check = function (id, info, t) {
          if (id !== tab.id || info.status !== "complete") return;
          var u = (t && t.url) || "";
          if (/^https:\/\/([a-z0-9-]+\.)?frame\.io\//.test(u)) finish(true);
          else if (!/^https:\/\/f\.io\//.test(u)) finish(false, label + " link didn't open on Frame.io (" + (u.split("/")[2] || "no page") + "). Check the link and that you can view it in this Chrome.");
        };
        chrome.tabs.onUpdated.addListener(check);
        setTimeout(function () { finish(false, label + " link took too long to open."); }, 60000);
      });
    });
  }
  async function prepare(tabId) {
    // the stream only starts loading on a visible tab: show it for a moment, then come back here
    var me = await chrome.tabs.getCurrent(), back = null;
    if (job.auto) {
      // a check that started by itself runs in its own window behind; bring it forward for a few seconds
      try { back = await chrome.windows.getLastFocused(); } catch (e) { back = null; }
      await chrome.windows.update(myWindow, {focused: true});
    }
    await chrome.tabs.update(tabId, {active: true});
    var info = await inTab(tabId, fioPrepare);
    await chrome.tabs.update(me.id, {active: true});
    if (back && back.id !== myWindow) chrome.windows.update(back.id, {focused: true}, function () { void chrome.runtime.lastError; });
    return info;
  }

  var fmt = AC.fmt;
  var SENS = {low: 1.4, medium: 1, high: 0.75};

  function kind(text) {
    var t = (text || "").toLowerCase();
    if (/jump ?cut/.test(t)) return "jump";
    if (/shak|stabil|jitter|wobbl|warp/.test(t)) return "shake";
    if (/font|title|spell|typo|caption|lower ?third|subtitle|text|name|logo|watermark/.test(t)) return "text";
    if (/sound|audio|glitch|noise|music|volume|loud|voice|mic\b|click|pop|hiss|bgm|song|track|dialog/.test(t)) return "audio";
    if (/black|dark|bright|exposure|contrast|grade|grading|colou?r|warm|cool|white ?balance|saturat|skin|tone|lut/.test(t)) return "look";
    if (/cut|remove|trim|delete|start|end|add|insert|speech|shot|replace|move|edit|extend|shorten|don.?t|keep|transition|slow|fast|speed/.test(t)) return "edit";
    return "other";
  }
  var throughout = function (t) { return /through ?out|thru ?out|thur ?out|thro ?out|whole film|all over|entire|everywhere|full film/i.test(t || ""); };
  var TAG = "[Hygiene] ";
  var ours = function (text) { return /^\s*\[(AutoCheck|Hygiene)\]/i.test(text || ""); };
  var clean = function (s) { return (s || "").replace(/^\s*\[(AutoCheck|Hygiene)\]\s*/i, "").replace(/#/g, "no. ").replace(/\s+/g, " ").trim(); };

  async function main() {
    var got = await chrome.storage.local.get("job:" + jobId);
    var rec = got["job:" + jobId];
    if (!rec) { log("This AutoCheck has expired. Start it again from Studio Ops."); return; }
    job = rec.job; portalTab = rec.portalTab;
    myWindow = (await chrome.tabs.getCurrent()).windowId;
    var P = job.prev_label || "V1", N = job.cur_label || "V2";
    document.getElementById("title").textContent = "AutoCheck · " + (job.film || "");
    var rules = job.rules || {}, s = SENS[rules.sensitivity] || 1, who = job.checker || "QC";
    if (job.repost) return repost(N);
    var report = {version: chrome.runtime.getManifest().version, comments: [], issues: [], posted: [], structure: [],
                  seen: [], followup: [], cands: {}, tune: rules.tune || {}};
    tell({type: "autocheck:status", id: jobId, status: "running"});

    // ---- 1. open the files
    log("Opening the files on Frame.io…", 0.02);
    var t2 = await openTab(job.v2, "The V2"), t1 = job.v1 ? await openTab(job.v1, "The V1") : null;
    var I2 = await prepare(t2), I1 = t1 ? await prepare(t1) : null;
    if (!I2 || I2.error) throw new Error("V2: " + ((I2 && I2.error) || "couldn't open"));
    if (t1 && (!I1 || I1.error)) throw new Error("V1: " + ((I1 && I1.error) || "couldn't open"));
    report.v2 = {title: I2.title, duration: I2.duration, quality: I2.quality};
    var comments = [];
    if (t1) {
      log("Reading " + P + "'s comments…", 0.05);
      comments = await inTab(t1, fioComments) || [];
      report.v1 = {title: I1.title, duration: I1.duration, comments: comments.length};
      log(comments.length + " comment(s) on " + P + ".", 0.06);
    }
    var maxFF = 0;
    comments.forEach(function (c) { maxFF = Math.max(maxFF, +c.tc.slice(9)); });
    var fps = maxFF >= 25 ? 30 : 25;
    // every comment on the earlier version, ours marked: QC's notes and what's left of our flags teach Studio Ops
    report.seen = comments.map(function (c) {
      return {t: Math.round(AC.tcToSec(c.tc, fps) * 10) / 10, text: (c.text || "").slice(0, 300), ours: ours(c.text)};
    });

    // ---- 2. quick pass + audio
    var Q2, A2, Q1, A1;
    log("Quick pass on " + N + " (whole film)…", 0.08);
    Q2 = await AC.quickPassHLS(I2.vBase, I2.duration, 0.5, function (f) { barEl.style.width = Math.round((0.08 + f * 0.17) * 100) + "%"; });
    log("Listening to " + N + "'s audio…", 0.26);
    A2 = await AC.audioPass(I2.aBase);
    if (I1) {
      log("Quick pass on " + P + "…", 0.32);
      Q1 = await AC.quickPassHLS(I1.vBase, I1.duration, 0.5, function (f) { barEl.style.width = Math.round((0.32 + f * 0.17) * 100) + "%"; });
      log("Listening to " + P + "'s audio…", 0.5);
      A1 = await AC.audioPass(I1.aBase);
    }
    var HD2 = new AC.HD(I2.vBase), HD1 = I1 ? new AC.HD(I1.vBase) : null;

    // ---- 3. line up and check each V1 comment
    var Al = null;
    if (Q1) {
      log("Lining up " + P + " with " + N + "…", 0.55);
      Al = AC.align(Q1, Q2);
      report.structure = Al.segs.map(function (g) { return {kind: g.kind, from: g.from, to: g.to}; });
    }
    var crushedV2 = AC.hyg.crushed(AC.hyg.out(rules.tune || {}), Q2, false);
    for (var ci = 0; ci < comments.length; ci++) {
      var c = comments[ci], tv1 = AC.tcToSec(c.tc, fps), k = kind(c.text), m = AC.toV2(Al, tv1);
      var tv2 = m ? m.t : null, near = AC.changedNear(Al, tv1, tv2, 6);
      var r = {n: c.n, tc: c.tc, t1: tv1, t2: tv2, text: c.text, kind: k, verdict: "check", note: ""};
      if (ours(c.text)) {      // our own flag from last time: the same check runs again below
        r.ours = true; r.note = "checked again by AutoCheck";
        report.comments.push(r);
        continue;
      }
      log("Checking " + P + " comment " + (c.n || ci + 1) + " (" + fmt(tv1) + ")…", 0.56 + 0.2 * ci / Math.max(1, comments.length));
      try {
        if (tv2 == null) { r.note = "this part isn't in V2 any more"; r.verdict = k === "edit" ? "fixed" : "check"; }
        else if (k === "text") {
          var fa = await HD1.frames(tv1, tv1 + 0.2, 10, 320), fb = await HD2.frames(tv2, tv2 + 0.2, 10, 320);
          if (fa.length && fb.length) {
            // titles are a small part of the frame, so look at the most-changed region, not the whole frame
            var bm = Math.max.apply(null, AC.blocks(fa[0], fb[0]));
            r.measure = [Math.round(bm)];
            if (bm >= 12) { r.verdict = "fixed"; r.note = "the text/graphic changed"; }
            else if (bm < 6) { r.verdict = "not_fixed"; r.note = "the frame looks the same as V1"; }
            else { r.verdict = "check"; r.note = "only a small change here; please check the text"; }
            if (/spell|name|typo/i.test(c.text) && r.verdict === "fixed") { r.verdict = "check"; r.note = "the text changed; please check the spelling"; }
          }
        } else if (k === "look") {
          var i1 = Math.round(tv1 / 0.5), i2 = Math.round(tv2 / 0.5);
          var dl = Q2.lum[i2] - Q1.lum[i1], dp = Q2.p5[i2] - Q1.p5[i1], dh = Q2.p95[i2] - Q1.p95[i1];
          var drgb = Math.max(Math.abs(Q2.rgb[i2 * 3] - Q1.rgb[i1 * 3]), Math.abs(Q2.rgb[i2 * 3 + 2] - Q1.rgb[i1 * 3 + 2]));
          var changedHere = Math.abs(dl) >= 4 || Math.abs(dp) >= 4 || Math.abs(dh) >= 4 || drgb >= 5;
          if (throughout(c.text)) {
            var ch = 0, tot = 0;
            for (var q = 0; q < Q1.n; q++) { var j = Al.map[q]; if (j < 0) continue; tot++; if (Math.abs(Q2.lum[j] - Q1.lum[q]) >= 3 || Math.abs(Q2.p5[j] - Q1.p5[q]) >= 3) ch++; }
            var pct = Math.round(100 * ch / Math.max(1, tot));
            if (/black|dark/i.test(c.text) && crushedV2.length) {
              r.verdict = "not_fixed";
              r.note = "blacks are still crushed at " + crushedV2.map(function (x) { return fmt(x[0]) + "–" + fmt(x[1]); }).join(", ");
              r.ranges = crushedV2;
            } else if (pct < 30) { r.verdict = "check"; r.note = "only about " + pct + "% of the film changed; please check it across the whole film"; }
            else { r.verdict = "fixed"; r.note = "about " + pct + "% of the film was adjusted"; }
          } else if (changedHere) { r.verdict = "fixed"; r.note = "the look changed here"; }
          else { r.verdict = "not_fixed"; r.note = "brightness and colour look the same as V1"; }
        } else if (k === "shake") {
          var s1 = AC.shake(await HD1.frames(tv1 - 1, tv1 + 1, 12.5, 320)), s2 = AC.shake(await HD2.frames(tv2 - 1, tv2 + 1, 12.5, 320));
          r.measure = [s1, s2];
          if (s1 == null || s2 == null) { r.verdict = "check"; r.note = "couldn't measure the motion"; }
          else if (s1 < 0.2) { r.verdict = "check"; r.note = "the shake is too slight to measure here; please look"; }
          else if (s2 < s1 * 0.5) { r.verdict = "fixed"; r.note = "the shake is reduced"; }
          else { r.verdict = "not_fixed"; r.note = "the shake looks the same as V1"; }
        } else if (k === "audio") {
          var a1 = Math.round(tv1 / 0.05), a2 = Math.round(tv2 / 0.05), best = -1, bs = 0;
          var e1 = function (x) { return Math.min(255, Math.round(A1.rms[x] * 400)); }, e2 = function (x) { return Math.min(255, Math.round(A2.rms[x] * 400)); };
          for (var sh = -20; sh <= 20; sh++) {
            var aa = 0, bb = 0, ab = 0;
            for (var w = -30; w <= 30; w++) { var x1 = e1(a1 + w) || 0, x2 = e2(a2 + sh + w) || 0; aa += x1 * x1; bb += x2 * x2; ab += x1 * x2; }
            var cc = ab / Math.sqrt(aa * bb + 1); if (cc > best) { best = cc; bs = sh; }
          }
          var mad = 0, sp1 = 0, sp2 = 0;
          for (var w2 = -30; w2 <= 30; w2++) {
            mad += Math.abs((e1(a1 + w2) || 0) - (e2(a2 + bs + w2) || 0));
            sp1 = Math.max(sp1, A1.score[a1 + w2] || 0); sp2 = Math.max(sp2, A2.score[a2 + bs + w2] || 0);
          }
          mad /= 61; r.measure = [Math.round(mad * 10) / 10, Math.round(sp1 * 10) / 10, Math.round(sp2 * 10) / 10];
          if (mad < 6 && sp1 < 1) { r.verdict = "check"; r.note = "the audio is unchanged, but the glitch is too subtle to measure; please listen"; }
          else if (mad < 6 && sp2 >= sp1 * 0.7) { r.verdict = "not_fixed"; r.note = "the audio here is unchanged from V1"; }
          else if (sp2 > 1.4 && sp2 >= sp1 * 0.5) { r.verdict = "check"; r.note = "the audio changed but a spike may still be there; please listen"; }
          else { r.verdict = "fixed"; r.note = "the audio here was reworked"; }
        } else if (k === "jump") {
          var fr = await HD2.frames(tv2 - 1.5, tv2 + 1.5, fps, 320), look = AC.cutLook(fr);
          if (look && look.jump != null) { r.verdict = "not_fixed"; r.note = "a jump cut is still there"; }
          else if (near.length) { r.verdict = "fixed"; r.note = "this part was re-edited"; }
          else { r.verdict = "check"; r.note = "no jump found, but please confirm"; }
        } else if (k === "edit") {
          if (near.length) { r.verdict = "fixed"; r.note = "this part was re-edited (" + near.map(function (g) { return g.kind + " " + Math.round(g.to - g.from) + "s"; }).join(", ") + ")"; }
          else if (/remove|cut|trim|delete|shorten/i.test(c.text)) { r.verdict = "not_fixed"; r.note = "nothing was removed here"; }
          else { r.verdict = "check"; r.note = "no change found here; please check"; }
        } else {
          r.note = near.length ? "this part changed; please check it matches the note" : "can't judge this one automatically";
        }
      } catch (err) { r.verdict = "check"; r.note = "couldn't check (" + err.message + ")"; }
      report.comments.push(r);
    }

    // ---- 4. new issues on V2 (hygiene)
    var o = AC.hyg.out(rules.tune || {}), fmtS = fmt;
    var already = function (t) {   // a QC comment on V1 already covers this moment (and isn't fixed)
      return report.comments.some(function (r) { return !r.ours && r.t2 != null && Math.abs(r.t2 - t) < 3 && r.verdict !== "fixed"; });
    };
    // flagged but covered by a QC comment: measured (Studio Ops learns from it) but not posted twice
    var flagC = function (kind, sev, t, end, text) {
      var before = o.issues.length;
      o.flag(kind, sev, t, end, text);
      if (o.issues.length > before && already(t)) o.issues[o.issues.length - 1].covered = true;
    };
    log("Looking for new issues on " + N + "…", 0.78);
    var dur = Q2.dur;
    if (rules.min_seconds && dur < rules.min_seconds) o.issues.push({kind: "length", t: 0, sev: 1, text: "The film is " + fmtS(dur) + " long, shorter than the " + fmtS(rules.min_seconds) + " minimum."});
    if (rules.max_seconds && dur > rules.max_seconds) o.issues.push({kind: "length", t: 0, sev: 1, text: "The film is " + fmtS(dur) + " long, longer than the " + fmtS(rules.max_seconds) + " maximum."});
    var ctx = {o: o, Q: Q2, A: A2, rules: rules, s: s, skip: already};
    AC.hyg.run(ctx);
    // crushed stretches a V1 "too dark" comment already reports aren't posted twice
    o.issues = o.issues.filter(function (x) {
      return x.kind !== "blacks" || !report.comments.some(function (r) { return r.ranges && r.ranges.some(function (g) { return Math.abs(g[0] - x.t) < 0.6; }); });
    });
    if (rules.black_frames !== false) {
      // black stretches: look at each edge in HD. A fade in or out is style, not a mistake
      var runsB = AC.hyg.blackRuns(Q2);
      for (var bi = 0; bi < runsB.length && bi < 40; bi++) {
        var rb = runsB[bi];
        try {
          var pre = await HD2.frames(rb.t - 1.4, rb.t + 0.3, fps, 64), post = await HD2.frames(rb.end - 0.3, rb.end + 1.4, fps, 64);
          var lumOf = function (f) { return AC.meanLum(f); };
          var outK = AC.hyg.edgeKind(pre.map(lumOf)), inK = AC.hyg.edgeKind(post.map(lumOf).reverse());
          if (outK !== "unknown" || inK !== "unknown") rb.edges = {fadeOut: outK === "fade", fadeIn: inK === "fade"};
        } catch (e) { /* the quick pass decides */ }
      }
      AC.hyg.blackFrames(o, Q2, runsB);
    }
    // HD pass: every cut, and the middle of each shot
    var cuts = AC.cuts(Q2).slice(0, 300);
    log("HD pass: " + cuts.length + " cuts…", 0.8);
    for (var cc2 = 0; cc2 < cuts.length; cc2++) {
      var tc2 = cuts[cc2];
      if (cc2 % 10 === 0) barEl.style.width = Math.round((0.8 + 0.08 * cc2 / Math.max(1, cuts.length)) * 100) + "%";
      try {
        var fr2 = await HD2.frames(tc2 - 0.6, tc2 + 0.6, fps, 160), lk = AC.cutLook(fr2);
        if (rules.media_offline !== false) {
          var off = fr2.filter(function (f) { return f.red >= 0.32; }).sort(function (a, b) { return b.red - a.red; })[0];
          if (off && !o.issues.some(function (x) { return x.kind === "media_offline" && Math.abs(x.t - off.t) < 2; }))
            o.flag("media_offline", off.red / 0.8, off.t, null, "Media Offline frame (red screen) at " + fmtS(off.t) + ". A clip is missing in the export: please relink it and export again.");
        }
        if (!lk || lk.dark) continue;     // cuts into / out of black are fades, not jumps
        if (rules.black_frames !== false && lk.flash != null) flagC("flash", lk.flashSev || 1, lk.flash, null, "Flash frame at " + fmtS(lk.flash) + " (one frame that doesn't belong).");
        if (rules.jump_cuts !== false && lk.jump != null) flagC("jump_cut", lk.jumpSev || 1, lk.jump, null, "Possible jump cut at " + fmtS(lk.jump) + ".");
        else if (rules.colour_jumps !== false && lk.colour != null) flagC("colour_jump", lk.colourSev || 1, lk.colour, null, "Colour/exposure jump between shots at " + fmtS(lk.colour) + ". Please match the shots.");
      } catch (e) { /* a segment that won't load: skip */ }
    }
    if (rules.shake !== false || rules.blur !== false) {
      // the middle of each shot; long shots (or two shots the cut finder took as one) every ~4 s
      var bounds = [0].concat(cuts, [dur]), shots = [];
      for (var b2 = 1; b2 < bounds.length; b2++) {
        var len = bounds[b2] - bounds[b2 - 1];
        if (len < 2.5) continue;
        var parts = Math.max(1, Math.min(4, Math.floor(len / 4)));
        for (var pp = 0; pp < parts; pp++) shots.push(bounds[b2 - 1] + len * (pp + 0.5) / parts);
      }
      shots = shots.slice(0, 300);
      log("HD pass: " + (rules.shake !== false ? "camera shake" + (rules.blur !== false ? " and focus" : "") : "focus") + " in " + shots.length + " shots…", 0.88);
      var sharpness = [];
      for (var si = 0; si < shots.length; si++) {
        if (si % 10 === 0) barEl.style.width = Math.round((0.88 + 0.06 * si / Math.max(1, shots.length)) * 100) + "%";
        try {
          var sf = await HD2.frames(shots[si] - 0.8, shots[si] + 0.8, 12.5, 320);
          if (rules.shake !== false) {
            var j3 = AC.shake(sf);
            if (j3 != null) flagC("shake", j3 / (0.75 * s), shots[si], null, "Camera shake in this shot. Please stabilise.");
          }
          if (rules.blur !== false && sf.length >= 3) {
            // three frames across the shot; the sharpest counts (a pan blurs some frames on purpose)
            var pick = [sf[0], sf[Math.floor(sf.length / 2)], sf[sf.length - 1]].map(AC.sharpness);
            var bestS = pick.reduce(function (a, b) { return b.sharp > a.sharp ? b : a; });
            sharpness.push({t: shots[si], sharp: bestS.sharp, lum: bestS.lum, std: bestS.std});
          }
        } catch (e) { /* skip */ }
      }
      if (rules.blur !== false) {
        var nb = o.issues.length;
        AC.hyg.blur(o, sharpness);
        o.issues.slice(nb).forEach(function (x) { if (already(x.t)) x.covered = true; });
      }
    }
    var found = o.finish();
    report.issues = found.issues;
    report.cands = found.cands;
    var issues = report.issues;
    var near = function (kind, t) { return issues.filter(function (x) { return x.kind === kind && x.t - 2.5 <= t && t <= (x.end || x.t) + 2.5; })[0]; };

    // what happened to the last check's flags on the earlier version: still here in this one, or gone?
    (job.prev_issues || []).forEach(function (pi) {
      var m2 = Al ? AC.toV2(Al, pi.t) : null, f = {i: pi.i, kind: pi.kind};
      if (!m2 || ["length", "loudness"].indexOf(pi.kind) >= 0) f.still = null;
      else {
        f.t2 = m2.t;
        f.still = !!near(pi.kind, m2.t);
        if (!f.still && AC.changedNear(Al, pi.t, m2.t, 1).length) f.still = null;      // that part was re-cut: can't tell
      }
      report.followup.push(f);
    });
    // our own flags from last time: fixed if the same check no longer finds them
    report.comments.forEach(function (r) {
      if (!r.ours) return;
      var pi = (job.prev_issues || []).filter(function (x) { return Math.abs(x.t - r.t1) <= 1.5; })[0];
      var again = r.t2 == null ? null : pi ? near(pi.kind, r.t2) : issues.filter(function (x) { return Math.abs(x.t - r.t2) <= 2.5; })[0];
      if (r.t2 == null) { r.verdict = "fixed"; r.note = "this part isn't in " + N + " any more"; }
      else if (again) { r.verdict = "not_fixed"; r.note = "still there: flagged again on " + N; again.repeat = true; }
      else if (pi && ["length", "loudness"].indexOf(pi.kind) >= 0) { r.verdict = "check"; r.note = "please check"; }
      else { r.verdict = "fixed"; r.note = "AutoCheck no longer finds it"; }
    });

    // ---- 5. post on V2
    var posts = [];
    report.comments.forEach(function (r) {
      if (r.verdict === "fixed" || r.t2 == null || r.ours) return;      // our own: re-flagged below if still there
      var lead = r.verdict === "not_fixed" ? "Pending from " + P + " comment " + (r.n || "") + ": " : who + " needs to check: " + P + " comment " + (r.n || "") + " (";
      var body = r.verdict === "not_fixed" ? clean(r.text) + " (" + r.note + ")." : clean(r.text) + "): " + r.note + ".";
      if (r.ranges) r.ranges.forEach(function (x) { posts.push({t: x[0], text: TAG + "Pending from " + P + " comment " + (r.n || "") + ": " + clean(r.text) + " (still crushed from " + fmt(x[0]) + " to " + fmt(x[1]) + ")."}); });
      else posts.push({t: r.t2, text: TAG + lead + body});
    });
    issues.forEach(function (x, i) {
      if (x.covered) return;
      posts.push({t: x.t, text: TAG + (x.repeat ? "Still there from " + P + ": " : "") + x.text, issue: i, kind: x.kind});
    });
    // every comment goes up, across the whole film. Only past a safety ceiling are the least
    // important kept back (QC's pending notes first, then the strongest flags); those are listed
    // as not posted, so "Post them again" on the report can still put them up
    var MAX_POSTS = 400;
    if (posts.length > MAX_POSTS) {
      var rank = function (p) { return p.issue == null ? 1000 : (issues[p.issue] && issues[p.issue].sev) || 1; };
      posts.sort(function (a, b) { return rank(b) - rank(a); });
      posts.slice(MAX_POSTS).forEach(function (p) {
        report.posted.push({t: p.t, text: p.text, ok: false, error: "kept back: more than " + MAX_POSTS + " comments", issue: p.issue, kind: p.kind});
      });
      posts = posts.slice(0, MAX_POSTS);
    }
    posts.sort(function (a, b) { return a.t - b.t; });
    log("Posting " + posts.length + " comment(s) on " + N + "…", 0.95);
    var me = await chrome.tabs.getCurrent();
    await chrome.tabs.update(t2, {active: true});
    for (var pi = 0; pi < posts.length; pi++) {
      // keep Studio Ops informed: a long posting run must not look stalled
      if (pi && pi % 10 === 0) log("Posted " + pi + " of " + posts.length + " (at " + fmt(posts[pi].t) + ")…", 0.95 + 0.04 * pi / posts.length);
      var res = await inTab(t2, fioPost, [Math.max(0, posts[pi].t), posts[pi].text]);
      report.posted.push({t: posts[pi].t, text: posts[pi].text, ok: !!(res && res.ok), error: res && res.error,
                          issue: posts[pi].issue, kind: posts[pi].kind});
    }
    // a second go at any that didn't post (the player is warmed up by now)
    var missed = report.posted.filter(function (x) { return !x.ok; });
    if (missed.length) {
      log("Trying " + missed.length + " comment(s) again…", 0.98);
      for (var mi = 0; mi < missed.length; mi++) {
        var res2 = await inTab(t2, fioPost, [Math.max(0, missed[mi].t), missed[mi].text]);
        if (res2 && res2.ok) { missed[mi].ok = true; delete missed[mi].error; }
        else if (res2) missed[mi].error = res2.error;
      }
    }
    await chrome.tabs.update(me.id, {active: true});
    var okN = report.posted.filter(function (p) { return p.ok; }).length;
    report.seconds = Math.round((Date.now() - started) / 1000);
    log("Done: " + okN + " of " + posts.length + " comment(s) posted on " + N + " in " + Math.round(report.seconds / 60) + " min.", 1);
    tell({type: "autocheck:done", id: jobId, report: report, posted: okN});
    chrome.tabs.remove([t2].concat(t1 ? [t1] : []));
    chrome.storage.local.remove("job:" + jobId);
    document.getElementById("done").hidden = false;
    if (job.auto) setTimeout(function () { chrome.windows.remove(myWindow); }, 8000);   // its own window: tidy up
  }

  /* only post comments that didn't go up last time (from the report's "Post them again") */
  async function repost(N) {
    tell({type: "autocheck:status", id: jobId, status: "running"});
    log("Opening " + N + " on Frame.io…", 0.05);
    var t2 = await openTab(job.v2, "The " + N);
    var I2 = await prepare(t2);
    if (!I2 || I2.error) throw new Error(N + ": " + ((I2 && I2.error) || "couldn't open"));
    var me = await chrome.tabs.getCurrent(), out = [];
    await chrome.tabs.update(t2, {active: true});
    for (var i = 0; i < job.repost.length; i++) {
      var p = job.repost[i];
      if (i % 5 === 0) log("Posting " + (i + 1) + " of " + job.repost.length + " (" + AC.fmt(p.t) + ")…", 0.1 + 0.85 * i / job.repost.length);
      var res = await inTab(t2, fioPost, [Math.max(0, p.t), p.text]);
      out.push({t: p.t, text: p.text, ok: !!(res && res.ok), error: res && res.error});
    }
    await chrome.tabs.update(me.id, {active: true});
    var okN = out.filter(function (x) { return x.ok; }).length;
    log("Done: " + okN + " of " + out.length + " posted on " + N + ".", 1);
    tell({type: "autocheck:done", id: jobId, report: {repost: true, posted: out, version: chrome.runtime.getManifest().version}, posted: okN});
    chrome.tabs.remove([t2]);
    chrome.storage.local.remove("job:" + jobId);
    document.getElementById("done").hidden = false;
  }

  main().catch(function (err) {
    log("Stopped: " + err.message);
    tell({type: "autocheck:failed", id: jobId, error: err.message});
    if (job && job.auto) setTimeout(function () { chrome.windows.remove(myWindow); }, 60000);
  });
})();
