(function () {
function initSettingsClientCardPage() {
const state = {
  clientCard: { blocks: [] },
  expandedBlocks: new Set(),
  editingFieldId: null,
  editingRuleId: null,
  pendingBlockColors: new Map(),
};

const colorSchemes = ["blue", "green", "yellow", "red", "gray", "purple"];
const fieldTypes = ["text", "textarea", "number", "date", "checkbox", "select", "phone", "url", "password"];
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
  saveBlock: "\u0421\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u044c \u0431\u043b\u043e\u043a",
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
  rulesSaved: "\u041f\u0440\u0430\u0432\u0438\u043b\u043e \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u043e",
  ruleDeleted: "\u041f\u0440\u0430\u0432\u0438\u043b\u043e \u0443\u0434\u0430\u043b\u0435\u043d\u043e",
};

const el = {
  statusText: document.getElementById("statusText"),
  backToSettingsBtn: document.getElementById("backToSettingsBtn"),
  addClientCardBlockBtn: document.getElementById("addClientCardBlockBtn"),
  addClientCardFieldBtn: document.getElementById("addClientCardFieldBtn"),
  clientCardSettings: document.getElementById("clientCardSettings"),
  emptyClientCardText: document.getElementById("emptyClientCardText"),
  addClientCardRuleBtn: document.getElementById("addClientCardRuleBtn"),
  clientCardRules: document.getElementById("clientCardRules"),
  emptyClientCardRulesText: document.getElementById("emptyClientCardRulesText"),
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

function findRuleById(ruleId) {
  return (state.clientCard.rules || []).find((rule) => rule.id === ruleId) || null;
}

function isCustomBlock(block) {
  return block && !systemBlockKeys.has(block.key) && String(block.key || "").startsWith("custom_block_");
}

function setClientCardAndRender(config) {
  if (config) state.clientCard = config;
  renderClientCardSettings();
  renderClientCardRules();
}

function fieldOptionsHtml(selectedKey = "") {
  return allClientCardFields()
    .map((field) => `<option value="${escapeHtml(field.key)}" ${field.key === selectedKey ? "selected" : ""}>${escapeHtml(field.label || field.key)} (${escapeHtml(field.key)})</option>`)
    .join("");
}

function saveFieldOptionsHtml(selectedKey = "") {
  return allClientCardFields()
    .filter((field) => !field.is_system || saveContextSystemFieldKeys.has(field.key))
    .map((field) => `<option value="${escapeHtml(field.key)}" ${field.key === selectedKey ? "selected" : ""}>${escapeHtml(field.label || field.key)} (${escapeHtml(field.key)})</option>`)
    .join("");
}

function blockOptionsHtml(selectedKey = "") {
  return (state.clientCard.blocks || [])
    .map((block) => `<option value="${escapeHtml(block.key)}" ${block.key === selectedKey ? "selected" : ""}>${escapeHtml(block.title || block.key)} (${escapeHtml(block.key)})</option>`)
    .join("");
}

function syncRuleValueVisibility() {
  const needsValue = el.ruleOperator.value === "equals" || el.ruleOperator.value === "not_equals";
  el.ruleConditionField.closest("label").classList.toggle("hidden", el.ruleOperator.value === "always");
  el.ruleValueWrap.classList.toggle("hidden", !needsValue);
}

function ruleActionOptions(targetType, scope, selected = "show") {
  const allowed = scope === "save"
    ? ["set_value"]
    : (targetType === "block" || scope !== "editor"
      ? ["show", "hide"]
      : ["show", "hide", "readonly", "editable", "required", "optional"]);
  const labels = {
    show: "\u041f\u043e\u043a\u0430\u0437\u0430\u0442\u044c",
    hide: "\u0421\u043a\u0440\u044b\u0442\u044c",
    readonly: "readonly",
    editable: "editable",
    required: "\u041e\u0431\u044f\u0437\u0430\u0442\u0435\u043b\u044c\u043d\u043e\u0435",
    optional: "\u041d\u0435\u043e\u0431\u044f\u0437\u0430\u0442\u0435\u043b\u044c\u043d\u043e\u0435",
    set_value: "\u0417\u0430\u0434\u0430\u0442\u044c \u0437\u043d\u0430\u0447\u0435\u043d\u0438\u0435",
  };
  const safeSelected = allowed.includes(selected) ? selected : allowed[0];
  return allowed
    .map((value) => `<option value="${value}" ${value === safeSelected ? "selected" : ""}>${labels[value]}</option>`)
    .join("");
}

function ruleActionRowHtml(action = {}, index = 0) {
  const targetType = action.target_type || "field";
  const scope = action.scope || "editor";
  const targetKey = action.target_key || "";
  const valueType = action.value_type || "literal";
  const mapText = Object.entries(action.map || {})
    .map(([key, value]) => `${key} => ${value ?? ""}`)
    .join("\n");
  return `
    <div class="client-card-rule-action-row" data-rule-action-row>
      <label>\u0422\u0438\u043f \u0446\u0435\u043b\u0438
        <select data-rule-action-prop="target_type">
          <option value="field" ${targetType === "field" ? "selected" : ""}>\u041f\u043e\u043b\u0435</option>
          <option value="block" ${targetType === "block" ? "selected" : ""}>\u0411\u043b\u043e\u043a</option>
        </select>
      </label>
      <label data-rule-field-target-wrap>\u0426\u0435\u043b\u044c<select data-rule-action-prop="target_field">${fieldOptionsHtml(targetKey)}</select></label>
      <label data-rule-block-target-wrap>\u0426\u0435\u043b\u044c<select data-rule-action-prop="target_block">${blockOptionsHtml(targetKey)}</select></label>
      <label>\u0414\u0435\u0439\u0441\u0442\u0432\u0438\u0435<select data-rule-action-prop="action">${ruleActionOptions(targetType, scope, action.action || "show")}</select></label>
      <label>Scope
        <select data-rule-action-prop="scope">
          <option value="editor" ${scope === "editor" ? "selected" : ""}>\u041e\u043a\u043d\u043e \u0440\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u044f</option>
          <option value="card" ${scope === "card" ? "selected" : ""}>\u041a\u0430\u0440\u0442\u043e\u0447\u043a\u0430</option>
          <option value="both" ${scope === "both" ? "selected" : ""}>\u041e\u0431\u0430</option>
          <option value="save" ${scope === "save" ? "selected" : ""}>\u0421\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u0438\u0435</option>
        </select>
      </label>
      <label data-rule-value-type-wrap>\u0422\u0438\u043f \u0437\u043d\u0430\u0447\u0435\u043d\u0438\u044f
        <select data-rule-action-prop="value_type">
          <option value="literal" ${valueType === "literal" ? "selected" : ""}>literal</option>
          <option value="field" ${valueType === "field" ? "selected" : ""}>field</option>
          <option value="template" ${valueType === "template" ? "selected" : ""}>template</option>
          <option value="map" ${valueType === "map" ? "selected" : ""}>map</option>
          <option value="null" ${valueType === "null" ? "selected" : ""}>null</option>
        </select>
      </label>
      <label data-rule-source-wrap>\u0418\u0441\u0442\u043e\u0447\u043d\u0438\u043a<select data-rule-action-prop="source_key">${fieldOptionsHtml(action.source_key || "")}</select></label>
      <label data-rule-value-wrap>\u0417\u043d\u0430\u0447\u0435\u043d\u0438\u0435<input data-rule-action-prop="value" value="${escapeHtml(action.value || "")}" autocomplete="off" /></label>
      <label data-rule-map-wrap>Map: \u043e\u0434\u043d\u0430 \u0441\u0442\u0440\u043e\u043a\u0430 = source =&gt; result<textarea data-rule-action-prop="map" rows="4">${escapeHtml(mapText)}</textarea></label>
      <button class="action danger ${index === 0 ? "hidden" : ""}" type="button" data-remove-rule-action>&times;</button>
    </div>
  `;
}

function syncRuleActionRow(row) {
  let targetType = row.querySelector('[data-rule-action-prop="target_type"]').value;
  const scope = row.querySelector('[data-rule-action-prop="scope"]').value;
  const valueType = row.querySelector('[data-rule-action-prop="value_type"]')?.value || "literal";
  if (scope === "save" && targetType !== "field") {
    row.querySelector('[data-rule-action-prop="target_type"]').value = "field";
    targetType = "field";
  }
  const targetFieldSelect = row.querySelector('[data-rule-action-prop="target_field"]');
  const sourceFieldSelect = row.querySelector('[data-rule-action-prop="source_key"]');
  const targetValue = targetFieldSelect.value;
  const sourceValue = sourceFieldSelect.value;
  targetFieldSelect.innerHTML = scope === "save" ? saveFieldOptionsHtml(targetValue) : fieldOptionsHtml(targetValue);
  sourceFieldSelect.innerHTML = saveFieldOptionsHtml(sourceValue);
  row.querySelector("[data-rule-field-target-wrap]").classList.toggle("hidden", targetType === "block");
  row.querySelector("[data-rule-block-target-wrap]").classList.toggle("hidden", targetType !== "block");
  const actionSelect = row.querySelector('[data-rule-action-prop="action"]');
  actionSelect.innerHTML = ruleActionOptions(targetType, scope, actionSelect.value);
  row.querySelector("[data-rule-value-type-wrap]").classList.toggle("hidden", actionSelect.value !== "set_value");
  row.querySelector("[data-rule-source-wrap]").classList.toggle("hidden", actionSelect.value !== "set_value" || !["field", "map"].includes(valueType));
  row.querySelector("[data-rule-value-wrap]").classList.toggle("hidden", actionSelect.value !== "set_value" || !["literal", "template"].includes(valueType));
  row.querySelector("[data-rule-map-wrap]").classList.toggle("hidden", actionSelect.value !== "set_value" || valueType !== "map");
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
  el.ruleValue.value = rule?.condition_value || "";
  renderRuleActions(rule?.actions || []);
  el.deleteClientCardRuleBtn.classList.toggle("hidden", !rule || rule.is_system);
  syncRuleValueVisibility();
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
  return {
    name,
    is_active: Boolean(el.ruleActive.checked),
    condition_field_key: el.ruleOperator.value === "always" ? null : el.ruleConditionField.value,
    condition_operator: el.ruleOperator.value,
    condition_value: (el.ruleOperator.value === "equals" || el.ruleOperator.value === "not_equals") ? (el.ruleValue.value.trim() || null) : null,
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
  try {
    const config = await api(`/api/v1/settings/client-card/rules/${ruleId}`, { method: "DELETE" });
    closeRuleModal();
    setClientCardAndRender(config);
    setStatus(uiText.ruleDeleted);
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
  el.editFieldType.innerHTML = fieldTypes.map((type) => `<option value="${type}">${type}</option>`).join("");
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

async function saveBlock(blockId, statusText = uiText.blockSaved) {
  try {
    await api(`/api/v1/settings/client-card/blocks/${blockId}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(blockPayloadFromDom(blockId)),
    });
    state.pendingBlockColors.delete(blockId);
    await loadClientCardConfig();
    setStatus(statusText);
  } catch (e) {
    showError(e.message);
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
    card.innerHTML = `
      <div class="client-card-accordion-head">
        <div class="client-card-accordion-main">
          <div class="client-card-accordion-title-row">
            <div class="client-card-accordion-title">${escapeHtml(block.title)}</div>
            <div class="client-card-accordion-key">${escapeHtml(block.key)}</div>
          </div>
          <div class="client-card-content-actions">
            <button class="action" type="button" data-toggle-block-id="${escapeHtml(block.id)}">${expanded ? uiText.collapse : uiText.expand}</button>
            <button class="action" type="button" data-save-block-id="${escapeHtml(block.id)}">${uiText.saveBlock}</button>
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
        <label>${uiText.blockColor}
          <div class="client-card-color-picker-row">
            <input class="client-card-color-input" type="color" data-block-id="${escapeHtml(block.id)}" data-prop="color_scheme" value="${escapeHtml(blockColor)}" />
            <span class="client-card-color-hex" data-block-id="${escapeHtml(block.id)}" data-color-hex>${escapeHtml(blockColor.toUpperCase())}</span>
          </div>
        </label>
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
            <div class="client-card-field-row">
              <div class="client-card-field-visible">
                <input type="checkbox" ${field.is_visible ? "checked" : ""} disabled />
                <input type="checkbox" ${field.is_visible_in_editor !== false ? "checked" : ""} disabled />
              </div>
              <div class="client-card-field-main">
                <div class="client-card-field-label">${escapeHtml(field.label)}</div>
                <div class="client-card-field-meta">
                  <span class="mono" title="${escapeHtml(field.key)}">${escapeHtml(field.key)}</span>
                  <span>${escapeHtml(field.field_type)}</span>
                  ${field.is_system ? `<span>${uiText.systemField}</span>` : ""}
                </div>
              </div>
              <div class="client-card-field-actions">
                <button class="action" type="button" data-move-field-id="${escapeHtml(field.id)}" data-direction="up" ${fieldIndex === 0 ? "disabled" : ""}>${uiText.moveUp}</button>
                <button class="action" type="button" data-move-field-id="${escapeHtml(field.id)}" data-direction="down" ${fieldIndex === fields.length - 1 ? "disabled" : ""}>${uiText.moveDown}</button>
                <button class="action" type="button" data-edit-field-id="${escapeHtml(field.id)}">${uiText.edit}</button>
                ${field.is_system ? "" : `<button class="action danger" type="button" data-delete-field-id="${escapeHtml(field.id)}">${uiText.deleteField}</button>`}
              </div>
            </div>
          `).join("")}
        </div>
      </div>
    `;

    applySettingsBlockColor(card, blockColor);
    el.clientCardSettings.appendChild(card);
  }

  el.clientCardSettings.querySelectorAll("[data-toggle-block-id]").forEach((btn) => {
    btn.addEventListener("click", () => toggleBlock(btn.dataset.toggleBlockId));
  });

  el.clientCardSettings.querySelectorAll("[data-save-block-id]").forEach((btn) => {
    btn.addEventListener("click", () => saveBlock(btn.dataset.saveBlockId, uiText.blockSaved));
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
    btn.addEventListener("click", () => moveBlock(btn.dataset.moveBlockId, btn.dataset.direction));
  });

  el.clientCardSettings.querySelectorAll("[data-delete-block-id]").forEach((btn) => {
    btn.addEventListener("click", () => deleteBlock(btn.dataset.deleteBlockId));
  });

  el.clientCardSettings.querySelectorAll("[data-move-field-id]").forEach((btn) => {
    btn.addEventListener("click", () => moveField(btn.dataset.moveFieldId, btn.dataset.direction));
  });

  el.clientCardSettings.querySelectorAll("[data-delete-field-id]").forEach((btn) => {
    btn.addEventListener("click", () => deleteField(btn.dataset.deleteFieldId));
  });

  el.clientCardSettings.querySelectorAll("[data-edit-field-id]").forEach((btn) => {
    btn.addEventListener("click", () => openEditFieldModal(btn.dataset.editFieldId));
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
        <button class="action" type="button" data-edit-rule-id="${escapeHtml(rule.id)}">${uiText.edit}</button>
        ${rule.is_system ? "" : `<button class="action danger" type="button" data-delete-rule-id="${escapeHtml(rule.id)}">${uiText.deleteField}</button>`}
      </div>
    `;
    el.clientCardRules.appendChild(row);
  }
  el.clientCardRules.querySelectorAll("[data-edit-rule-id]").forEach((btn) => {
    btn.addEventListener("click", () => openRuleModal(btn.dataset.editRuleId));
  });
  el.clientCardRules.querySelectorAll("[data-delete-rule-id]").forEach((btn) => {
    btn.addEventListener("click", () => deleteRule(btn.dataset.deleteRuleId));
  });
  el.clientCardRules.querySelectorAll("[data-toggle-rule-id]").forEach((input) => {
    input.addEventListener("change", () => toggleRule(input.dataset.toggleRuleId, input.checked));
  });
  el.clientCardRules.querySelectorAll("[data-move-rule-id]").forEach((btn) => {
    btn.addEventListener("click", () => moveRule(btn.dataset.moveRuleId, btn.dataset.direction));
  });
}

async function loadClientCardConfig() {
  el.emptyClientCardText.classList.remove("hidden");
  el.emptyClientCardText.textContent = "Загрузка настроек карточки...";
  state.clientCard = await api("/api/v1/settings/client-card");
  renderClientCardSettings();
  renderClientCardRules();
  if (!(state.clientCard.blocks || []).length) {
    el.emptyClientCardText.classList.remove("hidden");
    el.emptyClientCardText.textContent = "Блоки карточки пока не настроены";
  }
}

function openCreateModal() {
  el.newClientCardBlockTitle.value = "";
  el.newClientCardBlockColor.value = "gray";
  el.createClientCardBlockModal.classList.remove("hidden");
  el.newClientCardBlockTitle.focus();
}

function closeCreateModal() {
  el.createClientCardBlockModal.classList.add("hidden");
}

function openCreateFieldModal() {
  el.newClientCardFieldLabel.value = "";
  el.newClientCardFieldType.value = "text";
  el.newClientCardFieldVisible.checked = true;
  el.newClientCardFieldVisibleInEditor.checked = true;
  el.newClientCardFieldEditable.checked = true;
  el.newClientCardFieldRequired.checked = false;
  el.newClientCardFieldOptions.value = "";
  el.newClientCardFieldBlock.innerHTML = (state.clientCard.blocks || []).map((block) => `<option value="${escapeHtml(block.key)}">${escapeHtml(block.title)}</option>`).join("");
  syncCreateFieldOptionsVisibility();
  el.createClientCardFieldModal.classList.remove("hidden");
  el.newClientCardFieldLabel.focus();
}

function closeCreateFieldModal() {
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
    await api("/api/v1/settings/client-card/fields", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        label,
        field_type: fieldType,
        block_key: el.newClientCardFieldBlock.value,
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
    await api("/api/v1/settings/client-card/blocks", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ title, color_scheme: el.newClientCardBlockColor.value }),
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
  el.addClientCardFieldBtn.addEventListener("click", openCreateFieldModal);
  el.addClientCardRuleBtn.addEventListener("click", () => openRuleModal());
  el.closeCreateClientCardBlockModalBtn.addEventListener("click", closeCreateModal);
  el.saveCreateClientCardBlockBtn.addEventListener("click", createBlock);
  el.closeCreateClientCardFieldModalBtn.addEventListener("click", closeCreateFieldModal);
  el.saveCreateClientCardFieldBtn.addEventListener("click", createField);
  el.newClientCardFieldType.addEventListener("change", syncCreateFieldOptionsVisibility);
  el.editFieldType.addEventListener("change", syncEditFieldOptionsVisibility);
  el.closeEditClientCardFieldModalBtn.addEventListener("click", closeEditFieldModal);
  el.saveEditClientCardFieldBtn.addEventListener("click", saveFieldFromModal);
  el.closeClientCardRuleModalBtn.addEventListener("click", closeRuleModal);
  el.saveClientCardRuleBtn.addEventListener("click", saveRuleFromModal);
  el.deleteClientCardRuleBtn.addEventListener("click", () => {
    if (state.editingRuleId) deleteRule(state.editingRuleId);
  });
  el.ruleOperator.addEventListener("change", syncRuleValueVisibility);
  el.addRuleActionBtn.addEventListener("click", () => {
    el.ruleActions.insertAdjacentHTML("beforeend", ruleActionRowHtml({}, el.ruleActions.querySelectorAll("[data-rule-action-row]").length));
    syncRuleActionRows();
  });
  el.ruleActions.addEventListener("change", (event) => {
    if (event.target.matches("[data-rule-action-prop]")) syncRuleActionRows();
  });
  el.ruleActions.addEventListener("click", (event) => {
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
