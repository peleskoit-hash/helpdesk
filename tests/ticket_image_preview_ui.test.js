const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { gzipSync } = require('node:zlib');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const original = fs.readFileSync('api/app/static/icons/image-open-original.png');
const pdf = Buffer.from('%PDF-original file');
const stickers = {
  webp: { mime: 'image/webp', bytes: fs.readFileSync('api/app/static/icons/web-source.webp') },
  webm: { mime: 'video/webm', bytes: Buffer.from([0x1a, 0x45, 0xdf, 0xa3, 0x80]) },
  tgs: { mime: 'application/x-tgsticker', bytes: gzipSync('{"v":"5.5.2","layers":[]}') },
};
let browser, server, origin;
test.before(async () => {
  server = http.createServer((request, response) => {
    const params = new URL(request.url, 'http://localhost').searchParams;
    const file = params.has('file');
    const sticker = params.get('sticker');
    const resource = stickers[sticker];
    response.writeHead(200, {
      'Content-Type': resource?.mime || (file ? (params.get('file') === 'generic' ? 'application/octet-stream' : 'application/pdf') : 'image/png'),
      'Content-Disposition': resource ? `attachment; filename="custom.${sticker}"` : `attachment; filename="original.${file ? 'pdf' : 'png'}"`,
    });
    response.end(resource?.bytes || (file ? pdf : original));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ channel: 'msedge', headless: true });
});
test.after(async () => { await browser?.close(); await new Promise(resolve => server.close(resolve)); });

async function setup(t, theme, width, kind, { showPreview = true, originalReady } = {}) {
  const context = await browser.newContext({ viewport: { width, height: 900 }, acceptDownloads: true });
  t.after(() => context.close());
  await context.addCookies([{ name: 'desk_session', value: 'authorized', domain: '127.0.0.1', path: '/' }]);
  const requests = [], errors = [];
  const endpoint = `/api/v1/tickets/${kind === 'task' ? 531 : 530}/attachments/original/download`;
  await context.route(`${origin}/**`, async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.pathname.startsWith('/static/')) return route.fulfill({ body: fs.readFileSync(path.resolve('api/app', url.pathname.slice(1))),
      contentType: { '.js': 'text/javascript', '.css': 'text/css', '.png': 'image/png', '.jpg': 'image/jpeg' }[path.extname(url.pathname)] || 'application/octet-stream' });
    if (url.pathname === endpoint) {
      requests.push(request.headers());
      assert.match(request.headers().cookie, /desk_session=authorized/);
      if (originalReady) await originalReady;
      return route.continue();
    }
    return route.fulfill({ contentType: 'text/html', body: `<html><head><meta charset="utf-8"><link rel="stylesheet" href="/static/styles.css?v=20261106-ticket-table-v2"></head><body data-theme="${theme}"><div id="attachments"></div></body></html>` });
  });
  const page = await context.newPage(); page.on('pageerror', e => errors.push(e.message));
  await page.goto(`${origin}/tickets/${kind === 'task' ? 531 : 530}`);
  await page.addScriptTag({ url: '/static/ticket_api.js' });
  await page.addScriptTag({ url: '/static/ticket_attachments.js?v=20261117-ticket-attachment-source-v1' });
  await page.evaluate(({ endpoint, showPreview }) => {
    window.ctx = { el: {}, showError: message => { throw Error(message); }, setStatus: value => { window.statusText = value; },
      closeOnBackdropClick: (modal, close) => modal.addEventListener('click', event => { if (event.target === modal) close(); }) };
    window.attachment = { id: 'original', original_filename: 'original.png', mime_type: 'image/png', is_image: true, download_url: endpoint, thumbnail_url: '/thumbnail.png', file_size: 1024 };
    if (showPreview) TicketAttachments.renderAttachments(ctx, { attachments: [attachment] }, document.querySelector('#attachments'));
  }, { endpoint, showPreview });
  if (showPreview) {
    await page.locator('.attachment-image-preview').evaluate(img => img.decode());
    await page.locator('.attachment-block').click();
    await page.locator('.image-preview-modal:not(.hidden)').waitFor();
  }
  t.after(() => assert.deepEqual(errors, []));
  return { page, context, requests, endpoint };
}

