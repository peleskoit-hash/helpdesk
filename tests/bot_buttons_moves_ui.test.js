// Real V2 templates/scripts, mocked HTTP only. No application DB or report files.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });

for (const theme of ['light', 'dark']) test(`V2 cross-level draft moves: ${theme}`, async t => {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(5000);
  t.after(() => page.close());
  const calls = [], errors = [], assets = [];
  page.on('pageerror', error => errors.push(error.message));
  const me = { id: 'admin', role: 'admin', login: 'admin', full_name: 'Admin' };
  const leaf = (key, parent = '') => ({ kind: 'terminal', label: `Видимое ${key}`, technical_key: key,
    button_id: randomUUID(), stable_button_id: randomUUID(), parent_path: parent,
    full_path: `${parent}/internal-${key}`, workflow_type: 'ticket', children_rows: [], is_enabled: true });
  const folder = (key, label) => ({ kind: 'submenu', label, technical_key: key, button_id: randomUUID(),
    parent_path: '', full_path: `opaque/${key}`, level_key: `level-${key}`, children_rows: [] });
  const a = folder('a', 'Отображение А'), b = folder('b', 'Отображение Б');
  const moving = leaf('original-key'), other = leaf('other-key'), child = leaf('child', a.full_path);
  const originalId = moving.button_id;
  const group = { ...leaf('group'), kind: 'group_task_parent', workflow_type: 'group_task' };
  a.children_rows = [{ columns: 1, buttons: [child] }];
  let rows = [{ columns: 1, buttons: [a] }, { columns: 1, buttons: [b] },
    { columns: 2, buttons: [moving, other] }, { columns: 1, buttons: [group] }];
  let fail = 0, withoutIdentity = false, saveGate = null;
  const structure = () => {
    const tree = structuredClone(rows);
    if (withoutIdentity) {
      for (const row of tree) for (const node of row.buttons) { delete node.button_id; delete node.parent_path; }
    }
    return { root_level_key: 'root', tree_rows: tree, removed_items: [] };
  };
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    if (url.hostname !== 'desk.test') return route.abort();
    if (p.startsWith('/static/')) {
      assets.push(p + url.search);
      const file = path.resolve('api/app', p.slice(1));
      return route.fulfill({ status: fs.existsSync(file) ? 200 : 404,
        body: fs.existsSync(file) ? fs.readFileSync(file) : '',
        contentType: { '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream' });
    }
    if (!p.startsWith('/api/')) return route.fulfill({ contentType: 'text/html', body: fs.readFileSync('api/app/templates/settings_bot_buttons.html', 'utf8') });
    calls.push({ p, method: req.method(), data: req.postDataJSON() });
    let data = {};
    if (p.endsWith('/appearance-session')) data = { writer_id: '00000000-0000-4000-8000-000000000001', preferences: { theme_preference: theme, appearance_revision: '0' } };
    else if (p.endsWith('/preferences')) data = { theme_preference: theme, appearance_revision: '0' };
    else if (p.endsWith('/auth/session')) data = { real_user: me, effective_user: me, is_real_admin: true };
    else if (p.endsWith('/users/me') || p.endsWith('/users/me/profile')) data = me;
    else if (p.endsWith('/menu-types')) data = [{ menu_name: 'menu-a', display_name: 'Меню', destinations: [], buttons_count: 5, menus_count: 3 }];
    else if (p.endsWith('/structure')) data = structure();
    else if (p.endsWith('/item/move')) {
      if (saveGate) await saveGate;
      if (fail) return route.fulfill({ status: fail, contentType: 'application/json', body: JSON.stringify({ detail: { error: { message: `Ошибка ${fail}` } } }) });
      const payload = req.postDataJSON();
      assert.equal(payload.button_id, moving.button_id);
      assert.equal(payload.menu_type, 'menu-a');
      assert.equal(payload.new_row, true);
      const levels = [rows, a.children_rows, b.children_rows];
      for (const level of levels) {
        for (const row of level) row.buttons = row.buttons.filter(node => node !== moving);
        for (let i = level.length - 1; i >= 0; --i) if (!level[i].buttons.length) level.splice(i, 1);
      }
      moving.parent_path = payload.parent_path;
      const target = payload.parent_path === '' ? rows : [a, b].find(node => node.full_path === payload.parent_path)?.children_rows;
      assert.ok(target, 'Payload uses the structure path, not the displayed label');
      target.push({ columns: 1, buttons: [moving] });
      data = { ok: true, structure: structure() };
    } else if (p.endsWith('/item')) data = { button_key: url.searchParams.get('button_key'), button_style: 'primary' };
    else if (p.endsWith('/archived') || p.endsWith('/agents') || p.endsWith('/by-role') || p.endsWith('/assignees')) data = [];
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
  });
  const moveCalls = () => calls.filter(call => call.p.endsWith('/item/move'));
  const keyButton = key => page.locator(`[data-button-color-key="${key}"]`).first();
  const openContents = () => page.locator('.bot-structure-submenu').evaluateAll(nodes => nodes.forEach(node => { node.open = true; }));
  const zone = parent => page.locator(`[data-move-parent="${parent}"]`).first();
  async function edit() { await page.locator('#editLayoutBtn').click(); await openContents(); }
  async function drag(source, target) {
    await source.hover();
    const from = await source.boundingBox();
    await page.mouse.down();
    await page.mouse.move(from.x + from.width / 2 + 8, from.y + from.height / 2 + 8, { steps: 3 });
    await target.scrollIntoViewIfNeeded();
    const to = await target.boundingBox();
    await page.mouse.move(to.x + to.width / 2, to.y + to.height / 2, { steps: 12 });
    await page.mouse.up();
  }
  async function stage(parent) {
    assert.equal(await keyButton('original-key').getAttribute('draggable'), 'true');
    await drag(keyButton('original-key'), zone(parent));
    assert.equal(await page.locator('.bot-move-source-pending').count(), 1);
    await page.locator('.bot-move-source-pending').waitFor();
  }
  async function save() { await page.locator('#saveLayoutBtn').click(); await page.locator('#editLayoutBtn').waitFor(); }
  async function root() {
    while (await page.locator('#editSubmenuBtn').count()) await page.locator('#botButtonsBackBtn').click();
  }
  await page.goto('http://desk.test/settings/bot-buttons');
  const deep = folder('deep', 'Вложенный раздел');
  a.children_rows.push({ columns: 1, buttons: [deep] });
  await page.locator('[data-menu-name]').first().click();
  const menuUrl = page.url(), menuHistory = await page.evaluate(() => history.length);
  await page.evaluate(() => {
    window.botShellBackCalls = 0; window.botHistoryBackCalls = 0;
    const shellBack = HelpdeskAppShell.goBack, browserBack = history.back.bind(history);
    HelpdeskAppShell.goBack = (...args) => { botShellBackCalls++; return shellBack(...args); };
    history.back = () => { botHistoryBackCalls++; browserBack(); };
  });
  await keyButton('a').click(); await keyButton('deep').click();
  const up = page.locator('#botButtonsBackBtn');
  assert.equal(await up.textContent(), 'На уровень вверх');
  await up.click(); await keyButton('child').waitFor();
  await up.click(); await keyButton('a').waitFor();
  await up.click(); await page.locator('[data-menu-name]').first().waitFor();
  assert.equal(await up.textContent(), 'Назад');
  assert.equal(page.url(), menuUrl);
  assert.deepEqual(await page.evaluate(() => [history.length, botShellBackCalls, botHistoryBackCalls]), [menuHistory, 0, 0]);
  assert.equal(await up.getAttribute('data-shell-back'), null);
  a.children_rows.pop();
  await page.locator('[data-menu-name]').first().click();
  await edit();
  // A draft does not write; cancel restores the original list.
  await stage(a.full_path);
  assert.equal(moveCalls().length, 0);
  assert.equal(await keyButton('other-key').getAttribute('draggable'), 'false');
  assert.equal(await page.locator('.bot-layout-dialog').evaluate(dialog=>dialog.matches(':modal')), true, 'The modal blocks background navigation');
  assert.equal(await page.locator('#saveLayoutBtn').count(), 1, 'Navigation cannot discard a pending move');
  await page.locator('#cancelLayoutBtn').click();
  assert.equal(moveCalls().length, 0);
  assert.equal(await keyButton('original-key').count(), 1);
  // UUID/key and color survive save; the current root and nested content refresh.
  const beforeReads = calls.filter(call => call.p.endsWith('/item')).length;
  await edit(); await stage(a.full_path);
  let releaseSave;
  saveGate = new Promise(resolve => { releaseSave = resolve; });
  await page.locator('#saveLayoutBtn').click();
  assert.equal(await page.locator('#saveLayoutBtn').isDisabled(), true);
  assert.equal(await page.locator('#cancelLayoutBtn').isDisabled(), true);
  await page.locator('#saveLayoutBtn').dispatchEvent('click');
  await page.locator('#cancelLayoutBtn').dispatchEvent('click');
  assert.equal(await page.locator('.bot-move-source-pending').count(), 1);
  releaseSave(); saveGate = null;
  await page.locator('#editLayoutBtn').waitFor();
  assert.equal(moveCalls().length, 1);
  assert.equal(moving.button_id, originalId);
  assert.notEqual(moving.button_id, moving.stable_button_id);
  assert.equal(moving.technical_key, 'original-key');
  await keyButton('a').click();
  await page.locator('#editSubmenuBtn').waitFor();
  await page.waitForFunction(() => document.querySelector('[data-button-color-key="original-key"]')?.classList.contains('bot-button-color-primary'));
  assert.equal(await keyButton('original-key').evaluate(el => getComputedStyle(el).backgroundColor), 'rgb(47, 128, 237)');
  assert.ok(calls.filter(call => call.p.endsWith('/item')).length > beforeReads, 'Successful move invalidates the color cache');
  // Upward move; cancel and retry after 409 and 422 keep the draft.
  await edit(); await stage('');
  for (const status of [409, 422]) {
    fail = status;
    await page.locator('#saveLayoutBtn').click();
    await page.getByRole('alert').filter({ hasText: `Ошибка ${status}` }).waitFor();
    assert.equal(await page.locator('.bot-move-source-pending').count(), 1);
    assert.equal(await page.locator('#saveLayoutBtn').isEnabled(), true);
    assert.equal(moving.parent_path, a.full_path);
  }
  await page.locator('#cancelLayoutBtn').click();
  assert.equal(moving.parent_path, a.full_path);
  fail = 0;
  await edit(); await stage(''); await save();
  assert.equal(await page.locator('#editSubmenuBtn').count(), 1, 'Saving retains the current submenu navigation');
  await root();
  assert.equal(await keyButton('original-key').count(), 1);
  // A -> B from the expanded root, without an intermediate save to root.
  await edit(); await stage(a.full_path); await save();
  await edit(); await stage(b.full_path); await save();
  assert.equal(moving.parent_path, b.full_path);
  await keyButton('b').click();
  assert.equal(await keyButton('original-key').count(), 1);
  await root();
  // Local reorder and cross-level draft cannot be mixed.
  await edit();
  await drag(page.locator('[data-layout-drag-button="2:0"]'), page.locator('[data-layout-new-row-before="0"]'));
  await openContents();
  await drag(keyButton('original-key'), zone(a.full_path));
  assert.match(await zone(a.full_path).getAttribute('title'), /перестановки текущего уровня/);
  assert.equal(await page.locator('.bot-move-source-pending').count(), 0);
  await page.locator('#cancelLayoutBtn').click();
  // A submenu cannot become a cross-level move.
  await edit(); await drag(keyButton('b'), zone(a.full_path));
  assert.match(await zone(a.full_path).getAttribute('title'), /только обычные кнопки/);
  await page.locator('#cancelLayoutBtn').click();
  await edit(); await drag(keyButton('group'), zone(a.full_path));
  assert.match(await zone(a.full_path).getAttribute('title'), /только обычные кнопки/);
  await page.locator('#cancelLayoutBtn').click();
  // Missing actual ID disables the move; stable_button_id is never substituted.
  withoutIdentity = true;
  await page.locator('#botButtonsBackBtn').click();
  await page.locator('[data-menu-name]').first().click();
  await edit(); await drag(keyButton('other-key'), zone(a.full_path));
  assert.match(await zone(a.full_path).getAttribute('title'), /ID и путём из структуры/);
  await page.locator('#cancelLayoutBtn').click();
  assert.equal(calls.filter(call => call.method === 'PUT' && call.p.endsWith('/layout')).length, 0);
  for (const file of ['styles.css', 'desk-v2.css', 'bot-buttons-browser-v2.css', 'app_shell.js', 'bot_buttons_level_view.js', 'settings_bot_buttons.js']) {
    const declarations = ['api/app/templates/settings_bot_buttons.html', 'api/app/static/styles.css', 'api/app/static/desk-v2.css']
      .map(file => fs.readFileSync(file, 'utf8')).join('\n');
    const version = declarations.match(new RegExp(`/static/${file.replaceAll('.', '\\.')}\\?v=([^"'\\s)]+)`))?.[1];
    assert.ok(version, `Declared asset: ${file}`);
    assert.ok(assets.includes(`/static/${file}?v=${version}`), `Fresh asset: ${file}`);
  }
  assert.deepEqual(errors, []);
});
