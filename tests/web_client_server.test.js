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

function request(port, path = '/', headers = {}, method = 'GET') {
  return new Promise((resolve, reject) => {
    const req = http.request({ hostname: '127.0.0.1', port, path, headers, method }, res => {
      let body = '';
      res.on('data', data => { body += data; });
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body }));
    });
    req.on('error', reject);
    req.end();
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
