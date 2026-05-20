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

function botFilesCtx() {
  return {
    state,
    escapeHtml,
    FIELD_LABELS,
    OPERATOR_OPTIONS,
    renderEditor,
  };
}

function hasUnsavedChanges() {
  return (state.editorText || "") !== (state.savedText || "")
    || (state.editorBotLabel || "") !== (state.savedBotLabel || "")
    || (state.editorClientText || "") !== (state.savedClientText || "")
    || (state.editorSystemText || "") !== (state.savedSystemText || "")
    || state.fixedAssigneeEnabled !== state.savedFixedAssigneeEnabled
    || (state.fixedAssigneeUserId || "") !== (state.savedFixedAssigneeUserId || "")
    || state.freezeOnCreate !== state.savedFreezeOnCreate
    || state.confirmationEnabled !== state.savedConfirmationEnabled
    || (state.confirmationText || "") !== (state.savedConfirmationText || "")
    || state.filesOverridden !== state.savedFilesOverridden
    || JSON.stringify(state.selectedFiles || []) !== JSON.stringify(state.savedSelectedFiles || [])
    || state.conditionalFilesOverridden !== state.savedConditionalFilesOverridden
    || JSON.stringify(state.conditionalRules || []) !== JSON.stringify(state.savedConditionalRules || [])
    || state.isEnabled !== state.savedIsEnabled
    || (state.automationKeyValue || "") !== (state.savedAutomationKeyValue || "");
}

function confirmDiscard() {
  if (!hasUnsavedChanges()) return true;
  return window.confirm("Есть несохранённые изменения. Уйти без сохранения?");
}

function discardEditorChanges() {
  state.editorText = state.savedText || "";
  state.editorClientText = state.savedClientText || "";
  state.editorBotLabel = state.savedBotLabel || "";
  state.editorSystemText = state.savedSystemText || "";
  state.fixedAssigneeEnabled = !!state.savedFixedAssigneeEnabled;
  state.fixedAssigneeUserId = state.savedFixedAssigneeUserId || "";
  state.freezeOnCreate = !!state.savedFreezeOnCreate;
  state.confirmationEnabled = !!state.savedConfirmationEnabled;
  state.confirmationText = state.savedConfirmationText || "";
  state.filesOverridden = !!state.savedFilesOverridden;
  state.selectedFiles = [...(state.savedSelectedFiles || [])];
  state.conditionalFilesOverridden = !!state.savedConditionalFilesOverridden;
  state.conditionalRules = JSON.parse(JSON.stringify(state.savedConditionalRules || []));
  state.isEnabled = !!state.savedIsEnabled;
  state.automationKeyValue = state.savedAutomationKeyValue || "";
}

function nodeClass(node) {
  if (state.layoutEditMode) return "bot-buttons-level-item";
  const disabledClass = node.is_enabled === false ? " bot-button-disabled" : "";
  if (node.kind === "terminal") {
    if (node.removed) return "bot-buttons-level-item bot-button-leaf-removed";
    return node.has_instruction
      ? `bot-buttons-level-item bot-button-leaf-has${disabledClass}`
      : `bot-buttons-level-item bot-button-leaf-empty${disabledClass}`;
  }
  return `bot-buttons-level-item${disabledClass}`;
}

function cloneRows(rows) {
  return JSON.parse(JSON.stringify(rows || []));
}

function currentLevel() {
  return state.levelStack[state.levelStack.length - 1] || null;
}

function isLayoutEditableLevel(level) {
  if (!level) return false;
  if (state.view !== "level") return false;
  if (!state.selectedMenu) return false;
  if (!level.levelKey) return false;
  return !String(level.levelKey).includes("|level|__removed__");
}

function buildPreviewFromNode(node) {
  const childrenRows = Array.isArray(node?.children_rows) ? node.children_rows : [];
  const childrenCount = childrenRows.reduce((acc, row) => acc + ((row?.buttons || []).length), 0);
  return {
    kind: node?.kind || null,
    label: node?.label || "",
    technicalKey: node?.technical_key || "",
    workflowType: node?.workflow_type || "",
    fullPath: node?.full_path || "",
    childrenCount,
    item: null,
    loading: false,
    menuName: state.selectedMenu || "",
  };
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
  if (!node?.technical_key || !state.selectedMenu) return;
  const cacheKey = `${state.selectedMenu}|${node.technical_key}`;
  if (state.itemCache[cacheKey]) {
    state.preview.item = state.itemCache[cacheKey];
    const panel = document.getElementById("botButtonsRightPanel");
    if (panel) panel.innerHTML = renderRightPanel();
    return;
  }
  state.preview.loading = true;
  {
    const panel = document.getElementById("botButtonsRightPanel");
    if (panel) panel.innerHTML = renderRightPanel();
  }
  try {
    const payload = await api(`/api/v1/settings/bot-buttons/item?button_key=${encodeURIComponent(node.technical_key)}&menu_type=${encodeURIComponent(state.selectedMenu)}`);
    state.itemCache[cacheKey] = payload;
    if (state.preview.technicalKey === node.technical_key && state.preview.menuName === state.selectedMenu) {
      state.preview.item = payload;
    }
  } catch {
    if (state.preview.technicalKey === node.technical_key && state.preview.menuName === state.selectedMenu) {
      state.preview.item = null;
    }
  } finally {
    if (state.preview.technicalKey === node.technical_key && state.preview.menuName === state.selectedMenu) {
      state.preview.loading = false;
      const panel = document.getElementById("botButtonsRightPanel");
      if (panel) panel.innerHTML = renderRightPanel();
    }
  }
}

