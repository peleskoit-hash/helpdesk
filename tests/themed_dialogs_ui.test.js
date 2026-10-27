const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });
async function setup(t, theme) {
  const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
  t.after(() => page.close());
  const errors = [], calls = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('dialog', async dialog => { errors.push(`Unexpected native ${dialog.type()}`); await dialog.dismiss(); });
  t.after(() => assert.deepEqual(errors, []));
  const me = { id: 'admin', role: 'admin', login: 'admin', full_name: 'Admin' };
  const leaf = { kind: 'terminal', label: 'Кнопка', technical_key: 'menu|ticket|leaf', workflow_type: 'ticket',
    button_id: '10000000-0000-4000-8000-000000000001', full_path: 'leaf', parent_path: '', children_rows: [], is_enabled: true };
  let exists = true;
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    if (url.hostname !== 'desk.test') return route.abort();
    if (p.startsWith('/static/')) {
      const file = path.resolve('api/app', p.slice(1));
      return route.fulfill({ status: fs.existsSync(file) ? 200 : 404, body: fs.existsSync(file) ? fs.readFileSync(file) : '',
        contentType: { '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream' });
    }
    if (!p.startsWith('/api/')) return route.fulfill({ contentType: 'text/html', body: fs.readFileSync(p === '/settings' ? 'api/app/templates/settings.html' : 'api/app/templates/settings_bot_buttons.html', 'utf8') });
    calls.push({ p, method: req.method() });
    let data = {};
    if (p.endsWith('/appearance-session')) data = { writer_id: '00000000-0000-4000-8000-000000000001', preferences: { theme_preference: theme, appearance_revision: '0' } };
    else if (p.endsWith('/preferences')) data = { theme_preference: theme, appearance_revision: '0' };
    else if (p.endsWith('/auth/session')) data = { real_user: me, effective_user: me, is_real_admin: true };
    else if (p.endsWith('/users/me') || p.endsWith('/users/me/profile')) data = me;
    else if (p.endsWith('/menu-types')) data = exists ? [{ menu_name: 'menu', display_name: 'Меню', destinations: [], buttons_count: 1 }] : [];
    else if (p.endsWith('/menu-types/menu') && req.method() === 'DELETE') { exists = false; data = { ok: true }; }
    else if (p.endsWith('/structure')) data = { root_level_key: 'root', tree_rows: [{ columns: 1, buttons: [leaf] }], removed_items: [] };
    else if (p.endsWith('/item')) data = { button_key: leaf.technical_key, button_label: leaf.label };
    else if (p.endsWith('/archived') || p.endsWith('/agents') || p.endsWith('/by-role') || p.endsWith('/assignees')) data = [];
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
  });
  await page.goto('http://desk.test/settings/bot-buttons');
  await page.locator('[data-menu-name]').first().waitFor();
  return { page, calls };
}
const dialog = page => page.locator('dialog.desk-dialog[open]');
const cancel = page => dialog(page).locator('[data-dialog-cancel]');
const accept = page => dialog(page).locator('[data-dialog-accept]');
for (const theme of ['light', 'dark']) {
  test(`double menu deletion requires both themed confirmations: ${theme}`, async t => {
    const { page, calls } = await setup(t, theme);
    await page.locator('[data-menu-name]').first().click();
    const deletes = () => calls.filter(c => c.method === 'DELETE').length;
    for (const refusal of ['first', 'second']) {
      await page.locator('#deleteMenuTypeBtn').click();
      assert.equal(await dialog(page).count(), 1); assert.equal(await dialog(page).locator('.desk-dialog-message').textContent(), 'Вы уверены, что хотите удалить меню кнопок?');
      assert.equal(deletes(), 0);
      if (refusal === 'second') {
        await accept(page).click();
        await page.waitForFunction(() => document.querySelector('.desk-dialog-message')?.textContent.includes('ТОЧНО'));
        assert.equal(deletes(), 0); assert.equal(await dialog(page).count(), 1);
      }
      await cancel(page).click(); assert.equal(await dialog(page).count(), 0); assert.equal(deletes(), 0);
    }
    await page.locator('#deleteMenuTypeBtn').click(); await accept(page).click();
    await page.waitForFunction(() => document.querySelector('.desk-dialog-message')?.textContent.includes('ТОЧНО'));
    await accept(page).click(); await page.getByText('Типы меню не найдены', { exact: true }).waitFor();
    assert.equal(deletes(), 1);
  });
  test(`nested text editor, info messages, focus and theme: ${theme}`, async t => {
    const { page, calls } = await setup(t, theme);
    await page.locator('[data-menu-name]').first().click();
    await page.locator('[data-button-color-key]').first().click();
    await page.locator('#editInstructionBtn').click();
    const textarea = page.locator('#botTextEditorTextarea'); await textarea.fill('Несохранённый текст');
    await page.keyboard.press('Escape'); await dialog(page).waitFor();
    await cancel(page).click(); assert.equal(await textarea.inputValue(), 'Несохранённый текст');
    await page.locator('#botTextEditorCancelBtn').click();
    assert.equal(await dialog(page).locator('.desk-dialog-message').textContent(), 'Есть несохранённые изменения. Закрыть окно без применения?');
    const appearance = await dialog(page).evaluate(el => {
      const s = getComputedStyle(el), r = el.getBoundingClientRect(), back = getComputedStyle(el, '::backdrop');
      return { font: s.fontSize, background: s.backgroundColor, expected: getComputedStyle(document.body).getPropertyValue('--desk-surface').trim(),
        radius: s.borderRadius, blur: back.backdropFilter, centered: Math.abs(r.x + r.width / 2 - innerWidth / 2) < 1,
        top: document.elementFromPoint(r.x + 20, r.y + 20) === el, cancelFocus: document.activeElement === el.querySelector('[data-dialog-cancel]') };
    });
    assert.equal(appearance.font, '28px'); assert.equal(appearance.radius, '24px'); assert.equal(appearance.blur, 'blur(6px)');
    assert.ok(appearance.centered && appearance.top && appearance.cancelFocus);
    assert.equal(await dialog(page).evaluate(el => { const probe = document.createElement('span'); probe.style.color = 'var(--desk-surface)'; el.appendChild(probe); const color = getComputedStyle(probe).color; probe.remove(); return color; }), appearance.background);
    await page.keyboard.press('Tab'); assert.equal(await accept(page).evaluate(el => document.activeElement === el), true);
    await page.keyboard.press('Tab'); assert.equal(await cancel(page).evaluate(el => document.activeElement === el), true);
    await page.keyboard.press('Shift+Tab'); assert.equal(await accept(page).evaluate(el => document.activeElement === el), true);
    await page.keyboard.press('Escape'); assert.equal(await dialog(page).count(), 0);
    assert.equal(await textarea.inputValue(), 'Несохранённый текст'); assert.equal(await textarea.isVisible(), true);
    assert.equal(await page.locator('#botTextEditorCancelBtn').evaluate(el => document.activeElement === el), true);
    await page.locator('#botTextEditorCancelBtn').click(); await accept(page).click(); assert.equal(await textarea.count(), 0);
    await page.locator('#botLabelInput').fill(''); await page.locator('#saveInstructionBtn').click();
    assert.equal(await dialog(page).locator('.desk-dialog-message').textContent(), 'Название в боте не может быть пустым.');
    assert.equal(await cancel(page).isVisible(), false); assert.equal(await accept(page).evaluate(el => document.activeElement === el), true);
    await page.keyboard.press('Escape');
    // The existing breadcrumb and editor save handlers both validate this action.
    assert.equal(await dialog(page).locator('.desk-dialog-message').textContent(), 'Название в боте не может быть пустым.');
    await page.keyboard.press('Escape'); assert.equal(await dialog(page).count(), 0);
    assert.equal(calls.some(c => c.method === 'PUT'), false);
  });
  test(`dirty editor guards page and internal navigation: ${theme}`, async t => {
    const { page } = await setup(t, theme);
    await page.locator('[data-menu-name]').first().click(); await page.locator('[data-button-color-key]').first().click();
    await page.locator('#botLabelInput').fill('Черновик');
    await page.locator('#botButtonsBackBtn').click();
    assert.equal(await dialog(page).locator('.desk-dialog-message').textContent(), 'Есть несохранённые изменения. Уйти без сохранения?');
    await cancel(page).click(); assert.equal(await page.locator('#botLabelInput').inputValue(), 'Черновик');
    await page.evaluate(() => { window.navigationResult = undefined; window.HelpdeskAppShell.navigate('/settings').then(value => { window.navigationResult = value; }); });
    await dialog(page).waitFor(); await page.keyboard.press('Escape');
    await page.waitForFunction(() => window.navigationResult === false); assert.match(page.url(), /bot-buttons/);
    assert.equal(await page.locator('#botLabelInput').inputValue(), 'Черновик');
    await page.evaluate(() => { window.backResult = undefined; window.HelpdeskAppShell.goBack().then(value => { window.backResult = value; }); });
    await dialog(page).waitFor(); await cancel(page).click();
    await page.waitForFunction(() => window.backResult === false); assert.equal(await page.locator('#botLabelInput').inputValue(), 'Черновик');
    await page.evaluate(() => { history.pushState({}, '', '/settings/bot-buttons?history-test=1'); history.back(); });
    await dialog(page).waitFor(); await page.keyboard.press('Escape');
    assert.equal(await page.locator('#botLabelInput').inputValue(), 'Черновик');
    await page.evaluate(() => { window.HelpdeskAppShell.navigate('/settings'); }); await dialog(page).waitFor(); await accept(page).click();
    await page.waitForURL('**/settings'); assert.equal(await dialog(page).count(), 0);
  });
}

