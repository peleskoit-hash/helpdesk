const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });

async function setup(t, { theme = 'light', viewport = { width: 1440, height: 1000 }, touch = false, workerMode = '' } = {}) {
  const context = await browser.newContext({ viewport, hasTouch: touch });
  const page = await context.newPage(), errors = [], mutations = [];
  let before;
  page.on('pageerror', error => errors.push(error.message));
  context.on('request', request => { if (new URL(request.url()).pathname.startsWith('/api/') && request.method() !== 'GET') mutations.push(request.method() + ' ' + request.url()); });
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
  await page.addInitScript(mode => {
    window.workerState = { created: 0, terminated: 0, jobs: [], failJob: mode === 'message-error' };
    if (mode === 'unsupported') { window.Worker = undefined; return; }
    const NativeWorker = Worker;
    window.Worker = class extends NativeWorker {
      constructor(...args) {
        if (mode === 'constructor-error') throw new Error('Synthetic Worker failure');
        super(...args); workerState.created++; window.currentEditorWorker = this;
        let held = false;
        this.addEventListener('message', event => {
          if (mode === 'hold-first' && event.data.id && !held) {
            held = true; event.stopImmediatePropagation();
            window.releaseWorkerResult = () => this.onmessage?.({ data: event.data });
          }
        });
      }
      postMessage(message, ...args) {
        workerState.jobs.push({ id: message.id, crop: message.crop, settings: message.settings });
        if (workerState.failJob) {
          workerState.failJob = false;
          setTimeout(() => this.onmessage?.({ data: { id: message.id, error: 'Ошибка обработки Worker.' } }), 0);
          return;
        }
        return super.postMessage(message, ...args);
      }
      terminate() { workerState.terminated++; return super.terminate(); }
    };
  }, workerMode);
  const flags = { workerLoadError: workerMode === 'load-error' };
  await context.route('http://desk.test/**', route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith('/static/')) {
      if (flags.workerLoadError && url.pathname.endsWith('/contour_worker.js')) return route.fulfill({ status: 503, body: '' });
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
    if (['unsupported', 'constructor-error', 'load-error'].includes(workerMode)) await page.waitForFunction(() => !document.querySelector('[data-process-retry]').hidden);
    else await page.waitForFunction(() => !document.querySelector('.editor-processing').disabled);
  };
  await open();
  return { page, editor: page.locator('#contourEditor'), open, flags };
}

async function photo(page, mime = 'image/png', color = '#7760a8', width = 640, height = 480) {
  const data = await page.evaluate(async ({ mime, color, width, height }) => {
    const canvas = document.createElement('canvas'); canvas.width = width; canvas.height = height;
    const ctx = canvas.getContext('2d'); ctx.fillStyle = color; ctx.fillRect(0, 0, width, height);
    ctx.fillStyle = '#fffefd'; ctx.fillRect(width * .1, height * .125, width * .25, height * .5);
    const blob = await new Promise(resolve => canvas.toBlob(resolve, mime));
    return [...new Uint8Array(await blob.arrayBuffer())];
  }, { mime, color, width, height });
  return { name: 'photo.' + ({ 'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp' }[mime]), mimeType: mime, buffer: Buffer.from(data) };
}
async function load(h, file) {
  const previous = await h.editor.locator('.editor-photo-stage img').evaluateAll(nodes => nodes[0]?.getAttribute('src') ?? null);
  await h.editor.locator('#contourEditorFile').setInputFiles(file);
  await h.page.waitForFunction(previous => {
    const image = document.querySelector('.editor-photo-stage img');
    return image?.naturalWidth > 0 && image.src !== previous && !document.querySelector('.editor-crop').hidden;
  }, previous);
}
const processed = page => page.waitForFunction(() => {
  const image = document.querySelector('.editor-contour-image');
  return image && !image.hidden && image.complete && image.naturalWidth > 0 && document.querySelector('.editor-processing-status').textContent.startsWith('Готово:');
});
async function discard(page, source = 'cancel') {
  if (source === 'escape') await page.keyboard.press('Escape');
  else await page.locator(source === 'cross' ? '#contourEditor .editor-close' : '#contourEditor .modal-foot [data-editor-close]').click();
  await page.locator('.desk-dialog:not(#contourEditor)').waitFor();
  await page.locator('[data-dialog-accept]').click();
  await page.waitForFunction(() => !document.querySelector('#contourEditor'));
}
const frames = page => page.locator('.editor-scheme use').count();

