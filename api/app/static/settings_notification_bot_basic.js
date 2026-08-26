(function () {
  function initNotificationBotBasicPage() {
    const apiBase = "/api/v1/settings/notification-bot/basic";
    const tg = window.TelegramTextEditor || {};
    const state = {
      items: [],
      activeType: "",
      drafts: new Map(),
      activeTextarea: null,
    };

    const el = {
      statusText: document.getElementById("statusText"),
      backToSettingsBtn: document.getElementById("backToSettingsBtn"),
      root: document.getElementById("notificationBotBasicRoot"),
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
      return {
        type: String(item.type || ""),
        title: String(item.title || ""),
        enabled: item.enabled !== false,
        template: String(item.template || ""),
        htmlEnabled: item.html_enabled !== false,
        disableLinkPreview: !!item.disable_link_preview,
        sendSilently: !!item.send_silently,
        tags: Array.isArray(item.tags) ? item.tags : [],
        isSaving: false,
        error: "",
      };
    }

    function getActiveDraft() {
      return state.drafts.get(state.activeType) || null;
    }

    function syncActiveDraftFromDom() {
      const draft = getActiveDraft();
      if (!draft) return;
      const pane = el.root?.querySelector("[data-notification-pane]");
      const textarea = pane?.querySelector("[data-template-textarea]");
      const enabled = pane?.querySelector("[data-enabled]");
      draft.template = String(textarea?.value || "");
      draft.enabled = !!enabled?.checked;
      draft.disableLinkPreview = !!pane?.querySelector("[data-telegram-disable-link-preview]")?.checked;
      draft.sendSilently = !!pane?.querySelector("[data-telegram-send-silently]")?.checked;
    }

    function validateDraft(draft) {
      if (!draft) return "Не выбран тип уведомления";
      if (draft.enabled && !String(draft.template || "").trim()) {
        return "Добавьте текст уведомления или отключите этот тип";
      }
      if (tg.validateTelegramHtml) {
        return tg.validateTelegramHtml(draft.template || "");
      }
      return "";
    }

    function renderTabs() {
      return `
        <div class="notification-tabs" role="tablist" aria-label="Типы уведомлений">
          ${state.items.map((item) => `
            <button
              type="button"
              class="notification-tab ${item.type === state.activeType ? "active" : ""}"
              data-tab-type="${escapeHtml(item.type)}"
              role="tab"
              aria-selected="${item.type === state.activeType ? "true" : "false"}"
            >${escapeHtml(item.title)}</button>
          `).join("")}
        </div>
      `;
    }

    function renderEditor(draft) {
      if (!draft) {
        return `<div class="status">Настройки уведомлений не найдены.</div>`;
      }
      return `
        <div class="notification-pane" data-notification-pane="${escapeHtml(draft.type)}">
          <label class="status-panel-title admin-agent-header notification-enabled-row">
            <input type="checkbox" data-enabled ${draft.enabled ? "checked" : ""} />
            <span>Отправлять уведомление этого типа</span>
          </label>
          <div class="notification-editor-grid">
            <div class="notification-editor-main">
              <label class="field-label" for="notificationTemplateTextarea">Конструктор текста</label>
              <textarea id="notificationTemplateTextarea" class="input notification-template-textarea" data-template-textarea>${escapeHtml(draft.template)}</textarea>
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
                ${draft.tags.map((tag) => `<button type="button" class="action notification-tag-btn" data-insert-tag="${escapeHtml(tag)}">${escapeHtml(tag)}</button>`).join("")}
              </div>
            </div>
          </div>
          <div class="bot-telegram-preview-wrap notification-preview-wrap">
            <div class="bot-telegram-preview-title">Предпросмотр Telegram</div>
            <div class="bot-telegram-preview" data-preview></div>
          </div>
          <div class="bot-info-actions notification-actions">
            <button type="button" class="action action-primary" data-save-type="${escapeHtml(draft.type)}" ${draft.isSaving ? "disabled" : ""}>Сохранить</button>
          </div>
        </div>
      `;
    }

    function bindEditor() {
      const draft = getActiveDraft();
      const pane = el.root?.querySelector("[data-notification-pane]");
      const textarea = pane?.querySelector("[data-template-textarea]");
      const preview = pane?.querySelector("[data-preview]");
      const validation = pane?.querySelector("[data-validation]");
      state.activeTextarea = textarea || null;

      if (tg.bindToolbar && textarea && pane) tg.bindToolbar(pane, textarea);
      const sync = () => {
        syncActiveDraftFromDom();
        if (preview && tg.renderTelegramPreviewHtml) preview.innerHTML = tg.renderTelegramPreviewHtml(draft?.template || "");
        const error = validateDraft(draft);
        if (validation) validation.textContent = error;
        if (draft) draft.error = error;
      };
      textarea?.addEventListener("input", sync);
      pane?.querySelector("[data-enabled]")?.addEventListener("change", sync);
      pane?.querySelector("[data-telegram-disable-link-preview]")?.addEventListener("change", sync);
      pane?.querySelector("[data-telegram-send-silently]")?.addEventListener("change", sync);
      pane?.querySelectorAll("[data-insert-tag]").forEach((button) => {
        button.addEventListener("click", () => {
          if (tg.insertTextAtSelection && textarea) {
            tg.insertTextAtSelection(textarea, button.getAttribute("data-insert-tag") || "");
            sync();
          }
        });
      });
      pane?.querySelector("[data-save-type]")?.addEventListener("click", saveActive);
      sync();
    }

    function render() {
      if (!el.root) return;
      el.root.innerHTML = `
        ${renderTabs()}
        ${renderEditor(getActiveDraft())}
      `;
      el.root.querySelectorAll("[data-tab-type]").forEach((button) => {
        button.addEventListener("click", () => {
          syncActiveDraftFromDom();
          state.activeType = button.getAttribute("data-tab-type") || state.activeType;
          render();
        });
      });
      bindEditor();
    }

    async function load() {
      setStatus("Загружаю настройки уведомлений...");
      const payload = await api(apiBase);
      state.items = Array.isArray(payload?.items) ? payload.items : [];
      state.drafts = new Map();
      state.items.forEach((item) => state.drafts.set(item.type, normalizeDraft(item)));
      state.activeType = state.items[0]?.type || "";
      render();
      setStatus("Готово");
    }

    async function saveActive() {
      syncActiveDraftFromDom();
      const draft = getActiveDraft();
      const error = validateDraft(draft);
      if (error) {
        draft.error = error;
        render();
        return;
      }
      draft.isSaving = true;
      render();
      setStatus("Сохранение...");
      try {
        const updated = await api(`${apiBase}/${encodeURIComponent(draft.type)}`, {
          method: "PUT",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            enabled: draft.enabled,
            template: draft.template,
            html_enabled: draft.htmlEnabled,
            disable_link_preview: draft.disableLinkPreview,
            send_silently: draft.sendSilently,
          }),
        });
        const normalized = normalizeDraft(updated);
        state.drafts.set(normalized.type, normalized);
        state.items = state.items.map((item) => item.type === normalized.type ? { ...item, ...updated } : item);
        render();
        setStatus("Сохранено");
      } catch (error) {
        draft.isSaving = false;
        draft.error = error.message || "Ошибка сохранения";
        render();
        setStatus("Ошибка сохранения");
      }
    }

    el.backToSettingsBtn?.addEventListener("click", () => {
      if (window.HelpdeskAppShell?.navigate) {
        window.HelpdeskAppShell.navigate("/settings");
        return;
      }
      window.location.href = "/settings";
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
    window.HelpdeskAppShell.registerPage("settings-notification-bot-basic", initNotificationBotBasicPage);
  } else {
    initNotificationBotBasicPage();
  }
})();
