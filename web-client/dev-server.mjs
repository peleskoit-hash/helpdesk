// Static server + transparent WEB API proxy; no authentication logic.
import http from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';

const assets = new Map([
  ['/', ['index.html', 'text/html; charset=utf-8']],
  ['/styles.css', ['styles.css', 'text/css; charset=utf-8']],
  ['/app.js', ['app.js', 'text/javascript; charset=utf-8']],
  ['/workspace.js', ['workspace.js', 'text/javascript; charset=utf-8']],
  ['/history-media.js', ['history-media.js', 'text/javascript; charset=utf-8']],
  ['/task-files.js', ['task-files.js', 'text/javascript; charset=utf-8']],
  ['/appearance.js', ['appearance.js', 'text/javascript; charset=utf-8']],
  ['/workspace.css', ['workspace.css', 'text/css; charset=utf-8']],
  ['/Manrope-Variable.ttf', ['Manrope-Variable.ttf', 'font/ttf']],
  ['/api.js', ['api.js', 'text/javascript; charset=utf-8']],
  ['/assets/web-source.webp', ['assets/web-source.webp', 'image/webp']],
  ['/assets/17575.ttf', ['assets/17575.ttf', 'font/ttf']],
  ['/assets/telegram-source.svg', ['assets/telegram-source.svg', 'image/svg+xml']],
]);
const routes = new Map([
  ['/static/ticket_attachments.js', 'GET'],
  ['/api/v1/web/task-results', 'GET'],
  ['/api/v1/web/history', 'GET'],
  ['/api/v1/web/menu', 'GET'],
  ['/api/v1/web/chat', 'GET'],
  ['/api/v1/web/tasks/active', 'GET'],
  ['/api/v1/web/tasks/select', 'POST'],
  ['/api/v1/web/auth/login', 'POST'],
  ['/api/v1/web/auth/logout', 'POST'],
  ['/api/v1/web/auth/me', 'GET'],
]);

