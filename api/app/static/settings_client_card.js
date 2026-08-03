(function () {
function initSettingsClientCardPage() {
const state = {
  clientCard: { blocks: [], sources: [] },
  expandedBlocks: new Set(),
  editingFieldId: null,
  editingRuleId: null,
  editingSourceId: null,
  createFieldBlockKey: null,
  sourceEditorDirty: false,
  pendingBlockColors: new Map(),
  pendingBlockSaveTimers: new Map(),
  pendingBlockSaveMeta: new Map(),
  activeClientCardTab: "blocks",
  activeAutomationTab: "rules",
};

const colorSchemes = ["blue", "green", "yellow", "red", "gray", "purple"];
const fieldTypes = ["text", "textarea", "number", "date", "checkbox", "select", "phone", "url", "password"];
const fieldTypeLabels = {
  text: "\u0422\u0435\u043a\u0441\u0442",
  textarea: "\u041c\u043d\u043e\u0433\u043e\u0441\u0442\u0440\u043e\u0447\u043d\u044b\u0439 \u0442\u0435\u043a\u0441\u0442",
  number: "\u0427\u0438\u0441\u043b\u043e",
  date: "\u0414\u0430\u0442\u0430",
  checkbox: "\u0414\u0430 / \u041d\u0435\u0442",
  select: "\u0421\u043f\u0438\u0441\u043e\u043a",
  phone: "\u0422\u0435\u043b\u0435\u0444\u043e\u043d",
  url: "\u0421\u0441\u044b\u043b\u043a\u0430",
  password: "\u041f\u0430\u0440\u043e\u043b\u044c",
  readonly: "\u0422\u043e\u043b\u044c\u043a\u043e \u0447\u0442\u0435\u043d\u0438\u0435",
};
const ruleActionLabels = {
  show: "\u041f\u043e\u043a\u0430\u0437\u0430\u0442\u044c",
  hide: "\u0421\u043a\u0440\u044b\u0442\u044c",
  readonly: "\u0421\u0434\u0435\u043b\u0430\u0442\u044c \u0442\u043e\u043b\u044c\u043a\u043e \u0434\u043b\u044f \u0447\u0442\u0435\u043d\u0438\u044f",
  editable: "\u0420\u0430\u0437\u0440\u0435\u0448\u0438\u0442\u044c \u0440\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435",
  required: "\u0421\u0434\u0435\u043b\u0430\u0442\u044c \u043e\u0431\u044f\u0437\u0430\u0442\u0435\u043b\u044c\u043d\u044b\u043c",
  optional: "\u0421\u0434\u0435\u043b\u0430\u0442\u044c \u043d\u0435\u043e\u0431\u044f\u0437\u0430\u0442\u0435\u043b\u044c\u043d\u044b\u043c",
  set_value: "\u0417\u0430\u0434\u0430\u0442\u044c \u0437\u043d\u0430\u0447\u0435\u043d\u0438\u0435",
  lookup_source: "\u041f\u043e\u043b\u0443\u0447\u0438\u0442\u044c \u0434\u0430\u043d\u043d\u044b\u0435 \u0438\u0437 \u0438\u0441\u0442\u043e\u0447\u043d\u0438\u043a\u0430",
};
const ruleValueTypeLabels = {
  literal: "\u0422\u0435\u043a\u0441\u0442",
  field: "\u0417\u043d\u0430\u0447\u0435\u043d\u0438\u0435 \u0434\u0440\u0443\u0433\u043e\u0433\u043e \u043f\u043e\u043b\u044f",
  template: "\u0428\u0430\u0431\u043b\u043e\u043d",
  map: "\u0421\u043e\u043f\u043e\u0441\u0442\u0430\u0432\u043b\u0435\u043d\u0438\u0435",
  null: "\u041e\u0447\u0438\u0441\u0442\u0438\u0442\u044c",
};
const systemBlockKeys = new Set(["tg", "profile", "department", "work", "verification"]);
const saveContextSystemFieldKeys = new Set([
  "contact_name",
  "last_name",
  "email",
  "phone",
  "verification_status",
  "is_verified",
  "destination",
  "sand_owner",
  "sales_group",
  "department",
  "first_department",
  "group_name",
  "employee_type",
  "location_type",
  "anydesk",
  "internal_number",
  "external_user_id",
  "bitrix_login",
  "bitrix_password",
  "bitrix_id",
]);
const colorLabels = {
  blue: "\u0413\u043e\u043b\u0443\u0431\u043e\u0439",
  green: "\u0417\u0435\u043b\u0451\u043d\u044b\u0439",
  yellow: "\u0416\u0451\u043b\u0442\u044b\u0439",
  red: "\u041a\u0440\u0430\u0441\u043d\u044b\u0439",
  gray: "\u0421\u0435\u0440\u044b\u0439",
  purple: "\u0424\u0438\u043e\u043b\u0435\u0442\u043e\u0432\u044b\u0439",
};
const namedColorHex = {
  blue: "#dbeafe",
  green: "#dcfce7",
  yellow: "#fef3c7",
  red: "#ffe4e6",
  gray: "#e5e7eb",
  purple: "#ede9fe",
};
const fallbackColorHex = namedColorHex.gray;
const uiText = {
  active: "\u0410\u043a\u0442\u0438\u0432\u0435\u043d",
  inactive: "\u041d\u0435 \u0430\u043a\u0442\u0438\u0432\u0435\u043d",
  collapse: "\u0421\u0432\u0435\u0440\u043d\u0443\u0442\u044c",
  expand: "\u0420\u0430\u0441\u043a\u0440\u044b\u0442\u044c",
  title: "\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u0431\u043b\u043e\u043a\u0430",
  blockColor: "\u0426\u0432\u0435\u0442 \u0431\u043b\u043e\u043a\u0430",
  activeLabel: "\u0410\u043a\u0442\u0438\u0432\u0435\u043d",
  showInCard: "\u0412 \u043a\u0430\u0440\u0442\u043e\u0447\u043a\u0435",
  showInEditor: "\u0412 \u043e\u043a\u043d\u0435 \u0440\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u044f",
  showTitle: "\u041f\u043e\u043a\u0430\u0437\u044b\u0432\u0430\u0442\u044c \u043d\u0430\u0437\u0432\u0430\u043d\u0438\u0435",
  moveUp: "\u0412\u044b\u0448\u0435",
  moveDown: "\u041d\u0438\u0436\u0435",
  edit: "\u0420\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u0442\u044c",
  deleteBlock: "\u0423\u0434\u0430\u043b\u0438\u0442\u044c \u0431\u043b\u043e\u043a",
  deleteField: "\u0423\u0434\u0430\u043b\u0438\u0442\u044c \u043f\u043e\u043b\u0435",
  systemField: "\u0421\u0438\u0441\u0442\u0435\u043c\u043d\u043e\u0435 \u043f\u043e\u043b\u0435",
  blockSaved: "\u0411\u043b\u043e\u043a \u0441\u043e\u0445\u0440\u0430\u043d\u0451\u043d",
  colorSaved: "\u0426\u0432\u0435\u0442 \u0441\u043e\u0445\u0440\u0430\u043d\u0451\u043d",
  blockMoved: "\u041f\u043e\u0440\u044f\u0434\u043e\u043a \u0431\u043b\u043e\u043a\u043e\u0432 \u0441\u043e\u0445\u0440\u0430\u043d\u0451\u043d",
  fieldMoved: "\u041f\u043e\u0440\u044f\u0434\u043e\u043a \u043f\u043e\u043b\u0435\u0439 \u0441\u043e\u0445\u0440\u0430\u043d\u0451\u043d",
  blockDeleted: "\u0411\u043b\u043e\u043a \u0443\u0434\u0430\u043b\u0451\u043d",
  fieldDeleted: "\u041f\u043e\u043b\u0435 \u0443\u0434\u0430\u043b\u0435\u043d\u043e",
  deleteBlockConfirmPrefix: "\u0423\u0434\u0430\u043b\u0438\u0442\u044c \u0431\u043b\u043e\u043a \u00ab",
  deleteBlockConfirmSuffix: "\u00bb \u0438 \u0432\u0441\u0435 \u0435\u0433\u043e \u043f\u043e\u043b\u044f?",
  deleteFieldConfirmPrefix: "\u0423\u0434\u0430\u043b\u0438\u0442\u044c \u043f\u043e\u043b\u0435 \u00ab",
  deleteFieldConfirmSuffix: "\u00bb?",
  deleteRuleConfirmPrefix: "\u0423\u0434\u0430\u043b\u0438\u0442\u044c \u043f\u0440\u0430\u0432\u0438\u043b\u043e \u201c",
  deleteRuleConfirmSuffix: "\u201d?",
  addField: "+ \u0414\u043e\u0431\u0430\u0432\u0438\u0442\u044c \u043f\u043e\u043b\u0435",
  rulesSaved: "\u041f\u0440\u0430\u0432\u0438\u043b\u043e \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u043e",
  ruleDeleted: "\u041f\u0440\u0430\u0432\u0438\u043b\u043e \u0443\u0434\u0430\u043b\u0435\u043d\u043e",
  ruleCopied: "\u041a\u043e\u043f\u0438\u044f \u043f\u0440\u0430\u0432\u0438\u043b\u0430 \u0441\u043e\u0437\u0434\u0430\u043d\u0430",
  copy: "\u0421\u043a\u043e\u043f\u0438\u0440\u043e\u0432\u0430\u0442\u044c",
  sourceSaved: "\u0418\u0441\u0442\u043e\u0447\u043d\u0438\u043a \u0434\u0430\u043d\u043d\u044b\u0445 \u0441\u043e\u0445\u0440\u0430\u043d\u0451\u043d",
  sourceDeleted: "\u0418\u0441\u0442\u043e\u0447\u043d\u0438\u043a \u0434\u0430\u043d\u043d\u044b\u0445 \u0443\u0434\u0430\u043b\u0451\u043d",
};

const el = {
  statusText: document.getElementById("statusText"),
  backToSettingsBtn: document.getElementById("backToSettingsBtn"),
  addClientCardBlockBtn: document.getElementById("addClientCardBlockBtn"),
  clientCardMainTabs: Array.from(document.querySelectorAll("[data-client-card-main-tab]")),
  clientCardMainPanels: Array.from(document.querySelectorAll("[data-client-card-main-panel]")),
  clientCardAutomationTabs: Array.from(document.querySelectorAll("[data-client-card-automation-tab]")),
  clientCardAutomationPanels: Array.from(document.querySelectorAll("[data-client-card-automation-panel]")),
  clientCardSettings: document.getElementById("clientCardSettings"),
  emptyClientCardText: document.getElementById("emptyClientCardText"),
  addClientCardRuleBtn: document.getElementById("addClientCardRuleBtn"),
  clientCardRules: document.getElementById("clientCardRules"),
  emptyClientCardRulesText: document.getElementById("emptyClientCardRulesText"),
  addClientCardSourceBtn: document.getElementById("addClientCardSourceBtn"),
  clientCardSources: document.getElementById("clientCardSources"),
  emptyClientCardSourcesText: document.getElementById("emptyClientCardSourcesText"),
  clientCardSourceModal: document.getElementById("clientCardSourceModal"),
  clientCardSourceModalTitle: document.getElementById("clientCardSourceModalTitle"),
  closeClientCardSourceModalBtn: document.getElementById("closeClientCardSourceModalBtn"),
  saveClientCardSourceBtn: document.getElementById("saveClientCardSourceBtn"),
  deleteClientCardSourceBtn: document.getElementById("deleteClientCardSourceBtn"),
  testClientCardSourceBtn: document.getElementById("testClientCardSourceBtn"),
  sourceName: document.getElementById("sourceName"),
  sourceTechnicalKey: document.getElementById("sourceTechnicalKey"),
  sourceActive: document.getElementById("sourceActive"),
  sourceMethod: document.getElementById("sourceMethod"),
  sourceUrl: document.getElementById("sourceUrl"),
  sourceTimeoutMs: document.getElementById("sourceTimeoutMs"),
  sourceOnError: document.getElementById("sourceOnError"),
  sourceQueryRows: document.getElementById("sourceQueryRows"),
  addSourceQueryRowBtn: document.getElementById("addSourceQueryRowBtn"),
  sourceHeaderRows: document.getElementById("sourceHeaderRows"),
  addSourceHeaderRowBtn: document.getElementById("addSourceHeaderRowBtn"),
  sourceBodyJson: document.getElementById("sourceBodyJson"),
  sourceMappingRows: document.getElementById("sourceMappingRows"),
  addSourceMappingRowBtn: document.getElementById("addSourceMappingRowBtn"),
  sourceTestContext: document.getElementById("sourceTestContext"),
  sourceTestResult: document.getElementById("sourceTestResult"),
  sourceQueryComplexNotice: document.getElementById("sourceQueryComplexNotice"),
  sourceHeaderComplexNotice: document.getElementById("sourceHeaderComplexNotice"),
  sourceInsertUrlField: document.getElementById("sourceInsertUrlField"),
  sourceInsertUrlFieldBtn: document.getElementById("sourceInsertUrlFieldBtn"),
  sourceInsertBodyField: document.getElementById("sourceInsertBodyField"),
  sourceInsertBodyFieldBtn: document.getElementById("sourceInsertBodyFieldBtn"),
  clientCardRuleModal: document.getElementById("clientCardRuleModal"),
  clientCardRuleModalTitle: document.getElementById("clientCardRuleModalTitle"),
  closeClientCardRuleModalBtn: document.getElementById("closeClientCardRuleModalBtn"),
  saveClientCardRuleBtn: document.getElementById("saveClientCardRuleBtn"),
  deleteClientCardRuleBtn: document.getElementById("deleteClientCardRuleBtn"),
  ruleName: document.getElementById("ruleName"),
  ruleActive: document.getElementById("ruleActive"),
  ruleConditionField: document.getElementById("ruleConditionField"),
  ruleOperator: document.getElementById("ruleOperator"),
  ruleValueWrap: document.getElementById("ruleValueWrap"),
  ruleValue: document.getElementById("ruleValue"),
  ruleActions: document.getElementById("ruleActions"),
  addRuleActionBtn: document.getElementById("addRuleActionBtn"),
  createClientCardBlockModal: document.getElementById("createClientCardBlockModal"),
  closeCreateClientCardBlockModalBtn: document.getElementById("closeCreateClientCardBlockModalBtn"),
  saveCreateClientCardBlockBtn: document.getElementById("saveCreateClientCardBlockBtn"),
  newClientCardBlockTitle: document.getElementById("newClientCardBlockTitle"),
  newClientCardBlockColor: document.getElementById("newClientCardBlockColor"),
  newClientCardBlockColorHex: document.getElementById("newClientCardBlockColorHex"),
  createClientCardFieldModal: document.getElementById("createClientCardFieldModal"),
  closeCreateClientCardFieldModalBtn: document.getElementById("closeCreateClientCardFieldModalBtn"),
  saveCreateClientCardFieldBtn: document.getElementById("saveCreateClientCardFieldBtn"),
  newClientCardFieldLabel: document.getElementById("newClientCardFieldLabel"),
  newClientCardFieldType: document.getElementById("newClientCardFieldType"),
  newClientCardFieldBlock: document.getElementById("newClientCardFieldBlock"),
  newClientCardFieldVisible: document.getElementById("newClientCardFieldVisible"),
  newClientCardFieldVisibleInEditor: document.getElementById("newClientCardFieldVisibleInEditor"),
  newClientCardFieldEditable: document.getElementById("newClientCardFieldEditable"),
  newClientCardFieldRequired: document.getElementById("newClientCardFieldRequired"),
  newClientCardFieldOptionsWrap: document.getElementById("newClientCardFieldOptionsWrap"),
  newClientCardFieldOptions: document.getElementById("newClientCardFieldOptions"),
  editClientCardFieldModal: document.getElementById("editClientCardFieldModal"),
  closeEditClientCardFieldModalBtn: document.getElementById("closeEditClientCardFieldModalBtn"),
  saveEditClientCardFieldBtn: document.getElementById("saveEditClientCardFieldBtn"),
  editFieldLabel: document.getElementById("editFieldLabel"),
  editFieldVisible: document.getElementById("editFieldVisible"),
  editFieldVisibleInEditor: document.getElementById("editFieldVisibleInEditor"),
  editFieldEditable: document.getElementById("editFieldEditable"),
  editFieldRequired: document.getElementById("editFieldRequired"),
  editFieldSystemNote: document.getElementById("editFieldSystemNote"),
  editFieldBlockKey: document.getElementById("editFieldBlockKey"),
  editFieldKey: document.getElementById("editFieldKey"),
  editFieldType: document.getElementById("editFieldType"),
  editFieldOptionsWrap: document.getElementById("editFieldOptionsWrap"),
  editFieldOptions: document.getElementById("editFieldOptions"),
  deleteEditClientCardFieldBtn: document.getElementById("deleteEditClientCardFieldBtn"),
  errorModal: document.getElementById("errorModal"),
  errorText: document.getElementById("errorText"),
  closeErrorModalBtn: document.getElementById("closeErrorModalBtn"),
  copyErrorBtn: document.getElementById("copyErrorBtn"),
  okErrorBtn: document.getElementById("okErrorBtn"),
};

function setStatus(text) { el.statusText.textContent = text; }
function escapeHtml(v) { return String(v ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;"); }

function syncClientCardTabs() {
  const showBlocks = state.activeClientCardTab === "blocks";
  el.clientCardMainTabs.forEach((tab) => {
    const active = tab.dataset.clientCardMainTab === state.activeClientCardTab;
    tab.classList.toggle("primary", active);
    tab.setAttribute("aria-selected", active ? "true" : "false");
  });
  el.clientCardMainPanels.forEach((panel) => {
    panel.classList.toggle("hidden", panel.dataset.clientCardMainPanel !== state.activeClientCardTab);
  });
  el.addClientCardBlockBtn.classList.toggle("hidden", !showBlocks);

  el.clientCardAutomationTabs.forEach((tab) => {
    const active = tab.dataset.clientCardAutomationTab === state.activeAutomationTab;
    tab.classList.toggle("primary", active);
    tab.setAttribute("aria-selected", active ? "true" : "false");
  });
  el.clientCardAutomationPanels.forEach((panel) => {
    panel.classList.toggle("hidden", panel.dataset.clientCardAutomationPanel !== state.activeAutomationTab);
  });
}

function setClientCardTab(tab) {
  state.activeClientCardTab = tab === "automation" ? "automation" : "blocks";
  syncClientCardTabs();
}

function setAutomationTab(tab) {
  state.activeAutomationTab = tab === "sources" ? "sources" : "rules";
  syncClientCardTabs();
}

async function api(path, options = {}) {
  const r = await fetch(path, {
    ...options,
    credentials: "include",
    headers: { accept: "application/json", ...(options.headers || {}) },
  });
  const t = await r.text();
  let p = null;
  if (t) {
    try { p = JSON.parse(t); } catch { p = null; }
  }
  if (!r.ok) {
    const msg = p?.detail?.error?.message || t || "Ошибка запроса";
    const err = new Error(msg);
    err.status = r.status;
    err.code = p?.detail?.error?.code || "";
    err.details = p?.detail?.error?.details || {};
    throw err;
  }
  return p;
}

function showError(m) { el.errorText.value = m || "Неизвестная ошибка"; el.errorModal.classList.remove("hidden"); }
function closeError() { el.errorModal.classList.add("hidden"); }

function closeOnBackdropClick(modal, closeHandler) {
  let down = false;
  modal.addEventListener("pointerdown", (e) => { down = e.target === modal; });
  modal.addEventListener("click", (e) => {
    if (down && e.target === modal) closeHandler();
    down = false;
  });
}

function blockColorClass(color) {
  const value = String(color || "").trim().toLowerCase();
  return `client-card-color-${colorSchemes.includes(value) ? value : "custom"}`;
}

function isHexColor(value) {
  return /^#[0-9a-fA-F]{6}$/.test(String(value || "").trim());
}

function colorToHex(value) {
  const color = String(value || "").trim();
  const named = color.toLowerCase();
  if (namedColorHex[named]) return namedColorHex[named];
  if (isHexColor(color)) return color.toLowerCase();
  return fallbackColorHex;
}

function currentBlockColor(block) {
  return state.pendingBlockColors.get(block.id) || colorToHex(block.color_scheme);
}

function syncCreateBlockColorPreview() {
  const color = colorToHex(el.newClientCardBlockColor?.value);
  if (el.newClientCardBlockColor) el.newClientCardBlockColor.value = color;
  if (el.newClientCardBlockColorHex) el.newClientCardBlockColorHex.textContent = color.toUpperCase();
}

function applySettingsBlockColor(card, color) {
  const safeColor = colorToHex(color);
  card.style.setProperty("--client-card-settings-block-bg", safeColor);
  card.style.background = safeColor;
}

function toggleBlock(blockId) {
  if (state.expandedBlocks.has(blockId)) state.expandedBlocks.delete(blockId);
  else state.expandedBlocks.add(blockId);
  renderClientCardSettings();
}

function findFieldById(fieldId) {
  for (const block of state.clientCard.blocks || []) {
    for (const field of block.fields || []) {
      if (field.id === fieldId) return field;
    }
  }
  return null;
}

function allClientCardFields() {
  return (state.clientCard.blocks || []).flatMap((block) => block.fields || []);
}

function fieldTypeLabel(type) {
  const key = String(type || "text").toLowerCase();
  return fieldTypeLabels[key] || key;
}

function findRuleById(ruleId) {
  return (state.clientCard.rules || []).find((rule) => rule.id === ruleId) || null;
}

function deepCloneRuleActions(actions) {
  return JSON.parse(JSON.stringify(actions || []));
}

function escapeRegExp(value) {
  return String(value || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function nextRuleCopyName(sourceName, rules = []) {
  const baseName = String(sourceName || "").trim();
  const copySuffix = " \u2014 \u041a\u043e\u043f\u0438\u044f";
  const pattern = new RegExp(`^${escapeRegExp(baseName + copySuffix)}(?: (\\d+))?$`);
  let maxIndex = 0;
  for (const rule of rules) {
    const match = pattern.exec(String(rule?.name || "").trim());
    if (!match) continue;
    const index = match[1] ? Number(match[1]) : 1;
    if (Number.isFinite(index) && index > maxIndex) maxIndex = index;
  }
  if (maxIndex <= 0) return `${baseName}${copySuffix}`;
  return `${baseName}${copySuffix} ${maxIndex + 1}`;
}

function buildRuleDuplicatePayload(rule, rules = []) {
  return {
    name: nextRuleCopyName(rule?.name || "", rules),
    is_active: false,
    sort_order: Number(rule?.sort_order || 0) + 1,
    condition_field_key: rule?.condition_operator === "always" ? null : (rule?.condition_field_key || null),
    condition_operator: rule?.condition_operator || "always",
    condition_value: rule?.condition_value ?? null,
    actions: deepCloneRuleActions(rule?.actions || []),
  };
}

function isCustomBlock(block) {
  return block && !systemBlockKeys.has(block.key) && String(block.key || "").startsWith("custom_block_");
}

function findBlockByKey(blockKey) {
  return (state.clientCard.blocks || []).find((block) => block.key === blockKey) || null;
}

function setClientCardAndRender(config) {
  if (config) state.clientCard = config;
  renderClientCardSettings();
  renderClientCardRules();
  renderClientCardSources();
}

function fieldOptionsHtml(selectedKey = "") {
  return allClientCardFields()
    .map((field) => `<option value="${escapeHtml(field.key)}" ${field.key === selectedKey ? "selected" : ""}>${escapeHtml(field.label || field.key)}</option>`)
    .join("");
}

function saveFieldOptionsHtml(selectedKey = "") {
  return allClientCardFields()
    .filter((field) => !field.is_system || saveContextSystemFieldKeys.has(field.key))
    .map((field) => `<option value="${escapeHtml(field.key)}" ${field.key === selectedKey ? "selected" : ""}>${escapeHtml(field.label || field.key)}</option>`)
    .join("");
}

function blockOptionsHtml(selectedKey = "") {
  return (state.clientCard.blocks || [])
    .map((block) => `<option value="${escapeHtml(block.key)}" ${block.key === selectedKey ? "selected" : ""}>${escapeHtml(block.title || block.key)}</option>`)
    .join("");
}

function sourceOptionsHtml(selectedId = "") {
  return (state.clientCard.sources || [])
    .map((source) => `<option value="${escapeHtml(source.id)}" ${source.id === selectedId ? "selected" : ""}>${escapeHtml(source.name || source.technical_key || source.id)}</option>`)
    .join("");
}

function isInteractiveElement(target) {
  return Boolean(target?.closest?.("button, input, select, textarea, a, label, [data-no-card-open]"));
}

function interactiveBoundary(target) {
  return target?.closest?.("button, input, select, textarea, a, label, [role='button'], [data-no-card-open]") || null;
}

function bindKeyboardOpen(row, handler) {
  row.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const boundary = interactiveBoundary(event.target);
    if (boundary && boundary !== row) return;
    event.preventDefault();
    event.stopPropagation();
    handler();
  });
}

function findSourceById(sourceId) {
  return (state.clientCard.sources || []).find((source) => source.id === sourceId) || null;
}

function compactUrl(value) {
  try {
    const url = new URL(value);
    return `${url.hostname}${url.pathname === "/" ? "" : url.pathname}`;
  } catch {
    return String(value || "");
  }
}

function insertAtCursor(input, text) {
  if (!input) return;
  const start = input.selectionStart ?? input.value.length;
  const end = input.selectionEnd ?? start;
  input.value = `${input.value.slice(0, start)}${text}${input.value.slice(end)}`;
  input.focus();
  input.setSelectionRange(start + text.length, start + text.length);
}

function templateFieldOptionsHtml(selectedKey = "") {
  return allClientCardFields()
    .filter((field) => !field.is_system || saveContextSystemFieldKeys.has(field.key))
    .map((field) => `<option value="${escapeHtml(field.key)}" ${field.key === selectedKey ? "selected" : ""}>${escapeHtml(field.label || field.key)}</option>`)
    .join("");
}

function formatSourceTemplateRef(fieldKey) {
  const key = String(fieldKey || "").trim();
  return /^[a-zA-Z0-9_]+$/.test(key) ? `{{${key}}}` : "";
}

function ruleConditionFieldType(field) {
  return String(field?.field_type || field?.type || "text").toLowerCase();
}

function normalizedFieldOptions(field) {
  let rawOptions = field?.options;
  if (typeof rawOptions === "string") {
    try {
      rawOptions = JSON.parse(rawOptions);
    } catch {
      rawOptions = [];
    }
  } else if (!Array.isArray(rawOptions)) {
    rawOptions = field?.options_json;
  }
  if (typeof rawOptions === "string") {
    try {
      rawOptions = JSON.parse(rawOptions);
    } catch {
      rawOptions = [];
    }
  }
  if (!Array.isArray(rawOptions)) rawOptions = [];
  return rawOptions
    .map((option) => {
      if (option && typeof option === "object" && !Array.isArray(option)) {
        const value = String(option.value ?? option.key ?? "").trim();
        const label = String(option.label ?? option.title ?? option.name ?? value).trim();
        return value ? { value, label: label || value } : null;
      }
      const value = String(option ?? "").trim();
      return value ? { value, label: value } : null;
    })
    .filter(Boolean);
}

function ruleConditionValuePayload(control, fieldType) {
  if (!control) return null;
  const rawValue = String(control.value ?? "").trim();
  if (!rawValue) return null;
  return String(fieldType || "").toLowerCase() === "number" ? String(Number(rawValue)) : rawValue;
}

function ruleTemplateFieldRefs(template) {
  const refs = [];
  const seen = new Set();
  const re = /\{\{([a-zA-Z0-9_]+)\}\}/g;
  let match;
  while ((match = re.exec(String(template || ""))) !== null) {
    if (seen.has(match[1])) continue;
    seen.add(match[1]);
    refs.push(match[1]);
  }
  return refs;
}

function compactRenderedTemplate(value) {
  return String(value == null ? "" : value).replace(/\s+/g, " ").trim();
}

function renderRuleTemplateValue(template, context = {}) {
  const rendered = String(template || "").replace(/\{\{([a-zA-Z0-9_]+)\}\}/g, (_match, key) => {
    const value = context[key];
    if (value == null) return "";
    if (value === true) return "true";
    if (value === false) return "false";
    return String(value).trim();
  });
  return compactRenderedTemplate(rendered);
}

const complexTemplateValuePlaceholder = "\u005b\u0421\u043b\u043e\u0436\u043d\u0430\u044f\u0020\u0441\u0442\u0440\u0443\u043a\u0442\u0443\u0440\u0430\u003a\u0020\u0442\u043e\u043b\u044c\u043a\u043e\u0020\u0441\u043a\u0430\u043b\u044f\u0440\u043d\u043e\u0435\u0020\u0437\u043d\u0430\u0447\u0435\u043d\u0438\u0435\u0020\u0438\u043b\u0438\u0020\u043e\u0447\u0438\u0441\u0442\u043a\u0430\u005d";

function isScalarTemplateValue(value) {
  return value == null || typeof value === "string" || typeof value === "number" || typeof value === "boolean";
}

function analyzeTemplateObject(value) {
  const rows = [];
  const complexKeys = [];
  for (const [key, item] of Object.entries(value || {})) {
    if (isScalarTemplateValue(item)) {
      rows.push({ key, value: item == null ? "" : String(item), isComplex: false });
      continue;
    }
    complexKeys.push(key);
    rows.push({ key, value: complexTemplateValuePlaceholder, isComplex: true });
  }
  if (!rows.length) rows.push({ key: "", value: "", isComplex: false });
  return { rows, complexKeys };
}

function serializeTemplateRows(container, rowSelector) {
  const result = {};
  const unresolvedKeys = [];
  container.querySelectorAll(rowSelector).forEach((row) => {
    const key = row.querySelector("[data-source-row-key]").value.trim();
    const valueInput = row.querySelector("[data-source-row-value]");
    const value = valueInput.value;
    const isComplex = valueInput.dataset.sourceComplexValue === "1";
    if (!key) return;
    if (isComplex && value === complexTemplateValuePlaceholder) {
      unresolvedKeys.push(key);
      return;
    }
    result[key] = value;
  });
  return { value: Object.keys(result).length ? result : null, unresolvedKeys };
}

function renderTemplateComplexNotice(noticeEl, kind, complexKeys) {
  if (!noticeEl) return;
  if (!complexKeys.length) {
    noticeEl.textContent = "";
    noticeEl.classList.add("hidden");
    return;
  }
  noticeEl.textContent = `${kind}: \u043d\u0430\u0439\u0434\u0435\u043d\u044b \u0441\u043b\u043e\u0436\u043d\u044b\u0435 \u0437\u043d\u0430\u0447\u0435\u043d\u0438\u044f (${complexKeys.join(", ")}). \u0421\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u0438\u0435 \u0437\u0430\u0431\u043b\u043e\u043a\u0438\u0440\u043e\u0432\u0430\u043d\u043e, \u043f\u043e\u043a\u0430 \u0432\u044b \u043d\u0435 \u0437\u0430\u043c\u0435\u043d\u0438\u0442\u0435 \u0438\u0445 \u043d\u0430 \u0441\u043a\u0430\u043b\u044f\u0440\u043d\u044b\u0435 \u0437\u043d\u0430\u0447\u0435\u043d\u0438\u044f \u0438\u043b\u0438 \u043d\u0435 \u043e\u0447\u0438\u0441\u0442\u0438\u0442\u0435 key.`;
  noticeEl.classList.remove("hidden");
}

function keyValueRowHtml(kind, key = "", value = "", options = {}) {
  const complexAttr = options.isComplex ? ' data-source-complex-value="1"' : "";
  return `
    <div class="client-card-source-row" data-source-${kind}-row>
      <input data-source-row-key value="${escapeHtml(key)}" placeholder="key" autocomplete="off" />
      <input data-source-row-value${complexAttr} value="${escapeHtml(value)}" placeholder="value" autocomplete="off" />
      <select data-source-insert-field>${templateFieldOptionsHtml()}</select>
      <button class="action" type="button" data-source-insert-field-btn>\u0412\u0441\u0442\u0430\u0432\u0438\u0442\u044c \u043f\u043e\u043b\u0435</button>
      <button class="action danger" type="button" data-source-remove-row>&times;</button>
    </div>
  `;
}

function mappingRowHtml(responsePath = "", targetKey = "") {
  return `
    <div class="client-card-source-row" data-source-mapping-row>
      <input data-source-response-path value="${escapeHtml(responsePath)}" placeholder="data.user.name" autocomplete="off" />
      <span>&rarr;</span>
      <select data-source-target-key>${saveFieldOptionsHtml(targetKey)}</select>
      <button class="action danger" type="button" data-source-remove-row>&times;</button>
    </div>
  `;
}

function renderKeyValueRows(container, kind, value, noticeEl) {
  const analyzed = analyzeTemplateObject(value);
  container.innerHTML = analyzed.rows.map((row) => keyValueRowHtml(kind, row.key, row.value, row)).join("");
  renderTemplateComplexNotice(noticeEl, kind === "query" ? "Query" : "Headers", analyzed.complexKeys);
}

function renderMappingRows(mapping = []) {
  const rows = mapping.length ? mapping : [{ response_path: "", target_key: "" }];
  el.sourceMappingRows.innerHTML = rows.map((item) => mappingRowHtml(item.response_path || "", item.target_key || "")).join("");
}

function collectMappingRows() {
  const mapping = [];
  el.sourceMappingRows.querySelectorAll("[data-source-mapping-row]").forEach((row) => {
    const responsePath = row.querySelector("[data-source-response-path]").value.trim();
    const targetKey = row.querySelector("[data-source-target-key]").value;
    if (responsePath && targetKey) mapping.push({ response_path: responsePath, target_key: targetKey });
  });
  return mapping;
}

function parseBodyJson() {
  const raw = el.sourceBodyJson.value.trim();
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    throw new Error("\u0422\u0435\u043b\u043e \u0437\u0430\u043f\u0440\u043e\u0441\u0430 \u0434\u043e\u043b\u0436\u043d\u043e \u0431\u044b\u0442\u044c \u0432\u0430\u043b\u0438\u0434\u043d\u044b\u043c JSON");
  }
}

function sourcePayloadFromModal() {
  const name = el.sourceName.value.trim();
  if (!name) throw new Error("\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u0438\u0441\u0442\u043e\u0447\u043d\u0438\u043a\u0430 \u0434\u0430\u043d\u043d\u044b\u0445 \u043e\u0431\u044f\u0437\u0430\u0442\u0435\u043b\u044c\u043d\u043e");
  const queryTemplate = serializeTemplateRows(el.sourceQueryRows, "[data-source-query-row]");
  const headerTemplate = serializeTemplateRows(el.sourceHeaderRows, "[data-source-header-row]");
  if (queryTemplate.unresolvedKeys.length || headerTemplate.unresolvedKeys.length) {
    throw new Error("\u0421\u043d\u0430\u0447\u0430\u043b\u0430 \u0443\u0431\u0435\u0440\u0438\u0442\u0435 \u0438\u043b\u0438 \u0437\u0430\u043c\u0435\u043d\u0438\u0442\u0435 \u043d\u0435\u043f\u043e\u0434\u0434\u0435\u0440\u0436\u0438\u0432\u0430\u0435\u043c\u044b\u0435 \u0441\u043b\u043e\u0436\u043d\u044b\u0435 query/header \u0437\u043d\u0430\u0447\u0435\u043d\u0438\u044f");
  }
  return {
    name,
    technical_key: el.sourceTechnicalKey.value.trim() || null,
    kind: "http",
    is_active: Boolean(el.sourceActive.checked),
    method: el.sourceMethod.value,
    url_template: el.sourceUrl.value.trim(),
    timeout_ms: Number(el.sourceTimeoutMs.value || 3000),
    on_error: el.sourceOnError.value,
    query_template_json: queryTemplate.value,
    headers_template_json: headerTemplate.value,
    body_template_json: parseBodyJson(),
    mapping_json: collectMappingRows(),
  };
}

function sourceTemplateRefs(source) {
  const refs = new Set();
  const scan = (value) => {
    if (typeof value === "string") {
      const re = /\{\{([a-zA-Z0-9_]+)\}\}/g;
      let match;
      while ((match = re.exec(value)) !== null) refs.add(match[1]);
      return;
    }
    if (Array.isArray(value)) value.forEach(scan);
    else if (value && typeof value === "object") Object.values(value).forEach(scan);
  };
  scan(source.url_template || "");
  scan(source.query_template_json || {});
  scan(source.headers_template_json || {});
  scan(source.body_template_json);
  return [...refs];
}

function renderSourceTestContext(source) {
  const refs = sourceTemplateRefs(source);
  if (!refs.length) {
    el.sourceTestContext.innerHTML = `<div class="status">\u0412 templates \u043d\u0435\u0442 \u043f\u043e\u043b\u0435\u0439 \u043a\u0430\u0440\u0442\u043e\u0447\u043a\u0438</div>`;
    return;
  }
  const fields = new Map(allClientCardFields().map((field) => [field.key, field]));
  el.sourceTestContext.innerHTML = refs.map((key) => {
    const field = fields.get(key);
    return `<label>${escapeHtml(field?.label || key)}<input data-source-test-field="${escapeHtml(key)}" autocomplete="off" /></label>`;
  }).join("");
}

function collectSourceTestContext() {
  const context = {};
  el.sourceTestContext.querySelectorAll("[data-source-test-field]").forEach((input) => {
    context[input.dataset.sourceTestField] = input.value;
  });
  return context;
}

function renderSourceTestResult(result) {
  const mapped = Object.entries(result?.mapped_values || {})
    .map(([key, value]) => {
      const field = allClientCardFields().find((item) => item.key === key);
      return `${escapeHtml(field?.label || key)} &rarr; ${escapeHtml(value == null ? "null" : String(value))}`;
    })
    .join("<br />");
  const missing = (result?.missing_response_paths || []).map(escapeHtml).join(", ");
  el.sourceTestResult.innerHTML = `
    <div>HTTP: ${escapeHtml(result?.http_status ?? "-")}</div>
    <div>${escapeHtml(result?.duration_ms ?? 0)} ms</div>
    <div>${mapped || "\u041c\u0430\u043f\u043f\u0438\u043d\u0433 \u043f\u0443\u0441\u0442"}</div>
    <div>${missing ? `Missing: ${missing}` : ""}</div>
  `;
}

function openSourceModal(sourceId = null) {
  const source = sourceId ? findSourceById(sourceId) : null;
  state.editingSourceId = source?.id || null;
  el.clientCardSourceModalTitle.textContent = source ? "\u0420\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435 \u0438\u0441\u0442\u043e\u0447\u043d\u0438\u043a\u0430 \u0434\u0430\u043d\u043d\u044b\u0445" : "\u041d\u043e\u0432\u044b\u0439 \u0438\u0441\u0442\u043e\u0447\u043d\u0438\u043a \u0434\u0430\u043d\u043d\u044b\u0445";
  el.sourceName.value = source?.name || "";
  el.sourceTechnicalKey.value = source?.technical_key || "";
  el.sourceActive.checked = source?.is_active !== false;
  el.sourceMethod.value = source?.method || "GET";
  el.sourceUrl.value = source?.url_template || "";
  el.sourceTimeoutMs.value = source?.timeout_ms || 3000;
  el.sourceOnError.value = source?.on_error || "ignore";
  renderKeyValueRows(el.sourceQueryRows, "query", source?.query_template_json || {}, el.sourceQueryComplexNotice);
  renderKeyValueRows(el.sourceHeaderRows, "header", source?.headers_template_json || {}, el.sourceHeaderComplexNotice);
  el.sourceBodyJson.value = source?.body_template_json == null ? "" : JSON.stringify(source.body_template_json, null, 2);
  renderMappingRows(source?.mapping_json || []);
  renderSourceTestContext(source || sourcePayloadFromModalSafe());
  el.sourceTestResult.innerHTML = "";
  el.deleteClientCardSourceBtn.classList.toggle("hidden", !source);
  state.sourceEditorDirty = false;
  syncSourceTestAvailability();
  el.sourceInsertUrlField.innerHTML = templateFieldOptionsHtml();
  el.sourceInsertBodyField.innerHTML = templateFieldOptionsHtml();
  el.clientCardSourceModal.classList.remove("hidden");
  el.sourceName.focus();
}

function sourcePayloadFromModalSafe() {
  try { return sourcePayloadFromModal(); } catch { return {}; }
}

function closeSourceModal() {
  state.editingSourceId = null;
  state.sourceEditorDirty = false;
  el.clientCardSourceModal.classList.add("hidden");
}

function canTestSourceEditor({ editingSourceId, sourceEditorDirty }) {
  return Boolean(editingSourceId) && sourceEditorDirty !== true;
}

function syncSourceTestAvailability() {
  if (!el.testClientCardSourceBtn) return;
  el.testClientCardSourceBtn.disabled = !canTestSourceEditor(state);
}

function markSourceEditorDirty() {
  state.sourceEditorDirty = true;
  syncSourceTestAvailability();
}

async function saveSourceFromModal() {
  try {
    const payload = sourcePayloadFromModal();
    const path = state.editingSourceId ? `/api/v1/settings/client-card/sources/${state.editingSourceId}` : "/api/v1/settings/client-card/sources";
    await api(path, {
      method: state.editingSourceId ? "PATCH" : "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
    state.sourceEditorDirty = false;
    closeSourceModal();
    await loadClientCardConfig();
    setStatus(uiText.sourceSaved);
  } catch (e) {
    showError(e.message);
  }
}

async function testSourceFromModal() {
  if (!canTestSourceEditor(state)) {
    el.sourceTestResult.textContent = "\u0421\u043d\u0430\u0447\u0430\u043b\u0430 \u0441\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u0435 \u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u044f";
    return;
  }
  try {
    el.sourceTestResult.textContent = "\u041f\u0440\u043e\u0432\u0435\u0440\u043a\u0430...";
    const result = await api(`/api/v1/settings/client-card/sources/${state.editingSourceId}/test`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ context: collectSourceTestContext() }),
    });
    renderSourceTestResult(result);
  } catch (e) {
    el.sourceTestResult.textContent = e.message;
  }
}

async function toggleSource(sourceId, isActive) {
  try {
    await api(`/api/v1/settings/client-card/sources/${sourceId}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ is_active: isActive }),
    });
    await loadClientCardConfig();
    setStatus(uiText.sourceSaved);
  } catch (e) {
    showError(e.message);
  }
}

async function deleteSource(sourceId) {
  const source = findSourceById(sourceId);
  if (!source) return;
  if (!window.confirm(`\u0423\u0434\u0430\u043b\u0438\u0442\u044c \u0438\u0441\u0442\u043e\u0447\u043d\u0438\u043a \u0434\u0430\u043d\u043d\u044b\u0445 \u00ab${source.name || source.technical_key || source.id}\u00bb?`)) return;
  try {
    const sources = await api(`/api/v1/settings/client-card/sources/${sourceId}`, { method: "DELETE" });
    state.clientCard.sources = sources;
    closeSourceModal();
    renderClientCardSources();
    renderClientCardRules();
    setStatus(uiText.sourceDeleted);
  } catch (e) {
    if (e.status === 409 && Array.isArray(e.details?.rules)) {
      showError(`${e.message}\n\n${e.details.rules.map((rule) => rule.name || rule.technical_key || rule.id).join("\n")}`);
      return;
    }
    showError(e.message);
  }
}

function syncRuleValueVisibility() {
  const needsValue = el.ruleOperator.value === "equals" || el.ruleOperator.value === "not_equals";
  el.ruleConditionField.closest("label").classList.toggle("hidden", el.ruleOperator.value === "always");
  el.ruleValueWrap.classList.toggle("hidden", !needsValue);
  if (needsValue) syncRuleConditionValueControl();
}

function currentRuleValueControl() {
  return el.ruleValueWrap.querySelector("[data-rule-condition-value]") || el.ruleValue;
}

function ruleConditionField() {
  return allClientCardFields().find((field) => field.key === el.ruleConditionField.value) || null;
}

function syncRuleConditionValueControl(value = null) {
  const currentValue = value ?? currentRuleValueControl()?.value ?? "";
  const field = ruleConditionField();
  const type = ruleConditionFieldType(field);
  const options = normalizedFieldOptions(field);
  let controlHtml = "";
  if (type === "select" && options.length) {
    const hasCurrentOption = !String(currentValue).trim() || options.some((option) => option.value === String(currentValue));
    controlHtml = `<select id="ruleValue" data-rule-condition-value>
      <option value="">\u041d\u0435 \u0432\u044b\u0431\u0440\u0430\u043d\u043e</option>
      ${!hasCurrentOption ? `<option value="${escapeHtml(currentValue)}" selected>\u0422\u0435\u043a\u0443\u0449\u0435\u0435 \u0437\u043d\u0430\u0447\u0435\u043d\u0438\u0435: ${escapeHtml(currentValue)} (\u0432 \u0441\u043f\u0438\u0441\u043a\u0435 \u0431\u043e\u043b\u044c\u0448\u0435 \u043d\u0435\u0442)</option>` : ""}
      ${options.map((option) => `<option value="${escapeHtml(option.value)}" ${option.value === String(currentValue) ? "selected" : ""}>${escapeHtml(option.label)}</option>`).join("")}
    </select>`;
  } else if (type === "checkbox") {
    controlHtml = `<select id="ruleValue" data-rule-condition-value>
      <option value="">\u041d\u0435 \u0432\u044b\u0431\u0440\u0430\u043d\u043e</option>
      <option value="true" ${String(currentValue) === "true" ? "selected" : ""}>\u0414\u0430</option>
      <option value="false" ${String(currentValue) === "false" ? "selected" : ""}>\u041d\u0435\u0442</option>
    </select>`;
  } else {
    const inputType = type === "number" ? "number" : "text";
    controlHtml = `<input id="ruleValue" data-rule-condition-value type="${inputType}" value="${escapeHtml(currentValue)}" autocomplete="off" />`;
  }
  el.ruleValueWrap.innerHTML = `\u0417\u043d\u0430\u0447\u0435\u043d\u0438\u0435${controlHtml}`;
  el.ruleValue = document.getElementById("ruleValue");
}

function computeRuleActionUiState({ targetType = "field", scope = "editor", action = "show", valueType = "literal", previousNonSaveScope = "editor" }) {
  let nextTargetType = targetType;
  let nextScope = scope;
  let nextPreviousNonSaveScope = previousNonSaveScope;
  if (scope !== "save") nextPreviousNonSaveScope = scope;
  if (action === "lookup_source" || action === "set_value") {
    if (scope !== "save") nextPreviousNonSaveScope = scope;
    nextScope = "save";
    nextTargetType = "field";
  } else if (scope === "save") {
    nextScope = previousNonSaveScope || "editor";
  }
  return {
    targetType: nextTargetType,
    scope: nextScope,
    previousNonSaveScope: nextPreviousNonSaveScope,
    isLookupSource: action === "lookup_source",
    showValueType: action === "set_value",
    showSourceField: action === "set_value" && ["field", "map"].includes(valueType),
    showLookupSource: action === "lookup_source",
    showValue: action === "set_value" && ["literal", "template"].includes(valueType),
    showTemplateTools: action === "set_value" && valueType === "template",
    showMap: action === "set_value" && valueType === "map",
  };
}

function ruleActionOptions(targetType, scope, selected = "show") {
  const allowed = targetType === "block"
    ? ["show", "hide"]
    : ["show", "hide", "readonly", "editable", "required", "optional", "set_value", "lookup_source"];
  const safeSelected = allowed.includes(selected) ? selected : allowed[0];
  return allowed
    .map((value) => `<option value="${value}" ${value === safeSelected ? "selected" : ""}>${ruleActionLabels[value] || value}</option>`)
    .join("");
}

function ruleActionRowHtml(action = {}, index = 0) {
  const targetType = action.target_type || "field";
  const scope = action.scope || "editor";
  const targetKey = action.target_key || "";
  const valueType = action.value_type || "literal";
  const sourceId = action.source_id || "";
  const mapText = Object.entries(action.map || {})
    .map(([key, value]) => `${key} => ${value ?? ""}`)
    .join("\n");
  return `
    <div class="client-card-rule-action-row" data-rule-action-row>
      <label>\u0427\u0442\u043e \u0438\u0437\u043c\u0435\u043d\u0438\u0442\u044c
        <select data-rule-action-prop="target_type">
          <option value="field" ${targetType === "field" ? "selected" : ""}>\u041f\u043e\u043b\u0435</option>
          <option value="block" ${targetType === "block" ? "selected" : ""}>\u0411\u043b\u043e\u043a</option>
        </select>
      </label>
      <label data-rule-field-target-wrap>\u041a\u0430\u043a\u043e\u0439 \u0431\u043b\u043e\u043a \u0438\u043b\u0438 \u043f\u043e\u043b\u0435<select data-rule-action-prop="target_field">${fieldOptionsHtml(targetKey)}</select></label>
      <label data-rule-block-target-wrap>\u041a\u0430\u043a\u043e\u0439 \u0431\u043b\u043e\u043a \u0438\u043b\u0438 \u043f\u043e\u043b\u0435<select data-rule-action-prop="target_block">${blockOptionsHtml(targetKey)}</select></label>
      <label>\u0427\u0442\u043e \u0441\u0434\u0435\u043b\u0430\u0442\u044c<select data-rule-action-prop="action">${ruleActionOptions(targetType, scope, action.action || "show")}</select></label>
      <label>\u0413\u0434\u0435 \u043f\u0440\u0438\u043c\u0435\u043d\u044f\u0442\u044c
        <select data-rule-action-prop="scope">
          <option value="editor" ${scope === "editor" ? "selected" : ""}>\u041e\u043a\u043d\u043e \u0440\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u044f</option>
          <option value="card" ${scope === "card" ? "selected" : ""}>\u041a\u0430\u0440\u0442\u043e\u0447\u043a\u0430</option>
          <option value="both" ${scope === "both" ? "selected" : ""}>\u041e\u0431\u0430</option>
          <option value="save" ${scope === "save" ? "selected" : ""}>\u0421\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u0438\u0435</option>
        </select>
      </label>
      <label data-rule-value-type-wrap>\u0422\u0438\u043f \u0437\u043d\u0430\u0447\u0435\u043d\u0438\u044f
        <select data-rule-action-prop="value_type">
          <option value="literal" ${valueType === "literal" ? "selected" : ""}>${ruleValueTypeLabels.literal}</option>
          <option value="field" ${valueType === "field" ? "selected" : ""}>${ruleValueTypeLabels.field}</option>
          <option value="template" ${valueType === "template" ? "selected" : ""}>${ruleValueTypeLabels.template}</option>
          <option value="map" ${valueType === "map" ? "selected" : ""}>${ruleValueTypeLabels.map}</option>
          <option value="null" ${valueType === "null" ? "selected" : ""}>${ruleValueTypeLabels.null}</option>
        </select>
      </label>
      <label data-rule-source-wrap>\u0418\u0441\u0442\u043e\u0447\u043d\u0438\u043a<select data-rule-action-prop="source_key">${fieldOptionsHtml(action.source_key || "")}</select></label>
      <label data-rule-lookup-source-wrap>\u0418\u0441\u0442\u043e\u0447\u043d\u0438\u043a \u0434\u0430\u043d\u043d\u044b\u0445<select data-rule-action-prop="source_id">${sourceOptionsHtml(sourceId)}</select></label>
      <div data-rule-value-wrap class="client-card-rule-value-wrap">
        <label><span data-rule-value-label>\u0417\u043d\u0430\u0447\u0435\u043d\u0438\u0435</span><input data-rule-action-prop="value" value="${escapeHtml(action.value || "")}" placeholder="\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u043f\u043e\u043b\u044f \u043a\u043d\u043e\u043f\u043a\u043e\u0439 \u043d\u0438\u0436\u0435" autocomplete="off" /></label>
        <div class="client-card-rule-template-tools" data-rule-template-tools>
          <div class="client-card-template-insert-row">
            <select data-rule-template-insert-field>${templateFieldOptionsHtml()}</select>
            <button class="action" type="button" data-rule-template-insert-field-btn>\u0412\u0441\u0442\u0430\u0432\u0438\u0442\u044c \u043f\u043e\u043b\u0435</button>
          </div>
          <div class="status">\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u043f\u043e\u043b\u0435 \u0438\u0437 \u0441\u043f\u0438\u0441\u043a\u0430: \u043e\u043d\u043e \u0432\u0441\u0442\u0430\u0432\u0438\u0442\u0441\u044f \u0432 \u0448\u0430\u0431\u043b\u043e\u043d. \u041c\u043e\u0436\u043d\u043e \u0434\u043e\u0431\u0430\u0432\u043b\u044f\u0442\u044c \u043f\u0440\u043e\u0431\u0435\u043b\u044b \u0438 \u043e\u0431\u044b\u0447\u043d\u044b\u0439 \u0442\u0435\u043a\u0441\u0442. \u041f\u0440\u0438\u043c\u0435\u0440: [\u0424\u0430\u043c\u0438\u043b\u0438\u044f] [\u0418\u043c\u044f].</div>
          <div class="client-card-rule-template-preview-context" data-rule-template-preview-context></div>
          <div class="client-card-rule-template-preview" data-rule-template-preview>\u041f\u0440\u0435\u0434\u043f\u0440\u043e\u0441\u043c\u043e\u0442\u0440: \u043f\u0443\u0441\u0442\u043e</div>
        </div>
      </div>
      <label data-rule-map-wrap>Map: \u043e\u0434\u043d\u0430 \u0441\u0442\u0440\u043e\u043a\u0430 = source =&gt; result<textarea data-rule-action-prop="map" rows="4">${escapeHtml(mapText)}</textarea></label>
      <button class="action danger ${index === 0 ? "hidden" : ""}" type="button" data-remove-rule-action>&times;</button>
    </div>
  `;
}

function syncRuleTemplatePreview(row) {
  const tools = row.querySelector("[data-rule-template-tools]");
  const valueInput = row.querySelector('[data-rule-action-prop="value"]');
  const contextWrap = row.querySelector("[data-rule-template-preview-context]");
  const previewEl = row.querySelector("[data-rule-template-preview]");
  if (!tools || !valueInput || !contextWrap || !previewEl) return;

  const existingValues = {};
  contextWrap.querySelectorAll("[data-rule-template-preview-field]").forEach((input) => {
    existingValues[input.dataset.ruleTemplatePreviewField] = input.value;
  });
  const fieldsByKey = new Map(allClientCardFields().map((field) => [field.key, field]));
  const refs = ruleTemplateFieldRefs(valueInput.value);
  const refsSignature = refs.join("\u0001");
  if (contextWrap.dataset.ruleTemplateRefs !== refsSignature) {
    contextWrap.dataset.ruleTemplateRefs = refsSignature;
    contextWrap.innerHTML = refs.length
      ? refs.map((key) => {
          const field = fieldsByKey.get(key);
          return `<label>${escapeHtml(field?.label || key)}<input data-rule-template-preview-field="${escapeHtml(key)}" value="${escapeHtml(existingValues[key] || "")}" placeholder="\u041f\u0440\u0438\u043c\u0435\u0440 \u0437\u043d\u0430\u0447\u0435\u043d\u0438\u044f" autocomplete="off" /></label>`;
        }).join("")
      : `<div class="status">\u0414\u043e\u0431\u0430\u0432\u044c\u0442\u0435 \u043f\u043e\u043b\u0435 \u0438\u0437 \u0441\u043f\u0438\u0441\u043a\u0430, \u0447\u0442\u043e\u0431\u044b \u0443\u0432\u0438\u0434\u0435\u0442\u044c preview \u0441 \u0434\u0430\u043d\u043d\u044b\u043c\u0438.</div>`;
  }
  const context = {};
  contextWrap.querySelectorAll("[data-rule-template-preview-field]").forEach((input) => {
    context[input.dataset.ruleTemplatePreviewField] = input.value;
  });
  const rendered = renderRuleTemplateValue(valueInput.value, context);
  previewEl.textContent = `\u041f\u0440\u0435\u0434\u043f\u0440\u043e\u0441\u043c\u043e\u0442\u0440: ${rendered || "\u043f\u0443\u0441\u0442\u043e"}`;
}

function syncRuleActionRow(row) {
  let targetType = row.querySelector('[data-rule-action-prop="target_type"]').value;
  const targetTypeSelect = row.querySelector('[data-rule-action-prop="target_type"]');
  const scopeSelect = row.querySelector('[data-rule-action-prop="scope"]');
  const actionSelect = row.querySelector('[data-rule-action-prop="action"]');
  const previousNonSaveScope = row.dataset.previousNonSaveScope || "editor";
  let scope = scopeSelect.value;
  const valueType = row.querySelector('[data-rule-action-prop="value_type"]')?.value || "literal";
  const actionValue = actionSelect.value;
  if (scope === "save" && targetType !== "field") {
    targetTypeSelect.value = "field";
    targetType = "field";
  }
  const targetFieldSelect = row.querySelector('[data-rule-action-prop="target_field"]');
  const sourceFieldSelect = row.querySelector('[data-rule-action-prop="source_key"]');
  const lookupSourceSelect = row.querySelector('[data-rule-action-prop="source_id"]');
  const templateFieldSelect = row.querySelector("[data-rule-template-insert-field]");
  const targetValue = targetFieldSelect.value;
  const sourceValue = sourceFieldSelect.value;
  const lookupSourceValue = lookupSourceSelect.value;
  const templateFieldValue = templateFieldSelect?.value || "";
  targetFieldSelect.innerHTML = scope === "save" ? saveFieldOptionsHtml(targetValue) : fieldOptionsHtml(targetValue);
  sourceFieldSelect.innerHTML = saveFieldOptionsHtml(sourceValue);
  lookupSourceSelect.innerHTML = sourceOptionsHtml(lookupSourceValue);
  if (templateFieldSelect) templateFieldSelect.innerHTML = templateFieldOptionsHtml(templateFieldValue);
  actionSelect.innerHTML = ruleActionOptions(targetType, scope, actionSelect.value);
  const uiState = computeRuleActionUiState({ targetType, scope, action: actionValue, valueType, previousNonSaveScope });
  row.dataset.previousNonSaveScope = uiState.previousNonSaveScope;
  targetTypeSelect.value = uiState.targetType;
  scopeSelect.value = uiState.scope;
  scopeSelect.disabled = uiState.isLookupSource;
  scopeSelect.closest("label").classList.toggle("hidden", uiState.isLookupSource);
  targetFieldSelect.innerHTML = uiState.scope === "save" ? saveFieldOptionsHtml(targetValue) : fieldOptionsHtml(targetValue);
  row.querySelector('[data-rule-action-prop="target_type"]').closest("label").classList.toggle("hidden", uiState.isLookupSource);
  row.querySelector("[data-rule-field-target-wrap]").classList.toggle("hidden", uiState.isLookupSource || uiState.targetType === "block");
  row.querySelector("[data-rule-block-target-wrap]").classList.toggle("hidden", uiState.isLookupSource || uiState.targetType !== "block");
  row.querySelector("[data-rule-value-type-wrap]").classList.toggle("hidden", !uiState.showValueType);
  row.querySelector("[data-rule-source-wrap]").classList.toggle("hidden", !uiState.showSourceField);
  row.querySelector("[data-rule-lookup-source-wrap]").classList.toggle("hidden", !uiState.showLookupSource);
  row.querySelector("[data-rule-value-wrap]").classList.toggle("hidden", !uiState.showValue);
  row.querySelector("[data-rule-template-tools]")?.classList.toggle("hidden", !uiState.showTemplateTools);
  row.querySelector("[data-rule-value-label]").textContent = uiState.showTemplateTools ? "\u0428\u0430\u0431\u043b\u043e\u043d" : "\u0417\u043d\u0430\u0447\u0435\u043d\u0438\u0435";
  row.querySelector("[data-rule-map-wrap]").classList.toggle("hidden", !uiState.showMap);
  syncRuleTemplatePreview(row);
}

function syncRuleActionRows() {
  el.ruleActions.querySelectorAll("[data-rule-action-row]").forEach((row, index) => {
    syncRuleActionRow(row);
    row.querySelector("[data-remove-rule-action]").classList.toggle("hidden", index === 0 && el.ruleActions.querySelectorAll("[data-rule-action-row]").length === 1);
  });
}

function renderRuleActions(actions = []) {
  const rows = actions.length ? actions : [{ target_type: "field", target_key: "", action: "show", scope: "editor" }];
  el.ruleActions.innerHTML = rows.map(ruleActionRowHtml).join("");
  syncRuleActionRows();
}

function collectRuleActions() {
  const actions = [];
  el.ruleActions.querySelectorAll("[data-rule-action-row]").forEach((row) => {
    const targetType = row.querySelector('[data-rule-action-prop="target_type"]').value;
    const actionName = row.querySelector('[data-rule-action-prop="action"]').value;
    const item = {
      target_type: targetType,
      target_key: targetType === "block"
        ? row.querySelector('[data-rule-action-prop="target_block"]').value
        : row.querySelector('[data-rule-action-prop="target_field"]').value,
      action: actionName,
      scope: row.querySelector('[data-rule-action-prop="scope"]').value,
    };
    if (actionName === "lookup_source") {
      item.scope = "save";
      item.source_id = row.querySelector('[data-rule-action-prop="source_id"]').value;
      delete item.target_type;
      delete item.target_key;
    }
    if (actionName === "set_value") {
      const valueType = row.querySelector('[data-rule-action-prop="value_type"]').value;
      item.value_type = valueType;
      if (["literal", "template"].includes(valueType)) {
        item.value = row.querySelector('[data-rule-action-prop="value"]').value;
      }
      if (["field", "map"].includes(valueType)) {
        item.source_key = row.querySelector('[data-rule-action-prop="source_key"]').value;
      }
      if (valueType === "map") {
        const mapping = {};
        const lines = row.querySelector('[data-rule-action-prop="map"]').value.split("\n");
        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed) continue;
          const separator = trimmed.includes("=>") ? "=>" : "=";
          const parts = trimmed.split(separator);
          const key = (parts.shift() || "").trim();
          const value = parts.join(separator).trim();
          if (key) mapping[key] = value || null;
        }
        item.map = mapping;
      }
    }
    actions.push(item);
  });
  if (!actions.length) throw new Error("\u0414\u043e\u0431\u0430\u0432\u044c\u0442\u0435 \u0445\u043e\u0442\u044f \u0431\u044b \u043e\u0434\u043d\u043e \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u0435");
  return actions;
}

function openRuleModal(ruleId = null) {
  const rule = ruleId ? findRuleById(ruleId) : null;
  state.editingRuleId = rule?.id || null;
  el.clientCardRuleModalTitle.textContent = rule ? "\u0420\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435 \u043f\u0440\u0430\u0432\u0438\u043b\u0430" : "\u041d\u043e\u0432\u043e\u0435 \u043f\u0440\u0430\u0432\u0438\u043b\u043e";
  el.ruleName.value = rule?.name || "";
  el.ruleActive.checked = rule?.is_active !== false;
  el.ruleConditionField.innerHTML = fieldOptionsHtml(rule?.condition_field_key || "");
  el.ruleOperator.value = rule?.condition_operator || "equals";
  renderRuleActions(rule?.actions || []);
  el.deleteClientCardRuleBtn.classList.toggle("hidden", !rule || rule.is_system);
  syncRuleValueVisibility();
  if (el.ruleOperator.value === "equals" || el.ruleOperator.value === "not_equals") {
    syncRuleConditionValueControl(rule?.condition_value || "");
  }
  el.clientCardRuleModal.classList.remove("hidden");
  el.ruleName.focus();
}

function closeRuleModal() {
  state.editingRuleId = null;
  el.clientCardRuleModal.classList.add("hidden");
}

function rulePayloadFromModal() {
  const name = el.ruleName.value.trim();
  if (!name) throw new Error("\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u043f\u0440\u0430\u0432\u0438\u043b\u0430 \u043e\u0431\u044f\u0437\u0430\u0442\u0435\u043b\u044c\u043d\u043e");
  const conditionField = ruleConditionField();
  const conditionControl = currentRuleValueControl();
  return {
    name,
    is_active: Boolean(el.ruleActive.checked),
    condition_field_key: el.ruleOperator.value === "always" ? null : el.ruleConditionField.value,
    condition_operator: el.ruleOperator.value,
    condition_value: (el.ruleOperator.value === "equals" || el.ruleOperator.value === "not_equals")
      ? ruleConditionValuePayload(conditionControl, conditionField?.field_type)
      : null,
    actions: collectRuleActions(),
  };
}

async function saveRuleFromModal() {
  try {
    const payload = rulePayloadFromModal();
    const path = state.editingRuleId ? `/api/v1/settings/client-card/rules/${state.editingRuleId}` : "/api/v1/settings/client-card/rules";
    await api(path, {
      method: state.editingRuleId ? "PATCH" : "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
    closeRuleModal();
    await loadClientCardConfig();
    setStatus(uiText.rulesSaved);
  } catch (e) {
    showError(e.message);
  }
}

async function toggleRule(ruleId, isActive) {
  try {
    await api(`/api/v1/settings/client-card/rules/${ruleId}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ is_active: isActive }),
    });
    await loadClientCardConfig();
    setStatus(uiText.rulesSaved);
  } catch (e) {
    showError(e.message);
  }
}

