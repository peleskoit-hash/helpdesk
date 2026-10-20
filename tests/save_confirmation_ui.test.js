const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function load() {
  const listeners = new Map();
  const timers = new Map();
  let now = 0;
  let timerId = 0;
  const body = {
    children: [],
    appendChild(node) { this.children.push(node); node.parent = this; },
  };
  const document = {
    body, scripts: [], readyState: 'loading', addEventListener() {},
    createElement() {
      return {
        style: {}, attributes: {},
        setAttribute(name, value) { this.attributes[name] = value; },
        remove() {
          body.children = body.children.filter(node => node !== this);
          this.parent = null;
        },
      };
    },
  };
  const window = {
    addEventListener(name, fn) {
      if (!listeners.has(name)) listeners.set(name, new Set());
      listeners.get(name).add(fn);
    },
    removeEventListener(name, fn) { listeners.get(name)?.delete(fn); },
  };
  const context = vm.createContext({window, document, console, URL,
    setTimeout(fn, delay) { const id = ++timerId; timers.set(id, {fn, at: now + delay}); return id; },
    clearTimeout(id) { timers.delete(id); }, alert() {},
  });
  for (const file of ['app_shell.js', 'bot_buttons_editor.js']) {
    vm.runInContext(fs.readFileSync(`api/app/static/${file}`, 'utf8'), context);
  }
  return {window, body, timers, listeners,
    advance(ms) {
      now += ms;
      for (const [id, timer] of timers) {
        if (timer.at <= now) { timers.delete(id); timer.fn(); }
      }
    },
  };
}

function panel() {
  return {isConnected: true, style: {}, children: ['existing content'],
    rect: {left: 100, top: 200, width: 600, height: 400},
    getBoundingClientRect() { return this.rect; }};
}

test('public confirmation centers on the supplied panel without altering its content or layout', () => {
  const app = load();
  const editor = panel();
  app.window.HelpdeskAppShell.showSaved(editor);
  const notice = app.body.children[0];
  assert.equal(notice.textContent, 'Сохранено');
  assert.equal(notice.attributes.role, 'status');
  assert.equal(notice.style.left, '400px');
  assert.equal(notice.style.top, '400px');
  assert.deepEqual(editor.style, {});
  assert.deepEqual(editor.children, ['existing content']);
  editor.rect = {left: 20, top: 30, width: 400, height: 200};
  for (const listener of app.listeners.get('scroll')) listener();
  assert.equal(notice.style.left, '220px');
  assert.equal(notice.style.top, '130px');
  app.advance(500);
  assert.equal(app.body.children.length, 1);
  app.advance(499);
  assert.equal(app.body.children.length, 1);
  app.advance(1);
  assert.equal(app.body.children.length, 0);
  assert.equal(app.listeners.get('scroll').size, 0);
  assert.equal(app.listeners.get('resize').size, 0);
});

test('repeat save restarts the entire animation and page teardown removes pending confirmations', () => {
  const app = load();
  const editor = panel();
  app.window.HelpdeskAppShell.showSaved(editor);
  const first = app.body.children[0];
  app.advance(800);
  app.window.HelpdeskAppShell.showSaved(editor);
  assert.equal(app.body.children.length, 1);
  assert.notEqual(app.body.children[0], first);
  assert.equal(app.timers.size, 1);
  app.advance(200);
  assert.equal(app.body.children.length, 1);
  app.advance(799);
  assert.equal(app.body.children.length, 1);
  app.advance(1);
  assert.equal(app.body.children.length, 0);
  app.window.HelpdeskAppShell.showSaved(editor);
  app.window.HelpdeskAppShell.showSaved(panel());
  app.window.HelpdeskAppShell.destroyCurrentPage();
  assert.equal(app.body.children.length, 0);
  assert.equal(app.timers.size, 0);
});

test('disconnected panels do not retain notifications or listeners', () => {
  const app = load();
  const editor = panel();
  app.window.HelpdeskAppShell.showSaved(editor);
  editor.isConnected = false;
  for (const listener of app.listeners.get('resize')) listener();
  assert.equal(app.body.children.length, 0);
  assert.equal(app.timers.size, 0);
  app.window.HelpdeskAppShell.showSaved(editor);
  assert.equal(app.body.children.length, 0);
});

