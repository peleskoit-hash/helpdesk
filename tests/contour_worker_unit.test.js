const test = require('node:test');
const assert = require('node:assert/strict');
let extractContours, cropSize;
test.before(async () => { ({ extractContours, cropSize } = await import('../api/app/static/contour_worker.js')); });
const defaults = { sensitivity: 50, detail: 50, thickness: 2, invert: false, cleanup: true };
function source(width = 64, height = 64, background = 255) {
  const data = new Uint8ClampedArray(width * height * 4);
  for (let p = 0; p < data.length; p += 4) { data[p] = data[p + 1] = data[p + 2] = background; data[p + 3] = 255; }
  return { data, width, height };
}
function rectangle(image, x, y, width, height, shade) {
  for (let row = y; row < y + height; row++) for (let col = x; col < x + width; col++) {
    const p = (row * image.width + col) * 4; image.data[p] = image.data[p + 1] = image.data[p + 2] = shade;
  }
  return image;
}
const run = (image, options = {}) => extractContours(image.data, image.width, image.height, { ...defaults, ...options });
const alpha = (result, image, x, y) => result.pixels[(y * image.width + x) * 4 + 3];

test('real Sobel edges surround the object while its centre and background remain transparent', () => {
  const image = rectangle(source(), 16, 16, 32, 32, 0), result = run(image);
  assert.ok(result.count > 100 && result.count < image.width * image.height / 2);
  assert.equal(alpha(result, image, 32, 32), 0);
  assert.equal(alpha(result, image, 0, 0), 0);
  assert.ok(Array.from({ length: 10 }, (_, n) => alpha(result, image, n + 12, 32)).some(value => value === 255));
  assert.equal(result.pixels.filter((_, i) => i % 4 === 3 && result.pixels[i] === 255).length, result.count);
  for (let p = 0; p < result.pixels.length; p += 4) assert.deepEqual([...result.pixels.slice(p, p + 3)], [0, 0, 0]);
});

test('uniform and fully transparent images produce no invented contours', () => {
  const flat = source();
  assert.equal(run(flat).count, 0);
  const transparent = source();
  for (let p = 3; p < transparent.data.length; p += 4) transparent.data[p] = 0;
  assert.equal(run(transparent, { sensitivity: 100, detail: 100, invert: true }).count, 0);
});

test('sensitivity reveals faint boundaries', () => {
  const image = rectangle(source(64, 64, 210), 16, 16, 32, 32, 175);
  assert.ok(run(image, { sensitivity: 100, detail: 100 }).count > run(image, { sensitivity: 0, detail: 100 }).count);
});

test('detail controls smoothing and rejection of fine noise', () => {
  const image = source(); let seed = 17;
  for (let p = 0; p < image.data.length; p += 4) {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    image.data[p] = image.data[p + 1] = image.data[p + 2] = 90 + seed % 120;
  }
  assert.ok(run(image, { sensitivity: 100, detail: 100, cleanup: false }).count > run(image, { sensitivity: 100, detail: 0, cleanup: false }).count);
});

test('thickness increases the visible line area', () => {
  const image = rectangle(source(), 16, 16, 32, 32, 0);
  const counts = [1, 2, 5, 10].map(thickness => run(image, { thickness }).count);
  assert.ok(counts.every((count, i) => i === 0 || count > counts[i - 1]));
});

test('inversion changes boundary polarity without filling the background or changing RGB by theme', () => {
  const image = rectangle(source(), 16, 16, 32, 32, 0), normal = run(image), inverted = run(image, { invert: true });
  assert.notDeepEqual(normal.pixels, inverted.pixels);
  assert.ok(inverted.count > 0);
  assert.equal(alpha(inverted, image, 0, 0), 0);
  assert.equal(alpha(inverted, image, 32, 32), 0);
});

test('cleanup removes edge-connected contours while preserving isolated interior ones', () => {
  const image = rectangle(rectangle(source(), 12, 25, 20, 26, 0), 52, 6, 12, 12, 0);
  const raw = run(image, { cleanup: false }), clean = run(image);
  assert.ok(clean.count > 0 && clean.count < raw.count);
  for (let y = 0; y < 64; y++) for (let x = 45; x < 64; x++) assert.equal(alpha(clean, image, x, y), 0);
});

test('crop precedes bounded resize and never upscales a small input', () => {
  assert.deepEqual(cropSize(4000, 3000, { x: 0, y: 0, width: 100, height: 100 }), { sx: 0, sy: 0, sw: 4000, sh: 3000, width: 2048, height: 1536 });
  assert.deepEqual(cropSize(4000, 3000, { x: 25, y: 25, width: 50, height: 50 }), { sx: 1000, sy: 750, sw: 2000, sh: 1500, width: 2000, height: 1500 });
  assert.deepEqual(cropSize(200, 100, { x: 0, y: 0, width: 100, height: 100 }), { sx: 0, sy: 0, sw: 200, sh: 100, width: 200, height: 100 });
});

test('invalid dimensions, crop or processing options fail explicitly', () => {
  const image = source();
  assert.throws(() => extractContours(image.data, 4096, 64, defaults));
  assert.throws(() => run(image, { sensitivity: NaN }));
  assert.throws(() => run(image, { thickness: 100 }));
  assert.throws(() => cropSize(100, 100, { x: 90, y: 0, width: 20, height: 100 }));
});