for (const theme of ['light', 'dark']) test(`reusable dialog queue and small viewport: ${theme}`, async t => {
  const { page } = await setup(t, theme);
  assert.equal(await page.evaluate(() => [window.alert, window.confirm, window.prompt].every(fn => fn.toString().includes('[native code]'))), true);
  await page.setViewportSize({ width: 390, height: 740 });
  await page.evaluate(() => {
    window.results = [];
    window.HelpdeskDialogs.confirm('Первое').then(value => window.results.push(value));
    window.HelpdeskDialogs.alert('<b>Второе</b>\n' + 'Длинное сообщение '.repeat(20)).then(value => window.results.push(value));
  });
  await dialog(page).waitFor(); assert.equal(await dialog(page).count(), 1);
  assert.equal(await dialog(page).locator('.desk-dialog-message').textContent(), 'Первое');
  await cancel(page).click(); await page.waitForFunction(() => document.querySelector('.desk-dialog-message')?.textContent.startsWith('<b>'));
  assert.deepEqual(await page.evaluate(() => window.results), [false]); assert.equal(await dialog(page).locator('b').count(), 0);
  const box = await dialog(page).boundingBox(); assert.ok(box.x >= 0 && box.x + box.width <= 390 && box.y >= 0 && box.y + box.height <= 740);
  await page.keyboard.press('Enter'); await page.waitForFunction(() => window.results.length === 2);
  assert.deepEqual(await page.evaluate(() => window.results), [false, true]); assert.equal(await dialog(page).count(), 0);
});

