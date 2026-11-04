const test = require('node:test');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

let browser, server, origin, fixture, fixtureOrigin;
const password = 'Synthetic-Web-A1!';
const authPath = '/api/v1/web/auth/';
const menuExample = { menu_type: 'Базовое', rows: [
  [{ id: 'section', label: 'Техническая поддержка', style: 'primary', children: [[{ id: 'child', label: 'Компьютер и программы', style: 'success', children: [[{ id: 'leaf', label: 'Настройка', workflow_type: 'ticket', style: 'danger', children: null }], [{ id: 'leaf2', label: 'Диагностика', workflow_type: 'auto', style: 'primary', children: null }]] }], [{ id: 'side', label: 'Справка', workflow_type: 'group_task', style: null, children: null }]] }],
  [{ id: 'access', label: 'Доступы и учётная запись', workflow_type: 'ticket', style: 'danger', children: null }, { id: 'guide', label: 'Руководства', workflow_type: 'group_task', style: null, children: null }],
] };

const taskMenu = structuredClone(menuExample);
taskMenu.rows[0][0].children[0][0].children[0][0].workflow_type = 'task';
const taskId = '11111111-1111-4111-8111-111111111111';
test('Enter captions match both checkbox states and are restored without changing the control layout', async t => {
  const { page } = await pageFor(t);
  await submit(page);
  await page.locator('#scenario-status').getByText('Выберите тему обращения', { exact: true }).waitFor();
  const control = page.locator('.composer-options');
  const checkbox = page.locator('#sendByEnterCheckbox');
  const captions = async checked => {
    assert.equal(await checkbox.isChecked(), checked);
    assert.deepEqual(await control.locator('label > span, #sendHotkeyHint').allTextContents(), checked
      ? ['Отправлять по Enter', 'Новая строка Shift+Enter']
      : ['Отправка по Ctrl+Enter', 'Новая строка Enter']);
  };
  await captions(false);
  const before = await control.boundingBox();
  await checkbox.check();
  await captions(true);
  const after = await control.boundingBox();
  assert.ok(Math.abs(after.height - 41.984375) < 1);
  assert.ok(Math.abs(before.height - after.height) < 1);
  assert.ok(before.width <= 191 && after.width <= 191); // The existing control's maximum width was 190 px.
  assert.ok(Math.abs(before.x + before.width / 2 - after.x - after.width / 2) < 1);
  await page.locator('#logout-button').click();
  await waitLogin(page);
  await submit(page);
  await page.locator('#scenario-status').getByText('Выберите тему обращения', { exact: true }).waitFor();
  await captions(true);
  await checkbox.uncheck();
  await captions(false);
  await page.reload();
  await waitLogin(page);
  await submit(page);
  await captions(false);
});

for (const [mode, expected] of [
  ['menu', 'Выберите тему обращения'], ['confirmation', 'Ожидаем подтверждение'],
  ['description', 'Ожидаем описание задачи'], ['busy', 'Выполняем запрос…'],
  ['finished', 'Нажмите „Новое обращение“'], ['restore', 'Загружаем состояние…'],
  ['menu-loading', 'Загружаем меню…'], ['state-error', 'Не удалось обновить состояние'],
  ['menu-error', 'Не удалось загрузить меню'], ['unknown', 'Результат запроса неизвестен'],
  ['locked', 'Сессия завершена'],
]) {
  for (const width of mode === 'state-error' ? [1440, 320] : [1440]) {
  test(`compact status: ${mode} fits above the overlapping menu at ${width}px`, async t => {
    const { page, state } = await pageFor(t, { width, height: 900 });
    state.menu = taskMenu;
    let release;
    const hold = () => new Promise(resolve => { release = resolve; });
    t.after(() => release?.());
    if (mode === 'restore') state.activeHold = hold();
    if (mode === 'menu-loading') state.menuHold = hold();
    if (mode === 'state-error') state.syncStatus = 503;
    if (mode === 'menu-error') state.menuStatus = 503;
    if (mode === 'locked') state.syncStatus = 401;
    if (mode === 'confirmation' || mode === 'description') state.activeTask = activeIntake(mode === 'confirmation' ? 'confirmation_required' : 'collecting');
    if (mode === 'finished') state.chat = [chatRow(1, 'Completed scenario')];
    await submit(page);
    if (mode === 'busy' || mode === 'unknown') {
      await page.locator('#scenario-status').getByText('Выберите тему обращения', { exact: true }).waitFor();
      if (mode === 'busy') state.taskHold = hold();
      else state.taskNetwork = true;
      await openTask(page);
    }
    await page.locator('#scenario-status').getByText(expected, { exact: true }).waitFor();
    const layout = await page.locator('#scenario-status').evaluate(node => {
      const s = getComputedStyle(node), rect = node.getBoundingClientRect();
      const menu = document.querySelector('.bot-panel'), m = getComputedStyle(menu), menuRect = menu.getBoundingClientRect();
      const range = document.createRange(); range.selectNodeContents(node);
      const button = document.querySelector('#new-conversation'), b = getComputedStyle(button);
      return { height: rect.height, font: s.fontSize, weight: s.fontWeight, lines: range.getClientRects().length,
        topGap: range.getBoundingClientRect().top - rect.top, bottomGap: menuRect.top - range.getBoundingClientRect().bottom,
        bottom: range.getBoundingClientRect().bottom, menuTop: menuRect.top, overlap: rect.bottom - menuRect.top,
        frame: [s.borderColor, s.borderWidth, s.borderRadius], menuFrame: [m.borderColor, m.borderWidth, m.borderRadius],
        menuHeight: menuRect.height, overflow: s.overflowX, ellipsis: s.textOverflow,
        buttonHeight: button.getBoundingClientRect().height, buttonMin: b.minHeight, buttonFont: b.fontSize,
        buttonPadding: [b.paddingTop, b.paddingRight, b.paddingBottom, b.paddingLeft] };
    });
    assert.equal(layout.height, 104);
    assert.ok(layout.height < 176);
    assert.equal(layout.font, '18px');
    assert.equal(layout.weight, '800');
    assert.ok(layout.lines >= 1 && layout.lines <= (width > 700 ? 1 : 2), expected);
    assert.ok(layout.bottom < layout.menuTop, JSON.stringify(layout));
    assert.equal(layout.overlap, 32);
    assert.ok(Math.abs(layout.topGap - layout.bottomGap) < 2, JSON.stringify(layout));
    assert.deepEqual(layout.frame, layout.menuFrame);
    assert.ok(Math.abs(layout.menuHeight - (width > 700 ? 900 * .44 : 324)) < 1);
    assert.equal(layout.overflow, 'visible');
    assert.notEqual(layout.ellipsis, 'ellipsis');
    assert.ok(layout.buttonHeight >= 44 && layout.buttonHeight < 84);
    assert.equal(layout.buttonMin, '44px');
    assert.equal(layout.buttonFont, '19.5px');
    assert.deepEqual(layout.buttonPadding, ['10px', '18px', '10px', '18px']);
    if (process.env.COMPACT_STATUS_SCREENSHOT && mode === 'state-error' && width === 1440) {
      await page.locator('.client-sidebar').evaluate(node => { node.scrollTop = 0; });
      await page.screenshot({ path: process.env.COMPACT_STATUS_SCREENSHOT });
    }
  });
  }
}

for (const [width, height, composerHeight] of [[1440, 900, 242], [1024, 900, 242], [390, 900, 268.5], [667, 320, 267]]) {
  test(`WEB visual layout: original logo, overlapping frames and composer at ${width}x${height}`, async t => {
    const { page, state } = await pageFor(t, { width, height });
    state.menu = taskMenu;
    await submit(page);
    await page.locator('#scenario-status').getByText('Выберите тему обращения', { exact: true }).waitFor();
    await page.evaluate(() => document.fonts.ready);
    const result = await page.evaluate(() => {
      const rect = selector => document.querySelector(selector).getBoundingClientRect().toJSON();
      const frame = selector => { const s = getComputedStyle(document.querySelector(selector));
        return [s.borderTopWidth, s.borderTopStyle, s.borderTopColor, s.borderTopLeftRadius, s.borderBottomLeftRadius]; };
      const name = document.querySelector('.client-brand-name');
      const title = getComputedStyle(document.querySelector('#session-title'));
      return { brand: rect('.client-brand'), name: rect('.client-brand-name'), caption: rect('.client-brand small'),
        actions: rect('.client-sidebar > .chat-actions'), status: rect('#scenario-status'), menu: rect('.bot-panel'),
        statusFrame: frame('#scenario-status'), menuFrame: frame('.bot-panel'),
        logoSize: parseFloat(getComputedStyle(name).fontSize), logoAlign: getComputedStyle(name).textAlign, captionAlign: getComputedStyle(document.querySelector('.client-brand small')).justifyContent,
        font: getComputedStyle(name).fontFamily, loaded: document.fonts.check('34px ESHelpdeskLogo'),
        titleFont: title.fontFamily, titleWeight: title.fontWeight, titleSize: title.fontSize,
        composer: rect('#demo-composer'), textarea: rect('#demo-text'), toolbar: rect('.chat-toolbar'),
        files: rect('#demo-files'), send: rect('#demo-send'), enter: rect('.composer-options'),
        appearance: rect('#appearance-open'), menuZ: getComputedStyle(document.querySelector('.bot-panel')).zIndex,
        scrollY, overflow: document.documentElement.scrollWidth > innerWidth };
    });
    assert.match(result.font, /ESHelpdeskLogo/);
    assert.equal(result.logoSize, Math.min(34, Math.max(22, width * .025)) + 4);
    assert.equal(result.logoAlign, 'center');
    assert.equal(result.captionAlign, 'center');
    assert.equal(result.loaded, true);
    assert.ok(Math.abs(result.name.top + result.scrollY) < 1);
    assert.ok(result.actions.top >= result.caption.bottom);
    assert.ok(result.status.top >= result.actions.bottom);
    assert.deepEqual(result.statusFrame, result.menuFrame);
    assert.ok(result.menu.top < result.status.bottom && result.menu.top > result.status.top);
    assert.equal(result.menuZ, '1');
    if (width > 700) assert.ok(result.menu.height >= height * .43 && result.menu.height <= height * .45);
    assert.equal(await page.locator('.chat-toolbar .chat-actions').count(), 0);
    assert.equal(await page.locator('#bot-title').innerText(), 'Меню выбора кнопок');
    assert.equal(await page.locator('#conversation-label').isHidden(), true);
    assert.equal(result.titleSize, '17px');
    assert.equal(result.titleWeight, '800');
    assert.match(result.titleFont, /Manrope/);
    assert.ok(result.toolbar.height <= 60);
    assert.ok(result.appearance.right <= width);
    assert.ok(Math.abs(result.composer.height - composerHeight) < 1, JSON.stringify(result));
    assert.ok(result.textarea.height > 97.5);
    assert.ok(Math.abs(result.files.top - result.send.top) < 1);
    assert.ok(result.send.left - result.files.right <= 9);
    assert.ok(Math.abs(result.send.right - result.composer.right) <= 21);
    assert.equal(await page.locator('#composer-hint').isHidden(), true);
    assert.equal(result.overflow, false);
    await page.getByRole('button', { name: 'Техническая поддержка', exact: true }).waitFor();
    if (process.env.VISUAL_SCREENSHOT_DIR) {
      await page.locator('.client-sidebar').evaluate(node => { node.scrollTop = 0; });
      await page.screenshot({ path: path.join(process.env.VISUAL_SCREENSHOT_DIR, `web-layout-${width}.png`), fullPage: true });
    }
    await page.locator('#appearance-open').click();
    await page.locator('#appearanceMenu').waitFor({ state: 'visible' });
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#close-conversation').isDisabled(), true);
  });
}
for (const colorScheme of ['light', 'dark']) {
  test(`WEB panel styling: independent menu palette, raised controls and history button ${colorScheme}`, async t => {
    const { page, state } = await pageFor(t, { colorScheme });
    state.menu = structuredClone(menuExample);
    await submit(page);
    const menu = page.locator('#bot-buttons');
    await menu.locator('.bot-button').first().waitFor();
    const styles = () => menu.locator('button').evaluateAll(nodes => nodes.map(node => {
      const s = getComputedStyle(node);
      return [s.backgroundColor, s.color, s.fontSize, s.fontWeight, s.paddingTop, s.boxShadow, s.backgroundImage];
    }));
    const initial = await styles();
    assert.ok(initial.every(style => JSON.stringify(style) === JSON.stringify(initial[0])));
    assert.deepEqual(initial[0].slice(2, 5), ['15px', '800', '11.5px']);
    assert.notEqual(initial[0][5], 'none');
    assert.match(initial[0][6], /linear-gradient/);
    for (const row of state.menu.rows) for (const item of row) item.style = item.style === 'danger' ? 'success' : 'danger';
    state.me = 200;
    await page.reload();
    await menu.locator('.bot-button').first().waitFor();
    assert.deepEqual(await styles(), initial); // Server button styles cannot select WEB colors.
    const styleOf = button => button.evaluate(node => { const s = getComputedStyle(node); return {
      shadow: s.boxShadow, surface: s.backgroundImage, transform: s.transform, font: s.fontSize,
      weight: s.fontWeight, padding: [s.paddingTop, s.paddingRight], color: s.color, background: s.backgroundColor,
      height: node.getBoundingClientRect().height, touch: s.touchAction,
    }; });
    for (const button of [menu.locator('button').first(), page.locator('#new-conversation')]) {
      await page.mouse.move(0, 0);
      const idle = await styleOf(button);
      assert.equal(idle.touch, 'manipulation');
      if (await button.getAttribute('id') === 'new-conversation') {
        assert.equal(idle.font, '19.5px');
        assert.equal(idle.weight, '700');
        assert.deepEqual(idle.padding, ['10px', '18px']);
      } else assert.ok(Math.abs(idle.height - 46.2) < 1);
      await button.hover();
      const hover = await styleOf(button);
      const rgb = text => text.match(/[\d.]+/g).slice(0, 3).map(Number);
      const luminance = rgb => rgb.map(v => v / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4)
        .reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
      for (const [style, alpha] of [[idle, 32 / 255], [hover, 48 / 255]]) {
        const foreground = luminance(rgb(style.color));
        const surface = luminance(rgb(style.background).map(v => v * (1 - alpha) + 255 * alpha));
        assert.ok((Math.max(foreground, surface) + .05) / (Math.min(foreground, surface) + .05) >= 4.5);
      }
      assert.notEqual(hover.shadow, idle.shadow);
      assert.notEqual(hover.surface, idle.surface);
      await page.mouse.down();
      const pressed = await styleOf(button);
      assert.equal(pressed.transform, 'matrix(1, 0, 0, 1, 0, 2)');
      assert.notEqual(pressed.shadow, hover.shadow);
      await page.mouse.move(0, 0);
      await page.mouse.up(); // Release outside, so this check does not trigger navigation.
      assert.equal((await styleOf(button)).transform, 'none');
      await button.focus();
      await page.keyboard.press('Tab');
      await page.keyboard.press('Shift+Tab');
      assert.equal(await button.evaluate(node => node.matches(':focus-visible') && getComputedStyle(node).outlineWidth === '2px'), true);
    }
    const close = page.locator('#close-conversation');
    assert.equal(await close.isDisabled(), true);
    const disabled = await styleOf(close);
    assert.equal(disabled.shadow, 'none');
    assert.equal(disabled.transform, 'none');
    assert.equal(disabled.font, '19.5px');
    assert.deepEqual(disabled.padding, ['10px', '18px']);
    const history = page.locator('#history-open');
    const historyStyle = await history.evaluate(node => { const s = getComputedStyle(node); return {
      background: s.backgroundColor, color: s.color, border: s.borderColor,
      font: s.fontSize, weight: s.fontWeight, height: node.getBoundingClientRect().height,
    }; });
    assert.equal(historyStyle.background, colorScheme === 'light' ? 'rgb(228, 243, 232)' : 'rgb(36, 87, 62)');
    assert.notEqual(historyStyle.background, historyStyle.color);
    assert.notEqual(historyStyle.background, historyStyle.border);
    assert.equal(historyStyle.font, '18px');
    assert.equal(historyStyle.weight, '800');
    assert.ok(Math.abs(historyStyle.height - 48.2) < 1);
    await history.click();
    await page.locator('#history-dialog').waitFor({ state: 'visible' });
    assert.equal(state.taskCalls.length, 0);
    if (process.env.PANEL_SCREENSHOT_DIR) {
      await page.getByRole('button', { name: 'Закрыть историю', exact: true }).click();
      await page.locator('.client-sidebar').evaluate(node => { node.scrollTop = 0; });
      await page.screenshot({ path: path.join(process.env.PANEL_SCREENSHOT_DIR, `panel-${colorScheme}.png`) });
    }
  });
}

async function openTask(page) {
  await page.getByRole('button', { name: 'Техническая поддержка', exact: true }).click();
  await page.getByRole('button', { name: 'Компьютер и программы', exact: true }).click();
  await page.getByRole('button', { name: 'Настройка', exact: true }).click();
}

const { once } = require('node:events');

async function listen(server) {
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  return `http://127.0.0.1:${server.address().port}`;
}

