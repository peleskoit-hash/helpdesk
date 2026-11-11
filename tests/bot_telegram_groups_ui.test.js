const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

let browser;
test.before(async () => { browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });

async function setup(t) {
  const page = await browser.newPage();
  t.after(() => page.close());
  await page.setContent('<main><div id="widget"></div></main>');
  await page.addScriptTag({ content: fs.readFileSync('api/app/static/bot_telegram_groups.js', 'utf8') });
  await page.addScriptTag({ content: fs.readFileSync('api/app/static/bot_buttons_editor.js', 'utf8') });
  await page.evaluate(() => {
    window.errors = [];
    window.calls = [];
    window.HelpdeskDialogs = { alert: async message => window.errors.push(message), confirm: async () => true };
    const verified = {id: 'ok', title: '<b>Тестовая группа</b>', chat_id: '-100', status: 'verified'};
    const unavailable = {id: 'bad', title: 'Нет доступа', chat_id: '-200', status: 'unavailable'};
    window.groups = [verified, unavailable];
    window.ctx = {
      state: {automationTypeValue: 'telegram_group', telegramGroupId: '', telegramGroups: [],
        selectedLeaf: {technical_key: 'Menu|auto|Request', full_path: 'Request', label: 'Request', workflow_type: 'auto'},
        selectedMenu: 'Menu', editorWorkflowType: 'auto', editorBotLabel: 'Request', selectedFiles: [], conditionalRules: []},
      api: async (url, options = {}) => {
        window.calls.push({url, options});
        if (window.fail) throw Error('API failure');
        if (url.includes('/instruction?')) return {button_key: 'Menu|auto|Request'};
        if (url.endsWith('/registration-code')) return {command: '/register_group@dev_bot ONE_TIME_CODE', expires_at: '2099-01-01T00:10:00Z'};
        if (url.endsWith('/check')) { window.groups[0].status = 'unavailable'; return window.groups[0]; }
        return {items: window.groups};
      },
      setStatus: message => { window.status = message; }, refreshMenuStructure: async () => {},
    };
    const escape = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;');
    window.BotButtonsEditorView = {renderEditor(ctx) {
      document.querySelector('#widget').innerHTML = window.BotTelegramGroups.html(ctx, escape);
      window.BotTelegramGroups.bind(ctx);
    }};
    window.render = () => window.BotButtonsEditorView.renderEditor(window.ctx);
  });
  await page.evaluate(async () => { await window.BotTelegramGroups.load(window.ctx); window.render(); });
  return page;
}

test('group selector escapes titles, disables unverified groups and retains selection on redraw', async t => {
  const page = await setup(t);
  assert.match(await page.locator('#widget').textContent(), /После подтверждённой отправки/);
  assert.doesNotMatch(await page.locator('#widget').textContent(), /пока не реализована/);
  assert.equal(await page.locator('#botTelegramGroup option[value="bad"]').isDisabled(), true);
  assert.equal(await page.locator('#botTelegramGroup b').count(), 0);
  assert.match(await page.locator('#botTelegramGroup option[value="ok"]').textContent(), /<b>Тестовая группа<\/b>/);
  await page.locator('#botTelegramGroup').selectOption('ok');
  assert.equal(await page.locator('#botTelegramGroup').inputValue(), 'ok');
  assert.equal(await page.locator('#botTelegramGroupCheck').isEnabled(), true);
  await page.locator('#botTelegramGroupCheck').click();
  assert.equal(await page.locator('#botTelegramGroup option[value="ok"]').isDisabled(), true);
  assert.equal(await page.locator('#botTelegramGroup').inputValue(), 'ok');
});

test('create code shows command and expiry; refresh does not create another code or stack handlers', async t => {
  const page = await setup(t);
  await page.locator('#botTelegramGroupAdd').click();
  assert.equal(await page.locator('#widget code').textContent(), '/register_group@dev_bot ONE_TIME_CODE');
  assert.match(await page.locator('#widget').textContent(), /2099-01-01T00:10:00Z/);
  await page.evaluate(() => { window.render(); window.render(); });
  await page.locator('#botTelegramGroupRefresh').click();
  assert.equal(await page.evaluate(() => window.calls.filter(call => call.url.endsWith('/registration-code')).length), 1);
  assert.equal(await page.evaluate(() => window.calls.filter(call => call.url.endsWith('/telegram-groups')).length), 2);
});

test('API failure does not create a command or leave button disabled', async t => {
  const page = await setup(t);
  await page.evaluate(() => { window.fail = true; });
  await page.locator('#botTelegramGroupAdd').click();
  assert.equal(await page.locator('#widget code').count(), 0);
  assert.equal(await page.locator('#botTelegramGroupAdd').isEnabled(), true);
  assert.match(await page.evaluate(() => window.status), /Не удалось/);
});

test('save rejects missing or unverified group; verified group is sent with the button settings', async t => {
  const page = await setup(t);
  for (const id of ['', 'bad']) {
    await page.evaluate(async id => {
      window.ctx.state.telegramGroupId = id;
      await window.BotButtonsEditor.saveInstruction(window.ctx);
    }, id);
  }
  assert.equal(await page.evaluate(() => window.errors.length), 2);
  assert.equal(await page.evaluate(() => window.calls.filter(call => call.options.method === 'PUT').length), 0);
  await page.evaluate(async () => {
    window.ctx.state.telegramGroupId = 'ok';
    await window.BotButtonsEditor.saveInstruction(window.ctx);
  });
  const saved = await page.evaluate(() => JSON.parse(window.calls.find(call => call.options.method === 'PUT').options.body));
  assert.equal(saved.automation_type, 'telegram_group');
  assert.equal(saved.telegram_group_id, 'ok');
  assert.equal(await page.evaluate(() => window.ctx.state.savedTelegramGroupId), 'ok');
});

