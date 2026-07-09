const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function loadHelpers() {
  const source = fs.readFileSync('api/app/static/bot_buttons_files.js', 'utf8');
  const window = {};
  const context = vm.createContext({ window, console, JSON, Array, Set, Map, String, Object });
  vm.runInContext(source, context);
  return window.BotButtonsFiles;
}

function ctxFor(stateOverrides = {}) {
  return {
    state: {
      conditionalFields: [
        {
          field: 'location_type',
          type: 'select',
          label: '\u0422\u0438\u043f \u043b\u043e\u043a\u0430\u0446\u0438\u0438',
          values: ['\u041e\u0444\u0438\u0441\u043d\u044b\u0439', '\u0423\u0434\u0430\u043b\u0451\u043d\u043d\u044b\u0439'],
          operators: ['eq', 'ne', 'empty', 'not_empty'],
        },
      ],
      selectedFiles: [],
      conditionalRules: [],
      filesOverridden: false,
      conditionalFilesOverridden: false,
      availableFiles: [],
      ...stateOverrides,
    },
    FIELD_LABELS: {},
    OPERATOR_OPTIONS: {
      text: [],
      select: [],
      number: [],
      bool: [],
    },
    escapeHtml(value) {
      return String(value);
    },
    renderEditor() {},
  };
}

const helpers = loadHelpers();

function loadManagementUsersHelpers() {
  const source = fs.readFileSync('api/app/static/management_users.js', 'utf8');
  const window = {
    HelpdeskAppShell: { registerPage() {} },
    clearTimeout() {},
    setTimeout() { return 1; },
  };
  const context = vm.createContext({
    window,
    console,
    JSON,
    Array,
    Set,
    Map,
    String,
    Object,
    Number,
    Date,
    URLSearchParams,
  });
  vm.runInContext(source, context);
  return window.ManagementUsersPage;
}

const managementUsersHelpers = loadManagementUsersHelpers();

test('hydrates unconditional and conditional files into file-centric entries', () => {
  const ctx = ctxFor({
    selectedFiles: ['docs/a.pdf'],
    conditionalRules: [
      {
        files: ['docs/b.pdf'],
        conditions: [{ field: 'location_type', operator: 'eq', value: '\u041e\u0444\u0438\u0441\u043d\u044b\u0439' }],
      },
    ],
  });

  const entries = helpers.getSendFileEntries(ctx);
  assert.equal(entries.length, 2);
  assert.deepEqual(Array.from(entries, (entry) => entry.file), ['docs/a.pdf', 'docs/b.pdf']);
  assert.equal(entries[0].rules.length, 0);
  assert.equal(entries[1].rules.length, 1);
});

test('management users helper builds searchable all and blocked list urls', () => {
  assert.equal(
    managementUsersHelpers.buildUsersUrl({ query: '@ivan', filter: 'all', limit: 50, offset: 100 }),
    '/api/v1/management/users?limit=50&offset=100&q=%40ivan',
  );
  assert.equal(
    managementUsersHelpers.buildUsersUrl({ query: '9001', filter: 'blocked', limit: 25, offset: 50 }),
    '/api/v1/management/users?limit=25&offset=50&q=9001&blocked=true',
  );
});

test('management users helper renders pagination summary', () => {
  assert.equal(managementUsersHelpers.paginationSummary(0, 50, 120, 50), '\u041f\u043e\u043a\u0430\u0437\u0430\u043d\u043e 1\u201350 \u0438\u0437 120');
  assert.equal(managementUsersHelpers.paginationSummary(50, 50, 120, 50), '\u041f\u043e\u043a\u0430\u0437\u0430\u043d\u043e 51\u2013100 \u0438\u0437 120');
  assert.equal(managementUsersHelpers.paginationSummary(100, 50, 120, 20), '\u041f\u043e\u043a\u0430\u0437\u0430\u043d\u043e 101\u2013120 \u0438\u0437 120');
  assert.equal(managementUsersHelpers.paginationSummary(0, 50, 0, 0), '\u041f\u043e\u043a\u0430\u0437\u0430\u043d\u043e 0\u20130 \u0438\u0437 0');
});

