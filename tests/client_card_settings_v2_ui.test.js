const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });

async function setup(t, theme) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  t.after(() => page.close());
  const errors = [], writes = [];
  page.on('pageerror', e => errors.push(e.message));
  const field = { id: 'f1', key: 'custom_flag', label: 'Проверка', field_type: 'checkbox', is_system: false, is_editable: true, is_visible: true, is_visible_in_editor: true, block_key: 'custom_test' };
  const config = { blocks: [{ id: 'b1', key: 'custom_test', title: 'Тестовый блок', color_scheme: 'blue', is_active: true, show_in_card: true, show_in_editor: true, fields: [field] },
    { id: 'b2', key: 'profile', title: 'Профиль', color_scheme: '#FFC2C2', is_active: true, fields: [] }],
    rules: [{ id: 'r1', name: 'Проверка правила', is_active: true, condition_field_key: 'custom_flag', condition_operator: 'always', actions: [{ target_type: 'field', target_key: 'custom_flag', action: 'show', apply_to: 'both' }] }],
    sources: [{ id: 's1', name: 'Источник', technical_key: 'demo', is_active: true, method: 'GET', url_template: 'https://example.test/user', query_template_json: {}, headers_template_json: {}, mapping_json: [] }] };
  const me = { id: 'admin1', role: 'admin', full_name: 'Администратор', availability_status: 'online' };
  await page.route('**/*', route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    if (url.hostname !== 'desk.test') return route.abort();
    if (p.startsWith('/static/')) {
      const file = path.resolve('api/app', p.slice(1));
      return route.fulfill({ body: fs.readFileSync(file), contentType: { '.css': 'text/css', '.js': 'text/javascript', '.ttf': 'font/ttf', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream' });
    }
    if (!p.startsWith('/api/')) return route.fulfill({ contentType: 'text/html', body: fs.readFileSync('api/app/templates/settings_client_card.html', 'utf8') });
    let result = {};
    if (p.endsWith('/appearance-session')) result = { writer_id: '00000000-0000-4000-8000-000000000001', preferences: { theme_preference: theme } };
    else if (p.endsWith('/preferences')) result = { theme_preference: theme };
    else if (p.endsWith('/auth/session')) result = { real_user: me, effective_user: me, is_real_admin: true, app_timezone: 'Europe/Moscow' };
    else if (p.endsWith('/users/me')) result = me;
    else if (p.endsWith('/agents') || p.endsWith('/by-role')) result = [me];
    else if (p.endsWith('/counts')) result = { by_assignee: {} };
    else if (p.includes('/settings/client-card')) {
      result = config;
      if (['PATCH', 'POST', 'DELETE'].includes(req.method())) {
        const body = req.postDataJSON(); writes.push({ path: p, method: req.method(), body });
        if (p.endsWith('/blocks/b1') && body) Object.assign(config.blocks[0], body);
        if (p.endsWith('/fields/f1') && body) Object.assign(field, body);
      }
    }
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(result) });
  });
  await page.goto('http://desk.test/settings/client-card');
  await page.waitForSelector('.client-card-settings-block');
  await page.waitForFunction(theme => document.body.dataset.theme === theme, theme);
  await page.evaluate(() => document.fonts.ready);
  await page.addStyleTag({ content: '* { transition: none !important; }' });
  t.after(() => assert.deepEqual(errors, []));
  return { page, writes };
}

async function checkToggles(page, root) {
  const rows = await page.locator(`${root} input[type=checkbox]`).evaluateAll(nodes => nodes.filter(el => el.getClientRects().length).map(el => {
    const a = el.getBoundingClientRect(), label = el.closest('label'), s = getComputedStyle(el);
    return { toggle: el.classList.contains('desk-toggle'), appearance: s.appearance, label: !!label,
      inline: label && getComputedStyle(label).display === 'flex', width: a.width, height: a.height };
  }));
  assert.ok(rows.length);
  for (const row of rows) { assert.equal(row.toggle, true); assert.equal(row.appearance, 'none'); assert.equal(row.inline, true); assert.equal(row.width, 40); assert.equal(row.height, 24); }
}