test('group widget is absent for existing automation types', async t => {
  const page = await setup(t);
  for (const type of ['none', 'send_files', 'message_only', 'bitrix_deal_phone_lookup']) {
    await page.evaluate(type => { window.ctx.state.automationTypeValue = type; window.render(); }, type);
    assert.equal(await page.locator('#widget').textContent(), '');
  }
});

test('client origin automation is selectable only for auto buttons', async t => {
  const page = await setup(t);
  assert.deepEqual(await page.evaluate(() => ['task', 'ticket', 'auto'].map(type =>
    window.BotButtonsEditor.isAutomationAllowedForWorkflowType(type, 'client_origin'))), [false, false, true]);
});

test('phone to deal lookup is selectable and saved through the existing button editor', async t => {
  const page = await setup(t);
  assert.deepEqual(await page.evaluate(() => ['task', 'ticket', 'auto'].map(type =>
    window.BotButtonsEditor.isAutomationAllowedForWorkflowType(type, 'bitrix_phone_deal_lookup'))), [false, false, true]);
  await page.evaluate(async () => {
    window.ctx.state.automationTypeValue = 'bitrix_phone_deal_lookup';
    window.ctx.state.editorWorkflowType = 'auto';
    await window.BotButtonsEditor.saveInstruction(window.ctx);
  });
  const saved = await page.evaluate(() => window.calls.find(call => call.options.method === 'PUT'));
  assert.equal(JSON.parse(saved.options.body).automation_type, 'bitrix_phone_deal_lookup');
  assert.deepEqual(await page.evaluate(() => window.errors), []);
});

for (const key of ['bitrix_deal_phone_lookup', 'bitrix_phone_deal_lookup', 'client_origin', 'telegram_group']) {
  test(`${key}: real editor options follow task/auto switch and preserve legacy settings until explicit save`, async t => {
    const page = await setup(t);
    await page.addScriptTag({content: fs.readFileSync('api/app/static/bot_buttons_editor_view.js', 'utf8')});
    await page.evaluate(key => {
      const ctx = window.ctx;
      window.HelpdeskDialogs.confirm = async () => true;
      Object.assign(ctx.state, {editorWorkflowType: 'task', automationTypeValue: key, telegramGroupId: 'ok',
        availableAutomations: [
          {key: 'none', title: 'Нет', workflow_types: ['task', 'auto']},
          {key: 'send_files', title: 'Файлы', workflow_types: ['task', 'auto']},
          ...['bitrix_deal_phone_lookup', 'bitrix_phone_deal_lookup', 'client_origin', 'telegram_group'].map(key => ({key, title: key, workflow_types: ['auto']})),
        ]});
      ctx.state.selectedLeaf.workflow_type = 'task';
      ctx.state.selectedLeaf.technical_key = 'Menu|task|Request';
      Object.assign(ctx, {el: {content: document.querySelector('#widget')}, escapeHtml: String,
        renderAssigneeOptions: () => '', renderSelectedFiles: () => '',
        renderConditionalRules: () => '',
        render: () => window.BotButtonsEditorView.renderEditor(ctx),
        saveInstruction: () => window.BotButtonsEditor.saveInstruction(ctx)});
      ctx.render();
    }, key);
    assert.equal(await page.locator(`#automationKeySelect option[value="${key}"]`).count(), 0);
    await page.evaluate(() => window.BotButtonsEditor.saveInstruction(window.ctx));
    assert.equal(await page.evaluate(() => window.calls.filter(call => call.options.method === 'PUT').length), 0);
    assert.equal(await page.evaluate(() => window.ctx.state.automationTypeValue), key);
    await page.selectOption('#editorWorkflowTypeSelect', 'auto');
    assert.equal(await page.locator(`#automationKeySelect option[value="${key}"]`).count(), 1);
    assert.equal(await page.locator('#automationKeySelect').inputValue(), key);
    await page.evaluate(() => window.BotButtonsEditor.saveInstruction(window.ctx));
    const saved = await page.evaluate(() => window.calls.find(call => call.options.method === 'PUT'));
    assert.equal(JSON.parse(saved.options.body).automation_type, key);
    assert.ok(saved.url.includes('workflow_type=auto'));
    await page.selectOption('#editorWorkflowTypeSelect', 'task');
    assert.equal(await page.locator(`#automationKeySelect option[value="${key}"]`).count(), 0);
    assert.equal(await page.evaluate(() => window.ctx.state.automationTypeValue), 'none');
  });
}

test('direct and shell loading agree; every shell HTML entrypoint has the new shell version', () => {
  const version = '20261111-status-source-v1';
  const template = fs.readFileSync('api/app/templates/settings_bot_buttons.html', 'utf8');
  const shell = fs.readFileSync('api/app/static/app_shell.js', 'utf8');
  for (const file of ['bot_telegram_groups.js', 'bot_buttons_editor.js', 'bot_buttons_editor_view.js']) {
    const resourceVersion = file === 'bot_buttons_editor_view.js' ? '20261027-dialog-prompts-v4' : file === 'bot_telegram_groups.js' ? '20261021-group-copy' : '20261027-themed-dialogs-v1';
    assert.ok(template.includes(`/static/${file}?v=${resourceVersion}`), file);
    assert.ok(shell.includes(`/static/${file}?v=${resourceVersion}`), file);
  }
  for (const file of fs.readdirSync('api/app/templates').filter(name => name.endsWith('.html'))) {
    const source = fs.readFileSync(`api/app/templates/${file}`, 'utf8');
    const matches = [...source.matchAll(/\/static\/app_shell\.js\?v=([^"\s]+)/g)].map(match => match[1]);
    if (matches.length) assert.deepEqual([...new Set(matches)], [version], file);
  }
});
