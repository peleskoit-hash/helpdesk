const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const original = fs.readFileSync('api/app/static/icons/image-open-original.png');
let browser, server, origin;
test.before(async () => {
  server = http.createServer((request, response) => { response.writeHead(200, { 'Content-Type': 'image/png', 'Content-Disposition': 'attachment; filename="original.png"' }); response.end(original); });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ channel: 'msedge', headless: true });
});
test.after(async () => { await browser?.close(); await new Promise(resolve => server.close(resolve)); });

async function setup(t, theme, width, kind) {
  const context = await browser.newContext({ viewport: { width, height: 900 }, acceptDownloads: true });
  t.after(() => context.close());
  await context.addCookies([{ name: 'desk_session', value: 'authorized', domain: '127.0.0.1', path: '/' }]);
  const requests = [], errors = [];
  const endpoint = `/api/v1/tickets/${kind === 'task' ? 531 : 530}/attachments/original/download`;
  await context.route(`${origin}/**`, route => {
    const request = route.request(), url = new URL(request.url());
    if (url.pathname.startsWith('/static/')) return route.fulfill({ body: fs.readFileSync(path.resolve('api/app', url.pathname.slice(1))),
      contentType: { '.js': 'text/javascript', '.css': 'text/css', '.png': 'image/png', '.jpg': 'image/jpeg' }[path.extname(url.pathname)] || 'application/octet-stream' });
    if (url.pathname === endpoint) {
      requests.push(request.headers());
      assert.match(request.headers().cookie, /desk_session=authorized/);
      return route.continue();
    }
    return route.fulfill({ contentType: 'text/html', body: `<html><head><meta charset="utf-8"><link rel="stylesheet" href="/static/styles.css?v=20261029-image-actions-v1"></head><body data-theme="${theme}"><div id="attachments"></div></body></html>` });
  });
  const page = await context.newPage(); page.on('pageerror', e => errors.push(e.message));
  await page.goto(`${origin}/tickets/${kind === 'task' ? 531 : 530}`);
  await page.addScriptTag({ url: '/static/ticket_api.js' });
  await page.addScriptTag({ url: '/static/ticket_attachments.js?v=20261031-web-history-v1' });
  await page.evaluate(endpoint => {
    window.ctx = { el: {}, showError: message => { throw Error(message); }, setStatus: value => { window.statusText = value; },
      closeOnBackdropClick: (modal, close) => modal.addEventListener('click', event => { if (event.target === modal) close(); }) };
    TicketAttachments.renderAttachments(ctx, { attachments: [{ id: 'original', original_filename: 'original.png', mime_type: 'image/png', is_image: true, download_url: endpoint, thumbnail_url: '/thumbnail.png', file_size: 1024 }] }, document.querySelector('#attachments'));
  }, endpoint);
  await page.locator('.attachment-image-preview').evaluate(img => img.decode());
  await page.locator('.attachment-block').click();
  await page.locator('.image-preview-modal:not(.hidden)').waitFor();
  t.after(() => assert.deepEqual(errors, []));
  return { page, context, requests };
}

for (const theme of ['light', 'dark']) for (const width of [1440, 390]) for (const kind of ['ticket', 'task']) test(`original image opens in a separate tab and download/close stay intact: ${kind} ${theme} ${width}`, async t => {
  const { page, requests } = await setup(t, theme, width, kind);
  const open = page.getByRole('link', { name: 'Открыть в новой вкладке' }), download = page.getByRole('button', { name: 'Скачать', exact: true }), close = page.locator('.image-preview-close');
  assert.deepEqual(await page.locator('.image-preview-dialog > a, .image-preview-dialog > button').evaluateAll(nodes => nodes.map(n => n.className)), ['image-preview-open', 'image-preview-download', 'image-preview-close']);
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
