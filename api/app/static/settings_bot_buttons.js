(function () {
const SYSTEM_TICKET_SELECTOR = '[data-system-ticket-key]';
const SYSTEM_MENU_TYPE_PREFIX = "__system__";
const FIRST_CONNECTION_TITLE = "\u041f\u0435\u0440\u0432\u043e\u0435 \u043f\u043e\u0434\u043a\u043b\u044e\u0447\u0435\u043d\u0438\u0435 \u043a \u0431\u043e\u0442\u0443";
const REVIEW_SYSTEM_TITLE = "\u041e\u0442\u0437\u044b\u0432\u044b";
const FEEDBACK_SYSTEM_TITLE = "\u041f\u0440\u0435\u0434\u043b\u043e\u0436\u0435\u043d\u0438\u044f \u0438 \u0437\u0430\u043c\u0435\u0447\u0430\u043d\u0438\u044f";
const SYSTEM_BUTTON_GROUPS = {
  "review-buttons": {
    title: REVIEW_SYSTEM_TITLE,
    endpoint: "/api/v1/settings/bot-buttons/system/review-buttons",
  },
  "feedback-buttons": {
    title: FEEDBACK_SYSTEM_TITLE,
    endpoint: "/api/v1/settings/bot-buttons/system/feedback-buttons",
  },
};

function bindSystemTicketOpenHandler({ content, onOpen, onOpenGroup, onError, confirmDiscard, discardEditorChanges }) {
  if (!content || content.__systemFirstConnectionClickHandler) return false;
  const handler = async (event) => {
    const trigger = event.target?.closest?.(SYSTEM_TICKET_SELECTOR);
    if (!trigger) return;
    event.preventDefault();
    if (typeof confirmDiscard === "function" && !confirmDiscard()) return;
    if (typeof discardEditorChanges === "function") discardEditorChanges();
    try {
      const key = String(trigger.getAttribute("data-system-ticket-key") || "");
      if (key === "first-connection") await onOpen?.();
      else await onOpenGroup?.(key);
    } catch (error) {
      if (typeof onError === "function") onError(error);
    }
  };
  content.addEventListener("click", handler);
  content.__systemFirstConnectionClickHandler = handler;
  return true;
}

function unbindSystemTicketOpenHandler(content) {
  const handler = content?.__systemFirstConnectionClickHandler;
  if (!content || !handler) return false;
  content.removeEventListener("click", handler);
  delete content.__systemFirstConnectionClickHandler;
  return true;
}

async function loadSystemFirstConnectionEditorState({ api, state, render, setStatus }) {
  setStatus?.("\u0417\u0430\u0433\u0440\u0443\u0437\u043a\u0430 \u0441\u0438\u0441\u0442\u0435\u043c\u043d\u043e\u0439 \u0437\u0430\u044f\u0432\u043a\u0438...");
  state.systemTicketLoadError = "";
  try {
    const item = await api("/api/v1/settings/bot-buttons/system/first-connection");
    const draft = {
      subject: String(item?.subject_effective || ""),
      clientText: String(item?.client_text_effective || ""),
      systemText: String(item?.system_text_effective || ""),
      instructionText: String(item?.instruction_text || ""),
    };
    state.systemFirstConnection = item;
    state.systemFirstConnectionDraft = { ...draft };
    state.savedSystemFirstConnectionDraft = { ...draft };
    state.view = "system_editor";
    render?.();
    setStatus?.("\u0413\u043e\u0442\u043e\u0432\u043e");
    return state.systemFirstConnection;
  } catch (error) {
    state.systemFirstConnection = null;
    state.systemFirstConnectionDraft = {
      subject: "",
      clientText: "",
      systemText: "",
      instructionText: "",
    };
    state.savedSystemFirstConnectionDraft = {
      subject: "",
      clientText: "",
      systemText: "",
      instructionText: "",
    };
    state.systemTicketLoadError = error?.message || "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0437\u0430\u0433\u0440\u0443\u0437\u0438\u0442\u044c \u0441\u0438\u0441\u0442\u0435\u043c\u043d\u0443\u044e \u0437\u0430\u044f\u0432\u043a\u0443";
    state.view = "menu_types";
    render?.();
    setStatus?.(`\u041e\u0448\u0438\u0431\u043a\u0430: ${state.systemTicketLoadError}`);
    throw error;
  }
}

async function loadSystemButtonGroupEditorState({ api, state, render, setStatus }, groupKey) {
  const meta = SYSTEM_BUTTON_GROUPS[groupKey];
  if (!meta) throw new Error("\u041d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u0430\u044f \u0433\u0440\u0443\u043f\u043f\u0430 \u0441\u0438\u0441\u0442\u0435\u043c\u043d\u044b\u0445 \u043a\u043d\u043e\u043f\u043e\u043a");
  setStatus?.(`\u0417\u0430\u0433\u0440\u0443\u0437\u043a\u0430: ${meta.title}...`);
  state.systemTicketLoadError = "";
  const item = await api(meta.endpoint);
  const buttons = Array.isArray(item?.buttons)
    ? item.buttons.map((button) => ({
        key: String(button?.key || ""),
        text: String(button?.text || button?.default_text || ""),
        defaultText: String(button?.default_text || ""),
        buttonStyle: String(button?.button_style || ""),
      }))
    : [];
  state.systemButtonGroup = {
    key: groupKey,
    title: meta.title,
    endpoint: meta.endpoint,
    buttons,
  };
  state.savedSystemButtonGroup = JSON.parse(JSON.stringify(state.systemButtonGroup));
  state.view = "system_button_group_editor";
  render?.();
  setStatus?.("\u0413\u043e\u0442\u043e\u0432\u043e");
  return state.systemButtonGroup;
}

function isSystemMenuTypeRecord(item) {
  const menuName = String(item?.menu_name || "").trim();
  return !!menuName && menuName.startsWith(SYSTEM_MENU_TYPE_PREFIX);
}

function initSettingsBotButtonsPage() {
const el = {
  statusText: document.getElementById("statusText"),
  backToSettingsBtn: document.getElementById("backToSettingsBtn"),
  pageTitle: document.getElementById("botButtonsPageTitle"),
  breadcrumb: document.getElementById("botButtonsBreadcrumb"),
  content: document.getElementById("botButtonsContent"),
};

const state = {
  view: "menu_types", // menu_types | level | editor
  menuTypes: [],
  archivedMenuTypes: [],
  destinationOptions: [],
  freeDestinationOptions: [],
  selectedMenu: null,
  menuStructure: null,
  levelStack: [],
  layoutEditMode: false,
  layoutDraftRows: [],
  selectedLeaf: null,
  assignees: [],
  availableFiles: [],
  conditionalFields: [],

  editorText: "",
  savedText: "",
  editorClientText: "",
  savedClientText: "",
  editorBotLabel: "",
  savedBotLabel: "",
  buttonStyle: "",
  savedButtonStyle: "",
  editorSystemText: "",
  savedSystemText: "",
  editorWorkflowType: "",
  savedWorkflowType: "",
  systemTextOverridden: false,

  fixedAssigneeEnabled: false,
  savedFixedAssigneeEnabled: false,
  fixedAssigneeUserId: "",
  savedFixedAssigneeUserId: "",
  freezeOnCreate: false,
  savedFreezeOnCreate: false,

  confirmationEnabled: false,
  savedConfirmationEnabled: false,
  confirmationText: "",
  savedConfirmationText: "",
  filesOverridden: false,
  savedFilesOverridden: false,
  selectedFiles: [],
  savedSelectedFiles: [],
  conditionalFilesOverridden: false,
  savedConditionalFilesOverridden: false,
  conditionalRules: [],
  savedConditionalRules: [],
  loadedSendFilesSnapshot: null,
  sendFilesProjectionError: "",
  isEnabled: true,
  savedIsEnabled: true,
  automationKey: "",
  automationTitle: "",
  automationDescription: "",
  automationFrom: "none",
  automationStatus: "not_set",
  automationAvailable: false,
  automationHandler: "",
  automationType: "none",
  automationTypeValue: "none",
  savedAutomationTypeValue: "none",
  automationKeyValue: "",
  savedAutomationKeyValue: "",
  availableAutomations: [],
  systemFirstConnection: null,
  systemButtonGroup: null,
  savedSystemButtonGroup: null,
  systemTicketLoadError: "",
  systemFirstConnectionDraft: {
    subject: "",
    clientText: "",
    systemText: "",
    instructionText: "",
  },
  savedSystemFirstConnectionDraft: {
    subject: "",
    clientText: "",
    systemText: "",
    instructionText: "",
  },
  preview: {
    kind: null,
    label: "",
    technicalKey: "",
    workflowType: "",
    childrenCount: 0,
    item: null,
    loading: false,
    menuName: "",
  },
  itemCache: {},
};

const FIELD_LABELS = {};

const OPERATOR_OPTIONS = {
  text: [
    { value: "eq", label: "равно" },
    { value: "ne", label: "не равно" },
    { value: "contains", label: "содержит" },
    { value: "not_contains", label: "не содержит" },
    { value: "empty", label: "пусто" },
    { value: "not_empty", label: "не пусто" },
  ],
  select: [
    { value: "eq", label: "равно" },
    { value: "ne", label: "не равно" },
    { value: "empty", label: "пусто" },
    { value: "not_empty", label: "не пусто" },
  ],
  number: [
    { value: "eq", label: "равно" },
    { value: "ne", label: "не равно" },
    { value: "gt", label: "больше" },
    { value: "lt", label: "меньше" },
    { value: "gte", label: "больше или равно" },
    { value: "lte", label: "меньше или равно" },
    { value: "empty", label: "пусто" },
    { value: "not_empty", label: "не пусто" },
  ],
  bool: [
    { value: "eq", label: "равно" },
  ],
};

function setStatus(text) {
  if (el.statusText) el.statusText.textContent = text;
}

const {
  api,
  escapeHtml,
  safeText,
  yesNo,
  nodeKindLabel,
  renderInfoRow,
} = window.BotButtonsApi || {};
const BotButtonsFiles = window.BotButtonsFiles || {};
const BotButtonsLevelView = window.BotButtonsLevelView || {};
const BotButtonsEditor = window.BotButtonsEditor || {};
const BotButtonsMenuView = window.BotButtonsMenuView || {};
const BotButtonsEditorView = window.BotButtonsEditorView || {};

function botFilesCtx() {
  return {
    state,
    escapeHtml,
    FIELD_LABELS,
    OPERATOR_OPTIONS,
    renderEditor,
  };
}

function editorCtx() {
  return {
    state,
    el,
    api,
    setStatus,
    render,
    refreshMenuStructure,
    normalizeConditionalRuleForState,
    fieldMeta,
    fieldLabel,
    escapeHtml,
    renderEditorImpl: renderEditor,
  };
}

function menuViewCtx() {
  return {
    state,
    el,
    api,
    escapeHtml,
    safeText,
    yesNo,
    nodeKindLabel,
    renderInfoRow,
    setStatus,
    render,
    openMenu,
    loadMenuTypes,
    confirmDiscard,
    discardEditorChanges,
    currentLevel,
    renderRightPanel,
    selectedMenuInfo,
    systemTicketLoadError: state.systemTicketLoadError,
  };
}

function hasUnsavedChanges() {
  if (state.view === "system_editor") return hasSystemFirstConnectionUnsavedChanges();
  if (state.view === "system_button_group_editor") return hasSystemButtonGroupUnsavedChanges();
  return BotButtonsEditor.hasUnsavedChanges(editorCtx());
}

function confirmDiscard() {
  return BotButtonsEditor.confirmDiscard(editorCtx());
}

function discardEditorChanges() {
  if (state.view === "system_editor") {
    discardSystemFirstConnectionChanges();
    return;
  }
  if (state.view === "system_button_group_editor") {
    discardSystemButtonGroupChanges();
    return;
  }
  BotButtonsEditor.discardEditorChanges(editorCtx());
}

function emptySystemFirstConnectionDraft() {
  return {
    subject: "",
    clientText: "",
    systemText: "",
    instructionText: "",
  };
}

function textStatus(value) {
  return String(value || "").trim() ? "\u0417\u0430\u0434\u0430\u043d" : "\u041d\u0435 \u0437\u0430\u0434\u0430\u043d";
}

function hydrateSystemFirstConnectionState(item) {
  const draft = {
    subject: String(item?.subject_effective || ""),
    clientText: String(item?.client_text_effective || ""),
    systemText: String(item?.system_text_effective || ""),
    instructionText: String(item?.instruction_text || ""),
  };
  state.systemFirstConnection = item || null;
  state.systemFirstConnectionDraft = { ...draft };
  state.savedSystemFirstConnectionDraft = { ...draft };
}

function hasSystemFirstConnectionUnsavedChanges() {
  const current = state.systemFirstConnectionDraft || emptySystemFirstConnectionDraft();
  const saved = state.savedSystemFirstConnectionDraft || emptySystemFirstConnectionDraft();
  return JSON.stringify(current) !== JSON.stringify(saved);
}

function discardSystemFirstConnectionChanges() {
  state.systemFirstConnectionDraft = {
    ...(state.savedSystemFirstConnectionDraft || emptySystemFirstConnectionDraft()),
  };
}

function confirmSystemFirstConnectionDiscard() {
  if (!hasSystemFirstConnectionUnsavedChanges()) return true;
  return window.confirm("\u0415\u0441\u0442\u044c \u043d\u0435\u0441\u043e\u0445\u0440\u0430\u043d\u0451\u043d\u043d\u044b\u0435 \u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u044f. \u0423\u0439\u0442\u0438 \u0431\u0435\u0437 \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u0438\u044f?");
}

function hasSystemButtonGroupUnsavedChanges() {
  return JSON.stringify(state.systemButtonGroup || null) !== JSON.stringify(state.savedSystemButtonGroup || null);
}

function discardSystemButtonGroupChanges() {
  state.systemButtonGroup = state.savedSystemButtonGroup
    ? JSON.parse(JSON.stringify(state.savedSystemButtonGroup))
    : null;
}

function systemButtonGroupTitle() {
  return state.systemButtonGroup?.title || "";
}

function renderSystemButtonStylePicker(button) {
  const selectedStyle = String(button?.buttonStyle || "");
  const options = [
    { value: "", label: "\u0411\u0430\u0437\u043e\u0432\u044b\u0439", title: "\u0411\u0430\u0437\u043e\u0432\u044b\u0439" },
    { value: "primary", label: "", title: "\u0421\u0438\u043d\u0438\u0439" },
    { value: "success", label: "", title: "\u0417\u0435\u043b\u0451\u043d\u044b\u0439" },
    { value: "danger", label: "", title: "\u041a\u0440\u0430\u0441\u043d\u044b\u0439" },
  ];
  return `
    <div class="bot-button-style-picker" role="radiogroup" aria-label="\u0426\u0432\u0435\u0442 \u043a\u043d\u043e\u043f\u043a\u0438">
      ${options.map((option) => {
        const active = selectedStyle === option.value;
        const classes = [
          "bot-button-style-option",
          option.value ? `bot-button-style-${option.value}` : "bot-button-style-base",
          active ? "is-selected" : "",
        ].filter(Boolean).join(" ");
        return `
          <button
            type="button"
            class="${classes}"
            data-system-button-style="${escapeHtml(button.key)}"
            data-style-value="${escapeHtml(option.value)}"
            aria-label="${escapeHtml(option.title)}"
            title="${escapeHtml(option.title)}"
            aria-pressed="${active ? "true" : "false"}"
          >${option.label ? escapeHtml(option.label) : "<span aria-hidden=\"true\"></span>"}</button>
        `;
      }).join("")}
    </div>
  `;
}

function openSystemFirstConnectionTextEditor(options) {
  if (typeof BotButtonsEditorView.openTextEditorModal !== "function") {
    throw new Error("\u0422\u0435\u043a\u0441\u0442\u043e\u0432\u044b\u0439 \u0440\u0435\u0434\u0430\u043a\u0442\u043e\u0440 \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u0435\u043d");
  }
  BotButtonsEditorView.openTextEditorModal(options);
}

function nodeClass(node) {
  return BotButtonsLevelView.nodeClass(levelViewCtx(), node);
}

function cloneRows(rows) {
  return BotButtonsLevelView.cloneRows(levelViewCtx(), rows);
}

function currentLevel() {
  return BotButtonsLevelView.currentLevel(levelViewCtx());
}

async function openParentTaskEditor() {
  const level = currentLevel();
  if (!level || String(level.kind || "") !== "group_task_parent") return;
  const technicalKey = String(level.technicalKey || "").trim();
  if (!technicalKey) {
    alert("Не удалось определить технический ключ материнской задачи.");
    return;
  }
  if (!confirmDiscard()) return;
  discardEditorChanges();
  await openEditor({
    label: level.label || "Групповая задача",
    kind: "terminal",
    technical_key: technicalKey,
    workflow_type: level.workflowType || "group_task",
    full_path: level.fullPath || level.label || "",
    is_enabled: true,
  });
}

async function openSubmenuEditor() {
  const level = currentLevel();
  if (!level || String(level.kind || "") !== "submenu") return;
  const technicalKey = String(level.technicalKey || "").trim();
  if (!technicalKey) {
    alert("Не удалось определить технический ключ подменю.");
    return;
  }
  if (!confirmDiscard()) return;
  discardEditorChanges();
  await openEditor({
    label: level.label || "Подменю",
    kind: "submenu",
    technical_key: technicalKey,
    workflow_type: level.workflowType || "submenu",
    full_path: level.fullPath || level.label || "",
    is_enabled: true,
    children_rows: Array.isArray(level.rows) ? level.rows : [],
  });
}

function isLayoutEditableLevel(level) {
  return BotButtonsLevelView.isLayoutEditableLevel(levelViewCtx(), level);
}

function buildPreviewFromNode(node) {
  return BotButtonsMenuView.buildPreviewFromNode(menuViewCtx(), node);
}

async function saveLabelOverrideForNode(node, nextLabel, enabled) {
  if (!node?.technicalKey || !state.selectedMenu) return;
  const label = String(nextLabel || "").trim();
  if (enabled && !label) {
    alert("Название в боте не может быть пустым.");
    return;
  }
  await api(
    `/api/v1/settings/bot-buttons/item/instruction?button_key=${encodeURIComponent(node.technicalKey)}&menu_type=${encodeURIComponent(state.selectedMenu)}&workflow_type=${encodeURIComponent(node.workflowType || "submenu")}&button_path=${encodeURIComponent(node.fullPath || node.label || "")}&button_label=${encodeURIComponent(node.label || "")}`,
    {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        label_overridden: !!enabled,
        label_override: enabled ? label : null,
      }),
    },
  );
  await refreshMenuStructure();
  render();
}

async function loadPreviewItem(node) {
  await BotButtonsMenuView.loadPreviewItem(menuViewCtx(), node);
}

function renderRightPanel() {
  return BotButtonsMenuView.renderRightPanel(menuViewCtx());
}

function renderMenuTypes() {
  return BotButtonsMenuView.renderMenuTypes(menuViewCtx());
}

function menuTypeTitle(item) {
  return BotButtonsMenuView.menuTypeTitle(item);
}

function selectedMenuInfo() {
  return BotButtonsMenuView.selectedMenuInfo(menuViewCtx());
}

function renderMenuBindingsPanel() {
  return BotButtonsMenuView.renderMenuBindingsPanel(menuViewCtx());
}

function levelViewCtx() {
  return {
    state,
    el,
    api,
    escapeHtml,
    setStatus,
    render,
    renderEditor,
    renderRightPanel,
    renderMenuBindingsPanel,
    buildPreviewFromNode,
    loadPreviewItem,
    confirmDiscard,
    discardEditorChanges,
    openEditor,
    openParentTaskEditor,
    openSubmenuEditor,
    createLevelItem,
    refreshMenuStructure,
    reloadMenuMeta,
    loadMenuTypes,
    openSystemFirstConnectionEditor,
    openSystemButtonGroupEditor,
    saveLabelOverrideForNode,
    currentLevel,
    isLayoutEditableLevel,
    cloneRows,
  };
}

function renderLevel() {
  return BotButtonsLevelView.renderLevel(levelViewCtx());
}

async function openSystemFirstConnectionEditor() {
  return loadSystemFirstConnectionEditorState({ api, state, render, setStatus });
}

async function openSystemButtonGroupEditor(groupKey) {
  return loadSystemButtonGroupEditorState({ api, state, render, setStatus }, groupKey);
}

function renderSystemFirstConnectionEditor() {
  const item = state.systemFirstConnection;
  if (!item) {
    state.view = "menu_types";
    render();
    return;
  }
  const draft = state.systemFirstConnectionDraft || emptySystemFirstConnectionDraft();
  el.content.innerHTML = `
    <div class="bot-editor-shell" data-system-editor="first-connection">
      <div class="bot-editor-section">
        <div class="bot-editor-title">${FIRST_CONNECTION_TITLE}</div>
        <div class="status">\u041e\u0442\u0434\u0435\u043b\u044c\u043d\u0430\u044f \u0441\u0438\u0441\u0442\u0435\u043c\u043d\u0430\u044f \u0437\u0430\u044f\u0432\u043a\u0430 \u0441 runtime-\u043f\u043e\u043b\u044f\u043c\u0438 \u0438\u0437 \u0411\u0414.</div>
      </div>
      <div class="bot-editor-grid">
        <div class="bot-editor-col bot-editor-col-left">
          <div class="bot-editor-section">
            <label class="field-label" for="systemFirstConnectionSubject">\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u0437\u0430\u044f\u0432\u043a\u0438</label>
            <input id="systemFirstConnectionSubject" class="input" type="text" value="${escapeHtml(draft.subject || "")}" autocomplete="off" />
          </div>
          <div class="bot-editor-section">
            <div class="bot-editor-text-row">
              <label class="field-label">\u0422\u0435\u043a\u0441\u0442 \u043a\u043b\u0438\u0435\u043d\u0442\u0443</label>
              <button id="editSystemFirstConnectionClientTextBtn" type="button" class="action">\u0420\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u0442\u044c</button>
            </div>
            <div class="status">\u0421\u0442\u0430\u0442\u0443\u0441: ${escapeHtml(textStatus(draft.clientText))}</div>
          </div>
        </div>
        <div class="bot-editor-col bot-editor-col-right">
          <div class="bot-editor-section">
            <div class="bot-editor-text-row">
              <label class="field-label">\u0421\u0438\u0441\u0442\u0435\u043c\u043d\u044b\u0439 \u0442\u0435\u043a\u0441\u0442</label>
              <button id="editSystemFirstConnectionSystemTextBtn" type="button" class="action">\u0420\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u0442\u044c</button>
            </div>
            <div class="status">\u0421\u0442\u0430\u0442\u0443\u0441: ${escapeHtml(textStatus(draft.systemText))}</div>
          </div>
          <div class="bot-editor-section">
            <div class="bot-editor-text-row">
              <label class="field-label">Instruction text</label>
              <button id="editSystemFirstConnectionInstructionTextBtn" type="button" class="action">\u0420\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u0442\u044c</button>
            </div>
            <div class="status">\u0421\u0442\u0430\u0442\u0443\u0441: ${escapeHtml(textStatus(draft.instructionText))}</div>
          </div>
        </div>
      </div>
      <div class="bot-info-actions">
        <button id="backFromSystemEditorBtn" type="button" class="action">\u041d\u0430\u0437\u0430\u0434</button>
        <button id="saveSystemFirstConnectionBtn" type="button" class="action action-primary">\u0421\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u044c</button>
      </div>
    </div>`;

  document.getElementById("systemFirstConnectionSubject")?.addEventListener("input", (event) => {
    state.systemFirstConnectionDraft.subject = String(event.target?.value || "");
  });

  document.getElementById("editSystemFirstConnectionClientTextBtn")?.addEventListener("click", () => {
    openSystemFirstConnectionTextEditor({
      title: "\u0420\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435 \u0442\u0435\u043a\u0441\u0442\u0430 \u043a\u043b\u0438\u0435\u043d\u0442\u0443",
      value: draft.clientText || "",
      placeholder: "\u0421\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435, \u043a\u043e\u0442\u043e\u0440\u043e\u0435 \u0431\u043e\u0442 \u043e\u0442\u043f\u0440\u0430\u0432\u0438\u0442 \u043a\u043b\u0438\u0435\u043d\u0442\u0443",
      withTelegramPreview: true,
      withTelegramToolbar: true,
      onApply: (nextValue) => {
        state.systemFirstConnectionDraft.clientText = nextValue;
        renderSystemFirstConnectionEditor();
      },
    });
  });

  document.getElementById("editSystemFirstConnectionSystemTextBtn")?.addEventListener("click", () => {
    openSystemFirstConnectionTextEditor({
      title: "\u0420\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435 \u0441\u0438\u0441\u0442\u0435\u043c\u043d\u043e\u0433\u043e \u0442\u0435\u043a\u0441\u0442\u0430",
      value: draft.systemText || "",
      placeholder: "\u0421\u0438\u0441\u0442\u0435\u043c\u043d\u044b\u0439 \u0442\u0435\u043a\u0441\u0442 \u0434\u043b\u044f \u0441\u043e\u0442\u0440\u0443\u0434\u043d\u0438\u043a\u043e\u0432",
      onApply: (nextValue) => {
        state.systemFirstConnectionDraft.systemText = nextValue;
        renderSystemFirstConnectionEditor();
      },
    });
  });

  document.getElementById("editSystemFirstConnectionInstructionTextBtn")?.addEventListener("click", () => {
    openSystemFirstConnectionTextEditor({
      title: "\u0420\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435 \u0438\u043d\u0441\u0442\u0440\u0443\u043a\u0446\u0438\u0438",
      value: draft.instructionText || "",
      placeholder: "\u0412\u043d\u0443\u0442\u0440\u0435\u043d\u043d\u044f\u044f \u0438\u043d\u0441\u0442\u0440\u0443\u043a\u0446\u0438\u044f \u0434\u043b\u044f \u0441\u043e\u0442\u0440\u0443\u0434\u043d\u0438\u043a\u043e\u0432",
      onApply: (nextValue) => {
        state.systemFirstConnectionDraft.instructionText = nextValue;
        renderSystemFirstConnectionEditor();
      },
    });
  });

  document.getElementById("backFromSystemEditorBtn")?.addEventListener("click", () => {
    if (!confirmSystemFirstConnectionDiscard()) return;
    discardSystemFirstConnectionChanges();
    state.view = "menu_types";
    render();
  });

  document.getElementById("saveSystemFirstConnectionBtn")?.addEventListener("click", async () => {
    const previous = state.savedSystemFirstConnectionDraft || emptySystemFirstConnectionDraft();
    const payload = {
      ...(draft.subject.trim() && draft.subject.trim() !== previous.subject.trim() ? { subject: draft.subject.trim() } : {}),
      ...(draft.clientText.trim() && draft.clientText.trim() !== previous.clientText.trim() ? { client_text: draft.clientText.trim() } : {}),
      ...(draft.systemText.trim() && draft.systemText.trim() !== previous.systemText.trim() ? { system_text: draft.systemText.trim() } : {}),
      ...(draft.instructionText.trim() && draft.instructionText.trim() !== previous.instructionText.trim() ? { instruction_text: draft.instructionText.trim() } : {}),
    };
    if (!Object.keys(payload).length) {
      setStatus("\u041d\u0435\u0442 \u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u0439 \u0434\u043b\u044f \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u0438\u044f");
      return;
    }
    setStatus("\u0421\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u0438\u0435 \u0441\u0438\u0441\u0442\u0435\u043c\u043d\u043e\u0439 \u0437\u0430\u044f\u0432\u043a\u0438...");
    try {
      await api("/api/v1/settings/bot-buttons/system/first-connection", {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });
      await loadSystemFirstConnectionEditorState({ api, state, render, setStatus });
      setStatus("\u0421\u0438\u0441\u0442\u0435\u043c\u043d\u0430\u044f \u0437\u0430\u044f\u0432\u043a\u0430 \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u0430");
    } catch (error) {
      setStatus(`\u041e\u0448\u0438\u0431\u043a\u0430: ${error?.message || "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0441\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u044c"}`);
    }
  });
}

function renderSystemButtonGroupEditor() {
  const group = state.systemButtonGroup;
  if (!group) {
    state.view = "menu_types";
    render();
    return;
  }
  el.content.innerHTML = `
    <div class="bot-editor-shell" data-system-editor="button-group" data-system-button-group="${escapeHtml(group.key)}">
      <div class="bot-editor-section">
        <div class="bot-editor-title">${escapeHtml(group.title)}</div>
      </div>
      <div class="bot-editor-col">
        ${(group.buttons || []).map((button) => `
          <div class="bot-editor-section" data-system-button-row="${escapeHtml(button.key)}">
            <label class="field-label" for="systemButtonText-${escapeHtml(button.key)}">${escapeHtml(button.defaultText || button.text || "")}</label>
            <input id="systemButtonText-${escapeHtml(button.key)}" class="input" type="text" value="${escapeHtml(button.text || "")}" data-system-button-text="${escapeHtml(button.key)}" autocomplete="off" />
            <label class="field-label">\u0426\u0432\u0435\u0442 \u043a\u043d\u043e\u043f\u043a\u0438</label>
            ${renderSystemButtonStylePicker(button)}
          </div>
        `).join("")}
      </div>
      <div class="bot-info-actions">
        <button id="backFromSystemButtonGroupBtn" type="button" class="action">\u041d\u0430\u0437\u0430\u0434</button>
        <button id="saveSystemButtonGroupBtn" type="button" class="action action-primary">\u0421\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u044c</button>
      </div>
    </div>`;

  document.querySelectorAll("[data-system-button-text]").forEach((input) => {
    input.addEventListener("input", () => {
      const key = input.getAttribute("data-system-button-text") || "";
      const target = state.systemButtonGroup?.buttons?.find((button) => button.key === key);
      if (target) target.text = String(input.value || "");
    });
  });
  document.querySelectorAll("[data-system-button-style]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const key = btn.getAttribute("data-system-button-style") || "";
      const target = state.systemButtonGroup?.buttons?.find((button) => button.key === key);
      if (target) {
        target.buttonStyle = btn.getAttribute("data-style-value") || "";
        renderSystemButtonGroupEditor();
      }
    });
  });
  document.getElementById("backFromSystemButtonGroupBtn")?.addEventListener("click", () => {
    if (!confirmDiscard()) return;
    discardSystemButtonGroupChanges();
    state.view = "menu_types";
    render();
  });
  document.getElementById("saveSystemButtonGroupBtn")?.addEventListener("click", async () => {
    const buttons = (state.systemButtonGroup?.buttons || []).map((button) => ({
      key: button.key,
      text: String(button.text || "").trim(),
      button_style: button.buttonStyle || null,
    }));
    if (buttons.some((button) => !button.text)) {
      alert("\u0422\u0435\u043a\u0441\u0442 \u043a\u043d\u043e\u043f\u043a\u0438 \u043d\u0435 \u043c\u043e\u0436\u0435\u0442 \u0431\u044b\u0442\u044c \u043f\u0443\u0441\u0442\u044b\u043c");
      return;
    }
    setStatus(`\u0421\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u0438\u0435: ${group.title}...`);
    try {
      await api(group.endpoint, {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ buttons }),
      });
      await loadSystemButtonGroupEditorState({ api, state, render, setStatus }, group.key);
      setStatus("\u0421\u0438\u0441\u0442\u0435\u043c\u043d\u044b\u0435 \u043a\u043d\u043e\u043f\u043a\u0438 \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u044b");
    } catch (error) {
      setStatus(`\u041e\u0448\u0438\u0431\u043a\u0430: ${error?.message || "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0441\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u044c"}`);
    }
  });
}

