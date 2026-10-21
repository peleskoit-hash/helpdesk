const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function element(tag = 'div') {
  const classes = new Set();
  const node = {
    tagName: tag.toUpperCase(), children: [], handlers: {}, dataset: {}, isConnected: true,
    style: { setProperty(key, value) { this[key] = value; } },
    classList: { add: (...names) => names.forEach(n => classes.add(n)),
      remove: (...names) => names.forEach(n => classes.delete(n)), contains: n => classes.has(n) },
    setAttribute(key, value) { this[key] = value; },
    removeAttribute(key) { if (key === 'src') this._src = undefined; else delete this[key]; },
    mutations: 0, scrollTop: 0,
    appendChild(child) { return this.insertBefore(child, null); },
    insertBefore(child, next) {
      child.remove();
      const index = next ? this.children.indexOf(next) : this.children.length;
      child.parentNode = this; this.children.splice(index, 0, child); this.mutations++; return child;
    },
    append(...children) { children.forEach(child => this.appendChild(child)); },
    remove() { if (this.parentNode) { this.parentNode.mutations++; this.parentNode.children = this.parentNode.children.filter(n => n !== this); } this.parentNode = null; },
    addEventListener(name, handler) { (this.handlers[name] ||= []).push(handler); },
    click() { return this.handlers.click?.[0]({ stopPropagation() {} }); },
    querySelector(selector) { return this.children.find(n => selector.startsWith('.')
      ? n.classList.contains(selector.slice(1)) : n.tagName.toLowerCase() === selector)
      || this.children.map(n => n.querySelector(selector)).find(Boolean) || null; },
  };
  Object.defineProperty(node, 'className', { get: () => [...classes].join(' '),
    set: value => { classes.clear(); value.split(/\s+/).filter(Boolean).forEach(n => classes.add(n)); } });
  Object.defineProperty(node, 'innerHTML', { set: () => { node.children.forEach(n => { n.parentNode = null; }); node.children = []; } });
  Object.defineProperty(node, 'src', { get: () => node._src, set: value => {
    node.srcAssignments = (node.srcAssignments || 0) + 1; node._src = value;
  } });
  return node;
}

function fixture() {
  const created = [], requests = [], revoked = [], errors = [], timers = new Map();
  let nextUrl = 0, nextTimer = 0, decodeFails = false, holdDecode = false, finishDecode;
  const document = { body: element('body'), createElement(tag) { const n = element(tag); created.push(n); return n; } };
  const window = { TicketApi: { headers: () => ({}), formatFileSize: value => `${value} B` } };
  const sandbox = { window, document, console, AbortController,
    setTimeout: fn => { timers.set(++nextTimer, fn); return nextTimer; }, clearTimeout: id => timers.delete(id),
    URL: { createObjectURL: () => `blob:${++nextUrl}`, revokeObjectURL: url => revoked.push(url) },
    Image: class { decode() {
      if (holdDecode) return new Promise(resolve => { finishDecode = resolve; });
      return decodeFails ? Promise.reject(new Error('decode')) : Promise.resolve();
    } },
    fetch: (url, options) => new Promise((resolve, reject) => requests.push({ url, options, resolve, reject })),
  };
  for (const file of ['ticket_client_card.js', 'ticket_attachments.js']) {
    vm.runInNewContext(fs.readFileSync(`api/app/static/${file}`, 'utf8'), sandbox);
  }
  const ctx = { inlineClient: true, state: { ticketId: 'ticket', card: {}, clientCardInline: {} },
    el: { clientDynamicCard: element() }, isDestroyed: () => false,
    isBlank: value => value === null || value === undefined || value === '',
    closeOnBackdropClick: (modal, callback) => { modal.backdrop = callback; }, showError: value => errors.push(value),
  };
  const attachments = window.TicketAttachments;
  const clicks = [];
  ctx.openClientAvatarPreview = (...urls) => clicks.push(urls);
  return { ctx, created, requests, revoked, errors, timers, clicks, attachments,
    decodeFailure: () => { decodeFails = true; },
    holdDecode: () => { holdDecode = true; }, finishDecode: () => finishDecode(),
    render: (profile = { telegram_avatar_present: true }, block = {}, config = null) => window.TicketClientCard.renderClientDynamicCard(ctx, profile, config || {
      blocks: [{ key: 'tg', title: 'Renamed Socials', color_scheme: 'blue', fields: [], ...block }],
    }),
    open: () => attachments.openClientAvatarPreview(ctx, '/thumbnail', '/full'),
    close: () => attachments.closeImagePreview(ctx),
  };
}

