const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });

for (const nested of [false, true]) test(`saving a bot label refreshes the visible level and survives reopening (nested=${nested})`, async t => {
  const page = await browser.newPage(); t.after(() => page.close());
  const errors = [], puts = [];
  page.on('pageerror', e => errors.push(e.message)); t.after(() => assert.deepEqual(errors, []));
  const me = { id: 'admin', role: 'admin', login: 'admin', full_name: 'Admin' };
  const original = 'Исходное', renamed = 'Новое название';
  const key = 'menu|ticket|Internal/Original', fullPath = 'Internal/Original';
  let override = null, structureReads = 0;
  const effective = () => override || original;
  const structure = () => {
    const leaf = { kind: 'terminal', label: effective(), technical_key: key, button_id: 'leaf-id',
      workflow_type: 'ticket', full_path: fullPath, parent_path: nested ? 'Internal' : '', children_rows: [] };
    const rows = [{ columns: 1, buttons: [leaf] }];
    return { root_level_key: 'root', removed_items: [], tree_rows: nested ? [{ columns: 1, buttons: [{ kind: 'submenu', label: 'Раздел',
      technical_key: 'section-key', workflow_type: 'submenu', level_key: 'section-level', full_path: 'Internal', children_rows: rows }] }] : rows };
  };
  await page.route('http://desk.test/**', route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    if (p.startsWith('/static/')) return route.fulfill({ body: fs.readFileSync(path.resolve('api/app', p.slice(1))),
      contentType: { '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' }[path.extname(p)] || 'application/octet-stream' });
    if (!p.startsWith('/api/')) return route.fulfill({ contentType: 'text/html', body: fs.readFileSync('api/app/templates/settings_bot_buttons.html', 'utf8') });
    let data = {};
    if (p.endsWith('/auth/session')) data = { real_user: me, effective_user: me, is_real_admin: true };
    else if (p.endsWith('/users/me') || p.endsWith('/users/me/profile')) data = me;
    else if (p.endsWith('/menu-types')) data = [{ menu_name: 'menu', display_name: 'Меню', destinations: [], buttons_count: 1 }];
    else if (p.endsWith('/archived') || p.endsWith('/agents') || p.endsWith('/by-role') || p.endsWith('/assignees')) data = [];
    else if (p.endsWith('/structure')) { structureReads++; data = structure(); }
    else if (p.endsWith('/item') || p.endsWith('/item/instruction')) {
      if (req.method() === 'PUT') { puts.push({ body: req.postDataJSON(), params: Object.fromEntries(url.searchParams) }); override = req.postDataJSON().label_overridden ? req.postDataJSON().label_override : null; }
      data = { button_key: key, workflow_type: 'ticket', button_label: original, button_label_effective: effective(), label_override: override,
        label_overridden: !!override, currently_present: true, is_enabled: true, files: [], conditional_files: [], instruction_text: '', automation_type: 'none' };
    }
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
  });
  async function openLevel() {
    await page.locator('[data-menu-name="menu"]').click();
    await page.locator('#editLayoutBtn').waitFor();
    if (nested) await page.getByText('Раздел', { exact: true }).click();
  }
  await page.goto('http://desk.test/settings/bot-buttons'); await openLevel();
  await page.getByText(original, { exact: true }).click();
  await page.locator('#botLabelInput').fill(renamed);
  const readBefore = structureReads;
  const refreshed = page.waitForResponse(r => new URL(r.url()).pathname.endsWith('/structure'));
  await page.locator('#saveInstructionBtn').click(); await refreshed;
  await page.locator('#botButtonsBackBtn').click();
  assert.ok(structureReads > readBefore);
  assert.equal(await page.locator('.bot-buttons-level-item').filter({ hasText: renamed }).count(), 1);
  assert.equal(await page.locator('.bot-buttons-level-item').filter({ hasText: original }).count(), 0);
  assert.equal(puts[0].body.label_override, renamed);
  assert.equal(puts[0].params.button_key, key); assert.equal(puts[0].params.button_path, fullPath);
  await page.reload(); await openLevel();
  assert.equal(await page.locator('.bot-buttons-level-item').filter({ hasText: renamed }).count(), 1);
  // Simulate the existing reset action's persisted response, then reopen the same page.
  override = null;
  await page.reload(); await openLevel();
  assert.equal(await page.locator('.bot-buttons-level-item').filter({ hasText: original }).count(), 1);
});