async function checkContrast(page, selector) {
  const samples = await page.locator(selector).evaluateAll(nodes => nodes.filter(el => el.getClientRects().length && !el.disabled).map(el => {
    const s = getComputedStyle(el);
    let p = el, bg;
    do { bg = getComputedStyle(p).backgroundColor; p = p.parentElement; } while (bg === 'rgba(0, 0, 0, 0)' && p);
    const luminance = color => color.match(/[\d.]+/g).slice(0, 3).map(Number).map(v => { v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; }).reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
    const a = luminance(s.color), b = luminance(bg);
    return { id: el.id || el.className, ratio: (Math.max(a, b) + .05) / (Math.min(a, b) + .05) };
  }));
  for (const sample of samples) assert.ok(sample.ratio >= 4.5, JSON.stringify(sample));
}

async function screenshot(page, name) {
  if (!process.env.CLIENT_SETTINGS_SCREENSHOTS) return;
  fs.mkdirSync(process.env.CLIENT_SETTINGS_SCREENSHOTS, { recursive: true });
  await page.screenshot({ path: `${process.env.CLIENT_SETTINGS_SCREENSHOTS}/${name}.png` });
}

for (const theme of ['light', 'dark']) {
  test(`client-card unified tab panel and section insets: ${theme}`, async t => {
    const { page } = await setup(t, theme);
    for (const width of [1440, 1024, 640]) {
      await page.setViewportSize({ width, height: 1000 });
      await page.locator('#clientCardBlocksTab').click();
      const panel = await page.locator('.client-card-work-panel').boundingBox();
      const bar = await page.locator('.client-card-main-row').boundingBox();
      const button = page.locator('#addClientCardBlockBtn'), buttonBox = await button.boundingBox();
      assert.equal(await button.evaluate(el => el.closest('[role=tablist]') === null && el.parentElement.classList.contains('client-card-main-row')), true);
      assert.ok(Math.abs(bar.x + bar.width - buttonBox.x - buttonBox.width - 5) < 1, 'add button is inside the unified panel at its right inset');
      assert.ok(bar.x >= panel.x && bar.x + bar.width <= panel.x + panel.width);
      await button.click();
      assert.equal(await page.locator('#createClientCardBlockModal').isVisible(), true);
      await page.keyboard.press('Escape');
      for (const section of ['Rules', 'Sources']) {
        await page.locator(`#clientCard${section}Tab`).click();
        assert.equal(await button.isVisible(), false);
        assert.deepEqual(await page.locator('.client-card-work-panel').boundingBox(), panel, 'tab switching preserves outer bubble geometry');
        const measured = await page.locator(`#clientCard${section}Panel`).evaluate(el => {
          const p = el.getBoundingClientRect(), head = el.querySelector('.client-card-rules-head').getBoundingClientRect(), list = el.querySelector('.client-card-rules'), r = list.getBoundingClientRect();
          return { insets: [head.left - p.left, head.top - p.top, p.right - head.right], sameEdges: head.left === r.left && head.right === r.right,
            gap: getComputedStyle(list).gap, rowHeight: list.firstElementChild.getBoundingClientRect().height };
        });
        assert.deepEqual(measured.insets, [16, 16, 16]); assert.equal(measured.sameEdges, true); assert.equal(measured.gap, '8px');
        assert.ok(measured.rowHeight < 200, 'list rows keep natural height');
        await page.locator(section === 'Rules' ? '#addClientCardRuleBtn' : '#addClientCardSourceBtn').click();
        assert.equal(await page.locator(section === 'Rules' ? '#clientCardRuleModal' : '#clientCardSourceModal').isVisible(), true);
        await page.keyboard.press('Escape');
      }
    }
  });
  test(`client-card settings V2 tabs, nested fields and save: ${theme}`, async t => {
    const { page, writes } = await setup(t, theme);
    assert.deepEqual(await page.locator('[role=tab]').allTextContents(), ['Блоки карточки клиента', 'Правила автоматизаций', 'Источники данных']);
    assert.equal(await page.locator('#adminAgentToggleAll').evaluate(el => el.classList.contains('desk-toggle')), false, 'sidebar selection checkbox stays native');
    assert.equal(await page.locator('input[type=color]').count(), 0);
    const bounds = await page.evaluate(() => {
      const m = document.querySelector('#appContent').getBoundingClientRect(), p = document.querySelector('.client-card-work-panel').getBoundingClientRect(), h = document.querySelector('.toolbar').getBoundingClientRect();
      return [p.top - h.bottom, p.left - m.left, m.right - p.right, m.bottom - p.bottom];
    });
    assert.deepEqual(bounds, [14, 16, 16, 16]);
    await page.locator('.client-card-accordion-title').first().click();
    await checkToggles(page, '#clientCardBlocksPanel');
    assert.equal(await page.locator('.client-card-field-row input[type=checkbox]').count(), 0);
    assert.equal(await page.locator('.client-card-accordion-meta').count(), 0);
    const groups = page.locator('.client-card-block-options').first();
    assert.deepEqual(await groups.locator('.client-card-option-title').allTextContents(), ['Параметры', 'Отображение']);
    assert.deepEqual(await groups.locator('input').evaluateAll(nodes => nodes.map(el => el.dataset.prop)), ['is_active', 'show_title', 'show_in_editor', 'show_in_card']);
    const layout = await groups.evaluate(el => {
      const group = el.getBoundingClientRect(), editor = el.parentElement.getBoundingClientRect(), name = el.previousElementSibling.getBoundingClientRect();
      const sections = [...el.children].map(node => node.getBoundingClientRect());
      return { centerDelta: Math.abs((group.left + group.right) / 2 - (name.right + 24 + editor.right) / 2), sameRow: sections[0].top === sections[1].top,
        titleSize: getComputedStyle(document.querySelector('.client-card-accordion-title')).fontSize, fieldSize: getComputedStyle(document.querySelector('.client-card-field-label')).fontSize,
        tabSize: getComputedStyle(document.querySelector('[role=tab]')).fontSize };
    });
    assert.ok(layout.centerDelta <= 1); assert.equal(layout.sameRow, true);
    assert.equal(layout.titleSize, '18px'); assert.equal(layout.fieldSize, '15px'); assert.equal(layout.tabSize, '22px');
    await checkContrast(page, '.client-card-accordion-title, .client-card-field-label, .client-card-field-meta, .client-card-block-editor input:not([type=checkbox])');
    await screenshot(page, `${theme}-blocks`);
    for (const prop of ['is_active', 'show_title', 'show_in_editor', 'show_in_card']) {
      await Promise.all([page.waitForResponse(r => r.url().endsWith('/blocks/b1') && r.request().method() === 'PATCH'), page.locator(`[data-block-id=b1][data-prop=${prop}]`).uncheck()]);
      assert.equal(writes.filter(w => w.path.endsWith('/blocks/b1')).at(-1).body[prop], false);
    }
    assert.equal(writes.find(w => w.path.endsWith('/blocks/b1')).body.color_scheme, 'blue', 'historical color is preserved exactly');
    await page.locator('[data-open-field-id=f1]').click();
    await checkToggles(page, '#editClientCardFieldModal');
    assert.deepEqual(await page.locator('#editClientCardFieldModal .client-card-option-title').allTextContents(), ['Параметры', 'Отображение']);
    assert.deepEqual(await page.locator('#editClientCardFieldModal .client-card-option-section input').evaluateAll(nodes => nodes.map(el => el.id)),
      ['editFieldEditable', 'editFieldAdminOnlyEdit', 'editFieldRequired', 'editFieldShowWhenFalse', 'editFieldVisible', 'editFieldVisibleInEditor']);
    assert.deepEqual(await page.locator('#editClientCardFieldModal .modal-field-section > label > input, #editClientCardFieldModal .modal-field-section > label > select, #editClientCardFieldModal .modal-field-section > label > textarea').evaluateAll(nodes => nodes.map(el => el.id)),
      ['editFieldLabel', 'editFieldBlockKey', 'editFieldKey', 'editFieldType', 'editFieldOptions']);
    await checkContrast(page, '#editClientCardFieldModal label, #editClientCardFieldModal input:not([type=checkbox]), #editClientCardFieldModal button');
    await screenshot(page, `${theme}-field`);
    await page.locator('#editFieldLabel').fill('Новая подпись');
    await page.locator('#editFieldRequired').check();
    await page.locator('#saveEditClientCardFieldBtn').click();
    await page.locator('#editClientCardFieldModal').waitFor({ state: 'hidden' });
    assert.equal(writes.find(w => w.path.endsWith('/fields/f1')).body.is_required, true);
    await page.locator('#clientCardRulesTab').click();
    assert.equal(await page.locator('#clientCardBlocksPanel').isVisible(), false);
    await page.locator('#clientCardRules .client-card-rule-row').click();
    await checkToggles(page, '#clientCardRuleModal');
    await checkContrast(page, '#clientCardRuleModal label, #clientCardRuleModal input:not([type=checkbox]), #clientCardRuleModal select, #clientCardRuleModal button');
    await screenshot(page, `${theme}-rule`);
    await page.locator('#ruleName').fill('Изменённое правило');
    await page.locator('#saveClientCardRuleBtn').click();
    await page.locator('#clientCardRuleModal').waitFor({ state: 'hidden' });
    assert.ok(writes.some(w => w.path.endsWith('/rules/r1')));
    await page.locator('#clientCardSourcesTab').click();
    await page.locator('#clientCardSources .client-card-rule-row').click();
    await checkToggles(page, '#clientCardSourceModal');
    await checkContrast(page, '#clientCardSourceModal label, #clientCardSourceModal input:not([type=checkbox]), #clientCardSourceModal textarea, #clientCardSourceModal button');
    await screenshot(page, `${theme}-source`);
    await page.locator('#sourceName').fill('Новый источник');
    await page.locator('#saveClientCardSourceBtn').click();
    await page.locator('#clientCardSourceModal').waitFor({ state: 'hidden' });
    assert.ok(writes.some(w => w.path.endsWith('/sources/s1')));
  });

  test(`client-card settings V2 small windows and creation dialogs: ${theme}`, async t => {
    const { page } = await setup(t, theme);
    const chrome = () => page.locator('.sidebar, .sidebar *, .toolbar, .toolbar *').evaluateAll(nodes => nodes.filter(el => el.getClientRects().length).map(el => {
      const r = el.getBoundingClientRect(), s = getComputedStyle(el); return [r.x, r.y, r.width, r.height, s.font, s.backgroundColor, s.color];
    }));
    const before = await chrome();
    const disable = value => page.evaluate(value => {
      const visit = sheet => { for (const rule of sheet.cssRules) {
        if (rule.styleSheet && rule.href.includes('client-card-settings-v2.css')) rule.styleSheet.disabled = value;
        else if (rule.styleSheet) visit(rule.styleSheet);
      } };
      for (const sheet of document.styleSheets) visit(sheet);
    }, value);
    await disable(true); assert.deepEqual(await chrome(), before, 'header and sidebar are unchanged'); await disable(false);
    for (const [width, height] of [[1024, 740], [640, 740]]) {
      await page.setViewportSize({ width, height });
      const insets = await page.evaluate(() => {
        const p = document.querySelector('.client-card-work-panel').getBoundingClientRect(), m = document.querySelector('#appContent').getBoundingClientRect(), h = document.querySelector('.toolbar').getBoundingClientRect();
        return [p.left - m.left, p.top - h.bottom, m.right - p.right, m.bottom - p.bottom];
      });
      assert.deepEqual(insets, width <= 700 ? [12, 12, 12, 12] : [16, 14, 16, 16]);
      await page.locator('#addClientCardBlockBtn').click();
      assert.equal(await page.locator('input[type=color]').count(), 0);
      await page.keyboard.press('Escape');
      if (!await page.locator('.client-card-accordion-body').first().isVisible()) await page.locator('.client-card-accordion-title').first().click();
      await page.locator('[data-add-field-block-key]').first().click();
      await checkToggles(page, '#createClientCardFieldModal');
      const dialog = await page.locator('#createClientCardFieldModal .modal-dialog').boundingBox();
      assert.ok(dialog.x >= 0 && dialog.y >= 0 && dialog.x + dialog.width <= width && dialog.y + dialog.height <= height);
      await page.keyboard.press('Escape');
    }
  });
}
