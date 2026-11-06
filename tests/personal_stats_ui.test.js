const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });
const stat = n => ({ tickets: n, tasks: n + 1, total: n * 2 + 1 });
const stats = { calendar: { day: stat(1), week: stat(2), month: stat(3), year: stat(4) }, rolling: { week: stat(5), month: stat(6), year: stat(7) }, total: stat(8), legacy: 99 };
async function setup(t, role = 'agent', theme = 'light', width = 1000, ...legacyValue) {
  const page = await browser.newPage({ viewport: { width, height: 800 } });
  t.after(() => page.close());
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  const calls = [];
  let fail = false, release = null;
  await page.route('http://desk.test/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith('/static/')) {
      const file = path.resolve('api/app', url.pathname.slice(1));
      return route.fulfill({ body: fs.readFileSync(file), contentType: { '.css': 'text/css', '.js': 'text/javascript', '.ttf': 'font/ttf', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream' });
    }
    if (url.pathname.startsWith('/api/')) {
      let payload = {};
      if (url.pathname.endsWith('/auth/session')) payload = { real_user: { id: 'u1', role, full_name: 'Agent' }, app_timezone: 'Europe/Moscow' };
      if (url.pathname.endsWith('/agents')) payload = [];
      if (url.pathname.endsWith('/completion-stats')) {
        calls.push(url.pathname);
        if (release) await new Promise(resolve => { release = resolve; });
        if (fail) return route.fulfill({ status: 500, body: 'error' });
        payload = { ...stats, legacy: legacyValue.length ? legacyValue[0] : 99 };
      }
      return route.fulfill({ json: payload });
    }
    // Real application shell markup, scripts initialized explicitly below.
    const html = fs.readFileSync(url.pathname === '/reviews' ? 'api/app/templates/reviews.html' : 'api/app/templates/tickets.html', 'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '');
    return route.fulfill({ contentType: 'text/html', body: html });
  });
  page.setDefaultTimeout(7000);
  await page.goto('http://desk.test/tickets');
  await page.evaluate(theme => { document.body.dataset.theme = theme; }, theme);
  await page.addScriptTag({ url: '/static/sidebar.js' });
  await page.addScriptTag({ url: '/static/app_shell.js' });
  await page.evaluate(() => { window.HelpdeskAppShell.registerPage('reviews', () => ({ init() {} })); return window.HelpdeskSidebar.init(); });
  return { page, calls, errors, fail: () => { fail = true; }, hold: () => { release = true; }, release: () => { const resolve = release; release = null; resolve(); } };
}
async function open(page) {
  await page.locator('[data-sidebar-user-menu-toggle]').click();
  await page.locator('[data-sidebar-user-menu-item="statistics"]').click();
}
for (const role of ['agent', 'trainee', 'admin', 'chiki_piki', 'client']) test(`statistics visibility: ${role}`, async t => {
  const { page, calls, errors } = await setup(t, role);
  await page.locator('[data-sidebar-user-menu-toggle]').click();
  const item = page.locator('[data-sidebar-user-menu-item="statistics"]');
  assert.equal(await item.isVisible(), ['agent', 'trainee'].includes(role));
  assert.deepEqual(await page.locator('[data-sidebar-user-menu-item]').evaluateAll(items => items.map(item => item.dataset.sidebarUserMenuItem)), ['profile', 'bot-notifications', 'feedback', 'statistics', 'logout']);
  assert.equal(calls.length, 0);
  assert.deepEqual(errors, []);
});
for (const theme of ['light', 'dark']) test(`statistics modes, repeat open, AppShell, close and sizing: ${theme}`, async t => {
  const { page, calls, errors } = await setup(t, 'agent', theme, 375);
  await open(page);
  const dialog = page.locator('.personal-stats-dialog');
  await dialog.locator('table').waitFor({ state: 'visible' });
  const values = () => dialog.locator('tbody tr').evaluateAll(rows => rows.map(row => Array.from(row.querySelectorAll('td'), cell => cell.textContent)));
  assert.deepEqual(await values(), [1,2,3,4,8].map(n => [String(n), String(n+1), String(n*2+1)]));
  assert.equal(await dialog.locator('p').last().textContent(), 'Helpdeskeddy legacy count: 99');
  await dialog.locator('input').uncheck();
  assert.deepEqual(await values(), [1,5,6,7,8].map(n => [String(n), String(n+1), String(n*2+1)]));
  await page.evaluate(async () => {
    await window.HelpdeskSidebar.init();
    document.querySelector('[data-sidebar-user-menu-item="statistics"]').click();
  });
  assert.equal(calls.length, 1);
  assert.equal(await dialog.count(), 1);
  const box = await dialog.boundingBox();
  assert.ok(box.width <= 375 && box.x >= 0);
  const color = await dialog.evaluate(el => getComputedStyle(el).backgroundColor);
  assert.equal(color, theme === 'dark' ? 'rgb(32, 37, 53)' : 'rgb(255, 254, 253)');
  await page.keyboard.press('Escape');
  assert.equal(await dialog.count(), 0);
  await page.evaluate(() => window.HelpdeskAppShell.navigate('/reviews'));
  await page.waitForURL('**/reviews');
  await open(page);
  await dialog.locator('table').waitFor({ state: 'visible' });
  assert.equal(await dialog.locator('input').isChecked(), true);
  assert.equal(calls.length, 2);
  assert.equal(await dialog.count(), 1);
  await dialog.locator('button').click();
  assert.equal(await dialog.count(), 0);
  await open(page);
  await dialog.locator('table').waitFor({ state: 'visible' });
  await page.mouse.click(1, 1);
  assert.equal(await dialog.count(), 0);
  assert.equal(calls.length, 3);
  assert.deepEqual(errors, []);
});
test('statistics loading, API error, retry on reopen', async t => {
  const ctx = await setup(t);
  const { page, calls, errors } = ctx;
  ctx.hold();
  await open(page);
  const dialog = page.locator('.personal-stats-dialog');
  assert.equal(await dialog.locator('[role="status"]').textContent(), 'Загрузка…');
  assert.equal(await dialog.locator('table').isVisible(), false);
  ctx.fail(); ctx.release();
  await page.getByText('Не удалось загрузить статистику.', { exact: false }).waitFor();
  await page.keyboard.press('Escape');
  await open(page);
  await page.getByText('Не удалось загрузить статистику.', { exact: false }).waitFor();
  assert.equal(calls.length, 2);
  assert.deepEqual(errors, []);
});

for (const theme of ['light', 'dark']) for (const width of [320, 375, 1440]) test(`statistics readable layout without horizontal overflow: ${theme}/${width}`, async t => {
  const { page, errors } = await setup(t, 'agent', theme, width, 123456789);
  await open(page);
  const dialog = page.locator('.personal-stats-dialog');
  await dialog.locator('table').waitFor({ state: 'visible' });
  const layout = await dialog.evaluate(el => {
    const table = el.querySelector('table'), input = el.querySelector('input');
    const legacy = el.querySelector('p:last-of-type'), label = el.querySelector('label > span');
    return { labelFont: parseFloat(getComputedStyle(label).fontSize), legacyFont: parseFloat(getComputedStyle(legacy).fontSize),
      legacyBackground: getComputedStyle(legacy).backgroundColor, surface: getComputedStyle(el).backgroundColor,
      soft: getComputedStyle(el).getPropertyValue('--desk-soft').trim(), legacyPadding: parseFloat(getComputedStyle(legacy).paddingLeft),
      legacyRadius: parseFloat(getComputedStyle(legacy).borderRadius), toggleWidth: input.getBoundingClientRect().width, toggleHeight: input.getBoundingClientRect().height,
      width: el.getBoundingClientRect().width, scrollWidth: el.scrollWidth, clientWidth: el.clientWidth,
      tableWidth: table.getBoundingClientRect().width, tableFont: parseFloat(getComputedStyle(table).fontSize),
      font: parseFloat(getComputedStyle(el).fontSize), weight: Number(getComputedStyle(el).fontWeight),
      padding: parseFloat(getComputedStyle(el).paddingLeft), toggle: input.classList.contains('desk-toggle'),
      appearance: getComputedStyle(input).appearance, cellsFit: [...el.querySelectorAll('th, td')].every(cell => cell.scrollWidth <= cell.clientWidth) };
  });
  assert.ok(layout.scrollWidth <= layout.clientWidth, JSON.stringify(layout));
  assert.ok(layout.cellsFit, JSON.stringify(layout));
  assert.ok(layout.tableWidth <= layout.clientWidth);
  assert.ok(layout.weight >= 600);
  assert.equal(layout.labelFont, layout.font * .75);
  assert.equal(layout.legacyFont, layout.font * .75);
  assert.notEqual(layout.legacyBackground, layout.surface);
  assert.equal(layout.legacyBackground, theme === 'dark' ? 'rgb(38, 43, 61)' : 'rgb(246, 244, 249)');
  assert.equal(layout.legacyPadding, 16);
  assert.equal(layout.legacyRadius, 12);
  assert.equal(layout.toggleWidth, 40);
  assert.equal(layout.toggleHeight, 24);
  assert.equal(layout.toggle, true);
  assert.equal(layout.appearance, 'none');
  if (width === 1440) {
    assert.equal(layout.width, 960);
    assert.equal(layout.font, 32);
    assert.equal(layout.tableFont, 28);
    assert.equal(layout.padding, 40);
  }
  assert.deepEqual(errors, []);
});
for (const legacy of [0, null, undefined, '', '  ', 7]) test(`statistics conditional legacy: ${JSON.stringify(legacy)}`, async t => {
  const { page } = await setup(t, 'agent', 'light', 1000, legacy);
  await open(page);
  const dialog = page.locator('.personal-stats-dialog');
  await dialog.locator('table').waitFor({ state: 'visible' });
  assert.equal(await dialog.locator('p').last().isVisible(), legacy === 7);
  if (legacy === 7) assert.equal(await dialog.locator('p').last().textContent(), 'Helpdeskeddy legacy count: 7');
});