for (const theme of ['light', 'dark']) test(`V2 modal opens with local processing ready and saving disabled: ${theme}`, async t => {
  const h = await setup(t, { theme });
  assert.equal(await h.editor.evaluate(el => el.matches(':modal')), true);
  assert.match(await h.editor.locator('#contourEditorAvailability').textContent(), /локально/);
  for (const id of ['contourSensitivity', 'contourDetail', 'contourThickness', 'contourInvert', 'contourCleanup']) {
    assert.equal(await h.editor.locator('#' + id).isEnabled(), true);
  }
  assert.equal(await h.editor.getByRole('button', { name: 'Сохранить', exact: true }).isDisabled(), true);
  assert.equal(await h.editor.locator('canvas, .editor-unavailable img').count(), 0);
  assert.match(await h.editor.locator('.editor-scheme').getAttribute('class'), /scheme/);
  assert.match(await h.editor.locator('.editor-scheme-count').textContent(), /Нет обработанного/);
  assert.equal(await frames(h.page), 0);
  assert.equal(await h.editor.locator('#contourRings').isDisabled(), true);
  await h.editor.locator('#contourEditorName').press('Enter');
  assert.equal(await h.editor.isVisible(), true);
  await h.editor.locator('.editor-close').click();
  assert.equal(await h.page.locator('#contourEditor').count(), 0);
  assert.equal(await h.page.locator('.desk-dialog').count(), 0, 'unchanged modal closes without confirmation');
});

