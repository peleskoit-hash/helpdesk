const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });

async function setup(t, { theme = 'light', viewport = { width: 1440, height: 1000 }, touch = false } = {}) {
  const context = await browser.newContext({ viewport, hasTouch: touch });
  const page = await context.newPage(), errors = [], mutations = [];
  let before;
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (new URL(request.url()).pathname.startsWith('/api/') && request.method() !== 'GET') mutations.push(request.method() + ' ' + request.url()); });
  t.after(async () => {
    try {
      assert.deepEqual(errors, []); assert.deepEqual(mutations, []);
      if (before && !page.isClosed()) assert.equal(await page.evaluate(() => JSON.stringify(appearance)), before);
    } finally { await context.close(); }
  });
  await page.addInitScript(() => {
    window.editorMemory = { live: new Set(), created: [], revoked: [] };
    const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
    URL.createObjectURL = blob => { const url = create(blob); editorMemory.live.add(url); editorMemory.created.push(url); return url; };
    URL.revokeObjectURL = url => { editorMemory.live.delete(url); editorMemory.revoked.push(url); revoke(url); };
  });
  await page.route('http://desk.test/**', route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith('/static/')) {
      const file = path.resolve('api/app', url.pathname.slice(1));
      return route.fulfill({ body: fs.readFileSync(file), contentType: { '.js': 'text/javascript', '.css': 'text/css', '.ttf': 'font/ttf' }[path.extname(file)] || 'application/octet-stream' });
    }
    if (url.pathname === '/api/v1/background-contours') return route.fulfill({ json: { items: [] } });
    if (url.pathname.startsWith('/api/')) return route.fulfill({ json: { theme_preference: theme, appearance_revision: '0', background_patterns: null } });
    return route.fulfill({ contentType: 'text/html', body: fs.readFileSync('api/app/templates/settings.html', 'utf8')
      .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, script => script.includes('/static/theme.js?') ? script : '') });
  });
  await page.goto('http://desk.test/settings');
  await page.waitForFunction(() => confirmedAppearance.appearance_revision !== null);
  await page.locator('#themeToggleBtn').click();
  await page.locator('.appearance-pattern summary').click();
  before = await page.evaluate(() => JSON.stringify(appearance));
  const open = async () => {
    await page.getByRole('button', { name: 'Создать свой узор', exact: true }).click();
    await page.locator('#contourEditor').waitFor();
  };
  await open();
  return { page, editor: page.locator('#contourEditor'), open };
}

async function photo(page, mime = 'image/png', color = '#7760a8') {
  const data = await page.evaluate(async ({ mime, color }) => {
    const canvas = document.createElement('canvas'); canvas.width = 640; canvas.height = 480;
    const ctx = canvas.getContext('2d'); ctx.fillStyle = color; ctx.fillRect(0, 0, 640, 480);
    ctx.fillStyle = '#fffefd'; ctx.fillRect(70, 60, 160, 240);
    const blob = await new Promise(resolve => canvas.toBlob(resolve, mime));
    return [...new Uint8Array(await blob.arrayBuffer())];
  }, { mime, color });
  return { name: 'photo.' + ({ 'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp' }[mime]), mimeType: mime, buffer: Buffer.from(data) };
}
async function load(h, file) {
  await h.editor.locator('#contourEditorFile').setInputFiles(file);
  await h.page.waitForFunction(() => document.querySelector('.editor-photo-stage img')?.naturalWidth > 0 && !document.querySelector('.editor-crop').hidden);
}
async function discard(page, source = 'cancel') {
  if (source === 'escape') await page.keyboard.press('Escape');
  else await page.locator(source === 'cross' ? '#contourEditor .editor-close' : '#contourEditor .modal-foot [data-editor-close]').click();
  await page.locator('.desk-dialog:not(#contourEditor)').waitFor();
  await page.locator('[data-dialog-accept]').click();
  await page.waitForFunction(() => !document.querySelector('#contourEditor'));
}
const frames = page => page.locator('.editor-scheme rect').count();