function renderRightPanel() {
  const p = state.preview || {};
  if (!p.kind && !state.selectedMenu) {
    return '<div class="bot-buttons-empty-side">Выберите кнопку слева</div>';
  }
  if (!p.kind && state.selectedMenu) {
    return `
      <div class="bot-info-card">
        <div class="bot-info-title">${escapeHtml(state.selectedMenu)}</div>
        <div class="bot-info-note">Выберите раздел или кнопку слева</div>
      </div>
    `;
  }
  const typeLabel = nodeKindLabel(p.kind, p.workflowType);
  const isSection = p.kind === "submenu" || p.kind === "menu_group" || p.kind === "group_task_parent";
  const item = p.item;
  let html = `
    <div class="bot-info-card">
      <div class="bot-info-title">${escapeHtml(p.label || "Элемент")}</div>
      ${renderInfoRow("Тип", typeLabel)}
      ${renderInfoRow("Key", safeText(p.technicalKey))}
  `;
  const stableId = item?.stable_button_id ? String(item.stable_button_id) : "не задано";
  const statusText = item ? (item.currently_present === false ? "удалённая/изменённая" : "актуальная") : (p.kind === "terminal" ? "не задано" : "актуальная");
  const legacyKey = item?.legacy_button_key ? String(item.legacy_button_key) : "не задано";
  html += renderInfoRow("Stable id", stableId);
  html += renderInfoRow("Статус", statusText);
  if (legacyKey !== "не задано") html += renderInfoRow("Legacy key", legacyKey);
  if (isSection) {
    html += renderInfoRow("Вложенных элементов", String(p.childrenCount || 0));
    html += '<div class="bot-info-note">Откройте раздел, чтобы выбрать конечную кнопку</div>';
    html += `
      <div class="bot-info-actions">
        <button type="button" class="action" id="renameSectionBtn">Переименовать раздел</button>
        <button type="button" class="action" id="resetSectionLabelBtn">Сбросить название</button>
      </div>
    `;
  }
  if (p.loading) {
    html += '<div class="bot-info-note">Загрузка данных...</div>';
  } else {
    html += renderInfoRow("Инструкция", item ? yesNo(!!item.instruction_text) : "не задано");
    html += renderInfoRow("Название в боте", item ? (item.button_label_effective || item.button_label || "не задано") : safeText(p.label));
    html += renderInfoRow("Текст клиенту", item ? yesNo(!!item.client_text_effective) : "не задано");
    html += renderInfoRow("Системный текст", item ? yesNo(!!item.system_text_effective) : "не задано");
    html += renderInfoRow("Автоматизация", item ? safeText(item.automation_key_effective || "не задано") : "не задано");
    html += renderInfoRow(
      "Статус автоматизации",
      item
        ? (item.automation_status === "not_found"
          ? "не найдена"
          : (item.automation_status === "available"
            ? (item.automation_available ? "доступна" : "в реестре, но недоступна")
            : "не задана"))
        : "не задано",
    );
    html += renderInfoRow("Файлы", item ? yesNo(Array.isArray(item.files_effective) && item.files_effective.length > 0) : "не задано");
    html += renderInfoRow("Условные файлы", item ? yesNo(Array.isArray(item.conditional_files_effective) && item.conditional_files_effective.length > 0) : "не задано");
    const assignee = item?.fixed_assignee_enabled ? (item.fixed_assignee_user_id || "не задано") : "не задано";
    html += renderInfoRow("Фикс. ответственный", assignee);
    html += renderInfoRow("Заморозка при создании", item ? yesNo(!!item.freeze_on_create) : "не задано");
    html += renderInfoRow("Подтверждение", item ? yesNo(!!item.confirmation_enabled) : "не задано");
  }
  html += "</div>";
  return html;
}

function renderMenuTypes() {
  const items = state.menuTypes || [];
  const archived = state.archivedMenuTypes || [];
  if (!items.length && !archived.length) {
    el.content.innerHTML = `<div class="status settings-empty">Типы меню не найдены</div>`;
    return;
  }

  el.content.innerHTML = `
    <p class="bot-buttons-description">Здесь настраиваются кнопки Telegram-бота на основе текущих txt-файлов.</p>
    <div class="bot-buttons-split-layout">
      <div class="bot-buttons-split-left">
        <div class="bot-layout-toolbar">
          <button type="button" id="createMenuTypeBtn" class="action">Создать меню</button>
        </div>
        <div class="bot-buttons-level-list">
          ${items.map((item) => `
            <button type="button" class="bot-buttons-level-item ${item.ok ? "" : "bot-button-type-card-error"}" data-menu-name="${escapeHtml(item.menu_name)}">
              ${escapeHtml(menuTypeTitle(item))}
            </button>
          `).join("")}
        </div>
        ${archived.length ? `
        <div class="bot-buttons-archive-bottom">
          <div class="bot-menu-bindings-title">Архив меню</div>
          <div class="bot-buttons-level-list">
            ${archived.map((item) => `
              <div class="bot-removed-row">
                <button type="button" class="bot-buttons-level-item bot-button-leaf-removed" disabled>
                  ${escapeHtml(menuTypeTitle(item))}
                </button>
                <button type="button" class="action" data-restore-menu="${escapeHtml(item.menu_name)}">Восстановить</button>
              </div>
            `).join("")}
          </div>
        </div>` : ""}
      </div>
      <div class="bot-buttons-split-right" id="botButtonsRightPanel">
        ${renderRightPanel()}
      </div>
    </div>
  `;

  document.getElementById("createMenuTypeBtn")?.addEventListener("click", async () => {
    await createMenuType();
  });

  el.content.querySelectorAll("[data-menu-name]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!confirmDiscard()) return;
      discardEditorChanges();
      await openMenu(btn.dataset.menuName || "");
    });
  });
  el.content.querySelectorAll("[data-restore-menu]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const menuName = String(btn.dataset.restoreMenu || "").trim();
      if (!menuName) return;
      if (!window.confirm(`Восстановить меню "${menuName}"?`)) return;
      setStatus("Восстановление меню...");
      await api(`/api/v1/settings/bot-buttons/menu-types/${encodeURIComponent(menuName)}/restore`, {
        method: "POST",
      });
      await loadMenuTypes();
      setStatus("Меню восстановлено");
    });
  });
}

function menuTypeTitle(item) {
  const title = item.display_name || item.menu_name || "Меню";
  const destinations = Array.isArray(item.destinations) ? item.destinations : [];
  return `${title} (${destinations.length ? destinations.join(", ") : "не привязано"})`;
}

function selectedMenuInfo() {
  return (state.menuTypes || []).find((item) => item.menu_name === state.selectedMenu) || null;
}

function renderMenuBindingsPanel() {
  const current = currentLevel();
  if (!state.selectedMenu || String(current?.kind || "") !== "root") return "";
  const item = selectedMenuInfo();
  const destinations = Array.isArray(item?.destinations) ? item.destinations : [];
  const free = state.freeDestinationOptions || [];
  const canDeleteMenu = state.selectedMenu && state.selectedMenu !== "Базовое";
  return `
    <div class="bot-menu-bindings-panel">
      <div class="bot-menu-bindings-title">Привязанные типы клиентов:</div>
      <div class="bot-menu-bindings-list">
        ${destinations.length ? destinations.map((value) => `
          <span class="bot-menu-binding-chip">
            ${escapeHtml(value)}
            <button type="button" class="bot-menu-binding-remove" data-remove-destination="${escapeHtml(value)}">×</button>
          </span>
        `).join("") : `<span class="muted">не привязано</span>`}
      </div>
      <div class="bot-menu-bindings-actions">
        <select id="addDestinationSelect" class="input">
          <option value="">Свободный тип клиента</option>
          ${free.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join("")}
        </select>
        <button type="button" id="addDestinationBtn" class="action">Добавить тип клиента</button>
        ${canDeleteMenu ? '<button type="button" id="deleteMenuTypeBtn" class="action action-danger">Удалить меню</button>' : ""}
      </div>
    </div>
  `;
}

