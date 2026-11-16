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
  const extra = leaf("legacy-third");
  const group = { ...leaf('group'), kind: 'group_task_parent', workflow_type: 'group_task' };
  a.children_rows = [{ columns: 1, buttons: [child] }];
  let rows = [{ columns: 1, buttons: [a] }, { columns: 1, buttons: [b] },
    { columns: 3, buttons: [moving, other, extra] }, { columns: 1, buttons: [group] }];
  let fail = 0, saveGate = null;
  const structure = () => {
    const tree = structuredClone(rows);
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
    else if (p.endsWith('/structure-layout')) {
      if (saveGate) await saveGate;
      if (fail) return route.fulfill({ status: fail, contentType: 'application/json', body: JSON.stringify({ detail: { error: { message: 'Ошибка ' + fail } } }) });
      const payload = req.postDataJSON();
      const nodes = [a, b, child, moving, other, group, extra];
      const byKey = new Map(nodes.map(node => [node.technical_key, node]));
      assert.equal(payload.menu_type, 'menu-a');
      for (const move of payload.moves) {
        const node = nodes.find(node => node.button_id === move.button_id);
        assert.ok(node);
        assert.ok(['', a.full_path, b.full_path].includes(move.parent_path));
        node.parent_path = move.parent_path;
      }
      const layout = key => payload.layouts[key].map(buttons => ({
        columns: buttons.length, buttons: buttons.map(key => byKey.get(key)),
      }));
      if ('root' in payload.layouts) rows = layout('root');
      if ('level-a' in payload.layouts) a.children_rows = layout('level-a');
      if ('level-b' in payload.layouts) b.children_rows = layout('level-b');
      data = { ok: true, structure: structure() };
    } else if (p.endsWith('/item')) data = { button_key: url.searchParams.get('button_key'), button_style: 'primary' };
    else if (p.endsWith('/archived') || p.endsWith('/agents') || p.endsWith('/by-role') || p.endsWith('/assignees')) data = [];
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
  });
  const writes = () => calls.filter(call => call.method !== 'GET' && /bot-buttons\/(?:structure-layout|item\/move|layout)$/.test(call.p));
  const keyButton = key => page.locator('[data-button-color-key="' + key + '"]').first();
  const level = key => page.locator('[data-layout-level="' + key + '"]');
  const zone = (key, before) => level(key).locator(':scope > [data-layout-new-row-before="' + before + '"]');
  const draftRows = key => level(key).locator(':scope > [data-layout-drop-row]').evaluateAll(rows => rows.map(row =>
    [...row.querySelectorAll(':scope > .bot-structure-item > button')].map(button => button.dataset.buttonColorKey)));
  async function edit() {
    await page.locator('#editLayoutBtn').click();
    await page.locator('.bot-structure-submenu').evaluateAll(nodes => nodes.forEach(node => { node.open = true; }));
  }
  async function drag(source, target) {
    await source.hover();
    const from = await source.boundingBox();
    await page.mouse.down();
    await page.mouse.move(from.x + from.width / 2 + 16, from.y + from.height / 2, { steps: 4 });
    await page.locator('.bot-layout-new-row-drop:visible').first().waitFor();
    await target.scrollIntoViewIfNeeded();
    const to = await target.boundingBox();
    await page.mouse.move(to.x + to.width / 2, to.y + to.height / 2, { steps: 12 });
    await page.mouse.up();
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
  const original = [['a'], ['b'], ['original-key', 'other-key'], ['legacy-third'], ['group']];
  assert.deepEqual(await draftRows('root'), original);
  assert.equal(await page.locator('.bot-layout-new-row-drop:visible').count(), 0);
  assert.equal(await page.locator('[data-move-parent]').count(), 0);
  assert.equal(await page.locator('.bot-layout-dialog').evaluate(dialog => dialog.matches(':modal')), true);

  // Observe trusted native events after the application's dragstart handler runs.
  const source = keyButton('child');
  await source.hover();
  const from = await source.boundingBox();
  const rootBefore = await keyButton('a').boundingBox();
  await source.evaluate(button => {
    const dialog = button.closest('dialog');
    const visibleZones = () => [...dialog.querySelectorAll('.bot-layout-new-row-drop')]
      .filter(zone => zone.getBoundingClientRect().height > 0).length;
    window.nativeLayoutDrag = { start: [], over: 0, end: 0 };
    dialog.addEventListener('dragstart', event => {
      if (event.isTrusted && event.target === button) {
        window.nativeLayoutDrag.start.push({ zones: visibleZones(), top: button.getBoundingClientRect().top });
      }
    });
    dialog.addEventListener('dragover', event => {
      if (event.isTrusted) window.nativeLayoutDrag.over++;
    }, true);
    dialog.addEventListener('dragend', event => {
      if (event.isTrusted) window.nativeLayoutDrag.end++;
    }, true);
  });
  await page.mouse.down();
  await page.mouse.move(from.x + from.width / 2 + 16, from.y + from.height / 2, { steps: 4 });
  assert.deepEqual(await page.evaluate(() => window.nativeLayoutDrag.start), [{ zones: 0, top: from.y }]);
  await zone('root', 0).waitFor();
  assert.equal(await zone('level-a', 0).isVisible(), false);
  assert.equal(await zone('level-a', 1).isVisible(), false);
  assert.equal(await zone('level-b', 0).isVisible(), true);
  assert.ok((await keyButton('a').boundingBox()).y > rootBefore.y, 'Drop zones make room in the list');
  assert.ok(await page.evaluate(() => window.nativeLayoutDrag.over > 0 && window.nativeLayoutDrag.end === 0));

  await page.mouse.up();
  assert.equal(await page.evaluate(() => window.nativeLayoutDrag.end), 1);
  assert.equal(await page.locator('.bot-layout-new-row-drop:visible').count(), 0);

  const transfer = await page.evaluateHandle(() => new DataTransfer());
  const scrollArea = page.locator('.bot-layout-scroll-area');
  await scrollArea.evaluate(el => { el.style.height = '250px'; el.scrollTop = 0; });
  await source.dispatchEvent('dragstart', { dataTransfer: transfer });
  await source.dispatchEvent('dragover', { dataTransfer: transfer });
  const autoScrollDown = page.locator('[data-layout-scroll="1"]');
  await autoScrollDown.waitFor();
  await autoScrollDown.dispatchEvent('dragover', { dataTransfer: transfer });
  await page.waitForFunction(() => document.querySelector('.bot-layout-scroll-area').scrollTop > 0);
  await page.evaluate(() => window.dispatchEvent(new Event('blur')));
  assert.equal(await page.locator('.bot-layout-new-row-drop:visible').count(), 0);
  assert.equal(await autoScrollDown.isVisible(), false);
  await scrollArea.evaluate(el => { el.style.removeProperty('height'); el.scrollTop = 0; });

  const groupSource = keyButton('group');
  await groupSource.dispatchEvent('dragstart', { dataTransfer: transfer });
  await groupSource.dispatchEvent('dragover', { dataTransfer: transfer });
  assert.equal(await zone('level-b', 0).isVisible(), false, 'Group buttons stay on their own level');
  await groupSource.dispatchEvent('dragend', { dataTransfer: transfer });

  await drag(keyButton('original-key'), keyButton('other-key'));
  assert.deepEqual((await draftRows('root'))[2], ['other-key', 'original-key']);
  await drag(keyButton('original-key'), keyButton('other-key'));
  assert.deepEqual(await draftRows('root'), original);

  async function changeDraft() {
    // Third button is refused. The real native drops exercise the same targets as the dialog.
    await drag(keyButton('child'), keyButton('other-key'));
    assert.deepEqual(await draftRows('root'), original);
    await drag(keyButton('legacy-third'), zone('root', 1));
    await drag(keyButton('original-key'), keyButton('child'));
    assert.deepEqual(await draftRows('level-a'), [['original-key', 'child']]);
    assert.equal(await keyButton('other-key').getAttribute('draggable'), 'true');
    await drag(keyButton('original-key'), zone('level-b', 0));
    await drag(keyButton('child'), zone('root', 3));
    assert.deepEqual(await draftRows('root'), [
      ['a'], ['legacy-third'], ['b'], ['child'], ['other-key'], ['group'],
    ]);
    assert.deepEqual(await draftRows('level-a'), []);
    assert.deepEqual(await draftRows('level-b'), [['original-key']]);
    // Moving an earlier singleton down removes its source row before insertion.
    await drag(keyButton('legacy-third'), zone('root', 5));
    assert.deepEqual(await draftRows('root'), [['a'], ['b'], ['child'], ['other-key'], ['legacy-third'], ['group']]);
    assert.equal(await page.locator('.bot-layout-new-row-drop:visible').count(), 0);
  }

  await changeDraft();
  await page.locator('#cancelLayoutBtn').click();
  assert.equal(writes().length, 0);
  await edit();
  assert.deepEqual(await draftRows('root'), original);
  assert.deepEqual(await draftRows('level-a'), [['child']]);
  await changeDraft();

  for (const status of [409, 422]) {
    fail = status;
    await page.locator('#saveLayoutBtn').click();
    await page.getByRole('alert').filter({ hasText: 'Ошибка ' + status }).waitFor();
    assert.deepEqual(await draftRows('level-b'), [['original-key']]);
    assert.equal(await page.locator('#saveLayoutBtn').isEnabled(), true);
    assert.equal(moving.parent_path, '');
  }
  fail = 0;
  let releaseSave;
  saveGate = new Promise(resolve => { releaseSave = resolve; });
  await page.locator('#saveLayoutBtn').click();
  assert.equal(await page.locator('#saveLayoutBtn').isDisabled(), true);
  assert.equal(await page.locator('#cancelLayoutBtn').isDisabled(), true);
  await page.locator('#saveLayoutBtn').dispatchEvent('click');
  releaseSave();
  await page.locator('#editLayoutBtn').waitFor();
  assert.equal(writes().length, 3, 'Two failed attempts and one successful batch request');
  assert.ok(writes().every(call => call.method === 'POST' && call.p.endsWith('/structure-layout')));
  assert.deepEqual(writes().at(-1).data, {
    menu_type: 'menu-a',
    moves: [
      { button_id: moving.button_id, parent_path: a.full_path },
      { button_id: moving.button_id, parent_path: b.full_path },
      { button_id: child.button_id, parent_path: '' },
    ],
    layouts: {
      root: [['a'], ['b'], ['child'], ['other-key'], ['legacy-third'], ['group']],
      'level-a': [],
      'level-b': [['original-key']],
    },
  });
  assert.equal(moving.button_id, originalId);
  await keyButton('b').click();
  await page.waitForFunction(() => document.querySelector('[data-button-color-key="original-key"]')?.classList.contains('bot-button-color-primary'));
  await page.locator('#editLayoutBtn').click();
  assert.equal(await level('level-b').locator('..').getAttribute('open'), '');
  await drag(keyButton('original-key'), zone('root', 0));
  assert.deepEqual((await draftRows('root'))[0], ['original-key']);
  await page.locator('#cancelLayoutBtn').click();
  assert.equal(await page.locator('#editSubmenuBtn').count(), 1);
  assert.equal(await keyButton('original-key').count(), 1);
  assert.equal(writes().length, 3);
  await transfer.dispose();
  for (const file of ['styles.css', 'desk-v2.css', 'bot-buttons-browser-v2.css', 'app_shell.js', 'bot_buttons_level_view.js', 'bot_buttons_menu_view.js', 'settings_bot_buttons.js']) {
    const declarations = ['api/app/templates/settings_bot_buttons.html', 'api/app/static/styles.css', 'api/app/static/desk-v2.css']
      .map(file => fs.readFileSync(file, 'utf8')).join('\n');
    const version = declarations.match(new RegExp(`/static/${file.replaceAll('.', '\\.')}\\?v=([^"'\\s)]+)`))?.[1];
    assert.ok(version, `Declared asset: ${file}`);
    if (file.startsWith('bot_buttons_') || file === 'settings_bot_buttons.js') {
      assert.ok(fs.readFileSync('api/app/static/app_shell.js', 'utf8').includes('/static/' + file + '?v=' + version));
    }
    assert.ok(assets.includes(`/static/${file}?v=${version}`), `Fresh asset: ${file}`);
  }
  assert.deepEqual(errors, []);
});
