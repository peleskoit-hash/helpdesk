// Real templates/CSS/scripts with isolated API fixtures; no local DB or output files.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });
const pages = {
  '/tickets': 'tickets', '/statuses': 'statuses', '/management/integrations': 'management_integrations',
  '/management/feedback': 'management_feedback', '/management/statistics': 'management_statistics',
  '/management/saved-files': 'management_saved_files', '/quick-replies': 'quick_replies',
};

for (const theme of ['light', 'dark']) test(`status controls share one readable palette: ${theme}`, async t => {
  const { page } = await setup(t, theme, 1366, '/statuses');
  const expected = ['rgb(169, 227, 195)', 'rgb(201, 177, 244)', 'rgb(245, 212, 165)', 'rgb(175, 216, 249)', 'rgb(245, 186, 202)'];
  const expectedText = ['rgb(21, 91, 62)', 'rgb(86, 50, 146)', 'rgb(135, 83, 21)', 'rgb(35, 91, 138)', 'rgb(145, 61, 89)'];
  const board = await page.locator('.status-board-btn').evaluateAll(nodes => nodes.map(el => {
    const style = getComputedStyle(el), sidebar = document.querySelector(`.agent-status-btn[data-availability-status="${el.dataset.status}"]`);
    const rgb = value => value.match(/\d+/g).slice(0, 3).map(Number);
    const lum = value => rgb(value).map(v => { v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; })
      .reduce((sum, value, i) => sum + value * [.2126, .7152, .0722][i], 0);
    const a = lum(style.color), b = lum(style.backgroundColor);
    return { status: el.dataset.status, color: style.color, sidebarColor: getComputedStyle(sidebar).color,
      background: style.backgroundColor, sidebar: getComputedStyle(sidebar).backgroundColor, contrast: (Math.max(a, b) + .05) / (Math.min(a, b) + .05) };
  }));
  assert.deepEqual(board.map(row => row.background), expected);
  assert.deepEqual(board.map(row => row.color), expectedText);
  for (const row of board) {
    assert.equal(row.background, row.sidebar); assert.equal(row.color, row.sidebarColor);
    // The prescribed offline foreground/background pair has approximately 4.23:1 contrast.
    assert.ok(row.contrast >= (row.status === 'offline' ? 4.2 : 4.5));
  }
  await page.locator('.status-board-btn[data-status="waiting"]').click();
  await page.waitForFunction(() => document.querySelector('.status-board-btn[data-status="waiting"]').classList.contains('active'));
  assert.notEqual(await page.locator('.status-board-btn.active').evaluate(el => getComputedStyle(el).boxShadow), 'none');
  await page.locator('#themeToggleBtn').click();
  await page.locator(`[data-appearance-theme="${theme === 'light' ? 'dark' : 'light'}"]`).click();
  assert.deepEqual(await page.locator('.status-board-btn').evaluateAll(nodes => nodes.map(el => getComputedStyle(el).backgroundColor)), expected);
  assert.deepEqual(await page.locator('.status-board-btn').evaluateAll(nodes => nodes.map(el => getComputedStyle(el).color)), expectedText);
  await page.goto('http://desk.test/management/feedback');
  await page.waitForFunction(() => document.body.dataset.sidebarReady === '1');
  await page.evaluate(() => { window.paletteSidebar = document.querySelector('.sidebar'); });
  await page.locator('.sidebar a[href="/statuses"]').click();
  await page.waitForSelector('.status-board-btn');
  assert.equal(await page.evaluate(() => window.paletteSidebar === document.querySelector('.sidebar')), true);
  assert.deepEqual(await page.locator('.status-board-btn').evaluateAll(nodes => nodes.map(el => getComputedStyle(el).backgroundColor)), expected);
  assert.deepEqual(await page.locator('.status-board-btn').evaluateAll(nodes => nodes.map(el => getComputedStyle(el).color)), expectedText);
});
async function setup(t, theme, width, target = '/statuses') {
  const page = await browser.newPage({ viewport: { width, height: width >= 1000 ? 768 : width === 640 ? 740 : 844 }, reducedMotion: 'reduce' });
  t.after(() => page.close());
  const errors = [], calls = [];
  page.on('pageerror', e => errors.push(e.message));
  const me = { id: 'admin', role: 'admin', login: 'admin', full_name: 'Администратор', availability_status: 'online' };
  const employee = { id: 'agent', full_name: 'Александр Иванов', role: 'agent', is_active: true, availability_status: 'online', updated_at: '2026-10-25T10:00:00Z' };
  let saved = { id: 'file', display_filename: 'Инструкция.pdf', file_size: 1024, used: false, usage_sources: [] };
  const replies = [{ id: 'reply', short_title: 'Проверка подключения', body: 'Проверьте подключение к сети.', can_edit: true, can_delete: true, saved_files: [], author_name: me.full_name }];
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    if (url.hostname !== 'desk.test') return route.abort();
    if (p.startsWith('/static/')) {
      const file = path.resolve('api/app', p.slice(1));
      return route.fulfill({ status: fs.existsSync(file) ? 200 : 404, body: fs.existsSync(file) ? fs.readFileSync(file) : '',
        contentType: { '.js': 'text/javascript', '.css': 'text/css', '.ttf': 'font/ttf', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream' });
    }
    if (pages[p]) return route.fulfill({ contentType: 'text/html', body: fs.readFileSync(`api/app/templates/${pages[p]}.html`, 'utf8') });
    calls.push({ path: p, method: req.method(), query: url.search });
    let data = {};
    if (p.endsWith('/appearance-session')) data = { writer_id: '00000000-0000-4000-8000-000000000001', preferences: { theme_preference: theme, appearance_revision: '0' } };
    else if (p.endsWith('/preferences')) data = { theme_preference: theme, appearance_revision: '0' };
    else if (p === '/api/v1/auth/session') data = { real_user: me, effective_user: me, is_real_admin: true, session_login_ts: 1, app_timezone: 'Europe/Moscow' };
    else if (p === '/api/v1/users/me' || p === '/api/v1/users/me/profile') data = me;
    else if (['/api/v1/users/agents', '/api/v1/users/by-role', '/api/v1/settings/users'].includes(p)) data = [employee];
    else if (p.endsWith('/counts')) data = { active: 0, frozen: 0, unassigned: 0, special: 0, closed: 0, by_assignee: {} };
    else if (p === '/api/v1/users/status-board') data = [employee];
    else if (p === '/api/v1/users/agent/status') { Object.assign(employee, req.postDataJSON()); data = employee; }
    else if (p === '/api/v1/management/integrations/logs') data = { items: [{ id: 'log', created_at: '2026-10-25T10:00:00Z', event_type: 'telegram_send', result: 'success', code: 'ok', message_text: 'Уведомление доставлено' }], total: 1, limit: 50, offset: 0 };
    else if (p === '/api/v1/management/feedback') data = { items: [{ id: 'feedback', submitted_at: '2026-10-25T10:00:00Z', client_name_snapshot: 'Мария', ticket_title_snapshot: 'Настройка почты', text: 'Спасибо! '.repeat(40), ticket_available: true, ticket_id: 'ticket', ticket_number: 42, workflow_type: 'ticket' }], total_count: 1, page: 1, pages: 1, min_feedback_date: null };
    else if (p.endsWith('/statistics/work-time')) data = { total: { tickets: 2, tasks: 1, objects: 3, average_seconds: 1200, median_seconds: 1200 }, items: [{ user_id: employee.id, full_name: employee.full_name, is_active: true, tickets: 2, tasks: 1, objects: 3, average_seconds: 1200, median_seconds: 1200 }] };
    else if (p.endsWith('/statistics/statuses')) data = { total: { online_seconds: 3600, on_ticket_seconds: 1800, total_seconds: 5400 }, items: [{ user_id: employee.id, full_name: employee.full_name, is_active: true, online_seconds: 3600, on_ticket_seconds: 1800, total_seconds: 5400 }] };
    else if (p.endsWith('/statistics/status-transitions')) {
      let items = Array.from({ length: 125 }, (_, i) => ({ user_id: employee.id, full_name: employee.full_name, is_active: true, from_status: 'online', to_status: 'on_ticket', changed_by_admin: [true, false, null, undefined][i % 4], changed_at: new Date(Date.UTC(2026, 9, 25, 10, i)).toISOString() }));
      const q = url.searchParams;
      if (q.get('date_from')) items = items.filter(i => Date.parse(i.changed_at) >= Date.parse(q.get('date_from')));
      if (q.get('date_to')) items = items.filter(i => Date.parse(i.changed_at) < Date.parse(q.get('date_to')));
      if (q.get('user_id')) items = items.filter(i => i.user_id === q.get('user_id'));
      if (q.get('sort_order') !== 'asc') items.reverse();
      const offset = Number(q.get('cursor') || 0), limit = Number(q.get('limit') || 15);
      data = { items: items.slice(offset, offset + limit), total_count: q.get('include_total') ? items.length : null, has_more: offset + limit < items.length, next_cursor: offset + limit < items.length ? String(offset + limit) : null };
    }
    else if (p === '/api/v1/management/saved-files') data = { items: [saved] };
    else if (p === '/api/v1/management/saved-files/file' && req.method() === 'PATCH') { saved = { ...saved, ...req.postDataJSON() }; data = saved; }
    else if (p === '/api/v1/quick-replies') data = replies;
    else if (p === '/api/v1/quick-replies/reply' && req.method() === 'PATCH') { Object.assign(replies[0], req.postDataJSON()); data = replies[0]; }
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
  });
  await page.goto('http://desk.test' + target);
  await page.waitForFunction(theme => document.body.dataset.sidebarReady === '1' && document.body.dataset.theme === theme, theme);
  t.after(() => assert.deepEqual(errors, []));
  return { page, calls };
}
async function skin(page, selector) {
  return page.locator(selector).first().evaluate(el => {
    const s = getComputedStyle(el);
    return { radius: s.borderRadius, background: s.backgroundColor, color: s.color };
  });
}
async function checkTheme(page) {
  const result = await page.locator('#appContent .panel').evaluate(panel => {
    const probe = document.createElement('span');
    panel.append(probe);
    const resolve = token => { probe.style.color = `var(${token})`; return getComputedStyle(probe).color; };
    const s = getComputedStyle(panel);
    const themed = s.backgroundColor === resolve('--desk-surface') && s.color === resolve('--desk-text');
    probe.remove();
    return themed;
  });
  assert.equal(result, true);
}
async function checkTypography(page, template) {
  const samples = {
    statuses: { '.status-page-tabs button': 21, '.status-user-name': 27, '.status-user-avatar': 30, '.status-board-btn': 21, '.status-board-btn-time': 19.5, '#statusBoardEmpty': 25, '#errorText': 21 },
    management_integrations: { '.integration-log-title': 24, '.integration-log-filters label': 18, '#integrationLogEventType': 19.5, 'th': 19.5, 'td': 17.85, '#integrationLogStatus': 19.5, '#integrationLogResetBtn': 21 },
    management_feedback: { '.management-feedback-description': 21, '#managementFeedbackSearch': 19.5, '.management-feedback-text': 21, '.management-feedback-text-action': 21 },
    management_statistics: { 'th': 19.5, 'td': 21, '.management-statistics-median-help': 19.5, '[data-statistics-tab]': 21 },
    management_saved_files: { 'td': 21, '#managementSavedFilesUploadBtn': 21, '#managementSavedFilesRenameTitle': 21, '.modal-body label': 19.5, '#managementSavedFilesRenameInput': 21, '#managementSavedFilesRenameError': 19.5 },
    quick_replies: { '.quick-reply-card-title': 21, '#createQuickReplyBtn': 21, '#quickReplyModalTitle': 21, '.modal-label-title': 19.5, '#quickReplyTitleInput': 21, '#quickReplyBodyInput': 21 },
  };
  for (const [selector, size] of Object.entries(samples[template])) {
    assert.equal(await page.locator('#appContent ' + selector).first().evaluate(el => parseFloat(getComputedStyle(el).fontSize)), size, template + ': ' + selector);
  }
  assert.equal(await page.locator('.toolbar button').first().evaluate(el => getComputedStyle(el).fontSize), '14px');
  assert.equal(await page.locator('.sidebar .tab-btn').first().evaluate(el => getComputedStyle(el).fontSize), '14px');
  assert.equal(await page.locator('#appContent').evaluate(root => [...root.querySelectorAll('.panel button, .modal:not(.hidden) button')].some(el => el.clientHeight && (el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1))), false, template + ': buttons fit text');
}
for (const theme of ['light', 'dark']) for (const width of [1440, 640, 390, 320]) {
  test(`six menu pages V2 controls: ${theme}, ${width}`, async t => {
    const { page, calls } = await setup(t, theme, width);
    await page.locator('.status-board-btn[data-status="waiting"]').click();
    await page.waitForFunction(() => document.querySelector('.status-board-btn[data-status="waiting"]').classList.contains('active'));
    assert.equal((await skin(page, '.status-board-btn')).radius, '12px');
    assert.equal((await skin(page, '.status-user-column')).radius, '16px');
    await checkTheme(page);
    await checkTypography(page, 'statuses');
    assert.ok(calls.some(c => c.path === '/api/v1/users/agent/status' && c.method === 'PATCH'));
    for (const [url, template] of Object.entries(pages).slice(1)) {
      await page.evaluate(url => window.HelpdeskAppShell.navigate(url), url);
      await page.waitForFunction(template => document.querySelector('#appContent').dataset.shellPage === template.replaceAll('_', '-'), template);
      assert.equal((await skin(page, '#appContent .panel')).radius, '24px');
      await checkTheme(page);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, url);
      if (template === 'management_integrations') {
        await page.waitForSelector('#integrationLogTableBody tr');
        assert.equal(await page.locator('#integrationLogTableBody tr:first-child td:first-child .integration-log-date').count(), 2);
        assert.doesNotMatch(await page.locator('#integrationLogTableBody tr:first-child td:first-child').innerText(), /,/);
        assert.equal(await page.locator('.integration-log-table th').first().innerText(), 'Дата и время');
        assert.equal(await page.locator('.integration-log-code').first().evaluate(el => getComputedStyle(el).overflowWrap), 'normal');
        await page.selectOption('#integrationLogEventType', 'telegram_send');
        await page.waitForFunction(() => !document.querySelector('#integrationLogResetBtn').disabled);
        assert.ok(calls.some(c => c.path.endsWith('/logs') && c.query.includes('event_type=telegram_send')));
        assert.equal((await skin(page, '#integrationLogEventType')).radius, '12px');
      } else if (template === 'management_feedback') {
        await page.waitForSelector('.management-feedback-text-action');
        assert.equal(await page.locator('#managementFeedbackPagination').isVisible(), false);
        assert.ok(await page.locator('.management-feedback-table-wrap').evaluate(el => el.clientHeight >= 240));
        await page.locator('.management-feedback-text-action').click();
        assert.equal(await page.locator('.management-feedback-text-action').innerText(), 'Свернуть');
        await page.locator('#managementFeedbackSearch').fill('Почта');
        await page.locator('#managementFeedbackSearchBtn').click();
        assert.ok(calls.some(c => c.path.endsWith('/feedback') && decodeURIComponent(c.query).includes('Почта')));
      } else if (template === 'management_statistics') {
        await page.waitForSelector('#managementStatisticsWorkTimeTableBody tr');
        assert.ok(await page.locator('.management-statistics-work-time-table').evaluate(el => el.getBoundingClientRect().width >= 1294));
        assert.equal(await page.locator('.management-statistics-work-time-table th').count(), 6);
        assert.doesNotMatch(await page.locator('#managementStatisticsWorkTimeSummary').innerText(), /Общее время/);
        for (const selector of ['.management-statistics-summary', '.management-statistics-table-wrap']) {
          assert.equal(await page.locator(selector).first().evaluate(el => getComputedStyle(el).borderRadius), '16px');
        }
        await page.locator('[data-statistics-tab="statuses"]').click();
        await page.waitForSelector('#managementStatisticsStatusesTableBody tr');
        assert.equal(calls.filter(c => c.path.endsWith('/status-transitions')).length, 0);
        await page.selectOption('#managementStatisticsPeriod', 'all');
        await page.locator('[data-statistics-tab="work-time"]').click();
        await page.waitForSelector('#managementStatisticsWorkTimeTableBody tr');
      } else if (template === 'management_saved_files') {
        await page.waitForSelector('#managementSavedFilesTableBody tr');
        assert.equal(await page.locator('#managementSavedFilesRenameBtn').isDisabled(), true);
        await page.locator('#managementSavedFilesTableBody tr').click();
        await page.locator('#managementSavedFilesRenameBtn').click();
        await checkTypography(page, template);
        assert.equal(await page.locator('#managementSavedFilesRenameModal .modal-dialog').evaluate(el => el.scrollWidth <= el.clientWidth + 1), true);
        await page.locator('#managementSavedFilesRenameInput').fill('Новое имя.pdf');
        await page.locator('#managementSavedFilesRenameSaveBtn').click();
        await page.waitForFunction(() => document.querySelector('#managementSavedFilesTableBody').textContent.includes('Новое имя.pdf'));
      } else if (template === 'quick_replies') {
        await page.waitForSelector('.quick-reply-card');
        assert.equal(await page.locator('.quick-replies-workspace').evaluate(el => getComputedStyle(el).padding), '24px');
        await page.locator('.quick-reply-card').click();
        await page.waitForSelector('#quickReplyModal:not(.hidden)');
        assert.equal(await page.locator('#quickReplyTitleInput').inputValue(), 'Проверка подключения');
        assert.equal((await skin(page, '#quickReplyModal .modal-dialog')).radius, '24px');
        await checkTypography(page, template);
        assert.equal(await page.locator('#quickReplyModal .modal-dialog').evaluate(el => el.scrollWidth <= el.clientWidth + 1), true);
        await page.locator('#closeQuickReplyModalBtn').click();
        await page.locator('#createQuickReplyBtn').click();
        await page.waitForSelector('#quickReplyModal:not(.hidden)');
        assert.equal(await page.locator('#quickReplyTitleInput').inputValue(), '');
        await page.locator('#cancelQuickReplyBtn').click();
      }
      await checkTypography(page, template);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, url);
    }
    // Existing shell Back mounts the preceding page and its V2 styles again.
    await page.locator('[data-shell-back]').click();
    await page.waitForURL('**/management/saved-files');
    await page.waitForFunction(() => document.querySelector('#appContent').dataset.shellPage === 'management-saved-files');
    assert.equal((await skin(page, '#appContent .panel')).radius, '24px');
    await page.evaluate(() => window.HelpdeskErrorDialog.show({ title: 'Проверка сообщения', message: 'Текст ошибки', error: 'Подробности ошибки' }));
    await page.waitForSelector('#appErrorDialog:not(.hidden)');
    assert.equal(await page.locator('#appErrorDialogDetails').evaluate(el => getComputedStyle(el).fontSize), '21px');
    await page.locator('#appErrorDialogOkBtn').click();
    await page.evaluate(() => window.HelpdeskAppShell.showSaved(document.querySelector('#appContent .panel')));
    assert.equal(await page.locator('.save-confirmation').evaluate(el => getComputedStyle(el).fontSize), '36px');
  });
}

