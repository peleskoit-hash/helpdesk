(function initBotButtonsFiles(global) {
  function fieldMeta(ctx, field) {
    return (ctx.state.conditionalFields || []).find((item) => item.field === field) || null;
  }

  function fieldType(ctx, field) {
    const found = fieldMeta(ctx, field);
    return found?.type || "text";
  }

  function fieldLabel(ctx, field) {
    return fieldMeta(ctx, field)?.label || ctx.FIELD_LABELS[field] || field;
  }

  function operatorLabel(ctx, type, value) {
    const options = ctx.OPERATOR_OPTIONS[type] || ctx.OPERATOR_OPTIONS.text;
    return options.find((o) => o.value === value)?.label || value;
  }

  function renderSelectedFiles(ctx) {
    const files = ctx.state.selectedFiles || [];
    if (!files.length) {
      return '<div class="status">Файлы не выбраны</div>';
    }
    return `
      <ul class="bot-file-list">
        ${files.map((file) => `<li><label><input type="checkbox" class="bot-file-selected" value="${ctx.escapeHtml(file)}" /> ${ctx.escapeHtml(file)}</label></li>`).join("")}
      </ul>
    `;
  }

  function openFilePickerModal(ctx) {
    const available = ctx.state.availableFiles || [];
    if (!available.length) {
      alert("В папке TG buttons/files нет доступных файлов.");
      return;
    }
    const items = available.map((file) => `
      <label class="bot-file-picker-item">
        <span>${ctx.escapeHtml(file)}</span>
        <input type="checkbox" value="${ctx.escapeHtml(file)}" ${ctx.state.selectedFiles.includes(file) ? "checked" : ""} />
      </label>
    `).join("");
    const html = `
      <div class="modal-overlay">
        <div class="modal-dialog bot-file-picker-dialog">
          <div class="modal-header"><h3>Выбор файла</h3></div>
          <div class="modal-body">
            <div class="bot-file-picker-list">${items}</div>
          </div>
          <div class="modal-footer">
            <button type="button" class="action" id="cancelFilePickerBtn">Отмена</button>
            <button type="button" class="action action-primary" id="applyFilePickerBtn">Добавить</button>
          </div>
        </div>
      </div>
    `;
    const wrapper = document.createElement("div");
    wrapper.innerHTML = html;
    const overlay = wrapper.firstElementChild;
    document.body.appendChild(overlay);
    overlay.querySelector("#cancelFilePickerBtn")?.addEventListener("click", () => overlay.remove());
    overlay.querySelector("#applyFilePickerBtn")?.addEventListener("click", () => {
      const checked = Array.from(overlay.querySelectorAll(".bot-file-picker-item input:checked"))
        .map((input) => input.value);
      const merged = Array.from(new Set([...(ctx.state.selectedFiles || []), ...checked]));
      ctx.state.selectedFiles = merged;
      ctx.state.filesOverridden = true;
      ctx.renderEditor();
      overlay.remove();
    });
  }

  function normalizeConditionalRuleForState(ctx, rawRule) {
    const base = { conditions: [], files: [] };
    if (!rawRule || typeof rawRule !== "object") return base;
    const conditions = Array.isArray(rawRule.conditions) ? rawRule.conditions : [];
    const files = Array.isArray(rawRule.files) ? rawRule.files : [];
    return {
      conditions: conditions
        .filter((c) => c && typeof c === "object")
        .map((c) => {
          const field = String(c.field || "");
          const type = fieldType(ctx, field);
          let operator = String(c.operator || "");
          let value = c.value == null ? "" : String(c.value);
          if (type === "bool") {
            if (operator === "is_true") {
              operator = "eq";
              value = "true";
            } else if (operator === "is_false") {
              operator = "eq";
              value = "false";
            } else if (!operator) {
              operator = "eq";
            }
          }
          return { field, operator, value };
        }),
      files: files.map((f) => String(f || "")).filter(Boolean),
    };
  }

  function renderConditionalRules(ctx) {
    const rules = ctx.state.conditionalRules || [];
    if (!rules.length) {
      return '<div class="status">Условные правила не добавлены</div>';
    }
    return rules.map((rule, ruleIndex) => `
      <div class="bot-conditional-rule">
        <div class="bot-conditional-rule-head">
          <strong>Правило ${ruleIndex + 1}</strong>
          <button type="button" class="action action-danger" data-remove-rule="${ruleIndex}">Удалить условие</button>
        </div>
        <div class="bot-conditional-conditions">
          ${(rule.conditions || []).map((condition, cidx) => {
            const meta = fieldMeta(ctx, condition.field);
            const isUnavailableField = !!condition.field && !meta;
            const type = meta?.type || "text";
            const availableOps = Array.isArray(meta?.operators) && meta.operators.length
              ? meta.operators
              : (ctx.OPERATOR_OPTIONS[type] || ctx.OPERATOR_OPTIONS.text).map((o) => o.value);
            const selectedOp = condition.operator || "";
            const needsValue = !!condition.field
              && !!selectedOp
              && !["empty", "not_empty"].includes(selectedOp);
            const showOperator = !!condition.field && type !== "bool";
            const selectValues = Array.isArray(meta?.values) ? meta.values : [];
            const hasSelectValues = type === "select" && selectValues.length > 0;
            const boolValues = type === "bool" ? [{ value: "true", label: "Да" }, { value: "false", label: "Нет" }] : [];
            const isValueDropdown = (type === "bool" || hasSelectValues) && needsValue;
            const fieldOptions = (ctx.state.conditionalFields || [])
              .map((f) => `<option value="${ctx.escapeHtml(f.field)}" ${f.field === condition.field ? "selected" : ""}>${ctx.escapeHtml(fieldLabel(ctx, f.field))}</option>`)
              .join("");
            const unavailableFieldOption = isUnavailableField
              ? `<option value="${ctx.escapeHtml(condition.field)}" selected>${ctx.escapeHtml(`${condition.field} (поле недоступно)`)}</option>`
              : "";
            const opOptions = availableOps
              .map((op) => {
                const label = operatorLabel(ctx, type, op);
                return `<option value="${ctx.escapeHtml(op)}" ${op === selectedOp ? "selected" : ""}>${ctx.escapeHtml(label)}</option>`;
              })
              .join("");
            const valueOptions = (type === "bool" ? boolValues : selectValues)
              .map((v) => {
                const optionValue = typeof v === "string" ? v : String(v.value ?? "");
                const optionLabel = typeof v === "string" ? v : String(v.label ?? v.value ?? "");
                return `<option value="${ctx.escapeHtml(optionValue)}" ${optionValue === (condition.value || "") ? "selected" : ""}>${ctx.escapeHtml(optionLabel)}</option>`;
              })
              .join("");
            const noValuesOption = (type === "select" && !selectValues.length)
              ? '<option value="" selected>Нет доступных значений</option>'
              : '<option value="" selected>Не выбрано</option>';
            return `
              <div class="bot-conditional-condition-row">
                <span>Если</span>
                <select class="input" data-cond-field="${ruleIndex}:${cidx}">
                  <option value="" ${condition.field ? "" : "selected"}>Не выбрано</option>
                  ${unavailableFieldOption}
                  ${fieldOptions}
                </select>
                ${!condition.field
                  ? '<select class="input" disabled><option>Сначала выберите поле</option></select>'
                  : (showOperator ? `
                <select class="input" data-cond-op="${ruleIndex}:${cidx}">
                  <option value="" ${selectedOp ? "" : "selected"}>Не выбрано</option>
                  ${opOptions}
                </select>` : '<span class="status">равно</span>')
                }
                ${!needsValue ? "" : (isValueDropdown
                  ? `<select class="input" data-cond-value="${ruleIndex}:${cidx}">${(condition.value || "") ? '<option value="">Не выбрано</option>' : noValuesOption}${valueOptions}</select>`
                  : `<input class="input" data-cond-value="${ruleIndex}:${cidx}" value="${ctx.escapeHtml(condition.value || "")}" />`
                )}
              </div>
            `;
          }).join("")}
        </div>
        <div class="bot-conditional-files">
          <div class="status">Файлы: ${(rule.files || []).map((f) => ctx.escapeHtml(f)).join(", ") || "не выбраны"}</div>
          <div class="bot-file-actions">
            <button type="button" class="action" data-edit-rule-files="${ruleIndex}">Выбрать файлы</button>
          </div>
        </div>
      </div>
    `).join("");
  }

  function openConditionalRuleFilesModal(ctx, ruleIndex) {
    const rule = ctx.state.conditionalRules?.[ruleIndex];
    if (!rule) return;
    const available = ctx.state.availableFiles || [];
    if (!available.length) {
      alert("В папке TG buttons/files нет доступных файлов.");
      return;
    }
    const items = available.map((file) => `
      <label class="bot-file-picker-item">
        <span>${ctx.escapeHtml(file)}</span>
        <input type="checkbox" value="${ctx.escapeHtml(file)}" ${rule.files.includes(file) ? "checked" : ""} />
      </label>
    `).join("");
    const wrapper = document.createElement("div");
    wrapper.innerHTML = `
      <div class="modal-overlay">
        <div class="modal-dialog bot-file-picker-dialog">
          <div class="modal-header"><h3>Файлы для правила</h3></div>
          <div class="modal-body"><div class="bot-file-picker-list">${items}</div></div>
          <div class="modal-footer">
            <button type="button" class="action" id="cancelRuleFilesBtn">Отмена</button>
            <button type="button" class="action action-primary" id="applyRuleFilesBtn">Применить</button>
          </div>
        </div>
      </div>
    `;
    const overlay = wrapper.firstElementChild;
    document.body.appendChild(overlay);
    overlay.querySelector("#cancelRuleFilesBtn")?.addEventListener("click", () => overlay.remove());
    overlay.querySelector("#applyRuleFilesBtn")?.addEventListener("click", () => {
      const checked = Array.from(overlay.querySelectorAll(".bot-file-picker-item input:checked")).map((input) => input.value);
      ctx.state.conditionalRules[ruleIndex].files = Array.from(new Set(checked));
      ctx.state.conditionalFilesOverridden = true;
      ctx.renderEditor();
      overlay.remove();
    });
  }

  global.BotButtonsFiles = {
    renderSelectedFiles,
    openFilePickerModal,
    fieldType,
    fieldMeta,
    fieldLabel,
    operatorLabel,
    normalizeConditionalRuleForState,
    renderConditionalRules,
    openConditionalRuleFilesModal,
  };
})(window);