for (const theme of ['light', 'dark']) for (const width of [1440, 390]) for (const kind of ['ticket', 'task']) test(`original image opens in a separate tab and download/close stay intact: ${kind} ${theme} ${width}`, async t => {
  const { page, requests } = await setup(t, theme, width, kind);
  const open = page.getByRole('link', { name: 'Открыть в новой вкладке' }), download = page.getByRole('button', { name: 'Скачать', exact: true }), close = page.locator('.image-preview-close');
  assert.deepEqual(await page.locator('.image-preview-container > a, .image-preview-container > button').evaluateAll(nodes => nodes.map(n => n.className)), ['image-preview-open', 'image-preview-download', 'image-preview-close']);
  const boxes = await Promise.all([open, download, close].map(el => el.boundingBox()));
  assert.ok(boxes[0].x < boxes[1].x && boxes[1].x < boxes[2].x);
  assert.deepEqual(boxes.map(r => [r.y, r.width, r.height]), boxes.map(() => [boxes[0].y, 34, 34]));
  for (const button of [open, download]) {
    const centred = await button.evaluate(el => { const b = el.getBoundingClientRect(), i = el.querySelector('svg').getBoundingClientRect(); return [i.x + i.width / 2 - b.x - b.width / 2, i.y + i.height / 2 - b.y - b.height / 2]; });
    assert.ok(centred.every(n => Math.abs(n) < .1));
  }
  for (const button of [open, download]) {
    const ink = await button.evaluate(async el => {
      const svg = el.querySelector('svg').cloneNode(true), image = svg.querySelector('image');
      svg.setAttribute('xmlns', 'http://www.w3.org/2000/svg'); svg.setAttribute('width', '32'); svg.setAttribute('height', '32');
      svg.prepend(document.querySelector('#attachmentIconInk').parentNode.cloneNode(true));
      const blob = await (await fetch(image.getAttribute('href'))).blob();
      image.setAttribute('href', await new Promise(resolve => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.readAsDataURL(blob); }));
      const bitmap = new Image(); bitmap.src = 'data:image/svg+xml,' + encodeURIComponent(new XMLSerializer().serializeToString(svg)); await bitmap.decode();
      const canvas = document.createElement('canvas'); canvas.width = canvas.height = 32;
      const ctx = canvas.getContext('2d'); ctx.drawImage(bitmap, 0, 0);
      const pixels = ctx.getImageData(0, 0, 32, 32).data; let count = 0;
      for (let p = 0; p < pixels.length; p += 4) if (pixels[p + 3] > 127) { if (pixels[p] < 250 || pixels[p + 1] < 250 || pixels[p + 2] < 250) throw Error('Icon ink is not white'); count++; }
      return count / 1024;
    });
    assert.ok(ink > .1 && ink < .8, `provided icon has visible ink and a transparent background: ${ink}`);
  }
  assert.equal(await open.locator('image').getAttribute('href'), '/static/icons/image-open-original.png');
  assert.equal(await download.locator('image').getAttribute('href'), '/static/icons/image-download.jpg');
  const source = await page.locator('.image-preview-img').getAttribute('src');
  assert.equal(await open.getAttribute('href'), source); assert.match(source, /^blob:/);
  const popupPromise = page.waitForEvent('popup'); await open.click(); const popup = await popupPromise;
  await popup.waitForLoadState();
  assert.equal(popup.url(), source);
  await popup.waitForFunction(() => document.querySelector('img')?.complete);
  assert.deepEqual(await popup.locator('img').evaluate(img => [img.naturalWidth, img.naturalHeight]), [512, 512]);
  assert.equal(await popup.evaluate(() => window.opener), null);
  assert.deepEqual(await popup.evaluate(async () => [...new Uint8Array(await (await fetch(location.href)).arrayBuffer())]), [...original]);
  assert.equal(await page.locator('.image-preview-modal').isVisible(), true);
  assert.equal(requests.length, 1, 'opening uses the authenticated original already fetched by the preview');
  const downloadPromise = page.waitForEvent('download'); await download.click(); const saved = await downloadPromise;
  assert.equal(saved.suggestedFilename(), 'original.png');
  assert.deepEqual(fs.readFileSync(await saved.path()), original);
  assert.equal(await page.locator('.image-preview-modal').isVisible(), true);
  assert.equal(await page.evaluate(() => statusText), 'Скачивание начато...');
  await page.locator('.image-preview-img').hover(); await page.mouse.wheel(0, -20);
  await page.waitForFunction(() => document.querySelector('.image-preview-img').style.transform !== 'scale(1)');
  if (process.env.IMAGE_PREVIEW_SCREENSHOTS) await page.locator('.image-preview-dialog').screenshot({ path: path.join(process.env.IMAGE_PREVIEW_SCREENSHOTS, `${kind}-${theme}-${width}.png`) });
  await close.click(); assert.equal(await page.locator('.image-preview-modal').isVisible(), false);
  assert.equal(await page.locator('.image-preview-open').getAttribute('href'), null);
  assert.equal(await popup.locator('img').evaluate(img => img.naturalWidth), 512);
  await page.locator('.attachment-block').click();
  await page.locator('.image-preview-modal').click({ position: { x: 2, y: 2 } });
  assert.equal(await page.locator('.image-preview-modal').isVisible(), false);
});

