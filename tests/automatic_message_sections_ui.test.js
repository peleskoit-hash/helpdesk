const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

async function setup(t, theme = 'light', count = 8) {
  const browser = await chromium.launch({channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true});
  t.after(() => browser.close());
  const page = await browser.newPage({viewport: {width: 1280, height: 720}});
  const template = fs.readFileSync('api/app/templates/settings_automatic_messages.html', 'utf8');
  await page.setContent(template.replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '').replace(/<link\b[^>]*>/g, ''));
  await page.locator('body').evaluate((el, theme) => { el.dataset.theme = theme; }, theme);
  await page.addStyleTag({content: fs.readFileSync('api/app/static/styles.css', 'utf8')});
  await page.evaluate(count => {
    const items = Array.from({length: count}, (_, i) => ({key: `message.${i}`, title: `Сообщение ${i}`, description: 'Описание сообщения', effective_text: `Текст ${i}`, default_text: `Текст ${i}`, runtime_enabled: true, is_overridden: i === 0}));
    const payload = {items, categories: [
      {category: 'first', category_title: 'Первая категория', items: items.slice(0, count / 2)},
      {category: 'second', category_title: 'Вторая категория', items: items.slice(count / 2)},
    ]};
    window.HelpdeskSidebar = {init: () => Promise.resolve()};
    window.fetch = async (path, options) => {
      if (options.method === 'PATCH') {
        const item = items.find(item => item.key === decodeURIComponent(path.split('/').pop()));
        item.effective_text = JSON.parse(options.body).body;
        return {ok: true, text: async () => JSON.stringify(item)};
      }
      return {ok: true, text: async () => JSON.stringify(payload)};
    };
  }, count);
  await page.addScriptTag({content: fs.readFileSync('api/app/static/settings_automatic_messages.js', 'utf8')});
  await page.locator('details').last().waitFor({state: 'attached'});
  return page;
}

for (const theme of ['light', 'dark']) {
test(`long expanded categories scroll to the last editable message in the real page (${theme})`, async t => {
  const page = await setup(t, theme, 120);
  await page.locator('summary').first().click();
  await page.locator('summary').last().click();
  const sizes = await page.evaluate(() => ['.main', '.content', '.automatic-messages-panel', '#automaticMessagesRoot'].map(selector => {
    const el = document.querySelector(selector);
    const css = getComputedStyle(el);
    return {selector, height: el.clientHeight, scrollHeight: el.scrollHeight, overflow: css.overflowY, maxHeight: css.maxHeight};
  }));
  const panel = page.locator('.automatic-messages-panel');
  assert.ok(await panel.evaluate(el => el.clientHeight >= el.scrollHeight - 1), 'panel must contain all expanded rows');
  assert.ok(sizes[0].scrollHeight > sizes[0].height, 'main workspace must have vertical scroll');
  assert.equal(sizes[3].overflow, 'visible', 'message list must not introduce a nested scrollbar');
  await page.locator('.main').evaluate(el => { el.scrollTop = el.scrollHeight; });
  const last = page.locator('.automatic-message-row').last();
  const bounds = await last.boundingBox();
  assert.ok(bounds.y >= 0 && bounds.y + bounds.height <= 720, 'last message is visible after scrolling');
  await last.click({timeout: 3000});
  assert.equal(await page.locator('#automaticMessageTextarea').inputValue(), 'Текст 119');
  await page.locator('#automaticMessageCancelBtn').click();
  await page.setViewportSize({width: 1024, height: 600});
  await page.locator('.main').evaluate(el => { el.scrollTop = el.scrollHeight; });
  await last.click({timeout: 3000});
  assert.equal(await page.locator('#automaticMessageTextarea').inputValue(), 'Текст 119');
});
}