async function moveRule(ruleId, direction) {
  try {
    await api(`/api/v1/settings/client-card/rules/${ruleId}/move`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ direction }),
    });
    await loadClientCardConfig();
    setStatus(uiText.rulesSaved);
  } catch (e) {
    showError(e.message);
  }
}

async function deleteRule(ruleId) {
  const rule = findRuleById(ruleId);
  if (!rule || rule.is_system) return;
  if (!window.confirm(`${uiText.deleteRuleConfirmPrefix}${rule.name || rule.id}${uiText.deleteRuleConfirmSuffix}`)) return;
  try {
    const config = await api(`/api/v1/settings/client-card/rules/${ruleId}`, { method: "DELETE" });
    closeRuleModal();
    setClientCardAndRender(config);
    setStatus(uiText.ruleDeleted);
  } catch (e) {
    showError(e.message);
  }
}

async function duplicateRule(ruleId) {
  const sourceRule = findRuleById(ruleId);
  if (!sourceRule) return;
  try {
    const createdRule = await api("/api/v1/settings/client-card/rules", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(buildRuleDuplicatePayload(sourceRule, state.clientCard.rules || [])),
    });
    await loadClientCardConfig();
    let sourceIndex = (state.clientCard.rules || []).findIndex((rule) => rule.id === ruleId);
    let copyIndex = (state.clientCard.rules || []).findIndex((rule) => rule.id === createdRule.id);
    while (sourceIndex !== -1 && copyIndex > sourceIndex + 1) {
      const config = await api(`/api/v1/settings/client-card/rules/${createdRule.id}/move`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ direction: "up" }),
      });
      setClientCardAndRender(config);
      sourceIndex = (state.clientCard.rules || []).findIndex((rule) => rule.id === ruleId);
      copyIndex = (state.clientCard.rules || []).findIndex((rule) => rule.id === createdRule.id);
    }
    setStatus(uiText.ruleCopied);
  } catch (e) {
    showError(e.message);
  }
}