async function control(name, body) {
  const response = await fetch(`${fixtureOrigin}/__test__/${name}`, {
    method: name === 'state' ? 'GET' : 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  assert.equal(response.status, 200);
  return response.json();
}

test.before(async () => {
  const python = process.env.WEB_CLIENT_TEST_PYTHON || path.resolve('.venv/Scripts/python.exe');
  fixture = spawn(python, ['-u', '-c', "import runpy; runpy.run_path('tests/web_client_api_fixture.py', run_name='__main__')"],
    { cwd: process.cwd(), stdio: ['ignore', 'pipe', 'pipe'] });
  fixtureOrigin = await new Promise((resolve, reject) => {
    let output = '';
    const timeout = setTimeout(() => reject(new Error('Isolated API startup timed out')), 30000);
    fixture.stdout.on('data', data => {
      output += data.toString();
      const match = output.match(/READY (http:\/\/127\.0\.0\.1:\d+)/);
      if (match) { clearTimeout(timeout); resolve(match[1]); }
    });
    fixture.once('error', error => { clearTimeout(timeout); reject(error); });
    fixture.once('exit', code => { clearTimeout(timeout); reject(new Error(`Isolated API exited: ${code}`)); });
  });
  const { createDevServer } = await import('../web-client/dev-server.mjs');
  server = createDevServer({ apiOrigin: fixtureOrigin });
  origin = await listen(server);
  // READY precedes Uvicorn startup by a moment. Wait for the real HTTP service.
  for (let attempt = 0; ; attempt++) {
    try { await control('origin', { origin }); break; }
    catch (error) {
      if (attempt === 30) throw error;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
  }
  browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true });
});

test.after(async () => {
  await browser?.close();
  server?.closeAllConnections();
  if (server?.listening) await new Promise(resolve => server.close(resolve));
  if (fixture && fixture.exitCode === null) {
    try { await control('shutdown'); } catch { /* Fixture may already be stopped. */ }
    await Promise.race([once(fixture, 'exit'), new Promise(resolve => setTimeout(resolve, 3000))]);
    if (fixture.exitCode === null) fixture.kill();
  }
});

async function pageFor(t, { width = 1280, height = 900, colorScheme = 'light', mocked = true } = {}) {
  const context = await browser.newContext({ viewport: { width, height }, colorScheme });
  t.after(() => context.close());
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  t.after(() => assert.deepEqual(errors, []));
  const state = { me: 401, login: 200, logout: 204, retry: null, network: false, hold: null, loginCount: 0, logoutCount: 0, menuStatus: 200, menu: menuExample, menuHold: null, menuCount: 0, taskCalls: [], taskConfirmation: true, taskHold: null, taskNetwork: false, taskStatus: 200, taskError: { error: { code: 'test_error', message: 'Ошибка API' } }, preloadCalls: [], preloadRows: new Map(), preloadStatus: 'ready', preloadCreateHold: null, preloadStatusHold: null, preloadNetwork: false, preloadHttpStatus: 200, preloadUncertain: false };
  Object.assign(state, { chat: [], activeTask: null, chatCount: 0, activeCount: 0, syncStatus: 200, chatHold: null, activeHold: null });
  const events = new Set();
  function chatEvent(key, message_type, text) {
    if (events.has(key)) return;
    events.add(key);
    state.chat.push({ id: String(Math.max(0, ...state.chat.map(row => Number(row.id))) + 1), message_type, author: message_type === 'client' ? 'Вы' : 'Elite Sochi Support',
      text, created_at: '2026-11-03T10:00:00Z' });
    state.chat = state.chat.slice(-50);
  }
  if (mocked) await page.route(`**${authPath}*`, async route => {
    const kind = new URL(route.request().url()).pathname.split('/').pop();
    if (state.network) return route.abort('failed');
    if (kind === 'login') {
      state.loginCount++;
      if (state.hold) await state.hold;
    }
    if (kind === 'logout') state.logoutCount++;
    const status = state[kind];
    const headers = state.retry ? { 'Retry-After': state.retry } : {};
    await route.fulfill({ status, headers, contentType: 'application/json', body: status === 204 ? ''
      : JSON.stringify(status === 200 ? { client_id: 'synthetic-client' }
        : { detail: 'INTERNAL TRACEBACK AND SERVER SECRET MUST NEVER APPEAR' }) });
  });
  if (mocked) await page.route('**/api/v1/web/menu', async route => {
    state.menuCount++;
    if (state.menuHold) await state.menuHold;
    await route.fulfill({ status: state.menuStatus, contentType: 'application/json', body: JSON.stringify(state.menu) });
  });
  if (mocked) await page.route('**/api/v1/web/chat', async route => {
    state.chatCount++;
    const snapshot = structuredClone(state.chat);
    state.chatStarted?.();
    if (state.chatHold) await state.chatHold;
    await route.fulfill({ status: state.syncStatus, json: { items: snapshot, total: snapshot.length, page: 1, page_size: 50 } });
  });
  if (mocked) await page.route('**/api/v1/web/tasks/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/active')) {
      state.activeCount++;
      const snapshot = structuredClone(state.activeTask);
      if (state.activeHold) await state.activeHold;
      return route.fulfill({ status: state.syncStatus, json: snapshot });
    }
    let body;
    const multipart = (route.request().headers()['content-type'] || '').startsWith('multipart/form-data');
    if (url.pathname.includes('/attachment-preloads')) {
      const method = route.request().method();
      const create = url.pathname.endsWith('/attachment-preloads');
      if (multipart) {
        const form = await new Request('http://testserver', { method: 'POST', headers: route.request().headers(), body: route.request().postDataBuffer() }).formData();
        const file = form.get('file');
        body = { upload_id: form.get('upload_id'), file: { name: file.name, type: file.type, size: file.size,
          bytes: Buffer.from(await file.arrayBuffer()).toString('base64') } };
      } else body = route.request().postData() ? route.request().postDataJSON() : undefined;
      state.preloadCalls.push({ path: url.pathname, method, body, multipart });
      if (create && state.preloadCreateHold) await state.preloadCreateHold;
      if (method === 'GET' && state.preloadStatusHold) await state.preloadStatusHold;
      if (state.preloadNetwork) return route.abort('failed');
      if (state.preloadHttpStatus !== 200) return route.fulfill({ status: state.preloadHttpStatus, json: { detail: { error: { code: 'upload_failed', message: 'Telegram INTERNAL SECRET' } } } });
      let id = url.pathname.split('/').at(-1);
      if (create) {
        const existing = [...state.preloadRows.values()].find(row => row.uploadId === body.upload_id);
        id = existing?.id || require('node:crypto').randomUUID();
        state.preloadRows.set(id, { id, uploadId: body.upload_id });
      } else if (method === 'DELETE') {
        state.preloadRows.delete(id);
        return route.fulfill({ json: { ok: true } });
      } else if (url.pathname.endsWith('/retry')) {
        id = url.pathname.split('/').at(-2);
        if (state.preloadUncertain && !body.confirm_uncertain) return route.fulfill({ status: 409,
          json: { detail: { error: { code: 'attachment_retry_uncertain', message: 'Telegram uncertain' } } } });
        state.preloadStatus = 'ready';
        state.preloadUncertain = false;
      }
      return route.fulfill({ json: { id, status: create ? 'uploading' : state.preloadStatus, uncertain: state.preloadUncertain } });
    }
    if (multipart) {
      const form = await new Request('http://testserver', { method: 'POST', headers: route.request().headers(), body: route.request().postDataBuffer() }).formData();
      body = { text: form.get('text'), files: await Promise.all(form.getAll('files').map(async file => ({
        name: file.name, type: file.type, size: file.size, bytes: Buffer.from(await file.arrayBuffer()).toString('base64'),
      }))) };
    } else body = route.request().postData() ? route.request().postDataJSON() : undefined;
    state.taskCalls.push({ path: url.pathname, body, method: route.request().method() });
    if (state.taskHold) await state.taskHold;
    if (state.taskNetwork) return route.abort('failed');
    if (state.taskStatus === 410) state.activeTask = null;
    let result;
    if (url.pathname.endsWith('/select')) result = { state: state.taskConfirmation && !body.confirmed ? 'confirmation_required' : 'collecting', text: state.taskConfirmation && !body.confirmed ? 'Подтвердите создание (сервер)' : 'Опишите задачу (сервер)' };
    else if (url.pathname.endsWith('/confirm')) result = { state: 'collecting', text: 'Опишите задачу (сервер)' };
    else if (url.pathname.endsWith('/description')) result = { state: 'ready', text: 'Задача создана (сервер)' };
    else result = { state: 'cancelled', text: 'Создание отменено (сервер)' };
    if (state.taskStatus === 200) {
      const event = url.pathname.endsWith('/select') ? body.request_id : url.pathname;
      if (url.pathname.endsWith('/select')) chatEvent(event + ':client', 'system', 'Выбран пункт «Настройка»');
      if (url.pathname.endsWith('/confirm')) chatEvent(event + ':client', 'client', 'Подтвердить');
      if (url.pathname.endsWith('/description')) chatEvent(event + ':client', 'client', body.text);
      if (url.pathname.endsWith('/cancel')) chatEvent(event + ':client', 'client', 'Отменить создание задачи');
      chatEvent(event + ':bot', 'bot', result.text);
      state.activeTask = ['ready', 'cancelled'].includes(result.state) ? null
        : { ...result, task_id: taskId, status: 'unassigned', expires_at: '2099-11-03T10:10:00Z', button_id: 'leaf' };
    }
    await route.fulfill({ status: state.taskStatus, contentType: 'application/json', body: JSON.stringify(state.taskStatus === 200 ? { ...result, task_id: taskId, status: 'unassigned', expires_at: null } : { detail: state.taskError }) });
  });
  if (mocked) await page.route('**/api/v1/web/history?*', route => route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 15 } }));
  await page.goto(origin);
  return { page, context, state };
}

async function waitLogin(page) {
  await page.locator('#login-panel').waitFor({ state: 'visible' });
}

