// Run with PLAYWRIGHT_MODULE pointing at an installed Playwright, if not local.
// Real templates, styles and scripts; synthetic API keeps local databases untouched.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });

for (const theme of ['light', 'dark']) {
  test(`shell styles do not affect matching classes in settings content: ${theme}`, async t => {
    const page = await browser.newPage({ viewport: { width: 1366, height: 768 } });
    t.after(() => page.close());
    await page.setContent(fs.readFileSync('api/app/templates/settings_client_card.html', 'utf8')
      .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '').replace(/<link\b[^>]*>/gi, ''));
    await page.addStyleTag({ content: fs.readFileSync('api/app/static/styles.css', 'utf8').replace(/^@import[^;]+;/, '') });
    await page.evaluate(theme => {
      document.body.dataset.theme = theme;
      document.body.dataset.sidebarReady = '1';
      const content = document.querySelector('#appContent .content');
      const probe = document.createElement('div');
      probe.id = 'shellScopeProbe';
      // Keep available content width constant while shared sidebar geometry changes.
      probe.style.width = '600px';
      for (const cls of ['tab-btn', 'tab-btn active', 'settings-link', 'settings-link active', 'admin-agent-item',
        'admin-agent-item active', 'tab-btn-count', 'tab-list', 'sidebar-brand', 'sidebar-title', 'admin-agent-filter',
        'admin-agent-list', 'admin-agent-header', 'admin-agent-header pressed', 'admin-agent-count-active',
        'admin-agent-count-divider', 'admin-agent-count-frozen', 'admin-agent-count-divider-frozen',
        'sidebar-user-name', 'sidebar-user-menu-item', 'sidebar-user-menu-item-danger', 'sidebar-bottom',
        'agent-avatar', 'agent-status-btn', 'agent-status-btn active', 'display-settings', 'bulk-admin-actions']) {
        const el = document.createElement('div');
        el.className = cls;
        el.textContent = 'Контроль стилей';
        probe.appendChild(el);
      }
      content.appendChild(probe);
    }, theme);
    const appearance = () => page.locator('#shellScopeProbe > *').evaluateAll(nodes => nodes.map(el => {
      const style = getComputedStyle(el);
      return Object.fromEntries(['color', 'background', 'border', 'borderRadius', 'padding', 'margin',
        'font', 'boxShadow', 'backdropFilter', 'width', 'height', 'minWidth', 'maxHeight', 'gap', 'overflowY']
        .map(key => [key, style[key]]));
    }));
    const before = await appearance();
    await page.addStyleTag({ content: fs.readFileSync('api/app/static/desk-v2.css', 'utf8') });
    assert.deepEqual(await appearance(), before);
  });
}
const stamp = '2026-10-23T10:20:00Z';
const user = { id: 'agent-1', role: 'admin', login: 'demo', full_name: 'Александр Сергеевич Иванов', availability_status: 'online' };
const columns = ['subject', 'requester', 'assignee', 'department', 'type', 'location', 'age', 'created_at', 'last_reply'];
const subjects = ['Не подключается рабочая почта', 'Настройка доступа к Bitrix', 'Проверить подключение принтера', 'Новый сотрудник: доступы и оборудование'];
const rows = Array.from({ length: 28 }, (_, i) => ({ id: `demo-${i}`, subject: `${subjects[i % 4]} · ${1200 + i}`,
  workflow_type: i % 3 ? 'task' : 'ticket', status: 'new', assignee_id: user.id, assignee_name: user.full_name,
  requester_display_name: 'Мария Смирнова', requester_department: 'Отдел Продаж', requester_location_type: 'Офисный',
  created_at: stamp, last_reply_at: stamp, channel: 'telegram', seen_by_agent: i % 2 === 0,
  task_kind: i % 4 === 1 ? 'group_child' : 'normal', is_internal_task: i % 4 === 2,
}));
const card = { ...rows[0], client_name: 'Мария Смирнова', instruction_text: 'Проверьте настройки почтового клиента.\n'.repeat(80),
  client_profile: { last_name: 'Смирнова', contact_name: 'Мария', telegram_display_name: 'Мария Смирнова',
    telegram_username: 'maria_demo', destination: 'Отдел Продаж', sales_group: 'группа Власова', location_type: 'Офисный',
    internal_number: '204', bitrix_login: 'm.smirnova', bitrix_id: '1204', is_verified: true },
  messages: Array.from({ length: 32 }, (_, i) => ({ id: `m-${i}`, message_type: ['client', 'agent', 'internal', 'system'][i % 4],
    body: ['Добрый день! Не получается войти в рабочую почту. После ввода пароля появляется сообщение об ошибке подключения.',
      'Мария, добрый день! Проверю настройки подключения. Подскажите, пожалуйста, работает ли почта через браузер?',
      'Проверить параметры сервера и доступ из офисной сети. Ожидаем ответ клиента.',
      'Заявка назначена ответственному: Александр Иванов'][i % 4], author_name: user.full_name, created_at: stamp, attachments: [] })),
};

