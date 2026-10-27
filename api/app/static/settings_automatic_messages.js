(function () {
  const TEXT_MODAL_ID = "automaticMessageEditorModal";
  const EMOJI_PICKER_ID = "automaticMessageEmojiPicker";
  const VARIABLE_PICKER_ID = "automaticMessageVariablePicker";
  const FALLBACK_EMOJI_CATEGORIES = [
    { id: "smileys", label: "Смайлы", emojis: ["🙂", "😀", "😁", "😂", "😊", "😍", "😎", "😢", "😡", "👍"] },
    { id: "gestures", label: "Жесты", emojis: ["👍", "👎", "👏", "🙏", "👌", "✌️", "🤝", "💪", "👋", "☝️"] },
    { id: "symbols", label: "Символы", emojis: ["✅", "❌", "⚠️", "ℹ️", "⭐", "🔥", "💬", "📌", "➡️", "⬅️"] },
  ];

  function initAutomaticMessagesPage() {
    const state = {
      categories: [],
      openCategories: new Set(),
      itemsByKey: new Map(),
      activeTemplate: null,
      activeDraft: "",
      emojiOutsideClickHandler: null,
      variableOutsideClickHandler: null,
      modalEscHandler: null,
    };

    const el = {
      backToSettingsBtn: document.getElementById("backToSettingsBtn"),
      automaticMessagesRoot: document.getElementById("automaticMessagesRoot"),
      emptyAutomaticMessagesText: document.getElementById("emptyAutomaticMessagesText"),
      errorModal: document.getElementById("errorModal"),
      errorText: document.getElementById("errorText"),
      closeErrorModalBtn: document.getElementById("closeErrorModalBtn"),
      copyErrorBtn: document.getElementById("copyErrorBtn"),
      okErrorBtn: document.getElementById("okErrorBtn"),
    };

    function setStatus() {}

    function escapeHtml(value) {
      return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }

    function sanitizeLinkHref(rawHref) {
      const href = String(rawHref || "").trim();
      if (!href) return "";
      try {
        const parsed = new URL(href, window.location.origin);
        const protocol = String(parsed.protocol || "").toLowerCase();
        if (!["http:", "https:", "tg:", "mailto:"].includes(protocol)) return "";
        return href;
      } catch (_err) {
        return "";
      }
    }

    function stripAllowedTelegramTags(text) {
      return String(text || "").replace(/<\/?(?:b|strong|i|em|u|ins|s|strike|del|tg-spoiler|code|pre|blockquote|a)(?:\s+[^>]*)?>/gi, "");
    }

    function renderTelegramPreviewHtml(text) {
      const parser = new DOMParser();
      const doc = parser.parseFromString(`<div>${String(text || "")}</div>`, "text/html");
      const root = doc.body.firstElementChild || doc.body;
      const allowed = new Set(["b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "tg-spoiler", "code", "pre", "blockquote", "a"]);

      function walk(node) {
        if (node.nodeType === Node.TEXT_NODE) return document.createTextNode(node.textContent || "");
        if (node.nodeType !== Node.ELEMENT_NODE) return document.createTextNode("");
        const tag = String(node.tagName || "").toLowerCase();
        if (!allowed.has(tag)) {
          const fragment = document.createDocumentFragment();
          node.childNodes.forEach((child) => fragment.appendChild(walk(child)));
          return fragment;
        }
        let out;
        if (tag === "tg-spoiler") {
          out = document.createElement("span");
          out.className = "tg-preview-spoiler";
        } else if (tag === "strong") out = document.createElement("b");
        else if (tag === "em") out = document.createElement("i");
        else if (tag === "ins") out = document.createElement("u");
        else if (tag === "strike" || tag === "del") out = document.createElement("s");
        else out = document.createElement(tag);
        if (tag === "a") {
          const href = sanitizeLinkHref(node.getAttribute("href") || "");
          if (href) {
            out.setAttribute("href", href);
            out.setAttribute("target", "_blank");
            out.setAttribute("rel", "noopener noreferrer");
          }
        }
        if (tag === "blockquote" && node.hasAttribute("expandable")) out.classList.add("tg-preview-expandable");
        node.childNodes.forEach((child) => out.appendChild(walk(child)));
        return out;
      }

      const previewRoot = document.createElement("div");
      root.childNodes.forEach((child) => previewRoot.appendChild(walk(child)));
      return previewRoot.innerHTML || "Текст не задан";
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
      if (!response.ok) {
        const message = payload?.detail?.error?.message || raw || "Ошибка запроса";
        const error = new Error(message);
        error.status = response.status;
        throw error;
      }
      return payload;
    }

    function showError(message) {
      if (!el.errorText || !el.errorModal) return;
      el.errorText.value = message || "Неизвестная ошибка";
      el.errorModal.classList.remove("hidden");
    }

    function closeError() {
      el.errorModal?.classList.add("hidden");
    }

    function closeOnBackdropClick(modal, closeHandler) {
      if (!modal) return;
      let down = false;
      modal.addEventListener("pointerdown", (event) => { down = event.target === modal; });
      modal.addEventListener("click", (event) => {
        if (down && event.target === modal) closeHandler();
        down = false;
      });
    }

    function getEmojiCategories() {
      const source = Array.isArray(window.BotButtonsEmojiData) && window.BotButtonsEmojiData.length
        ? window.BotButtonsEmojiData
        : FALLBACK_EMOJI_CATEGORIES;
      return source.map((category, index) => ({
        id: String(category.id || `emoji-${index}`),
        label: String(category.label || category.title || `Категория ${index + 1}`),
        emojis: Array.isArray(category.emojis) ? category.emojis : [],
      })).filter((category) => category.emojis.length);
    }

    function renderEmojiPickerHtml() {
      const categories = getEmojiCategories();
      return `
        <div id="${EMOJI_PICKER_ID}" class="bot-emoji-picker" role="dialog" aria-label="Emoji">
          <div class="bot-emoji-tabs" role="tablist" aria-label="Категории emoji">
            ${categories.map((category, index) => `
              <button type="button" class="bot-emoji-tab ${index === 0 ? "active" : ""}" data-emoji-category-index="${index}">${escapeHtml(category.label)}</button>
            `).join("")}
          </div>
          <div class="bot-emoji-scroll">
            ${categories.map((category, index) => `
              <section class="bot-emoji-category" data-emoji-category="${index}">
                <div class="bot-emoji-category-title">${escapeHtml(category.label)}</div>
                <div class="bot-emoji-grid">
                  ${category.emojis.map((emoji) => `<button type="button" class="bot-emoji-item" data-emoji="${escapeHtml(emoji)}" aria-label="${escapeHtml(emoji)}">${escapeHtml(emoji)}</button>`).join("")}
                </div>
              </section>
            `).join("")}
          </div>
        </div>
      `;
    }

    function closeEmojiPicker() {
      const picker = document.getElementById(EMOJI_PICKER_ID);
      if (picker) picker.remove();
      if (state.emojiOutsideClickHandler) {
        document.removeEventListener("mousedown", state.emojiOutsideClickHandler, true);
        state.emojiOutsideClickHandler = null;
      }
      document.querySelectorAll("[data-format-action='emoji']").forEach((btn) => btn.setAttribute("aria-expanded", "false"));
    }

    function closeVariablePicker() {
      const picker = document.getElementById(VARIABLE_PICKER_ID);
      if (picker) picker.remove();
      if (state.variableOutsideClickHandler) {
        document.removeEventListener("mousedown", state.variableOutsideClickHandler, true);
        state.variableOutsideClickHandler = null;
      }
      document.querySelectorAll("[data-variable-trigger]").forEach((btn) => btn.setAttribute("aria-expanded", "false"));
    }

    function applyTagToSelection(textarea, before, after = "") {
      const start = textarea.selectionStart ?? 0;
      const end = textarea.selectionEnd ?? 0;
      const source = textarea.value || "";
      const selected = source.slice(start, end);
      textarea.value = source.slice(0, start) + before + selected + after + source.slice(end);
      const cursor = start + before.length + selected.length;
      textarea.focus();
      if (selected) textarea.setSelectionRange(start + before.length, start + before.length + selected.length);
      else textarea.setSelectionRange(cursor, cursor);
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    }

    function insertTextAtSelection(textarea, text, range = null) {
      const source = textarea.value || "";
      const start = Math.max(0, Math.min(range?.start ?? textarea.selectionStart ?? 0, source.length));
      const end = Math.max(start, Math.min(range?.end ?? textarea.selectionEnd ?? start, source.length));
      textarea.value = source.slice(0, start) + text + source.slice(end);
      const cursor = start + text.length;
      textarea.focus();
      textarea.setSelectionRange(cursor, cursor);
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    }

    function editorDraftChanged() {
      const textarea = document.getElementById("automaticMessageTextarea");
      return Boolean(textarea && state.activeDraft !== textarea.value);
    }

    async function closeEditorModal(force = false) {
      const modal = document.getElementById(TEXT_MODAL_ID);
      if (!force && editorDraftChanged()) {
        const ok = await window.HelpdeskDialogs.confirm("Есть несохранённые изменения. Закрыть окно без применения?");
        if (!ok) return false;
      }
      closeEmojiPicker();
      closeVariablePicker();
      if (state.modalEscHandler) {
        document.removeEventListener("keydown", state.modalEscHandler);
        state.modalEscHandler = null;
      }
      if (modal) modal.remove();
      state.activeTemplate = null;
      state.activeDraft = "";
      return true;
    }

    function variableItemsFor(item) {
      if (Array.isArray(item.allowed_variable_items) && item.allowed_variable_items.length) {
        return item.allowed_variable_items
          .map((variable) => ({
            name: String(variable?.name || "").trim(),
            description: String(variable?.description || "").trim(),
            scope: String(variable?.scope || "explicit").trim() || "explicit",
            scopeTitle: String(variable?.scope_title || "Дополнительно").trim() || "Дополнительно",
          }))
          .filter((variable) => variable.name);
      }
      return (item.allowed_variables || [])
        .map((name) => ({
          name: String(name || "").trim(),
          description: "Переменная шаблона",
          scope: "explicit",
          scopeTitle: "Дополнительно",
        }))
        .filter((variable) => variable.name);
    }

    function renderVariablePickerHtml(items) {
      const groups = [];
      for (const variable of items) {
        let group = groups.find((candidate) => candidate.scope === variable.scope);
        if (!group) {
          group = { scope: variable.scope, title: variable.scopeTitle || "Дополнительно", items: [] };
          groups.push(group);
        }
        group.items.push(variable);
      }
      const content = groups.length
        ? groups.map((group) => `
            <section class="automatic-variable-group">
              <div class="automatic-variable-group-title">${escapeHtml(group.title)}</div>
              <div class="automatic-variable-group-list">
                ${group.items.map((variable) => `
                  <button type="button" class="automatic-variable-option" data-variable-name="${escapeHtml(variable.name)}">
                    <code>{{${escapeHtml(variable.name)}}}</code>
                    <span>${escapeHtml(variable.description || "Переменная шаблона")}</span>
                  </button>
                `).join("")}
              </div>
            </section>
          `).join("")
        : `<div class="automatic-variable-empty">Для этого шаблона переменные пока не доступны.</div>`;
      return `
        <div id="${VARIABLE_PICKER_ID}" class="automatic-variable-picker" role="dialog" aria-label="Доступные переменные">
          <div class="automatic-variable-picker-title">Доступные переменные</div>
          <div class="automatic-variable-list">${content}</div>
        </div>
      `;
    }

    async function openEditorModal(item) {
      if (!await closeEditorModal()) return;
      state.activeTemplate = item;
      const overlay = document.createElement("div");
      overlay.id = TEXT_MODAL_ID;
      overlay.className = "modal-overlay";
      overlay.innerHTML = `
        <div class="modal-dialog bot-text-editor-dialog automatic-message-editor-dialog" role="dialog" aria-modal="true">
          <div class="modal-head automatic-message-editor-head">
            <div>
              <strong>Редактирование автоматического сообщения</strong>
              <div class="automatic-message-editor-subtitle">${escapeHtml(item.key)} · ${escapeHtml(item.category_title || "")}</div>
            </div>
            <button type="button" class="action automatic-variable-trigger" data-variable-trigger aria-expanded="false" aria-haspopup="dialog">Переменные</button>
          </div>
          <div class="modal-body bot-text-editor-body bot-text-editor-body-split bot-text-editor-body-toolbar">
            <div class="automatic-message-editor-meta">
              <div><b>${escapeHtml(item.title || item.key)}</b></div>
              <div>${escapeHtml(item.description || "")}</div>
            </div>
            <div class="bot-text-toolbar" id="automaticMessageToolbar">
              <button type="button" class="action" data-format-action="bold"><b>B</b></button>
              <button type="button" class="action" data-format-action="italic"><i>I</i></button>
              <button type="button" class="action" data-format-action="underline"><u>U</u></button>
              <button type="button" class="action" data-format-action="strike"><s>S</s></button>
              <button type="button" class="action" data-format-action="spoiler">Спойлер</button>
              <button type="button" class="action" data-format-action="code">Код</button>
              <button type="button" class="action" data-format-action="pre">Блок кода</button>
              <button type="button" class="action" data-format-action="quote">Цитата</button>
              <button type="button" class="action" data-format-action="quote_expandable">Под кат</button>
              <button type="button" class="action" data-format-action="link">Ссылка</button>
              <button type="button" class="action bot-emoji-trigger" data-format-action="emoji" aria-expanded="false" aria-haspopup="dialog">🙂</button>
              <button type="button" class="action" data-format-action="clear">Очистить формат</button>
            </div>
            <textarea id="automaticMessageTextarea" class="input bot-text-editor-textarea">${escapeHtml(item.effective_text || "")}</textarea>
            <div class="bot-telegram-preview-wrap">
              <div class="bot-telegram-preview-title">Предпросмотр Telegram</div>
              <div id="automaticMessagePreview" class="bot-telegram-preview"></div>
            </div>
          </div>
          <div class="modal-foot">
            <button id="automaticMessageCancelBtn" type="button" class="action">Отмена</button>
            <button id="automaticMessageResetBtn" type="button" class="action">Вернуть к базовому</button>
            <button id="automaticMessageApplyBtn" type="button" class="action action-primary">Применить</button>
          </div>
        </div>
      `;
      document.body.appendChild(overlay);

      const textarea = document.getElementById("automaticMessageTextarea");
      state.activeDraft = textarea.value;
      const preview = document.getElementById("automaticMessagePreview");
      const toolbar = document.getElementById("automaticMessageToolbar");
      const variableTrigger = overlay.querySelector("[data-variable-trigger]");
      const variableItems = variableItemsFor(item);
      let lastSelection = { start: textarea.selectionStart ?? 0, end: textarea.selectionEnd ?? 0 };
      let backdropMouseDown = false;

      function rememberSelection() {
        lastSelection = { start: textarea.selectionStart ?? 0, end: textarea.selectionEnd ?? 0 };
      }
      function syncPreview() {
        preview.innerHTML = renderTelegramPreviewHtml(textarea.value || "");
      }
      function toggleEmojiPicker(triggerBtn) {
        closeVariablePicker();
        if (document.getElementById(EMOJI_PICKER_ID)) {
          closeEmojiPicker();
          textarea.focus();
          return;
        }
        toolbar.insertAdjacentHTML("beforeend", renderEmojiPickerHtml());
        triggerBtn?.setAttribute("aria-expanded", "true");
        const picker = document.getElementById(EMOJI_PICKER_ID);
        state.emojiOutsideClickHandler = (event) => {
          const activePicker = document.getElementById(EMOJI_PICKER_ID);
          if (!activePicker) return;
          if (activePicker.contains(event.target) || triggerBtn?.contains?.(event.target)) return;
          closeEmojiPicker();
        };
        document.addEventListener("mousedown", state.emojiOutsideClickHandler, true);
        picker?.addEventListener("click", (event) => {
          const tab = event.target?.closest?.("[data-emoji-category-index]");
          if (tab) {
            const idx = Number(tab.getAttribute("data-emoji-category-index"));
            const target = picker.querySelector(`[data-emoji-category="${idx}"]`);
            const scrollRoot = picker.querySelector(".bot-emoji-scroll");
            picker.querySelectorAll(".bot-emoji-tab").forEach((btn) => btn.classList.remove("active"));
            tab.classList.add("active");
            if (target && scrollRoot) scrollRoot.scrollTo({ top: target.offsetTop - scrollRoot.offsetTop, behavior: "smooth" });
            return;
          }
          const emojiBtn = event.target?.closest?.("[data-emoji]");
          if (!emojiBtn) return;
          insertTextAtSelection(textarea, emojiBtn.getAttribute("data-emoji") || "", lastSelection);
          rememberSelection();
          closeEmojiPicker();
        });
      }
      function toggleVariablePicker(triggerBtn) {
        closeEmojiPicker();
        if (document.getElementById(VARIABLE_PICKER_ID)) {
          closeVariablePicker();
          textarea.focus();
          return;
        }
        triggerBtn.insertAdjacentHTML("afterend", renderVariablePickerHtml(variableItems));
        triggerBtn.setAttribute("aria-expanded", "true");
        const picker = document.getElementById(VARIABLE_PICKER_ID);
        state.variableOutsideClickHandler = (event) => {
          const activePicker = document.getElementById(VARIABLE_PICKER_ID);
          if (!activePicker) return;
          if (activePicker.contains(event.target) || triggerBtn.contains(event.target)) return;
          closeVariablePicker();
        };
        document.addEventListener("mousedown", state.variableOutsideClickHandler, true);
        picker?.addEventListener("mousedown", (event) => event.preventDefault());
        picker?.addEventListener("click", (event) => {
          const option = event.target?.closest?.("[data-variable-name]");
          if (!option) return;
          insertTextAtSelection(textarea, `{{${option.getAttribute("data-variable-name") || ""}}}`, lastSelection);
          rememberSelection();
          closeVariablePicker();
        });
      }

      toolbar.addEventListener("mousedown", (event) => {
        if (event.target?.closest?.("[data-format-action], .bot-emoji-picker")) event.preventDefault();
      });
      variableTrigger?.addEventListener("mousedown", (event) => event.preventDefault());
      variableTrigger?.addEventListener("click", () => toggleVariablePicker(variableTrigger));
      toolbar.querySelectorAll("[data-format-action]").forEach((button) => {
        button.addEventListener("click", async () => {
          const action = button.getAttribute("data-format-action");
          if (action !== "emoji") closeEmojiPicker();
          if (action === "emoji") return toggleEmojiPicker(button);
          if (action === "bold") return applyTagToSelection(textarea, "<b>", "</b>");
          if (action === "italic") return applyTagToSelection(textarea, "<i>", "</i>");
          if (action === "underline") return applyTagToSelection(textarea, "<u>", "</u>");
          if (action === "strike") return applyTagToSelection(textarea, "<s>", "</s>");
          if (action === "spoiler") return applyTagToSelection(textarea, "<tg-spoiler>", "</tg-spoiler>");
          if (action === "code") return applyTagToSelection(textarea, "<code>", "</code>");
          if (action === "pre") return applyTagToSelection(textarea, "<pre>", "</pre>");
          if (action === "quote") return applyTagToSelection(textarea, "<blockquote>", "</blockquote>");
          if (action === "quote_expandable") return applyTagToSelection(textarea, "<blockquote expandable>", "</blockquote>");
          if (action === "link") {
            const start = textarea.selectionStart ?? 0, end = textarea.selectionEnd ?? 0;
            const selected = (textarea.value || "").slice(start, end);
            const safeHref = sanitizeLinkHref((await window.HelpdeskDialogs.prompt("Введите URL (http/https/tg/mailto):", "https://")) || "");
            if (!safeHref) return;
            textarea.setSelectionRange(start, end);
            return selected ? applyTagToSelection(textarea, `<a href="${safeHref}">`, "</a>") : applyTagToSelection(textarea, `<a href="${safeHref}">ссылка</a>`, "");
          }
          if (action === "clear") {
            const source = textarea.value || "";
            const start = textarea.selectionStart ?? 0;
            const end = textarea.selectionEnd ?? 0;
            const selected = source.slice(start, end);
            if (selected) {
              const cleaned = stripAllowedTelegramTags(selected);
              textarea.value = source.slice(0, start) + cleaned + source.slice(end);
              textarea.focus();
              textarea.setSelectionRange(start, start + cleaned.length);
            } else {
              textarea.value = stripAllowedTelegramTags(source);
              textarea.focus();
              textarea.setSelectionRange(textarea.value.length, textarea.value.length);
            }
            textarea.dispatchEvent(new Event("input", { bubbles: true }));
          }
        });
      });

      textarea.addEventListener("input", syncPreview);
      textarea.addEventListener("input", rememberSelection);
      textarea.addEventListener("click", rememberSelection);
      textarea.addEventListener("keyup", rememberSelection);
      textarea.addEventListener("select", rememberSelection);
      document.getElementById("automaticMessageCancelBtn")?.addEventListener("click", () => closeEditorModal(false));
      document.getElementById("automaticMessageApplyBtn")?.addEventListener("click", async () => saveActiveTemplate(textarea.value));
      document.getElementById("automaticMessageResetBtn")?.addEventListener("click", async () => resetActiveTemplate());
      overlay.addEventListener("mousedown", (event) => { backdropMouseDown = event.target === overlay; });
      overlay.addEventListener("mouseup", (event) => {
        if (event.target === overlay && backdropMouseDown) closeEditorModal(false);
        backdropMouseDown = false;
      });
      state.modalEscHandler = (event) => {
        if (event.key !== "Escape") return;
        event.preventDefault();
        if (document.getElementById(VARIABLE_PICKER_ID)) {
          event.preventDefault();
          closeVariablePicker();
          textarea.focus();
          return;
        }
        if (document.getElementById(EMOJI_PICKER_ID)) {
          event.preventDefault();
          closeEmojiPicker();
          textarea.focus();
          return;
        }
        closeEditorModal(false);
      };
      document.addEventListener("keydown", state.modalEscHandler);
      textarea.focus();
      textarea.setSelectionRange(textarea.value.length, textarea.value.length);
      rememberSelection();
      syncPreview();
    }

    function renderTemplateRow(item) {
      return `
        <div class="automatic-message-row" role="button" tabindex="0" data-edit-template-key="${escapeHtml(item.key)}" aria-label="Редактировать: ${escapeHtml(item.title || item.key)}">
          <div class="automatic-message-row-main">
            <div class="automatic-message-title-line">
              <strong>${escapeHtml(item.title || item.key)}</strong>
              <span class="automatic-message-badge ${item.is_overridden ? "is-db" : ""}">${item.is_overridden ? "Изменено" : "Базовый"}</span>
              <span class="automatic-message-badge ${item.runtime_enabled ? "is-runtime" : ""}">${item.runtime_enabled ? "Подключено" : "Только каталог"}</span>
            </div>
            <div class="automatic-message-key">${escapeHtml(item.key)}</div>
            <div class="automatic-message-description">${escapeHtml(item.description || "")}</div>
          </div>
        </div>
      `;
    }

    function render() {
      if (!el.automaticMessagesRoot) return;
      el.automaticMessagesRoot.innerHTML = "";
      el.emptyAutomaticMessagesText?.classList.toggle("hidden", state.categories.length > 0);
      for (const category of state.categories) {
        const items = Array.isArray(category.items) ? category.items : [];
        if (!items.length) continue;
        const section = document.createElement("details");
        section.className = "automatic-message-section";
        section.open = state.openCategories.has(category.category);
        section.addEventListener("toggle", () => {
          if (section.open) state.openCategories.add(category.category);
          else state.openCategories.delete(category.category);
        });
        section.innerHTML = `
          <summary class="automatic-message-section-head">
            <h3>${escapeHtml(category.category_title || category.category)}</h3>
            <span>${items.length}</span>
            <i class="automatic-message-section-arrow" aria-hidden="true"></i>
          </summary>
          <div class="automatic-message-list">
            ${items.map(renderTemplateRow).join("")}
          </div>
        `;
        el.automaticMessagesRoot.appendChild(section);
      }
      el.automaticMessagesRoot.querySelectorAll("[data-edit-template-key]").forEach((button) => {
        button.addEventListener("click", () => {
          const item = state.itemsByKey.get(button.getAttribute("data-edit-template-key") || "");
          if (item) openEditorModal(item);
        });
        button.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            if (!event.repeat) button.click();
          }
        });
      });
    }

    async function loadTemplates() {
      setStatus("Загружаю автоматические сообщения...");
      const payload = await api("/api/v1/settings/automatic-messages");
      state.categories = Array.isArray(payload?.categories) ? payload.categories : [];
      state.itemsByKey = new Map();
      for (const item of payload?.items || []) state.itemsByKey.set(item.key, item);
      render();
      setStatus("Готово");
    }

    async function saveActiveTemplate(value) {
      const item = state.activeTemplate;
      if (!item) return;
      try {
        const updated = await api(`/api/v1/settings/automatic-messages/${encodeURIComponent(item.key)}`, {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ body: value, html_enabled: true }),
        });
        state.itemsByKey.set(updated.key, updated);
        await loadTemplates();
        closeEditorModal(true);
        setStatus("Шаблон сохранён");
      } catch (error) {
        showError(error.message);
      }
    }

    async function resetActiveTemplate() {
      const item = state.activeTemplate;
      if (!item) return;
      if (!await window.HelpdeskDialogs.confirm("Вернуть этот текст к базовому шаблону?")) return;
      try {
        await api(`/api/v1/settings/automatic-messages/${encodeURIComponent(item.key)}`, { method: "DELETE" });
        await loadTemplates();
        closeEditorModal(true);
        setStatus("Шаблон возвращён к базовому");
      } catch (error) {
        showError(error.message);
      }
    }

    const handlePageKeydown = (event) => {
      if (event.key === "Escape") closeError();
    };

    el.backToSettingsBtn?.addEventListener("click", () => {
      if (window.HelpdeskAppShell?.goBack) {
        window.HelpdeskAppShell.goBack();
        return;
      }
      window.history.back();
    });
    el.closeErrorModalBtn?.addEventListener("click", closeError);
    el.okErrorBtn?.addEventListener("click", closeError);
    el.copyErrorBtn?.addEventListener("click", async () => {
      el.errorText?.select();
      try { await navigator.clipboard.writeText(el.errorText?.value || ""); }
      catch { document.execCommand("copy"); }
    });
    closeOnBackdropClick(el.errorModal, closeError);
    document.addEventListener("keydown", handlePageKeydown);

    window.HelpdeskSidebar.init({ onError: (error) => showError(error.message) })
      .then(loadTemplates)
      .catch((error) => {
        if (error?.status !== 401) showError(error.message);
      });

    return {
      canNavigateAway: () => !editorDraftChanged() || window.HelpdeskDialogs.confirm("Есть несохранённые изменения. Закрыть окно без применения?"),
      destroy() {
        closeEditorModal(true);
        closeError();
        document.removeEventListener("keydown", handlePageKeydown);
      },
    };
  }

  if (window.HelpdeskAppShell?.registerPage) {
    window.HelpdeskAppShell.registerPage("settings-automatic-messages", initAutomaticMessagesPage);
  } else {
    initAutomaticMessagesPage();
  }
})();