function openEditFieldModal(fieldId) {
  const field = findFieldById(fieldId);
  if (!field) return;
  state.editingFieldId = field.id;

  el.editFieldLabel.value = field.label || "";
  el.editFieldLabel.readOnly = false;
  el.editFieldVisible.checked = Boolean(field.is_visible);
  el.editFieldVisibleInEditor.checked = field.is_visible_in_editor !== false;
  el.editFieldEditable.checked = Boolean(field.is_editable);
  el.editFieldRequired.checked = Boolean(field.is_required);
  el.editFieldKey.value = field.key || "";
  el.editFieldKey.disabled = true;
  el.editFieldType.innerHTML = fieldTypes.map((type) => `<option value="${type}">${fieldTypeLabel(type)}</option>`).join("");
  el.editFieldType.value = field.field_type || "text";

  const isSystem = Boolean(field.is_system);
  el.editFieldSystemNote.textContent = isSystem ? uiText.systemField : "";
  el.editFieldSystemNote.classList.toggle("hidden", !isSystem);
  el.deleteEditClientCardFieldBtn.classList.toggle("hidden", isSystem);
  if (isSystem) {
    el.editFieldType.disabled = true;
    el.editFieldEditable.disabled = true;
    el.editFieldRequired.disabled = true;
    el.editFieldOptions.disabled = true;
  } else {
    el.editFieldType.disabled = false;
    el.editFieldEditable.disabled = false;
    el.editFieldRequired.disabled = false;
    el.editFieldOptions.disabled = false;
  }

  const options = Array.isArray(field.options) ? field.options : [];
  el.editFieldOptions.value = options.join("\n");
  el.editFieldOptionsWrap.classList.toggle("hidden", (field.field_type || "") !== "select");

  el.editFieldBlockKey.innerHTML = (state.clientCard.blocks || []).map((b) => `<option value="${escapeHtml(b.key)}">${escapeHtml(b.title)}</option>`).join("");
  el.editFieldBlockKey.value = field.block_key;
  el.editFieldBlockKey.disabled = true;

  el.editClientCardFieldModal.classList.remove("hidden");
  el.editFieldLabel.focus();
}