async function setupAttachmentMenu(t, originalReady) {
  const { page, endpoint, requests } = await setup(t, 'light', 1440, 'ticket', { showPreview: false, originalReady });
  await page.addScriptTag({ url: '/static/ticket_message_view.js' });
  await page.evaluate(() => {
    const bubble = document.querySelector('#attachments');
    bubble.textContent = 'Photo message';
    const menu = document.createElement('div');
    menu.className = 'message-context-menu hidden';
    document.body.appendChild(menu);
    Object.assign(ctx, {
      state: { role: 'agent' }, quoteMessage() {}, showError: message => { window.errorText = message; },
      stickerType: TicketAttachments.stickerType,
      isPhotoAttachment: TicketAttachments.isPhotoAttachment,
      isPdfAttachment: TicketAttachments.isPdfAttachment,
      downloadAttachment: attachment => TicketAttachments.downloadAttachment(ctx, attachment),
      copyAttachmentImage: (attachment, kind) => TicketAttachments.copyAttachmentImage(ctx, attachment, kind),
      openAttachmentInTab: (attachment, kind) => TicketAttachments.openAttachmentInTab(ctx, attachment, kind),
    });
    ctx.el.messageContextMenu = menu;
    bubble.addEventListener('contextmenu', event => TicketMessageView.showMessageContextMenu(ctx, event, {
      message_type: 'client', attachments: [attachment],
    }));
  });
  async function action(label) {
    await page.locator('#attachments').click({ button: 'right' });
    await page.locator('.message-context-menu').getByRole('button', { name: label, exact: true }).click();
  }
  return { page, endpoint, requests, action };
}

test('attachment menu opens the original after loading and downloads photos and files with their names', async t => {
  let release;
  const loading = new Promise(resolve => { release = resolve; });
  const { page, endpoint, requests, action } = await setupAttachmentMenu(t, loading);
  const popupPromise = page.waitForEvent('popup', { timeout: 5000 });
  try {
    await action('Открыть фото в новом окне');
    const popup = await popupPromise;
    assert.equal(popup.url(), 'about:blank');
    release();
    await popup.waitForURL(/^blob:/);
    assert.equal(await popup.evaluate(() => window.opener), null);
    assert.deepEqual(Buffer.from(await popup.evaluate(async () =>
      [...new Uint8Array(await (await fetch(location.href)).arrayBuffer())])), original);
    assert.equal(requests.length, 1);
    await popup.close();
  } finally {
    release();
  }
  const downloadPhoto = page.waitForEvent('download');
  await action('Скачать фото');
  const savedPhoto = await downloadPhoto;
  assert.equal(savedPhoto.suggestedFilename(), 'original.png');
  assert.deepEqual(fs.readFileSync(await savedPhoto.path()), original);

  const filePath = `${endpoint}?file=1`;
  await page.evaluate(filePath => Object.assign(attachment, {
    id: 'file', original_filename: 'original.pdf', mime_type: 'application/pdf', is_image: false, download_url: filePath,
  }), filePath);
  const downloadFile = page.waitForEvent('download');
  await action('Скачать файл');
  const savedFile = await downloadFile;
  assert.equal(savedFile.suggestedFilename(), 'original.pdf');
  assert.deepEqual(fs.readFileSync(await savedFile.path()), pdf);
});

