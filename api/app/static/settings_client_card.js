(function () {
function initSettingsClientCardPage() {
const state = {
  clientCard: { blocks: [] },
  expandedBlocks: new Set(),
  editingFieldId: null,
  pendingBlockColors: new Map(),
};

const colorSchemes = ["blue", "green", "yellow", "red", "gray", "purple"];
const fieldTypes = ["text", "textarea", "number", "date", "checkbox", "select", "phone", "url", "password"];
const systemBlockKeys = new Set(["tg", "profile", "department", "work", "verification"]);
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
  moveUp: "\u0412\u044b\u0448\u0435",
  moveDown: "\u041d\u0438\u0436\u0435",
  edit: "\u0420\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u0442\u044c",
  deleteBlock: "\u0423\u0434\u0430\u043b\u0438\u0442\u044c \u0431\u043b\u043e\u043a",
  deleteField: "\u0423\u0434\u0430\u043b\u0438\u0442\u044c \u043f\u043e\u043b\u0435",
  systemField: "\u0421\u0438\u0441\u0442\u0435\u043c\u043d\u043e\u0435 \u043f\u043e\u043b\u0435",
  blockSaved: "\u0411\u043b\u043e\u043a \u0441\u043e\u0445\u0440\u0430\u043d\u0451\u043d",
  blockMoved: "\u041f\u043e\u0440\u044f\u0434\u043e\u043a \u0431\u043b\u043e\u043a\u043e\u0432 \u0441\u043e\u0445\u0440\u0430\u043d\u0451\u043d",
  fieldMoved: "\u041f\u043e\u0440\u044f\u0434\u043e\u043a \u043f\u043e\u043b\u0435\u0439 \u0441\u043e\u0445\u0440\u0430\u043d\u0451\u043d",
  blockDeleted: "\u0411\u043b\u043e\u043a \u0443\u0434\u0430\u043b\u0451\u043d",
  fieldDeleted: "\u041f\u043e\u043b\u0435 \u0443\u0434\u0430\u043b\u0435\u043d\u043e",
  deleteBlockConfirmPrefix: "\u0423\u0434\u0430\u043b\u0438\u0442\u044c \u0431\u043b\u043e\u043a \u00ab",
  deleteBlockConfirmSuffix: "\u00bb \u0438 \u0432\u0441\u0435 \u0435\u0433\u043e \u043f\u043e\u043b\u044f?",
  deleteFieldConfirmPrefix: "\u0423\u0434\u0430\u043b\u0438\u0442\u044c \u043f\u043e\u043b\u0435 \u00ab",
  deleteFieldConfirmSuffix: "\u00bb?",
};

const el = {
  statusText: document.getElementById("statusText"),
  backToSettingsBtn: document.getElementById("backToSettingsBtn"),
  addClientCardBlockBtn: document.getElementById("addClientCardBlockBtn"),
  addClientCardFieldBtn: document.getElementById("addClientCardFieldBtn"),
  clientCardSettings: document.getElementById("clientCardSettings"),
  emptyClientCardText: document.getElementById("emptyClientCardText"),
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
  newClientCardFieldEditable: document.getElementById("newClientCardFieldEditable"),
  newClientCardFieldRequired: document.getElementById("newClientCardFieldRequired"),
  newClientCardFieldOptionsWrap: document.getElementById("newClientCardFieldOptionsWrap"),
  newClientCardFieldOptions: document.getElementById("newClientCardFieldOptions"),
  editClientCardFieldModal: document.getElementById("editClientCardFieldModal"),
  closeEditClientCardFieldModalBtn: document.getElementById("closeEditClientCardFieldModalBtn"),
  saveEditClientCardFieldBtn: document.getElementById("saveEditClientCardFieldBtn"),
  editFieldLabel: document.getElementById("editFieldLabel"),
  editFieldVisible: document.getElementById("editFieldVisible"),
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

function isCustomBlock(block) {
  return block && !systemBlockKeys.has(block.key) && String(block.key || "").startsWith("custom_block_");
}

function setClientCardAndRender(config) {
  if (config) state.clientCard = config;
  renderClientCardSettings();
}

function openEditFieldModal(fieldId) {
  const field = findFieldById(fieldId);
  if (!field) return;
  state.editingFieldId = field.id;

  el.editFieldLabel.value = field.label || "";
  el.editFieldLabel.readOnly = false;
  el.editFieldVisible.checked = Boolean(field.is_visible);
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
      </div>

      <div class="client-card-accordion-body ${expanded ? "" : "hidden"}">
        <div class="client-card-field-list">
          ${(block.fields || []).map((field, fieldIndex, fields) => `
            <div class="client-card-field-row">
              <div class="client-card-field-visible">
                <input type="checkbox" ${field.is_visible ? "checked" : ""} disabled />
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
    btn.addEventListener("click", async () => {
      const id = btn.dataset.saveBlockId;
      const title = el.clientCardSettings.querySelector(`[data-block-id="${id}"][data-prop="title"]`).value.trim();
      const color = state.pendingBlockColors.get(id) || el.clientCardSettings.querySelector(`[data-block-id="${id}"][data-prop="color_scheme"]`)?.value || fallbackColorHex;
      const active = Boolean(el.clientCardSettings.querySelector(`[data-block-id="${id}"][data-prop="is_active"]`).checked);
      try {
        await api(`/api/v1/settings/client-card/blocks/${id}`, {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ title, color_scheme: color, is_active: active }),
        });
        state.pendingBlockColors.delete(id);
        await loadClientCardConfig();
        setStatus(uiText.blockSaved);
      } catch (e) {
        showError(e.message);
      }
    });
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

async function loadClientCardConfig() {
  el.emptyClientCardText.classList.remove("hidden");
  el.emptyClientCardText.textContent = "Загрузка настроек карточки...";
  state.clientCard = await api("/api/v1/settings/client-card");
  renderClientCardSettings();
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
  el.closeCreateClientCardBlockModalBtn.addEventListener("click", closeCreateModal);
  el.saveCreateClientCardBlockBtn.addEventListener("click", createBlock);
  el.closeCreateClientCardFieldModalBtn.addEventListener("click", closeCreateFieldModal);
  el.saveCreateClientCardFieldBtn.addEventListener("click", createField);
  el.newClientCardFieldType.addEventListener("change", syncCreateFieldOptionsVisibility);
  el.editFieldType.addEventListener("change", syncEditFieldOptionsVisibility);
  el.closeEditClientCardFieldModalBtn.addEventListener("click", closeEditFieldModal);
  el.saveEditClientCardFieldBtn.addEventListener("click", saveFieldFromModal);
  el.deleteEditClientCardFieldBtn.addEventListener("click", () => {
    if (state.editingFieldId) deleteField(state.editingFieldId);
  });

  closeOnBackdropClick(el.createClientCardBlockModal, closeCreateModal);
  closeOnBackdropClick(el.createClientCardFieldModal, closeCreateFieldModal);
  closeOnBackdropClick(el.editClientCardFieldModal, closeEditFieldModal);
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
