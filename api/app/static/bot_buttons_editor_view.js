(function initBotButtonsEditorView(global) {
  const EditorView = {};
  const TEXT_MODAL_ID = "botTextEditorModal";
  const EMOJI_PICKER_ID = "botEmojiPicker";
  const FALLBACK_EMOJI_CATEGORIES = [
    {
      id: "smileys",
      label: "Смайлы",
      emojis: ["😀", "😃", "😄", "😁", "😆", "😊", "🙂", "😉", "😍", "😘", "😎", "🤔", "😐", "😔", "😢", "😡", "😴", "🥳"],
    },
    {
      id: "gestures",
      label: "Жесты",
      emojis: ["👍", "👎", "👌", "✌️", "🤝", "🙏", "👏", "🙌", "👋", "🤙", "💪", "☝️", "👇", "👈", "👉", "✍️"],
    },
    {
      id: "objects",
      label: "Объекты",
      emojis: ["📞", "📱", "💻", "🖥️", "⌨️", "🖱️", "📎", "📌", "📍", "📄", "📁", "🗂️", "🔑", "🔒", "⚙️", "🛠️", "💡", "📷"],
    },
    {
      id: "symbols",
      label: "Символы",
      emojis: ["✅", "❌", "⚠️", "❗", "❓", "ℹ️", "➕", "➖", "➡️", "⬅️", "⬆️", "⬇️", "🔴", "🟢", "🟡", "⭐", "🔥", "💬"],
    },
  ];
  let textModalEscHandler = null;
  let emojiOutsideClickHandler = null;

  function escapeModalHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function textStatus(value) {
    return String(value || "").trim() ? "Задан" : "Не задан";
  }

  function closeTextEditorModal() {
    closeEmojiPicker();
    const modal = document.getElementById(TEXT_MODAL_ID);
    if (modal) modal.remove();
    if (textModalEscHandler) {
      document.removeEventListener("keydown", textModalEscHandler);
      textModalEscHandler = null;
    }
  }

  function closeEmojiPicker() {
    const picker = document.getElementById(EMOJI_PICKER_ID);
    if (picker) picker.remove();
    if (emojiOutsideClickHandler) {
      document.removeEventListener("mousedown", emojiOutsideClickHandler, true);
      emojiOutsideClickHandler = null;
    }
    document.querySelectorAll("[data-format-action='emoji']").forEach((btn) => {
      btn.setAttribute("aria-expanded", "false");
    });
  }

  function getEmojiCategories() {
    const source = Array.isArray(global.BotButtonsEmojiData) && global.BotButtonsEmojiData.length
      ? global.BotButtonsEmojiData
      : FALLBACK_EMOJI_CATEGORIES;
    return source.map((category, index) => ({
      id: String(category.id || `emoji-${index}`),
      label: String(category.label || category.title || `Категория ${index + 1}`),
      emojis: Array.isArray(category.emojis)
        ? category.emojis
        : (Array.isArray(category.items) ? category.items : []),
    })).filter((category) => category.emojis.length);
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

  function applyTagToSelection(textarea, before, after = "") {
    if (!textarea) return;
    const start = textarea.selectionStart ?? 0;
    const end = textarea.selectionEnd ?? 0;
    const source = textarea.value || "";
    const selected = source.slice(start, end);
    const next = source.slice(0, start) + before + selected + after + source.slice(end);
    textarea.value = next;
    const cursorPos = start + before.length + selected.length;
    textarea.focus();
    if (selected.length) {
      textarea.setSelectionRange(start + before.length, start + before.length + selected.length);
    } else {
      textarea.setSelectionRange(cursorPos, cursorPos);
    }
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function insertTextAtSelection(textarea, text, range = null) {
    if (!textarea || !text) return;
    const source = textarea.value || "";
    const start = Math.max(0, Math.min(range?.start ?? textarea.selectionStart ?? 0, source.length));
    const end = Math.max(start, Math.min(range?.end ?? textarea.selectionEnd ?? start, source.length));
    textarea.value = source.slice(0, start) + text + source.slice(end);
    const nextCursor = start + text.length;
    textarea.focus();
    textarea.setSelectionRange(nextCursor, nextCursor);
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function renderEmojiPickerHtml() {
    const categories = getEmojiCategories();
    return `
      <div id="${EMOJI_PICKER_ID}" class="bot-emoji-picker" role="dialog" aria-label="Emoji">
        <div class="bot-emoji-tabs" role="tablist" aria-label="Категории emoji">
          ${categories.map((category, index) => `
            <button type="button" class="bot-emoji-tab ${index === 0 ? "active" : ""}" data-emoji-category-index="${index}">${escapeModalHtml(category.label)}</button>
          `).join("")}
        </div>
        <div class="bot-emoji-scroll">
        ${categories.map((category, index) => `
          <section class="bot-emoji-category" data-emoji-category="${index}">
            <div class="bot-emoji-category-title">${escapeModalHtml(category.label)}</div>
            <div class="bot-emoji-grid">
              ${category.emojis.map((emoji) => `
                <button type="button" class="bot-emoji-item" data-emoji="${escapeModalHtml(emoji)}" aria-label="${escapeModalHtml(emoji)}">${escapeModalHtml(emoji)}</button>
              `).join("")}
            </div>
          </section>
        `).join("")}
        </div>
      </div>
    `;
  }

  function renderTelegramPreviewHtml(text) {
    const parser = new DOMParser();
    const doc = parser.parseFromString(`<div>${String(text || "")}</div>`, "text/html");
    const root = doc.body.firstElementChild || doc.body;
    const allowed = new Set(["b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "tg-spoiler", "code", "pre", "blockquote", "a"]);

    function walk(node) {
      if (node.nodeType === Node.TEXT_NODE) {
        return document.createTextNode(node.textContent || "");
      }
      if (node.nodeType !== Node.ELEMENT_NODE) {
        return document.createTextNode("");
      }
      const tag = String(node.tagName || "").toLowerCase();
      if (!allowed.has(tag)) {
        const frag = document.createDocumentFragment();
        node.childNodes.forEach((child) => frag.appendChild(walk(child)));
        return frag;
      }

      let el;
      if (tag === "tg-spoiler") {
        el = document.createElement("span");
        el.className = "tg-preview-spoiler";
      } else if (tag === "strong") {
        el = document.createElement("b");
      } else if (tag === "em") {
        el = document.createElement("i");
      } else if (tag === "ins") {
        el = document.createElement("u");
      } else if (tag === "strike" || tag === "del") {
        el = document.createElement("s");
      } else {
        el = document.createElement(tag);
      }

      if (tag === "a") {
        const href = sanitizeLinkHref(node.getAttribute("href") || "");
        if (href) {
          el.setAttribute("href", href);
          el.setAttribute("target", "_blank");
          el.setAttribute("rel", "noopener noreferrer");
        }
      }
      if (tag === "blockquote" && node.hasAttribute("expandable")) {
        el.classList.add("tg-preview-expandable");
      }
      node.childNodes.forEach((child) => el.appendChild(walk(child)));
      return el;
    }

    const previewRoot = document.createElement("div");
    root.childNodes.forEach((child) => previewRoot.appendChild(walk(child)));
    return previewRoot.innerHTML || "Текст не задан";
  }

  function openTextEditorModal(options) {
    closeTextEditorModal();
    const {
      title,
      value,
      onApply,
      withTelegramPreview = false,
      withTelegramToolbar = false,
      placeholder = "",
    } = options || {};
    const overlay = document.createElement("div");
    overlay.id = TEXT_MODAL_ID;
    overlay.className = "modal-overlay";
    overlay.innerHTML = `
      <div class="modal-dialog bot-text-editor-dialog" role="dialog" aria-modal="true">
        <div class="modal-head">
          <strong>${escapeModalHtml(title || "Редактирование текста")}</strong>
        </div>
        <div class="modal-body bot-text-editor-body ${withTelegramPreview ? "bot-text-editor-body-split" : ""} ${withTelegramToolbar ? "bot-text-editor-body-toolbar" : ""}">
          ${withTelegramToolbar ? `
            <div class="bot-text-toolbar" id="botTextToolbar">
              <button type="button" class="action" data-format-action="bold"><b>Ж</b></button>
              <button type="button" class="action" data-format-action="italic"><i>К</i></button>
              <button type="button" class="action" data-format-action="underline"><u>Ч</u></button>
              <button type="button" class="action" data-format-action="strike"><s>З</s></button>
              <button type="button" class="action" data-format-action="spoiler">Спойлер</button>
              <button type="button" class="action" data-format-action="code">Код</button>
              <button type="button" class="action" data-format-action="pre">Блок кода</button>
              <button type="button" class="action" data-format-action="quote">Цитата</button>
              <button type="button" class="action" data-format-action="quote_expandable">Под кат</button>
              <button type="button" class="action" data-format-action="link">Ссылка</button>
              <button type="button" class="action bot-emoji-trigger" data-format-action="emoji" aria-expanded="false" aria-haspopup="dialog">🙂</button>
              <button type="button" class="action" data-format-action="clear">Очистить формат</button>
            </div>
          ` : ""}
          <textarea id="botTextEditorTextarea" class="input bot-text-editor-textarea" placeholder="${escapeModalHtml(placeholder)}">${escapeModalHtml(value || "")}</textarea>
          ${withTelegramPreview ? `
            <div class="bot-telegram-preview-wrap">
              <div class="bot-telegram-preview-title">Предпросмотр Telegram</div>
              <div id="botTelegramPreview" class="bot-telegram-preview"></div>
            </div>
          ` : ""}
        </div>
        <div class="modal-foot">
          <button id="botTextEditorCancelBtn" type="button" class="action">Отмена</button>
          <button id="botTextEditorApplyBtn" type="button" class="action action-primary">Применить</button>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);

    const textarea = document.getElementById("botTextEditorTextarea");
    const preview = document.getElementById("botTelegramPreview");
    const cancelBtn = document.getElementById("botTextEditorCancelBtn");
    const applyBtn = document.getElementById("botTextEditorApplyBtn");
    const toolbar = document.getElementById("botTextToolbar");
    const initialValue = String(value || "");
    let backdropMouseDown = false;
    let lastSelection = {
      start: textarea?.selectionStart ?? 0,
      end: textarea?.selectionEnd ?? 0,
    };

    const syncPreview = () => {
      if (!preview || !textarea) return;
      preview.innerHTML = renderTelegramPreviewHtml(textarea.value || "");
    };

    const hasUnsavedChanges = () => String(textarea?.value || "") !== initialValue;
    const requestClose = (force = false) => {
      if (!force && hasUnsavedChanges()) {
        const ok = window.confirm("Есть несохранённые изменения. Закрыть окно без применения?");
        if (!ok) return;
      }
      closeTextEditorModal();
    };

    textarea?.focus();
    textarea?.setSelectionRange((textarea.value || "").length, (textarea.value || "").length);
    const rememberSelection = () => {
      if (!textarea) return;
      lastSelection = {
        start: textarea.selectionStart ?? 0,
        end: textarea.selectionEnd ?? 0,
      };
    };
    rememberSelection();
    textarea?.addEventListener("input", syncPreview);
    textarea?.addEventListener("input", rememberSelection);
    textarea?.addEventListener("keyup", rememberSelection);
    textarea?.addEventListener("click", rememberSelection);
    textarea?.addEventListener("select", rememberSelection);
    syncPreview();

    if (withTelegramToolbar) {
      toolbar?.addEventListener("mousedown", (event) => {
        if (event.target?.closest?.("[data-format-action], .bot-emoji-picker")) {
          event.preventDefault();
        }
      });
      const toggleEmojiPicker = (triggerBtn) => {
        const existing = document.getElementById(EMOJI_PICKER_ID);
        if (existing) {
          closeEmojiPicker();
          textarea?.focus();
          return;
        }
        toolbar?.insertAdjacentHTML("beforeend", renderEmojiPickerHtml());
        triggerBtn?.setAttribute("aria-expanded", "true");
        const picker = document.getElementById(EMOJI_PICKER_ID);
        emojiOutsideClickHandler = (event) => {
          const activePicker = document.getElementById(EMOJI_PICKER_ID);
          if (!activePicker) return;
          const isInsidePicker = activePicker.contains(event.target);
          const isEmojiTrigger = triggerBtn?.contains?.(event.target);
          if (isInsidePicker || isEmojiTrigger) return;
          closeEmojiPicker();
        };
        document.addEventListener("mousedown", emojiOutsideClickHandler, true);
        picker?.addEventListener("click", (event) => {
          const tabBtn = event.target?.closest?.("[data-emoji-category-index]");
          if (tabBtn) {
            const idx = Number(tabBtn.getAttribute("data-emoji-category-index"));
            const target = picker.querySelector(`[data-emoji-category="${idx}"]`);
            const scrollRoot = picker.querySelector(".bot-emoji-scroll");
            if (target) {
              picker.querySelectorAll(".bot-emoji-tab").forEach((btn) => btn.classList.remove("active"));
              tabBtn.classList.add("active");
              if (scrollRoot) {
                scrollRoot.scrollTo({ top: target.offsetTop - scrollRoot.offsetTop, behavior: "smooth" });
              }
            }
            return;
          }
          const emojiBtn = event.target?.closest?.("[data-emoji]");
          if (!emojiBtn || !textarea) return;
          insertTextAtSelection(textarea, emojiBtn.getAttribute("data-emoji") || "", lastSelection);
          rememberSelection();
          closeEmojiPicker();
        });
      };
      document.querySelectorAll("[data-format-action]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const action = btn.getAttribute("data-format-action");
          if (!textarea || !action) return;
          if (action !== "emoji") closeEmojiPicker();
          if (action === "emoji") return toggleEmojiPicker(btn);
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
            const selected = (textarea.value || "").slice(textarea.selectionStart ?? 0, textarea.selectionEnd ?? 0);
            const hrefInput = window.prompt("Введите URL (http/https/tg/mailto):", "https://");
            const safeHref = sanitizeLinkHref(hrefInput || "");
            if (!safeHref) return;
            if (selected) return applyTagToSelection(textarea, `<a href="${safeHref}">`, "</a>");
            return applyTagToSelection(textarea, `<a href="${safeHref}">ссылка</a>`, "");
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
    }

    cancelBtn?.addEventListener("click", () => requestClose(false));
    applyBtn?.addEventListener("click", () => {
      if (typeof onApply === "function") onApply(textarea?.value || "");
      requestClose(true);
    });
    overlay.addEventListener("mousedown", (event) => {
      backdropMouseDown = event.target === overlay;
    });
    overlay.addEventListener("mouseup", (event) => {
      if (event.target === overlay && backdropMouseDown) requestClose(false);
      backdropMouseDown = false;
    });
    textModalEscHandler = (event) => {
      if (event.key !== "Escape") return;
      if (document.getElementById(EMOJI_PICKER_ID)) {
        event.preventDefault();
        closeEmojiPicker();
        textarea?.focus();
        return;
      }
      requestClose(false);
    };
    document.addEventListener("keydown", textModalEscHandler);
  }

  EditorView.renderEditor = function renderEditor(ctx) {
    const {
      state,
      el,
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
    } = ctx;

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
              <div class="bot-editor-text-row">
                <label class="field-label">Инструкция</label>
                <button id="editInstructionBtn" type="button" class="action">Редактировать</button>
              </div>
              <div class="status">Статус: ${escapeHtml(textStatus(state.editorText))}</div>
            </div>

            <div class="bot-editor-section">
              <div class="bot-editor-text-row">
                <label class="field-label">Текст клиенту</label>
                <button id="editClientTextBtn" type="button" class="action">Редактировать</button>
              </div>
              <div class="status">Статус: ${escapeHtml(textStatus(state.editorClientText))}</div>
            </div>

            <div class="bot-editor-section">
              <div class="bot-editor-text-row">
                <label class="field-label">Текст системный</label>
                <button id="editSystemTextBtn" type="button" class="action">Редактировать</button>
              </div>
              <div class="status">Статус: ${escapeHtml(textStatus(state.editorSystemText))}</div>
            </div>

            <div class="bot-editor-section">
              <label class="status-panel-title admin-agent-header">
                <input id="confirmationEnabled" type="checkbox" ${state.confirmationEnabled ? "checked" : ""} />
                <span>Требовать подтверждение перед созданием</span>
              </label>
              <div class="bot-editor-text-row">
                <label class="field-label">Подтверждение перед созданием</label>
                <button id="editConfirmationTextBtn" type="button" class="action ${state.confirmationEnabled ? "" : "action-muted"}">Редактировать</button>
              </div>
              <div class="status">Статус: ${escapeHtml(textStatus(state.confirmationText))}</div>
              ${state.confirmationEnabled ? "" : '<div class="status">Подтверждение выключено, но текст сохранится и применится после включения.</div>'}
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

    const botLabelInput = document.getElementById("botLabelInput");
    const editInstructionBtn = document.getElementById("editInstructionBtn");
    const editClientTextBtn = document.getElementById("editClientTextBtn");
    const editSystemTextBtn = document.getElementById("editSystemTextBtn");
    const editConfirmationTextBtn = document.getElementById("editConfirmationTextBtn");
    const fixedEnabledEl = document.getElementById("fixedAssigneeEnabled");
    const buttonIsEnabledEl = document.getElementById("buttonIsEnabled");
    const fixedUserEl = document.getElementById("fixedAssigneeUserId");
    const freezeOnCreateEl = document.getElementById("freezeOnCreate");
    const automationKeySelectEl = document.getElementById("automationKeySelect");
    const confirmationEnabledEl = document.getElementById("confirmationEnabled");
    const addBotFileBtn = document.getElementById("addBotFileBtn");
    const removeBotFileBtn = document.getElementById("removeBotFileBtn");
    const addConditionalRuleBtn = document.getElementById("addConditionalRuleBtn");
    const openNestedBtn = document.getElementById("openNestedBtn");
    const saveBtn = document.getElementById("saveInstructionBtn");

    botLabelInput?.addEventListener("input", () => {
      state.editorBotLabel = botLabelInput.value;
    });
    editInstructionBtn?.addEventListener("click", () => {
      openTextEditorModal({
        title: "Редактирование инструкции",
        value: state.editorText || "",
        placeholder: "Введите инструкцию для этой конечной кнопки...",
        onApply: (nextValue) => {
          state.editorText = nextValue;
          EditorView.renderEditor(ctx);
        },
      });
    });
    editClientTextBtn?.addEventListener("click", () => {
      openTextEditorModal({
        title: "Редактирование текста клиенту",
        value: state.editorClientText || "",
        placeholder: "Сообщение, которое бот отправит клиенту",
        withTelegramPreview: true,
        withTelegramToolbar: true,
        onApply: (nextValue) => {
          state.editorClientText = nextValue;
          EditorView.renderEditor(ctx);
        },
      });
    });
    editSystemTextBtn?.addEventListener("click", () => {
      openTextEditorModal({
        title: "Редактирование системного текста",
        value: state.editorSystemText || "",
        placeholder: "Системное сообщение для чата заявки/задачи",
        onApply: (nextValue) => {
          state.editorSystemText = nextValue;
          state.systemTextOverridden = true;
          EditorView.renderEditor(ctx);
        },
      });
    });
    editConfirmationTextBtn?.addEventListener("click", () => {
      openTextEditorModal({
        title: "Редактирование подтверждения",
        value: state.confirmationText || "",
        placeholder: "Текст подтверждения (если пусто — стандартный)",
        withTelegramPreview: true,
        withTelegramToolbar: true,
        onApply: (nextValue) => {
          state.confirmationText = nextValue;
          EditorView.renderEditor(ctx);
        },
      });
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
      EditorView.renderEditor(ctx);
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
      EditorView.renderEditor(ctx);
    });
    addConditionalRuleBtn?.addEventListener("click", () => {
      state.conditionalRules.push({
        conditions: [{ field: "", operator: "", value: "" }],
        files: [],
      });
      state.conditionalFilesOverridden = true;
      EditorView.renderEditor(ctx);
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
        EditorView.renderEditor(ctx);
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
        EditorView.renderEditor(ctx);
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
        EditorView.renderEditor(ctx);
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
        : "Элемент будет скрыт из меню, но настройки сохраняются. Продолжить?";
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
          const targetItem = await ctx.api(`/api/v1/settings/bot-buttons/item?button_key=${encodeURIComponent(targetKey)}&menu_type=${encodeURIComponent(state.selectedMenu || "")}`);
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
  };

  EditorView.cleanup = function cleanup() {
    closeTextEditorModal();
  };

  global.BotButtonsEditorView = EditorView;
})(window);

