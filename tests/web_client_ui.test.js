const test = require('node:test');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

let browser, server, origin, fixture, fixtureOrigin;
const password = 'Synthetic-Web-A1!';
const authPath = '/api/v1/web/auth/';
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
  const state = { me: 401, login: 200, logout: 204, retry: null, network: false, hold: null, loginCount: 0, logoutCount: 0 };
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

test('successful login clears fields and shows the temporary screen', async t => {
  const { page } = await pageFor(t);
  await submit(page);
  await page.getByRole('heading', { name: 'Вход выполнен' }).waitFor();
  assert.equal(await page.getByText('Интерфейс обращений находится в разработке').isVisible(), true);
  assert.equal(await page.locator('#password').inputValue(), '');
  assert.equal(await page.locator('#login').inputValue(), '');
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
  await page.getByRole('heading', { name: 'Вход выполнен' }).waitFor();
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
  await page.getByRole('heading', { name: 'Вход выполнен' }).waitFor();
  state.logout = 401;
  await page.getByRole('button', { name: 'Выйти', exact: true }).click();
  await waitLogin(page);
  state.me = 401;
  await page.reload();
  await waitLogin(page);
});

test('failed logout preserves the signed-in view and allows retry', async t => {
  const { page, state } = await pageFor(t);
  await submit(page);
  await page.getByRole('heading', { name: 'Вход выполнен' }).waitFor();
  state.logout = 503;
  await page.getByRole('button', { name: 'Выйти', exact: true }).click();
  await page.locator('#logout-error').waitFor({ state: 'visible' });
  assert.equal(await page.locator('#session-panel').isVisible(), true);
  state.logout = 204;
  await page.getByRole('button', { name: 'Выйти', exact: true }).click();
  await waitLogin(page);
});

test('real API through proxy: cookie, Origin, reload, idle, expiry, logout and empty storage', async t => {
  await control('reset');
  const { page, context } = await pageFor(t, { mocked: false });
  await waitLogin(page);
  await context.addCookies([{ name: 'esdesk_session', value: 'synthetic-staff-cookie', url: origin, httpOnly: true }]);
  await submit(page);
  await page.getByRole('heading', { name: 'Вход выполнен' }).waitFor();
  const cookie = (await context.cookies()).find(cookie => cookie.name === 'esdesk_web_session');
  assert.ok(cookie && cookie.httpOnly && cookie.sameSite === 'Strict' && cookie.path === '/api/v1/web');
  assert.equal(await page.evaluate(() => document.cookie.includes('esdesk_web_session')), false);
  const before = await control('state');
  await page.reload();
  await page.getByRole('heading', { name: 'Вход выполнен' }).waitFor();
  const after = await control('state');
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
  await page.getByRole('heading', { name: 'Вход выполнен' }).waitFor();
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