function closeEditFieldModal() {
  state.editingFieldId = null;
  el.editClientCardFieldModal.classList.add("hidden");
}

async function saveFieldFromModal() {
  if (!state.editingFieldId) return;
  const field = findFieldById(state.editingFieldId);
  const isSystem = Boolean(field?.is_system);
  const selectedType = el.editFieldType.value;
  const payload = {
    label: el.editFieldLabel.value.trim(),
    is_visible: Boolean(el.editFieldVisible.checked),
    is_visible_in_editor: Boolean(el.editFieldVisibleInEditor.checked),
  };

  if (!isSystem) {
    payload.is_editable = Boolean(el.editFieldEditable.checked);
    payload.is_required = Boolean(el.editFieldRequired.checked);
    payload.field_type = selectedType;
    if (selectedType === "select") {
      const options = el.editFieldOptions.value.split("\n").map((item) => item.trim()).filter(Boolean);
      if (!options.length) {
        showError("Для поля select нужно указать хотя бы один вариант");
        return;
      }
      payload.options = options;
    } else {
      payload.options = null;
    }
  }

  try {
    await api(`/api/v1/settings/client-card/fields/${state.editingFieldId}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
    closeEditFieldModal();
    await loadClientCardConfig();
    setStatus("Поле сохранено");
  } catch (e) {
    showError(e.message);
  }
}

function syncCreateFieldOptionsVisibility() {
  el.newClientCardFieldOptionsWrap.classList.toggle("hidden", el.newClientCardFieldType.value !== "select");
}

function syncEditFieldOptionsVisibility() {
  el.editFieldOptionsWrap.classList.toggle("hidden", el.editFieldType.value !== "select");
}

function blockPayloadFromDom(blockId) {
  const title = el.clientCardSettings.querySelector(`[data-block-id="${blockId}"][data-prop="title"]`).value.trim();
  const colorInput = el.clientCardSettings.querySelector(`[data-block-id="${blockId}"][data-prop="color_scheme"]`);
  const color = state.pendingBlockColors.get(blockId) || colorInput?.value || fallbackColorHex;
  const active = Boolean(el.clientCardSettings.querySelector(`[data-block-id="${blockId}"][data-prop="is_active"]`).checked);
  const showInCard = Boolean(el.clientCardSettings.querySelector(`[data-block-id="${blockId}"][data-prop="show_in_card"]`).checked);
  const showInEditor = Boolean(el.clientCardSettings.querySelector(`[data-block-id="${blockId}"][data-prop="show_in_editor"]`).checked);
  const showTitle = Boolean(el.clientCardSettings.querySelector(`[data-block-id="${blockId}"][data-prop="show_title"]`).checked);
  return { title, color_scheme: color, is_active: active, show_in_card: showInCard, show_in_editor: showInEditor, show_title: showTitle };
}

function clearPendingBlockSaveTimer(blockId) {
  const timer = state.pendingBlockSaveTimers.get(blockId);
  if (timer) window.clearTimeout(timer);
  state.pendingBlockSaveTimers.delete(blockId);
}

function blockSaveMeta(blockId) {
  if (!state.pendingBlockSaveMeta.has(blockId)) {
    state.pendingBlockSaveMeta.set(blockId, { version: 0, pendingVersion: 0, inFlight: false, statusText: uiText.blockSaved });
  }
  return state.pendingBlockSaveMeta.get(blockId);
}

function scheduleBlockAutosave(blockId, { delay = 0, statusText = uiText.blockSaved } = {}) {
  const meta = blockSaveMeta(blockId);
  meta.version += 1;
  meta.pendingVersion = meta.version;
  meta.statusText = statusText;
  clearPendingBlockSaveTimer(blockId);
  if (delay > 0) {
    state.pendingBlockSaveTimers.set(blockId, window.setTimeout(() => {
      state.pendingBlockSaveTimers.delete(blockId);
      flushBlockAutosave(blockId);
    }, delay));
    return;
  }
  flushBlockAutosave(blockId);
}

async function saveBlock(blockId, statusText = uiText.blockSaved) {
  scheduleBlockAutosave(blockId, { statusText });
}

async function flushBlockAutosave(blockId) {
  const meta = blockSaveMeta(blockId);
  if (meta.inFlight) return;
  const savingVersion = meta.pendingVersion;
  meta.inFlight = true;
  try {
    await api(`/api/v1/settings/client-card/blocks/${blockId}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(blockPayloadFromDom(blockId)),
    });
    if (meta.pendingVersion === savingVersion) {
      state.pendingBlockColors.delete(blockId);
      await loadClientCardConfig();
      setStatus(meta.statusText);
    }
  } catch (e) {
    showError(e.message);
  } finally {
    meta.inFlight = false;
    if (meta.pendingVersion !== savingVersion) {
      flushBlockAutosave(blockId);
    }
  }
}
async function moveBlock(blockId, direction) {
  try {
    const config = await api(`/api/v1/settings/client-card/blocks/${blockId}/move`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ direction }),
    });
    setClientCardAndRender(config);
    setStatus(uiText.blockMoved);
  } catch (e) {
    showError(e.message);
  }
}