function buildMoveCandidates() {
  const candidates = [];
  const walkRows = (rows) => {
    (rows || []).forEach((row) => {
      (row.buttons || []).forEach((node) => {
        if (node.kind === "terminal" && !node.removed) {
          candidates.push({
            key: node.technical_key,
            label: node.label,
            path: node.full_path || node.label,
            workflowType: node.workflow_type,
          });
        }
        if (Array.isArray(node.children_rows) && node.children_rows.length) {
          walkRows(node.children_rows);
        }
      });
    });
  };
  walkRows(state.menuStructure?.tree_rows || []);
  return candidates;
}

function buildArchiveRowsFromStructure() {
  return BotButtonsLevelView.buildArchiveRowsFromStructure(levelViewCtx());
}

function hasAnyStoredSettings(item) {
  if (!item) return false;
  return Boolean(
    (item.instruction_text && String(item.instruction_text).trim()) ||
    item.system_text_overridden ||
    item.fixed_assignee_enabled ||
    item.fixed_assignee_user_id ||
    item.freeze_on_create ||
    item.confirmation_overridden ||
    item.confirmation_enabled ||
    (item.confirmation_text && String(item.confirmation_text).trim()) ||
    item.files_overridden ||
    (Array.isArray(item.files) && item.files.length > 0) ||
    item.conditional_files_overridden ||
    (Array.isArray(item.conditional_files) && item.conditional_files.length > 0) ||
    item.visibility_overridden
  );
}

