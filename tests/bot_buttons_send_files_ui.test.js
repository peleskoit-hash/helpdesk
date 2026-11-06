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
const settingsClientCardSource = fs.readFileSync('api/app/static/settings_client_card.js', 'utf8');
const settingsClientCardTemplate = fs.readFileSync('api/app/templates/settings_client_card.html', 'utf8');
const stylesSource = fs.readFileSync('api/app/static/styles.css', 'utf8');
const deskV2Source = fs.readFileSync('api/app/static/desk-v2.css', 'utf8');
const ticketClientCardSource = fs.readFileSync('api/app/static/ticket_client_card.js', 'utf8');
const ticketPageSource = fs.readFileSync('api/app/static/ticket.js', 'utf8');
const ticketControlsSource = fs.readFileSync('api/app/static/ticket_controls.js', 'utf8');
const ticketComposerSource = fs.readFileSync('api/app/static/ticket_composer.js', 'utf8');
const quickRepliesSource = fs.readFileSync('api/app/static/quick_replies.js', 'utf8');
const ticketsListSource = fs.readFileSync('api/app/static/list.js', 'utf8');
const ticketsTemplate = fs.readFileSync('api/app/templates/tickets.html', 'utf8');
const quickRepliesTemplate = fs.readFileSync('api/app/templates/quick_replies.html', 'utf8');
const managementUsersSource = fs.readFileSync('api/app/static/management_users.js', 'utf8');
const ticketTemplate = fs.readFileSync('api/app/templates/ticket.html', 'utf8');
const managementUsersTemplate = fs.readFileSync('api/app/templates/management_users.html', 'utf8');
const appShellSource = fs.readFileSync('api/app/static/app_shell.js', 'utf8');

function sourceBetween(source, start, end) {
  const startIndex = source.indexOf(start);
  assert.notEqual(startIndex, -1, `missing start marker: ${start}`);
  const endIndex = source.indexOf(end, startIndex);
  assert.notEqual(endIndex, -1, `missing end marker: ${end}`);
  return source.slice(startIndex, endIndex);
}

function loadSettingsClientCardHelpers() {
  const helperSource = [
    sourceBetween(settingsClientCardSource, 'function deepCloneRuleActions', 'function isCustomBlock'),
    sourceBetween(settingsClientCardSource, 'function formatSourceTemplateRef', 'function ruleTemplateFieldRefs'),
    sourceBetween(settingsClientCardSource, 'function ruleConditionFieldType', 'function ruleTemplateFieldRefs'),
    sourceBetween(settingsClientCardSource, 'function normalizedFieldOptions', 'function ruleTemplateFieldRefs'),
    sourceBetween(settingsClientCardSource, 'function ruleTemplateFieldRefs', 'const complexTemplateValuePlaceholder'),
    sourceBetween(settingsClientCardSource, 'const complexTemplateValuePlaceholder', 'function keyValueRowHtml'),
    sourceBetween(settingsClientCardSource, 'function canTestSourceEditor', 'function syncSourceTestAvailability'),
    sourceBetween(settingsClientCardSource, 'function computeRuleActionUiState', 'function ruleActionOptions'),
    'window.__settingsClientCardHelpers = { analyzeTemplateObject, buildRuleDuplicatePayload, canTestSourceEditor, complexTemplateValuePlaceholder, computeRuleActionUiState, deepCloneRuleActions, escapeRegExp, formatSourceTemplateRef, nextRuleCopyName, normalizedFieldOptions, renderRuleTemplateValue, ruleConditionFieldType, ruleTemplateFieldRefs, serializeTemplateRows };',
  ].join('\n');
  const window = {};
  const context = vm.createContext({ window, console, JSON, Array, Set, Map, String, Object });
  vm.runInContext(helperSource, context);
  return window.__settingsClientCardHelpers;
}

function loadManagementUsersHelpers() {
  const source = fs.readFileSync('api/app/static/management_users.js', 'utf8');
  const window = {
    HelpdeskAppShell: { registerPage() {} },
    clearTimeout() {},
    setTimeout() { return 1; },
    location: {
      href: 'https://example.test/management/users',
      origin: 'https://example.test',
    },
    history: {
      pushState() {},
      replaceState() {},
    },
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
    URL,
    URLSearchParams,
  });
  vm.runInContext(source, context);
  return window.ManagementUsersPage;
}

const managementUsersHelpers = loadManagementUsersHelpers();
const settingsClientCardHelpers = loadSettingsClientCardHelpers();

test('settings client card UI hides manual order controls in the main block and field editor', () => {
  assert.doesNotMatch(settingsClientCardSource, /data-prop="sort_order"/);
  assert.doesNotMatch(settingsClientCardSource, /client-card-field-order/);
  assert.doesNotMatch(settingsClientCardTemplate, /editFieldSortOrder/);
});

test('settings client card UI keeps move actions without color controls', () => {
  assert.match(settingsClientCardSource, /data-move-block-id/);
  assert.match(settingsClientCardSource, /data-move-field-id/);
  assert.doesNotMatch(settingsClientCardSource, /type="color"/);
  assert.doesNotMatch(settingsClientCardSource, /data-color-hex/);
  assert.doesNotMatch(settingsClientCardSource, /client-card-color-chip/);
  assert.doesNotMatch(settingsClientCardSource, /aria-pressed/);
});

test('settings client card color helpers normalize named, hex, and invalid colors', () => {
  assert.match(settingsClientCardSource, /blue: "#dbeafe"/);
  assert.match(settingsClientCardSource, /function isHexColor/);
  assert.match(settingsClientCardSource, /function colorToHex/);
  assert.match(settingsClientCardSource, /return fallbackColorHex/);
  assert.match(settingsClientCardSource, /card\.style\.background = safeColor/);
});

test('settings client card save block sends current color and keeps pending on errors', () => {
  const payloadSource = sourceBetween(settingsClientCardSource, 'function blockPayloadFromDom(blockId)', 'function clearPendingBlockSaveTimer');
  assert.match(payloadSource, /state\.clientCard\.blocks\.find\([\s\S]*color_scheme/);
  assert.match(payloadSource, /return \{ title, color_scheme: color, is_active: active, show_in_card: showInCard, show_in_editor: showInEditor, show_title: showTitle \}/);

  const saveSource = sourceBetween(settingsClientCardSource, 'function clearPendingBlockSaveTimer', 'async function moveBlock');
  assert.match(saveSource, /window\.setTimeout/);
  assert.match(saveSource, /if \(meta\.inFlight\) return/);
  assert.match(saveSource, /method: "PATCH"/);
  assert.match(saveSource, /body: JSON\.stringify\(blockPayloadFromDom\(blockId\)\)/);
  assert.match(saveSource, /state\.pendingBlockColors\.delete\(blockId\)/);
  assert.match(saveSource, /catch \(e\) \{\s+showError\(e\.message\);\s+\}/);
});

test('settings client card block header separates content and reorder actions', () => {
  assert.match(settingsClientCardSource, /client-card-content-actions/);
  assert.match(settingsClientCardSource, /client-card-reorder-actions/);
  const contentActions = sourceBetween(settingsClientCardSource, '<div class="client-card-content-actions">', '<div class="client-card-reorder-actions">');
  const reorderActions = sourceBetween(settingsClientCardSource, '<div class="client-card-reorder-actions">', '<div class="client-card-block-editor">');
  assert.doesNotMatch(contentActions, /data-save-block-id/);
  assert.doesNotMatch(contentActions, /data-toggle-block-id/);
  assert.doesNotMatch(contentActions, /data-move-block-id/);
  assert.doesNotMatch(settingsClientCardSource, /compact-icon|data-toggle-block-id/);
  assert.match(reorderActions, /data-move-block-id/);
  assert.doesNotMatch(reorderActions, /data-save-block-id/);
});

test('settings client card block autosave removes dedicated save button and saves title on debounce', () => {
  assert.doesNotMatch(settingsClientCardSource, /data-save-block-id/);
  assert.match(settingsClientCardSource, /input\.addEventListener\("input", \(\) => scheduleBlockAutosave\(input\.dataset\.blockId, \{ delay: 500, statusText: uiText\.blockSaved \}\)\)/);
  assert.match(settingsClientCardSource, /input\.addEventListener\("blur", \(\) => scheduleBlockAutosave\(input\.dataset\.blockId, \{ statusText: uiText\.blockSaved \}\)\)/);
  assert.match(settingsClientCardSource, /data-prop='is_active'], \[data-prop='show_in_card'], \[data-prop='show_in_editor'], \[data-prop='show_title']/);
});

