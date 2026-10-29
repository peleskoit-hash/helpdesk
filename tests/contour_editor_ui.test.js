const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });

async function setup(t, { theme = 'light', viewport = { width: 1440, height: 1000 }, touch = false, workerMode = '', publishing = false, expand = true, deviceScaleFactor = 1 } = {}) {
  const context = await browser.newContext({ viewport, hasTouch: touch, deviceScaleFactor });
  const page = await context.newPage(), errors = [], mutations = [];
  let before;
  page.on('pageerror', error => errors.push(error.message));
  context.on('request', request => { if (new URL(request.url()).pathname.startsWith('/api/') && request.method() !== 'GET' && !(publishing && request.method() === 'POST' && new URL(request.url()).pathname === '/api/v1/background-contours')) mutations.push(request.method() + ' ' + request.url()); });
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
          if (mode === 'oversized-result' && event.data.blob) {
            event.stopImmediatePropagation();
            const blob = new Blob([event.data.blob, new Uint8Array(2 * 1024 * 1024)], { type: 'image/png' });
            this.onmessage?.({ data: { ...event.data, blob } });
          }
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
  const flags = { workerLoadError: workerMode === 'load-error', postError: null, posts: [], items: [], getCount: 0, getError: false, postHold: null };
  await context.route('http://desk.test/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith('/static/')) {
      if (flags.workerLoadError && url.pathname.endsWith('/contour_worker.js')) return route.fulfill({ status: 503, body: '' });
      const file = path.resolve('api/app', url.pathname.slice(1));
      return route.fulfill({ body: fs.readFileSync(file), contentType: { '.js': 'text/javascript', '.css': 'text/css', '.ttf': 'font/ttf' }[path.extname(file)] || 'application/octet-stream' });
    }
    if (url.pathname === '/api/v1/background-contours' && route.request().method() === 'POST') {
      const request = route.request();
      const form = await new Request(request.url(), { method: 'POST', headers: request.headers(), body: request.postDataBuffer() }).formData();
      const file = form.get('file'), buffer = Buffer.from(await file.arrayBuffer());
      const fields = Object.fromEntries([...form].filter(([, value]) => typeof value === 'string'));
      flags.posts.push({ fields, keys: [...form.keys()], buffer, type: file.type, filename: file.name });
      if (flags.postHold) await flags.postHold;
      if (flags.postError === 'network') return route.abort('failed');
      if (flags.postError === 'malformed') return route.fulfill({ status: 201, json: {} });
      if (flags.postError) return route.fulfill({ status: flags.postError, json: { detail: { error: { message: 'Ошибка сохранения контура ' + flags.postError } } } });
      const item = { id: `10000000-0000-4000-8000-${String(flags.posts.length).padStart(12, '0')}`,
        ...fields, rotation: Number(fields.rotation), rings: Number(fields.rings), distance: Number(fields.distance), base_scale: Number(fields.base_scale),
        author_name: 'Тестовый автор', can_edit: true, can_delete: true };
      flags.items.unshift(item); flags.publishedImage = buffer;
      return route.fulfill({ status: 201, json: item });
    }
    if (url.pathname === '/api/v1/background-contours') { flags.getCount++; return route.fulfill(flags.getError ? { status: 503, body: '' } : { json: { items: flags.items } }); }
    if (url.pathname.startsWith('/api/v1/background-contours/') && url.pathname.endsWith('/image')) return route.fulfill({ contentType: 'image/png', body: flags.publishedImage });
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
    await page.getByRole('button', { name: 'Добавить свой узор +', exact: true }).click();
    await page.locator('#contourEditor').waitFor();
    if (['unsupported', 'constructor-error', 'load-error'].includes(workerMode)) await page.waitForFunction(() => !document.querySelector('[data-process-retry]').hidden);
    else await page.waitForFunction(() => !document.querySelector('.editor-processing').disabled);
    if (expand) for (const summary of await page.locator('#contourEditor .editor-settings details:not([open]) > summary').all()) await summary.click();
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
async function openCrop(h) { if (!await h.editor.locator('.editor-crop-section').evaluate(el => el.open)) await h.editor.locator('.editor-crop-section > summary').click(); }
async function load(h, file) {
  const previous = await h.editor.locator('.editor-photo-stage img').evaluateAll(nodes => nodes[0]?.getAttribute('src') ?? null);
  await h.editor.locator('#contourEditorFile').setInputFiles(file);
  await h.page.waitForFunction(previous => {
    const image = document.querySelector('.editor-photo-stage img');
    return image?.naturalWidth > 0 && image.src !== previous && !document.querySelector('.editor-crop').hidden;
  }, previous);
}
const processed = page => page.waitForFunction(() => {
  const image = document.querySelector('.editor-scheme image');
  return image?.getAttribute('href') && document.querySelector('.editor-processing-status').textContent.startsWith('Готово:');
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
  await openCrop(h);
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
  const fits = () => h.editor.locator('.editor-scheme svg').first().evaluate(svg => {
    const area = svg.viewBox.baseVal, image = svg.querySelector('svg#editorContour');
    const width = Number(image.getAttribute('width')), height = Number(image.getAttribute('height'));
    return [...svg.querySelectorAll('use')].every(node => {
      const [x, y, angle] = node.getAttribute('transform').match(/-?[\d.]+/g).map(Number), radians = angle * Math.PI / 180;
      return [-1, 1].every(dx => [-1, 1].every(dy => {
        const px = x + dx * width / 2 * Math.cos(radians) - dy * height / 2 * Math.sin(radians);
        const py = y + dx * width / 2 * Math.sin(radians) + dy * height / 2 * Math.cos(radians);
        return px >= area.x && px <= area.x + area.width && py >= area.y && py <= area.y + area.height;
      }));
    });
  });
  assert.equal(await fits(), true);
  await h.editor.locator('#contourScale').fill('200');
  assert.equal(await fits(), false);
  assert.equal(await h.editor.locator('.editor-scheme > svg').evaluate(el => getComputedStyle(el).overflow), 'hidden');
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

async function imageSize(page) {
  return page.locator('.editor-scheme image').evaluate(async node => {
    const bitmap = await createImageBitmap(await (await fetch(node.getAttribute('href'))).blob());
    const size = [bitmap.width, bitmap.height]; bitmap.close(); return size;
  });
}
async function pixels(page) {
  return page.locator('.editor-scheme image').evaluate(async node => {
    const image = new Image(); image.src = node.getAttribute('href'); await image.decode();
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
  assert.equal(await h.editor.locator('.editor-scheme > svg').evaluate(el => getComputedStyle(el).filter), theme === 'dark' ? 'invert(1)' : 'none');
  const url = await h.editor.locator('.editor-scheme image').getAttribute('href');
  assert.equal(await frames(h.page), 1);
  await h.editor.locator('#contourRepeat').check(); assert.equal(await frames(h.page), 9);
  await h.editor.locator('#contourRings').selectOption('2'); assert.equal(await frames(h.page), 25);
  assert.equal(await h.editor.locator('.editor-scheme image').getAttribute('href'), url, 'composition does not reprocess the photograph');
  if (process.env.EDITOR_SCREENSHOT_DIR) {
    await h.editor.locator('.modal-body').evaluate(el => { el.scrollTop = 0; });
    await h.editor.screenshot({ path: path.join(process.env.EDITOR_SCREENSHOT_DIR, `processed-${theme}-${width}.png`) });
    if (width === 390) {
      await h.editor.locator('.editor-scheme').scrollIntoViewIfNeeded();
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
  assert.deepEqual(await imageSize(h.page), [2048, 1024]);
  await openCrop(h);
  await h.editor.locator('[data-crop="width"]').fill('50'); await processed(h.page);
  assert.deepEqual(await imageSize(h.page), [1500, 1500]);
  await openCrop(h);
  await h.editor.locator('[data-crop="x"]').fill('50');
  await h.page.waitForFunction(() => document.querySelector('.editor-processing-status').textContent.startsWith('Контуры не найдены'));
  assert.equal(await frames(h.page), 0, 'crop of a uniform area must not fabricate a contour');
  assert.equal(await h.editor.locator('.editor-scheme image').count(), 0);
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
  const latest = await h.editor.locator('.editor-scheme image').getAttribute('href');
  await h.page.evaluate(() => releaseWorkerResult());
  assert.equal(await h.editor.locator('.editor-scheme image').getAttribute('href'), latest);
  assert.equal(await h.page.evaluate(() => editorMemory.live.size), 2);
});

test('replacement clears previous output immediately and discards old in-flight results', async t => {
  const h = await setup(t, { workerMode: 'hold-first' }); await load(h, await photo(h.page));
  await h.page.waitForFunction(() => typeof releaseWorkerResult === 'function');
  await load(h, await photo(h.page, 'image/webp', '#227799', 800, 600));
  assert.equal(await h.editor.locator('.editor-scheme image').count(), 0);
  await h.page.evaluate(() => releaseWorkerResult()); await processed(h.page);
  assert.deepEqual(await imageSize(h.page), [800, 600]);
  const old = await h.editor.locator('.editor-scheme image').getAttribute('href');
  const file = await photo(h.page);
  await h.page.evaluate(() => {
    const decode = Image.prototype.decode;
    Image.prototype.decode = function () { return decode.call(this).then(() => new Promise(resolve => { window.continueDecode = resolve; })); };
  });
  await h.editor.locator('#contourEditorFile').setInputFiles(file);
  await h.page.waitForFunction(() => typeof continueDecode === 'function');
  assert.equal(await h.editor.locator('.editor-scheme image').count(), 0);
  assert.equal(await frames(h.page), 0);
  assert.equal(await h.page.evaluate(url => editorMemory.revoked.includes(url), old), true);
  await discard(h.page); await h.page.evaluate(() => continueDecode());
  assert.equal(await h.page.evaluate(() => editorMemory.live.size), 0);
});

for (const workerMode of ['unsupported', 'constructor-error', 'load-error']) test(`Worker ${workerMode} is recoverable without fake results`, async t => {
  const h = await setup(t, { workerMode }); await load(h, await photo(h.page));
  assert.equal(await h.editor.locator('#contourSensitivity').isDisabled(), true);
  assert.equal(await h.editor.locator('.editor-scheme image').count(), 0);
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
  assert.equal(await h.editor.locator('.editor-scheme image').count(), 0);
  assert.equal(await h.page.evaluate(() => workerState.terminated), 1);
  await h.editor.locator('[data-process-retry]').click(); await processed(h.page);
  await h.page.evaluate(() => document.querySelector('#contourThickness').dispatchEvent(new Event('input', { bubbles: true })));
  assert.equal(await h.editor.locator('.editor-scheme image').count(), 0);
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
  const url = await h.editor.locator('.editor-scheme image').getAttribute('href');
  await h.page.evaluate(type => currentEditorWorker.dispatchEvent(type === 'error' ? new ErrorEvent('error', { message: 'Synthetic failure', cancelable: true }) : new MessageEvent('messageerror')), type);
  assert.equal(await h.editor.locator('.editor-scheme image').count(), 0);
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

for (const [theme, width, rings] of [['light', 1440, 0], ['dark', 1440, 1], ['light', 390, 2], ['dark', 390, 2]]) test(`publish exact processed PNG and refresh library without selecting it: ${theme} ${width} ${rings}`, async t => {
  const h = await setup(t, { theme, viewport: { width, height: width === 390 ? 844 : 1000 }, touch: width === 390, publishing: true });
  await h.editor.locator('#contourEditorName').fill('   Мой контур   ');
  assert.equal(await h.editor.locator('[data-editor-save]').isDisabled(), true);
  const original = await photo(h.page, 'image/jpeg');
  await load(h, original); await processed(h.page);
  await h.editor.locator('#contourEditorName').fill('   ');
  assert.equal(await h.editor.locator('[data-editor-save]').isDisabled(), true);
  await h.editor.locator('#contourEditorName').fill('   Мой контур   ');
  assert.equal(await h.editor.locator('[data-editor-save]').isEnabled(), true);
  if (rings) { await h.editor.locator('#contourRepeat').check(); await h.editor.locator('#contourRings').selectOption(String(rings)); }
  await h.editor.locator('#contourRotation').fill('47');
  if (rings) await h.editor.locator('#contourDistance').fill('73');
  await h.editor.locator('#contourScale').fill('135');
  const expected = Buffer.from(await h.editor.locator('.editor-scheme image').evaluate(async image => [...new Uint8Array(await (await fetch(image.getAttribute('href'))).arrayBuffer())]));
  const gets = h.flags.getCount;
  if (process.env.EDITOR_SCREENSHOT_DIR) {
    await h.editor.locator('[data-editor-save]').scrollIntoViewIfNeeded();
    await h.editor.screenshot({ path: path.join(process.env.EDITOR_SCREENSHOT_DIR, `publish-${theme}-${width}.png`) });
  }
  await h.editor.locator('[data-editor-save]').click();
  await h.page.waitForFunction(() => !document.querySelector('#contourEditor'));
  await h.page.locator('.contour-card').waitFor();
  assert.equal(h.flags.posts.length, 1);
  const post = h.flags.posts[0];
  assert.deepEqual(post.keys.sort(), ['base_scale', 'distance', 'file', 'name', 'rings', 'rotation']);
  assert.deepEqual(post.fields, { name: 'Мой контур', rotation: '47', rings: String(rings), distance: rings ? '73' : '20', base_scale: '135' });
  assert.equal(post.type, 'image/png'); assert.equal(post.filename, 'contour.png');
  assert.deepEqual(post.buffer, expected); assert.notDeepEqual(post.buffer, original.buffer);
  assert.ok(post.buffer.length <= 2 * 1024 * 1024); assert.ok(h.flags.getCount > gets);
  assert.deepEqual(await h.page.evaluate(() => [editorMemory.live.size, workerState.created, workerState.terminated]), [0, 1, 1]);
  assert.equal(await h.page.locator('.contour-select').getAttribute('aria-pressed'), 'false');
  assert.equal(await h.page.locator('.contour-select').isEnabled(), true);
  assert.equal(await h.page.locator('.contour-name').textContent(), 'Мой контур');
  assert.deepEqual(await h.page.evaluate(() => [Object.keys(localStorage), Object.keys(sessionStorage)]), [[], []]);
});

test('save is invalidated by pending processing, uniform crop and photo replacement', async t => {
  const h = await setup(t); await load(h, await photo(h.page)); await processed(h.page);
  await h.editor.locator('#contourEditorName').fill('Контур');
  assert.equal(await h.editor.locator('[data-editor-save]').isEnabled(), true);
  await h.editor.locator('#contourThickness').fill('3');
  assert.equal(await h.editor.locator('[data-editor-save]').isDisabled(), true); await processed(h.page);
  await openCrop(h);
  await h.editor.locator('[data-crop="x"]').fill('50');
  await h.page.waitForFunction(() => document.querySelector('.editor-processing-status').textContent.startsWith('Контуры не найдены'));
  assert.equal(await h.editor.locator('[data-editor-save]').isDisabled(), true);
  await load(h, await photo(h.page));
  assert.equal(await h.editor.locator('[data-editor-save]').isDisabled(), true); await processed(h.page);
  assert.equal(await h.editor.locator('[data-editor-save]').isEnabled(), true);
});

test('API scale bounds apply to the visible preview and fit before saving', async t => {
  const h = await setup(t, { publishing: true }); await load(h, await photo(h.page)); await processed(h.page);
  assert.deepEqual(await h.editor.locator('#contourScale').evaluate(el => [el.min, el.max]), ['50', '200']);
  await h.editor.locator('#contourEditorName').fill('Масштаб');
  await h.editor.locator('#contourRepeat').check(); await h.editor.locator('#contourRings').selectOption('2');
  await h.editor.locator('#contourDistance').fill('200'); await h.editor.locator('#contourRotation').fill('45');
  await h.editor.locator('[data-editor-fit]').click();
  assert.ok(Number(await h.editor.locator('#contourScale').inputValue()) >= 50);
  const shownScale = await h.editor.locator('output[for="contourScale"]').textContent();
  await h.editor.locator('[data-editor-save]').click(); await h.page.waitForFunction(() => !document.querySelector('#contourEditor'));
  assert.equal(h.flags.posts[0].fields.base_scale + '%', shownScale);
});

for (const postError of [403, 413, 422, 503, 'network', 'malformed']) test(`POST ${postError} preserves the contour, name and composition for manual retry`, async t => {
  const h = await setup(t, { publishing: true }); await load(h, await photo(h.page)); await processed(h.page);
  await h.editor.locator('#contourEditorName').fill('Повторить');
  await h.editor.locator('#contourRotation').fill('91');
  const url = await h.editor.locator('.editor-scheme image').getAttribute('href');
  h.flags.postError = postError;
  await h.editor.locator('[data-editor-save]').click();
  await h.page.waitForFunction(() => !document.querySelector('[data-editor-save]').disabled && document.querySelector('#contourEditorError').textContent.length > 0);
  assert.equal(h.flags.posts.length, 1);
  assert.equal(await h.editor.locator('#contourEditorFile').isEnabled(), true);
  assert.equal(await h.editor.locator('#contourSensitivity').isEnabled(), true);
  assert.equal(await h.page.evaluate(() => { const event = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(event); return event.defaultPrevented; }), false);
  assert.equal(await h.editor.locator('#contourEditorName').inputValue(), 'Повторить');
  assert.equal(await h.editor.locator('#contourRotation').inputValue(), '91');
  assert.equal(await h.editor.locator('.editor-scheme image').getAttribute('href'), url);
  const preservedPNG = await pixels(h.page);
  assert.ok(preservedPNG.count > 0); assert.equal(preservedPNG.corner, 0);
  assert.equal(await h.editor.locator('.editor-scheme use').count(), 1);
  assert.equal(await h.page.evaluate(() => editorMemory.live.size), 2);
  assert.equal(await h.page.locator('.contour-card').count(), 0);
  h.flags.postError = null;
  await h.editor.locator('[data-editor-save]').click(); await h.page.waitForFunction(() => !document.querySelector('#contourEditor'));
  assert.equal(h.flags.posts.length, 2); assert.deepEqual(h.flags.posts[0].buffer, h.flags.posts[1].buffer);
  assert.equal(await h.page.evaluate(() => editorMemory.live.size), 0);
});

test('pending POST blocks duplicate submission, edits, Escape/cancel/cross and warns before leaving', async t => {
  const h = await setup(t, { publishing: true }); await load(h, await photo(h.page)); await processed(h.page);
  await h.editor.locator('#contourEditorName').fill('Ожидание');
  let release; h.flags.postHold = new Promise(resolve => { release = resolve; });
  await h.editor.locator('.editor-photo-stage').scrollIntoViewIfNeeded();
  const stage = await h.editor.locator('.editor-photo-stage').boundingBox();
  const contourURL = await h.editor.locator('.editor-scheme image').getAttribute('href');
  await h.page.mouse.move(stage.x + stage.width * .2, stage.y + stage.height * .2); await h.page.mouse.down();
  await h.page.evaluate(() => { const button = document.querySelector('[data-editor-save]'); button.click(); button.click(); });
  await h.page.waitForFunction(() => document.querySelector('#contourEditor')?.getAttribute('aria-busy') === 'true');
  await h.page.mouse.move(stage.x + stage.width * .8, stage.y + stage.height * .8); await h.page.mouse.up();
  assert.equal(await h.editor.locator('.editor-scheme image').getAttribute('href'), contourURL);
  assert.equal(await h.editor.locator('[data-crop="width"]').inputValue(), '100');
  assert.equal(await h.editor.locator('[data-editor-save]').isDisabled(), true);
  assert.equal(await h.editor.locator('#contourEditorName').isDisabled(), true);
  assert.equal(await h.editor.locator('#contourScale').isDisabled(), true);
  assert.equal(await h.page.evaluate(() => { const event = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(event); return event.defaultPrevented; }), true);
  for (const action of ['escape', 'cross', 'cancel']) {
    if (action === 'escape') await h.page.keyboard.press('Escape');
    else await h.editor.locator(action === 'cross' ? '.editor-close' : '.modal-foot [data-editor-close]').click();
    assert.equal(await h.editor.isVisible(), true);
    assert.match(await h.editor.locator('#contourEditorError').textContent(), /Сохранение ещё выполняется/);
    assert.equal(await h.page.locator('.desk-dialog:not(#contourEditor)').count(), 0);
  }
  release(); await h.page.waitForFunction(() => !document.querySelector('#contourEditor'));
  assert.equal(h.flags.posts.length, 1);
  assert.equal(await h.page.evaluate(() => { const event = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(event); return event.defaultPrevented; }), false);
});

test('oversized processed PNG is reduced proportionally with transparency and lines before POST', async t => {
  const h = await setup(t, { workerMode: 'oversized-result', publishing: true }); await load(h, await photo(h.page)); await processed(h.page);
  const bytes = await h.editor.locator('.editor-scheme image').evaluate(async image => (await (await fetch(image.getAttribute('href'))).blob()).size);
  assert.ok(bytes > 2 * 1024 * 1024);
  await h.editor.locator('#contourEditorName').fill('Уменьшенный');
  await h.editor.locator('[data-editor-save]').click(); await h.page.waitForFunction(() => !document.querySelector('#contourEditor'));
  const file = h.flags.posts[0].buffer;
  assert.ok(file.length <= 2 * 1024 * 1024);
  assert.equal(file.readUInt32BE(16), 480); assert.equal(file.readUInt32BE(20), 360);
  const inspected = await h.page.evaluate(async bytes => {
    const bitmap = await createImageBitmap(new Blob([new Uint8Array(bytes)], { type: 'image/png' }));
    const canvas = new OffscreenCanvas(bitmap.width, bitmap.height), ctx = canvas.getContext('2d'); ctx.drawImage(bitmap, 0, 0); bitmap.close();
    const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    return { transparent: data.some((value, i) => i % 4 === 3 && value === 0), visible: data.some((value, i) => i % 4 === 3 && value > 0) };
  }, [...file]);
  assert.deepEqual(inspected, { transparent: true, visible: true });
});

test('failed PNG reduction sends nothing and preserves processed data for another attempt', async t => {
  const h = await setup(t, { workerMode: 'oversized-result', publishing: true }); await load(h, await photo(h.page)); await processed(h.page);
  await h.editor.locator('#contourEditorName').fill('Большой');
  const url = await h.editor.locator('.editor-scheme image').getAttribute('href');
  await h.page.evaluate(() => {
    window.originalConvertToBlob = OffscreenCanvas.prototype.convertToBlob;
    OffscreenCanvas.prototype.convertToBlob = async function () { const blob = await originalConvertToBlob.call(this, { type: 'image/png' }); return new Blob([blob, new Uint8Array(2 * 1024 * 1024)], { type: 'image/png' }); };
  });
  await h.editor.locator('[data-editor-save]').click();
  await h.page.waitForFunction(() => document.querySelector('#contourEditorError').textContent.includes('до 2 МБ'));
  assert.equal(h.flags.posts.length, 0);
  assert.equal(await h.editor.locator('[data-editor-save]').isEnabled(), true);
  await h.page.evaluate(() => { OffscreenCanvas.prototype.convertToBlob = originalConvertToBlob; });
  await h.editor.locator('[data-editor-save]').click(); await h.page.waitForFunction(() => !document.querySelector('#contourEditor'));
  assert.equal(h.flags.posts.length, 1);
});

test('PNG preflight rejects wrong content, oversized dimensions, opacity and missing lines', async t => {
  const h = await setup(t);
  const messages = await h.page.evaluate(async () => {
    const { prepareContourPNG } = await import('/static/contour_export.js?v=20261029-contour-geometry-v1');
    const make = async (width, height, opaque, empty) => {
      const canvas = new OffscreenCanvas(width, height), ctx = canvas.getContext('2d');
      if (opaque) { ctx.fillStyle = 'white'; ctx.fillRect(0, 0, width, height); }
      if (!empty) ctx.fillRect(1, 1, 20, 20);
      return canvas.convertToBlob({ type: 'image/png' });
    };
    const blobs = [new Blob(['not PNG'], { type: 'image/png' }), await make(2049, 1, false, false), await make(32, 32, true, false), await make(32, 32, false, true)];
    const messages = [];
    for (const blob of blobs) { try { await prepareContourPNG(blob); messages.push('accepted'); } catch (error) { messages.push(error.message); } }
    return messages;
  });
  assert.match(messages[0], /PNG/); assert.match(messages[1], /2048/); assert.match(messages[2], /прозрачном фоне/); assert.match(messages[3], /видимый контур/);
});

test('confirmed publish stays closed if library refresh fails; library Retry reveals the published contour', async t => {
  const h = await setup(t, { publishing: true }); await load(h, await photo(h.page)); await processed(h.page);
  await h.editor.locator('#contourEditorName').fill('Опубликован'); h.flags.getError = true;
  await h.editor.locator('[data-editor-save]').click(); await h.page.waitForFunction(() => !document.querySelector('#contourEditor'));
  await h.page.waitForFunction(() => !document.querySelector('.contour-retry').hidden);
  assert.equal(h.flags.posts.length, 1); assert.equal(await h.page.evaluate(() => editorMemory.live.size), 0);
  assert.match(await h.page.locator('.contour-status').textContent(), /Не удалось загрузить/);
  h.flags.getError = false;
  await h.page.locator('.contour-retry').click(); await h.page.locator('.contour-card').waitFor();
  assert.equal(h.flags.posts.length, 1);
  assert.equal(await h.page.locator('.contour-select').getAttribute('aria-pressed'), 'false');
});

test('PNG accepts exactly 2048px and 2 MB without hidden resizing and releases native export resources', async t => {
  const h = await setup(t);
  const result = await h.page.evaluate(async () => {
    const { prepareContourPNG } = await import('/static/contour_export.js?v=20261029-contour-geometry-v1');
    const canvas = new OffscreenCanvas(2048, 1024), ctx = canvas.getContext('2d'); ctx.fillRect(500, 200, 20, 200);
    const png = await canvas.convertToBlob({ type: 'image/png' });
    const blob = new Blob([png, new Uint8Array(2 * 1024 * 1024 - png.size)], { type: 'image/png' });
    const create = window.createImageBitmap; let closed = 0;
    window.createImageBitmap = async (...args) => { const bitmap = await create(...args), close = bitmap.close.bind(bitmap); bitmap.close = () => { closed++; close(); }; return bitmap; };
    const prepared = await prepareContourPNG(blob);
    let message;
    try { await prepareContourPNG(new Blob([png.slice(0, 20)], { type: 'image/png' })); } catch (error) { message = error.message; }
    const Canvas = window.OffscreenCanvas;
    window.OffscreenCanvas = class extends Canvas { getContext() { return null; } };
    try { await prepareContourPNG(blob); } catch (error) { message = error.message; }
    finally { window.OffscreenCanvas = Canvas; window.createImageBitmap = create; }
    return { same: prepared.blob === blob, size: prepared.blob.size, width: prepared.width, height: prepared.height, closed, message };
  });
  assert.deepEqual(result, { same: true, size: 2 * 1024 * 1024, width: 2048, height: 1024, closed: 2, message: 'Не удалось проверить PNG: Canvas 2D недоступен.' });
});

for (const theme of ['light', 'dark']) for (const viewport of [{ width: 1920, height: 1080 }, { width: 390, height: 844 }]) test(`compact editor has two previews, inline hints and expanded independent settings: ${theme} ${viewport.width}`, async t => {
  const h = await setup(t, { theme, viewport, touch: viewport.width === 390, expand: false, publishing: true });
  assert.deepEqual(await h.editor.locator('.editor-previews > section > h3').allTextContents(), ['Оригинал', 'Фоновый узор']);
  assert.equal(await h.editor.locator('.editor-previews > section').count(), 2);
  assert.equal(await h.editor.locator('.editor-contour-stage, .editor-contour-image, .editor-notice').count(), 0);
  assert.equal(await h.editor.locator('.modal-head #contourEditorAvailability').count(), 1);
  assert.equal(await h.editor.locator('.modal-body #contourEditorAvailability').count(), 0);
  assert.deepEqual(await h.editor.locator('details').evaluateAll(nodes => nodes.map(node => node.open)), [false]);
  assert.deepEqual(await h.editor.locator('details > summary').allTextContents(), ['Кадрирование']);
  const original = await h.editor.locator('.editor-photo-stage').boundingBox(), composition = await h.editor.locator('.editor-scheme').boundingBox();
  const bounds = await h.editor.boundingBox();
  assert.ok(bounds.x >= 0 && bounds.y >= 0 && bounds.x + bounds.width <= viewport.width && bounds.y + bounds.height <= viewport.height);
  assert.equal(await h.editor.locator('.modal-body').evaluate(el => el.scrollWidth <= el.clientWidth), true);
  if (viewport.width === 1920) {
    assert.ok(bounds.width >= 1450 && original.width > 650 && composition.width > 650);
    assert.equal(original.y, composition.y); assert.equal(original.height, composition.height);
    assert.ok(original.height >= 320);
    assert.equal(await h.editor.locator('.modal-body').evaluate(el => el.scrollHeight <= el.clientHeight), true);
  } else assert.ok(composition.y > original.y + original.height);
  await load(h, await photo(h.page)); await processed(h.page);
  await h.editor.locator('#contourEditorName').fill('Компактный редактор');
  assert.equal(await h.editor.locator('[data-editor-save]').isEnabled(), true);
  assert.deepEqual(await h.editor.locator('details').evaluateAll(nodes => nodes.map(node => node.open)), [false]);
  if (process.env.EDITOR_SCREENSHOT_DIR) {
    await h.editor.locator('.modal-body').evaluate(el => { el.scrollTop = 0; });
    await h.editor.screenshot({ path: path.join(process.env.EDITOR_SCREENSHOT_DIR, `compact-${theme}-${viewport.width}.png`) });
  }
  assert.equal(await h.editor.locator('.editor-settings details, .editor-settings summary').count(), 0);
  assert.deepEqual(await h.editor.locator('.editor-settings > section > h3').allTextContents(), ['Обработка', 'Композиция']);
  await h.editor.locator('#contourScale').fill('137'); await h.editor.locator('#contourSensitivity').fill('73'); await processed(h.page);
  const jobs = await h.page.evaluate(() => workerState.jobs.length), url = await h.editor.locator('.editor-scheme image').getAttribute('href');
  await h.editor.locator('.editor-processing-section > h3').click();
  assert.equal(await h.editor.locator('#contourSensitivity').isVisible(), true);
  assert.equal(await h.editor.locator('#contourSensitivity').inputValue(), '73');
  assert.equal(await h.editor.locator('#contourScale').inputValue(), '137');
  assert.equal(await h.page.evaluate(() => workerState.jobs.length), jobs);
  if (process.env.EDITOR_SCREENSHOT_DIR) {
    await h.editor.locator('.editor-settings').scrollIntoViewIfNeeded();
    await h.editor.screenshot({ path: path.join(process.env.EDITOR_SCREENSHOT_DIR, `expanded-${theme}-${viewport.width}.png`) });
  }
  await h.editor.locator('[data-editor-save]').click(); await h.page.waitForFunction(() => !document.querySelector('#contourEditor'));
  assert.equal(h.flags.posts[0].fields.base_scale, '137'); assert.equal(h.flags.posts.length, 1);
  assert.equal(await h.page.evaluate(() => editorMemory.live.size), 0);
});

test('crop drag works while collapsed; numeric crop and reset stay functional after toggling', async t => {
  const h = await setup(t, { expand: false }); await load(h, await photo(h.page)); await processed(h.page);
  const rect = await h.editor.locator('.editor-photo-stage').boundingBox();
  await h.page.mouse.move(rect.x + rect.width * .3, rect.y + rect.height * .2); await h.page.mouse.down();
  await h.page.mouse.move(rect.x + rect.width * .7, rect.y + rect.height * .8); await h.page.mouse.up();
  assert.equal(await h.editor.locator('.editor-crop-section').evaluate(el => el.open), false);
  assert.ok(Number(await h.editor.locator('[data-crop="width"]').inputValue()) < 90);
  await h.editor.locator('.editor-crop-section > summary').focus(); await h.page.keyboard.press('Enter');
  await h.editor.locator('[data-crop-reset]').click(); await processed(h.page);
  await openCrop(h);
  await h.editor.locator('[data-crop="width"]').fill('80'); await processed(h.page);
  assert.deepEqual(await imageSize(h.page), [512, 480]);
  const cropWidth = await h.editor.locator('.editor-crop').evaluate(el => el.style.width);
  await h.editor.locator('.editor-crop-section > summary').click();
  await h.editor.locator('.editor-crop-section > summary').click();
  assert.equal(await h.editor.locator('[data-crop="width"]').inputValue(), '80');
  assert.equal(await h.editor.locator('.editor-crop').evaluate(el => el.style.width), cropWidth);
  await h.editor.locator('[data-crop-reset]').click(); await processed(h.page);
  assert.deepEqual(await imageSize(h.page), [640, 480]);
  await discard(h.page);
});

test('expansion alone does not mark the editor dirty and all sections reset on a new session', async t => {
  const h = await setup(t, { expand: false });
  await h.editor.locator('.editor-crop-section > summary').click();
  await h.editor.locator('.editor-close').click();
  assert.equal(await h.page.locator('.desk-dialog').count(), 0);
  await h.open();
  assert.deepEqual(await h.editor.locator('details').evaluateAll(nodes => nodes.map(node => node.open)), [false]);
});

for (const theme of ['light', 'dark']) for (const viewport of [{ width: 1920, height: 1080 }, { width: 390, height: 844 }]) test(`editor and published background share viewport geometry and keep the same PNG: ${theme} ${viewport.width}`, async t => {
  const h = await setup(t, { theme, viewport, publishing: true }); await load(h, await photo(h.page)); await processed(h.page);
  await h.editor.locator('#contourEditorName').fill('Полноэкранный контур');
  assert.equal(await h.editor.locator('#contourRepeat').isChecked(), false);
  const read = () => h.editor.locator('.editor-scheme > svg').evaluate(svg => {
    const picture = svg.querySelector('svg#editorContour');
    return { viewBox: svg.getAttribute('viewBox'), width: Number(picture.getAttribute('width')), height: Number(picture.getAttribute('height')),
      transforms: [...svg.querySelectorAll('use')].map(node => node.getAttribute('transform')) };
  });
  const normal = await read();
  assert.equal(normal.viewBox, `${-viewport.width / 2} ${-viewport.height / 2} ${viewport.width} ${viewport.height}`);
  assert.ok(Math.abs(Math.max(normal.width / viewport.width, normal.height / viewport.height) - .94) < 1e-9);
  await h.editor.locator('#contourScale').fill('150'); const zoom = await read();
  assert.ok(Math.abs(zoom.width / normal.width - 1.5) < 1e-9);
  assert.ok(zoom.width > viewport.width || zoom.height > viewport.height);
  await h.editor.locator('#contourScale').fill('50'); assert.ok(Math.abs((await read()).width / normal.width - .5) < 1e-9);
  await h.editor.locator('#contourRepeat').check(); await h.editor.locator('#contourRings').selectOption('2');
  await h.editor.locator('#contourRotation').fill('47'); await h.editor.locator('#contourDistance').fill('70'); await h.editor.locator('#contourScale').fill('135');
  const expected = await read(), url = await h.editor.locator('.editor-scheme image').getAttribute('href');
  const png = Buffer.from(await h.editor.locator('.editor-scheme image').evaluate(async node => [...new Uint8Array(await (await fetch(node.getAttribute('href'))).arrayBuffer())]));
  assert.equal(expected.transforms.length, 25);
  assert.ok(expected.transforms.every(value => value.endsWith('rotate(47)')));
  if (process.env.EDITOR_SCREENSHOT_DIR) {
    await h.editor.locator('.editor-scheme').scrollIntoViewIfNeeded();
    await h.editor.screenshot({ path: path.join(process.env.EDITOR_SCREENSHOT_DIR, `geometry-${theme}-${viewport.width}.png`) });
  }
  await h.editor.locator('[data-editor-save]').click(); await h.page.waitForFunction(() => !document.querySelector('#contourEditor'));
  await h.page.locator('.contour-card .contour-select').waitFor();
  assert.deepEqual(h.flags.posts[0].buffer, png);
  const actual = await h.page.evaluate(async item => {
    const module = await import('/static/background_contour_paint.js?v=20261029-appearance-compact-v1');
    await module.loadContours(true); await module.contourImage(item.id);
    const paint = module.paintContour({ type: item.id, scale: 100, intensity: 100, variable: false, seed: 1 }, document.body.dataset.theme === 'dark', { width: innerWidth, height: innerHeight });
    const text = decodeURIComponent(paint.image.slice(paint.image.indexOf(',') + 1, paint.image.lastIndexOf('"')));
    const root = new DOMParser().parseFromString(text, 'image/svg+xml'), picture = root.querySelector('svg#contour');
    return { viewBox: root.documentElement.getAttribute('viewBox'), width: Number(picture.getAttribute('width')), height: Number(picture.getAttribute('height')),
      transforms: [...root.querySelectorAll('use')].map(node => node.getAttribute('transform')) };
  }, h.flags.items[0]);
  assert.deepEqual(actual, expected, 'saving keeps the exact viewport layout, including the visible PNG bounds');
  assert.equal(await h.page.evaluate(url => editorMemory.revoked.includes(url), url), true);
  assert.equal(await h.page.locator('.contour-select').getAttribute('aria-pressed'), 'false');
});

test('viewport aspect ratio refreshes even when the editor preview has reached its CSS maximum height', async t => {
  const h = await setup(t, { viewport: { width: 1920, height: 1200 } }); await load(h, await photo(h.page)); await processed(h.page);
  const before = await h.editor.locator('.editor-scheme').boundingBox(), url = await h.editor.locator('.editor-scheme image').getAttribute('href');
  const jobs = await h.page.evaluate(() => workerState.jobs.length);
  await h.page.setViewportSize({ width: 1920, height: 1300 });
  await h.page.waitForFunction(() => document.querySelector('.editor-scheme > svg').getAttribute('viewBox') === '-960 -650 1920 1300');
  assert.ok(Math.abs((await h.editor.locator('.editor-scheme').boundingBox()).height - before.height) < 1);
  assert.equal(await h.editor.locator('.editor-scheme image').getAttribute('href'), url);
  assert.equal(await h.page.evaluate(() => workerState.jobs.length), jobs);
  await discard(h.page);
  await h.page.setViewportSize({ width: 1440, height: 1000 });
  assert.equal(await h.page.evaluate(() => editorMemory.live.size), 0);
});

test('repetitions chosen before photo processing fit once; manual settings override a pending fit', async t => {
  const h = await setup(t);
  await h.editor.locator('#contourRepeat').check(); await h.editor.locator('#contourRings').selectOption('2');
  await load(h, await photo(h.page)); await processed(h.page);
  assert.equal(await frames(h.page), 25);
  const fitted = await h.editor.locator('#contourScale').inputValue();
  await h.editor.locator('#contourThickness').fill('7'); await processed(h.page);
  assert.equal(await h.editor.locator('#contourScale').inputValue(), fitted);
  await discard(h.page); await h.open();
  await h.editor.locator('#contourRepeat').check(); await h.editor.locator('#contourScale').fill('137');
  await load(h, await photo(h.page)); await processed(h.page);
  assert.equal(await h.editor.locator('#contourScale').inputValue(), '137');
});

async function previewSizes(page) {
  return page.locator('.editor-photo-stage, .editor-scheme').evaluateAll(nodes => nodes.map(node => {
    const r = node.getBoundingClientRect();
    return { width: r.width, height: r.height, x: r.x, y: r.y, panelWidth: node.parentElement.clientWidth };
  }));
}
function assertPreviewRatio(sizes, viewport) {
  const ratio = viewport.width / viewport.height;
  assert.equal(sizes.length, 2);
  for (const size of sizes) {
    assert.ok(Math.abs(size.width - size.height * ratio) <= 1, JSON.stringify({ size, ratio }));
    assert.ok(size.width <= size.panelWidth);
    const cap = viewport.width <= 850 ? Math.min(viewport.height * .7, 600) : Math.min(viewport.height * .4, 420);
    assert.ok(size.height <= cap + 1, JSON.stringify({ size, cap }));
  }
  assert.ok(Math.abs(sizes[0].width - sizes[1].width) < .1);
  assert.ok(Math.abs(sizes[0].height - sizes[1].height) < .1);
  if (viewport.width > 850) assert.equal(sizes[0].y, sizes[1].y);
  else assert.ok(sizes[1].y > sizes[0].y + sizes[0].height);
}
async function containedPhoto(page) {
  return page.locator('.editor-photo-stage').evaluate(stage => {
    const image = stage.querySelector('img'), r = stage.getBoundingClientRect();
    const factor = Math.min(stage.clientWidth / image.naturalWidth, stage.clientHeight / image.naturalHeight);
    const width = image.naturalWidth * factor, height = image.naturalHeight * factor;
    const crop = stage.querySelector('.editor-crop').getBoundingClientRect();
    return { width, height, x: r.x + stage.clientLeft + (stage.clientWidth - width) / 2,
      y: r.y + stage.clientTop + (stage.clientHeight - height) / 2,
      crop: { width: crop.width, height: crop.height, x: crop.x, y: crop.y }, fit: getComputedStyle(image).objectFit };
  });
}
function assertCropBox(photo, { x = 0, y = 0, width = 100, height = 100 } = {}) {
  assert.equal(photo.fit, 'contain');
  assert.ok(Math.abs(photo.width / photo.height - 640 / 480) < 1e-9);
  for (const [actual, expected] of [[photo.crop.x, photo.x + photo.width * x / 100], [photo.crop.y, photo.y + photo.height * y / 100],
    [photo.crop.width, photo.width * width / 100], [photo.crop.height, photo.height * height / 100]]) assert.ok(Math.abs(actual - expected) < 1);
}
for (const theme of ['light', 'dark']) for (const viewport of [
  { width: 1920, height: 1080 }, { width: 1600, height: 1000 }, { width: 1379, height: 907 }, { width: 390, height: 844 }, { width: 844, height: 390 },
]) test(`actual preview DOM windows share the viewport ratio and contain the photo: ${theme} ${viewport.width}x${viewport.height}`, async t => {
  const h = await setup(t, { theme, viewport, touch: viewport.width === 390, expand: false });
  assertPreviewRatio(await previewSizes(h.page), viewport);
  await load(h, await photo(h.page)); await processed(h.page);
  assertPreviewRatio(await previewSizes(h.page), viewport);
  assertCropBox(await containedPhoto(h.page));
  const modal = await h.editor.boundingBox();
  assert.ok(modal.x >= 0 && modal.y >= 0 && modal.x + modal.width <= viewport.width && modal.y + modal.height <= viewport.height);
  assert.equal(await h.editor.locator('.modal-body').evaluate(el => el.scrollWidth <= el.clientWidth), true);
  if (process.env.EDITOR_SCREENSHOT_DIR) await h.editor.screenshot({ path: path.join(process.env.EDITOR_SCREENSHOT_DIR, `aspect-${theme}-${viewport.width}x${viewport.height}.png`) });
});
for (const theme of ['light', 'dark']) test(`resizing preserves processed PNG and numeric/drag crop coordinates: ${theme}`, async t => {
  const h = await setup(t, { theme, viewport: { width: 1920, height: 1080 }, expand: false });
  await load(h, await photo(h.page)); await processed(h.page);
  await h.editor.locator('.editor-crop-section > summary').click();
  for (const [key, value] of Object.entries({ x: 5, y: 5, width: 80, height: 90 })) await h.editor.locator(`[data-crop="${key}"]`).fill(String(value));
  await processed(h.page);
  const url = await h.editor.locator('.editor-scheme image').getAttribute('href'), jobs = await h.page.evaluate(() => workerState.jobs.length);
  for (const viewport of [{ width: 1600, height: 1000 }, { width: 1379, height: 907 }, { width: 390, height: 844 }, { width: 844, height: 390 }]) {
    await h.page.setViewportSize(viewport);
    await h.page.waitForFunction(viewport => {
      const stages = [...document.querySelectorAll('.editor-photo-stage, .editor-scheme')];
      return stages.every(node => { const r = node.getBoundingClientRect(); return Math.abs(r.width - r.height * viewport.width / viewport.height) <= 1; });
    }, viewport);
    assertPreviewRatio(await previewSizes(h.page), viewport);
    assertCropBox(await containedPhoto(h.page), { x: 5, y: 5, width: 80, height: 90 });
    assert.equal(await h.editor.locator('.editor-scheme image').getAttribute('href'), url);
    assert.equal(await h.page.evaluate(() => workerState.jobs.length), jobs);
  }
  await h.page.setViewportSize({ width: 390, height: 844 });
  await h.editor.locator('[data-crop-reset]').click(); await processed(h.page);
  if (await h.editor.locator('.editor-crop-section').evaluate(el => el.open)) await h.editor.locator('.editor-crop-section > summary').click();
  await h.editor.locator('.editor-photo-stage').scrollIntoViewIfNeeded();
  const content = await containedPhoto(h.page);
  await h.page.mouse.move(content.x + content.width * .15, content.y + content.height * .2); await h.page.mouse.down();
  await h.page.mouse.move(content.x + content.width * .75, content.y + content.height * .8); await h.page.mouse.up();
  for (const [key, expected] of Object.entries({ x: 15, y: 20, width: 60, height: 60 })) assert.ok(Math.abs(Number(await h.editor.locator(`[data-crop="${key}"]`).inputValue()) - expected) <= 1);
  assertCropBox(await containedPhoto(h.page), { x: 15, y: 20, width: 60, height: 60 });
  await discard(h.page);
});

for (const theme of ['light', 'dark']) test(`editor distance grows gaps without changing scale or PNG; fit respects new extents: ${theme}`, async t => {
  const h = await setup(t, { theme }); await load(h, await photo(h.page, 'image/png', '#7760a8', theme === 'light' ? 640 : 960, theme === 'light' ? 480 : 320)); await processed(h.page);
  const url = await h.editor.locator('.editor-scheme image').getAttribute('href'), jobs = await h.page.evaluate(() => workerState.jobs.length);
  await h.editor.locator('#contourRepeat').check();
  const read = () => h.editor.locator('.editor-scheme > svg').evaluate(svg => {
    const image = svg.querySelector('#editorContour');
    return { width: Number(image.getAttribute('width')), height: Number(image.getAttribute('height')),
      positions: [...svg.querySelectorAll('use')].map(node => node.getAttribute('transform').match(/translate\(([-\d.]+) ([-\d.]+)\)/).slice(1).map(Number)) };
  });
  for (const rings of [1, 2]) {
    await h.editor.locator('#contourRings').selectOption(String(rings));
    const side = rings * 2 + 1;
    for (const rotation of [0, 45, 90]) {
      await h.editor.locator('#contourRotation').fill(String(rotation)); await h.editor.locator('#contourScale').fill('137');
      await h.editor.locator('#contourDistance').fill('0'); const zero = await read();
      const angle = rotation * Math.PI / 180;
      const projectedWidth = zero.width * Math.abs(Math.cos(angle)) + zero.height * Math.abs(Math.sin(angle));
      const projectedHeight = zero.width * Math.abs(Math.sin(angle)) + zero.height * Math.abs(Math.cos(angle));
      assert.equal(zero.positions.length, side ** 2);
      for (const distance of [0, 100, 200]) {
        await h.editor.locator('#contourDistance').fill(String(distance)); const current = await read();
        assert.equal(await h.editor.locator('#contourScale').inputValue(), '137');
        assert.equal(current.width, zero.width); assert.equal(current.height, zero.height);
        assert.ok(Math.abs(current.positions[1][0] - current.positions[0][0] - projectedWidth * (1 + distance / 100)) < 1e-6);
        assert.ok(Math.abs(current.positions[side][1] - current.positions[0][1] - projectedHeight * (1 + distance / 100)) < 1e-6);
      }
    }
  }
  await h.editor.locator('[data-editor-fit]').click();
  const value = Number(await h.editor.locator('#contourScale').inputValue()), fitted = await read();
  assert.ok(value >= 50 && value <= 200);
  // The final angle is 90 degrees: rotated height and width swap.
  assert.ok(fitted.height + fitted.positions.at(-1)[0] * 2 <= 1440 * .94 + 1e-6);
  assert.ok(fitted.width + fitted.positions.at(-1)[1] * 2 <= 1000 * .94 + 1e-6);
  assert.equal(await h.editor.locator('.editor-scheme image').getAttribute('href'), url);
  assert.equal(await h.page.evaluate(() => workerState.jobs.length), jobs);
});

for (const theme of ['light', 'dark']) for (const [viewport, zoom] of [[{ width: 1920, height: 1080 }, 1], [{ width: 390, height: 844 }, 1], [{ width: 960, height: 600 }, 1.5], [{ width: 720, height: 450 }, 2]]) test(`dense editor inline hints and overlay crop stay usable without layout shifts: ${theme} ${viewport.width} zoom ${zoom}`, async t => {
  const h = await setup(t, { theme, viewport, expand: false, deviceScaleFactor: zoom });
  assert.deepEqual(await h.editor.locator('details').evaluateAll(nodes => nodes.map(node => node.open)), [false]);
  assert.equal(await h.page.evaluate(() => devicePixelRatio), zoom);
  const title = await h.editor.locator('h2').boundingBox(), hint = await h.editor.locator('#contourEditorAvailability').boundingBox();
  assert.ok(hint.x >= title.x + title.width + 4);
  assert.ok(hint.y < title.y + title.height && hint.y + hint.height > title.y);
  assert.equal(await h.editor.locator('label[for="contourEditorFile"] .editor-file-caption #contourEditorFileHint').count(), 1);
  assert.equal(await h.editor.locator('.modal-body > #contourEditorFileHint').count(), 0);
  await load(h, await photo(h.page)); await processed(h.page);
  assertPreviewRatio(await previewSizes(h.page), viewport);
  const before = await h.editor.locator('.editor-previews > section').first().evaluate(el => el.getBoundingClientRect().height);
  const settingsTop = await h.editor.locator('.editor-settings').evaluate(el => el.offsetTop), jobs = await h.page.evaluate(() => workerState.jobs.length);
  await openCrop(h);
  await h.page.waitForFunction(() => document.querySelector('.editor-crop-popup').style.top !== '');
  const overlay = await h.editor.locator('.editor-crop-popup').evaluate(el => {
    const r = el.getBoundingClientRect(), body = el.closest('.modal-body').getBoundingClientRect();
    const target = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
    return { top: r.top, bottom: r.bottom, bodyTop: body.top, bodyBottom: body.bottom, height: r.height, hit: el.contains(target), position: getComputedStyle(el).position };
  });
  assert.equal(overlay.position, 'absolute'); assert.equal(overlay.hit, true);
  assert.ok(overlay.height > 50 && overlay.top >= overlay.bodyTop && overlay.bottom <= overlay.bodyBottom + 1, JSON.stringify(overlay));
  assert.equal(await h.editor.locator('.editor-previews > section').first().evaluate(el => el.getBoundingClientRect().height), before);
  assert.equal(await h.editor.locator('.editor-settings').evaluate(el => el.offsetTop), settingsTop);
  assert.equal(await h.page.evaluate(() => workerState.jobs.length), jobs);
  await h.editor.locator('[data-crop="width"]').fill('80'); await processed(h.page);
  assert.deepEqual(await imageSize(h.page), [512, 480]);
  await h.editor.locator('[data-crop-reset]').click(); await processed(h.page);
  assert.deepEqual(await imageSize(h.page), [640, 480]);
  if (process.env.EDITOR_SCREENSHOT_DIR) await h.editor.screenshot({ path: path.join(process.env.EDITOR_SCREENSHOT_DIR, `dense-${theme}-${viewport.width}-${zoom}.png`) });
  await h.editor.locator('.editor-crop-section > summary').click();
  await h.editor.locator('#contourScale').fill('137');
  await discard(h.page);
  assert.equal(await h.page.evaluate(() => editorMemory.live.size), 0);
});

for (const theme of ['light', 'dark']) for (const viewport of [{ width: 1920, height: 1080 }, { width: 390, height: 844 }, { width: 720, height: 450 }]) test(`preview information rows stay symmetric with empty, loaded and long captions: ${theme} ${viewport.width}`, async t => {
  const h = await setup(t, { theme, viewport, expand: false });
  const symmetry = async () => {
    const cards = await h.editor.locator('.editor-previews > section').evaluateAll(nodes => nodes.map(node => {
      const r = node.getBoundingClientRect(); return { height: r.height, width: r.width };
    }));
    assert.ok(Math.abs(cards[0].height - cards[1].height) < .1, JSON.stringify(cards));
    assert.ok(Math.abs(cards[0].width - cards[1].width) < .1);
    assertPreviewRatio(await previewSizes(h.page), viewport);
    assert.equal(await h.editor.locator('.modal-body').evaluate(el => el.scrollWidth <= el.clientWidth), true);
    return cards[0].height;
  };
  const emptyHeight = await symmetry();
  if (process.env.EDITOR_SCREENSHOT_DIR) await h.editor.screenshot({ path: path.join(process.env.EDITOR_SCREENSHOT_DIR, `empty-${theme}-${viewport.width}.png`) });
  assert.equal(await h.editor.locator('.editor-scheme-count').isHidden(), true);
  assert.equal(await h.editor.locator('.editor-preview-info').last().locator('p:visible').count(), 1);
  assert.equal(await h.editor.locator('.editor-settings details, .editor-settings summary, .editor-settings [aria-expanded]').count(), 0);
  assert.deepEqual(await h.editor.locator('.editor-settings > section > h3').allTextContents(), ['Обработка', 'Композиция']);
  for (const selector of ['#contourSensitivity', '#contourDetail', '#contourThickness', '#contourInvert', '#contourCleanup', '#contourScale', '#contourRotation', '#contourRepeat', '#contourRings', '#contourDistance']) assert.equal(await h.editor.locator(selector).isVisible(), true);
  const file = await photo(h.page); await load(h, file); await processed(h.page);
  await symmetry(); assert.equal(await h.editor.locator('.editor-scheme-count').isVisible(), true);
  const rows = await h.editor.locator('.editor-preview-info').evaluateAll(nodes => nodes.map(node => {
    const first = node.querySelector('p'), last = node.lastElementChild, a = first.getBoundingClientRect(), b = last.getBoundingClientRect(), r = node.getBoundingClientRect();
    return { left: a.left - r.left, right: r.right - b.right, gap: b.left - a.right };
  }));
  for (const row of rows) { assert.ok(Math.abs(row.left) < 1); assert.ok(Math.abs(row.right) < 1); assert.ok(row.gap >= 7); }
  await load(h, { ...file, name: 'Очень_длинное_название_исходной_фотографии_'.repeat(5) + '.png' }); await processed(h.page);
  const longFileHeight = await symmetry(); assert.ok(longFileHeight >= emptyHeight);
  await h.editor.locator('.editor-processing-status').evaluate(el => { el.textContent += ' Дополнительная длинная информационная подпись для проверки переноса текста.'.repeat(8); });
  const longStatusHeight = await symmetry(); assert.ok(longStatusHeight > longFileHeight);
  await openCrop(h); await h.editor.locator('[data-crop="width"]').fill('80'); await processed(h.page);
  assert.deepEqual(await imageSize(h.page), [512, 480]); await symmetry();
  await h.editor.locator('.editor-crop-section > summary').click();
  await h.editor.locator('.editor-processing-section > h3').click();
  await h.editor.locator('.editor-composition-section > h3').click();
  assert.equal(await h.editor.locator('#contourSensitivity').isVisible(), true); assert.equal(await h.editor.locator('#contourScale').isVisible(), true);
  if (process.env.EDITOR_SCREENSHOT_DIR) {
    await h.editor.locator('.modal-body').evaluate(el => { el.scrollTop = 0; });
    await h.editor.screenshot({ path: path.join(process.env.EDITOR_SCREENSHOT_DIR, `symmetry-${theme}-${viewport.width}.png`) });
  }
  await discard(h.page);
});

for (const theme of ['light', 'dark']) for (const width of [1440, 390]) test(`composition rows and disabled repetition controls: ${theme} ${width}`, async t => {
  const h = await setup(t, { theme, viewport: { width, height: 1000 }, touch: width === 390 });
  const order = ['contourScale', 'contourRotation', 'fit', 'contourRepeat', 'contourRings', 'contourDistance'];
  const read = () => h.editor.locator('.editor-composition').evaluate(el => [...el.children].map(node => {
    const r = node.getBoundingClientRect();
    return { id: node.querySelector('input,select')?.id || 'fit', x: r.x, y: r.y, bottom: r.bottom };
  }));
  let boxes = await read(); assert.deepEqual(boxes.map(b => b.id), order);
  if (width > 600) {
    assert.ok(boxes[0].x < boxes[1].x && boxes[1].x < boxes[2].x);
    assert.ok(boxes[3].x < boxes[4].x && boxes[4].x < boxes[5].x);
    assert.ok(Math.min(...boxes.slice(3).map(b => b.y)) >= Math.max(...boxes.slice(0, 3).map(b => b.bottom)));
  } else for (let i = 1; i < boxes.length; i++) assert.ok(boxes[i].y >= boxes[i - 1].bottom);
  for (const id of ['contourRings', 'contourDistance']) {
    assert.equal(await h.editor.locator(`#${id}`).isDisabled(), true);
    assert.ok(Number(await h.editor.locator(`#${id}`).evaluate(el => getComputedStyle(el.closest('label')).opacity)) < .6);
  }
  await load(h, await photo(h.page)); await processed(h.page);
  await h.editor.locator('#contourRepeat').check();
  for (const id of ['contourRings', 'contourDistance']) {
    assert.equal(await h.editor.locator(`#${id}`).isEnabled(), true);
    assert.equal(await h.editor.locator(`#${id}`).evaluate(el => getComputedStyle(el.closest('label')).opacity), '1');
  }
  assert.equal(await frames(h.page), 9);
  await h.editor.locator('#contourDistance').fill('123'); await h.editor.locator('#contourScale').fill('175');
  await h.editor.locator('#contourRepeat').uncheck();
  assert.equal(await frames(h.page), 1);
  assert.equal(await h.editor.locator('#contourDistance').isDisabled(), true);
  assert.equal(await h.editor.locator('#contourDistance').inputValue(), '123');
  assert.equal(await h.editor.locator('#contourScale').inputValue(), '175');
  await h.editor.locator('#contourRepeat').check();
  assert.equal(await frames(h.page), 9);
  assert.equal(await h.editor.locator('#contourDistance').inputValue(), '123');
  assert.notEqual(await h.editor.locator('#contourScale').inputValue(), '175');
});
