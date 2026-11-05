const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

async function setup(t, theme = 'light', count = 8, withOrigin = false) {
  const browser = await chromium.launch({channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true});
  t.after(() => browser.close());
  const page = await browser.newPage({viewport: {width: 1280, height: 720}});
  if (withOrigin) {
    await page.route('**/*', route => route.request().url() === 'http://desk.test/'
      ? route.fulfill({contentType: 'text/html', body: '<html></html>'}) : route.abort());
    await page.route('**/static/*.css*', route => route.fulfill({contentType: 'text/css',
      body: fs.readFileSync('api/app' + new URL(route.request().url()).pathname, 'utf8')}));
    await page.goto('http://desk.test/');
  }
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  page.on('dialog', async dialog => { errors.push(`Unexpected native ${dialog.type()}`); await dialog.dismiss(); });
  t.after(() => assert.deepEqual(errors, []));
  const template = fs.readFileSync('api/app/templates/settings_automatic_messages.html', 'utf8');
  await page.setContent(template.replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '').replace(/<link\b[^>]*>/g, ''));
  await page.locator('body').evaluate((el, theme) => { el.dataset.theme = theme; el.dataset.sidebarReady = '1'; }, theme);
  await page.addStyleTag({content: fs.readFileSync('api/app/static/styles.css', 'utf8')});
  for (const file of ['desk-v2.css', 'admin-editors-v2.css']) await page.addStyleTag({content: fs.readFileSync('api/app/static/' + file, 'utf8')});
  await page.evaluate(count => {
    const items = Array.from({length: count}, (_, i) => ({key: `message.${i}`, title: `Сообщение ${i}`, description: 'Описание сообщения', effective_text: `Текст ${i}`, default_text: `Текст ${i}`, runtime_enabled: true, is_overridden: i === 0, allowed_variables: ['ticket_id']}));
    const payload = {items, categories: [
      {category: 'first', category_title: 'Первая категория', items: items.slice(0, count / 2)},
      {category: 'second', category_title: 'Вторая категория', items: items.slice(count / 2)},
    ]};
    window.HelpdeskSidebar = {init: () => Promise.resolve()};
    window.HelpdeskAppShell = {registerPage(name, factory) { window.automaticLifecycle = factory(); }};
    window.messageRequests = [];
    window.fetch = async (path, options) => {
      window.messageRequests.push({path, method: options.method || 'GET'});
      if (options.method === 'DELETE') { const item = items.find(item => item.key === decodeURIComponent(path.split('/').pop())); item.effective_text = item.default_text; return {ok: true, text: async () => JSON.stringify(item)}; }
      if (options.method === 'PATCH') {
        const item = items.find(item => item.key === decodeURIComponent(path.split('/').pop()));
        item.effective_text = JSON.parse(options.body).body;
        return {ok: true, text: async () => JSON.stringify(item)};
      }
      return {ok: true, text: async () => JSON.stringify(payload)};
    };
  }, count);
  await page.addStyleTag({content: fs.readFileSync('api/app/static/desk-dialogs.css', 'utf8')});
  await page.addScriptTag({content: fs.readFileSync('api/app/static/desk_dialogs.js', 'utf8')});
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
  assert.ok(await panel.evaluate(el => el.scrollHeight > el.clientHeight), 'panel must scroll expanded rows inside the fixed V2 bubble');
  assert.ok(sizes[0].scrollHeight <= sizes[0].height + 1, 'main workspace keeps the V2 bubble inside the viewport');
  assert.equal(sizes[3].overflow, 'visible', 'message list must not introduce a nested scrollbar');
  await page.locator('.automatic-messages-panel').evaluate(el => { el.scrollTop = el.scrollHeight; });
  const last = page.locator('.automatic-message-row').last();
  const bounds = await last.boundingBox();
  assert.ok(bounds.y >= 0 && bounds.y + bounds.height <= 720, 'last message is visible after scrolling');
  await last.click({timeout: 3000});
  assert.equal(await page.locator('#automaticMessageTextarea').inputValue(), 'Текст 119');
  await page.locator('#automaticMessageCancelBtn').click();
  await page.setViewportSize({width: 1024, height: 600});
  await page.locator('.automatic-messages-panel').evaluate(el => { el.scrollTop = el.scrollHeight; });
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
    const height = await page.locator('#automaticMessagesRoot').evaluate(el => el.scrollHeight);
    await sections.first().locator('summary').click();
    assert.equal(await arrow(), '"▲"');
    assert.ok(await page.locator('#automaticMessagesRoot').evaluate(el => el.scrollHeight) > height);
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
  const version = '20261027-dialog-prompts-v4';
  const shell = fs.readFileSync('api/app/static/app_shell.js', 'utf8');
  const template = fs.readFileSync('api/app/templates/settings_automatic_messages.html', 'utf8');
  for (const source of [shell, template]) assert.ok(source.includes(`settings_automatic_messages.js?v=${version}`));
  for (const file of ['quick_replies', 'settings_automatic_messages']) {
    const direct = fs.readFileSync(`api/app/templates/${file}.html`, 'utf8');
    const fileVersion = file === 'quick_replies' ? '20261027-message-dialogs-v2' : version;
    assert.ok(direct.indexOf('/static/desk_dialogs.js?v=20261027-dialog-prompts-v4') < direct.indexOf(`/static/${file}.js?v=${fileVersion}`));
    assert.ok(shell.includes(`/static/${file}.js?v=${fileVersion}`));
  }
  for (const file of fs.readdirSync('api/app/templates').filter(name => name.endsWith('.html'))) {
    const source = fs.readFileSync(`api/app/templates/${file}`, 'utf8');
    for (const resource of ['styles.css', 'app_shell.js']) {
      const versions = [...source.matchAll(new RegExp(`/static/${resource.replace('.', '\\.')}\\?v=([^"\\s]+)`, 'g'))].map(match => match[1]);
      if (versions.length) assert.deepEqual([...new Set(versions)], [resource === 'styles.css' ? '20261106-system-editors-v2' : '20261105-column-auto-width-v8'], `${file}: ${resource}`);
    }
  }
});


