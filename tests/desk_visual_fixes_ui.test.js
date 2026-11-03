const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });

async function setup(t, screen, theme, width) {
  const page = await browser.newPage({ viewport: { width, height: 900 } });
  t.after(() => page.close());
  const errors = [], calls = [];
  page.on('pageerror', e => errors.push(e.message));
  t.after(() => assert.deepEqual(errors, []));
  const me = { id: 'admin', role: 'admin', login: 'admin', full_name: 'Admin' };
  const user = { id: 'employee', full_name: 'Сотрудник', availability_status: 'online', updated_at: new Date().toISOString() };
  await page.route('http://desk.test/**', route => {
    const req = route.request(), p = new URL(req.url()).pathname;
    if (p.startsWith('/static/')) return route.fulfill({ body: fs.readFileSync(path.resolve('api/app', p.slice(1))),
      contentType: { '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' }[path.extname(p)] || 'application/octet-stream' });
    if (p === '/statuses') return route.fulfill({ contentType: 'text/html', body: fs.readFileSync('api/app/templates/statuses.html', 'utf8') });
    if (p === '/menu') return route.fulfill({ contentType: 'text/html', body: `<html><head><meta charset="utf-8"><link rel="stylesheet" href="/static/styles.css"></head><body><div class="layout"><main id="appContent" class="main" data-shell-page="settings-bot-buttons"><button id="themeToggleBtn">Оформление</button></main></div><script src="/static/theme.js"></script><script src="/static/ticket_api.js"></script><script src="/static/bot_buttons_menu_view.js"></script></body></html>` });
    let data = {};
    if (req.method() === 'PATCH' && p === '/api/v1/users/employee/status') {
      calls.push(req.postDataJSON()); user.availability_status = req.postDataJSON().availability_status;
    } else if (req.method() === 'POST') { calls.push(req.postDataJSON()); data = { menu_name: 'new-menu' }; }
    else if (p.endsWith('/status-board')) data = [user];
    else if (p.endsWith('/auth/session')) data = { real_user: me, effective_user: me, is_real_admin: true };
    else if (p.endsWith('/users/me') || p.endsWith('/users/me/profile')) data = me;
    else if (p.endsWith('/preferences')) data = { theme_preference: theme };
    else if (p.endsWith('/agents') || p.endsWith('/menu-types') || p.endsWith('/archived')) data = [];
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
  });
  await page.goto(`http://desk.test/${screen}`);
  await page.waitForFunction(theme => document.body.dataset.theme === theme, theme);
  return { page, calls };
}

for (const theme of ['light', 'dark']) for (const width of [1366, 390]) {
  test(`employee selected status follows server state with the agent outline: ${theme}/${width}`, async t => {
    const { page, calls } = await setup(t, 'statuses', theme, width);
    await page.locator('.status-board-btn.active').waitFor();
    for (const status of ['online', 'on_ticket', 'waiting', 'lunch', 'offline']) {
      if (status !== 'online') await page.locator(`.status-board-btn[data-status="${status}"]`).click();
      await page.waitForFunction(status => {
        const selected = document.querySelector('.status-board-btn.active');
        return selected?.dataset.status === status && !selected.disabled;
      }, status);
      assert.equal(await page.locator('.status-board-btn.active').count(), 1);
      const styles = await page.locator('.status-board-btn').evaluateAll(buttons => buttons.map(el => {
        const s = getComputedStyle(el);
        return { active: el.classList.contains('active'), weight: s.fontWeight, shadow: s.boxShadow, color: s.color, border: s.borderTopColor };
      }));
      for (const s of styles) {
        if (s.active) { assert.equal(s.weight, '700'); assert.ok(s.shadow.includes('5px')); assert.equal(s.border, s.color); }
        else { assert.equal(s.weight, '600'); assert.equal(s.shadow, 'none'); }
      }
    }
    assert.deepEqual(calls, ['on_ticket', 'waiting', 'lunch', 'offline'].map(availability_status => ({ availability_status })));
    if (process.env.TICKET_DIALOG_SCREENSHOTS) await page.locator('.status-user-column').screenshot({ path: path.join(process.env.TICKET_DIALOG_SCREENSHOTS, `statuses-${theme}-${width}.png`) });
  });

  test(`menu creation keeps validation, selection, cancel and v2 layout: ${theme}/${width}`, async t => {
    const { page, calls } = await setup(t, 'menu', theme, width);
    async function open(options = ['Офис', 'Склад']) {
      await page.evaluate(options => {
        window.menuResult = 'pending';
        const ctx = { state: { freeDestinationOptions: options }, escapeHtml: TicketApi.escapeHtml, api: TicketApi.api,
          openMenu: async name => { window.menuResult = name; } };
        BotButtonsMenuView.createMenuType(ctx).then(() => { if (window.menuResult === 'pending') window.menuResult = null; });
      }, options);
    }
    await open();
    const dialog = page.locator('.bot-create-menu-dialog');
    const style = await dialog.evaluate(el => {
      const r = el.getBoundingClientRect(), s = getComputedStyle(el);
      return { radius: s.borderRadius, height: r.height, centered: Math.abs(r.y + r.height / 2 - innerHeight / 2) < 1,
        fits: r.x >= 0 && r.right <= innerWidth, font: getComputedStyle(el.querySelector('h2')).fontSize,
        rowBackground: getComputedStyle(el.querySelector('.bot-create-menu-option')).backgroundColor };
    });
    assert.equal(style.radius, '24px'); assert.equal(style.centered, true); assert.equal(style.fits, true);
    assert.ok(style.height < 650); assert.equal(style.font, '28px'); assert.equal(style.rowBackground, 'rgba(0, 0, 0, 0)');
    assert.equal(await page.locator('.bot-create-menu-option input').first().evaluate(el => getComputedStyle(el).appearance), 'none');
    await page.locator('#applyCreateMenuBtn').click();
    assert.equal(await page.locator('#botCreateMenuError').textContent(), 'Введите название меню.');
    assert.deepEqual(calls, []);
    await page.locator('#cancelCreateMenuBtn').click(); assert.equal(await dialog.count(), 0);
    await open(); await page.locator('.bot-create-menu-overlay').click({ position: { x: 2, y: 2 } });
    assert.equal(await dialog.count(), 0); assert.deepEqual(calls, []);
    await open();
    await page.locator('#botCreateMenuNameInput').fill(' Новое меню ');
    await page.getByLabel('Склад', { exact: true }).check();
    if (process.env.TICKET_DIALOG_SCREENSHOTS) await dialog.screenshot({ path: path.join(process.env.TICKET_DIALOG_SCREENSHOTS, `menu-${theme}-${width}.png`) });
    await page.locator('#botCreateMenuNameInput').press('Enter');
    await page.waitForFunction(() => window.menuResult === 'new-menu');
    assert.deepEqual(calls, [{ display_name: 'Новое меню', destinations: ['Склад'] }]);
    await open([]); await page.locator('#botCreateMenuNameInput').fill('Без привязки');
    await page.locator('#applyCreateMenuBtn').click();
    await page.waitForFunction(() => window.menuResult === 'new-menu');
    assert.deepEqual(calls.at(-1), { display_name: 'Без привязки', destinations: [] });
  });
}
