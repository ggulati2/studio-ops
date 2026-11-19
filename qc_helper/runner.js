/* Red Wed QC Helper: runs one AutoCheck.
   1. Open V1 and V2 on Frame.io, read V1's comments, find each file's video and audio streams.
   2. Quick pass (whole film, small preview, every half second) + audio, for both.
   3. Line V1 up with V2, then the HD pass (1080p) on each comment point, every cut and each shot.
   4. Verdict per V1 comment + new issues on V2 -> posted on V2 as [AutoCheck] comments.
   5. Report back to Studio Ops. */
(function () {
  "use strict";
  var AC = window.AC, jobId = location.hash.slice(1), job = null, portalTab = null;
  var started = Date.now(), logEl = document.getElementById("log"), barEl = document.getElementById("bar");

  function log(text, frac) {
    var li = document.createElement("li"); li.textContent = text; logEl.appendChild(li);
    if (frac != null) barEl.style.width = Math.round(frac * 100) + "%";
    tell({type: "autocheck:progress", id: jobId, text: text, frac: frac});
  }
  function tell(msg) {
    msg.source = "redwed-helper";
    if (portalTab != null) chrome.tabs.sendMessage(portalTab, msg, function () { void chrome.runtime.lastError; });
  }
  function inTab(tabId, func, args) {
    return chrome.scripting.executeScript({target: {tabId: tabId}, func: func, args: args || [], world: "MAIN"})
      .then(function (r) { return r && r[0] ? r[0].result : null; });
  }
  function openTab(url, label) {
    return new Promise(function (res, rej) {
      chrome.tabs.create({url: url, active: false}, function (tab) {
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
    var me = await chrome.tabs.getCurrent();
    await chrome.tabs.update(tabId, {active: true});
    var info = await inTab(tabId, fioPrepare);
    await chrome.tabs.update(me.id, {active: true});
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
  var throughout = function (t) { return /through ?out|thru ?out|whole film|all over|entire|everywhere|full film/i.test(t || ""); };
  var clean = function (s) { return (s || "").replace(/#/g, "no. ").replace(/\s+/g, " ").trim(); };

  function runs(n, pred, minLen, step) {
    var out = [], k = 0;
    while (k < n) { if (pred(k)) { var e = k; while (e < n && pred(e)) e++; if (e - k >= minLen) out.push([k * step, e * step]); k = e; } else k++; }
    return out;
  }

  async function main() {
    var got = await chrome.storage.local.get("job:" + jobId);
    var rec = got["job:" + jobId];
    if (!rec) { log("This AutoCheck has expired. Start it again from Studio Ops."); return; }
    job = rec.job; portalTab = rec.portalTab;
    document.getElementById("title").textContent = "AutoCheck · " + (job.film || "");
    var rules = job.rules || {}, s = SENS[rules.sensitivity] || 1, who = job.checker || "QC";
    var report = {version: chrome.runtime.getManifest().version, comments: [], issues: [], posted: [], structure: []};
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
      log("Reading V1's comments…", 0.05);
      comments = await inTab(t1, fioComments) || [];
      report.v1 = {title: I1.title, duration: I1.duration, comments: comments.length};
      log(comments.length + " comment(s) on V1.", 0.06);
    }
    var maxFF = 0;
    comments.forEach(function (c) { maxFF = Math.max(maxFF, +c.tc.slice(9)); });
    var fps = maxFF >= 25 ? 30 : 25;

    // ---- 2. quick pass + audio
    var Q2, A2, Q1, A1;
    log("Quick pass on V2 (whole film)…", 0.08);
    Q2 = await AC.quickPass(await AC.loadProxy(I2.proxy), 0.5, function (f) { barEl.style.width = Math.round((0.08 + f * 0.17) * 100) + "%"; });
    log("Listening to V2's audio…", 0.26);
    A2 = await AC.audioPass(I2.aBase);
    if (I1) {
      log("Quick pass on V1…", 0.32);
      Q1 = await AC.quickPass(await AC.loadProxy(I1.proxy), 0.5, function (f) { barEl.style.width = Math.round((0.32 + f * 0.17) * 100) + "%"; });
      log("Listening to V1's audio…", 0.5);
      A1 = await AC.audioPass(I1.aBase);
    }
    var HD2 = new AC.HD(I2.vBase), HD1 = I1 ? new AC.HD(I1.vBase) : null;

    // ---- 3. line up and check each V1 comment
    var Al = null;
    if (Q1) {
      log("Lining up V1 with V2…", 0.55);
      Al = AC.align(Q1, Q2);
      report.structure = Al.segs.map(function (g) { return {kind: g.kind, from: g.from, to: g.to}; });
    }
    var crushedV2 = runs(Q2.n, function (i) { return Q2.p5[i] <= 2 && Q2.lum[i] > 40; }, 4, 0.5);
    for (var ci = 0; ci < comments.length; ci++) {
      var c = comments[ci], tv1 = AC.tcToSec(c.tc, fps), k = kind(c.text), m = AC.toV2(Al, tv1);
      var tv2 = m ? m.t : null, near = AC.changedNear(Al, tv1, tv2, 6);
      var r = {n: c.n, tc: c.tc, t1: tv1, t2: tv2, text: c.text, kind: k, verdict: "check", note: ""};
      log("Checking V1 comment " + (c.n || ci + 1) + " (" + fmt(tv1) + ")…", 0.56 + 0.2 * ci / Math.max(1, comments.length));
      try {
        if (tv2 == null) { r.note = "this part isn't in V2 any more"; r.verdict = k === "edit" ? "fixed" : "check"; }
        else if (k === "text") {
          var fa = await HD1.frames(tv1, tv1 + 0.2, 10, 320), fb = await HD2.frames(tv2, tv2 + 0.2, 10, 320);
          if (fa.length && fb.length) {
            var dd = AC.diff(fa[0], fb[0]);
            if (dd > 6) { r.verdict = "fixed"; r.note = "the text/graphic changed"; }
            else { r.verdict = "not_fixed"; r.note = "the frame looks the same as V1"; }
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
          if (mad < 6 && sp2 >= sp1 * 0.7) { r.verdict = "not_fixed"; r.note = "the audio here is unchanged from V1"; }
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
    var issues = report.issues, add = function (kind, t, text, end) { issues.push({kind: kind, t: t, end: end || null, text: text}); };
    var already = function (t) {   // skip what a V1 comment already covers at the same moment
      return report.comments.some(function (r) { return r.t2 != null && Math.abs(r.t2 - t) < 3 && r.verdict !== "fixed"; });
    };
    log("Looking for new issues on V2…", 0.78);
    var dur = Q2.dur;
    if (rules.min_seconds && dur < rules.min_seconds) add("length", 0, "The film is " + fmt(dur) + " long, shorter than the " + fmt(rules.min_seconds) + " minimum.");
    if (rules.max_seconds && dur > rules.max_seconds) add("length", 0, "The film is " + fmt(dur) + " long, longer than the " + fmt(rules.max_seconds) + " maximum.");
    if (rules.blacks !== false) crushedV2.forEach(function (x) {
      if (!report.comments.some(function (r) { return r.ranges && r.ranges.indexOf(x) >= 0; }))
        add("blacks", x[0], "Blacks look crushed (too dark) from " + fmt(x[0]) + " to " + fmt(x[1]) + ". Please lift the blacks.", x[1]);
    });
    if (rules.highlights !== false) runs(Q2.n, function (i) { return Q2.p95[i] >= 253 && Q2.lum[i] > 60; }, 4, 0.5).forEach(function (x) {
      add("highlights", x[0], "Highlights look blown out from " + fmt(x[0]) + " to " + fmt(x[1]) + ".", x[1]);
    });
    if (rules.black_frames !== false) runs(Q2.n, function (i) { return Q2.lum[i] < 6 && Q2.p95[i] < 20 && i * 0.5 > 2 && i * 0.5 < dur - 3; }, 1, 0.5).forEach(function (x) {
      if (x[1] - x[0] <= 3) add("black_frame", x[0], "Unexpected black frame(s) at " + fmt(x[0]) + ".");
    });
    // audio
    var m = A2.rms.length;
    if (rules.audio_peaks !== false) {
      var clips = [];
      for (var w3 = 0; w3 < m; w3++) if (A2.pk[w3] >= 0.985) clips.push(w3 * 0.05);
      var groups = [];
      clips.forEach(function (t) { if (!groups.length || t - groups[groups.length - 1][1] > 30) groups.push([t, t, 1]); else { groups[groups.length - 1][1] = t; groups[groups.length - 1][2]++; } });
      groups.filter(function (g) { return g[2] * 0.05 >= 0.5 * s; }).forEach(function (g) {
        add("audio_peaks", g[0], g[1] - g[0] > 5
          ? "Audio hits 0 dB (near clipping) on and off from " + fmt(g[0]) + " to " + fmt(g[1]) + ". Please bring the level down."
          : "Audio hits 0 dB (near clipping) at " + fmt(g[0]) + ". Please bring the level down.", g[1]);
      });
    }
    if (rules.loudness !== false) {
      var per = [];
      for (var mi = 0; mi * 60 < A2.dur; mi++) {
        var se = 0, cnt = 0;
        for (var j2 = mi * 1200; j2 < Math.min(m, (mi + 1) * 1200); j2++) { se += A2.rms[j2] * A2.rms[j2]; cnt++; }
        var db = 10 * Math.log10(se / Math.max(1, cnt) + 1e-12);
        if (db > -40 && cnt > 400) per.push([mi, db]);
      }
      if (per.length >= 4) {
        var lo = per.reduce(function (a, b) { return b[1] < a[1] ? b : a; }), hi = per.reduce(function (a, b) { return b[1] > a[1] ? b : a; });
        if (hi[1] - lo[1] > 12 * s) add("loudness", hi[0] * 60, "Loudness is uneven: around " + fmt(hi[0] * 60) + " the audio is about " + Math.round(hi[1] - lo[1]) + " dB louder than around " + fmt(lo[0] * 60) + ". Please level the audio.");
      }
    }
    if (rules.pops !== false) {
      var pops = [];
      for (var w4 = 0; w4 < m; w4++) if (A2.score[w4] > 6 / s && A2.rms[w4] > 0.01) { var tp = w4 * 0.05; if (!pops.length || tp - pops[pops.length - 1] > 3) pops.push(tp); }
      pops = pops.filter(function (t) { return !already(t); });
      if (pops.length) add("pops", pops[0], "Possible audio pop here, please listen." + (pops.length > 1 ? " Also check " + pops.slice(1, 8).map(fmt).join(", ") + (pops.length > 8 ? " and " + (pops.length - 8) + " more" : "") + "." : ""));
    }
    if (rules.silence !== false) {
      var k5 = 0;
      while (k5 < m) {
        if (A2.rms[k5] < 0.0015 && k5 * 0.05 > 3 && k5 * 0.05 < A2.dur - 5) {
          var e5 = k5; while (e5 < m && A2.rms[e5] < 0.0015) e5++;
          var len = (e5 - k5) * 0.05, vi = Math.round(k5 * 0.05 / 0.5);
          if (len >= 1.5 && Q2.lum[vi] > 25) add("silence", k5 * 0.05, "Sudden silence for " + len.toFixed(1) + " s. Intended?");
          k5 = e5;
        } else k5++;
      }
    }
    // HD pass: every cut, and the middle of each shot
    var cuts = AC.cuts(Q2).slice(0, 300);
    log("HD pass: " + cuts.length + " cuts…", 0.8);
    for (var cc2 = 0; cc2 < cuts.length; cc2++) {
      var tc2 = cuts[cc2];
      if (cc2 % 10 === 0) barEl.style.width = Math.round((0.8 + 0.08 * cc2 / Math.max(1, cuts.length)) * 100) + "%";
      try {
        var fr2 = await HD2.frames(tc2 - 0.6, tc2 + 0.6, fps, 160), lk = AC.cutLook(fr2);
        if (!lk) continue;
        if (rules.black_frames !== false && lk.flash != null && !already(lk.flash)) add("flash", lk.flash, "Flash frame at " + fmt(lk.flash) + " (one frame that doesn't belong).");
        if (rules.jump_cuts !== false && lk.jump != null && !already(lk.jump)) add("jump_cut", lk.jump, "Possible jump cut at " + fmt(lk.jump) + ".");
        else if (rules.colour_jumps !== false && lk.colour != null && !already(lk.colour)) add("colour_jump", lk.colour, "Colour/exposure jump between shots at " + fmt(lk.colour) + ". Please match the shots.");
      } catch (e) { /* a segment that won't load: skip */ }
    }
    if (rules.shake !== false) {
      var bounds = [0].concat(cuts, [dur]), shots = [];
      for (var b2 = 1; b2 < bounds.length; b2++) if (bounds[b2] - bounds[b2 - 1] >= 2.5) shots.push((bounds[b2] + bounds[b2 - 1]) / 2);
      shots = shots.slice(0, 250);
      log("HD pass: camera shake in " + shots.length + " shots…", 0.88);
      var shaky = [];
      for (var si = 0; si < shots.length; si++) {
        if (si % 10 === 0) barEl.style.width = Math.round((0.88 + 0.06 * si / Math.max(1, shots.length)) * 100) + "%";
        try {
          var j3 = AC.shake(await HD2.frames(shots[si] - 0.8, shots[si] + 0.8, 12.5, 320));
          if (j3 != null && j3 > 0.45 * s && !already(shots[si])) shaky.push(shots[si]);
        } catch (e) { /* skip */ }
      }
      shaky.forEach(function (t) { add("shake", t, "Camera shake in this shot. Please stabilise."); });
    }
    issues.sort(function (a, b) { return a.t - b.t; });

    // ---- 5. post on V2
    var posts = [];
    report.comments.forEach(function (r) {
      if (r.verdict === "fixed" || r.t2 == null) return;
      var lead = r.verdict === "not_fixed" ? "Pending from V1 comment " + (r.n || "") + ": " : who + " needs to check: V1 comment " + (r.n || "") + " (";
      var body = r.verdict === "not_fixed" ? clean(r.text) + " (" + r.note + ")." : clean(r.text) + "): " + r.note + ".";
      if (r.ranges) r.ranges.forEach(function (x) { posts.push({t: x[0], text: "[AutoCheck] Pending from V1 comment " + (r.n || "") + ": " + clean(r.text) + " (still crushed from " + fmt(x[0]) + " to " + fmt(x[1]) + ")."}); });
      else posts.push({t: r.t2, text: "[AutoCheck] " + lead + body});
    });
    issues.forEach(function (x) { posts.push({t: x.t, text: "[AutoCheck] " + x.text}); });
    posts.sort(function (a, b) { return a.t - b.t; });
    posts = posts.slice(0, 60);
    log("Posting " + posts.length + " comment(s) on V2…", 0.95);
    var me = await chrome.tabs.getCurrent();
    await chrome.tabs.update(t2, {active: true});
    for (var pi = 0; pi < posts.length; pi++) {
      var res = await inTab(t2, fioPost, [Math.max(0, posts[pi].t), posts[pi].text]);
      report.posted.push({t: posts[pi].t, text: posts[pi].text, ok: !!(res && res.ok), error: res && res.error});
    }
    await chrome.tabs.update(me.id, {active: true});
    var okN = report.posted.filter(function (p) { return p.ok; }).length;
    report.seconds = Math.round((Date.now() - started) / 1000);
    log("Done: " + okN + " of " + posts.length + " comment(s) posted on V2 in " + Math.round(report.seconds / 60) + " min.", 1);
    tell({type: "autocheck:done", id: jobId, report: report, posted: okN});
    chrome.tabs.remove([t2].concat(t1 ? [t1] : []));
    chrome.storage.local.remove("job:" + jobId);
    document.getElementById("done").hidden = false;
  }

  main().catch(function (err) {
    log("Stopped: " + err.message);
    tell({type: "autocheck:failed", id: jobId, error: err.message});
  });
})();
