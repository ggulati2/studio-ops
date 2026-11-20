/* Red Wed QC Helper: the hygiene checks on one version (no Frame.io here, so they can be tested
   on their own). Every finding has a strength `sev`: 1 = exactly at the limit, 2 = twice as bad.
   A check flags when sev reaches its tuned limit (`f`, 1 unless Studio Ops has learnt otherwise
   from what QC kept, removed or added). Near misses (sev 0.4 up to the limit) are kept as
   "candidates", so Studio Ops can tell later whether QC found something there. */
(function (root) {
  "use strict";
  var AC = root.AC = root.AC || {};
  var H = AC.hyg = {};
  var NEAR = 0.4, MAX_CANDS = 30;

  function fmt(s) { return AC.fmt ? AC.fmt(s) : String(Math.round(s)); }
  H.fmt = fmt;
  /* "from 0:12 to 0:20", or "at 0:12" for a moment */
  function span(a, b) { return fmt(b) === fmt(a) || b - a < 1.5 ? "at " + fmt(a) : "from " + fmt(a) + " to " + fmt(b); }

  /* collector: flag at the limit, remember near misses */
  H.out = function (factors) {
    var o = {issues: [], cands: {}, factors: factors || {}};
    o.f = function (kind) { var v = +o.factors[kind]; return v > 0 ? v : 1; };
    o.flag = function (kind, sev, t, end, text) {
      sev = Math.round(sev * 100) / 100; t = Math.round(t * 10) / 10; if (end != null) end = Math.round(end * 10) / 10;
      if (sev >= o.f(kind)) { o.issues.push({kind: kind, t: t, end: end == null ? null : end, text: text, sev: sev}); return true; }
      if (sev >= NEAR) {
        var c = o.cands[kind] = o.cands[kind] || [];
        c.push([Math.round(t * 10) / 10, sev]);
        if (c.length > MAX_CANDS * 2) { c.sort(function (a, b) { return b[1] - a[1]; }); c.length = MAX_CANDS; }
      }
      return false;
    };
    o.finish = function () {
      Object.keys(o.cands).forEach(function (k) {
        var c = o.cands[k]; c.sort(function (a, b) { return b[1] - a[1]; }); if (c.length > MAX_CANDS) c.length = MAX_CANDS;
      });
      o.issues.sort(function (a, b) { return a.t - b.t; });
      return {issues: o.issues, cands: o.cands};
    };
    return o;
  };

  /* stretches where sev[i] >= lim lasting at least minLen samples: [[i0, i1), …] */
  function stretches(n, sevAt, lim, minLen) {
    var out = [], k = 0;
    while (k < n) {
      if (sevAt(k) >= lim) { var e = k; while (e < n && sevAt(e) >= lim) e++; if (e - k >= minLen) out.push([k, e]); k = e; } else k++;
    }
    return out;
  }
  function mean(sevAt, a, b) { var s = 0; for (var i = a; i < b; i++) s += sevAt(i); return s / Math.max(1, b - a); }

  /* a level that runs for a while (crushed blacks, blown highlights): flag the stretches at the
     limit, and keep the strongest near-miss stretches as candidates */
  function ranged(o, kind, Q, sevAt, minLen, text) {
    var f = o.f(kind), step = Q.step, flagged = [];
    stretches(Q.n, sevAt, f, minLen).forEach(function (r) {
      o.flag(kind, Math.max(f, mean(sevAt, r[0], r[1])), r[0] * step, r[1] * step, text(r[0] * step, r[1] * step));
      flagged.push([r[0] * step, r[1] * step]);
    });
    stretches(Q.n, function (i) { var v = sevAt(i); return v < f ? v : 0; }, NEAR, minLen).forEach(function (r) {
      o.flag(kind, Math.min(f - 0.01, mean(sevAt, r[0], r[1])), r[0] * step, r[1] * step, "");
    });
    return flagged;
  }

  /* ---------------- picture (quick pass) ---------------- */
  /* a sample that was actually seen (gaps in the quick pass are copied from a neighbour: never evidence) */
  function seen(Q, i) { return !Q.has || Q.has[i]; }
  /* share of the frame inside black bars (32x18 quick-pass frames) */
  function barShare(Q, i) {
    var t = Q.bars[i * 4], b = Q.bars[i * 4 + 1], l = Q.bars[i * 4 + 2], r = Q.bars[i * 4 + 3];
    var rows = (t + b) / 18, cols = (l + r) / 32;
    return Math.min(0.95, rows + cols - rows * cols);
  }
  H.barShare = barShare;

  H.crushed = function (o, Q, on) {
    var sevAt = function (i) {
      if (!seen(Q, i)) return 0;
      var bs = barShare(Q, i), pic = Math.max(0.05, 1 - bs);
      var dark = Math.max(0, Q.dark[i] - bs) / pic, lum = (Q.lum[i] - 0) / pic;       // the picture only, bars left out
      return lum > 40 ? dark / 0.05 : 0;
    };
    if (!on) { var f = o.f("blacks"); return stretches(Q.n, sevAt, f, 4).map(function (r) { return [r[0] * Q.step, r[1] * Q.step]; }); }
    return ranged(o, "blacks", Q, sevAt, 4, function (a, b) {
      return "Blacks look crushed (too dark) " + span(a, b) + ". Please lift the blacks.";
    });
  };

  H.highlights = function (o, Q) {
    ranged(o, "highlights", Q, function (i) {
      var pic = Math.max(0.05, 1 - barShare(Q, i));
      return seen(Q, i) && Q.lum[i] / pic > 60 ? Q.bright[i] / pic / 0.05 : 0;
    }, 4, function (a, b) {
      return "Highlights look blown out " + span(a, b) + ".";
    });
  };

  /* black stretches in the middle of the film. Fades and dips to black are style, not mistakes:
     `edges` (from the HD pass) says for each stretch whether it starts / ends with a fade.
     Without HD edges, the quick pass is used: a picture that darkens step by step is a fade. */
  H.blackRuns = function (Q) {
    var dur = Q.dur, step = Q.step, out = [];
    stretches(Q.n, function (i) { return seen(Q, i) && Q.lum[i] < 6 && Q.p95[i] < 20 && i * step > 2 && i * step < dur - 3 ? 1 : 0; }, 1, 1)
      .forEach(function (r) { if ((r[1] - r[0]) * step <= 3) out.push({i0: r[0], i1: r[1], t: r[0] * step, end: r[1] * step}); });
    return out;
  };
  H.quickFade = function (Q, i0, i1) {
    var lum = Q.lum, before = lum[i0 - 1], ref = Math.max(lum[i0 - 2] || 0, lum[i0 - 3] || 0);
    var after = lum[i1], ref2 = Math.max(lum[i1 + 1] || 0, lum[i1 + 2] || 0);
    var out = i0 >= 2 && ref > 15 && before > 6 && before < ref * 0.8;
    var inn = i1 + 1 < Q.n && ref2 > 15 && after > 6 && after < ref2 * 0.8;
    return {fadeOut: out, fadeIn: inn};
  };
  /* frames (bright side first, black last) -> "fade" if the light goes down over several frames */
  H.edgeKind = function (lums) {
    if (lums.length < 4) return "unknown";
    var top = Math.max.apply(null, lums.slice(0, Math.max(2, lums.length >> 2)));
    if (top < 15) return "unknown";
    var mid = 0;
    for (var i = 0; i < lums.length; i++) if (lums[i] > 8 && lums[i] < top * 0.85) mid++;
    return mid >= 3 ? "fade" : "cut";
  };
  H.blackFrames = function (o, Q, runs) {
    runs.forEach(function (r) {
      var e = r.edges || H.quickFade(Q, r.i0, r.i1);
      if (e.fadeOut || e.fadeIn) return;                      // a fade to / from black: intended
      var len = Math.max(Q.step, r.end - r.t);
      o.flag("black_frame", 2.5 - len * 0.5, r.t, r.end, len <= 1
        ? "Unexpected black frame(s) at " + fmt(r.t) + "."
        : "Black gap of " + len.toFixed(1) + " s at " + fmt(r.t) + " (hard cut in and out, no fade). Missing clip?");
    });
  };

  H.offline = function (o, Q) {
    var step = Q.step;
    // Premiere's Media Offline screen is flat red with a little white text: most of the frame
    stretches(Q.n, function (i) { return seen(Q, i) ? Math.max(Q.red[i], Q.green[i]) / 0.8 : 0; }, NEAR, 1).forEach(function (r) {
      var red = 0, green = 0;
      for (var i = r[0]; i < r[1]; i++) if (seen(Q, i)) { red = Math.max(red, Q.red[i]); green = Math.max(green, Q.green[i]); }
      var a = r[0] * step, b = r[1] * step;
      o.flag("media_offline", Math.max(red, green) / 0.8, a, b, red >= green
        ? "Media Offline (red screen) " + span(a, b) + ". A clip is missing in the export: please relink it and export again."
        : "Green / broken frames " + span(a, b) + ". Please check the export.");
    });
  };

  /* black bars: what most of the film looks like is the house style; a stretch that differs is flagged */
  H.framing = function (o, Q) {
    var n = Q.n, step = Q.step, cls = new Array(n), count = {};
    for (var i = 0; i < n; i++) {
      if (Q.lum[i] < 20 || !seen(Q, i)) { cls[i] = null; continue; }
      var t = Q.bars[i * 4], b = Q.bars[i * 4 + 1], l = Q.bars[i * 4 + 2], r = Q.bars[i * 4 + 3];
      var lb = Math.min(t, b), pb = Math.min(l, r);
      cls[i] = lb >= 2 && pb >= 3 ? "border" : pb >= 4 ? "pillar" : lb >= 2 ? "letter" : "none";
      count[cls[i]] = (count[cls[i]] || 0) + 1;
    }
    var mode = Object.keys(count).sort(function (a, b) { return count[b] - count[a]; })[0];
    if (!mode) return;
    var size = function (i, c) {
      var t = Q.bars[i * 4], b = Q.bars[i * 4 + 1], l = Q.bars[i * 4 + 2], r = Q.bars[i * 4 + 3];
      if (c === "pillar") return Math.min(l, r) / 4;
      if (c === "letter") return Math.min(t, b) / 2;
      if (c === "border") return Math.min(Math.min(t, b) / 2, Math.min(l, r) / 3);
      return 1.2;     // bars missing where the rest of the film has them
    };
    var k = 0;
    while (k < n) {
      var c = cls[k];
      if (!c || c === mode || (c === "none" && mode !== "letter")) { k++; continue; }
      var e = k; while (e < n && (cls[e] === c || cls[e] === null)) e++;
      if (e - k >= 2) {
        var sev = 0; for (var j = k; j < e; j++) if (cls[j]) sev += size(j, c);
        sev /= Math.max(1, e - k);
        var a = k * step, z = e * step;
        o.flag("framing", sev, a, z,
          c === "pillar" ? "Side bars from " + fmt(a) + " to " + fmt(z) + ": looks like a vertical (phone) clip not filling the frame."
          : c === "border" ? "Black border around the picture from " + fmt(a) + " to " + fmt(z) + ": the clip isn't scaled to fill the frame."
          : c === "letter" ? "Black bars top and bottom from " + fmt(a) + " to " + fmt(z) + " only. The rest of the film has none."
          : "The black bars (letterbox) are missing from " + fmt(a) + " to " + fmt(z) + ". The rest of the film has them.");
      }
      k = e;
    }
  };

  /* shots that are soft compared with the rest of this film: [{t, sharp, lum, std}] */
  H.blur = function (o, shots) {
    var ok = shots.filter(function (s) { return s.lum > 25 && s.std > 18 && s.sharp > 0; });
    if (ok.length < 8) return;
    var med = ok.map(function (s) { return s.sharp; }).sort(function (a, b) { return a - b; })[Math.floor(ok.length / 2)];
    ok.forEach(function (s) {
      var r = s.sharp / med;
      o.flag("blur", 0.4 / Math.max(r, 0.05), s.t, null, "Shot looks out of focus (soft) here. Please check it or use a sharper take.");
    });
  };

  /* ---------------- audio ---------------- */
  function groups(times, gap) {
    var g = [];
    times.forEach(function (x) {
      if (!g.length || x[0] - g[g.length - 1].end > gap) g.push({t: x[0], end: x[0], vals: [x[1]]});
      else { g[g.length - 1].end = x[0]; g[g.length - 1].vals.push(x[1]); }
    });
    return g;
  }

  H.peaks = function (o, A, s) {
    var hits = [];
    for (var w = 0; w < A.rms.length; w++) if (A.pk[w] >= 0.985) hits.push([w * A.win, 1]);
    groups(hits, 30).forEach(function (g) {
      o.flag("audio_peaks", g.vals.length * A.win / (0.5 * s), g.t, g.end, g.end - g.t > 5
        ? "Audio hits 0 dB (near clipping) on and off from " + fmt(g.t) + " to " + fmt(g.end) + ". Please bring the level down."
        : "Audio hits 0 dB (near clipping) at " + fmt(g.t) + ". Please bring the level down.");
    });
  };

  /* distorted audio: waves flattened against their peak (recorded too hot), at a level below 0 dB.
     Needs both a pile-up at the peak and a squashed peak-to-average ratio. */
  H.clipping = function (o, A, s) {
    var hits = [];
    for (var w = 0; w < A.rms.length; w++) {
      if (!A.flat || A.pk[w] < 0.25 || A.pk[w] >= 0.95 || A.rms[w] < 0.02) continue;
      var crest = A.pk[w] / A.rms[w];
      var v = Math.min(A.flat[w] / (0.12 * s), 2.8 / Math.max(crest, 1));
      if (v >= NEAR) hits.push([w * A.win, v]);
    }
    groups(hits, 1).forEach(function (g) {
      if (g.vals.length < 3) return;
      var top = g.vals.slice().sort(function (a, b) { return b - a; }).slice(0, 4);
      var sev = top.reduce(function (a, b) { return a + b; }, 0) / 4;
      o.flag("clipping", sev, g.t, g.end + A.win, "Audio sounds distorted (clipped) " + span(g.t, g.end + A.win)
        + ". Please listen: use a cleaner take or lower the gain at the source.");
    });
  };

  H.loudness = function (o, A, s) {
    var per = [], m = A.rms.length, perMin = Math.round(60 / A.win);
    for (var mi = 0; mi * 60 < A.dur; mi++) {
      var se = 0, cnt = 0;
      for (var j = mi * perMin; j < Math.min(m, (mi + 1) * perMin); j++) { se += A.rms[j] * A.rms[j]; cnt++; }
      var db = 10 * Math.log10(se / Math.max(1, cnt) + 1e-12);
      if (db > -40 && cnt > perMin / 3) per.push([mi, db]);
    }
    if (per.length < 4) return;
    var lo = per.reduce(function (a, b) { return b[1] < a[1] ? b : a; }), hi = per.reduce(function (a, b) { return b[1] > a[1] ? b : a; });
    o.flag("loudness", (hi[1] - lo[1]) / (15 * s), hi[0] * 60, null, "Loudness is uneven: around " + fmt(hi[0] * 60) + " the audio is about "
      + Math.round(hi[1] - lo[1]) + " dB louder than around " + fmt(lo[0] * 60) + ". Please level the audio.");
  };

  H.pops = function (o, A, s, skip) {
    var hits = [];
    for (var w = 0; w < A.rms.length; w++) {
      if (A.rms[w] <= 0.01) continue;
      var v = A.score[w] * s / 6, t = w * A.win;
      if (v < NEAR || (skip && skip(t))) continue;
      if (hits.length && t - hits[hits.length - 1][0] <= 3) { if (v > hits[hits.length - 1][1]) hits[hits.length - 1][1] = v; continue; }
      hits.push([t, v]);
    }
    var f = o.f("pops"), real = hits.filter(function (h) { return h[1] >= f; });
    hits.filter(function (h) { return h[1] < f; }).forEach(function (h) { o.flag("pops", h[1], h[0], null, ""); });
    if (!real.length) return;
    var top = Math.max.apply(null, real.map(function (h) { return h[1]; }));
    var more = real.slice(1).map(function (h) { return h[0]; });
    o.flag("pops", top, real[0][0], null, "Possible audio pop here, please listen." + (more.length
      ? " Also check " + more.slice(0, 7).map(fmt).join(", ") + (more.length > 7 ? " and " + (more.length - 7) + " more" : "") + "." : ""));
    o.issues[o.issues.length - 1].also = more;
  };

  H.silence = function (o, A, Q) {
    var m = A.rms.length, k = 0;
    while (k < m) {
      if (A.rms[k] < 0.0015 && k * A.win > 3 && k * A.win < A.dur - 5) {
        var e = k; while (e < m && A.rms[e] < 0.0015) e++;
        var len = (e - k) * A.win, vi = Math.min(Q.n - 1, Math.round(k * A.win / Q.step));
        if (Q.lum[vi] > 25) o.flag("silence", len / 1.5, k * A.win, e * A.win, "Sudden silence for " + len.toFixed(1) + " s. Intended?");
        k = e;
      } else k++;
    }
  };

  /* the end of the film: music that stops dead instead of fading, or sound that ends while the
     picture carries on */
  H.musicEnd = function (o, A, Q) {
    var m = A.rms.length, db = function (x) { return 20 * Math.log10(x + 1e-9); }, w = A.win;
    var last = m - 1; while (last > 0 && A.rms[last] < 0.003) last--;          // last sound above -50 dB
    if (last < 40) return;
    var tEnd = (last + 1) * w, lvl = function (a, b) { var s = 0, c = 0; for (var i = Math.max(0, a); i < Math.min(m, b); i++) { s += A.rms[i] * A.rms[i]; c++; } return db(Math.sqrt(s / Math.max(1, c))); };
    var tail = lvl(last - 9, last + 1);                                           // the last half second
    var before = lvl(last - 60, last - 20);                                       // 1–3 s earlier
    // how long the fall to silence took: from 10 dB under the earlier level to the end
    var k = last; while (k > last - 80 && db(A.rms[k]) < before - 10) k--;
    var fall = (last - k) * w;
    var atStreamEnd = tEnd >= A.dur - 0.15;
    if (tail > -50 && (atStreamEnd || (fall <= 0.3 && tail > before - 10))) {
      o.flag("music_end", (tail + 50) / 20, Math.max(0, tEnd - 1), tEnd, atStreamEnd
        ? "The music is still playing when the film ends at " + fmt(tEnd) + ". Please fade it out."
        : "The music stops abruptly at " + fmt(tEnd) + " (no fade out). Please fade it out.");
    }
    // the picture keeps going after the sound has ended
    var lastPic = Q.n - 1; while (lastPic > 0 && Q.lum[lastPic] < 12) lastPic--;
    var picEnd = (lastPic + 1) * Q.step, gap = picEnd - tEnd;
    if (gap >= 1.2) o.flag("music_end", gap / 3, tEnd, picEnd, "The sound ends at " + fmt(tEnd) + " but the picture carries on for "
      + Math.round(gap) + " s. Intended?");
  };

  /* everything that only needs the quick pass and the audio */
  H.run = function (ctx) {
    var o = ctx.o, Q = ctx.Q, A = ctx.A, r = ctx.rules || {}, s = ctx.s || 1;
    if (r.blacks !== false) ctx.crushed = H.crushed(o, Q, true); else ctx.crushed = H.crushed(o, Q, false);
    if (r.highlights !== false) H.highlights(o, Q);
    if (r.media_offline !== false) H.offline(o, Q);
    if (r.framing !== false) H.framing(o, Q);
    if (r.audio_peaks !== false) H.peaks(o, A, s);
    if (r.clipping !== false) H.clipping(o, A, s);
    if (r.loudness !== false) H.loudness(o, A, s);
    if (r.pops !== false) H.pops(o, A, s, ctx.skip);
    if (r.silence !== false) H.silence(o, A, Q);
    if (r.music_end !== false) H.musicEnd(o, A, Q);
  };
})(typeof window !== "undefined" ? window : globalThis);
