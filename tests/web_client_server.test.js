const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const { once } = require('node:events');
const { spawn } = require('node:child_process');

const auth = '/api/v1/web/auth/';
let createDevServer;
test.before(async () => { ({ createDevServer } = await import('../web-client/dev-server.mjs')); });

async function listen(t, server) {
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(async () => {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  });
  return server.address().port;
}

function request(port, path = '/', headers = {}, method = 'GET', body) {
  return new Promise((resolve, reject) => {
    const req = http.request({ hostname: '127.0.0.1', port, path, headers, method }, res => {
      let body = '';
      res.on('data', data => { body += data; });
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body }));
    });
    req.on('error', reject);
    req.end(body);
  });
}

test('upstream stays loopback-only unless Docker mode is explicitly true', () => {
  for (const dockerMode of [false, undefined, 'true']) {
    assert.throws(() => createDevServer({ apiOrigin: 'http://api:8000', dockerMode }));
  }
  createDevServer({ apiOrigin: 'http://127.0.0.1:8001' }).close();
  createDevServer({ apiOrigin: 'http://api:8000', dockerMode: true }).close();
  for (const apiOrigin of ['http://api:8001', 'https://api:8000', 'http://10.171.1.218:8000',
    'http://evil.example:8000', 'http://api:8000/path', 'http://user:pass@api:8000',
    'http://api:8000/?x=1', 'http://api:8000/#fragment']) {
    assert.throws(() => createDevServer({ apiOrigin, dockerMode: true }), apiOrigin);
  }
});

test('browser origin must be explicit and valid; local mode stays on loopback', () => {
  assert.throws(() => createDevServer({ browserOrigin: 'http://10.171.1.218:5180' }));
  for (const browserOrigin of ['null', '*', 'http://0.0.0.0:5173', 'http://[::]:5173',
    'http://user:pass@127.0.0.1:5173', 'http://127.0.0.1:5173/path',
    'http://127.0.0.1:5173?query', 'http://127.0.0.1:5173#hash']) {
    assert.throws(() => createDevServer({ browserOrigin, dockerMode: true }), browserOrigin);
  }
});

for (const browserOrigin of ['http://127.0.0.1:5173', 'http://10.171.1.218:5180']) {
  test(`Docker Host/Origin use browser address, independently of listener: ${browserOrigin}`, async t => {
    // The future address is only an HTTP header; no request goes to the server IP.
    const port = await listen(t, createDevServer({ dockerMode: true, browserOrigin }));
    const host = new URL(browserOrigin).host;
    assert.equal((await request(port, '/', { Host: host })).status, 200);
    for (const badHost of [`127.0.0.1:${port}`, 'localhost:5173', 'api:8000', 'evil.example']) {
      assert.equal((await request(port, '/', { Host: badHost })).status, 400);
    }
    for (const origin of ['http://evil.example', 'null', 'http://api:8000', browserOrigin + '/']) {
      assert.equal((await request(port, auth + 'login', { Host: host, Origin: origin }, 'POST')).status, 403);
    }
    assert.equal((await request(port, auth + 'login', { Host: host }, 'POST')).status, 403);
    assert.equal((await request(port, '/', { Host: host, Origin: 'http://evil.example' })).status, 403);
  });
}

