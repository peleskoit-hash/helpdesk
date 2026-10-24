// Real page scripts and CSS, with isolated API fixtures (no local DB writes).
// PLAYWRIGHT_MODULE may point to the bundled Playwright installation.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });
const pages = {
  profile: '/profile',
  profile_notification_bot: '/profile/notification-bot',
  settings_notification_bot_basic: '/settings/notification-bot-basic',
};
const types = ['new_assignment', 'new_message', 'internal_task_completion', 'transfer_to_agent', 'transfer_from_agent'];
async function setup(t, template, theme, role = 'agent') {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  t.after(() => page.close());
  const errors = [], writes = [];
  page.on('pageerror', error => errors.push(error.message));
  const me = { id: 'demo-user', role, login: 'demo', full_name: 'Александр Иванов', legacy_ticket_count: 42, availability_status: 'online' };
  const preferences = { theme_preference: theme, appearance_revision: '0' };
  const items = types.map(type => ({ type, title: type, enabled: true, use_base_settings: false,
    template: 'Уведомление <b>клиенту</b> {ticket_number}', html_enabled: true,
    disable_link_preview: false, send_silently: false,
    tag_items: [{ label: '{ticket_number}', description: 'Номер заявки' }] }));
  const notification = { role, use_base_settings: false, delivery: {}, telegram_binding: { active: true }, items: items.filter(item => role === 'admin' ? item.type === 'internal_task_completion' : ['new_assignment', 'new_message'].includes(item.type)) };
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    if (url.hostname !== 'desk.test') return route.abort();
    if (p.startsWith('/static/')) {
      const file = path.resolve('api/app', p.slice(1));
      return route.fulfill({ status: fs.existsSync(file) ? 200 : 404,
        body: fs.existsSync(file) ? fs.readFileSync(file) : '',
        contentType: { '.css': 'text/css', '.js': 'text/javascript', '.svg': 'image/svg+xml', '.ttf': 'font/ttf' }[path.extname(file)] || 'application/octet-stream' });
    }
    if (!p.startsWith('/api/') && p !== '/logout') {
      const target = Object.keys(pages).find(key => pages[key] === p) || template;
      return route.fulfill({ contentType: 'text/html', body: fs.readFileSync(`api/app/templates/${target}.html`, 'utf8') });
    }
    let result = {};
    if (['PUT', 'PATCH', 'POST'].includes(req.method())) writes.push({ path: p, body: req.postDataJSON() });
    if (p.endsWith('/appearance-session')) result = { writer_id: '00000000-0000-4000-8000-000000000001', preferences };
    else if (p.endsWith('/preferences')) {
      if (req.method() === 'PATCH') Object.assign(preferences, req.postDataJSON());
      result = preferences;
    } else if (p === '/api/v1/auth/session') result = { real_user: me, effective_user: me, is_real_admin: role === 'admin', session_login_ts: 1, app_timezone: 'Europe/Moscow' };
    else if (p === '/api/v1/users/me' || p === '/api/v1/users/me/profile') result = me;
    else if (p === '/api/v1/users/agents' || p === '/api/v1/users/by-role') result = [me];
    else if (p.endsWith('/counts')) result = { active: 1, unassigned: 0, frozen: 0, closed: 0, special: 0, by_assignee: {} };
    else if (p === '/api/v1/me/notification-bot') {
      if (req.method() === 'PUT') {
        const body = req.postDataJSON();
        Object.assign(notification.delivery, body.delivery);
        notification.items = notification.items.map(item => ({ ...item, ...body.items.find(x => x.type === item.type) }));
        notification.use_base_settings = body.use_base_settings;
      }
      result = notification;
    } else if (p === '/api/v1/settings/notification-bot/basic') result = { items };
    else if (p.startsWith('/api/v1/settings/notification-bot/basic/')) {
      const item = items.find(item => item.type === p.split('/').pop());
      Object.assign(item, req.postDataJSON()); result = item;
    }
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(result) });
  });
  await page.goto('http://desk.test' + pages[template]);
  await page.waitForSelector(template === 'profile' ? '#profileRoot:not(.hidden)' : '[data-template-textarea]');
  await page.waitForFunction(theme => document.body.dataset.theme === theme, theme);
  await page.evaluate(() => document.fonts.ready);
  await page.addStyleTag({ content: '* { transition: none !important; animation: none !important; }' });
  t.after(() => assert.deepEqual(errors, []));
  return { page, writes, preferences };
}
async function screenshot(page, name) {
  if (!process.env.PROFILE_V2_SCREENSHOT_DIR) return;
  fs.mkdirSync(process.env.PROFILE_V2_SCREENSHOT_DIR, { recursive: true });
  await page.screenshot({ path: path.join(process.env.PROFILE_V2_SCREENSHOT_DIR, `${name}.png`), fullPage: true });
}
async function style(page, selector, pseudo) {
  return page.locator(selector).first().evaluate((el, pseudo) => {
    const s = getComputedStyle(el, pseudo);
    return { background: s.backgroundColor, color: s.color, border: s.borderColor, radius: s.borderRadius,
      width: s.width, height: s.height, appearance: s.appearance, transform: s.transform, outline: s.outlineStyle,
      surface: getComputedStyle(document.body).getPropertyValue('--desk-surface').trim(),
      accent: getComputedStyle(document.body).getPropertyValue('--desk-accent').trim() };
  }, pseudo);
}
async function assertContrast(page, selectors) {
  const results = await page.evaluate(selectors => {
    const rgb = c => c.match(/[\d.]+/g)?.slice(0, 3).map(Number);
    const luminance = c => rgb(c).map(v => { v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; })
      .reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
    return selectors.map(selector => {
      const el = document.querySelector(selector);
      if (!el) throw new Error(`Missing contrast target: ${selector}`);
      const s = getComputedStyle(el);
      let parent = el, bg;
      do { bg = getComputedStyle(parent).backgroundColor; parent = parent.parentElement; } while (bg === 'rgba(0, 0, 0, 0)' && parent);
      const a = luminance(s.color), b = luminance(bg);
      return { selector, color: s.color, bg, ratio: (Math.max(a, b) + .05) / (Math.min(a, b) + .05) };
    });
  }, selectors);
  for (const result of results) assert.ok(result.ratio >= 4.5, JSON.stringify(result));
}
for (const theme of ['light', 'dark']) {
  test(`personal data V2: ${theme}, edit and save login/password`, async t => {
    const { page, writes } = await setup(t, 'profile', theme);
    assert.equal((await style(page, '.profile-row')).radius, '16px');
    await assertContrast(page, ['.profile-label', '.profile-value', '.profile-login-hero', '#changeAvatarBtn']);
    await screenshot(page, `${theme}-profile`);
    const originalAvatar = await page.locator('#profileAvatar').boundingBox();
    assert.equal(originalAvatar.width, 164);
    await page.locator('#editLoginBtn').click();
    await page.locator('#loginInput').fill('new-login');
    await assertContrast(page, ['#loginInput', '#saveLoginBtn']);
    await page.locator('#saveLoginBtn').click();
    await page.waitForFunction(() => !document.querySelector('#profileSuccessMessage').classList.contains('hidden'));
    assert.deepEqual(writes.find(x => x.path.endsWith('/me/login')).body, { login: 'new-login' });
    // Reload before the existing delayed logout; editing behavior is unchanged.
    await page.reload();
    await page.locator('#editPasswordBtn').click();
    await page.locator('#currentPasswordInput').fill('Old-password-123');
    await page.locator('#newPasswordInput').fill('New-password-456');
    await page.locator('#newPasswordConfirmationInput').fill('New-password-456');
    await page.locator('#savePasswordBtn').click();
    await page.waitForFunction(() => !document.querySelector('#profileSuccessMessage').classList.contains('hidden'));
    assert.equal(writes.find(x => x.path.endsWith('/me/password')).body.new_password, 'New-password-456');
  });
  for (const role of ['agent', 'admin']) {
    test(`personal bot V2: ${theme}, ${role}, native toggles and saved settings`, async t => {
      const { page, writes } = await setup(t, 'profile_notification_bot', theme, role);
      const switchEl = page.locator('[data-telegram-send-silently]');
      assert.equal((await style(page, '[data-telegram-send-silently]')).appearance, 'none');
      assert.equal((await style(page, '[data-telegram-send-silently]')).width, '40px');
      assert.equal((await style(page, '[data-telegram-send-silently]', '::before')).width, '18px');
      await switchEl.focus();
      assert.equal((await style(page, '[data-telegram-send-silently]')).outline, 'solid');
      await page.keyboard.press('Space');
      assert.equal(await switchEl.isChecked(), true);
      assert.equal((await style(page, '[data-telegram-send-silently]', '::before')).transform, 'matrix(1, 0, 0, 1, 16, 0)');
      // Current accent follows Appearance immediately in either theme.
      await page.locator('#themeToggleBtn').click();
      const sliders = page.locator('#appearanceMenu input[type=range]');
      assert.equal(await sliders.count(), 4);
      const before = (await style(page, '[data-telegram-send-silently]')).background;
      await sliders.nth(2).evaluate(input => { input.value = '135'; input.dispatchEvent(new Event('input', { bubbles: true })); });
      assert.notEqual((await style(page, '[data-telegram-send-silently]')).background, before);
      await page.keyboard.press('Escape');
      await page.locator('[data-template-textarea]').fill('Новый текст <b>уведомления</b>');
      await page.locator('[data-insert-tag]').first().click();
      assert.match(await page.locator('[data-template-textarea]').inputValue(), /\{ticket_number\}/);
      await assertContrast(page, ['.notification-enabled-row', ...(role === 'agent' ? ['.notification-tab.active'] : []), '[data-template-textarea]', '.notification-tag-btn', '.bot-telegram-preview-title', '.bot-telegram-preview', '[data-save]']);
      if (role === 'agent') {
        assert.equal(await page.locator('[data-delivery-switch="new_assignment"]').isDisabled(), true);
        assert.equal(await page.locator('.notification-tab').count(), 2);
        await page.locator('.notification-tab').nth(1).click();
        await page.locator('.notification-tab').nth(0).click();
        assert.match(await page.locator('[data-template-textarea]').inputValue(), /Новый текст/);
      } else assert.equal(await page.locator('.notification-tab').count(), 0);
      await Promise.all([page.waitForResponse(r => r.url().endsWith('/api/v1/me/notification-bot') && r.request().method() === 'PUT'), page.locator('[data-save]').click()]);
      const saved = writes.filter(x => x.path === '/api/v1/me/notification-bot').at(-1).body;
      assert.equal(saved.items[0].send_silently, true);
      await page.reload();
      await page.waitForSelector('[data-template-textarea]');
      assert.equal(await page.locator('[data-telegram-send-silently]').isChecked(), true);
      assert.match(await page.locator('[data-template-textarea]').inputValue(), /Новый текст/);
      await screenshot(page, `${theme}-${role}`);
      await Promise.all([page.waitForResponse(r => r.url().endsWith('/api/v1/me/notification-bot') && r.request().method() === 'PUT'), page.locator('[data-use-base-settings]').check()]);
      await page.waitForFunction(() => document.querySelector('[data-template-textarea]').disabled);
      await assertContrast(page, ['[data-template-textarea]']);
    });
  }
  test(`admin basic bot V2: ${theme}, toggle and editor save`, async t => {
    const { page, writes } = await setup(t, 'settings_notification_bot_basic', theme, 'admin');
    assert.equal(await page.locator('.notification-tab').count(), 5);
    await page.locator('[data-enabled]').uncheck();
    assert.equal((await style(page, '[data-enabled]', '::before')).transform, 'none');
    await page.locator('[data-template-textarea]').fill('Базовый текст');
    await page.locator('[data-telegram-disable-link-preview]').check();
    await assertContrast(page, ['[data-template-textarea]', '.notification-tab.active', '.bot-telegram-preview-title', '.bot-telegram-preview', '[data-save-type]']);
    await Promise.all([page.waitForResponse(r => r.url().includes('/basic/new_assignment') && r.request().method() === 'PUT'), page.locator('[data-save-type]').click()]);
    const saved = writes.find(x => x.path.endsWith('/basic/new_assignment')).body;
    assert.equal(saved.enabled, false);
    assert.equal(saved.disable_link_preview, true);
    assert.equal(saved.template, 'Базовый текст');
  });
}