async function submit(page) {
  await page.getByLabel('Логин', { exact: true }).fill('synthetic');
  await page.getByLabel('Пароль', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
}

for (const colorScheme of ['light', 'dark']) for (const width of [1440, 390]) {
  test(`responsive login: ${width}px, ${colorScheme}`, async t => {
    const { page } = await pageFor(t, { width, colorScheme });
    await waitLogin(page);
    assert.equal(await page.getByRole('heading', { name: 'Вход в поддержку' }).isVisible(), true);
    assert.equal(await page.getByLabel('Пароль').getAttribute('type'), 'password');
    assert.equal(await page.getByLabel('Пароль').getAttribute('autocomplete'), 'current-password');
    assert.equal(await page.getByLabel('Логин').getAttribute('autocomplete'), 'username');
    const layout = await page.evaluate(() => ({
      overflow: document.documentElement.scrollWidth > innerWidth,
      background: getComputedStyle(document.body).backgroundColor,
      card: document.querySelector('#login-panel').getBoundingClientRect().toJSON(),
    }));
    assert.equal(layout.overflow, false);
    assert.equal(layout.background, colorScheme === 'dark' ? 'rgb(17, 25, 35)' : 'rgb(244, 246, 248)');
    assert.ok(layout.card.left >= 0 && layout.card.right <= width);
    await page.getByLabel('Логин').focus();
    await page.keyboard.press('Tab');
    assert.equal(await page.getByLabel('Пароль').evaluate(el => el === document.activeElement), true);
    if (process.env.WEB_CLIENT_SCREENSHOT_DIR) {
      await page.screenshot({ path: path.join(process.env.WEB_CLIENT_SCREENSHOT_DIR, `login-${width}-${colorScheme}.png`) });
    }
  });
}

test('empty fields are validated without API requests', async t => {
  const { page, state } = await pageFor(t);
  await waitLogin(page);
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
  assert.equal(await page.locator('#login-error').textContent(), 'Введите логин и пароль.');
  assert.equal(state.loginCount, 0);
});

const chatRow = (id, text = `Event ${id}`, kind = 'bot') => ({ id: String(id), message_type: kind,
  author: 'Legacy author', text, created_at: '2026-11-03T10:00:00Z' });
const activeIntake = state => ({ task_id: taskId, button_id: 'leaf', state, status: 'unassigned',
  text: 'Restore prompt', expires_at: '2099-11-03T10:10:00Z' });
async function pollWorkspace(page) {
  const response = page.waitForResponse(response => response.url().endsWith('/api/v1/web/chat'));
  await page.clock.fastForward(5100);
  await response;
}

for (const [finish, width] of [['create', 1440], ['cancel', 390], ['timeout', 1440]]) {
  test(`TASK selection: system separator, hidden navigation and restoration after ${finish}`, async t => {
    const { page, state } = await pageFor(t, { width, colorScheme: width === 390 ? 'dark' : 'light' });
    await page.clock.install();
    state.menu = taskMenu;
    await submit(page);
    await page.locator('#bot-buttons .bot-button').first().waitFor({ state: 'attached' });
    await page.getByRole('button', { name: 'Техническая поддержка', exact: true }).click();
    await page.getByRole('button', { name: 'Компьютер и программы', exact: true }).click();
    await page.getByRole('button', { name: '⬅ Назад', exact: true }).click();
    await page.getByRole('button', { name: '⬅ Назад', exact: true }).click();
    assert.equal(state.chat.length, 0);
    assert.equal(state.taskCalls.length, 0);
    await openTask(page);
    const separator = page.locator('#chat-messages article.system');
    await separator.waitFor();
    assert.equal(await separator.innerText(), 'Выбран пункт «Настройка»');
    assert.equal(await separator.locator('.message-header, strong, span').count(), 0);
    const systemStyle = await separator.evaluate(node => {
      const style = getComputedStyle(node), ordinary = getComputedStyle(document.querySelector('#chat-messages article.bot'));
      return { align: style.alignSelf, border: style.borderTopWidth, background: style.backgroundColor,
        ordinaryBackground: ordinary.backgroundColor,
        fontDifference: parseFloat(style.fontSize) - parseFloat(ordinary.fontSize) };
    });
    assert.equal(systemStyle.align, 'center');
    assert.equal(systemStyle.border, '1px');
    assert.notEqual(systemStyle.background, 'rgba(0, 0, 0, 0)');
    assert.notEqual(systemStyle.background, systemStyle.ordinaryBackground);
    assert.equal(systemStyle.fontDifference, 1);
    assert.equal(await page.locator('#bot-buttons').isHidden(), true);
    await page.locator('#new-conversation').click();
    assert.equal(await page.locator('#bot-buttons').isHidden(), true);
    assert.equal(state.taskCalls.length, 1); // The active TASK handler must not cancel or replace it.
    if (process.env.SCENARIO_SCREENSHOT && finish === 'create') await page.screenshot({ path: process.env.SCENARIO_SCREENSHOT });
    if (finish === 'create') {
      await page.locator('#task-confirm').click();
      await page.getByText('Опишите задачу (сервер)', { exact: true }).waitFor();
      await page.locator('#demo-text').fill('Описание задачи');
      await page.locator('#demo-send').click();
      await page.getByText('Задача создана (сервер)', { exact: true }).waitFor();
      assert.match(await page.locator('#chat-messages article.client').last().innerText(), /Описание задачи/);
      assert.equal(await page.locator('#chat-messages article.client').last().evaluate(node => getComputedStyle(node).alignSelf), 'flex-end');
    } else if (finish === 'cancel') {
      await page.locator('#close-conversation').click();
      await page.getByText('Создание отменено (сервер)', { exact: true }).waitFor();
    } else {
      state.activeTask = null;
      state.chat.push(chatRow(3, 'Timeout from background'));
      await pollWorkspace(page);
      await page.getByText('Timeout from background', { exact: true }).waitFor();
    }
    assert.equal(await page.locator('#bot-buttons').isHidden(), true);
    assert.equal(await page.locator('#scenario-status').innerText(), 'Нажмите „Новое обращение“');
    await page.locator('#new-conversation').click();
    assert.equal(await page.locator('#bot-buttons').isVisible(), true);
    assert.equal(await page.locator('#scenario-status').innerText(), 'Выберите тему обращения');
    assert.equal(await page.getByRole('button', { name: '⬅ Назад', exact: true }).isVisible(), true);
    await pollWorkspace(page);
    assert.equal(await page.locator('#bot-buttons').isVisible(), true);
    const history = await page.locator('#chat-messages').innerText();
    state.me = 200;
    await page.reload();
    await separator.waitFor();
    assert.equal(await page.locator('#bot-buttons').isHidden(), true);
    assert.equal(await page.locator('#chat-messages').innerText(), history);
    await page.locator('#logout-button').click();
    await waitLogin(page);
    await submit(page);
    await separator.waitFor();
    assert.equal(await page.locator('#bot-buttons').isHidden(), true);
    assert.equal(await page.locator('#chat-messages').innerText(), history);
  });
}

test('persistent chat: restore blocks actions, confirms by task ID, and survives reload and relog', async t => {
  const { page, state } = await pageFor(t);
  state.menu = taskMenu;
  state.activeTask = activeIntake('confirmation_required');
  state.chat = [chatRow(1, '<img src=x onerror=alert(1)>', 'client'), chatRow(2, 'Restore prompt')];
  let release;
  state.activeHold = new Promise(resolve => { release = resolve; });
  await submit(page);
  await page.locator('#bot-buttons .bot-button').first().waitFor({ state: 'attached' });
  assert.equal(await page.locator('#demo-text').isDisabled(), true);
  assert.equal(await page.locator('#bot-buttons').isHidden(), true);
  state.activeHold = null;
  release();
  await page.locator('#task-confirm:not(:disabled)').waitFor();
  assert.equal(await page.locator('#bot-buttons').isHidden(), true);
  assert.deepEqual(await page.locator('#chat-messages strong').allTextContents(), ['Вы', 'Elite Sochi Support']);
  assert.equal(await page.locator('#chat-messages img').count(), 0);
  assert.doesNotMatch(await page.locator('#session-panel').innerText(), /история не сохраняется|Чат временный/);
  await page.locator('#task-confirm').click();
  await page.getByText('Опишите задачу (сервер)', { exact: true }).waitFor();
  assert.equal(state.taskCalls[0].path, `/api/v1/web/tasks/${taskId}/confirm`);
  assert.equal(state.taskCalls[0].body, undefined);
  state.me = 200;
  await page.reload();
  await page.locator('#demo-text:not(:disabled)').waitFor();
  assert.equal(await page.locator('#task-confirm').isHidden(), true);
  assert.equal(await page.locator('#close-conversation').isEnabled(), true);
  assert.equal(await page.locator('#task-cancel').isHidden(), true);
  const before = await page.locator('#chat-messages').innerText();
  await page.locator('#logout-button').click();
  await waitLogin(page);
  await submit(page);
  await page.locator('#close-conversation:not(:disabled)').waitFor();
  assert.equal(await page.locator('#chat-messages').innerText(), before);
  assert.deepEqual(await page.evaluate(() => ({ local: { ...localStorage }, session: { ...sessionStorage } })), { local: {}, session: {} });
});

test('persistent chat: one poll, stable nodes and scrolling, 50-row eviction, timeout and session stop', async t => {
  const { page, state } = await pageFor(t);
  await page.clock.install();
  state.activeTask = activeIntake('collecting');
  state.chat = Array.from({ length: 50 }, (_, i) => chatRow(i + 1));
  await submit(page);
  await page.locator('#chat-messages article').last().waitFor();
  await page.waitForFunction(() => document.querySelectorAll('#chat-messages article').length === 50);
  await page.evaluate(() => {
    const log = document.querySelector('#chat-messages');
    log.scrollTop = 300;
    window.chatNode = log.children[0];
    window.chatScroll = log.scrollTop;
  });
  await pollWorkspace(page);
  assert.equal(await page.evaluate(() => window.chatNode === document.querySelector('#chat-messages').children[0]), true);
  assert.equal(await page.locator('#chat-messages').evaluate(log => log.scrollTop), await page.evaluate(() => window.chatScroll));
  assert.equal(state.activeCount, 2);
  assert.equal(state.chatCount, 2);
  state.chat = Array.from({ length: 50 }, (_, i) => chatRow(i + 2));
  state.chat[49] = chatRow(51, 'Timeout from background');
  state.activeTask = null;
  await pollWorkspace(page);
  await page.getByText('Timeout from background', { exact: true }).waitFor();
  assert.deepEqual(await page.locator('#chat-messages article').evaluateAll(nodes => nodes.map(node => node.dataset.messageId)),
    Array.from({ length: 50 }, (_, i) => String(i + 2)));
  assert.equal(await page.locator('#task-cancel').isHidden(), true);
  assert.equal(await page.locator('#demo-send').isDisabled(), true);
  await page.locator('#logout-button').click();
  await waitLogin(page);
  const stopped = state.activeCount;
  await page.clock.fastForward(16000);
  assert.equal(state.activeCount, stopped);
  await submit(page);
  await page.getByText('Timeout from background', { exact: true }).waitFor();
  assert.equal(state.activeCount, stopped + 1);
  state.syncStatus = 401;
  await page.clock.fastForward(5100);
  await page.locator('#session-ended-dialog').waitFor({ state: 'visible' });
  const expired = state.activeCount;
  await page.clock.fastForward(16000);
  assert.equal(state.activeCount, expired);
});

test('persistent chat: a stale poll cannot overwrite a successful action or start another loop', async t => {
  const { page, state } = await pageFor(t);
  await page.clock.install();
  state.menu = taskMenu;
  state.taskConfirmation = false;
  await submit(page);
  await page.waitForFunction(() => !document.querySelector('#demo-text').disabled);
  let release;
  state.chatHold = new Promise(resolve => { release = resolve; });
  const held = new Promise(resolve => { state.chatStarted = resolve; });
  await page.clock.fastForward(5100);
  await held;
  const count = state.chatCount;
  await page.clock.fastForward(6000);
  assert.equal(state.chatCount, count); // No interval can create overlapping requests.
  state.chatHold = null;
  await openTask(page);
  await page.getByText('Опишите задачу (сервер)', { exact: true }).waitFor();
  release();
  await page.waitForFunction(() => document.querySelectorAll('#chat-messages article').length === 2);
  assert.deepEqual(await page.locator('#chat-messages p').allTextContents(), ['Выбран пункт «Настройка»', 'Опишите задачу (сервер)']);
  assert.equal(await page.locator('#close-conversation').isEnabled(), true);
  assert.equal(await page.locator('#task-cancel').isHidden(), true);
});

test('persistent chat: initial read failure blocks actions until polling restores the server state', async t => {
  const { page, state } = await pageFor(t);
  await page.clock.install();
  state.menu = taskMenu;
  state.syncStatus = 503;
  await submit(page);
  await page.locator('#task-error').waitFor({ state: 'visible' });
  assert.equal(await page.locator('#demo-text').isDisabled(), true);
  state.syncStatus = 200;
  state.activeTask = activeIntake('confirmation_required');
  await pollWorkspace(page);
  await page.locator('#task-confirm:not(:disabled)').waitFor();
  assert.equal(await page.locator('#task-error').isHidden(), true);
  assert.equal(state.taskCalls.length, 0);
});

for (const width of [1440, 390]) {
  test(`session lock: polling 401 preserves history, blocks input and exits locally at ${width}px`, async t => {
    const { page, state } = await pageFor(t, { width, colorScheme: width === 390 ? 'dark' : 'light' });
    await page.clock.install();
    state.menu = taskMenu;
    state.activeTask = activeIntake('collecting');
    state.chat = [chatRow(1, 'История прежнего клиента')];
    state.preloadStatus = 'uploading';
    await submit(page);
    await page.getByText('История прежнего клиента', { exact: true }).waitFor();
    await page.locator('#task-file-input').setInputFiles(uploadImage());
    await page.locator('.task-selected-file').waitFor();
    await page.locator('#demo-text').fill('Несохранённое описание');
    const before = await page.locator('#chat-messages').textContent();
    state.syncStatus = 401;
    await page.clock.fastForward(5100);
    const dialog = page.locator('#session-ended-dialog');
    await dialog.waitFor({ state: 'visible' });
    assert.match(await dialog.innerText(), /Сессия завершена\s+Для продолжения работы войдите снова\.\s+Выйти/);
    assert.equal(await page.locator('#chat-messages').textContent(), before);
    assert.equal(await dialog.evaluate(node => getComputedStyle(node, '::backdrop').backdropFilter), 'blur(4px)');
    await assert.rejects(page.locator('#history-open').click({ timeout: 250 }));
    await page.keyboard.press('Escape');
    assert.equal(await dialog.isVisible(), true);
    for (let i = 0; i < 3; i++) {
      await page.keyboard.press('Tab');
      assert.equal(await page.evaluate(() => Boolean(document.activeElement.closest('#session-ended-dialog'))
        || document.activeElement === document.body), true);
    }
    await page.locator('#demo-text').evaluate(node => node.focus());
    assert.equal(await page.locator('#demo-text').evaluate(node => node === document.activeElement), false);
    const requests = [];
    page.on('request', request => { if (request.url().includes('/api/')) requests.push(request.method() + ' ' + request.url()); });
    await page.clock.fastForward(16000);
    assert.deepEqual(requests, []);
    if (process.env.SESSION_LOCK_SCREENSHOT && width === 1440) await page.screenshot({ path: process.env.SESSION_LOCK_SCREENSHOT });
    await dialog.getByRole('button', { name: 'Выйти', exact: true }).click();
    await waitLogin(page);
    assert.equal(await page.locator('#chat-messages').textContent(), '');
    assert.equal(await page.locator('#demo-text').inputValue(), '');
    assert.equal(await page.locator('.task-selected-file').count(), 0);
    assert.equal(state.logoutCount, 0);
    assert.deepEqual(requests, []);
    assert.equal(state.preloadCalls.some(call => call.method === 'DELETE'), false);
    state.syncStatus = 200;
    state.chat = [chatRow(2, 'История следующего клиента')];
    state.activeTask = activeIntake('confirmation_required');
    await submit(page);
    await page.getByText('История следующего клиента', { exact: true }).waitFor();
    await page.locator('#task-confirm:not(:disabled)').waitFor();
    assert.doesNotMatch(await page.locator('#chat-messages').textContent(), /прежнего клиента/);
  });
}

test('session lock: a preload 401 stops sibling uploads without deleting remote files', async t => {
  const { page, state } = await pageFor(t);
  state.activeTask = activeIntake('collecting');
  state.chat = [chatRow(1, 'Сохранённая история')];
  await submit(page);
  await page.getByText('Сохранённая история', { exact: true }).waitFor();
  state.preloadHttpStatus = 401;
  await page.locator('#task-file-input').setInputFiles([uploadImage(), uploadNote()]);
  await page.locator('#session-ended-dialog').waitFor({ state: 'visible' });
  const calls = state.preloadCalls.length;
  await page.locator('#session-ended-dialog button').click();
  await waitLogin(page);
  assert.equal(state.preloadCalls.length, calls);
  assert.equal(state.preloadCalls.some(call => call.method === 'DELETE'), false);
  assert.equal(state.logoutCount, 0);
});

for (const [width, height] of [[1440, 1000], [1024, 600], [390, 700], [667, 320]]) {
  test(`sidebar: stable assistant and readable scenario status at ${width}x${height}`, async t => {
    const { page, state } = await pageFor(t, { width, height });
    state.menu = structuredClone(taskMenu);
    state.menu.rows.push(...Array.from({ length: 24 }, (_, i) => [{
      id: `extra-${i}`, label: `Дополнительная тема ${i}`, workflow_type: 'ticket', children: null,
    }]));
    await submit(page);
    await page.locator('#scenario-status').getByText('Выберите тему обращения', { exact: true }).waitFor();
    const geometry = () => page.locator('.client-sidebar').evaluate(sidebar => {
      const panel = sidebar.querySelector('.bot-panel').getBoundingClientRect();
      return { height: panel.height,
        history: sidebar.querySelector('#history-open').getBoundingClientRect().top - panel.top,
        info: sidebar.querySelector('.client-info').getBoundingClientRect().top - panel.top };
    });
    const baseline = await geometry();
    const stable = async () => {
      const actual = await geometry();
      for (const key of Object.keys(baseline)) assert.ok(Math.abs(actual[key] - baseline[key]) < 1, `${key}: ${JSON.stringify(actual)}`);
    };
    assert.equal(await page.locator('#bot-buttons').evaluate(node => node.scrollHeight > node.clientHeight), true);
    assert.ok(baseline.height <= height);
    await page.getByRole('button', { name: 'Дополнительная тема 23', exact: true }).scrollIntoViewIfNeeded();
    await page.getByRole('button', { name: 'Дополнительная тема 23', exact: true }).click();
    await stable();
    await page.getByRole('button', { name: 'Техническая поддержка', exact: true }).click();
    await stable();
    await page.getByRole('button', { name: 'Компьютер и программы', exact: true }).click();
    await stable();
    await page.getByRole('button', { name: 'Настройка', exact: true }).click();
    await page.locator('#scenario-status').getByText('Ожидаем подтверждение', { exact: true }).waitFor();
    assert.equal(await page.locator('#task-feedback').count(), 0);
    await stable();
    if (process.env.SIDEBAR_SCREENSHOT && width === 1440) await page.screenshot({ path: process.env.SIDEBAR_SCREENSHOT });
    await page.locator('#task-confirm').click();
    await page.locator('#scenario-status').getByText('Ожидаем описание задачи', { exact: true }).waitFor();
    await stable();
    state.me = 200;
    await page.reload();
    await page.locator('#scenario-status').getByText('Ожидаем описание задачи', { exact: true }).waitFor();
    await stable();
    await page.locator('#close-conversation').click();
    await page.locator('#scenario-status').getByText('Нажмите „Новое обращение“', { exact: true }).waitFor();
    await stable();
    await page.locator('#new-conversation').click();
    await page.locator('#scenario-status').getByText('Выберите тему обращения', { exact: true }).waitFor();
    await stable();
    assert.match(await page.locator('#history-open').innerText(), /^История обращений\s+↗$/);
    await page.locator('#history-open').click();
    await page.getByRole('heading', { name: 'История обращений', exact: true }).waitFor();
    assert.equal(await page.locator('#session-panel').getByText('История заявок', { exact: true }).count(), 0);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  });
}

test('sidebar status: loading and failed state reads do not report an active scenario', async t => {
  const { page, state } = await pageFor(t);
  await page.clock.install();
  let release;
  state.activeHold = new Promise(resolve => { release = resolve; });
  await submit(page);
  await page.locator('#scenario-status').getByText('Загружаем состояние…', { exact: true }).waitFor();
  state.activeHold = null;
  state.syncStatus = 503;
  release();
  await page.locator('#scenario-status').getByText('Не удалось обновить состояние', { exact: true }).waitFor();
  state.syncStatus = 200;
  state.activeTask = activeIntake('confirmation_required');
  await pollWorkspace(page);
  await page.locator('#scenario-status').getByText('Ожидаем подтверждение', { exact: true }).waitFor();
  await page.locator('#logout-button').click();
  await waitLogin(page);
  await submit(page);
  await page.locator('#scenario-status').getByText('Ожидаем подтверждение', { exact: true }).waitFor();
});

test('successful login clears fields and shows client workspace', async t => {
  const { page } = await pageFor(t);
  await submit(page);
  await page.getByRole('heading', { name: 'Техническая поддержка Элитного Сочи' }).waitFor();
  assert.equal(await page.getByRole('button', { name: 'Новое обращение', exact: true }).isVisible(), true);
  assert.equal(await page.locator('#password').inputValue(), '');
  assert.equal(await page.locator('#login').inputValue(), '');
});

for (const colorScheme of ['light', 'dark']) for (const width of [1440, 390]) {
  test(`client prototype: local scenarios, ${width}px, ${colorScheme}`, async t => {
    const { page } = await pageFor(t, { width, colorScheme });
    await submit(page);
    await page.getByRole('heading', { name: 'Техническая поддержка Элитного Сочи' }).waitFor();
    await page.locator('#bot-buttons .bot-button').first().waitFor({ state: 'attached' });
    const requests = [];
    page.on('request', request => { if (request.url().includes('/api/')) requests.push(request.url()); });
    const workspace = page.locator('#session-panel');
    assert.equal(await workspace.getAttribute('data-theme'), colorScheme);
    const layout = await workspace.evaluate(el => ({
      overflow: document.documentElement.scrollWidth > innerWidth,
      sidebar: el.querySelector('.client-sidebar').getBoundingClientRect().width,
      chat: el.querySelector('.client-chat').getBoundingClientRect().width,
      font: getComputedStyle(el).fontFamily,
    }));
    assert.equal(layout.overflow, false);
    assert.ok(layout.font.includes('Manrope'));
    if (width > 700) assert.ok(Math.abs(layout.chat / layout.sidebar - 2) < .1);
    if (process.env.WEB_CLIENT_SCREENSHOT_DIR) await page.screenshot({ path: path.join(process.env.WEB_CLIENT_SCREENSHOT_DIR, `workspace-${width}-${colorScheme}.png`), fullPage: true });
    assert.equal(await page.locator('#scenario-status').innerText(), 'Выберите тему обращения');
    assert.equal(await page.locator('#notifications-toggle').count(), 0);
    assert.equal(await page.locator('#bot-buttons button').count(), 3);
    for (const button of await page.locator('#bot-buttons button').all()) assert.equal(await button.isEnabled(), true);
    const beforeMenu = await page.locator('#chat-messages').innerText();
    await page.getByRole('button', { name: 'Доступы и учётная запись', exact: true }).click();
    assert.equal(await page.locator('#chat-messages').innerText(), beforeMenu);
    assert.equal(await page.getByRole('button', { name: 'Компьютер и программы', exact: true }).count(), 0);
    assert.equal(await page.locator('.columns-2 > div').count(), 2);
    await page.locator('#demo-text').fill('Не запускается рабочая программа');
    assert.equal(await page.locator('#demo-send').isDisabled(), true);
    assert.equal(await page.getByText('Демонстрационная задача Д-1043 создана:', { exact: false }).count(), 0);
    assert.equal(await page.getByText('Не запускается рабочая программа', { exact: true }).count(), 0);
    await page.locator('#demo-files').click();
    assert.ok((await page.locator('#composer-hint').innerText()).includes('только текстовое описание'));
    assert.equal(await page.locator('#close-conversation').isDisabled(), true);
    assert.equal(await page.locator('#demo-text').isEnabled(), true);
    assert.equal(await page.locator('#demo-send').isDisabled(), true);
    assert.equal(await page.getByText('Не запускается рабочая программа', { exact: true }).count(), 0);
    await page.locator('#history-open').click();
    await page.getByText('Заявок пока нет.', { exact: true }).waitFor();
    if (process.env.WEB_CLIENT_SCREENSHOT_DIR) await page.screenshot({ path: path.join(process.env.WEB_CLIENT_SCREENSHOT_DIR, `history-${width}-${colorScheme}.png`) });
    await page.keyboard.press('Escape');
    await page.locator('#appearance-open').click();
    await page.locator('[data-appearance-theme="dark"]').click();
    assert.equal(await workspace.getAttribute('data-theme'), 'dark');
    await page.locator('#interface-hue').fill('120');
    await page.locator('#interface-hue').dispatchEvent('input');
    const changed = await workspace.evaluate(el => el.style.getPropertyValue('--desk-bg'));
    await page.locator('[data-appearance-theme="light"]').click();
    await page.locator('[data-appearance-theme="dark"]').click();
    assert.equal(await workspace.evaluate(el => el.style.getPropertyValue('--desk-bg')), changed);
    assert.equal(await page.locator('#appearanceMenu').getByText('Фоновые узоры').count(), 0);
    if (process.env.WEB_CLIENT_SCREENSHOT_DIR) await page.screenshot({ path: path.join(process.env.WEB_CLIENT_SCREENSHOT_DIR, `appearance-${width}-${colorScheme}.png`) });
    await page.keyboard.press('Escape');
    await page.locator('#new-conversation').click();
    assert.equal(await page.locator('#demo-text').isEnabled(), true);
    assert.equal(await page.locator('#bot-buttons button').count(), 3);
    assert.equal(await page.locator('.bot-panel').evaluate(el => el.classList.contains('waiting')), true);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    assert.equal(requests.length, 1);
    assert.ok(requests[0].includes('/api/v1/web/history?page=1'));
  });
}

for (const sendByEnter of [false, true]) {
  test(`client composer: Enter preference ${sendByEnter}, shared server send`, async t => {
    const { page, state } = await pageFor(t);
    state.menu = taskMenu;
    state.taskConfirmation = false;
    await submit(page);
    await page.getByRole('heading', { name: 'Техническая поддержка Элитного Сочи' }).waitFor();
    await page.locator('#bot-buttons .bot-button').first().waitFor({ state: 'attached' });
    await openTask(page);
    await page.getByText('Опишите задачу (сервер)', { exact: true }).waitFor();
    const requests = [];
    page.on('request', request => { if (request.url().includes('/api/')) requests.push(request.url()); });
    const toggle = page.locator('#sendByEnterCheckbox');
    const input = page.locator('#demo-text');
    const messages = page.locator('#chat-messages');
    assert.equal(await toggle.isChecked(), false);
    await toggle.setChecked(sendByEnter);
    assert.equal(await page.locator('#sendHotkeyHint').innerText(), sendByEnter ? 'Новая строка Shift+Enter' : 'Новая строка Enter');
    const before = await messages.locator('article').count();
    await input.fill('Первая строка');
    await input.press(sendByEnter ? 'Shift+Enter' : 'Enter');
    await input.press('x');
    assert.equal(await input.inputValue(), 'Первая строка\nx');
    assert.equal(await messages.locator('article').count(), before);
    if (sendByEnter) {
      await input.dispatchEvent('keydown', { key: 'Enter', isComposing: true });
      assert.equal(await input.inputValue(), 'Первая строка\nx');
      assert.equal(await messages.locator('article').count(), before);
    }
    await input.press(sendByEnter ? 'Enter' : 'Control+Enter');
    await messages.getByText('Первая строка\nx', { exact: true }).waitFor();
    assert.equal(await input.inputValue(), '');
    assert.equal(await messages.getByText('Первая строка\nx', { exact: true }).count(), 1);
    assert.equal(await messages.getByText('Демонстрационная задача Д-1043 создана:', { exact: false }).count(), 0);
    // Both button and keyboard use the same submit handler, without duplicates.
    await input.fill('Повторное сообщение');
    assert.equal(await page.locator('#demo-send').isDisabled(), true);
    assert.equal(await messages.getByText('Повторное сообщение', { exact: true }).count(), 0);
    assert.equal(await messages.getByText('Это пример ответа поддержки.', { exact: false }).count(), 0);
    const sent = await messages.locator('article').count();
    await input.fill('   ');
    await input.press(sendByEnter ? 'Enter' : 'Control+Enter');
    assert.equal(await messages.locator('article').count(), sent);
    assert.equal(await page.locator('#close-conversation').isDisabled(), true);
    const closed = await messages.locator('article').count();
    await input.dispatchEvent('keydown', { key: 'Enter', ctrlKey: true });
    assert.equal(await messages.locator('article').count(), closed);
    assert.equal(requests.filter(url => url.endsWith('/description')).length, 1);
    assert.deepEqual(await page.evaluate(() => ({ local: { ...localStorage }, session: { ...sessionStorage } })), { local: {}, session: {} });
  });
}

for (const width of [1440, 390]) {
  test(`client menu: nested navigation, back and visual-only terminal buttons at ${width}px`, async t => {
    const { page, state } = await pageFor(t, { width, colorScheme: width === 390 ? 'dark' : 'light' });
    await submit(page);
    await page.locator('#bot-buttons .bot-button').first().waitFor({ state: 'attached' });
    const menu = page.locator('#bot-buttons');
    const rows = () => menu.locator('.menu-row').evaluateAll(lines => lines.map(line => [...line.querySelectorAll('button')].map(button => ({ label: button.textContent, color: button.className }))));
    const original = await rows();
    assert.deepEqual(original.map(row => row.map(button => button.label)), [['Техническая поддержка'], ['Доступы и учётная запись', 'Руководства']]);
    assert.equal(await menu.getByRole('button', { name: '⬅ Назад', exact: true }).count(), 0);
    const chat = await page.locator('#chat-messages').innerText();
    const conversation = await page.locator('#conversation-label').innerText();
    const requests = [];
    page.on('request', request => requests.push(request.method() + ' ' + request.url()));
    await menu.getByRole('button', { name: 'Техническая поддержка', exact: true }).click();
    assert.deepEqual(await menu.locator('button').allTextContents(), ['Компьютер и программы', 'Справка', '⬅ Назад']);
    assert.equal(await menu.getByRole('button', { name: 'Компьютер и программы', exact: true }).innerText(), 'Компьютер и программы');
    await menu.getByRole('button', { name: 'Компьютер и программы', exact: true }).press('Enter');
    assert.deepEqual(await menu.locator('button').allTextContents(), ['Настройка', 'Диагностика', '⬅ Назад']);
    assert.equal(await menu.getByRole('button', { name: 'Настройка', exact: true }).innerText(), 'Настройка');
    assert.equal(await menu.getByRole('button', { name: 'Диагностика', exact: true }).innerText(), 'Диагностика');
    await menu.getByRole('button', { name: 'Настройка', exact: true }).click();
    assert.equal(await menu.getByRole('button', { name: 'Настройка', exact: true }).getAttribute('aria-pressed'), 'true');
    assert.ok((await page.locator('#bot-hint').innerText()).includes('действие пока недоступно'));
    await menu.getByRole('button', { name: 'Диагностика', exact: true }).click();
    assert.equal(await menu.getByRole('button', { name: 'Настройка', exact: true }).getAttribute('aria-pressed'), 'false');
    assert.equal(await menu.getByRole('button', { name: 'Диагностика', exact: true }).getAttribute('aria-pressed'), 'true');
    await menu.getByRole('button', { name: '⬅ Назад', exact: true }).click();
    assert.deepEqual(await menu.locator('button').allTextContents(), ['Компьютер и программы', 'Справка', '⬅ Назад']);
    await menu.getByRole('button', { name: '⬅ Назад', exact: true }).click();
    assert.deepEqual(await rows(), original);
    assert.equal(await menu.getByRole('button', { name: '⬅ Назад', exact: true }).count(), 0);
    await menu.getByRole('button', { name: 'Руководства', exact: true }).click();
    assert.equal(await menu.getByRole('button', { name: 'Руководства', exact: true }).getAttribute('aria-pressed'), 'true');
    assert.equal(await page.locator('#chat-messages').innerText(), chat);
    assert.equal(await page.locator('#conversation-label').innerText(), conversation);
    assert.deepEqual(requests, []);
    assert.equal(state.menuCount, 1);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  });
}

for (const colorScheme of ['light', 'dark']) for (const width of [1440, 390]) {
test(`WEB task: confirmation, one description via Enter, READY and persisted chat ${width}px ${colorScheme}`, async t => {
  const { page, state } = await pageFor(t, { width, colorScheme });
  state.menu = taskMenu;
  await submit(page);
  await page.locator('#bot-buttons .bot-button').first().waitFor({ state: 'attached' });
  assert.equal(await page.locator('#demo-send').isDisabled(), true);
  let release;
  state.taskHold = new Promise(resolve => { release = resolve; });
  await openTask(page);
  await page.locator('#scenario-status').getByText('Выполняем запрос…', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Настройка', exact: true }).evaluate(button => button.click());
  assert.equal(state.taskCalls.length, 1);
  state.taskHold = null;
  release();
  await page.locator('#task-confirm').waitFor({ state: 'visible' });
  assert.equal(await page.locator('#demo-text').isDisabled(), true);
  assert.equal(await page.locator('#scenario-status').innerText(), 'Ожидаем подтверждение');
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  if (process.env.WEB_CLIENT_SCREENSHOT_DIR) await page.screenshot({ path: path.join(process.env.WEB_CLIENT_SCREENSHOT_DIR, `task-confirm-${width}-${colorScheme}.png`), fullPage: true });
  await page.locator('#task-confirm').evaluate(button => { button.click(); button.click(); });
  await page.getByText('Опишите задачу (сервер)', { exact: true }).waitFor();
  assert.equal(state.taskCalls.length, 2);
  assert.equal(state.taskCalls[1].path, `/api/v1/web/tasks/${taskId}/confirm`);
  assert.match(state.taskCalls[0].body.request_id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
  assert.equal(state.taskCalls[1].body, undefined);
  await page.locator('#sendByEnterCheckbox').check();
  await page.locator('#demo-text').fill('Описание');
  await page.locator('#demo-text').press('Shift+Enter');
  await page.locator('#demo-text').press('x');
  assert.equal(state.taskCalls.length, 2);
  state.taskHold = new Promise(resolve => { release = resolve; });
  await page.locator('#demo-text').press('Enter');
  await page.locator('#demo-composer').evaluate(form => form.dispatchEvent(new Event('submit', { cancelable: true })));
  assert.equal(await page.locator('#demo-text').isDisabled(), true);
  state.taskHold = null;
  release();
  await page.getByText('Задача создана (сервер)', { exact: true }).waitFor();
  assert.equal(state.taskCalls.length, 3);
  assert.deepEqual(state.taskCalls[2].body, { text: 'Описание\nx', preload_ids: [] });
  assert.equal(await page.locator('#demo-text').inputValue(), '');
  assert.equal(await page.getByText('Временная заметка', { exact: true }).count(), 0);
  assert.equal(await page.getByText('Описание\nx', { exact: true }).count(), 1);
  assert.equal(await page.locator('#conversation-label').innerText(), 'Технический WEB-чат');
  if (process.env.WEB_CLIENT_SCREENSHOT_DIR) await page.screenshot({ path: path.join(process.env.WEB_CLIENT_SCREENSHOT_DIR, `task-ready-${width}-${colorScheme}.png`), fullPage: true });
  await page.locator('#demo-text').fill('После READY — временная заметка');
  assert.equal(await page.locator('#demo-send').isDisabled(), true);
  assert.equal(state.taskCalls.length, 3);
  assert.equal(await page.getByText('Это пример ответа поддержки.', { exact: false }).count(), 0);
  assert.deepEqual(await page.evaluate(() => ({ local: { ...localStorage }, session: { ...sessionStorage } })), { local: {}, session: {} });
});
}

for (const confirmation of [false, true]) {
  test(`WEB task: server cancellation, confirmation ${confirmation}`, async t => {
    const { page, state } = await pageFor(t);
    state.menu = taskMenu;
    state.taskConfirmation = confirmation;
    await submit(page);
    await openTask(page);
    await page.locator('#close-conversation:not(:disabled)').waitFor();
    assert.equal(await page.locator('#task-cancel').isHidden(), true);
    state.me = 200;
    await page.reload();
    await page.locator('#close-conversation:not(:disabled)').waitFor();
    assert.equal(await page.locator('#task-confirm').isVisible(), confirmation);
    let release;
    state.taskHold = new Promise(resolve => { release = resolve; });
    t.after(() => release());
    const request = page.waitForRequest(`**/tasks/${taskId}/cancel`);
    await page.locator('#close-conversation').evaluate(button => { button.click(); button.click(); });
    await request;
    assert.equal(await page.locator('#close-conversation').isDisabled(), true);
    assert.equal(state.taskCalls.length, 2);
    state.taskHold = null;
    release();
    await page.getByText('Создание отменено (сервер)', { exact: true }).waitFor();
    assert.equal(state.taskCalls.length, 2);
    assert.equal(state.taskCalls[1].path, `/api/v1/web/tasks/${taskId}/cancel`);
    assert.equal(await page.locator('#bot-buttons').isHidden(), true);
    assert.equal(await page.locator('#task-controls').isHidden(), true);
    assert.equal(await page.locator('#close-conversation').isDisabled(), true);
  });
}

test('WEB task: close preserves an intake after a known cancellation error', async t => {
  const { page, state } = await pageFor(t);
  state.menu = taskMenu;
  state.taskConfirmation = false;
  await submit(page);
  await openTask(page);
  await page.locator('#close-conversation:not(:disabled)').waitFor();
  state.taskStatus = 400;
  await page.locator('#close-conversation').click();
  await page.locator('#task-error').waitFor({ state: 'visible' });
  assert.equal(await page.locator('#close-conversation').isEnabled(), true);
  assert.equal(await page.locator('#task-cancel').isHidden(), true);
  state.taskStatus = 200;
  await page.locator('#close-conversation').click();
  await page.getByText('Создание отменено (сервер)', { exact: true }).waitFor();
  assert.equal(state.taskCalls.filter(call => call.path.endsWith('/cancel')).length, 2);
  assert.equal(await page.locator('#close-conversation').isDisabled(), true);
});

for (const operation of ['select', 'description', 'cancel']) {
  test(`WEB task: lost ${operation} response retries the exact same operation`, async t => {
    const { page, state } = await pageFor(t);
    state.menu = taskMenu;
    state.taskConfirmation = false;
    await submit(page);
    state.taskNetwork = operation === 'select';
    await openTask(page);
    if (operation !== 'select') {
      await page.getByText('Опишите задачу (сервер)', { exact: true }).waitFor();
      state.taskNetwork = true;
      if (operation === 'description') {
        await page.locator('#demo-text').fill('Описание для повтора');
        await page.locator('#demo-send').click();
      } else await page.locator('#close-conversation').click();
    }
    await page.locator('#task-retry').waitFor({ state: 'visible' });
    const first = structuredClone(state.taskCalls.at(-1));
    assert.equal(await page.locator('#close-conversation').isDisabled(), true);
    assert.equal(await page.locator('#demo-text').isDisabled(), true);
    state.taskNetwork = false;
    await page.locator('#task-retry').evaluate(button => { button.click(); button.click(); });
    await page.getByText(operation === 'select' ? 'Опишите задачу (сервер)' : operation === 'description' ? 'Задача создана (сервер)' : 'Создание отменено (сервер)', { exact: true }).waitFor();
    assert.deepEqual(state.taskCalls.at(-1), first);
    assert.equal(state.taskCalls.length, operation === 'select' ? 2 : 3);
  });
}

for (const [status, code] of [[401, 'web_unauthorized'], [403, 'web_csrf_failed'], [410, 'web_task_expired'], [503, 'unavailable']]) {
  test(`WEB task: API error ${status} shown without fabricated success`, async t => {
    const { page, state } = await pageFor(t);
    state.menu = taskMenu;
    state.taskConfirmation = false;
    await submit(page);
    await openTask(page);
    await page.getByText('Опишите задачу (сервер)', { exact: true }).waitFor();
    state.taskStatus = status;
    state.taskError = { error: { code, message: `Сообщение API ${status}` } };
    await page.locator('#demo-text').fill('Не потерять описание');
    await page.locator('#demo-send').click();
    if (status === 401) {
      await page.locator('#session-ended-dialog').waitFor({ state: 'visible' });
      assert.equal(await page.locator('#demo-text').inputValue(), 'Не потерять описание');
      await page.locator('#session-ended-dialog button').click();
      await waitLogin(page);
      assert.equal(state.logoutCount, 0);
      return;
    }
    await page.locator('#task-error').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#task-error').innerText(), `Сообщение API ${status}`);
    assert.equal(await page.locator('#demo-text').inputValue(), 'Не потерять описание');
    assert.equal(await page.getByText('Задача создана (сервер)', { exact: true }).count(), 0);
    if (status === 503) {
      state.taskStatus = 200;
      await page.locator('#task-retry').click();
      await page.getByText('Задача создана (сервер)', { exact: true }).waitFor();
    } else if (status === 410) {
      assert.equal(await page.locator('#task-cancel').isHidden(), true);
      assert.equal(await page.locator('#bot-buttons').isHidden(), true);
    }
  });
}

test('WEB task: retry keeps its UUID until 401 locks the session', async t => {
  const { page, state } = await pageFor(t);
  state.menu = taskMenu;
  state.taskConfirmation = false;
  state.taskNetwork = true;
  await submit(page);
  await openTask(page);
  await page.locator('#task-retry').waitFor({ state: 'visible' });
  const uuid = state.taskCalls[0].body.request_id;
  state.taskNetwork = false;
  state.taskStatus = 401;
  await page.locator('#task-retry').click();
  await page.locator('#session-ended-dialog').waitFor({ state: 'visible' });
  assert.equal(state.taskCalls.length, 2);
  assert.ok(state.taskCalls.every(call => call.body.request_id === uuid));
  await page.locator('#session-ended-dialog button').click();
  await waitLogin(page);
  assert.equal(state.logoutCount, 0);
});

test('WEB task: other terminal workflows never call select', async t => {
  const { page, state } = await pageFor(t);
  state.menu = { menu_type: 'Базовое', rows: ['ticket', 'auto', 'group_task', 'group_task_child', undefined].map((workflow_type, i) => [
    { id: `unsupported-${i}`, label: `Тип ${i}`, workflow_type, style: 'danger', children: null },
  ]) };
  await submit(page);
  await page.locator('#bot-buttons .bot-button').first().waitFor({ state: 'attached' });
  const before = await page.locator('#chat-messages').innerText();
  for (let i = 0; i < 5; i++) await page.getByRole('button', { name: `Тип ${i}`, exact: true }).click();
  assert.deepEqual(state.taskCalls, []);
  assert.equal(await page.locator('#chat-messages').innerText(), before);
});

test('client menu: loading, safe error, retry, empty state and read-only colors', async t => {
  const { page, state } = await pageFor(t);
  let release;
  state.menuHold = new Promise(resolve => { release = resolve; });
  await submit(page);
  await page.getByText('Загружаем меню…', { exact: true }).waitFor();
  assert.equal(await page.locator('.bot-panel').getAttribute('aria-busy'), 'true');
  assert.equal(await page.locator('#bot-buttons button').count(), 0);
  state.menuStatus = 500;
  release();
  await page.getByText('Не удалось загрузить меню. Попробуйте ещё раз.', { exact: true }).waitFor();
  state.menuHold = null;
  state.menuStatus = 200;
  state.menu = { menu_type: 'Базовое', rows: [] };
  await page.getByRole('button', { name: 'Повторить загрузку меню' }).click();
  await page.getByText('В вашем меню пока нет доступных кнопок', { exact: true }).waitFor();
  assert.equal(await page.locator('#bot-buttons button').count(), 0);
  assert.equal(await page.locator('.bot-panel.waiting').count(), 0);
  state.menu = menuExample;
  state.me = 200;
  await page.reload();
  await page.locator('#bot-buttons .bot-button').first().waitFor({ state: 'attached' });
  assert.equal(await page.locator('#bot-buttons').getByRole('button', { name: 'Техническая поддержка', exact: true }).innerText(), 'Техническая поддержка');
  assert.equal(await page.locator('#bot-buttons .blue, #bot-buttons .green, #bot-buttons .red').count(), 0);
  assert.equal(await page.locator('#bot-buttons').getByRole('button', { name: 'Доступы и учётная запись', exact: true }).innerText(), 'Доступы и учётная запись');
  const requests = [];
  page.on('request', request => requests.push(request.method() + ' ' + request.url()));
  const chat = await page.locator('#chat-messages').innerText();
  await page.getByRole('button', { name: 'Доступы и учётная запись', exact: true }).click();
  assert.equal(await page.locator('#chat-messages').innerText(), chat);
  assert.deepEqual(requests, []);
  assert.equal(state.menuCount, 3);
});

for (const [status, text] of [[401, 'Неверный логин или пароль.'], [422, 'Проверьте правильность'],
  [403, 'безопасность запроса'], [500, 'временно недоступен'], [503, 'временно недоступен']]) {
  test(`safe login error ${status}`, async t => {
    const { page, state } = await pageFor(t);
    state.login = status;
    await submit(page);
    await page.locator('#login-error').waitFor({ state: 'visible' });
    assert.ok((await page.locator('#login-error').textContent()).includes(text));
    assert.equal((await page.locator('body').innerText()).includes('INTERNAL TRACEBACK'), false);
    assert.equal(await page.locator('#password').inputValue(), '');
  });
}

for (const header of ['30', 'http-date']) {
  test(`Retry-After countdown: ${header}`, async t => {
    const { page, state } = await pageFor(t);
    await waitLogin(page);
    await page.clock.install({ time: new Date('2026-10-19T12:00:00Z') });
    state.login = 429;
    state.retry = header === 'http-date' ? 'Mon, 19 Oct 2026 12:00:30 GMT' : header;
    await submit(page);
    await page.getByRole('button', { name: 'Повторить через 0:30' }).waitFor();
    assert.equal(await page.locator('#login-button').isDisabled(), true);
    await page.locator('#login-form').evaluate(form => form.dispatchEvent(new Event('submit', { cancelable: true })));
    assert.equal(state.loginCount, 1);
    await page.clock.fastForward(30000);
    assert.equal(await page.locator('#login-button').isEnabled(), true);
  });
}

test('in-flight requests disable duplicate form submissions and clear password immediately', async t => {
  const { page, state } = await pageFor(t);
  let release;
  state.hold = new Promise(resolve => { release = resolve; });
  await submit(page);
  assert.equal(await page.locator('#login-button').isDisabled(), true);
  assert.equal(await page.locator('#password').inputValue(), '');
  await page.locator('#login-form').evaluate(form => form.dispatchEvent(new Event('submit', { cancelable: true })));
  assert.equal(state.loginCount, 1);
  release();
  await page.getByRole('heading', { name: 'Техническая поддержка Элитного Сочи' }).waitFor();
});

test('network errors are safe and checking session can be retried', async t => {
  const { page, state } = await pageFor(t);
  state.network = true;
  await page.reload();
  await page.getByRole('heading', { name: 'Не удалось подключиться' }).waitFor();
  state.network = false;
  await page.getByRole('button', { name: 'Повторить', exact: true }).click();
  await waitLogin(page);
  state.network = true;
  await submit(page);
  await page.locator('#login-error').waitFor({ state: 'visible' });
  assert.ok((await page.locator('#login-error').textContent()).includes('временно недоступен'));
});

test('session check times out without leaving a stuck screen', async t => {
  const { page } = await pageFor(t);
  await waitLogin(page);
  await page.clock.install();
  await page.route(`**${authPath}me`, () => {});
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.clock.fastForward(16000);
  await page.getByRole('heading', { name: 'Не удалось подключиться' }).waitFor();
});

test('active session on reopen, expired session and revoked logout', async t => {
  const { page, state } = await pageFor(t);
  state.me = 200;
  await page.reload();
  await page.getByRole('heading', { name: 'Техническая поддержка Элитного Сочи' }).waitFor();
  state.logout = 401;
  await page.getByRole('button', { name: 'Выйти', exact: true }).click();
  await page.locator('#session-ended-dialog button').click();
  await waitLogin(page);
  state.me = 401;
  await page.reload();
  await waitLogin(page);
});

test('failed logout preserves the signed-in view and allows retry', async t => {
  const { page, state } = await pageFor(t);
  await submit(page);
  await page.getByRole('heading', { name: 'Техническая поддержка Элитного Сочи' }).waitFor();
  state.logout = 503;
  await page.getByRole('button', { name: 'Выйти', exact: true }).click();
  await page.locator('#logout-error').waitFor({ state: 'visible' });
  assert.equal(await page.locator('#session-panel').isVisible(), true);
  state.logout = 204;
  await page.getByRole('button', { name: 'Выйти', exact: true }).click();
  await waitLogin(page);
});

test('real task API through proxy: lost committed select, confirmation, description and one READY task', async t => {
  await control('reset');
  await control('origin', { origin });
  const { page } = await pageFor(t, { mocked: false });
  await submit(page);
  await page.getByRole('button', { name: 'Synthetic menu', exact: true }).waitFor();
  const selections = [];
  page.on('request', request => {
    if (request.url().endsWith('/api/v1/web/tasks/select')) selections.push(request.postDataJSON());
  });
  let lost = false;
  await page.route('**/api/v1/web/tasks/select', async route => {
    if (!lost) {
      lost = true;
      const result = await route.fetch();
      assert.equal(result.status(), 200);
      await route.abort('failed');
    } else await route.continue();
  });
  await page.getByRole('button', { name: 'Synthetic menu', exact: true }).click();
  await page.locator('#task-retry').waitFor({ state: 'visible' });
  assert.equal((await control('state')).tasks.length, 1);
  await page.locator('#task-retry').click();
  await page.locator('#task-confirm').waitFor({ state: 'visible' });
  await page.locator('#scenario-status').getByText('Ожидаем подтверждение', { exact: true }).waitFor();
  await page.locator('#task-confirm').click();
  await page.getByText('Synthetic description prompt', { exact: true }).waitFor();
  let descriptionLost = false;
  await page.route('**/api/v1/web/tasks/*/description', async route => {
    if (!descriptionLost) {
      descriptionLost = true;
      assert.equal((await route.fetch()).status(), 200);
      await route.abort('failed');
    } else await route.continue();
  });
  await page.locator('#demo-text').fill('Real isolated task description');
  await page.locator('#demo-send').click();
  await page.locator('#task-retry').waitFor({ state: 'visible' });
  assert.deepEqual((await control('state')).client_messages, ['Real isolated task description']);
  await page.locator('#task-retry').click();
  await page.getByText('Synthetic task created', { exact: true }).waitFor();
  await page.locator('#task-retry').waitFor({ state: 'hidden' });
  const server = await control('state');
  assert.equal(server.tasks.length, 1);
  assert.equal(server.tasks[0].channel, 'web');
  assert.equal(server.tasks[0].workflow_type, 'task');
  assert.equal(server.tasks[0].intake_state, 'ready');
  assert.deepEqual(server.client_messages, ['Real isolated task description']);
  assert.equal(selections.length, 2);
  assert.ok(selections.every(body => body.request_id === selections[0].request_id));
  assert.equal(server.requests.some(request => request.cookie_names.includes('esdesk_session')), false);
  const requests = [];
  page.on('request', request => { if (request.url().includes('/api/') && request.method() !== 'GET') requests.push(request.url()); });
  await page.locator('#demo-text').fill('Unsent note');
  assert.equal(await page.locator('#demo-send').isDisabled(), true);
  assert.deepEqual(requests, []);
  assert.deepEqual(await page.evaluate(() => ({ local: { ...localStorage }, session: { ...sessionStorage } })), { local: {}, session: {} });
});

for (const action of ['cancel', 'timeout']) {
  test(`real task API through proxy: ${action}`, async t => {
    await control('reset');
    await control('origin', { origin });
    const { page } = await pageFor(t, { mocked: false });
    await submit(page);
    await page.getByRole('button', { name: 'Synthetic menu', exact: true }).click();
    await page.locator('#task-confirm').waitFor({ state: 'visible' });
    if (action === 'cancel') {
      await page.locator('#close-conversation').click();
      await page.getByText('Synthetic task cancelled', { exact: true }).waitFor();
    } else {
      await page.locator('#task-confirm').click();
      await page.getByText('Synthetic description prompt', { exact: true }).waitFor();
      await control('task-timeout');
      await page.locator('#demo-text').fill('Too late');
      await page.locator('#demo-send').click();
      await page.locator('#task-error').getByText('Synthetic task expired', { exact: true }).waitFor();
    }
    const server = await control('state');
    assert.equal(server.tasks.length, 1);
    assert.equal(server.tasks[0].intake_state, 'cancelled');
    assert.deepEqual(server.client_messages, []);
  });
}

test('real API through proxy: cookie, Origin, reload, idle, expiry, logout and empty storage', async t => {
  await control('reset');
  const { page, context } = await pageFor(t, { mocked: false });
  await waitLogin(page);
  await context.addCookies([{ name: 'esdesk_session', value: 'synthetic-staff-cookie', url: origin, httpOnly: true }]);
  await submit(page);
  await page.getByRole('heading', { name: 'Техническая поддержка Элитного Сочи' }).waitFor();
  const cookie = (await context.cookies()).find(cookie => cookie.name === 'esdesk_web_session');
  assert.ok(cookie && cookie.httpOnly && cookie.sameSite === 'Strict' && cookie.path === '/api/v1/web');
  assert.equal(await page.evaluate(() => document.cookie.includes('esdesk_web_session')), false);
  await page.getByRole('button', { name: 'Synthetic menu', exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: 'Synthetic menu', exact: true }).isEnabled(), true);
  const before = await control('state');
  await page.reload();
  await page.getByRole('heading', { name: 'Техническая поддержка Элитного Сочи' }).waitFor();
  await page.getByRole('button', { name: 'Synthetic menu', exact: true }).waitFor();
  const after = await control('state');
  assert.ok(after.requests.some(request => request.path === '/api/v1/web/menu'));
  assert.equal(after.app_public_base_url, 'http://127.0.0.1:8000');
  assert.deepEqual(after.activities, before.activities);
  const posts = after.requests.filter(request => request.path.endsWith('/login'));
  assert.equal(posts.length, 1);
  assert.equal(posts[0].origin, origin);
  assert.equal(after.requests.some(request => request.cookie_names.includes('esdesk_session')), false);
  assert.equal(after.requests.some(request => request.forwarded_for !== null), false);
  assert.ok(after.requests.some(request => request.cookie_names.includes('esdesk_web_session')));
  assert.deepEqual(await page.evaluate(() => ({ local: { ...localStorage }, session: { ...sessionStorage } })), { local: {}, session: {} });
  assert.equal(new URL(page.url()).search, '');
  await page.getByRole('button', { name: 'Выйти', exact: true }).click();
  await waitLogin(page);
  assert.equal((await context.cookies()).some(cookie => cookie.name === 'esdesk_web_session'), false);
  assert.equal((await context.cookies()).find(cookie => cookie.name === 'esdesk_session').value, 'synthetic-staff-cookie');
  await page.reload();
  await waitLogin(page);
  await submit(page);
  await page.getByRole('heading', { name: 'Техническая поддержка Элитного Сочи' }).waitFor();
  await control('expire');
  await page.reload();
  await waitLogin(page);
});

test('real API rejects missing or foreign Origin, no staff API is proxied', async () => {
  for (const value of [null, 'http://evil.example']) {
    const response = await fetch(origin + authPath + 'login', { method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(value ? { Origin: value } : {}) },
      body: JSON.stringify({ login: 'synthetic', password }) });
    assert.equal(response.status, 403);
  }
  for (const route of ['/api/v1/management/users', '/login', '/.env', '/README.md', '/dev-server.mjs', '/api/v1/web/auth/me/extra']) {
    assert.equal((await fetch(origin + route)).status, 404);
  }
  assert.equal((await fetch(origin + authPath + 'login')).status, 405);
});

test('real API throttle status and Retry-After reach the browser through proxy', async t => {
  await control('reset');
  const { page } = await pageFor(t, { mocked: false });
  await waitLogin(page);
  await page.getByLabel('Логин', { exact: true }).fill('synthetic');
  await page.clock.install();
  for (let attempt = 1; attempt <= 4; attempt++) {
    await page.getByLabel('Пароль', { exact: true }).fill('wrong');
    const pending = page.waitForResponse(response => response.url().endsWith(authPath + 'login'));
    await page.getByRole('button', { name: 'Войти', exact: true }).click();
    const response = await pending;
    assert.equal(response.status(), 401);
    assert.equal(response.headers()['retry-after'], attempt === 4 ? '30' : undefined);
    await page.locator('#login-error').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#login-error').textContent(), 'Неверный логин или пароль.');
    if (attempt < 4) {
      await page.waitForFunction(() => !document.querySelector('#login-button').disabled);
      assert.equal(await page.locator('#login-button').textContent(), 'Войти');
    }
  }
  await page.getByRole('button', { name: 'Повторить через 0:30' }).waitFor();
  assert.equal(await page.locator('#login-button').isDisabled(), true);
  const response = await fetch(origin + authPath + 'login', { method: 'POST',
    headers: { 'Content-Type': 'application/json', Origin: origin }, body: JSON.stringify({ login: 'synthetic', password }) });
  assert.equal(response.status, 429);
  assert.equal(response.headers.get('retry-after'), '30');
  await page.clock.fastForward(30000);
  assert.equal(await page.locator('#login-button').isEnabled(), true);
});

test('proxy cannot bypass mismatched WEB_PUBLIC_BASE_URL', async t => {
  await control('reset');
  await control('origin', { origin: 'http://127.0.0.1:8000' });
  t.after(() => control('origin', { origin }));
  const { page } = await pageFor(t, { mocked: false });
  await waitLogin(page);
  await submit(page);
  await page.locator('#login-error').waitFor({ state: 'visible' });
  assert.ok((await page.locator('#login-error').textContent()).includes('безопасность запроса'));
  assert.equal((await control('state')).activities.length, 0);
});

test('unavailable upstream returns a generic 502', async () => {
  const unavailable = http.createServer();
  const unavailableOrigin = await listen(unavailable);
  await new Promise(resolve => unavailable.close(resolve));
  const { createDevServer } = await import('../web-client/dev-server.mjs');
  const proxy = createDevServer({ apiOrigin: unavailableOrigin });
  const proxyOrigin = await listen(proxy);
  try {
    const response = await fetch(proxyOrigin + authPath + 'me');
    assert.equal(response.status, 502);
    assert.deepEqual(await response.json(), { detail: 'API unavailable' });
  } finally {
    proxy.closeAllConnections();
    await new Promise(resolve => proxy.close(resolve));
  }
});

const resultBase = '/api/v1/web/task-results';
const historyBase = '/api/v1/web/history';
const fileId = '33333333-3333-4333-8333-333333333333';
const secondTaskId = '22222222-2222-4222-8222-222222222222';
const resultSummary = (id = taskId) => ({ task_id: id, message_id: fileId, closed_at: '2026-10-30T12:00:00Z', read_at: null });
const resultDetail = (id = taskId) => ({ ...resultSummary(id), text: '<script>secret()</script>\nИтоговый ответ',
  files: [{ id: fileId, filename: 'Ответ.txt', size: 12, mime_type: 'text/plain', download_url: 'https://foreign.example/secret' }],
  messages: [{ body: 'INTERNAL SECRET' }], intermediate_text: 'INTERNAL SECRET', system: 'INTERNAL SECRET' });

const historyEntry = (id = taskId) => ({ id, subject: 'Тема из Ticket ' + id, channel: 'web',
  status: 'closed', created_at: '2026-10-29T10:00:00Z', has_web_result: true, read_at: null });

async function mockResults(page) {
  const state = { items: [historyEntry()], total: 1, detail: resultDetail(), calls: [], status: {}, hold: {}, network: {}, invalid: {} };
  const handle = async route => {
    const request = route.request();
    const url = new URL(request.url());
    const action = /\/(files|attachments)\//.test(url.pathname) ? 'file' : url.pathname.endsWith('/read') ? 'read'
      : url.pathname === historyBase ? 'list' : 'detail';
    state.calls.push({ action, path: url.pathname, query: url.search, method: request.method() });
    if (state.hold[action]) await state.hold[action];
    if (state.network[action]) return route.abort('failed');
    const status = state.status[action] || 200;
    if (status !== 200) return route.fulfill({ status, json: { detail: 'INTERNAL SECRET' } });
    if (action === 'file') return route.fulfill({ contentType: 'application/octet-stream', body: state.filePayloads?.[url.pathname.split('/').at(-2)] || state.filePayloads?.[url.pathname.split('/').at(-1)] || 'result bytes' });
    const id = url.pathname.split('/')[5];
    const pageNumber = Number(url.searchParams.get('page') || 1);
    const json = action === 'list' ? { items: state.items.slice((pageNumber - 1) * 15, pageNumber * 15)
      .map(item => ({ ...item, read_at: item.read_at || state.detail.read_at })), total: state.total, page: pageNumber, page_size: 15 }
      : action === 'read' ? { task_id: id, read_at: '2026-10-31T12:00:00Z' } : { ticket_id: id, items: state.messages || [{
        id: fileId, message_type: 'agent', text: state.detail.text, created_at: state.detail.closed_at,
        files: state.detail.files.map(file => ({ ...file, source: 'web_result', preview_kind: null })),
      }] };
    await route.fulfill({ json: state.invalid[action] || json });
  };
  await page.route('**/api/v1/web/task-results**', handle);
  await page.route('**/api/v1/web/history**', handle);
  return state;
}
async function openResults(page) {
  await submit(page);
  await page.locator('#history-open').click();
}
async function selectResult(page, id = taskId) {
  await page.locator(`[data-record="${id}"]`).click();
}

for (const width of [1440, 390]) for (const colorScheme of ['light', 'dark']) {
  test(`WEB results: final answer, read marker, literal text and responsive layout ${width} ${colorScheme}`, async t => {
    const { page } = await pageFor(t, { width, colorScheme });
    const state = await mockResults(page);
    await openResults(page);
    const row = page.locator(`[data-record="${taskId}"]`);
    await row.waitFor();
    assert.equal(await row.innerText(), historyEntry().subject);
    assert.match(await row.getAttribute('title'), /Не прочитана/);
    assert.match(await page.locator('.history-table time').innerText(), /29\.10\.2026/);
    assert.equal(await page.locator('.history-table th').count(), 4);
    assert.equal(state.calls.some(call => call.action === 'read'), false);
    await row.click();
    await page.waitForFunction(() => document.querySelector('[data-record]')?.dataset.read === 'true');
    assert.match(await page.locator('#history-detail').innerText(), /<script>secret\(\)<\/script>/);
    assert.equal(await page.locator('#history-detail script').count(), 0);
    assert.doesNotMatch(await page.locator('#history-dialog').innerText(), /INTERNAL SECRET|Демо|Оставить отзыв|Предложения и замечания|Задача/);
    assert.equal(state.calls.filter(call => call.action === 'read').length, 1);
    assert.equal(state.calls.find(call => call.action === 'read').method, 'POST');
    const bounds = await page.locator('#history-dialog').evaluate(el => ({ width: el.clientWidth, scroll: el.scrollWidth, right: el.getBoundingClientRect().right }));
    assert.ok(bounds.scroll <= bounds.width + 1);
    assert.ok(bounds.right <= width);
    if (process.env.WEB_CLIENT_SCREENSHOT_DIR) await page.screenshot({ path: path.join(process.env.WEB_CLIENT_SCREENSHOT_DIR, `results-${width}-${colorScheme}.png`) });
  });
}

test('WEB results: loading, numbered pagination and list refresh on reopen', async t => {
  const { page } = await pageFor(t);
  const state = await mockResults(page);
  state.items = Array.from({ length: 16 }, (_, i) => historyEntry(`00000000-0000-4000-8000-${String(i).padStart(12, '0')}`));
  state.total = 16;
  let release;
  state.hold.list = new Promise(resolve => { release = resolve; });
  await openResults(page);
  await page.getByText('Загружаем историю…').waitFor();
  assert.equal(await page.locator('#history-list').getAttribute('aria-busy'), 'true');
  release();
  await page.getByRole('button', { name: 'Страница 2', exact: true }).waitFor();
  assert.equal(await page.locator('[data-record]').count(), 15);
  assert.equal(await page.getByRole('button', { name: 'Назад', exact: true }).isDisabled(), true);
  state.status.list = 500;
  await page.getByRole('button', { name: 'Страница 2', exact: true }).click();
  await page.locator('#history-list').getByRole('alert').waitFor();
  state.status.list = 200;
  await page.locator('#history-list').getByRole('button', { name: 'Повторить', exact: true }).click();
  await page.locator(`[data-record="${state.items[15].id}"]`).waitFor();
  assert.equal(await page.locator('[data-record]').count(), 1);
  assert.deepEqual(state.calls.filter(call => call.action === 'list').slice(1).map(call => call.query), ['?page=2', '?page=2']);
  assert.equal(await page.getByRole('button', { name: 'Вперёд', exact: true }).isDisabled(), true);
  await page.keyboard.press('Escape');
  await page.locator('#history-open').click();
  await page.locator(`[data-record="${state.items[0].id}"]`).waitFor();
  assert.equal(state.calls.filter(call => call.action === 'list').at(-1).query, '?page=1');
});

for (const action of ['list', 'detail', 'read', 'file']) {
  for (const failure of [401, 403, 404, 500, 'network', 'invalid']) {
    if (action === 'file' && failure === 'invalid') continue;
    test(`WEB results: ${action} ${failure} stays safe and retries`, async t => {
      const { page } = await pageFor(t);
      const state = await mockResults(page);
      if (failure === 'network') state.network[action] = true;
      else if (failure === 'invalid') state.invalid[action] = {};
      else state.status[action] = failure;
      await openResults(page);
      if (action !== 'list') await selectResult(page);
      if (action === 'file') await page.getByRole('button', { name: 'Скачать: Ответ.txt', exact: true }).click();
      if (failure === 401) {
        await page.locator('#session-ended-dialog').waitFor({ state: 'visible' });
        await page.locator('#session-ended-dialog button').click();
        await waitLogin(page);
        return;
      }
      const container = page.locator(action === 'list' ? '#history-list' : '#history-detail');
      await container.getByRole('alert').waitFor();
      assert.doesNotMatch(await container.innerText(), /INTERNAL SECRET/);
      if (action === 'read') {
        assert.match(await container.innerText(), /Итоговый ответ/);
        assert.match(await page.locator('[data-record]').getAttribute('title'), /Не прочитана/);
      }
      if (action === 'detail') assert.equal(state.calls.filter(call => call.action === 'read').length, 0);
      state.status[action] = 200;
      state.network[action] = false;
      delete state.invalid[action];
      const download = action === 'file' ? page.waitForEvent('download') : null;
      await container.getByRole('button', { name: 'Повторить', exact: true }).click();
      if (download) await download;
      await container.getByRole('alert').waitFor({ state: 'detached' });
      assert.ok(state.calls.filter(call => call.action === action).length >= 2);
      if (action === 'read') assert.equal(state.calls.filter(call => call.action === 'detail').length, 1);
    });
  }
}

test('WEB results: attachment uses WEB endpoint, selected ids and browser download', async t => {
  const { page } = await pageFor(t);
  const state = await mockResults(page);
  await openResults(page);
  await selectResult(page);
  const waiting = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Скачать: Ответ.txt', exact: true }).click();
  const download = await waiting;
  assert.equal(download.suggestedFilename(), 'Ответ.txt');
  const stream = await download.createReadStream();
  const chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  assert.equal(Buffer.concat(chunks).toString(), 'result bytes');
  assert.equal(state.calls.find(call => call.action === 'file').path, `${resultBase}/${taskId}/files/${fileId}`);
  assert.equal(await download.failure(), null);
});

test('WEB results: file-only result and already-read result', async t => {
  const { page } = await pageFor(t);
  const state = await mockResults(page);
  state.detail.text = '';
  state.detail.read_at = '2026-10-31T12:00:00Z';
  await openResults(page);
  await selectResult(page);
  await page.getByRole('button', { name: 'Скачать: Ответ.txt', exact: true }).waitFor();
  assert.equal(state.calls.some(call => call.action === 'read'), false);
});

test('WEB results: stale detail after switching or closing is discarded; logout clears history', async t => {
  const { page } = await pageFor(t);
  const state = await mockResults(page);
  state.items.push(historyEntry(secondTaskId));
  state.total = 2;
  let release;
  state.hold.detail = new Promise(resolve => { release = resolve; });
  await openResults(page);
  await selectResult(page);
  await page.getByText('Загружаем ответ…').waitFor();
  delete state.hold.detail;
  await selectResult(page, secondTaskId);
  await page.waitForFunction(id => document.querySelector('#history-detail h3')?.textContent === 'Тема из Ticket ' + id, secondTaskId);
  release();
  assert.deepEqual(state.calls.filter(call => call.action === 'read').map(call => call.path), [`${resultBase}/${secondTaskId}/read`]);
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => document.querySelector('#history-detail').childElementCount === 0);
  await page.getByRole('button', { name: 'Выйти', exact: true }).click();
  await waitLogin(page);
  assert.equal(await page.locator('[data-record]').count(), 0);
});