function renderLevel() {
  const current = state.levelStack[state.levelStack.length - 1];
  const isArchiveLevel = String(current?.levelKey || "").includes("|level|__removed__");
  const rows = current?.rows || [];
  const visibleRows = rows
    .filter((row) => !((row?.buttons || [])[0]?.kind === "removed_folder"))
    .map((row) => {
      const buttons = (row?.buttons || []).filter((node) => isArchiveLevel || !node?.removed);
      return { ...row, buttons };
    })
    .filter((row) => (row.buttons || []).length > 0);
  const removedCount = Array.isArray(state.menuStructure?.removed_items) ? state.menuStructure.removed_items.length : 0;
  const canEditLayout = isLayoutEditableLevel(current);
  const addButtonLabel = String(current?.kind || "") === "group_task_parent" ? "Добавить child task" : "Добавить";
  const toolbarHtml = canEditLayout
    ? `
      <div class="bot-layout-toolbar">
        ${state.layoutEditMode
          ? `<button type="button" id="saveLayoutBtn" class="action action-primary">Сохранить раскладку</button>
             <button type="button" id="cancelLayoutBtn" class="action">Отмена</button>`
          : `<button type="button" id="editLayoutBtn" class="action">Изменить порядок</button>
             <button type="button" id="addLevelItemBtn" class="action">${addButtonLabel}</button>`}
      </div>
    `
    : `<div class="bot-layout-toolbar"><button type="button" id="addLevelItemBtn" class="action">${addButtonLabel}</button></div>`;

  const rowsForRender = state.layoutEditMode ? (state.layoutDraftRows || []) : visibleRows;

  el.content.innerHTML = `
    <div class="bot-buttons-split-layout">
      <div class="bot-buttons-split-left">
        ${renderMenuBindingsPanel()}
        ${toolbarHtml}
        <div class="bot-buttons-level-list">
          ${rowsForRender.length ? rowsForRender.map((row, rowIndex) => `
            <div class="bot-buttons-row ${state.layoutEditMode ? "bot-layout-row-edit" : ""}">
              ${state.layoutEditMode ? `
                <div class="bot-layout-row-head">
                  <label>Ряд ${rowIndex + 1}</label>
                  <select class="input bot-layout-cols" data-layout-row="${rowIndex}">
                    <option value="1" ${Number(row.columns || 1) === 1 ? "selected" : ""}>1 кнопка в ряду</option>
                    <option value="2" ${Number(row.columns || 1) === 2 ? "selected" : ""}>2 кнопки в ряду</option>
                  </select>
                  <button type="button" class="action" data-layout-add-row="${rowIndex}">+ Ряд ниже</button>
                  <button type="button" class="action" data-layout-del-row="${rowIndex}">Удалить ряд</button>
                </div>
              ` : ""}
              ${(row.buttons || []).map((node, nodeIndex) => `
                <button
                  type="button"
                  class="${nodeClass(node)}"
                  data-row-index="${rowIndex}"
                  data-node-index="${nodeIndex}"
                >
                  ${escapeHtml(node.label)}
                </button>
                ${state.layoutEditMode ? `
                <div class="bot-layout-item-actions">
                  <button type="button" class="action" data-move-left="${rowIndex}:${nodeIndex}">←</button>
                  <button type="button" class="action" data-move-right="${rowIndex}:${nodeIndex}">→</button>
                  <button type="button" class="action" data-move-up="${rowIndex}:${nodeIndex}">↑ ряд</button>
                  <button type="button" class="action" data-move-down="${rowIndex}:${nodeIndex}">↓ ряд</button>
                </div>` : ""}
              `).join("")}
            </div>
          `).join("") : `<div class="status">Пусто</div>`}
        </div>
        ${!state.layoutEditMode && !isArchiveLevel && removedCount > 0 ? `
          <div class="bot-buttons-archive-bottom">
            <button type="button" id="openRemovedArchiveBtn" class="bot-buttons-level-item bot-button-leaf-removed bot-button-archive-main">
              Архив удалённых (${removedCount})
            </button>
          </div>
        ` : ""}
      </div>
      <div class="bot-buttons-split-right" id="botButtonsRightPanel">
        ${renderRightPanel()}
      </div>
    </div>
  `;

  el.content.querySelectorAll("[data-row-index][data-node-index]").forEach((btn) => {
    btn.addEventListener("mouseenter", () => {
      if (state.layoutEditMode) return;
      const row = rows[Number(btn.dataset.rowIndex)];
      const mappedRow = visibleRows[Number(btn.dataset.rowIndex)];
      const sourceRow = mappedRow || row;
      const node = sourceRow?.buttons?.[Number(btn.dataset.nodeIndex)];
      if (!node) return;
      state.preview = buildPreviewFromNode(node);
      const panel = document.getElementById("botButtonsRightPanel");
      if (panel) panel.innerHTML = renderRightPanel();
      loadPreviewItem(node);
    });
    btn.addEventListener("click", async () => {
      if (state.layoutEditMode) return;
      if (!confirmDiscard()) return;
      discardEditorChanges();
      const row = rows[Number(btn.dataset.rowIndex)];
      const node = row?.buttons?.[Number(btn.dataset.nodeIndex)];
      if (!node) return;
      state.preview = buildPreviewFromNode(node);
      {
        const panel = document.getElementById("botButtonsRightPanel");
        if (panel) panel.innerHTML = renderRightPanel();
      }
      loadPreviewItem(node);
      const nextRows = Array.isArray(node.children_rows) ? node.children_rows : [];
      if (nextRows.length > 0) {
        state.levelStack.push({ label: node.label, rows: nextRows, levelKey: node.level_key || null, fullPath: node.full_path || "", kind: node.kind || "" });
        render();
        return;
      }
      if (node.kind === "terminal") {
        await openEditor(node);
        return;
      }
    });
  });

  const editLayoutBtn = document.getElementById("editLayoutBtn");
  const addLevelItemBtn = document.getElementById("addLevelItemBtn");
  const cancelLayoutBtn = document.getElementById("cancelLayoutBtn");
  const saveLayoutBtn = document.getElementById("saveLayoutBtn");
  editLayoutBtn?.addEventListener("click", () => {
    state.layoutEditMode = true;
    state.layoutDraftRows = cloneRows(
      visibleRows.map((r) => ({ columns: Number(r.columns || 1) === 2 ? 2 : 1, buttons: r.buttons || [] })),
    );
    render();
  });
  addLevelItemBtn?.addEventListener("click", async () => {
    await createLevelItem();
  });
  cancelLayoutBtn?.addEventListener("click", () => {
    state.layoutEditMode = false;
    state.layoutDraftRows = [];
    render();
  });
  saveLayoutBtn?.addEventListener("click", async () => {
    const level = currentLevel();
    if (!level?.levelKey || !state.selectedMenu) return;
    for (const row of state.layoutDraftRows || []) {
      if ((row.buttons || []).length > 2) {
        alert("В одном ряду нельзя сохранить больше 2 кнопок.");
        return;
      }
    }
    const serializedRows = [];
    for (const row of state.layoutDraftRows || []) {
      const cols = Number(row.columns || 1) === 2 ? 2 : 1;
      const keys = (row.buttons || []).map((n) => n.technical_key).filter(Boolean);
      if (!keys.length) {
        continue;
      }
      if (cols === 1) {
        for (const key of keys) {
          serializedRows.push({ columns: 1, button_keys: [key] });
        }
      } else {
        for (let i = 0; i < keys.length; i += 2) {
          serializedRows.push({ columns: 2, button_keys: keys.slice(i, i + 2) });
        }
      }
    }
    const payload = { rows: serializedRows };
    setStatus("Сохранение раскладки...");
    await api(`/api/v1/settings/bot-buttons/layout?menu_type=${encodeURIComponent(state.selectedMenu)}&level_key=${encodeURIComponent(level.levelKey)}`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
    state.layoutEditMode = false;
    state.layoutDraftRows = [];
    await refreshMenuStructure();
    render();
    setStatus("Раскладка сохранена");
  });

  document.getElementById("addDestinationBtn")?.addEventListener("click", async () => {
    const select = document.getElementById("addDestinationSelect");
    const value = String(select?.value || "").trim();
    if (!value || !state.selectedMenu) return;
    await api(`/api/v1/settings/bot-buttons/menu-types/${encodeURIComponent(state.selectedMenu)}/destinations`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ destination: value }),
    });
    await reloadMenuMeta();
    render();
  });
  document.getElementById("deleteMenuTypeBtn")?.addEventListener("click", async () => {
    if (!state.selectedMenu) return;
    if (!window.confirm("Вы уверены что хотите удалить меню кнопок?")) return;
    if (!window.confirm("Вы ТОЧНО уверены что хотите удалить меню кнопок?")) return;
    setStatus("Удаление меню...");
    await api(`/api/v1/settings/bot-buttons/menu-types/${encodeURIComponent(state.selectedMenu)}`, {
      method: "DELETE",
    });
    await loadMenuTypes();
    setStatus("Меню удалено");
  });
  el.content.querySelectorAll("[data-remove-destination]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const value = btn.dataset.removeDestination || "";
      if (!value || !state.selectedMenu) return;
      await api(`/api/v1/settings/bot-buttons/menu-types/${encodeURIComponent(state.selectedMenu)}/destinations/${encodeURIComponent(value)}`, {
        method: "DELETE",
      });
      await reloadMenuMeta();
      render();
    });
  });

  if (state.layoutEditMode) {
    const draft = state.layoutDraftRows || [];
    el.content.querySelectorAll("[data-layout-row]").forEach((input) => {
      input.addEventListener("change", () => {
        const idx = Number(input.dataset.layoutRow);
        if (Number.isNaN(idx) || !draft[idx]) return;
        draft[idx].columns = Number(input.value) === 2 ? 2 : 1;
      });
    });
    el.content.querySelectorAll("[data-layout-add-row]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const idx = Number(btn.dataset.layoutAddRow);
        const at = Number.isNaN(idx) ? draft.length - 1 : idx;
        draft.splice(at + 1, 0, { columns: 1, buttons: [] });
        render();
      });
    });
    el.content.querySelectorAll("[data-layout-del-row]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const idx = Number(btn.dataset.layoutDelRow);
        if (Number.isNaN(idx) || !draft[idx]) return;
        if ((draft[idx].buttons || []).length > 0) {
          alert("Можно удалить только пустой ряд.");
          return;
        }
        draft.splice(idx, 1);
        if (!draft.length) draft.push({ columns: 1, buttons: [] });
        render();
      });
    });
    const cleanupEmptyRows = () => {
      for (let idx = draft.length - 1; idx >= 0; idx -= 1) {
        if ((draft[idx]?.buttons || []).length === 0) {
          draft.splice(idx, 1);
        }
      }
      if (!draft.length) draft.push({ columns: 1, buttons: [] });
    };
    const moveBetweenRows = (fromRow, fromIndex, toRow) => {
      if (!draft[fromRow] || !draft[toRow] || !draft[fromRow].buttons?.[fromIndex]) return;
      if ((draft[toRow].buttons || []).length >= 2) {
        alert("В целевом ряду уже 2 кнопки.");
        return;
      }
      const [item] = draft[fromRow].buttons.splice(fromIndex, 1);
      draft[toRow].buttons.push(item);
      cleanupEmptyRows();
      render();
    };
    const moveToOwnRow = (fromRow, fromIndex, direction) => {
      if (!draft[fromRow] || !draft[fromRow].buttons?.[fromIndex]) return;
      const [item] = draft[fromRow].buttons.splice(fromIndex, 1);
      const baseIndex = direction === "up" ? fromRow : fromRow + 1;
      const insertIndex = Math.max(0, Math.min(baseIndex, draft.length));
      draft.splice(insertIndex, 0, { columns: 1, buttons: [item] });
      cleanupEmptyRows();
      render();
    };
    const moveUpOrDown = (fromRow, fromIndex, direction) => {
      if (!draft[fromRow] || !draft[fromRow].buttons?.[fromIndex]) return;
      const targetRow = direction === "up" ? fromRow - 1 : fromRow + 1;
      if (targetRow >= 0 && targetRow < draft.length) {
        const target = draft[targetRow];
        const targetCols = Number(target?.columns || 1) === 2 ? 2 : 1;
        const targetLen = (target?.buttons || []).length;
        if (targetCols === 2 && targetLen < 2) {
          moveBetweenRows(fromRow, fromIndex, targetRow);
          return;
        }
      }
      moveToOwnRow(fromRow, fromIndex, direction);
    };
    el.content.querySelectorAll("[data-move-left]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const [r, i] = String(btn.dataset.moveLeft || "").split(":").map(Number);
        if (!draft[r] || Number.isNaN(i)) return;
        if (i <= 0) return;
        const arr = draft[r].buttons || [];
        [arr[i - 1], arr[i]] = [arr[i], arr[i - 1]];
        render();
      });
    });
    el.content.querySelectorAll("[data-move-right]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const [r, i] = String(btn.dataset.moveRight || "").split(":").map(Number);
        const arr = draft[r]?.buttons || [];
        if (i < 0 || i >= arr.length - 1) return;
        [arr[i + 1], arr[i]] = [arr[i], arr[i + 1]];
        render();
      });
    });
    el.content.querySelectorAll("[data-move-up]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const [r, i] = String(btn.dataset.moveUp || "").split(":").map(Number);
        if (!draft[r] || Number.isNaN(i)) return;
        moveUpOrDown(r, i, "up");
      });
    });
    el.content.querySelectorAll("[data-move-down]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const [r, i] = String(btn.dataset.moveDown || "").split(":").map(Number);
        if (!draft[r] || Number.isNaN(i)) return;
        moveUpOrDown(r, i, "down");
      });
    });
  }

  const archiveBtn = document.getElementById("openRemovedArchiveBtn");
  archiveBtn?.addEventListener("click", () => {
    const rowsForArchive = buildArchiveRowsFromStructure();
    state.levelStack.push({ label: "Архив удалённых", rows: rowsForArchive, levelKey: `${state.selectedMenu}|level|__removed__` });
    render();
  });

  const renameSectionBtn = document.getElementById("renameSectionBtn");
  const resetSectionLabelBtn = document.getElementById("resetSectionLabelBtn");
  renameSectionBtn?.addEventListener("click", async () => {
    const current = state.preview || {};
    const initial = (current.item?.button_label_effective || current.label || "").trim();
    const next = window.prompt("Название в боте", initial);
    if (next === null) return;
    await saveLabelOverrideForNode(current, next, true);
  });
  resetSectionLabelBtn?.addEventListener("click", async () => {
    const current = state.preview || {};
    if (!window.confirm("Сбросить переопределённое название и вернуть название из txt?")) return;
    await saveLabelOverrideForNode(current, "", false);
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
  const removedItems = Array.isArray(state.menuStructure?.removed_items) ? state.menuStructure.removed_items : [];
  return removedItems.map((leaf) => ({
    buttons: [{
      label: leaf.label,
      kind: "terminal",
      technical_key: leaf.technical_key,
      workflow_type: leaf.workflow_type,
      full_path: leaf.full_path,
      has_instruction: !!leaf.has_instruction,
      removed: true,
      is_enabled: true,
      is_db_created: !!leaf.is_db_created,
      is_archived: !!leaf.is_archived,
      children_rows: [],
    }],
  }));
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
  const options = ["<option value=''>Выберите пользователя</option>"];
  state.assignees.forEach((u) => {
    options.push(`<option value="${escapeHtml(u.id)}" ${u.id === state.fixedAssigneeUserId ? "selected" : ""}>${escapeHtml(u.full_name)} (${escapeHtml(u.role_label || u.role)})</option>`);
  });
  return options.join("");
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

function renderEditor() {
  const leaf = state.selectedLeaf;
  if (!leaf) return;
  const isRemoved = !!leaf.removed;
  const isDbCreated = !!leaf.is_db_created;
  const moveOptions = buildMoveCandidates()
    .filter((c) => c.key !== leaf.technical_key)
    .filter((c) => !leaf.workflow_type || c.workflowType === leaf.workflow_type)
    .map((c) => `<option value="${escapeHtml(c.key)}" data-path="${escapeHtml(c.path)}" data-workflow-type="${escapeHtml(c.workflowType)}">${escapeHtml(c.path)}</option>`)
    .join("");

  el.content.innerHTML = `
    <div class="panel settings-panel bot-instruction-editor">
      <div class="status">Technical key: <code>${escapeHtml(leaf.technical_key)}</code></div>
      ${isRemoved ? '<div class="bot-removed-warning">Эта кнопка отсутствует в текущей структуре и находится в блоке «Удалённые/изменённые».</div>' : ""}
      ${(!isRemoved && isDbCreated) ? '<div class="bot-editor-inline-actions"><button id="archiveButtonBtn" type="button" class="action action-danger">Удалить из меню</button></div>' : ""}

      <div class="bot-editor-grid">
        <div class="bot-editor-col bot-editor-col-left">
          <div class="bot-editor-section">
            <label class="field-label">Название в боте</label>
            <input id="botLabelInput" class="input" type="text" maxlength="255" value="${escapeHtml(state.editorBotLabel || "")}" placeholder="Введите название кнопки/раздела в боте" />
          </div>

          <div class="bot-editor-section">
            <div class="bot-removed-title">Параметры создания</div>
            <label class="status-panel-title admin-agent-header">
              <input id="buttonIsEnabled" type="checkbox" ${state.isEnabled ? "checked" : ""} />
              <span>Кнопка активна</span>
            </label>
            <label class="status-panel-title admin-agent-header">
              <input id="fixedAssigneeEnabled" type="checkbox" ${state.fixedAssigneeEnabled ? "checked" : ""} />
              <span>Фиксированный ответственный</span>
            </label>
            <select id="fixedAssigneeUserId" class="input" ${state.fixedAssigneeEnabled ? "" : "disabled"}>
              ${renderAssigneeOptions()}
            </select>
            <label class="status-panel-title admin-agent-header">
              <input id="freezeOnCreate" type="checkbox" ${state.freezeOnCreate ? "checked" : ""} />
              <span>Сразу замораживать при создании</span>
            </label>
          </div>

          <div class="bot-editor-section">
            <label class="field-label">Файлы, отправляемые при создании</label>
            <div id="botSelectedFilesWrap">${renderSelectedFiles()}</div>
            <div class="bot-file-actions">
              <button id="addBotFileBtn" type="button" class="action">Добавить файл</button>
              <button id="removeBotFileBtn" type="button" class="action">Удалить</button>
              <button id="addConditionalRuleBtn" type="button" class="action">+ Добавить условие</button>
            </div>
            <div id="conditionalRulesWrap">${renderConditionalRules()}</div>
            <div class="status">Источник файлов: ${state.filesOverridden ? "из БД (override)" : "fallback из txt"}</div>
            <div class="status">Источник условий: ${state.conditionalFilesOverridden ? "из БД (override)" : "fallback из txt"}</div>
            ${Array.isArray(leaf.children_rows) && leaf.children_rows.length ? '<button id="openNestedBtn" type="button" class="action">Открыть вложенные кнопки</button>' : ""}
          </div>

          <div class="bot-editor-section">
            <label class="field-label">Автоматизация</label>
            <select id="automationKeySelect" class="input">
              <option value="">Не использовать</option>
              ${(state.availableAutomations || []).map((a) => {
                const key = String(a?.key || "");
                const title = String(a?.title || key || "Без названия");
                return `<option value="${escapeHtml(key)}" ${state.automationKeyValue === key ? "selected" : ""}>${escapeHtml(title)} (${escapeHtml(key)})</option>`;
              }).join("")}
            </select>
            <div class="status">Название: ${escapeHtml(state.automationTitle || "не задано")}</div>
            <div class="status">Key: ${escapeHtml(state.automationKey || "не задано")}</div>
            <div class="status">Описание: ${escapeHtml(state.automationDescription || "не задано")}</div>
            <div class="status">Источник: настройки БД</div>
            <div class="status">Статус: ${escapeHtml(
              state.automationStatus === "not_found"
                ? "не найдена"
                : (state.automationStatus === "available"
                  ? (state.automationAvailable ? "доступна" : "в реестре, но недоступна")
                  : "не задана")
            )}</div>
            ${state.automationHandler ? `<div class="status">Handler: <code>${escapeHtml(state.automationHandler)}</code></div>` : ""}
          </div>
        </div>

        <div class="bot-editor-col bot-editor-col-right">
          <div class="bot-editor-section">
            <label class="field-label">Инструкция</label>
            <textarea id="botInstructionTextarea" class="input" rows="7" placeholder="Введите инструкцию для этой конечной кнопки...">${escapeHtml(state.editorText)}</textarea>
          </div>

          <div class="bot-editor-section">
            <label class="field-label">Текст клиенту</label>
            <textarea id="botClientTextTextarea" class="input" rows="4" placeholder="Сообщение, которое бот отправит клиенту">${escapeHtml(state.editorClientText)}</textarea>
            <div class="status">Источник текста клиенту: настройки БД</div>
          </div>

          <div class="bot-editor-section">
            <label class="field-label">Текст системный</label>
            <textarea id="botSystemTextTextarea" class="input" rows="5" placeholder="Системное сообщение для чата заявки/задачи">${escapeHtml(state.editorSystemText)}</textarea>
            <div class="status">Источник системного текста: ${state.systemTextOverridden ? "из БД (override)" : "fallback из txt"}</div>
          </div>

          <div class="bot-editor-section">
            <label class="status-panel-title admin-agent-header">
              <input id="confirmationEnabled" type="checkbox" ${state.confirmationEnabled ? "checked" : ""} />
              <span>Требовать подтверждение перед созданием</span>
            </label>
            <textarea id="confirmationText" class="input" rows="3" ${state.confirmationEnabled ? "" : "disabled"} placeholder="Текст подтверждения (если пусто — стандартный)">${escapeHtml(state.confirmationText)}</textarea>
          </div>
        </div>
      </div>

      ${isRemoved ? `
      <div class="bot-removed-actions">
        <div class="bot-removed-title">Удалённая запись</div>
        <div class="bot-removed-row">
          ${isDbCreated ? '<button id="restoreRemovedBtn" type="button" class="action action-primary">Восстановить</button>' : ''}
          <button id="deleteRemovedBtn" type="button" class="action action-danger">Удалить окончательно</button>
        </div>
        <div class="bot-removed-row">
          <select id="moveTargetSelect" class="input">
            <option value="">Выберите актуальную кнопку для переноса</option>
            ${moveOptions}
          </select>
          <button id="moveRemovedBtn" type="button" class="action">Перенести настройки</button>
        </div>
      </div>` : ""}
    </div>
  `;

  const textarea = document.getElementById("botInstructionTextarea");
  const botLabelInput = document.getElementById("botLabelInput");
  const botClientTextTextarea = document.getElementById("botClientTextTextarea");
  const systemTextarea = document.getElementById("botSystemTextTextarea");
  const fixedEnabledEl = document.getElementById("fixedAssigneeEnabled");
  const buttonIsEnabledEl = document.getElementById("buttonIsEnabled");
  const fixedUserEl = document.getElementById("fixedAssigneeUserId");
  const freezeOnCreateEl = document.getElementById("freezeOnCreate");
  const automationKeySelectEl = document.getElementById("automationKeySelect");
  const confirmationEnabledEl = document.getElementById("confirmationEnabled");
  const confirmationTextEl = document.getElementById("confirmationText");
  const addBotFileBtn = document.getElementById("addBotFileBtn");
  const removeBotFileBtn = document.getElementById("removeBotFileBtn");
  const addConditionalRuleBtn = document.getElementById("addConditionalRuleBtn");
  const openNestedBtn = document.getElementById("openNestedBtn");
  const saveBtn = document.getElementById("saveInstructionBtn");

  botLabelInput?.addEventListener("input", () => {
    state.editorBotLabel = botLabelInput.value;
  });
  botClientTextTextarea?.addEventListener("input", () => {
    state.editorClientText = botClientTextTextarea.value;
  });
  textarea?.addEventListener("input", () => {
    state.editorText = textarea.value;
  });
  systemTextarea?.addEventListener("input", () => {
    state.editorSystemText = systemTextarea.value;
    state.systemTextOverridden = true;
  });
  buttonIsEnabledEl?.addEventListener("change", () => {
    state.isEnabled = !!buttonIsEnabledEl.checked;
  });
  fixedEnabledEl?.addEventListener("change", () => {
    state.fixedAssigneeEnabled = !!fixedEnabledEl.checked;
    if (fixedUserEl) fixedUserEl.disabled = !state.fixedAssigneeEnabled;
    if (!state.fixedAssigneeEnabled) state.fixedAssigneeUserId = "";
  });
  fixedUserEl?.addEventListener("change", () => {
    state.fixedAssigneeUserId = fixedUserEl.value || "";
  });
  freezeOnCreateEl?.addEventListener("change", () => {
    state.freezeOnCreate = !!freezeOnCreateEl.checked;
  });
  automationKeySelectEl?.addEventListener("change", () => {
    state.automationKeyValue = String(automationKeySelectEl.value || "");
  });
  confirmationEnabledEl?.addEventListener("change", () => {
    state.confirmationEnabled = !!confirmationEnabledEl.checked;
    if (confirmationTextEl) confirmationTextEl.disabled = !state.confirmationEnabled;
  });
  confirmationTextEl?.addEventListener("input", () => {
    state.confirmationText = confirmationTextEl.value;
  });
  addBotFileBtn?.addEventListener("click", () => {
    openFilePickerModal();
  });
  removeBotFileBtn?.addEventListener("click", () => {
    const selected = Array.from(document.querySelectorAll(".bot-file-selected:checked")).map((input) => input.value);
    if (!selected.length) {
      alert("Выберите файлы в списке, которые хотите удалить.");
      return;
    }
    state.selectedFiles = (state.selectedFiles || []).filter((file) => !selected.includes(file));
    state.filesOverridden = true;
    renderEditor();
  });
  addConditionalRuleBtn?.addEventListener("click", () => {
    state.conditionalRules.push({
      conditions: [{ field: "", operator: "", value: "" }],
      files: [],
    });
    state.conditionalFilesOverridden = true;
    renderEditor();
  });
  openNestedBtn?.addEventListener("click", () => {
    if (!Array.isArray(leaf.children_rows) || !leaf.children_rows.length) return;
    state.levelStack.push({ label: leaf.label, rows: leaf.children_rows, levelKey: leaf.level_key || null, fullPath: leaf.full_path || "", kind: leaf.kind || "" });
    state.view = "level";
    render();
  });
  document.querySelectorAll("[data-remove-rule]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const idx = Number(btn.dataset.removeRule);
      if (Number.isNaN(idx)) return;
      state.conditionalRules.splice(idx, 1);
      state.conditionalFilesOverridden = true;
      renderEditor();
    });
  });
  document.querySelectorAll("[data-edit-rule-files]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const idx = Number(btn.dataset.editRuleFiles);
      if (Number.isNaN(idx)) return;
      openConditionalRuleFilesModal(idx);
    });
  });
  document.querySelectorAll("[data-cond-field]").forEach((input) => {
    input.addEventListener("change", () => {
      const [r, c] = String(input.dataset.condField || "").split(":").map((v) => Number(v));
      const rule = state.conditionalRules?.[r];
      const condition = rule?.conditions?.[c];
      if (!rule || !condition) return;
      condition.field = input.value;
      condition.value = "";
      if (!condition.field) {
        condition.operator = "";
      } else {
        const meta = fieldMeta(condition.field);
        const type = meta?.type || "text";
        if (type === "bool") {
          condition.operator = "eq";
        } else {
          const allowed = Array.isArray(meta?.operators) && meta.operators.length
            ? meta.operators
            : (OPERATOR_OPTIONS[type] || OPERATOR_OPTIONS.text).map((o) => o.value);
          if (!allowed.includes(condition.operator)) {
            condition.operator = "";
          }
        }
      }
      state.conditionalFilesOverridden = true;
      renderEditor();
    });
  });
  document.querySelectorAll("[data-cond-op]").forEach((input) => {
    input.addEventListener("change", () => {
      const [r, c] = String(input.dataset.condOp || "").split(":").map((v) => Number(v));
      const rule = state.conditionalRules?.[r];
      const condition = rule?.conditions?.[c];
      if (!rule || !condition) return;
      condition.operator = input.value;
      if (["empty", "not_empty"].includes(condition.operator)) {
        condition.value = "";
      }
      state.conditionalFilesOverridden = true;
      renderEditor();
    });
  });
  document.querySelectorAll("[data-cond-value]").forEach((input) => {
    const onValueChange = () => {
      const [r, c] = String(input.dataset.condValue || "").split(":").map((v) => Number(v));
      const rule = state.conditionalRules?.[r];
      const condition = rule?.conditions?.[c];
      if (!rule || !condition) return;
      condition.value = input.value;
      state.conditionalFilesOverridden = true;
    };
    input.addEventListener("input", onValueChange);
    input.addEventListener("change", onValueChange);
  });
  saveBtn?.addEventListener("click", async () => {
    await saveInstruction();
  });

  const archiveButtonBtn = document.getElementById("archiveButtonBtn");
  archiveButtonBtn?.addEventListener("click", async () => {
    const hasNested = Array.isArray(leaf.children_rows) && leaf.children_rows.length > 0;
    const message = hasNested
      ? "Будут скрыты также вложенные кнопки. Продолжить?"
      : "Элемент будет скрыт из меню, но настройки сохранятся. Продолжить?";
    if (!window.confirm(message)) return;
    await archiveMenuButton();
  });

  if (isRemoved) {
    const delBtn = document.getElementById("deleteRemovedBtn");
    const restoreBtn = document.getElementById("restoreRemovedBtn");
    const moveBtn = document.getElementById("moveRemovedBtn");
    const moveSelect = document.getElementById("moveTargetSelect");
    restoreBtn?.addEventListener("click", async () => {
      await restoreRemovedSetting();
    });
    delBtn?.addEventListener("click", async () => {
      if (!window.confirm("Удалить эту запись из удалённых/изменённых?")) return;
      await deleteRemovedSetting();
    });
    moveBtn?.addEventListener("click", async () => {
      const targetKey = moveSelect?.value || "";
      if (!targetKey) {
        alert("Сначала выберите кнопку для переноса.");
        return;
      }
      let warning = "Настройки старой кнопки будут перенесены на выбранную актуальную кнопку. Продолжить?";
      try {
        const targetItem = await api(`/api/v1/settings/bot-buttons/item?button_key=${encodeURIComponent(targetKey)}&menu_type=${encodeURIComponent(state.selectedMenu || "")}`);
        if (hasAnyStoredSettings(targetItem)) {
          warning = "У выбранной актуальной кнопки уже есть собственные настройки. Они будут перезаписаны. Продолжить?";
        }
      } catch (e) {
        console.warn("Не удалось проверить целевую кнопку перед переносом", e);
      }
      if (!window.confirm(warning)) return;
      const selected = moveSelect.options[moveSelect.selectedIndex];
      const targetPath = selected?.dataset?.path || "";
      const targetWorkflowType = selected?.dataset?.workflowType || "ticket";
      await moveRemovedSetting(targetKey, targetPath, targetWorkflowType);
    });
  }
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
  const [menuTypes, archivedMenuTypes, destinations] = await Promise.all([
    api("/api/v1/settings/bot-buttons/menu-types"),
    api("/api/v1/settings/bot-buttons/menu-types/archived"),
    api("/api/v1/settings/bot-buttons/destination-options"),
  ]);
  state.menuTypes = menuTypes;
  state.archivedMenuTypes = Array.isArray(archivedMenuTypes) ? archivedMenuTypes : [];
  state.destinationOptions = Array.isArray(destinations?.items) ? destinations.items : [];
  state.freeDestinationOptions = Array.isArray(destinations?.free) ? destinations.free : [];
}

