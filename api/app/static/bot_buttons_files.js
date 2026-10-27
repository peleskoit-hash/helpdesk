(function initBotButtonsFiles(global) {
  function deepClone(value) {
    return value == null ? value : JSON.parse(JSON.stringify(value));
  }

  function normalizeFileList(values) {
    return Array.from(
      new Set(
        (Array.isArray(values) ? values : [])
          .map((value) => String(value || "").trim())
          .filter(Boolean),
      ),
    );
  }

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

  function normalizeConditionalRuleForState(ctx, rawRule) {
    const source = rawRule && typeof rawRule === "object" ? rawRule : {};
    const conditions = Array.isArray(source.conditions) ? source.conditions : [];
    const files = Array.isArray(source.files) ? source.files : [];
    const normalized = {};
    Object.entries(source).forEach(([key, value]) => {
      if (key === "conditions" || key === "files") return;
      normalized[key] = deepClone(value);
    });
    normalized.conditions = conditions
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
      });
    normalized.files = normalizeFileList(files);
    return normalized;
  }

  function renderRuleConditions(ctx, ruleIndex, conditions) {
    return (conditions || []).map((condition, cidx) => {
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
    }).join("");
  }

  function getSendFileEntries(ctx) {
    const entries = [];
    const byFile = new Map();
    const ensureEntry = (file) => {
      const normalized = String(file || "").trim();
      if (!normalized) return null;
      if (!byFile.has(normalized)) {
        const entry = { file: normalized, unconditional: false, rules: [] };
        byFile.set(normalized, entry);
        entries.push(entry);
      }
      return byFile.get(normalized);
    };

    normalizeFileList(ctx.state.selectedFiles || []).forEach((file) => {
      const entry = ensureEntry(file);
      if (entry) entry.unconditional = true;
    });

    (Array.isArray(ctx.state.conditionalRules) ? ctx.state.conditionalRules : []).forEach((rawRule, ruleIndex) => {
      const normalizedRule = normalizeConditionalRuleForState(ctx, rawRule);
      normalizedRule.files.forEach((file) => {
        const entry = ensureEntry(file);
        if (!entry) return;
        entry.rules.push({
          ruleIndex,
          conditions: deepClone(normalizedRule.conditions || []),
          extra: Object.fromEntries(
            Object.entries(normalizedRule).filter(([key]) => key !== "conditions" && key !== "files"),
          ),
        });
      });
    });

    entries.forEach((entry) => {
      if (entry.rules.length) {
        entry.unconditional = false;
      }
    });

    return entries;
  }

  function _sortedConditions(conditions) {
    return (Array.isArray(conditions) ? conditions : [])
      .map((condition) => ({
        field: String(condition?.field || ""),
        operator: String(condition?.operator || ""),
        value: condition?.value == null ? null : String(condition.value),
      }))
      .sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
  }

  function buildSendFilesSnapshot(ctxLike) {
    const ctx = {
      ...ctxLike,
      state: {
        ...(ctxLike?.state || {}),
        selectedFiles: normalizeFileList(ctxLike?.state?.selectedFiles || []),
        conditionalRules: Array.isArray(ctxLike?.state?.conditionalRules)
          ? ctxLike.state.conditionalRules.map((rule) => normalizeConditionalRuleForState(ctxLike, rule))
          : [],
      },
    };
    return getSendFileEntries(ctx)
      .map((entry) => ({
        file: String(entry.file || ""),
        unconditional: !!entry.unconditional,
        rules: (Array.isArray(entry.rules) ? entry.rules : [])
          .map((ruleEntry) => ({
            conditions: _sortedConditions(ruleEntry.conditions),
            extra: Object.fromEntries(
              Object.entries(ruleEntry.extra || {}).sort(([left], [right]) => left.localeCompare(right)),
            ),
          }))
          .sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b))),
      }))
      .sort((a, b) => a.file.localeCompare(b.file));
  }

  function hasProjectionLoss(ctxLike, sourceFiles, sourceConditionalRules) {
    const sourceCtx = {
      ...ctxLike,
      state: {
        ...(ctxLike?.state || {}),
        selectedFiles: normalizeFileList(sourceFiles || []),
        conditionalRules: Array.isArray(sourceConditionalRules)
          ? sourceConditionalRules.map((rule) => normalizeConditionalRuleForState(ctxLike, rule))
          : [],
      },
    };
    const roundTripPayload = buildSendFilesPayload({
      ...ctxLike,
      state: {
        ...(ctxLike?.state || {}),
        selectedFiles: normalizeFileList(ctxLike?.state?.selectedFiles || []),
        conditionalRules: Array.isArray(ctxLike?.state?.conditionalRules)
          ? ctxLike.state.conditionalRules.map((rule) => normalizeConditionalRuleForState(ctxLike, rule))
          : [],
      },
    });
    const roundTripCtx = {
      ...ctxLike,
      state: {
        ...(ctxLike?.state || {}),
        selectedFiles: roundTripPayload.files || [],
        conditionalRules: roundTripPayload.conditionalRules || [],
      },
    };
    const sourceSnapshot = buildSendFilesSnapshot(sourceCtx);
    const roundTripSnapshot = buildSendFilesSnapshot(roundTripCtx);
    return {
      sourceSnapshot,
      roundTripSnapshot,
      equal: JSON.stringify(sourceSnapshot) === JSON.stringify(roundTripSnapshot),
    };
  }

  function renderSelectedFiles(ctx) {
    const entries = getSendFileEntries(ctx);
    if (!entries.length) {
      return '<div class="status">Файлы не выбраны</div>';
    }
    return `
      <div class="bot-send-files-list">
        ${entries.map((entry) => {
          const hasRules = entry.rules.length > 0;
          return `
            <div class="bot-conditional-rule">
              <div class="bot-conditional-rule-head">
                <strong>${ctx.escapeHtml(entry.file)}</strong>
                <div class="bot-file-actions">
                  <button type="button" class="action" data-add-file-condition="${ctx.escapeHtml(entry.file)}">${hasRules ? "Добавить ещё условие" : "Добавить условие"}</button>
                  <button type="button" class="action action-danger" data-remove-file="${ctx.escapeHtml(entry.file)}">Удалить файл</button>
                </div>
              </div>
              <div class="status">${hasRules ? `Условий: ${entry.rules.length}` : "Отправляется всегда"}</div>
              ${hasRules ? `
                <div class="bot-send-files-rules">
                  ${entry.rules.map((ruleEntry, index) => `
                    <div class="bot-conditional-rule">
                      <div class="bot-conditional-rule-head">
                        <strong>Условие ${index + 1}</strong>
                        <button type="button" class="action action-danger" data-remove-file-rule="${ruleEntry.ruleIndex}">Удалить условие</button>
                      </div>
                      <div class="bot-conditional-conditions">
                        ${renderRuleConditions(ctx, ruleEntry.ruleIndex, ruleEntry.conditions)}
                      </div>
                    </div>
                  `).join("")}
                </div>
              ` : ""}
            </div>
          `;
        }).join("")}
      </div>
    `;
  }

  async function openFilePickerModal(ctx) {
    const currentFiles = new Set(getSendFileEntries(ctx).map((entry) => entry.file));
    const available = (ctx.state.availableFiles || []).filter((file) => !currentFiles.has(file));
    if (!available.length) {
      await window.HelpdeskDialogs.alert("В папке TG buttons/files нет новых доступных файлов");
      return;
    }
    const items = available.map((file) => `
      <label class="bot-file-picker-item">
        <span>${ctx.escapeHtml(file)}</span>
        <input type="checkbox" value="${ctx.escapeHtml(file)}" />
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
      const checked = Array.from(overlay.querySelectorAll(".bot-file-picker-item input:checked")).map((input) => input.value);
      if (!checked.length) {
        overlay.remove();
        return;
      }
      const merged = [...normalizeFileList(ctx.state.selectedFiles || [])];
      checked.forEach((file) => {
        if (!merged.includes(file)) {
          merged.push(file);
        }
      });
      ctx.state.selectedFiles = merged;
      ctx.state.filesOverridden = true;
      ctx.renderEditor();
      overlay.remove();
    });
  }

  function addConditionToFile(ctx, file) {
    const normalized = String(file || "").trim();
    if (!normalized) return;
    ctx.state.selectedFiles = normalizeFileList(ctx.state.selectedFiles || []).filter((item) => item !== normalized);
    ctx.state.conditionalRules = [...(ctx.state.conditionalRules || []), {
      conditions: [{ field: "", operator: "", value: "" }],
      files: [normalized],
    }];
    ctx.state.filesOverridden = true;
    ctx.state.conditionalFilesOverridden = true;
  }

  function removeFileFromState(ctx, file) {
    const normalized = String(file || "").trim();
    if (!normalized) return;
    ctx.state.selectedFiles = normalizeFileList(ctx.state.selectedFiles || []).filter((item) => item !== normalized);
    const nextRules = [];
    for (const rawRule of (ctx.state.conditionalRules || [])) {
      const normalizedRule = normalizeConditionalRuleForState(ctx, rawRule);
      normalizedRule.files = normalizedRule.files.filter((item) => item !== normalized);
      if (normalizedRule.files.length) {
        nextRules.push(normalizedRule);
      }
    }
    ctx.state.conditionalRules = nextRules;
    ctx.state.filesOverridden = true;
    ctx.state.conditionalFilesOverridden = true;
  }

  function removeConditionalRule(ctx, ruleIndex) {
    const currentRules = Array.isArray(ctx.state.conditionalRules) ? ctx.state.conditionalRules : [];
    const targetRule = currentRules[ruleIndex];
    if (!targetRule) return;
    const targetFiles = normalizeFileList(targetRule.files || []);
    const nextRules = currentRules
      .filter((_, idx) => idx !== ruleIndex)
      .map((rule) => normalizeConditionalRuleForState(ctx, rule));
    const remainingConditionalFiles = new Set(nextRules.flatMap((rule) => normalizeFileList(rule.files || [])));
    const nextSelectedFiles = normalizeFileList(ctx.state.selectedFiles || []);
    targetFiles.forEach((file) => {
      if (!remainingConditionalFiles.has(file) && !nextSelectedFiles.includes(file)) {
        nextSelectedFiles.push(file);
      }
    });
    ctx.state.selectedFiles = nextSelectedFiles;
    ctx.state.conditionalRules = nextRules;
    ctx.state.filesOverridden = true;
    ctx.state.conditionalFilesOverridden = true;
  }

  function buildSendFilesPayload(ctx) {
    const entries = getSendFileEntries(ctx);
    const files = [];
    const conditionalRules = [];
    entries.forEach((entry) => {
      if (!entry.rules.length) {
        files.push(entry.file);
        return;
      }
      entry.rules.forEach((ruleEntry) => {
        conditionalRules.push({
          ...deepClone(ruleEntry.extra || {}),
          conditions: deepClone(ruleEntry.conditions || []),
          files: [entry.file],
        });
      });
    });
    return {
      files: normalizeFileList(files),
      conditionalRules,
    };
  }

  function renderConditionalRules() {
    return "";
  }

  function openConditionalRuleFilesModal() {
    return null;
  }

  global.BotButtonsFiles = {
    addConditionToFile,
    buildSendFilesPayload,
    buildSendFilesSnapshot,
    fieldType,
    fieldMeta,
    fieldLabel,
    getSendFileEntries,
    hasProjectionLoss,
    normalizeConditionalRuleForState,
    openConditionalRuleFilesModal,
    openFilePickerModal,
    operatorLabel,
    removeConditionalRule,
    removeFileFromState,
    renderConditionalRules,
    renderSelectedFiles,
  };
})(window);
