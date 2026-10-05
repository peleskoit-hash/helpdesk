(function () {
  const PERSONAL_NOTIFICATION_TAB_CONFIG = {
    new_assignment: {
      title: "Новая заявка / задача",
      baseType: "new_assignment",
    },
    new_message: {
      title: "Новое сообщение",
      baseType: "new_message",
    },
    internal_task_completion: {
      title: "Выполнение внутренних задач",
      baseType: "internal_task_completion",
    },
  };

  const DELIVERY_SWITCH_COLUMNS = [
    [
      {
        key: "new_assignment",
        label: "Получать уведомления о новых заявках и задачах",
        mandatory: true,
      },
      {
        key: "new_message",
        label: "Получать уведомления о новых сообщениях и комментариях",
      },
      {
        key: "notify_internal_task_completion",
        label: "Выполнение внутренних задач",
      },
    ],
    [
      {
        key: "transfer_to_agent",
        label: "Получать уведомления, когда заявку передали мне",
      },
      {
        key: "transfer_from_agent",
        label: "Получать уведомления, когда мою заявку передали другому агенту",
      },
    ],
  ];

  function shouldSkipTemplateValidationForInheritanceTransition(options, config, draft) {
    return !!(
      options?.skipTemplateValidationForInheritanceTransitionType &&
      config?.baseType === options.skipTemplateValidationForInheritanceTransitionType &&
      draft &&
      draft.useBaseSettings === false
    );
  }

  function initProfileNotificationBotPage() {
    const apiBase = "/api/v1/me/notification-bot";
    const tg = window.TelegramTextEditor || {};
    const state = {
      items: [],
      activeType: "new_assignment",
      drafts: new Map(),
      delivery: {
        new_assignment: true,
        transfer_to_agent: true,
        transfer_from_agent: true,
        new_message: true,
        notify_internal_task_completion: true,
      },
      telegramBindingActive: false,
      activeTextarea: null,
      role: "",
      useBaseSettings: true,
    };

    const el = {
      root: document.getElementById("profileNotificationBotRoot"),
      statusText: document.getElementById("statusText"),
      backToTicketsBtn: document.getElementById("backToTicketsBtn"),
    };

    function setStatus(text) {
      if (el.statusText) el.statusText.textContent = text;
    }

    function escapeHtml(value) {
      if (tg.escapeHtml) return tg.escapeHtml(value);
      return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }

    async function api(path, options = {}) {
      const response = await fetch(path, {
        ...options,
        credentials: "include",
        headers: { accept: "application/json", ...(options.headers || {}) },
      });
      const raw = await response.text();
      let payload = null;
      if (raw) {
        try { payload = JSON.parse(raw); } catch { payload = null; }
      }
      if (!response.ok) throw new Error(payload?.detail?.error?.message || raw || "Ошибка запроса");
      return payload;
    }

    function normalizeDraft(item) {
      const config = PERSONAL_NOTIFICATION_TAB_CONFIG[item.type] || {};
      const tagItems = Array.isArray(item.tag_items) && item.tag_items.length
        ? item.tag_items.map((tag) => ({
          label: String(tag?.label || ""),
          description: String(tag?.description || ""),
        }))
        : (Array.isArray(item.tags) ? item.tags : []).map((tag) => ({
          label: String(tag || ""),
          description: "",
        }));
      return {
        type: String(item.type || ""),
        title: String(config.title || item.title || ""),
        useBaseSettings: item.use_base_settings !== false,
        template: String(item.template || ""),
        htmlEnabled: item.html_enabled !== false,
        disableLinkPreview: !!item.disable_link_preview,
        sendSilently: !!item.send_silently,
        tags: tagItems.map((tag) => tag.label),
        tagItems,
        error: "",
      };
    }

    function getActiveConfig() {
      return PERSONAL_NOTIFICATION_TAB_CONFIG[state.activeType] || null;
    }

    function getActiveDraft() {
      return state.drafts.get(state.activeType) || null;
    }

    function isAdminMode() {
      return state.role === "admin";
    }

    function visibleTabConfigs() {
      return Object.values(PERSONAL_NOTIFICATION_TAB_CONFIG).filter((config) => (
        isAdminMode()
          ? config.baseType === "internal_task_completion"
          : config.baseType !== "internal_task_completion"
      ));
    }

    function syncDeliveryFromDom() {
      el.root?.querySelectorAll("[data-delivery-switch]").forEach((input) => {
        const key = input.getAttribute("data-delivery-switch") || "";
        if (key === "new_assignment") {
          state.delivery.new_assignment = true;
          input.checked = true;
          return;
        }
        state.delivery[key] = !!input.checked;
      });
    }

    function syncDraft(config) {
      const draft = state.drafts.get(config.baseType);
      if (!draft) return;
      const pane = el.root?.querySelector(`[data-notification-pane="${config.baseType}"]`);
      if (!pane) return;
      const textarea = pane.querySelector("[data-template-textarea]");
      draft.template = String(textarea?.value || "");
      draft.disableLinkPreview = !!pane.querySelector("[data-telegram-disable-link-preview]")?.checked;
      draft.sendSilently = !!pane.querySelector("[data-telegram-send-silently]")?.checked;
    }

    function validateTab(config, options = {}) {
      const draft = state.drafts.get(config.baseType);
      if (!draft) return "Не выбран тип уведомления";
      if (
        !draft.useBaseSettings &&
        !shouldSkipTemplateValidationForInheritanceTransition(options, config, draft) &&
        !String(draft.template || "").trim()
      ) {
        return "Добавьте текст уведомления";
      }
      if (!draft.useBaseSettings && tg.validateTelegramHtml) {
        return tg.validateTelegramHtml(draft.template || "");
      }
      return "";
    }

    function renderDeliverySwitches() {
      const columns = isAdminMode()
        ? [[DELIVERY_SWITCH_COLUMNS.flat().find((item) => item.key === "notify_internal_task_completion")].filter(Boolean)]
        : DELIVERY_SWITCH_COLUMNS;
      return `
        <div class="notification-control-card notification-delivery-card ${isAdminMode() ? "notification-admin-compact" : ""}">
          <div class="notification-delivery-grid">
            ${columns.map((column) => `
              <div class="notification-delivery-column">
                ${column.map((item) => {
                  const checked = item.mandatory ? true : state.delivery[item.key] !== false;
                  const disabled = item.mandatory ? "disabled" : "";
                  return `
                    <label class="checkbox-label notification-enabled-row notification-delivery-row ${item.mandatory ? "notification-switch-disabled" : ""}">
                      <input type="checkbox" data-delivery-switch="${escapeHtml(item.key)}" ${checked ? "checked" : ""} ${disabled} />
                      <span>${escapeHtml(item.label)}</span>
                    </label>
                  `;
                }).join("")}
              </div>
            `).join("")}
          </div>
        </div>
      `;
    }

    function renderModeControls(draft) {
      const restoreDisabled = !draft || draft.useBaseSettings;
      const checked = draft?.useBaseSettings !== false;
      return `
        <div class="notification-control-card notification-page-mode ${isAdminMode() ? "notification-admin-compact" : ""}">
          <label class="checkbox-label notification-enabled-row">
            <input type="checkbox" data-use-base-settings ${checked ? "checked" : ""} />
            <span>Использовать базовые настройки уведомлений</span>
          </label>
          ${isAdminMode() ? "" : `<button type="button" class="action" data-restore-base ${restoreDisabled ? "disabled" : ""}>Восстановить базовые настройки</button>`}
        </div>
      `;
    }

    function renderTabs() {
      const tabs = visibleTabConfigs();
      if (isAdminMode() && tabs.length <= 1) return "";
      return `
        <div class="notification-tabs notification-tabs-personal" role="tablist" aria-label="Персональные уведомления">
          ${tabs.map((config) => `
            <button
              type="button"
              class="notification-tab ${config.baseType === state.activeType ? "active" : ""}"
              data-tab-type="${escapeHtml(config.baseType)}"
              role="tab"
              aria-selected="${config.baseType === state.activeType ? "true" : "false"}"
            >${escapeHtml(config.title)}</button>
          `).join("")}
        </div>
      `;
    }

    function renderTab(config) {
      const draft = state.drafts.get(config.baseType);
      if (!draft) return `<div class="status">Настройки уведомлений не найдены.</div>`;
      const readonly = draft.useBaseSettings;
      return `
        <div class="notification-pane" data-notification-pane="${escapeHtml(config.baseType)}">
          <div class="notification-editor-grid">
            <div class="notification-editor-main">
              <label class="field-label" for="profileNotificationTemplateTextarea">Конструктор текста</label>
              <textarea id="profileNotificationTemplateTextarea" class="input notification-template-textarea" data-template-textarea ${readonly ? "readonly disabled" : ""}>${escapeHtml(draft.template)}</textarea>
              ${tg.toolbarHtml ? tg.toolbarHtml() : ""}
              ${tg.settingsHtml ? tg.settingsHtml({
                disableLinkPreview: draft.disableLinkPreview,
                sendSilently: draft.sendSilently,
              }) : ""}
              <div class="notification-validation" data-validation>${escapeHtml(draft.error || "")}</div>
            </div>
            <div class="notification-tags">
              <div class="field-label">Доступные теги</div>
              <div class="notification-tag-list">
                ${draft.tagItems.map((tag) => `
                  <button
                    type="button"
                    class="action notification-tag-btn"
                    data-insert-tag="${escapeHtml(tag.label)}"
                    data-tooltip="${escapeHtml(tag.description)}"
                    aria-label="${escapeHtml(tag.description ? `${tag.label}: ${tag.description}` : tag.label)}"
                    ${readonly ? "disabled" : ""}
                  >${escapeHtml(tag.label)}</button>
                `).join("")}
              </div>
            </div>
          </div>
          <div class="bot-telegram-preview-wrap notification-preview-wrap">
            <div class="bot-telegram-preview-title">Предпросмотр Telegram</div>
            <div class="bot-telegram-preview" data-preview></div>
          </div>
        </div>
      `;
    }

    function bindTab(config) {
      const draft = state.drafts.get(config.baseType);
      const pane = el.root?.querySelector(`[data-notification-pane="${config.baseType}"]`);
      const textarea = pane?.querySelector("[data-template-textarea]");
      const preview = pane?.querySelector("[data-preview]");
      const validation = pane?.querySelector("[data-validation]");
      state.activeTextarea = textarea || null;

      if (!draft?.useBaseSettings && tg.bindAtomicTokens && textarea) tg.bindAtomicTokens(textarea, draft.tags);
      if (!draft?.useBaseSettings && tg.bindToolbar && textarea && pane) tg.bindToolbar(pane, textarea, { tags: draft.tags });
      const sync = () => {
        syncDraft(config);
        if (preview && tg.renderTelegramPreviewHtml) preview.innerHTML = tg.renderTelegramPreviewHtml(draft?.template || "");
        const error = validateTab(config);
        if (validation) validation.textContent = error;
        if (draft) draft.error = error;
      };
      textarea?.addEventListener("input", sync);
      pane?.querySelector("[data-telegram-disable-link-preview]")?.addEventListener("change", sync);
      pane?.querySelector("[data-telegram-send-silently]")?.addEventListener("change", sync);
      pane?.querySelectorAll("[data-insert-tag]").forEach((button) => {
        button.addEventListener("click", () => {
          if (draft?.useBaseSettings) return;
          if (tg.insertTextAtSelection && textarea) {
            tg.insertTextAtSelection(textarea, button.getAttribute("data-insert-tag") || "");
            sync();
          }
        });
      });
      if (draft?.useBaseSettings) {
        pane?.querySelectorAll("[data-telegram-disable-link-preview], [data-telegram-send-silently]").forEach((input) => {
          input.disabled = true;
        });
        pane?.querySelectorAll("[data-telegram-toolbar] button").forEach((button) => {
          button.disabled = true;
        });
      }
      sync();
    }

    function syncAllDrafts() {
      visibleTabConfigs().forEach(syncDraft);
      syncDeliveryFromDom();
    }

    function render() {
      if (!el.root) return;
      const config = getActiveConfig();
      const draft = getActiveDraft();
      el.root.innerHTML = `
        ${renderModeControls(draft)}
        ${renderDeliverySwitches()}
        ${renderTabs()}
        ${config ? renderTab(config) : ""}
        <div class="notification-actions notification-actions-with-binding">
          <div class="notification-binding-status">
            ${state.telegramBindingActive ? "ТГ-привязка активна" : "ТГ-привязка неактивна"}
          </div>
          <button type="button" class="action action-primary" data-save>Сохранить</button>
        </div>
      `;
      el.root.querySelectorAll("[data-delivery-switch]").forEach((input) => input.addEventListener("change", syncDeliveryFromDom));
      el.root.querySelectorAll("[data-tab-type]").forEach((button) => {
        button.addEventListener("click", () => {
          const current = getActiveConfig();
          if (current) syncDraft(current);
          syncDeliveryFromDom();
          state.activeType = button.getAttribute("data-tab-type") || state.activeType;
          render();
        });
      });
      el.root.querySelector("[data-use-base-settings]")?.addEventListener("change", async (event) => {
        const current = getActiveConfig();
        const activeDraft = getActiveDraft();
        if (current) syncDraft(current);
        syncDeliveryFromDom();
        const nextUseBaseSettings = !!event.target.checked;
        if (isAdminMode()) {
          const skipTemplateValidationForInheritanceTransitionType = activeDraft?.useBaseSettings && !nextUseBaseSettings
            ? current?.baseType || state.activeType
            : "";
          if (activeDraft) activeDraft.useBaseSettings = nextUseBaseSettings;
          await save({
            quiet: true,
            skipTemplateValidationForInheritanceTransitionType,
          });
          return;
        }
        const skipTemplateValidationForInheritanceTransitionType = activeDraft?.useBaseSettings && !nextUseBaseSettings
          ? current?.baseType || state.activeType
          : "";
        if (activeDraft) activeDraft.useBaseSettings = nextUseBaseSettings;
        await save({
          quiet: true,
          skipTemplateValidationForInheritanceTransitionType,
        });
      });
      el.root.querySelector("[data-save]")?.addEventListener("click", () => save());
      el.root.querySelector("[data-restore-base]")?.addEventListener("click", restoreBase);
      if (config) bindTab(config);
    }

    function applyPayload(payload) {
      state.delivery = {
        new_assignment: true,
        transfer_to_agent: payload?.delivery?.transfer_to_agent !== false,
        transfer_from_agent: payload?.delivery?.transfer_from_agent !== false,
        new_message: payload?.delivery?.new_message !== false,
        notify_internal_task_completion: payload?.delivery?.notify_internal_task_completion !== false,
      };
      state.role = String(payload?.role || "");
      state.useBaseSettings = payload?.use_base_settings !== false;
      state.telegramBindingActive = !!payload?.telegram_binding?.active;
      state.items = Array.isArray(payload?.items) ? payload.items : [];
      state.drafts = new Map();
      state.items.forEach((item) => {
        if (PERSONAL_NOTIFICATION_TAB_CONFIG[item.type]) {
          state.drafts.set(item.type, normalizeDraft(item));
        }
      });
      if (!state.drafts.has(state.activeType)) {
        state.activeType = state.drafts.keys().next().value || "new_assignment";
      }
    }

    function validateBeforeSave(options = {}) {
      for (const config of visibleTabConfigs()) {
        const error = validateTab(config, options);
        const draft = state.drafts.get(config.baseType);
        if (draft) draft.error = error;
        if (error) {
          state.activeType = config.baseType;
          return error;
        }
      }
      return "";
    }

    function requestPayload() {
      return {
        items: visibleTabConfigs().map((config) => {
          const draft = state.drafts.get(config.baseType);
          return {
            type: config.baseType,
            use_base_settings: draft?.useBaseSettings !== false,
            template: draft?.template || "",
            html_enabled: draft?.htmlEnabled !== false,
            disable_link_preview: !!draft?.disableLinkPreview,
            send_silently: !!draft?.sendSilently,
          };
        }),
        use_base_settings: getActiveDraft()?.useBaseSettings !== false,
        delivery: {
          transfer_to_agent: state.delivery.transfer_to_agent !== false,
          transfer_from_agent: state.delivery.transfer_from_agent !== false,
          new_message: state.delivery.new_message !== false,
          notify_internal_task_completion: state.delivery.notify_internal_task_completion !== false,
        },
      };
    }

    async function load() {
      setStatus("Загружаю настройки уведомлений...");
      const payload = await api(apiBase);
      applyPayload(payload);
      render();
      setStatus("Готово");
    }

    async function save(options = {}) {
      syncAllDrafts();
      const error = validateBeforeSave(options);
      if (error) {
        render();
        setStatus(error);
        return;
      }
      setStatus(options.quiet ? "Обновляю режим..." : "Сохранение...");
      try {
        const payload = await api(apiBase, {
          method: "PUT",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(requestPayload()),
        });
        applyPayload(payload);
        render();
        setStatus("Сохранено");
      } catch (error) {
        setStatus(`Ошибка: ${error.message || "не удалось сохранить"}`);
        render();
      }
    }

    async function restoreBase() {
      const activeType = state.activeType;
      if (!window.confirm("Текущий персональный текст будет заменён актуальной базовой настройкой. Продолжить?")) {
        return;
      }
      setStatus("Восстанавливаю базовые настройки...");
      try {
        const payload = await api(`${apiBase}/restore-base/${encodeURIComponent(activeType)}`, { method: "POST" });
        applyPayload(payload);
        state.activeType = activeType;
        render();
        setStatus("Базовые настройки скопированы");
      } catch (error) {
        setStatus(`Ошибка: ${error.message || "не удалось восстановить"}`);
      }
    }

    el.backToTicketsBtn?.addEventListener("click", () => {
      if (window.HelpdeskAppShell?.navigate) {
        window.HelpdeskAppShell.navigate("/tickets");
        return;
      }
      window.location.href = "/tickets";
    });

    window.HelpdeskSidebar.init({ onError: (error) => setStatus(`Ошибка: ${error.message}`) })
      .then(load)
      .catch((error) => {
        setStatus(`Ошибка: ${error.message}`);
      });

    return () => {
      if (tg.closeEmojiPicker) tg.closeEmojiPicker();
    };
  }

  if (window.HelpdeskAppShell?.registerPage) {
    window.HelpdeskAppShell.registerPage("profile-notification-bot", initProfileNotificationBotPage);
  } else {
    initProfileNotificationBotPage();
  }
})();