for (const theme of ['light', 'dark']) {
  test(`categories collapse independently and grow naturally (${theme})`, async t => {
    const page = await setup(t, theme);
    const sections = page.locator('details');
    assert.deepEqual(await sections.locator('h3').allTextContents(), ['Первая категория', 'Вторая категория']);
    assert.equal(await page.locator('details[open]').count(), 0);
    assert.equal(await sections.first().locator('.automatic-message-row').first().isVisible(), false);
    assert.deepEqual(await sections.locator('summary > span').allTextContents(), ['4', '4']);
    const arrow = () => sections.first().locator('i').evaluate(el => getComputedStyle(el, '::before').content);
    assert.equal(await arrow(), '"▼"');
    const height = (await page.locator('.automatic-messages-panel').boundingBox()).height;
    await sections.first().locator('summary').click();
    assert.equal(await arrow(), '"▲"');
    assert.ok((await page.locator('.automatic-messages-panel').boundingBox()).height > height);
    await sections.last().locator('summary').click();
    assert.equal(await page.locator('details[open]').count(), 2);
    assert.equal(await page.locator('#automaticMessagesRoot').evaluate(el => getComputedStyle(el).overflowY), 'visible');
    await sections.first().locator('summary').click();
    assert.equal(await sections.first().getAttribute('open'), null);
    assert.notEqual(await sections.last().getAttribute('open'), null);
    await sections.first().locator('summary').focus();
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('details[open]').count(), 2);
    await page.keyboard.press('Space');
    assert.equal(await page.locator('details[open]').count(), 1);
  });
}

test('whole message row and keyboard open the existing editor; saving preserves expanded categories', async t => {
  const page = await setup(t);
  await page.locator('summary').first().click();
  await page.locator('summary').last().click();
  const row = page.locator('.automatic-message-row').first();
  assert.equal(await row.getAttribute('role'), 'button');
  assert.equal(await row.getAttribute('tabindex'), '0');
  assert.equal(await page.locator('.automatic-message-row button').count(), 0);
  assert.deepEqual(await row.locator('.automatic-message-badge').allTextContents(), ['Изменено', 'Подключено']);
  for (const action of ['click', 'Enter', 'Space']) {
    if (action === 'click') await row.locator('.automatic-message-description').click();
    else { await row.focus(); await page.keyboard.press(action); }
    assert.equal(await page.locator('#automaticMessageTextarea').inputValue(), 'Текст 0');
    await page.locator('#automaticMessageCancelBtn').click();
    assert.equal(await page.locator('#automaticMessageEditorModal').count(), 0);
  }
  const box = await row.boundingBox();
  await row.click({position: {x: box.width - 5, y: box.height / 2}});
  await page.locator('#automaticMessageTextarea').fill('Изменённый текст');
  await page.locator('#automaticMessageApplyBtn').click();
  await page.locator('#automaticMessageEditorModal').waitFor({state: 'detached'});
  assert.equal(await page.locator('details[open]').count(), 2);
  await row.click();
  assert.equal(await page.locator('#automaticMessageTextarea').inputValue(), 'Изменённый текст');
});

test('direct and shell resources share new versions', () => {
  const version = '20261022-auto-message-sections';
  const shell = fs.readFileSync('api/app/static/app_shell.js', 'utf8');
  const template = fs.readFileSync('api/app/templates/settings_automatic_messages.html', 'utf8');
  for (const source of [shell, template]) assert.ok(source.includes(`settings_automatic_messages.js?v=${version}`));
  for (const file of fs.readdirSync('api/app/templates').filter(name => name.endsWith('.html'))) {
    const source = fs.readFileSync(`api/app/templates/${file}`, 'utf8');
    for (const resource of ['styles.css', 'app_shell.js']) {
      const versions = [...source.matchAll(new RegExp(`/static/${resource.replace('.', '\\.')}\\?v=([^"\\s]+)`, 'g'))].map(match => match[1]);
      if (versions.length) assert.deepEqual([...new Set(versions)], [resource === 'styles.css' ? '20261024-sidebar-stable-v2' : '20261024-notification-layout-v2'], `${file}: ${resource}`);
    }
  }
});