test('real result API through proxy: WEB cookie, final answer, read persistence and file bytes', async t => {
  await control('reset');
  await control('seed-results');
  const { page, context } = await pageFor(t, { mocked: false });
  await context.addCookies([{ name: 'esdesk_session', value: 'synthetic-staff-cookie', url: origin, httpOnly: true }]);
  await openResults(page);
  await selectResult(page);
  await page.getByText('Published final answer', { exact: true }).waitFor();
  await page.waitForFunction(() => document.querySelector('[data-record]')?.dataset.read === 'true');
  const waiting = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Скачать: final.txt', exact: true }).click();
  const download = await waiting;
  const chunks = [];
  for await (const chunk of await download.createReadStream()) chunks.push(chunk);
  assert.equal(Buffer.concat(chunks).toString(), 'result bytes');
  assert.equal(download.suggestedFilename(), 'final.txt');
  await page.keyboard.press('Escape');
  await page.reload();
  await page.locator('#history-open').click();
  await page.waitForFunction(() => document.querySelector('[data-record]')?.dataset.read === 'true');
  const state = await control('state');
  const requests = state.requests.filter(request => request.path.startsWith(resultBase));
  assert.ok(requests.some(request => request.path.endsWith('/read') && request.origin === origin));
  assert.ok(requests.some(request => request.path.endsWith('/files/' + fileId)));
  assert.ok(requests.every(request => request.cookie_names.includes('esdesk_web_session') && !request.cookie_names.includes('esdesk_session')));
});

