(function initBotButtonsEditor(global) {
  const Editor = {};

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
      || (state.automationKeyValue || "") !== (state.savedAutomationKeyValue || "");
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
    state.automationKeyValue = state.savedAutomationKeyValue || "";
  };

  Editor.renderAssigneeOptions = function renderAssigneeOptions(ctx) {
    const { state, escapeHtml } = ctx;
    const options = ["<option value=''>Выберите пользователя</option>"];
    state.assignees.forEach((u) => {
      options.push(`<option value="${escapeHtml(u.id)}" ${u.id === state.fixedAssigneeUserId ? "selected" : ""}>${escapeHtml(u.full_name)} (${escapeHtml(u.role_label || u.role)})</option>`);
    });
    return options.join("");
  };

  Editor.openEditor = async function openEditor(ctx, leaf) {
    const { state, api, setStatus, render, normalizeConditionalRuleForState } = ctx;
    setStatus(`Загрузка настройки "${leaf.label}"...`);
    const payload = await api(`/api/v1/settings/bot-buttons/item?button_key=${encodeURIComponent(leaf.technical_key)}&menu_type=${encodeURIComponent(state.selectedMenu || "")}`);
    state.selectedLeaf = {
      ...leaf,
      is_db_created: !!payload?.is_db_created,
      is_archived: !!payload?.is_archived,
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
    state.automationKey = String(payload?.automation_key || "");
    state.automationTitle = String(payload?.automation_title || "");
    state.automationDescription = String(payload?.automation_description || "");
    state.automationFrom = String(payload?.automation_from || "none");
    state.automationStatus = String(payload?.automation_status || "not_set");
    state.automationAvailable = !!payload?.automation_available;
    state.automationHandler = String(payload?.automation_handler || "");
    state.savedAutomationKeyValue = String(payload?.automation_key ?? "");
    state.automationKeyValue = state.savedAutomationKeyValue;

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
  };

  // UI renderer kept in main for now? no - exported placeholder for orchestration compatibility.
  Editor.renderEditor = function renderEditor(ctx) {
    return ctx.renderEditorImpl();
  };

  global.BotButtonsEditor = Editor;
})(window);
