const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const zooms = [0.8, 0.9, 1, 1.1, 1.25, 1.5];

async function setup(page, pageName = 'management_tasks') {
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('response', r => { if (r.status() >= 400) errors.push(`${r.status()} ${r.url()}`); });
  await page.route('http://toolbar.test/**', route => {
    const p = new URL(route.request().url()).pathname;
    if (p.startsWith('/static/')) return route.fulfill({ body: fs.readFileSync(path.resolve(root, 'api/app', p.slice(1))), contentType: p.endsWith('.css') ? 'text/css' : p.endsWith('.ttf') ? 'font/ttf' : 'image/svg+xml' });
    return route.fulfill({ contentType: 'text/html', body: fs.readFileSync(path.join(root, `api/app/templates/${pageName}.html`), 'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '').replace('<title>', '<title>Toolbar border probe · ') });
  });
  await page.goto('http://toolbar.test/probe');
  await page.evaluate(async () => { document.body.dataset.sidebarReady = '1'; await document.fonts.ready; });
  return errors;
}

async function read(page) {
  return page.evaluate(() => {
    const main = document.querySelector('#appContent'), toolbar = main.querySelector(':scope > .toolbar');
    const s = getComputedStyle(toolbar), p = getComputedStyle(main, '::before');
    const box = el => { const r = el.getBoundingClientRect(); return [r.x, r.y, r.width, r.height]; };
    const color = document.createElement('span'); color.style.color = 'var(--desk-border)'; toolbar.append(color);
    const expected = getComputedStyle(color).color; color.remove();
    return { width: innerWidth, dpr: devicePixelRatio, expected, border: s.borderBottomColor, borderWidth: parseFloat(s.borderBottomWidth),
      topWidth: parseFloat(p.borderTopWidth), sides: [p.borderRightWidth, p.borderBottomWidth, p.borderLeftWidth],
      sideColors: [p.borderRightColor, p.borderBottomColor, p.borderLeftColor],
      layout: { toolbar: box(toolbar), children: [...toolbar.children].map(box), main: box(main),
        padding: s.padding, gap: s.gap, radius: p.borderRadius, inset: p.inset, pseudoHeight: p.height, pseudoWidth: p.width } };
  });
}

async function verify(page, theme, label) {
  await page.evaluate(theme => { document.body.dataset.theme = theme; }, theme);
  const after = await read(page);
  assert.equal(after.border, after.expected, label);
  assert.ok(after.borderWidth > 0, label);
  assert.equal(after.topWidth, 0, `duplicate contour edge: ${label}`);
  assert.ok(after.sides.every(width => parseFloat(width) > 0), label);
  assert.ok(after.sideColors.every(color => color === after.expected), label);
  const old = await page.addStyleTag({ content: '@media(min-width:701px){body:has(.layout > .main) .layout > .main > .toolbar{border-bottom-color:transparent}body:has(.layout > .main) #appContent.main::before{border-width:1px}}' });
  const before = await read(page);
  await old.evaluate(el => el.remove());
  // Outer boxes and radii stay identical; the pseudo-element's content box gains the removed top border.
  const { pseudoHeight: oldHeight, ...oldLayout } = before.layout;
  const { pseudoHeight: newHeight, ...newLayout } = after.layout;
  assert.deepEqual(newLayout, oldLayout, `layout changed: ${label}`);
  assert.ok(Math.abs(parseFloat(newHeight) - parseFloat(oldHeight) - after.borderWidth) < 0.1, label);
  const image = await page.screenshot();
  const pixels = await page.evaluate(async ({ base64, expected }) => {
    const img = new Image(); img.src = `data:image/png;base64,${base64}`; await img.decode();
    const c = document.createElement('canvas'); c.width = img.width; c.height = img.height;
    const ctx = c.getContext('2d'); ctx.drawImage(img, 0, 0);
    const r = document.querySelector('.toolbar').getBoundingClientRect(), scale = devicePixelRatio;
    const rgb = expected.match(/\d+/g).slice(0, 3).map(Number);
    const x1 = Math.ceil((r.left + 32) * scale), x2 = Math.min(img.width - 1, Math.floor((r.right - 32) * scale));
    const rows = [];
    for (let y = Math.floor(r.bottom * scale) - 3; y <= Math.ceil(r.bottom * scale); y++) {
      const data = ctx.getImageData(x1, y, x2 - x1, 1).data;
      let matches = 0;
      for (let x = 0; x < x2 - x1; x++) if (rgb.every((v, i) => Math.abs(v - data[x * 4 + i]) <= 12)) matches++;
      rows.push(matches / (x2 - x1));
    }
    return rows;
  }, { base64: image.toString('base64'), expected: after.expected });
  assert.ok(Math.max(...pixels) > 0.98, `painted border missing: ${label}: ${pixels}`);
  return { theme, label, dpr: after.dpr, width: after.width, border: after.border, coverage: Math.max(...pixels) };
}

// CSS zoom + device scale exercise fractional rasterization in a repeatable headless check.
// The same verify() is also used with native Ctrl +/- browser zoom in the desktop smoke.
if (require.main === module) {
  for (const channel of ['msedge', 'chrome']) test(`${channel}: toolbar border fractional scaling, themes and page shells`, async t => {
    const browser = await chromium.launch({ channel, headless: true });
    t.after(() => browser.close());
    for (const pageName of ['management_tasks', 'tickets', 'ticket', 'management_users']) {
      const page = await browser.newPage({ viewport: { width: 1800, height: 1000 }, deviceScaleFactor: 2 });
      const errors = await setup(page, pageName);
      for (const zoom of zooms) for (const theme of ['light', 'dark']) {
        await page.evaluate(zoom => { document.documentElement.style.zoom = zoom; }, zoom);
        await verify(page, theme, `${channel}/${pageName}/${theme}/${zoom * 100}%`);
      }
      assert.deepEqual(errors, []);
      await page.close();
    }
  });
}
module.exports = { setup, read, verify, zooms };