for (const mime of ['image/jpeg', 'image/png', 'image/webp']) test(`photo ${mime} is decoded locally and its URL is cleared on close`, async t => {
  const h = await setup(t); await load(h, await photo(h.page, mime));
  assert.ok(await h.page.evaluate(() => editorMemory.live.size >= 1 && editorMemory.live.size <= 2));
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
  assert.ok(await h.page.evaluate(() => editorMemory.live.size >= 1 && editorMemory.live.size <= 2));
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
  assert.ok(await h.page.evaluate(() => editorMemory.live.size >= 1 && editorMemory.live.size <= 2));
  await h.editor.locator('#contourEditorName').fill('Мой контур');
  await h.editor.locator('#contourRepeat').check();
  await discard(h.page, 'cross');
  assert.equal(await h.page.evaluate(() => editorMemory.live.size), 0);
  await h.open();
  assert.equal(await h.editor.locator('#contourEditorName').inputValue(), '');
  assert.equal(await h.editor.locator('.editor-photo-stage img').count(), 0);
  assert.equal(await h.editor.locator('#contourRepeat').isChecked(), false);
  assert.equal(await frames(h.page), 0);
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
  const h = await setup(t); await load(h, await photo(h.page)); await processed(h.page);
  await h.editor.locator('#contourRepeat').check();
  assert.equal(await h.editor.locator('#contourRings').inputValue(), '1');
  assert.equal(await frames(h.page), 9);
  const firstFit = await h.editor.locator('#contourScale').inputValue();
  await h.editor.locator('#contourScale').fill('180');
  await h.editor.locator('#contourDistance').fill('150');
  await h.editor.locator('#contourRotation').fill('47');
  assert.equal(await h.editor.locator('#contourScale').inputValue(), '180');
  assert.equal(await frames(h.page), 9);
  const transforms = await h.editor.locator('.editor-scheme use').evaluateAll(nodes => nodes.map(node => node.getAttribute('transform')));
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
  const h = await setup(t); await load(h, await photo(h.page)); await processed(h.page);
  await h.editor.locator('#contourRepeat').check(); await h.editor.locator('#contourRings').selectOption('2');
  await h.editor.locator('#contourDistance').fill('200'); await h.editor.locator('#contourRotation').fill('45');
  await h.editor.locator('[data-editor-fit]').click();
  const fits = () => h.editor.locator('.editor-scheme svg').evaluate(svg => {
    const area = svg.getBoundingClientRect();
    return [...svg.querySelectorAll('use')].every(node => {
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
  assert.ok(await h.page.evaluate(() => editorMemory.live.size >= 1 && editorMemory.live.size <= 2));
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
    await processed(h.page);
    await h.editor.locator('.modal-body').evaluate(el => { el.scrollTop = 0; });
    await h.editor.screenshot({ path: path.join(process.env.EDITOR_SCREENSHOT_DIR, `editor-${viewport.width}.png`) });
  }
  await discard(h.page);
});

async function pixels(page) {
  return page.locator('.editor-contour-image').evaluate(async image => {
    const canvas = document.createElement('canvas'); canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
    const ctx = canvas.getContext('2d'); ctx.drawImage(image, 0, 0);
    const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    let count = 0, colored = 0;
    for (let p = 0; p < data.length; p += 4) { if (data[p + 3]) count++; if (data[p] || data[p + 1] || data[p + 2]) colored++; }
    return { width: canvas.width, height: canvas.height, count, colored, corner: data[3], center: data[(Math.floor(canvas.height / 2) * canvas.width + Math.floor(canvas.width / 2)) * 4 + 3] };
  });
}

for (const [theme, width] of [['light', 1440], ['dark', 1440], ['dark', 390]]) test(`real transparent PNG and composition use theme-independent black lines: ${theme} ${width}`, async t => {
  const h = await setup(t, { theme, viewport: { width, height: width === 390 ? 844 : 1000 }, touch: width === 390 }); await load(h, await photo(h.page)); await processed(h.page);
  const result = await pixels(h.page);
  assert.deepEqual([result.width, result.height, result.colored, result.corner, result.center], [640, 480, 0, 0, 0]);
  assert.ok(result.count > 100 && result.count < 640 * 480 / 10);
  assert.equal(await h.editor.locator('.editor-contour-image').evaluate(el => getComputedStyle(el).filter), theme === 'dark' ? 'invert(1)' : 'none');
  const url = await h.editor.locator('.editor-contour-image').getAttribute('src');
  assert.equal(await h.editor.locator('.editor-scheme image').getAttribute('href'), url);
  assert.equal(await frames(h.page), 1);
  await h.editor.locator('#contourRepeat').check(); assert.equal(await frames(h.page), 9);
  await h.editor.locator('#contourRings').selectOption('2'); assert.equal(await frames(h.page), 25);
  assert.equal(await h.editor.locator('.editor-contour-image').getAttribute('src'), url, 'composition does not reprocess the photograph');
  if (process.env.EDITOR_SCREENSHOT_DIR) {
    await h.editor.locator('.modal-body').evaluate(el => { el.scrollTop = 0; });
    await h.editor.screenshot({ path: path.join(process.env.EDITOR_SCREENSHOT_DIR, `processed-${theme}-${width}.png`) });
    if (width === 390) {
      await h.editor.locator('.editor-contour-stage').scrollIntoViewIfNeeded();
      await h.editor.screenshot({ path: path.join(process.env.EDITOR_SCREENSHOT_DIR, 'mobile-contour.png') });
      await h.editor.locator('.editor-scheme').scrollIntoViewIfNeeded();
      await h.editor.screenshot({ path: path.join(process.env.EDITOR_SCREENSHOT_DIR, 'mobile-composition.png') });
    }
  }
  await discard(h.page);
  assert.deepEqual(await h.page.evaluate(() => [workerState.created, workerState.terminated, editorMemory.live.size]), [1, 1, 0]);
});

test('real Worker downsizes to 2048 and crops the source before resizing', async t => {
  const h = await setup(t); await load(h, await photo(h.page, 'image/png', '#7760a8', 3000, 1500)); await processed(h.page);
  assert.deepEqual(await h.editor.locator('.editor-contour-image').evaluate(el => [el.naturalWidth, el.naturalHeight]), [2048, 1024]);
  await h.editor.locator('[data-crop="width"]').fill('50'); await processed(h.page);
  assert.deepEqual(await h.editor.locator('.editor-contour-image').evaluate(el => [el.naturalWidth, el.naturalHeight]), [1500, 1500]);
  await h.editor.locator('[data-crop="x"]').fill('50');
  await h.page.waitForFunction(() => document.querySelector('.editor-processing-status').textContent.startsWith('Контуры не найдены'));
  assert.equal(await frames(h.page), 0, 'crop of a uniform area must not fabricate a contour');
  assert.equal(await h.editor.locator('.editor-contour-image').isHidden(), true);
});

test('processing controls change real pixels and send every setting to Worker', async t => {
  const h = await setup(t); await load(h, await photo(h.page)); await processed(h.page);
  const before = await pixels(h.page);
  await h.editor.locator('#contourThickness').fill('8'); await processed(h.page);
  assert.ok((await pixels(h.page)).count > before.count);
  await h.editor.locator('#contourSensitivity').fill('83');
  await h.editor.locator('#contourDetail').fill('92');
  await h.editor.locator('#contourInvert').check();
  await h.editor.locator('#contourCleanup').uncheck(); await processed(h.page);
  assert.deepEqual(await h.page.evaluate(() => workerState.jobs.at(-1).settings), { sensitivity: 83, detail: 92, thickness: 8, invert: true, cleanup: false });
  assert.equal((await pixels(h.page)).colored, 0);
});

test('fast sliders coalesce, only one job runs and stale replies cannot replace the latest result', async t => {
  const h = await setup(t, { workerMode: 'hold-first' }); await load(h, await photo(h.page));
  await h.page.waitForFunction(() => typeof releaseWorkerResult === 'function');
  await h.page.evaluate(() => {
    const input = document.querySelector('#contourSensitivity');
    for (let value = 10; value <= 90; value++) { input.value = value; input.dispatchEvent(new Event('input', { bubbles: true })); }
  });
  await h.page.waitForTimeout(250);
  assert.equal(await h.page.evaluate(() => workerState.jobs.length), 1);
  await h.page.evaluate(() => releaseWorkerResult()); await processed(h.page);
  assert.deepEqual(await h.page.evaluate(() => [workerState.jobs.length, workerState.jobs.at(-1).settings.sensitivity]), [2, 90]);
  const latest = await h.editor.locator('.editor-contour-image').getAttribute('src');
  await h.page.evaluate(() => releaseWorkerResult());
  assert.equal(await h.editor.locator('.editor-contour-image').getAttribute('src'), latest);
  assert.equal(await h.page.evaluate(() => editorMemory.live.size), 2);
});

test('replacement clears previous output immediately and discards old in-flight results', async t => {
  const h = await setup(t, { workerMode: 'hold-first' }); await load(h, await photo(h.page));
  await h.page.waitForFunction(() => typeof releaseWorkerResult === 'function');
  await load(h, await photo(h.page, 'image/webp', '#227799', 800, 600));
  assert.equal(await h.editor.locator('.editor-contour-image').isHidden(), true);
  await h.page.evaluate(() => releaseWorkerResult()); await processed(h.page);
  assert.deepEqual(await h.editor.locator('.editor-contour-image').evaluate(el => [el.naturalWidth, el.naturalHeight]), [800, 600]);
  const old = await h.editor.locator('.editor-contour-image').getAttribute('src');
  const file = await photo(h.page);
  await h.page.evaluate(() => {
    const decode = Image.prototype.decode;
    Image.prototype.decode = function () { return decode.call(this).then(() => new Promise(resolve => { window.continueDecode = resolve; })); };
  });
  await h.editor.locator('#contourEditorFile').setInputFiles(file);
  await h.page.waitForFunction(() => typeof continueDecode === 'function');
  assert.equal(await h.editor.locator('.editor-contour-image').isHidden(), true);
  assert.equal(await frames(h.page), 0);
  assert.equal(await h.page.evaluate(url => editorMemory.revoked.includes(url), old), true);
  await discard(h.page); await h.page.evaluate(() => continueDecode());
  assert.equal(await h.page.evaluate(() => editorMemory.live.size), 0);
});

for (const workerMode of ['unsupported', 'constructor-error', 'load-error']) test(`Worker ${workerMode} is recoverable without fake results`, async t => {
  const h = await setup(t, { workerMode }); await load(h, await photo(h.page));
  assert.equal(await h.editor.locator('#contourSensitivity').isDisabled(), true);
  assert.equal(await h.editor.locator('.editor-contour-image').isHidden(), true);
  assert.equal(await frames(h.page), 0);
  assert.match(await h.editor.locator('.editor-processing-status').textContent(), /Браузер|Worker/);
  if (workerMode === 'load-error') {
    h.flags.workerLoadError = false;
    await h.editor.locator('[data-process-retry]').click(); await processed(h.page);
    assert.ok((await pixels(h.page)).count > 0);
  }
  await discard(h.page);
  assert.equal(await h.page.evaluate(() => editorMemory.live.size), 0);
});

test('Worker processing error clears output, terminates Worker and retry processes the retained source', async t => {
  const h = await setup(t, { workerMode: 'message-error' }); await load(h, await photo(h.page));
  await h.page.waitForFunction(() => !document.querySelector('[data-process-retry]').hidden);
  assert.match(await h.editor.locator('.editor-processing-status').textContent(), /Ошибка обработки/);
  assert.equal(await h.editor.locator('.editor-contour-image').isHidden(), true);
  assert.equal(await h.page.evaluate(() => workerState.terminated), 1);
  await h.editor.locator('[data-process-retry]').click(); await processed(h.page);
  await h.page.evaluate(() => document.querySelector('#contourThickness').dispatchEvent(new Event('input', { bubbles: true })));
  assert.equal(await h.editor.locator('.editor-contour-image').isHidden(), true);
  await discard(h.page);
  assert.deepEqual(await h.page.evaluate(() => [workerState.created, workerState.terminated, editorMemory.live.size]), [2, 2, 0]);
  assert.deepEqual(await h.page.evaluate(() => [Object.keys(localStorage), Object.keys(sessionStorage)]), [[], []]);
});

test('closing during Worker processing terminates it and late responses cannot resurrect resources', async t => {
  const h = await setup(t, { workerMode: 'hold-first', viewport: { width: 390, height: 844 }, theme: 'dark', touch: true });
  await load(h, await photo(h.page)); await h.page.waitForFunction(() => typeof releaseWorkerResult === 'function');
  await discard(h.page); await h.page.evaluate(() => releaseWorkerResult());
  assert.deepEqual(await h.page.evaluate(() => [workerState.created, workerState.terminated, editorMemory.live.size]), [1, 1, 0]);
  assert.equal(await h.page.locator('#contourEditor').count(), 0);
});

for (const type of ['error', 'messageerror']) test(`Worker ${type} event clears an existing contour and supports retry`, async t => {
  const h = await setup(t); await load(h, await photo(h.page)); await processed(h.page);
  const url = await h.editor.locator('.editor-contour-image').getAttribute('src');
  await h.page.evaluate(type => currentEditorWorker.dispatchEvent(type === 'error' ? new ErrorEvent('error', { message: 'Synthetic failure', cancelable: true }) : new MessageEvent('messageerror')), type);
  assert.equal(await h.editor.locator('.editor-contour-image').isHidden(), true);
  assert.equal(await frames(h.page), 0);
  assert.equal(await h.page.evaluate(url => editorMemory.revoked.includes(url), url), true);
  assert.equal(await h.page.evaluate(() => workerState.terminated), 1);
  await h.editor.locator('[data-process-retry]').click(); await processed(h.page);
});

test('closing while the processed PNG decodes revokes its pending URL', async t => {
  const h = await setup(t); await load(h, await photo(h.page)); await processed(h.page);
  await h.page.evaluate(() => {
    const decode = Image.prototype.decode;
    Image.prototype.decode = function () { return decode.call(this).then(() => new Promise(resolve => { window.finishResultDecode = resolve; })); };
  });
  await h.editor.locator('#contourThickness').fill('5');
  await h.page.waitForFunction(() => typeof finishResultDecode === 'function');
  await discard(h.page); await h.page.evaluate(() => finishResultDecode());
  assert.equal(await h.page.evaluate(() => editorMemory.live.size), 0);
  assert.equal(await h.page.evaluate(() => workerState.terminated), 1);
  assert.equal(await h.page.locator('#contourEditor').count(), 0);
});
