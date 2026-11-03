const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });

test('ticket API and page script versions agree for direct and AppShell navigation', () => {
  const template = fs.readFileSync('api/app/templates/ticket.html', 'utf8');
  const shell = fs.readFileSync('api/app/static/app_shell.js', 'utf8');
  for (const name of ['ticket_api', 'ticket']) {
    const resource = template.match(new RegExp(`/static/${name}\\.js\\?v=[^"']+`))[0];
    assert.ok(shell.includes(resource));
  }
});

async function setup(t, status = 200, theme = 'light', width = 1366) {
  const page = await browser.newPage({ viewport: { width, height: 900 } });
  t.after(() => page.close());
  const errors = [], responses = [], posts = [];
  const me = { id: 'admin', role: 'admin', login: 'admin', full_name: 'Admin' };
  const card = { id: '123', subject: 'Test ticket', status: 'pending', workflow_type: 'ticket',
    client_profile: {}, messages: [], quick_actions_used: {}, intake_state: 'ready' };
  const fixture = { status, message: status === 500 ? 'Тикет не найден' : 'API error', posts, responses, page, sendByEnter: false, preferences: [] };
  page.on('pageerror', e => errors.push(e.message));
  page.on('response', r => { if (r.url().endsWith('/api/v1/tickets/123')) responses.push(r.status()); });
  t.after(() => assert.deepEqual(errors, []));
  await page.addInitScript(theme => localStorage.setItem('hd_theme', theme), theme);
  await page.route('http://desk.test/**', route => {
    const req = route.request(), p = new URL(req.url()).pathname;
    if (p.startsWith('/static/')) return route.fulfill({ body: fs.readFileSync(path.resolve('api/app', p.slice(1))),
      contentType: { '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' }[path.extname(p)] || 'application/octet-stream' });
    if (p === '/tickets/123') return route.fulfill({ contentType: 'text/html', body: fs.readFileSync('api/app/templates/ticket.html', 'utf8') });
    if (p === '/tickets') return route.fulfill({ contentType: 'text/html', body: '<main id="appContent" data-shell-page="tickets-list">Ticket list</main>' });
    let data = {}, code = 200;
    if (req.method() === 'POST') { posts.push(p); data = { closed: 1, already_closed: 0 }; }
    else if (p === '/api/v1/tickets/123') {
      code = fixture.status;
      data = code === 200 ? card : { detail: { error: { code: code === 404 ? 'ticket_not_found' : 'forbidden_ticket_access', message: fixture.message } } };
    } else if (p.endsWith('/auth/session')) data = { real_user: me, effective_user: me, is_real_admin: true };
    else if (p.endsWith('/users/me') || p.endsWith('/users/me/profile')) data = me;
    else if (p.endsWith('/users/me/preferences')) {
      if (req.method() === 'PATCH') {
        fixture.sendByEnter = req.postDataJSON().send_by_enter;
        fixture.preferences.push(fixture.sendByEnter);
      }
      data = { theme_preference: theme, send_by_enter: fixture.sendByEnter };
    }
    else if (p.endsWith('/agents') || p.endsWith('/by-role') || p.endsWith('/task-panel')) data = [];
    else if (p.endsWith('/client-card-view-config')) data = { blocks: [] };
    return route.fulfill({ status: code, contentType: 'application/json', body: JSON.stringify(data) });
  });
  await page.goto('http://desk.test/tickets/123');
  await page.locator(status === 200 ? '#ticketSubject' : '#errorModal:not(.hidden)').waitFor();
  if (status === 200) await page.waitForFunction(() => document.querySelector('#ticketSubject').textContent === 'Test ticket');
  await page.waitForFunction(theme => document.body.dataset.theme === theme, theme);
  return fixture;
}

async function checkCompact(page, selector) {
  const dialog = page.locator(selector), viewport = page.viewportSize();
  const first = await dialog.boundingBox();
  await page.setViewportSize({ ...viewport, height: viewport.height + 300 });
  const second = await dialog.boundingBox();
  assert.ok(Math.abs(first.height - second.height) < 1, 'height follows content, not viewport');
  assert.ok(Math.abs(second.y + second.height / 2 - (viewport.height + 300) / 2) < 1, 'vertically centered');
  assert.ok(first.height < viewport.height * .8, 'short message stays compact');
  await page.setViewportSize(viewport);
}

