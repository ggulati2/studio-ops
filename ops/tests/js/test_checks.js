/* Tests for the QC Helper's hygiene checks on made-up pictures and sound.
   Run: node ops/tests/js/test_checks.js   (the Django suite runs it too when node is installed) */
"use strict";
global.window = global;
var path = require("path"), dir = path.join(__dirname, "..", "..", "..", "qc_helper");
require(path.join(dir, "analyse.js"));
require(path.join(dir, "checks.js"));
var assert = require("assert"), H = AC.hyg, failed = 0, passed = 0;
function test(name, fn) { try { fn(); passed++; } catch (e) { failed++; console.error("FAIL " + name + ": " + e.message); } }

/* a quick pass of n samples (0.5 s apart), every sample a plain mid-grey picture with no bars */
function Q(n) {
  var q = AC.emptyQ(n * 0.5, 0.5, n);
  for (var i = 0; i < n; i++) { q.lum[i] = 110; q.p95[i] = 180; q.p5[i] = 30; }
  return q;
}
function black(q, a, b) { for (var i = a; i < b; i++) { q.lum[i] = 1; q.p95[i] = 3; q.p5[i] = 0; } }
function kinds(o) { return o.finish().issues.map(function (x) { return x.kind; }); }

test("hard cut to black for a second is flagged", function () {
  var q = Q(120); black(q, 50, 52);
  var o = H.out(); H.blackFrames(o, q, H.blackRuns(q));
  assert.deepStrictEqual(kinds(o), ["black_frame"]);
});
test("fade to black and back (quick pass) is not flagged", function () {
  var q = Q(120); q.lum[48] = 80; q.lum[49] = 30; black(q, 50, 53); q.lum[53] = 35; q.lum[54] = 85;
  var o = H.out(); H.blackFrames(o, q, H.blackRuns(q));
  assert.deepStrictEqual(kinds(o), []);
});
test("HD edges decide: a fade on one side is enough to leave it", function () {
  assert.strictEqual(H.edgeKind([110, 110, 100, 80, 60, 40, 20, 5, 2, 1]), "fade");
  assert.strictEqual(H.edgeKind([110, 110, 110, 110, 110, 2, 1, 1]), "cut");
  var q = Q(120); black(q, 50, 52);
  var runs = H.blackRuns(q); runs[0].edges = {fadeOut: true, fadeIn: false};
  var o = H.out(); H.blackFrames(o, q, runs);
  assert.deepStrictEqual(kinds(o), []);
});
test("start and end of the film are never black-frame flags", function () {
  var q = Q(120); black(q, 0, 3); black(q, 115, 120);
  var o = H.out(); H.blackFrames(o, q, H.blackRuns(q));
  assert.deepStrictEqual(kinds(o), []);
});
test("Media Offline red screen is flagged", function () {
  var q = Q(100); q.red[40] = 0.9; q.red[41] = 0.92;
  var o = H.out(); H.offline(o, q);
  var r = o.finish().issues;
  assert.strictEqual(r.length, 1); assert.strictEqual(r[0].kind, "media_offline"); assert.ok(/Media Offline/.test(r[0].text));
});
test("a red-lit dance floor (some red pixels) is not media offline", function () {
  var q = Q(100); for (var i = 30; i < 40; i++) q.red[i] = 0.3;
  var o = H.out(); H.offline(o, q);
  assert.deepStrictEqual(kinds(o), []);
});
test("side bars on a phone clip in a full-frame film are flagged", function () {
  var q = Q(100); for (var i = 60; i < 66; i++) { q.bars[i * 4 + 2] = 11; q.bars[i * 4 + 3] = 11; }
  var o = H.out(); H.framing(o, q);
  var r = o.finish().issues;
  assert.strictEqual(r.length, 1); assert.ok(/vertical/.test(r[0].text));
});
test("a letterboxed film is fine; a stretch without the bars is flagged", function () {
  var q = Q(100); for (var i = 0; i < 100; i++) { q.bars[i * 4] = 2; q.bars[i * 4 + 1] = 2; }
  var o = H.out(); H.framing(o, q); assert.deepStrictEqual(kinds(o), []);
  for (var j = 20; j < 26; j++) { q.bars[j * 4] = 0; q.bars[j * 4 + 1] = 0; }
  var o2 = H.out(); H.framing(o2, q);
  var r = o2.finish().issues; assert.strictEqual(r.length, 1); assert.ok(/missing/.test(r[0].text));
});
test("bars() finds real bars but not dark content", function () {
  var W = 32, Hh = 18, Y = new Float32Array(W * Hh).fill(120);
  for (var y = 0; y < Hh; y++) for (var x = 0; x < 11; x++) { Y[y * W + x] = 2; Y[y * W + (W - 1 - x)] = 2; }
  assert.deepStrictEqual(AC.bars({Y: Y, W: W, H: Hh}), [0, 0, 11, 11]);
  var half = new Float32Array(W * Hh).fill(70);       // bar edge falls mid-column: that column is half grey
  for (var y2 = 0; y2 < Hh; y2++) { for (var x2 = 0; x2 < 10; x2++) { half[y2 * W + x2] = 1; half[y2 * W + W - 1 - x2] = 1; } half[y2 * W + 10] = 8; half[y2 * W + W - 11] = 8; }
  half[5 * W + 10] = 12; half[5 * W + W - 11] = 12;
  assert.deepStrictEqual(AC.bars({Y: half, W: W, H: Hh}).slice(2), [11, 11]);
  var night = new Float32Array(W * Hh).fill(5);       // a night scene: dark everywhere, no edge
  assert.deepStrictEqual(AC.bars({Y: night, W: W, H: Hh}), [0, 0, 0, 0]);
});
test("a soft shot among sharp ones is flagged as out of focus", function () {
  var shots = [];
  for (var i = 0; i < 12; i++) shots.push({t: i * 10, sharp: 900 + i * 10, lum: 100, std: 40});
  shots.push({t: 200, sharp: 150, lum: 100, std: 40});
  var o = H.out(); H.blur(o, shots);
  var r = o.finish().issues; assert.strictEqual(r.length, 1); assert.strictEqual(r[0].t, 200);
});
test("sharpness: an edge pattern is sharper than a smooth gradient", function () {
  var W = 64, Hh = 36, a = new Float32Array(W * Hh), b = new Float32Array(W * Hh);
  for (var y = 0; y < Hh; y++) for (var x = 0; x < W; x++) { a[y * W + x] = (x + y) % 2 ? 200 : 40; b[y * W + x] = 40 + x * 2; }
  assert.ok(AC.sharpness({Y: a, W: W, H: Hh}).sharp > AC.sharpness({Y: b, W: W, H: Hh}).sharp * 50);
});
test("flatShare: clipped wave piles up at its peak, a clean one doesn't", function () {
  var n = 800, clean = new Float32Array(n), clip = new Float32Array(n);
  for (var k = 0; k < n; k++) {
    var v = 0.6 * Math.sin(2 * Math.PI * 220 * k / 16000) + 0.25 * Math.sin(2 * Math.PI * 523 * k / 16000);
    clean[k] = v; clip[k] = Math.max(-0.35, Math.min(0.35, v));
  }
  var pc = Math.max.apply(null, Array.from(clean, Math.abs)), pq = Math.max.apply(null, Array.from(clip, Math.abs));
  assert.ok(AC.flatShare(clip, 0, n, pq) > 0.2, "clipped " + AC.flatShare(clip, 0, n, pq));
  assert.ok(AC.flatShare(clean, 0, n, pc) < 0.08, "clean " + AC.flatShare(clean, 0, n, pc));
});
/* audio windows (0.05 s) */
function A(sec, level) {
  var m = Math.round(sec / 0.05), a = {win: 0.05, dur: sec, rms: new Float32Array(m).fill(level), pk: new Float32Array(m).fill(level * 3),
                                       clk: new Float32Array(m), score: new Float32Array(m), flat: new Float32Array(m)};
  return a;
}
test("distorted speech is flagged, clean speech isn't", function () {
  var a = A(120, 0.1);
  var o = H.out(); H.clipping(o, a, 1); assert.deepStrictEqual(kinds(o), []);
  for (var w = 600; w < 640; w++) { a.flat[w] = 0.3; a.pk[w] = 0.4; a.rms[w] = 0.25; }
  var o2 = H.out(); H.clipping(o2, a, 1); assert.deepStrictEqual(kinds(o2), ["clipping"]);
});
test("music stopping dead at the end is flagged; a fade out isn't", function () {
  var q = Q(240), a = A(120, 0.1);
  for (var w = 2380; w < 2400; w++) a.rms[w] = 0.0001;      // sound stops dead 1 s before the end
  q.lum.fill(110, 0, 240);
  for (var i = 238; i < 240; i++) q.lum[i] = 2;             // picture ends then too
  var o = H.out(); H.musicEnd(o, a, q); assert.deepStrictEqual(kinds(o), ["music_end"]);
  var b = A(120, 0.1);
  for (var w2 = 2300; w2 < 2400; w2++) b.rms[w2] = 0.1 * Math.pow(10, -(w2 - 2300) / 25);   // 4 dB per window: a 5 s fade
  var o2 = H.out(); H.musicEnd(o2, b, q); assert.deepStrictEqual(kinds(o2), []);
});
test("sound ending while the picture carries on is flagged", function () {
  var q = Q(240), a = A(120, 0.1);
  for (var w = 2000; w < 2400; w++) a.rms[w] = 0.0001 * Math.pow(10, -(w - 2000) / 50);   // gone after 100 s
  for (var w0 = 1900; w0 < 2000; w0++) a.rms[w0] = 0.1 * Math.pow(10, -(w0 - 1900) / 25); // faded out nicely
  var o = H.out(); H.musicEnd(o, a, q);
  var r = o.finish().issues; assert.strictEqual(r.length, 1); assert.ok(/carries on/.test(r[0].text));
});
test("a learnt limit turns a flag into a near miss (candidate)", function () {
  var q = Q(120); black(q, 50, 52);
  var o = H.out({black_frame: 3}); H.blackFrames(o, q, H.blackRuns(q));
  var r = o.finish(); assert.strictEqual(r.issues.length, 0); assert.strictEqual(r.cands.black_frame.length, 1);
});
test("black bars don't count as crushed blacks", function () {
  var q = Q(100); for (var i = 10; i < 20; i++) { q.bars[i * 4 + 2] = 11; q.bars[i * 4 + 3] = 11; q.dark[i] = 0.66; q.lum[i] = 45; }
  var o = H.out(); H.crushed(o, q, true); assert.deepStrictEqual(kinds(o), []);
});
test("samples the quick pass never saw are not evidence", function () {
  var q = Q(100); q.has = new Uint8Array(100).fill(1); q.red[40] = 0.95;
  for (var i = 41; i < 48; i++) { q.red[i] = 0.95; q.has[i] = 0; }     // copied forward from the red frame
  var o = H.out(); H.offline(o, q);
  var r = o.finish().issues; assert.strictEqual(r.length, 1); assert.strictEqual(r[0].end, 20.5);
});
test("crushed blacks: flagged at the limit, candidates below it", function () {
  var q = Q(100); for (var i = 10; i < 20; i++) q.dark[i] = 0.08; for (var j = 50; j < 60; j++) q.dark[j] = 0.03;
  var o = H.out(); H.crushed(o, q, true);
  var r = o.finish(); assert.deepStrictEqual(r.issues.map(function (x) { return x.kind; }), ["blacks"]);
  assert.strictEqual(r.cands.blacks.length, 1); assert.ok(r.cands.blacks[0][1] < 1);
});
console.log(passed + " passed, " + failed + " failed");
process.exit(failed ? 1 : 0);
