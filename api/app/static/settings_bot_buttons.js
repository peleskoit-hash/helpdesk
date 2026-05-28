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
  editorSystemText: "",
  savedSystemText: "",
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
  isEnabled: true,
  savedIsEnabled: true,
  automationKey: "",
  automationTitle: "",
  automationDescription: "",
  automationFrom: "none",
  automationStatus: "not_set",
  automationAvailable: false,
  automationHandler: "",
  automationKeyValue: "",
  savedAutomationKeyValue: "",
  availableAutomations: [],
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
  };
}

function hasUnsavedChanges() {
  return BotButtonsEditor.hasUnsavedChanges(editorCtx());
}

function confirmDiscard() {
  return BotButtonsEditor.confirmDiscard(editorCtx());
}

function discardEditorChanges() {
  BotButtonsEditor.discardEditorChanges(editorCtx());
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
    createLevelItem,
    refreshMenuStructure,
    reloadMenuMeta,
    loadMenuTypes,
    saveLabelOverrideForNode,
    currentLevel,
    isLayoutEditableLevel,
    cloneRows,
  };
}

function renderLevel() {
  return BotButtonsLevelView.renderLevel(levelViewCtx());
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
  state.menuTypes = menuTypes;
  state.archivedMenuTypes = Array.isArray(archivedMenuTypes) ? archivedMenuTypes : [];
  state.destinationOptions = Array.isArray(destinations?.items) ? destinations.items : [];
  state.freeDestinationOptions = Array.isArray(destinations?.free) ? destinations.free : [];
  state.view = "menu_types";
  state.selectedMenu = null;
  state.menuStructure = null;
  state.levelStack = [];
  state.selectedLeaf = null;
  state.editorText = "";
  state.savedText = "";
  state.editorClientText = "";
  state.savedClientText = "";
  state.editorBotLabel = "";
  state.savedBotLabel = "";
  state.editorSystemText = "";
  state.savedSystemText = "";
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
  window.location.href = "/settings";
}

window.addEventListener("beforeunload", (event) => {
  if (!hasUnsavedChanges()) return;
  event.preventDefault();
  event.returnValue = "";
});

(function init() {
  el.backToSettingsBtn?.addEventListener("click", handleBack);
window.HelpdeskSidebar.init({ onError: (e) => setStatus(`Ошибка: ${e.message}`) })
    .then(async () => {
      await loadAssignees();
      const filesPayload = await api("/api/v1/settings/bot-buttons/files");
      state.availableFiles = Array.isArray(filesPayload?.items) ? filesPayload.items : [];
      const automationsPayload = await api("/api/v1/settings/bot-buttons/automations");
      state.availableAutomations = Array.isArray(automationsPayload?.items) ? automationsPayload.items : [];
      await loadMenuTypes();
    })
    .catch((e) => {
      setStatus(`Ошибка: ${e.message}`);
      console.error(e);
    });
})();