async function setup(t, { width, height, theme, pageName, role = 'admin', workflowType = 'ticket' }) {
  const page = await browser.newPage({ viewport: { width, height } });
  t.after(() => page.close());
  const errors = [], requests = [];
  page.on('pageerror', error => errors.push(error.message));
  const me = { ...user, role };
  const preferences = { theme_preference: theme, color_enabled: true, task_panel_enabled: true,
    ticket_list_columns: columns.map(key => ({ key, width: key === 'subject' ? 340 : 160 })) };
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    if (url.hostname !== 'desk.test') return route.abort();
    if (p.startsWith('/static/')) {
      const file = path.resolve('api/app', p.slice(1));
      if (!fs.existsSync(file)) return route.fulfill({ status: 404, body: '' });
      const ext = path.extname(file);
      return route.fulfill({ body: fs.readFileSync(file), contentType: { '.css': 'text/css', '.js': 'text/javascript', '.svg': 'image/svg+xml', '.ttf': 'font/ttf' }[ext] || 'application/octet-stream' });
    }
    if (!p.startsWith('/api/')) {
      const template = p === '/tickets' ? 'tickets' : p.startsWith('/tickets/') ? 'ticket' : 'profile';
      return route.fulfill({ contentType: 'text/html', body: fs.readFileSync(`api/app/templates/${template}.html`, 'utf8') });
    }
    requests.push({ path: p, method: req.method(), query: url.search });
    let result = {};
    if (p.endsWith('/preferences')) {
      if (req.method() === 'PATCH') Object.assign(preferences, req.postDataJSON());
      result = preferences;
    } else if (p === '/api/v1/auth/session') result = { real_user: me, effective_user: me, is_real_admin: role === 'admin', session_login_ts: 1, app_timezone: 'Europe/Moscow' };
    else if (p === '/api/v1/users/me' || p === '/api/v1/users/me/profile') result = me;
    else if (p === '/api/v1/users/agents' || p === '/api/v1/users/by-role') result = [me, { ...me, id: 'agent-2', full_name: 'Елена Александровна Волкова' }];
    else if (p.endsWith('/counts')) result = { active: 28, unassigned: 3, frozen: 4, closed: 158, special: 1,
      by_assignee: { 'agent-1': { active: 123, frozen: 12 }, 'agent-2': { active: 3, frozen: 456 } } };
    else if (p.endsWith('/task-panel')) result = rows.slice(0, 10);
    else if (/tickets\/(active|closed|frozen|special|unassigned)$/.test(p)) result = { items: rows, total: 56, pages: 2, page: Number(url.searchParams.get('page') || 1), limit: 28 };
    else if (p.endsWith('/client-card-view-config')) result = null;
    else if (p.endsWith('/problem-menu')) result = { rows: [{ buttons: [{ id: 'p1', kind: 'submenu', label: 'Почта', children: [{ buttons: [{ id: 'p2', label: 'Доступ к почте', kind: 'final' }] }] }] }] };
    else if (p === '/api/v1/tickets/demo-0') result = { ...card, workflow_type: workflowType };
    else if (p.endsWith('/client-profile')) result = card.client_profile;
    else if (p === '/api/v1/quick-replies/search') result = { items: [] };
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(result) });
  });
  await page.goto(`http://desk.test/${pageName === 'ticket' ? 'tickets/demo-0' : 'tickets'}`);
  await page.waitForFunction(() => document.body.dataset.sidebarReady === '1');
  await page.waitForSelector(pageName === 'ticket' ? '.message' : '.ticket-row');
  await page.evaluate(() => document.fonts.ready);
  assert.equal(await page.locator('#errorModal').isVisible(), false);
  t.after(() => assert.deepEqual(errors, []));
  return { page, requests };
}