async function createMenuType() {
  const payload = await openCreateMenuTypeModal();
  if (!payload) return;
  const result = await api("/api/v1/settings/bot-buttons/menu-types", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  await reloadMenuMeta();
  await openMenu(result.menu_name);
}

function openCreateMenuTypeModal() {
  return new Promise((resolve) => {
    const free = Array.isArray(state.freeDestinationOptions) ? state.freeDestinationOptions : [];
    const destinationHtml = free.length
      ? free.map((value) => `
          <label class="bot-create-menu-destination">
            <input type="checkbox" value="${escapeHtml(value)}" />
            <span>${escapeHtml(value)}</span>
          </label>
        `).join("")
      : `<div class="bot-create-item-note">Свободных типов клиента сейчас нет. Меню можно создать без привязки и привязать тип позже.</div>`;
    const wrapper = document.createElement("div");
    wrapper.innerHTML = `
      <div class="modal-overlay">
        <div class="modal-dialog bot-file-picker-dialog bot-create-item-dialog bot-create-menu-dialog">
          <div class="modal-header"><h3>Создание меню</h3></div>
          <div class="modal-body">
            <div class="bot-create-item-section">
              <div class="bot-create-item-label">Название меню</div>
              <input id="botCreateMenuNameInput" class="input" type="text" maxlength="120" placeholder="Например: Администрация" />
            </div>
            <div class="bot-create-item-section">
              <div class="bot-create-item-label">Привязанные типы клиентов</div>
              <div class="bot-create-menu-destinations">${destinationHtml}</div>
            </div>
            <div id="botCreateMenuError" class="status bot-create-menu-error"></div>
          </div>
          <div class="modal-footer">
            <button type="button" id="cancelCreateMenuBtn" class="action">Отмена</button>
            <button type="button" id="applyCreateMenuBtn" class="action action-primary">Создать</button>
          </div>
        </div>
      </div>`;
    const overlay = wrapper.firstElementChild;
    document.body.appendChild(overlay);

    const nameInput = overlay.querySelector("#botCreateMenuNameInput");
    const errorBox = overlay.querySelector("#botCreateMenuError");
    const close = (result) => {
      overlay.remove();
      resolve(result);
    };
    const showError = (text) => {
      if (errorBox) errorBox.textContent = text;
    };
    const apply = () => {
      const displayName = String(nameInput?.value || "").trim();
      if (!displayName) {
        showError("Введите название меню.");
        nameInput?.focus();
        return;
      }
      const destinations = Array.from(overlay.querySelectorAll(".bot-create-menu-destination input:checked"))
        .map((input) => String(input.value || "").trim())
        .filter(Boolean);
      close({ display_name: displayName, destinations });
    };

    overlay.querySelector("#cancelCreateMenuBtn")?.addEventListener("click", () => close(null));
    overlay.querySelector("#applyCreateMenuBtn")?.addEventListener("click", apply);
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) close(null);
    });
    nameInput?.addEventListener("keydown", (event) => {
      if (event.key === "Enter") apply();
    });
    nameInput?.focus();
  });
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
  setStatus(`Загрузка настройки "${leaf.label}"...`);
  const payload = await api(`/api/v1/settings/bot-buttons/item?button_key=${encodeURIComponent(leaf.technical_key)}&menu_type=${encodeURIComponent(state.selectedMenu || "")}`);
  state.selectedLeaf = {
    ...leaf,
    is_db_created: !!payload?.is_db_created,
    is_archived: !!payload?.is_archived,
  };

  state.savedText = String(payload?.instruction_text || "");
  state.editorText = state.savedText;
  state.savedClientText = String(payload?.client_text ?? payload?.client_text_effective ?? "");
  state.editorClientText = state.savedClientText;
  const effectiveLabel = String(payload?.button_label_effective || payload?.button_label || leaf.label || "");
  state.savedBotLabel = String(payload?.label_override ?? effectiveLabel);
  state.editorBotLabel = state.savedBotLabel;

  state.savedSystemText = String(payload?.system_text_effective || "");
  state.editorSystemText = state.savedSystemText;
  state.systemTextOverridden = !!payload?.system_text_overridden;

  state.savedFixedAssigneeEnabled = !!payload?.fixed_assignee_enabled;
  state.fixedAssigneeEnabled = state.savedFixedAssigneeEnabled;
  state.savedFixedAssigneeUserId = String(payload?.fixed_assignee_user_id || "");
  state.fixedAssigneeUserId = state.savedFixedAssigneeUserId;
  state.savedFreezeOnCreate = !!payload?.freeze_on_create;
  state.freezeOnCreate = state.savedFreezeOnCreate;

  state.savedConfirmationEnabled = !!payload?.confirmation_enabled;
  state.confirmationEnabled = state.savedConfirmationEnabled;
  state.savedConfirmationText = String(payload?.confirmation_text || "");
  state.confirmationText = state.savedConfirmationText;
  state.savedFilesOverridden = !!payload?.files_overridden;
  state.filesOverridden = state.savedFilesOverridden;
  const sourceFiles = state.savedFilesOverridden ? payload?.files : payload?.files_effective;
  state.savedSelectedFiles = Array.isArray(sourceFiles) ? sourceFiles : [];
  state.selectedFiles = [...state.savedSelectedFiles];
  state.savedConditionalFilesOverridden = !!payload?.conditional_files_overridden;
  state.conditionalFilesOverridden = state.savedConditionalFilesOverridden;
  const sourceConditional = state.savedConditionalFilesOverridden ? payload?.conditional_files : payload?.conditional_files_effective;
  const normalizedConditional = Array.isArray(sourceConditional) ? sourceConditional.map(normalizeConditionalRuleForState) : [];
  state.savedConditionalRules = normalizedConditional;
  state.conditionalRules = JSON.parse(JSON.stringify(normalizedConditional));
  state.savedIsEnabled = payload?.is_enabled !== false;
  state.isEnabled = state.savedIsEnabled;
  state.automationKey = String(payload?.automation_key_effective || "");
  state.automationTitle = String(payload?.automation_title || "");
  state.automationDescription = String(payload?.automation_description || "");
  state.automationFrom = String(payload?.automation_from || "none");
  state.automationStatus = String(payload?.automation_status || "not_set");
  state.automationAvailable = !!payload?.automation_available;
  state.automationHandler = String(payload?.automation_handler || "");
  state.savedAutomationKeyValue = String(payload?.automation_key ?? payload?.automation_key_effective ?? "");
  state.automationKeyValue = state.savedAutomationKeyValue;

  state.view = "editor";
  render();
  setStatus("Готово");
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
  const leaf = state.selectedLeaf;
  if (!leaf || !state.selectedMenu) return;
  if (!String(state.editorBotLabel || "").trim()) {
    alert("Название в боте не может быть пустым.");
    return;
  }

  if (state.fixedAssigneeEnabled && !state.fixedAssigneeUserId) {
    alert("Выберите фиксированного ответственного.");
    return;
  }

  let conditionalPayload = null;
  if (state.conditionalFilesOverridden) {
    const rules = JSON.parse(JSON.stringify(state.conditionalRules || []));
    for (let ridx = 0; ridx < rules.length; ridx += 1) {
      const rule = rules[ridx];
      const conditions = Array.isArray(rule.conditions) ? rule.conditions : [];
      if (!conditions.length) {
        alert(`Правило ${ridx + 1}: добавьте хотя бы одно условие.`);
        return;
      }
      for (let cidx = 0; cidx < conditions.length; cidx += 1) {
        const condition = conditions[cidx];
        const meta = fieldMeta(condition.field);
        const type = meta?.type || "text";
        if (condition.field && !meta) {
          alert(`Правило ${ridx + 1}: поле "${condition.field}" больше недоступно. Удалите или замените это условие.`);
          return;
        }
        if (!condition.field) {
          alert(`Правило ${ridx + 1}: выберите поле в условии ${cidx + 1}.`);
          return;
        }
        if (type !== "bool" && !condition.operator) {
          alert(`Правило ${ridx + 1}: выберите оператор в условии ${cidx + 1}.`);
          return;
        }
        if (type === "bool") {
          if (!["true", "false"].includes(String(condition.value || "").toLowerCase())) {
            alert(`Правило ${ridx + 1}: для поля "${fieldLabel(condition.field)}" выберите Да или Нет.`);
            return;
          }
          condition.operator = "eq";
          condition.value = String(condition.value || "").toLowerCase() === "true";
        } else if (["empty", "not_empty"].includes(condition.operator)) {
          condition.value = null;
        } else if (!String(condition.value || "").trim()) {
          alert(`Правило ${ridx + 1}: укажите значение в условии ${cidx + 1}.`);
          return;
        }
      }
      const files = Array.isArray(rule.files) ? rule.files : [];
      if (!files.length) {
        alert(`Правило ${ridx + 1}: выберите хотя бы один файл.`);
        return;
      }
    }
    conditionalPayload = rules;
  }

  setStatus("Сохранение...");
  await api(
    `/api/v1/settings/bot-buttons/item/instruction?button_key=${encodeURIComponent(leaf.technical_key)}&menu_type=${encodeURIComponent(state.selectedMenu)}&workflow_type=${encodeURIComponent(leaf.workflow_type)}&button_path=${encodeURIComponent(leaf.full_path || leaf.label)}&button_label=${encodeURIComponent(leaf.label)}`,
    {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        instruction_text: state.editorText,
        client_text_overridden: true,
        client_text: state.editorClientText,
        label_overridden: true,
        label_override: state.editorBotLabel,
        system_text: state.editorSystemText,
        system_text_overridden: true,
        fixed_assignee_enabled: state.fixedAssigneeEnabled,
        fixed_assignee_user_id: state.fixedAssigneeUserId || null,
        freeze_on_create: state.freezeOnCreate,
        confirmation_enabled: state.confirmationEnabled,
        confirmation_text: state.confirmationText,
        confirmation_overridden: true,
        files_overridden: state.filesOverridden,
        files: state.filesOverridden ? (state.selectedFiles || []) : null,
        conditional_files_overridden: state.conditionalFilesOverridden,
        conditional_files: conditionalPayload,
        automation_overridden: true,
        automation_key: state.automationKeyValue || null,
        is_enabled: state.isEnabled,
        visibility_overridden: true,
      }),
    },
  );

  state.savedText = state.editorText;
  state.savedClientText = state.editorClientText;
  state.savedBotLabel = state.editorBotLabel;
  state.savedSystemText = state.editorSystemText;
  state.systemTextOverridden = true;
  state.savedFixedAssigneeEnabled = state.fixedAssigneeEnabled;
  state.savedFixedAssigneeUserId = state.fixedAssigneeUserId;
  state.savedFreezeOnCreate = state.freezeOnCreate;
  state.savedConfirmationEnabled = state.confirmationEnabled;
  state.savedConfirmationText = state.confirmationText;
  state.savedFilesOverridden = state.filesOverridden;
  state.savedSelectedFiles = [...(state.selectedFiles || [])];
  state.savedConditionalFilesOverridden = state.conditionalFilesOverridden;
  state.savedConditionalRules = JSON.parse(JSON.stringify(state.conditionalRules || []));
  state.savedIsEnabled = state.isEnabled;
  state.savedAutomationKeyValue = state.automationKeyValue;

  await refreshMenuStructure();
  setStatus("Сохранено");
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