export function createDevServer({ apiOrigin = 'http://127.0.0.1:8000', dockerMode = false, browserOrigin } = {}) {
  const upstream = new URL(apiOrigin);
  const dockerUpstream = dockerMode === true && upstream.origin === 'http://api:8000';
  if (upstream.protocol !== 'http:' || (!dockerUpstream && upstream.hostname !== '127.0.0.1')
      || upstream.pathname !== '/' || upstream.username || upstream.password || upstream.search || upstream.hash) {
    throw new Error('WEB_API_ORIGIN must be HTTP on 127.0.0.1, or http://api:8000 in Docker mode');
  }
  let publicUrl;
  if (browserOrigin !== undefined) {
    publicUrl = new URL(browserOrigin);
    if (!['http:', 'https:'].includes(publicUrl.protocol) || publicUrl.origin !== browserOrigin
        || publicUrl.hostname === '0.0.0.0' || publicUrl.hostname === '[::]'
        || (dockerMode !== true && (publicUrl.protocol !== 'http:' || publicUrl.hostname !== '127.0.0.1'))) {
      throw new Error('WEB_CLIENT_ORIGIN must be a browser origin; non-Docker mode requires HTTP on 127.0.0.1');
    }
  }
  return http.createServer(async (req, res) => {
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('Referrer-Policy', 'no-referrer');
    res.setHeader('Content-Security-Policy', "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net/npm/pako@2.1.0/dist/pako.min.js https://cdn.jsdelivr.net/npm/lottie-web@5.12.2/build/player/lottie.min.js; style-src 'self'; connect-src 'self'; img-src 'self' blob:; media-src blob:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'");
    const expectedOrigin = publicUrl?.origin || `http://127.0.0.1:${res.socket.localPort}`;
    const expectedHost = publicUrl?.host || `127.0.0.1:${res.socket.localPort}`;
    if (req.headers.host !== expectedHost) {
      res.writeHead(400).end();
      return;
    }
    if ((req.headers.origin !== undefined && req.headers.origin !== expectedOrigin)
        || (['POST', 'DELETE'].includes(req.method) && req.headers.origin === undefined)) {
      res.writeHead(403).end();
      return;
    }
    let path;
    try { path = new URL(req.url, 'http://127.0.0.1').pathname; }
    catch { res.writeHead(400).end(); return; }
    const taskAction = /^\/api\/v1\/web\/tasks\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/(description|cancel|confirm)$/i.test(path);
    const uuid = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}';
    const resultRoute = new RegExp(`^/api/v1/web/task-results/${uuid}(/read|/files/${uuid})?$`, 'i').test(path);
    const historyRoute = new RegExp(`^/api/v1/web/history/${uuid}/(messages|attachments/${uuid}/download)$`, 'i').test(path);
    const preloadCreate = new RegExp(`^/api/v1/web/tasks/${uuid}/attachment-preloads$`, 'i').test(path);
    const preloadFile = new RegExp(`^/api/v1/web/tasks/${uuid}/attachment-preloads/${uuid}$`, 'i').test(path);
    const preloadRetry = new RegExp(`^/api/v1/web/tasks/${uuid}/attachment-preloads/${uuid}/retry$`, 'i').test(path);
    const method = (preloadFile ? 'GET, DELETE' : preloadCreate || preloadRetry ? 'POST' : null) || routes.get(path) || (taskAction ? 'POST' : historyRoute ? 'GET' : resultRoute ? (path.endsWith('/read') ? 'POST' : 'GET') : undefined);
    if (method) {
      if (!method.split(', ').includes(req.method)) {
        res.writeHead(405, { Allow: method }).end();
        return;
      }
      const headers = { host: req.headers.host };
      for (const key of ['content-type', 'content-length', 'origin', 'sec-fetch-site']) {
        if (req.headers[key]) headers[key] = req.headers[key];
      }
      // Browser cookies share a host across ports. Do not forward staff cookies.
      const cookie = (req.headers.cookie || '').split(';').map(part => part.trim())
        .filter(part => part.startsWith('esdesk_web_session=')).join('; ');
      if (cookie) headers.cookie = cookie;
      const query = ['/api/v1/web/task-results', '/api/v1/web/history'].includes(path) ? new URL(req.url, expectedOrigin).search : '';
      const proxy = http.request(new URL(path + query, upstream), { method: req.method, headers }, incoming => {
        res.statusCode = incoming.statusCode;
        for (const key of ['content-type', 'retry-after', 'content-disposition']) {
          if (incoming.headers[key]) res.setHeader(key, incoming.headers[key]);
        }
        const cookies = (incoming.headers['set-cookie'] || []).filter(value => value.startsWith('esdesk_web_session='));
        if (cookies.length) res.setHeader('Set-Cookie', cookies);
        incoming.on('error', () => res.destroy());
        incoming.pipe(res);
      });
      const upload = (preloadCreate || path.endsWith('/description')) && (req.headers['content-type'] || '').startsWith('multipart/form-data');
      proxy.setTimeout(upload ? 360000 : 15000, () => proxy.destroy());
      proxy.on('error', () => {
        if (res.headersSent) res.destroy();
        else res.writeHead(502, { 'Content-Type': 'application/json' }).end('{"detail":"API unavailable"}');
      });
      req.on('aborted', () => proxy.destroy());
      res.on('close', () => { if (!res.writableEnded) proxy.destroy(); });
      req.pipe(proxy);
      return;
    }
    const asset = assets.get(path);
    if (!asset) { res.writeHead(404).end(); return; }
    if (!['GET', 'HEAD'].includes(req.method)) { res.writeHead(405, { Allow: 'GET, HEAD' }).end(); return; }
    try {
      const bytes = await readFile(fileURLToPath(new URL(asset[0], import.meta.url)));
      res.writeHead(200, { 'Content-Type': asset[1] }).end(req.method === 'HEAD' ? undefined : bytes);
    } catch {
      res.writeHead(500).end('Page unavailable');
    }
  });
}

if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  try {
    const port = Number(process.env.WEB_CLIENT_PORT || 5173);
    if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Invalid port');
    const mode = process.env.WEB_DOCKER_MODE || 'false';
    if (!['true', 'false'].includes(mode)) throw new Error('Invalid Docker mode');
    const dockerMode = mode === 'true';
    const browserOrigin = process.env.WEB_CLIENT_ORIGIN || `http://127.0.0.1:${port}`;
    const server = createDevServer({ apiOrigin: process.env.WEB_API_ORIGIN, dockerMode, browserOrigin });
    server.on('error', () => { console.error('Не удалось запустить WEB-клиент. Проверьте адрес и свободный порт.'); process.exitCode = 1; });
    server.listen(port, dockerMode ? '0.0.0.0' : '127.0.0.1', () => console.log(`ES DESK WEB: ${browserOrigin}`));
  } catch {
    console.error('Проверьте WEB_CLIENT_PORT, WEB_API_ORIGIN, WEB_DOCKER_MODE и WEB_CLIENT_ORIGIN.');
    process.exitCode = 1;
  }
}
