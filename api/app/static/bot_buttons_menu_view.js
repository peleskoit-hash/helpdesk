(function initBotButtonsMenuView(global) {
  const MenuView = {};
  const SYSTEM_TICKETS_TITLE = "\u0421\u0438\u0441\u0442\u0435\u043c\u043d\u044b\u0435 \u0437\u0430\u044f\u0432\u043a\u0438";
  const FIRST_CONNECTION_TITLE = "\u041f\u0435\u0440\u0432\u043e\u0435 \u043f\u043e\u0434\u043a\u043b\u044e\u0447\u0435\u043d\u0438\u0435 \u043a \u0431\u043e\u0442\u0443";

  MenuView.buildPreviewFromNode = function buildPreviewFromNode(ctx, node) {
    const { state } = ctx;
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
  };

  MenuView.loadPreviewItem = async function loadPreviewItem(ctx, node) {
    const { state, api, renderRightPanel } = ctx;
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
  };

  MenuView.renderRightPanel = function renderRightPanel(ctx) {
    const { state, escapeHtml, nodeKindLabel, renderInfoRow, safeText, yesNo } = ctx;
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
  };

  MenuView.menuTypeTitle = function menuTypeTitle(item) {
    const title = item.display_name || item.menu_name || "Меню";
    const destinations = Array.isArray(item.destinations) ? item.destinations : [];
    return `${title} (${destinations.length ? destinations.join(", ") : "не привязано"})`;
  };

  MenuView.isSystemMenuTypeRecord = function isSystemMenuTypeRecord(item) {
    const menuName = String(item?.menu_name || "").trim();
    return !!menuName && menuName.startsWith("__system__");
  };

  MenuView.selectedMenuInfo = function selectedMenuInfo(ctx) {
    const { state } = ctx;
    return (state.menuTypes || []).find((item) => item.menu_name === state.selectedMenu) || null;
  };

  MenuView.renderMenuBindingsPanel = function renderMenuBindingsPanel(ctx) {
    const { state, currentLevel, escapeHtml } = ctx;
    const current = currentLevel();
    if (!state.selectedMenu || String(current?.kind || "") !== "root") return "";
    const item = MenuView.selectedMenuInfo(ctx);
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
  };

  MenuView.reloadMenuMeta = async function reloadMenuMeta(ctx) {
    const { state, api } = ctx;
    const [menuTypes, archivedMenuTypes, destinations] = await Promise.all([
      api("/api/v1/settings/bot-buttons/menu-types"),
      api("/api/v1/settings/bot-buttons/menu-types/archived"),
      api("/api/v1/settings/bot-buttons/destination-options"),
    ]);
    state.menuTypes = menuTypes;
    state.archivedMenuTypes = Array.isArray(archivedMenuTypes) ? archivedMenuTypes : [];
    state.destinationOptions = Array.isArray(destinations?.items) ? destinations.items : [];
    state.freeDestinationOptions = Array.isArray(destinations?.free) ? destinations.free : [];
  };

  MenuView.openCreateMenuTypeModal = function openCreateMenuTypeModal(ctx) {
    const { state, escapeHtml } = ctx;
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
  };

  MenuView.createMenuType = async function createMenuType(ctx) {
    const { api, openMenu } = ctx;
    const payload = await MenuView.openCreateMenuTypeModal(ctx);
    if (!payload) return;
    const result = await api("/api/v1/settings/bot-buttons/menu-types", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
    await MenuView.reloadMenuMeta(ctx);
    await openMenu(result.menu_name);
  };

  MenuView.renderMenuTypes = function renderMenuTypes(ctx) {
    const { state, el, escapeHtml, renderRightPanel, confirmDiscard, discardEditorChanges, openMenu, api, setStatus, loadMenuTypes, systemTicketLoadError } = ctx;
    const items = (state.menuTypes || []).filter((item) => !MenuView.isSystemMenuTypeRecord(item));
    const archived = (state.archivedMenuTypes || []).filter((item) => !MenuView.isSystemMenuTypeRecord(item));
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
                ${escapeHtml(MenuView.menuTypeTitle(item))}
              </button>
            `).join("")}
          </div>
          ${archived.length ? `
          <div class="bot-buttons-archive-bottom">
            <div class="bot-menu-bindings-title">Архив меню</div>
            ${systemTicketLoadError ? `<div class="status">${escapeHtml(systemTicketLoadError)}</div>` : ""}
            <div class="bot-buttons-level-list">
              ${archived.map((item) => `
                <div class="bot-removed-row">
                  <button type="button" class="bot-buttons-level-item bot-button-leaf-removed" disabled>
                    ${escapeHtml(MenuView.menuTypeTitle(item))}
                  </button>
                  <button type="button" class="action" data-restore-menu="${escapeHtml(item.menu_name)}">Восстановить</button>
                </div>
              `).join("")}
            </div>
          </div>` : ""}
          <div class="bot-buttons-archive-bottom">
            <div class="bot-menu-bindings-title">${SYSTEM_TICKETS_TITLE}</div>
            ${systemTicketLoadError ? `<div class="status">${escapeHtml(systemTicketLoadError)}</div>` : ""}
            <div class="bot-buttons-level-list">
              <button type="button" id="openFirstConnectionSystemBtn" data-system-ticket-key="first-connection" class="bot-buttons-level-item">
                ${FIRST_CONNECTION_TITLE}
              </button>
            </div>
          </div>
        </div>
        <div class="bot-buttons-split-right" id="botButtonsRightPanel">
          ${renderRightPanel()}
        </div>
      </div>
    `;

    document.getElementById("createMenuTypeBtn")?.addEventListener("click", async () => {
      await MenuView.createMenuType(ctx);
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
  };

  global.BotButtonsMenuView = MenuView;
  global.BotButtonsMenuViewSystemText = {
    SYSTEM_TICKETS_TITLE,
    FIRST_CONNECTION_TITLE,
  };
})(window);
