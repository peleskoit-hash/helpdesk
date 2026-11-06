const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const read = name => fs.readFileSync(`api/app/static/${name}.js`, 'utf8');
function functionBody(file, name, next) {
  const source = read(file);
  const start = source.indexOf(`async function ${name}(`);
  const end = source.indexOf(next, start);
  assert.ok(start >= 0 && end > start, `${file}: ${name}`);
  return source.slice(start, end);
}
function buttonHandler(file, id, indent) {
  const source = read(file);
  const marker = `document.getElementById("${id}")?.addEventListener("click", async () => {`;
  const start = source.indexOf(marker);
  const end = source.indexOf(`\n${indent}});`, start);
  assert.ok(start >= 0 && end > start, `${file}: ${id}`);
  return `async function action() {${source.slice(start + marker.length, end)}\n}`;
}
const handlers = [
  ['settings_users', 'saveUser', 'const handlePageKeydown'],
  ['management_users', 'saveClientProfile', 'async function toggleClientBlock'],
  ['settings_client_card', 'saveSourceFromModal', 'async function testSourceFromModal'],
  ['settings_client_card', 'saveRuleFromModal', 'async function toggleRule'],
  ['settings_client_card', 'saveFieldFromModal', 'function syncCreateFieldOptionsVisibility'],
  ['ticket_client_card', 'saveClientProfile', 'function fieldValueForEditor'],
  ['list', 'saveColumnsModal', 'function ticketCellHtml'],
  ['management_tasks', 'saveTask', 'function confirmAction'],
  ['management_saved_files', 'renameSelected', 'async function deleteSelected'],
  ['ticket_message_view', 'editAgentMessage', 'async function deleteAgentMessage'],
  ['settings_bot_buttons', 'saveSystemFirstConnectionBtn', '  '],
  ['settings_bot_buttons', 'saveSystemButtonGroupBtn', '  '],
  ['bot_buttons_level_view', 'saveLayoutBtn', '    '],
];

for (const [file, name, next] of handlers) {
  for (const failure of [null, 'api', 'reload', 'validation']) {
    test(`${file}/${name}: ${failure || 'success'}`, async () => {
      const events = [], panel = {};
      const expectedSelector = {
        settings_users: '.panel', management_users: '.panel.management-users-table',
        settings_client_card: '.panel.client-card-work-panel', ticket_client_card: '.panel.ticket-client-side',
        list: '.panel', management_tasks: '.panel.management-tasks-panel',
        management_saved_files: '.panel.management-saved-files-panel',
        ticket_message_view: '.panel.ticket-card', settings_bot_buttons: '.panel.bot-buttons-panel',
        bot_buttons_level_view: '.panel.bot-buttons-panel',
      }[file];
      const anchor = {closest(selector) { assert.equal(selector, expectedSelector); return panel; }};
      const root = {querySelector(selector) { assert.equal(selector, expectedSelector); return panel; }};
      const api = async () => { events.push('api'); if (failure === 'api') throw Error('api'); return {id: 'file'}; };
      const reload = async () => { events.push('reload'); if (failure === 'reload') throw Error('reload'); };
      const showSaved = (target, options) => {
        assert.equal(target, panel);
        if (file === 'ticket_client_card') {
          assert.equal(options.position, 'top');
          assert.equal(options.scrollContainer, el.clientDynamicCard);
          assert.equal(options.anchor, el.editClientBtn);
        } else assert.equal(options, undefined);
        events.push('saved');
      };
      const state = {editingUserId: 'user', selectedClientId: 'client', editingSourceId: 'source',
        editingRuleId: 'rule', editingFieldId: 'field', editingDefinitionId: 'task', editingKind: 'recurring',
        ticketId: 'ticket', draftTicketListColumns: [], selectedMenu: 'menu', layoutDraftRows: [], levelStack: [],
        savedSystemFirstConnectionDraft: {subject: 'Old', systemText: '', instructionText: ''},
        systemButtonGroup: {buttons: [{key: 'key', text: 'Text'}]}, items: []};
      const el = {usersTableBody: anchor, clientCardSettings: anchor, clientDynamicCard: anchor,
        ticketsTable: anchor, TableBody: anchor, messages: anchor, content: anchor, editClientBtn: {},
        RenameInput: {value: 'Name'}, RenameSaveBtn: {}, RenameError: {},
        editFieldType: {value: 'text'}, editFieldLabel: {value: 'Label'}, editFieldVisible: {},
        editFieldVisibleInEditor: {}, editFieldShowWhenFalse: {}, editFieldAdminOnlyEdit: {},
        editFieldEditable: {}, editFieldRequired: {}, saveBtn: {}, timing: {value: 'recurring'}};
      const ctx = {state, el, api, setStatus() {}, loadCard: reload, isDestroyed: () => false,
        refreshMenuStructure: reload, render() {}, showError() {events.push('error');}};
      const sandbox = {state, el, ctx, root, global: {HelpdeskAppShell: {showSaved}},
        window: {HelpdeskDialogs: {alert: async () => {}}, HelpdeskAppShell: {showSaved}, TicketClientCard: {getModalPayload: () => ({})}},
        api, apiWrite: api, fetchJson: api, API: '/files',
        modalPayload: () => ({full_name: 'Name', login: 'login'}),
        sourcePayloadFromModal: () => ({}), rulePayloadFromModal: () => ({}),
        getModalPayload: () => ({}), clientCardCtx: () => ctx, findFieldById: () => ({}),
        loadUsers: reload, loadClientCardConfig: reload, loadTaskPanel: reload, loadData: reload,
        loadCard: reload, loadSystemFirstConnectionEditorState: reload, loadSystemButtonGroupEditorState: reload,
        closeUserModal() {}, closeClientModal() {}, closeSourceModal() {}, closeRuleModal() {},
        closeEditFieldModal() {}, closeColumnsModal() {}, closeModal() {}, closeRenameModal() {},
        renderTable() {}, render() {
          if (file === 'management_saved_files') {
            events.push('reload');
            if (failure === 'reload') throw Error('reload');
          }
        }, setStatus() {}, setFormError() {},
        showError() {events.push('error');},
        normalizeTicketColumns: value => value || [], canUseTaskPanel: () => false,
        els: () => el, validateDailyTimes: () => true, collectPayload: () => ({}),
        selectedItem: () => ({id: 'file'}),
        currentLevel: () => ({levelKey: 'level'}), serializeLayoutRows: () => [],
        cancelLayoutEdit(state) { state.layoutEditMode = false; state.layoutDraftRows = []; },
        draft: {subject: 'New', systemText: '', instructionText: ''}, group: {endpoint: '/group', key: 'key'},
        uiText: {}, openEditMessageModal: async () => 'Edited', alert() {},
      };
      if (failure === 'validation') {
        if (file === 'settings_users') sandbox.modalPayload = () => ({});
        if (file === 'management_users') state.selectedClientId = '';
        if (name === 'saveSourceFromModal') sandbox.sourcePayloadFromModal = () => {throw Error('validation');};
        if (name === 'saveRuleFromModal') sandbox.rulePayloadFromModal = () => {throw Error('validation');};
        if (name === 'saveFieldFromModal') state.editingFieldId = '';
        if (file === 'ticket_client_card') {
          ctx.inlineClient = true;
          sandbox.inlineEditorState = () => ({open: false});
        }
        if (file === 'list') sandbox.normalizeTicketColumns = () => {throw Error('validation');};
        if (file === 'management_tasks') sandbox.validateDailyTimes = () => false;
        if (file === 'management_saved_files') sandbox.selectedItem = () => null;
        if (file === 'ticket_message_view') sandbox.openEditMessageModal = async () => null;
        if (name === 'saveSystemFirstConnectionBtn') sandbox.draft.subject = 'Old';
        if (name === 'saveSystemButtonGroupBtn') state.systemButtonGroup.buttons[0].text = '';
        if (name === 'saveLayoutBtn') sandbox.currentLevel = () => null;
      }
      vm.createContext(sandbox);
      const dynamic = name.endsWith('Btn');
      vm.runInContext(dynamic ? buttonHandler(file, name, next) : functionBody(file, name, next), sandbox);
      const args = file === 'ticket_message_view' ? [ctx, {id: 'message'}]
        : file === 'ticket_client_card' ? [ctx]
        : ['management_users', 'management_tasks'].includes(file) ? [root]
        : file === 'management_saved_files' ? [{preventDefault() {}}] : [];
      try { await sandbox[dynamic ? 'action' : name](...args); }
      catch (error) { if (!failure) throw error; assert.equal(error.message, failure); }
      assert.equal(events.includes('api'), failure !== 'validation');
      if (failure) assert.ok(!events.includes('saved'));
      else {
        assert.equal(events.filter(event => event === 'saved').length, 1);
        if (file !== 'management_saved_files') assert.ok(events.includes('reload'));
      }
    });
  }
}