async function geometry(page) {
  return page.evaluate(() => {
    const box = selector => {
      const el = document.querySelector(selector), r = el.getBoundingClientRect();
      return { x: r.x, y: r.y, width: r.width, height: r.height, bottom: r.bottom };
    };
    return { sidebar: box('.sidebar'), toolbar: box('.toolbar'), content: box('.content'),
      overflow: document.documentElement.scrollWidth > innerWidth,
      font: getComputedStyle(document.querySelector('#appContent')).fontFamily,
      background: getComputedStyle(document.querySelector('.panel')).backgroundColor,
      radius: getComputedStyle(document.querySelector('.panel')).borderRadius };
  });
}

async function modalVisible(page, selector) {
  await page.locator(selector).waitFor({ state: 'visible' });
  const r = await page.locator(`${selector} .modal-dialog`).boundingBox(), vp = page.viewportSize();
  assert.ok(r.x >= 0 && r.y >= 0 && r.x + r.width <= vp.width + 1 && r.y + r.height <= vp.height + 1, JSON.stringify(r));
}

for (const [width, height] of [[1366, 768], [1920, 1080]]) {
  for (const theme of ['light', 'dark']) {
    for (const pageName of ['ticket', 'tickets']) {
      test(`${pageName}: ${theme}, ${width}px — real assets, scrolling and controls`, async t => {
        const { page, requests } = await setup(t, { width, height, theme, pageName });
        const before = await geometry(page);
        assert.equal(before.overflow, false);
        assert.match(before.font, /Manrope/);
        assert.equal(before.radius, '24px');
        assert.equal(before.background, theme === 'dark' ? 'rgb(32, 37, 53)' : 'rgb(255, 254, 253)');
        assert.ok(before.content.bottom <= height + 1);
        const sidebarChecks = await page.locator('.sidebar').evaluate(sidebar => ({
          width: sidebar.getBoundingClientRect().width,
          overflow: [sidebar, ...sidebar.querySelectorAll('.admin-agent-filter, .admin-agent-list, .admin-agent-item, .admin-agent-name')]
            .some(el => el.scrollWidth > el.clientWidth + 1),
        }));
        assert.ok(sidebarChecks.width >= 246 && sidebarChecks.width <= 420);
        assert.equal(sidebarChecks.overflow, false);
        const indicators = await page.locator('.admin-agent-counts').evaluateAll(nodes => nodes.map(el => ({
          dividers: [...el.querySelectorAll('.admin-agent-count-divider')].map(node => {
            const r = node.getBoundingClientRect(), s = getComputedStyle(node);
            return { x: r.x, y: r.y, height: r.height, width: r.width, color: s.color };
          }),
          centers: [...el.children].map(node => { const r = node.getBoundingClientRect(); return r.y + r.height / 2; }),
          frozenColor: getComputedStyle(el.querySelector('.admin-agent-count-frozen')).color,
          zones: [...el.querySelectorAll('.admin-agent-count')].map(node => ({ width: node.getBoundingClientRect().width, align: getComputedStyle(node).textAlign })),
          gaps: [el.children[1].getBoundingClientRect().left - el.children[0].getBoundingClientRect().right,
            el.children[3].getBoundingClientRect().left - el.children[2].getBoundingClientRect().right],
        })));
        for (const indicator of indicators) {
          assert.equal(indicator.dividers.length, 2);
          assert.ok(indicator.dividers.every(bar => bar.width === 2 && bar.height === 16));
          assert.ok(Math.max(...indicator.centers) - Math.min(...indicator.centers) < 1);
          assert.equal(indicator.dividers[1].color, theme === 'dark' ? 'rgb(142, 203, 255)' : 'rgb(22, 103, 200)');
          assert.equal(indicator.frozenColor, indicator.dividers[1].color);
          assert.deepEqual(indicator.zones, [{ width: 28, align: 'right' }, { width: 28, align: 'left' }]);
          assert.equal(indicator.dividers[1].x - indicator.dividers[0].x, 6);
          assert.deepEqual(indicator.gaps, [4, 4]);
        }
        assert.deepEqual(indicators[0].dividers.map(bar => bar.x), indicators[1].dividers.map(bar => bar.x));
        assert.equal(await page.evaluate(() => document.fonts.check('15px Manrope', 'Заявка переписка')), true);
        await page.locator('.agent-avatar').click();
        await page.locator('.sidebar-user-menu').waitFor({ state: 'visible' });
        await page.keyboard.press('Escape');
        assert.equal(await page.locator('.sidebar-user-menu').isVisible(), false);
        if (pageName === 'ticket') {
          assert.equal(await page.locator('.message').count(), 32);
          assert.equal(await page.locator('.message.client').first().evaluate(el => getComputedStyle(el).fontSize), '18px');
          const internal = page.locator('.message.internal').first();
          assert.equal(await internal.evaluate(el => getComputedStyle(el).borderTopStyle), 'dashed');
          assert.equal(await internal.evaluate(el => getComputedStyle(el, '::before').content), '"Комментарий"');
          const system = await page.locator('.message.system').first().evaluate(el => {
            const s = getComputedStyle(el);
            return { color: s.color, background: s.backgroundColor, border: s.borderTopStyle, font: s.fontSize };
          });
          assert.equal(system.border, 'solid');
          assert.equal(system.font, '15px');
          assert.notEqual(system.background, 'rgba(0, 0, 0, 0)');
          assert.equal(system.color, theme === 'dark' ? 'rgb(237, 234, 245)' : 'rgb(48, 44, 64)');
          assert.equal(await page.locator('.message.agent').first().evaluate(el => getComputedStyle(el).backgroundColor), theme === 'dark' ? 'rgb(42, 53, 78)' : 'rgb(234, 240, 250)');
          const badgeColors = await page.evaluate(() => {
            const wrap = document.createElement('div');
            document.querySelector('#appContent').appendChild(wrap);
            const colors = ['type-ticket', 'type-task', 'status-special', 'status-unassigned', 'status-active', 'status-closed', 'frozen'].map(kind => {
              const badge = document.createElement('span');
              badge.className = `ticket-status-badge ticket-badge-${kind}`;
              wrap.appendChild(badge);
              return getComputedStyle(badge).backgroundColor;
            });
            wrap.remove(); return colors;
          });
          assert.equal(new Set(badgeColors).size, 7);
          assert.equal(badgeColors.at(-1), theme === 'dark' ? 'rgb(42, 53, 78)' : 'rgb(234, 240, 250)');
          assert.ok((await page.locator('.ticket-client-side > .card-head').boundingBox()).height <= 56);
          const headline = await page.locator('.ticket-head-title-wrap').evaluate(el => {
            const title = el.querySelector('.ticket-head-title').getBoundingClientRect();
            const badges = el.querySelector('.ticket-status-badges').getBoundingClientRect();
            return { direction: getComputedStyle(el).flexDirection, titleRight: title.right, badgesLeft: badges.left,
              titleCenter: title.y + title.height / 2, badgesCenter: badges.y + badges.height / 2 };
          });
          assert.equal(headline.direction, 'row');
          assert.ok(headline.badgesLeft >= headline.titleRight);
          assert.ok(Math.abs(headline.titleCenter - headline.badgesCenter) < 1);
          assert.ok((await page.locator('.ticket-card > .ticket-card-wrap > .card-head').boundingBox()).height <= 102);
          assert.equal(await page.locator('.toolbar #instructionBtn').count(), 1);
          const composer = await page.locator('.card-actions').boundingBox();
          assert.ok(composer.y + composer.height <= height);
          await page.locator('#messages').evaluate(el => { el.scrollTop = 0; });
          assert.equal(await page.locator('#messages').evaluate(el => el.scrollTop), 0);
          assert.deepEqual(await page.locator('.card-actions').boundingBox(), composer);
          for (const selector of ['#clientDynamicCard', '.ticket-control-wrap']) {
            const scrolled = await page.locator(selector).evaluate(el => { el.scrollTop = el.scrollHeight; return el.scrollHeight <= el.clientHeight || el.scrollTop > 0; });
            assert.ok(scrolled, selector);
          }
          await page.locator('#editClientBtn').click();
          await page.locator('#fContactName').waitFor({ state: 'visible' });
          assert.equal(await page.locator('#fContactName').inputValue(), 'Мария');
          await page.locator('#closeClientModalBtn').click();
          await page.locator('#instructionBtn').click();
          await modalVisible(page, '#instructionModal');
          await page.locator('#closeInstructionModalBtn').click();
          await page.locator('#wrongProblemBtn').click();
          await modalVisible(page, '#wrongProblemModal');
          await page.locator('.wrong-problem-item.submenu').click();
          assert.equal(await page.locator('.wrong-problem-children .wrong-problem-item').isVisible(), true);
          await page.locator('#closeWrongProblemModalBtn').click();
          await page.locator('#adminDeleteBtn').click();
          await modalVisible(page, '#adminActionModal');
          await page.locator('#cancelAdminActionBtn').click();
          assert.equal(requests.some(r => r.method === 'DELETE'), false);
        } else {
          assert.equal(await page.locator('th[data-column-key]').count(), 9);
          assert.equal(await page.locator('.ticket-row').count(), 28);
          const selectAll = page.locator('#selectAllTicketsBtn');
          const checkAlignment = await selectAll.evaluate(el => {
            const rect = el.getBoundingClientRect();
            const range = document.createRange();
            range.selectNodeContents(el);
            const glyph = range.getBoundingClientRect();
            return { padding: getComputedStyle(el).padding,
              inside: glyph.left >= rect.left && glyph.right <= rect.right && glyph.top >= rect.top && glyph.bottom <= rect.bottom,
              horizontalOffset: Math.abs((glyph.left + glyph.right - rect.left - rect.right) / 2) };
          });
          assert.equal(checkAlignment.padding, '0px');
          assert.equal(checkAlignment.inside, true);
          assert.ok(checkAlignment.horizontalOffset < 1);
          await selectAll.click();
          assert.equal(await page.locator('.ticket-check:checked').count(), 28);
          await selectAll.click();
          assert.equal(await page.locator('.ticket-check:checked').count(), 0);
          const rowHeights = await page.locator('.ticket-row').evaluateAll(rows => rows.map(row => row.getBoundingClientRect().height));
          assert.ok(rowHeights.every(height => height >= 58 && height <= 62), JSON.stringify(rowHeights));
          assert.equal(await page.locator('.ticket-row td[data-column-key="requester"]').first().evaluate(el => getComputedStyle(el).fontSize), '16px');
          await page.locator('.table-scroll').evaluate(el => { el.scrollLeft = el.scrollWidth; el.scrollTop = el.scrollHeight; });
          assert.ok(await page.locator('.table-scroll').evaluate(el => el.scrollLeft > 0 && el.scrollTop > 0));
          await page.locator('.table-scroll').evaluate(el => { el.scrollLeft = 0; el.scrollTop = 0; });
          await page.locator('#columnsSettingsBtn').click();
          await modalVisible(page, '#columnsModal');
          assert.equal(await page.locator('.columns-draft-row').count(), 9);
          await page.locator('#closeColumnsModalBtn').click();
          await page.locator('.ticket-check').first().check();
          await page.locator('#bulkAssignBtn').click();
          await modalVisible(page, '#assignModal');
          await page.locator('#closeAssignModalBtn').click();
          await page.locator('.ticket-check').first().uncheck();
          await page.locator('.table-sort-btn[data-sort-key="subject"]').click();
          assert.equal(await page.evaluate(() => localStorage.getItem('hd_table_sort_key')), 'subject');
          await page.locator('.table-sort-btn[data-sort-key="subject"]').click();
          assert.equal(await page.evaluate(() => localStorage.getItem('hd_table_sort_dir')), 'desc');
          await page.locator('#colorEnabledInput').uncheck();
          assert.equal(await page.locator('tr.ticket-row').first().evaluate(el => getComputedStyle(el).backgroundImage), 'none');
          await page.locator('#colorEnabledInput').check();
          await page.locator('#nextPageBtn').click();
          await page.waitForURL(/page=2/);
          await page.locator('#searchInput').fill('почта');
          await page.locator('#searchInput').press('Enter');
          await page.waitForURL(/q=/);
          assert.ok(requests.some(r => /query=/.test(r.query)));
        }
        // Theme geometry must be identical, including header wrapping.
        await page.locator('#themeToggleBtn').click();
        await page.waitForFunction(theme => document.body.dataset.theme !== theme, theme);
        const after = await geometry(page);
        for (const key of ['sidebar', 'toolbar', 'content']) assert.deepEqual(after[key], before[key]);
        await page.locator('#themeToggleBtn').click();
        await page.waitForFunction(theme => document.body.dataset.theme === theme, theme);
        if (process.env.DESK_V2_SCREENSHOTS) {
          await page.evaluate(() => {
            for (const selector of ['.sidebar', '#clientDynamicCard', '.ticket-control-wrap', '#messages', '.table-scroll']) {
              const el = document.querySelector(selector);
              if (el) { el.scrollTop = 0; el.scrollLeft = 0; }
            }
          });
          fs.mkdirSync(process.env.DESK_V2_SCREENSHOTS, { recursive: true });
          await page.screenshot({ path: path.join(process.env.DESK_V2_SCREENSHOTS, `${pageName}-${theme}-${width}.png`) });
          if (pageName === 'ticket') {
            await page.locator('#messages').evaluate(el => {
              el.scrollTop += el.querySelector('.message.internal').getBoundingClientRect().top - el.getBoundingClientRect().top - 16;
            });
            await page.screenshot({ path: path.join(process.env.DESK_V2_SCREENSHOTS, `messages-${theme}-${width}.png`) });
          }
        }
      });
    }
  }
}