for (const theme of ['light', 'dark']) test(`automatic message dirty guards all close paths and editor actions: ${theme}`, async t => {
  const page = await setup(t, theme);
  await page.locator('summary').first().click();
  const row = page.locator('.automatic-message-row').first(), textarea = page.locator('#automaticMessageTextarea');
  const dialog = page.locator('dialog.desk-dialog[open]');
  const cancel = dialog.locator('[data-dialog-cancel]'), accept = dialog.locator('[data-dialog-accept]');
  async function close(method) {
    if (method === 'button') await page.locator('#automaticMessageCancelBtn').click();
    if (method === 'Escape') await page.keyboard.press('Escape');
    if (method === 'backdrop') await page.locator('#automaticMessageEditorModal').click({position: {x: 3, y: 3}});
  }
  const writes = () => page.evaluate(() => window.messageRequests.filter(r => r.method !== 'GET'));
  for (const method of ['button', 'Escape', 'backdrop']) {
    await row.click(); await close(method); assert.equal(await textarea.count(), 0); assert.equal(await dialog.count(), 0);
  }
  for (const [change, method] of [['text', 'button'], ['text', 'Escape'], ['text', 'backdrop'], ['format', 'Escape'], ['variable', 'button'], ['emoji', 'backdrop']]) {
    await row.click();
    if (change === 'text') await textarea.fill('Черновик');
    if (change === 'format') { await textarea.evaluate(node => node.select()); await page.locator('[data-format-action="bold"]').click(); }
    if (change === 'variable') { await page.locator('[data-variable-trigger]').click(); await page.locator('[data-variable-name]').first().click(); }
    if (change === 'emoji') { await page.locator('[data-format-action="emoji"]').click(); await page.locator('[data-emoji]').first().click(); }
    const draft = await textarea.inputValue(); assert.notEqual(draft, 'Текст 0');
    await close(method); await dialog.waitFor({timeout: 3000}).catch(error => { throw Error(`${change}/${method}: ${error.message}`); });
    assert.equal(await dialog.locator('.desk-dialog-message').textContent(), 'Есть несохранённые изменения. Закрыть окно без применения?');
    await cancel.click(); assert.equal(await textarea.inputValue(), draft); assert.deepEqual(await writes(), []);
    await close(method); await dialog.waitFor({timeout: 3000}).catch(error => { throw Error(`${change}/${method}: ${error.message}`); }); await accept.click(); await textarea.waitFor({state: 'detached'});
    await row.click(); assert.equal(await textarea.inputValue(), 'Текст 0'); await close('button');
    assert.equal(await dialog.count(), 0); assert.deepEqual(await writes(), []);
  }
  await row.click(); await textarea.fill('Изменение'); await textarea.fill('Текст 0'); await close('Escape');
  assert.equal(await textarea.count(), 0); assert.equal(await dialog.count(), 0, 'reverted draft is clean');
  await row.click();
  for (const picker of ['[data-variable-trigger]', '[data-format-action="emoji"]']) {
    await page.locator(picker).click(); await page.keyboard.press('Escape');
    assert.equal(await textarea.isVisible(), true); assert.equal(await dialog.count(), 0, 'picker state is not a content edit');
  }
  await textarea.fill('Сохранённый текст'); await page.locator('#automaticMessageApplyBtn').click();
  await textarea.waitFor({state: 'detached'}); assert.equal(await dialog.count(), 0);
  await row.click(); assert.equal(await textarea.inputValue(), 'Сохранённый текст'); await close('button'); assert.equal(await dialog.count(), 0);
  await row.click(); await page.locator('#automaticMessageResetBtn').click(); await dialog.waitFor();
  assert.equal(await dialog.locator('.desk-dialog-message').textContent(), 'Вернуть этот текст к базовому шаблону?');
  const beforeReset = await writes(); await cancel.click(); assert.equal(await textarea.inputValue(), 'Сохранённый текст'); assert.deepEqual(await writes(), beforeReset);
  await page.locator('#automaticMessageResetBtn').click(); await accept.click(); await textarea.waitFor({state: 'detached'});
  await row.click(); assert.equal(await textarea.inputValue(), 'Текст 0');
  await textarea.fill('Другой черновик');
  await page.locator('.automatic-message-row').nth(1).dispatchEvent('click'); await dialog.waitFor(); await cancel.click();
  assert.equal(await textarea.inputValue(), 'Другой черновик', 'switching templates cannot discard a dirty editor');
  await page.evaluate(() => { window.leaveResult = undefined; Promise.resolve(window.automaticLifecycle.canNavigateAway()).then(value => { window.leaveResult = value; }); });
  await dialog.waitFor(); await page.keyboard.press('Escape'); await page.waitForFunction(() => window.leaveResult === false);
  assert.equal(await textarea.inputValue(), 'Другой черновик');
  await page.evaluate(() => { Promise.resolve(window.automaticLifecycle.canNavigateAway()).then(approved => { if (approved) window.automaticLifecycle.destroy(); }); });
  await dialog.waitFor(); await accept.click(); await textarea.waitFor({state: 'detached'});
});