function editorContext(workflow = 'ticket') {
  return {
    state: {selectedMenu: 'Menu', selectedLeaf: {technical_key: `Menu|${workflow}|A`, workflow_type: workflow, label: 'A'},
      editorBotLabel: 'A', automationTypeValue: 'none', selectedFiles: [], conditionalRules: []},
    setStatus() {}, fieldMeta() {}, fieldLabel(value) { return value; },
    async api() { return {ok: true}; }, async refreshMenuStructure() {},
  };
}

for (const workflow of ['ticket', 'task', 'auto', 'submenu', 'group_task', 'group_task_child']) {
  test(`one confirmation after full successful ${workflow} save targets the inner editor panel`, async () => {
    const app = load();
    const editor = panel();
    const ctx = editorContext(workflow);
    let resolveSave, resolveRefresh;
    ctx.api = () => new Promise(resolve => { resolveSave = resolve; });
    ctx.refreshMenuStructure = () => new Promise(resolve => { resolveRefresh = resolve; });
    ctx.el = {content: {querySelector(selector) {
      assert.equal(selector, '.panel.bot-instruction-editor');
      return editor;
    }}};
    const saving = app.window.BotButtonsEditor.saveInstruction(ctx);
    assert.equal(app.body.children.length, 0);
    resolveSave({ok: true});
    await new Promise(setImmediate);
    assert.equal(typeof resolveRefresh, 'function');
    assert.equal(app.body.children.length, 0);
    resolveRefresh();
    await saving;
    assert.equal(app.body.children.length, 1);
    assert.equal(app.body.children[0].style.left, '400px');
    assert.equal(app.timers.size, 1);
  });
}

for (const reason of ['no_leaf', 'no_menu', 'empty_label', 'assignee', 'projection', 'condition', 'api', 'refresh']) {
  test(`no success confirmation for ${reason}`, async () => {
    const app = load();
    const ctx = editorContext();
    ctx.el = {content: {querySelector: () => panel()}};
    if (reason === 'no_leaf') ctx.state.selectedLeaf = null;
    if (reason === 'no_menu') ctx.state.selectedMenu = '';
    if (reason === 'empty_label') ctx.state.editorBotLabel = ' ';
    if (reason === 'assignee') ctx.state.fixedAssigneeEnabled = true;
    if (reason === 'projection') ctx.state.sendFilesProjectionError = 'Invalid projection';
    if (reason === 'condition') {
      ctx.state.conditionalFilesOverridden = true;
      ctx.state.conditionalRules = [{conditions: [], files: ['file']}];
    }
    if (reason === 'api') ctx.api = async () => { throw Error('Failed save'); };
    if (reason === 'refresh') ctx.refreshMenuStructure = async () => { throw Error('Failed refresh'); };
    if (['api', 'refresh'].includes(reason)) {
      await assert.rejects(app.window.BotButtonsEditor.saveInstruction(ctx), /Failed/);
    } else await app.window.BotButtonsEditor.saveInstruction(ctx);
    assert.equal(app.body.children.length, 0);
    assert.equal(app.timers.size, 0);
  });
}

test('CSS keeps the confirmation noninteractive and visible for 500ms before a 500ms fade', () => {
  const css = fs.readFileSync('api/app/static/styles.css', 'utf8');
  const rule = css.split('.save-confirmation {')[1].split('}')[0];
  assert.match(rule, /min-width:\s*10em/);
  assert.match(rule, /align-items:\s*center/);
  assert.match(rule, /justify-content:\s*center/);
  assert.match(rule, /line-height:\s*1\.2/);
  assert.match(rule, /position:\s*fixed/);
  assert.match(rule, /pointer-events:\s*none/);
  assert.match(rule, /translate\(-50%, -50%\)/);
  assert.match(rule, /font-size:\s*1\.5em/);
  assert.match(rule, /animation:\s*save-confirmation-fade 1000ms linear forwards/);
  assert.match(css, /@keyframes save-confirmation-fade\s*\{\s*0%, 50%\s*\{\s*opacity: 1;\s*\}\s*100%\s*\{\s*opacity: 0;/);
  const view = fs.readFileSync('api/app/static/bot_buttons_editor_view.js', 'utf8');
  assert.match(view, /class="panel settings-panel bot-instruction-editor"/);
});
