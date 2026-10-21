const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

async function loadPage(personal) {
  const file = personal ? 'profile_notification_bot.js' : 'settings_notification_bot_basic.js';
  const calls = [], confirmations = [], errors = [];
  const panel = {name: 'notification panel'};
  const textarea = {value: 'Edited', addEventListener() {}};
  const pane = {
    querySelector(selector) {
      if (selector === '[data-template-textarea]') return textarea;
      if (selector === '[data-enabled]') return {checked: true, addEventListener() {}};
      return null;
    },
    querySelectorAll() { return []; },
  };
  const root = {
    renders: 0,
    set innerHTML(value) { this.renders++; this.html = value; },
    querySelector(selector) {
      if (selector === '[data-notification-pane]' || selector === '[data-notification-pane="new_assignment"]') return pane;
      return null;
    },
    querySelectorAll() { return []; },
    closest(selector) { assert.equal(selector, '.panel.notification-bot-basic-panel'); return panel; },
  };
  const items = ['new_assignment', 'new_message'].map(type => ({
    type, title: type, template: 'Original', use_base_settings: false,
  }));
  const responsePayload = () => personal ? {items, role: 'agent'} : {items};
  const document = {getElementById: id => id.endsWith('Root') ? root : null};
  let factory, hooks;
  const window = {
    HelpdeskAppShell: {
      registerPage(name, fn) { factory = fn; },
      showSaved(target) {
        confirmations.push({target, renders: root.renders});
        assert.equal(hooks.state.drafts.get('new_assignment').template, 'Edited');
        if (!personal) assert.equal(hooks.state.baselines.get('new_assignment').template, 'Edited');
      },
    },
    HelpdeskSidebar: {init: () => new Promise(() => {})},
    HelpdeskErrorDialog: {show: error => errors.push(error)},
    addEventListener() {}, removeEventListener() {}, confirm: () => true,
  };
  let write = async () => {
    const saved = items.map(item => ({...item, template: 'Edited'}));
    return personal ? {items: saved, role: 'agent'} : saved[0];
  };
  const context = vm.createContext({window, document,
    fetch: async (url, options) => {
      const method = options.method || 'GET';
      calls.push({url, method, body: options.body});
      const payload = method === 'GET' ? responsePayload() : await write();
      return {ok: true, text: async () => JSON.stringify(payload)};
    },
  });
  const source = fs.readFileSync(`api/app/static/${file}`, 'utf8');
  const exposed = personal ? '{save, restoreBase, state, load}' : '{save: saveActive, state, load}';
  const instrumented = source.replace('window.HelpdeskSidebar.init({',
    `window.__notificationPageTest = ${exposed};\n    window.HelpdeskSidebar.init({`);
  assert.notEqual(instrumented, source);
  vm.runInContext(instrumented, context);
  factory();
  hooks = window.__notificationPageTest;
  await hooks.load();
  return {hooks, root, panel, textarea, confirmations, calls, errors, window,
    setWrite(fn) { write = fn; }};
}

for (const personal of [false, true]) {
  const name = personal ? 'personal' : 'basic';
  test(`${name}: saved feedback follows PUT, state update and render`, async () => {
    const page = await loadPage(personal);
    let finish;
    page.setWrite(() => new Promise(resolve => { finish = resolve; }));
    const saving = page.hooks.save();
    assert.equal(page.calls.at(-1).method, 'PUT');
    assert.equal(page.confirmations.length, 0);
    const rendersBeforeResponse = page.root.renders;
    const item = {type: 'new_assignment', title: 'Saved', template: 'Edited', use_base_settings: false};
    finish(personal ? {role: 'agent', items: [item, {...item, type: 'new_message'}]} : item);
    await saving;
    assert.equal(page.confirmations.length, 1);
    assert.equal(page.confirmations[0].target, page.panel);
    assert.ok(page.confirmations[0].renders > rendersBeforeResponse);
  });

  test(`${name}: failed PUT keeps existing error handling and has no saved feedback`, async () => {
    const page = await loadPage(personal);
    page.setWrite(async () => { throw Error('Failed PUT'); });
    await page.hooks.save();
    assert.equal(page.confirmations.length, 0);
    if (personal) assert.equal(page.errors.length, 1);
    else {
      assert.equal(page.hooks.state.drafts.get('new_assignment').isSaving, false);
      assert.equal(page.hooks.state.baselines.get('new_assignment').template, 'Original');
    }
  });

  test(`${name}: validation exit makes no PUT or saved feedback`, async () => {
    const page = await loadPage(personal);
    page.textarea.value = '';
    await page.hooks.save();
    assert.equal(page.calls.filter(call => call.method === 'PUT').length, 0);
    assert.equal(page.confirmations.length, 0);
  });
}

test('personal: quiet autosave still updates state and renders without feedback', async () => {
  const page = await loadPage(true);
  const renders = page.root.renders;
  await page.hooks.save({quiet: true});
  assert.equal(page.calls.at(-1).method, 'PUT');
  assert.equal(page.hooks.state.drafts.get('new_assignment').template, 'Edited');
  assert.ok(page.root.renders > renders);
  assert.equal(page.confirmations.length, 0);
});

test('personal: restore base and cancelling restore show no saved feedback', async () => {
  const page = await loadPage(true);
  await page.hooks.restoreBase();
  assert.equal(page.calls.at(-1).method, 'POST');
  assert.match(page.calls.at(-1).url, /restore-base\/new_assignment$/);
  assert.equal(page.confirmations.length, 0);
  const calls = page.calls.length;
  page.window.confirm = () => false;
  await page.hooks.restoreBase();
  assert.equal(page.calls.length, calls);
  assert.equal(page.confirmations.length, 0);
});