async function moveField(fieldId, direction) {
  try {
    const config = await api(`/api/v1/settings/client-card/fields/${fieldId}/move`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ direction }),
    });
    setClientCardAndRender(config);
    setStatus(uiText.fieldMoved);
  } catch (e) {
    showError(e.message);
  }
}

async function deleteBlock(blockId) {
  const block = (state.clientCard.blocks || []).find((item) => item.id === blockId);
  if (!block || !isCustomBlock(block)) return;
  if (!window.confirm(`${uiText.deleteBlockConfirmPrefix}${block.title || block.key}${uiText.deleteBlockConfirmSuffix}`)) return;
  try {
    const config = await api(`/api/v1/settings/client-card/blocks/${blockId}`, { method: "DELETE" });
    state.expandedBlocks.delete(blockId);
    state.pendingBlockColors.delete(blockId);
    setClientCardAndRender(config);
    setStatus(uiText.blockDeleted);
  } catch (e) {
    showError(e.message);
  }
}

async function deleteField(fieldId) {
  const field = findFieldById(fieldId);
  if (!field || field.is_system) return;
  if (!window.confirm(`${uiText.deleteFieldConfirmPrefix}${field.label || field.key}${uiText.deleteFieldConfirmSuffix}`)) return;
  try {
    const config = await api(`/api/v1/settings/client-card/fields/${fieldId}`, { method: "DELETE" });
    if (state.editingFieldId === fieldId) closeEditFieldModal();
    setClientCardAndRender(config);
    setStatus(uiText.fieldDeleted);
  } catch (e) {
    showError(e.message);
  }
}

