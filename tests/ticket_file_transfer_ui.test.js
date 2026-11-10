const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function fixture() {
  class Node {
    constructor() {
      this.listeners = new Map();
      this.classes = new Set();
      this.classList = {add: name => this.classes.add(name), remove: name => this.classes.delete(name)};
    }
    addEventListener(type, handler) {
      if (!this.listeners.has(type)) this.listeners.set(type, new Set());
      this.listeners.get(type).add(handler);
    }
    removeEventListener(type, handler) {this.listeners.get(type)?.delete(handler);}
    emit(type, data = {}) {
      const event = {type, target: this, prevented: false, preventDefault() {this.prevented = true;}, ...data};
      for (let node = this; node; node = node.parent) {
        for (const handler of node.listeners.get(type) || []) handler(event);
      }
      return event;
    }
  }
  const panel = new Node(), input = new Node(), header = new Node(), messages = new Node(), other = new Node();
  input.closest = selector => {assert.equal(selector, '.panel.ticket-card'); return panel;};
  input.parent = header.parent = messages.parent = panel;
  const document = {activeElement: input};
  const calls = [];
  const window = {TicketApi: {}, setInterval: () => 1, clearInterval() {}, crypto: {randomUUID: () => 'archive'}};
  const context = vm.createContext({window, document, AbortController,
    FormData: class {append() {}}, console});
  vm.runInContext(fs.readFileSync('api/app/static/ticket_composer.js', 'utf8'), context);
  const composer = window.TicketComposer;
  const ctx = {
    state: {role: 'agent', ticketId: 'ticket', card: {status: 'pending'}, composeMode: 'reply', selectedFiles: []},
    el: {messageInput: input, replyFilesInput: new Node(), selectedFilesList: null,
      modeReplyBtn: new Node(), modeCommentBtn: new Node(), sendNowBtn: new Node(),
      addFilesBtn: new Node(), clearQuoteBtn: new Node()},
    api: async (url, options) => {calls.push({url, options}); return {id: 'preload', status: 'uploading'};},
    showError: message => {throw Error(message);},
  };
  composer.bindComposerHandlers(ctx);
  const file = name => ({name, size: 100, type: 'application/pdf'});
  const transfer = files => ({types: ['Files'], files, items: files.map(file => ({kind: 'file', getAsFile: () => file}))});
  return {composer, ctx, input, panel, header, messages, other, document, calls, file, transfer};
}

for (const count of [1, 3]) {
  for (const area of ['panel', 'header', 'messages', 'input']) {
    test(`drop ${count} file(s) on ${area} queues all without sending`, () => {
      const f = fixture(), files = Array.from({length: count}, (_, i) => f.file(`file-${i}.pdf`));
      const dataTransfer = f.transfer(files);
      assert.equal(f[area].emit('dragenter', {dataTransfer}).prevented, true);
      assert.ok(f.panel.classes.has('file-drop-active'));
      assert.equal(f[area].emit('dragover', {dataTransfer}).prevented, true);
      assert.equal(dataTransfer.dropEffect, 'copy');
      const dropped = f[area].emit('drop', {dataTransfer});
      assert.equal(dropped.prevented, true);
      assert.deepEqual(Array.from(f.ctx.state.selectedFiles), files);
      assert.ok(!f.panel.classes.has('file-drop-active'));
      assert.equal(f.calls.length, 0);
    });
  }
  test(`paste ${count} file(s) uses all clipboard files`, () => {
    const f = fixture(), files = Array.from({length: count}, (_, i) => f.file(`file-${i}.pdf`));
    const event = f.input.emit('paste', {clipboardData: f.transfer(files)});
    assert.equal(event.prevented, true);
    assert.deepEqual(Array.from(f.ctx.state.selectedFiles), files);
    assert.equal(f.calls.length, 0);
  });
}

test('clipboard items fallback includes images and other files once, ignoring null entries', () => {
  const f = fixture(), files = [f.file('photo.png'), f.file('document.docx')];
  files[0].type = 'image/png';
  const clipboardData = {files: [], items: [
    {kind: 'string'}, {kind: 'file', getAsFile: () => null},
    ...files.map(file => ({kind: 'file', getAsFile: () => file})),
  ]};
  assert.equal(f.input.emit('paste', {clipboardData}).prevented, true);
  assert.deepEqual(Array.from(f.ctx.state.selectedFiles), files);
});

test('text paste, unavailable Explorer files and text drag retain browser defaults', () => {
  const f = fixture();
  for (const clipboardData of [{files: [], items: [{kind: 'string'}]}, {files: []}, null]) {
    assert.equal(f.input.emit('paste', {clipboardData}).prevented, false);
  }
  const dataTransfer = {types: ['text/plain'], files: []};
  for (const type of ['dragenter', 'dragover', 'drop']) assert.equal(f.panel.emit(type, {dataTransfer}).prevented, false);
  assert.equal(f.ctx.state.selectedFiles.length, 0);
  assert.ok(!f.panel.classes.has('file-drop-active'));
});