test('ticket client card safely applies hex block colors for ticket and management views', () => {
  assert.match(ticketClientCardSource, /function isHexColor/);
  assert.match(ticketClientCardSource, /client-card-block-custom-color/);
  assert.match(ticketClientCardSource, /--client-card-block-bg/);
  assert.match(ticketClientCardSource, /\^#\[0-9a-fA-F\]\{6\}\$/);
});

test('client card script cache busting is updated for changed JS files', () => {
  assert.match(settingsClientCardTemplate, /settings_client_card\.js\?v=20261027-dialogs-complete-v3/);
  assert.match(appShellSource, /settings_client_card\.js\?v=20261027-dialogs-complete-v3/);
  assert.match(ticketTemplate, /ticket_client_card\.js\?v=20261027-dialogs-complete-v3/);
  assert.match(managementUsersTemplate, /ticket_client_card\.js\?v=20261027-dialogs-complete-v3/);
  assert.match(appShellSource, /ticket_client_card\.js\?v=20261027-dialogs-complete-v3/);
  assert.match(ticketTemplate, /ticket\.js\?v=20261029-ticket-history-v1/);
  assert.match(appShellSource, /ticket\.js\?v=20261029-ticket-history-v1/);
  assert.match(managementUsersTemplate, /management_users\.js\?v=20261027-dialogs-complete-v3/);
});

test('ticket page binds static handlers before sidebar async initialization', () => {
  const runSource = sourceBetween(ticketPageSource, '(async function run() {', '})();');
  const controlsCtxSource = sourceBetween(ticketPageSource, 'function controlsCtx()', 'function messageViewCtx()');
  const bindSource = sourceBetween(ticketPageSource, 'function bindPageHandlers()', 'function initPostSidebarState()');
  const postSidebarSource = sourceBetween(ticketPageSource, 'function initPostSidebarState()', 'function startPolling()');
  const pollingSource = sourceBetween(ticketPageSource, 'function startPolling()', '(async function run() {');

  assert.match(bindSource, /if \(pageHandlersBound\) return/);
  assert.match(bindSource, /pageHandlersBound = true/);
  assert.match(bindSource, /TicketComposer\.bindComposerHandlers\(composerCtx\(\)\)/);
  assert.match(bindSource, /TicketControls\.bindTicketControlHandlers\(controlsCtx\(\)\)/);
  assert.match(controlsCtxSource, /openClientModal/);
  assert.doesNotMatch(bindSource, /HelpdeskSidebar\.getState/);
  assert.doesNotMatch(bindSource, /setInterval/);

  assert.match(ticketPageSource, /function cleanupTicketCardPage\(\)[\s\S]*pageHandlersBound = false/);
  assert.ok(bindSource.indexOf('if (pageHandlersBound) return;') < bindSource.indexOf('pageHandlersBound = true;'));
  assert.ok(ticketPageSource.indexOf('pageHandlersBound = false;') < ticketPageSource.indexOf('function bindPageHandlers()'));
  assert.ok(ticketPageSource.lastIndexOf('pageHandlersBound = false;') > ticketPageSource.indexOf('function cleanupTicketCardPage()'));

  assert.match(postSidebarSource, /el\.roleSelect\.value = state\.role/);
  assert.match(postSidebarSource, /el\.agentSelect\.value = state\.userId/);
  assert.match(pollingSource, /pollTimer = window\.setInterval/);

  assert.ok(runSource.indexOf('bindPageHandlers();') < runSource.indexOf('await window.HelpdeskSidebar.init();'));
  assert.ok(runSource.indexOf('syncSidebarState(window.HelpdeskSidebar.getState());') > runSource.indexOf('await window.HelpdeskSidebar.init();'));
  assert.ok(runSource.indexOf('initPostSidebarState();') > runSource.indexOf('syncSidebarState(window.HelpdeskSidebar.getState());'));
  assert.ok(runSource.indexOf('startPolling();') > runSource.indexOf('await Promise.all([loadCard(), loadTabCounts()]);'));
});

test('ticket page task panel mirrors list behavior with short card links', () => {
  assert.match(ticketTemplate, /id="taskPanel"/);
  assert.match(ticketTemplate, /id="taskPanelSlots"/);
  assert.match(ticketTemplate, /id="taskPanelMoreBtn"[\s\S]*…/);
  assert.match(ticketTemplate, /id="taskPanelDropdown"/);
  assert.ok(ticketTemplate.indexOf('id="taskPanel"') > ticketTemplate.indexOf('<div class="toolbar">'));
  assert.ok(ticketTemplate.indexOf('id="taskPanel"') < ticketTemplate.indexOf('<section class="content ticket-layout">'));

  const stateSource = sourceBetween(ticketPageSource, 'const state = {', '};\n\nconst el = {');
  assert.match(stateSource, /taskPanelEnabled: false/);
  assert.match(stateSource, /taskPanelItems: \[\]/);
  assert.match(stateSource, /taskPanelDropdownOpen: false/);

  const roleSource = sourceBetween(ticketPageSource, 'function canUseTaskPanel', 'function taskPanelTicketHref');
  assert.match(roleSource, /return role === "agent" \|\| role === "trainee"/);
  assert.doesNotMatch(roleSource, /admin|chiki_piki/);
  const roleContext = vm.createContext({ state: { role: 'agent' } });
  vm.runInContext(`${roleSource}\nglobalThis.canUseTaskPanel = canUseTaskPanel;`, roleContext);
  assert.equal(roleContext.canUseTaskPanel('agent'), true);
  assert.equal(roleContext.canUseTaskPanel('trainee'), true);
  assert.equal(roleContext.canUseTaskPanel('admin'), false);
  assert.equal(roleContext.canUseTaskPanel('chiki_piki'), false);

  const preferencesSource = sourceBetween(ticketPageSource, 'async function loadComposerPreferences', 'async function updateSendByEnterPreference');
  assert.match(preferencesSource, /state\.taskPanelEnabled = payload\?\.task_panel_enabled === true/);
  assert.match(preferencesSource, /renderTaskPanel\(\)/);

  const loadPanelSource = sourceBetween(ticketPageSource, 'async function loadTaskPanel', 'function cleanupTicketCardPage');
  assert.match(loadPanelSource, /if \(!canUseTaskPanel\(\) \|\| !state\.taskPanelEnabled\)[\s\S]*return/);
  assert.match(loadPanelSource, /api\("\/api\/v1\/tickets\/task-panel"\)/);

  const loadCardSource = sourceBetween(ticketPageSource, 'async function loadCard', 'async function sendByMode');
  assert.ok(loadCardSource.indexOf('api(`/api/v1/tickets/${state.ticketId}`)') < loadCardSource.indexOf('await loadTaskPanel({ silent: true });'));
  assert.ok(loadCardSource.indexOf('renderCard();') < loadCardSource.indexOf('await loadTaskPanel({ silent: true });'));

  assert.doesNotMatch(ticketPageSource, /taskPanelReturnUrl|return_url|ticketListReturnUrl/);
  const hrefSource = sourceBetween(ticketPageSource, 'function taskPanelTicketHref', 'function taskPanelItemHtml');
  assert.ok(hrefSource.includes('return `/tickets/${panelTicketId}`;'));
  assert.doesNotMatch(hrefSource, /searchParams/);

  const bindSource = sourceBetween(ticketPageSource, 'function bindPageHandlers()', 'function initPostSidebarState()');
  assert.match(bindSource, /taskPanelMoreBtn[\s\S]*state\.taskPanelDropdownOpen = !state\.taskPanelDropdownOpen/);
  assert.match(bindSource, /if \(event\.target\.closest\("#taskPanel"\)\) return/);
});

test('ticket freeze button stays visible but disabled for unassigned cards', () => {
  const renderSource = sourceBetween(ticketControlsSource, 'function renderTicketControlsView(ctx)', 'function bindTicketControlHandlers(ctx)');

  assert.match(renderSource, /const isUnassigned = card\.status === "unassigned" \|\| !card\.assignee_id/);
  assert.match(renderSource, /const canFreezeToggle = card\.is_frozen \|\| !isUnassigned/);
  assert.match(renderSource, /setVisible\(el\.freezeToggleBtn, !isReadonly\)/);
  assert.match(renderSource, /el\.freezeToggleBtn\.disabled = !showFreezeToggle \|\| !canFreezeToggle \|\| stateActionLocked/);
  assert.match(renderSource, /const showMarkSpecial = state\.role === "admin";/);
  assert.match(renderSource, /setVisible\(el\.markSpecialBtn, showMarkSpecial\)/);
  assert.match(renderSource, /el\.markSpecialBtn\.disabled = isClosed \|\| Boolean\(card\.is_special\) \|\| !\["ticket", "task"\]\.includes\(workflowType\)/);
  assert.doesNotMatch(renderSource, /showMarkSpecial[\s\S]*isUnassigned/);
});

test('task panel layout stays compact and dropdown is opaque in light and dark themes', () => {
  assert.match(stylesSource, /\.main\[data-shell-page="tickets-list"\],\s*\.main\[data-shell-page="ticket-card"\] \{[\s\S]*grid-template-rows: auto auto minmax\(0, 1fr\);/);
  assert.match(stylesSource, /\.main\[data-shell-page="ticket-card"\] > \.content\.ticket-layout \{[\s\S]*grid-row: 3;[\s\S]*min-height: 0;/);
  assert.match(stylesSource, /\.main\[data-shell-page="tickets-list"\] > \.content \{[\s\S]*grid-row: 3;[\s\S]*min-height: 0;/);
  assert.match(stylesSource, /\.task-panel \{[\s\S]*align-self: start;[\s\S]*background: #fff;/);
  assert.match(stylesSource, /\.task-panel-dropdown \{[\s\S]*z-index: 80;[\s\S]*background: #fff;/);
  assert.match(stylesSource, /\.task-panel-chip,\s*\.task-panel-menu-item \{[\s\S]*background: #f1f7ff;/);
  assert.match(stylesSource, /body\[data-theme="dark"\] \.task-panel \{[\s\S]*background: #0f172a;/);
  assert.match(stylesSource, /body\[data-theme="dark"\] \.task-panel-chip,\s*body\[data-theme="dark"\] \.task-panel-menu-item,\s*body\[data-theme="dark"\] \.task-panel-more \{[\s\S]*background: #1e293b;/);
  assert.match(stylesSource, /body\[data-theme="dark"\] \.task-panel-dropdown \{[\s\S]*background: #111827;/);
});

test('settings client card has three global V2 tabs with blocks initially open', () => {
  for (const tab of ['blocks', 'rules', 'sources']) assert.ok(settingsClientCardTemplate.includes(`data-client-card-main-tab="${tab}"`));
  assert.match(settingsClientCardSource, /activeClientCardTab: "blocks"/);
  assert.doesNotMatch(settingsClientCardTemplate, /data-client-card-automation-tab|id="clientCardAutomationPanel"/);
  assert.match(settingsClientCardTemplate, /role="tabpanel" data-client-card-main-panel="sources"/);
  assert.match(settingsClientCardTemplate, /class="panel settings-panel client-card-work-panel"/);
});

test('settings client card tab switching keeps loaded config and toolbar actions scoped', () => {
  const tabSyncSource = sourceBetween(settingsClientCardSource, 'function syncClientCardTabs', 'async function api');
  assert.match(tabSyncSource, /classList\.toggle\("hidden", panel\.dataset\.clientCardMainPanel !== state\.activeClientCardTab\)/);
  assert.match(tabSyncSource, /classList\.toggle\("hidden", !showBlocks\)/);
  assert.doesNotMatch(tabSyncSource, /api\(|loadClientCardConfig\(/);

  const saveSource = sourceBetween(settingsClientCardSource, 'async function saveSourceFromModal()', 'async function testSourceFromModal()');
  const deleteSource = sourceBetween(settingsClientCardSource, 'async function deleteSource(sourceId)', 'function syncRuleValueVisibility()');
  const saveRule = sourceBetween(settingsClientCardSource, 'async function saveRuleFromModal()', 'async function toggleRule');
  assert.doesNotMatch(saveSource, /activeAutomationTab\s*=/);
  assert.doesNotMatch(deleteSource, /activeAutomationTab\s*=/);
  assert.doesNotMatch(saveRule, /activeAutomationTab\s*=/);
});

test('settings client card uses V2 workspace bounds and internal scrolling', () => {
  const css = fs.readFileSync('api/app/static/client-card-settings-v2.css', 'utf8');
  assert.match(css, /padding: 14px 16px 16px/);
  assert.match(css, /height: 100%;[\s\S]*overflow: auto;[\s\S]*border-radius: 24px/);
});

test('settings client card UI exposes delete actions only for custom config entries', () => {
  assert.match(settingsClientCardSource, /isCustomBlock/);
  assert.match(settingsClientCardSource, /data-delete-block-id/);
  assert.match(settingsClientCardSource, /data-delete-field-id/);
  assert.match(settingsClientCardSource, /field\.is_system \? ""/);
});

test('settings client card edit modal locks existing field key and block', () => {
  assert.match(settingsClientCardSource, /el\.editFieldKey\.disabled = true/);
  assert.match(settingsClientCardSource, /el\.editFieldBlockKey\.disabled = true/);
});

test('settings client card edit save does not send block key for existing fields', () => {
  const saveFieldSource = sourceBetween(settingsClientCardSource, 'async function saveFieldFromModal()', 'function syncCreateFieldOptionsVisibility()');
  assert.doesNotMatch(saveFieldSource, /block_key/);
});

test('settings client card supports separate card and editor visibility flags', () => {
  assert.match(settingsClientCardTemplate, /id="newClientCardFieldVisibleInEditor"/);
  assert.match(settingsClientCardTemplate, /id="editFieldVisibleInEditor"/);
  assert.match(settingsClientCardSource, /editFieldVisibleInEditor\.checked = field\.is_visible_in_editor !== false/);
  assert.match(settingsClientCardSource, /is_visible_in_editor: Boolean\(el\.editFieldVisibleInEditor\.checked\)/);
  assert.match(settingsClientCardSource, /is_visible_in_editor: Boolean\(el\.newClientCardFieldVisibleInEditor\.checked\)/);
});

test('settings client card supports checkbox show-when-false flag', () => {
  assert.match(settingsClientCardTemplate, /id="newClientCardFieldShowWhenFalse"/);
  assert.match(settingsClientCardTemplate, /id="editFieldShowWhenFalse"/);
  const editFieldModal = sourceBetween(settingsClientCardTemplate, '<div id="editClientCardFieldModal"', '<button id="deleteEditClientCardFieldBtn"');
  const typeIndex = editFieldModal.indexOf('id="editFieldType"');
  const showWhenFalseIndex = editFieldModal.indexOf('id="editFieldShowWhenFalseWrap"');
  const optionsIndex = editFieldModal.indexOf('id="editFieldOptionsWrap"');
  assert.ok(typeIndex !== -1 && showWhenFalseIndex < typeIndex, 'edit show_when_false belongs to the upper parameters group');
  assert.ok(optionsIndex === -1 || showWhenFalseIndex < optionsIndex, 'edit show_when_false must stay before select options');
  assert.match(settingsClientCardSource, /editFieldShowWhenFalse\.checked = field\.show_when_false !== false/);
  assert.match(settingsClientCardSource, /show_when_false: selectedType === "checkbox" \? Boolean\(el\.editFieldShowWhenFalse\.checked\) : true/);
  assert.match(settingsClientCardSource, /show_when_false: fieldType === "checkbox" \? Boolean\(el\.newClientCardFieldShowWhenFalse\.checked\) : true/);
  assert.match(settingsClientCardSource, /newClientCardFieldShowWhenFalseWrap\.classList\.toggle\("hidden", el\.newClientCardFieldType\.value !== "checkbox"\)/);
  assert.match(settingsClientCardSource, /editFieldShowWhenFalseWrap\.classList\.toggle\("hidden", el\.editFieldType\.value !== "checkbox"\)/);
  assert.match(settingsClientCardSource, /el\.editFieldType\.addEventListener\("change", syncEditFieldOptionsVisibility\)/);
});

test('settings client card block save sends separate display mode flags', () => {
  const blockPayloadSource = sourceBetween(settingsClientCardSource, 'function blockPayloadFromDom(blockId)', 'async function saveBlock');
  assert.match(settingsClientCardSource, /data-prop="show_in_card"/);
  assert.match(settingsClientCardSource, /data-prop="show_in_editor"/);
  assert.match(settingsClientCardSource, /data-prop="show_title"/);
  assert.match(blockPayloadSource, /show_in_card: showInCard/);
  assert.match(blockPayloadSource, /show_in_editor: showInEditor/);
  assert.match(blockPayloadSource, /show_title: showTitle/);
});

test('ticket client card renderer separates card and editor visibility modes', () => {
  assert.match(ticketClientCardSource, /function blockShownInCard/);
  assert.match(ticketClientCardSource, /block\.show_in_card !== false/);
  assert.match(ticketClientCardSource, /function blockShownInEditor/);
  assert.match(ticketClientCardSource, /block\.show_in_editor !== false/);
  assert.match(ticketClientCardSource, /function fieldShownInCard/);
  assert.match(ticketClientCardSource, /field\.is_visible !== false/);
  assert.match(ticketClientCardSource, /function fieldShownInEditor/);
  assert.match(ticketClientCardSource, /field\.is_visible_in_editor !== false/);
});

test('ticket client card hides false checkbox only when configured', () => {
  assert.match(ticketClientCardSource, /fieldType === "checkbox"[\s\S]*return value \|\| field\.show_when_false !== false/);
  assert.match(ticketClientCardSource, /function renderEditableCustomField/);
  assert.doesNotMatch(sourceBetween(ticketClientCardSource, 'function renderEditableCustomField', 'function renderConfiguredSystemEditorField'), /show_when_false/);
});

// DOM moves preserve identity and detach nodes, as the browser renderer expects.
class ClientCardDomMock {
  constructor() {
    this.parentElement = null;
    this.attributes = new Map();
  }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  appendChild(child) { return this.insertBefore(child, null); }
  insertBefore(child, reference) {
    if (reference !== null && !this.children.includes(reference)) throw new Error('NotFoundError');
    if (child === reference) return child;
    child.remove();
    const index = reference === null ? this.children.length : this.children.indexOf(reference);
    this.children.splice(index, 0, child);
    child.parentElement = this;
    return child;
  }
  remove() {
    if (this.parentElement) {
      const siblings = this.parentElement.children;
      siblings.splice(siblings.indexOf(this), 1);
      this.parentElement = null;
    }
  }
}

test('ticket client card applies show_when_false to system verification checkbox', () => {
  class FakeElement extends ClientCardDomMock {
    constructor(tagName) {
      super();
      this.tagName = tagName.toUpperCase();
      this.children = [];
      this.dataset = {};
      this.textContent = '';
      this.style = { setProperty() {} };
      this._className = '';
      this.classList = {
        values: new Set(),
        add: (...items) => items.forEach((item) => this.classList.values.add(item)),
        remove: (...items) => items.forEach((item) => this.classList.values.delete(item)),
        contains: (item) => this.classList.values.has(item),
      };
    }
    set className(value) {
      this._className = String(value || '');
      this.classList.values = new Set(this._className.split(/\s+/).filter(Boolean));
    }
    get className() {
      return this._className;
    }
    set innerHTML(value) {
      this.children = [];
      this.textContent = value;
    }
    querySelectorAll(selector) {
      if (!selector.startsWith('.')) return [];
      const className = selector.slice(1);
      const result = [];
      const queue = [...this.children];
      while (queue.length) {
        const item = queue.shift();
        if (item.classList?.contains(className)) result.push(item);
        queue.push(...(item.children || []));
      }
      return result;
    }
  }

  const document = { createElement: (tagName) => new FakeElement(tagName) };
  const window = {};
  const context = vm.createContext({ window, document, console, JSON, Array, Set, Map, String, Object, Number, Math, Uint8Array });
  vm.runInContext(ticketClientCardSource, context);

  const renderRows = (isVerified, showWhenFalse, root = new FakeElement('div')) => {
    const ctx = {
      state: {},
      el: { clientDynamicCard: root },
      isBlank: (value) => value === null || value === undefined || String(value).trim() === '',
    };
    window.TicketClientCard.renderClientDynamicCard(
      ctx,
      { is_verified: isVerified },
      {
        blocks: [{
          key: 'verification',
          title: 'Verification',
          is_active: true,
          show_in_card: true,
          fields: [{ key: 'is_verified', label: 'Верификация', field_type: 'checkbox', is_visible: true, show_when_false: showWhenFalse }],
        }],
        rules: [],
      },
    );
    return root.querySelectorAll('.client-card-field');
  };

  assert.equal(renderRows(false, true).length, 1);
  assert.equal(renderRows(false, false).length, 0);
  assert.equal(renderRows(true, false).length, 1);
  const root = new FakeElement('div');
  assert.equal(renderRows(true, false, root).length, 1);
  assert.equal(renderRows(false, false, root).length, 0);
  assert.equal(root.children.length, 0);
});

test('ticket client card editor renders readonly copyable fields without payload dataset', () => {
  assert.match(ticketClientCardSource, /function renderReadonlyEditorField/);
  assert.match(ticketClientCardSource, /readonly-copyable/);
  assert.match(ticketClientCardSource, /navigator\.clipboard\.writeText/);
  const readonlySource = sourceBetween(ticketClientCardSource, 'function renderReadonlyEditorField', 'function renderEditableCustomField');
  assert.doesNotMatch(readonlySource, /dataset\.customFieldKey/);
  const payloadSource = sourceBetween(ticketClientCardSource, 'function getModalPayload', 'function openClientModal');
  assert.ok(payloadSource.includes('querySelectorAll("[data-custom-field-key]")'));
});

test('settings client card UI contains configurable rules section and form', () => {
  assert.match(settingsClientCardTemplate, /id="clientCardRules"/);
  assert.match(settingsClientCardTemplate, /id="addClientCardRuleBtn"/);
  assert.match(settingsClientCardTemplate, /id="ruleConditionField"/);
  assert.match(settingsClientCardTemplate, /id="ruleOperator"/);
  assert.match(settingsClientCardTemplate, /id="ruleValue"/);
  assert.match(settingsClientCardTemplate, /id="ruleActions"/);
  assert.match(settingsClientCardTemplate, /id="addRuleActionBtn"/);
  assert.match(settingsClientCardSource, /\/api\/v1\/settings\/client-card\/rules/);
  assert.match(settingsClientCardSource, /function renderRuleActions/);
  assert.match(settingsClientCardSource, /function collectRuleActions/);
  assert.match(settingsClientCardSource, /function ruleActionOptions/);
  assert.match(settingsClientCardSource, /data-rule-action-row/);
  assert.match(settingsClientCardSource, /target_type: targetType/);
  assert.match(settingsClientCardSource, /condition_value: \(el\.ruleOperator\.value === "equals" \|\| el\.ruleOperator\.value === "not_equals"\)/);
  assert.match(settingsClientCardSource, /ruleConditionValuePayload\(conditionControl, conditionField\?\.field_type\)/);
  assert.match(settingsClientCardSource, /actions: collectRuleActions\(\)/);
  assert.match(settingsClientCardSource, /\/api\/v1\/settings\/client-card\/rules\/\$\{ruleId\}\/move/);
});

test('rule condition value control derives type and options from selected field config', () => {
  const destinationField = {
    key: 'destination',
    label: 'Куда..?',
    field_type: 'select',
    block_key: 'department',
    sort_order: 10,
    is_system: true,
    options: ['Песок', 'Отдел Продаж', 'Колл Центр', 'Руководство'],
  };
  const customShadeField = {
    key: 'custom_shade',
    label: 'Предпочитаемый оттенок',
    field_type: 'select',
    block_key: 'custom_block_1',
    sort_order: 20,
    is_system: false,
    options: JSON.stringify([
      { label: 'Красный', value: 'red' },
      { label: 'Зелёный', value: 'green' },
    ]),
  };
  const textField = {
    key: 'contact_name',
    label: 'Имя',
    type: 'text',
    block_key: 'profile',
    sort_order: 30,
    is_system: true,
    options: null,
  };

  assert.equal(settingsClientCardHelpers.ruleConditionFieldType(destinationField), 'select');
  assert.equal(settingsClientCardHelpers.ruleConditionFieldType(textField), 'text');
  assert.deepEqual(
    JSON.parse(JSON.stringify(settingsClientCardHelpers.normalizedFieldOptions(destinationField))),
    [
      { value: 'Песок', label: 'Песок' },
      { value: 'Отдел Продаж', label: 'Отдел Продаж' },
      { value: 'Колл Центр', label: 'Колл Центр' },
      { value: 'Руководство', label: 'Руководство' },
    ],
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(settingsClientCardHelpers.normalizedFieldOptions(customShadeField))),
    [
      { value: 'red', label: 'Красный' },
      { value: 'green', label: 'Зелёный' },
    ],
  );
  assert.match(settingsClientCardSource, /function normalizedFieldOptions/);
  assert.match(settingsClientCardSource, /function ruleConditionFieldType/);
  assert.match(settingsClientCardSource, /const options = normalizedFieldOptions\(field\)/);
  assert.match(settingsClientCardSource, /const type = ruleConditionFieldType\(field\)/);
  assert.match(settingsClientCardSource, /type === "select" && options\.length/);
  assert.match(settingsClientCardSource, /typeof rawOptions === "string"/);
  assert.match(settingsClientCardSource, /JSON\.parse\(rawOptions\)/);
  assert.match(settingsClientCardSource, /option\.value === String\(currentValue\)/);
  assert.match(settingsClientCardSource, /option\.label/);
  assert.match(settingsClientCardSource, /type === "checkbox"/);
  assert.match(settingsClientCardSource, /type === "number" \? "number" : "text"/);
  assert.match(settingsClientCardSource, /el\.ruleValueWrap\.classList\.toggle\("hidden", !needsValue\)/);
  assert.match(settingsClientCardSource, /syncRuleConditionValueControl\(rule\?\.condition_value \|\| ""\)/);
});

test('backend client card config now preserves system select options for rule editor runtime', () => {
  const clientCardServiceSource = fs.readFileSync('api/app/services/client_card.py', 'utf8');
  const settingsRouterSource = fs.readFileSync('api/app/routers/settings_client_card.py', 'utf8');
  assert.match(clientCardServiceSource, /SYSTEM_SELECT_FIELD_OPTIONS = \{/);
  assert.match(clientCardServiceSource, /"destination": \[/);
  assert.match(clientCardServiceSource, /"Колл Центр"/);
  assert.match(clientCardServiceSource, /def _field_options_out\(field: ClientCardFieldConfig\)/);
  assert.match(clientCardServiceSource, /options=_field_options_out\(field\)/);
  assert.match(settingsRouterSource, /from \.\.services\.client_card import _field_editor_visibility_default, _field_options_out/);
  assert.match(settingsRouterSource, /options=_field_options_out\(field\)/);
});

test('rule copy helpers preserve actions, template text and isolate nested data', () => {
  const sourceRule = {
    id: 'rule-1',
    name: 'ФИО',
    is_active: true,
    sort_order: 20,
    condition_field_key: 'department_name',
    condition_operator: 'equals',
    condition_value: 'Колл Центр',
    actions: [
      {
        target_type: 'field',
        target_key: 'custom_fio',
        action: 'set_value',
        scope: 'save',
        value_type: 'template',
        value: '{{last_name}} {{contact_name}}',
      },
      {
        target_type: 'field',
        target_key: 'bitrix_login',
        action: 'set_value',
        scope: 'save',
        value_type: 'field',
        source_key: 'bitrix_login',
      },
    ],
  };

  const payload = settingsClientCardHelpers.buildRuleDuplicatePayload(sourceRule, [sourceRule]);

  assert.equal(payload.name, 'ФИО — Копия');
  assert.equal(payload.is_active, false);
  assert.equal(payload.sort_order, 21);
  assert.equal(payload.condition_field_key, 'department_name');
  assert.equal(payload.condition_operator, 'equals');
  assert.equal(payload.condition_value, 'Колл Центр');
  assert.deepEqual(payload.actions, sourceRule.actions);
  assert.equal(payload.actions[0].value, '{{last_name}} {{contact_name}}');
  assert.notEqual(payload.actions, sourceRule.actions);

  payload.actions[0].value = '{{last_name}}';
  payload.actions[1].source_key = 'changed';
  assert.equal(sourceRule.actions[0].value, '{{last_name}} {{contact_name}}');
  assert.equal(sourceRule.actions[1].source_key, 'bitrix_login');
});

test('rule copy naming increments existing copy suffixes', () => {
  const rules = [
    { name: 'ФИО' },
    { name: 'ФИО — Копия' },
    { name: 'ФИО — Копия 2' },
    { name: 'Другое правило — Копия' },
  ];

  assert.equal(settingsClientCardHelpers.nextRuleCopyName('ФИО', rules), 'ФИО — Копия 3');
  assert.equal(settingsClientCardHelpers.nextRuleCopyName('Новое правило', rules), 'Новое правило — Копия');
});

test('rule copy button uses existing create API and isolates click from card actions', () => {
  assert.match(settingsClientCardSource, /data-copy-rule-id="\$\{escapeHtml\(rule\.id\)\}">\$\{uiText\.copy\}<\/button>/);
  assert.match(settingsClientCardSource, /await api\("\/api\/v1\/settings\/client-card\/rules", \{/);
  assert.match(settingsClientCardSource, /buildRuleDuplicatePayload\(sourceRule, state\.clientCard\.rules \|\| \[\]\)/);
  assert.match(settingsClientCardSource, /await api\(`\/api\/v1\/settings\/client-card\/rules\/\$\{createdRule\.id\}\/move`, \{/);
  assert.match(settingsClientCardSource, /event\.preventDefault\(\);[\s\S]*event\.stopPropagation\(\);[\s\S]*duplicateRule\(btn\.dataset\.copyRuleId\);/);
  assert.doesNotMatch(settingsClientCardSource, /data-copy-rule-id[\s\S]*openRuleModal\(btn\.dataset\.copyRuleId\)/);
  assert.doesNotMatch(settingsClientCardSource, /data-copy-rule-id[\s\S]*toggleRule\(btn\.dataset\.copyRuleId/);
});

test('rule cards open editor from free area and keep controls isolated', () => {
  const rulesSource = sourceBetween(settingsClientCardSource, 'function renderClientCardRules', 'function renderClientCardSources');
  assert.match(rulesSource, /row\.tabIndex = 0/);
  assert.match(rulesSource, /row\.setAttribute\("role", "button"\)/);
  assert.match(rulesSource, /openRuleModal\(rule\.id\)/);
  assert.match(rulesSource, /bindKeyboardOpen\(row, \(\) => openRuleModal\(rule\.id\)\)/);
  assert.doesNotMatch(rulesSource, /data-edit-rule-id/);
  assert.match(rulesSource, /event\.stopPropagation\(\);[\s\S]*deleteRule\(btn\.dataset\.deleteRuleId\)/);
  assert.match(rulesSource, /event\.stopPropagation\(\);[\s\S]*toggleRule\(input\.dataset\.toggleRuleId, input\.checked\)/);
  assert.match(rulesSource, /event\.stopPropagation\(\);[\s\S]*moveRule\(btn\.dataset\.moveRuleId, btn\.dataset\.direction\)/);
});

test('rule delete asks for explicit confirmation with rule name', () => {
  const deleteSource = sourceBetween(settingsClientCardSource, 'async function deleteRule', 'async function duplicateRule');
  assert.match(deleteSource, /window\.HelpdeskDialogs\.confirm/);
  assert.match(deleteSource, /uiText\.deleteRuleConfirmPrefix/);
  assert.match(deleteSource, /rule\.name \|\| rule\.id/);
  assert.match(deleteSource, /uiText\.deleteRuleConfirmSuffix/);
});

test('rule editor uses user-facing Russian labels while preserving API values', () => {
  assert.match(settingsClientCardTemplate, />Если поле<select id="ruleConditionField"/);
  assert.match(settingsClientCardTemplate, />Условие сравнения/);
  assert.match(settingsClientCardSource, /\\u0427\\u0442\\u043e \\u0438\\u0437\\u043c\\u0435\\u043d\\u0438\\u0442\\u044c/);
  assert.match(settingsClientCardSource, /\\u041a\\u0430\\u043a\\u043e\\u0439 \\u0431\\u043b\\u043e\\u043a \\u0438\\u043b\\u0438 \\u043f\\u043e\\u043b\\u0435/);
  assert.match(settingsClientCardSource, /\\u0427\\u0442\\u043e \\u0441\\u0434\\u0435\\u043b\\u0430\\u0442\\u044c/);
  assert.match(settingsClientCardSource, /\\u0413\\u0434\\u0435 \\u043f\\u0440\\u0438\\u043c\\u0435\\u043d\\u044f\\u0442\\u044c/);
  assert.match(settingsClientCardSource, /readonly: "\\u0422\\u043e\\u043b\\u044c\\u043a\\u043e \\u0447\\u0442\\u0435\\u043d\\u0438\\u0435"/);
  assert.match(settingsClientCardSource, /"readonly"/);
  assert.match(settingsClientCardSource, /ruleActionLabels\[value\] \|\| value/);
  assert.doesNotMatch(settingsClientCardSource, />readonly<\/option>|>editable<\/option>|>template<\/option>|>literal<\/option>/);
});

test('rule condition value input follows selected field type', () => {
  assert.match(settingsClientCardSource, /function syncRuleConditionValueControl/);
  assert.match(settingsClientCardSource, /function normalizedFieldOptions/);
  assert.match(settingsClientCardSource, /const options = normalizedFieldOptions\(field\)/);
  assert.match(settingsClientCardSource, /type === "select" && options\.length/);
  assert.match(settingsClientCardSource, /<select id="ruleValue" data-rule-condition-value>/);
  assert.match(settingsClientCardSource, /option\.label/);
  assert.match(settingsClientCardSource, /type === "checkbox"/);
  assert.match(settingsClientCardSource, />\\u0414\\u0430<\/option>/);
  assert.match(settingsClientCardSource, /inputType = type === "number" \? "number" : "text"/);
  assert.match(settingsClientCardSource, /el\.ruleValueWrap\.classList\.toggle\("hidden", !needsValue\)/);
  assert.match(settingsClientCardSource, /el\.ruleConditionField\.addEventListener\("change", \(\) => syncRuleConditionValueControl\(""\)\)/);
});

test('source cards open editor from free area and keep controls isolated', () => {
  const sourcesSource = sourceBetween(settingsClientCardSource, 'function renderClientCardSources', 'async function loadClientCardConfig');
  assert.match(sourcesSource, /row\.tabIndex = 0/);
  assert.match(sourcesSource, /row\.setAttribute\("role", "button"\)/);
  assert.match(sourcesSource, /bindKeyboardOpen\(row, \(\) => openSourceModal\(source\.id\)\)/);
  assert.match(sourcesSource, /if \(boundary && boundary !== row\) return;/);
  assert.doesNotMatch(sourcesSource, /data-edit-source-id/);
  assert.match(sourcesSource, /data-test-source-id/);
  assert.match(sourcesSource, /data-toggle-source-id/);
  assert.match(sourcesSource, /event\.preventDefault\(\);[\s\S]*event\.stopPropagation\(\);[\s\S]*deleteSource/);
  assert.match(sourcesSource, /event\.stopPropagation\(\);[\s\S]*toggleSource/);
});

test('settings blocks and field rows use free-area click without wide checkbox or color hit area', () => {
  const settingsSource = sourceBetween(settingsClientCardSource, 'function renderClientCardSettings', 'function renderClientCardRules');
  assert.match(settingsSource, /card\.tabIndex = 0/);
  assert.match(settingsSource, /card\.setAttribute\("role", "button"\)/);
  assert.match(settingsSource, /toggleBlock\(block\.id\)/);
  assert.match(settingsSource, /const boundary = interactiveBoundary\(event\.target\)/);
  assert.match(settingsSource, /if \(boundary && boundary !== card\) return/);
  assert.doesNotMatch(settingsSource, /data-toggle-block-id|compact-icon/);
  assert.match(settingsSource, /data-open-field-id="\$\{escapeHtml\(field\.id\)\}"/);
  assert.doesNotMatch(settingsSource, /data-edit-field-id/);
  assert.match(settingsSource, /event\.stopPropagation\(\);[\s\S]*openEditFieldModal\(fieldId\)/);
  assert.match(settingsSource, /openEditFieldModal\(fieldId\)/);
  assert.match(stylesSource, /\.checkbox-label \{[\s\S]*width: fit-content;[\s\S]*max-width: max-content;/);
  assert.match(settingsSource, /client-card-block-color-label/);
  assert.match(settingsSource, /client-card-block-color-control/);
  assert.match(settingsSource, /for="client-card-color-\$\{escapeHtml\(block\.id\)\}"/);
  assert.match(settingsSource, /id="client-card-color-\$\{escapeHtml\(block\.id\)\}"/);
  assert.match(stylesSource, /\.client-card-block-color-control \{[\s\S]*width: fit-content;[\s\S]*max-width: max-content;/);
  assert.match(stylesSource, /\.client-card-block-color-label \{[\s\S]*display: inline-flex;[\s\S]*width: fit-content;[\s\S]*max-width: max-content;[\s\S]*cursor: pointer;/);
  assert.match(stylesSource, /\.client-card-color-picker-row \{[\s\S]*width: fit-content;[\s\S]*max-width: max-content;/);
  assert.match(stylesSource, /\.client-card-field-row\[role="button"\]:focus-visible/);
  assert.match(stylesSource, /\.client-card-settings-block\[role="button"\]:focus-visible/);
});

test('block free-area toggle keeps controls isolated and keyboard toggle intact', () => {
  const settingsSource = sourceBetween(settingsClientCardSource, 'function interactiveBoundary', 'function renderClientCardRules');
  assert.match(settingsSource, /\[role='button'\]/);
  assert.match(settingsSource, /bindKeyboardOpen\(card, \(\) => toggleBlock\(block\.id\)\)/);
  assert.match(settingsSource, /event\.stopPropagation\(\);[\s\S]*scheduleBlockAutosave\(input\.dataset\.blockId, \{ statusText: uiText\.blockSaved \}\)/);
  assert.match(settingsSource, /event\.stopPropagation\(\);[\s\S]*moveBlock\(btn\.dataset\.moveBlockId, btn\.dataset\.direction\)/);
  assert.match(settingsSource, /event\.stopPropagation\(\);[\s\S]*moveField\(btn\.dataset\.moveFieldId, btn\.dataset\.direction\)/);
  assert.match(settingsSource, /event\.stopPropagation\(\);[\s\S]*deleteField\(btn\.dataset\.deleteFieldId\)/);
  assert.match(settingsSource, /event\.stopPropagation\(\);[\s\S]*openCreateFieldModal\(btn\.dataset\.addFieldBlockKey\)/);
});

test('top matching rule wins for conflicting runtime actions', () => {
  const runtimeSource = sourceBetween(ticketClientCardSource, 'function ruleConflictKey', 'function bindClientCardRuleInputs');
  assert.match(runtimeSource, /function compareRulePriority/);
  assert.match(runtimeSource, /field:\$\{action\.target_key\}:visibility/);
  assert.match(runtimeSource, /field:\$\{action\.target_key\}:editability/);
  assert.match(runtimeSource, /String\(a\?\.id \|\| ""\)\.localeCompare\(String\(b\?\.id \|\| ""\)\)/);
  assert.match(runtimeSource, /const resolved = new Set\(\)/);
  assert.match(runtimeSource, /if \(conflictKey && resolved\.has\(conflictKey\)\) continue/);
  assert.match(runtimeSource, /if \(conflictKey\) resolved\.add\(conflictKey\)/);
  assert.match(runtimeSource, /\.sort\(compareRulePriority\)/);
  const cardSource = sourceBetween(ticketClientCardSource, 'function applyClientCardRulesToCardConfig', 'function getClientFieldValue');
  assert.match(cardSource, /const resolved = new Set\(\)/);
  assert.match(cardSource, /if \(conflictKey && resolved\.has\(conflictKey\)\) continue/);
  assert.match(cardSource, /\.sort\(compareRulePriority\)/);
});

test('source modal is wide, adaptive and avoids horizontal scroll', () => {
  const sourceUrlRowStyles = sourceBetween(stylesSource, '#clientCardSourceModal .client-card-source-url-row {', '#clientCardSourceModal .modal-foot {');
  assert.match(stylesSource, /#clientCardSourceModal \.modal-dialog \{[\s\S]*width: min\(1120px, calc\(100vw - 32px\)\);[\s\S]*max-width: calc\(100vw - 32px\);/);
  assert.match(stylesSource, /#clientCardSourceModal \.modal-body \{[\s\S]*overflow-y: auto;[\s\S]*overflow-x: hidden;/);
  assert.match(stylesSource, /@media \(max-width: 980px\) \{[\s\S]*#clientCardSourceModal \.client-card-source-row/);
  assert.match(settingsClientCardTemplate, /class="client-card-source-url-row"/);
  assert.match(settingsClientCardTemplate, /class="client-card-source-url-tools"/);
  assert.match(sourceUrlRowStyles, /display: grid;/);
  assert.match(sourceUrlRowStyles, /grid-template-columns: minmax\(0, 1fr\) auto;/);
  assert.match(sourceUrlRowStyles, /gap: 10px;/);
  assert.match(sourceUrlRowStyles, /#clientCardSourceModal \.client-card-source-url-row > input \{[\s\S]*min-width: 0;[\s\S]*width: 100%;[\s\S]*max-width: none;/);
  assert.match(sourceUrlRowStyles, /#clientCardSourceModal \.client-card-source-url-tools \{[\s\S]*display: grid;[\s\S]*grid-template-columns: clamp\(180px, 20vw, 220px\) max-content;[\s\S]*justify-content: start;/);
  assert.match(sourceUrlRowStyles, /#clientCardSourceModal \.client-card-source-url-tools > select \{[\s\S]*width: 100%;[\s\S]*min-width: 0;/);
  assert.doesNotMatch(sourceUrlRowStyles, /justify-content: space-between;/);
  assert.match(stylesSource, /@media \(max-width: 760px\) \{[\s\S]*#clientCardSourceModal \.client-card-source-url-tools \{[\s\S]*grid-template-columns: 1fr;[\s\S]*\}[\s\S]*#clientCardSourceModal \.client-card-source-url-row \{[\s\S]*grid-template-columns: 1fr;/);
  assert.match(stylesSource, /#clientCardSourceModal \.modal-foot \{[\s\S]*flex-wrap: wrap;/);
});

test('ticket client card runtime applies configurable rules and filters payload', () => {
  assert.match(ticketClientCardSource, /function applyClientCardRules/);
  assert.match(ticketClientCardSource, /function applyClientCardRulesToCardConfig/);
  assert.match(ticketClientCardSource, /function activeEditorRuleFieldTargets/);
  assert.match(ticketClientCardSource, /function activeEditorRuleBlockTargets/);
  assert.match(ticketClientCardSource, /function fieldShownInEditorOrRuleTarget/);
  assert.match(ticketClientCardSource, /function blockShownInEditorOrRuleTarget/);
  assert.match(ticketClientCardSource, /return fieldShownInEditor\(field\) \|\| ruleTargets\.has\(field\?\.key\)/);
  assert.doesNotMatch(ticketClientCardSource, /!\(?field\?\.is_system/);
  assert.match(ticketClientCardSource, /function ruleMatches/);
  assert.match(ticketClientCardSource, /function bindClientCardRuleInputs/);
  assert.match(ticketClientCardSource, /addEventListener\("change", \(\) => applyClientCardRules\(ctx\)\)/);
  assert.match(ticketClientCardSource, /function validateClientCardRuleRequirements/);
  assert.match(ticketClientCardSource, /scope !== "card" && scope !== "both"/);
  assert.match(ticketClientCardSource, /action\.action !== "show" && action\.action !== "hide" && scope !== "editor"/);
  assert.match(ticketClientCardSource, /data-client-card-block-key/);
  assert.match(ticketClientCardSource, /function syncBlockEditorVisibility/);
  assert.match(ticketClientCardSource, /state\.hiddenBlocks\.add\(key\)/);
  assert.match(ticketClientCardSource, /state\.hiddenBlocks\.delete\(key\)/);
  assert.match(ticketClientCardSource, /const configBlock = findClientCardBlock\(ctx\.state\.clientCardViewConfig, blockKey\)/);
  assert.match(ticketClientCardSource, /for \(const field of configBlock\?\.fields \|\| \[\]\)/);
  assert.match(ticketClientCardSource, /blockShownInEditorOrRuleTarget\(block, blockRuleTargets\)/);
  assert.match(ticketClientCardSource, /if \(!blockShownInEditor\(block\)\) blockWrap\.classList\.add\("hidden"\)/);
  assert.match(ticketClientCardSource, /ctx\.state\.clientCardRuleProfile = profile \|\| \{\}/);
  assert.match(ticketClientCardSource, /if \(baseHidden\) state\.hidden\.add\(key\)/);
  assert.match(ticketClientCardSource, /if \(baseHidden\) fieldEl\.classList\.add\("hidden"\)/);
  assert.match(ticketClientCardSource, /const effectiveConfig = applyClientCardRulesToCardConfig\(config, profile\)/);
  const payloadSource = sourceBetween(ticketClientCardSource, 'function getModalPayload', 'function openClientModal');
  assert.match(payloadSource, /validateClientCardRuleRequirements\(ctx\)/);
  assert.match(payloadSource, /isRuntimeFieldHidden\(ctx, key\) \|\| isRuntimeFieldReadonly\(ctx, key\)/);
  assert.match(payloadSource, /for \(const key of Object\.keys\(payload\)\)/);
  assert.match(payloadSource, /delete payload\[key\]/);
});

test('ticket client card no longer computes derived department metadata in frontend', () => {
  assert.doesNotMatch(ticketClientCardSource, /buildDepartmentMetaPayload/);
  assert.doesNotMatch(ticketPageSource, /SALES_FIRST_DEPARTMENT/);
  assert.doesNotMatch(ticketPageSource, /SALES_SECOND_DEPARTMENT/);
  const payloadSource = sourceBetween(ticketClientCardSource, 'function getModalPayload', 'function openClientModal');
  assert.doesNotMatch(payloadSource, /departmentMeta/);
  assert.doesNotMatch(payloadSource, /first_department:/);
  assert.doesNotMatch(payloadSource, /group_name:/);
});

test('ticket client card block hide then field show still excludes field from payload', () => {
  const ensureSource = sourceBetween(ticketClientCardSource, 'function ensureRuleState', 'function fieldEditorNodes');
  const blockActionSource = sourceBetween(ticketClientCardSource, 'if (action.target_type === "block")', 'if (action.target_type !== "field")');
  const fieldActionSource = sourceBetween(ticketClientCardSource, 'if (action.action === "show")', '} else if (action.action === "readonly")');
  const hiddenSource = sourceBetween(ticketClientCardSource, 'function isRuntimeFieldHidden', 'function isRuntimeFieldReadonly');

  assert.match(ensureSource, /hiddenBlocks: new Set\(\)/);
  assert.match(blockActionSource, /state\.hiddenBlocks\.add\(key\)/);
  assert.doesNotMatch(blockActionSource, /state\.hidden\.add\(field\.key\)/);
  assert.match(fieldActionSource, /state\.hidden\.delete\(key\)/);
  assert.match(hiddenSource, /state\.hidden\.has\(key\) \|\| \(blockKey \? state\.hiddenBlocks\.has\(blockKey\) : false\)/);
});

test('ticket client card field hide then block show keeps field-level hidden overlay', () => {
  const blockActionSource = sourceBetween(ticketClientCardSource, 'if (action.target_type === "block")', 'if (action.target_type !== "field")');
  const fieldActionSource = sourceBetween(ticketClientCardSource, 'if (action.action === "show")', '} else if (action.action === "readonly")');
  const syncSource = sourceBetween(ticketClientCardSource, 'function syncBlockEditorVisibility', 'function applyClientCardRuleAction');

  assert.match(fieldActionSource, /state\.hidden\.add\(key\)/);
  assert.match(blockActionSource, /state\.hiddenBlocks\.delete\(key\)/);
  assert.doesNotMatch(blockActionSource, /state\.hidden\.delete\(field\.key\)/);
  assert.match(syncSource, /syncFieldEditorVisibility\(ctx, field\.key\)/);
});

test('ticket client card base-hidden block plus base-visible field becomes visible on matching show block rule', () => {
  const resetSource = sourceBetween(ticketClientCardSource, 'function resetClientCardRuleOverlay', 'function fieldBlockKey');
  const hiddenSource = sourceBetween(ticketClientCardSource, 'function isRuntimeFieldHidden', 'function isRuntimeFieldReadonly');
  const blockActionSource = sourceBetween(ticketClientCardSource, 'if (action.target_type === "block")', 'if (action.target_type !== "field")');

  assert.doesNotMatch(resetSource, /state\.hidden\.add\(field\.key\)/);
  assert.match(resetSource, /state\.hiddenBlocks\.add\(blockEl\.dataset\.clientCardBlockKey\)/);
  assert.match(blockActionSource, /state\.hiddenBlocks\.delete\(key\)/);
  assert.match(hiddenSource, /return state\.hidden\.has\(key\) \|\| \(blockKey \? state\.hiddenBlocks\.has\(blockKey\) : false\)/);
});

test('ticket client card admin-only base readonly cannot be removed by editable rules', () => {
  const resetSource = sourceBetween(ticketClientCardSource, 'function resetClientCardRuleOverlay', 'function fieldBlockKey');
  const readonlySource = sourceBetween(ticketClientCardSource, 'function isRuntimeFieldReadonly', 'function validateClientCardRuleRequirements');
  const editableSource = sourceBetween(ticketClientCardSource, '} else if (action.action === "editable")', '} else if (action.action === "required")');
  const customEditorSource = sourceBetween(ticketClientCardSource, 'function renderEditableCustomField', 'function renderConfiguredSystemEditorField');
  const systemEditorSource = sourceBetween(ticketClientCardSource, 'function renderConfiguredSystemEditorField', 'function renderCustomFieldsEditor');
  const editorSource = sourceBetween(ticketClientCardSource, 'function renderCustomFieldsEditor', 'global.TicketClientCard = {');

  assert.match(ticketClientCardSource, /function fieldBaseReadonly\(ctx, key\)/);
  assert.match(ticketClientCardSource, /function setControlReadonly\(control, readonly\)/);
  assert.match(ticketClientCardSource, /function renderEditableCustomField\(ctx, field, currentValue\)/);
  assert.match(resetSource, /setControlReadonly\(control, baseReadonly\)/);
  assert.match(readonlySource, /return fieldBaseReadonly\(ctx, key\) \|\| ensureRuleState\(ctx\)\.readonly\.has\(key\)/);
  assert.match(editableSource, /const baseReadonly = fieldBaseReadonly\(ctx, key\)/);
  assert.match(editableSource, /setControlReadonly\(control, baseReadonly\)/);
  assert.match(customEditorSource, /!isAdminRole\(ctx\) && field\.admin_only_edit/);
  assert.match(systemEditorSource, /!isAdminRole\(ctx\) && field\.admin_only_edit/);
  assert.match(editorSource, /renderEditableCustomField\(ctx, field, currentValue\)/);
});

test('ticket client card modal payload omits system fields that are not edited by the modal', () => {
  const payloadSource = sourceBetween(ticketClientCardSource, 'function getModalPayload', 'function openClientModal');
  for (const key of ['email', 'phone', 'verification_status', 'employee_type', 'external_user_id']) {
    assert.doesNotMatch(payloadSource, new RegExp(`${key}:\\s*null`));
  }
});

test('settings client card rule move buttons use escaped arrows without mojibake', () => {
  assert.match(settingsClientCardSource, /data-direction="up">\\u2191<\/button>/);
  assert.match(settingsClientCardSource, /data-direction="down">\\u2193<\/button>/);
  assert.doesNotMatch(settingsClientCardSource, /\u0442\u0416|\u0432\u2020/);
});

test('AnyDesk visibility is represented by default rule config without renderer hardcode', () => {
  const defaultConfigSource = sourceBetween(ticketClientCardSource, 'function defaultClientCardViewConfig()', 'function clientCardColorClass');
  const syncSource = sourceBetween(ticketClientCardSource, 'function syncDynamicModalFields', 'function fillModal');
  const seedSource = fs.readFileSync('api/app/services/seed_data.py', 'utf8');
  assert.match(defaultConfigSource, /key: "anydesk", label: "Anydesk"[\s\S]*is_visible: false[\s\S]*is_visible_in_editor: false/);
  assert.match(defaultConfigSource, /condition_field_key: "location_type"/);
  assert.match(defaultConfigSource, /condition_value: "\\u0423\\u0434\\u0430\\u043b\\u0451\\u043d\\u043d\\u044b\\u0439"/);
  assert.match(defaultConfigSource, /target_key: "anydesk", action: "show", scope: "both"/);
  assert.match(seedSource, /\("anydesk", "Anydesk", "text", "department", 80, False, True, True\)/);
  assert.match(seedSource, /"condition_field_key": "location_type"/);
  assert.match(seedSource, /"target_key": "anydesk", "action": "show", "scope": "both"/);
  assert.doesNotMatch(syncSource, /Удал|\\u0423\\u0434\\u0430\\u043b|anydesk|fAnydesk/);
});

test('ticket client card keeps derived department metadata hidden by default in card config', () => {
  const defaultConfigSource = sourceBetween(ticketClientCardSource, 'function defaultClientCardViewConfig()', 'function clientCardColorClass');
  const seedSource = fs.readFileSync('api/app/services/seed_data.py', 'utf8');
  assert.match(defaultConfigSource, /key: "department", label: "Отдел"[\s\S]*is_visible: false/);
  assert.match(defaultConfigSource, /key: "first_department", label: "Департамент"[\s\S]*is_visible: false/);
  assert.match(defaultConfigSource, /key: "group_name", label: "Группа"[\s\S]*is_visible: false/);
  assert.match(seedSource, /\("department", "Отдел", "readonly", "department", 40, False, False, True\)/);
  assert.match(seedSource, /\("first_department", "Департамент", "readonly", "department", 50, False, False, True\)/);
  assert.match(seedSource, /\("group_name", "Группа", "readonly", "department", 60, False, False, True\)/);
});

test('ticket client card custom fields editor is exported and used by shared modal flow', () => {
  assert.equal((ticketClientCardSource.match(/function renderCustomFieldsEditor/g) || []).length, 1);
  const exportSource = ticketClientCardSource.slice(ticketClientCardSource.indexOf('global.TicketClientCard = {'));
  assert.match(exportSource, /renderCustomFieldsEditor/);
  const fillModalSource = sourceBetween(ticketClientCardSource, 'function fillModal', 'function parseCustomFieldValueForPayload');
  assert.match(fillModalSource, /renderCustomFieldsEditor\(ctx, profile\)/);
});

test('ticket client card modal editor follows configured mixed system/custom field order', () => {
  assert.match(ticketClientCardSource, /const profileSystemFieldKeys = new Set/);
  const editorSource = sourceBetween(ticketClientCardSource, 'function renderCustomFieldsEditor', 'global.TicketClientCard = {');
  assert.match(editorSource, /\.sort\(\(a, b\) => \(a\.sort_order \|\| 0\) - \(b\.sort_order \|\| 0\)\)/);
  assert.match(editorSource, /if \(field\.is_system\) \{/);
  assert.match(editorSource, /renderConfiguredSystemEditorField\(ctx, field, currentValue\)/);
  assert.match(editorSource, /document\.createElement\("section"\)/);
  assert.match(editorSource, /blockWrap\.className = "modal-field-section custom-field-editor-block"/);
  assert.match(editorSource, /if \(block\.id\) blockWrap\.dataset\.blockId = block\.id/);
  assert.match(editorSource, /blockWrap\.dataset\.blockKey = block\.key/);
  assert.match(editorSource, /blockWrap\.dataset\.clientCardBlockKey = block\.key/);
  assert.match(editorSource, /blockWrap\.dataset\.clientCardGeneratedSection = "1"/);
  assert.match(editorSource, /blockWrap\.dataset\.blockColorScheme = String\(block\.color_scheme \|\| ""\)/);
  assert.match(editorSource, /applyClientCardEditorSectionColor\(blockWrap, block\.color_scheme\)/);
  assert.match(editorSource, /sectionParent\.insertBefore\(blockWrap, el\.customFieldsSection\)/);
  assert.doesNotMatch(editorSource, /customFieldsContainer\.appendChild\(blockWrap\)/);
  assert.doesNotMatch(editorSource, /filter\(\(field\) => !profileSystemFieldKeys\.has\(field\?\.key\)\)/);
});

test('ticket client card modal editor keeps system blocks when their fields are editor-visible or rule targets', () => {
  const defaultConfigSource = sourceBetween(ticketClientCardSource, 'function defaultClientCardViewConfig()', 'function clientCardColorClass');
  const editorSource = sourceBetween(ticketClientCardSource, 'function renderCustomFieldsEditor', 'global.TicketClientCard = {');
  const seedSource = fs.readFileSync('api/app/services/seed_data.py', 'utf8');
  const clientCardServiceSource = fs.readFileSync('api/app/services/client_card.py', 'utf8');
  const settingsRouterSource = fs.readFileSync('api/app/routers/settings_client_card.py', 'utf8');

  assert.match(defaultConfigSource, /key: "tg"[\s\S]*show_in_editor: true[\s\S]*key: "telegram_username"[\s\S]*is_visible_in_editor: true[\s\S]*is_editable: false/);
  assert.match(defaultConfigSource, /key: "department"[\s\S]*key: "destination"[\s\S]*is_visible_in_editor: true[\s\S]*key: "location_type"[\s\S]*is_visible_in_editor: true/);
  assert.match(defaultConfigSource, /key: "sand_owner"[\s\S]*is_visible_in_editor: false/);
  assert.match(defaultConfigSource, /key: "sales_group"[\s\S]*is_visible_in_editor: false/);
  assert.match(defaultConfigSource, /key: "work"[\s\S]*key: "internal_number"[\s\S]*is_visible_in_editor: true[\s\S]*key: "bitrix_id"[\s\S]*is_visible_in_editor: true/);
  assert.match(defaultConfigSource, /key: "verification"[\s\S]*key: "is_verified"[\s\S]*is_visible_in_editor: true/);
  assert.match(editorSource, /fieldShownInEditorOrRuleTarget\(field, ruleTargets\)/);
  assert.match(ticketClientCardSource, /return fieldShownInEditor\(field\) \|\| ruleTargets\.has\(field\?\.key\)/);
  assert.match(editorSource, /renderConfiguredSystemEditorField\(ctx, field, currentValue\)/);
  assert.match(editorSource, /if \(baseHidden\) fieldEl\.classList\.add\("hidden"\)/);
  assert.match(seedSource, /system_field_editor_visibility_defaults = \{/);
  for (const key of ['telegram_username', 'telegram_display_name', 'last_name', 'contact_name', 'destination', 'location_type', 'internal_number', 'bitrix_login', 'bitrix_password', 'bitrix_id', 'is_verified']) {
    assert.match(seedSource, new RegExp(`"${key}": True`));
  }
  for (const key of ['sand_owner', 'sales_group', 'department', 'first_department', 'group_name', 'anydesk']) {
    assert.match(seedSource, new RegExp(`"${key}": False`));
  }
  assert.match(clientCardServiceSource, /SYSTEM_FIELD_EDITOR_VISIBILITY_DEFAULTS = \{/);
  assert.match(clientCardServiceSource, /_field_editor_visibility_default\(field\)/);
  assert.match(settingsRouterSource, /from \.\.services\.client_card import _field_editor_visibility_default/);
});

test('ticket client card renders bitrix password by presence flag and reveals through context endpoint', () => {
  const valueSource = sourceBetween(ticketClientCardSource, 'function getClientFieldValue', 'function renderClientDynamicCard');
  const revealSource = sourceBetween(ticketClientCardSource, 'async function revealBitrixPassword', 'function bindBitrixPasswordField');

  assert.match(ticketClientCardSource, /function generateBitrixPasswordMask\(\)/);
  assert.match(ticketClientCardSource, /const bytes = new Uint8Array\(10\)/);
  assert.match(ticketClientCardSource, /return Array\.from\(bytes, \(byte\) => alphabet\[byte % alphabet\.length\]\)\.join\(""\)/);
  assert.match(valueSource, /if \(key === "bitrix_password"\) return profile\.bitrix_password_set \? "\*{8}" : null/);
  assert.match(valueSource, /if \(key === "bitrix_password"\) return !ctx\.isBlank\(value\)/);
  assert.match(valueSource, /function bindBitrixPasswordValueReveal/);
  assert.match(valueSource, /function resetBitrixPasswordValueEl\(ctx, valueEl, key = "bitrix_password"\)/);
  assert.match(valueSource, /valueEl\.textContent = bitrixPasswordMask\(ctx, key\)/);
  assert.match(valueSource, /valueEl\.addEventListener\("click", async \(\) =>/);
  assert.match(ticketClientCardSource, /clientCardBitrixPasswordReveal/);
  assert.match(ticketClientCardSource, /const PASSWORD_REVEAL_MS = 5000/);
  assert.match(ticketClientCardSource, /state\.revealedUntil = Date\.now\(\) \+ PASSWORD_REVEAL_MS/);
  assert.match(ticketClientCardSource, /function activeBitrixPasswordCardReveal/);
  assert.match(ticketClientCardSource, /function scheduleBitrixPasswordCardRevealHide/);
  assert.match(ticketClientCardSource, /hideBitrixPasswordCardReveal\(ctx, ownerKey, key\)/);
  assert.match(valueSource, /applyBitrixPasswordValueRevealState\(ctx, valueEl\)/);
  assert.match(ticketClientCardSource, /if \(!profile\?\.bitrix_password_set\) clearBitrixPasswordCardReveal\(ctx\)/);
  assert.doesNotMatch(valueSource, /valueEl\.addEventListener\("blur"/);
  assert.match(valueSource, /resetBitrixPasswordValueEl\(ctx, valueEl, key\)/);
  assert.match(revealSource, /async function revealBitrixPassword\(ctx, key = "bitrix_password"\)/);
  assert.match(revealSource, /const suffix = key === "bitrix_password" \? "bitrix-password" : "web-password"/);
  assert.match(revealSource, /\/api\/v1\/tickets\/\$\{encodeURIComponent\(ctx\.state\.ticketId\)\}\/client-profile\/\$\{suffix\}/);
  assert.match(revealSource, /\/api\/v1\/management\/users\/\$\{encodeURIComponent\(clientId\)\}\/profile\/\$\{suffix\}/);
});

test('ticket client card reveal survives rerender while async reveal is pending', async () => {
  class FakeElement extends ClientCardDomMock {
    constructor(tagName) {
      super();
      this.tagName = tagName.toUpperCase();
      this.children = [];
      this.dataset = {};
      this.eventListeners = {};
      this.textContent = '';
      this.parentElement = null;
      this.classList = {
        values: new Set(),
        add: (...items) => items.forEach((item) => this.classList.values.add(item)),
        remove: (...items) => items.forEach((item) => this.classList.values.delete(item)),
        contains: (item) => this.classList.values.has(item),
        toggle: (item, force) => {
          const enabled = force === undefined ? !this.classList.values.has(item) : Boolean(force);
          if (enabled) this.classList.values.add(item);
          else this.classList.values.delete(item);
        },
      };
      this.style = { setProperty() {} };
    }
    set innerHTML(value) {
      this.children = [];
      this.textContent = value;
    }
    addEventListener(type, handler) {
      this.eventListeners[type] = this.eventListeners[type] || [];
      this.eventListeners[type].push(handler);
    }
    querySelector(selector) {
      if (selector !== "[data-client-card-bitrix-password-value='1']") return null;
      const queue = [...this.children];
      while (queue.length) {
        const item = queue.shift();
        if (item.dataset.clientCardBitrixPasswordValue === '1') return item;
        queue.push(...item.children);
      }
      return null;
    }
  }

  let now = 1000;
  const timers = [];
  let revealResolve;
  const revealPromise = new Promise((resolve) => {
    revealResolve = resolve;
  });
  const document = {
    createElement: (tagName) => new FakeElement(tagName),
  };
  const window = {};
  const context = vm.createContext({
    window,
    document,
    console,
    JSON,
    Array,
    Set,
    Map,
    String,
    Object,
    Number,
    Math,
    Uint8Array,
    Date: { now: () => now },
    setTimeout: (handler, delay) => {
      timers.push({ handler, delay });
      return timers.length;
    },
    clearTimeout() {},
  });
  vm.runInContext(ticketClientCardSource, context);

  const revealRequests = [];
  const ctx = {
    state: { ticketId: 'ticket-1', card: { client_id: 'client-1' } },
    el: { clientDynamicCard: new FakeElement('div') },
    api: (url) => { revealRequests.push(url); return revealPromise; },
    isBlank: (value) => value === null || value === undefined || String(value).trim() === '',
  };
  const config = {
    blocks: [{
      key: 'work',
      title: 'Work',
      is_active: true,
      show_in_card: true,
      fields: [{ key: 'bitrix_password', label: 'Пароль Bitrix', field_type: 'password', is_visible: true }],
    }],
    rules: [],
  };

  window.TicketClientCard.renderClientDynamicCard(ctx, { bitrix_password_set: true }, config);
  const oldValueEl = ctx.el.clientDynamicCard.querySelector("[data-client-card-bitrix-password-value='1']");
  assert.equal(oldValueEl.textContent.length, 10);
  assert.equal(oldValueEl.classList.contains('client-card-password'), true);

  const clickPromise = oldValueEl.eventListeners.click[0]();
  window.TicketClientCard.renderClientDynamicCard(ctx, { bitrix_password_set: true }, config);
  const currentValueEl = ctx.el.clientDynamicCard.querySelector("[data-client-card-bitrix-password-value='1']");
  assert.equal(currentValueEl, oldValueEl);
  assert.equal(currentValueEl.parentElement.children.filter(node => node === currentValueEl).length, 1);
  assert.equal(currentValueEl.eventListeners.click.length, 1);
  assert.equal(currentValueEl.getAttribute('role'), 'button');
  assert.equal(currentValueEl.getAttribute('aria-disabled'), 'false');
  assert.equal(currentValueEl.tabIndex, 0);
  assert.equal(currentValueEl.textContent.length, 10);

  revealResolve({ bitrix_password: 'plain-secret' });
  await clickPromise;

  assert.equal(oldValueEl.textContent, 'plain-secret');
  assert.equal(currentValueEl.textContent, 'plain-secret');
  assert.deepEqual(revealRequests, ['/api/v1/tickets/ticket-1/client-profile/bitrix-password']);
  assert.equal(currentValueEl.classList.contains('client-card-password-revealed'), true);
  assert.equal(ctx.state.clientCardBitrixPasswordReveal.plaintext, 'plain-secret');
  assert.equal(ctx.state.clientCardBitrixPasswordReveal.revealedUntil, 6000);
  assert.equal(timers[0].delay, 5000);

  now = 6000;
  timers[0].handler();
  assert.equal(ctx.state.clientCardBitrixPasswordReveal.plaintext, '');
  assert.equal(currentValueEl.textContent.length, 10);
  assert.equal(currentValueEl.classList.contains('client-card-password'), true);
});

test('ticket client card ignores async reveal result after owner changes', async () => {
  class FakeElement extends ClientCardDomMock {
    constructor(tagName) {
      super();
      this.tagName = tagName.toUpperCase();
      this.children = [];
      this.dataset = {};
      this.eventListeners = {};
      this.textContent = '';
      this.classList = {
        values: new Set(),
        add: (...items) => items.forEach((item) => this.classList.values.add(item)),
        remove: (...items) => items.forEach((item) => this.classList.values.delete(item)),
        contains: (item) => this.classList.values.has(item),
      };
      this.style = { setProperty() {} };
    }
    set innerHTML(value) {
      this.children = [];
      this.textContent = value;
    }
    addEventListener(type, handler) {
      this.eventListeners[type] = this.eventListeners[type] || [];
      this.eventListeners[type].push(handler);
    }
    querySelector(selector) {
      if (selector !== "[data-client-card-bitrix-password-value='1']") return null;
      const queue = [...this.children];
      while (queue.length) {
        const item = queue.shift();
        if (item.dataset.clientCardBitrixPasswordValue === '1') return item;
        queue.push(...item.children);
      }
      return null;
    }
  }

  const document = { createElement: (tagName) => new FakeElement(tagName) };
  const window = {};
  let revealResolve;
  const revealPromise = new Promise((resolve) => {
    revealResolve = resolve;
  });
  const context = vm.createContext({
    window,
    document,
    console,
    JSON,
    Array,
    Set,
    Map,
    String,
    Object,
    Number,
    Math,
    Uint8Array,
    Date: { now: () => 1000 },
    setTimeout: () => 1,
    clearTimeout() {},
  });
  vm.runInContext(ticketClientCardSource, context);

  const ctx = {
    state: { ticketId: 'ticket-1', card: { client_id: 'client-1' } },
    el: { clientDynamicCard: new FakeElement('div') },
    api: () => revealPromise,
    isBlank: (value) => value === null || value === undefined || String(value).trim() === '',
  };
  const config = {
    blocks: [{
      key: 'work',
      title: 'Work',
      is_active: true,
      show_in_card: true,
      fields: [{ key: 'bitrix_password', label: 'Пароль Bitrix', field_type: 'password', is_visible: true }],
    }],
    rules: [],
  };

  window.TicketClientCard.renderClientDynamicCard(ctx, { bitrix_password_set: true }, config);
  const valueEl = ctx.el.clientDynamicCard.querySelector("[data-client-card-bitrix-password-value='1']");
  const clickPromise = valueEl.eventListeners.click[0]();
  ctx.state.card.client_id = 'client-2';
  window.TicketClientCard.renderClientDynamicCard(ctx, { bitrix_password_set: true }, config);

  revealResolve({ bitrix_password: 'plain-secret' });
  await clickPromise;

  const currentValueEl = ctx.el.clientDynamicCard.querySelector("[data-client-card-bitrix-password-value='1']");
  assert.notEqual(currentValueEl, valueEl);
  assert.notEqual(valueEl.textContent, 'plain-secret');
  assert.notEqual(currentValueEl.textContent, 'plain-secret');
  assert.equal(currentValueEl.textContent.length, 10);
  assert.equal(ctx.state.clientCardBitrixPasswordReveal?.plaintext || '', '');
});

test('ticket client card sends null when dirty bitrix password field is cleared', () => {
  const payloadSource = sourceBetween(ticketClientCardSource, 'function getModalPayload', 'function openClientModal');

  assert.match(payloadSource, /if \(bitrixPasswordState\(ctx\)\.dirty\) \{/);
  assert.match(payloadSource, /payload\.bitrix_password = bitrixPasswordState\(ctx\)\.draftValue\.trim\(\) \|\| null/);
  assert.doesNotMatch(payloadSource, /payload\.bitrix_password = el\.fBitrixPassword\.value/);
  assert.doesNotMatch(payloadSource, /bitrixPasswordState\(ctx\)\.dirty && el\.fBitrixPassword\.value\.trim\(\)/);
});

test('bitrix password editor uses visual mask without browser password autofill', () => {
  const editorSource = sourceBetween(ticketClientCardSource, 'function bitrixPasswordState', 'function ensureRuleState');

  assert.match(editorSource, /draftValue: ""/);
  assert.match(editorSource, /state\.hasSaved = false/);
  assert.match(editorSource, /state\.draftValue = ""/);
  assert.match(editorSource, /el\.fBitrixPassword\.type = "text"/);
  assert.match(editorSource, /el\.fBitrixPassword\.autocomplete = "off"/);
  assert.match(editorSource, /el\.fBitrixPassword\.value = bitrixPasswordMask\(ctx\)/);
  assert.match(editorSource, /state\.draftValue = el\.fBitrixPassword\.value/);
  assert.doesNotMatch(editorSource, /fBitrixPassword\.type = "password"/);
  assert.doesNotMatch(ticketTemplate, /id="fBitrixPassword" autocomplete="new-password"/);
  assert.doesNotMatch(managementUsersTemplate, /id="fBitrixPassword" autocomplete="new-password"/);
  assert.match(ticketTemplate, /id="fBitrixPassword" autocomplete="off"/);
  assert.match(managementUsersTemplate, /id="fBitrixPassword" autocomplete="off"/);
});

test('bitrix password blur is controlled by javascript reveal class without hover reveal', () => {
  assert.match(stylesSource, /\.client-card-password\s*\{[\s\S]*filter:\s*blur\(4px\)/);
  assert.match(stylesSource, /\.client-card-password\.client-card-password-revealed\s*\{[\s\S]*filter:\s*blur\(0\)/);
  assert.doesNotMatch(stylesSource, /\.client-card-password:hover\s*\{/);
  assert.doesNotMatch(stylesSource, /#clientBitrixPasswordText:hover\s*\{/);
});

test('ticket client card modal editor derives section color from block config instead of block key', () => {
  const colorSource = sourceBetween(ticketClientCardSource, 'function clientCardColorHex', 'function blockShownInCard');
  const editorSource = sourceBetween(ticketClientCardSource, 'function renderCustomFieldsEditor', 'global.TicketClientCard = {');
  const modalSectionTitleStyle = sourceBetween(stylesSource, '.modal-field-section > .client-card-block-title {', '.modal-body label {');
  const darkModalSectionTitleStyle = sourceBetween(stylesSource, 'body[data-theme="dark"] .modal-field-section > .client-card-block-title {', 'body[data-theme="dark"] .modal-field-section label,');
  assert.match(colorSource, /function clientCardColorHex/);
  assert.match(colorSource, /blue: "#dbeafe"/);
  assert.match(colorSource, /purple: "#ede9fe"/);
  assert.match(colorSource, /if \(isHexColor\(value\)\) return value\.toLowerCase\(\)/);
  assert.match(colorSource, /function blendHexColors/);
  assert.match(colorSource, /function contrastRatio/);
  assert.match(colorSource, /function contrastTextColor/);
  assert.match(colorSource, /function applyClientCardEditorSectionColor/);
  assert.match(colorSource, /const lightBackground = blendHexColors\(hex, "#ffffff", 0\.86\)/);
  assert.match(colorSource, /const darkBackground = blendHexColors\(hex, "#0f172a", 0\.58\)/);
  assert.match(colorSource, /const lightText = contrastTextColor\(lightBackground\)/);
  assert.match(colorSource, /const darkText = contrastTextColor\(darkBackground\)/);
  assert.match(colorSource, /sectionEl\.style\.setProperty\("--client-card-editor-bg", lightBackground\)/);
  assert.match(colorSource, /sectionEl\.style\.setProperty\("--client-card-editor-title-text", lightText\)/);
  assert.match(colorSource, /sectionEl\.style\.setProperty\("--client-card-editor-label-text", lightText\)/);
  assert.match(colorSource, /sectionEl\.style\.setProperty\("--client-card-editor-title-text-dark", darkText\)/);
  assert.match(colorSource, /sectionEl\.style\.setProperty\("--client-card-editor-label-text-dark", darkText\)/);
  assert.match(editorSource, /applyClientCardEditorSectionColor\(blockWrap, block\.color_scheme\)/);
  assert.doesNotMatch(colorSource, /block\.key === "tg"|block\.key === "profile"|block\.key === "department"|block\.key === "work"|block\.key === "verification"/);
  assert.doesNotMatch(editorSource, /editorBlockSectionClass\(block\.key\)/);
  assert.match(stylesSource, /\.modal-field-section \{[\s\S]*background: var\(--client-card-editor-bg, #e5e7eb\);/);
  assert.match(modalSectionTitleStyle, /display: block;/);
  assert.match(modalSectionTitleStyle, /color: var\(--client-card-editor-title-text, #0f172a\);/);
  assert.doesNotMatch(modalSectionTitleStyle, /border-radius: 999px/);
  assert.doesNotMatch(modalSectionTitleStyle, /background: var\(--client-card-editor-title-bg/);
  assert.match(stylesSource, /body\[data-theme="dark"\] \.modal-field-section \{[\s\S]*background: var\(--client-card-editor-bg-dark, rgba\(14, 28, 48, 0\.56\)\);/);
  assert.match(darkModalSectionTitleStyle, /color: var\(--client-card-editor-title-text-dark, #f8fafc\);/);
  assert.match(stylesSource, /body\[data-theme="dark"\] \.client-card-field-readonly-rule input,[\s\S]*color: #e2e8f0;/);
  assert.doesNotMatch(stylesSource, /\.modal-field-section-profile|\.modal-field-section-department|\.modal-field-section-work|\.modal-field-section-verification/);
});

test('ticket client card modal editor chooses text color by contrast ratio on blended background', () => {
  const helpersSource = sourceBetween(ticketClientCardSource, 'function hexLuminance', 'function applyClientCardBlockColor');
  const editorColorSource = sourceBetween(ticketClientCardSource, 'function applyClientCardEditorSectionColor', 'function blockShownInCard');
  assert.match(helpersSource, /function contrastRatio/);
  assert.match(helpersSource, /return \(lighter \+ 0\.05\) \/ \(darker \+ 0\.05\)/);
  assert.match(helpersSource, /const dark = "#000000"/);
  assert.match(helpersSource, /const light = "#ffffff"/);
  assert.match(helpersSource, /return contrastRatio\(backgroundHex, dark\) >= contrastRatio\(backgroundHex, light\) \? dark : light/);
  assert.match(editorColorSource, /const lightBackground = blendHexColors\(hex, "#ffffff", 0\.86\)/);
  assert.match(editorColorSource, /const darkBackground = blendHexColors\(hex, "#0f172a", 0\.58\)/);
  assert.match(editorColorSource, /const lightText = contrastTextColor\(lightBackground\)/);
  assert.match(editorColorSource, /const darkText = contrastTextColor\(darkBackground\)/);
  assert.doesNotMatch(editorColorSource, /contrastTextColor\(hex\)/);

  const blendHexColors = (foreground, background, alpha) => {
    const parse = (hex) => [1, 3, 5].map((index) => Number.parseInt(hex.slice(index, index + 2), 16));
    const [fr, fg, fb] = parse(foreground);
    const [br, bg, bb] = parse(background);
    const mix = (front, back) => Math.round((front * alpha) + (back * (1 - alpha)));
    return `#${[mix(fr, br), mix(fg, bg), mix(fb, bb)].map((value) => value.toString(16).padStart(2, '0')).join('')}`;
  };
  const luminance = (hex) => {
    const channels = [1, 3, 5].map((index) => Number.parseInt(hex.slice(index, index + 2), 16) / 255);
    const normalized = channels.map((channel) => (
      channel <= 0.03928 ? channel / 12.92 : Math.pow((channel + 0.055) / 1.055, 2.4)
    ));
    return (0.2126 * normalized[0]) + (0.7152 * normalized[1]) + (0.0722 * normalized[2]);
  };
  const contrastRatio = (backgroundHex, textHex) => {
    const background = luminance(backgroundHex);
    const text = luminance(textHex);
    const lighter = Math.max(background, text);
    const darker = Math.min(background, text);
    return (lighter + 0.05) / (darker + 0.05);
  };
  const contrastTextColor = (backgroundHex) => (
    contrastRatio(backgroundHex, '#000000') >= contrastRatio(backgroundHex, '#ffffff') ? '#000000' : '#ffffff'
  );

  const lightPinkBackground = blendHexColors('#f4a7c5', '#ffffff', 0.86);
  const lightGreenBackground = blendHexColors('#8fd19e', '#ffffff', 0.86);
  const darkBackground = blendHexColors('#4b1d6b', '#0f172a', 0.58);

  assert.equal(contrastTextColor(lightPinkBackground), '#000000');
  assert.equal(contrastTextColor(lightGreenBackground), '#000000');
  assert.equal(contrastTextColor(darkBackground), '#ffffff');
});

test('ticket client card editor builds sibling modal sections instead of one custom wrapper', () => {
  const editorSource = sourceBetween(ticketClientCardSource, 'function renderCustomFieldsEditor', 'global.TicketClientCard = {');
  const fieldNodesSource = sourceBetween(ticketClientCardSource, 'function fieldEditorNodes', 'function selectorKey');
  const payloadSource = sourceBetween(ticketClientCardSource, 'function getModalPayload', 'function openClientModal');

  assert.match(editorSource, /const sectionParent = el\.customFieldsSection\.parentElement/);
  assert.match(editorSource, /Array\.from\(sectionParent\.children\)\.forEach/);
  assert.match(editorSource, /node\.dataset\?\.clientCardGeneratedSection === "1"/);
  assert.match(editorSource, /el\.customFieldsSection\.classList\.add\("hidden"\)/);
  assert.match(editorSource, /sectionParent\.insertBefore\(blockWrap, el\.customFieldsSection\)/);
  assert.match(editorSource, /\.filter\(\(block\) => blockShownInEditorOrRuleTarget\(block, blockRuleTargets\)\)/);
  assert.match(editorSource, /\.filter\(\(field\) => fieldShownInEditorOrRuleTarget\(field, ruleTargets\)\)/);
  assert.match(fieldNodesSource, /ctx\.el\.clientModal\?\.querySelector/);
  assert.match(fieldNodesSource, /data-client-card-field-key/);
  assert.match(payloadSource, /\(el\.clientModal \|\| el\.customFieldsContainer\)\.querySelectorAll\("\[data-custom-field-key\]"\)/);
});

test('ticket and management users pages use the shared TicketClientCard modal renderer', () => {
  assert.match(ticketPageSource, /return TicketClientCard\.fillModal\(clientCardCtx\(\), profile\)/);
  assert.match(ticketPageSource, /return TicketClientCard\.renderCustomFieldsEditor\(clientCardCtx\(\), profile\)/);
  assert.match(managementUsersSource, /window\.TicketClientCard\.fillModal\(clientCardCtx\(root\), profile \|\| \{\}\)/);
  assert.doesNotMatch(managementUsersSource, /function renderCustomFieldsEditor/);
  assert.match(ticketClientCardSource, /applyClientCardEditorSectionColor\(blockWrap, block\.color_scheme\)/);
});

test('ordinary card and editor use the same runtime color field and do not cross-apply key-based modal colors', () => {
  const cardSource = sourceBetween(ticketClientCardSource, 'function renderClientDynamicCard', 'function fillClientPanel');
  const editorSource = sourceBetween(ticketClientCardSource, 'function renderCustomFieldsEditor', 'global.TicketClientCard = {');
  assert.match(cardSource, /const color = block\.color_scheme \|\| ""/);
  assert.match(cardSource, /if \(entry\.color !== color\) \{[\s\S]*clientCardColorClass\(color\)/);
  assert.match(cardSource, /applyClientCardBlockColor\(blockEl, color\)/);
  assert.match(cardSource, /entry\.color = color/);
  assert.match(editorSource, /blockWrap\.dataset\.blockColorScheme = String\(block\.color_scheme \|\| ""\)/);
  assert.match(editorSource, /applyClientCardEditorSectionColor\(blockWrap, block\.color_scheme\)/);
  assert.match(editorSource, /blockWrap\.className = "modal-field-section custom-field-editor-block"/);
  assert.doesNotMatch(editorSource, /blockWrap\.className = .*modal-field-section-profile|blockWrap\.className = .*modal-field-section-department|blockWrap\.className = .*modal-field-section-work|blockWrap\.className = .*modal-field-section-verification/);
  assert.doesNotMatch(editorSource, /blocks\[\s*index|blocks\[index\]|blockIndex.*color|color.*blockIndex/);
});

test('client card settings renders source list and source editor controls', () => {
  assert.match(settingsClientCardTemplate, /id="clientCardSources"/);
  assert.match(settingsClientCardTemplate, /id="addClientCardSourceBtn"/);
  assert.match(settingsClientCardTemplate, /id="clientCardSourceModal"/);
  assert.match(settingsClientCardTemplate, /id="sourceQueryRows"/);
  assert.match(settingsClientCardTemplate, /id="sourceHeaderRows"/);
  assert.match(settingsClientCardTemplate, /id="sourceBodyJson"/);
  assert.match(settingsClientCardTemplate, /id="sourceMappingRows"/);
  assert.match(settingsClientCardTemplate, /CLIENT_CARD_SOURCE_SECRET_&lt;NAME&gt;/);
  assert.match(settingsClientCardSource, /function renderClientCardSources/);
  assert.match(settingsClientCardSource, /\/api\/v1\/settings\/client-card\/sources/);
  assert.match(settingsClientCardSource, /function sourcePayloadFromModal/);
  assert.match(settingsClientCardSource, /function analyzeTemplateObject/);
  assert.match(settingsClientCardSource, /function serializeTemplateRows/);
  assert.match(settingsClientCardSource, /function parseBodyJson/);
  assert.match(settingsClientCardSource, /function collectMappingRows/);
  assert.match(settingsClientCardTemplate, /id="sourceQueryComplexNotice"/);
  assert.match(settingsClientCardTemplate, /id="sourceHeaderComplexNotice"/);
});

test('client card source headings use Russian data source wording', () => {
  assert.match(settingsClientCardTemplate, /&#1048;&#1089;&#1090;&#1086;&#1095;&#1085;&#1080;&#1082;&#1080; &#1076;&#1072;&#1085;&#1085;&#1099;&#1093;/);
  assert.match(settingsClientCardTemplate, />\+ &#1048;&#1089;&#1090;&#1086;&#1095;&#1085;&#1080;&#1082; &#1076;&#1072;&#1085;&#1085;&#1099;&#1093;<\/button>/);
  assert.match(settingsClientCardTemplate, /&#1053;&#1086;&#1074;&#1099;&#1081; &#1080;&#1089;&#1090;&#1086;&#1095;&#1085;&#1080;&#1082; &#1076;&#1072;&#1085;&#1085;&#1099;&#1093;/);
  assert.match(settingsClientCardSource, /\\u0420\\u0435\\u0434\\u0430\\u043a\\u0442\\u0438\\u0440\\u043e\\u0432\\u0430\\u043d\\u0438\\u0435 \\u0438\\u0441\\u0442\\u043e\\u0447\\u043d\\u0438\\u043a\\u0430 \\u0434\\u0430\\u043d\\u043d\\u044b\\u0445/);
  assert.match(settingsClientCardSource, /\\u041d\\u043e\\u0432\\u044b\\u0439 \\u0438\\u0441\\u0442\\u043e\\u0447\\u043d\\u0438\\u043a \\u0434\\u0430\\u043d\\u043d\\u044b\\u0445/);
  assert.match(settingsClientCardSource, /data-rule-lookup-source-wrap>\\u0418\\u0441\\u0442\\u043e\\u0447\\u043d\\u0438\\u043a \\u0434\\u0430\\u043d\\u043d\\u044b\\u0445/);
  assert.doesNotMatch(settingsClientCardTemplate, />\+ Source<\/button>|>Source<\/div>/);
  assert.doesNotMatch(settingsClientCardSource, /Название source|Новый source|Редактирование source|Удалить source/);
});

test('rule action editor uses full-width vertical card layout and widened modal', () => {
  assert.match(settingsClientCardSource, /\\u0413\\u0434\\u0435 \\u043f\\u0440\\u0438\\u043c\\u0435\\u043d\\u044f\\u0442\\u044c/);
  assert.match(stylesSource, /\.client-card-rule-action-row \{[\s\S]*grid-template-columns: minmax\(0, 1fr\);[\s\S]*border: 1px solid rgba\(148, 163, 184, 0\.35\);[\s\S]*padding: 12px;/);
  assert.match(stylesSource, /\.client-card-rule-action-row > label \{[\s\S]*display: grid;[\s\S]*gap: 6px;/);
  assert.match(stylesSource, /\.client-card-rule-action-row select,[\s\S]*\.client-card-rule-action-row input,[\s\S]*\.client-card-rule-action-row textarea \{[\s\S]*width: 100%;[\s\S]*min-width: 0;/);
  assert.match(stylesSource, /#clientCardRuleModal \.modal-dialog \{[\s\S]*width: min\(760px, 94vw\);/);
  assert.match(stylesSource, /\.client-card-rule-actions \{[\s\S]*gap: 12px;/);
});

test('client card source editor supports template insertion, dependency context and preview rendering', () => {
  assert.match(settingsClientCardSource, /sourceInsertUrlFieldBtn/);
  assert.match(settingsClientCardSource, /sourceInsertBodyFieldBtn/);
  assert.match(settingsClientCardSource, /insertAtCursor\(el\.sourceUrl, formatSourceTemplateRef\(el\.sourceInsertUrlField\.value\)\)/);
  assert.match(settingsClientCardSource, /function sourceTemplateRefs/);
  assert.match(settingsClientCardSource, /function renderSourceTestContext/);
  assert.match(settingsClientCardSource, /data-source-test-field/);
  assert.match(settingsClientCardSource, /function renderSourceTestResult/);
  assert.match(settingsClientCardSource, /missing_response_paths/);
  assert.match(settingsClientCardSource, /\/test`/);
});

test('source editor helper detects nested query and header values without object-string corruption', () => {
  const query = settingsClientCardHelpers.analyzeTemplateObject({
    flat: 'value',
    nested: { deep: 'x' },
    list: [1, 2],
  });
  const headers = settingsClientCardHelpers.analyzeTemplateObject({
    token: '{{secret:API}}',
    extra: { region: 'ru' },
  });

  assert.deepEqual(Array.from(query.complexKeys), ['nested', 'list']);
  assert.deepEqual(Array.from(headers.complexKeys), ['extra']);
  assert.equal(query.rows.find((row) => row.key === 'flat').value, 'value');
  assert.equal(query.rows.find((row) => row.key === 'nested').value, settingsClientCardHelpers.complexTemplateValuePlaceholder);
  assert.equal(query.rows.some((row) => String(row.value).includes('[object Object]')), false);
});

test('source editor serialization keeps flat values and blocks unresolved complex rows', () => {
  const complexPlaceholder = settingsClientCardHelpers.complexTemplateValuePlaceholder;
  const makeRow = (key, value, isComplex = false) => ({
    querySelector(selector) {
      if (selector === '[data-source-row-key]') return { value: key };
      if (selector === '[data-source-row-value]') return { value, dataset: { sourceComplexValue: isComplex ? '1' : '0' } };
      return null;
    },
  });
  const container = {
    querySelectorAll() {
      return [
        makeRow('flat', 'value'),
        makeRow('nested', complexPlaceholder, true),
        makeRow('token', '{{contact_name}}'),
      ];
    },
  };

  const serialized = settingsClientCardHelpers.serializeTemplateRows(container, '[data-source-query-row]');
  assert.deepEqual(JSON.parse(JSON.stringify(serialized.value)), { flat: 'value', token: '{{contact_name}}' });
  assert.deepEqual(Array.from(serialized.unresolvedKeys), ['nested']);
});

test('source editor uses backend-compatible template token syntax for query values', () => {
  assert.equal(settingsClientCardHelpers.formatSourceTemplateRef('internal_number'), '{{internal_number}}');
  assert.equal(settingsClientCardHelpers.formatSourceTemplateRef(' bad key '), '');
  assert.match(settingsClientCardSource, /formatSourceTemplateRef\(row\?\.querySelector\("\[data-source-insert-field\]"\)\?\.value \|\| ""\)/);
});

test('source editor test availability blocks unsaved and new sources', () => {
  assert.equal(settingsClientCardHelpers.canTestSourceEditor({ editingSourceId: 'source-1', sourceEditorDirty: false }), true);
  assert.equal(settingsClientCardHelpers.canTestSourceEditor({ editingSourceId: 'source-1', sourceEditorDirty: true }), false);
  assert.equal(settingsClientCardHelpers.canTestSourceEditor({ editingSourceId: null, sourceEditorDirty: false }), false);
  assert.match(settingsClientCardSource, /\\u0421\\u043d\\u0430\\u0447\\u0430\\u043b\\u0430 \\u0441\\u043e\\u0445\\u0440\\u0430\\u043d\\u0438\\u0442\\u0435 \\u0438\\u0437\\u043c\\u0435\\u043d\\u0435\\u043d\\u0438\\u044f/);
});

test('lookup_source rule action auto-forces save scope and restores non-save scope on switch back', () => {
  const lookupState = settingsClientCardHelpers.computeRuleActionUiState({
    targetType: 'field',
    scope: 'editor',
    action: 'lookup_source',
    valueType: 'literal',
    previousNonSaveScope: 'editor',
  });
  const showState = settingsClientCardHelpers.computeRuleActionUiState({
    targetType: lookupState.targetType,
    scope: lookupState.scope,
    action: 'show',
    valueType: 'literal',
    previousNonSaveScope: lookupState.previousNonSaveScope,
  });
  const setValueState = settingsClientCardHelpers.computeRuleActionUiState({
    targetType: 'field',
    scope: 'editor',
    action: 'set_value',
    valueType: 'field',
    previousNonSaveScope: 'both',
  });

  assert.equal(lookupState.scope, 'save');
  assert.equal(lookupState.targetType, 'field');
  assert.equal(lookupState.isLookupSource, true);
  assert.equal(showState.scope, 'editor');
  assert.equal(showState.isLookupSource, false);
  assert.equal(setValueState.scope, 'save');
  assert.equal(setValueState.showSourceField, true);
  assert.match(settingsClientCardSource, /scopeSelect\.disabled = uiState\.isLookupSource/);
  assert.match(settingsClientCardSource, /row\.dataset\.previousNonSaveScope = uiState\.previousNonSaveScope/);
});

test('lookup_source rule action uses source_id and hides set_value controls', () => {
  assert.match(settingsClientCardSource, /lookup_source: "\\u041f\\u043e\\u043b\\u0443\\u0447\\u0438\\u0442\\u044c/);
  assert.match(settingsClientCardSource, /data-rule-action-prop="source_id"/);
  assert.match(settingsClientCardSource, /sourceOptionsHtml\(sourceId\)/);
  assert.match(settingsClientCardSource, /item\.source_id = row\.querySelector\('\[data-rule-action-prop="source_id"\]'\)\.value/);
  assert.match(settingsClientCardSource, /delete item\.target_type/);
  assert.match(settingsClientCardSource, /data-rule-lookup-source-wrap/);
});

test('set_value template action editor exposes field picker, hint and preview', () => {
  assert.match(settingsClientCardSource, /data-rule-template-insert-field/);
  assert.match(settingsClientCardSource, /data-rule-template-insert-field-btn/);
  assert.match(settingsClientCardSource, /data-rule-template-preview/);
  assert.match(settingsClientCardSource, /data-rule-template-preview-context/);
  assert.match(settingsClientCardSource, /templateFieldOptionsHtml\(\)/);
  assert.match(settingsClientCardSource, /insertAtCursor\(row\.querySelector\('\[data-rule-action-prop="value"\]'\), `\{\{\$\{fieldKey\}\}\}`\)/);
  assert.match(settingsClientCardSource, /\\u041f\\u0440\\u0438\\u043c\\u0435\\u0440: \[\\u0424\\u0430\\u043c\\u0438\\u043b\\u0438\\u044f\] \[\\u0418\\u043c\\u044f\]/);
});

test('set_value template preview trims empty parts without hardcoded full-name logic', () => {
  const template = '{{last_name}} {{contact_name}}';

  assert.deepEqual(Array.from(settingsClientCardHelpers.ruleTemplateFieldRefs(template)), ['last_name', 'contact_name']);
  assert.equal(settingsClientCardHelpers.renderRuleTemplateValue(template, { last_name: 'Иванова', contact_name: 'Мария' }), 'Иванова Мария');
  assert.equal(settingsClientCardHelpers.renderRuleTemplateValue(template, { last_name: 'Иванова', contact_name: '' }), 'Иванова');
  assert.equal(settingsClientCardHelpers.renderRuleTemplateValue(template, { last_name: '', contact_name: 'Мария' }), 'Мария');
  assert.equal(settingsClientCardHelpers.renderRuleTemplateValue(template, { last_name: '', contact_name: '' }), '');
  assert.equal(settingsClientCardHelpers.renderRuleTemplateValue('ID {{bitrix_id}} login {{bitrix_login}}', { bitrix_id: 42, bitrix_login: 'm.ivanova' }), 'ID 42 login m.ivanova');
});

test('manual source run UI is removed while backend source-run routes stay outside this frontend contract', () => {
  assert.doesNotMatch(ticketTemplate, /id="runClientSourceBtn"/);
  assert.doesNotMatch(ticketTemplate, /id="clientSourceRunModal"/);
  assert.doesNotMatch(managementUsersTemplate, /id="runClientSourceBtn"/);
  assert.doesNotMatch(managementUsersTemplate, /id="clientSourceRunModal"/);
  assert.doesNotMatch(ticketClientCardSource, /function bindManualSourceRun/);
  assert.doesNotMatch(ticketClientCardSource, /function runManualSource/);
  assert.doesNotMatch(ticketPageSource, /manualSourceRunPath:/);
  assert.doesNotMatch(managementUsersSource, /manualSourceRunPath:/);
  assert.match(ticketTemplate, /id="editClientBtn"/);
});

test('tickets source-run readonly error line stays in unicode escapes and blocks mojibake regression', () => {
  const ticketsRouterSource = fs.readFileSync('api/app/routers/tickets.py', 'utf8');
  const readonlyRoleLine = ticketsRouterSource
    .split('\n')
    .find((line) => line.includes('\"readonly_role\"') && line.includes('\\u041d\\u0435\\u0442'));
  assert.ok(readonlyRoleLine);
  assert.match(
    ticketsRouterSource,
    /_error\(403, "readonly_role", "\\u041d\\u0435\\u0442 \\u043f\\u0440\\u0430\\u0432 \\u043d\\u0430 \\u0440\\u0435\\u0434\\u0430\\u043a\\u0442\\u0438\\u0440\\u043e\\u0432\\u0430\\u043d\\u0438\\u0435 \\u043a\\u0430\\u0440\\u0442\\u043e\\u0447\\u043a\\u0438 \\u043a\\u043b\\u0438\\u0435\\u043d\\u0442\\u0430"\)/,
  );
  assert.doesNotMatch(readonlyRoleLine, /[\u2568\u2564\u00D0\u00D1]|\u0420[\u045F\u040E\u045C]|\?\?\?|\u0442\u0416/);
});

test('verification block title renders through common client card config mechanics', () => {
  const defaultConfigSource = sourceBetween(ticketClientCardSource, 'function defaultClientCardViewConfig()', 'function clientCardColorClass');
  const renderSource = sourceBetween(ticketClientCardSource, 'function renderClientDynamicCard', 'function fillClientPanel');
  const seedSource = fs.readFileSync('api/app/services/seed_data.py', 'utf8');
  assert.match(defaultConfigSource, /key: "verification"/);
  assert.match(defaultConfigSource, /key: "verification"[\s\S]*show_in_card: true/);
  assert.match(defaultConfigSource, /key: "verification"[\s\S]*show_title: true/);
  assert.match(defaultConfigSource, /key: "is_verified"[\s\S]*is_visible: true/);
  assert.match(seedSource, /"key": "verification"[\s\S]*"is_active": True/);
  assert.match(seedSource, /show_in_card=True/);
  assert.match(seedSource, /show_title=True/);
  assert.match(renderSource, /\.filter\(blockShownInCard\)/);
  assert.match(renderSource, /\.filter\(fieldShownInCard\)/);
  assert.match(renderSource, /if \(blockShowsTitle\(block\)\)/);
  assert.match(renderSource, /client-card-block-title/);
  assert.doesNotMatch(renderSource, /block\.key === "verification"/);
});

test('settings client card new block keeps the legacy default without a color control', () => {
  assert.match(settingsClientCardTemplate, /id="newClientCardBlockColor" type="hidden"/);
  assert.doesNotMatch(settingsClientCardTemplate, /type="color"/);
});

test('settings client card create field modal still uses block select with contextual lock', () => {
  assert.match(settingsClientCardTemplate, /id="newClientCardFieldBlock"/);
  assert.match(settingsClientCardSource, /newClientCardFieldBlock\.innerHTML/);
  assert.match(settingsClientCardSource, /state\.createFieldBlockKey = blockKey \|\| null/);
  assert.match(settingsClientCardSource, /const contextBlock = state\.createFieldBlockKey \? findBlockByKey\(state\.createFieldBlockKey\) : null/);
  assert.match(settingsClientCardSource, /el\.newClientCardFieldBlock\.disabled = Boolean\(contextBlock\)/);
  assert.match(settingsClientCardSource, /const targetBlockKey = state\.createFieldBlockKey \|\| el\.newClientCardFieldBlock\.value/);
  assert.match(settingsClientCardSource, /block_key: targetBlockKey/);
});

test('settings client card removes global add field button and renders local block add field action', () => {
  assert.doesNotMatch(settingsClientCardTemplate, /id="addClientCardFieldBtn"/);
  assert.doesNotMatch(settingsClientCardSource, /addClientCardFieldBtn/);
  assert.match(settingsClientCardSource, /data-add-field-block-key="\$\{escapeHtml\(block\.key\)\}"/);
  assert.match(settingsClientCardSource, /querySelectorAll\("\[data-add-field-block-key\]"\)/);
  assert.match(settingsClientCardSource, /openCreateFieldModal\(btn\.dataset\.addFieldBlockKey\)/);
  assert.match(settingsClientCardSource, /state\.expandedBlocks\.add\(targetBlock\.id\)/);
  assert.match(stylesSource, /\.client-card-block-footer \{[\s\S]*margin-top: 12px;[\s\S]*display: flex;/);
});

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

test('management users helper keeps blocked view in page url state', () => {
  assert.equal(
    managementUsersHelpers.filterFromPageUrl('https://example.test/management/users?view=blocked'),
    'blocked',
  );
  assert.equal(
    managementUsersHelpers.filterFromPageUrl('https://example.test/management/users'),
    'all',
  );
  assert.equal(
    managementUsersHelpers.buildPageUrlForFilter('blocked', 'https://example.test/management/users'),
    '/management/users?view=blocked',
  );
  assert.equal(
    managementUsersHelpers.buildPageUrlForFilter('all', 'https://example.test/management/users?view=blocked&q=keep'),
    '/management/users?q=keep',
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

test('management users UI exposes block and unblock controls', () => {
  const html = managementUsersHelpers.tableRowHtml({
    client_id: 'client-1',
    telegram_display_name: 'Ivan TG',
    telegram_username: 'belfiss',
    telegram_user_id: '453920478',
    display_name: 'Ivan',
    internal_number: '4455',
    is_blocked: true,
    last_activity_at: null,
  });
  assert.match(html, /data-is-blocked="1"/);
  assert.match(html, /management-user-row-blocked/);
  assert.doesNotMatch(html, /\u0417\u0430\u0431\u043b\u043e\u043a\u0438\u0440\u043e\u0432\u0430\u043d/);
  assert.match(managementUsersTemplate, /id="clientBlockedState"/);
  assert.match(managementUsersTemplate, /id="toggleClientBlockBtn"/);
  assert.match(managementUsersTemplate, /id="resetClientDataBtn"/);
  assert.match(managementUsersSource, /async function toggleClientBlock/);
  assert.match(managementUsersSource, /async function resetClientData/);
  assert.match(managementUsersSource, /\/\$\{action\}/);
  assert.match(managementUsersSource, /\/reset-data/);
  assert.match(managementUsersSource, /window\.HelpdeskDialogs\.confirm/);
  assert.match(managementUsersSource, /state\.selectedClientBlocked/);
});

test('management users reset uses visible error modal on api refusal', () => {
  assert.match(managementUsersTemplate, /id="errorModal"/);
  assert.match(managementUsersTemplate, /id="errorText"/);
  assert.match(managementUsersTemplate, /id="okErrorBtn"/);
  assert.match(managementUsersSource, /function showError\(root, message\)/);
  assert.match(managementUsersSource, /showError\(root, error\.message\)/);
  assert.match(managementUsersSource, /closeErrorModalBtn/);
});

test('management users view mode is restored from url and synced via history api', () => {
  assert.match(managementUsersSource, /function filterFromPageUrl/);
  assert.match(managementUsersSource, /function buildPageUrlForFilter/);
  assert.match(managementUsersSource, /window\.history\.pushState/);
  assert.match(managementUsersSource, /onUrlChange\(nextUrl\)/);
  assert.match(managementUsersSource, /filterFromPageUrl\(url \|\| window\.location\.href\)/);
});

test('management users blocked view uses dedicated row styling while normal mode has no blocked badge text', () => {
  const styles = fs.readFileSync('api/app/static/styles.css', 'utf8');
  assert.match(styles, /\.management-user-row-blocked td/);
  assert.match(styles, /#fff1f2/);
  assert.doesNotMatch(managementUsersSource, /management-user-blocked-badge/);
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

test('tickets list requester column uses backend requester_display_name without profile recomputation', () => {
  assert.match(ticketsListSource, /requester: \{ title: "Заявка от", className: "ticket-client-col", sortKey: "requester_display_name"/);
  assert.match(ticketsTemplate, /list\.js\?v=20261105-column-auto-width-v7/);
  assert.match(ticketsListSource, /case "requester_display_name":\s+return String\(ticket\.requester_display_name \|\| ""\)\.toLocaleLowerCase\("ru"\);/);
  assert.match(ticketsListSource, /const textKeys = new Set\(\["subject", "requester_display_name", "assignee_name", "requester_department", "requester_location_type", "ticket_type"\]\)/);
  assert.match(ticketsListSource, /linkedCell\("ticket-client-col", ticket\.requester_display_name \|\| ticket\.client_name \|\| "", column\.width\)/);
  assert.match(ticketsListSource, /state\.tableSortDir = \["subject", "requester_display_name", "assignee_name", "requester_department", "requester_location_type", "ticket_type"\]\.includes\(key\) \? "asc" : "desc";/);
  assert.doesNotMatch(ticketsListSource, /profile\.last_name|profile\.contact_name|telegram_display_name/);
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
  assert.match(html, /Режим изменения структуры/);
  assert.match(html, /data-layout-drag-button/);
  assert.match(html, /data-layout-new-row-before/);
  assert.doesNotMatch(html, /data-layout-row|data-layout-add-row|data-layout-del-row|bot-layout-row-head|bot-layout-cols/);
});

test('workflow type draft change resets incompatible automation and discard restores saved type', async () => {
  const confirmations = [];
  const editorSource = fs.readFileSync('api/app/static/bot_buttons_editor.js', 'utf8');
  const window = {
    BotButtonsFiles: helpers,
    HelpdeskDialogs: { confirm(message) {
      confirmations.push(message);
      return Promise.resolve(true);
    } },
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

  assert.equal(await localEditorHelpers.applyWorkflowTypeDraft(ctx, 'ticket'), true);
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
  assert.equal(systemTicket.helpers.selector, '[data-system-ticket-key]');
  assert.match(source, /data-system-ticket-key="first-connection"/);
  assert.match(source, /data-system-ticket-key="review-buttons"/);
  assert.match(source, /data-system-ticket-key="feedback-buttons"/);
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
  assert.equal(labels.SYSTEM_TICKETS_TITLE, '\u0421\u0438\u0441\u0442\u0435\u043c\u043d\u044b\u0435 \u0441\u043e\u0431\u044b\u0442\u0438\u044f');
  assert.equal(labels.FIRST_CONNECTION_TITLE, '\u041f\u0435\u0440\u0432\u043e\u0435 \u043f\u043e\u0434\u043a\u043b\u044e\u0447\u0435\u043d\u0438\u0435 \u043a \u0431\u043e\u0442\u0443');
  assert.equal(labels.REVIEW_SYSTEM_TITLE, '\u041e\u0442\u0437\u044b\u0432\u044b');
  assert.equal(labels.FEEDBACK_SYSTEM_TITLE, '\u041f\u0440\u0435\u0434\u043b\u043e\u0436\u0435\u043d\u0438\u044f \u0438 \u0437\u0430\u043c\u0435\u0447\u0430\u043d\u0438\u044f');
  assert.match(el.content.innerHTML, /\u0421\u0438\u0441\u0442\u0435\u043c\u043d\u044b\u0435 \u0441\u043e\u0431\u044b\u0442\u0438\u044f/);
  assert.match(el.content.innerHTML, /\u041f\u0435\u0440\u0432\u043e\u0435 \u043f\u043e\u0434\u043a\u043b\u044e\u0447\u0435\u043d\u0438\u0435 \u043a \u0431\u043e\u0442\u0443/);
  assert.match(el.content.innerHTML, /\u041e\u0442\u0437\u044b\u0432\u044b/);
  assert.match(el.content.innerHTML, /\u041f\u0440\u0435\u0434\u043b\u043e\u0436\u0435\u043d\u0438\u044f \u0438 \u0437\u0430\u043c\u0435\u0447\u0430\u043d\u0438\u044f/);
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
    target: { closest(selector) { return selector === systemTicket.helpers.selector ? { getAttribute: () => 'first-connection' } : null; } },
  });

  assert.equal(calls[0], 'discard');
  assert.equal(calls[1], '/api/v1/settings/bot-buttons/system/first-connection');
  assert.equal(state.view, 'system_editor');
  assert.equal(Object.hasOwn(state.systemFirstConnectionDraft, 'clientText'), false);
  assert.equal(statuses.at(-1), '\u0413\u043e\u0442\u043e\u0432\u043e');
});

test('first connection editor does not render or save duplicated client text', () => {
  const source = fs.readFileSync('api/app/static/settings_bot_buttons.js', 'utf8');

  assert.doesNotMatch(source, /editSystemFirstConnectionClientTextBtn/);
  assert.doesNotMatch(source, /systemFirstConnectionDraft\.clientText/);
  assert.doesNotMatch(source, /client_text: draft\.clientText/);
  assert.doesNotMatch(source, /clientText: String\(item\?\.client_text_effective/);
});

test('bot Back climbs nested levels without history calls and binds once after rerender', async () => {
  class MockElement {
    constructor(id = '') {
      this.id = id;
      this.textContent = '';
      this._innerHTML = '';
      this.listeners = {};
      this.classList = {
        values: new Set(),
        add: (...items) => items.forEach((item) => this.classList.values.add(item)),
        remove: (...items) => items.forEach((item) => this.classList.values.delete(item)),
        toggle: (item, force) => {
          if (force) this.classList.values.add(item); else this.classList.values.delete(item);
          return Boolean(force);
        },
      };
    }
    set innerHTML(value) {
      this._innerHTML = String(value || '');
      if (this.id === 'botButtonsBreadcrumb' && this._innerHTML.includes('saveInstructionBtn')) {
        documentElements.saveInstructionBtn = new MockElement('saveInstructionBtn');
      }
    }
    get innerHTML() { return this._innerHTML; }
    addEventListener(type, handler) {
      this.listeners[type] = this.listeners[type] || [];
      this.listeners[type].push(handler);
    }
    removeEventListener(type, handler) {
      this.listeners[type] = (this.listeners[type] || []).filter((item) => item !== handler);
    }
    async click() {
      for (const handler of this.listeners.click || []) {
        await handler({ preventDefault() {} });
      }
    }
    closest() { return null; }
    querySelectorAll() { return []; }
  }

  const documentElements = {
    statusText: new MockElement('statusText'),
    botButtonsBackBtn: new MockElement('botButtonsBackBtn'),
    botButtonsPageTitle: new MockElement('botButtonsPageTitle'),
    botButtonsBreadcrumb: new MockElement('botButtonsBreadcrumb'),
    botButtonsContent: new MockElement('botButtonsContent'),
  };
  let menuCtx = null;
  let levelCtx = null;
  const registerCalls = [];
  const saveCalls = [];
  let allowDiscard = true;
  const source = fs.readFileSync('api/app/static/settings_bot_buttons.js', 'utf8');
  const window = {
    BotButtonsApi: {
      api(url) {
        const path = String(url || '');
        if (path.includes('/assignees')) return Promise.resolve([]);
        if (path.includes('/files')) return Promise.resolve({ items: [] });
        if (path.includes('/automations')) return Promise.resolve({ items: [] });
        if (path.includes('/menu-types/archived')) return Promise.resolve([]);
        if (path.includes('/destination-options')) return Promise.resolve({ items: [], free: [] });
        if (path.endsWith('/menu-types')) return Promise.resolve([{ menu_name: 'menu-a', display_name: 'Menu A' }]);
        if (path.includes('/menu-types/menu-a/structure')) {
          return Promise.resolve({ root_level_key: 'menu-a|level|__root__', tree_rows: [] });
        }
        return Promise.resolve({});
      },
      escapeHtml(value) { return String(value); },
      safeText(value) { return String(value || ''); },
      yesNo(value) { return value ? 'Да' : 'Нет'; },
      nodeKindLabel(value) { return String(value || ''); },
      renderInfoRow() { return ''; },
      },
    BotButtonsFiles: {},
    BotButtonsLevelView: {
      cancelLayoutEdit(state) { state.layoutEditMode = false; state.layoutDraftRows = []; },
      renderLevel(ctx) {
        levelCtx = ctx;
        return '';
      },
    },
    BotButtonsEditor: {
      openEditor(ctx, leaf) {
        ctx.state.selectedLeaf = leaf;
        ctx.state.view = 'editor';
        ctx.render();
        return Promise.resolve();
      },
      saveInstruction() {
        saveCalls.push('save');
        return Promise.resolve();
      },
      hasUnsavedChanges() { return false; },
      confirmDiscard() { return allowDiscard; },
      discardEditorChanges() {},
      syncAutomationSelectionMeta() {},
    },
    BotButtonsMenuView: {
      renderMenuTypes(ctx) {
        menuCtx = ctx;
        return '';
      },
      renderRightPanel() { return ''; },
      selectedMenuInfo() { return { display_name: 'Menu A' }; },
    },
    BotButtonsEditorView: {
      renderEditor() {
        documentElements.botButtonsContent.innerHTML = '<div data-editor="1"></div>';
      },
      cleanup() {},
    },
    HelpdeskSidebar: { init: () => Promise.resolve() },
    HelpdeskAppShell: {
      registerPage(name, fn) { registerCalls.push({ name, fn }); },
      goBack() { saveCalls.push('shell-back'); },
    },
    addEventListener() {},
    removeEventListener() {},
    location: { href: '/settings' },
  };
  const document = {
    getElementById(id) { return documentElements[id] || null; },
    querySelectorAll() { return []; },
    createElement() { return new MockElement(); },
    body: { appendChild() {} },
  };
  const context = vm.createContext({ window, document, console, JSON, Array, Set, Map, String, Object });
  vm.runInContext(source, context);

  assert.equal(registerCalls.length, 1);
  const page = registerCalls[0].fn();
  await new Promise((resolve) => setImmediate(resolve));
  const backButton = documentElements.botButtonsBackBtn;
  assert.equal(backButton.textContent, 'Назад');
  assert.equal((backButton.listeners.click || []).length, 1);
  await backButton.click();
  assert.deepEqual(saveCalls, ['shell-back']);
  saveCalls.length = 0;
  await menuCtx.openMenu('menu-a', 'Menu A');
  assert.equal(backButton.textContent, 'На уровень вверх');
  assert.equal(documentElements.botButtonsBreadcrumb.classList.values.has('hidden'), false);
  allowDiscard = false;
  await backButton.click();
  assert.equal(levelCtx.state.view, 'level');
  assert.equal(levelCtx.state.selectedMenu, 'menu-a');
  allowDiscard = true;
  levelCtx.state.levelStack.push({ label: 'Nested', rows: [], levelKey: 'menu-a|level|nested' });
  levelCtx.state.levelStack.push({ label: 'Deep', rows: [], levelKey: 'menu-a|level|deep' });
  await backButton.click();
  assert.equal(levelCtx.state.levelStack.length, 2);
  assert.deepEqual(saveCalls, []);
  assert.equal((backButton.listeners.click || []).length, 1);
  await backButton.click();
  assert.equal(levelCtx.state.levelStack.length, 1);
  assert.deepEqual(saveCalls, [], 'moving up never invokes shell or browser back');
  assert.equal(backButton.textContent, 'На уровень вверх');
  await backButton.click();
  assert.equal(levelCtx.state.view, 'menu_types');
  assert.equal(backButton.textContent, 'Назад');
  assert.equal(documentElements.botButtonsBreadcrumb.classList.values.has('hidden'), true);
  await menuCtx.openMenu('menu-a', 'Menu A');
  await levelCtx.openEditor({
    technical_key: 'menu-a|group_task|Parent',
    workflow_type: 'group_task',
    label: 'Parent',
    full_path: 'Parent',
  });
  levelCtx.render();
  assert.ok(documentElements.saveInstructionBtn, 'breadcrumb save button should be rendered');

  await documentElements.saveInstructionBtn.click();
  assert.deepEqual(saveCalls, ['save']);

  levelCtx.render();
  await documentElements.saveInstructionBtn.click();
  assert.deepEqual(saveCalls, ['save', 'save']);
  page.destroy();
});

test('system event click handler opens review button group api load path', async () => {
  const calls = [];
  const state = { view: 'menu_types', systemTicketLoadError: '', systemButtonGroup: null };
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
    onOpen: () => { throw new Error('first connection should not open'); },
    onOpenGroup: (key) => systemTicket.helpers.loadButtonGroupEditorState({
      state,
      render: () => calls.push('render'),
      setStatus: (value) => calls.push(value),
      api: async (path) => {
        calls.push(path);
        return {
          buttons: Array.from({ length: 8 }, (_, index) => ({
            key: `review-${index}`,
            text: `Button ${index}`,
            default_text: `Button ${index}`,
            button_style: index === 0 ? 'success' : null,
          })),
        };
      },
    }, key),
  });

  await content.listeners.click({
    preventDefault() {},
    target: { closest(selector) { return selector === systemTicket.helpers.selector ? { getAttribute: () => 'review-buttons' } : null; } },
  });

  assert.equal(calls[0], 'discard');
  assert.ok(calls.includes('/api/v1/settings/bot-buttons/system/review-buttons'));
  assert.equal(state.view, 'system_button_group_editor');
  assert.equal(state.systemButtonGroup.buttons.length, 8);
  assert.equal(state.systemButtonGroup.buttons[0].buttonStyle, 'success');
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
    target: { closest() { return { getAttribute: () => 'first-connection' }; } },
  });
  assert.equal(opened, 1);
});

test('system editor breadcrumb source keeps clean first-connection label', () => {
  const source = fs.readFileSync('api/app/static/settings_bot_buttons.js', 'utf8');
  assert.equal(systemTicket.helpers.labels.firstConnectionTitle, '\u041f\u0435\u0440\u0432\u043e\u0435 \u043f\u043e\u0434\u043a\u043b\u044e\u0447\u0435\u043d\u0438\u0435 \u043a \u0431\u043e\u0442\u0443');
  assert.equal(systemTicket.helpers.labels.reviewTitle, '\u041e\u0442\u0437\u044b\u0432\u044b');
  assert.equal(systemTicket.helpers.labels.feedbackTitle, '\u041f\u0440\u0435\u0434\u043b\u043e\u0436\u0435\u043d\u0438\u044f \u0438 \u0437\u0430\u043c\u0435\u0447\u0430\u043d\u0438\u044f');
  assert.match(source, /parts\.push\(FIRST_CONNECTION_TITLE\)/);
  assert.match(source, /parts\.push\(systemButtonGroupTitle\(\)\)/);
  assert.doesNotMatch(source, /╨|╤|Ð|Ñ|Рџ|РЎ|Рњ|\?\?\?/);
});

test('system button group editor has compact text and color controls without callback editor', () => {
  const source = fs.readFileSync('api/app/static/settings_bot_buttons.js', 'utf8');
  assert.match(source, /function renderSystemButtonGroupEditor/);
  assert.match(source, /data-system-editor="button-group"/);
  assert.match(source, /data-system-button-text/);
  assert.match(source, /data-system-button-style/);
  assert.match(source, /bot-button-style-base/);
  assert.match(source, /`bot-button-style-\$\{option\.value\}`/);
  assert.match(source, /value: "primary"/);
  assert.match(source, /value: "success"/);
  assert.match(source, /value: "danger"/);
  assert.ok(source.includes('option.label ? escapeHtml(option.label) : "<span aria-hidden=\\"true\\"></span>"'));
  assert.match(source, /body: JSON\.stringify\(\{ buttons \}\)/);
  assert.match(source, /loadSystemButtonGroupEditorState\(\{ api, state, render, setStatus \}, group\.key\)/);
  assert.doesNotMatch(source, /callback_data|callbackData|data-system-button-callback/);
});

test('bot button editor checkboxes do not reuse sidebar pressed header class', () => {
  const source = fs.readFileSync('api/app/static/bot_buttons_editor_view.js', 'utf8');

  assert.doesNotMatch(source, /admin-agent-header/);
  assert.match(source, /class="checkbox-label"[\s\S]+id="buttonIsEnabled"/);
  assert.match(source, /class="checkbox-label"[\s\S]+id="fixedAssigneeEnabled"/);
  assert.match(source, /class="checkbox-label"[\s\S]+id="freezeOnCreate"/);
  assert.match(source, /class="checkbox-label"[\s\S]+id="confirmationEnabled"/);
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
  const ctx = {
    state: {
      selectedLeaf: {
        technical_key: 'menu-a|ticket|A',
        label: 'A',
        full_path: 'A',
        children_rows: [],
        removed: false,
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
  };
  editorViewHelpers.renderEditor(ctx);

  assert.match(content.innerHTML, /id="editorWorkflowTypeSelect"/);
  assert.match(content.innerHTML, /\u0417\u0430\u044f\u0432\u043a\u0430/);
  assert.match(content.innerHTML, /\u0417\u0430\u0434\u0430\u0447\u0430/);
  assert.match(content.innerHTML, /\u0410\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0447\u0435\u0441\u043a\u0430\u044f \u0437\u0430\u0434\u0430\u0447\u0430/);
  assert.match(content.innerHTML, /\u0422\u0435\u043a\u0441\u0442 \u043a\u043b\u0438\u0435\u043d\u0442\u0443/);
  assert.match(content.innerHTML, /id="editClientTextBtn"/);
  assert.match(content.innerHTML, /id="archiveButtonBtn"/);
  assert.doesNotMatch(content.innerHTML, /id="restoreRemovedBtn"/);
  ctx.state.selectedLeaf.removed = true;
  editorViewHelpers.renderEditor(ctx);
  assert.match(content.innerHTML, /id="restoreRemovedBtn"/);
  assert.doesNotMatch(content.innerHTML, /id="archiveButtonBtn"/);
});

test('editor view renders compact button color picker without visual color labels', () => {
  const content = { innerHTML: '' };
  editorViewHelpers.renderEditor({
    state: {
      selectedLeaf: {
        technical_key: 'menu-a|ticket|A',
        label: 'A',
        full_path: 'A',
        children_rows: [],
        removed: false,
      },
      editorWorkflowType: 'ticket',
      editorBotLabel: 'A',
      buttonStyle: 'success',
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
        { key: 'none', title: '\u041d\u0435 \u0438\u0441\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u044c', description: '', available: true, handler: '', workflow_types: ['ticket'] },
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
  });

  assert.match(content.innerHTML, /\u0426\u0432\u0435\u0442 \u043a\u043d\u043e\u043f\u043a\u0438/);
  assert.match(content.innerHTML, />\u0411\u0430\u0437\u043e\u0432\u044b\u0439</);
  assert.match(content.innerHTML, /data-button-style-option=""/);
  assert.match(content.innerHTML, /data-button-style-option="primary"/);
  assert.match(content.innerHTML, /data-button-style-option="success"[\s\S]*aria-pressed="true"/);
  assert.match(content.innerHTML, /data-button-style-option="danger"/);
  assert.doesNotMatch(content.innerHTML, />\u0421\u0438\u043d\u0438\u0439</);
  assert.doesNotMatch(content.innerHTML, />\u0417\u0435\u043b\u0451\u043d\u044b\u0439</);
  assert.doesNotMatch(content.innerHTML, />\u041a\u0440\u0430\u0441\u043d\u044b\u0439</);
});

test('editor save sends selected button style and base style as null', async () => {
  const calls = [];
  const baseState = {
    selectedMenu: 'menu-a',
    selectedLeaf: {
      technical_key: 'menu-a|ticket|A',
      workflow_type: 'ticket',
      label: 'A',
      full_path: 'A',
      children_rows: [],
    },
    editorWorkflowType: 'ticket',
    editorBotLabel: 'A',
    editorText: '',
    editorClientText: '',
    editorSystemText: '',
    fixedAssigneeEnabled: false,
    fixedAssigneeUserId: '',
    freezeOnCreate: false,
    confirmationEnabled: false,
    confirmationText: '',
    filesOverridden: false,
    selectedFiles: [],
    conditionalFilesOverridden: false,
    conditionalRules: [],
    sendFilesProjectionError: '',
    isEnabled: true,
    automationTypeValue: 'none',
    availableAutomations: [],
  };
  async function saveWithStyle(buttonStyle) {
    const state = { ...baseState, buttonStyle };
    await editorHelpers.saveInstruction({
      state,
      api(_url, opts) {
        calls.push(JSON.parse(opts.body));
        return Promise.resolve({ ok: true });
      },
      setStatus() {},
      refreshMenuStructure() { return Promise.resolve(); },
      fieldMeta() { return null; },
      fieldLabel(value) { return String(value || ''); },
    });
  }

  await saveWithStyle('primary');
  await saveWithStyle('');

  assert.equal(calls[0].button_style, 'primary');
  assert.equal(calls[1].button_style, null);
});

test('editor open loads saved button style into state', async () => {
  const state = {
    selectedMenu: 'menu-a',
    availableAutomations: [],
  };
  await editorHelpers.openEditor({
    state,
    api() {
      return Promise.resolve({
        workflow_type: 'ticket',
        button_label: 'A',
        button_style: 'danger',
        automation_type: 'none',
        automation_key: null,
        files: [],
        conditional_files: [],
        is_enabled: true,
      });
    },
    setStatus() {},
    render() {},
    normalizeConditionalRuleForState(rule) { return rule; },
  }, {
    technical_key: 'menu-a|ticket|A',
    workflow_type: 'ticket',
    label: 'A',
    full_path: 'A',
    children_rows: [],
  });

  assert.equal(state.buttonStyle, 'danger');
  assert.equal(state.savedButtonStyle, 'danger');
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
  });

  assert.match(content.innerHTML, /<option value="task" selected>/);
  assert.doesNotMatch(content.innerHTML, /<option value="ticket" selected>/);
});

test('group task child editor renders child-only fields and hides client button fields', () => {
  const content = { innerHTML: '' };
  editorViewHelpers.renderEditor({
    state: {
      selectedLeaf: {
        technical_key: 'menu-a|group_task_child|Parent/Child',
        workflow_type: 'group_task_child',
        label: 'Child',
        full_path: 'Parent/Child',
        children_rows: [],
        removed: false,
      },
      editorWorkflowType: 'group_task_child',
      editorBotLabel: 'Child',
      editorText: '',
      editorClientText: 'legacy client text',
      editorSystemText: '',
      confirmationEnabled: true,
      confirmationText: 'legacy confirmation',
      fixedAssigneeEnabled: false,
      fixedAssigneeUserId: '',
      freezeOnCreate: false,
      suppressClientAnswer: true,
      isEnabled: true,
      automationTypeValue: 'none',
      availableAutomations: [
        { key: 'none', title: '\u041d\u0435 \u0438\u0441\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u044c', description: '', available: true, handler: '', workflow_types: ['group_task_child'] },
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
  });

  assert.match(content.innerHTML, /\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u0437\u0430\u0434\u0430\u0447\u0438/);
  assert.match(content.innerHTML, /\u0422\u0435\u043a\u0441\u0442 \u0441\u0438\u0441\u0442\u0435\u043c\u043d\u044b\u0439/);
  assert.match(content.innerHTML, /\u041d\u0435 \u043e\u0442\u043f\u0440\u0430\u0432\u043b\u044f\u0442\u044c \u043e\u0442\u0432\u0435\u0442 \u043a\u043b\u0438\u0435\u043d\u0442\u0443/);
  assert.match(content.innerHTML, /id="suppressClientAnswer"[^>]*checked/);
  assert.doesNotMatch(content.innerHTML, /\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u0432 \u0431\u043e\u0442\u0435/);
  assert.doesNotMatch(content.innerHTML, /\u0426\u0432\u0435\u0442 \u043a\u043d\u043e\u043f\u043a\u0438/);
  assert.doesNotMatch(content.innerHTML, /\u0422\u0435\u043a\u0441\u0442 \u043a\u043b\u0438\u0435\u043d\u0442\u0443/);
  assert.doesNotMatch(content.innerHTML, /\u0422\u0440\u0435\u0431\u043e\u0432\u0430\u0442\u044c \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0438\u0435/);
});

test('group task child save preserves hidden field values and clears dirty state', async () => {
  let sentBody = null;
  const state = {
    selectedMenu: 'menu-a',
    selectedLeaf: {
      technical_key: 'menu-a|group_task_child|Parent/Child',
      workflow_type: 'group_task_child',
      label: 'Child',
      full_path: 'Parent/Child',
      children_rows: [],
    },
    editorWorkflowType: 'group_task_child',
    savedWorkflowType: 'group_task_child',
    editorBotLabel: 'Child updated',
    savedBotLabel: 'Child',
    editorText: 'Instruction',
    savedText: 'Instruction',
    editorClientText: 'legacy client text',
    savedClientText: 'legacy client text',
    editorSystemText: 'System',
    savedSystemText: 'System',
    buttonStyle: 'danger',
    savedButtonStyle: 'danger',
    fixedAssigneeEnabled: false,
    savedFixedAssigneeEnabled: false,
    fixedAssigneeUserId: '',
    savedFixedAssigneeUserId: '',
    freezeOnCreate: false,
    savedFreezeOnCreate: false,
    suppressClientAnswer: true,
    savedSuppressClientAnswer: false,
    confirmationEnabled: true,
    savedConfirmationEnabled: true,
    confirmationText: 'legacy confirmation',
    savedConfirmationText: 'legacy confirmation',
    filesOverridden: false,
    savedFilesOverridden: false,
    selectedFiles: [],
    savedSelectedFiles: [],
    conditionalFilesOverridden: false,
    savedConditionalFilesOverridden: false,
    conditionalRules: [],
    savedConditionalRules: [],
    sendFilesProjectionError: '',
    isEnabled: true,
    savedIsEnabled: true,
    automationTypeValue: 'none',
    savedAutomationTypeValue: 'none',
    availableAutomations: [],
  };

  assert.equal(editorHelpers.hasUnsavedChanges({ state }), true);
  await editorHelpers.saveInstruction({
    state,
    api(_url, opts) {
      sentBody = JSON.parse(opts.body);
      return Promise.resolve({ ok: true });
    },
    setStatus() {},
    refreshMenuStructure() { return Promise.resolve(); },
    fieldMeta() { return null; },
    fieldLabel(value) { return String(value || ''); },
  });

  assert.equal(sentBody.suppress_client_answer, true);
  assert.equal(Object.prototype.hasOwnProperty.call(sentBody, 'client_text'), false);
  assert.equal(Object.prototype.hasOwnProperty.call(sentBody, 'button_style'), false);
  assert.equal(Object.prototype.hasOwnProperty.call(sentBody, 'confirmation_enabled'), false);
  assert.equal(editorHelpers.hasUnsavedChanges({ state }), false);
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

test('ticket draft autosave is debounced and flushed on page exit', () => {
  assert.match(ticketPageSource, /draftSaveTimer = window\.setTimeout\(\(\) => \{[\s\S]+saveDraftNow\(\)[\s\S]+}, delayMs\)/);
  assert.match(ticketPageSource, /scheduleDraftSave\(400\)/);
  assert.match(ticketComposerSource, /el\.messageInput\.addEventListener\("input", \(\) => ctx\.onComposerInput\?\.\(\)\)/);
  assert.match(ticketPageSource, /window\.fetch\(`\/api\/v1\/tickets\/\$\{state\.ticketId\}\/draft`[\s\S]+keepalive: true/);
  assert.match(ticketPageSource, /window\.addEventListener\("pagehide", beforeUnloadHandler\)/);
  assert.match(ticketPageSource, /function cleanupTicketCardPage\(\) \{[\s\S]+flushDraftKeepalive\(\)/);
});

test('ticket draft autosave is single flight and resaves newer dirty text', () => {
  const source = sourceBetween(ticketPageSource, 'async function saveDraftNow', 'function scheduleDraftSave');
  assert.match(source, /if \(!keepalive && state\.draftSavePromise\)/);
  assert.match(source, /return state\.draftSavePromise\.then/);
  assert.match(source, /return saveDraftNow\(\)/);
  assert.match(source, /state\.draftSavePromise = \(async \(\) =>/);
  assert.match(source, /if \(state\.draftDirty\) \{[\s\S]+scheduleDraftSave\(400\)/);
});

test('ticket send flow flushes draft before reply or comment post', () => {
  const source = sourceBetween(ticketComposerSource, 'async function sendByMode', 'function bindComposerHandlers');
  assert.ok(source.indexOf('await ctx.flushDraftBeforeSend?.();') < source.indexOf('await api(`/api/v1/tickets/${state.ticketId}/${actionName}`'));
  assert.match(ticketPageSource, /async function flushDraftBeforeSend\(\)[\s\S]+while \(state\.draftSavePromise \|\| state\.draftDirty\)/);
});

test('ticket successful send cannot race a local normal draft save', () => {
  const source = sourceBetween(ticketComposerSource, 'async function sendByMode', 'function bindComposerHandlers');
  assert.ok(source.indexOf('await ctx.flushDraftBeforeSend?.();') < source.indexOf('ctx.clearDraftAfterSuccessfulSend?.();'));
  assert.match(ticketPageSource, /if \(state\.draftSavePromise\) \{[\s\S]+await state\.draftSavePromise/);
});

test('ticket draft exit flush is idempotent across cleanup and page exit', () => {
  const source = sourceBetween(ticketPageSource, 'function flushDraftKeepalive()', 'function clearDraftAfterSuccessfulSend');
  assert.match(source, /const signature = draftExitSignature\(\)/);
  assert.match(source, /if \(state\.draftExitFlushSignature === signature\) return/);
  assert.match(source, /state\.draftExitFlushSignature = signature/);
  assert.match(ticketPageSource, /state\.draftExitFlushSignature = ""/);
});

test('ticket draft exit flush waits for inflight normal save before saving newer text', () => {
  const source = sourceBetween(ticketPageSource, 'function flushDraftKeepalive()', 'function clearDraftAfterSuccessfulSend');
  assert.match(source, /if \(state\.draftSavePromise\) \{/);
  assert.match(source, /state\.draftSavePromise[\s\S]+\.then\(\(\) => \{/);
  assert.match(source, /latestText !== state\.draftLastSavedText/);
  assert.match(source, /return saveDraftNow\(\)/);
  assert.ok(source.indexOf('if (state.draftSavePromise) {') < source.indexOf('void saveDraftNow({ keepalive: true });'));
});

test('ticket draft polling ignores stale or locally dirty draft state', () => {
  const source = sourceBetween(ticketPageSource, 'function applyServerDraft(card)', 'function markDraftCleanFromServer');
  assert.match(source, /revision < state\.draftAckRevision/);
  assert.match(source, /reconcileCleanDraftState\(\)/);
  assert.match(source, /state\.draftDirty \|\| state\.draftSaveInFlight/);
  assert.match(source, /state\.draftAckRevision = revision/);
  assert.ok(source.indexOf('if (!changed || state.draftDirty || state.draftSaveInFlight) return;') < source.indexOf('state.draftAckRevision = revision;'));
  assert.match(ticketPageSource, /applyServerDraft\(card\)/);
});

test('ticket draft stale dirty flag is reconciled before newer remote revision is applied', () => {
  const reconcileSource = sourceBetween(ticketPageSource, 'function reconcileCleanDraftState()', 'function applyServerDraft(card)');
  const applySource = sourceBetween(ticketPageSource, 'function applyServerDraft(card)', 'function markDraftCleanFromServer');
  assert.match(reconcileSource, /currentDraftText\(\) !== state\.draftLastSavedText/);
  assert.match(reconcileSource, /normalizeDraftMode\(state\.composeMode\) !== state\.draftLastSavedMode/);
  assert.match(reconcileSource, /state\.draftDirty = false/);
  assert.ok(applySource.indexOf('reconcileCleanDraftState();') < applySource.indexOf('if (!changed || state.draftDirty || state.draftSaveInFlight) return;'));
});

test('ticket draft applies newer clean remote text and mode while preserving cursor', () => {
  const source = sourceBetween(ticketPageSource, 'function applyServerDraft(card)', 'function markDraftCleanFromServer');
  assert.match(source, /saveDraftSelection\(\)/);
  assert.match(source, /el\.messageInput\.value = text/);
  assert.match(source, /state\.composeMode = mode/);
  assert.match(source, /restoreDraftSelection\(\)/);
});

test('ticket draft cursor is stored per user and ticket then cleared after successful send', () => {
  assert.match(ticketPageSource, /hd_ticket_draft_selection:\$\{userKey\}:\$\{state\.ticketId\}/);
  assert.match(ticketComposerSource, /ctx\.clearDraftAfterSuccessfulSend\?\.\(\)/);
  assert.match(ticketPageSource, /function clearDraftSelection\(\)[\s\S]+localStorage\.removeItem\(draftSelectionStorageKey\(\)\)/);
  assert.match(ticketComposerSource, /el\.messageInput\.addEventListener\("select", \(\) => ctx\.onComposerSelection\?\.\(\)\)/);
});

test('ticket draft changes leave attachment preload restoration untouched', () => {
  assert.match(ticketPageSource, /TicketComposer\.syncRestoredPreloads\?\.\(composerCtx\(\), card\.active_attachment_preloads \|\| \[\]\)/);
  assert.doesNotMatch(ticketPageSource, /draft[\s\S]{0,80}active_attachment_preloads/);
});

test('quick replies editor route and static wiring exist', () => {
  const mainSource = fs.readFileSync('api/app/main.py', 'utf8');
  assert.match(mainSource, /@app\.get\("\/quick-replies"\)/);
  assert.match(appShellSource, /\["\/quick-replies", \{ page: "quick-replies", scripts: \["\/static\/desk_dialogs\.js\?v=20261027-dialog-prompts-v4", "\/static\/quick_replies\.js\?v=20261027-message-dialogs-v2"\] \}\]/);
  assert.match(quickRepliesTemplate, /styles\.css\?v=20261106-ticket-table-v2/);
  assert.match(quickRepliesTemplate, /quick_replies\.js\?v=20261027-message-dialogs-v2/);
  assert.match(quickRepliesTemplate, /data-shell-page="quick-replies"/);
});

test('quick replies management mode renders compact cards without edit or use columns', () => {
  assert.match(quickRepliesTemplate, /id="quickRepliesList" class="quick-replies-list"/);
  assert.match(quickRepliesSource, /card\.className = `quick-reply-card\$\{isOwnAdminReply\(reply\) \? " ticket-type-request" : ""\}`/);
  assert.match(quickRepliesSource, /card\.dataset\.replyId = reply\.id/);
  assert.doesNotMatch(quickRepliesTemplate, /<table|<thead|<tbody|quickRepliesHeadRow|quickRepliesRows/);
  assert.doesNotMatch(quickRepliesSource, /Редактировать|data-edit|const cols|renderHead/);
  assert.doesNotMatch(quickRepliesTemplate, />Использовать</);
  assert.doesNotMatch(quickRepliesSource, /ticketMode[\s\S]{0,80}<th>[\s\S]{0,80}Использовать/);
});

test('quick replies ticket context keeps disabled use action separate from card open', () => {
  assert.match(quickRepliesSource, /const ticketId = \(params\.get\("ticket_id"\) \|\| ""\)\.trim\(\)/);
  assert.match(quickRepliesSource, /ticketMode \? `<span class="quick-reply-card-actions"><button type="button" class="action" data-use="\$\{escapeHtml\(reply\.id\)\}">Использовать<\/button><\/span>` : ""/);
  assert.match(quickRepliesSource, /event\.stopPropagation\(\)/);
  assert.match(quickRepliesSource, /await api\(`\/api\/v1\/quick-replies\/\$\{encodeURIComponent\(reply\.id\)\}\/use`, \{ method: "POST" \}\)/);
  assert.match(quickRepliesSource, /window\.location\.href = ticketUrl\(\)/);
});

test('quick replies ticket context use button is single-flight and keyboard-isolated', () => {
  assert.match(quickRepliesSource, /const state = \{[\s\S]*replies: \[\],[\s\S]*editing: null,[\s\S]*useInFlight: false,[\s\S]*\}/);
  const useSource = sourceBetween(quickRepliesSource, 'async function useReply(reply)', 'el.create.addEventListener');
  assert.match(useSource, /if \(state\.useInFlight\) return/);
  assert.match(useSource, /state\.useInFlight = true;[\s\S]*try \{[\s\S]*await api\(`\/api\/v1\/quick-replies\/\$\{encodeURIComponent\(reply\.id\)\}\/use`/);
  assert.match(useSource, /finally \{[\s\S]*state\.useInFlight = false;[\s\S]*\}/);
  const keydownSource = sourceBetween(quickRepliesSource, 'el.list.addEventListener("keydown"', 'await load();');
  assert.match(keydownSource, /if \(event\.target\?\.closest\?\.\("\[data-use\]"\)\) return/);
  assert.match(keydownSource, /if \(reply\) openEdit\(reply\)/);
});

test('quick replies card opens any visible reply including readonly foreign replies', () => {
  assert.match(quickRepliesSource, /const card = event\.target\?\.closest\?\.\("\[data-reply-id\]"\)/);
  assert.match(quickRepliesSource, /const reply = state\.replies\.find\(\(item\) => String\(item\.id\) === String\(replyId\)\)/);
  assert.match(quickRepliesSource, /if \(reply\) openEdit\(reply\)/);
  assert.doesNotMatch(quickRepliesSource, /if \(!reply\.can_edit\) return/);
});

test('quick replies edit modal uses backend can_edit for readonly fields and save state', () => {
  assert.match(quickRepliesSource, /el\.titleInput\.readOnly = !reply\.can_edit/);
  assert.match(quickRepliesSource, /el\.bodyInput\.readOnly = !reply\.can_edit/);
  assert.match(quickRepliesSource, /el\.save\.disabled = !canEdit \|\| state\.uploadInFlight/);
  assert.match(quickRepliesSource, /if \(state\.editing && !state\.editing\.can_edit\) return/);
  assert.match(quickRepliesSource, /role === "admin" \|\| role === "agent"/);
  assert.match(quickRepliesSource, /el\.create\.classList\.toggle\("hidden", !canCreate\(\)\)/);
});

test('quick replies edit modal shows immutable author footer', () => {
  assert.match(quickRepliesTemplate, /id="quickReplyAuthorFooter"/);
  assert.match(quickRepliesSource, /el\.authorFooter\.textContent = `Создал: \$\{reply\.author_name \|\| reply\.author_user_id \|\| "-"\}`/);
});

test('quick replies admin-owned cards reuse existing orange ticket type style', () => {
  assert.match(quickRepliesSource, /user\.role === "admin" && reply\.author_user_id === user\.id/);
  assert.match(quickRepliesSource, /ticket-type-request/);
  assert.doesNotMatch(quickRepliesSource, /client-card-block-yellow/);
  assert.match(stylesSource, /\.ticket-type-request \{[\s\S]*background: rgba\(255, 202, 158, 0\.78\);/);
});

test('quick replies modal and fields use wider responsive layout', () => {
  assert.match(stylesSource, /\.modal-dialog \{[\s\S]*width: min\(480px, 92vw\);/);
  assert.match(stylesSource, /#quickReplyModal \.quick-reply-dialog \{[\s\S]*width: min\(1040px, calc\(100vw - 32px\)\);[\s\S]*max-width: calc\(100vw - 32px\);/);
  assert.match(stylesSource, /\.quick-reply-dialog \.modal-body input,\s*\.quick-reply-dialog \.modal-body textarea \{[\s\S]*width: 100%;[\s\S]*min-width: 0;/);
  assert.match(stylesSource, /\.quick-replies-content \{[\s\S]*grid-template-columns: minmax\(0, 1fr\);/);
});

test('quick replies management navigation and file attachment editor are present', () => {
  assert.match(fs.readFileSync('api/app/templates/management.html', 'utf8'), /href="\/quick-replies">&#1041;&#1099;&#1089;&#1090;&#1088;&#1099;&#1077; &#1086;&#1090;&#1074;&#1077;&#1090;&#1099;/);
  assert.match(quickRepliesTemplate, /id="quickReplyFileInput" type="file" hidden/);
  assert.doesNotMatch(quickRepliesTemplate, /id="quickReplyFileInput"[^>]*multiple/);
  assert.match(quickRepliesTemplate, /id="attachQuickReplyFileBtn"[\s\S]*Прикрепить файл/);
  assert.match(quickRepliesSource, /const formData = new FormData\(\)/);
  assert.match(quickRepliesSource, /formData\.append\("file", file\)/);
  assert.match(quickRepliesSource, /api\("\/api\/v1\/quick-replies\/files", \{\s*method: "POST",\s*body: formData,\s*\}\)/);
  assert.doesNotMatch(quickRepliesSource, /headers: \{ "content-type": "multipart\/form-data"/i);
  assert.match(quickRepliesSource, /state\.draftFiles\.length >= 5/);
  assert.match(quickRepliesSource, /window\.HelpdeskDialogs\.confirm\(`Отвязать файл/);
});

test('ticket quick reply button sits between add files and send', () => {
  assert.match(ticketTemplate, /styles\.css\?v=20261106-ticket-table-v2/);
  assert.match(ticketTemplate, /<div class="composer-send-actions">[\s\S]*id="addFilesBtn"[\s\S]*id="quickRepliesBtn"[\s\S]*id="sendNowBtn"/);
  assert.doesNotMatch(ticketTemplate, /quick-replies-anchor/);
  assert.match(ticketTemplate, /<\/div>\s*<div id="quickRepliesPanel" class="quick-replies-panel hidden">/);
  const composerActions = sourceBetween(ticketTemplate, '<div class="composer-send-actions">', '<button id="sendNowBtn"');
  assert.doesNotMatch(composerActions, /title="Быстрые ответы"/);
  assert.match(composerActions, /aria-label="Быстрые ответы"/);
  assert.match(composerActions, /<img src="\/static\/quick_reply_book_icon\.png" alt="" class="quick-replies-icon" \/>/);
  assert.ok(fs.existsSync('api/app/static/quick_reply_book_icon.png'));
});

test('ticket quick reply button shows visual Ctrl+Q tooltip on hover and focus', () => {
  const toggleStyle = sourceBetween(stylesSource, '.quick-replies-toggle {', '.quick-replies-panel {');
  assert.match(toggleStyle, /\.quick-replies-icon \{[\s\S]*width: 24px;[\s\S]*height: 24px;[\s\S]*object-fit: contain;/);
  assert.match(toggleStyle, /\.quick-replies-toggle::after \{[\s\S]*content: "CTRL\+Q";[\s\S]*bottom: calc\(100% \+ 6px\);[\s\S]*opacity: 0;/);
  assert.match(toggleStyle, /\.quick-replies-toggle:hover::after,\s*\.quick-replies-toggle:focus-visible::after \{[\s\S]*opacity: 1;/);
  assert.match(toggleStyle, /position: absolute;/);
  assert.match(toggleStyle, /pointer-events: none;/);
});

test('ticket quick replies overlay sizes to populated results plus editor row', () => {
  const panelStyle = sourceBetween(stylesSource, '.quick-replies-panel {', '.quick-replies-search {');
  assert.match(ticketTemplate, /id="quickRepliesPanel" class="quick-replies-panel hidden"/);
  assert.match(ticketTemplate, /id="quickRepliesSearchInput"/);
  assert.match(ticketTemplate, /id="quickRepliesEditorRow"[\s\S]*Редактор быстрых ответов/);
  assert.doesNotMatch(stylesSource, /quick-replies-anchor/);
  assert.match(panelStyle, /position: fixed;/);
  assert.match(panelStyle, /left: 0;[\s\S]*bottom: 0;[\s\S]*transform: translateX\(-50%\);/);
  assert.match(panelStyle, /width: min\(460px, calc\(100vw - 28px\)\);/);
  assert.match(deskV2Source, /#quickRepliesPanel \{[\s\S]*height: auto; max-height:/);
  assert.match(panelStyle, /grid-template-rows: auto 1fr auto;/);
  assert.match(deskV2Source, /\.quick-replies-panel-rows \{[^}]*grid-template-rows: none;[^}]*grid-auto-rows: minmax\(48px, auto\)/);
  const renderRows = sourceBetween(ticketPageSource, 'function renderQuickReplyPanelRows()', 'async function searchQuickReplies(');
  assert.doesNotMatch(renderRows, /placeholder|index < 5/);
  assert.doesNotMatch(panelStyle, /overflow-y: auto/);
});

test('ticket quick replies fixed overlay is positioned from the book button rect', () => {
  const positionSource = sourceBetween(ticketPageSource, 'function positionQuickRepliesPanel()', 'function closeQuickRepliesPanel');
  assert.match(positionSource, /el\.quickRepliesBtn\.getBoundingClientRect\(\)/);
  assert.match(positionSource, /const buttonCenterX = rect\.left \+ rect\.width \/ 2/);
  assert.match(positionSource, /const panelBottom = window\.innerHeight - rect\.top \+ 14/);
  assert.match(positionSource, /style\.right = ""[\s\S]*style\.top = ""[\s\S]*style\.inset = ""/);
  assert.match(positionSource, /el\.quickRepliesPanel\.style\.left = `\$\{buttonCenterX\}px`/);
  assert.match(positionSource, /el\.quickRepliesPanel\.style\.bottom = `\$\{panelBottom\}px`/);
  assert.match(ticketPageSource, /if \(el\.quickRepliesBtn\) el\.quickRepliesBtn\.classList\.add\("primary"\);\s*positionQuickRepliesPanel\(\);/);
  assert.match(ticketPageSource, /resizeHandler = \(\) => \{[\s\S]*if \(state\.quickRepliesOpen\) positionQuickRepliesPanel\(\);[\s\S]*\};/);
});

test('ticket quick replies panel is portaled to body and cleaned up on ticket teardown', () => {
  const mountSource = sourceBetween(ticketPageSource, 'function mountQuickRepliesPanelOverlay()', 'function positionQuickRepliesPanel');
  assert.doesNotMatch(ticketPageSource, /quickRepliesPanelOriginalParent|quickRepliesPanelOriginalNextSibling/);
  assert.match(mountSource, /el\.quickRepliesPanel\.parentElement === document\.body/);
  assert.match(mountSource, /document\.body\.appendChild\(el\.quickRepliesPanel\)/);
  assert.match(ticketPageSource, /mountQuickRepliesPanelOverlay\(\);[\s\S]*if \(el\.quickRepliesPanel\) el\.quickRepliesPanel\.classList\.remove\("hidden"\)/);
  const cleanupSource = sourceBetween(ticketPageSource, 'function cleanupTicketCardPage()', 'function attachmentsCtx');
  assert.match(cleanupSource, /if \(el\.quickRepliesPanel\?\.parentElement === document\.body\) \{[\s\S]*el\.quickRepliesPanel\.remove\(\);[\s\S]*\}/);
});

test('ticket quick replies outside click keeps button and panel clicks inside', () => {
  const insideSource = sourceBetween(ticketPageSource, 'function isQuickRepliesClickInside(target)', 'function quickReplyEditorUrl');
  assert.match(insideSource, /el\.quickRepliesBtn && el\.quickRepliesBtn\.contains\(target\)/);
  assert.match(insideSource, /el\.quickRepliesPanel && el\.quickRepliesPanel\.contains\(target\)/);
  const bindSource = sourceBetween(ticketPageSource, 'if (el.quickRepliesBtn) {', 'if (el.quickRepliesSearchInput) {');
  assert.match(bindSource, /el\.quickRepliesBtn\.addEventListener\("click", \(event\) => \{[\s\S]*toggleQuickRepliesPanel\(\{ focusSearch: false \}\)/);
  assert.doesNotMatch(bindSource, /stopPropagation\(\)/);
  assert.match(ticketPageSource, /documentClickHandler = \(event\) => \{[\s\S]*if \(state\.quickRepliesOpen && !isQuickRepliesClickInside\(event\.target\)\) \{[\s\S]*closeQuickRepliesPanel\(\);/);
});

test('ticket quick replies use is single-flight to avoid double insertion', () => {
  assert.match(ticketPageSource, /quickReplyUseInFlight: false/);
  const useSource = sourceBetween(ticketPageSource, 'async function useQuickReply(reply)', 'async function openQuickRepliesEditorFromPanel');
  assert.match(useSource, /if \(state\.quickReplyUseInFlight\) return/);
  assert.match(useSource, /state\.quickReplyUseInFlight = true;[\s\S]*try \{[\s\S]*await api\(`\/api\/v1\/quick-replies\/\$\{encodeURIComponent\(reply\.id\)\}\/use`/);
  assert.match(useSource, /const applied = await applyQuickReplyToComposer\(payload, reply\.body \|\| "", token, cardId\)/);
  assert.match(useSource, /finally \{[\s\S]*state\.quickReplyUseInFlight = false;[\s\S]*\}/);
});

test('ticket quick replies highlight preserves preview metrics and uses V2 tokens', () => {
  const scope = sourceBetween(deskV2Source, 'body:has(#appContent.main[data-shell-page="ticket-card"]) #quickRepliesPanel {', '/* Appearance is shared chrome;');
  assert.match(scope, /\.quick-replies-panel-preview \{[^}]*font-weight: 400;/);
  assert.match(scope, /\.quick-replies-panel-row:is\(:hover, \.highlighted\) \{[^}]*font-weight: 400;/);
  assert.match(scope, /background: var\(--desk-surface\);/);
  assert.match(scope, /\.quick-replies-search \{[^}]*background: var\(--desk-soft\);[^}]*color: var\(--desk-text\);/);
  assert.match(scope, /\.quick-replies-editor-row[^}]*background: var\(--desk-tint\);/);
});

test('ticket quick replies search uses backend MRU, allows one char, and ignores stale responses', () => {
  assert.match(ticketPageSource, /await searchQuickReplies\(""\)/);
  assert.match(ticketPageSource, /\/api\/v1\/quick-replies\/search\?q=\$\{encodeURIComponent\(query \|\| ""\)\}/);
  assert.match(ticketPageSource, /state\.quickRepliesSearchTimer = window\.setTimeout\(\(\) => \{[\s\S]*}, 150\)/);
  assert.doesNotMatch(ticketPageSource, /query\.length\s*[<>=]/);
  assert.match(ticketPageSource, /const seq = \+\+state\.quickRepliesSearchSeq/);
  assert.match(ticketPageSource, /if \(seq !== state\.quickRepliesSearchSeq\) return/);
  assert.match(ticketPageSource, /payload\.slice\(0, 5\)/);
});

test('ticket quick replies keyboard uses physical Ctrl+Q only and cycles through search and rows', () => {
  assert.match(ticketPageSource, /function isQuickRepliesHotkey\(event\) \{[\s\S]*event\.ctrlKey && !event\.metaKey && !event\.altKey && event\.code === "KeyQ"/);
  assert.doesNotMatch(ticketPageSource, /key\.toLowerCase\(\) === "q"/);
  assert.match(ticketPageSource, /if \(isQuickRepliesHotkey\(e\)\) \{/);
  assert.doesNotMatch(ticketPageSource, /e\.metaKey &&[\s\S]{0,80}quickReplies/);
  assert.match(ticketPageSource, /toggleQuickRepliesPanel\(\{ focusSearch: true \}\)/);
  assert.match(ticketPageSource, /if \(el\.quickRepliesSearchInput\) rows\.push\(el\.quickRepliesSearchInput\)/);
  assert.match(ticketPageSource, /event\.key === "ArrowDown"[\s\S]*event\.stopPropagation\(\);[\s\S]*setQuickReplyHighlight\(state\.quickRepliesHighlightedIndex \+ 1, \{ focusSearch: true \}\)/);
  assert.match(ticketPageSource, /event\.key === "ArrowUp"[\s\S]*event\.stopPropagation\(\);[\s\S]*setQuickReplyHighlight\(state\.quickRepliesHighlightedIndex - 1, \{ focusSearch: true \}\)/);
  assert.match(ticketPageSource, /const next = \(\(index % rows\.length\) \+ rows\.length\) % rows\.length/);
  assert.match(ticketPageSource, /if \(focusSearch && rows\[next\] === el\.quickRepliesSearchInput\) \{[\s\S]*el\.quickRepliesSearchInput\.focus\(\);/);
  assert.match(ticketPageSource, /if \(row === el\.quickRepliesSearchInput\) return/);
  assert.match(ticketPageSource, /event\.key === "Escape"[\s\S]*closeQuickRepliesPanel\(\{ focusComposer: true \}\)/);
});

test('ticket quick replies mouse open preserves composer focus and selection without focusing search', () => {
  const openSource = sourceBetween(ticketPageSource, 'async function openQuickRepliesPanel', 'async function toggleQuickRepliesPanel');
  const bindSource = sourceBetween(ticketPageSource, 'if (el.quickRepliesBtn) {', 'if (el.quickRepliesSearchInput) {');
  assert.match(openSource, /\{ focusSearch = false \} = \{\}/);
  assert.match(openSource, /saveDraftSelection\(\)/);
  assert.doesNotMatch(openSource, /quickRepliesSearchInput\.focus\(\)/);
  assert.match(openSource, /setQuickReplyHighlight\(0, \{ focusSearch \}\)/);
  assert.match(bindSource, /el\.quickRepliesBtn\.addEventListener\("mousedown", \(event\) => \{[\s\S]*event\.preventDefault\(\);[\s\S]*saveDraftSelection\(\);/);
  assert.match(bindSource, /toggleQuickRepliesPanel\(\{ focusSearch: false \}\)/);
  assert.doesNotMatch(bindSource, /quickRepliesSearchInput\.focus/);
});

test('ticket quick replies insert uses /use, cursor selection, and existing draft hooks', () => {
  assert.match(ticketPageSource, /await api\(`\/api\/v1\/quick-replies\/\$\{encodeURIComponent\(reply\.id\)\}\/use`, \{ method: "POST" \}\)/);
  assert.match(ticketPageSource, /const value = String\(el\.messageInput\.value \|\| ""\)/);
  assert.match(ticketPageSource, /el\.messageInput\.value = `\$\{value\.slice\(0, start\)\}\$\{inserted\}\$\{value\.slice\(end\)\}`/);
  assert.match(ticketPageSource, /el\.messageInput\.setSelectionRange\(cursor, cursor\)/);
  assert.match(ticketPageSource, /handleComposerInput\(\)/);
  assert.match(ticketPageSource, /saveDraftSelection\(\)/);
  assert.match(ticketPageSource, /if \(raw && document\.activeElement !== el\.messageInput\) \{[\s\S]*start = Number\(parsed\?\.start\);[\s\S]*end = Number\(parsed\?\.end\);/);
});

test('ticket quick replies download saved files and queue them through composer', () => {
  assert.match(ticketPageSource, /async function downloadQuickReplyFiles\(replyId, savedFiles\)/);
  assert.match(ticketPageSource, /\/api\/v1\/quick-replies\/\$\{encodeURIComponent\(replyId\)\}\/files\/\$\{encodeURIComponent\(savedFileId\)\}\/download/);
  assert.match(ticketPageSource, /downloaded\.push\(new File\(/);
  assert.match(ticketPageSource, /const files = await downloadQuickReplyFiles\(replyId, payload\?\.saved_files \|\| \[\]\)/);
  assert.match(ticketPageSource, /TicketComposer\.queueSelectedFiles\(composerCtx\(\), files\)/);
  assert.doesNotMatch(ticketPageSource, /sendByMode\(composerCtx\(\)\)[\s\S]{0,200}quickReply/);
});

test('ticket quick replies cannot insert or count use when composer is readonly', () => {
  assert.match(ticketPageSource, /function canWriteComposer\(\) \{[\s\S]*!el\.messageInput\.disabled[\s\S]*!el\.messageInput\.readOnly[\s\S]*state\.card\?\.status !== "closed"/);
  assert.match(ticketPageSource, /if \(el\.quickRepliesBtn\) el\.quickRepliesBtn\.disabled = isClosed \|\| isReadonly/);
  const useSource = sourceBetween(ticketPageSource, 'async function useQuickReply(reply)', 'async function openQuickRepliesEditorFromPanel');
  assert.match(useSource, /if \(!canWriteComposer\(\)\) \{[\s\S]*closeQuickRepliesPanel\(\);[\s\S]*return;[\s\S]*\}[\s\S]*await api\(`\/api\/v1\/quick-replies/);
  const applySource = sourceBetween(ticketPageSource, 'async function applyQuickReplyToComposer', 'async function useQuickReply');
  assert.match(applySource, /if \(destroyed \|\| token !== state\.quickReplyUseSeq \|\| state\.card\?\.id !== cardId \|\| !canWriteComposer\(\)\) \{/);
  const renderSource = sourceBetween(ticketPageSource, 'function renderCard()', 'async function loadCard');
  assert.match(renderSource, /const isReadonly = state\.role === "chiki_piki"/);
  assert.match(renderSource, /if \(isClosed \|\| isReadonly\) closeQuickRepliesPanel\(\)/);
});

test('ticket quick replies editor row flushes draft and uses ticket context route', () => {
  assert.match(ticketPageSource, /function quickReplyEditorUrl\(\)[\s\S]*\/quick-replies\?ticket_id=\$\{encodeURIComponent\(state\.ticketId\)\}/);
  assert.match(ticketPageSource, /await flushDraftBeforeSend\(\)/);
  assert.match(ticketPageSource, /window\.location\.href = quickReplyEditorUrl\(\)/);
  assert.match(ticketPageSource, /el\.quickRepliesEditorRow\.disabled = !canUseQuickRepliesEditor\(\)/);
  assert.match(ticketPageSource, /return state\.role === "admin" \|\| state\.role === "agent"/);
});

test('ticket quick replies panel closes outside and survives polling render', () => {
  assert.match(ticketPageSource, /if \(state\.quickRepliesOpen && !isQuickRepliesClickInside\(event\.target\)\) \{/);
  const renderSource = sourceBetween(ticketPageSource, 'function renderCard()', 'async function loadCard');
  assert.doesNotMatch(renderSource, /quickRepliesPanel\.innerHTML|quickRepliesPanel\.remove|quickRepliesOpen = false/);
});

test('ticket polling reload does not steal focus after initial composer autofocus', () => {
  const loadSource = sourceBetween(ticketPageSource, 'async function loadCard(options = {})', 'async function sendByMode');
  assert.match(ticketPageSource, /initialComposerFocusApplied: false/);
  assert.match(loadSource, /if \(!state\.initialComposerFocusApplied\) \{[\s\S]*state\.initialComposerFocusApplied = true;[\s\S]*focusMessageInputIfAvailable\(\);[\s\S]*restoreDraftSelection\(\);[\s\S]*\}[\s\S]*await consumePendingQuickReplyUse\(\)/);
  assert.doesNotMatch(loadSource, /\}\s*restoreDraftSelection\(\);\s*await consumePendingQuickReplyUse\(\)/);
  assert.match(ticketPageSource, /pollTimer = window\.setInterval\(\(\) => \{[\s\S]*loadCard\(\{ silent: true \}\)/);
});

test('ticket polling selection restore stays scoped to initial load and remote draft apply', () => {
  const loadSource = sourceBetween(ticketPageSource, 'async function loadCard(options = {})', 'async function sendByMode');
  const loadRestoreCalls = loadSource.match(/restoreDraftSelection\(\)/g) || [];
  assert.strictEqual(loadRestoreCalls.length, 1);
  assert.match(loadSource, /restoreDraftSelection\(\);[\s\S]*await consumePendingQuickReplyUse\(\)/);

  const applyDraftSource = sourceBetween(ticketPageSource, 'function applyServerDraft(card)', 'function markDraftCleanFromServer');
  assert.match(applyDraftSource, /saveDraftSelection\(\);[\s\S]*restoreDraftSelection\(\);/);
  assert.match(ticketPageSource, /function restoreDraftSelection\(\)[\s\S]*el\.messageInput\.setSelectionRange\(start, end\)/);
});

test('ticket context quick replies use stores only pending reply body and returns to ticket', () => {
  assert.match(quickRepliesSource, /function pendingQuickReplyStorageKey\(\)[\s\S]*hd_quick_reply_pending_use:\$\{ticketId\}/);
  assert.match(quickRepliesSource, /sessionStorage\.setItem\(pendingQuickReplyStorageKey\(\), JSON\.stringify\(\{/);
  assert.match(quickRepliesSource, /saved_files: payload\?\.saved_files \|\| reply\.saved_files \|\| \[\]/);
  assert.doesNotMatch(quickRepliesSource, /draft_text|draftText|messageInput|ticket_message_body/);
  assert.match(ticketPageSource, /sessionStorage\.getItem\(pendingQuickReplyStorageKey\(\)\)/);
  assert.match(ticketPageSource, /await applyQuickReplyToComposer\(payload, "", token, state\.card\?\.id\)/);
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

for (const [initialKeyType, initialWorkflow, changes] of [
  ['ticket', 'ticket', ['task', 'ticket']],
  ['task', 'task', ['ticket', 'auto']],
  ['db', 'task', ['ticket', 'auto']],
]) {
  test(`editor updates technical key immediately for ${initialKeyType} -> ${changes.join(' -> ')}`, async () => {
    let serverKey = `menu-a|${initialKeyType}|A`;
    const state = {
      selectedMenu: 'menu-a',
      selectedLeaf: { technical_key: serverKey, workflow_type: initialWorkflow, label: 'A', full_path: 'A' },
      savedWorkflowType: initialWorkflow, editorWorkflowType: initialWorkflow,
      editorBotLabel: 'A', automationTypeValue: 'none', availableAutomations: [],
      selectedFiles: [], conditionalRules: [],
    };
    let refreshes = 0;
    const ctx = {
      state,
      async api(url, options) {
        assert.equal(options.method, 'PUT');
        const query = new URL(url, 'http://localhost').searchParams;
        assert.equal(query.get('button_key'), serverKey);
        assert.equal(query.get('workflow_type'), state.editorWorkflowType);
        serverKey = `menu-a|${state.editorWorkflowType}|A`;
        return { ok: true, button_key: serverKey, workflow_type: state.editorWorkflowType };
      },
      setStatus() {},
      async refreshMenuStructure() {
        assert.equal(state.selectedLeaf.technical_key, serverKey);
        refreshes++;
      },
      fieldMeta() { return null; }, fieldLabel(value) { return String(value || ''); },
    };
    for (const next of changes) {
      state.editorWorkflowType = next;
      await editorHelpers.saveInstruction(ctx);
      assert.equal(state.selectedLeaf.technical_key, `menu-a|${next}|A`);
      assert.equal(state.savedWorkflowType, next);
    }
    assert.equal(refreshes, changes.length);
  });
}

test('ticket Back and bot level-up are distinct DOM controls with independent handlers', () => {
  const ticket = fs.readFileSync('api/app/static/ticket.js', 'utf8'), bot = fs.readFileSync('api/app/static/settings_bot_buttons.js', 'utf8');
  const ticketHtml = fs.readFileSync('api/app/templates/ticket.html', 'utf8'), botHtml = fs.readFileSync('api/app/templates/settings_bot_buttons.html', 'utf8');
  assert.match(ticketHtml, /<button id="backToListBtn"[^>]*>Назад<\/button>/);
  assert.match(botHtml, /<button id="botButtonsBackBtn"[^>]*>Назад<\/button>/);
  assert.doesNotMatch(botHtml.match(/<button id="botButtonsBackBtn"[^>]*>/)[0], /data-shell-back/);
  assert.match(ticket, /el\.backToListBtn\.addEventListener\("click"/);
  assert.doesNotMatch(ticket, /botButtonsBackBtn|На уровень вверх/);
  assert.match(bot, /const isRoot = state\.view === "menu_types";[\s\S]*isRoot \? "Назад" : "На уровень вверх"/);
  assert.match(bot, /el\.backButton\?\.addEventListener\("click", \(\) => \{[\s\S]*if \(state\.view === "menu_types"\)[\s\S]*handleBack\(\);/);
});