for (const theme of ['light', 'dark']) for (const width of [320, 768, 1280, 1920]) {
  test(`status log full pages, filters, sorting and tab reuse: ${theme}, ${width}`, async t => {
    const { page, calls } = await setup(t, theme, width);
    const requests = () => calls.filter(c => c.path.endsWith('/status-transitions'));
    assert.equal(requests().length, 0);
    await page.locator('[data-status-tab="log"]').click();
    await page.waitForFunction(() => document.querySelectorAll('#statusLogTableBody tr').length === 50);
    assert.equal(requests().length, 1);
    assert.match(requests()[0].query, /limit=50/);
    assert.match(await page.locator('#statusLogSummary').innerText(), /1–50 из 125/);
    assert.equal(await page.locator('#statusLogPrev').isDisabled(), true);
    assert.equal(await page.locator('#statusLogTableBody tr').first().locator('td').count(), 6);
    assert.equal(await page.locator('.status-log-table th').count(), 6);
    assert.equal(await page.locator('.status-log-admin-heading').innerHTML(), 'Изменено<br>Админом');
    assert.deepEqual(await page.locator('#statusLogTableBody tr').evaluateAll(rows => rows.slice(0, 4).map(row => row.cells[5].textContent)), ['Да', '', '', '']);
    assert.equal(await page.locator('.status-log-admin-heading').evaluate(el => getComputedStyle(el).fontSize), '23px');
    const presentation = await page.locator('#statusLogTableBody tr').first().evaluate(row => ({
      display: getComputedStyle(row).display,
      labels: [...row.cells].map(cell => cell.dataset.label),
      visibleLabels: [...row.cells].map(cell => getComputedStyle(cell, '::before').content),
    }));
    const labels = ['Сотрудник', 'Был статус', 'Стал статус', 'Дата', 'Время', 'Изменено Админом'];
    assert.deepEqual(presentation.labels, labels);
    assert.equal(presentation.display, width >= 1280 ? 'table-row' : 'grid');
    if (width < 1280) assert.deepEqual(presentation.visibleLabels, labels.map(label => `"${label}"`));

    await page.locator('#statusLogNext').click();
    await page.waitForFunction(() => document.querySelector('#statusLogSummary').textContent.includes('51–100'));
    await page.locator('#statusLogNext').click();
    await page.waitForFunction(() => document.querySelectorAll('#statusLogTableBody tr').length === 25);
    assert.match(await page.locator('#statusLogSummary').innerText(), /101–125 из 125/);
    assert.equal(await page.locator('#statusLogNext').isDisabled(), true);
    await page.locator('#statusLogPrev').click();
    await page.waitForFunction(() => document.querySelector('#statusLogSummary').textContent.includes('51–100'));
    await page.selectOption('#statusLogOrder', 'asc');
    await page.waitForFunction(() => document.querySelector('#statusLogSummary').textContent.includes('1–50'));
    assert.match(requests().at(-1).query, /sort_order=asc/);
    assert.doesNotMatch(requests().at(-1).query, /cursor=/);
    assert.equal(await page.locator('#statusLogTableBody tr').first().locator('td').nth(4).innerText(), '13:00:00');
    await page.selectOption('#statusLogPeriod', 'custom');
    await page.locator('#statusLogDateFrom').fill('2026-10-26');
    await page.locator('#statusLogDateTo').fill('2026-10-26');
    await page.waitForFunction(() => document.querySelector('#statusLogSummary').textContent.includes('из 0'));
    assert.equal(await page.locator('#statusLogNext').isDisabled(), true);
    await page.selectOption('#statusLogPeriod', 'all');
    await page.selectOption('#statusLogEmployee', 'agent');
    await page.waitForFunction(() => document.querySelectorAll('#statusLogTableBody tr').length === 50);
    assert.match(requests().at(-1).query, /user_id=agent/);
    assert.doesNotMatch(requests().at(-1).query, /date_from|date_to/);
    await page.selectOption('#statusLogOrder', 'desc');
    await page.waitForFunction(() => document.querySelector('[data-status-log-sort]').getAttribute('aria-sort') === 'descending' && document.querySelectorAll('#statusLogTableBody tr').length === 50);
    assert.match(requests().at(-1).query, /sort_order=desc/);
    const count = requests().length;
    await page.locator('[data-status-tab="board"]').click();
    await page.locator('[data-status-tab="log"]').click();
    assert.equal(requests().length, count);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.deepEqual(await page.locator('.status-log-viewport').evaluate(el => el.scrollWidth > el.clientWidth ? { viewport: el.clientWidth, table: el.querySelector('table').offsetWidth, columns: [...el.querySelectorAll('th')].map(th => ({ width: th.offsetWidth, text: th.textContent })) } : false), false);
    assert.equal(await page.locator('.status-log-table td').first().evaluate(el => getComputedStyle(el).fontSize), '21px');
    const logGeometry = await page.locator('.status-log-viewport').evaluate(el => {
      const panel = el.closest('.panel').getBoundingClientRect(), rect = el.getBoundingClientRect();
      return { ratio: rect.width / panel.width, left: rect.left - panel.left, right: panel.right - rect.right,
        clipped: [...el.querySelectorAll('td')].some(td => td.scrollWidth > td.clientWidth + 1) };
    });
    assert.equal(logGeometry.clipped, false);
    if (width > 1000) { assert.ok(logGeometry.ratio > .6 && logGeometry.ratio < .68); assert.ok(logGeometry.right > logGeometry.left + 100); }
    else assert.ok(logGeometry.ratio > .8);

    assert.equal(await page.locator('.status-log-table th').first().evaluate(el => getComputedStyle(el).fontSize), '29.25px');
    await checkTheme(page);
  });
}

