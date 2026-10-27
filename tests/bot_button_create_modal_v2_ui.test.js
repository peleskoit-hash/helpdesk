// Real editor and handlers; HTTP is mocked so no application database is touched.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });

for (const theme of ['light', 'dark']) for (const width of [1440, 390]) {
  test(`create button modal V2: ${theme}, ${width}px`, async t => {
    const page = await browser.newPage({ viewport: { width, height: 900 } });
    page.setDefaultTimeout(5000);
    t.after(() => page.close());
    const errors = [], creates = [];
    page.on('pageerror', error => errors.push(error.message));
    const me = { id: 'admin', role: 'admin', login: 'admin', full_name: 'Admin' };
    await page.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url()), p = url.pathname;
      if (url.hostname !== 'desk.test') return route.abort();
      if (p.startsWith('/static/')) {
        const file = path.resolve('api/app', p.slice(1));
        return route.fulfill({ status: fs.existsSync(file) ? 200 : 404,
          body: fs.existsSync(file) ? fs.readFileSync(file) : '',
          contentType: { '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream' });
      }
      if (!p.startsWith('/api/')) return route.fulfill({ contentType: 'text/html', body: fs.readFileSync('api/app/templates/settings_bot_buttons.html', 'utf8') });
      let data = {};
      if (p.endsWith('/appearance-session')) data = { writer_id: '00000000-0000-4000-8000-000000000001', preferences: { theme_preference: theme, appearance_revision: '0' } };
      else if (p.endsWith('/preferences')) data = { theme_preference: theme, appearance_revision: '0' };
      else if (p.endsWith('/auth/session')) data = { real_user: me, effective_user: me, is_real_admin: true };
      else if (p.endsWith('/users/me') || p.endsWith('/users/me/profile')) data = me;
      else if (p.endsWith('/menu-types')) data = [{ menu_name: 'menu-a', display_name: 'Меню', destinations: [], buttons_count: 0, menus_count: 0 }];
      else if (p.endsWith('/structure')) data = { root_level_key: 'root', tree_rows: [], removed_items: [] };
      else if (p.endsWith('/item/create')) { creates.push(request.postDataJSON()); data = { ok: true }; }
      else if (p.endsWith('/archived') || p.endsWith('/assignees')) data = [];
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
    });
    await page.goto('http://desk.test/settings/bot-buttons');
    await page.locator('[data-menu-name]').first().click();
    await page.locator('#addLevelItemBtn').click();
    const modal = page.locator('.bot-create-item-dialog').filter({ has: page.locator('#botCreateLabelInput') });
    await modal.waitFor();
    assert.equal(await page.locator('#botCreateLabelInput').getAttribute('autocomplete'), 'off');
    assert.equal(await modal.locator('input[type=radio]').count(), 5);
    assert.equal(await modal.locator('input[type=radio]:checked').inputValue(), 'ticket');
    const metrics = await modal.evaluate(el => {
      const style = getComputedStyle(el), box = el.getBoundingClientRect();
      return { font: getComputedStyle(el.querySelector('.bot-create-item-type')).fontSize,
        labelFont: getComputedStyle(el.querySelector('.bot-create-item-label')).fontSize, buttonFont: getComputedStyle(el.querySelector('#applyCreateItemBtn')).fontSize, inputFont: getComputedStyle(el.querySelector('#botCreateLabelInput')).fontSize,
        titleFont: getComputedStyle(el.querySelector('h3')).fontSize, radius: style.borderRadius,
        background: style.backgroundColor, expectedBackground: getComputedStyle(document.body).getPropertyValue('--desk-surface').trim(),
        fits: box.left >= 0 && box.right <= innerWidth && box.top >= 0 && box.bottom <= innerHeight,
        overflow: el.scrollWidth > el.clientWidth };
    });
    assert.equal(metrics.font, '19.5px');
    assert.equal(metrics.labelFont, '24px');
    assert.equal(metrics.buttonFont, '20px');
    assert.equal(metrics.inputFont, '21px');
    assert.equal(metrics.titleFont, '28.08px');
    assert.equal(metrics.background, metrics.expectedBackground);
    assert.equal(metrics.radius, '24px');
    assert.ok(metrics.fits && !metrics.overflow);
    const alert = page.waitForEvent('dialog').then(async dialog => { assert.match(dialog.message(), /Название обязательно/); await dialog.accept(); });
    await page.locator('#applyCreateItemBtn').click();
    await alert;
    assert.equal(creates.length, 0);
    await page.locator('#cancelCreateItemBtn').click();
    assert.equal(await modal.count(), 0);
    for (const kind of ['section', 'ticket', 'task', 'auto', 'group_task']) {
      await page.locator('#addLevelItemBtn').click();
      await page.locator(`input[name=botCreateKind][value=${kind}]`).check();
      await page.locator('#botCreateLabelInput').fill(`  Test ${kind}  `);
      await page.locator('#botCreateEnabledInput').uncheck();
      await page.locator('#botCreateEnabledInput').check();
      if (kind === 'auto') await page.locator('#botCreateEnabledInput').uncheck();
      if (kind === 'task') await page.locator('#botCreateLabelInput').press('Enter');
      else await page.locator('#applyCreateItemBtn').click();
      await page.waitForFunction(() => !document.getElementById('botCreateLabelInput'));
      assert.deepEqual(creates.at(-1), { menu_type: 'menu-a', parent_path: '', button_kind: kind,
        workflow_type: kind === 'section' ? 'submenu' : kind, label: `Test ${kind}`, is_enabled: kind !== 'auto' });
      await page.locator('#addLevelItemBtn').waitFor();
    }
    assert.equal(creates.length, 5);
    assert.deepEqual(errors, []);
  });
}