function renderClientCardSettings() {
  const blocks = state.clientCard.blocks || [];
  el.clientCardSettings.innerHTML = "";
  el.emptyClientCardText.classList.toggle("hidden", blocks.length > 0);

  for (const [blockIndex, block] of blocks.entries()) {
    const expanded = state.expandedBlocks.has(block.id);
    const customBlock = isCustomBlock(block);
    const blockColor = currentBlockColor(block);

    const card = document.createElement("div");
    card.className = `client-card-settings-block compact ${blockColorClass(block.color_scheme)}`;
    card.tabIndex = 0;
    card.setAttribute("role", "button");
    card.setAttribute("aria-label", `${expanded ? uiText.collapse : uiText.expand}: ${block.title || block.key}`);
    card.innerHTML = `
      <div class="client-card-accordion-head">
        <div class="client-card-accordion-main">
          <div class="client-card-accordion-title-row">
            <div class="client-card-accordion-title">${escapeHtml(block.title)}</div>
            <div class="client-card-accordion-key">${escapeHtml(block.key)}</div>
          </div>
          <div class="client-card-content-actions">
            ${customBlock ? `<button class="action danger" type="button" data-delete-block-id="${escapeHtml(block.id)}">${uiText.deleteBlock}</button>` : ""}
          </div>
          <div class="client-card-accordion-meta">
            <span>${block.is_active ? uiText.active : uiText.inactive}</span>
          </div>
        </div>
        <div class="client-card-reorder-actions">
          <button class="action" type="button" data-move-block-id="${escapeHtml(block.id)}" data-direction="up" ${blockIndex === 0 ? "disabled" : ""}>${uiText.moveUp}</button>
          <button class="action" type="button" data-move-block-id="${escapeHtml(block.id)}" data-direction="down" ${blockIndex === blocks.length - 1 ? "disabled" : ""}>${uiText.moveDown}</button>
        </div>
      </div>

      <div class="client-card-block-editor">
        <label>${uiText.title}
          <input data-block-id="${escapeHtml(block.id)}" data-prop="title" value="${escapeHtml(block.title)}" />
        </label>
        <div class="client-card-block-color-control" data-no-card-open>
          <label class="client-card-block-color-label" for="client-card-color-${escapeHtml(block.id)}">${uiText.blockColor}</label>
          <div class="client-card-color-picker-row">
            <input id="client-card-color-${escapeHtml(block.id)}" class="client-card-color-input" type="color" data-block-id="${escapeHtml(block.id)}" data-prop="color_scheme" value="${escapeHtml(blockColor)}" />
            <span class="client-card-color-hex" data-block-id="${escapeHtml(block.id)}" data-color-hex>${escapeHtml(blockColor.toUpperCase())}</span>
          </div>
        </div>
        <label class="checkbox-label">${uiText.activeLabel}
          <input type="checkbox" data-block-id="${escapeHtml(block.id)}" data-prop="is_active" ${block.is_active ? "checked" : ""} />
        </label>
        <label class="checkbox-label">${uiText.showInCard}
          <input type="checkbox" data-block-id="${escapeHtml(block.id)}" data-prop="show_in_card" ${block.show_in_card !== false ? "checked" : ""} />
        </label>
        <label class="checkbox-label">${uiText.showInEditor}
          <input type="checkbox" data-block-id="${escapeHtml(block.id)}" data-prop="show_in_editor" ${block.show_in_editor !== false ? "checked" : ""} />
        </label>
        <label class="checkbox-label">${uiText.showTitle}
          <input type="checkbox" data-block-id="${escapeHtml(block.id)}" data-prop="show_title" ${block.show_title !== false ? "checked" : ""} />
        </label>
      </div>

      <div class="client-card-accordion-body ${expanded ? "" : "hidden"}">
        <div class="client-card-field-list">
          ${(block.fields || []).map((field, fieldIndex, fields) => `
            <div class="client-card-field-row" data-open-field-id="${escapeHtml(field.id)}">
              <div class="client-card-field-visible">
                <input type="checkbox" ${field.is_visible ? "checked" : ""} disabled />
                <input type="checkbox" ${field.is_visible_in_editor !== false ? "checked" : ""} disabled />
              </div>
              <div class="client-card-field-main">
                <div class="client-card-field-label">${escapeHtml(field.label)}</div>
                <div class="client-card-field-meta">
                  <span class="mono" title="${escapeHtml(field.key)}">${escapeHtml(field.key)}</span>
                  <span>${escapeHtml(fieldTypeLabel(field.field_type))}</span>
                  ${field.is_system ? `<span>${uiText.systemField}</span>` : ""}
                </div>
              </div>
              <div class="client-card-field-actions">
                <button class="action" type="button" data-move-field-id="${escapeHtml(field.id)}" data-direction="up" ${fieldIndex === 0 ? "disabled" : ""}>${uiText.moveUp}</button>
                <button class="action" type="button" data-move-field-id="${escapeHtml(field.id)}" data-direction="down" ${fieldIndex === fields.length - 1 ? "disabled" : ""}>${uiText.moveDown}</button>
                ${field.is_system ? "" : `<button class="action danger" type="button" data-delete-field-id="${escapeHtml(field.id)}">${uiText.deleteField}</button>`}
              </div>
            </div>
          `).join("")}
        </div>
        <div class="client-card-block-footer">
          <button class="action" type="button" data-add-field-block-key="${escapeHtml(block.key)}">${uiText.addField}</button>
        </div>
      </div>
    `;

    applySettingsBlockColor(card, blockColor);
    el.clientCardSettings.appendChild(card);
    card.addEventListener("click", (event) => {
      const boundary = interactiveBoundary(event.target);
      if (boundary && boundary !== card) return;
      toggleBlock(block.id);
    });
    bindKeyboardOpen(card, () => toggleBlock(block.id));
  }

  el.clientCardSettings.querySelectorAll("[data-prop='title']").forEach((input) => {
    input.addEventListener("input", () => scheduleBlockAutosave(input.dataset.blockId, { delay: 500, statusText: uiText.blockSaved }));
    input.addEventListener("change", () => scheduleBlockAutosave(input.dataset.blockId, { statusText: uiText.blockSaved }));
    input.addEventListener("blur", () => scheduleBlockAutosave(input.dataset.blockId, { statusText: uiText.blockSaved }));
  });

  el.clientCardSettings.querySelectorAll("[data-prop='color_scheme']").forEach((input) => {
    input.addEventListener("input", () => {
      const id = input.dataset.blockId;
      const color = colorToHex(input.value);
      state.pendingBlockColors.set(id, color);
      const card = input.closest(".client-card-settings-block");
      const hex = el.clientCardSettings.querySelector(`[data-block-id="${id}"][data-color-hex]`);
      if (hex) hex.textContent = color.toUpperCase();
      if (card) applySettingsBlockColor(card, color);
    });
    input.addEventListener("change", () => saveBlock(input.dataset.blockId, uiText.colorSaved));
  });

  el.clientCardSettings.querySelectorAll("[data-move-block-id]").forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      moveBlock(btn.dataset.moveBlockId, btn.dataset.direction);
    });
  });

  el.clientCardSettings.querySelectorAll("[data-prop='is_active'], [data-prop='show_in_card'], [data-prop='show_in_editor'], [data-prop='show_title']").forEach((input) => {
    input.addEventListener("change", (event) => {
      event.stopPropagation();
      scheduleBlockAutosave(input.dataset.blockId, { statusText: uiText.blockSaved });
    });
  });

  el.clientCardSettings.querySelectorAll("[data-delete-block-id]").forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      deleteBlock(btn.dataset.deleteBlockId);
    });
  });

  el.clientCardSettings.querySelectorAll("[data-move-field-id]").forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      moveField(btn.dataset.moveFieldId, btn.dataset.direction);
    });
  });

  el.clientCardSettings.querySelectorAll("[data-delete-field-id]").forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      deleteField(btn.dataset.deleteFieldId);
    });
  });

  el.clientCardSettings.querySelectorAll("[data-open-field-id]").forEach((row) => {
    const fieldId = row.dataset.openFieldId;
    row.tabIndex = 0;
    row.setAttribute("role", "button");
    row.setAttribute("aria-label", `${uiText.edit}: ${row.querySelector(".client-card-field-label")?.textContent || fieldId}`);
    row.addEventListener("click", (event) => {
      event.stopPropagation();
      const boundary = interactiveBoundary(event.target);
      if (boundary && boundary !== row) return;
      openEditFieldModal(fieldId);
    });
    bindKeyboardOpen(row, () => openEditFieldModal(fieldId));
  });
  el.clientCardSettings.querySelectorAll("[data-add-field-block-key]").forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      openCreateFieldModal(btn.dataset.addFieldBlockKey);
    });
  });
}