for (const width of [1366, 1920]) {
  for (const theme of ['light', 'dark']) {
    for (const pageName of ['ticket', 'tickets']) {
      test(`sidebar adapts only to a visible agent block: ${pageName}, ${theme}, ${width}`, async t => {
        const { page } = await setup(t, { width, height: 900, theme, pageName, role: 'agent' });
        assert.equal((await page.locator('.sidebar').boundingBox()).width, 246);
        const dimensions = () => page.locator('.sidebar').evaluate(sidebar => ({
          width: sidebar.getBoundingClientRect().width,
          overflow: [sidebar, ...sidebar.querySelectorAll('#adminAgentFilter, .admin-agent-list, .admin-agent-item, .admin-agent-name')]
            .some(el => el.scrollWidth > el.clientWidth + 1),
        }));
        // Simulate the same DOM block that Sidebar renders after a role/session refresh.
        await page.evaluate(() => {
          const filter = document.querySelector('#adminAgentFilter');
          filter.classList.remove('hidden');
          const list = filter.querySelector('.admin-agent-list');
          const item = document.createElement('button');
          item.className = 'admin-agent-item';
          item.innerHTML = '<span class="admin-agent-name">Александр Иванов</span><span class="admin-agent-counts"><span class="admin-agent-count admin-agent-count-active">123</span><span class="admin-agent-count-divider">|</span><span class="admin-agent-count-divider admin-agent-count-divider-frozen">|</span><span class="admin-agent-count admin-agent-count-frozen">12</span></span>';
          list.replaceChildren(item);
        });
        const short = await dimensions();
        await page.locator('.admin-agent-name').evaluate(el => { el.textContent = 'Александра Константиновна Рождественская'; });
        const long = await dimensions();
        assert.ok(long.width > short.width, `${JSON.stringify(short)} → ${JSON.stringify(long)}`);
        assert.ok(long.width <= 420);
        assert.equal(long.overflow, false);
        const counters = await page.locator('.admin-agent-counts > *').evaluateAll(nodes => nodes.every(el => el.scrollWidth <= el.clientWidth + 1));
        assert.equal(counters, true);
        await page.locator('.admin-agent-name').evaluate(el => { el.textContent = 'ОченьДлинноеСоставноеИмяБезПробелов'.repeat(3); });
        assert.equal((await dimensions()).overflow, false);
        await page.locator('#adminAgentFilter').evaluate(el => el.classList.add('hidden'));
        assert.equal((await dimensions()).width, 246);
        assert.equal((await geometry(page)).overflow, false);
      });
    }
  }
}

