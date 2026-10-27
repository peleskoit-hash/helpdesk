const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });
for (const theme of ['light', 'dark']) test(`quick reply themed deletion, file detach and dirty close: ${theme}`, async t => {
  const page = await browser.newPage({ viewport: { width: 1366, height: 1000 } });
  t.after(() => page.close());
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  page.on('dialog', async dialog => { errors.push(`Unexpected native ${dialog.type()}`); await dialog.dismiss(); });
  t.after(() => assert.deepEqual(errors, []));
  const html = fs.readFileSync('api/app/templates/quick_replies.html', 'utf8');
  await page.setContent(html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '').replace(/<link\b[^>]*>/g, ''));
  await page.locator('body').evaluate((el, theme) => { el.dataset.theme = theme; el.dataset.sidebarReady = '1'; }, theme);
  for (const file of ['styles.css','desk-v2.css','admin-pages-v2.css','admin-editors-v2.css','desk-dialogs.css']) await page.addStyleTag({ content: fs.readFileSync('api/app/static/' + file, 'utf8') });
  await page.evaluate(() => {
    window.HelpdeskSidebar = { init: async () => {} };
    window.replyWrites = [];
    const file = { id: 'f1', display_filename: 'test.txt' };
    const replies = [{ id: 'r1', author_user_id: 'admin', short_title: 'Ответ', body: 'Текст', can_edit: true, saved_files: [file] }];
    window.fetch = async (url, options = {}) => {
      if (options.method) window.replyWrites.push({ url, method: options.method, data: JSON.parse(options.body || '{}') });
      if (options.method === 'DELETE') replies.splice(0);
      if (options.method === 'PATCH') { const data = JSON.parse(options.body); Object.assign(replies[0], data, { saved_files: data.saved_file_ids.length ? [file] : [] }); }
      return { ok: true, status: 200, text: async () => JSON.stringify(url.endsWith('/session') ? { effective_user: { id: 'admin', role: 'admin' } } : options.method ? replies[0] || {} : replies) };
    };
  });
  await page.addScriptTag({ content: fs.readFileSync('api/app/static/desk_dialogs.js', 'utf8') });
  await page.addScriptTag({ content: fs.readFileSync('api/app/static/quick_replies.js', 'utf8') });
  await page.evaluate(async () => { await window.HelpdeskQuickReplies.initQuickRepliesPage(); });
  const card = page.locator('[data-reply-id]').first(), modal = page.locator('#quickReplyModal');
  const title = page.locator('#quickReplyTitleInput'), body = page.locator('#quickReplyBodyInput');
  const dialog = page.locator('dialog.desk-dialog[open]'), cancel = dialog.locator('[data-dialog-cancel]'), accept = dialog.locator('[data-dialog-accept]');
  async function close(method) {
    if (method === 'close') await page.locator('#closeQuickReplyModalBtn').click();
    if (method === 'cancel') await page.locator('#cancelQuickReplyBtn').click();
    if (method === 'Escape') await page.keyboard.press('Escape');
    if (method === 'backdrop') await modal.click({ position: { x: 3, y: 3 } });
  }
  for (const method of ['close', 'cancel', 'Escape', 'backdrop']) {
    await card.click(); await close(method); assert.equal(await modal.isVisible(), false); assert.equal(await dialog.count(), 0);
    await card.click(); const input = method === 'close' ? title : body; await input.fill('Черновик');
    await close(method); await dialog.waitFor({ timeout: 3000 });
    assert.equal(await dialog.locator('.desk-dialog-message').textContent(), 'Есть несохранённые изменения. Закрыть без сохранения?');
    await page.keyboard.press('Escape'); assert.equal(await input.inputValue(), 'Черновик'); assert.equal(await modal.isVisible(), true);
    await close(method); await accept.click(); await modal.waitFor({ state: 'hidden' });
    await card.click(); assert.equal(await title.inputValue(), 'Ответ'); assert.equal(await body.inputValue(), 'Текст'); await close('cancel');
  }
  assert.deepEqual(await page.evaluate(() => window.replyWrites), []);
  await card.click(); await body.fill('Новый'); await body.fill('Текст'); await close('Escape'); assert.equal(await dialog.count(), 0);
  await card.click(); await page.locator('[data-file-id]').click(); await dialog.waitFor();
  assert.equal(await dialog.locator('.desk-dialog-message').textContent(), 'Отвязать файл «test.txt» от быстрого ответа?');
  await cancel.click(); assert.equal(await page.locator('[data-file-id]').count(), 1);
  await page.locator('[data-file-id]').click(); await accept.click(); assert.equal(await page.locator('[data-file-id]').count(), 0);
  await close('cancel'); await cancel.click(); assert.equal(await page.locator('[data-file-id]').count(), 0, 'cancel keeps detached file draft');
  await close('close'); await accept.click(); await modal.waitFor({ state: 'hidden' });
  await card.click(); assert.equal(await page.locator('[data-file-id]').count(), 1, 'discard restores original attachments');
  await body.fill('Сохранено'); await page.locator('#saveQuickReplyBtn').click(); await modal.waitFor({ state: 'hidden' }); assert.equal(await dialog.count(), 0);
  await card.click(); assert.equal(await body.inputValue(), 'Сохранено'); await close('Escape'); assert.equal(await dialog.count(), 0);
  await page.locator('[data-delete]').click(); await dialog.waitFor();
  assert.equal(await dialog.locator('.desk-dialog-message').textContent(), 'Удалить быстрый ответ «Ответ»?');
  await cancel.click(); assert.equal(await page.locator('[data-reply-id]').count(), 1);
  assert.equal(await page.evaluate(() => window.replyWrites.some(r => r.method === 'DELETE')), false);
  await page.locator('[data-delete]').click(); await accept.click(); await page.locator('[data-reply-id]').waitFor({ state: 'detached' });
  assert.equal(await page.evaluate(() => window.replyWrites.filter(r => r.method === 'DELETE').length), 1);
});
