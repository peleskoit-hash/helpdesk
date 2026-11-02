const test = require('node:test');
const assert = require('node:assert/strict');
const { readFile } = require('node:fs/promises');
let api;
const task = '11111111-1111-4111-8111-111111111111';
const id = '22222222-2222-4222-8222-222222222222';
const uploadId = '33333333-3333-4333-8333-333333333333';
const signal = () => new AbortController().signal;
const response = value => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
test.before(async () => {
  api = await import('data:text/javascript;base64,' + Buffer.from(await readFile('web-client/api.js', 'utf8')).toString('base64'));
});

test('API-client preloads: one file + stable UUID, WEB URL, credentials, no fabricated multipart header', async t => {
  const calls = [];
  t.mock.method(global, 'fetch', async (url, options) => { calls.push({ url, ...options }); return response({ id, status: 'uploading' }); });
  const file = new File(['original bytes'], 'ordinary.webp', { type: 'image/webp' });
  for (let retry = 0; retry < 2; retry++) await api.createTaskPreload(task, file, uploadId, signal());
  for (const call of calls) {
    assert.equal(call.url, `/api/v1/web/tasks/${task}/attachment-preloads`);
    assert.equal(call.method, 'POST');
    assert.equal(call.body instanceof FormData, true);
    assert.deepEqual([...call.body.keys()], ['file', 'upload_id']);
    assert.equal(call.body.get('upload_id'), uploadId);
    assert.equal(call.body.get('file').name, file.name);
    assert.equal(await call.body.get('file').text(), 'original bytes');
    assert.deepEqual(call.headers, {});
    assert.equal(call.credentials, 'same-origin');
    assert.equal(call.mode, 'same-origin');
    assert.equal(call.cache, 'no-store');
    assert.equal(call.redirect, 'error');
  }
});

test('API-client preloads: GET status, explicit retry confirmation and DELETE use exact routes', async t => {
  const calls = [];
  t.mock.method(global, 'fetch', async (url, options) => {
    calls.push({ url, ...options });
    return response(options.method === 'DELETE' ? { ok: true } : { id, status: 'ready' });
  });
  await api.getTaskPreload(task, id, signal());
  await api.retryTaskPreload(task, id, false, signal());
  await api.retryTaskPreload(task, id, true, signal());
  await api.deleteTaskPreload(task, id, signal());
  assert.deepEqual(calls.map(call => call.method), ['GET', 'POST', 'POST', 'DELETE']);
  assert.ok(calls.every(call => call.url.startsWith(`/api/v1/web/tasks/${task}/attachment-preloads/${id}`)));
  assert.ok(calls[1].url.endsWith('/retry'));
  assert.deepEqual(JSON.parse(calls[1].body), { confirm_uncertain: false });
  assert.deepEqual(JSON.parse(calls[2].body), { confirm_uncertain: true });
  assert.equal(calls[0].body, undefined);
  assert.equal(calls[3].body, undefined);
});

test('API-client description: JSON contains text and ready IDs, never file bytes', async t => {
  let sent;
  t.mock.method(global, 'fetch', async (url, options) => { sent = { url, ...options }; return response({ task_id: task, state: 'ready', text: 'Created' }); });
  await api.sendTaskAction(task + '/description', { text: 'Initial', preload_ids: [id] }, signal());
  assert.equal(sent.headers['Content-Type'], 'application/json');
  assert.deepEqual(JSON.parse(sent.body), { text: 'Initial', preload_ids: [id] });
  assert.equal(sent.url, `/api/v1/web/tasks/${task}/description`);
});

test('API-client preloads: malformed ready responses never become readiness', async t => {
  for (const result of [{ status: 'ready' }, { id, status: 'unknown' }, null]) {
    const mock = t.mock.method(global, 'fetch', async () => response(result));
    await assert.rejects(api.getTaskPreload(task, id, signal()), error => error.status === 502);
    mock.mock.restore();
  }
});

test('API-client preloads: uncertain response is an error, not an automatic retry', async t => {
  let count = 0;
  t.mock.method(global, 'fetch', async () => {
    count++;
    return new Response(JSON.stringify({ detail: { error: { code: 'attachment_retry_uncertain', message: 'Unknown delivery' } } }), { status: 409 });
  });
  await assert.rejects(api.retryTaskPreload(task, id, false, signal()), error => error.status === 409 && error.code === 'attachment_retry_uncertain');
  assert.equal(count, 1);
});

test('API-client preloads: caller abort cancels the active request', async t => {
  t.mock.method(global, 'fetch', (url, options) => new Promise((resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
  }));
  const controller = new AbortController();
  const pending = api.getTaskPreload(task, id, controller.signal);
  controller.abort();
  await assert.rejects(pending, error => error.name === 'AbortError');
});