test('AppShell list → card → list preserves shell and new stylesheet', async t => {
  const { page } = await setup(t, { width: 1366, height: 768, theme: 'dark', pageName: 'tickets' });
  await page.evaluate(() => { window.originalSidebar = document.querySelector('.sidebar'); });
  await page.locator('.ticket-row-link').first().click();
  await page.waitForSelector('.message');
  assert.equal(await page.evaluate(() => window.originalSidebar === document.querySelector('.sidebar')), true);
  assert.equal((await geometry(page)).radius, '24px');
  await page.locator('#backToListBtn').click();
  await page.waitForSelector('.ticket-row');
  assert.equal(await page.evaluate(() => window.originalSidebar === document.querySelector('.sidebar')), true);
  assert.equal((await geometry(page)).radius, '24px');
});

for (const width of [1366, 1920]) {
  for (const theme of ['light', 'dark']) {
    test(`task close button keeps ready and disabled states: ${theme}, ${width}`, async t => {
      const { page } = await setup(t, { width, height: width === 1366 ? 768 : 1080, theme, pageName: 'ticket', workflowType: 'task' });
      const close = page.locator('#closeBtn');
      assert.equal(await close.textContent(), 'Закрыть задачу');
      assert.equal(await close.isEnabled(), true);
      assert.equal(await close.evaluate(el => el.classList.contains('close-action-ready')), true);
      const ready = await close.evaluate(el => {
        const s = getComputedStyle(el);
        return { font: s.fontSize, height: el.getBoundingClientRect().height, padding: s.padding, opacity: s.opacity, background: s.backgroundColor };
      });
      assert.equal(ready.font, '18px');
      assert.ok(ready.height >= 60);
      assert.equal(ready.padding, '14px 18px');
      assert.equal(ready.opacity, '1');
      await close.scrollIntoViewIfNeeded();
      if (process.env.DESK_V2_SCREENSHOTS) await page.screenshot({ path: path.join(process.env.DESK_V2_SCREENSHOTS, `task-${theme}-${width}.png`) });
      // Exercise the CSS state without issuing a close request to the API.
      await close.evaluate(el => { el.disabled = true; el.classList.remove('close-action-ready'); });
      await page.waitForFunction(background => getComputedStyle(document.querySelector('#closeBtn')).backgroundColor === background,
        theme === 'dark' ? 'rgb(38, 43, 61)' : 'rgb(246, 244, 249)');
      const disabled = await close.evaluate(el => ({ opacity: getComputedStyle(el).opacity, background: getComputedStyle(el).backgroundColor }));
      assert.equal(await close.isEnabled(), false);
      assert.equal(disabled.opacity, '0.45');
      assert.notEqual(disabled.background, ready.background);
    });
  }
}