test('Docker proxy keeps allowlist, cookie isolation, Origin and security headers', async t => {
  const seen = [];
  const upstreamPort = await listen(t, http.createServer((req, res) => {
    seen.push({ path: req.url, headers: req.headers });
    res.writeHead(200, { 'Content-Type': 'application/json', 'Retry-After': '30',
      'Set-Cookie': ['esdesk_session=staff; HttpOnly', 'esdesk_web_session=web; HttpOnly; SameSite=Strict; Path=/api/v1/web'],
      'X-Internal-Secret': 'hidden' });
    res.end('{}');
  }));
  const browserOrigin = 'http://127.0.0.1:5173';
  const port = await listen(t, createDevServer({ dockerMode: true, apiOrigin: 'http://api:8000', browserOrigin }));
  const originalRequest = http.request;
  t.mock.method(http, 'request', function (url, options, callback) {
    if (url instanceof URL && url.hostname === 'api') {
      assert.equal(url.origin, 'http://api:8000');
      const local = new URL(url);
      local.host = `127.0.0.1:${upstreamPort}`;
      return originalRequest.call(this, local, options, callback);
    }
    return originalRequest.apply(this, arguments);
  });
  const headers = { Host: '127.0.0.1:5173', Origin: browserOrigin, 'Sec-Fetch-Site': 'same-origin',
    Cookie: 'esdesk_session=staff; esdesk_web_session=web; unrelated=value',
    'X-Forwarded-For': '203.0.113.1', Authorization: 'Bearer staff' };
  const result = await request(port, auth + 'login', headers, 'POST');
  assert.equal(result.status, 200);
  assert.equal(result.headers['retry-after'], '30');
  assert.deepEqual(result.headers['set-cookie'], ['esdesk_web_session=web; HttpOnly; SameSite=Strict; Path=/api/v1/web']);
  assert.equal(result.headers['x-internal-secret'], undefined);
  assert.equal(result.headers['cache-control'], 'no-store');
  assert.equal(result.headers['x-content-type-options'], 'nosniff');
  assert.equal(result.headers['referrer-policy'], 'no-referrer');
  assert.ok(result.headers['content-security-policy'].includes("connect-src 'self'"));
  assert.ok(result.headers['content-security-policy'].includes("frame-ancestors 'none'"));
  assert.equal(seen[0].headers.host, headers.Host);
  assert.equal(seen[0].headers.origin, browserOrigin);
  assert.equal(seen[0].headers['sec-fetch-site'], 'same-origin');
  assert.equal(seen[0].headers.cookie, 'esdesk_web_session=web');
  assert.equal(seen[0].headers['x-forwarded-for'], undefined);
  assert.equal(seen[0].headers.authorization, undefined);
  for (const route of ['/api/v1/management/users', '/api/v1/auth/me', '/login', '/.env',
    '/dev-server.mjs', '/Dockerfile', '/README.md', auth + 'me/extra']) {
    assert.equal((await request(port, route, headers)).status, 404, route);
  }
  assert.equal((await request(port, auth + 'login', headers)).status, 405);
  assert.equal(seen.length, 1);
  for (const [route, method] of [['me', 'GET'], ['logout', 'POST']]) {
    assert.equal((await request(port, auth + route, headers, method)).status, 200);
  }
  assert.equal(seen.length, 3);
});

test('menu, chat and active intake proxy allow only exact GET with isolated WEB cookies', async t => {
  const seen = [];
  const upstream = http.createServer((req, res) => {
    seen.push({ method: req.method, url: req.url, cookie: req.headers.cookie, authorization: req.headers.authorization });
    res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify({ menu_type: 'Базовое', rows: [] }));
  });
  const upstreamPort = await listen(t, upstream);
  const port = await listen(t, createDevServer({ apiOrigin: `http://127.0.0.1:${upstreamPort}` }));
  const headers = { Cookie: 'esdesk_session=staff; esdesk_web_session=web', Authorization: 'Bearer staff', Origin: `http://127.0.0.1:${port}` };
  const paths = ['/api/v1/web/menu', '/api/v1/web/chat', '/api/v1/web/tasks/active'];
  for (const path of paths) {
    const result = await request(port, path, headers);
    assert.equal(result.status, 200);
    assert.deepEqual(JSON.parse(result.body), { menu_type: 'Базовое', rows: [] });
    assert.equal(result.headers['cache-control'], 'no-store');
    assert.equal((await request(port, path, headers, 'POST')).status, 405);
    assert.equal((await request(port, path, { ...headers, Origin: 'http://foreign.example' })).status, 403);
    assert.equal((await request(port, path + '/extra', headers)).status, 404);
  }
  assert.equal((await request(port, '/api/v1/settings/bot-buttons', headers)).status, 404);
  assert.deepEqual(seen, paths.map(url => ({ method: 'GET', url, cookie: 'esdesk_web_session=web', authorization: undefined })));
});

