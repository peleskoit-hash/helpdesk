const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });
for (const theme of ['light', 'dark']) for (const workflow of ['ticket', 'task']) test(`composer comment surface: ${workflow} ${theme}`, async t => {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  t.after(() => page.close());
  const errors = [], assets = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('http://desk.test/**', route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith('/static/')) {
      assets.push(url.pathname + url.search);
      const file = path.resolve('api/app', url.pathname.slice(1));
      return route.fulfill({ body: fs.readFileSync(file), contentType: { '.css': 'text/css', '.js': 'text/javascript', '.svg': 'image/svg+xml', '.ttf': 'font/ttf' }[path.extname(file)] || 'application/octet-stream' });
    }
    const html = fs.readFileSync('api/app/templates/ticket.html', 'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '');
    return route.fulfill({ contentType: 'text/html', body: html });
  });
  await page.goto('http://desk.test/tickets/example');
  await page.locator('body').evaluate((body, theme) => { body.dataset.theme = theme; body.dataset.sidebarReady = '1'; }, theme);
  await page.addScriptTag({ url: '/static/ticket_composer.js' });
  await page.evaluate(workflow => {
    document.querySelector('#messages').innerHTML = '<div class="message internal">Сохранённый комментарий</div>';
    const ids = { messageInput: 'ticketMessageBody', modeReplyBtn: 'modeReplyBtn', modeCommentBtn: 'modeCommentBtn', sendNowBtn: 'sendNowBtn', addFilesBtn: 'addFilesBtn', replyFilesInput: 'replyFilesInput', clearQuoteBtn: 'clearQuoteBtn' };
    const ctx = { state: { composeMode: 'reply', card: { workflow_type: workflow, status: 'active' }, selectedFiles: [] }, el: Object.fromEntries(Object.entries(ids).map(([key, id]) => [key, document.getElementById(id)])) };
    TicketComposer.bindComposerHandlers(ctx);
    TicketComposer.applyComposeModeUi(ctx);
  }, workflow);
  const input = page.locator('#ticketMessageBody');
  const snapshot = () => page.evaluate(() => {
    const el = document.querySelector('#ticketMessageBody'), css = getComputedStyle(el);
    const rect = node => { const r = node.getBoundingClientRect(); return [r.x, r.y, r.width, r.height]; };
    return { background: css.backgroundColor, color: css.color, placeholder: getComputedStyle(el, '::placeholder').color,
      border: css.borderColor, borderWidth: css.borderWidth, radius: css.borderRadius,
      geometry: ['#ticketMessageBody', '#modeReplyBtn', '#modeCommentBtn', '#sendNowBtn'].map(selector => rect(document.querySelector(selector))),
      comment: getComputedStyle(document.querySelector('.message.internal')).backgroundColor,
      commentBorder: getComputedStyle(document.querySelector('.message.internal')).borderColor };
  });
  const reply = await snapshot();
  await input.fill('Текст черновика');
  await page.locator('#modeCommentBtn').click();
  const comment = await snapshot();
  assert.equal(comment.background, comment.comment);
  assert.notEqual(comment.background, reply.background);
  assert.equal(comment.border, comment.commentBorder);
  assert.equal(comment.color, reply.color);
  assert.equal(comment.placeholder, reply.placeholder);
  assert.equal(comment.borderWidth, reply.borderWidth);
  assert.equal(comment.radius, reply.radius);
  assert.deepEqual(comment.geometry, reply.geometry);
  assert.equal(await input.getAttribute('placeholder'), 'Внутренний комментарий');
  await page.locator('#modeReplyBtn').click();
  assert.deepEqual(await snapshot(), reply);
  assert.equal(await input.inputValue(), 'Текст черновика');
  assert.equal(await input.getAttribute('placeholder'), 'Текст ответа клиенту');
  for (const file of ['styles.css', 'desk-v2.css']) assert.ok(assets.includes(`/static/${file}?v=20261106-personal-stats-v3`), `Fresh asset: ${file}`);
  assert.deepEqual(errors, []);
});