const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
const imageResponse = () => ({ ok: true, blob: async () => ({ type: 'image/jpeg' }) });

const pollingProfile = { telegram_avatar_present: true, telegram_avatar_updated_at: '2026-10-17T12:00:00',
  telegram_username: 'client', contact_name: 'Client', destination: 'Sales', internal_number: '123' };
const pollingConfig = { blocks: [
  { key: 'tg', fields: [{ key: 'telegram_username' }] },
  { key: 'profile', fields: [{ key: 'contact_name' }] },
  { key: 'department', fields: [{ key: 'destination' }, { key: 'internal_number' }] },
] };

test('identical polling preserves every block, field, avatar and scroll position without src reassignment', () => {
  const f = fixture(), root = f.ctx.el.clientDynamicCard;
  f.render(pollingProfile, {}, pollingConfig);
  const blocks = [...root.children], rows = blocks.flatMap(block => block.children);
  const image = f.created.find(node => node.tagName === 'IMG');
  image.onload();
  root.scrollTop = 175;
  const mutations = root.mutations, created = f.created.length;
  for (let i = 0; i < 5; i++) f.render({ ...pollingProfile }, {}, JSON.parse(JSON.stringify(pollingConfig)));
  assert.deepEqual(root.children, blocks);
  assert.deepEqual(blocks.flatMap(block => block.children), rows);
  assert.equal(root.mutations, mutations);
  assert.equal(f.created.length, created);
  assert.equal(image.srcAssignments, 1);
  assert.equal(root.scrollTop, 175);
});

test('changing one field patches only its value and preserves other rows, blocks and loaded avatar', () => {
  const f = fixture(), root = f.ctx.el.clientDynamicCard;
  f.render(pollingProfile, {}, pollingConfig);
  const blocks = [...root.children], row = blocks[2].children[1], label = row.children[0];
  const unchangedRow = blocks[2].children[2], image = f.created.find(node => node.tagName === 'IMG');
  image.onload(); root.scrollTop = 140;
  f.render({ ...pollingProfile, destination: 'Support' }, {}, pollingConfig);
  assert.deepEqual(root.children, blocks);
  assert.equal(blocks[2].children[1], row);
  assert.equal(row.children[0], label);
  assert.equal(row.children.at(-1).textContent, 'Support');
  assert.equal(blocks[2].children[2], unchangedRow);
  assert.equal(image.srcAssignments, 1);
  assert.equal(root.scrollTop, 140);
});

test('pending thumbnail onload remains valid across polling without a second request', () => {
  const f = fixture();
  f.render();
  const block = f.ctx.el.clientDynamicCard.children[0], image = f.created.find(n => n.tagName === 'IMG');
  const onload = image.onload;
  f.render();
  assert.equal(f.ctx.el.clientDynamicCard.children[0], block);
  assert.equal(image.onload, onload);
  onload();
  assert.equal(block.classList.contains('hidden'), false);
  assert.equal(image.srcAssignments, 1);
});

test('Users keeps unchanged rows and updates changed values without creating an avatar', () => {
  const f = fixture(), root = f.ctx.el.clientDynamicCard;
  f.ctx.inlineClient = false;
  f.render(pollingProfile, {}, pollingConfig);
  const blocks = [...root.children], row = blocks[1].children[1];
  f.render({ ...pollingProfile, contact_name: 'Updated' }, {}, pollingConfig);
  assert.ok(root.children.every((block, index) => block === blocks[index]));
  assert.equal(blocks[1].children[1], row);
  assert.equal(row.children.at(-1).textContent, 'Updated');
  assert.equal(f.created.filter(node => node.tagName === 'IMG').length, 0);
});

test('avatar version changes reload only the image without collapsing its loaded block', () => {
  const f = fixture(), root = f.ctx.el.clientDynamicCard;
  f.render(pollingProfile, {}, pollingConfig);
  const blocks = [...root.children], image = f.created.find(node => node.tagName === 'IMG');
  image.onload();
  f.render({ ...pollingProfile, telegram_avatar_updated_at: '2026-10-17T13:00:00' }, {}, pollingConfig);
  assert.deepEqual(root.children, blocks);
  assert.equal(f.created.filter(node => node.tagName === 'IMG').length, 1);
  assert.equal(image.srcAssignments, 2);
  assert.match(image.src, /13%3A00%3A00/);
  assert.equal(blocks[0].children[1].classList.contains('hidden'), false);
});