for (const width of [1024, 390]) {
  for (const pageName of ['ticket', 'tickets']) {
    test(`responsive ${pageName}: ${width}px retains accessible content`, async t => {
      const { page } = await setup(t, { width, height: 844, theme: 'dark', pageName, role: 'agent' });
      assert.equal((await geometry(page)).overflow, false);
      if (pageName === 'ticket') {
        await page.locator('#ticketMessageBody').scrollIntoViewIfNeeded();
        assert.ok(await page.locator('#ticketMessageBody').isVisible());
        await page.locator('#editClientBtn').click();
        await page.locator('#fContactName').waitFor({ state: 'visible' });
      } else {
        await page.locator('#columnsSettingsBtn').click();
        await modalVisible(page, '#columnsModal');
      }
    });
  }
}


for (const width of [1366, 1920]) for (const theme of ['light', 'dark']) {
  test(`client and agent bubbles fit two thirds: ${theme}, ${width}`, async t => {
    const { page } = await setup(t, { width, height: 1080, theme, pageName: 'ticket' });
    const metrics = await page.evaluate(() => {
      const messages = document.querySelector('#messages');
      const style = getComputedStyle(messages);
      const available = messages.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
      const result = {};
      for (const kind of ['client', 'agent']) {
        const original = messages.querySelector(`.message.${kind}`);
        result[kind] = {};
        for (const [sample, text] of [['short', 'Ок'], ['long', 'Проверяем доступ к рабочей почте и настройки подключения. '.repeat(30)], ['continuous', 'НепрерывнаяСтрока'.repeat(100)]]) {
          const bubble = original.cloneNode(true);
          bubble.querySelector('.message-header strong').textContent = kind === 'client' ? 'Клиент' : 'Агент';
          bubble.querySelector('.message-date').textContent = '12:00';
          bubble.querySelector('.message-body').textContent = text;
          messages.appendChild(bubble);
          const rect = bubble.getBoundingClientRect(), computed = getComputedStyle(bubble);
          result[kind][sample] = { width: rect.width, overflow: bubble.scrollWidth > bubble.clientWidth + 1,
            align: computed.alignSelf, color: computed.backgroundColor, maxWidth: computed.maxWidth };
          bubble.remove();
        }
      }
      return { available, result };
    });
    for (const kind of ['client', 'agent']) {
      const samples = metrics.result[kind];
      for (const item of Object.values(samples)) {
        assert.equal(item.maxWidth, '66.67%');
        assert.ok(item.width <= metrics.available * .6667 + 1);
        assert.equal(item.overflow, false);
        assert.equal(item.align, kind === 'client' ? 'flex-start' : 'flex-end');
        assert.equal(item.color, samples.short.color);
      }
      assert.ok(samples.short.width < samples.long.width);
      assert.ok(Math.abs(samples.long.width - metrics.available * .6667) < 1);
      assert.ok(Math.abs(samples.continuous.width - metrics.available * .6667) < 1);
    }
  });
}


