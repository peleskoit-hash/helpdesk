// Real Contacts page/scripts; isolated API responses never write to the local DB.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });
async function setup(t, theme, role = 'admin', initialColumns = null) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  t.after(() => page.close());
  const errors = [], queries = [], assets = [];
  page.on('pageerror', error => errors.push(error.message));
  const me = { id: 'admin-1', role, login: 'demo', full_name: 'Иван Петров', availability_status: 'online' };
  const items = [false, true].map((blocked, i) => ({ client_id: `client-${i}`, display_name: i ? 'Елена Волкова' : 'Мария Смирнова',
    telegram_display_name: i ? 'Елена' : 'Мария', telegram_username: i ? 'elena' : 'maria', telegram_user_id: `${100 + i}`,
    internal_number: '204', destination: 'Отдел Продаж', is_verified: !blocked, is_blocked: blocked, last_activity_at: '2026-10-24T08:00:00Z' }));
  const saved = [];
  let preferences = { theme_preference: theme, management_users_columns: initialColumns };
  const catalog = [
    ['telegram_display_name', 'Имя в TG', 'readonly'],
    ['telegram_username', 'TG @username', 'username'],
    ['custom_fio', 'ФИО', 'text'],
    ['internal_number', 'Внутренний номер', 'text'],
    ['destination', 'Отдел', 'text'],
    ['is_verified', 'Верификация', 'checkbox'],
    ['custom_office', 'Кабинет', 'text'],
    ['custom_location', 'Расположение рабочего места', 'text'],
    ['custom_office_other', 'Кабинет', 'text'],
    ['custom_department_note', 'Должность в подразделении', 'text'],
  ].map(([key, label, type]) => ({ key, label, type }));
  const config = { blocks: [{ key: 'profile', title: 'Profile', fields: [
    { key: 'contact_name', label: 'Name', field_type: 'text', is_system: true },
  ] }], rules: [] };
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    if (url.hostname !== 'desk.test') return route.abort();
    if (p.startsWith('/static/')) {
      assets.push(p + url.search);
      const file = path.resolve('api/app', p.slice(1));
      return route.fulfill({ status: fs.existsSync(file) ? 200 : 404, body: fs.existsSync(file) ? fs.readFileSync(file) : '',
        contentType: { '.css': 'text/css', '.js': 'text/javascript', '.ttf': 'font/ttf', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream' });
    }
    if (!p.startsWith('/api/')) return route.fulfill({ contentType: 'text/html', body: fs.readFileSync(`api/app/templates/${p === '/management' ? 'management' : 'management_users'}.html`, 'utf8') });
    let result = {};
    if (p.endsWith('/appearance-session')) result = { writer_id: '00000000-0000-4000-8000-000000000001', preferences: { theme_preference: theme } };
    else if (p.endsWith('/preferences')) {
      if (req.method() === 'PATCH') {
        const changes = req.postDataJSON();
        saved.push(changes);
        preferences = { ...preferences, ...changes };
      }
      result = preferences;
    }
    else if (p === '/api/v1/auth/session') result = { real_user: me, effective_user: me, is_real_admin: role === 'admin', session_login_ts: 1, app_timezone: 'Europe/Moscow' };
    else if (p === '/api/v1/users/me' || p === '/api/v1/users/me/profile') result = me;
    else if (p === '/api/v1/users/agents' || p === '/api/v1/users/by-role') result = [me];
    else if (p.endsWith('/counts')) result = { active: 12, unassigned: 1, frozen: 0, closed: 595, special: 0, by_assignee: {} };
    else if (p === '/api/v1/management/users/client-card-view-config') result = config;
    else if (p === '/api/v1/management/users') {
      queries.push(Object.fromEntries(url.searchParams));
      if (url.searchParams.get('q') === 'error') return route.fulfill({ status: 500, body: 'Не удалось загрузить контакты' });
      const empty = url.searchParams.get('q') === 'missing';
      const limit = Number(url.searchParams.get('limit'));
      const offset = Number(url.searchParams.get('offset'));
      const total = empty ? 0 : url.searchParams.has('blocked') ? 61 : 101;
      const rows = Array.from({ length: Math.max(0, Math.min(limit, total - offset)) }, (_, index) => {
        const item = url.searchParams.has('blocked') ? items[1] : items[index % 2];
        return { ...item, client_id: `client-${offset + index}`, telegram_chat_id: '9000',
          column_values: {
            custom_fio: index % 2 ? 'Волкова Елена' : 'Смирнова Мария',
            telegram_display_name: item.telegram_display_name, telegram_username: item.telegram_username,
            internal_number: item.internal_number, destination: item.destination, is_verified: item.is_verified,
            custom_office_other: 'Южный корпус, кабинет 350', custom_department_note: 'Старший специалист первой линии поддержки',
            custom_office: 'Северный корпус, кабинет 345', custom_location: 'Служба поддержки, 3 этаж, рабочее место номер 260' } };
      });
      result = { items: rows, columns: catalog, total, limit, offset };
    } else if (p.endsWith('/profile')) result = { contact_name: 'Мария', last_name: 'Смирнова', internal_number: '204', destination: 'Отдел Продаж', is_verified: true };
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(result) });
  });
  if (theme === 'dark' && role === 'admin') {
    await page.goto('http://desk.test/management');
    await page.evaluate(() => { window.contactsEntryProbe = true; });
    await page.locator('#appContent a[href="/management/users"]').click();
    assert.equal(await page.evaluate(() => window.contactsEntryProbe), true);
  } else {
    await page.goto('http://desk.test/management/users');
  }
  await page.waitForFunction(theme => document.body.dataset.theme === theme && document.body.dataset.sidebarReady === '1', theme);
  await page.waitForSelector('.management-user-row');
  await page.addStyleTag({ content: '* {transition:none !important; animation:none !important}' });
  t.after(() => assert.deepEqual(errors, []));
  return { page, queries, assets, saved };
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
    assert.ok(assets.includes('/static/contacts-v2.css?v=20261120-users-toggle-row-v1'));
    assert.ok(assets.includes('/static/management_users.js?v=20261118-scroll-reset-v1'));
    assert.ok(assets.includes('/static/management_user_columns.js?v=20261120-users-equal-width-v1'));
    assert.equal(await page.locator('.management-users-table').evaluate(el => getComputedStyle(el).borderRadius), '12px');
    await contrast(page, ['#managementUsersTable td', '.management-user-row-blocked td', 'th', '#managementUsersSearch', '#managementUsersSearchBtn', '.management-users-filters .active', '#managementUsersPageSummary']);
    await page.locator('.management-user-row-blocked').first().hover();
    await contrast(page, ['.management-user-row-blocked td']);
    assert.equal(await page.locator('#managementUsersPrevBtn').isDisabled(), true);
    await Promise.all([page.waitForResponse(r => r.url().includes('/management/users?') && r.url().includes('offset=50')), page.locator('#managementUsersNextBtn').click()]);
    assert.deepEqual(queries.at(-1), { limit: '50', offset: '50' });
    const current = page.locator('#managementUsersPages [aria-current="page"]');
    await page.waitForFunction(() => document.querySelector('#managementUsersPages [aria-current]').textContent === '2');
    assert.equal(await current.textContent(), '2');
    assert.equal(await page.locator('#managementUsersPageSummary').textContent(), 'Всего: 101');
    await page.locator('#managementUsersSearch').fill('Мария');
    await Promise.all([page.waitForResponse(r => r.url().includes('/management/users?') && r.url().includes('q=')), page.locator('#managementUsersSearch').press('Enter')]);
    assert.equal(queries.at(-1).q, 'Мария'); assert.equal(queries.at(-1).offset, '0');
    await Promise.all([page.waitForResponse(r => r.url().includes('blocked=true')), page.locator('[data-management-user-filter="blocked"]').click()]);
    await page.waitForFunction(() => document.querySelector('#managementUsersPageSummary').textContent === 'Всего: 61');
    assert.equal(await page.locator('.management-user-row-blocked').count(), 50);
    assert.equal(await page.locator('[data-management-user-filter="blocked"]').getAttribute('aria-pressed'), 'true');
    const matchList = (params) => r => {
      const url = new URL(r.url());
      return url.pathname === '/api/v1/management/users' &&
        Object.entries(params).every(([key, value]) => url.searchParams.get(key) === value);
    };
    await Promise.all([page.waitForResponse(matchList({ offset: '50', blocked: 'true', q: 'Мария' })),
      page.getByRole('button', { name: '2', exact: true }).click()]);
    await page.waitForFunction(() => document.querySelector('#managementUsersPages [aria-current]').textContent === '2');
    const clear = page.getByRole('button', { name: 'Очистить поиск' });
    await Promise.all([page.waitForResponse(matchList({ offset: '0', blocked: 'true', q: null })), clear.click()]);
    assert.equal(await clear.isVisible(), false);
    assert.equal(await page.locator('#managementUsersSearch').inputValue(), '');
    await page.waitForFunction(() => document.querySelector('#managementUsersPages [aria-current]').textContent === '1');
    assert.match(page.url(), /view=blocked/);
    await page.goBack();
    await page.waitForFunction(() => document.querySelector('#managementUsersPageSummary').textContent === 'Всего: 101');
    assert.equal(await page.locator('[data-management-user-filter="all"]').getAttribute('aria-pressed'), 'true');
    await page.goForward();
    await page.waitForFunction(() => document.querySelector('#managementUsersPageSummary').textContent === 'Всего: 61');
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
    const { page, queries } = await setup(t, theme);
    await page.locator('.management-user-row').first().click();
    await page.waitForSelector('#clientModal:not(.hidden)');
    const snapshot = () => page.locator('.sidebar, [data-shell-back], #themeToggleBtn, #clientModal .modal-dialog, #clientModal input, #clientModal button').evaluateAll(nodes => nodes.map(el => {
      const s = getComputedStyle(el), r = el.getBoundingClientRect();
      return [s.background, s.color, s.border, s.font, s.padding, s.boxShadow, r.width, r.height];
    }));
    const enabled = await snapshot();
    await toggleStyles(page, true);
    assert.deepEqual(await snapshot(), enabled, 'shell and individual contact editor remain unchanged');
    await toggleStyles(page, false);
    await page.locator('#closeClientModalBtn').click();
    await page.locator('#managementUsersSearch').fill('Мария');
    await Promise.all([
      page.waitForResponse(response => new URL(response.url()).searchParams.get('q') === 'Мария'),
      page.locator('#managementUsersSearchBtn').click(),
    ]);
    await page.waitForFunction(() => document.querySelector('#managementUsersPages [aria-current]')?.textContent === '1');
    await page.evaluate(() => { window.contactsSpaProbe = true; });
    await page.locator('[data-sidebar-user-menu-toggle]').click();
    await page.locator('[data-sidebar-user-menu-item="profile"]').click();
    await page.waitForSelector('#profileRoot:not(.hidden)');
    await page.locator('#closeProfileBtn').click();
    await page.waitForSelector('.management-user-row');
    assert.equal(await page.evaluate(() => window.contactsSpaProbe), true);
    assert.deepEqual(queries.at(-1), { limit: '50', offset: '0', q: 'Мария' });
    assert.equal(await page.locator('#managementUsersSearch').inputValue(), 'Мария');
    assert.equal(await page.locator('.management-users-table').evaluate(el => getComputedStyle(el).borderRadius), '12px');
    if (process.env.CONTACTS_V2_SCREENSHOTS) {
      fs.mkdirSync(process.env.CONTACTS_V2_SCREENSHOTS, { recursive: true });
      await page.screenshot({ path: path.join(process.env.CONTACTS_V2_SCREENSHOTS, `${theme}.png`) });
    }
  });
  test(`Contacts V2 style for agents: ${theme}`, async t => {
    const { page, queries } = await setup(t, theme, 'agent');
    assert.deepEqual(queries, [{ limit: "50", offset: "0" }]);
    assert.equal(await page.locator('.management-users-table').evaluate(el => getComputedStyle(el).borderRadius), '12px');
    await contrast(page, ['#managementUsersPageSummary']);
  });
}