test('task proxy forwards exact task actions, WEB cookie, JSON and Origin', async t => {
  const seen = [];
  const upstream = http.createServer((req, res) => {
    let body = '';
    req.on('data', data => { body += data; });
    req.on('end', () => {
      seen.push({ method: req.method, path: req.url, cookie: req.headers.cookie, origin: req.headers.origin,
        authorization: req.headers.authorization, body });
      res.setHeader('Content-Type', 'application/json');
      res.end('{"state":"collecting"}');
    });
  });
  const upstreamPort = await listen(t, upstream);
  const port = await listen(t, createDevServer({ apiOrigin: `http://127.0.0.1:${upstreamPort}` }));
  const origin = `http://127.0.0.1:${port}`;
  const headers = { Origin: origin, Cookie: 'esdesk_session=staff; esdesk_web_session=web', 'Content-Type': 'application/json', Authorization: 'Bearer staff' };
  const uuid = '11111111-1111-4111-8111-111111111111';
  for (const path of ['/api/v1/web/tasks/select', `/api/v1/web/tasks/${uuid}/description`, `/api/v1/web/tasks/${uuid}/cancel`, `/api/v1/web/tasks/${uuid}/confirm`]) {
    assert.equal((await request(port, path, headers, 'POST', '{"text":"test"}')).status, 200);
    assert.equal((await request(port, path, headers)).status, 405);
    assert.equal((await request(port, path, { ...headers, Origin: 'http://foreign.example' }, 'POST')).status, 403);
    assert.equal((await request(port, path, {}, 'POST')).status, 403);
  }
  for (const path of [`/api/v1/web/tasks/${uuid}/close`, '/api/v1/web/tasks/not-a-uuid/cancel', '/api/v1/web/tasks', '/api/v1/web/tasks/select/extra', '/api/v1/settings/bot-buttons']) {
    assert.equal((await request(port, path, headers, 'POST')).status, 404);
  }
  assert.equal(seen.length, 4);
  assert.ok(seen.every(item => item.method === 'POST' && item.origin === origin && item.cookie === 'esdesk_web_session=web'
    && item.authorization === undefined && item.body === '{"text":"test"}'));
});

test('CLI selects loopback by default and wildcard only in explicit Docker mode', async () => {
  // Capture the real CLI listen call without opening a port or contacting any API.
  const script = `import http from 'node:http';
    import { resolve } from 'node:path';
    http.Server.prototype.listen = function(port, host) { console.log(JSON.stringify({port, host})); return this; };
    process.argv[1] = resolve('web-client/dev-server.mjs');
    await import('./web-client/dev-server.mjs');`;
  for (const [mode, host] of [['false', '127.0.0.1'], ['true', '0.0.0.0'], ['yes', null]]) {
    const child = spawn(process.execPath, ['--input-type=module', '-e', script], {
      env: { ...process.env, WEB_DOCKER_MODE: mode, WEB_CLIENT_PORT: '5173',
        WEB_CLIENT_ORIGIN: 'http://127.0.0.1:5173', WEB_API_ORIGIN: mode === 'false' ? 'http://127.0.0.1:8000' : 'http://api:8000' },
    });
    let output = '';
    child.stdout.on('data', data => { output += data; });
    const [code] = await once(child, 'exit');
    assert.equal(code, host ? 0 : 1);
    if (host) assert.deepEqual(JSON.parse(output), { port: 5173, host });
    else assert.equal(output, '');
  }
});

