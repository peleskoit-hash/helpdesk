// Real shell, sidebar, theme, templates and CSS; inner-page business scripts are
// isolated because this test exercises navigation without touching the local DB.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });
const pages = {
  '/settings': 'settings', '/settings/users': 'settings_users',
  '/settings/client-card': 'settings_client_card', '/settings/bot-buttons': 'settings_bot_buttons',
  '/settings/notification-bot-basic': 'settings_notification_bot_basic',
  '/settings/automatic-messages': 'settings_automatic_messages',
  '/management/integrations': 'management_integrations', '/quick-replies': 'quick_replies',
  '/management/users': 'management_users', '/management/reviews': 'reviews',
  '/management/feedback': 'management_feedback', '/management/statistics': 'management_statistics',
  '/management/saved-files': 'management_saved_files', '/mass-notifications': 'mass_notifications',
  '/tickets': 'tickets', '/management/tasks': 'management_tasks',
};
const targets = Object.keys(pages).filter(p => !['/settings', '/tickets', '/management/tasks'].includes(p));
async function setup(t, theme, width = 1440, role = 'admin') {
  const page = await browser.newPage({ viewport: { width, height: 1000 } });
  t.after(() => page.close());
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  const me = { id: 'fixture', role, full_name: 'Тест', login: 'test', availability_status: 'online' };
  await page.route('**/*', route => {
    const url = new URL(route.request().url()), p = url.pathname;
    if (url.hostname !== 'desk.test') return route.abort();
    if (p.startsWith('/static/')) {
      const file = path.resolve('api/app', p.slice(1));
      const shared = ['theme.js', 'sidebar.js', 'app_shell.js', 'error_dialog.js', 'settings_home.js'];
      if (p.endsWith('.js') && !shared.includes(path.basename(p))) {
        const body = `window.HelpdeskAppShell.registerPage(document.querySelector('#appContent').dataset.shellPage, () => {
          window.HelpdeskSidebar.init();
          document.querySelectorAll('#backToSettingsBtn, #botButtonsBackBtn, #backToManagementBtn').forEach(button => {
            button.onclick = () => window.HelpdeskAppShell.goBack();
          });
        });`;
        return route.fulfill({ contentType: 'text/javascript', body });
      }
      return route.fulfill({ body: fs.readFileSync(file), contentType: { '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.ttf': 'font/ttf' }[path.extname(file)] || 'application/octet-stream' });
    }
    if (p === '/management') return route.fulfill({ status: 303, headers: { location: '/settings' + url.search } });
    if (pages[p]) return route.fulfill({ contentType: 'text/html', body: fs.readFileSync(`api/app/templates/${pages[p]}.html`, 'utf8') });
    let data = {};
    if (p.endsWith('/appearance-session')) data = { writer_id: '00000000-0000-4000-8000-000000000001', preferences: { theme_preference: theme, appearance_revision: '0' } };
    else if (p.endsWith('/preferences')) data = { theme_preference: theme, appearance_revision: '0' };
    else if (p === '/api/v1/auth/session') data = { real_user: me, effective_user: me, is_real_admin: role === 'admin', session_login_ts: 1 };
    else if (p === '/api/v1/users/me' || p === '/api/v1/users/me/profile') data = me;
    else if (p === '/api/v1/users/agents' || p === '/api/v1/users/by-role') data = [];
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
  });
  t.after(() => assert.deepEqual(errors, []));
  return page;
}
for (const theme of ['light', 'dark']) for (const width of [1440, 640, 390]) {
  test(`unified settings V2: ${theme}, ${width}`, async t => {
    const page = await setup(t, theme, width);
    // The real HTTP redirect and role guards are covered by the page tests.
    await page.goto('http://desk.test/settings?source=bookmark#section');
    await page.waitForFunction(theme => document.body.dataset.theme === theme && document.body.dataset.sidebarReady === '1', theme);
    assert.equal(new URL(page.url()).pathname, '/settings');
    assert.equal(new URL(page.url()).search, '?source=bookmark');
    const links = page.locator('.settings-home-link');
    assert.deepEqual(await links.evaluateAll(nodes => nodes.map(n => n.getAttribute('href'))), targets);
    assert.equal(await page.locator('.settings-home-section').count(), 2);
    assert.equal(await page.locator('.sidebar a[href="/management"]').count(), 0);
    assert.equal(await page.locator('.sidebar a[href="/settings"]:visible').count(), 1);
    assert.equal(await page.locator('.settings-home-link-description').count(), 13);
    assert.deepEqual(await page.evaluate(() => {
      const main = document.querySelector('#appContent'), panel = main.querySelector('.panel');
      const m = main.getBoundingClientRect(), p = panel.getBoundingClientRect(), toolbar = main.querySelector('.toolbar').getBoundingClientRect();
      return [p.top - toolbar.bottom, p.left - m.left, m.right - p.right, m.bottom - p.bottom, getComputedStyle(panel).borderRadius];
    }), width <= 700 ? [12, 12, 12, 12, '24px'] : [14, 16, 16, 16, '24px']);
    assert.equal(await page.locator('.settings-home-links').first().evaluate(el => getComputedStyle(el).gridTemplateColumns.split(' ').length), width > 1000 ? 2 : 1);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.equal(await page.locator('.settings-home-panel').evaluate(el => {
      const probe = document.createElement('span'); probe.style.color = 'var(--desk-surface)'; el.append(probe);
      const matches = getComputedStyle(el).backgroundColor === getComputedStyle(probe).color; probe.remove(); return matches;
    }), true);
    await links.last().scrollIntoViewIfNeeded();
    assert.equal(await links.last().isVisible(), true);
    if (process.env.SETTINGS_V2_SCREENSHOT_DIR) {
      fs.mkdirSync(process.env.SETTINGS_V2_SCREENSHOT_DIR, { recursive: true });
      await page.locator('.settings-home-panel').evaluate(el => el.scrollTop = 0);
      await page.screenshot({ path: path.join(process.env.SETTINGS_V2_SCREENSHOT_DIR, `${theme}-${width}.png`) });
    }
    // Retain the same document through every card, toolbar Back and browser Forward.
    await page.evaluate(() => window.navigationMarker = true);
    for (const target of targets) {
      await page.locator(`.settings-home-link[href="${target}"]`).click();
      await page.waitForURL('**' + target);
      assert.equal(await page.evaluate(() => window.navigationMarker), true);
      assert.equal(await page.locator('.sidebar a[href="/settings"]').evaluate(el => el.classList.contains('active')), true);
      const back = page.locator('.toolbar button').first();
      await back.click();
      await page.waitForURL('**/settings?source=bookmark#section');
      await page.waitForSelector('.settings-home-link');
    }
    await page.goForward();
    await page.waitForURL('**/mass-notifications');
    await page.goBack();
    await page.waitForSelector('.settings-home-link');
    await page.evaluate(() => window.HelpdeskAppShell.navigate('/management?legacy=1#saved'));
    assert.equal(new URL(page.url()).pathname, '/settings');
    assert.equal(new URL(page.url()).hash, '#saved');
    await page.goBack();
    await page.waitForURL('**/settings?source=bookmark#section');
  });
}
test('agent sidebar retains navigation and hides admin settings', async t => {
  const page = await setup(t, 'light', 1440, 'agent');
  await page.goto('http://desk.test/tickets');
  await page.waitForFunction(() => document.body.dataset.sidebarReady === '1');
  assert.equal(await page.locator('.sidebar a[href="/settings"]:visible').count(), 0);
  assert.equal(await page.locator('.sidebar a[href="/management"]:visible').count(), 0);
  assert.equal(await page.locator('.sidebar a[href="/tickets?tab=active"]:visible').count(), 1);
});
test('direct admin entrypoints merge the legacy sidebar and retain Tasks highlighting', async t => {
  const page = await setup(t, 'dark');
  for (const target of ['/tickets', '/management/users', '/quick-replies', '/management/tasks']) {
    await page.goto('http://desk.test' + target);
    await page.waitForFunction(() => document.body.dataset.sidebarReady === '1');
    assert.equal(await page.locator('.sidebar a[href="/management"]').count(), 0);
    assert.equal(await page.locator('.sidebar a[href="/settings"]:visible').count(), 1);
    assert.equal(await page.locator('.sidebar a[href="/settings"]').evaluate(el => el.classList.contains('active')), ['/management/users', '/quick-replies'].includes(target));
    assert.equal(await page.locator('.sidebar a[href="/management/tasks"]').evaluate(el => el.classList.contains('active')), target === '/management/tasks');
  }
});
