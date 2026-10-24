// Real Contacts page/scripts; isolated API responses never write to the local DB.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });
async function setup(t, theme, role = 'admin') {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  t.after(() => page.close());
  const errors = [], queries = [], assets = [];
  page.on('pageerror', error => errors.push(error.message));
  const me = { id: 'admin-1', role, login: 'demo', full_name: 'Иван Петров', availability_status: 'online' };
  const items = [false, true].map((blocked, i) => ({ client_id: `client-${i}`, display_name: i ? 'Елена Волкова' : 'Мария Смирнова',
    telegram_display_name: i ? 'Елена' : 'Мария', telegram_username: i ? 'elena' : 'maria', telegram_user_id: `${100 + i}`,
    internal_number: '204', destination: 'Отдел Продаж', is_verified: !blocked, is_blocked: blocked, last_activity_at: '2026-10-24T08:00:00Z' }));
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    if (url.hostname !== 'desk.test') return route.abort();
    if (p.startsWith('/static/')) {
      assets.push(p + url.search);
      const file = path.resolve('api/app', p.slice(1));
      return route.fulfill({ status: fs.existsSync(file) ? 200 : 404, body: fs.existsSync(file) ? fs.readFileSync(file) : '',
        contentType: { '.css': 'text/css', '.js': 'text/javascript', '.ttf': 'font/ttf', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream' });
    }
    if (!p.startsWith('/api/')) return route.fulfill({ contentType: 'text/html', body: fs.readFileSync(`api/app/templates/${p === '/profile' ? 'profile' : 'management_users'}.html`, 'utf8') });
    let result = {};
    if (p.endsWith('/appearance-session')) result = { writer_id: '00000000-0000-4000-8000-000000000001', preferences: { theme_preference: theme } };
    else if (p.endsWith('/preferences')) result = { theme_preference: theme };
    else if (p === '/api/v1/auth/session') result = { real_user: me, effective_user: me, is_real_admin: role === 'admin', session_login_ts: 1, app_timezone: 'Europe/Moscow' };
    else if (p === '/api/v1/users/me' || p === '/api/v1/users/me/profile') result = me;
    else if (p === '/api/v1/users/agents' || p === '/api/v1/users/by-role') result = [me];
    else if (p.endsWith('/counts')) result = { active: 12, unassigned: 1, frozen: 0, closed: 595, special: 0, by_assignee: {} };
    else if (p === '/api/v1/management/users/client-card-view-config') result = null;
    else if (p === '/api/v1/management/users') {
      queries.push(Object.fromEntries(url.searchParams));
      if (url.searchParams.get('q') === 'error') return route.fulfill({ status: 500, body: 'Не удалось загрузить контакты' });
      const empty = url.searchParams.get('q') === 'missing';
      result = { items: empty ? [] : url.searchParams.has('blocked') ? [items[1]] : items,
        total: empty ? 0 : 101, limit: 50, offset: Number(url.searchParams.get('offset') || 0) };
    } else if (p.endsWith('/profile')) result = { contact_name: 'Мария', last_name: 'Смирнова', internal_number: '204', destination: 'Отдел Продаж', is_verified: true };
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(result) });
  });
  await page.goto('http://desk.test/management/users');
  await page.waitForFunction(theme => document.body.dataset.theme === theme && document.body.dataset.sidebarReady === '1', theme);
  if (role === 'admin') await page.waitForSelector('.management-user-row');
  else await page.waitForFunction(() => document.querySelector('#managementUsersStatus').textContent.includes('админа'));
  await page.addStyleTag({ content: '* {transition:none !important; animation:none !important}' });
  t.after(() => assert.deepEqual(errors, []));
  return { page, queries, assets };
}
async function contrast(page, selectors) {
  const checks = await page.evaluate(selectors => {
    const ctx = document.createElement('canvas').getContext('2d');
    const l = color => { ctx.fillStyle = color; ctx.fillRect(0, 0, 1, 1); return [...ctx.getImageData(0, 0, 1, 1).data].slice(0, 3).map(v => { v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; }).reduce((a, v, i) => a + v * [.2126, .7152, .0722][i], 0); };
    return selectors.map(selector => {
      const el = document.querySelector(selector), s = getComputedStyle(el);
      let node = el, bg;
      do { bg = getComputedStyle(node).backgroundColor; node = node.parentElement; } while (bg === 'rgba(0, 0, 0, 0)' && node);
      const a = l(s.color), b = l(bg);
      return { selector, ratio: (Math.max(a, b) + .05) / (Math.min(a, b) + .05) };
    });
  }, selectors);
  for (const check of checks) assert.ok(check.ratio >= 4.5, JSON.stringify(check));
}
async function toggleStyles(page, disabled) {
  await page.evaluate(disabled => {
    const sheet = [...document.styleSheets].find(s => s.href?.includes('/contacts-v2.css'));
    if (!sheet) throw new Error('Contacts stylesheet missing');
    sheet.disabled = disabled;
  }, disabled);
}
for (const theme of ['light', 'dark']) {
  test(`Contacts V2 list actions and contrast: ${theme}`, async t => {
    const { page, queries, assets } = await setup(t, theme);
    assert.ok(assets.includes('/static/contacts-v2.css?v=20261024-contacts-labels-v2'));
    assert.equal(await page.locator('.management-users-table').evaluate(el => getComputedStyle(el).borderRadius), '24px');
    await contrast(page, ['.management-user-primary', '.management-user-muted', '.management-user-row-blocked td', 'th', '#managementUsersSearch', '#managementUsersSearchBtn', '.management-users-filters .active', '#managementUsersPageSummary']);
    await page.locator('.management-user-row-blocked').hover();
    await contrast(page, ['.management-user-row-blocked td']);
    assert.equal(await page.locator('#managementUsersPrevBtn').isDisabled(), true);
    await Promise.all([page.waitForResponse(r => r.url().includes('/management/users?') && r.url().includes('offset=50')), page.locator('#managementUsersNextBtn').click()]);
    assert.equal(queries.at(-1).offset, '50');
    await page.locator('#managementUsersSearch').fill('Мария');
    await Promise.all([page.waitForResponse(r => r.url().includes('/management/users?') && r.url().includes('q=')), page.locator('#managementUsersSearch').press('Enter')]);
    assert.equal(queries.at(-1).q, 'Мария'); assert.equal(queries.at(-1).offset, '0');
    await Promise.all([page.waitForResponse(r => r.url().includes('blocked=true')), page.locator('[data-management-user-filter="blocked"]').click()]);
    assert.equal(await page.locator('.management-user-row-blocked').count(), 1);
    assert.match(page.url(), /view=blocked/);
    await page.locator('#managementUsersSearch').fill('missing');
    await Promise.all([page.waitForResponse(r => r.url().includes('q=missing')), page.locator('#managementUsersSearchBtn').click()]);
    assert.equal(await page.locator('#managementUsersEmpty').isVisible(), true);
    assert.equal(await page.locator('#managementUsersNextBtn').isDisabled(), true);
    await contrast(page, ['#managementUsersEmpty']);
    await page.locator('#managementUsersSearch').fill('error');
    await Promise.all([page.waitForResponse(r => r.status() === 500), page.locator('#managementUsersSearchBtn').click()]);
    await page.waitForFunction(() => document.querySelector('#managementUsersStatus').textContent.includes('Не удалось'));
    await contrast(page, ['#managementUsersStatus']);
  });
  test(`Contacts V2 scope, editor opening and AppShell: ${theme}`, async t => {
    const { page } = await setup(t, theme);
    await page.locator('.management-user-row').first().click();
    await page.waitForSelector('#clientModal:not(.hidden)');
    const snapshot = () => page.locator('.sidebar, .toolbar, #clientModal .modal-dialog, #clientModal input, #clientModal button').evaluateAll(nodes => nodes.map(el => {
      const s = getComputedStyle(el), r = el.getBoundingClientRect();
      return [s.background, s.color, s.border, s.font, s.padding, s.boxShadow, r.width, r.height];
    }));
    const enabled = await snapshot();
    await toggleStyles(page, true);
    assert.deepEqual(await snapshot(), enabled, 'shell and individual contact editor remain unchanged');
    await toggleStyles(page, false);
    await page.locator('#closeClientModalBtn').click();
    await page.evaluate(() => {
      const a = document.createElement('a'); a.id = 'test-profile-link'; a.href = '/profile'; a.textContent = 'Profile';
      document.querySelector('#appContent').appendChild(a);
    });
    await page.locator('#test-profile-link').click();
    await page.waitForSelector('#profileRoot:not(.hidden)');
    await page.goBack();
    await page.waitForSelector('.management-user-row');
    assert.equal(await page.locator('.management-users-table').evaluate(el => getComputedStyle(el).borderRadius), '24px');
    if (process.env.CONTACTS_V2_SCREENSHOTS) {
      fs.mkdirSync(process.env.CONTACTS_V2_SCREENSHOTS, { recursive: true });
      await page.screenshot({ path: path.join(process.env.CONTACTS_V2_SCREENSHOTS, `${theme}.png`) });
    }
  });
  test(`Contacts V2 style is role independent; agent access remains restricted: ${theme}`, async t => {
    const { page, queries } = await setup(t, theme, 'agent');
    assert.equal(queries.length, 0);
    assert.equal(await page.locator('.management-users-table').evaluate(el => getComputedStyle(el).borderRadius), '24px');
    await contrast(page, ['#managementUsersStatus']);
  });
}


