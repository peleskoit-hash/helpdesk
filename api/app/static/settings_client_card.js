(function () {
function initSettingsClientCardPage() {
const state = {
  clientCard: { blocks: [] },
  expandedBlocks: new Set(),
  editingFieldId: null,
};

const colorSchemes = ["blue", "green", "yellow", "red", "gray", "purple"];
const fieldTypes = ["text", "textarea", "number", "date", "checkbox", "select", "phone", "url", "password"];
const colorLabels = {
  blue: "Голубой",
  green: "Зелёный",
  yellow: "Жёлтый",
  red: "Красный",
  gray: "Серый",
  purple: "Фиолетовый",
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
  editFieldSortOrder: document.getElementById("editFieldSortOrder"),
  editFieldBlockKey: document.getElementById("editFieldBlockKey"),
  editFieldKey: document.getElementById("editFieldKey"),
  editFieldType: document.getElementById("editFieldType"),
  editFieldOptionsWrap: document.getElementById("editFieldOptionsWrap"),
  editFieldOptions: document.getElementById("editFieldOptions"),
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
  return `client-card-color-${colorSchemes.includes(color) ? color : "gray"}`;
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

function openEditFieldModal(fieldId) {
  const field = findFieldById(fieldId);
  if (!field) return;
  state.editingFieldId = field.id;

  el.editFieldLabel.value = field.label || "";
  el.editFieldLabel.readOnly = false;
  el.editFieldVisible.checked = Boolean(field.is_visible);
  el.editFieldEditable.checked = Boolean(field.is_editable);
  el.editFieldRequired.checked = Boolean(field.is_required);
  el.editFieldSortOrder.value = String(field.sort_order ?? 0);
  el.editFieldKey.value = field.key || "";
  el.editFieldType.innerHTML = fieldTypes.map((type) => `<option value="${type}">${type}</option>`).join("");
  el.editFieldType.value = field.field_type || "text";

  const isSystem = Boolean(field.is_system);
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
    sort_order: Number(el.editFieldSortOrder.value || 0),
    block_key: el.editFieldBlockKey.value,
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

function renderClientCardSettings() {
  const blocks = state.clientCard.blocks || [];
  el.clientCardSettings.innerHTML = "";
  el.emptyClientCardText.classList.toggle("hidden", blocks.length > 0);

  for (const block of blocks) {
    const expanded = state.expandedBlocks.has(block.id);
    const colorLabel = colorLabels[block.color_scheme] || block.color_scheme;

    const card = document.createElement("div");
    card.className = `client-card-settings-block compact ${blockColorClass(block.color_scheme)}`;
    card.innerHTML = `
      <div class="client-card-accordion-head">
        <div class="client-card-accordion-main">
          <div class="client-card-accordion-title-row">
            <div class="client-card-accordion-title">${escapeHtml(block.title)}</div>
            <div class="client-card-accordion-key">${escapeHtml(block.key)}</div>
          </div>
          <div class="client-card-accordion-meta">
            <span>Цвет: <b>${escapeHtml(colorLabel)}</b></span>
            <span>Порядок: <b>${Number(block.sort_order || 0)}</b></span>
            <span>Статус: <b>${block.is_active ? "Активен" : "Не активен"}</b></span>
          </div>
        </div>
        <div class="client-card-accordion-actions">
          <button class="action" type="button" data-toggle-block-id="${escapeHtml(block.id)}">${expanded ? "Свернуть" : "Раскрыть"}</button>
          <button class="action" type="button" data-save-block-id="${escapeHtml(block.id)}">Сохранить блок</button>
        </div>
      </div>

      <div class="client-card-block-editor">
        <label>Название блока
          <input data-block-id="${escapeHtml(block.id)}" data-prop="title" value="${escapeHtml(block.title)}" />
        </label>
        <label>Цветовая схема
          <select data-block-id="${escapeHtml(block.id)}" data-prop="color_scheme">
            ${colorSchemes.map((c) => `<option value="${c}" ${c === block.color_scheme ? "selected" : ""}>${colorLabels[c] || c}</option>`).join("")}
          </select>
        </label>
        <label>Порядок
          <input type="number" data-block-id="${escapeHtml(block.id)}" data-prop="sort_order" value="${Number(block.sort_order || 0)}" />
        </label>
        <label class="checkbox-label">Активен
          <input type="checkbox" data-block-id="${escapeHtml(block.id)}" data-prop="is_active" ${block.is_active ? "checked" : ""} />
        </label>
      </div>

      <div class="client-card-accordion-body ${expanded ? "" : "hidden"}">
        <div class="client-card-field-list">
          ${(block.fields || []).map((field) => `
            <div class="client-card-field-row">
              <div class="client-card-field-visible">
                <input type="checkbox" ${field.is_visible ? "checked" : ""} disabled />
              </div>
              <div class="client-card-field-main">
                <div class="client-card-field-label">${escapeHtml(field.label)}</div>
                <div class="client-card-field-meta">
                  <span class="mono" title="${escapeHtml(field.key)}">${escapeHtml(field.key)}</span>
                  <span>${escapeHtml(field.field_type)}</span>
                </div>
              </div>
              <div class="client-card-field-order">${Number(field.sort_order || 0)}</div>
              <div class="client-card-field-actions">
                <button class="action" type="button" data-edit-field-id="${escapeHtml(field.id)}">Редактировать</button>
              </div>
            </div>
          `).join("")}
        </div>
      </div>
    `;

    el.clientCardSettings.appendChild(card);
  }

  el.clientCardSettings.querySelectorAll("[data-toggle-block-id]").forEach((btn) => {
    btn.addEventListener("click", () => toggleBlock(btn.dataset.toggleBlockId));
  });

  el.clientCardSettings.querySelectorAll("[data-save-block-id]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.dataset.saveBlockId;
      const title = el.clientCardSettings.querySelector(`[data-block-id="${id}"][data-prop="title"]`).value.trim();
      const color = el.clientCardSettings.querySelector(`[data-block-id="${id}"][data-prop="color_scheme"]`).value;
      const order = Number(el.clientCardSettings.querySelector(`[data-block-id="${id}"][data-prop="sort_order"]`).value || 0);
      const active = Boolean(el.clientCardSettings.querySelector(`[data-block-id="${id}"][data-prop="is_active"]`).checked);
      try {
        await api(`/api/v1/settings/client-card/blocks/${id}`, {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ title, color_scheme: color, sort_order: order, is_active: active }),
        });
        await loadClientCardConfig();
        setStatus("Блок сохранён");
      } catch (e) {
        showError(e.message);
      }
    });
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