test('editing leaves the hidden view intact, then patches saved values without reloading the avatar', () => {
  const f = fixture(), root = f.ctx.el.clientDynamicCard;
  f.render(pollingProfile, {}, pollingConfig);
  const blocks = [...root.children], image = f.created.find(node => node.tagName === 'IMG');
  image.onload();
  f.ctx.state.clientCardInline.open = true;
  f.render({ ...pollingProfile, contact_name: 'Updated' }, {}, pollingConfig);
  assert.deepEqual(root.children, blocks);
  f.ctx.state.clientCardInline.open = false;
  f.render({ ...pollingProfile, contact_name: 'Updated' }, {}, pollingConfig);
  assert.deepEqual(root.children, blocks);
  assert.equal(blocks[1].children[1].children.at(-1).textContent, 'Updated');
  assert.equal(image.srcAssignments, 1);
});

test('avatar is centered below the tg header before fields, including a renamed Socials block', () => {
  const f = fixture();
  f.render({ telegram_avatar_present: true, email: 'client@example.test' }, { fields: [{ key: 'email', label: 'Email' }] });
  const block = f.ctx.el.clientDynamicCard.children[0];
  assert.equal(block.children[0].textContent, 'Renamed Socials');
  assert.equal(block.children[1].tagName, 'BUTTON');
  assert.equal(block.children[2].className, 'client-card-field');
  block.children[1].children[0].onload();
  block.children[1].click();
  assert.deepEqual(f.clicks[0], ['/api/v1/tickets/ticket/client-avatar/thumbnail', '/api/v1/tickets/ticket/client-avatar/full']);
  assert.equal(f.requests.length, 0);
});

test('thumbnail alone can display tg; absent or failed thumbnail leaves no block/space', () => {
  const f = fixture();
  f.render();
  assert.equal(f.ctx.el.clientDynamicCard.children[0].classList.contains('hidden'), true);
  f.created.find(n => n.tagName === 'IMG').onload();
  assert.equal(f.ctx.el.clientDynamicCard.children.length, 1);
  f.render({ telegram_avatar_present: false });
  assert.equal(f.ctx.el.clientDynamicCard.children.length, 0);
  f.render();
  f.created.filter(n => n.tagName === 'IMG').at(-1).onerror();
  assert.equal(f.ctx.el.clientDynamicCard.children.length, 0);
});

for (const context of ['Users', 'editor', 'other block', 'no ticket']) {
  test(`no avatar element is created in ${context}`, () => {
    const f = fixture();
    if (context === 'Users') f.ctx.inlineClient = false;
    if (context === 'editor') f.ctx.state.clientCardInline.open = true;
    if (context === 'no ticket') f.ctx.state.ticketId = null;
    f.render(undefined, context === 'other block' ? { key: 'other', title: 'Socials' } : {});
    assert.equal(f.created.filter(n => n.tagName === 'IMG').length, 0);
  });
}

test('late thumbnail load cannot restore a previous card', () => {
  const f = fixture();
  f.render();
  const old = f.created.find(n => n.tagName === 'IMG');
  f.render({ telegram_avatar_present: false });
  old.onload();
  assert.equal(f.ctx.el.clientDynamicCard.children.length, 0);
});

test('viewer opens immediately with a small thumbnail, then replaces it with the decoded full photo', async () => {
  const f = fixture(), loading = f.open(), el = f.ctx.el;
  assert.equal(el.imagePreviewModal.classList.contains('hidden'), false);
  assert.equal(el.imagePreviewImg.src, '/thumbnail');
  assert.equal(el.imagePreviewAvatarLoading.classList.contains('hidden'), false);
  assert.equal(f.requests[0].url, '/full');
  f.requests[0].resolve(imageResponse());
  await loading;
  assert.equal(el.imagePreviewImg.src, 'blob:1');
  assert.equal(el.imagePreviewModal.classList.contains('avatar-preview-full'), true);
  assert.equal(el.imagePreviewAvatarLoading.classList.contains('hidden'), true);
  el.imagePreviewModal.handlers.wheel[0]({ deltaY: -1, preventDefault() {} });
  assert.equal(el.imagePreviewImg.style.transform, 'scale(1.12)');
  el.imagePreviewClose.click();
  assert.deepEqual(f.revoked, ['blob:1']);
});