test('result proxy preserves pagination and isolates WEB authorization for all result routes', async t => {
  const seen = [];
  const upstream = http.createServer((req, res) => {
    seen.push({ path: req.url, method: req.method, cookie: req.headers.cookie, authorization: req.headers.authorization });
    res.setHeader('Content-Type', req.url.includes('/files/') ? 'application/octet-stream' : 'application/json');
    res.end(req.url.includes('/files/') ? 'binary result' : '{}');
  });
  const upstreamPort = await listen(t, upstream);
  const port = await listen(t, createDevServer({ apiOrigin: `http://127.0.0.1:${upstreamPort}` }));
  const headers = { Origin: `http://127.0.0.1:${port}`, Cookie: 'esdesk_session=staff; esdesk_web_session=web', Authorization: 'Bearer staff' };
  const id = '11111111-1111-4111-8111-111111111111';
  const base = '/api/v1/web/task-results';
  for (const [path, method] of [[base + '?limit=50&offset=50', 'GET'], [`${base}/${id}`, 'GET'],
    [`${base}/${id}/read`, 'POST'], [`${base}/${id}/files/${id}`, 'GET'],
    [`/api/v1/web/history/${id}/messages`, 'GET'], [`/api/v1/web/history/${id}/attachments/${id}/download`, 'GET'],
    ['/static/ticket_attachments.js', 'GET'],
    ['/api/v1/web/history?page=2', 'GET']]) {
    const response = await request(port, path, headers, method);
    assert.equal(response.status, 200);
    assert.equal(response.headers['cache-control'], 'no-store');
    if (path.includes('/files/')) {
      assert.equal(response.body, 'binary result');
      assert.equal(response.headers['content-type'], 'application/octet-stream');
    }
    assert.equal((await request(port, path, headers, method === 'GET' ? 'POST' : 'GET')).status, 405);
    assert.equal((await request(port, path, { ...headers, Origin: 'http://foreign.example' }, method)).status, 403);
  }
  assert.equal((await request(port, `${base}/${id}/read`, {}, 'POST')).status, 403);
  for (const path of [base + '/bad-id', `${base}/${id}/files/no-id`, `${base}/${id}/messages`, `${base}/${id}/close`,
    `/api/v1/web/history/${id}/close`, `/api/v1/web/history/${id}/attachments/bad/download`,
    `/api/v1/web/history/${id}/messages/extra`, '/static/ticket_api.js']) {
    assert.equal((await request(port, path, headers)).status, 404);
  }
  assert.equal(seen.length, 8);
  assert.equal(seen.at(-1).path, '/api/v1/web/history?page=2');
  assert.equal(seen[0].path, base + '?limit=50&offset=50');
  assert.ok(seen.every(item => item.cookie === 'esdesk_web_session=web' && item.authorization === undefined));
});

test('source icons: static assets preserve original WEB bytes and correct media types', async t => {
  const { readFile } = require('node:fs/promises');
  const { createHash } = require('node:crypto');
  const port = await listen(t, createDevServer());
  for (const [name, mime] of [['web-source.webp', 'image/webp'], ['telegram-source.svg', 'image/svg+xml']]) {
    const bytes = await readFile(`web-client/assets/${name}`);
    const response = await fetch(`http://127.0.0.1:${port}/assets/${name}`);
    assert.equal(response.status, 200);
    assert.equal(response.headers.get('content-type'), mime);
    assert.equal(response.headers.get('x-content-type-options'), 'nosniff');
    assert.deepEqual(Buffer.from(await response.arrayBuffer()), bytes);
    if (name === 'web-source.webp') assert.equal(createHash('sha256').update(bytes).digest('hex'),
      '997a2edcfa40a1fb1c3eb273fe19daeafec3a5417de09cc8d272fffdb3845014');
  }
});

test('source icons: client Telegram matches the staff contour and WEB copies match', async () => {
  const { readFile } = require('node:fs/promises');
  const staff = await readFile('api/app/static/list.js', 'utf8');
  const telegram = await readFile('web-client/assets/telegram-source.svg', 'utf8');
  const original = staff.match(/if \(channel === "telegram"\) \{\s*return '([^']+)';/)[1];
  assert.equal(telegram.match(/<path d="([^"]+)"/)[1], original.match(/<path d="([^"]+)"/)[1]);
  assert.match(telegram, /fill="#229ed9"/);
  assert.doesNotMatch(telegram, /<circle/);
  assert.deepEqual(await readFile('api/app/static/icons/web-source.webp'), await readFile('web-client/assets/web-source.webp'));
});