for (const theme of ['light', 'dark']) test(`V2 modal opens with honest unavailable processing and no fictitious result: ${theme}`, async t => {
  const h = await setup(t, { theme });
  assert.equal(await h.editor.evaluate(el => el.matches(':modal')), true);
  assert.match(await h.editor.locator('#contourEditorAvailability').textContent(), /пока недоступны/);
  for (const id of ['contourSensitivity', 'contourDetail', 'contourThickness', 'contourInvert', 'contourCleanup']) {
    assert.equal(await h.editor.locator('#' + id).isDisabled(), true);
  }
  assert.equal(await h.editor.getByRole('button', { name: 'Сохранить', exact: true }).isDisabled(), true);
  assert.equal(await h.editor.locator('canvas, .editor-unavailable img').count(), 0);
  assert.match(await h.editor.locator('.editor-scheme').getAttribute('class'), /scheme/);
  assert.match(await h.editor.locator('.editor-scheme-count').textContent(), /1 элемент/);
  assert.equal(await frames(h.page), 1);
  assert.equal(await h.editor.locator('#contourRings').isDisabled(), true);
  await h.editor.locator('#contourEditorName').press('Enter');
  assert.equal(await h.editor.isVisible(), true);
  await h.editor.locator('.editor-close').click();
  assert.equal(await h.page.locator('#contourEditor').count(), 0);
  assert.equal(await h.page.locator('.desk-dialog').count(), 0, 'unchanged modal closes without confirmation');
});

for (const mime of ['image/jpeg', 'image/png', 'image/webp']) test(`photo ${mime} is decoded locally and its URL is cleared on close`, async t => {
  const h = await setup(t); await load(h, await photo(h.page, mime));
  assert.equal(await h.page.evaluate(() => editorMemory.live.size), 1);
  assert.equal(await h.editor.locator('#contourEditorFile').evaluate(el => el.files.length), 0);
  assert.equal(await h.editor.locator('.editor-photo-stage img').getAttribute('src').then(url => url.startsWith('blob:')), true);
  assert.match(await h.editor.locator('.editor-photo-name').textContent(), /photo\.(jpg|png|webp) · 640×480 px/);
  assert.equal(await h.editor.getByRole('button', { name: 'Сохранить', exact: true }).isDisabled(), true);
  assert.equal(await h.editor.locator('.editor-unavailable img').count(), 0);
  await discard(h.page);
  assert.equal(await h.page.evaluate(() => editorMemory.live.size), 0);
  assert.equal(await h.page.evaluate(() => editorMemory.created.length === editorMemory.revoked.length), true);
});

test('byte limit accepts 20 MB exactly and rejects larger files without losing the existing photo', async t => {
  const h = await setup(t), valid = await photo(h.page);
  const buffer = Buffer.concat([valid.buffer, Buffer.alloc(20 * 1024 * 1024 - valid.buffer.length)]);
  await load(h, { ...valid, buffer });
  const current = await h.editor.locator('.editor-photo-stage img').getAttribute('src');
  await h.editor.locator('#contourEditorFile').setInputFiles({ ...valid, buffer: Buffer.concat([buffer, Buffer.from([0])]) });
  await h.page.waitForFunction(() => document.querySelector('#contourEditorError').textContent.includes('20 МБ'));
  assert.equal(await h.editor.locator('.editor-photo-stage img').getAttribute('src'), current);
  assert.equal(await h.page.evaluate(() => editorMemory.live.size), 1);
  await discard(h.page);
});

for (const kind of ['svg', 'gif', 'text', 'corrupt-png']) test(`rejects invalid ${kind} by content and cleans failed image URLs`, async t => {
  const h = await setup(t);
  const content = { svg: '<svg xmlns="http://www.w3.org/2000/svg"/>', gif: 'GIF89a invalid', text: 'not a photo', 'corrupt-png': '\x89PNG\r\n\x1a\nbroken' }[kind];
  await h.editor.locator('#contourEditorFile').setInputFiles({ name: 'spoof.png', mimeType: 'image/png', buffer: Buffer.from(content, 'latin1') });
  await h.page.waitForFunction(() => {
    const text = document.querySelector('#contourEditorError').textContent;
    return text && !text.includes('Загрузка');
  });
  assert.match(await h.editor.locator('#contourEditorError').textContent(), /JPG, PNG|прочитать/);
  assert.equal(await h.page.evaluate(() => editorMemory.live.size), 0);
  assert.equal(await h.editor.locator('.editor-photo-stage img').count(), 0);
  await h.editor.locator('.editor-close').click();
  assert.equal(await h.page.locator('.desk-dialog:not(#contourEditor)').count(), 0);
});