for (const theme of ['light', 'dark']) {
  test(`AppShell and CSS scope: ${theme}`, async t => {
    const { page } = await setup(t, 'profile', theme, 'admin');
    const snapshot = () => page.locator('.sidebar, .toolbar, .sidebar input[type="checkbox"]').evaluateAll(nodes => nodes.map(el => {
      const s = getComputedStyle(el), r = el.getBoundingClientRect();
      return [s.color, s.background, s.border, s.font, s.boxShadow, s.appearance, r.width, r.height];
    }));
    const enabled = await snapshot();
    const disable = disabled => page.evaluate(disabled => {
      const visit = sheet => {
        for (const rule of sheet.cssRules) {
          if (rule.styleSheet && rule.href.includes('profile-settings-v2.css')) rule.styleSheet.disabled = disabled;
          else if (rule.styleSheet) visit(rule.styleSheet);
        }
      };
      for (const sheet of document.styleSheets) visit(sheet);
    }, disabled);
    await disable(true);
    assert.deepEqual(await snapshot(), enabled, 'sidebar and header are unchanged');
    // Matching legacy classes on an unrelated page retain their old appearance.
    await page.locator('#appContent').evaluate(el => { el.dataset.shellPage = 'settings-home'; });
    const oldRow = await style(page, '.profile-row');
    await disable(false);
    assert.deepEqual(await style(page, '.profile-row'), oldRow);
    await page.locator('#appContent').evaluate(el => { el.dataset.shellPage = 'profile'; });
    await page.evaluate(() => {
      const a = document.createElement('a'); a.href = '/profile/notification-bot'; a.textContent = 'Notification link';
      a.id = 'test-nav'; document.querySelector('#appContent').appendChild(a);
    });
    await page.locator('#test-nav').click();
    await page.waitForSelector('[data-template-textarea]');
    assert.equal((await style(page, '[data-use-base-settings]')).appearance, 'none');
    await assertContrast(page, ['.notification-enabled-row', '.bot-telegram-preview']);
    await page.goBack();
    await page.waitForSelector('#profileRoot:not(.hidden)');
    assert.equal((await style(page, '.profile-row')).radius, '16px');
  });
}