test('WEB logo serves the exact original ESHelpdeskLogo font', async t => {
  const { readFile } = require('node:fs/promises');
  const original = await readFile('api/app/static/fonts/17575.ttf');
  assert.deepEqual(await readFile('web-client/assets/17575.ttf'), original);
  const port = await listen(t, createDevServer());
  const response = await fetch(`http://127.0.0.1:${port}/assets/17575.ttf`);
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('content-type'), 'font/ttf');
  assert.deepEqual(Buffer.from(await response.arrayBuffer()), original);
});

test('initial task multipart upload forwards original bytes and only the WEB cookie', async t => {
  let received;
  const upstreamPort = await listen(t, http.createServer(async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    received = { body: Buffer.concat(chunks).toString(), headers: req.headers, path: req.url };
    res.writeHead(200, { 'Content-Type': 'application/json' }).end('{}');
  }));
  const port = await listen(t, createDevServer({ apiOrigin: `http://127.0.0.1:${upstreamPort}` }));
  const body = '--test-boundary\r\nContent-Disposition: form-data; name="text"\r\n\r\nDescription\r\n'
    + '--test-boundary\r\nContent-Disposition: form-data; name="files"; filename="note.txt"\r\nContent-Type: text/plain\r\n\r\nFile bytes\r\n--test-boundary--\r\n';
  const path = '/api/v1/web/tasks/11111111-1111-4111-8111-111111111111/description';
  const response = await request(port, path, { Origin: `http://127.0.0.1:${port}`,
    'Content-Type': 'multipart/form-data; boundary=test-boundary',
    Cookie: 'esdesk_session=staff; esdesk_web_session=client', Authorization: 'Bearer staff' }, 'POST', body);
  assert.equal(response.status, 200);
  assert.equal(received.body, body);
  assert.equal(received.path, path);
  assert.equal(received.headers['content-type'], 'multipart/form-data; boundary=test-boundary');
  assert.equal(received.headers.cookie, 'esdesk_web_session=client');
  assert.equal(received.headers.authorization, undefined);
});

test('WEB resource versions match the direct page, updated workspace and unchanged dependencies', async t => {
  const { readFile } = require('node:fs/promises');
  const version = '20261105-web-file-size-notice-v1';
  const workspaceVersion = '20261105-web-file-size-notice-v1';
  const index = await readFile('web-client/index.html', 'utf8');
  assert.ok(index.includes('/app.js?v=' + workspaceVersion) && index.includes('/workspace.css?v=' + version));
  const expected = { 'app.js': ['api.js', 'workspace.js'], 'workspace.js': ['api.js', 'history-media.js', 'task-files.js', 'composer-draft.js'], 'composer-draft.js': ['api.js'], 'history-media.js': ['api.js'], 'task-files.js': ['api.js', 'file-size-notices.js'], 'file-size-notices.js': ['api.js'] };
  for (const [file, imports] of Object.entries(expected)) {
    const source = await readFile('web-client/' + file, 'utf8');
    for (const dependency of imports) {
      const expectedVersion = workspaceVersion;
      assert.ok(source.includes(`./${dependency}?v=${expectedVersion}`), file + ': ' + dependency);
    }
  }
  const port = await listen(t, createDevServer());
  for (const file of ['app.js', 'workspace.js', 'composer-draft.js']) {
    const response = await request(port, '/' + file + '?v=' + workspaceVersion);
    assert.equal(response.status, 200);
    assert.equal(response.body, await readFile('web-client/' + file, 'utf8'));
  }
  assert.equal((await request(port, '/task-files.js?v=' + workspaceVersion)).status, 200);
  assert.ok((await readFile('web-client/Dockerfile', 'utf8')).includes('task-files.js'));
  assert.ok((await readFile('web-client/Dockerfile', 'utf8')).includes('composer-draft.js'));
  assert.ok((await readFile('web-client/.dockerignore', 'utf8')).includes('!composer-draft.js'));
  assert.ok((await readFile('web-client/.dockerignore', 'utf8')).includes('!task-files.js'));
});