for (const theme of ['light', 'dark']) for (const width of [1024, 1366, 1920]) {
  test(`Contacts search focus and centered pagination: ${theme}, ${width}`, async t => {
    const { page } = await setup(t, theme);
    await page.setViewportSize({ width, height: 1000 });
    await page.locator('#managementUsersSearch').focus();
    const focus = await page.locator('#managementUsersSearch').evaluate(el => {
      const r = el.getBoundingClientRect(), panel = el.closest('.toolbar').getBoundingClientRect();
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
    const filterButtons = page.locator('[data-management-user-filter]');
    const sizes = () => filterButtons.evaluateAll(nodes => nodes.map(el => [el.offsetWidth, el.offsetHeight]));
    const beforeSizes = await sizes();
    await filterButtons.last().click();
    assert.deepEqual(await sizes(), beforeSizes);
    assert.notEqual(await filterButtons.last().evaluate(el => getComputedStyle(el, '::before').content), 'none');
    await page.waitForFunction(() => document.querySelector('#managementUsersPageSummary').textContent === 'Всего: 61');
    const sticky = await page.locator('.management-users-table .table-scroll').evaluate(el => {
      const head = el.querySelector('thead');
      const top = head.getBoundingClientRect().top;
      el.scrollTop = 200;
      return [el.scrollTop, head.getBoundingClientRect().top - top];
    });
    assert.deepEqual(sticky, [200, 0]);
    const before = await stable();
    for (const text of ['Всего: 142', 'Всего: 9999999999']) {
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

for (const theme of ['light', 'dark']) test(`Contacts column order, persistence and auto width: ${theme}`, async t => {
  const { page, saved } = await setup(t, theme);
  const gear = page.getByRole('button', { name: 'Настройка списка пользователей' });
  const dialog = page.getByRole('dialog', { name: 'Настройка списка пользователей' });
  const headers = () => page.locator('#managementUsersHeader th').evaluateAll(nodes => nodes.map(el => el.dataset.columnKey));
  const widthOf = (key) => page.locator(`#managementUsersHeader th[data-column-key="${key}"]`).evaluate(el => el.offsetWidth);
  assert.deepEqual(await headers(), ['telegram_display_name', 'telegram_username', 'custom_fio', 'internal_number', 'destination', 'is_verified']);
  const wideName = await widthOf('custom_fio');
  const wideTelegram = await widthOf('telegram_display_name');

  await gear.click();
  const add = dialog.getByLabel('Добавить колонку');
  assert.equal(await dialog.getByRole('button', { name: 'Удалить: ФИО', exact: true }).count(), 0);
  const nameHandle = await dialog.getByRole('button', { name: 'Переместить: ФИО', exact: true }).boundingBox();
  const firstHandle = await dialog.getByRole('button', { name: 'Переместить: Имя в TG', exact: true }).boundingBox();
  await page.mouse.move(nameHandle.x + 8, nameHandle.y + 8);
  await page.mouse.down();
  await page.mouse.move(firstHandle.x + 8, firstHandle.y + 2, { steps: 6 });
  await page.mouse.up();
  const draft = () => dialog.locator('.columns-draft-row').evaluateAll(nodes => nodes.map(el => el.dataset.columnKey));
  assert.equal((await draft())[0], 'custom_fio');

  const options = () => add.locator('option').evaluateAll(nodes => nodes.slice(1).map(el => el.value));
  assert.deepEqual(await options(), ['custom_office', 'custom_location', 'custom_office_other', 'custom_department_note']);
  await add.selectOption('custom_office');
  assert.deepEqual(await options(), ['custom_location', 'custom_office_other', 'custom_department_note']);
  await dialog.getByRole('button', { name: 'Удалить: Кабинет', exact: true }).click();
  assert.ok((await options()).includes('custom_office'));
  for (const key of ['custom_office', 'custom_location', 'custom_department_note']) await add.selectOption(key);
  await dialog.getByRole('button', { name: 'Удалить: Верификация' }).click();
  assert.ok((await options()).includes('is_verified'));
  const expected = await draft();
  await dialog.getByRole('button', { name: 'Сохранить' }).click();
  await dialog.waitFor({ state: 'hidden' });
  assert.deepEqual(saved, [{ management_users_columns: expected }]);
  assert.deepEqual(await headers(), expected);
  assert.ok(await page.getByText('Северный корпус, кабинет 345', { exact: true }).first().isVisible());
  assert.ok(await page.getByText('Смирнова Мария', { exact: true }).first().isVisible());
  assert.equal(await page.getByText('9000', { exact: true }).count(), 0);

  await page.setViewportSize({ width: 1024, height: 1000 });
  await page.waitForFunction(() => document.querySelector('#managementUsersHeader [data-column-key="custom_fio"]').offsetWidth === 180);
  const dimensions = await page.locator('#managementUsersTable').evaluate(table => {
    const scroller = table.parentElement;
    return [scroller.scrollWidth, scroller.clientWidth];
  });
  assert.ok(dimensions[0] > dimensions[1]);
  assert.ok(wideName > await widthOf('custom_fio'));
  assert.equal(await widthOf('telegram_display_name'), wideTelegram);
  await page.evaluate(() => { window.contactsColumnsSpaProbe = true; });
  await page.locator('[data-sidebar-user-menu-toggle]').click();
  await page.locator('[data-sidebar-user-menu-item="profile"]').click();
  await page.waitForSelector('#profileRoot:not(.hidden)');
  await page.locator('#closeProfileBtn').click();
  await page.waitForFunction(order => [...document.querySelectorAll('#managementUsersHeader th')].map(el => el.dataset.columnKey).join() === order.join(), expected);
  assert.equal(await page.evaluate(() => window.contactsColumnsSpaProbe), true);
  await page.reload();
  await page.waitForFunction(order => [...document.querySelectorAll('#managementUsersHeader th')].map(el => el.dataset.columnKey).join() === order.join(), expected);
});

test('Contacts columns omit stale keys and restore mandatory name', async t => {
  const { page, saved } = await setup(t, 'light', 'agent', ['custom_missing', 'telegram_chat_id', 'internal_number', 'custom_fio']);
  await page.waitForFunction(() => document.querySelector('#managementUsersHeader th:last-child')?.dataset.columnKey === 'custom_fio');
  assert.deepEqual(await page.locator('#managementUsersHeader th').allTextContents(), ['Внутренний номер', 'ФИО']);
  await page.getByRole('button', { name: 'Настройка списка пользователей' }).click();
  const dialog = page.getByRole('dialog', { name: 'Настройка списка пользователей' });
  await dialog.getByLabel('Добавить колонку').selectOption('custom_office');
  await dialog.getByRole('button', { name: 'Закрыть' }).click();
  assert.deepEqual(await page.locator('#managementUsersHeader th').allTextContents(), ['Внутренний номер', 'ФИО']);
  assert.deepEqual(saved, []);
});


const equalWidthMarker = '__equal_width__';
async function assertEqualUserWidths(page, count) {
  await page.waitForFunction(count => {
    const table = document.querySelector('#managementUsersTable');
    const cells = [...table.tHead.rows[0].cells];
    const widths = cells.map(cell => cell.getBoundingClientRect().width);
    const scroll = table.parentElement, style = getComputedStyle(scroll);
    const available = scroll.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
    return widths.length === count && Math.max(...widths) - Math.min(...widths) < 1
      && Math.abs(table.getBoundingClientRect().width - available) < 2;
  }, count);
}

for (const theme of ['light', 'dark']) test(`Contacts equal widths save, restore, cancel and standard mode: ${theme}`, async t => {
  const original = ['custom_fio', 'internal_number', 'telegram_display_name'];
  const { page, saved } = await setup(t, theme, 'admin', original);
  const gear = page.getByRole('button', { name: 'Настройка списка пользователей' });
  const dialog = page.getByRole('dialog', { name: 'Настройка списка пользователей' });
  const checkbox = dialog.getByRole('checkbox', { name: 'Одинаковая ширина столбцов' });
  const widths = () => page.locator('#managementUsersHeader th').evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().width));
  await page.waitForFunction(() => document.querySelector('#managementUsersHeader th')?.dataset.columnKey === 'custom_fio');
  const standard = await widths();
  assert.ok(standard[0] > standard[1]);
  await gear.click();
  const alignment = await checkbox.evaluate(input => {
    const label = input.closest('label'), text = label.querySelector('span');
    const box = input.getBoundingClientRect(), caption = text.getBoundingClientRect();
    return { left: box.right <= caption.left, offset: Math.abs((box.top + box.height / 2) - (caption.top + caption.height / 2)),
      lines: caption.height / parseFloat(getComputedStyle(text).lineHeight) };
  });
  assert.equal(alignment.left, true);
  assert.ok(alignment.offset < 1, JSON.stringify(alignment));
  assert.ok(alignment.lines <= 1.1, JSON.stringify(alignment));
  assert.equal(await checkbox.isChecked(), false, 'legacy settings default to standard mode');
  await checkbox.check();
  await dialog.getByLabel('Добавить колонку').selectOption('destination');
  await dialog.getByRole('button', { name: 'Закрыть' }).click();
  assert.deepEqual(await widths(), standard, 'cancel keeps widths and columns');
  assert.deepEqual(saved, []);
  await gear.click();
  assert.equal(await checkbox.isChecked(), false);
  assert.equal(await dialog.locator('.columns-draft-row').count(), 3);
  await checkbox.check();
  await dialog.getByLabel('Добавить колонку').selectOption('destination');
  await dialog.getByRole('button', { name: 'Сохранить' }).click();
  await dialog.waitFor({ state: 'hidden' });
  const expected = [...original, 'destination', equalWidthMarker];
  assert.deepEqual(saved, [{ management_users_columns: expected }]);
  await assertEqualUserWidths(page, 4);
  await page.reload();
  await page.waitForSelector('.management-user-row');
  await assertEqualUserWidths(page, 4);
  await gear.click();
  assert.equal(await checkbox.isChecked(), true);
  assert.equal(await dialog.locator('.columns-draft-row').count(), 4, 'marker is not an editable column');
  assert.equal(await dialog.getByLabel('Добавить колонку').locator(`option[value="${equalWidthMarker}"]`).count(), 0);
  await checkbox.uncheck();
  await dialog.getByRole('button', { name: 'Закрыть' }).click();
  await assertEqualUserWidths(page, 4);
  await gear.click();
  assert.equal(await checkbox.isChecked(), true, 'cancel preserves saved equal mode');
  await checkbox.uncheck();
  await dialog.getByRole('button', { name: 'Удалить: Отдел', exact: true }).click();
  await dialog.getByRole('button', { name: 'Сохранить' }).click();
  await dialog.waitFor({ state: 'hidden' });
  assert.deepEqual(saved.at(-1), { management_users_columns: original });
  assert.deepEqual(await widths(), standard, 'turning equal mode off restores the original algorithm');
});

for (const count of [1, 3, 6, 10]) test(`Contacts equal widths resize with ${count} columns`, async t => {
  const keys = ['custom_fio', 'internal_number', 'telegram_display_name', 'telegram_username', 'destination',
    'is_verified', 'custom_office', 'custom_location', 'custom_office_other', 'custom_department_note'].slice(0, count);
  const { page } = await setup(t, 'light', 'agent', [...keys, equalWidthMarker]);
  for (const width of [700, 1024, 1920]) {
    await page.setViewportSize({ width, height: 1000 });
    await assertEqualUserWidths(page, count);
  }
  await page.locator('#managementUsersTable').evaluate(table => { table.parentElement.style.width = '650px'; });
  await assertEqualUserWidths(page, count);
});
