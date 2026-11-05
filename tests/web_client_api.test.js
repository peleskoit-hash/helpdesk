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

test('API-client chat and active intake use authenticated read-only routes and validate responses', async t => {
  const calls = [];
  const row = { id: '9007199254740993', message_type: 'bot', text: '<script>literal</script>', created_at: '2026-11-03T10:00:00Z' };
  let payload = { items: [row] };
  t.mock.method(global, 'fetch', async (url, options) => { calls.push({ url, ...options }); return response(payload); });
  assert.deepEqual(await api.getWebChat(signal()), [row]);
  payload = null;
  assert.equal(await api.getActiveWebTask(signal()), null);
  payload = { task_id: task, button_id: 'button', state: 'confirmation_required', text: 'Confirm', expires_at: '2026-11-03T10:10:00Z' };
  assert.deepEqual(await api.getActiveWebTask(signal()), payload);
  assert.deepEqual(calls.map(call => call.url), ['/api/v1/web/chat', '/api/v1/web/tasks/active', '/api/v1/web/tasks/active']);
  assert.ok(calls.every(call => !call.body && call.credentials === 'same-origin' && call.cache === 'no-store'));
  for (payload of [{ items: [row, row] }, { items: [{ ...row, id: '<bad>' }] }, { items: Array(51).fill(row) }]) {
    await assert.rejects(api.getWebChat(signal()), error => error.status === 502);
  }
  payload = { task_id: task, state: 'ready' };
  await assert.rejects(api.getActiveWebTask(signal()), error => error.status === 502);
});

test('API-client 401 stops active requests and blocks further WEB calls until a new session', async t => {
  t.after(() => api.resumeWebRequests());
  const calls = [];
  t.mock.method(global, 'fetch', async (url, options) => {
    calls.push({ url, ...options });
    return new Response('{}', { status: 401 });
  });
  await assert.rejects(api.getActiveWebTask(signal()), error => error.status === 401);
  assert.equal(calls[0].signal.aborted, true);
  for (const action of [() => api.getComposerDraft(signal()), () => api.putComposerDraft('', 0, signal()), () => api.getWebChat(signal()), () => api.getWebMenu(signal()),
    () => api.deleteTaskPreload(task, id, signal()), () => api.sendTaskAction(task + '/cancel', undefined, signal()),
    () => api.webAuth.logout()]) await assert.rejects(action(), error => error.status === 401);
  assert.equal(calls.length, 1);
  api.resumeWebRequests();
  t.mock.method(global, 'fetch', async () => response({ items: [] }));
  assert.deepEqual(await api.getWebChat(signal()), []);
});

test('API-client ignores late 401 from an aborted session request', async t => {
  t.after(() => api.resumeWebRequests());
  let release;
  t.mock.method(global, 'fetch', () => new Promise(resolve => { release = resolve; }));
  const controller = new AbortController();
  const old = api.getActiveWebTask(controller.signal);
  controller.abort();
  api.resumeWebRequests();
  release(new Response('{}', { status: 401 }));
  await assert.rejects(old, error => error.status === 401);
  t.mock.method(global, 'fetch', async () => response({ items: [] }));
  assert.deepEqual(await api.getWebChat(signal()), []);
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

test('API-client composer draft: exact text, expected version, same-session PUT and validation', async t => {
  api.resumeWebRequests();
  const calls = [];
  let value = { id, text: '  Text\n ', version: 4 };
  t.mock.method(global, 'fetch', async (url, options) => { calls.push({ url, ...options }); return response(value); });
  assert.deepEqual(await api.getComposerDraft(signal()), value);
  assert.deepEqual(await api.putComposerDraft(value.text, 3, signal()), value);
  assert.deepEqual(calls.map(call => [call.url, call.method]), [
    ['/api/v1/web/composer-draft', 'GET'], ['/api/v1/web/composer-draft', 'PUT'],
  ]);
  assert.deepEqual(JSON.parse(calls[1].body), { text: value.text, version: 3 });
  assert.equal(calls[1].headers['Content-Type'], 'application/json');
  assert.equal(calls[1].credentials, 'same-origin');
  assert.equal(calls[1].cache, 'no-store');
  for (value of [{ id: 'bad', text: '', version: 0 }, { id, text: null, version: 0 },
                 { id, text: '', version: -1 }, { id, text: '', version: 1.5 }]) {
    await assert.rejects(api.getComposerDraft(signal()), error => error.status === 502);
  }
  t.mock.method(global, 'fetch', async () => new Response('{}', { status: 409 }));
  await assert.rejects(api.putComposerDraft('Keep', 2, signal()), error => error.status === 409);
});


test('API universal preloads: no task ID, stable upload, protected content and strict metadata', async t => {
  api.resumeWebRequests();
  const calls = [], root = '/api/v1/web/composer-draft/attachment-preloads';
  const row = { id, status: 'ready', original_filename: 'clip.mp4', mime_type: 'video/mp4', file_size: 10 };
  t.mock.method(global, 'fetch', async (url, options) => {
    calls.push({ url, ...options });
    return url.endsWith('/content') ? new Response('original bytes') : response(
      options.method === 'DELETE' ? { ok: true } : options.method === 'GET' && url === root ? [row] : row);
  });
  const file = new File(['mp4'], 'clip.mp4', { type: 'video/mp4' });
  await api.createDraftPreload(file, uploadId, signal());
  assert.deepEqual(await api.listDraftPreloads(signal()), [row]);
  await api.getDraftPreload(id, signal());
  await api.retryDraftPreload(id, false, signal());
  assert.equal(await (await api.getDraftPreloadContent(id, signal())).text(), 'original bytes');
  await api.deleteDraftPreload(id, signal());
  assert.deepEqual(calls.map(call => [call.url, call.method]), [
    [root, 'POST'], [root, 'GET'], [root+'/'+id, 'GET'], [root+'/'+id+'/retry', 'POST'],
    [root+'/'+id+'/content', 'GET'], [root+'/'+id, 'DELETE'],
  ]);
  assert.equal(calls[0].body.get('upload_id'), uploadId);
  assert.equal(calls[0].body.get('file').name, 'clip.mp4');
  assert.equal(calls[0].headers['Content-Type'], undefined);
  assert.ok(calls.every(call => call.credentials === 'same-origin' && call.redirect === 'error'));
  t.mock.method(global, 'fetch', async () => response([{ ...row, id: 'not-a-uuid' }]));
  await assert.rejects(api.listDraftPreloads(signal()), error => error.status === 502);
});


test('API oversized-file notice sends metadata only and requires an acknowledgement', async t => {
  api.resumeWebRequests();
  const metadata={file_name:'video.mp4',file_size:20*1024*1024+1,event_id:uploadId};
  const calls=[];
  t.mock.method(global,'fetch',async(url,options)=>{calls.push({url,...options});return response({ok:true,message_id:'1'});});
  await api.notifyAttachmentTooLarge(metadata,signal());
  assert.equal(calls[0].url,'/api/v1/web/chat/attachment-too-large');
  assert.equal(calls[0].method,'POST');
  assert.deepEqual(JSON.parse(calls[0].body),metadata);
  assert.equal(calls[0].headers['Content-Type'],'application/json');
  assert.equal(calls[0].credentials,'same-origin');
  assert.ok(calls[0].body.length<256);
  for (const result of [{ok:false,message_id:'1'},{ok:true},{ok:true,message_id:1}]) {
    t.mock.method(global,'fetch',async()=>response(result));
    await assert.rejects(api.notifyAttachmentTooLarge(metadata,signal()),error=>error.status===502);
  }
});