for (const theme of ['light', 'dark']) test(`automatic message URL prompt waits, cancels and sanitizes: ${theme}`, async t => {
  const page = await setup(t, theme, 8, true);
  await page.locator('summary').first().click(); await page.locator('.automatic-message-row').first().click();
  const textarea = page.locator('#automaticMessageTextarea'), dialog = page.locator('dialog.desk-dialog[open]');
  for (const action of ['cancel', 'escape', 'empty', 'unsafe', 'accept']) {
    await textarea.evaluate(el => { el.focus(); el.select(); }); await page.locator('[data-format-action="link"]').click();
    const input = dialog.locator('input'); await input.waitFor();
    assert.equal(await input.inputValue(), 'https://'); assert.equal(await textarea.inputValue(), 'Текст 0');
    if (action === 'cancel') await dialog.locator('[data-dialog-cancel]').click();
    else if (action === 'escape') await page.keyboard.press('Escape');
    else { await input.fill(action === 'empty' ? '' : action === 'unsafe' ? 'javascript:alert(1)' : 'tg://resolve?domain=example'); await page.keyboard.press('Enter'); }
    await dialog.waitFor({state: 'detached'});
    assert.equal(await textarea.isVisible(), true);
    assert.equal(await textarea.inputValue(), action === 'accept' ? '<a href="tg://resolve?domain=example">Текст 0</a>' : 'Текст 0');
    assert.equal(await page.evaluate(() => messageRequests.some(r => r.method !== 'GET')), false);
  }
});
