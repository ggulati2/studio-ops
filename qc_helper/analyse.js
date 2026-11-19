/* Red Wed QC Helper: the checks. Everything runs in this browser; nothing leaves it except
   the comments posted on Frame.io and the report sent to Studio Ops. */
(function (root) {
  "use strict";
  var AC = root.AC = {};
  var sleep = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };
  AC.sleep = sleep;

  AC.fmt = function (s) {
    s = Math.max(0, Math.round(s));
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(x).padStart(2, "0");
  };
  AC.tcToSec = function (tc, fps) {
    var p = tc.split(":").map(Number);
    return p[0] * 3600 + p[1] * 60 + p[2] + (p[3] || 0) / (fps || 25);
  };

  function seekTo(v, t) {
    return new Promise(function (res) {
      var done = false;
      var fin = function () { if (!done) { done = true; res(); } };
      v.addEventListener("seeked", fin, {once: true});
      setTimeout(fin, 4000);
      v.currentTime = t;
    });
  }

  /* ---------------- quick pass: the small preview, every half second ---------------- */
  AC.loadProxy = async function (url) {
    var buf = await (await fetch(url)).blob();
    var v = document.createElement("video");
    v.muted = true; v.preload = "auto"; v.src = URL.createObjectURL(buf);
    await new Promise(function (r, j) { v.onloadedmetadata = r; v.onerror = function () { j(new Error("preview won't load")); }; });
    return v;
  };

  AC.quickPass = async function (v, step, onProgress) {
    var W = 32, H = 18, c = document.createElement("canvas"); c.width = W; c.height = H;
    var g = c.getContext("2d", {willReadFrequently: true});
    var dur = v.duration, n = Math.floor(dur / step);
    var sig = new Uint8Array(n * 32), lum = new Float32Array(n), p5 = new Uint8Array(n), p95 = new Uint8Array(n),
        rgb = new Uint8Array(n * 3);
    for (var i = 0; i < n; i++) {
      await seekTo(v, i * step + 0.01);
      g.drawImage(v, 0, 0, W, H);
      var d = g.getImageData(0, 0, W, H).data, Y = new Uint8Array(W * H), s = 0, R = 0, G = 0, B = 0;
      for (var k = 0; k < W * H; k++) {
        var y = 0.299 * d[k * 4] + 0.587 * d[k * 4 + 1] + 0.114 * d[k * 4 + 2];
        Y[k] = y; s += y; R += d[k * 4]; G += d[k * 4 + 1]; B += d[k * 4 + 2];
      }
      lum[i] = s / (W * H); rgb[i * 3] = R / (W * H); rgb[i * 3 + 1] = G / (W * H); rgb[i * 3 + 2] = B / (W * H);
      var so = Array.from(Y).sort(function (a, b) { return a - b; });
      p5[i] = so[Math.floor(W * H * 0.05)]; p95[i] = so[Math.floor(W * H * 0.95)];
      for (var by = 0; by < 4; by++) for (var bx = 0; bx < 8; bx++) {
        var t = 0, cnt = 0;
        for (var yy = Math.floor(by * H / 4); yy < Math.floor((by + 1) * H / 4); yy++)
          for (var xx = bx * 4; xx < bx * 4 + 4; xx++) { t += Y[yy * W + xx]; cnt++; }
        sig[i * 32 + by * 8 + bx] = t / cnt;
      }
      if (onProgress && i % 100 === 0) onProgress(i / n);
    }
    return {dur: dur, step: step, n: n, sig: sig, lum: lum, p5: p5, p95: p95, rgb: rgb};
  };

  /* ---------------- audio: Frame.io's audio stream, decoded at 16 kHz ---------------- */
  AC.lastSegment = async function (base) {
    var ok = async function (n) { try { return (await fetch(base + n + ".m4s", {method: "HEAD"})).status === 200; } catch (e) { return false; } };
    var lo = 1, hi = 8000;
    while (lo < hi) { var m = Math.ceil((lo + hi) / 2); if (await ok(m)) lo = m; else hi = m - 1; }
    return lo;
  };

  AC.audioPass = async function (base, onProgress) {
    var last = await AC.lastSegment(base);
    var parts = [await (await fetch(base + "init.mp4")).arrayBuffer()];
    for (var i = 1; i <= last; i += 8) {
      var ps = [];
      for (var j = i; j < Math.min(i + 8, last + 1); j++) ps.push(fetch(base + j + ".m4s").then(function (r) { return r.arrayBuffer(); }));
      parts.push.apply(parts, await Promise.all(ps));
      if (onProgress) onProgress(i / last);
    }
    var SR = 16000, ac = new OfflineAudioContext(1, SR * (last * 2 + 4), SR);
    var ab = await ac.decodeAudioData(await new Blob(parts).arrayBuffer());
    var x = ab.getChannelData(0), win = SR * 0.05, m = Math.floor(x.length / win);
    var rms = new Float32Array(m), pk = new Float32Array(m), clk = new Float32Array(m);
    for (var w = 0; w < m; w++) {
      var s = 0, p = 0, dd = 0;
      for (var k = w * win + 1; k < (w + 1) * win; k++) {
        var a = x[k]; s += a * a; var aa = Math.abs(a); if (aa > p) p = aa;
        var df = Math.abs(a - x[k - 1]); if (df > dd) dd = df;
      }
      rms[w] = Math.sqrt(s / win); pk[w] = p; clk[w] = dd;
    }
    var score = new Float32Array(m);
    for (var q = 0; q < m; q++) {
      var a0 = Math.max(0, q - 20), a1 = Math.min(m, q + 20), arr = Array.from(rms.slice(a0, a1)).sort(function (a, b) { return a - b; });
      var med = arr[Math.floor(arr.length / 2)] || 0;
      score[q] = clk[q] / (med * 3 + 0.003);
    }
    return {win: 0.05, dur: ab.duration, rms: rms, pk: pk, clk: clk, score: score};
  };

  /* ---------------- line V1 up with V2 (robust to exposure changes) ---------------- */
  function normSig(s, n) {
    var o = new Float32Array(n * 32);
    for (var i = 0; i < n; i++) {
      var m = 0, k; for (k = 0; k < 32; k++) m += s[i * 32 + k]; m /= 32;
      var v = 0; for (k = 0; k < 32; k++) v += Math.pow(s[i * 32 + k] - m, 2); v = Math.sqrt(v / 32) + 4;
      for (k = 0; k < 32; k++) o[i * 32 + k] = (s[i * 32 + k] - m) / v;
    }
    return o;
  }
  AC.normSig = normSig;

  AC.align = function (Q1, Q2) {
    var n1 = Q1.n, n2 = Q2.n, a1 = normSig(Q1.sig, n1), a2 = normSig(Q2.sig, n2);
    var d = function (i, j) { var t = 0; for (var k = 0; k < 32; k++) t += Math.abs(a1[i * 32 + k] - a2[j * 32 + k]); return t / 32; };
    var band = Math.min(600, Math.max(150, Math.abs(n2 - n1) + 150)), gap = 0.9, INF = 1e9, W = 2 * band + 1;
    var cost = new Float32Array((n1 + 1) * W).fill(INF), bt = new Int8Array((n1 + 1) * W);
    var idx = function (i, dj) { return i * W + (dj + band); };
    for (var dj0 = 0; dj0 <= band; dj0++) { cost[idx(0, dj0)] = dj0 * gap; bt[idx(0, dj0)] = 2; }
    for (var i = 1; i <= n1; i++) for (var dj = -band; dj <= band; dj++) {
      var j = i + dj; if (j < 0 || j > n2) continue;
      var best = INF, b = 0, c;
      if (j >= 1) { c = cost[idx(i - 1, dj)]; if (c < INF) { var v0 = c + d(i - 1, j - 1); if (v0 < best) { best = v0; b = 0; } } }
      if (dj + 1 <= band) { c = cost[idx(i - 1, dj + 1)]; if (c < INF && c + gap < best) { best = c + gap; b = 1; } }
      if (dj - 1 >= -band && j >= 1) { c = cost[idx(i, dj - 1)]; if (c < INF && c + gap < best) { best = c + gap; b = 2; } }
      cost[idx(i, dj)] = best; bt[idx(i, dj)] = b;
    }
    var map = new Int32Array(n1).fill(-1), used = new Uint8Array(n2), ii = n1, dd = n2 - n1;
    if (Math.abs(dd) > band) dd = Math.sign(dd) * band;
    while (ii > 0 || ii + dd > 0) {
      var jj = ii + dd, bb = bt[idx(ii, dd)];
      if (ii > 0 && jj > 0 && bb === 0) { map[ii - 1] = jj - 1; used[jj - 1] = 1; ii--; }
      else if (bb === 1 && ii > 0) { ii--; dd++; } else { dd--; }
      if (Math.abs(dd) > band) break;
    }
    var segs = [], k = 0;
    while (k < n1) { if (map[k] < 0) { var e = k; while (e < n1 && map[e] < 0) e++; if (e - k >= 2) segs.push({kind: "removed", from: k * Q1.step, to: e * Q1.step}); k = e; } else k++; }
    var q = 0;
    while (q < n2) { if (!used[q]) { var e2 = q; while (e2 < n2 && !used[e2]) e2++; if (e2 - q >= 2) segs.push({kind: "added", from: q * Q2.step, to: e2 * Q2.step}); q = e2; } else q++; }
    return {map: map, used: used, segs: segs, step: Q1.step};
  };

  /* V1 time -> V2 time (nearest matched moment), and whether that moment changed */
  AC.toV2 = function (A, t) {
    var i = Math.round(t / A.step), m = A.map;
    for (var d = 0; d < 40; d++) {
      if (i + d < m.length && m[i + d] >= 0) return {t: m[i + d] * A.step - d * A.step, moved: d > 2};
      if (i - d >= 0 && m[i - d] >= 0) return {t: m[i - d] * A.step + d * A.step, moved: d > 2};
    }
    return null;
  };
  AC.changedNear = function (A, t1, t2, span) {
    return A.segs.filter(function (s) {
      var at = s.kind === "removed" ? t1 : t2;
      return at != null && s.from - span <= at && at <= s.to + span;
    });
  };

  /* ---------------- HD frames: Frame.io's 1080p stream, a few seconds at a time ---------------- */
  AC.HD = function (base) {
    this.base = base; this.segDur = 2; this.init = null; this.codec = null;
  };
  AC.HD.prototype.frames = async function (t0, t1, fps, W) {
    var self = this;
    if (!self.init) {
      self.init = await (await fetch(self.base + "init.mp4")).arrayBuffer();
      var u8 = new Uint8Array(self.init); self.codec = "avc1.640028";
      for (var i = 0; i < u8.length - 8; i++) {
        if (u8[i] === 0x61 && u8[i + 1] === 0x76 && u8[i + 2] === 0x63 && u8[i + 3] === 0x43) {
          self.codec = "avc1." + [u8[i + 5], u8[i + 6], u8[i + 7]].map(function (x) { return x.toString(16).padStart(2, "0"); }).join("");
          break;
        }
      }
    }
    t0 = Math.max(0, t0);
    var s0 = Math.floor(t0 / self.segDur) + 1, s1 = Math.floor(Math.max(t0, t1 - 0.001) / self.segDur) + 1;
    var bufs = [];
    for (var s = s0; s <= s1; s++) {
      var r = await fetch(self.base + s + ".m4s");
      if (r.status !== 200) break;
      bufs.push(await r.arrayBuffer());
    }
    if (!bufs.length) return [];
    var v = document.createElement("video"); v.muted = true;
    var ms = new MediaSource(); v.src = URL.createObjectURL(ms);
    await new Promise(function (r) { ms.addEventListener("sourceopen", r, {once: true}); });
    var sb = ms.addSourceBuffer('video/mp4; codecs="' + self.codec + '"');
    var app = function (b) { return new Promise(function (r, j) { sb.addEventListener("updateend", r, {once: true}); sb.addEventListener("error", j, {once: true}); sb.appendBuffer(b); }); };
    await app(self.init);
    for (var b = 0; b < bufs.length; b++) await app(bufs[b]);
    W = W || 320; var H = Math.round(W * 9 / 16);
    var c = document.createElement("canvas"); c.width = W; c.height = H;
    var g = c.getContext("2d", {willReadFrequently: true});
    var out = [], br = sb.buffered, lo = br.length ? br.start(0) : t0, hi = br.length ? br.end(br.length - 1) : t1;
    for (var t = Math.max(t0, lo) + 0.001; t < Math.min(t1, hi - 0.01); t += 1 / fps) {
      await seekTo(v, t);
      g.drawImage(v, 0, 0, W, H);
      var d = g.getImageData(0, 0, W, H).data, Y = new Float32Array(W * H), R = 0, G = 0, Bb = 0;
      for (var k = 0; k < W * H; k++) { Y[k] = 0.299 * d[k * 4] + 0.587 * d[k * 4 + 1] + 0.114 * d[k * 4 + 2]; R += d[k * 4]; G += d[k * 4 + 1]; Bb += d[k * 4 + 2]; }
      out.push({t: t, Y: Y, W: W, H: H, rgb: [R / (W * H), G / (W * H), Bb / (W * H)]});
    }
    try { ms.endOfStream(); } catch (e) {}
    URL.revokeObjectURL(v.src);
    return out;
  };

  function meanLum(f) { var s = 0; for (var i = 0; i < f.Y.length; i++) s += f.Y[i]; return s / f.Y.length; }
  function diff(a, b, step) { var s = 0, n = 0; step = step || 2; for (var i = 0; i < a.Y.length; i += step) { s += Math.abs(a.Y[i] - b.Y[i]); n++; } return s / n; }
  function ndiff(a, b) {   // structure only (brightness-normalised)
    var ma = meanLum(a), mb = meanLum(b), s = 0, n = 0;
    for (var i = 0; i < a.Y.length; i += 3) { s += Math.abs((a.Y[i] - ma) - (b.Y[i] - mb)); n++; }
    return s / n;
  }
  AC.meanLum = meanLum; AC.diff = diff; AC.ndiff = ndiff;

  /* global camera motion between frames: (dx, dy) by block matching on a small copy */
  function shift(A, B) {
    var W = A.W, H = A.H, best = 1e9, bx = 0, by = 0;
    for (var dy = -4; dy <= 4; dy++) for (var dx = -4; dx <= 4; dx++) {
      var s = 0, n = 0;
      for (var y = 8; y < H - 8; y += 3) for (var x = 8; x < W - 8; x += 3) { s += Math.abs(A.Y[y * W + x] - B.Y[(y + dy) * W + x + dx]); n++; }
      s /= n; if (s < best) { best = s; bx = dx; by = dy; }
    }
    return [bx, by, best];
  }
  AC.shake = function (frames) {
    if (frames.length < 6) return null;
    var mv = [];
    for (var i = 1; i < frames.length; i++) mv.push(shift(frames[i - 1], frames[i]));
    var med = mv.map(function (m) { return m[2]; }).sort(function (a, b) { return a - b; })[Math.floor(mv.length / 2)];
    // skip a cut inside the window
    mv = mv.filter(function (m) { return m[2] < med * 4 + 6; });
    var jit = 0;
    for (var k = 1; k < mv.length; k++) jit += Math.abs(mv[k][0] - mv[k - 1][0]) + Math.abs(mv[k][1] - mv[k - 1][1]);
    return mv.length > 2 ? jit / (mv.length - 1) : null;
  };

  /* look at the frames around a cut: flash frame, jump cut, colour jump */
  AC.cutLook = function (frames) {
    if (frames.length < 5) return null;
    var ds = [];
    for (var i = 1; i < frames.length; i++) ds.push(diff(frames[i - 1], frames[i]));
    var med = ds.slice().sort(function (a, b) { return a - b; })[Math.floor(ds.length / 2)];
    var out = {flash: null, jump: null, colour: null, cut: null};
    // flash: one frame unlike both neighbours, while the neighbours are alike
    for (var k = 1; k < frames.length - 1; k++) {
      var a = diff(frames[k - 1], frames[k]), b = diff(frames[k], frames[k + 1]), c = diff(frames[k - 1], frames[k + 1]);
      if (a > 25 && b > 25 && c < Math.min(a, b) * 0.35) { out.flash = frames[k].t; break; }
    }
    var ci = ds.indexOf(Math.max.apply(null, ds));
    out.peak = ds[ci]; out.med = med;
    // a small jump inside a still shot (a few frames cut out): a lone spike far above the noise
    if (ds[ci] <= 12 && ds[ci] >= 2 && ds[ci] > med * 6) {
      out.jump = frames[ci + 1].t; out.struct = ndiff(frames[ci], frames[ci + 1]);
      return out;
    }
    if (ds[ci] > Math.max(12, med * 4)) {
      var pre = frames[ci], post = frames[ci + 1];
      out.cut = post.t;
      var nd = ndiff(pre, post), dl = Math.abs(meanLum(pre) - meanLum(post));
      var dc = Math.max(Math.abs(pre.rgb[0] - post.rgb[0]), Math.abs(pre.rgb[1] - post.rgb[1]), Math.abs(pre.rgb[2] - post.rgb[2]));
      out.struct = nd; out.dl = dl; out.dc = dc;
      if (nd < 9 && ds[ci] > 6) out.jump = post.t;                 // same framing, picture jumped
      if (nd < 14 && (dl > 16 || dc > 18)) out.colour = post.t;      // same scene, different look
    }
    return out;
  };

  /* ---------------- cuts from the quick pass ---------------- */
  AC.cuts = function (Q) {
    var a = normSig(Q.sig, Q.n), out = [];
    for (var i = 1; i < Q.n; i++) {
      var t = 0; for (var k = 0; k < 32; k++) t += Math.abs(a[(i - 1) * 32 + k] - a[i * 32 + k]);
      if (t / 32 > 0.55) out.push((i - 0.5) * Q.step);
    }
    var merged = [];
    out.forEach(function (t) { if (!merged.length || t - merged[merged.length - 1] > 1.2) merged.push(t); });
    return merged;
  };
})(typeof window !== "undefined" ? window : globalThis);