for (const theme of ['light', 'dark']) for (const width of [1024, 1366, 1920]) {
  test(`Contacts search focus and centered pagination: ${theme}, ${width}`, async t => {
    const { page } = await setup(t, theme);
    await page.setViewportSize({ width, height: 1000 });
    await page.locator('#managementUsersSearch').focus();
    const focus = await page.locator('#managementUsersSearch').evaluate(el => {
      const r = el.getBoundingClientRect(), panel = el.closest('.management-users-table').getBoundingClientRect();
      const s = getComputedStyle(el), extent = parseFloat(s.outlineWidth) + parseFloat(s.outlineOffset);
      return { left: r.left - panel.left, top: r.top - panel.top - extent, outline: s.outlineStyle };
    });
    assert.equal(focus.outline, 'solid');
    assert.ok(focus.left >= 12, JSON.stringify(focus));
    assert.ok(focus.top >= 2, JSON.stringify(focus));
    const stable = () => page.locator('.management-users-table, .management-users-table thead').evaluateAll(nodes => nodes.map(el => {
      const r = el.getBoundingClientRect(); return [r.x, r.y, r.width, r.height];
    }));
    const textAlignment = await page.locator('.management-users-pagination .table-page-btn').evaluateAll(buttons => buttons.map(button => {
      const r = button.getBoundingClientRect(), range = document.createRange(); range.selectNodeContents(button);
      const text = range.getBoundingClientRect();
      return { x: Math.abs((r.left + r.right - text.left - text.right) / 2), y: Math.abs((r.top + r.bottom - text.top - text.bottom) / 2) };
    }));
    for (const delta of textAlignment) { assert.ok(delta.x <= 1, JSON.stringify(delta)); assert.ok(delta.y <= 2, JSON.stringify(delta)); }
    const before = await stable();
    const filters = await page.locator('.management-users-filters').boundingBox();
    const originalSearch = await page.addStyleTag({ content: '#appContent .management-users-search {margin-left:0 !important;translate:none !important}' });
    assert.deepEqual(await stable(), before, 'search inset preserves panel and table geometry');
    assert.deepEqual(await page.locator('.management-users-filters').boundingBox(), filters, 'filters stay in place');
    await originalSearch.evaluate(el => el.remove());
    for (const text of ['Показано 1–50 из 142', 'Показано 100001–100050 из 9999999999']) {
      await page.locator('#managementUsersPageSummary').evaluate((el, text) => { el.textContent = text; }, text);
      const delta = await page.locator('.management-users-pagination').evaluate(el => {
        const panel = el.closest('.management-users-table').getBoundingClientRect();
        const nav = el.querySelector('.table-pagination-nav').getBoundingClientRect();
        return Math.abs((panel.left + panel.right) / 2 - (nav.left + nav.right) / 2);
      });
      assert.ok(delta <= .5, `pagination center delta = ${delta}`);
    }
    assert.deepEqual(await stable(), before, 'counter text does not resize the table');
  });
}