for (const theme of ['light', 'dark']) test(`Telegram button upper panel typography stays stable: ${theme}`, async t => {
  const { page } = await setup(t, theme);
  await page.locator('[data-menu-name]').first().click();
  const metrics = () => page.evaluate(() => {
    const result = {};
    for (const selector of ['.main > .toolbar', '#botButtonsBackBtn', '#botButtonsPageTitle', '#themeToggleBtn', '#botButtonsBreadcrumb']) {
      const el = document.querySelector(selector), s = getComputedStyle(el), r = el.getBoundingClientRect();
      result[selector] = { font: s.font, padding: s.padding, margin: s.margin, height: r.height, y: r.y };
    }
    return result;
  });
  await page.locator('#editLayoutBtn').waitFor();
  const list = await metrics();
  await page.locator('[data-button-color-key]').first().click();
  await page.locator('#editInstructionBtn').waitFor();
  assert.deepEqual(await metrics(), list);
});

for (const theme of ['light', 'dark']) test(`prompt values, keyboard, focus and queue: ${theme}`, async t => {
  const { page } = await setup(t, theme);
  await page.evaluate(() => {
    document.querySelector('[data-menu-name]').focus();
    window.promptFocus = document.activeElement;
    window.promptResults = [];
    HelpdeskDialogs.prompt('Имя <b>раздела</b>', 'Исходное').then(v => promptResults.push(v));
    HelpdeskDialogs.prompt('Второй').then(v => promptResults.push(v));
    HelpdeskDialogs.prompt('Третий', 'Не сохранять').then(v => promptResults.push(v));
  });
  const input = dialog(page).locator('input'); await input.waitFor();
  assert.equal(await input.inputValue(), 'Исходное');
  assert.equal(await input.evaluate(el => document.activeElement === el && el.selectionEnd === el.value.length && el.selectionStart === 0), true);
  assert.equal(await dialog(page).locator('b').count(), 0);
  assert.deepEqual(await page.evaluate(() => promptResults), []);
  const colors = await input.evaluate(el => ({color: getComputedStyle(el).color, background: getComputedStyle(el).backgroundColor}));
  assert.notEqual(colors.color, colors.background);
  await page.keyboard.press('Shift+Tab'); assert.equal(await accept(page).evaluate(el => el === document.activeElement), true);
  await page.keyboard.press('Tab'); assert.equal(await input.evaluate(el => el === document.activeElement), true);
  await page.keyboard.press('Tab'); assert.equal(await cancel(page).evaluate(el => el === document.activeElement), true);
  await page.keyboard.press('Tab'); assert.equal(await accept(page).evaluate(el => el === document.activeElement), true);
  await page.keyboard.press('Tab'); await input.fill(''); await page.keyboard.press('Enter');
  await page.waitForFunction(() => document.querySelector('.desk-dialog-message')?.textContent === 'Второй');
  assert.equal(await input.inputValue(), ''); assert.deepEqual(await page.evaluate(() => promptResults), ['']);
  await input.fill('Черновик'); await cancel(page).click();
  await page.waitForFunction(() => document.querySelector('.desk-dialog-message')?.textContent === 'Третий');
  await page.keyboard.press('Escape'); await page.waitForFunction(() => promptResults.length === 3);
  assert.deepEqual(await page.evaluate(() => promptResults), ['', null, null]);
  assert.equal(await page.evaluate(() => document.activeElement === promptFocus), true);
  await page.evaluate(() => { HelpdeskDialogs.prompt('Строка', 42).then(v => promptResults.push(v)); });
  await input.waitFor(); assert.equal(await input.inputValue(), '42');
  await input.fill('  Новое имя  '); await accept(page).click();
  await page.waitForFunction(() => promptResults.length === 4);
  assert.equal(await page.evaluate(() => promptResults[3]), '  Новое имя  ');
});

