const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser;
test.before(async () => { browser = await chromium.launch({ channel: 'msedge', headless: true }); });
test.after(async () => { await browser?.close(); });

for (const theme of ['light', 'dark']) test(`button editor V2 text cards and overrides: ${theme}`, async t => {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(5000);
  t.after(() => page.close());
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const html = fs.readFileSync('api/app/templates/settings_bot_buttons.html', 'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '').replace(/<link\b[^>]*>/g, '');
  await page.setContent(html);
  await page.locator('body').evaluate((body, theme) => { body.dataset.theme = theme; body.dataset.sidebarReady = '1'; }, theme);
  for (const file of ['styles.css', 'desk-v2.css', 'profile-settings-v2.css', 'bot-buttons-browser-v2.css']) await page.addStyleTag({ content: fs.readFileSync(`api/app/static/${file}`, 'utf8') });
  for (const file of ['bot_buttons_api', 'bot_buttons_files', 'telegram_text_editor', 'bot_buttons_editor', 'bot_buttons_editor_view']) {
    await page.addScriptTag({ content: fs.readFileSync(`api/app/static/${file}.js`, 'utf8') });
  }
  await page.evaluate(() => {
    window.centralOverride = true;
    window.requests = [];
    window.fetch = async (url, options = {}) => {
      requests.push({ url, method: options.method || 'GET', body: options.body && JSON.parse(options.body) });
      let payload;
      if (url.includes('/automatic-messages/')) {
        const key = url.split('/').pop();
        payload = { key, is_overridden: centralOverride, effective_text: `Central ${key}\nLine two\nLine three\nLine four\nLine five` };
      } else if (options.method === 'PUT') {
        Object.assign(window.serverButton, JSON.parse(options.body));
        payload = serverButton;
      } else payload = serverButton;
      return { ok: true, text: async () => JSON.stringify(payload) };
    };
    const state = window.editorState = { selectedMenu: 'menu', availableFiles: [], conditionalFields: [],
      availableAutomations: ['none', 'send_files', 'message_only', 'client_origin'].map(key => ({ key, title: key, workflow_types: ['ticket', 'task', 'auto'] })) };
    const ctx = window.editorContext = { state, el: { content: document.getElementById('botButtonsContent') }, api: BotButtonsApi.api,
      escapeHtml: BotButtonsApi.escapeHtml, setStatus() {}, render: () => BotButtonsEditorView.renderEditor(ctx),
      normalizeConditionalRuleForState: rule => rule, renderSelectedFiles: () => '', renderAssigneeOptions: () => '<option value="agent">Agent</option>',
      restoreRemovedSetting: async () => archiveActions.push(['restore']),
      deleteRemovedSetting: async () => archiveActions.push(['delete']),
      refreshMenuStructure: async () => {},
      saveInstruction: () => BotButtonsEditor.saveInstruction(ctx), fieldMeta: () => null, fieldLabel: value => value };
    document.getElementById('botButtonsBreadcrumb').classList.remove('hidden');
    document.getElementById('botButtonsBreadcrumb').innerHTML = '<button id="saveInstructionBtn">Сохранить</button>';
    window.reopenButton = () => BotButtonsEditor.openEditor(ctx, { technical_key: serverButton.button_key, workflow_type: serverButton.workflow_type, label: 'Button', full_path: 'Internal/Button' });
    window.openButton = async (workflow, automation = 'none') => {
      window.serverButton = { button_key: `menu|${workflow}|Button`, workflow_type: workflow, button_label: 'Button', label_override: 'Button',
        label_overridden: true, currently_present: true, is_enabled: true, instruction_text: 'Instruction\nTwo\nThree\nFour\nFive',
        client_text: 'Individual saved', system_text: 'System', confirmation_text: 'Confirmation saved', confirmation_enabled: false,
        files: [], conditional_files: [], automation_type: automation, automation_key: automation, button_style: 'success' };
      await reopenButton();
    };
  });

  for (const workflow of ['ticket', 'task', 'group_task']) {
    await page.evaluate(workflow => openButton(workflow), workflow);
    const client = page.locator('#editClientTextBtn');
    assert.equal(await client.isDisabled(), true);
    const key = workflow === 'ticket' ? 'telegram.ticket.create.default_client_text' : 'telegram.task.request_text';
    assert.match(await client.innerText(), new RegExp(key.replaceAll('.', '\\.')));
    assert.match(await client.innerText(), /Central/);
    await client.dispatchEvent('click');
    assert.equal(await page.locator('#botTextEditorModal').count(), 0);
    assert.equal(await page.locator('.bot-editor-col-left #confirmationEnabled').count(), 1);
    assert.equal(await page.locator('.bot-editor-col-right #confirmationEnabled').count(), 0);
    assert.equal(await page.getByRole('button', { name: 'Редактировать', exact: true }).count(), 0);
    const geometry = await page.locator('.bot-editor-grid').evaluate(grid => {
      const [left, right] = grid.children;
      const card = right.querySelector('.bot-text-card');
      return { left: left.getBoundingClientRect().right, right: right.getBoundingClientRect().left,
        font: getComputedStyle(card).fontSize, clamp: getComputedStyle(card.querySelector('.bot-text-card-preview')).webkitLineClamp };
    });
    assert.ok(geometry.left < geometry.right);
    assert.equal(geometry.font, '16.8px');
    assert.equal(geometry.clamp, '4');
    await page.locator('#saveInstructionBtn').click();
    assert.equal(await page.evaluate(() => serverButton.client_text), 'Individual saved');
    await page.evaluate(async () => { centralOverride = false; await reopenButton(); });
    assert.equal(await client.isEnabled(), true);
    assert.match(await client.innerText(), /Individual saved/);
    await client.click();
    await page.locator('#botTextEditorTextarea').fill('<b>Individual edited</b>');
    assert.equal(await page.locator('#botTelegramPreview b').innerText(), 'Individual edited');
    const telegramSize = await page.locator('.bot-text-editor-dialog').boundingBox();
    assert.equal(telegramSize.width, 1404);
    assert.equal(telegramSize.height, 976);
    assert.equal(await page.locator('#botTextEditorTextarea').evaluate(el => getComputedStyle(el).fontSize), '16.8px');
    assert.ok(await page.locator('[data-format-action]').count() > 0);
    await page.locator('#botTextEditorApplyBtn').click();
    await page.locator('#saveInstructionBtn').click();
    assert.equal(await page.evaluate(() => serverButton.client_text), '<b>Individual edited</b>');
    await page.evaluate(async () => { centralOverride = true; await reopenButton(); });
    assert.equal(await client.isDisabled(), true);
    await page.locator('#saveInstructionBtn').click();
    assert.equal(await page.evaluate(() => serverButton.client_text), '<b>Individual edited</b>');
    await page.evaluate(async () => { centralOverride = false; await reopenButton(); });
    assert.match(await client.innerText(), /Individual edited/);

    await page.locator('#editConfirmationTextBtn').dispatchEvent('click');
    assert.equal(await page.locator('#botTextEditorModal').count(), 0);
    await page.locator('#confirmationEnabled').check();
    await page.locator('#editConfirmationTextBtn').click();
    assert.equal(await page.locator('#botTextEditorTextarea').inputValue(), 'Confirmation saved');
    await page.locator('#botTextEditorApplyBtn').click();
    await page.locator('#confirmationEnabled').uncheck();
    assert.equal(await page.locator('#editConfirmationTextBtn').isDisabled(), true);
    await page.locator('#saveInstructionBtn').click();
    assert.equal(await page.evaluate(() => serverButton.confirmation_text), 'Confirmation saved');

    await page.locator('#editInstructionBtn').click();
    const plainSize = await page.locator('.bot-text-editor-dialog').boundingBox();
    assert.deepEqual([plainSize.width, plainSize.height], [telegramSize.width, telegramSize.height]);
    assert.equal(await page.locator('#botTelegramPreview, [data-format-action]').count(), 0);
    await page.locator('#botTextEditorTextarea').fill('Unsaved instruction');
    page.once('dialog', dialog => dialog.dismiss());
    await page.locator('#botTextEditorCancelBtn').click();
    assert.equal(await page.locator('#botTextEditorModal').count(), 1);
    page.once('dialog', dialog => dialog.accept());
    await page.locator('#botTextEditorCancelBtn').click();
    assert.equal(await page.locator('#botTextEditorModal').count(), 0);
    assert.match(await page.locator('#editInstructionBtn').innerText(), /Instruction/);
    await page.evaluate(() => { centralOverride = true; });
  }
  await page.evaluate(() => openButton('ticket'));
  page.once('dialog', dialog => dialog.accept());
  await page.locator('#editorWorkflowTypeSelect').selectOption('task');
  assert.match(await page.locator('#editClientTextBtn').innerText(), /telegram.task.request_text/);
  page.once('dialog', dialog => dialog.accept());
  await page.locator('#editorWorkflowTypeSelect').selectOption('auto');
  assert.equal(await page.locator('#editClientTextBtn').isEnabled(), true);
  assert.match(await page.locator('#editClientTextBtn').innerText(), /Individual saved/);
  for (const automation of ['send_files', 'message_only', 'client_origin']) {
    await page.evaluate(automation => openButton('auto', automation), automation);
    assert.equal(await page.locator('#editClientTextBtn').isEnabled(), true);
    assert.match(await page.locator('#editClientTextBtn').innerText(), /Individual saved/);
  }
  await page.evaluate(() => openButton('submenu'));
  assert.equal(await page.locator('.bot-text-card').count(), 1);
  assert.equal(await page.locator('#editClientTextBtn').isEnabled(), true);
  assert.equal(await page.locator('#confirmationEnabled').count(), 0);
  await page.evaluate(() => openButton('group_task_child'));
  assert.equal(await page.locator('.bot-text-card').count(), 2);
  assert.equal(await page.locator('#editClientTextBtn, #editConfirmationTextBtn, #confirmationEnabled').count(), 0);
  // Child-only boolean keeps the same change handler and persistence.
  assert.equal(await page.locator('#suppressClientAnswer').getAttribute('class'), 'desk-toggle');
  await page.locator('#suppressClientAnswer').check();
  await page.locator('#saveInstructionBtn').click();
  assert.equal(await page.evaluate(() => serverButton.suppress_client_answer), true);
  await page.evaluate(() => reopenButton());
  assert.equal(await page.locator('#suppressClientAnswer').isChecked(), true);

  await page.evaluate(() => openButton('ticket'));
  assert.equal(await page.locator('.bot-removed-warning, .bot-removed-actions').count(), 0);
  for (const id of ['buttonIsEnabled', 'fixedAssigneeEnabled', 'freezeOnCreate', 'confirmationEnabled']) {
    const input = page.locator(`#${id}`);
    assert.equal(await input.getAttribute('class'), 'desk-toggle');
    const style = await input.evaluate(el => {
      const toggle = getComputedStyle(el), label = getComputedStyle(el.closest('label'));
      const section = el.closest('.bot-editor-section');
      return { appearance: toggle.appearance, width: toggle.width, height: toggle.height,
        color: label.color, expected: getComputedStyle(section.querySelector('.bot-removed-title')).color };
    });
    assert.equal(style.appearance, 'none');
    assert.equal(style.width, '40px');
    assert.equal(style.height, '24px');
    assert.equal(style.color, style.expected);
  }
  await page.locator('#buttonIsEnabled').uncheck();
  await page.locator('#fixedAssigneeEnabled').check();
  assert.equal(await page.locator('#fixedAssigneeUserId').isEnabled(), true);
  await page.locator('#fixedAssigneeUserId').selectOption('agent');
  await page.locator('#freezeOnCreate').check();
  await page.locator('#confirmationEnabled').check();
  await page.locator('#saveInstructionBtn').click();
  const flags = () => page.evaluate(() => ({ enabled: serverButton.is_enabled,
    fixed: serverButton.fixed_assignee_enabled, assignee: serverButton.fixed_assignee_user_id,
    freeze: serverButton.freeze_on_create, confirmation: serverButton.confirmation_enabled }));
  assert.deepEqual(await flags(), { enabled: false, fixed: true, assignee: 'agent', freeze: true, confirmation: true });
  await page.evaluate(() => reopenButton());
  assert.equal(await page.locator('#buttonIsEnabled').isChecked(), false);
  for (const id of ['fixedAssigneeEnabled', 'freezeOnCreate', 'confirmationEnabled']) assert.equal(await page.locator(`#${id}`).isChecked(), true);
  await page.locator('#fixedAssigneeEnabled').uncheck();
  assert.equal(await page.locator('#fixedAssigneeUserId').isDisabled(), true);
  await page.locator('#freezeOnCreate').uncheck();
  await page.locator('#confirmationEnabled').uncheck();
  await page.locator('#buttonIsEnabled').check();
  await page.locator('#saveInstructionBtn').click();
  assert.deepEqual(await flags(), { enabled: true, fixed: false, assignee: null, freeze: false, confirmation: false });

  // Opening an archived record must work even when only the API carries its archived status.
  await page.evaluate(async () => {
    window.archiveActions = [];
    serverButton.removed = true;
    serverButton.is_archived = true;
    serverButton.currently_present = false;
    await reopenButton();
  });
  assert.equal(await page.locator('.bot-removed-warning').isVisible(), true);
  assert.equal(await page.locator('.bot-removed-actions').isVisible(), true);
  assert.equal(await page.locator('.bot-removed-warning + .bot-removed-actions').count(), 1);
  assert.equal(await page.locator('.bot-removed-actions').evaluate(el => el.nextElementSibling.classList.contains('bot-editor-grid')), true);
  await page.locator('#restoreRemovedBtn').click();
  page.once('dialog', dialog => dialog.accept());
  await page.locator('#deleteRemovedBtn').click();
  assert.equal(await page.locator('#moveTargetSelect, #moveRemovedBtn').count(), 0);
  assert.equal(await page.getByText('Перенести настройки', { exact: true }).count(), 0);
  assert.deepEqual(await page.evaluate(() => archiveActions), [['restore'], ['delete']]);
  await page.evaluate(() => openButton('ticket'));
  assert.equal(await page.locator('.bot-removed-warning, .bot-removed-actions').count(), 0);
  assert.deepEqual(errors, []);
});