for (const width of [1366, 1920]) for (const theme of ['light', 'dark']) {
  test(`sidebar counter geometry for 0, 1, 12, 595: ${theme}, ${width}`, async t => {
    const { page } = await setup(t, { width, height: 1080, theme, pageName: 'tickets' });
    const metrics = await page.evaluate(() => {
      const glyph = el => {
        const range = document.createRange(); range.selectNodeContents(el);
        return range.getBoundingClientRect();
      };
      const samples = [];
      for (const value of ['0', '1', '12', '595']) {
        for (const counts of document.querySelectorAll('.sidebar .admin-agent-counts')) {
          const [left, barOne, barTwo, right] = counts.children;
          left.textContent = right.textContent = value;
          const l = left.getBoundingClientRect(), r = right.getBoundingClientRect();
          const a = barOne.getBoundingClientRect(), b = barTwo.getBoundingClientRect();
          samples.push({ kind: 'agent', value, leftWidth: l.width, rightWidth: r.width,
            leftGap: a.left - glyph(left).right, rightGap: glyph(right).left - b.right,
            barWidth: [a.width, b.width], barHeight: [a.height, b.height], barGap: b.left - a.right,
            numeric: getComputedStyle(counts).fontVariantNumeric,
            overflow: counts.scrollWidth > counts.clientWidth + 1 });
        }
        for (const badge of document.querySelectorAll('.sidebar .tab-btn-count')) {
          badge.textContent = value;
          const box = badge.getBoundingClientRect(), text = glyph(badge);
          samples.push({ kind: 'category', value, tab: badge.dataset.tabCount,
            width: box.width, height: box.height,
            dx: (text.left + text.right - box.left - box.right) / 2,
            dy: (text.top + text.bottom - box.top - box.bottom) / 2,
            radius: getComputedStyle(badge).borderRadius,
            overflow: badge.scrollWidth > badge.clientWidth + 1 });
        }
      }
      return { samples, overflow: [...document.querySelectorAll('.sidebar, .admin-agent-list, .admin-agent-item')]
        .some(el => el.scrollWidth > el.clientWidth + 1) };
    });
    assert.equal(metrics.overflow, false);
    assert.ok(metrics.samples.some(sample => sample.tab === 'frozen'));
    for (const sample of metrics.samples) {
      assert.equal(sample.overflow, false, JSON.stringify(sample));
      if (sample.kind === 'agent') {
        assert.equal(sample.leftWidth, 28);
        assert.equal(sample.rightWidth, 28);
        assert.ok(Math.abs(sample.leftGap - 4) < .1, JSON.stringify(sample));
        assert.ok(Math.abs(sample.rightGap - 4) < .1, JSON.stringify(sample));
        assert.deepEqual(sample.barWidth, [2, 2]);
        assert.deepEqual(sample.barHeight, [16, 16]);
        assert.equal(sample.barGap, 4);
        assert.equal(sample.numeric, 'tabular-nums');
      } else {
        assert.ok(Math.abs(sample.dx) <= .5, JSON.stringify(sample));
        assert.ok(Math.abs(sample.dy) <= 1, JSON.stringify(sample));
        assert.equal(sample.height, 24);
        assert.equal(sample.radius, '999px');
        if (sample.value === '595') assert.ok(sample.width > sample.height);
      }
    }
  });
}
