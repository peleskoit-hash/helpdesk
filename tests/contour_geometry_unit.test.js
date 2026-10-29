const test = require('node:test');
const assert = require('node:assert/strict');
let contourLayout, fitContourScale, alphaBounds, contourMarkup;
test.before(async () => ({ contourLayout, fitContourScale, alphaBounds, contourMarkup } = await import('../api/app/static/contour_geometry.js')));
const asset = { width: 2048, height: 1536, bounds: { x: 900, y: 200, width: 200, height: 400 } };
const composition = { rings: 0, rotation: 0, distance: 20, base_scale: 100 };
for (const area of [{ width: 1920, height: 1080 }, { width: 390, height: 844 }]) test(`100% fits visible lines, proportional zoom crops and reduction shrinks: ${area.width}`, () => {
  const normal = contourLayout(asset, composition, area);
  assert.ok(Math.abs(Math.max(normal.extentWidth / area.width, normal.extentHeight / area.height) - .94) < 1e-9);
  assert.equal(normal.width / normal.height, .5);
  const zoom = contourLayout(asset, { ...composition, base_scale: 150 }, area), small = contourLayout(asset, { ...composition, base_scale: 50 }, area);
  assert.ok(Math.abs(zoom.width - normal.width * 1.5) < 1e-9); assert.equal(small.width, normal.width / 2);
  assert.ok(zoom.extentWidth > area.width || zoom.extentHeight > area.height);
  assert.ok(Math.abs(contourLayout(asset, composition, area, 150).width - zoom.width) < 1e-9);
});
test('rotation fits the rotated drawing while the repetition grid remains axis-aligned', () => {
  const area = { width: 1920, height: 1080 };
  for (const rotation of [0, 47, 90, 180, 360]) {
    const layout = contourLayout(asset, { ...composition, rotation }, area);
    assert.ok(Math.abs(Math.max(layout.extentWidth / area.width, layout.extentHeight / area.height) - .94) < 1e-9);
  }
});
test('auto-fit covers all rings, gaps, aspect ratios and angles within existing API limits', () => {
  for (const area of [{ width: 1920, height: 1080 }, { width: 390, height: 844 }, { width: 320, height: 1000 }])
    for (const ratio of [.1, .5, 1, 2, 10]) for (const rings of [0, 1, 2]) for (const distance of [0, 20, 100, 200]) for (const rotation of [0, 45, 47, 90]) {
      const image = { width: 100 * ratio, height: 100 }, parameters = { ...composition, rings, distance, rotation };
      const base_scale = fitContourScale(image, parameters, area), layout = contourLayout(image, { ...parameters, base_scale }, area);
      assert.ok(base_scale >= 50 && base_scale <= 200);
      assert.ok(layout.extentWidth <= area.width * .94 + 1e-6 && layout.extentHeight <= area.height * .94 + 1e-6);
    }
});
test('manual distance and scale are independent of fitting and old metadata needs no version', () => {
  const area = { width: 1440, height: 1000 }, parameters = { ...composition, rings: 2, base_scale: 137 };
  const a = contourLayout(asset, parameters, area), b = contourLayout(asset, { ...parameters, distance: 200 }, area);
  assert.equal(a.width, b.width); assert.ok(b.stepX > a.stepX && b.stepY > a.stepY);
  const old = contourLayout({ width: 80, height: 64 }, parameters, area);
  assert.ok(old.width > 0 && old.height > 0);
});
test('alpha bounds discard transparent margins without changing source pixels', () => {
  const data = new Uint8ClampedArray(20 * 10 * 4); data[(2 * 20 + 4) * 4 + 3] = 255; data[(7 * 20 + 12) * 4 + 3] = 1;
  const before = data.slice(); assert.deepEqual(alphaBounds(data, 20, 10), { x: 4, y: 2, width: 9, height: 6 });
  assert.deepEqual(data, before); assert.equal(alphaBounds(new Uint8ClampedArray(16), 2, 2), null);
});

test('zero distance touches rotated visible bounds on both axes for 9/25 copies without overlap', () => {
  for (const [width, height] of [[80, 64], [20, 200], [200, 20], [100, 100]]) for (const rings of [1, 2])
    for (const rotation of [0, 45, 90]) for (const base_scale of [50, 100, 200]) {
      const image = { width: 512, height: 512, bounds: { x: 30, y: 40, width, height } };
      const params = { rings, rotation, base_scale, distance: 0 };
      const layout = contourLayout(image, params, { width: 1440, height: 1000 });
      const angle = rotation * Math.PI / 180;
      const projectedX = layout.width * Math.abs(Math.cos(angle)) + layout.height * Math.abs(Math.sin(angle));
      const projectedY = layout.width * Math.abs(Math.sin(angle)) + layout.height * Math.abs(Math.cos(angle));
      const markup = contourMarkup(image, params, layout, 'test', 'blob:test');
      const positions = [...markup.matchAll(/translate\(([-\d.e+]+) ([-\d.e+]+)\)/g)].map(match => match.slice(1).map(Number));
      const side = rings * 2 + 1;
      assert.equal(positions.length, side ** 2);
      for (let y = 0; y < side; y++) for (let x = 0; x < side; x++) {
        const p = positions[y * side + x];
        if (x + 1 < side) assert.ok(Math.abs(positions[y * side + x + 1][0] - p[0] - projectedX) < 1e-9);
        if (y + 1 < side) assert.ok(Math.abs(positions[(y + 1) * side + x][1] - p[1] - projectedY) < 1e-9);
      }
    }
});
test('distance grows gaps linearly and independently on both axes without changing scale or drawing size', () => {
  for (const rings of [1, 2]) for (const rotation of [0, 45, 90]) {
    const params = Object.freeze({ rings, rotation, base_scale: 137, distance: 0 }), area = { width: 390, height: 844 };
    const zero = contourLayout(asset, params, area, 150);
    for (const distance of [0, 25, 50, 100, 150, 200]) {
      const layout = contourLayout(asset, { ...params, distance }, area, 150);
      assert.equal(layout.width, zero.width); assert.equal(layout.height, zero.height);
      assert.ok(Math.abs(layout.stepX - zero.stepX * (1 + distance / 100)) < 1e-9);
      assert.ok(Math.abs(layout.stepY - zero.stepY * (1 + distance / 100)) < 1e-9);
    }
    assert.equal(params.base_scale, 137);
  }
});
test('single-contour SVG remains identical regardless of distance', () => {
  const area = { width: 1920, height: 1080 };
  for (const rotation of [0, 45, 90]) for (const base_scale of [50, 100, 200]) {
    const params = { ...composition, rotation, base_scale, distance: 0 };
    const normal = contourLayout(asset, params, area);
    const spaced = contourLayout(asset, { ...params, distance: 200 }, area);
    assert.equal(normal.width, spaced.width); assert.equal(normal.height, spaced.height);
    assert.equal(contourMarkup(asset, params, normal, 'test', 'blob:test'), contourMarkup(asset, params, spaced, 'test', 'blob:test'));
  }
});