for (const status of [403, 404]) for (const close of ['button', 'escape', 'backdrop']) {
  test(`initial ${status}: ${close} returns a directly opened ticket to the list`, async t => {
    const { page, responses } = await setup(t, status);
    assert.ok(responses.includes(status));
    assert.equal(await page.locator('#errorText').textContent(), status === 404 ? 'Тикет не найден' : 'Нет доступа к заявке');
    assert.equal(await page.locator('#errorText').evaluate(el => el.tagName), 'DIV');
    await checkCompact(page, '#errorModal .desk-dialog');
    if (process.env.TICKET_DIALOG_SCREENSHOTS && close === 'button') await page.locator('#errorModal .desk-dialog').screenshot({
      path: path.join(process.env.TICKET_DIALOG_SCREENSHOTS, `error-${status}.png`) });
    if (close === 'button') await page.locator('#okErrorBtn').click();
    else if (close === 'escape') await page.keyboard.press('Escape');
    else await page.locator('#errorModal').click({ position: { x: 2, y: 2 } });
    await page.waitForURL('http://desk.test/tickets');
    await page.locator('[data-shell-page="tickets-list"]').waitFor();
  });
}

test('an opening 500 with a misleading message stays on the ticket', async t => {
  const { page } = await setup(t, 500);
  await page.locator('#okErrorBtn').click();
  assert.equal(page.url(), 'http://desk.test/tickets/123');
});

test('errors after opening and copying details keep the card open', async t => {
  const { page } = await setup(t);
  await page.evaluate(() => {
    window.copied = '';
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async text => { window.copied = text; } } });
  });
  await page.route('**/api/v1/tickets/admin/close', route => route.fulfill({ status: 403,
    contentType: 'application/json', body: JSON.stringify({ detail: { error: { message: 'Action denied\nDetails' } } }) }));
  await page.locator('#adminForceCloseBtn').click();
  await page.locator('#confirmAdminActionBtn').click();
  await page.locator('#errorModal:not(.hidden)').waitFor();
  await page.locator('#copyErrorBtn').click();
  assert.equal(await page.evaluate(() => window.copied), 'Action denied\nDetails');
  await page.evaluate(() => {
    navigator.clipboard.writeText = async () => { throw Error('Clipboard unavailable'); };
    document.execCommand = command => { window.copied = command === 'copy' ? getSelection().toString() : ''; return true; };
    window.copied = '';
  });
  await page.locator('#copyErrorBtn').click();
  assert.equal(await page.evaluate(() => window.copied), 'Action denied\nDetails');
  await page.keyboard.press('Escape');
  assert.equal(page.url(), 'http://desk.test/tickets/123');
  assert.equal(await page.locator('#ticketSubject').textContent(), 'Test ticket');
});

for (const status of [403, 404]) test(`card refresh ${status} after opening does not redirect`, async t => {
  const fixture = await setup(t);
  fixture.status = status;
  await fixture.page.locator('#adminForceCloseBtn').click();
  await fixture.page.locator('#confirmAdminActionBtn').click();
  await fixture.page.locator('#errorModal:not(.hidden)').waitFor();
  await fixture.page.locator('#okErrorBtn').click();
  assert.equal(fixture.page.url(), 'http://desk.test/tickets/123');
  assert.equal(await fixture.page.locator('#ticketSubject').textContent(), 'Test ticket');
});