test('paste is scoped to the focused message input, not other fields or the chat panel', () => {
  const f = fixture(), clipboardData = f.transfer([f.file('file')]);
  f.document.activeElement = f.other;
  for (const node of [f.other, f.input, f.panel]) assert.equal(node.emit('paste', {clipboardData}).prevented, false);
  assert.equal(f.ctx.state.selectedFiles.length, 0);
});

for (const reason of ['closed', 'readonly-role', 'disabled', 'readonly-input', 'disabled-files', 'loading']) {
  test(`${reason}: drop cannot navigate or add attachments, paste adds none`, () => {
    const f = fixture(), data = f.transfer([f.file('file')]);
    if (reason === 'closed') f.ctx.state.card.status = 'closed';
    if (reason === 'readonly-role') f.ctx.state.role = 'chiki_piki';
    if (reason === 'disabled') f.input.disabled = true;
    if (reason === 'readonly-input') f.input.readOnly = true;
    if (reason === 'disabled-files') f.ctx.el.addFilesBtn.disabled = true;
    if (reason === 'loading') f.ctx.state.card = null;
    f.panel.emit('dragover', {dataTransfer: data});
    assert.equal(data.dropEffect, 'none');
    assert.equal(f.panel.emit('drop', {dataTransfer: data}).prevented, true);
    f.input.emit('paste', {clipboardData: data});
    assert.equal(f.ctx.state.selectedFiles.length, 0);
    assert.ok(!f.panel.classes.has('file-drop-active'));
    assert.equal(f.calls.length, 0);
  });
}

test('nested enter/leave does not remove highlight until leaving the panel; dragend clears it', () => {
  const f = fixture(), dataTransfer = f.transfer([f.file('file')]);
  f.panel.emit('dragenter', {dataTransfer});
  f.header.emit('dragenter', {dataTransfer});
  f.header.emit('dragleave');
  assert.ok(f.panel.classes.has('file-drop-active'));
  f.panel.emit('dragleave');
  assert.ok(!f.panel.classes.has('file-drop-active'));
  f.panel.emit('dragenter', {dataTransfer});
  f.panel.emit('dragend');
  assert.ok(!f.panel.classes.has('file-drop-active'));
});

test('repeat SPA binding and destruction do not duplicate or retain file handlers', () => {
  const f = fixture(), data = f.transfer([f.file('file')]);
  f.composer.bindComposerHandlers(f.ctx);
  assert.equal(f.input.listeners.get('paste').size, 1);
  assert.equal(f.panel.listeners.get('drop').size, 1);
  f.panel.emit('drop', {dataTransfer: data});
  assert.equal(f.ctx.state.selectedFiles.length, 1);
  f.panel.emit('dragenter', {dataTransfer: data});
  f.composer.destroyComposerState(f.ctx);
  assert.equal(f.input.listeners.get('paste').size, 0);
  assert.equal(f.panel.listeners.get('drop').size, 0);
  assert.ok(!f.panel.classes.has('file-drop-active'));
  f.panel.emit('drop', {dataTransfer: data});
  assert.equal(f.ctx.state.selectedFiles.length, 1);
  f.composer.bindComposerHandlers(f.ctx);
  f.input.emit('paste', {clipboardData: data});
  assert.equal(f.ctx.state.selectedFiles.length, 2);
});

test('file transfers reuse attachment preload in both modes without sending a reply or comment', async () => {
  for (const mode of ['reply', 'comment']) {
    const f = fixture();
    f.ctx.state.composeMode = mode;
    f.ctx.canUseAttachmentPreload = () => true;
    f.input.emit('paste', {clipboardData: f.transfer([f.file('a'), f.file('b')])});
    await new Promise(setImmediate);
    assert.equal(f.calls.length, 2);
    assert.ok(f.calls.every(call => call.url.endsWith('/attachment-preloads')));
    assert.equal(f.ctx.state.selectedFiles.length, 2);
    f.composer.destroyComposerState(f.ctx);
  }
});

test('drag highlight is limited to the active chat panel without changing layout', () => {
  const css = fs.readFileSync('api/app/static/styles.css', 'utf8');
  const rule = css.split('.panel.ticket-card.file-drop-active {')[1].split('}')[0];
  assert.match(rule, /outline: 2px solid var\(--accent\)/);
  assert.match(rule, /outline-offset: -2px/);
  assert.doesNotMatch(rule, /padding|margin|width|height|pointer-events/);
});


for (const method of ['drop','paste']) test(`observer comment ${method} reuses preload without sending`, async () => {
  const f=fixture();
  f.ctx.state.role='chiki_piki';
  f.ctx.state.composeMode='comment';
  f.ctx.canUseAttachmentPreload=() => true;
  const transfer=f.transfer([f.file('observer.pdf')]);
  const event=method==='drop' ? f.panel.emit('drop',{dataTransfer:transfer}) : f.input.emit('paste',{clipboardData:transfer});
  assert.equal(event.prevented,true);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.ctx.state.selectedFiles.length,1);
  assert.equal(f.calls.filter(call => call.url.endsWith('/attachment-preloads')).length,1);
  assert.equal(f.calls.filter(call => /\/(reply|comment)$/.test(call.url)).length,0);
});