test('preload proxy forwards only exact WEB routes, methods, original upload and isolated cookie', async t => {
  const received = [];
  const upstreamPort = await listen(t, http.createServer(async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    received.push({ path: req.url, method: req.method, headers: req.headers, body: Buffer.concat(chunks).toString() });
    res.writeHead(200, { 'Content-Type': 'application/json' }).end('{}');
  }));
  const port = await listen(t, createDevServer({ apiOrigin: `http://127.0.0.1:${upstreamPort}` }));
  const root = '/api/v1/web/tasks/11111111-1111-4111-8111-111111111111/attachment-preloads';
  const item = root + '/22222222-2222-4222-8222-222222222222';
  const headers = { Origin: `http://127.0.0.1:${port}`, Cookie: 'esdesk_session=staff; esdesk_web_session=client', Authorization: 'Bearer staff' };
  const body = '--boundary\r\nContent-Disposition: form-data; name="upload_id"\r\n\r\nstable\r\n'
    + '--boundary\r\nContent-Disposition: form-data; name="file"; filename="note.txt"\r\n\r\nOriginal bytes\r\n--boundary--\r\n';
  assert.equal((await request(port, root, { ...headers, 'Content-Type': 'multipart/form-data; boundary=boundary' }, 'POST', body)).status, 200);
  assert.equal((await request(port, item, headers)).status, 200);
  assert.equal((await request(port, item + '/retry', { ...headers, 'Content-Type': 'application/json' }, 'POST', '{"confirm_uncertain":false}')).status, 200);
  assert.equal((await request(port, item, headers, 'DELETE')).status, 200);
  assert.equal(received[0].body, body);
  assert.deepEqual(received.map(call => call.method), ['POST', 'GET', 'POST', 'DELETE']);
  assert.ok(received.every(call => call.headers.cookie === 'esdesk_web_session=client' && !call.headers.authorization));
  assert.equal((await request(port, root, headers)).status, 405);
  assert.equal((await request(port, item, headers, 'POST')).status, 405);
  assert.equal((await request(port, item, headers, 'PUT')).status, 405);
  assert.equal((await request(port, item, {}, 'DELETE')).status, 403);
  assert.equal((await request(port, item, { Origin: 'https://foreign.invalid' }, 'DELETE')).status, 403);
  for (const path of [root + '/bad', item + '/extra', item + '/download', '/api/v1/tickets/11111111-1111-4111-8111-111111111111/attachment-preloads']) {
    assert.equal((await request(port, path, headers)).status, 404);
  }
  assert.equal(received.length, 4);
});

test('composer proxy: only GET/PUT, exact JSON, required Origin and isolated WEB cookie', async t => {
  const seen = [];
  const upstream = await listen(t, http.createServer(async (req, res) => {
    let body = ''; for await (const chunk of req) body += chunk;
    seen.push({ method: req.method, path: req.url, headers: req.headers, body });
    res.writeHead(req.method === 'PUT' ? 409 : 200, { 'Content-Type': 'application/json' }).end('{}');
  }));
  const port = await listen(t, createDevServer({ apiOrigin: `http://127.0.0.1:${upstream}` }));
  const path = '/api/v1/web/composer-draft';
  const headers = { Origin: `http://127.0.0.1:${port}`, Cookie: 'esdesk_session=staff; esdesk_web_session=client', Authorization: 'Bearer staff', 'Content-Type': 'application/json' };
  const body = JSON.stringify({ text: '  Exact text\n ', version: 7 });
  assert.equal((await request(port, path, headers)).status, 200);
  assert.equal((await request(port, path, headers, 'PUT', body)).status, 409);
  assert.equal((await request(port, path, {}, 'PUT', body)).status, 403);
  for (const method of ['POST', 'DELETE', 'HEAD']) assert.equal((await request(port, path, headers, method)).status, 405);
  assert.equal((await request(port, path + '/other', headers)).status, 404);
  assert.equal(seen.length, 2);
  assert.equal(seen[1].body, body);
  assert.ok(seen.every(call => call.path === path && call.headers.cookie === 'esdesk_web_session=client' && !call.headers.authorization));
});


