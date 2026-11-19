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
  tickets: '/tickets',
  profile_notification_bot: '/profile/notification-bot',
  settings_notification_bot_basic: '/settings/notification-bot-basic',
};
const types = ['new_assignment', 'new_message', 'internal_task_completion', 'transfer_to_agent', 'transfer_from_agent'];
const profileApi = '/api/v1/users/me/profile';
async function openProfile(page) {
  await page.locator('[data-sidebar-user-menu-toggle]').click();
  await page.locator('[data-sidebar-user-menu-item="profile"]').click();
  await page.locator('#profileModal').waitFor({ state: 'visible' });
}

async function setup(t, template, theme, role = 'agent') {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  page.setDefaultTimeout(6500);
  t.after(() => page.close());
  const errors = [], writes = [];
  page.on('pageerror', error => errors.push(error.message));
  const me = { id: 'demo-user', role, login: 'demo', full_name: 'Александр Иванов', legacy_ticket_count: 42, availability_status: 'online' };
  const reads = [];
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
    if (p === '/login') return route.fulfill({ contentType: 'text/html', body: '<title>Вход</title>' });
    if (p === '/avatar.png') return route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><rect width="16" height="16" fill="blue"/></svg>' });
    if (!p.startsWith('/api/') && p !== '/logout') {
      const target = Object.keys(pages).find(key => pages[key] === p) || template;
      return route.fulfill({ contentType: 'text/html', body: fs.readFileSync(`api/app/templates/${target}.html`, 'utf8') });
    }
    let result = {};
    if (['PUT', 'PATCH', 'POST', 'DELETE'].includes(req.method())) {
      writes.push({ path: p, method: req.method(), body: p.endsWith('/me/avatar') ? req.postData() : req.postDataJSON() });
    }
    if (p === profileApi) reads.push(p);
    if (p.endsWith('/appearance-session')) result = { writer_id: '00000000-0000-4000-8000-000000000001', preferences };
    else if (p.endsWith('/preferences')) {
      if (req.method() === 'PATCH') Object.assign(preferences, req.postDataJSON());
      result = preferences;
    } else if (p === '/api/v1/auth/session') result = { real_user: me, effective_user: me, is_real_admin: role === 'admin', session_login_ts: 1, app_timezone: 'Europe/Moscow' };
    else if (p === '/api/v1/users/me' || p === profileApi) result = me;
    else if (p.endsWith('/me/avatar')) {
      Object.assign(me, { avatar_present: req.method() === 'PUT', avatar_url: req.method() === 'PUT' ? '/avatar.png' : null });
      result = me;
    } else if (p === '/api/v1/tickets/active') {
      result = { items: Array.from({ length: 40 }, (_, i) => ({ id: `ticket-${i}`, subject: `Заявка ${i}`, status: 'new' })), total: 40, page: 1, pages: 1 };
    }
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
  await page.waitForSelector(template === 'tickets' ? '.ticket-row' : '[data-template-textarea]');
  await page.waitForFunction(theme => document.body.dataset.theme === theme, theme);
  await page.evaluate(() => document.fonts.ready);
  await page.addStyleTag({ content: '* { transition: none !important; animation: none !important; }' });
  t.after(() => assert.deepEqual(errors, []));
  return { page, writes, preferences, reads };
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
  test(`personal data V2: ${theme}, edit credentials and relogin after closing`, async t => {
    const { page, writes } = await setup(t, 'tickets', theme);
    await openProfile(page);
    await page.locator('#profileRoot').waitFor();
    assert.equal((await style(page, '.profile-row')).radius, '12px');
    await assertContrast(page, ['.profile-label', '.profile-value', '.profile-login-hero', '#changeAvatarBtn']);
    await screenshot(page, `${theme}-profile`);
    const originalAvatar = await page.locator('#profileAvatar').boundingBox();
    assert.equal(originalAvatar.width, 164);
    if (theme === 'light') {
      await page.locator('#editLoginBtn').click();
      await page.locator('#loginInput').fill('new-login');
      await assertContrast(page, ['#loginInput', '#saveLoginBtn']);
      await page.locator('#saveLoginBtn').click();
    } else {
      await page.locator('#editPasswordBtn').click();
      await page.locator('#currentPasswordInput').fill('Old-password-123');
      await page.locator('#newPasswordInput').fill('New-password-456');
      await page.locator('#newPasswordConfirmationInput').fill('New-password-456');
      await page.locator('#savePasswordBtn').click();
    }
    await page.waitForFunction(() => !document.querySelector('#profileSuccessMessage').classList.contains('hidden'));
    if (theme === 'light') assert.deepEqual(writes.find(x => x.path.endsWith('/me/login')).body, { login: 'new-login' });
    else assert.equal(writes.find(x => x.path.endsWith('/me/password')).body.new_password, 'New-password-456');
    await page.locator('#closeProfileBtn').click();
    await page.locator('#profileModal').waitFor({ state: 'hidden' });
    await page.waitForURL('**/login');
    assert.equal(writes.filter(x => x.path === '/logout' && x.method === 'POST').length, 1);
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
      const accentHue = page.locator('#appearanceMenu [data-appearance-field="accent_hue"]');
      const before = (await style(page, '[data-telegram-send-silently]')).background;
      await accentHue.evaluate(input => { input.value = '135'; input.dispatchEvent(new Event('input', { bubbles: true })); });
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
      await page.locator('.notification-settings-toggle').click();
      await Promise.all([page.waitForResponse(r => r.url().endsWith('/api/v1/me/notification-bot') && r.request().method() === 'PUT'), page.locator('[data-use-base-settings]').check()]);
      await page.waitForFunction(() => document.querySelector('[data-template-textarea]').disabled);
      assert.equal(await page.locator('#notificationReceivingSettings').evaluate(el => el.matches(':popover-open')), true);
      await page.keyboard.press('Escape');
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
  test(`profile overlay preserves workspace through close and SPA navigation: ${theme}`, async t => {
    const { page, reads } = await setup(t, 'tickets', theme);
    const dialog = page.locator('#profileModal');
    const toggle = page.locator('[data-sidebar-user-menu-toggle]');
    const scroller = page.locator('#appContent .table-scroll');
    const scroll = () => scroller.evaluate(el => el.scrollTop);
    const url = page.url();
    const before = reads.length;
    assert.equal(await dialog.count(), 0);
    assert.equal(await page.locator('script[src*="/static/profile.js"]').count(), 0);
    await scroller.evaluate(el => { el.scrollTop = 220; });
    assert.equal(await scroll(), 220);

    await openProfile(page);
    await page.locator('#profileRoot').waitFor();
    assert.equal(await page.locator('#appContent #profileModal').count(), 0);
    assert.equal(page.url(), url);
    assert.equal(await page.locator('#profileFullName').textContent(), 'Александр Иванов');
    assert.equal(await page.locator('#profileLegacyCount').textContent(), '42');
    const geometry = await page.evaluate(() => {
      const box = selector => document.querySelector(selector).getBoundingClientRect();
      const details = [...document.querySelectorAll('.profile-row')];
      const pencils = ['#editLoginBtn', '#editPasswordBtn'].map(box);
      return {
        modalWidth: box('#profileModal').width,
        availableWidth: box('#profileRoot').width - 48,
        rowInsets: details.map(el => {
          const rect = el.getBoundingClientRect(), root = box('#profileRoot');
          return [rect.left - root.left, root.right - rect.right];
        }),
        offset: (box('#profileModal').left + box('#profileModal').right - box('#appContent').left - box('#appContent').right) / 2,
        rowWidths: details.map(el => el.getBoundingClientRect().width),
        rowRights: details.map(el => el.getBoundingClientRect().right),
        rowHeights: details.map(el => el.getBoundingClientRect().height),
        pencils: pencils.map((rect, i) => [rect.left - details[i + 1].getBoundingClientRect().right, rect.width, rect.height]),
        pencilAligned: pencils[0].left === pencils[1].left,
        pencilCentered: pencils.every((rect, i) => {
          const row = details[i + 1].getBoundingClientRect();
          return Math.abs(rect.top + rect.height / 2 - row.top - row.height / 2) < 1;
        }),
        textClear: pencils.every((rect, i) => {
          const value = details[i + 1].querySelector('.profile-value').getBoundingClientRect();
          return value.right <= rect.left;
        }),
      };
    });
    assert.ok(Math.abs(geometry.offset) <= 1, JSON.stringify(geometry));
    assert.equal(geometry.modalWidth, 892);
    assert.deepEqual(geometry.rowWidths, Array(6).fill(geometry.availableWidth));
    assert.deepEqual(geometry.rowInsets, Array.from({ length: 6 }, () => [24, 24]));
    assert.deepEqual(geometry.rowHeights, Array(6).fill(68));
    assert.deepEqual(geometry.rowRights, Array(6).fill(geometry.rowRights[0]));
    assert.deepEqual(geometry.pencils, [[-58, 48, 48], [-58, 48, 48]]);
    assert.equal(geometry.pencilAligned, true);
    assert.equal(geometry.pencilCentered, true);
    assert.equal(geometry.textClear, true);

    for (const [button, nested] of [['#editLoginBtn', '#loginModal'], ['#editPasswordBtn', '#passwordModal']]) {
      await page.locator(button).click();
      await page.locator(nested).waitFor();
      assert.equal(await page.locator(nested).evaluate(el => el.matches(':modal')), true);
      await page.keyboard.press('Escape');
      await page.locator(nested).waitFor({ state: 'hidden' });
      assert.equal(await dialog.isVisible(), true);
      assert.equal(await page.locator(button).evaluate(el => document.activeElement === el), true);
    }
    await page.keyboard.press('Escape');
    await dialog.waitFor({ state: 'hidden' });
    assert.equal(await toggle.evaluate(el => document.activeElement === el), true);
    assert.equal(await scroll(), 220);
    await openProfile(page);
    assert.equal(await dialog.count(), 1);
    assert.equal(reads.length, before + 1);
    await page.mouse.click(5, 5);
    await dialog.waitFor({ state: 'hidden' });
    assert.equal(await scroll(), 220);

    await page.evaluate(() => window.HelpdeskAppShell.navigate('/profile/notification-bot'));
    await page.waitForSelector('[data-template-textarea]');
    const otherUrl = page.url();
    await page.setViewportSize({ width: theme === 'light' ? 750 : 320, height: 540 });
    await openProfile(page);
    assert.equal(page.url(), otherUrl);
    assert.equal(await dialog.count(), 1);
    assert.equal(reads.length, before + 1);
    const body = await page.locator('.profile-window-body').evaluate(el => {
      el.scrollTop = el.scrollHeight;
      return { scrollTop: el.scrollTop, overflowX: el.scrollWidth - el.clientWidth };
    });
    assert.ok(body.scrollTop > 0);
    assert.equal(body.overflowX, 0);
    await page.locator('#closeProfileBtn').click();
    await dialog.waitFor({ state: 'hidden' });
    assert.equal(await page.locator('[data-template-textarea]').isVisible(), true);
  });
}


test('profile load can be retried and avatar dialogs update both images', async t => {
  const { page, writes } = await setup(t, 'tickets', 'dark');
  let fail = true;
  await page.route('**' + profileApi, route => {
    if (!fail) return route.fallback();
    fail = false;
    return route.fulfill({ status: 503, json: { detail: 'Недоступно' } });
  });
  await openProfile(page);
  await page.locator('#profileError').waitFor();
  assert.match(await page.locator('#profileError').textContent(), /Не удалось загрузить личные данные/);
  await page.locator('#closeProfileBtn').click();
  await openProfile(page);
  await page.locator('#profileRoot').waitFor();

  const avatar = page.locator('#profileAvatarImage');
  const sidebar = page.locator('[data-sidebar-user-menu-toggle]');
  const crop = page.locator('#avatarCropModal');
  const removal = page.locator('#deleteAvatarModal');
  const file = await page.evaluate(() => {
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 32;
    canvas.getContext('2d').fillRect(0, 0, 32, 32);
    return canvas.toDataURL().split(',')[1];
  });
  const selectImage = async () => {
    const [chooser] = await Promise.all([page.waitForEvent('filechooser'), page.locator('#changeAvatarBtn').click()]);
    await chooser.setFiles({ name: 'avatar.png', mimeType: 'image/png', buffer: Buffer.from(file, 'base64') });
    await crop.waitFor();
  };
  await selectImage();
  assert.equal(await crop.evaluate(el => el.matches(':modal')), true);
  await page.keyboard.press('Escape');
  await crop.waitFor({ state: 'hidden' });
  assert.equal(await page.locator('#profileModal').isVisible(), true);
  await selectImage();
  await page.locator('#saveAvatarCropBtn').click();
  await crop.waitFor({ state: 'hidden' });
  await page.locator('#profileModal .save-confirmation').waitFor();
  await avatar.waitFor();
  await page.locator('[data-sidebar-user-menu-toggle][data-sidebar-avatar-image="1"]').waitFor({ state: 'attached' });
  const avatarUrl = await avatar.getAttribute('src');
  assert.match(avatarUrl, /^\/avatar\.png\?/);
  assert.ok((await sidebar.evaluate(el => el.style.backgroundImage)).includes(avatarUrl));
  assert.equal(writes.filter(x => x.path.endsWith('/me/avatar') && x.method === 'PUT').length, 1);

  await page.locator('#deleteAvatarBtn').click();
  await removal.waitFor();
  assert.equal(await removal.evaluate(el => el.matches(':modal')), true);
  await page.keyboard.press('Escape');
  await removal.waitFor({ state: 'hidden' });
  assert.equal(await avatar.isVisible(), true);
  await page.locator('#deleteAvatarBtn').click();
  await page.locator('#confirmDeleteAvatarBtn').click();
  await removal.waitFor({ state: 'hidden' });
  await avatar.waitFor({ state: 'hidden' });
  assert.equal(await sidebar.getAttribute('data-sidebar-avatar-image'), null);
  assert.equal(await sidebar.evaluate(el => el.style.backgroundImage), '');
  assert.equal(writes.filter(x => x.path.endsWith('/me/avatar') && x.method === 'DELETE').length, 1);
});


for (const theme of ['light', 'dark']) for (const [width, height] of [[1920, 1080], [1440, 1000], [1366, 768], [1024, 740]]) {
  test(`notification workspaces and overlay: ${theme}, ${width}x${height}`, async t => {
    let previous;
    for (const [template, role] of [['profile_notification_bot', 'agent'], ['profile_notification_bot', 'admin'], ['settings_notification_bot_basic', 'admin']]) {
      const { page } = await setup(t, template, theme, role);
      await page.setViewportSize({ width, height });
      const geometry = () => page.evaluate(() => {
        const box = selector => {
          const r = document.querySelector(selector).getBoundingClientRect();
          return { x: r.x, y: r.y, width: r.width, height: r.height, bottom: r.bottom };
        };
        return { panel: box('.notification-bot-basic-panel'), main: box('#appContent'), toolbar: box('.toolbar'),
          editor: box('[data-template-textarea]'), preview: box('[data-preview]') };
      });
      const initial = await geometry();
      assert.equal(height - initial.panel.bottom, 16, JSON.stringify(initial));
      assert.equal(initial.panel.y - initial.toolbar.bottom, 14);
      assert.equal(initial.panel.x - initial.main.x, 16);
      assert.equal(initial.editor.height, initial.preview.height);
      assert.ok(initial.editor.height >= 280 && initial.editor.height <= 360);
      if (width === 1920) {
        const save = await page.locator('.notification-actions button').boundingBox();
        assert.ok(save.y + save.height <= initial.panel.bottom, 'desktop save button fits inside the panel');
      }
      const normalized = { ...initial.panel, x: initial.panel.x - initial.main.x, width: initial.main.width - initial.panel.width };
      if (previous) assert.deepEqual(normalized, previous, 'all three pages have identical workspace insets and height');
      previous = normalized;
      if (template === 'settings_notification_bot_basic') {
        assert.equal(await page.locator('.notification-settings-toggle').count(), 0);
        if (process.env.PROFILE_V2_SCREENSHOT_DIR) await screenshot(page, `${theme}-basic-${width}x${height}`);
        continue;
      }
      const toggle = page.locator('.notification-settings-toggle'), popup = page.locator('#notificationReceivingSettings');
      assert.equal(await popup.isVisible(), false);
      await toggle.click();
      assert.equal(await popup.evaluate(el => el.matches(':popover-open')), true);
      const overlay = await popup.boundingBox(), anchor = await toggle.boundingBox();
      assert.ok(Math.abs(overlay.x - anchor.x) <= 1 && Math.abs(overlay.width - anchor.width) <= 1);
      assert.ok(overlay.y >= anchor.y + anchor.height);
      assert.deepEqual(await geometry(), initial, 'opening settings never moves editor or resizes panel');
      if (role === 'agent') assert.equal(await page.locator('[data-delivery-switch="new_assignment"]').isDisabled(), true);
      const delivery = page.locator(`[data-delivery-switch="${role === 'agent' ? 'new_message' : 'notify_internal_task_completion'}"]`);
      const checked = await delivery.isChecked();
      await delivery.click();
      assert.equal(await delivery.isChecked(), !checked);
      await toggle.click();
      assert.equal(await popup.isVisible(), false);
      await toggle.click();
      await page.keyboard.press('Escape');
      assert.equal(await popup.isVisible(), false);
      await toggle.click();
      await page.locator('.settings-title').click();
      assert.equal(await popup.isVisible(), false);
      assert.deepEqual(await geometry(), initial);
      const clear = await toggle.evaluate(el => {
        const r = el.getBoundingClientRect(); return document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2) === el.querySelector('span') || el.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2));
      });
      assert.equal(clear, true, 'closed overlay cannot intercept header clicks');
      assert.equal(await page.locator('[data-template-textarea]').evaluate(el => { const r = el.getBoundingClientRect(); return document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2) === el; }), true, 'closed overlay cannot intercept the editor');
      if (process.env.PROFILE_V2_SCREENSHOT_DIR) await screenshot(page, `${theme}-${role}-${width}x${height}`);
    }
  });
}