for (const [mode, mime] of [['1', 'application/pdf'], ['generic', 'application/octet-stream']]) test(`PDF opens in a new tab from the menu and downloads on a left click: ${mime}`, async t => {
  const { page, endpoint, requests, action } = await setupAttachmentMenu(t);
  await page.evaluate(({ url, mime }) => {
    Object.assign(attachment, { download_url: url, mime_type: mime, original_filename: 'original.pdf', is_image: false });
    TicketAttachments.renderAttachments(ctx, { attachments: [attachment] }, document.querySelector('#attachments'));
  }, { url: `${endpoint}?file=${mode}`, mime });
  const saved = page.waitForEvent('download');
  await page.locator('.attachment-file').click();
  const download = await saved;
  assert.equal(download.suggestedFilename(), 'original.pdf');
  assert.deepEqual(fs.readFileSync(await download.path()), pdf);
  const popup = page.waitForEvent('popup');
  await action('Открыть PDF в новой вкладке');
  const tab = await popup;
  await tab.waitForURL(/^blob:/);
  assert.ok(requests.some(headers => headers.accept === 'application/json'));
  assert.deepEqual(Buffer.from(await page.evaluate(async url =>
    [...new Uint8Array(await (await fetch(url)).arrayBuffer())], tab.url())), pdf);
  await tab.close();
});

for (const [format, { mime, bytes }] of Object.entries(stickers)) test(`sticker ignores a left click and uses its original in the menu: ${format}`, async t => {
  const { page, endpoint, action } = await setupAttachmentMenu(t);
  await page.evaluate(({ url, mime, format }) => {
    Object.assign(attachment, { source: 'sticker', download_url: url, mime_type: mime,
      original_filename: `custom.${format}`, is_image: format === 'webp' });
    window.pako = { ungzip: () => '{"layers":[]}' };
    window.lottie = { loadAnimation({ container }) {
      container.append(document.createElement('svg'));
      return { destroy() {} };
    } };
    window.leftClickDownload = false;
    document.addEventListener('click', event => {
      if (event.target.closest('a[download]')) window.leftClickDownload = true;
    });
    TicketAttachments.renderAttachments(ctx, { attachments: [attachment] }, document.querySelector('#attachments'));
  }, { url: `${endpoint}?sticker=${format}`, mime, format });
  const sticker = page.locator('.attachment-sticker');
  await sticker.click();
  assert.equal(await page.evaluate(() => window.leftClickDownload), false);
  assert.equal(await page.locator('.image-preview-modal').count(), 0);
  const saved = page.waitForEvent('download');
  await action('Скачать стикер');
  const download = await saved;
  assert.equal(download.suggestedFilename(), `custom.${format}`);
  assert.deepEqual(fs.readFileSync(await download.path()), bytes);
  if (format !== 'tgs') {
    const popup = page.waitForEvent('popup');
    await action('Открыть в новой вкладке');
    const tab = await popup;
    await tab.waitForURL(/^blob:/);
    assert.deepEqual(Buffer.from(await page.evaluate(async url =>
      [...new Uint8Array(await (await fetch(url)).arrayBuffer())], tab.url())), bytes);
    await tab.close();
  }
  if (format === 'webp') {
    await page.evaluate(() => {
      const bubble = document.querySelector('#attachments');
      bubble.replaceChildren();
      attachment.source = 'photo';
      attachment.original_filename = 'sticker.webp';
      TicketAttachments.renderAttachments(ctx, { attachments: [attachment] }, bubble);
    });
    assert.equal(await page.locator('.attachment-sticker').count(), 0);
    await page.locator('.attachment-image').click();
    await page.locator('.image-preview-modal:not(.hidden)').waitFor();
  }
  if (format === 'webm') {
    await page.evaluate(() => {
      const bubble = document.querySelector('#attachments');
      bubble.replaceChildren();
      attachment.source = 'video';
      TicketAttachments.renderAttachments(ctx, { attachments: [attachment] }, bubble);
    });
    assert.equal(await page.locator('.attachment-sticker').count(), 0);
    const savedVideo = page.waitForEvent('download');
    await page.locator('.attachment-file').click();
    assert.deepEqual(fs.readFileSync(await (await savedVideo).path()), bytes);
  }
});

