const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {spawnSync} = require('node:child_process');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

// Exercise the existing editor against the actual registry, without a database or external API.
const registry = spawnSync('.\\.venv\\Scripts\\python.exe', ['-c',
  'import json; from dataclasses import asdict; from api.app.services.automatic_messages import list_automatic_message_definitions; print(json.dumps([asdict(x) for x in list_automatic_message_definitions()]))'], {encoding: 'utf8'});
assert.equal(registry.status, 0, 'registry must load');
const items = JSON.parse(registry.stdout).map(item => ({...item, effective_text: item.default_text}));
const categories = [];
for (const item of items) {
  let category = categories.find(value => value.category === item.category);
  if (!category) { category = {category: item.category, category_title: item.category_title, items: []}; categories.push(category); }
  category.items.push(item);
}

test('actual Bitrix templates form an independent block after origin and use the existing editor/save', async t => {
  const browser = await chromium.launch({channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true});
  t.after(() => browser.close());
  const page = await browser.newPage();
  await page.setContent('<div id="automaticMessagesRoot"></div><div id="emptyAutomaticMessagesText"></div>');
  await page.evaluate(payload => {
    window.HelpdeskSidebar = {init: () => Promise.resolve()};
    window.saved = [];
    window.fetch = async (path, options) => {
      if (options.method === 'PATCH') {
        const key = decodeURIComponent(path.split('/').pop());
        const change = JSON.parse(options.body);
        const item = payload.items.find(item => item.key === key);
        item.effective_text = change.body;
        window.saved.push({key, ...change});
        return {ok: true, text: async () => JSON.stringify(item)};
      }
      return {ok: true, text: async () => JSON.stringify(payload)};
    };
  }, {items, categories});
  await page.addScriptTag({content: fs.readFileSync('api/app/static/settings_automatic_messages.js', 'utf8')});
  await page.locator('.automatic-message-section').last().waitFor();
  const titles = await page.locator('.automatic-message-section h3').allTextContents();
  const index = titles.indexOf('Автоматизация');
  assert.deepEqual(titles.slice(index, index + 3), ['Автоматизация', 'Откуда клиент', 'Расшифровка номера Bitrix']);
  const block = page.locator('.automatic-message-section').filter({has: page.locator('h3', {hasText: 'Расшифровка номера Bitrix'})});
  assert.equal(await block.locator('[data-edit-template-key]').count(), 20);
  await block.locator('summary').click();
  for (const item of items.filter(item => item.category === 'bitrix_phone')) {
    await block.locator(`[data-edit-template-key="${item.key}"]`).click();
    await page.locator('#automaticMessageTextarea').fill('Новый текст ' + item.title);
    await page.locator('#automaticMessageApplyBtn').click();
    await page.locator('#automaticMessageEditorModal').waitFor({state: 'detached'});
  }
  assert.equal(await page.evaluate(() => window.saved.length), 20);
  assert.equal(await page.locator('.automatic-message-section .automatic-message-section').count(), 0);
});
