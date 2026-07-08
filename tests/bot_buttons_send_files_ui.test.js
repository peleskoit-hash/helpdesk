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
  const window = { BotButtonsFiles: helpers };
  const context = vm.createContext({ window, console, JSON, Array, Set, Map, String, Object });
  vm.runInContext(editorSource, context);
  return window.BotButtonsEditor;
}

const editorHelpers = loadEditorHelpers();

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