for (const width of [1440, 390]) for (const colorScheme of ['light', 'dark']) {
  test(`WEB history: 15 compact rows, four columns and mixed sources ${width}px ${colorScheme}`, async t => {
    const { page } = await pageFor(t, { width, colorScheme });
    const state = await mockResults(page);
    state.items = Array.from({ length: 15 }, (_, i) => ({ ...historyEntry(`00000000-0000-4000-8000-${String(i).padStart(12, '0')}`),
      subject: i === 0 ? '<img src=x onerror=alert(1)> ' + 'Long subject '.repeat(30) : `Заявка ${i}`,
      channel: i % 2 ? 'telegram' : 'web', status: i % 2 ? 'pending' : 'closed', has_web_result: false }));
    state.total = 15;
    await openResults(page);
    await page.locator('[data-record]').last().waitFor();
    assert.equal(await page.locator('.history-table tbody tr').count(), 15);
    assert.deepEqual(await page.locator('.history-table th').allTextContents(), ['Источник', 'Тема', 'Дата и время', 'Статус']);
    assert.equal(await page.locator('.history-table tbody tr').first().locator('td').count(), 4);
    assert.equal(await page.locator('.history-table tbody tr').first().locator('td').last().innerText(), 'Закрыта');
    assert.equal(await page.locator('.history-table tbody tr').nth(1).locator('td').last().innerText(), 'В работе');
    const sourceHeaderFits = await page.locator('.history-table th').first().evaluate(el => {
      const range = document.createRange();
      range.selectNodeContents(el);
      return range.getBoundingClientRect().right <= el.getBoundingClientRect().right;
    });
    assert.equal(sourceHeaderFits, true);
    assert.equal(await page.locator('.history-table img').count(), 15);
    assert.equal(await page.locator('.history-table button img').count(), 0);
    await page.waitForFunction(() => [...document.querySelectorAll('.history-source')].every(icon => icon.complete && icon.naturalWidth > 0));
    const icons = await page.locator('.history-source').evaluateAll(elements => elements.map(el => ({
      width: el.getBoundingClientRect().width, height: el.getBoundingClientRect().height, fit: getComputedStyle(el).objectFit,
      src: el.getAttribute('src'), alt: el.alt,
    })));
    assert.ok(icons.every(icon => icon.width === 26 && icon.height === 26 && icon.fit === 'contain'));
    assert.ok(icons.every(icon => icon.src === (icon.alt === 'WEB' ? '/assets/web-source.webp' : '/assets/telegram-source.svg')));
    assert.equal(await page.getByRole('img', { name: 'WEB', exact: true }).count(), 8);
    assert.equal(await page.getByRole('img', { name: 'Telegram', exact: true }).count(), 7);
    const bounds = await page.locator('#history-dialog').evaluate(el => ({ height: el.clientHeight, scroll: el.scrollHeight,
      width: el.clientWidth, scrollWidth: el.scrollWidth, bottom: el.getBoundingClientRect().bottom }));
    assert.ok(bounds.scrollWidth <= bounds.width + 1);
    if (width > 700) { assert.ok(bounds.scroll <= bounds.height + 1); assert.ok(bounds.bottom <= 900); }
    await selectResult(page, state.items[1].id);
    await page.locator('.history-message-text').waitFor();
    assert.equal(state.calls.some(call => call.action === 'detail'), true);
    assert.equal(state.calls.some(call => call.action === 'read'), false);
    assert.equal(await page.getByRole('button', { name: 'Показать ещё', exact: true }).count(), 0);
    if (process.env.WEB_CLIENT_SCREENSHOT_DIR) await page.screenshot({ path: path.join(process.env.WEB_CLIENT_SCREENSHOT_DIR, `history-table-${width}-${colorScheme}.png`) });
  });
}

