const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });

for (const theme of ['light', 'dark']) test(`system event editors V2: ${theme}`, async t => {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(5000);
  t.after(() => page.close());
  const errors = [], writes = [], resources = [];
  page.on('pageerror', error => errors.push(error.message));
  const me = { id: 'admin', role: 'admin', login: 'admin', full_name: 'Admin' };
  const first = { subject_effective: 'Первое подключение', system_text_effective: 'Системный текст', instruction_text: 'Инструкция' };
  const groups = Object.fromEntries(['review-buttons', 'feedback-buttons'].map(key => [key, { buttons: [
    { key: `${key}-one`, text: 'Первая', default_text: 'Первая', button_style: null },
    { key: `${key}-two`, text: 'Вторая', default_text: 'Вторая', button_style: 'danger' },
  ] }]));
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    if (url.hostname !== 'desk.test') return route.abort();
    if (p.startsWith('/static/')) {
      resources.push(url.pathname + url.search);
      const file = path.resolve('api/app', p.slice(1));
      return route.fulfill({ status: fs.existsSync(file) ? 200 : 404, body: fs.existsSync(file) ? fs.readFileSync(file) : '',
        contentType: { '.css': 'text/css', '.js': 'text/javascript', '.svg': 'image/svg+xml', '.ttf': 'font/ttf' }[path.extname(file)] || 'application/octet-stream' });
    }
    if (!p.startsWith('/api/')) return route.fulfill({ contentType: 'text/html', body: fs.readFileSync('api/app/templates/settings_bot_buttons.html', 'utf8') });
    let data = {};
    if (p.endsWith('/appearance-session')) data = { writer_id: '00000000-0000-4000-8000-000000000001', preferences: { theme_preference: theme, appearance_revision: '0' } };
    else if (p.endsWith('/preferences')) data = { theme_preference: theme, appearance_revision: '0' };
    else if (p.endsWith('/auth/session')) data = { real_user: me, effective_user: me, is_real_admin: true, session_login_ts: 1 };
    else if (p.endsWith('/users/me') || p.endsWith('/users/me/profile')) data = me;
    else if (p.endsWith('/menu-types')) data = [{ menu_name: 'Основное', ok: true }];
    else if (/\/(agents|by-role|assignees|archived)$/.test(p)) data = [];
    else if (p.endsWith('/counts')) data = { active: 0, frozen: 0, closed: 0, special: 0, unassigned: 0, by_assignee: {} };
    else if (p.includes('/bot-buttons/system/')) {
      const key = p.split('/').pop();
      if (req.method() === 'PUT') {
        const body = req.postDataJSON(); writes.push({ key, body });
        if (key === 'first-connection') {
          if (body.subject) first.subject_effective = body.subject;
          if (body.system_text) first.system_text_effective = body.system_text;
          if (body.instruction_text) first.instruction_text = body.instruction_text;
        } else groups[key].buttons = body.buttons;
      }
      data = key === 'first-connection' ? first : groups[key];
    }
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
  });
  await page.goto('http://desk.test/settings/bot-buttons');
  await page.locator('[data-system-ticket-key="first-connection"]').waitFor();
  assert.equal(await page.locator('body').getAttribute('data-theme'), theme);
  const open = async key => {
    await page.locator(`[data-system-ticket-key="${key}"]`).click();
    await page.locator('[data-system-editor]').waitFor();
    const styles = await page.locator('[data-system-editor]').evaluate(el => {
      const panel = el.closest('.bot-buttons-panel'), section = el.querySelector('.bot-editor-section'), input = el.querySelector('input');
      const css = getComputedStyle(el);
      return { radius: getComputedStyle(panel).borderRadius, section: getComputedStyle(section).borderRadius,
        background: getComputedStyle(section).backgroundColor, expected: getComputedStyle(document.querySelector('#botButtonsBreadcrumb')).backgroundColor,
        font: css.fontSize, inputRadius: getComputedStyle(input).borderRadius, inputColor: getComputedStyle(input).color, color: css.color };
    });
    assert.equal(styles.radius, '24px'); assert.equal(styles.section, '16px'); assert.equal(styles.background, styles.expected);
    assert.equal(styles.font, '16.8px'); assert.equal(styles.inputRadius, '10px'); assert.equal(styles.inputColor, styles.color);
    assert.equal(await page.locator('[data-system-editor] .action-primary').evaluate(el => getComputedStyle(el).borderRadius), '12px');
    await page.locator('[data-system-editor] input').first().focus();
    assert.equal(await page.locator('[data-system-editor] input').first().evaluate(el => getComputedStyle(el).outlineStyle), 'solid');
  };
  await open('first-connection');
  await page.setViewportSize({ width: 600, height: 800 });
  assert.equal(await page.locator('.bot-editor-grid').evaluate(el => getComputedStyle(el).gridTemplateColumns.split(' ').length), 1);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.locator('#systemFirstConnectionSubject').fill('Новое название');
  for (const [button, value] of [['SystemText', 'Новый системный текст'], ['InstructionText', 'Новая инструкция']]) {
    await page.locator(`#editSystemFirstConnection${button}Btn`).click();
    assert.equal(await page.locator('#botTextEditorTextarea').inputValue(), button === 'SystemText' ? 'Системный текст' : 'Инструкция');
    assert.equal(await page.locator('.bot-text-editor-dialog').evaluate(el => getComputedStyle(el).borderRadius), '24px');
    await page.locator('#botTextEditorTextarea').fill(value);
    await page.locator('#botTextEditorApplyBtn').click();
  }
  await page.locator('#saveSystemFirstConnectionBtn').click();
  await page.waitForFunction(() => document.querySelector('#systemFirstConnectionSubject')?.value === 'Новое название');
  assert.deepEqual(writes[0], { key: 'first-connection', body: { subject: 'Новое название', system_text: 'Новый системный текст', instruction_text: 'Новая инструкция' } });
  await page.locator('#backFromSystemEditorBtn').click();
  for (const key of Object.keys(groups)) {
    await open(key);
    assert.equal(await page.locator('[data-system-button-row]').count(), 2);
    await page.locator('[data-system-button-text]').first().fill(`Изменено ${key}`);
    await page.locator(`[data-system-button-style="${key}-one"][data-style-value="success"]`).click();
    assert.equal(await page.locator('[data-system-button-text]').first().inputValue(), `Изменено ${key}`);
    const selected = page.locator(`[data-system-button-style="${key}-one"][data-style-value="success"]`);
    assert.equal(await selected.getAttribute('aria-pressed'), 'true');
    assert.equal(await selected.evaluate(el => getComputedStyle(el).backgroundColor), 'rgb(39, 174, 96)');
    await page.locator('#saveSystemButtonGroupBtn').click();
    await page.waitForFunction(key => document.querySelector('[data-system-button-text]')?.value === `Изменено ${key}`, key);
    assert.deepEqual(writes.at(-1), { key, body: { buttons: [
      { key: `${key}-one`, text: `Изменено ${key}`, button_style: 'success' },
      { key: `${key}-two`, text: 'Вторая', button_style: 'danger' },
    ] } });
    await page.locator('#backFromSystemButtonGroupBtn').click();
    await open(key);
    assert.equal(await selected.getAttribute('aria-pressed'), 'true');
    await page.setViewportSize({ width: 600, height: 800 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.locator('#backFromSystemButtonGroupBtn').click();
  }
  for (const file of ['styles.css', 'desk-v2.css', 'bot-buttons-browser-v2.css']) {
    assert.ok(resources.includes(`/static/${file}?v=20261106-system-editors-v2`), `Fresh asset: ${file}`);
  }
  assert.deepEqual(errors, []);
});