function renderAssigneeOptions() {
  return BotButtonsEditor.renderAssigneeOptions(editorCtx());
}

function renderSelectedFiles() {
  return BotButtonsFiles.renderSelectedFiles(botFilesCtx());
}

function openFilePickerModal() {
  return BotButtonsFiles.openFilePickerModal(botFilesCtx());
}

function fieldType(field) {
  return BotButtonsFiles.fieldType(botFilesCtx(), field);
}

function fieldMeta(field) {
  return BotButtonsFiles.fieldMeta(botFilesCtx(), field);
}

function fieldLabel(field) {
  return BotButtonsFiles.fieldLabel(botFilesCtx(), field);
}

function operatorLabel(type, value) {
  return BotButtonsFiles.operatorLabel(botFilesCtx(), type, value);
}

function normalizeConditionalRuleForState(rawRule) {
  return BotButtonsFiles.normalizeConditionalRuleForState(botFilesCtx(), rawRule);
}

function renderConditionalRules() {
  return BotButtonsFiles.renderConditionalRules(botFilesCtx());
}

function openConditionalRuleFilesModal(ruleIndex) {
  return BotButtonsFiles.openConditionalRuleFilesModal(botFilesCtx(), ruleIndex);
}

function editorViewCtx() {
  return {
    state,
    el,
    api,
    escapeHtml,
    renderSelectedFiles,
    renderConditionalRules,
    buildMoveCandidates,
    renderAssigneeOptions,
    openFilePickerModal,
    openConditionalRuleFilesModal,
    fieldMeta,
    fieldLabel,
    OPERATOR_OPTIONS,
    render,
    saveInstruction,
    archiveMenuButton,
    restoreRemovedSetting,
    deleteRemovedSetting,
    moveRemovedSetting,
    hasAnyStoredSettings,
  };
}