test('WEB history: closing cancels list; deletion of last page returns to an existing page', async t => {
  const { page } = await pageFor(t);
  const state = await mockResults(page);
  let release;
  state.hold.list = new Promise(resolve => { release = resolve; });
  await openResults(page);
  await page.getByText('Загружаем историю…').waitFor();
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => document.querySelector('#history-list').childElementCount === 0);
  delete state.hold.list;
  release();
  state.items = Array.from({ length: 16 }, (_, i) => historyEntry(`00000000-0000-4000-8000-${String(i).padStart(12, '0')}`));
  state.total = 16;
  await page.locator('#history-open').click();
  await page.getByRole('button', { name: 'Страница 2', exact: true }).waitFor();
  state.items.pop();
  state.total = 15;
  await page.getByRole('button', { name: 'Страница 2', exact: true }).click();
  await page.locator('[aria-current="page"]').waitFor();
  await page.waitForFunction(() => document.querySelector('[aria-current="page"]')?.textContent === '1');
  assert.equal(await page.locator('[data-record]').count(), 15);
  assert.deepEqual(state.calls.filter(call => call.action === 'list').slice(-2).map(call => call.query), ['?page=2', '?page=1']);
});

async function stubHistoryClipboard(page) {
  await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', { configurable: true, value: {
    writeText: async text => {
      if (window.historyCopyDenied) throw new Error('PRIVATE clipboard failure');
      window.historyCopiedText = text;
    },
  } }));
}

for (const [width, height] of [[1600, 900], [1366, 768]]) for (const colorScheme of ['light', 'dark']) {
  test(`history modal: desktop geometry, scroll, selection and copy ${width} ${colorScheme}`, async t => {
    const { page, state: workspaceState } = await pageFor(t, { width, height, colorScheme });
    workspaceState.chat = [chatRow(1, 'Сообщение клиента', 'client')];
    const state = await mockResults(page);
    state.items = Array.from({ length: 15 }, (_, i) => historyEntry(i === 0 ? taskId : `00000000-0000-4000-8000-${String(i).padStart(12, '0')}`));
    state.total = 15;
    state.detail.text = 'Итоговый ответ сотрудника\n' + 'Подробное описание выполненной работы.\n'.repeat(60);
    await stubHistoryClipboard(page);
    await openResults(page);
    await page.locator('[data-record]').last().waitFor();
    const dimensions = await page.locator('#history-dialog').evaluate(el => {
      const box = el.getBoundingClientRect(), list = el.querySelector('#history-list'), detail = el.querySelector('#history-detail');
      return { width: box.width, height: box.height, x: box.x, y: box.y,
        ratio: list.getBoundingClientRect().width / detail.getBoundingClientRect().width,
        listHeight: list.clientHeight, listScroll: list.scrollHeight, dialogHeight: el.clientHeight, dialogScroll: el.scrollHeight,
        paginationBottom: el.querySelector('.history-pagination').getBoundingClientRect().bottom, bottom: box.bottom, radius: getComputedStyle(el).borderTopLeftRadius };
    });
    assert.equal(dimensions.width, width - 100);
    assert.equal(dimensions.height, height - 100);
    assert.equal(dimensions.x, 50);
    assert.equal(dimensions.y, 50);
    assert.equal(dimensions.radius, '18px');
    assert.ok(Math.abs(dimensions.ratio - 1) < .01);
    assert.ok(dimensions.listHeight <= dimensions.listScroll);
    await page.locator('.history-table-scroll').evaluate(el => { el.scrollTop = 0; });
    assert.ok(dimensions.dialogScroll <= dimensions.dialogHeight + 1);
    assert.ok(dimensions.paginationBottom <= dimensions.bottom - 20);
    assert.equal(await page.locator('.history-table tbody tr').count(), 15);
    await selectResult(page);
    await page.locator('.history-message-text').waitFor();
    const detail = page.locator('#history-detail');
    assert.equal(await page.locator('#history-list').isVisible(), true);
    assert.equal(await page.locator('#history-back').isVisible(), false);
    const scroll = await detail.evaluate(el => { el.scrollTop = 120; return { top: el.scrollTop, height: el.clientHeight, scroll: el.scrollHeight }; });
    assert.ok(scroll.scroll > scroll.height && scroll.top > 0);
    const selection = await page.locator('.history-message-text').evaluate(el => {
      const range = document.createRange(); range.selectNodeContents(el);
      const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
      return { text: String(selection), userSelect: getComputedStyle(el).userSelect, fontSize: getComputedStyle(el).fontSize };
    });
    assert.equal(selection.text, state.detail.text.trimEnd()); // Browser selection omits the final unpainted newline.
    assert.equal(selection.userSelect, 'text');
    assert.equal(selection.fontSize, '18px');
    assert.deepEqual(await page.locator('.history-message-text').evaluate(el => {
      const preview = getComputedStyle(el), chat = getComputedStyle(document.querySelector('#chat-messages article'));
      return [preview.fontFamily === chat.fontFamily, preview.fontSize === chat.fontSize, preview.lineHeight === chat.lineHeight];
    }), [true, true, true]);
    assert.equal(await page.locator('.history-table button').first().evaluate(el => getComputedStyle(el).fontSize), '19px');
    await page.getByRole('button', { name: 'Копировать текст ответа', exact: true }).click();
    await page.getByText('Скопировано', { exact: true }).waitFor();
    assert.equal(await page.evaluate(() => window.historyCopiedText), state.detail.text);
    assert.equal(await page.locator('.history-table tbody tr').count(), 15);
    assert.equal(await page.locator('#history-dialog').evaluate(el => el.scrollHeight <= el.clientHeight + 1), true);
    if (process.env.WEB_CLIENT_SCREENSHOT_DIR) await page.screenshot({ path: path.join(process.env.WEB_CLIENT_SCREENSHOT_DIR, `history-modal-${width}-${colorScheme}.png`) });
  });
}

for (const width of [1440, 390]) {
  test(`history panels: pinned pagination, fixed API pages and resize preserve selection at ${width}px`, async t => {
    const { page } = await pageFor(t, { width, height: 900 });
    const state = await mockResults(page);
    state.total = width > 700 ? 16 : 17;
    state.items = Array.from({ length: state.total }, (_, i) => historyEntry(i === 0 ? taskId : `00000000-0000-4000-8000-${String(i).padStart(12, '0')}`));
    await openResults(page);
    await page.locator('[data-record]').first().waitFor();
    await page.getByRole('heading', { name: 'Список заявок', exact: true }).waitFor();
    const pinned = async () => {
      const sizes = await page.locator('#history-list').evaluate(node => {
        const scroll = node.querySelector('.history-table-scroll'), table = scroll.getBoundingClientRect();
        const rows = [...node.querySelectorAll('tbody tr')].map(row => row.getBoundingClientRect());
        const pagination = node.querySelector('.history-pagination').getBoundingClientRect();
        const pane = document.querySelector('#history-list-pane');
        return { gap: node.getBoundingClientRect().bottom - pagination.bottom, tableBottom: table.bottom,
          rowHeights: rows.map(row => row.height), lastBottom: rows.at(-1).bottom,
          scrollHeight: scroll.scrollHeight, clientHeight: scroll.clientHeight,
          tableHeight: scroll.querySelector('table').getBoundingClientRect().height,
          paginationTop: pagination.top, border: getComputedStyle(pane).borderTopWidth,
          overflow: document.querySelector('#history-dialog').scrollWidth > document.querySelector('#history-dialog').clientWidth };
      });
      assert.ok(Math.abs(sizes.gap) < 1, JSON.stringify(sizes));
      assert.ok(sizes.tableBottom <= sizes.paginationTop);
      assert.equal(sizes.border, '1px');
      assert.equal(sizes.overflow, false);
      if (sizes.rowHeights.length === 15) {
        assert.ok(sizes.tableHeight >= sizes.clientHeight - 2, JSON.stringify(sizes));
        assert.ok(Math.max(...sizes.rowHeights) - Math.min(...sizes.rowHeights) < 1);
        if (sizes.scrollHeight <= sizes.clientHeight + 1) assert.ok(sizes.paginationTop - sizes.lastBottom <= 16, JSON.stringify(sizes));
      } else assert.ok(sizes.rowHeights.every(height => height >= 38 && height <= 50), JSON.stringify(sizes));
    };
    await pinned();
    assert.equal(await page.locator('[data-record]').count(), 15);
    assert.equal(await page.locator('#history-title').evaluate(node => getComputedStyle(node).fontSize), '21px');
    assert.deepEqual(await page.locator('.history-table tbody tr').first().locator('td').evaluateAll(nodes => nodes.slice(2).map(node => getComputedStyle(node).fontSize)), ['19px', '19px']);
    const noOverlap = await page.locator('.history-table tbody tr').first().evaluate(row => [...row.cells].slice(2).every(cell => {
      const range = document.createRange(); range.selectNodeContents(cell);
      const text = range.getBoundingClientRect(), box = cell.getBoundingClientRect();
      return text.left >= box.left && text.right <= box.right;
    }));
    assert.equal(noOverlap, true);
    await selectResult(page);
    await page.locator('.history-message-text').waitFor();
    await page.getByRole('heading', { name: 'Предпросмотр', exact: true }).waitFor();
    if (width > 700) {
      await page.setViewportSize({ width, height: 1400 });
      await pinned();
      await page.setViewportSize({ width, height: 600 });
      await pinned();
      assert.equal(await page.locator('.history-table-scroll').evaluate(node => node.scrollHeight > node.clientHeight), true);
    }
    const calls = state.calls.length;
    await page.setViewportSize({ width, height: 600 });
    await page.setViewportSize({ width, height: 1000 });
    assert.equal(await page.locator(`[data-record="${taskId}"]`).getAttribute('aria-pressed'), 'true');
    assert.equal(state.calls.length, calls); // Fixed page_size=15; resize needs no additional requests.
    if (width <= 700) await page.locator('#history-back').click();
    await pinned();
    await page.getByRole('button', { name: 'Страница 2', exact: true }).click();
    await page.locator(`[data-record="${state.items[15].id}"]`).waitFor();
    assert.equal(await page.locator('[data-record]').count(), state.total - 15);
    await pinned();
    assert.equal(await page.getByRole('button', { name: 'Вперёд', exact: true }).isDisabled(), true);
    await page.getByRole('button', { name: 'Страница 1', exact: true }).click();
    await page.locator(`[data-record="${taskId}"]`).waitFor();
    assert.equal(await page.locator('[data-record]').count(), 15);
    await pinned();
  });
}

