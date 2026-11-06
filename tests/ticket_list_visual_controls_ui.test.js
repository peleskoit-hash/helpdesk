const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });

async function setup(t, theme, width) {
  const page = await browser.newPage({ viewport: { width, height: 1000 } });
  t.after(() => page.close());
  const errors = [], writes = [];
  let rejectAssignment = false;
  page.on('pageerror', e => errors.push(e.message));
  t.after(() => assert.deepEqual(errors, []));
  const kinds = ['request', 'task', 'group-task', 'internal-task', 'frozen', 'closed'];
  const items = Array.from({ length: 20 }, (_, i) => ({ id: String(i + 1), subject: `Заявка ${i + 1}`,
    workflow_type: i % 6 === 0 ? 'ticket' : 'task', task_kind: i % 6 === 2 ? 'group_parent' : 'normal',
    is_internal_task: i % 6 === 3, is_frozen: i % 6 === 4, status: i % 6 === 5 ? 'closed' : 'new',
    seen_by_agent: i >= 6, created_at: '2026-10-30T09:00:00Z' }));
  const preferences = { color_enabled: true, task_panel_enabled: false,
    ticket_list_columns: [{ key: 'subject', width: 300 }, { key: 'type', width: 150 }, { key: 'assignee', width: 170 }] };
  await page.route('http://desk.test/**', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    if (p.startsWith('/static/')) {
      const file = path.resolve('api/app', p.slice(1));
      return route.fulfill({ body: fs.readFileSync(file), contentType: { '.css': 'text/css', '.js': 'text/javascript', '.ttf': 'font/ttf', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream' });
    }
    if (!p.startsWith('/api/')) return route.fulfill({ contentType: 'text/html', body: fs.readFileSync('api/app/templates/tickets.html', 'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '') });
    if (req.method() === 'POST') {
      writes.push({ path: p, body: req.postDataJSON() });
      if (rejectAssignment) return route.fulfill({ status: 409, json: { detail: 'Назначение отклонено' } });
      return route.fulfill({ json: {} });
    }
    if (p.endsWith('/preferences')) {
      if (req.method() === 'PATCH') Object.assign(preferences, req.postDataJSON());
      return route.fulfill({ json: preferences });
    }
    if (p.endsWith('/users/me')) return route.fulfill({ json: { full_name: 'Admin' } });
    return route.fulfill({ json: { items, total: 20, page: 1, pages: 1, limit: 20 } });
  });
  await page.goto('http://desk.test/tickets?tab=active');
  await page.evaluate(theme => {
    document.body.dataset.theme = theme;
    document.body.dataset.sidebarReady = '1';
    const sidebar = { role: 'admin', userId: 'admin', agents: [
      { id: 'agent', full_name: 'Сотрудник', role: 'agent' },
      { id: 'trainee', full_name: 'Стажёр', role: 'trainee' }], counts: {} };
    window.HelpdeskSidebar = { init: async () => {}, getState: () => sidebar, onChange: () => () => {}, refreshCounts: async () => {} };
    window.HelpdeskAppShell = { registerPage: (_, init) => { window.initList = init; }, getCurrentPage: () => 'tickets-list' };
  }, theme);
  await page.addScriptTag({ content: fs.readFileSync('api/app/static/list.js', 'utf8') });
  await page.evaluate(() => { window.listPage = window.initList(); });
  await page.waitForFunction(() => document.querySelectorAll('.ticket-row').length === 20);
  return { page, writes, kinds, rejectAssignment: () => { rejectAssignment = true; } };
}

for (const theme of ['light', 'dark']) for (const width of [1440, 700, 390]) {
  test(`list controls, native assignment and type colors: ${theme}, ${width}`, async t => {
    const { page, writes, kinds } = await setup(t, theme, width);
    const assign = page.locator('#bulkAssignBtn');
    const geometry = () => page.locator('#bulkAssignBtn, #bulkAdminActions, #bulkCloseBtn, #bulkDeleteBtn').evaluateAll(nodes => nodes.map(n => {
      const r = n.getBoundingClientRect(); return [r.x, r.y, r.width, r.height];
    }));
    const before = await geometry();
    for (const count of [1, 9, 10, 20]) {
      await page.locator('.ticket-check').evaluateAll((nodes, count) => nodes.forEach((n, i) => {
        if (n.checked !== (i < count)) { n.click(); }
      }), count);
      assert.deepEqual(await geometry(), before, `stable count ${count}`);
      assert.equal(await assign.getAttribute('aria-label'), `Назначить ответственного (${count})`);
    }
    assert.equal(await assign.evaluate(n => getComputedStyle(n).appearance), 'auto');
    assert.equal(await assign.evaluate(n => {
      const s = getComputedStyle(n), ctx = document.createElement('canvas').getContext('2d');
      ctx.font = s.font;
      return ctx.measureText(n.options[0].text).width + parseFloat(s.paddingLeft) + parseFloat(s.paddingRight) + 24 <= n.clientWidth;
    }), true, 'the full (20) label leaves room for the native arrow');
    const rowOptions = await page.locator('.row-assignee-select').first().locator('option').evaluateAll(ns => ns.map(n => [n.value, n.textContent]));
    assert.deepEqual(await assign.locator('option').evaluateAll(ns => ns.slice(1).map(n => [n.value, n.textContent])), rowOptions);
    const ui = await page.evaluate(() => {
      const style = s => getComputedStyle(document.querySelector(s));
      const group = style('#bulkAdminActions'), header = style('#ticketsTable thead');
      return { border: group.borderTopWidth, borderStyle: group.borderTopStyle, bg: group.backgroundColor,
        headerBg: header.backgroundColor, surface: header.getPropertyValue('--desk-surface').trim(),
        headerPosition: header.position, headerShadow: header.boxShadow,
        headerHeight: document.querySelector('thead').getBoundingClientRect().height,
        footerHeight: document.querySelector('.table-pagination-footer').getBoundingClientRect().height,
        overflow: document.documentElement.scrollWidth > innerWidth };
    });
    assert.equal(ui.border, '1px'); assert.equal(ui.borderStyle, 'solid'); assert.notEqual(ui.bg, 'rgba(0, 0, 0, 0)');
    assert.equal(ui.headerPosition, 'sticky');
    assert.match(ui.headerBg, /^rgb\(/, 'header surface is opaque');
    assert.ok(ui.headerShadow.includes(ui.headerBg), 'surface shadow covers the gaps around the header');
    assert.ok(ui.headerHeight <= ui.footerHeight, JSON.stringify(ui)); assert.equal(ui.overflow, false);
    for (const kind of kinds) {
      const colors = await page.locator(`tr.ticket-color-${kind}`).first().evaluate(row => ({
        gradient: getComputedStyle(row).backgroundImage,
        badge: getComputedStyle(row.querySelector('.ticket-type-badge')).backgroundColor,
        cells: [...row.cells].map(n => getComputedStyle(n).backgroundColor),
      }));
      assert.ok(colors.gradient.startsWith(`linear-gradient(90deg, ${colors.badge}, `), JSON.stringify(colors));
      assert.ok(colors.gradient.endsWith('70%)'), colors.gradient);
      assert.ok(colors.cells.every(c => c === 'rgba(0, 0, 0, 0)'), JSON.stringify(colors));
    }
    await assign.selectOption('trainee');
    await page.waitForFunction(() => !document.querySelectorAll('.ticket-check:checked').length && document.querySelector('#bulkAssignBtn').disabled);
    assert.deepEqual(writes[0], { path: '/api/v1/tickets/assign', body: { ticket_ids: Array.from({ length: 20 }, (_, i) => String(i + 1)), assignee_id: 'trainee' } });
    await page.locator('.ticket-check').first().check();
    await assign.selectOption('');
    await page.waitForFunction(() => !document.querySelectorAll('.ticket-check:checked').length && document.querySelector('#bulkAssignBtn').disabled);
    assert.equal(writes[1].body.assignee_id, null);
    await page.locator('#colorEnabledInput').uncheck();
    assert.equal(await page.locator('tr.ticket-row').first().evaluate(n => getComputedStyle(n).backgroundImage), 'none');
    await page.locator('#colorEnabledInput').check();
    assert.match(await page.locator('tr.ticket-row').first().evaluate(n => getComputedStyle(n).backgroundImage), /90deg.*70%/);
  });
}

test('assignment failure preserves selection and permits retry', async t => {
  const { page, writes, rejectAssignment } = await setup(t, 'light', 1440);
  await page.locator('.ticket-check').first().check();
  rejectAssignment();
  await page.locator('#bulkAssignBtn').selectOption('agent');
  await page.waitForFunction(() => !document.querySelector('#bulkAssignBtn').disabled);
  assert.equal(writes[0].body.assignee_id, 'agent');
  assert.equal(await page.locator('.ticket-check:checked').count(), 1);
  assert.equal(await page.locator('#bulkAssignBtn').inputValue(), '__placeholder__');
  assert.equal(await page.locator('#errorModal').isVisible(), true);
});

test('row gradients preserve the base geometry and change only the first color', () => {
  const css = fs.readFileSync('api/app/static/desk-v2.css', 'utf8');
  for (const kind of ['request', 'task', 'group-task', 'internal-task', 'frozen', 'closed']) {
    assert.ok(css.includes(`& tr.ticket-color-${kind} { background: linear-gradient(90deg, var(--ticket-type-${kind}-bg), var(--desk-surface) 70%); }`));
  }
});