function renderEditor() {
  return BotButtonsEditorView.renderEditor(editorViewCtx());
}

function renderBreadcrumb() {
  const parts = ["Кнопки бота"];
  if (state.selectedMenu) parts.push(state.selectedMenu);
  state.levelStack.forEach((level, index) => {
    if (index === 0) return;
    if (level.label) parts.push(level.label);
  });
  if (state.view === "editor" && state.selectedLeaf?.label) parts.push(state.selectedLeaf.label);
  if (state.view === "system_editor") parts.push(FIRST_CONNECTION_TITLE);
  if (state.view === "system_button_group_editor") parts.push(systemButtonGroupTitle());
  if (el.breadcrumb) {
    const pathText = parts.join(" → ");
    if (state.view === "editor") {
      el.breadcrumb.classList.add("bot-buttons-breadcrumb--with-action");
      el.breadcrumb.innerHTML = `
        <span class="bot-buttons-breadcrumb-path">${escapeHtml(pathText)}</span>
        <button id="saveInstructionBtn" type="button" class="action action-primary bot-breadcrumb-save-btn">Сохранить</button>
      `;
    } else {
      el.breadcrumb.classList.remove("bot-buttons-breadcrumb--with-action");
      el.breadcrumb.textContent = pathText;
    }
  }
  const selectedInfo = selectedMenuInfo();
  if (el.pageTitle) el.pageTitle.textContent = (selectedInfo?.display_name || state.selectedMenu) || "Кнопки бота";
}

