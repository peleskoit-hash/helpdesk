const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
const actionWidths = {};
test.before(async () => { browser = await chromium.launch({ channel: 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });
for (const role of ['admin', 'agent']) for (const ticket of [false, true]) {
  test(`quick reply columns, scroll and actions: ${role}, ticket=${ticket}`, async t => {
    const page = await browser.newPage({ viewport: { width: 1100, height: 700 } });
    t.after(() => page.close());
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('http://desk.test/**', route => route.fulfill({ contentType: 'text/html', body: '<html></html>' }));
    await page.goto('http://desk.test/quick-replies' + (ticket ? '?ticket_id=t1' : ''));
    const html = fs.readFileSync('api/app/templates/quick_replies.html', 'utf8');
    await page.setContent(html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '').replace(/<link\b[^>]*>/g, ''));
    for (const file of ['styles.css', 'desk-v2.css', 'admin-pages-v2.css', 'admin-editors-v2.css', 'quick_replies.css']) {
      await page.addStyleTag({ content: fs.readFileSync('api/app/static/' + file, 'utf8') });
    }
    await page.evaluate(({ role }) => {
      document.body.dataset.sidebarReady = '1';
      document.body.dataset.theme = 'light';
      window.HelpdeskSidebar = { init: async () => {} };
      window.HelpdeskDialogs = { confirm: async () => true };
      window.errors = []; window.writes = [];
      window.HelpdeskErrorDialog = { show: payload => window.errors.push(payload.error.message) };
      const replies = Array.from({ length: 40 }, (_, i) => ({ id: `r${i}`, short_title: 'Очень длинное название '.repeat(15), author_name: i ? null : 'Автор API', created_at: i ? null : '2026-11-06T10:00:00', body: 'Текст', can_edit: i === 0, author_user_id: 'me' }));
      window.fetch = async (url, options = {}) => {
        if (options.method) window.writes.push({ url, method: options.method });
        const duplicate = options.method === 'PATCH';
        return { ok: !duplicate, status: duplicate ? 409 : 200, text: async () => JSON.stringify(duplicate ? { detail: { error: { message: 'Быстрый ответ с таким кратким названием уже существует' } } } : url.endsWith('/session') ? { effective_user: { id: 'me', role } } : url.endsWith('/use') ? replies[0] : replies) };
      };
    }, { role });
    await page.addScriptTag({ content: fs.readFileSync('api/app/static/quick_replies.js', 'utf8') });
    await page.evaluate(() => window.HelpdeskQuickReplies.initQuickRepliesPage());
    const cards = page.locator('.quick-reply-card'), header = page.locator('.quick-replies-header');
    assert.equal(await cards.count(), 40);
    assert.deepEqual(await header.locator('span').allTextContents(), ['Краткое название', 'Автор', 'Дата создания', 'Действия']);
    assert.equal(await cards.first().locator('.quick-reply-card-author').textContent(), 'Автор API');
    assert.equal(await cards.first().locator('.quick-reply-card-date').textContent(), '06.11.2026');
    assert.equal(await cards.nth(1).locator('.quick-reply-card-author').textContent(), '—');
    assert.equal(await cards.nth(1).locator('.quick-reply-card-date').textContent(), '—');
    assert.equal(await cards.first().locator('button').count(), Number(role === 'admin') + Number(ticket));
    async function geometry() {
      return page.evaluate(() => {
        const header = document.querySelector('.quick-replies-header');
        const card = document.querySelector('.quick-reply-card');
        return { headerTop: header.getBoundingClientRect().top, height: header.getBoundingClientRect().height,
          columns: [...header.children].map((el, i) => [el.getBoundingClientRect().left, card.children[i].getBoundingClientRect().left]),
          actionWidth: card.lastElementChild.getBoundingClientRect().width,
          buttons: [...card.querySelectorAll('button')].map(el => ({ width: el.getBoundingClientRect().width, scroll: el.scrollWidth, client: el.clientWidth })),
          title: { overflow: getComputedStyle(card.firstElementChild).textOverflow, width: card.firstElementChild.clientWidth, scroll: card.firstElementChild.scrollWidth, title: card.firstElementChild.title },
          background: getComputedStyle(header).backgroundColor,
        };
      });
    }
    const before = await geometry();
    actionWidths[`${role}:${ticket}`] = before.actionWidth;
    for (const theme of ['dark', 'light']) {
      await page.evaluate(theme => { document.body.dataset.theme = theme; }, theme);
      assert.notEqual((await geometry()).background, 'rgba(0, 0, 0, 0)');
    }
    for (const [h, c] of before.columns) assert.ok(Math.abs(h - c) < 1, JSON.stringify(before));
    assert.equal(before.title.overflow, 'ellipsis'); assert.ok(before.title.scroll > before.title.width); assert.ok(before.title.title.length > 100);
    for (const b of before.buttons) assert.ok(b.scroll <= b.client);
    if (before.buttons.length === 2) assert.ok(before.actionWidth >= before.buttons.reduce((n, b) => n + b.width, 0) + 8);
    assert.notEqual(before.background, 'rgba(0, 0, 0, 0)');
    const createTop = await page.locator('#createQuickReplyBtn').evaluate(el => el.getBoundingClientRect().top);
    await page.locator('#quickRepliesList').evaluate(el => { el.scrollTop = 300; });
    const after = await geometry();
    assert.equal(after.headerTop, before.headerTop); assert.equal(after.height, before.height); assert.deepEqual(after.columns, before.columns);
    assert.equal(await page.locator('#createQuickReplyBtn').evaluate(el => el.getBoundingClientRect().top), createTop);
    await page.locator('#quickRepliesList').evaluate(el => { el.scrollTop = 0; });
    await cards.first().locator('.quick-reply-card-author').click();
    assert.equal(await page.locator('#quickReplyModal').isVisible(), true);
    await page.locator('#quickReplyTitleInput').fill('Занято');
    await page.locator('#saveQuickReplyBtn').click();
    assert.equal(await page.locator('#quickReplyModal').isVisible(), true);
    assert.equal(await page.locator('#quickReplyTitleInput').inputValue(), 'Занято');
    assert.equal((await page.evaluate(() => window.errors))[0], 'Быстрый ответ с таким кратким названием уже существует');
    await page.locator('#cancelQuickReplyBtn').click();
    await cards.nth(1).focus(); await page.keyboard.press('Enter');
    assert.equal(await page.locator('#quickReplyTitleInput').getAttribute('readonly'), '');
    await page.locator('#closeQuickReplyModalBtn').click();
    if (role === 'admin') {
      await cards.first().locator('[data-delete]').focus(); await page.keyboard.press('Enter');
      assert.equal(await page.locator('#quickReplyModal').isVisible(), false);
      assert.equal((await page.evaluate(() => window.writes.filter(w => w.method === 'DELETE'))).length, 1);
    }
    if (ticket) {
      await cards.first().locator('[data-use]').click();
      await page.waitForURL('**/tickets/t1');
      const pending = await page.evaluate(() => JSON.parse(sessionStorage.getItem('hd_quick_reply_pending_use:t1')));
      assert.equal(pending.body, 'Текст');
    }
    assert.deepEqual(errors, []);
  });
}


test('actions reserve only the available buttons', () => {
  assert.ok(actionWidths['admin:true'] > actionWidths['admin:false']);
  assert.ok(actionWidths['admin:true'] > actionWidths['agent:true']);
  assert.ok(actionWidths['agent:true'] > actionWidths['agent:false']);
});

test('quick reply page bundles have versioned URLs', () => {
  const html = fs.readFileSync('api/app/templates/quick_replies.html', 'utf8');
  const shell = fs.readFileSync('api/app/static/app_shell.js', 'utf8');
  const script = html.match(/\/static\/quick_replies\.js\?v=[^" ]+/)[0];
  assert.ok(shell.includes(script));
  assert.match(html, /quick_replies\.css\?v=20261106-columns-v1/);
  for (const file of fs.readdirSync('api/app/templates')) {
    if (!file.endsWith('.html')) continue;
    const content = fs.readFileSync('api/app/templates/' + file, 'utf8');
    if (content.includes('/static/app_shell.js?')) assert.ok(content.includes('/static/app_shell.js?v=20261111-ticket-escape-v2'), file);
  }
});

test('shell navigation loads the current quick reply JS and CSS', async t => {
  const page = await browser.newPage();
  t.after(() => page.close());
  const requested = [];
  await page.route('http://desk.test/**', async route => {
    const url = new URL(route.request().url()); requested.push(url.pathname + url.search);
    if (url.pathname.startsWith('/static/')) {
      return route.fulfill({ contentType: url.pathname.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync('api/app' + url.pathname, 'utf8') });
    }
    if (url.pathname === '/quick-replies') return route.fulfill({ contentType: 'text/html', body: fs.readFileSync('api/app/templates/quick_replies.html', 'utf8') });
    if (url.pathname.startsWith('/api/')) return route.fulfill({ contentType: 'application/json', body: JSON.stringify(url.pathname.endsWith('/session') ? { effective_user: { id: 'me', role: 'admin' } } : [{ id: 'r1', short_title: 'Ответ', body: 'Текст', author_user_id: 'me' }]) });
    return route.fulfill({ contentType: 'text/html', body: '<main id="appContent" class="main" data-shell-page="settings-home"></main>' });
  });
  await page.goto('http://desk.test/settings');
  await page.evaluate(() => { window.HelpdeskSidebar = { init: async () => {} }; });
  await page.addScriptTag({ content: fs.readFileSync('api/app/static/app_shell.js', 'utf8') });
  assert.equal(await page.evaluate(() => window.HelpdeskAppShell.navigate('/quick-replies?ticket_id=t1')), true);
  await page.locator('.quick-reply-card').waitFor();
  await page.waitForFunction(() => getComputedStyle(document.querySelector('.quick-replies-header')).position === 'sticky');
  assert.ok(requested.includes('/static/quick_replies.js?v=20261106-columns-v1'));
  assert.ok(requested.includes('/static/quick_replies.css?v=20261106-columns-v1'));
  assert.equal(await page.locator('[data-use]').count(), 1);
});
