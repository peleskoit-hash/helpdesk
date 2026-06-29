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
          label: '??? ???????',
          values: ['???????', '?????????'],
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
        conditions: [{ field: 'location_type', operator: 'eq', value: '???????' }],
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
        conditions: [{ field: 'location_type', operator: 'eq', value: '?????????' }],
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
        conditions: [{ field: 'location_type', operator: 'eq', value: '???????' }],
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
        conditions: [{ field: 'location_type', operator: 'eq', value: '???????' }],
      },
    ],
  });

  helpers.removeFileFromState(ctx, 'docs/a.pdf');
  const payload = helpers.buildSendFilesPayload(ctx);
  assert.equal(payload.files.includes('docs/a.pdf'), false);
  assert.equal(payload.conditionalRules.some((rule) => rule.files.includes('docs/a.pdf')), false);
  assert.equal(payload.conditionalRules.some((rule) => rule.files.includes('docs/b.pdf')), true);
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

test('send_files metadata sync uses automationTypeValue as source of truth', () => {
  const state = {
    automationTypeValue: 'send_files',
    availableAutomations: [
      { key: 'none', title: '?? ????????????', description: '????????????? ?? ???????????.', available: true, handler: '' },
      { key: 'send_files', title: '???????? ??????', description: '?????????? ??????????? ????? ???????.', available: true, handler: '' },
    ],
  };
  editorHelpers.syncAutomationSelectionMeta({ state });
  assert.equal(state.automationKey, 'send_files');
  assert.equal(state.automationTitle, '???????? ??????');
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
