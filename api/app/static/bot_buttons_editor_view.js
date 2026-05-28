(function initBotButtonsEditorView(global) {
  const EditorView = {};
  const TEXT_MODAL_ID = "botTextEditorModal";
  let textModalEscHandler = null;

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
    const modal = document.getElementById(TEXT_MODAL_ID);
    if (modal) modal.remove();
    if (textModalEscHandler) {
      document.removeEventListener("keydown", textModalEscHandler);
      textModalEscHandler = null;
    }
  }

  function openTextEditorModal(options) {
    closeTextEditorModal();
    const {
      title,
      value,
      onApply,
      withTelegramPreview = false,
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
        <div class="modal-body bot-text-editor-body ${withTelegramPreview ? "bot-text-editor-body-split" : ""}">
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
    const initialValue = String(value || "");
    let backdropMouseDown = false;

    const syncPreview = () => {
      if (!preview || !textarea) return;
      preview.textContent = textarea.value || "Текст не задан";
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
    textarea?.addEventListener("input", syncPreview);
    syncPreview();

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
      if (event.key === "Escape") requestClose(false);
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

  global.BotButtonsEditorView = EditorView;
})(window);

