/* Red Wed QC Helper: the checks. Everything runs in this browser; nothing leaves it except
   the comments posted on Frame.io and the report sent to Studio Ops. */
(function (root) {
  "use strict";
  var AC = root.AC = {};
  var sleep = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };
  AC.sleep = sleep;

  /* fetch with a time limit and retries: Frame.io's stream server sometimes stalls on a segment */
  AC.get = async function (url, opts) {
    var last;
    for (var a = 0; a < 4; a++) {
      try {
        var r = await fetch(url, Object.assign({}, opts || {}, {signal: AbortSignal.timeout(a < 2 ? 20000 : 45000)}));
        if (r.status < 500) {
          if ((opts || {}).method === "HEAD") return r;
          var b = await r.arrayBuffer();   // read the body inside the time limit too
          return {status: r.status, arrayBuffer: function () { return Promise.resolve(b); },
                  blob: function () { return Promise.resolve(new Blob([b])); }};
        }
        last = new Error("server error " + r.status);
      } catch (e) { last = e; }
      await sleep(1000 * (a + 1));
    }
    throw new Error("Frame.io didn't send part of the video (" + (last && last.name === "TimeoutError" ? "timed out" : last && last.message) + ")");
  };

  AC.fmt = function (s) {
    s = Math.max(0, Math.round(s));
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(x).padStart(2, "0");
  };
  AC.tcToSec = function (tc, fps) {
    var p = tc.split(":").map(Number);
    return p[0] * 3600 + p[1] * 60 + p[2] + (p[3] || 0) / (fps || 25);
  };

  /* what one small frame tells us: brightness per pixel, average colour, and the share of pixels
     that are pure black, pure white, Media-Offline red or corrupt green */
  function feats(d, W, H) {
    var n = W * H, Y = new Float32Array(n), R = 0, G = 0, B = 0, dark = 0, bright = 0, red = 0, green = 0;
    for (var k = 0; k < n; k++) {
      var r = d[k * 4], g = d[k * 4 + 1], b = d[k * 4 + 2], y = 0.299 * r + 0.587 * g + 0.114 * b;
      Y[k] = y; R += r; G += g; B += b;
      if (y <= 3) dark++; else if (y >= 252) bright++;
      if (r > 150 && g < 60 && b < 60) red++;
      else if (g > 150 && r < 70 && b < 70) green++;
    }
    return {Y: Y, W: W, H: H, rgb: [R / n, G / n, B / n], dark: dark / n, bright: bright / n, red: red / n, green: green / n};
  }
  AC.feats = feats;

  /* black bars: rows / columns at the edges that are black all the way across, with picture next to them */
  function bars(f) {
    var W = f.W, H = f.H, Y = f.Y;
    var rowMax = function (y) { var m = 0; for (var x = 0; x < W; x++) m = Math.max(m, Y[y * W + x]); return m; };
    var rowMean = function (y) { var s = 0; for (var x = 0; x < W; x++) s += Y[y * W + x]; return s / W; };
    var colMax = function (x) { var m = 0; for (var y = 0; y < H; y++) m = Math.max(m, Y[y * W + x]); return m; };
    var colMean = function (x) { var s = 0; for (var y = 0; y < H; y++) s += Y[y * W + x]; return s / H; };
    var edge = function (max, mean, from, dir, lim) {
      var k = 0; while (k < lim && max(from + dir * k) < 16) k++;
      if (!k || k >= lim) return 0;
      // picture must start right there (the first line may be half bar, half picture at this size);
      // dark content running to the edge isn't a bar
      var a = mean(from + dir * k), b = k + 1 < lim ? mean(from + dir * (k + 1)) : 0;
      return a >= 20 || b >= 28 || max(from + dir * k) >= 60 ? k : 0;
    };
    return [edge(rowMax, rowMean, 0, 1, H >> 1), edge(rowMax, rowMean, H - 1, -1, H >> 1),
            edge(colMax, colMean, 0, 1, W >> 1), edge(colMax, colMean, W - 1, -1, W >> 1)];
  }
  AC.bars = bars;

  /* how sharp a frame is: the sharpest of 4x3 regions (a sharp subject on a soft background still counts) */
  AC.sharpness = function (f) {
    var W = f.W, H = f.H, Y = f.Y, best = 0;
    for (var ry = 0; ry < 3; ry++) for (var rx = 0; rx < 4; rx++) {
      var y0 = Math.max(1, Math.floor(ry * H / 3)), y1 = Math.min(H - 1, Math.floor((ry + 1) * H / 3));
      var x0 = Math.max(1, Math.floor(rx * W / 4)), x1 = Math.min(W - 1, Math.floor((rx + 1) * W / 4)), s = 0, n = 0;
      for (var y = y0; y < y1; y++) for (var x = x0; x < x1; x++) {
        var i = y * W + x, l = 4 * Y[i] - Y[i - 1] - Y[i + 1] - Y[i - W] - Y[i + W];
        s += l * l; n++;
      }
      if (n) best = Math.max(best, s / n);
    }
    var m = 0, v = 0, k;
    for (k = 0; k < Y.length; k++) m += Y[k];
    m /= Y.length;
    for (k = 0; k < Y.length; k += 3) v += (Y[k] - m) * (Y[k] - m);
    return {sharp: best, lum: m, std: Math.sqrt(v / Math.ceil(Y.length / 3))};
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
    var buf = await (await AC.get(url)).blob();
    var v = document.createElement("video");
    v.muted = true; v.preload = "auto"; v.src = URL.createObjectURL(buf);
    await new Promise(function (r, j) { v.onloadedmetadata = r; v.onerror = function () { j(new Error("preview won't load")); }; });
    return v;
  };

  AC.quickPass = async function (v, step, onProgress) {
    var W = 32, H = 18, c = document.createElement("canvas"); c.width = W; c.height = H;
    var g = c.getContext("2d", {willReadFrequently: true});
    var dur = v.duration, n = Math.floor(dur / step);
    var out = AC.emptyQ(dur, step, n);
    for (var i = 0; i < n; i++) {
      await seekTo(v, i * step + 0.01);
      g.drawImage(v, 0, 0, W, H);
      qpFrom(feats(g.getImageData(0, 0, W, H).data, W, H), out, i);
      if (onProgress && i % 100 === 0) onProgress(i / n);
    }
    return out;
  };

  /* the arrays a quick pass fills: one entry per sample */
  AC.emptyQ = function (dur, step, n) {
    return {dur: dur, step: step, n: n, sig: new Uint8Array(n * 32), lum: new Float32Array(n), p5: new Uint8Array(n),
            p95: new Uint8Array(n), rgb: new Uint8Array(n * 3), dark: new Float32Array(n), bright: new Float32Array(n),
            red: new Float32Array(n), green: new Float32Array(n), bars: new Uint8Array(n * 4)};
  };

  /* ---------------- audio: Frame.io's audio stream, decoded at 16 kHz ---------------- */
  AC.lastSegment = async function (base) {
    var ok = async function (n) { try { return (await AC.get(base + n + ".m4s", {method: "HEAD"})).status === 200; } catch (e) { return false; } };
    var lo = 1, hi = 8000;
    while (lo < hi) { var m = Math.ceil((lo + hi) / 2); if (await ok(m)) lo = m; else hi = m - 1; }
    return lo;
  };

  /* share of samples pressed against the peak: flattened (clipped) waves pile up there */
  AC.flatShare = function (x, a, b, p) {
    var near = 0, lim = p * 0.94;
    for (var k = a; k < b; k++) if (Math.abs(x[k]) >= lim) near++;
    return near / Math.max(1, b - a);
  };

  AC.audioPass = async function (base, onProgress) {
    var last = await AC.lastSegment(base);
    var parts = [await (await AC.get(base + "init.mp4")).arrayBuffer()];
    for (var i = 1; i <= last; i += 8) {
      var ps = [];
      for (var j = i; j < Math.min(i + 8, last + 1); j++) ps.push(AC.get(base + j + ".m4s").then(function (r) { return r.arrayBuffer(); }));
      parts.push.apply(parts, await Promise.all(ps));
      if (onProgress) onProgress(i / last);
    }
    var SR = 16000, ac = new OfflineAudioContext(1, SR * (last * 2 + 4), SR);
    var ab = await ac.decodeAudioData(await new Blob(parts).arrayBuffer());
    var x = ab.getChannelData(0), win = SR * 0.05, m = Math.floor(x.length / win);
    var rms = new Float32Array(m), pk = new Float32Array(m), clk = new Float32Array(m), flat = new Float32Array(m);
    for (var w = 0; w < m; w++) {
      var s = 0, p = 0, dd = 0;
      for (var k = w * win + 1; k < (w + 1) * win; k++) {
        var a = x[k]; s += a * a; var aa = Math.abs(a); if (aa > p) p = aa;
        var df = Math.abs(a - x[k - 1]); if (df > dd) dd = df;
      }
      rms[w] = Math.sqrt(s / win); pk[w] = p; clk[w] = dd;
      if (p >= 0.1) flat[w] = AC.flatShare(x, w * win, (w + 1) * win, p);
    }
    var score = new Float32Array(m);
    for (var q = 0; q < m; q++) {
      var a0 = Math.max(0, q - 20), a1 = Math.min(m, q + 20), arr = Array.from(rms.slice(a0, a1)).sort(function (a, b) { return a - b; });
      var med = arr[Math.floor(arr.length / 2)] || 0;
      score[q] = clk[q] / (med * 3 + 0.003);
    }
    return {win: 0.05, dur: ab.duration, rms: rms, pk: pk, clk: clk, score: score, flat: flat};
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
      self.init = await (await AC.get(self.base + "init.mp4")).arrayBuffer();
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
      var r = await AC.get(self.base + s + ".m4s");
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
      var fr = feats(g.getImageData(0, 0, W, H).data, W, H);
      fr.t = t;
      out.push(fr);
    }
    try { ms.endOfStream(); } catch (e) {}
    URL.revokeObjectURL(v.src);
    return out;
  };

  /* fetch the segments of a stretch of a stream (6 at a time) */
  AC.HD.prototype.fetchSegs = async function (t0, t1) {
    var self = this;
    if (!self.init) await self.frames(0, 0.01, 1, 16);
    var s0 = Math.floor(Math.max(0, t0) / self.segDur) + 1, s1 = Math.floor(Math.max(t0, t1 - 0.001) / self.segDur) + 1, bufs = [];
    for (var s = s0; s <= s1; s += 6) {
      var ps = [];
      for (var j = s; j <= Math.min(s1, s + 5); j++) ps.push(AC.get(self.base + j + ".m4s").then(function (r) { return r.status === 200 ? r.arrayBuffer() : null; }));
      (await Promise.all(ps)).forEach(function (b) { if (b) bufs.push(b); });
    }
    return bufs;
  };

  /* load a stretch into a video element (pass `pending` to use segments already being fetched) */
  AC.HD.prototype.load = async function (t0, t1, pending) {
    var self = this;
    var bufs = await (pending || self.fetchSegs(t0, t1));
    var v = document.createElement("video"); v.muted = true;
    var ms = new MediaSource(); v.src = URL.createObjectURL(ms);
    await new Promise(function (r) { ms.addEventListener("sourceopen", r, {once: true}); });
    var sb = ms.addSourceBuffer('video/mp4; codecs="' + self.codec + '"');
    var app = function (b) { return new Promise(function (r, j) { sb.addEventListener("updateend", r, {once: true}); sb.addEventListener("error", j, {once: true}); sb.appendBuffer(b); }); };
    await app(self.init);
    for (var b = 0; b < bufs.length; b++) await app(bufs[b]);
    try { ms.endOfStream(); } catch (e) {}
    return v;
  };

  /* play a stretch at 16x and grab a small frame every `step` seconds (much faster than seeking) */
  AC.HD.prototype.sample = async function (t0, t1, step, W, cb, pending) {
    var v = await this.load(t0, t1, pending); W = W || 32; var H = Math.round(W * 9 / 16);
    var c = document.createElement("canvas"); c.width = W; c.height = H;
    var g = c.getContext("2d", {willReadFrequently: true});
    var next = Math.ceil(t0 / step) * step;
    await new Promise(function (res) { v.addEventListener("seeked", res, {once: true}); v.currentTime = Math.max(0, t0); setTimeout(res, 3000); });
    await new Promise(function (done) {
      var fin = false, end = function () { if (!fin) { fin = true; v.pause(); done(); } };
      var grab = function (now, meta) {
        var t = meta && meta.mediaTime != null ? meta.mediaTime : v.currentTime;
        if (t + 0.021 >= next) {
          g.drawImage(v, 0, 0, W, H);
          var fr = feats(g.getImageData(0, 0, W, H).data, W, H);
          fr.t = t;
          cb(fr);
          while (next <= t + 0.021) next += step;
        }
        if (v.ended || t >= t1 - 0.05) return end();
        v.requestVideoFrameCallback(grab);
      };
      v.onended = end;
      v.playbackRate = 16; v.requestVideoFrameCallback(grab); v.play().catch(end);
      setTimeout(end, (t1 - t0) / 16 * 3000 + 5000);   // a hidden tab stops playback; the caller re-does gaps
    });
    URL.revokeObjectURL(v.src);
  };

  function qpFrom(f, out, i) {
    var Y = f.Y, n = Y.length, s = 0;
    for (var k = 0; k < n; k++) s += Y[k];
    out.lum[i] = s / n; out.rgb[i * 3] = f.rgb[0]; out.rgb[i * 3 + 1] = f.rgb[1]; out.rgb[i * 3 + 2] = f.rgb[2];
    out.dark[i] = f.dark || 0; out.bright[i] = f.bright || 0; out.red[i] = f.red || 0; out.green[i] = f.green || 0;
    var b = bars(f);
    for (var q = 0; q < 4; q++) out.bars[i * 4 + q] = b[q];
    var so = Array.from(Y).sort(function (a, b) { return a - b; });
    out.p5[i] = so[Math.floor(n * 0.05)]; out.p95[i] = so[Math.floor(n * 0.95)];
    for (var by = 0; by < 4; by++) for (var bx = 0; bx < 8; bx++) {
      var t = 0, c = 0;
      for (var yy = Math.floor(by * f.H / 4); yy < Math.floor((by + 1) * f.H / 4); yy++)
        for (var xx = bx * 4; xx < bx * 4 + 4; xx++) { t += Y[yy * f.W + xx]; c++; }
      out.sig[i * 32 + by * 8 + bx] = t / c;
    }
  }

  /* quick pass from Frame.io's 360p stream (the 180p preview isn't always loaded by the page) */
  AC.quickPassHLS = async function (base, dur, step, onProgress) {
    var low = base;
    var tries = ["h264_360", "h264_540", "h264_720"];
    for (var q = 0; q < tries.length; q++) {
      var b = base.replace(/\/h264_[a-z0-9_]+\//, "/" + tries[q] + "/");
      try { if ((await AC.get(b + "init.mp4", {method: "HEAD"})).status === 200) { low = b; break; } } catch (e) { /* next */ }
    }
    if (!dur) dur = (await AC.lastSegment(low)) * 2;
    var hd = new AC.HD(low), n = Math.floor(dur / step), has = new Uint8Array(n);
    var out = AC.emptyQ(dur, step, n);
    out.has = has;          // samples actually seen (the rest are copied from a neighbour)
    var CH = 120, pending = hd.fetchSegs(0, Math.min(dur, CH));
    for (var t0 = 0; t0 < dur; t0 += CH) {
      var t1 = Math.min(dur, t0 + CH), got = 0, mine = pending;
      pending = t1 < dur ? hd.fetchSegs(t1, Math.min(dur, t1 + CH)) : null;   // fetch the next stretch while this one plays
      var put = function (f) { var i = Math.round(f.t / step); if (i < n && !has[i]) { qpFrom(f, out, i); has[i] = 1; got++; } };
      if (!document.hidden) await hd.sample(t0, t1, step, 32, put, mine);
      if (got < (t1 - t0) / step * 0.6) (await hd.frames(t0, t1, 1 / step, 32)).forEach(put);   // tab in the background: seek instead
      if (onProgress) onProgress(t0 / dur);
    }
    var fill = function (i, j) {
      ["lum", "p5", "p95", "dark", "bright", "red", "green"].forEach(function (key) { out[key][i] = out[key][j]; });
      for (var k = 0; k < 32; k++) out.sig[i * 32 + k] = out.sig[j * 32 + k];
      for (var k2 = 0; k2 < 3; k2++) out.rgb[i * 3 + k2] = out.rgb[j * 3 + k2];
      for (var k3 = 0; k3 < 4; k3++) out.bars[i * 4 + k3] = out.bars[j * 4 + k3];
    };
    for (var i = 1; i < n; i++) if (!has[i] && has[i - 1]) { fill(i, i - 1); has[i] = 1; }
    for (var i2 = n - 2; i2 >= 0; i2--) if (!has[i2] && has[i2 + 1]) { fill(i2, i2 + 1); has[i2] = 1; }
    return out;
  };

  function meanLum(f) { var s = 0; for (var i = 0; i < f.Y.length; i++) s += f.Y[i]; return s / f.Y.length; }
  function diff(a, b, step) { var s = 0, n = 0; step = step || 2; for (var i = 0; i < a.Y.length; i += step) { s += Math.abs(a.Y[i] - b.Y[i]); n++; } return s / n; }
  function ndiff(a, b) {   // structure only (brightness-normalised)
    var ma = meanLum(a), mb = meanLum(b), s = 0, n = 0;
    for (var i = 0; i < a.Y.length; i += 3) { s += Math.abs((a.Y[i] - ma) - (b.Y[i] - mb)); n++; }
    return s / n;
  }
  /* mean difference in each of 8x6 regions (a changed title shows up in a few regions only) */
  AC.blocks = function (a, b) {
    var W = a.W, H = a.H, out = [];
    for (var by = 0; by < 6; by++) for (var bx = 0; bx < 8; bx++) {
      var s = 0, n = 0;
      for (var y = Math.floor(by * H / 6); y < Math.floor((by + 1) * H / 6); y++)
        for (var x = Math.floor(bx * W / 8); x < Math.floor((bx + 1) * W / 8); x++) { s += Math.abs(a.Y[y * W + x] - b.Y[y * W + x]); n++; }
      out.push(s / n);
    }
    return out;
  };
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
      if (a > 25 && b > 25 && c < Math.min(a, b) * 0.35) { out.flash = frames[k].t; out.flashSev = Math.min(a, b) / 25; break; }
    }
    var ci = ds.indexOf(Math.max.apply(null, ds));
    out.peak = ds[ci]; out.med = med;
    // the change is into / out of black (a fade or a dip): not a jump or a colour change
    if (meanLum(frames[ci]) < 20 || meanLum(frames[ci + 1]) < 20) { out.cut = frames[ci + 1].t; out.dark = true; return out; }
    // a small jump inside a still shot (a few frames cut out): a lone spike far above the noise
    if (ds[ci] <= 12 && ds[ci] >= 2 && ds[ci] > med * 6) {
      out.jump = frames[ci + 1].t; out.struct = ndiff(frames[ci], frames[ci + 1]);
      out.jumpSev = ds[ci] / (med * 6 + 0.01);
      return out;
    }
    if (ds[ci] > Math.max(12, med * 4)) {
      var pre = frames[ci], post = frames[ci + 1];
      out.cut = post.t;
      var nd = ndiff(pre, post), dl = Math.abs(meanLum(pre) - meanLum(post));
      var dc = Math.max(Math.abs(pre.rgb[0] - post.rgb[0]), Math.abs(pre.rgb[1] - post.rgb[1]), Math.abs(pre.rgb[2] - post.rgb[2]));
      out.struct = nd; out.dl = dl; out.dc = dc;
      if (nd < 9 && ds[ci] > 6) { out.jump = post.t; out.jumpSev = 9 / Math.max(nd, 3); }    // same framing, picture jumped
      if (nd < 14 && (dl > 16 || dc > 18)) { out.colour = post.t; out.colourSev = Math.max(dl / 16, dc / 18); }   // same scene, different look
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