function render() {
  renderBreadcrumb();
  if (state.view === "menu_types") return renderMenuTypes();
  if (state.view === "level") return renderLevel();
  if (state.view === "system_editor") return renderSystemFirstConnectionEditor();
  if (state.view === "system_button_group_editor") return renderSystemButtonGroupEditor();
  return renderEditor();
}

async function loadAssignees() {
  state.assignees = await api("/api/v1/settings/bot-buttons/assignees");
}

async function loadMenuTypes() {
  setStatus("Загрузка типов кнопок...");
  const [menuTypes, archivedMenuTypes, destinations] = await Promise.all([
    api("/api/v1/settings/bot-buttons/menu-types"),
    api("/api/v1/settings/bot-buttons/menu-types/archived"),
    api("/api/v1/settings/bot-buttons/destination-options"),
  ]);
  state.menuTypes = Array.isArray(menuTypes) ? menuTypes.filter((item) => !isSystemMenuTypeRecord(item)) : [];
  state.archivedMenuTypes = Array.isArray(archivedMenuTypes) ? archivedMenuTypes.filter((item) => !isSystemMenuTypeRecord(item)) : [];
  state.destinationOptions = Array.isArray(destinations?.items) ? destinations.items : [];
  state.freeDestinationOptions = Array.isArray(destinations?.free) ? destinations.free : [];
  state.view = "menu_types";
  state.selectedMenu = null;
  state.menuStructure = null;
  state.levelStack = [];
  state.selectedLeaf = null;
  state.systemFirstConnection = null;
  state.systemButtonGroup = null;
  state.savedSystemButtonGroup = null;
  state.systemTicketLoadError = "";
  state.systemFirstConnectionDraft = emptySystemFirstConnectionDraft();
  state.savedSystemFirstConnectionDraft = emptySystemFirstConnectionDraft();
  state.editorText = "";
  state.savedText = "";
  state.editorClientText = "";
  state.savedClientText = "";
  state.editorBotLabel = "";
  state.savedBotLabel = "";
  state.editorSystemText = "";
  state.savedSystemText = "";
  state.editorWorkflowType = "";
  state.savedWorkflowType = "";
  state.systemTextOverridden = false;
  state.fixedAssigneeEnabled = false;
  state.savedFixedAssigneeEnabled = false;
  state.fixedAssigneeUserId = "";
  state.savedFixedAssigneeUserId = "";
  state.freezeOnCreate = false;
  state.savedFreezeOnCreate = false;
  state.confirmationEnabled = false;
  state.savedConfirmationEnabled = false;
  state.confirmationText = "";
  state.savedConfirmationText = "";
  state.filesOverridden = false;
  state.savedFilesOverridden = false;
  state.selectedFiles = [];
  state.savedSelectedFiles = [];
  state.conditionalFilesOverridden = false;
  state.savedConditionalFilesOverridden = false;
  state.conditionalRules = [];
  state.savedConditionalRules = [];
  state.loadedSendFilesSnapshot = null;
  state.sendFilesProjectionError = "";
  state.isEnabled = true;
  state.savedIsEnabled = true;
  state.automationKeyValue = "";
  state.savedAutomationKeyValue = "";
  state.preview = {
    kind: null,
    label: "",
    technicalKey: "",
    workflowType: "",
    childrenCount: 0,
    item: null,
    loading: false,
    menuName: "",
  };
  render();
  setStatus("Готово");
}