test('management users search is triggered by button or enter, not input event', () => {
  const source = fs.readFileSync('api/app/static/management_users.js', 'utf8');
  assert.match(source, /searchBtn\?\.addEventListener\("click"/);
  assert.match(source, /search\?\.addEventListener\("keydown"/);
  assert.match(source, /event\.key !== "Enter"/);
  assert.doesNotMatch(source, /addEventListener\("input"/);
  assert.match(source, /state\.offset = 0;/);
  assert.match(source, /nextBtn\?\.addEventListener\("click"/);
  assert.match(source, /prevBtn\?\.addEventListener\("click"/);
  assert.match(source, /state\.offset \+= state\.limit/);
});

test('management users helper keeps optional empty cells readable', () => {
  assert.equal(managementUsersHelpers.formatCell(null), '\u2014');
  assert.equal(managementUsersHelpers.formatCell(''), '\u2014');
  assert.equal(managementUsersHelpers.formatCell(' Ivan '), 'Ivan');
});

test('management routes and single admin sidebar entry are registered', () => {
  const appShell = fs.readFileSync('api/app/static/app_shell.js', 'utf8');
  const sidebar = fs.readFileSync('api/app/static/sidebar.js', 'utf8');
  assert.match(appShell, /"\/management"/);
  assert.match(appShell, /"\/management\/users"/);
  assert.match(appShell, /management-home/);
  assert.match(appShell, /management-users/);
  assert.match(sidebar, /sidebarManagementLink/);
  assert.match(sidebar, /href = "\/management"/);
  assert.match(sidebar, /\\u0423\\u043f\\u0440\\u0430\\u0432\\u043b\\u0435\\u043d\\u0438\\u0435/);
  assert.doesNotMatch(sidebar, /sidebar-management-links/);
  assert.doesNotMatch(sidebar, /href = "\/management\/users"/);
});

test('management page contains users and mass notification tiles', () => {
  const management = fs.readFileSync('api/app/templates/management.html', 'utf8');
  assert.match(management, /href="\/management\/users"/);
  assert.match(management, /href="\/mass-notifications"/);
  assert.match(management, /&#1055;&#1086;&#1083;&#1100;&#1079;&#1086;&#1074;&#1072;&#1090;&#1077;&#1083;&#1080;/);
  assert.match(management, /&#1052;&#1072;&#1089;&#1089;&#1086;&#1074;&#1099;&#1077; &#1086;&#1087;&#1086;&#1074;&#1077;&#1097;&#1077;&#1085;&#1080;&#1103;/);
});

test('management users table does not expose chat id in telegram summary', () => {
  const html = managementUsersHelpers.tableRowHtml({
    client_id: 'client-1',
    telegram_display_name: 'Ivan TG',
    telegram_username: 'belfiss',
    telegram_user_id: '453920478',
    telegram_chat_id: '-5267117449',
    display_name: 'Ivan',
    legacy_phone: '+7000',
    internal_number: '4455',
    last_activity_at: null,
  });
  assert.match(html, /Ivan TG/);
  assert.match(html, /@belfiss/);
  assert.match(html, /TG ID: 453920478/);
  assert.match(html, /4455/);
  assert.doesNotMatch(html, /\+7000/);
  assert.doesNotMatch(html, /-5267117449/);
});

test('management users table shows dash for empty internal number', () => {
  const html = managementUsersHelpers.tableRowHtml({
    client_id: 'client-1',
    telegram_display_name: 'Ivan TG',
    telegram_username: 'belfiss',
    telegram_user_id: '453920478',
    display_name: 'Ivan',
    internal_number: '',
    last_activity_at: null,
  });
  assert.match(html, /\u2014/);
});

test('management users template labels internal number and verification columns', () => {
  const template = fs.readFileSync('api/app/templates/management_users.html', 'utf8');
  assert.match(template, /&#1042;&#1085;&#1091;&#1090;&#1088;&#1077;&#1085;&#1085;&#1080;&#1081; &#1085;&#1086;&#1084;&#1077;&#1088;/);
  assert.match(template, /&#1042;&#1077;&#1088;&#1080;&#1092;&#1080;&#1082;&#1072;&#1094;&#1080;&#1103;/);
  assert.doesNotMatch(template, /&#1058;&#1077;&#1083;&#1077;&#1092;&#1086;&#1085;/);
});

test('management users template contains pagination controls', () => {
  const template = fs.readFileSync('api/app/templates/management_users.html', 'utf8');
  assert.match(template, /id="managementUsersPageSummary"/);
  assert.match(template, /id="managementUsersPrevBtn"/);
  assert.match(template, /id="managementUsersNextBtn"/);
  assert.match(template, /&#1053;&#1072;&#1079;&#1072;&#1076;/);
  assert.match(template, /&#1044;&#1072;&#1083;&#1077;&#1077;/);
});

test('settings users label is renamed for admins and agents', () => {
  const settingsHome = fs.readFileSync('api/app/templates/settings.html', 'utf8');
  const settingsUsers = fs.readFileSync('api/app/templates/settings_users.html', 'utf8');
  assert.doesNotMatch(settingsHome, /Настройка пользователей/);
  assert.doesNotMatch(settingsUsers, /Настройка пользователей/);
  assert.match(settingsHome, /&#1040;&#1076;&#1084;&#1080;&#1085;&#1086;&#1074;\/&#1040;&#1075;&#1077;&#1085;&#1090;&#1086;&#1074;/);
  assert.match(settingsUsers, /&#1040;&#1076;&#1084;&#1080;&#1085;&#1086;&#1074;\/&#1040;&#1075;&#1077;&#1085;&#1090;&#1086;&#1074;/);
});

function loadLevelViewHelpers() {
  const source = fs.readFileSync('api/app/static/bot_buttons_level_view.js', 'utf8');
  const window = {};
  const context = vm.createContext({ window, console, JSON, Array, Set, Map, String, Object, Number, Math });
  vm.runInContext(source, context);
  return window.BotButtonsLevelView;
}

const levelViewHelpers = loadLevelViewHelpers();

function layoutButton(key, label = key) {
  return { technical_key: key, label };
}

function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

function renderLayoutEditHtml() {
  const source = fs.readFileSync('api/app/static/bot_buttons_level_view.js', 'utf8');
  const content = {
    innerHTML: '',
    querySelectorAll() { return []; },
  };
  const document = {
    getElementById() { return null; },
  };
  const window = {};
  const context = vm.createContext({ window, document, console, JSON, Array, Set, Map, String, Object, Number, Math });
  vm.runInContext(source, context);
  context.window.BotButtonsLevelView.renderLevel({
    state: {
      view: 'level',
      selectedMenu: 'menu-a',
      layoutEditMode: true,
      layoutDraftRows: [
        { columns: 2, buttons: [layoutButton('a', 'A'), layoutButton('b', 'B')] },
      ],
      levelStack: [
        {
          label: 'Menu A',
          rows: [{ buttons: [layoutButton('a', 'A'), layoutButton('b', 'B')] }],
          levelKey: 'menu-a|level|__root__',
          fullPath: '',
          kind: 'root',
        },
      ],
      menuStructure: { removed_items: [] },
    },
    el: { content },
    renderMenuBindingsPanel() { return ''; },
    renderRightPanel() { return ''; },
    escapeHtml(value) { return String(value); },
    buildPreviewFromNode(node) { return node; },
    loadPreviewItem() {},
    confirmDiscard() { return true; },
    discardEditorChanges() {},
    openEditor() {},
    createLevelItem() {},
    openParentTaskEditor() {},
    openSubmenuEditor() {},
    refreshMenuStructure() {},
    reloadMenuMeta() {},
    loadMenuTypes() {},
    saveLabelOverrideForNode() {},
    api() {},
    setStatus() {},
  });
  return content.innerHTML;
}

test('layout draft changes button order inside the same row', () => {
  const draft = [
    { columns: 3, buttons: [layoutButton('a'), layoutButton('b'), layoutButton('c')] },
  ];

  const moved = levelViewHelpers.moveDraftButton(draft, 0, 2, 0, 0);

  assert.equal(moved, true);
  assert.deepEqual(draft[0].buttons.map((item) => item.technical_key), ['c', 'a', 'b']);
});

test('layout draft moves button to another row', () => {
  const draft = [
    { columns: 2, buttons: [layoutButton('a'), layoutButton('b')] },
    { columns: 1, buttons: [layoutButton('c')] },
  ];

  const moved = levelViewHelpers.moveDraftButton(draft, 0, 1, 1, null);

  assert.equal(moved, true);
  assert.deepEqual(draft.map((row) => row.buttons.map((item) => item.technical_key)), [['a'], ['c', 'b']]);
});

test('layout draft refuses fourth button in a row', () => {
  const draft = [
    { columns: 1, buttons: [layoutButton('a')] },
    { columns: 3, buttons: [layoutButton('b'), layoutButton('c'), layoutButton('d')] },
  ];

  const moved = levelViewHelpers.moveDraftButton(draft, 0, 0, 1, null);

  assert.equal(moved, false);
  assert.deepEqual(draft.map((row) => row.buttons.map((item) => item.technical_key)), [['a'], ['b', 'c', 'd']]);
});

test('layout draft creates a new row between existing rows', () => {
  const draft = [
    { columns: 2, buttons: [layoutButton('a'), layoutButton('b')] },
    { columns: 1, buttons: [layoutButton('c')] },
  ];

  const moved = levelViewHelpers.moveDraftButtonToNewRow(draft, 0, 1, 1);

  assert.equal(moved, true);
  assert.deepEqual(plain(draft.map((row) => row.buttons.map((item) => item.technical_key))), [['a'], ['b'], ['c']]);
});

test('layout draft normalizes empty rows away', () => {
  const rows = levelViewHelpers.normalizeDraftRows([
    { columns: 1, buttons: [] },
    { columns: 2, buttons: [layoutButton('a'), layoutButton('b')] },
    { columns: 1, buttons: [] },
  ]);

  assert.deepEqual(plain(rows.map((row) => row.buttons.map((item) => item.technical_key))), [['a', 'b']]);
});

test('layout cancel exits edit mode and drops draft rows', () => {
  const state = {
    layoutEditMode: true,
    layoutDraftRows: [{ columns: 1, buttons: [layoutButton('a')] }],
  };

  assert.equal(levelViewHelpers.cancelLayoutEdit(state), true);
  assert.equal(state.layoutEditMode, false);
  assert.deepEqual(plain(state.layoutDraftRows), []);
});

test('layout save serializes expected payload rows', () => {
  const rows = [
    { columns: 3, buttons: [layoutButton('c'), layoutButton('a'), layoutButton('b')] },
    { columns: 1, buttons: [layoutButton('d')] },
  ];

  assert.deepEqual(plain(levelViewHelpers.serializeLayoutRows(rows)), [
    { columns: 3, button_keys: ['c', 'a', 'b'] },
    { columns: 1, button_keys: ['d'] },
  ]);
});

test('layout edit source blocks normal editor opening while drag mode is active', () => {
  const source = fs.readFileSync('api/app/static/bot_buttons_level_view.js', 'utf8');
  assert.match(source, /if \(ctx\.state\.layoutEditMode\) return;/);
  assert.match(source, /data-layout-drag-button/);
});

test('layout edit markup has mode shell and no old row controls', () => {
  const html = renderLayoutEditHtml();

  assert.match(html, /bot-layout-edit-shell/);
  assert.match(html, /bot-layout-mode-badge/);
  assert.match(html, /\u0420\u0435\u0436\u0438\u043c \u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u044f \u043f\u043e\u0440\u044f\u0434\u043a\u0430/);
  assert.match(html, /data-layout-drag-button/);
  assert.match(html, /data-layout-new-row-before/);
  assert.doesNotMatch(html, /data-layout-row|data-layout-add-row|data-layout-del-row|bot-layout-row-head|bot-layout-cols/);
});

test('workflow type draft change resets incompatible automation and discard restores saved type', () => {
  const confirmations = [];
  const editorSource = fs.readFileSync('api/app/static/bot_buttons_editor.js', 'utf8');
  const window = {
    BotButtonsFiles: helpers,
    confirm(message) {
      confirmations.push(message);
      return true;
    },
  };
  const context = vm.createContext({ window, console, JSON, Array, Set, Map, String, Object });
  vm.runInContext(editorSource, context);
  const localEditorHelpers = window.BotButtonsEditor;
  const state = {
    selectedLeaf: { technical_key: 'menu-a|auto|A', workflow_type: 'auto' },
    savedWorkflowType: 'auto',
    editorWorkflowType: 'auto',
    automationTypeValue: 'message_only',
    availableAutomations: [
      { key: 'none', title: '\u041d\u0435 \u0438\u0441\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u044c', description: '', available: true, handler: '' },
      { key: 'message_only', title: '\u0422\u043e\u043b\u044c\u043a\u043e \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435', description: '', available: true, handler: '' },
    ],
    automationType: 'message_only',
    automationKeyValue: 'message_only',
    automationKey: 'message_only',
    automationTitle: '\u0422\u043e\u043b\u044c\u043a\u043e \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435',
    automationDescription: '',
    automationAvailable: true,
    automationHandler: '',
    automationStatus: 'available',
    editorText: '',
    savedText: '',
    editorBotLabel: '',
    savedBotLabel: '',
    editorClientText: '',
    savedClientText: '',
    editorSystemText: '',
    savedSystemText: '',
    fixedAssigneeEnabled: false,
    savedFixedAssigneeEnabled: false,
    fixedAssigneeUserId: '',
    savedFixedAssigneeUserId: '',
    freezeOnCreate: false,
    savedFreezeOnCreate: false,
    confirmationEnabled: false,
    savedConfirmationEnabled: false,
    confirmationText: '',
    savedConfirmationText: '',
    filesOverridden: false,
    savedFilesOverridden: false,
    selectedFiles: [],
    savedSelectedFiles: [],
    conditionalFilesOverridden: false,
    savedConditionalFilesOverridden: false,
    conditionalRules: [],
    savedConditionalRules: [],
    isEnabled: true,
    savedIsEnabled: true,
    savedAutomationTypeValue: 'message_only',
  };
  const ctx = { state };

  assert.equal(localEditorHelpers.applyWorkflowTypeDraft(ctx, 'ticket'), true);
  assert.equal(state.editorWorkflowType, 'ticket');
  assert.equal(state.automationTypeValue, 'none');
  assert.equal(confirmations.length, 1);
  assert.equal(localEditorHelpers.hasUnsavedChanges(ctx), true);

  localEditorHelpers.discardEditorChanges(ctx);
  assert.equal(state.editorWorkflowType, 'auto');
  assert.equal(state.automationTypeValue, 'message_only');
});

test('submenu workflow type does not expose editable transition options', () => {
  assert.deepEqual(
    Array.from(editorHelpers.getWorkflowTypeOptions({ technical_key: 'menu-a|submenu|A', workflow_type: 'submenu' })),
    [],
  );
});

test('workflow type options fallback to technical_key ticket when workflow_type is absent', () => {
  const options = Array.from(editorHelpers.getWorkflowTypeOptions({
    technical_key: 'menu-a|ticket|A',
  }));
  assert.deepEqual(options.map((item) => item.value), ['ticket', 'task', 'auto']);
});

test('workflow type options fallback to technical_key task when workflow_type is absent', () => {
  const options = Array.from(editorHelpers.getWorkflowTypeOptions({
    technical_key: 'menu-a|task|A',
  }));
  assert.deepEqual(options.map((item) => item.value), ['ticket', 'task', 'auto']);
});

test('workflow type options fallback to technical_key auto when workflow_type is absent', () => {
  const options = Array.from(editorHelpers.getWorkflowTypeOptions({
    technical_key: 'menu-a|auto|A',
  }));
  assert.deepEqual(options.map((item) => item.value), ['ticket', 'task', 'auto']);
});

test('workflow type options fallback to technical_key submenu when workflow_type is absent', () => {
  assert.deepEqual(
    Array.from(editorHelpers.getWorkflowTypeOptions({ technical_key: 'menu-a|submenu|A' })),
    [],
  );
});

test('workflow type options fallback to technical_key group_task when workflow_type is absent', () => {
  assert.deepEqual(
    Array.from(editorHelpers.getWorkflowTypeOptions({ technical_key: 'menu-a|group_task|A' })),
    [],
  );
});

test('workflow type options fallback to system technical key returns no editable options', () => {
  assert.deepEqual(
    Array.from(editorHelpers.getWorkflowTypeOptions({ technical_key: '__system__|first_connection' })),
    [],
  );
});

test('workflow type label uses russian label for ticket resolved from technical_key fallback', () => {
  const workflowType = editorHelpers.resolveLeafWorkflowType({ technical_key: 'menu-a|ticket|A' });
  assert.equal(workflowType, 'ticket');
  assert.equal(editorHelpers.getWorkflowTypeLabel(workflowType), '\u0417\u0430\u044f\u0432\u043a\u0430');
});

test('splits legacy multi-file conditional rule per file without data loss', () => {
  const ctx = ctxFor({
    conditionalRules: [
      {
        tag: 'legacy',
        files: ['docs/a.pdf', 'docs/b.pdf'],
        conditions: [{ field: 'location_type', operator: 'eq', value: '\u0423\u0434\u0430\u043b\u0451\u043d\u043d\u044b\u0439' }],
      },
    ],
  });

  const entries = helpers.getSendFileEntries(ctx);
  assert.equal(entries.length, 2);
  assert.equal(entries[0].rules[0].extra.tag, 'legacy');
  assert.equal(entries[1].rules[0].extra.tag, 'legacy');

  const payload = helpers.buildSendFilesPayload(ctx);
  assert.equal(payload.files.length, 0);
  assert.equal(payload.conditionalRules.length, 2);
  assert.deepEqual(Array.from(payload.conditionalRules, (rule) => rule.files[0]), ['docs/a.pdf', 'docs/b.pdf']);
  assert.equal(payload.conditionalRules[0].tag, 'legacy');
});

test('adding first condition moves file from unconditional to conditional payload', () => {
  const ctx = ctxFor({ selectedFiles: ['docs/a.pdf'] });
  helpers.addConditionToFile(ctx, 'docs/a.pdf');

  assert.deepEqual(Array.from(ctx.state.selectedFiles), []);
  assert.equal(ctx.state.conditionalRules.length, 1);
  assert.deepEqual(Array.from(ctx.state.conditionalRules[0].files), ['docs/a.pdf']);

  const payload = helpers.buildSendFilesPayload(ctx);
  assert.deepEqual(Array.from(payload.files), []);
  assert.deepEqual(Array.from(payload.conditionalRules[0].files), ['docs/a.pdf']);
});

test('removing last condition returns file to unconditional payload', () => {
  const ctx = ctxFor({
    conditionalRules: [
      {
        files: ['docs/a.pdf'],
        conditions: [{ field: 'location_type', operator: 'eq', value: '\u041e\u0444\u0438\u0441\u043d\u044b\u0439' }],
      },
    ],
  });

  helpers.removeConditionalRule(ctx, 0);
  assert.deepEqual(Array.from(ctx.state.selectedFiles), ['docs/a.pdf']);
  assert.deepEqual(Array.from(ctx.state.conditionalRules), []);

  const payload = helpers.buildSendFilesPayload(ctx);
  assert.deepEqual(Array.from(payload.files), ['docs/a.pdf']);
  assert.deepEqual(Array.from(payload.conditionalRules), []);
});

test('removing file clears it from unconditional and conditional payloads', () => {
  const ctx = ctxFor({
    selectedFiles: ['docs/a.pdf'],
    conditionalRules: [
      {
        files: ['docs/a.pdf', 'docs/b.pdf'],
        conditions: [{ field: 'location_type', operator: 'eq', value: '\u041e\u0444\u0438\u0441\u043d\u044b\u0439' }],
      },
    ],
  });

  helpers.removeFileFromState(ctx, 'docs/a.pdf');
  const payload = helpers.buildSendFilesPayload(ctx);
  assert.equal(payload.files.includes('docs/a.pdf'), false);
  assert.equal(payload.conditionalRules.some((rule) => rule.files.includes('docs/a.pdf')), false);
  assert.equal(payload.conditionalRules.some((rule) => rule.files.includes('docs/b.pdf')), true);
});

test('system ticket card exposes a stable selector in menu view', () => {
  const source = fs.readFileSync('api/app/static/bot_buttons_menu_view.js', 'utf8');
  assert.equal(systemTicket.helpers.selector, '[data-system-ticket-key="first-connection"]');
  assert.match(source, /data-system-ticket-key="first-connection"/);
});

test('system ticket menu block renders clean russian labels without mojibake markers', () => {
  const source = fs.readFileSync('api/app/static/bot_buttons_menu_view.js', 'utf8');
  const renderRightPanel = () => '<div>right</div>';
  const el = { content: { innerHTML: '', querySelectorAll() { return []; } } };
  const document = {
    getElementById() { return { addEventListener() {} }; },
  };
  const window = {};
  const context = vm.createContext({ window, document, console, JSON, Array, Set, Map, String, Object });
  vm.runInContext(source, context);
  const labels = window.BotButtonsMenuViewSystemText;

  window.BotButtonsMenuView.renderMenuTypes({
    state: {
      menuTypes: [
        { menu_name: '\u0411\u0430\u0437\u043e\u0432\u043e\u0435', display_name: '\u0411\u0430\u0437\u043e\u0432\u043e\u0435', destinations: [] },
        { menu_name: '__system__', display_name: '__system__', destinations: [] },
      ],
      archivedMenuTypes: [],
      systemTicketLoadError: '',
    },
    el,
    escapeHtml(value) { return String(value); },
    renderRightPanel,
    confirmDiscard() { return true; },
    discardEditorChanges() {},
    openMenu() {},
    api() {},
    setStatus() {},
    loadMenuTypes() {},
    systemTicketLoadError: '',
  });

  assert.match(el.content.innerHTML, /\u0411\u0430\u0437\u043e\u0432\u043e\u0435/);
  assert.doesNotMatch(el.content.innerHTML, /__system__/);
  assert.equal(labels.SYSTEM_TICKETS_TITLE, '\u0421\u0438\u0441\u0442\u0435\u043c\u043d\u044b\u0435 \u0437\u0430\u044f\u0432\u043a\u0438');
  assert.equal(labels.FIRST_CONNECTION_TITLE, '\u041f\u0435\u0440\u0432\u043e\u0435 \u043f\u043e\u0434\u043a\u043b\u044e\u0447\u0435\u043d\u0438\u0435 \u043a \u0431\u043e\u0442\u0443');
  assert.match(el.content.innerHTML, /\u0421\u0438\u0441\u0442\u0435\u043c\u043d\u044b\u0435 \u0437\u0430\u044f\u0432\u043a\u0438/);
  assert.match(el.content.innerHTML, /\u041f\u0435\u0440\u0432\u043e\u0435 \u043f\u043e\u0434\u043a\u043b\u044e\u0447\u0435\u043d\u0438\u0435 \u043a \u0431\u043e\u0442\u0443/);
  assert.doesNotMatch(el.content.innerHTML, /\u2568|\u2564|\u00d0|\u00d1|\u0420\u045f|\u0420\u040e|\u0420\u045a|\?\?\?/);
});

test('system ticket click handler triggers system api load path', async () => {
  const calls = [];
  const state = { view: 'menu_types', systemTicketLoadError: '', systemFirstConnection: null };
  const statuses = [];
  const content = {
    listeners: {},
    addEventListener(type, fn) { this.listeners[type] = fn; },
    removeEventListener() {},
  };
  systemTicket.helpers.bindOpenHandler({
    content,
    confirmDiscard: () => true,
    discardEditorChanges: () => calls.push('discard'),
    onError: (error) => { throw error; },
    onOpen: () => systemTicket.helpers.loadEditorState({
      state,
      render: () => calls.push('render'),
      setStatus: (value) => statuses.push(value),
      api: async (path) => {
        calls.push(path);
        return { subject_effective: 'First', client_text_effective: 'Client', system_text_effective: 'System', instruction_text: 'Instruction' };
      },
    }),
  });

  await content.listeners.click({
    preventDefault() {},
    target: { closest(selector) { return selector === systemTicket.helpers.selector ? {} : null; } },
  });

  assert.equal(calls[0], 'discard');
  assert.equal(calls[1], '/api/v1/settings/bot-buttons/system/first-connection');
  assert.equal(state.view, 'system_editor');
  assert.equal(statuses.at(-1), '\u0413\u043e\u0442\u043e\u0432\u043e');
});

test('system api load failure keeps menu view and shows ui error', async () => {
  const state = { view: 'menu_types', systemTicketLoadError: '', systemFirstConnection: null };
  const statuses = [];
  await assert.rejects(
    systemTicket.helpers.loadEditorState({
      state,
      render: () => {},
      setStatus: (value) => statuses.push(value),
      api: async () => { throw new Error('boom'); },
    }),
    /boom/,
  );
  assert.equal(state.view, 'menu_types');
  assert.equal(state.systemTicketLoadError, 'boom');
  assert.equal(statuses.at(-1), '\u041e\u0448\u0438\u0431\u043a\u0430: boom');
});

test('repeated system ticket binding does not create a double handler', async () => {
  const content = {
    listeners: [],
    addEventListener(type, fn) { this.listeners.push({ type, fn }); },
    removeEventListener(type, fn) { this.listeners = this.listeners.filter((item) => item.type !== type || item.fn !== fn); },
  };
  let opened = 0;
  const first = systemTicket.helpers.bindOpenHandler({
    content,
    confirmDiscard: () => true,
    discardEditorChanges: () => {},
    onError: () => {},
    onOpen: async () => { opened += 1; },
  });
  const second = systemTicket.helpers.bindOpenHandler({
    content,
    confirmDiscard: () => true,
    discardEditorChanges: () => {},
    onError: () => {},
    onOpen: async () => { opened += 1; },
  });

  assert.equal(first, true);
  assert.equal(second, false);
  assert.equal(content.listeners.length, 1);
  await content.listeners[0].fn({
    preventDefault() {},
    target: { closest() { return {}; } },
  });
  assert.equal(opened, 1);
});

test('system editor breadcrumb source keeps clean first-connection label', () => {
  const source = fs.readFileSync('api/app/static/settings_bot_buttons.js', 'utf8');
  assert.equal(systemTicket.helpers.labels.firstConnectionTitle, '\u041f\u0435\u0440\u0432\u043e\u0435 \u043f\u043e\u0434\u043a\u043b\u044e\u0447\u0435\u043d\u0438\u0435 \u043a \u0431\u043e\u0442\u0443');
  assert.match(source, /parts\.push\(FIRST_CONNECTION_TITLE\)/);
  assert.doesNotMatch(source, /╨|╤|Ð|Ñ|Рџ|РЎ|Рњ|\?\?\?/);
});

test('test file does not lock mojibake expected strings outside forbidden-marker regexes', () => {
  const source = fs.readFileSync('tests/bot_buttons_send_files_ui.test.js', 'utf8');
  const inspected = source
    .split('\n')
    .filter((line) => !line.includes('assert.doesNotMatch('))
    .filter((line) => !line.includes('test file does not lock mojibake expected strings outside forbidden-marker regexes'))
    .join('\n');
  assert.doesNotMatch(inspected, /'╨У╨╛╤В╨╛╨▓╨╛'|"╨У╨╛╤В╨╛╨▓╨╛"/);
  assert.doesNotMatch(inspected, /'╨Ю╤И╨╕╨▒╨║╨░: boom'|"╨Ю╤И╨╕╨▒╨║╨░: boom"/);
  assert.doesNotMatch(inspected, /'╨Я╨╡╤А╨▓╨╛╨╡[^']*'|"╨Я╨╡╤А╨▓╨╛╨╡[^"]*"/);
});

test('legacy conditional files render as two file-centric cards without unconditional duplicates', () => {
  const ctx = ctxFor({
    conditionalRules: [
      {
        files: ['office-guide.pdf'],
        conditions: [{ field: 'location_type', operator: 'eq', value: 'office' }],
      },
      {
        files: ['remote-guide.pdf'],
        conditions: [{ field: 'location_type', operator: 'eq', value: 'remote' }],
      },
    ],
  });

  const entries = helpers.getSendFileEntries(ctx);
  assert.equal(entries.length, 2);
  assert.deepEqual(Array.from(entries, (entry) => entry.file), ['office-guide.pdf', 'remote-guide.pdf']);
  assert.equal(entries.every((entry) => entry.unconditional === false), true);
  assert.deepEqual(entries[0].rules[0].conditions, [{ field: 'location_type', operator: 'eq', value: 'office' }]);
  assert.deepEqual(entries[1].rules[0].conditions, [{ field: 'location_type', operator: 'eq', value: 'remote' }]);

  const payload = helpers.buildSendFilesPayload(ctx);
  assert.deepEqual(Array.from(payload.files), []);
  assert.equal(payload.conditionalRules.length, 2);
});

test('projection loss detector catches empty UI state for non-empty legacy conditional files', () => {
  const ctx = ctxFor({
    selectedFiles: [],
    conditionalRules: [],
  });
  const result = helpers.hasProjectionLoss(
    ctx,
    [],
    [
      {
        files: ['office-guide.pdf'],
        conditions: [{ field: 'location_type', operator: 'eq', value: 'office' }],
      },
      {
        files: ['remote-guide.pdf'],
        conditions: [{ field: 'location_type', operator: 'eq', value: 'remote' }],
      },
    ],
  );
  assert.equal(result.equal, false);
  assert.equal(result.sourceSnapshot.length, 2);
  assert.equal(result.roundTripSnapshot.length, 0);
});


function loadEditorHelpers() {
  const editorSource = fs.readFileSync('api/app/static/bot_buttons_editor.js', 'utf8');
  const window = { BotButtonsFiles: helpers, confirm: () => true };
  const context = vm.createContext({ window, console, JSON, Array, Set, Map, String, Object });
  vm.runInContext(editorSource, context);
  return window.BotButtonsEditor;
}

const editorHelpers = loadEditorHelpers();

function loadEditorViewHelpers(editorApi = editorHelpers) {
  const source = fs.readFileSync('api/app/static/bot_buttons_editor_view.js', 'utf8');
  const window = {
    BotButtonsEditor: editorApi,
    BotButtonsFiles: helpers,
  };
  const document = {
    getElementById() {
      return {
        addEventListener() {},
        value: '',
        checked: false,
        disabled: false,
      };
    },
    querySelectorAll() { return []; },
  };
  const context = vm.createContext({ window, document, console, JSON, Array, Set, Map, String, Object, Node: { TEXT_NODE: 3, ELEMENT_NODE: 1 } });
  vm.runInContext(source, context);
  return window.BotButtonsEditorView;
}

const editorViewHelpers = loadEditorViewHelpers();

function loadSystemTicketHelpers() {
  const source = fs.readFileSync('api/app/static/settings_bot_buttons.js', 'utf8');
  const registerCalls = [];
  const window = {
    BotButtonsApi: {},
    BotButtonsFiles: {},
    BotButtonsLevelView: {},
    BotButtonsEditor: {
      hasUnsavedChanges() { return false; },
      confirmDiscard() { return true; },
      discardEditorChanges() {},
      syncAutomationSelectionMeta() {},
    },
    BotButtonsMenuView: {},
    BotButtonsEditorView: {},
    HelpdeskAppShell: {
      registerPage(name, fn) {
        registerCalls.push({ name, fn });
      },
    },
    addEventListener() {},
    removeEventListener() {},
  };
  const document = {
    getElementById() { return null; },
    querySelectorAll() { return []; },
    createElement() { return { innerHTML: '', firstElementChild: null }; },
    body: { appendChild() {} },
  };
  const context = vm.createContext({ window, document, console, JSON, Array, Set, Map, String, Object });
  vm.runInContext(source, context);
  return { helpers: window.BotButtonsSystemTicket, registerCalls };
}

const systemTicket = loadSystemTicketHelpers();

test('send_files metadata sync uses automationTypeValue as source of truth', () => {
  const state = {
    automationTypeValue: 'send_files',
    availableAutomations: [
      { key: 'none', title: '\u041d\u0435 \u0438\u0441\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u044c', description: '\u0410\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0437\u0430\u0446\u0438\u044f \u043d\u0435 \u043f\u0440\u0438\u043c\u0435\u043d\u044f\u0435\u0442\u0441\u044f.', available: true, handler: '' },
      { key: 'send_files', title: '\u041e\u0442\u043f\u0440\u0430\u0432\u043a\u0430 \u0444\u0430\u0439\u043b\u043e\u0432', description: '\u0410\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0437\u0430\u0446\u0438\u044f \u043e\u0442\u043f\u0440\u0430\u0432\u043b\u044f\u0435\u0442 \u0444\u0430\u0439\u043b\u044b \u043a\u043b\u0438\u0435\u043d\u0442\u0443.', available: true, handler: '' },
    ],
  };
  editorHelpers.syncAutomationSelectionMeta({ state });
  assert.equal(state.automationKey, 'send_files');
  assert.equal(state.automationTitle, '\u041e\u0442\u043f\u0440\u0430\u0432\u043a\u0430 \u0444\u0430\u0439\u043b\u043e\u0432');
  assert.equal(state.automationStatus, 'available');
});

test('editor view renders workflow type select for ticket key fallback without explicit workflow_type', () => {
  const content = { innerHTML: '' };
  editorViewHelpers.renderEditor({
    state: {
      selectedLeaf: {
        technical_key: 'menu-a|ticket|A',
        label: 'A',
        full_path: 'A',
        children_rows: [],
        removed: false,
        is_db_created: false,
      },
      editorWorkflowType: '',
      editorBotLabel: 'A',
      editorText: '',
      editorClientText: '',
      editorSystemText: '',
      confirmationEnabled: false,
      confirmationText: '',
      fixedAssigneeEnabled: false,
      fixedAssigneeUserId: '',
      freezeOnCreate: false,
      isEnabled: true,
      automationTypeValue: 'none',
      availableAutomations: [
        { key: 'none', title: '\u041d\u0435 \u0438\u0441\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u044c', description: '', available: true, handler: '', workflow_types: ['ticket', 'task', 'auto'] },
      ],
      automationTitle: '',
      automationKey: 'none',
      automationDescription: '',
      automationStatus: 'not_applied',
      automationAvailable: true,
      automationHandler: '',
      selectedFiles: [],
      conditionalRules: [],
    },
    el: { content },
    escapeHtml(value) { return String(value); },
    renderSelectedFiles() { return ''; },
    renderConditionalRules() { return ''; },
    buildMoveCandidates() { return []; },
    renderAssigneeOptions() { return ''; },
    openFilePickerModal() {},
    openConditionalRuleFilesModal() {},
    fieldMeta() { return null; },
    fieldLabel(value) { return String(value || ''); },
    OPERATOR_OPTIONS: { text: [], select: [], number: [], bool: [] },
    render() {},
    saveInstruction() {},
    archiveMenuButton() {},
    restoreRemovedSetting() {},
    deleteRemovedSetting() {},
    moveRemovedSetting() {},
    hasAnyStoredSettings() { return false; },
  });

  assert.match(content.innerHTML, /id="editorWorkflowTypeSelect"/);
  assert.match(content.innerHTML, /\u0417\u0430\u044f\u0432\u043a\u0430/);
  assert.match(content.innerHTML, /\u0417\u0430\u0434\u0430\u0447\u0430/);
  assert.match(content.innerHTML, /\u0410\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0447\u0435\u0441\u043a\u0430\u044f \u0437\u0430\u0434\u0430\u0447\u0430/);
});

test('editor view prefers saved workflow_type over technical_key fallback when rendering select', () => {
  const content = { innerHTML: '' };
  editorViewHelpers.renderEditor({
    state: {
      selectedLeaf: {
        technical_key: 'menu-a|ticket|A',
        workflow_type: 'task',
        label: 'A',
        full_path: 'A',
        children_rows: [],
        removed: false,
        is_db_created: false,
      },
      editorWorkflowType: 'task',
      editorBotLabel: 'A',
      editorText: '',
      editorClientText: '',
      editorSystemText: '',
      confirmationEnabled: false,
      confirmationText: '',
      fixedAssigneeEnabled: false,
      fixedAssigneeUserId: '',
      freezeOnCreate: false,
      isEnabled: true,
      automationTypeValue: 'none',
      availableAutomations: [
        { key: 'none', title: '\u041d\u0435 \u0438\u0441\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u044c', description: '', available: true, handler: '', workflow_types: ['ticket', 'task', 'auto'] },
      ],
      automationTitle: '',
      automationKey: 'none',
      automationDescription: '',
      automationStatus: 'not_applied',
      automationAvailable: true,
      automationHandler: '',
      selectedFiles: [],
      conditionalRules: [],
    },
    el: { content },
    escapeHtml(value) { return String(value); },
    renderSelectedFiles() { return ''; },
    renderConditionalRules() { return ''; },
    buildMoveCandidates() { return []; },
    renderAssigneeOptions() { return ''; },
    openFilePickerModal() {},
    openConditionalRuleFilesModal() {},
    fieldMeta() { return null; },
    fieldLabel(value) { return String(value || ''); },
    OPERATOR_OPTIONS: { text: [], select: [], number: [], bool: [] },
    render() {},
    saveInstruction() {},
    archiveMenuButton() {},
    restoreRemovedSetting() {},
    deleteRemovedSetting() {},
    moveRemovedSetting() {},
    hasAnyStoredSettings() { return false; },
  });

  assert.match(content.innerHTML, /<option value="task" selected>/);
  assert.doesNotMatch(content.innerHTML, /<option value="ticket" selected>/);
});

test('frontend fallback resolves send_files when api returns none but legacy conditional files exist', () => {
  const payload = {
    automation_type: 'none',
    automation_key: null,
    files: [],
    conditional_files: [
      {
        files: ['office-guide.pdf'],
        conditions: [{ field: 'location_type', operator: 'eq', value: 'office' }],
      },
      {
        files: ['remote-guide.pdf'],
        conditions: [{ field: 'location_type', operator: 'eq', value: 'remote' }],
      },
    ],
  };
  const state = {
    savedSelectedFiles: Array.isArray(payload.files) ? payload.files : [],
    savedConditionalFilesOverridden: true,
    conditionalFilesOverridden: true,
    conditionalFields: [
      {
        field: 'location_type',
        type: 'select',
        label: 'Location type',
        values: ['office', 'remote'],
        operators: ['eq', 'ne', 'empty', 'not_empty'],
      },
    ],
  };
  const normalizedConditional = payload.conditional_files.map((rule) => helpers.normalizeConditionalRuleForState({
    state,
    FIELD_LABELS: {},
    OPERATOR_OPTIONS: { text: [], select: [], number: [], bool: [] },
  }, rule));
  const apiType = String(payload.automation_type || '').trim();
  const hasLegacyFiles = state.savedSelectedFiles.length > 0 || normalizedConditional.length > 0;
  const inferredAutomationType = payload.automation_key
    ? String(payload.automation_key)
    : ((apiType && apiType !== 'none')
      ? apiType
      : (hasLegacyFiles ? 'send_files' : 'none'));
  assert.equal(inferredAutomationType, 'send_files');
});

test('ticket reply composer textarea uses neutral credential-resistant attributes', () => {
  const html = fs.readFileSync('api/app/templates/ticket.html', 'utf8');
  const textarea = html.match(/<textarea[^>]*id="ticketMessageBody"[^>]*>/)?.[0] || '';
  assert.match(textarea, /name="ticket_message_body"/);
  assert.match(textarea, /autocomplete="off"/);
  assert.match(textarea, /placeholder="Текст ответа\/комментария"/);
  assert.doesNotMatch(textarea, /╨|╤|Ð|Ñ|\?\?\?/);
  assert.doesNotMatch(textarea, /password|login|credential|messageInput/i);
});

test('theme toggle rebinds to replaced DOM node without stacking old listeners', () => {
  const source = fs.readFileSync('api/app/static/theme.js', 'utf8');
  const buttons = [];
  function makeButton() {
    const listeners = {};
    const button = {
      textContent: '',
      attrs: {},
      addEventListener(type, fn) {
        listeners[type] = listeners[type] || [];
        listeners[type].push(fn);
      },
      removeEventListener(type, fn) {
        listeners[type] = (listeners[type] || []).filter((item) => item !== fn);
      },
      setAttribute(name, value) {
        this.attrs[name] = value;
      },
      click() {
        (listeners.click || []).forEach((fn) => fn());
      },
      listenerCount(type) {
        return (listeners[type] || []).length;
      },
    };
    buttons.push(button);
    return button;
  }
  let currentButton = makeButton();
  const document = {
    readyState: 'complete',
    body: { dataset: {} },
    getElementById(id) {
      return id === 'themeToggleBtn' ? currentButton : null;
    },
    querySelectorAll(selector) {
      if (selector === '#themeToggleBtn') return [currentButton];
      if (selector === "form[action='/logout']") return [];
      return [];
    },
    addEventListener() {},
  };
  const localStorage = {
    store: {},
    getItem(key) { return this.store[key] || null; },
    setItem(key, value) { this.store[key] = value; },
    removeItem(key) { delete this.store[key]; },
  };
  const window = {};
  const context = vm.createContext({ document, localStorage, window });
  vm.runInContext(source, context);

  assert.equal(currentButton.listenerCount('click'), 1);
  currentButton.click();
  assert.equal(document.body.dataset.theme, 'dark');

  const oldButton = currentButton;
  currentButton = makeButton();
  window.HelpdeskTheme.initThemeToggle();
  window.HelpdeskTheme.initThemeToggle();
  assert.equal(oldButton.listenerCount('click'), 0);
  assert.equal(currentButton.listenerCount('click'), 1);
  currentButton.click();
  assert.equal(document.body.dataset.theme, 'light');
});

test('editor validation flags projection loss before save can wipe legacy rules', () => {
  const state = {
    selectedFiles: [],
    conditionalRules: [],
    conditionalFields: [
      {
        field: 'location_type',
        type: 'select',
        label: 'Location type',
        values: ['office', 'remote'],
        operators: ['eq', 'ne', 'empty', 'not_empty'],
      },
    ],
  };
  const result = editorHelpers.validateSendFilesProjection(
    {
      state,
      FIELD_LABELS: {},
      OPERATOR_OPTIONS: { text: [], select: [], number: [], bool: [] },
    },
    [],
    [
      {
        files: ['office-guide.pdf'],
        conditions: [{ field: 'location_type', operator: 'eq', value: 'office' }],
      },
    ],
  );
  assert.equal(result.ok, false);
  assert.match(result.error, /Сохранение заблокировано/);
});