function renderClientCardRules() {
  const rules = state.clientCard.rules || [];
  if (!el.clientCardRules) return;
  el.clientCardRules.innerHTML = "";
  el.emptyClientCardRulesText.classList.toggle("hidden", rules.length > 0);
  for (const rule of rules) {
    const actions = rule.actions || [];
    const row = document.createElement("div");
    row.className = "client-card-rule-row";
    row.tabIndex = 0;
    row.setAttribute("role", "button");
    row.setAttribute("aria-label", `${uiText.edit}: ${rule.name || rule.id}`);
    row.innerHTML = `
      <div class="client-card-field-main">
        <div class="client-card-field-label">${escapeHtml(rule.name)}</div>
        <div class="client-card-field-meta">
          <span>${rule.is_active ? uiText.active : uiText.inactive}</span>
          ${rule.is_system ? `<span>${uiText.systemField}</span>` : ""}
          <span>${escapeHtml(rule.condition_field_key)} ${escapeHtml(rule.condition_operator)} ${escapeHtml(rule.condition_value || "")}</span>
          <span>${actions.map((action) => `${escapeHtml(action.action || "")} ${escapeHtml(action.target_key || "")} (${escapeHtml(action.scope || "editor")})`).join("; ")}</span>
        </div>
      </div>
      <div class="client-card-field-actions">
        <label class="checkbox-label">${uiText.activeLabel}
          <input type="checkbox" data-toggle-rule-id="${escapeHtml(rule.id)}" ${rule.is_active ? "checked" : ""} />
        </label>
        <button class="action" type="button" data-move-rule-id="${escapeHtml(rule.id)}" data-direction="up">\u2191</button>
        <button class="action" type="button" data-move-rule-id="${escapeHtml(rule.id)}" data-direction="down">\u2193</button>
        <button class="action" type="button" data-copy-rule-id="${escapeHtml(rule.id)}">${uiText.copy}</button>
        ${rule.is_system ? "" : `<button class="action danger" type="button" data-delete-rule-id="${escapeHtml(rule.id)}">${uiText.deleteField}</button>`}
      </div>
    `;
    row.addEventListener("click", (event) => {
      if (event.target !== row && isInteractiveElement(event.target)) return;
      openRuleModal(rule.id);
    });
    bindKeyboardOpen(row, () => openRuleModal(rule.id));
    el.clientCardRules.appendChild(row);
  }
  el.clientCardRules.querySelectorAll("[data-delete-rule-id]").forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      deleteRule(btn.dataset.deleteRuleId);
    });
  });
  el.clientCardRules.querySelectorAll("[data-copy-rule-id]").forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      duplicateRule(btn.dataset.copyRuleId);
    });
  });
  el.clientCardRules.querySelectorAll("[data-toggle-rule-id]").forEach((input) => {
    input.addEventListener("click", (event) => event.stopPropagation());
    input.addEventListener("change", (event) => {
      event.stopPropagation();
      toggleRule(input.dataset.toggleRuleId, input.checked);
    });
  });
  el.clientCardRules.querySelectorAll("[data-move-rule-id]").forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      moveRule(btn.dataset.moveRuleId, btn.dataset.direction);
    });
  });
}

function renderClientCardSources() {
  if (!el.clientCardSources) return;
  const sources = state.clientCard.sources || [];
  el.clientCardSources.innerHTML = "";
  el.emptyClientCardSourcesText.classList.toggle("hidden", sources.length > 0);
  for (const source of sources) {
    const row = document.createElement("div");
    row.className = "client-card-rule-row";
    row.tabIndex = 0;
    row.setAttribute("role", "button");
    row.setAttribute("aria-label", `${uiText.edit}: ${source.name || source.technical_key || source.id}`);
    row.innerHTML = `
      <div class="client-card-field-main">
        <div class="client-card-field-label">${escapeHtml(source.name || source.technical_key || source.id)}</div>
        <div class="client-card-field-meta">
          <span>${source.is_active ? uiText.active : uiText.inactive}</span>
          <span class="mono">${escapeHtml(source.technical_key || "-")}</span>
          <span>${escapeHtml(source.method || "GET")}</span>
          <span title="${escapeHtml(source.url_template || "")}">${escapeHtml(compactUrl(source.url_template || ""))}</span>
          <span>${source.on_error === "block_save" ? "\u0411\u043b\u043e\u043a\u0438\u0440\u043e\u0432\u0430\u0442\u044c" : "\u0418\u0433\u043d\u043e\u0440\u0438\u0440\u043e\u0432\u0430\u0442\u044c"}</span>
        </div>
      </div>
      <div class="client-card-field-actions">
        <label class="checkbox-label">${uiText.activeLabel}
          <input type="checkbox" data-toggle-source-id="${escapeHtml(source.id)}" ${source.is_active ? "checked" : ""} />
        </label>
        <button class="action" type="button" data-test-source-id="${escapeHtml(source.id)}">\u041f\u0440\u043e\u0432\u0435\u0440\u0438\u0442\u044c</button>
        <button class="action danger" type="button" data-delete-source-id="${escapeHtml(source.id)}">\u0423\u0434\u0430\u043b\u0438\u0442\u044c</button>
      </div>
    `;
    row.addEventListener("click", (event) => {
      const boundary = interactiveBoundary(event.target);
      if (boundary && boundary !== row) return;
      openSourceModal(source.id);
    });
    bindKeyboardOpen(row, () => openSourceModal(source.id));
    el.clientCardSources.appendChild(row);
  }
  el.clientCardSources.querySelectorAll("[data-test-source-id]").forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      openSourceModal(btn.dataset.testSourceId);
    });
  });
  el.clientCardSources.querySelectorAll("[data-delete-source-id]").forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      deleteSource(btn.dataset.deleteSourceId);
    });
  });
  el.clientCardSources.querySelectorAll("[data-toggle-source-id]").forEach((input) => {
    input.addEventListener("click", (event) => event.stopPropagation());
    input.addEventListener("change", (event) => {
      event.stopPropagation();
      toggleSource(input.dataset.toggleSourceId, input.checked);
    });
  });
}