test('replacing a photo revokes the old URL; confirmed close leaves no draft on reopen', async t => {
  const h = await setup(t); await load(h, await photo(h.page));
  const old = await h.editor.locator('.editor-photo-stage img').getAttribute('src');
  const replacement = await photo(h.page, 'image/webp', '#29a599');
  await h.editor.locator('#contourEditorFile').setInputFiles(replacement);
  await h.page.waitForFunction(old => document.querySelector('.editor-photo-stage img')?.src !== old && editorMemory.revoked.includes(old), old);
  assert.equal(await h.page.evaluate(() => editorMemory.live.size), 1);
  await h.editor.locator('#contourEditorName').fill('Мой контур');
  await h.editor.locator('#contourRepeat').check();
  await discard(h.page, 'cross');
  assert.equal(await h.page.evaluate(() => editorMemory.live.size), 0);
  await h.open();
  assert.equal(await h.editor.locator('#contourEditorName').inputValue(), '');
  assert.equal(await h.editor.locator('.editor-photo-stage img').count(), 0);
  assert.equal(await h.editor.locator('#contourRepeat').isChecked(), false);
  assert.equal(await frames(h.page), 1);
  assert.deepEqual(await h.page.evaluate(() => [Object.keys(localStorage), Object.keys(sessionStorage)]), [[], []]);
});

test('crop drag and keyboard coordinates stay inside the actual photo and do not close the editor', async t => {
  const h = await setup(t); await load(h, await photo(h.page));
  await h.editor.locator('.editor-photo-stage').scrollIntoViewIfNeeded();
  const rect = await h.editor.locator('.editor-photo-stage').boundingBox();
  await h.page.mouse.move(rect.x + rect.width * .2, rect.y + rect.height * .25);
  await h.page.mouse.down(); await h.page.mouse.move(rect.x + rect.width * .75, rect.y + rect.height * .8, { steps: 6 }); await h.page.mouse.up();
  const width = Number(await h.editor.locator('[data-crop="width"]').inputValue());
  assert.ok(width > 30 && width < 90);
  await h.editor.locator('[data-crop="x"]').fill('95');
  assert.ok(Number(await h.editor.locator('[data-crop="width"]').inputValue()) <= 5);
  await h.editor.locator('[data-crop-reset]').click();
  assert.equal(await h.editor.locator('[data-crop="width"]').inputValue(), '100');
  assert.equal(await h.editor.isVisible(), true);
  assert.equal(await h.page.locator('.desk-dialog:not(#contourEditor)').count(), 0);
});