for (const theme of ['light', 'dark']) for (const width of [1920, 1440, 390]) {
  test(`statistics blocks hug tables at left: ${theme}, ${width}`, async t => {
    const { page } = await setup(t, theme, width, '/management/statistics');
    for (const tab of ['work-time', 'statuses']) {
      await page.locator(`[data-statistics-tab="${tab}"]`).click();
      const section = page.locator(`[data-statistics-panel="${tab}"]`);
      await section.locator('tbody tr').waitFor();
      const geometry = await section.locator('.management-statistics-table-wrap').evaluate(el => {
        const table = el.querySelector('table'), rect = el.getBoundingClientRect(), outer = el.closest('.panel').getBoundingClientRect();
        return { width: rect.width, table: table.getBoundingClientRect().width, available: outer.width - 44,
          left: rect.left - outer.left, scrollable: getComputedStyle(el).overflowX,
          overflow: el.scrollWidth > el.clientWidth };
      });
      assert.ok(Math.abs(geometry.width - Math.min(geometry.table + 2, geometry.available)) <= 2, JSON.stringify(geometry));
      assert.ok(Math.abs(geometry.left - 22) <= 1, JSON.stringify(geometry));
      assert.equal(geometry.scrollable, 'auto');
      assert.equal(geometry.overflow, geometry.table + 2 > geometry.available);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await checkTheme(page);
    }
  });
}