async function loadClientCardConfig() {
  el.emptyClientCardText.classList.remove("hidden");
  el.emptyClientCardText.textContent = "Загрузка настроек карточки...";
  state.clientCard = await api("/api/v1/settings/client-card");
  renderClientCardSettings();
  renderClientCardRules();
  renderClientCardSources();
  if (!(state.clientCard.blocks || []).length) {
    el.emptyClientCardText.classList.remove("hidden");
    el.emptyClientCardText.textContent = "Блоки карточки пока не настроены";
  }
}

function openCreateModal() {
  el.newClientCardBlockTitle.value = "";
  el.newClientCardBlockColor.value = fallbackColorHex;
  syncCreateBlockColorPreview();
  el.createClientCardBlockModal.classList.remove("hidden");
  el.newClientCardBlockTitle.focus();
}

function closeCreateModal() {
  el.createClientCardBlockModal.classList.add("hidden");
}

function openCreateFieldModal(blockKey = null) {
  state.createFieldBlockKey = blockKey || null;
  const contextBlock = state.createFieldBlockKey ? findBlockByKey(state.createFieldBlockKey) : null;
  el.newClientCardFieldLabel.value = "";
  el.newClientCardFieldType.value = "text";
  el.newClientCardFieldVisible.checked = true;
  el.newClientCardFieldVisibleInEditor.checked = true;
  el.newClientCardFieldEditable.checked = true;
  el.newClientCardFieldRequired.checked = false;
  el.newClientCardFieldOptions.value = "";
  el.newClientCardFieldBlock.innerHTML = (state.clientCard.blocks || []).map((block) => `<option value="${escapeHtml(block.key)}">${escapeHtml(block.title)}</option>`).join("");
  if (contextBlock) {
    el.newClientCardFieldBlock.value = state.createFieldBlockKey;
  }
  el.newClientCardFieldBlock.disabled = Boolean(contextBlock);
  syncCreateFieldOptionsVisibility();
  el.createClientCardFieldModal.classList.remove("hidden");
  el.newClientCardFieldLabel.focus();
}

function closeCreateFieldModal() {
  state.createFieldBlockKey = null;
  el.newClientCardFieldBlock.disabled = false;
  el.createClientCardFieldModal.classList.add("hidden");
}

async function createField() {
  try {
    const label = el.newClientCardFieldLabel.value.trim();
    if (!label) throw new Error("Название поля обязательно");
    const fieldType = el.newClientCardFieldType.value;
    const options = fieldType === "select" ? el.newClientCardFieldOptions.value.split("\n").map((item) => item.trim()).filter(Boolean) : null;
    if (fieldType === "select" && (!options || !options.length)) {
      throw new Error("Для поля select нужно указать хотя бы один вариант");
    }
    const targetBlockKey = state.createFieldBlockKey || el.newClientCardFieldBlock.value;
    const targetBlock = findBlockByKey(targetBlockKey);
    if (targetBlock) state.expandedBlocks.add(targetBlock.id);
    await api("/api/v1/settings/client-card/fields", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        label,
        field_type: fieldType,
        block_key: targetBlockKey,
        is_visible: Boolean(el.newClientCardFieldVisible.checked),
        is_visible_in_editor: Boolean(el.newClientCardFieldVisibleInEditor.checked),
        is_editable: Boolean(el.newClientCardFieldEditable.checked),
        is_required: Boolean(el.newClientCardFieldRequired.checked),
        options,
      }),
    });
    closeCreateFieldModal();
    await loadClientCardConfig();
    setStatus("Поле создано");
  } catch (e) {
    showError(e.message);
  }
}

async function createBlock() {
  try {
    const title = el.newClientCardBlockTitle.value.trim();
    if (!title) throw new Error("Название блока обязательно");
    const color = colorToHex(el.newClientCardBlockColor.value);
    await api("/api/v1/settings/client-card/blocks", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ title, color_scheme: color }),
    });
    closeCreateModal();
    await loadClientCardConfig();
    setStatus("Блок создан");
  } catch (e) {
    showError(e.message);
  }
}

const handlePageKeydown = (e) => {
  if (e.key === "Escape") {
    closeCreateModal();
    closeCreateFieldModal();
    closeEditFieldModal();
    closeRuleModal();
    closeSourceModal();
    closeError();
  }
};

(function init() {
  el.backToSettingsBtn.addEventListener("click", () => {
    if (window.HelpdeskAppShell?.navigate) {
      window.HelpdeskAppShell.navigate("/settings");
      return;
    }
    window.location.href = "/settings";
  });
  el.addClientCardBlockBtn.addEventListener("click", openCreateModal);
  el.clientCardMainTabs.forEach((tab) => {
    tab.addEventListener("click", () => setClientCardTab(tab.dataset.clientCardMainTab));
  });
  el.clientCardAutomationTabs.forEach((tab) => {
    tab.addEventListener("click", () => setAutomationTab(tab.dataset.clientCardAutomationTab));
  });
  el.addClientCardRuleBtn.addEventListener("click", () => openRuleModal());
  el.addClientCardSourceBtn?.addEventListener("click", () => openSourceModal());
  el.closeCreateClientCardBlockModalBtn.addEventListener("click", closeCreateModal);
  el.saveCreateClientCardBlockBtn.addEventListener("click", createBlock);
  el.newClientCardBlockColor.addEventListener("input", syncCreateBlockColorPreview);
  el.closeCreateClientCardFieldModalBtn.addEventListener("click", closeCreateFieldModal);
  el.saveCreateClientCardFieldBtn.addEventListener("click", createField);
  el.newClientCardFieldType.addEventListener("change", syncCreateFieldOptionsVisibility);
  el.editFieldType.addEventListener("change", syncEditFieldOptionsVisibility);
  el.closeEditClientCardFieldModalBtn.addEventListener("click", closeEditFieldModal);
  el.saveEditClientCardFieldBtn.addEventListener("click", saveFieldFromModal);
  el.closeClientCardRuleModalBtn.addEventListener("click", closeRuleModal);
  el.saveClientCardRuleBtn.addEventListener("click", saveRuleFromModal);
  el.closeClientCardSourceModalBtn?.addEventListener("click", closeSourceModal);
  el.saveClientCardSourceBtn?.addEventListener("click", saveSourceFromModal);
  el.testClientCardSourceBtn?.addEventListener("click", testSourceFromModal);
  el.deleteClientCardSourceBtn?.addEventListener("click", () => {
    if (state.editingSourceId) deleteSource(state.editingSourceId);
  });
  el.addSourceQueryRowBtn?.addEventListener("click", () => {
    el.sourceQueryRows.insertAdjacentHTML("beforeend", keyValueRowHtml("query"));
    markSourceEditorDirty();
  });
  el.addSourceHeaderRowBtn?.addEventListener("click", () => {
    el.sourceHeaderRows.insertAdjacentHTML("beforeend", keyValueRowHtml("header"));
    markSourceEditorDirty();
  });
  el.addSourceMappingRowBtn?.addEventListener("click", () => {
    el.sourceMappingRows.insertAdjacentHTML("beforeend", mappingRowHtml());
    markSourceEditorDirty();
  });
  el.sourceInsertUrlFieldBtn?.addEventListener("click", () => {
    insertAtCursor(el.sourceUrl, formatSourceTemplateRef(el.sourceInsertUrlField.value));
    markSourceEditorDirty();
    renderSourceTestContext(sourcePayloadFromModalSafe());
  });
  el.sourceInsertBodyFieldBtn?.addEventListener("click", () => {
    insertAtCursor(el.sourceBodyJson, formatSourceTemplateRef(el.sourceInsertBodyField.value));
    markSourceEditorDirty();
    renderSourceTestContext(sourcePayloadFromModalSafe());
  });
  [el.sourceQueryRows, el.sourceHeaderRows, el.sourceMappingRows].forEach((container) => {
    container?.addEventListener("click", (event) => {
      const removeBtn = event.target.closest("[data-source-remove-row]");
      if (removeBtn) {
        removeBtn.closest(".client-card-source-row")?.remove();
        markSourceEditorDirty();
        return;
      }
      const insertBtn = event.target.closest("[data-source-insert-field-btn]");
      if (insertBtn) {
        const row = insertBtn.closest(".client-card-source-row");
        insertAtCursor(
          row?.querySelector("[data-source-row-value]"),
          formatSourceTemplateRef(row?.querySelector("[data-source-insert-field]")?.value || ""),
        );
        row?.querySelector("[data-source-row-value]")?.removeAttribute("data-source-complex-value");
        markSourceEditorDirty();
        renderSourceTestContext(sourcePayloadFromModalSafe());
      }
    });
  });
  [el.sourceName, el.sourceTechnicalKey, el.sourceActive, el.sourceMethod, el.sourceUrl, el.sourceTimeoutMs, el.sourceOnError, el.sourceBodyJson, el.sourceQueryRows, el.sourceHeaderRows, el.sourceMappingRows].forEach((node) => {
    node?.addEventListener("input", (event) => {
      if (event?.target?.matches?.("[data-source-row-value]")) {
        event.target.removeAttribute("data-source-complex-value");
      }
      markSourceEditorDirty();
      renderSourceTestContext(sourcePayloadFromModalSafe());
    });
    node?.addEventListener("change", (event) => {
      if (event?.target?.matches?.("[data-source-row-value]")) {
        event.target.removeAttribute("data-source-complex-value");
      }
      markSourceEditorDirty();
      renderSourceTestContext(sourcePayloadFromModalSafe());
    });
  });
  el.deleteClientCardRuleBtn.addEventListener("click", () => {
    if (state.editingRuleId) deleteRule(state.editingRuleId);
  });
  el.ruleOperator.addEventListener("change", syncRuleValueVisibility);
  el.ruleConditionField.addEventListener("change", () => syncRuleConditionValueControl(""));
  el.addRuleActionBtn.addEventListener("click", () => {
    el.ruleActions.insertAdjacentHTML("beforeend", ruleActionRowHtml({}, el.ruleActions.querySelectorAll("[data-rule-action-row]").length));
    syncRuleActionRows();
  });
  el.ruleActions.addEventListener("change", (event) => {
    if (event.target.matches("[data-rule-action-prop]")) syncRuleActionRows();
    if (event.target.matches("[data-rule-template-preview-field]")) {
      syncRuleTemplatePreview(event.target.closest("[data-rule-action-row]"));
    }
  });
  el.ruleActions.addEventListener("input", (event) => {
    const row = event.target.closest("[data-rule-action-row]");
    if (!row) return;
    if (event.target.matches('[data-rule-action-prop="value"], [data-rule-template-preview-field]')) {
      syncRuleTemplatePreview(row);
    }
  });
  el.ruleActions.addEventListener("click", (event) => {
    const insertBtn = event.target.closest("[data-rule-template-insert-field-btn]");
    if (insertBtn) {
      const row = insertBtn.closest("[data-rule-action-row]");
      const fieldKey = row?.querySelector("[data-rule-template-insert-field]")?.value || "";
      if (fieldKey) {
        insertAtCursor(row.querySelector('[data-rule-action-prop="value"]'), `{{${fieldKey}}}`);
        syncRuleTemplatePreview(row);
      }
      return;
    }
    const btn = event.target.closest("[data-remove-rule-action]");
    if (!btn) return;
    const rows = el.ruleActions.querySelectorAll("[data-rule-action-row]");
    if (rows.length <= 1) return;
    btn.closest("[data-rule-action-row]").remove();
    syncRuleActionRows();
  });
  el.deleteEditClientCardFieldBtn.addEventListener("click", () => {
    if (state.editingFieldId) deleteField(state.editingFieldId);
  });

  closeOnBackdropClick(el.createClientCardBlockModal, closeCreateModal);
  closeOnBackdropClick(el.createClientCardFieldModal, closeCreateFieldModal);
  closeOnBackdropClick(el.editClientCardFieldModal, closeEditFieldModal);
  closeOnBackdropClick(el.clientCardRuleModal, closeRuleModal);
  if (el.clientCardSourceModal) closeOnBackdropClick(el.clientCardSourceModal, closeSourceModal);
  closeOnBackdropClick(el.errorModal, closeError);

  el.closeErrorModalBtn.addEventListener("click", closeError);
  el.okErrorBtn.addEventListener("click", closeError);
  el.copyErrorBtn.addEventListener("click", async () => {
    el.errorText.select();
    try {
      await navigator.clipboard.writeText(el.errorText.value);
    } catch {
      document.execCommand("copy");
    }
    setStatus("Текст ошибки скопирован");
  });

  document.addEventListener("keydown", handlePageKeydown);
  syncClientCardTabs();

  setStatus("Загружаю настройки...");
  window.HelpdeskSidebar.init({ onError: (e) => setStatus(`Ошибка: ${e.message}`) })
    .then(() => loadClientCardConfig())
    .then(() => setStatus("Готово"))
    .catch((e) => {
      if (e?.status !== 401) showError(e.message);
      setStatus("Ошибка загрузки настроек");
    });
})();
return () => {
  document.removeEventListener("keydown", handlePageKeydown);
};
}

if (window.HelpdeskAppShell?.registerPage) {
  window.HelpdeskAppShell.registerPage("settings-client-card", initSettingsClientCardPage);
} else {
  initSettingsClientCardPage();
}
})();
