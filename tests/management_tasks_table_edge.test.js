const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });
const tabs = ['active', 'scheduled', 'recurring', 'completed'];
const long = 'Константинопольский Александр Александрович';
const rows = [
  { title: 'Я задача', materialized_at: '2026-01-31T09:00:00', scheduled_for: '2026-01-31T09:00:00', next_occurrence_at: '2026-01-31T09:00:00', closed_at: '2026-01-31T09:00:00', deadline_at: null },
  { title: 'А задача', materialized_at: '2026-02-01T09:00:00Z', scheduled_for: '2026-02-01T09:00:00Z', next_occurrence_at: '2026-02-01T09:00:00Z', closed_at: '2026-02-01T09:00:00Z', deadline_at: '2026-01-31T09:00:00Z' },
  { title: '', deadline_at: '2026-02-01T09:00:00Z' },
].map((r, i) => ({ ...r, definition_id: `d${i}`, ticket_id: `t${i}`, current_assignee: { full_name: i ? 'Иван' : long }, assignee: { full_name: i ? 'Иван' : long }, responsible_admin: { full_name: long }, ticket_status: 'pending', assignment_mode: 'direct', recurrence_mode: 'selected_weekdays', recurrence_config: { weekdays: [1, 3, 5], time: '10:00' }, state: i === 0 ? 'paused' : 'active' }));
async function setup(t, theme = 'light') {
  const page = await browser.newPage({ viewport: { width: 1920, height: 1000 } });
  t.after(() => page.close());
  const errors = [], writes = [], saved = { admin1: {}, admin2: {} };
  let admin = 'admin1';
  page.on('pageerror', e => errors.push(e.message));
  t.after(() => assert.deepEqual(errors, []));
  await page.route('http://desk.test/**', route => {
    const req = route.request(), p = new URL(req.url()).pathname;
    if (p.startsWith('/static/')) {
      const file = path.resolve('api/app', p.slice(1));
      return route.fulfill({ body: fs.readFileSync(file), contentType: { '.css': 'text/css', '.js': 'text/javascript', '.ttf': 'font/ttf', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream' });
    }
    if (!p.startsWith('/api/')) return route.fulfill({ contentType: 'text/html', body: fs.readFileSync('api/app/templates/management_tasks.html', 'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '') });
    if (p.endsWith('/preferences')) {
      if (req.method() === 'PATCH') { const value = req.postDataJSON().management_tasks_sort; writes.push(value); Object.assign(saved[admin], value); }
      return route.fulfill({ json: { management_tasks_sort: saved[admin] } });
    }
    if (p.endsWith('/options')) return route.fulfill({ json: { current_admin_id: admin, app_timezone: 'Europe/Moscow' } });
    return route.fulfill({ json: Object.fromEntries(tabs.map(tab => [tab, rows])) });
  });
  async function boot() {
    await page.goto('http://desk.test/management/tasks');
    await page.evaluate(theme => {
      document.body.dataset.theme = theme;
      document.body.dataset.sidebarReady = '1';
      window.HelpdeskSidebar = { init: async () => ({ role: 'admin' }) };
      window.HelpdeskAppShell = { registerPage: (_, init) => { window.initTasks = init; } };
    }, theme);
    await page.addScriptTag({ path: 'api/app/static/management_tasks.js' });
    await page.evaluate(() => { window.taskPage = window.initTasks({ root: document.querySelector('#appContent') }); });
    await page.waitForSelector('[data-task-sort]:not([disabled])');
  }
  await boot();
  return { page, writes, saved, boot, switchAdmin: value => { admin = value; } };
}
async function titles(page, tab) { return page.locator(`[data-internal-task-panel="${tab}"] tbody tr td:first-child`).allTextContents(); }
for (const theme of ['light', 'dark']) test(`Edge: all task tabs sorting, persistence and geometry (${theme})`, async t => {
  const { page, writes, saved, boot, switchAdmin } = await setup(t, theme);
  for (const tab of tabs) {
    await page.locator(`[data-internal-task-tab="${tab}"]`).click();
    const panel = page.locator(`[data-internal-task-panel="${tab}"]`);
    assert.deepEqual(await titles(page, tab), ['Я задача', 'А задача', '—']);
    const headers = panel.locator('thead th');
    assert.equal(await headers.last().innerText(), 'Действия');
    assert.equal(await headers.last().locator('button').count(), 0);
    // Exercise every sortable column and both directions.
    for (const button of await panel.locator('[data-task-sort]').all()) {
      await button.click(); assert.equal(await button.locator('..').getAttribute('aria-sort'), 'ascending');
      await button.click(); assert.equal(await button.locator('..').getAttribute('aria-sort'), 'descending');
    }
    const date = { active: 'materialized_at', scheduled: 'scheduled_for', recurring: 'next_occurrence_at', completed: 'closed_at' }[tab];
    await panel.locator(`[data-task-sort="${date}"]`).click();
    assert.deepEqual(await titles(page, tab), ['Я задача', 'А задача', '—']);
    await panel.locator(`[data-task-sort="${date}"]`).click();
    assert.deepEqual(await titles(page, tab), ['А задача', 'Я задача', '—']);
    await panel.locator('[data-task-sort="title"]').click();
    assert.deepEqual(await titles(page, tab), ['А задача', 'Я задача', '—']);
    await panel.locator('[data-task-sort="title"]').click();
    assert.deepEqual(await titles(page, tab), ['Я задача', 'А задача', '—']);
    for (const width of [1920, 1280, 900, 2200]) {
      await page.setViewportSize({ width, height: 1000 });
      await page.waitForTimeout(80);
      const geo = await panel.evaluate(panel => {
        const table = panel.querySelector('table'), scroll = table.parentElement;
        const cells = [...table.querySelectorAll('th,td')];
        return {
          title: table.tBodies[0].rows[0].cells[0].getBoundingClientRect().width,
          overflow: cells.slice(1).filter(c => c.cellIndex !== 0 && c.scrollWidth > c.clientWidth + 1).map(c => c.textContent),
          clippedControls: [...table.querySelectorAll('td:last-child button,td:last-child a')].filter(b => { const r=b.getBoundingClientRect(), c=b.closest('td').getBoundingClientRect(); return r.left<c.left || r.right>c.right; }).length,
          table: table.getBoundingClientRect().width, scroll: scroll.clientWidth, scrollWidth: scroll.scrollWidth,
        };
      });
      assert.ok(geo.title >= 219, JSON.stringify(geo));
      assert.deepEqual(geo.overflow, [], JSON.stringify(geo));
      assert.equal(geo.clippedControls, 0, JSON.stringify(geo));
      if (geo.table > geo.scroll) assert.ok(geo.scrollWidth >= geo.table - 1);
    }
  }
  await page.waitForFunction(() => document.querySelectorAll('.management-tasks-width-probe').length === 0);
  await page.locator('[data-internal-task-scope="mine"]').click();
  await page.waitForTimeout(100);
  assert.deepEqual(await titles(page, 'completed'), ['Я задача', 'А задача', '—']);
  await page.waitForTimeout(100);
  assert.equal(Object.keys(saved.admin1).length, 4);
  assert.ok(writes.length >= 40);
  await boot();
  for (const tab of tabs) {
    await page.locator(`[data-internal-task-tab="${tab}"]`).click();
    assert.equal(await page.locator(`[data-internal-task-panel="${tab}"] th`).first().getAttribute('aria-sort'), 'descending');
    assert.deepEqual(await titles(page, tab), ['Я задача', 'А задача', '—']);
  }
  switchAdmin('admin2'); await boot();
  for (const tab of tabs) assert.equal(await page.locator(`[data-internal-task-panel="${tab}"] th`).first().getAttribute('aria-sort'), 'none');
});

test('Edge: refreshed rows resize columns; workspace resize and failed saves stay observable', async t => {
  const { page } = await setup(t);
  await page.locator('[data-internal-task-tab="recurring"]').click();
  const panel = page.locator('[data-internal-task-panel="recurring"]');
  await panel.locator('[data-task-sort="title"]').click();
  const before = await panel.locator('tbody td').nth(2).evaluate(c => c.getBoundingClientRect().width);
  await page.route('**/api/v1/management/internal-tasks/overview?scope=mine', route => route.fulfill({ json: Object.fromEntries(tabs.map(tab => [tab, rows.map(r => ({ ...r, responsible_admin: {full_name: long.repeat(2)} }))])) }));
  await page.locator('[data-internal-task-scope="mine"]').click();
  await page.waitForFunction(() => document.querySelector('[data-internal-task-panel="recurring"] tbody tr td:nth-child(3)').textContent.length > 60);
  const after = await panel.locator('tbody td').nth(2).evaluate(c => c.getBoundingClientRect().width);
  assert.ok(after > before + 100);
  assert.deepEqual(await titles(page, 'recurring'), ['А задача', 'Я задача', '—']);
  const oldTitleWidth = await panel.locator('tbody td').first().evaluate(c => c.getBoundingClientRect().width);
  await page.evaluate(() => { document.querySelector('#appContent').style.width = '2800px'; });
  await page.waitForFunction(oldWidth => document.querySelector('[data-internal-task-panel="recurring"] tbody td').getBoundingClientRect().width > oldWidth + 20, oldTitleWidth);
  await page.route('**/api/v1/users/me/preferences', route => route.request().method() === 'PATCH' ? route.fulfill({status: 503, body: 'Unavailable'}) : route.fallback());
  await panel.locator('[data-task-sort="title"]').click();
  await page.waitForFunction(() => document.querySelector('#internalTasksValidation').textContent.includes('Не удалось сохранить сортировку'));
  assert.equal(await page.locator('#internalTasksValidation').isVisible(), true);
});
