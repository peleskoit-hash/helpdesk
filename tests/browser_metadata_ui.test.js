const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const templates = path.resolve('api/app/templates');
const iconPath = '/static/chert.svg?v=20261028-favicon-v1';
const html = name => fs.readFileSync(path.join(templates, name), 'utf8');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });

test('all HTML pages expose the same versioned SVG favicon and retain page-specific titles', async t => {
  const page = await browser.newPage(); t.after(() => page.close());
  for (const file of fs.readdirSync(templates).filter(name => name.endsWith('.html'))) {
    // Metadata smoke is independent of application API requests and page scripts.
    await page.setContent(html(file).replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, ''));
    const links = page.locator('head link[rel="icon"]');
    assert.equal(await links.count(), 1, file);
    assert.equal(await links.getAttribute('href'), iconPath, file);
    assert.equal(await links.getAttribute('type'), 'image/svg+xml');
    assert.ok(!(await page.title()).includes('Helpdesk MVP'), file);
    assert.ok(!(await page.title()).includes('ES Helpdesk'), file);
    assert.ok(!(await page.title()).includes('ES DESK+'), file);
  }
  await page.setContent(html('login.html').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, ''));
  assert.equal(await page.locator('#loginTitle').textContent(), 'ES DESK');
  const titles = { 'index.html': 'ES DESK', 'login.html': 'ES DESK', 'tickets.html': 'ES DESK - Список заявок',
    'ticket.html': 'ES DESK - Карточка заявки', 'statuses.html': 'ES DESK - Статусы сотрудников', 'profile.html': 'ES DESK - Личные данные' };
  for (const [file, title] of Object.entries(titles)) {
    await page.setContent(html(file).replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, ''));
    assert.equal(await page.title(), title);
  }
});

test('AppShell uses updated template titles on navigation without replacing the favicon', async t => {
  const page = await browser.newPage(); t.after(() => page.close());
  await page.setContent(html('tickets.html').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, ''));
  await page.addScriptTag({ path: path.resolve('api/app/static/app_shell.js') });
  for (const file of ['ticket.html', 'statuses.html', 'profile.html']) {
    const title = await page.evaluate(source => {
      const payload = HelpdeskAppShell.extractShellPayload(source);
      if (!HelpdeskAppShell.mountShellPayload(payload)) throw new Error('Mount failed');
      return document.title;
    }, html(file));
    assert.equal(title, /<title>(.*?)<\/title>/.exec(html(file))[1]);
    assert.equal(await page.locator('link[rel="icon"]').getAttribute('href'), iconPath);
  }
});

test('favicon loads as transparent square SVG and remains visible at 16 and 32 pixels', async t => {
  const page = await browser.newPage(); t.after(() => page.close());
  await page.route('https://desk.test/**', route => {
    const url = new URL(route.request().url());
    return url.pathname === '/static/chert.svg'
      ? route.fulfill({ contentType: 'image/svg+xml', body: fs.readFileSync('api/app/static/chert.svg') })
      : route.fulfill({ contentType: 'text/html', body: html('login.html').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '') });
  });
  await page.goto('https://desk.test/login');
  // A routed browser fetch checks the actual favicon URL, independent of native icon-cache behavior.
  const result = await page.evaluate(async () => {
    const href = document.querySelector('link[rel="icon"]').href;
    const response = await fetch(href);
    const source = await response.text();
    const doc = new DOMParser().parseFromString(source, 'image/svg+xml');
    const viewBox = doc.documentElement.getAttribute('viewBox').split(/\s+/).map(Number);
    const image = new Image(); image.src = href; await image.decode();
    const sizes = [16,32].map(size => {
      const canvas = document.createElement('canvas'); canvas.width = canvas.height = size;
      const ctx = canvas.getContext('2d'); ctx.drawImage(image,0,0,size,size);
      const data = ctx.getImageData(0,0,size,size).data;
      let ink=0, warm=0, minY=size, maxY=-1;
      for (let y=0;y<size;y++) for(let x=0;x<size;x++) {
        const i=(y*size+x)*4;
        if (data[i+3]>40) { ink++;minY=Math.min(minY,y);maxY=Math.max(maxY,y); }
        if(data[i+3]>80 && data[i]>data[i+2]*1.2 && data[i]>150) warm++;
      }
      return { size, ink, warm, height:maxY-minY+1, corner:data[3] };
    });
    return { status:response.status, mime:response.headers.get('content-type'), viewBox, sizes };
  });
  assert.equal(result.status,200); assert.match(result.mime,/image\/svg\+xml/);
  assert.equal(result.viewBox[2],result.viewBox[3]);
  for (const sample of result.sizes) {
    assert.equal(sample.corner,0);
    assert.ok(sample.height>=sample.size-2);
    assert.ok(sample.ink>sample.size*sample.size*.2 && sample.warm>5);
  }
});