async function reloadMenuMeta() {
  await BotButtonsMenuView.reloadMenuMeta(menuViewCtx());
}

async function createMenuType() {
  await BotButtonsMenuView.createMenuType(menuViewCtx());
}

function openCreateMenuTypeModal() {
  return BotButtonsMenuView.openCreateMenuTypeModal(menuViewCtx());
}

async function openMenu(menuName) {
  if (!menuName) return;
  setStatus(`Загрузка структуры "${menuName}"...`);
  state.selectedMenu = menuName;
  const info = (state.menuTypes || []).find((item) => item.menu_name === menuName);
  const displayName = info?.display_name || menuName;
  state.preview = {
    kind: null,
    label: "",
    technicalKey: "",
    workflowType: "",
    childrenCount: 0,
    item: null,
    loading: false,
    menuName: menuName,
  };
  render();
  const fieldsPayload = await api(`/api/v1/settings/bot-buttons/conditional-fields?menu_type=${encodeURIComponent(menuName)}`);
  state.conditionalFields = Array.isArray(fieldsPayload?.items) ? fieldsPayload.items : [];
  state.conditionalRules = [];
  state.savedConditionalRules = [];
  state.conditionalFilesOverridden = false;
  state.savedConditionalFilesOverridden = false;
  state.loadedSendFilesSnapshot = null;
  state.sendFilesProjectionError = "";
  const structure = await api(`/api/v1/settings/bot-buttons/menu-types/${encodeURIComponent(menuName)}/structure`);
  state.preview = {
    kind: null,
    label: "",
    technicalKey: "",
    workflowType: "",
    childrenCount: 0,
    item: null,
    loading: false,
    menuName: menuName,
  };
  state.menuStructure = structure;
  state.levelStack = [{ label: displayName, rows: structure.tree_rows || [], levelKey: structure.root_level_key || `${menuName}|level|__root__`, fullPath: "", kind: "root" }];
  state.selectedLeaf = null;
  state.view = "level";
  render();
  setStatus("Готово");
}