for (const failure of ['404', 'network', 'decode', 'timeout']) {
  test(`viewer retains thumbnail without endless loading after ${failure}`, async () => {
    const f = fixture(), pending = f.open();
    if (failure === 'network') f.requests[0].reject(new Error('network'));
    else if (failure === '404') f.requests[0].resolve({ ok: false });
    else {
      if (failure === 'timeout') [...f.timers.values()][0]();
      if (failure === 'decode') f.decodeFailure();
      f.requests[0].resolve(imageResponse());
    }
    await pending;
    assert.equal(f.ctx.el.imagePreviewImg.src, '/thumbnail');
    assert.equal(f.ctx.el.imagePreviewModal.classList.contains('hidden'), false);
    assert.equal(f.ctx.el.imagePreviewAvatarLoading.classList.contains('hidden'), true);
    assert.deepEqual(f.errors, []);
    if (failure === 'decode') assert.deepEqual(f.revoked, ['blob:1']);
  });
}

test('closing while loading aborts and prevents a late response reopening the viewer', async () => {
  const f = fixture(), pending = f.open();
  f.ctx.el.imagePreviewModal.backdrop();
  assert.equal(f.requests[0].options.signal.aborted, true);
  f.requests[0].resolve(imageResponse());
  await pending;
  assert.equal(f.ctx.el.imagePreviewModal.classList.contains('hidden'), true);
  assert.equal(f.ctx.el.imagePreviewImg.src, undefined);
});

test('switching views prevents stale avatar response replacing another view', async () => {
  const f = fixture(), first = f.open(), second = f.open();
  f.requests[1].resolve(imageResponse());
  await second;
  f.requests[0].resolve(imageResponse());
  await first;
  assert.equal(f.ctx.el.imagePreviewImg.src, 'blob:1');
  f.attachments.destroyAllAttachmentResources();
  assert.deepEqual(f.revoked, ['blob:1']);
});

test('closing or timing out during image decoding releases its object URL immediately', async () => {
  for (const timedOut of [false, true]) {
    const f = fixture();
    f.holdDecode();
    const pending = f.open();
    f.requests[0].resolve(imageResponse());
    await flush();
    if (timedOut) [...f.timers.values()][0]();
    else f.close();
    assert.deepEqual(f.revoked, ['blob:1']);
    f.finishDecode();
    await pending;
    assert.notEqual(f.ctx.el.imagePreviewImg.src, 'blob:1');
  }
});

test('ordinary attachments keep their viewer, download button and cached object URLs', async () => {
  const f = fixture(), avatar = f.open(), container = element();
  f.attachments.renderAttachments(f.ctx, { attachments: [{ id: 'file', is_image: true,
    original_filename: 'photo.jpg', mime_type: 'image/jpeg', file_size: 20, download_url: '/attachment' }] }, container);
  f.requests[1].resolve(imageResponse());
  await flush();
  await container.children[0].children[0].click();
  assert.equal(f.ctx.el.imagePreviewImg.src, 'blob:1');
  assert.equal(f.ctx.el.imagePreviewModal.classList.contains('avatar-preview'), false);
  assert.equal(f.ctx.el.imagePreviewDownload.classList.contains('hidden'), false);
  f.requests[0].resolve(imageResponse());
  await avatar;
  assert.equal(f.ctx.el.imagePreviewImg.src, 'blob:1');
  f.attachments.destroyAllAttachmentResources();
  assert.deepEqual(f.revoked, ['blob:1']);
});

test('CSS bounds the Socials square and full preview without cropping or changing the block style', () => {
  const css = fs.readFileSync('api/app/static/ticket_client_card.css', 'utf8');
  assert.match(css, /width: min\(66\.666%, 160px\)/);
  assert.match(css, /aspect-ratio: 1/);
  assert.match(css, /object-fit: contain/);
  assert.match(css, /width: min\(600px, calc\(100vw - 56px\)\)/);
  assert.match(css, /prefers-reduced-motion/);
});

test('avatar resources use matching versions on direct pages and AppShell mounts', () => {
  const files = ['api/app/templates/ticket.html', 'api/app/templates/management_users.html', 'api/app/static/app_shell.js'];
  for (const resource of ['ticket_client_card.js', 'ticket_attachments.js', 'ticket.js']) {
    const versions = files.flatMap(file => [...fs.readFileSync(file, 'utf8').matchAll(
      new RegExp(`/static/${resource.replaceAll('.', '\\.')}\\?v=([^"']+)`, 'g'))].map(match => match[1]));
    assert.ok(versions.length >= 2);
    assert.deepEqual([...new Set(versions)], [resource === 'ticket_client_card.js' ? '20261021-client-save-position' : resource === 'ticket.js' ? '20261021-task-panel-refresh' : '20261020-quotes']);
  }
});