test('draft, create/delete and already connected button saves have no additional confirmation', () => {
  const list = read('list');
  assert.doesNotMatch(list.slice(list.indexOf('function saveColumnResizeDraft()'), list.indexOf('function cancelColumnResizeDraft()')), /showSaved/);
  const settings = read('settings_client_card');
  assert.doesNotMatch(settings.slice(settings.indexOf('async function flushBlockAutosave('), settings.indexOf('async function moveBlock(')), /showSaved/);
  const bots = read('settings_bot_buttons');
  assert.doesNotMatch(bots.slice(bots.indexOf('async function saveInstruction()'), bots.indexOf('async function saveInstruction()') + 120), /showSaved/);
});

test('changed scripts and shell have matching versions in every direct and dynamic connection', () => {
  const files = [...fs.readdirSync('api/app/templates').filter(name => name.endsWith('.html'))
    .map(name => `api/app/templates/${name}`), 'api/app/static/app_shell.js'];
  const resources = ['settings_users', 'management_users', 'settings_client_card', 'ticket_client_card',
    'list', 'management_tasks', 'management_saved_files', 'settings_bot_buttons', 'bot_buttons_level_view',
    'ticket_message_view', 'app_shell', 'bot_buttons_editor', 'bot_buttons_editor_view'];
  for (const name of resources) {
    const versions = files.flatMap(file => [...fs.readFileSync(file, 'utf8')
      .matchAll(new RegExp(`/static/${name}\\.js\\?v=([^"']+)`, 'g'))].map(match => match[1]));
    assert.ok(versions.length >= 2, name);
    assert.deepEqual([...new Set(versions)],
      [name === 'app_shell' ? '20261106-table-shell-v1' : ['app_shell','bot_buttons_editor_view','bot_buttons_level_view'].includes(name) ? '20261027-dialog-prompts-v4' : ['app_shell','management_users','ticket_client_card','settings_client_card','management_tasks','management_saved_files','ticket_message_view','bot_buttons_editor_view'].includes(name) ? '20261027-dialogs-complete-v3' : name === 'settings_users' ? '20261025-roles-editor-v2' : name === 'list' ? '20261105-column-auto-width-v7' : '20261027-themed-dialogs-v1'], name);
  }
});