for (const theme of ['light', 'dark']) for (const fallback of [false, true]) test(`Telegram URL prompt preserves draft and sanitization (${theme}, fallback=${fallback})`, async t => {
  const { page, calls } = await setup(t, theme);
  await page.locator('[data-menu-name]').first().click(); await page.locator('[data-button-color-key]').first().click();
  if (fallback) await page.evaluate(() => { window.TelegramTextEditor = undefined; });
  await page.locator('#editClientTextBtn').click();
  const textarea = page.locator('#botTextEditorTextarea'); await textarea.fill('Текст');
  const link = page.locator('[data-format-action="link"]');
  for (const action of ['cancel', 'escape', 'empty', 'unsafe', 'accept']) {
    await textarea.evaluate(el => { el.focus(); el.select(); }); await link.click();
    const input = dialog(page).locator('input'); await input.waitFor();
    assert.equal(await input.inputValue(), 'https://');
    assert.equal(await textarea.inputValue(), 'Текст'); assert.equal(await textarea.isVisible(), true);
    assert.equal(await input.evaluate(el => el === document.activeElement), true);
    assert.equal(await dialog(page).evaluate(el => { const r = el.getBoundingClientRect(); return document.elementFromPoint(r.x + 20, r.y + 20) === el; }), true);
    if (action === 'cancel') { await input.fill('https://discard.test'); await cancel(page).click(); }
    else if (action === 'escape') await page.keyboard.press('Escape');
    else { await input.fill(action === 'empty' ? '' : action === 'unsafe' ? 'javascript:alert(1)' : 'https://example.test?a=1&b=2'); await page.keyboard.press('Enter'); }
    await dialog(page).waitFor({state: 'detached'});
    assert.equal(await textarea.inputValue(), action === 'accept' ? '<a href="https://example.test?a=1&b=2">Текст</a>' : 'Текст');
    assert.equal(calls.some(c => c.method === 'PUT' || c.method === 'PATCH'), false);
  }
});

for (const theme of ['light', 'dark']) test(`section rename awaits input and distinguishes empty from cancel: ${theme}`, async t => {
  const { page } = await setup(t, theme);
  const source = fs.readFileSync('api/app/static/bot_buttons_level_view.js', 'utf8');
  const handler = source.match(/document\.getElementById\("renameSectionBtn"\)\?\.addEventListener\("click", async \(\) => \{([\s\S]*?)\n    \}\);/)[1];
  await page.evaluate(body => {
    window.renameCalls = [];
    const ctx = {state: {preview: {label: '  Раздел  '}}, saveLabelOverrideForNode: async (...args) => renameCalls.push(args)};
    document.body.insertAdjacentHTML('beforeend', '<button id="renameSectionBtn">Rename</button>');
    document.querySelector('#renameSectionBtn').addEventListener('click', new Function('ctx', `return async () => {${body}}`)(ctx));
  }, handler);
  for (const value of [null, '', 'Новое название']) {
    await page.locator('#renameSectionBtn').click(); const input = dialog(page).locator('input'); await input.waitFor();
    assert.equal(await input.inputValue(), 'Раздел'); assert.equal(await dialog(page).locator('.desk-dialog-message').textContent(), 'Название в боте');
    const before = await page.evaluate(() => renameCalls.length);
    if (value === null) await cancel(page).click(); else { await input.fill(value); assert.equal(await page.evaluate(() => renameCalls.length), before); await accept(page).click(); }
    await dialog(page).waitFor({state: 'detached'});
    assert.equal(await page.evaluate(() => renameCalls.length), before + (value === null ? 0 : 1));
    if (value !== null) assert.equal(await page.evaluate(() => renameCalls.at(-1)[1]), value);
  }
});