for (const variant of ['png', 'jpeg', 'webp', 'sticker']) test(`attachment menu copies original pixels to a PNG: ${variant}`, async t => {
  const { page, endpoint, action } = await setupAttachmentMenu(t);
  const format = variant === 'sticker' ? 'webp' : variant;
  const mime = `image/${format}`;
  const source = await page.evaluate(async mime => {
    const canvas = document.createElement('canvas');
    canvas.width = 17;
    canvas.height = 11;
    const context = canvas.getContext('2d');
    context.fillStyle = '#de4a32';
    context.fillRect(0, 0, 17, 11);
    context.fillStyle = '#2f59c2';
    context.fillRect(4, 2, 9, 6);
    const img = await new Promise(resolve => canvas.toBlob(resolve, mime));
    window.readPixels = async blob => {
      const bitmap = await createImageBitmap(blob);
      const { width, height } = bitmap;
      const decoded = document.createElement('canvas');
      Object.assign(decoded, { width, height });
      const ctx = decoded.getContext('2d');
      ctx.drawImage(bitmap, 0, 0);
      bitmap.close();
      return { width, height, pixels: [...ctx.getImageData(0, 0, width, height).data] };
    };
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: {
      async write([item]) {
        const blob = await item.getType('image/png');
        window.copiedPhoto = { type: blob.type, ...(await readPixels(blob)) };
      },
    } });
    return { type: img.type, bytes: [...new Uint8Array(await img.arrayBuffer())], decoded: await readPixels(img) };
  }, mime);
  assert.equal(source.type, mime);
  await page.route(`${origin}${endpoint}`, route => {
    assert.match(route.request().headers().cookie, /desk_session=authorized/);
    return route.fulfill({ contentType: mime, body: Buffer.from(source.bytes) });
  });
  const sourceType = variant === 'sticker' ? 'sticker' : 'photo';
  await page.evaluate(({ mime, format, sourceType }) => Object.assign(attachment, {
    source: sourceType, mime_type: mime, original_filename: `original.${format}`,
  }), { mime, format, sourceType });
  await action(variant === 'sticker' ? 'Скопировать стикер в буфер' : 'Скопировать фото в буфер');
  await page.waitForFunction(() => window.statusText);
  assert.deepEqual(await page.evaluate(() => window.copiedPhoto), { type: 'image/png', ...source.decoded });
  assert.equal(await page.evaluate(() => window.errorText), undefined);
});

test('attachment menu reports clipboard, loading and popup failures', async t => {
  const { page, endpoint, action } = await setupAttachmentMenu(t);
  async function checkError(label, expected) {
    await page.evaluate(() => { window.errorText = ''; window.statusText = ''; });
    await action(label);
    await page.waitForFunction(() => window.errorText);
    const result = await page.evaluate(() => ({ error: window.errorText, status: window.statusText }));
    assert.match(result.error, expected);
    assert.equal(result.status, '');
  }
  const copy = 'Скопировать фото в буфер';
  const open = 'Открыть фото в новом окне';
  await page.evaluate(() => {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: {} });
    window.open = () => null;
  });
  await checkError(copy, /Браузер не поддерживает копирование фото/);
  await checkError(open, /Разрешите всплывающие окна/);
  await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', { configurable: true, value: {
    async write([item]) {
      await item.getType('image/png');
      throw new DOMException('Denied', 'NotAllowedError');
    },
  } }));
  await checkError(copy, /Проверьте разрешение браузера/);
  await page.route(`${origin}${endpoint}?missing=1`, route =>
    route.fulfill({ status: 403, json: { detail: { error: { message: 'Нет доступа к вложению' } } } }));
  await page.evaluate(() => {
    Object.assign(attachment, { id: 'missing', download_url: `${attachment.download_url}?missing=1` });
    window.open = () => ({ close() { window.failedTabClosed = true; } });
  });
  await checkError(copy, /Не удалось подготовить фото для копирования: Нет доступа к вложению/);
  await checkError(open, /Не удалось открыть фото: Нет доступа к вложению/);
  assert.equal(await page.evaluate(() => window.failedTabClosed), true);
});