async function openEditor(leaf) {
  await BotButtonsEditor.openEditor(editorCtx(), leaf);
}

async function refreshMenuStructure() {
  if (!state.selectedMenu) return;
  const refreshed = await api(`/api/v1/settings/bot-buttons/menu-types/${encodeURIComponent(state.selectedMenu)}/structure`);
  state.menuStructure = refreshed;
  if (state.view === "level") {
    state.levelStack = [{ label: state.selectedMenu, rows: refreshed.tree_rows || [], levelKey: refreshed.root_level_key || `${state.selectedMenu}|level|__root__`, fullPath: "", kind: "root" }];
  }
}

async function saveInstruction() {
  await BotButtonsEditor.saveInstruction(editorCtx());
}

async function deleteRemovedSetting() {
  const leaf = state.selectedLeaf;
  if (!leaf) return;
  const wasInArchive = String(currentLevel()?.levelKey || "").includes("|level|__removed__");
  setStatus("Удаление записи...");
  await api(`/api/v1/settings/bot-buttons/item?button_key=${encodeURIComponent(leaf.technical_key)}&hard=true`, { method: "DELETE" });
  state.view = "level";
  await refreshMenuStructure();
  if (wasInArchive) {
    state.levelStack.push({
      label: "Архив удалённых",
      rows: buildArchiveRowsFromStructure(),
      levelKey: `${state.selectedMenu}|level|__removed__`,
    });
  }
  state.view = "level";
  state.selectedLeaf = null;
  render();
  setStatus("Запись удалена");
}

async function archiveMenuButton() {
  const leaf = state.selectedLeaf;
  if (!leaf) return;
  setStatus("Скрытие из меню...");
  await api(`/api/v1/settings/bot-buttons/item?button_key=${encodeURIComponent(leaf.technical_key)}`, { method: "DELETE" });
  await refreshMenuStructure();
  state.view = "level";
  state.selectedLeaf = null;
  render();
  setStatus("Элемент скрыт");
}

async function restoreRemovedSetting() {
  const leaf = state.selectedLeaf;
  if (!leaf) return;
  const wasInArchive = String(currentLevel()?.levelKey || "").includes("|level|__removed__");
  setStatus("Восстановление...");
  await api(`/api/v1/settings/bot-buttons/item/restore?button_key=${encodeURIComponent(leaf.technical_key)}`, { method: "POST" });
  state.view = "level";
  await refreshMenuStructure();
  if (wasInArchive) {
    state.levelStack.push({
      label: "Архив удалённых",
      rows: buildArchiveRowsFromStructure(),
      levelKey: `${state.selectedMenu}|level|__removed__`,
    });
  }
  state.view = "level";
  state.selectedLeaf = null;
  render();
  setStatus("Элемент восстановлен");
}

async function moveRemovedSetting(targetKey, targetPath, targetWorkflowType) {
  const leaf = state.selectedLeaf;
  if (!leaf || !state.selectedMenu) return;
  setStatus("Перенос...");
  await api(
    `/api/v1/settings/bot-buttons/item/rebind?old_button_key=${encodeURIComponent(leaf.technical_key)}&new_button_key=${encodeURIComponent(targetKey)}&menu_type=${encodeURIComponent(state.selectedMenu)}&workflow_type=${encodeURIComponent(targetWorkflowType)}&button_path=${encodeURIComponent(targetPath)}&button_label=${encodeURIComponent((targetPath || "").split("/").pop() || targetPath)}`,
    { method: "POST" },
  );
  await refreshMenuStructure();
  state.view = "level";
  state.selectedLeaf = null;
  render();
  setStatus("Перенесено");
}

function openCreateItemModal({ isChildTaskLevel = false } = {}) {
  return new Promise((resolve) => {
    const typeOptions = [
      { value: "section", label: "Раздел / подменю" },
      { value: "ticket", label: "Заявка" },
      { value: "task", label: "Задача" },
      { value: "auto", label: "Автоматическая задача" },
      { value: "group_task", label: "Групповая задача" },
    ];
    const bodyHtml = isChildTaskLevel
      ? `<div class="bot-create-item-note">Уровень групповой задачи: будет создана <b>child task</b>.</div>`
      : `<div class="bot-create-item-types">
          ${typeOptions.map((opt, idx) => `
            <label class="bot-create-item-type">
              <input type="radio" name="botCreateKind" value="${opt.value}" ${idx === 1 ? "checked" : ""} />
              <span>${escapeHtml(opt.label)}</span>
            </label>
          `).join("")}
        </div>`;
    const wrapper = document.createElement("div");
    wrapper.innerHTML = `
      <div class="modal-overlay">
        <div class="modal-dialog bot-file-picker-dialog bot-create-item-dialog">
          <div class="modal-header"><h3>Создание кнопки</h3></div>
          <div class="modal-body">
            <div class="bot-create-item-section">
              <div class="bot-create-item-label">Выберите тип создаваемой кнопки</div>
              ${bodyHtml}
            </div>
            <div class="bot-create-item-section">
              <div class="bot-create-item-label">Название кнопки</div>
              <input id="botCreateLabelInput" class="input" type="text" maxlength="120" />
            </div>
            <div class="bot-create-item-section">
              <label class="bot-create-item-active">
                <span>Кнопка активна</span>
                <input id="botCreateEnabledInput" type="checkbox" checked />
              </label>
            </div>
          </div>
          <div class="modal-footer">
            <button type="button" id="cancelCreateItemBtn" class="action">Отмена</button>
            <button type="button" id="applyCreateItemBtn" class="action action-primary">Создать</button>
          </div>
        </div>
      </div>`;
    const overlay = wrapper.firstElementChild;
    document.body.appendChild(overlay);

    const close = (result) => {
      overlay.remove();
      resolve(result);
    };
    overlay.querySelector("#cancelCreateItemBtn")?.addEventListener("click", () => close(null));
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) close(null);
    });

    const labelInput = overlay.querySelector("#botCreateLabelInput");
    const enabledInput = overlay.querySelector("#botCreateEnabledInput");
    labelInput?.focus();

    const apply = () => {
      const label = String(labelInput?.value || "").trim();
      if (!label) {
        alert("Название обязательно.");
        labelInput?.focus();
        return;
      }
      let kind = "group_task_child";
      if (!isChildTaskLevel) {
        const checked = overlay.querySelector("input[name='botCreateKind']:checked");
        kind = String(checked?.value || "").trim().toLowerCase();
      }
      close({
        kind,
        label,
        enabled: !!enabledInput?.checked,
      });
    };
    overlay.querySelector("#applyCreateItemBtn")?.addEventListener("click", apply);
    labelInput?.addEventListener("keydown", (event) => {
      if (event.key === "Enter") apply();
    });
  });
}

