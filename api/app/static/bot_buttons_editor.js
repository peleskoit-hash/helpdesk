(function initBotButtonsEditor(global) {
  const Editor = {};
  const SYSTEM_FIRST_CONNECTION_BUTTON_KEY = "__system__|first_connection";
  const EDITABLE_WORKFLOW_TYPES = ["ticket", "task", "auto"];
  const WORKFLOW_TYPE_LABELS = {
    ticket: "\u0417\u0430\u044f\u0432\u043a\u0430",
    task: "\u0417\u0430\u0434\u0430\u0447\u0430",
    auto: "\u0410\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0447\u0435\u0441\u043a\u0430\u044f \u0437\u0430\u0434\u0430\u0447\u0430",
    submenu: "\u041f\u043e\u0434\u043c\u0435\u043d\u044e",
    group_task: "\u0413\u0440\u0443\u043f\u043f\u043e\u0432\u0430\u044f \u0437\u0430\u0434\u0430\u0447\u0430",
    group_task_child: "\u0414\u043e\u0447\u0435\u0440\u043d\u044f\u044f \u0437\u0430\u0434\u0430\u0447\u0430",
  };

  Editor.normalizeWorkflowType = function normalizeWorkflowType(value) {
    return String(value || "").trim().toLowerCase();
  };

  Editor.getWorkflowTypeLabel = function getWorkflowTypeLabel(value) {
    const normalized = Editor.normalizeWorkflowType(value);
    return WORKFLOW_TYPE_LABELS[normalized] || normalized || "-";
  };

  Editor.isEditableWorkflowType = function isEditableWorkflowType(value) {
    return EDITABLE_WORKFLOW_TYPES.includes(Editor.normalizeWorkflowType(value));
  };

  Editor.isAutomationAllowedForWorkflowType = function isAutomationAllowedForWorkflowType(workflowType, automationType) {
    const workflow = Editor.normalizeWorkflowType(workflowType);
    const automation = String(automationType || "none").trim() || "none";
    if (!Editor.isEditableWorkflowType(workflow) || automation === "none") return true;
    if (workflow === "ticket") return automation === "send_files";
    if (workflow === "task") return automation === "send_files" || automation === "bitrix_deal_phone_lookup";
    if (workflow === "auto") return automation === "send_files" || automation === "message_only";
    return true;
  };

  Editor.canChangeWorkflowType = function canChangeWorkflowType(leaf) {
    const technicalKey = String(leaf?.technical_key || leaf?.button_key || "").trim();
    if (!technicalKey || technicalKey === SYSTEM_FIRST_CONNECTION_BUTTON_KEY) return false;
    return Editor.isEditableWorkflowType(leaf?.workflow_type);
  };

  Editor.getWorkflowTypeOptions = function getWorkflowTypeOptions(leaf) {
    if (!Editor.canChangeWorkflowType(leaf)) return [];
    return EDITABLE_WORKFLOW_TYPES.map((value) => ({
      value,
      label: Editor.getWorkflowTypeLabel(value),
    }));
  };

  Editor.applyWorkflowTypeDraft = function applyWorkflowTypeDraft(ctx, nextWorkflowType) {
    const { state } = ctx;
    const nextType = Editor.normalizeWorkflowType(nextWorkflowType);
    const currentType = Editor.normalizeWorkflowType(state.editorWorkflowType || state.savedWorkflowType || state.selectedLeaf?.workflow_type);
    if (!nextType || nextType === currentType) return true;
    if (!Editor.canChangeWorkflowType(state.selectedLeaf) || !Editor.isEditableWorkflowType(nextType)) return false;
    const currentLabel = Editor.getWorkflowTypeLabel(currentType);
    const nextLabel = Editor.getWorkflowTypeLabel(nextType);
    const confirmed = window.confirm(
      `\u0412\u044b \u043c\u0435\u043d\u044f\u0435\u0442\u0435 \u0442\u0438\u043f \u0441\u0443\u0449\u0435\u0441\u0442\u0432\u0443\u044e\u0449\u0435\u0439 \u043a\u043d\u043e\u043f\u043a\u0438 \u0441 "${currentLabel}" \u043d\u0430 "${nextLabel}".\n\n` +
      `\u0427\u0430\u0441\u0442\u044c \u0434\u0430\u043d\u043d\u044b\u0445 \u043c\u043e\u0436\u0435\u0442 \u0441\u0442\u0430\u0442\u044c \u043d\u0435\u043f\u0440\u0438\u043c\u0435\u043d\u0438\u043c\u043e\u0439 \u0434\u043b\u044f \u043d\u043e\u0432\u043e\u0433\u043e \u0442\u0438\u043f\u0430. \u0422\u0430\u043a\u0438\u0435 \u043f\u043e\u043b\u044f \u0431\u0443\u0434\u0443\u0442 \u043e\u0447\u0438\u0449\u0435\u043d\u044b \u0438\u043b\u0438 \u043d\u0435 \u0431\u0443\u0434\u0443\u0442 \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u044b.\n\n` +
      `\u041f\u0440\u043e\u0434\u043e\u043b\u0436\u0438\u0442\u044c?`
    );
    if (!confirmed) return false;
    state.editorWorkflowType = nextType;
    if (!Editor.isAutomationAllowedForWorkflowType(nextType, state.automationTypeValue)) {
      state.automationTypeValue = "none";
      Editor.syncAutomationSelectionMeta(ctx);
    }
    return true;
  };

  Editor.isSubmenuLeaf = function isSubmenuLeaf(leaf) {
    return String(leaf?.workflow_type || "").trim().toLowerCase() === "submenu";
  };

  Editor.syncAutomationSelectionMeta = function syncAutomationSelectionMeta(ctx) {
    const { state } = ctx;
    const selectedType = String(state.automationTypeValue || "none").trim() || "none";
    state.automationTypeValue = selectedType;
    const options = Array.isArray(state.availableAutomations) ? state.availableAutomations : [];
    const selected = options.find((item) => String(item?.key || "") === selectedType) || null;
    const selectedMeta = selected || (selectedType === "none"
      ? {
          key: "none",
          title: "Не использовать",
          description: "Автоматизация не применяется.",
          available: true,
          handler: "",
        }
      : null);
    state.automationType = selectedType;
    state.automationKeyValue = selectedType === "none" ? "" : selectedType;
    state.automationKey = String(selectedMeta?.key || selectedType || "none");
    state.automationTitle = String(selectedMeta?.title || "");
    state.automationDescription = String(selectedMeta?.description || "");
    state.automationAvailable = !!selectedMeta?.available;
    state.automationHandler = String(selectedMeta?.handler || "");
    state.automationStatus = selectedType === "none"
      ? "not_applied"
      : (selectedMeta
        ? (selectedMeta.available ? "available" : "not_found")
        : "not_found");
  };

  Editor.hasUnsavedChanges = function hasUnsavedChanges(ctx) {
    const { state } = ctx;
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
      || Editor.normalizeWorkflowType(state.editorWorkflowType) !== Editor.normalizeWorkflowType(state.savedWorkflowType)
      || (state.automationTypeValue || "none") !== (state.savedAutomationTypeValue || "none");
  };

  Editor.confirmDiscard = function confirmDiscard(ctx) {
    if (!Editor.hasUnsavedChanges(ctx)) return true;
    return window.confirm("Есть несохранённые изменения. Уйти без сохранения?");
  };

  Editor.discardEditorChanges = function discardEditorChanges(ctx) {
    const { state } = ctx;
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
    state.editorWorkflowType = state.savedWorkflowType || state.selectedLeaf?.workflow_type || "";
    state.automationTypeValue = state.savedAutomationTypeValue || "none";
    Editor.syncAutomationSelectionMeta(ctx);
  };

  Editor.renderAssigneeOptions = function renderAssigneeOptions(ctx) {
    const { state, escapeHtml } = ctx;
    const options = ["<option value=''>Выберите пользователя</option>"];
    state.assignees.forEach((u) => {
      options.push(`<option value="${escapeHtml(u.id)}" ${u.id === state.fixedAssigneeUserId ? "selected" : ""}>${escapeHtml(u.full_name)} (${escapeHtml(u.role_label || u.role)})</option>`);
    });
    return options.join("");
  };

  Editor.validateSendFilesProjection = function validateSendFilesProjection(ctx, sourceFiles, sourceConditionalRules) {
    const helpers = global.BotButtonsFiles || {};
    if (!helpers.hasProjectionLoss) {
      return { ok: true, snapshot: null, error: "" };
    }
    const result = helpers.hasProjectionLoss(ctx, sourceFiles || [], sourceConditionalRules || []);
    return {
      ok: !!result?.equal,
      snapshot: result?.sourceSnapshot || null,
      error: result?.equal
        ? ""
        : "Редактор не смог безопасно восстановить настройки отправки файлов из сохранённых данных. Сохранение заблокировано, чтобы не затереть условия.",
    };
  };

  Editor.openEditor = async function openEditor(ctx, leaf) {
    const { state, api, setStatus, render, normalizeConditionalRuleForState } = ctx;
    setStatus(`Загрузка настройки "${leaf.label}"...`);
    const payload = await api(`/api/v1/settings/bot-buttons/item?button_key=${encodeURIComponent(leaf.technical_key)}&menu_type=${encodeURIComponent(state.selectedMenu || "")}`);
    state.selectedLeaf = {
      ...leaf,
      is_db_created: !!payload?.is_db_created,
      is_archived: !!payload?.is_archived,
      editor_mode: Editor.isSubmenuLeaf(leaf) ? "submenu" : "default",
    };

    state.savedText = String(payload?.instruction_text || "");
    state.editorText = state.savedText;
    state.savedClientText = String(payload?.client_text ?? "");
    state.editorClientText = state.savedClientText;
    const effectiveLabel = String(payload?.button_label_effective || payload?.button_label || leaf.label || "");
    state.savedBotLabel = String(payload?.label_override ?? effectiveLabel);
    state.editorBotLabel = state.savedBotLabel;

    state.savedSystemText = String(payload?.system_text || "");
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
    const sourceFiles = payload?.files;
    state.savedSelectedFiles = Array.isArray(sourceFiles) ? sourceFiles : [];
    state.selectedFiles = [...state.savedSelectedFiles];
    state.savedConditionalFilesOverridden = !!payload?.conditional_files_overridden;
    state.conditionalFilesOverridden = state.savedConditionalFilesOverridden;
    const sourceConditional = payload?.conditional_files;
    const normalizedConditional = Array.isArray(sourceConditional) ? sourceConditional.map(normalizeConditionalRuleForState) : [];
    state.savedConditionalRules = normalizedConditional;
    state.conditionalRules = JSON.parse(JSON.stringify(normalizedConditional));
    state.savedIsEnabled = payload?.is_enabled !== false;
    state.isEnabled = state.savedIsEnabled;
    state.savedWorkflowType = Editor.normalizeWorkflowType(payload?.workflow_type || leaf?.workflow_type);
    state.editorWorkflowType = state.savedWorkflowType;
    state.automationFrom = String(payload?.automation_from || "none");
    const apiType = String(payload?.automation_type || "").trim();
    const hasLegacyFiles = state.savedSelectedFiles.length > 0 || normalizedConditional.length > 0;
    const inferredAutomationType = payload?.automation_key
      ? String(payload.automation_key)
      : ((apiType && apiType !== "none")
        ? apiType
        : (hasLegacyFiles ? "send_files" : "none"));
    state.savedAutomationTypeValue = String(inferredAutomationType || "none");
    state.automationTypeValue = state.savedAutomationTypeValue;
    Editor.syncAutomationSelectionMeta(ctx);
    const projectionCheck = Editor.validateSendFilesProjection(ctx, state.savedSelectedFiles, normalizedConditional);
    state.loadedSendFilesSnapshot = projectionCheck.snapshot;
    state.sendFilesProjectionError = projectionCheck.error;

    state.view = "editor";
    render();
    setStatus("Готово");
  };

  Editor.saveInstruction = async function saveInstruction(ctx) {
    const {
      state,
      api,
      setStatus,
      refreshMenuStructure,
      fieldMeta,
      fieldLabel,
    } = ctx;
    const leaf = state.selectedLeaf;
    if (!leaf || !state.selectedMenu) return;
    const requestWorkflowType = Editor.normalizeWorkflowType(state.editorWorkflowType || leaf.workflow_type);
    const isSubmenu = requestWorkflowType === "submenu";
    if (!String(state.editorBotLabel || "").trim()) {
      alert("Название в боте не может быть пустым.");
      return;
    }
    if (!isSubmenu && state.fixedAssigneeEnabled && !state.fixedAssigneeUserId) {
      alert("Выберите фиксированного ответственного.");
      return;
    }

    const sendFilesPayload = global.BotButtonsFiles?.buildSendFilesPayload
      ? global.BotButtonsFiles.buildSendFilesPayload(ctx)
      : {
          files: [...(state.selectedFiles || [])],
          conditionalRules: JSON.parse(JSON.stringify(state.conditionalRules || [])),
        };
    if (!isSubmenu && state.sendFilesProjectionError) {
      alert(state.sendFilesProjectionError);
      return;
    }
    let conditionalPayload = null;
    if (!isSubmenu && state.conditionalFilesOverridden) {
      const rules = JSON.parse(JSON.stringify(sendFilesPayload.conditionalRules || []));
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
    if (!isSubmenu && !Editor.isAutomationAllowedForWorkflowType(requestWorkflowType, state.automationTypeValue)) {
      state.automationTypeValue = "none";
      Editor.syncAutomationSelectionMeta(ctx);
    }

    const requestBody = isSubmenu
      ? {
          client_text_overridden: true,
          client_text: state.editorClientText,
          label_overridden: true,
          label_override: state.editorBotLabel,
          is_enabled: state.isEnabled,
          visibility_overridden: true,
        }
      : {
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
          files: state.filesOverridden ? (sendFilesPayload.files || []) : null,
          conditional_files_overridden: state.conditionalFilesOverridden,
          conditional_files: conditionalPayload,
          automation_overridden: true,
          automation_type: state.automationTypeValue || "none",
          is_enabled: state.isEnabled,
          visibility_overridden: true,
        };

    await api(
      `/api/v1/settings/bot-buttons/item/instruction?button_key=${encodeURIComponent(leaf.technical_key)}&menu_type=${encodeURIComponent(state.selectedMenu)}&workflow_type=${encodeURIComponent(requestWorkflowType)}&button_path=${encodeURIComponent(leaf.full_path || leaf.label)}&button_label=${encodeURIComponent(leaf.label)}`,
      {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(requestBody),
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
    state.selectedFiles = [...(sendFilesPayload.files || [])];
    state.savedSelectedFiles = [...(sendFilesPayload.files || [])];
    state.savedConditionalFilesOverridden = state.conditionalFilesOverridden;
    state.conditionalRules = JSON.parse(JSON.stringify(sendFilesPayload.conditionalRules || []));
    state.savedConditionalRules = JSON.parse(JSON.stringify(sendFilesPayload.conditionalRules || []));
    {
      const projectionCheck = Editor.validateSendFilesProjection(ctx, state.savedSelectedFiles, state.savedConditionalRules);
      state.loadedSendFilesSnapshot = projectionCheck.snapshot;
      state.sendFilesProjectionError = projectionCheck.error;
    }
    state.savedIsEnabled = state.isEnabled;
    state.savedWorkflowType = requestWorkflowType;
    state.editorWorkflowType = requestWorkflowType;
    state.selectedLeaf = {
      ...state.selectedLeaf,
      workflow_type: requestWorkflowType,
      editor_mode: isSubmenu ? "submenu" : "default",
    };
    state.savedAutomationTypeValue = state.automationTypeValue;
    state.savedAutomationKeyValue = state.automationKeyValue;

    await refreshMenuStructure();
    setStatus("Сохранено");
  };

  // UI renderer kept in main for now? no - exported placeholder for orchestration compatibility.
  Editor.renderEditor = function renderEditor(ctx) {
    return ctx.renderEditorImpl();
  };

  global.BotButtonsEditor = Editor;
})(window);