test('universal preload proxy preserves exact methods, upload bytes, content and WEB-only cookies', async t => {
  const seen = [];
  const upstream = await listen(t, http.createServer(async (req, res) => {
    let body = ''; for await (const chunk of req) body += chunk;
    seen.push({ path: req.url, method: req.method, headers: req.headers, body });
    res.writeHead(200, { 'Content-Type': 'application/octet-stream' }).end('content');
  }));
  const port = await listen(t, createDevServer({ apiOrigin: `http://127.0.0.1:${upstream}` }));
  const root = '/api/v1/web/composer-draft/attachment-preloads';
  const item = root+'/11111111-1111-4111-8111-111111111111';
  const headers = { Origin: `http://127.0.0.1:${port}`, Cookie: 'esdesk_session=staff; esdesk_web_session=client', Authorization: 'Bearer staff' };
  for (const [url, method] of [[root,'GET'],[root,'POST'],[item,'GET'],[item,'DELETE'],[item+'/retry','POST'],[item+'/content','GET']]) {
    assert.equal((await request(port, url, headers, method, method === 'POST' ? 'original bytes' : undefined)).status, 200);
  }
  for (const [url, method] of [[root,'PUT'],[item,'POST'],[item+'/retry','GET'],[item+'/content','DELETE']]) {
    assert.equal((await request(port, url, headers, method)).status, 405);
  }
  for (const url of [root+'/bad',item+'/extra',item+'/content/extra']) assert.equal((await request(port, url, headers)).status, 404);
  assert.ok(seen.every(call => call.headers.cookie === 'esdesk_web_session=client' && !call.headers.authorization));
  assert.equal(seen[1].body, 'original bytes');
  const icon = await request(port, '/assets/paperclip.svg');
  assert.equal(icon.status, 200);
  const { readFile } = require('node:fs/promises');
  assert.equal(icon.body, await readFile('web-client/assets/paperclip.svg', 'utf8'));
  assert.match(icon.headers['content-type'], /image\/svg\+xml/);
  assert.match(await readFile('web-client/Dockerfile', 'utf8'), /COPY[^\n]*assets/);
  assert.match(await readFile('web-client/.dockerignore', 'utf8'), /!assets\/\*\*/);
});


test('oversized-file notification proxy: exact POST, metadata and isolated WEB cookie', async t=>{
  const seen=[];
  const upstream=await listen(t,http.createServer(async(req,res)=>{
    let body='';for await(const chunk of req) body+=chunk;
    seen.push({method:req.method,path:req.url,headers:req.headers,body});
    res.writeHead(200,{'Content-Type':'application/json'}).end('{"ok":true,"message_id":"1"}');
  }));
  const port=await listen(t,createDevServer({apiOrigin:`http://127.0.0.1:${upstream}`}));
  const path='/api/v1/web/chat/attachment-too-large';
  const headers={Origin:`http://127.0.0.1:${port}`,Cookie:'esdesk_session=staff; esdesk_web_session=client','Content-Type':'application/json'};
  const body=JSON.stringify({file_name:'video.mp4',file_size:20971521,event_id:'11111111-1111-4111-8111-111111111111'});
  assert.equal((await request(port,path,headers,'POST',body)).status,200);
  for(const method of ['GET','PUT','DELETE']) assert.equal((await request(port,path,headers,method)).status,405);
  assert.equal((await request(port,path,{},'POST',body)).status,403);
  assert.equal((await request(port,path+'/extra',headers,'POST',body)).status,404);
  assert.equal(seen.length,1);assert.equal(seen[0].body,body);
  assert.equal(seen[0].headers.cookie,'esdesk_web_session=client');
  assert.equal((await request(port,'/file-size-notices.js?v=20261105-web-file-size-notice-v1')).status,200);
  const {readFile}=require('node:fs/promises');
  assert.match(await readFile('web-client/Dockerfile','utf8'),/file-size-notices\.js/);
  assert.match(await readFile('web-client/.dockerignore','utf8'),/!file-size-notices\.js/);
});