for (const theme of ['light', 'dark']) for (const width of [1366, 390]) {
  test(`confirmations preserve cancel/confirm and shared dialog style: ${theme}/${width}`, async t => {
    const { page, posts } = await setup(t, 200, theme, width);
    for (const [open, modal, cancel, confirm, endpoint] of [
      ['markSpecialBtn', 'specialConfirmModal', 'closeSpecialConfirmBtn', 'confirmSpecialBtn', '/api/v1/tickets/123/special'],
      ['adminForceCloseBtn', 'adminActionModal', 'cancelAdminActionBtn', 'confirmAdminActionBtn', '/api/v1/tickets/admin/close'],
    ]) {
      await page.locator(`#${open}`).click();
      await checkCompact(page, `#${modal} .desk-dialog`);
      if (modal === 'specialConfirmModal') assert.equal(await page.locator('#confirmSpecialBtn').evaluate(el =>
        getComputedStyle(el).backgroundColor === getComputedStyle(el).getPropertyValue('--desk-primary').trim()), true);
      const style = await page.locator(`#${modal} .desk-dialog`).evaluate(el => {
        const s = getComputedStyle(el), r = el.getBoundingClientRect();
        return { radius: s.borderRadius, font: getComputedStyle(el.querySelector('h2')).fontSize,
          fits: r.x >= 0 && r.right <= innerWidth, actions: getComputedStyle(el.querySelector('.desk-dialog-actions')).justifyContent };
      });
      assert.deepEqual(style, { radius: '24px', font: '28px', fits: true, actions: 'flex-end' });
      assert.equal(await page.locator(`#${modal} .modal-head, #${modal} .modal-foot`).count(), 0);
      if (process.env.TICKET_DIALOG_SCREENSHOTS) await page.locator(`#${modal} .desk-dialog`).screenshot({
        path: path.join(process.env.TICKET_DIALOG_SCREENSHOTS, `${modal}-${theme}-${width}.png`) });
      const before = posts.length;
      await page.locator(`#${cancel}`).click();
      assert.equal(posts.length, before);
      await page.locator(`#${open}`).click();
      await page.locator(`#${modal}`).click({ position: { x: 2, y: 2 } });
      assert.equal(await page.locator(`#${modal}`).isVisible(), false);
      await page.locator(`#${open}`).click();
      await page.locator(`#${confirm}`).click();
      await page.waitForFunction(id => document.getElementById(id).classList.contains('hidden'), modal);
      assert.equal(posts.at(-1), endpoint);
    }
  });
}

test('admin delete cancellation preserves the ticket; confirmation deletes and returns to the list', async t => {
  const { page, posts } = await setup(t);
  await page.locator('#adminDeleteBtn').click();
  assert.equal(await page.locator('#adminActionModalTitle').textContent(), 'Удаление заявки');
  assert.equal(await page.locator('#confirmAdminActionBtn').evaluate(el => el.classList.contains('danger')), true);
  await page.keyboard.press('Escape');
  assert.deepEqual(posts, []);
  await page.locator('#adminDeleteBtn').click();
  await page.locator('#cancelAdminActionBtn').click();
  assert.deepEqual(posts, []);
  await page.locator('#adminDeleteBtn').click();
  await page.locator('#confirmAdminActionBtn').click();
  await page.waitForURL('http://desk.test/tickets?tab=active');
  assert.deepEqual(posts, ['/api/v1/tickets/admin/delete']);
});

for (const theme of ['light', 'dark']) test(`Enter toggle saves the preference and preserves keyboard behavior: ${theme}`, async t => {
  const { page, posts, preferences } = await setup(t, 200, theme);
  const toggle = page.locator('#sendByEnterCheckbox'), input = page.locator('#ticketMessageBody');
  assert.equal(await toggle.evaluate(el => getComputedStyle(el).appearance), 'none');
  assert.equal(await page.locator('.composer-enter-settings #sendHotkeyHint').count(), 1);
  const settings = await page.locator('.composer-enter-settings').boundingBox();
  const comment = await page.locator('#modeCommentBtn').boundingBox();
  assert.ok(settings.x - comment.x - comment.width >= 6);
  assert.ok(settings.height <= 48);
  await toggle.check();
  await page.waitForFunction(() => !document.querySelector('#sendByEnterCheckbox').disabled);
  assert.deepEqual(preferences, [true]);
  assert.equal(await page.locator('#sendHotkeyHint').textContent(), 'Новая строка: Shift+Enter');
  await input.fill('line'); await input.press('Shift+Enter');
  assert.equal(await input.inputValue(), 'line\n');
  assert.deepEqual(posts, []);
  const sent = page.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith('/reply'));
  await input.press('Enter'); await sent;
  assert.equal(posts.at(-1), '/api/v1/tickets/123/reply');
  await toggle.uncheck();
  await page.waitForFunction(() => !document.querySelector('#sendByEnterCheckbox').disabled);
  assert.deepEqual(preferences, [true, false]);
  assert.equal(await page.locator('#sendHotkeyHint').textContent(), 'Отправка: Ctrl+Enter');
  await input.fill('next'); await input.press('Enter');
  assert.equal(await input.inputValue(), 'next\n');
  const response = page.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith('/reply'));
  await input.press('Control+Enter'); await response;
  assert.equal(posts.filter(p => p.endsWith('/reply')).length, 2);
  if (process.env.TICKET_DIALOG_SCREENSHOTS) await page.locator('.btn-row').screenshot({
    path: path.join(process.env.TICKET_DIALOG_SCREENSHOTS, `composer-${theme}.png`) });
});