test('repeat layouts 1/9/25 auto-fit only when enabling repetitions or adding a ring', async t => {
  const h = await setup(t);
  await h.editor.locator('#contourRepeat').check();
  assert.equal(await h.editor.locator('#contourRings').inputValue(), '1');
  assert.equal(await frames(h.page), 9);
  const firstFit = await h.editor.locator('#contourScale').inputValue();
  await h.editor.locator('#contourScale').fill('180');
  await h.editor.locator('#contourDistance').fill('150');
  await h.editor.locator('#contourRotation').fill('47');
  assert.equal(await h.editor.locator('#contourScale').inputValue(), '180');
  assert.equal(await frames(h.page), 9);
  const transforms = await h.editor.locator('.editor-scheme rect').evaluateAll(nodes => nodes.map(node => node.getAttribute('transform')));
  assert.ok(transforms.every(transform => transform.endsWith('rotate(47)')));
  assert.equal(new Set(transforms.map(value => value.match(/translate\(([^ ]+)/)[1])).size, 3);
  await h.editor.locator('#contourRings').selectOption('2');
  assert.equal(await frames(h.page), 25);
  assert.notEqual(await h.editor.locator('#contourScale').inputValue(), '180');
  await h.editor.locator('#contourScale').fill('200');
  await h.editor.locator('#contourRings').selectOption('1');
  assert.equal(await h.editor.locator('#contourScale').inputValue(), '200');
  await h.editor.locator('#contourRepeat').uncheck();
  assert.equal(await frames(h.page), 1);
  assert.equal(await h.editor.locator('#contourScale').inputValue(), '200');
  await h.editor.locator('#contourRepeat').check();
  assert.equal(await h.editor.locator('#contourRings').inputValue(), '1');
  assert.notEqual(await h.editor.locator('#contourScale').inputValue(), '200');
  assert.ok(Number(firstFit) > 0);
});

test('manual fit contains the rotated frames; later changes may clip them without auto-fitting', async t => {
  const h = await setup(t);
  await h.editor.locator('#contourRepeat').check(); await h.editor.locator('#contourRings').selectOption('2');
  await h.editor.locator('#contourDistance').fill('200'); await h.editor.locator('#contourRotation').fill('45');
  await h.editor.locator('[data-editor-fit]').click();
  const fits = () => h.editor.locator('.editor-scheme svg').evaluate(svg => {
    const area = svg.getBoundingClientRect();
    return [...svg.querySelectorAll('rect')].every(node => {
      const r = node.getBoundingClientRect(); return r.left >= area.left && r.right <= area.right && r.top >= area.top && r.bottom <= area.bottom;
    });
  });
  assert.equal(await fits(), true);
  await h.editor.locator('#contourScale').fill('400');
  assert.equal(await fits(), false);
  assert.equal(await h.editor.locator('.editor-scheme svg').evaluate(el => getComputedStyle(el).overflow), 'hidden');
});

for (const source of ['escape', 'cross', 'cancel']) test(`dirty ${source} requests confirmation and declining preserves the local photo`, async t => {
  const h = await setup(t); await load(h, await photo(h.page));
  if (source === 'escape') await h.page.keyboard.press('Escape');
  else await h.editor.locator(source === 'cross' ? '.editor-close' : '.modal-foot [data-editor-close]').click();
  await h.page.locator('.desk-dialog:not(#contourEditor)').waitFor();
  await h.page.locator('[data-dialog-cancel]').click();
  assert.equal(await h.editor.isVisible(), true);
  assert.equal(await h.page.evaluate(() => editorMemory.live.size), 1);
  await discard(h.page, source);
  assert.equal(await h.page.evaluate(() => editorMemory.live.size), 0);
});

test('closing during decode cannot resurrect an image or object URL', async t => {
  const h = await setup(t), file = await photo(h.page);
  await h.page.evaluate(() => {
    const decode = Image.prototype.decode;
    Image.prototype.decode = function () { return decode.call(this).then(() => new Promise(resolve => { window.releasePhotoDecode = resolve; })); };
  });
  await h.editor.locator('#contourEditorFile').setInputFiles(file);
  await h.page.waitForFunction(() => typeof releasePhotoDecode === 'function');
  await discard(h.page);
  await h.page.evaluate(() => releasePhotoDecode());
  assert.equal(await h.page.evaluate(() => editorMemory.live.size), 0);
  assert.equal(await h.page.locator('#contourEditor').count(), 0);
});

test('concurrent opening creates one modal and one stylesheet without carrying state across sessions', async t => {
  const h = await setup(t);
  await h.editor.locator('.editor-close').click();
  await h.page.evaluate(() => { const button = document.querySelector('[data-contour-create]'); button.click(); button.click(); });
  await h.page.locator('#contourEditor').waitFor();
  assert.equal(await h.page.locator('#contourEditor').count(), 1);
  assert.equal(await h.page.locator('link[href*="/static/contour-editor.css"]').count(), 1);
  assert.equal(await h.page.locator('#contourEditorName').inputValue(), '');
});

test('touch drag crops the photo while scrolling the editor elsewhere remains available', async t => {
  const h = await setup(t, { viewport: { width: 390, height: 844 }, touch: true });
  await load(h, await photo(h.page));
  await h.editor.locator('.editor-photo-stage').scrollIntoViewIfNeeded();
  const box = await h.editor.locator('.editor-photo-stage').boundingBox();
  const session = await h.page.context().newCDPSession(h.page);
  await session.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: box.x + box.width * .2, y: box.y + box.height * .3 }] });
  await session.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: box.x + box.width * .8, y: box.y + box.height * .7 }] });
  await session.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await session.detach();
  assert.ok(Number(await h.editor.locator('[data-crop="width"]').inputValue()) < 90);
  assert.equal(await h.editor.isVisible(), true);
  await h.editor.locator('#contourRotation').scrollIntoViewIfNeeded();
  assert.ok(await h.editor.locator('.modal-body').evaluate(el => el.scrollTop > 0));
});

for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }, { width: 640, height: 360 }]) test(`responsive editor and safe backdrop: ${viewport.width}x${viewport.height}`, async t => {
  const h = await setup(t, { viewport, touch: viewport.width === 390 });
  const bounds = await h.editor.boundingBox();
  assert.ok(bounds.x >= 0 && bounds.y >= 0 && bounds.x + bounds.width <= viewport.width && bounds.y + bounds.height <= viewport.height);
  assert.equal(await h.editor.locator('.modal-body').evaluate(el => el.scrollWidth <= el.clientWidth), true);
  await h.page.mouse.click(1, 1);
  assert.equal(await h.editor.isVisible(), true);
  await h.editor.locator('#contourRepeat').check();
  await h.editor.locator('#contourRings').selectOption('2');
  if (process.env.EDITOR_SCREENSHOT_DIR) {
    await load(h, await photo(h.page));
    await h.editor.locator('.modal-body').evaluate(el => { el.scrollTop = 0; });
    await h.editor.screenshot({ path: path.join(process.env.EDITOR_SCREENSHOT_DIR, `editor-${viewport.width}.png`) });
  }
  await discard(h.page);
});