for (const width of [390, 320]) for (const colorScheme of ['light', 'dark']) {
  test(`history modal: mobile detail replaces list and returns to same page ${width} ${colorScheme}`, async t => {
    const { page } = await pageFor(t, { width, height: 844, colorScheme });
    const state = await mockResults(page);
    state.items = Array.from({ length: 16 }, (_, i) => historyEntry(`00000000-0000-4000-8000-${String(i).padStart(12, '0')}`));
    state.total = 16;
    await stubHistoryClipboard(page);
    await openResults(page);
    await page.getByRole('button', { name: 'Страница 2', exact: true }).click();
    const id = state.items[15].id;
    await page.locator(`[data-record="${id}"]`).waitFor();
    const listCalls = state.calls.filter(call => call.action === 'list').length;
    await selectResult(page, id);
    await page.locator('.history-message-text').waitFor();
    assert.equal(await page.locator('#history-list').isVisible(), false);
    assert.equal(await page.locator('#history-detail').isVisible(), true);
    assert.equal(await page.locator('#history-back').isVisible(), true);
    assert.equal(await page.locator('.history-message-text').evaluate(el => getComputedStyle(el).fontSize), '14px');
    const box = await page.locator('#history-dialog').boundingBox();
    assert.equal(box.x, 16);
    assert.equal(box.y, 16);
    assert.equal(box.width, width - 32);
    await page.getByRole('button', { name: 'Копировать текст ответа', exact: true }).click();
    await page.getByText('Скопировано', { exact: true }).waitFor();
    assert.equal(await page.evaluate(() => window.historyCopiedText), state.detail.text);
    const waiting = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Скачать: Ответ.txt', exact: true }).click();
    assert.equal((await waiting).suggestedFilename(), 'Ответ.txt');
    assert.equal(await page.locator('#history-dialog').evaluate(el => el.scrollWidth <= el.clientWidth + 1), true);
    if (process.env.WEB_CLIENT_SCREENSHOT_DIR) await page.screenshot({ path: path.join(process.env.WEB_CLIENT_SCREENSHOT_DIR, `history-detail-${width}-${colorScheme}.png`) });
    await page.locator('#history-back').click();
    assert.equal(await page.locator('#history-list').isVisible(), true);
    assert.equal(await page.locator('#history-detail').isVisible(), false);
    assert.equal(await page.locator('[aria-current="page"]').textContent(), '2');
    assert.equal(await page.locator(`[data-record="${id}"]`).evaluate(el => el === document.activeElement), true);
    assert.equal(state.calls.filter(call => call.action === 'list').length, listCalls);
  });
}

test('history modal: copy denial keeps text selectable; mobile back cancels pending detail', async t => {
  const { page } = await pageFor(t, { width: 390 });
  const state = await mockResults(page);
  await stubHistoryClipboard(page);
  await page.evaluate(() => { window.historyCopyDenied = true; });
  await openResults(page);
  await selectResult(page);
  await page.getByRole('button', { name: 'Копировать текст ответа', exact: true }).click();
  await page.getByText('Не удалось скопировать. Выделите текст и скопируйте вручную.', { exact: true }).waitFor();
  assert.equal(await page.locator('.history-message-text').textContent(), state.detail.text);
  assert.doesNotMatch(await page.locator('#history-detail').innerText(), /PRIVATE/);
  await page.locator('#history-back').click();
  const reads = state.calls.filter(call => call.action === 'read').length;
  let release;
  state.hold.detail = new Promise(resolve => { release = resolve; });
  await selectResult(page);
  await page.getByText('Загружаем ответ…', { exact: true }).waitFor();
  await page.locator('#history-back').click();
  release();
  assert.equal(await page.locator('#history-list').isVisible(), true);
  assert.equal(await page.locator('#history-detail').isVisible(), false);
  assert.equal(state.calls.filter(call => call.action === 'read').length, reads);
});

const historyFile = (index, source, mime) => ({ id: `44444444-4444-4444-8444-${String(index).padStart(12, '0')}`,
  source, mime_type: mime, filename: `file-${index}.${mime === 'image/webp' ? 'webp' : mime.split('/')[1]}`,
  size: 2048, preview_kind: null, download_url: 'https://foreign.example/forbidden' });
const historyMessage = (text, files = [], type = 'client', created = '2026-10-29T10:00:00Z') => ({
  id: fileId, message_type: type, text, files, created_at: created,
});

async function trackHistoryUrls(page) {
  await page.evaluate(() => {
    window.liveHistoryUrls = new Set();
    const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
    URL.createObjectURL = blob => { const url = create(blob); window.liveHistoryUrls.add(url); return url; };
    URL.revokeObjectURL = url => { window.liveHistoryUrls.delete(url); revoke(url); };
  });
}

for (const width of [1440, 390, 320]) {
test(`history conversation: message widths, alignment and API order at ${width}px`, async t => {
  const { page } = await pageFor(t, { width });
  const state = await mockResults(page);
  state.messages = [historyMessage('Итог из API', [], 'agent', '2026-10-30T10:00:00Z'), historyMessage('Исходный запрос')];
  await openResults(page);
  await selectResult(page);
  await page.waitForFunction(() => document.querySelectorAll('#history-detail article').length === 2);
  assert.deepEqual(await page.locator('.history-message-text').allTextContents(), ['Исходный запрос', 'Итог из API']);
  assert.equal(await page.getByText('Итог из API', { exact: true }).count(), 1);
  const geometry = await page.locator('#history-detail').evaluate(node => {
    const style = getComputedStyle(node), box = node.getBoundingClientRect();
    const left = box.left + parseFloat(style.paddingLeft), right = box.right - parseFloat(style.paddingRight);
    const innerWidth = node.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
    return [...node.querySelectorAll('article')].map(message => {
      const rect = message.getBoundingClientRect();
      return { ratio: rect.width / innerWidth, alignmentGap: message.classList.contains('client') ? right - rect.right : rect.left - left };
    });
  });
  for (const message of geometry) {
    assert.ok(Math.abs(message.ratio - (width > 700 ? .6 : .95)) < .01, JSON.stringify(geometry));
    assert.ok(Math.abs(message.alignmentGap) < 1, JSON.stringify(geometry));
  }
  // Visual fixture: system blocks must not inherit ordinary preview bubble widths.
  const system = await page.locator('#history-detail').evaluate(node => {
    const message = document.createElement('article');
    message.className = 'chat-message system'; message.textContent = 'Событие'; node.append(message);
    const style = getComputedStyle(message), ratio = message.getBoundingClientRect().width / node.clientWidth;
    const result = { ratio, align: style.alignSelf, border: style.borderTopWidth, fill: style.backgroundColor };
    message.remove();
    return result;
  });
  assert.ok(system.ratio < .5);
  assert.equal(system.align, 'center');
  assert.equal(system.border, '1px');
  assert.notEqual(system.fill, 'rgba(0, 0, 0, 0)');
  assert.deepEqual(state.calls.filter(call => call.action === 'detail').map(call => call.path), [`${historyBase}/${taskId}/messages`]);
  state.messages = [];
  if (width <= 700) await page.locator('#history-back').click();
  await selectResult(page);
  await page.getByText('Доступных сообщений пока нет.', { exact: true }).waitFor();
});

}

for (const width of [1440, 320]) {
test(`history media: authorized blobs and contained previews at ${width}px`, async t => {
  const { page } = await pageFor(t, { width });
  const state = await mockResults(page);
  state.items[0].has_web_result = false;
  const files = ['image/jpeg', 'image/png', 'image/gif', 'image/webp', 'application/pdf', 'video/webm', 'audio/ogg']
    .map((mime, index) => historyFile(index, 'document', mime));
  files[4].filename = 'ДлинноеИмя'.repeat(10) + '.pdf';
  state.messages = [historyMessage('Мои файлы ' + 'ДлинноеСлово'.repeat(80), files)];
  const png = require('node:fs').readFileSync('api/app/static/icons/image-open-original.png');
  state.filePayloads = Object.fromEntries(files.map(file => [file.id, file.mime_type === 'image/webp'
    ? require('node:fs').readFileSync('web-client/assets/web-source.webp') : png]));
  const unexpected = [];
  page.on('request', request => { if (request.url().includes('foreign.example')) unexpected.push(request.url()); });
  await trackHistoryUrls(page);
  await openResults(page);
  await selectResult(page);
  await page.waitForFunction(() => document.querySelectorAll('.history-image img').length === 4);
  assert.equal(await page.locator('#history-detail article').evaluate(node => node.scrollWidth <= node.clientWidth), true);
  assert.equal(await page.locator('#history-dialog').evaluate(node => node.scrollWidth <= node.clientWidth), true);
  assert.equal(await page.locator('.history-sticker').count(), 0);
  assert.equal(await page.locator('#history-detail video').count(), 0);
  assert.equal(await page.locator('.history-file small').first().innerText(), '2.0 КБ');
  assert.equal(await page.locator('.history-file button').filter({ hasText: 'Скачать:' }).count(), 7);
  assert.ok(state.calls.filter(call => call.action === 'file').every(call => call.path.startsWith(`${historyBase}/${taskId}/attachments/`)));
  await page.getByRole('button', { name: `Увеличить: ${files[1].filename}`, exact: true }).click();
  await page.locator('.history-image-dialog[open] img').evaluate(img => img.decode());
  assert.equal(await page.locator('.history-image-dialog[open] img').getAttribute('src').then(src => src.startsWith('blob:')), true);
  await page.getByRole('button', { name: 'Закрыть изображение', exact: true }).click();
  if (process.env.WEB_CLIENT_SCREENSHOT_DIR) await page.screenshot({ path: path.join(process.env.WEB_CLIENT_SCREENSHOT_DIR, 'history-media.png') });
  const waiting = page.waitForEvent('download');
  await page.getByRole('button', { name: `Скачать: ${files[4].filename}`, exact: true }).click();
  assert.equal((await waiting).suggestedFilename(), files[4].filename);
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => window.liveHistoryUrls.size === 0);
  assert.deepEqual(unexpected, []);
});

}

for (const width of [1440, 320]) {
test(`history stickers: contained WEBP/WEBM/TGS and resource cleanup at ${width}px`, async t => {
  const { page } = await pageFor(t, { width });
  const state = await mockResults(page);
  state.items.push(historyEntry(secondTaskId));
  state.total = 2;
  const files = ['image/webp', 'video/webm', 'application/x-tgsticker'].map((mime, index) => historyFile(index, 'sticker', mime));
  files[2].filename = 'animated.tgs';
  state.messages = [historyMessage('', files)];
  state.filePayloads = { [files[0].id]: require('node:fs').readFileSync('web-client/assets/web-source.webp'),
    [files[2].id]: require('node:zlib').gzipSync(JSON.stringify({ v: '5.5.2', layers: [] })) };
  // Exercise the shared loader/player lifecycle without relying on external CDN availability.
  await page.route('https://cdn.jsdelivr.net/npm/pako@2.1.0/dist/pako.min.js', route => route.fulfill({
    contentType: 'text/javascript', body: 'window.pako={ungzip(bytes){if(bytes[0]!==31||bytes[1]!==139)throw Error("Not gzip");window.tgsDecoded=true;return JSON.stringify({v:"5.5.2",layers:[]})}};' }));
  await page.route('https://cdn.jsdelivr.net/npm/lottie-web@5.12.2/build/player/lottie.min.js', route => route.fulfill({
    contentType: 'text/javascript', body: 'window.lottie={loadAnimation({container}){const svg=document.createElementNS("http://www.w3.org/2000/svg","svg");container.append(svg);return {destroy(){svg.remove();window.tgsDestroyed=true}}}};' }));
  await trackHistoryUrls(page);
  await openResults(page);
  await selectResult(page);
  await page.locator('.history-sticker svg').waitFor();
  assert.equal(await page.evaluate(() => window.tgsDecoded), true);
  assert.equal(await page.locator('#history-detail article').evaluate(node => node.scrollWidth <= node.clientWidth), true);
  assert.equal(await page.locator('.history-sticker img').count(), 1);
  await page.locator('.history-sticker video').evaluate(video => { window.previousStickerVideo = video; });
  assert.deepEqual(await page.locator('.history-sticker video').evaluate(video => [video.muted, video.loop, video.autoplay, video.playsInline]), [true, true, true, true]);
  state.messages = [];
  if (width <= 700) await page.locator('#history-back').click();
  await selectResult(page, secondTaskId);
  await page.getByText('Доступных сообщений пока нет.', { exact: true }).waitFor();
  assert.equal(await page.evaluate(() => window.tgsDestroyed), true);
  assert.equal(await page.evaluate(() => window.liveHistoryUrls.size), 0);
  assert.equal(await page.evaluate(() => window.previousStickerVideo.getAttribute('src')), null);
});

}

test('history preview: switching aborts pending file fetch and discards late blobs', async t => {
  const { page } = await pageFor(t);
  const state = await mockResults(page);
  state.items.push(historyEntry(secondTaskId));
  state.total = 2;
  state.messages = [historyMessage('Первая запись', [historyFile(1, 'photo', 'image/png')])];
  let release;
  state.hold.file = new Promise(resolve => { release = resolve; });
  const aborted = [];
  page.on('requestfailed', request => { if (request.url().includes('/attachments/')) aborted.push(request.failure()?.errorText); });
  await trackHistoryUrls(page);
  await openResults(page);
  await selectResult(page);
  await page.waitForRequest('**/attachments/**/download');
  state.messages = [];
  await selectResult(page, secondTaskId);
  release();
  await page.getByText('Доступных сообщений пока нет.', { exact: true }).waitFor();
  await page.waitForFunction(() => window.liveHistoryUrls.size === 0);
  assert.ok(aborted.length > 0);
  assert.equal(await page.locator('#history-detail img').count(), 0);
});

async function taskWithFiles(t, options = {}) {
  const result = await pageFor(t, options);
  result.state.menu = taskMenu;
  result.state.taskConfirmation = false;
  await submit(result.page);
  await openTask(result.page);
  await result.page.getByText('Опишите задачу (сервер)', { exact: true }).waitFor();
  return result;
}

async function filesReady(page) {
  await page.waitForFunction(() => {
    const rows = [...document.querySelectorAll('#task-selected-files .task-selected-file')];
    return rows.length > 0 && rows.every(row => row.dataset.status === 'ready');
  });
}

const uploadImage = () => ({ name: 'ordinary.webp', mimeType: 'image/webp', buffer: require('node:fs').readFileSync('web-client/assets/web-source.webp') });
const uploadNote = () => ({ name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('Original client file') });

test('task files: immediate preloads, image preview, remove and JSON description', async t => {
  const { page, state } = await taskWithFiles(t);
  await trackHistoryUrls(page);
  const chooser = page.waitForEvent('filechooser');
  await page.locator('#demo-files').click();
  await (await chooser).setFiles([uploadImage(), uploadNote()]);
  await page.locator('#task-selected-files img').evaluate(img => img.decode());
  assert.equal(await page.locator('.task-selected-file').count(), 2);
  assert.match(await page.locator('#task-selected-files').innerText(), /notes.txt.*Б/);
  await page.getByRole('button', { name: 'Удалить файл: ordinary.webp', exact: true }).click();
  assert.equal(await page.evaluate(() => window.liveHistoryUrls.size), 0);
  await page.locator('#task-file-input').setInputFiles(uploadImage());
  await page.locator('#demo-text').fill('Описание с файлами');
  await filesReady(page);
  let release;
  state.taskHold = new Promise(resolve => { release = resolve; });
  await page.locator('#demo-send').evaluate(button => { button.click(); button.click(); });
  assert.equal(await page.locator('#demo-send').isDisabled(), true);
  assert.equal(await page.getByRole('button', { name: 'Удалить файл: ordinary.webp', exact: true }).isDisabled(), true);
  assert.equal(await page.locator('#demo-send').innerText(), 'Отправить ↑');
  assert.equal(await page.locator('#demo-composer').getAttribute('aria-busy'), 'true');
  assert.match(await page.locator('#composer-hint').innerText(), /Ожидаем ответ/);
  assert.doesNotMatch(await page.locator('#scenario-status').innerText(), /\d+%/);
  release();
  await page.getByText('Задача создана (сервер)', { exact: true }).waitFor();
  const posted = state.taskCalls.filter(call => call.path.endsWith('/description'));
  assert.equal(posted.length, 1);
  assert.equal(posted[0].body.text, 'Описание с файлами');
  assert.equal(posted[0].body.preload_ids.length, 2);
  assert.equal(posted[0].body.files, undefined);
  const creates = state.preloadCalls.filter(call => call.multipart);
  assert.equal(creates.length, 3);
  assert.equal(creates[1].body.file.bytes, uploadNote().buffer.toString('base64'));
  assert.ok(creates.every(call => /^[0-9a-f-]{36}$/i.test(call.body.upload_id)));
  assert.equal(await page.locator('#task-selected-files').isVisible(), false);
  assert.equal(await page.evaluate(() => window.liveHistoryUrls.size), 1);
  const sent = page.locator('#chat-messages article.client').last();
  assert.match(await sent.innerText(), /Описание с файлами/);
  assert.match(await sent.innerText(), /notes.txt/);
  assert.match(await sent.innerText(), /ordinary.webp/);
  assert.equal(await sent.locator('.task-sent-file').count(), 2);
  assert.equal(await sent.locator('img').count(), 1);
  await sent.locator('img').evaluate(img => img.decode());
  assert.equal(await sent.getByRole('button', { name: /Удалить/ }).count(), 0);
  assert.equal(await page.locator('#demo-composer').getAttribute('aria-busy'), 'false');
  await page.getByRole('button', { name: 'Выйти', exact: true }).click();
  await waitLogin(page);
  assert.equal(await page.evaluate(() => window.liveHistoryUrls.size), 0);
});