test('status board disables rapid same-employee clicks while PATCH is pending', async t => {
  const { page } = await setup(t, 'light', 1440, '/statuses');
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  const requests = [];
  await page.route('**/api/v1/users/agent/status', async route => {
    requests.push(route.request().postDataJSON().availability_status);
    await pending;
    await route.fulfill({ contentType: 'application/json', body: '{}' });
  });
  await page.locator('.status-board-btn[data-status="waiting"]').click();
  await page.waitForFunction(() => document.querySelectorAll('.status-board-btn:disabled').length === 5);
  await page.evaluate(() => document.querySelector('.status-board-btn[data-status="lunch"]').click());
  assert.deepEqual(requests, ['waiting']);
  release();
  await page.waitForFunction(() => document.querySelectorAll('.status-board-btn:disabled').length === 0);
  assert.deepEqual(requests, ['waiting']);
});


test('status admin resource versions stay synchronized across templates and CSS imports', () => {
  const version = '20261106-status-admin-v1';
  for (const file of fs.readdirSync('api/app/templates').filter(file => file.endsWith('.html'))) {
    const html = fs.readFileSync(path.join('api/app/templates', file), 'utf8');
    for (const resource of ['styles.css', 'app_shell.js']) {
      const urls = html.match(new RegExp('/static/' + resource.replace('.', '\\.') + '(?:\\?[^"\\s<>]*)?', 'g')) || [];
      for (const url of urls) assert.equal(url, `/static/${resource}?v=${version}`, file);
    }
  }
  assert.ok(fs.readFileSync('api/app/static/styles.css', 'utf8').includes(`/static/desk-v2.css?v=${version}`));
  assert.ok(fs.readFileSync('api/app/static/desk-v2.css', 'utf8').includes(`/static/admin-pages-v2.css?v=${version}`));
  const shell = fs.readFileSync('api/app/static/app_shell.js', 'utf8');
  assert.match(shell, /\["\/statuses",.*"\/static\/status_transitions.js\?v=20261106-status-admin-v1"/);
});

for (const start of ['/statuses', '/tickets']) test(`status admin resources load directly or through SPA from ${start}`, async t => {
  const { page } = await setup(t, 'light', 1440, start);
  assert.equal(await page.locator('link[href="/static/styles.css?v=20261106-status-admin-v1"]').count(), 1);
  assert.equal(await page.locator('script[src="/static/app_shell.js?v=20261106-status-admin-v1"]').count(), 1);
  await page.evaluate(() => { window.statusCacheDocument = document; window.statusCacheSidebar = document.querySelector('.sidebar'); });
  if (start === '/tickets') {
    await page.locator('.sidebar a[href="/statuses"]').click();
    await page.waitForSelector('[data-shell-page="statuses"]');
    assert.equal(await page.evaluate(() => window.statusCacheDocument === document && window.statusCacheSidebar === document.querySelector('.sidebar')), true);
  }
  await page.locator('[data-status-tab="log"]').click();
  await page.waitForSelector('#statusLogTableBody tr');
  assert.equal(await page.locator('.status-log-table th').count(), 6);
  assert.equal(await page.locator('#statusLogTableBody tr').first().locator('td').count(), 6);
  assert.equal(await page.locator('.status-log-admin-heading').innerHTML(), 'Изменено<br>Админом');
  assert.equal(await page.locator('.status-log-admin-heading').evaluate(el => getComputedStyle(el).fontSize), '23px');
  const resources = await page.evaluate(() => performance.getEntriesByType('resource').map(entry => new URL(entry.name).pathname + new URL(entry.name).search));
  for (const resource of ['styles.css', 'desk-v2.css', 'admin-pages-v2.css', 'app_shell.js', 'status_transitions.js']) {
    const matching = resources.filter(url => url.startsWith('/static/' + resource + '?'));
    assert.ok(matching.length > 0, resource);
    assert.ok(matching.every(url => url === '/static/' + resource + '?v=20261106-status-admin-v1'), matching.join(', '));
  }
});
