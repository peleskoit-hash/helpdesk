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

  function renderButtonStylePicker(ctx) {
    const escapeHtml = ctx.escapeHtml || escapeModalHtml;
    const selectedStyle = String(ctx.state?.buttonStyle || "");
    const options = [
      { value: "", label: "Базовый", title: "Базовый" },
      { value: "primary", label: "", title: "Синий" },
      { value: "success", label: "", title: "Зелёный" },
      { value: "danger", label: "", title: "Красный" },
    ];
    return `
      <div class="bot-button-style-picker" role="radiogroup" aria-label="Цвет кнопки">
        ${options.map((option) => {
          const active = selectedStyle === option.value;
          const classes = [
            "bot-button-style-option",
            option.value ? `bot-button-style-${option.value}` : "bot-button-style-base",
            active ? "is-selected" : "",
          ].filter(Boolean).join(" ");
          return `
            <button
              type="button"
              class="${classes}"
              data-button-style-option="${escapeHtml(option.value)}"
              aria-label="${escapeHtml(option.title)}"
              title="${escapeHtml(option.title)}"
              aria-pressed="${active ? "true" : "false"}"
            >${option.label ? escapeHtml(option.label) : "<span aria-hidden=\"true\"></span>"}</button>
          `;
        }).join("")}
      </div>
    `;
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
    if (global.TelegramTextEditor?.renderTelegramPreviewHtml) {
      return global.TelegramTextEditor.renderTelegramPreviewHtml(text);
    }
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
          ${withTelegramToolbar ? (global.TelegramTextEditor?.toolbarHtml ? global.TelegramTextEditor.toolbarHtml() : `
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
          `) : ""}
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

    if (withTelegramToolbar && global.TelegramTextEditor?.bindToolbar) {
      global.TelegramTextEditor.bindToolbar(overlay, textarea);
    }

    if (withTelegramToolbar && !global.TelegramTextEditor?.bindToolbar) {
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
    const editorHelpers = global.BotButtonsEditor || {};
    const isRemoved = !!leaf.removed;
    const isDbCreated = !!leaf.is_db_created;
    const resolvedLeafWorkflowType = typeof editorHelpers.resolveLeafWorkflowType === "function"
      ? editorHelpers.resolveLeafWorkflowType(leaf)
      : String(leaf.workflow_type || "").trim().toLowerCase();
    const editorWorkflowType = String(state.editorWorkflowType || resolvedLeafWorkflowType || "").trim().toLowerCase();
    const isSubmenuEditor = editorWorkflowType === "submenu";
    const isGroupTaskChildEditor = editorWorkflowType === "group_task_child";
    const workflowTypeOptions = typeof editorHelpers.getWorkflowTypeOptions === "function"
      ? editorHelpers.getWorkflowTypeOptions(leaf)
      : [];
    const canChangeWorkflowType = workflowTypeOptions.length > 0;
    const workflowTypeLabel = typeof editorHelpers.getWorkflowTypeLabel === "function"
      ? editorHelpers.getWorkflowTypeLabel(editorWorkflowType || resolvedLeafWorkflowType)
      : (editorWorkflowType || resolvedLeafWorkflowType || "");
    const countNestedChildren = (rows) => (Array.isArray(rows) ? rows : []).reduce((count, row) => {
      return count + (row?.buttons || []).reduce((inner, node) => {
        return inner + 1 + countNestedChildren(node?.children_rows);
      }, 0);
    }, 0);
    const nestedChildCount = countNestedChildren(leaf.children_rows);
    const moveOptions = buildMoveCandidates()
      .filter((c) => c.key !== leaf.technical_key)
      .filter((c) => !resolvedLeafWorkflowType || c.workflowType === resolvedLeafWorkflowType)
      .map((c) => `<option value="${escapeHtml(c.key)}" data-path="${escapeHtml(c.path)}" data-workflow-type="${escapeHtml(c.workflowType)}">${escapeHtml(c.path)}</option>`)
      .join("");
    const deleteActionHtml = isSubmenuEditor && !isRemoved
      ? `<div class="bot-editor-section">
           <div class="bot-removed-title">Удаление подменю</div>
           <button id="deleteSubmenuBtn" type="button" class="action action-danger">Удалить подменю</button>
           <div class="status">Будут удалены дочерние элементы: ${escapeHtml(String(nestedChildCount))}</div>
         </div>`
      : "";
    const workflowType = editorWorkflowType;
    const visibleAutomations = (state.availableAutomations || []).filter((item) => {
      const types = Array.isArray(item?.workflow_types) ? item.workflow_types.map((value) => String(value || "").trim().toLowerCase()) : [];
      return types.includes(workflowType);
    });
    const selectedAutomationKnown = visibleAutomations.some((item) => String(item?.key || "") === String(state.automationTypeValue || "none"));
    const showSendFilesSettings = String(state.automationTypeValue || "none") === "send_files";
    const nameLabel = isGroupTaskChildEditor ? "Название задачи" : "Название в боте";
    const namePlaceholder = isGroupTaskChildEditor ? "Введите название задачи" : "Введите название кнопки/раздела в боте";

    const leftColumnHtml = isSubmenuEditor
      ? `
          <div class="bot-editor-section">
            <label class="field-label">Тип кнопки</label>
            <input class="input" type="text" value="Подменю" disabled />
          </div>

          <div class="bot-editor-section">
            <label class="field-label">Название в боте</label>
            <input id="botLabelInput" class="input" type="text" maxlength="255" value="${escapeHtml(state.editorBotLabel || "")}" placeholder="Введите название раздела в боте" />
          </div>

          <div class="bot-editor-section">
            <label class="field-label">Цвет кнопки</label>
            ${renderButtonStylePicker(ctx)}
          </div>

          <div class="bot-editor-section">
            <div class="bot-removed-title">Параметры подменю</div>
            <label class="checkbox-label">
              <input id="buttonIsEnabled" type="checkbox" ${state.isEnabled ? "checked" : ""} />
              <span>Кнопка активна</span>
            </label>
          </div>

          ${deleteActionHtml}
        `
      : `
          <div class="bot-editor-section">
            <label class="field-label">\u0422\u0438\u043f \u0441\u0443\u0449\u0435\u0441\u0442\u0432\u0443\u044e\u0449\u0435\u0439 \u043a\u043d\u043e\u043f\u043a\u0438</label>
            ${canChangeWorkflowType
              ? `
                  <select id="editorWorkflowTypeSelect" class="input">
                    ${workflowTypeOptions.map((option) => `
                      <option value="${escapeHtml(option.value)}" ${editorWorkflowType === option.value ? "selected" : ""}>${escapeHtml(option.label)}</option>
                    `).join("")}
                  </select>
                  <div class="status">\u0414\u043e \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u0438\u044f \u044d\u0442\u043e \u0442\u043e\u043b\u044c\u043a\u043e draft-\u0438\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u0435.</div>
                `
              : `<input class="input" type="text" value="${escapeHtml(workflowTypeLabel)}" disabled />`}
          </div>

          <div class="bot-editor-section">
            <label class="field-label">${escapeHtml(nameLabel)}</label>
            <input id="botLabelInput" class="input" type="text" maxlength="255" value="${escapeHtml(state.editorBotLabel || "")}" placeholder="${escapeHtml(namePlaceholder)}" />
          </div>

          ${isGroupTaskChildEditor ? "" : `
            <div class="bot-editor-section">
              <label class="field-label">Цвет кнопки</label>
              ${renderButtonStylePicker(ctx)}
            </div>
          `}

          <div class="bot-editor-section">
            <div class="bot-removed-title">Параметры создания</div>
            <label class="checkbox-label">
              <input id="buttonIsEnabled" type="checkbox" ${state.isEnabled ? "checked" : ""} />
              <span>Кнопка активна</span>
            </label>
            <label class="checkbox-label">
              <input id="fixedAssigneeEnabled" type="checkbox" ${state.fixedAssigneeEnabled ? "checked" : ""} />
              <span>Фиксированный ответственный</span>
            </label>
            <select id="fixedAssigneeUserId" class="input" ${state.fixedAssigneeEnabled ? "" : "disabled"}>
              ${renderAssigneeOptions()}
            </select>
            <label class="checkbox-label">
              <input id="freezeOnCreate" type="checkbox" ${state.freezeOnCreate ? "checked" : ""} />
              <span>Сразу замораживать при создании</span>
            </label>
            ${isGroupTaskChildEditor ? `
              <label class="checkbox-label">
                <input id="suppressClientAnswer" type="checkbox" ${state.suppressClientAnswer ? "checked" : ""} />
                <span>Не отправлять ответ клиенту</span>
              </label>
            ` : ""}
          </div>

          <div class="bot-editor-section">
            <label class="field-label">Автоматизация</label>
            <select id="automationKeySelect" class="input">

              ${!selectedAutomationKnown && state.automationTypeValue && state.automationTypeValue !== "none"
                ? `<option value="${escapeHtml(state.automationTypeValue)}" selected>${escapeHtml(state.automationKey || state.automationTypeValue)} (legacy)</option>`
                : ""}
              ${visibleAutomations.map((a) => {
                const key = String(a?.key || "");
                const title = String(a?.title || key || "Без названия");
                return `<option value="${escapeHtml(key)}" ${state.automationTypeValue === key ? "selected" : ""}>${escapeHtml(title)} (${escapeHtml(key)})</option>`;
              }).join("")}
            </select>
            <div class="status">Название: ${escapeHtml(state.automationTitle || "не задано")}</div>
            <div class="status">Key: ${escapeHtml(state.automationKey || "none")}</div>
            <div class="status">Описание: ${escapeHtml(state.automationDescription || "не задано")}</div>
            <div class="status">Статус: ${escapeHtml(
              state.automationStatus === "not_applied"
                ? "не применяется"
                : (state.automationStatus === "not_found"
                  ? "не найдена"
                  : (state.automationStatus === "available"
                    ? (state.automationAvailable ? "доступна" : "в реестре, но недоступна")
                    : "не задана"))
            )}</div>
            ${state.automationHandler ? `<div class="status">Handler: <code>${escapeHtml(state.automationHandler)}</code></div>` : ""}
            ${showSendFilesSettings ? `
              <div class="bot-automation-settings">
                <label class="field-label">Файлы</label>
                <div id="botSelectedFilesWrap">${renderSelectedFiles()}</div>
                <div class="bot-file-actions">
                  <button id="addBotFileBtn" type="button" class="action">Добавить файл</button>
                </div>
              </div>
            ` : ""}
            ${Array.isArray(leaf.children_rows) && leaf.children_rows.length ? '<button id="openNestedBtn" type="button" class="action">Открыть вложенные кнопки</button>' : ""}
          </div>
        `;
    const rightColumnHtml = isSubmenuEditor
      ? `
          <div class="bot-editor-section">
            <div class="bot-editor-text-row">
              <label class="field-label">Текст клиенту</label>
              <button id="editClientTextBtn" type="button" class="action">Редактировать</button>
            </div>
            <div class="status">Статус: ${escapeHtml(textStatus(state.editorClientText))}</div>
          </div>
        `
      : `
          <div class="bot-editor-section">
            <div class="bot-editor-text-row">
              <label class="field-label">Инструкция</label>
              <button id="editInstructionBtn" type="button" class="action">Редактировать</button>
            </div>
            <div class="status">Статус: ${escapeHtml(textStatus(state.editorText))}</div>
          </div>

          ${isGroupTaskChildEditor ? "" : `
            <div class="bot-editor-section">
              <div class="bot-editor-text-row">
                <label class="field-label">Текст клиенту</label>
                <button id="editClientTextBtn" type="button" class="action">Редактировать</button>
              </div>
              <div class="status">Статус: ${escapeHtml(textStatus(state.editorClientText))}</div>
            </div>
          `}

          <div class="bot-editor-section">
            <div class="bot-editor-text-row">
              <label class="field-label">Текст системный</label>
              <button id="editSystemTextBtn" type="button" class="action">Редактировать</button>
            </div>
            <div class="status">Статус: ${escapeHtml(textStatus(state.editorSystemText))}</div>
          </div>

          ${isGroupTaskChildEditor ? "" : `
            <div class="bot-editor-section">
              <label class="checkbox-label">
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
          `}
        `;

    el.content.innerHTML = `
      <div class="panel settings-panel bot-instruction-editor">
        <div class="status">Technical key: <code>${escapeHtml(leaf.technical_key)}</code></div>
        ${isRemoved ? '<div class="bot-removed-warning">Эта кнопка отсутствует в текущей структуре и находится в блоке «Удалённые/изменённые».</div>' : ""}
        ${(!isRemoved && isDbCreated && !isSubmenuEditor) ? '<div class="bot-editor-inline-actions"><button id="archiveButtonBtn" type="button" class="action action-danger">Удалить из меню</button></div>' : ""}

        <div class="bot-editor-grid">
          <div class="bot-editor-col bot-editor-col-left">
            ${leftColumnHtml}
          </div>

          <div class="bot-editor-col bot-editor-col-right">
            ${rightColumnHtml}
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
    const workflowTypeSelectEl = document.getElementById("editorWorkflowTypeSelect");
    const fixedUserEl = document.getElementById("fixedAssigneeUserId");
    const freezeOnCreateEl = document.getElementById("freezeOnCreate");
    const suppressClientAnswerEl = document.getElementById("suppressClientAnswer");
    const automationKeySelectEl = document.getElementById("automationKeySelect");
    const confirmationEnabledEl = document.getElementById("confirmationEnabled");
    const addBotFileBtn = document.getElementById("addBotFileBtn");
    const openNestedBtn = document.getElementById("openNestedBtn");
    const deleteSubmenuBtn = document.getElementById("deleteSubmenuBtn");
    const saveBtn = document.getElementById("saveInstructionBtn");

    botLabelInput?.addEventListener("input", () => {
      state.editorBotLabel = botLabelInput.value;
    });
    workflowTypeSelectEl?.addEventListener("change", () => {
      const previous = String(state.editorWorkflowType || resolvedLeafWorkflowType || "").trim().toLowerCase();
      const nextValue = String(workflowTypeSelectEl.value || "").trim().toLowerCase();
      if (typeof editorHelpers.applyWorkflowTypeDraft !== "function") return;
      const changed = editorHelpers.applyWorkflowTypeDraft(ctx, nextValue);
      if (!changed) {
        workflowTypeSelectEl.value = previous;
        return;
      }
      EditorView.renderEditor(ctx);
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
    document.querySelectorAll("[data-button-style-option]").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.buttonStyle = btn.getAttribute("data-button-style-option") || "";
        EditorView.renderEditor(ctx);
      });
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
    suppressClientAnswerEl?.addEventListener("change", () => {
      state.suppressClientAnswer = !!suppressClientAnswerEl.checked;
    });
    automationKeySelectEl?.addEventListener("change", () => {
      state.automationTypeValue = String(automationKeySelectEl.value || "none");
      if (global.BotButtonsEditor?.syncAutomationSelectionMeta) {
        global.BotButtonsEditor.syncAutomationSelectionMeta(ctx);
      }
      EditorView.renderEditor(ctx);
    });
    confirmationEnabledEl?.addEventListener("change", () => {
      state.confirmationEnabled = !!confirmationEnabledEl.checked;
      EditorView.renderEditor(ctx);
    });
    addBotFileBtn?.addEventListener("click", () => {
      openFilePickerModal();
    });
    openNestedBtn?.addEventListener("click", () => {
      if (!Array.isArray(leaf.children_rows) || !leaf.children_rows.length) return;
      state.levelStack.push({ label: leaf.label, rows: leaf.children_rows, levelKey: leaf.level_key || null, fullPath: leaf.full_path || "", kind: leaf.kind || "" });
      state.view = "level";
      render();
    });
    deleteSubmenuBtn?.addEventListener("click", async () => {
      const message = `Подменю содержит ${nestedChildCount} дочерних элементов.\nПри удалении подменю они также будут удалены из меню.\nВы уверены?`;
      if (!window.confirm(message)) return;
      await archiveMenuButton();
    });
    document.querySelectorAll("[data-add-file-condition]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const file = String(btn.dataset.addFileCondition || "");
        if (!file) return;
        if (global.BotButtonsFiles?.addConditionToFile) {
          global.BotButtonsFiles.addConditionToFile(ctx, file);
        }
        EditorView.renderEditor(ctx);
      });
    });
    document.querySelectorAll("[data-remove-file]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const file = String(btn.dataset.removeFile || "");
        if (!file) return;
        if (global.BotButtonsFiles?.removeFileFromState) {
          global.BotButtonsFiles.removeFileFromState(ctx, file);
        }
        EditorView.renderEditor(ctx);
      });
    });
    document.querySelectorAll("[data-remove-file-rule]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const idx = Number(btn.dataset.removeFileRule);
        if (Number.isNaN(idx)) return;
        if (global.BotButtonsFiles?.removeConditionalRule) {
          global.BotButtonsFiles.removeConditionalRule(ctx, idx);
        }
        EditorView.renderEditor(ctx);
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
    if (global.TelegramTextEditor?.closeEmojiPicker) global.TelegramTextEditor.closeEmojiPicker();
  };

  EditorView.openTextEditorModal = openTextEditorModal;

  global.BotButtonsEditorView = EditorView;
})(window);