test('task files: per-file size limit and sticker rejection happen before upload', async t => {
  const { page, state } = await taskWithFiles(t);
  await page.locator('#task-file-input').setInputFiles({ name: 'large.bin', mimeType: 'application/octet-stream', buffer: Buffer.alloc(20 * 1024 * 1024 + 1) });
  await page.getByText('large.bin: размер превышает 20 МиБ.', { exact: true }).waitFor();
  assert.equal(await page.locator('.task-selected-file').count(), 0);
  assert.equal(state.taskCalls.length, 1);
  await page.locator('#task-file-input').setInputFiles({ name: 'sticker.tgs', mimeType: 'application/octet-stream', buffer: Buffer.from('gzip') });
  await page.getByText('sticker.tgs: стикеры не поддерживаются.', { exact: true }).waitFor();
  assert.equal(await page.locator('.task-selected-file').count(), 0);
  await page.locator('#task-file-input').setInputFiles(uploadImage());
  assert.equal(await page.locator('.task-selected-file img').count(), 1);
});

test('task files: unknown response locks files and retry preserves exact bytes', async t => {
  const { page, state } = await taskWithFiles(t);
  await trackHistoryUrls(page);
  await page.locator('#task-file-input').setInputFiles([uploadImage(), uploadNote()]);
  await page.locator('#demo-text').fill('Retry same upload');
  await filesReady(page);
  state.taskNetwork = true;
  await page.locator('#demo-send').click();
  await page.locator('#task-retry').waitFor({ state: 'visible' });
  assert.equal(await page.getByRole('button', { name: 'Удалить файл: ordinary.webp', exact: true }).isDisabled(), true);
  assert.equal(await page.locator('.task-selected-file').count(), 2);
  state.taskNetwork = false;
  await page.locator('#task-retry').click();
  await page.getByText('Задача создана (сервер)', { exact: true }).waitFor();
  const posts = state.taskCalls.filter(call => call.path.endsWith('/description'));
  assert.equal(posts.length, 2);
  assert.deepEqual(posts[0].body, posts[1].body);
  assert.equal(await page.evaluate(() => window.liveHistoryUrls.size), 1);
  assert.equal(await page.locator('#chat-messages .task-sent-file').count(), 2);
  assert.equal(await page.locator('#chat-messages article.client').count(), 1);
  await page.getByRole('button', { name: 'Выйти', exact: true }).click();
  await waitLogin(page);
  assert.equal(await page.evaluate(() => window.liveHistoryUrls.size), 0);
});

test('task files: known rollback keeps selection editable; cancel frees preview', async t => {
  const { page, state } = await taskWithFiles(t);
  await trackHistoryUrls(page);
  await page.locator('#task-file-input').setInputFiles([uploadImage(), uploadNote()]);
  await page.locator('#demo-text').fill('Описание');
  await filesReady(page);
  state.taskStatus = 502;
  state.taskError = { error: { code: 'web_task_upload_failed', message: 'Не удалось загрузить файлы. Повторите отправку.' } };
  await page.locator('#demo-send').click();
  await page.locator('#task-error').waitFor({ state: 'visible' });
  assert.equal(await page.locator('#demo-send').isEnabled(), true);
  assert.equal(await page.locator('#task-selected-files .task-selected-file').count(), 2);
  assert.equal(await page.locator('#chat-messages .task-sent-file').count(), 0);
  assert.equal(await page.locator('#demo-send').innerText(), 'Отправить ↑');
  await page.getByRole('button', { name: 'Удалить файл: notes.txt', exact: true }).click();
  assert.equal(await page.locator('.task-selected-file').count(), 1);
  state.taskStatus = 200;
  await page.locator('#close-conversation').click();
  await page.getByText('Создание отменено (сервер)', { exact: true }).waitFor();
  assert.equal(await page.locator('#task-selected-files').isVisible(), false);
  assert.equal(await page.evaluate(() => window.liveHistoryUrls.size), 0);
  await page.locator('#demo-files').click();
  assert.match(await page.locator('#composer-hint').innerText(), /только текстовое описание/);
});

test('real task upload through proxy: lost committed response archives once and history downloads same bytes', async t => {
  await control('reset');
  const { page } = await pageFor(t, { mocked: false });
  await submit(page);
  await page.getByRole('button', { name: 'Synthetic menu', exact: true }).click();
  await page.locator('#task-confirm').click();
  await page.getByText('Synthetic description prompt', { exact: true }).waitFor();
  // Finish preload setup before testing a lost description response.
  await page.locator('#task-file-input').setInputFiles(uploadImage());
  await filesReady(page);
  await page.locator('#task-file-input').setInputFiles(uploadNote());
  await filesReady(page);
  let lost = false;
  await page.route('**/api/v1/web/tasks/*/description', async route => {
    if (lost) return route.continue();
    lost = true;
    assert.equal((await route.fetch()).status(), 200);
    await route.abort('failed');
  });
  await page.locator('#demo-text').fill('Real upload request');
  await page.locator('#demo-send').click();
  await page.locator('#task-retry').waitFor({ state: 'visible' });
  await page.locator('#task-retry').click();
  await page.getByText('Synthetic task created', { exact: true }).waitFor();
  const state = await control('state');
  assert.deepEqual(state.client_messages, ['Real upload request']);
  assert.equal(state.tasks.length, 1);
  assert.equal(state.attachments.length, 2);
  assert.equal(new Set(state.attachments.map(file => file.message_id)).size, 1);
  assert.equal(state.archive_calls.length, 2);
  const summaries = Object.values(state.active_summaries);
  assert.equal(summaries.length, 1, JSON.stringify({ active: state.active_summaries, sent: state.summary_calls, deleted: state.deleted_archive_ids }));
  assert.ok(summaries[0].includes('ordinary.webp') && summaries[0].includes('notes.txt'));
  assert.equal(state.summary_calls.length - state.deleted_archive_ids.filter(id => Number(id) >= 2000).length, 1);
  await page.waitForFunction(() => document.querySelectorAll('#chat-messages .task-sent-file').length === 2);
  assert.equal(await page.locator('#chat-messages .task-sent-file').count(), 2);
  await page.locator('#history-open').click();
  await page.locator('[data-record]').click();
  await page.locator('#history-detail').getByText('Real upload request', { exact: true }).waitFor();
  await page.locator('.history-image img').evaluate(img => img.decode());
  const waiting = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Скачать: notes.txt', exact: true }).click();
  const chunks = [];
  for await (const chunk of await (await waiting).createReadStream()) chunks.push(chunk);
  assert.equal(Buffer.concat(chunks).toString(), 'Original client file');
});


test('task preloads: loading text and indeterminate bar disappear only on ready; label never changes', async t => {
  const { page, state } = await taskWithFiles(t);
  state.preloadStatus = 'uploading';
  await page.locator('#demo-text').fill('Text while uploading');
  await page.locator('#task-file-input').setInputFiles([uploadImage(), uploadNote()]);
  await page.waitForFunction(() => document.querySelectorAll('.task-file-progress').length === 2);
  assert.deepEqual(await page.locator('.task-file-loading').allTextContents(), ['Идёт загрузка...', 'Идёт загрузка...']);
  assert.equal(await page.locator('.task-file-progress').first().evaluate(el => getComputedStyle(el, '::after').animationName), 'task-file-loading');
  assert.equal(await page.locator('.task-file-progress').first().getAttribute('aria-valuenow'), null);
  assert.equal(await page.locator('#demo-send').isDisabled(), true);
  assert.equal(await page.locator('#demo-send').innerText(), 'Отправить ↑');
  assert.doesNotMatch(await page.locator('#task-selected-files').innerText(), /Telegram|Телеграм|\d+%/i);
  assert.equal(state.preloadCalls.filter(call => call.multipart).length, 2);
  await page.locator('#demo-composer').evaluate(form => form.dispatchEvent(new Event('submit', { cancelable: true })));
  assert.equal(state.taskCalls.filter(call => call.path.endsWith('/description')).length, 0);
  state.preloadStatus = 'ready';
  await filesReady(page);
  assert.equal(await page.locator('.task-file-loading, .task-file-progress').count(), 0);
  assert.equal(await page.locator('#task-selected-files img').count(), 1);
  assert.equal(await page.locator('#demo-send').isEnabled(), true);
  await page.locator('#demo-text').fill('   ');
  assert.equal(await page.locator('#demo-send').isDisabled(), true);
});


test('task preloads: file errors block submit; retry uses existing ID without another file POST', async t => {
  const { page, state } = await taskWithFiles(t);
  state.preloadStatus = 'failed';
  await page.locator('#demo-text').fill('Description');
  await page.locator('#task-file-input').setInputFiles(uploadNote());
  await page.locator('.task-file-failure').waitFor();
  assert.equal(await page.locator('#demo-send').isDisabled(), true);
  assert.equal(await page.locator('.task-file-progress').count(), 0);
  await page.locator('#task-selected-files').getByRole('button', { name: 'Повторить', exact: true }).click();
  await filesReady(page);
  const retries = state.preloadCalls.filter(call => call.path.endsWith('/retry'));
  assert.equal(retries.length, 1);
  assert.deepEqual(retries[0].body, { confirm_uncertain: false });
  assert.equal(state.preloadCalls.filter(call => call.multipart).length, 1);
  assert.equal(await page.locator('#demo-send').isEnabled(), true);
});


test('task preloads: unknown delivery requires a separate explicit confirmation and hides transport details', async t => {
  const { page, state } = await taskWithFiles(t);
  state.preloadStatus = 'failed';
  state.preloadUncertain = true;
  await page.locator('#demo-text').fill('Description');
  await page.locator('#task-file-input').setInputFiles(uploadNote());
  await page.getByRole('button', { name: 'Подтвердить повтор загрузки', exact: true }).waitFor();
  assert.equal(state.preloadCalls.filter(call => call.path.endsWith('/retry')).length, 0);
  assert.equal(await page.locator('#demo-send').isDisabled(), true);
  assert.match(await page.locator('.task-file-failure').innerText(), /лишнюю копию/);
  assert.doesNotMatch(await page.locator('#task-selected-files').innerText(), /Telegram|Телеграм/i);
  await page.getByRole('button', { name: 'Подтвердить повтор загрузки', exact: true }).click();
  await filesReady(page);
  assert.deepEqual(state.preloadCalls.find(call => call.path.endsWith('/retry')).body, { confirm_uncertain: true });
});


test('task preloads: lost create response retries with identical upload UUID and file', async t => {
  const { page, state } = await taskWithFiles(t);
  state.preloadNetwork = true;
  await page.locator('#demo-text').fill('Description');
  await page.locator('#task-file-input').setInputFiles(uploadNote());
  await page.locator('.task-file-failure').waitFor();
  assert.equal(await page.locator('#demo-send').isDisabled(), true);
  state.preloadNetwork = false;
  await page.locator('#task-selected-files').getByRole('button', { name: 'Повторить', exact: true }).click();
  await filesReady(page);
  const creates = state.preloadCalls.filter(call => call.multipart);
  assert.equal(creates.length, 2);
  assert.deepEqual(creates[0].body, creates[1].body);
});


test('task preloads: removal during create deletes late ID and sends only the other file', async t => {
  const { page, state } = await taskWithFiles(t);
  await trackHistoryUrls(page);
  let release;
  state.preloadCreateHold = new Promise(resolve => { release = resolve; });
  t.after(() => release());
  await page.locator('#task-file-input').setInputFiles([uploadImage(), uploadNote()]);
  await page.getByRole('button', { name: 'Удалить файл: ordinary.webp', exact: true }).click();
  assert.equal(await page.locator('#task-selected-files .task-selected-file').count(), 1);
  assert.equal(await page.evaluate(() => window.liveHistoryUrls.size), 0);
  release();
  state.preloadCreateHold = null;
  await filesReady(page);
  await page.waitForFunction(() => document.querySelector('#task-selected-files').innerText.includes('notes.txt'));
  await page.locator('#demo-text').fill('Only note');
  await page.locator('#demo-send').click();
  await page.getByText('Задача создана (сервер)', { exact: true }).waitFor();
  const deleted = state.preloadCalls.find(call => call.method === 'DELETE');
  assert.ok(deleted);
  const submitted = state.taskCalls.find(call => call.path.endsWith('/description')).body;
  assert.equal(submitted.preload_ids.length, 1);
  assert.ok(!deleted.path.endsWith(submitted.preload_ids[0]));
  assert.equal(submitted.files, undefined);
  assert.equal(await page.locator('#chat-messages .task-sent-file').count(), 1);
  assert.match(await page.locator('#chat-messages article.client').last().innerText(), /notes.txt/);
});


test('task preloads: removal during status polling never restores a selected file', async t => {
  const { page, state } = await taskWithFiles(t);
  let release;
  state.preloadStatusHold = new Promise(resolve => { release = resolve; });
  t.after(() => release());
  const polling = page.waitForRequest(request => request.method() === 'GET' && request.url().includes('/attachment-preloads/'));
  await page.locator('#task-file-input').setInputFiles(uploadNote());
  await polling;
  await page.getByRole('button', { name: 'Удалить файл: notes.txt', exact: true }).click();
  release();
  state.preloadStatusHold = null;
  await page.locator('#demo-text').fill('Without file');
  await page.locator('#demo-send').click();
  await page.getByText('Задача создана (сервер)', { exact: true }).waitFor();
  assert.equal(await page.locator('#task-selected-files .task-selected-file').count(), 0);
  assert.equal(await page.locator('#chat-messages .task-sent-file').count(), 0);
  assert.deepEqual(state.taskCalls.find(call => call.path.endsWith('/description')).body.preload_ids, []);
  assert.equal(state.preloadCalls.filter(call => call.method === 'DELETE').length, 1);
});


test('task preloads: cancel clears loading rows and URLs without creating another task', async t => {
  const { page, state } = await taskWithFiles(t);
  await trackHistoryUrls(page);
  state.preloadStatus = 'uploading';
  await page.locator('#task-file-input').setInputFiles(uploadImage());
  await page.locator('.task-file-progress').waitFor();
  await page.locator('#close-conversation').click();
  await page.getByText('Создание отменено (сервер)', { exact: true }).waitFor();
  assert.equal(await page.locator('#task-selected-files').isHidden(), true);
  assert.equal(await page.locator('.task-file-progress').count(), 0);
  assert.equal(await page.evaluate(() => window.liveHistoryUrls.size), 0);
  assert.equal(state.taskCalls.filter(call => call.path.endsWith('/select')).length, 1);
  assert.equal(state.taskCalls.filter(call => call.path.endsWith('/description')).length, 0);
  assert.equal(await page.locator('#demo-send').innerText(), 'Отправить ↑');
});

test('task preloads: mobile uncertainty keeps file text readable and actions within the row', async t => {
  const { page, state } = await taskWithFiles(t, { width: 390 });
  state.preloadStatus = 'failed';
  state.preloadUncertain = true;
  await page.locator('#task-file-input').setInputFiles(uploadImage());
  await page.getByRole('button', { name: 'Подтвердить повтор загрузки', exact: true }).waitFor();
  const layout = await page.locator('#task-selected-files').evaluate(list => ({
    text: list.querySelector('.task-file-details').getBoundingClientRect().width,
    scroll: list.scrollWidth, width: list.clientWidth,
    overflow: document.documentElement.scrollWidth > innerWidth,
  }));
  assert.ok(layout.text >= 100, JSON.stringify(layout));
  assert.ok(layout.scroll <= layout.width + 1, JSON.stringify(layout));
  assert.equal(layout.overflow, false);
});

test('task preloads: one failed file blocks ready siblings until only that file is removed', async t => {
  const { page, state } = await taskWithFiles(t);
  state.preloadStatus = 'failed';
  await page.locator('#demo-text').fill('Only ready files');
  await page.locator('#task-file-input').setInputFiles(uploadImage());
  await page.locator('.task-file-failure').waitFor();
  state.preloadStatus = 'ready';
  await page.locator('#task-file-input').setInputFiles(uploadNote());
  await page.waitForFunction(() => document.querySelectorAll('#task-selected-files [data-status="ready"]').length === 1);
  assert.equal(await page.locator('#demo-send').isDisabled(), true);
  await page.getByRole('button', { name: 'Удалить файл: ordinary.webp', exact: true }).click();
  await page.locator('#demo-send').click();
  await page.getByText('Задача создана (сервер)', { exact: true }).waitFor();
  const description = state.taskCalls.find(call => call.path.endsWith('/description'));
  assert.equal(description.body.preload_ids.length, 1);
  assert.equal(await page.locator('#chat-messages .task-sent-file').count(), 1);
  assert.match(await page.locator('#chat-messages .task-sent-file').innerText(), /notes.txt/);
});


test('task preloads: retry conflict never auto-confirms an unknown result', async t => {
  const { page, state } = await taskWithFiles(t);
  state.preloadStatus = 'failed';
  await page.locator('#demo-text').fill('Description');
  await page.locator('#task-file-input').setInputFiles(uploadNote());
  await page.locator('.task-file-failure').waitFor();
  state.preloadUncertain = true;
  await page.locator('#task-selected-files').getByRole('button', { name: 'Повторить', exact: true }).click();
  await page.getByRole('button', { name: 'Подтвердить повтор загрузки', exact: true }).waitFor();
  assert.equal(await page.locator('#demo-send').isDisabled(), true);
  assert.deepEqual(state.preloadCalls.filter(call => call.path.endsWith('/retry')).map(call => call.body), [{ confirm_uncertain: false }]);
  assert.doesNotMatch(await page.locator('#task-selected-files').innerText(), /Telegram|INTERNAL/i);
  await page.getByRole('button', { name: 'Подтвердить повтор загрузки', exact: true }).click();
  await filesReady(page);
  assert.deepEqual(state.preloadCalls.filter(call => call.path.endsWith('/retry')).map(call => call.body), [{ confirm_uncertain: false }, { confirm_uncertain: true }]);
});


test('task preloads: failed DELETE can be retried without reuploading or restoring selection', async t => {
  const { page, state } = await taskWithFiles(t);
  await page.locator('#task-file-input').setInputFiles(uploadNote());
  await filesReady(page);
  state.preloadHttpStatus = 500;
  await page.getByRole('button', { name: 'Удалить файл: notes.txt', exact: true }).click();
  await page.getByRole('button', { name: 'Повторить удаление', exact: true }).waitFor();
  assert.doesNotMatch(await page.locator('#task-file-error').innerText(), /Telegram|INTERNAL/i);
  state.preloadHttpStatus = 200;
  await page.getByRole('button', { name: 'Повторить удаление', exact: true }).click();
  assert.equal(await page.locator('#task-selected-files').isHidden(), true);
  assert.equal(state.preloadCalls.filter(call => call.multipart).length, 1);
  assert.equal(state.preloadCalls.filter(call => call.method === 'DELETE').length, 2);
});