async function createLevelItem() {
  if (!state.selectedMenu) return;
  if (!confirmDiscard()) return;
  discardEditorChanges();
  const current = currentLevel();
  const parentPath = String(current?.fullPath || "");
  const isChildTaskLevel = String(current?.kind || "") === "group_task_parent";
  const result = await openCreateItemModal({ isChildTaskLevel });
  if (!result) return;
  const kind = String(result.kind || "").trim().toLowerCase();
  if (!["section", "ticket", "task", "auto", "group_task", "group_task_child"].includes(kind)) {
    alert("Недопустимый тип кнопки.");
    return;
  }
  const label = String(result.label || "").trim();
  const enabled = !!result.enabled;
  setStatus("Создание кнопки...");
  await api("/api/v1/settings/bot-buttons/item/create", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      menu_type: state.selectedMenu,
      parent_path: parentPath,
      button_kind: kind,
      workflow_type: kind === "section" ? "submenu" : (kind === "group_task" ? "group_task" : (kind === "group_task_child" ? "group_task_child" : kind)),
      label,
      is_enabled: enabled,
    }),
  });
  await refreshMenuStructure();
  render();
  setStatus("Кнопка добавлена");
}

function handleBack() {
  if (state.view === "system_editor") {
    if (!confirmSystemFirstConnectionDiscard()) return;
    discardSystemFirstConnectionChanges();
    state.view = "menu_types";
    render();
    return;
  }
  if (state.view === "system_button_group_editor") {
    if (!confirmDiscard()) return;
    discardSystemButtonGroupChanges();
    state.view = "menu_types";
    render();
    return;
  }
  if (state.view === "editor") {
    if (!confirmDiscard()) return;
    discardEditorChanges();
    state.view = "level";
    render();
    return;
  }
  if (state.view === "level") {
    if (!confirmDiscard()) return;
    discardEditorChanges();
    state.layoutEditMode = false;
    state.layoutDraftRows = [];
    if (state.levelStack.length > 1) {
      state.levelStack.pop();
      state.preview = {
        kind: null,
        label: "",
        technicalKey: "",
        workflowType: "",
        childrenCount: 0,
        item: null,
        loading: false,
        menuName: state.selectedMenu || "",
      };
      render();
      return;
    }
    state.view = "menu_types";
    state.selectedMenu = null;
    state.menuStructure = null;
    state.levelStack = [];
    state.preview = {
      kind: null,
      label: "",
      technicalKey: "",
      workflowType: "",
      childrenCount: 0,
      item: null,
      loading: false,
      menuName: "",
    };
    render();
    return;
  }
  if (window.HelpdeskAppShell?.navigate) {
    window.HelpdeskAppShell.navigate("/settings");
    return;
  }
  window.location.href = "/settings";
}

function handleBeforeUnload(event) {
  if (!hasUnsavedChanges()) return;
  event.preventDefault();
  event.returnValue = "";
}

function cleanupBotButtonOverlays() {
  if (window.BotButtonsEditorView?.cleanup) {
    window.BotButtonsEditorView.cleanup();
  }
  document.querySelectorAll("body > .modal-overlay").forEach((overlay) => overlay.remove());
}

window.addEventListener("beforeunload", handleBeforeUnload);

(function init() {
  bindSystemTicketOpenHandler({
    content: el.content,
    onOpen: openSystemFirstConnectionEditor,
    onOpenGroup: openSystemButtonGroupEditor,
    onError: (error) => setStatus(`\u041e\u0448\u0438\u0431\u043a\u0430: ${error?.message || "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043e\u0442\u043a\u0440\u044b\u0442\u044c \u0441\u0438\u0441\u0442\u0435\u043c\u043d\u0443\u044e \u0437\u0430\u044f\u0432\u043a\u0443"}`),
    confirmDiscard,
    discardEditorChanges,
  });
  el.backToSettingsBtn?.addEventListener("click", handleBack);
window.HelpdeskSidebar.init({ onError: (e) => setStatus(`Ошибка: ${e.message}`) })
    .then(async () => {
      await loadAssignees();
      const filesPayload = await api("/api/v1/settings/bot-buttons/files");
      state.availableFiles = Array.isArray(filesPayload?.items) ? filesPayload.items : [];
      const automationsPayload = await api("/api/v1/settings/bot-buttons/automations");
      state.availableAutomations = Array.isArray(automationsPayload?.items) ? automationsPayload.items : [];
      if (window.BotButtonsEditor?.syncAutomationSelectionMeta) {
        window.BotButtonsEditor.syncAutomationSelectionMeta(editorCtx());
      }
      if (state.view === "editor" || state.view === "system_editor" || state.view === "system_button_group_editor") {
        render();
      }
      await loadMenuTypes();
    })
    .catch((e) => {
      setStatus(`Ошибка: ${e.message}`);
    });
})();
return {
  canNavigateAway: confirmDiscard,
  destroy() {
    window.removeEventListener("beforeunload", handleBeforeUnload);
    unbindSystemTicketOpenHandler(el.content);
    cleanupBotButtonOverlays();
  },
};
}

if (window.HelpdeskAppShell?.registerPage) {
  window.HelpdeskAppShell.registerPage("settings-bot-buttons", initSettingsBotButtonsPage);
} else {
  initSettingsBotButtonsPage();
}
window.BotButtonsSystemTicket = {
  selector: SYSTEM_TICKET_SELECTOR,
  labels: {
    firstConnectionTitle: FIRST_CONNECTION_TITLE,
    reviewTitle: REVIEW_SYSTEM_TITLE,
    feedbackTitle: FEEDBACK_SYSTEM_TITLE,
  },
  bindOpenHandler: bindSystemTicketOpenHandler,
  unbindOpenHandler: unbindSystemTicketOpenHandler,
  loadEditorState: loadSystemFirstConnectionEditorState,
  loadButtonGroupEditorState: loadSystemButtonGroupEditorState,
};
})();
