(function initTicketClientCard(global) {
  function defaultClientCardViewConfig() {
    return {
      blocks: [
        {
          key: "tg",
          title: "Telegram",
          color_scheme: "blue",
          sort_order: 10,
          is_active: true,
          fields: [
            { key: "telegram_username", label: "@username", field_type: "readonly", sort_order: 10, is_visible: true, is_editable: false, is_system: true },
            { key: "telegram_display_name", label: "Имя в TG", field_type: "readonly", sort_order: 20, is_visible: true, is_editable: false, is_system: true },
          ],
        },
        {
          key: "profile",
          title: "Профиль",
          color_scheme: "green",
          sort_order: 20,
          is_active: true,
          fields: [
            { key: "last_name", label: "Фамилия", field_type: "text", sort_order: 10, is_visible: true, is_editable: true, is_system: true },
            { key: "contact_name", label: "Имя", field_type: "text", sort_order: 20, is_visible: true, is_editable: true, is_system: true },
          ],
        },
        {
          key: "department",
          title: "Отдел",
          color_scheme: "blue",
          sort_order: 30,
          is_active: true,
          fields: [
            { key: "destination", label: "Куда..?", field_type: "select", sort_order: 10, is_visible: true, is_editable: true, is_system: true },
            { key: "sand_owner", label: "Чей песок", field_type: "select", sort_order: 20, is_visible: true, is_editable: true, is_system: true },
            { key: "sales_group", label: "Группа отдела продаж", field_type: "select", sort_order: 30, is_visible: true, is_editable: true, is_system: true },
            { key: "department", label: "Отдел", field_type: "text", sort_order: 40, is_visible: true, is_editable: true, is_system: true },
            { key: "first_department", label: "Департамент", field_type: "text", sort_order: 50, is_visible: true, is_editable: true, is_system: true },
            { key: "group_name", label: "Группа", field_type: "text", sort_order: 60, is_visible: true, is_editable: true, is_system: true },
            { key: "location_type", label: "Расположение", field_type: "select", sort_order: 70, is_visible: true, is_editable: true, is_system: true },
            { key: "anydesk", label: "Anydesk", field_type: "text", sort_order: 80, is_visible: true, is_editable: true, is_system: true },
          ],
        },
        {
          key: "work",
          title: "Рабочие данные",
          color_scheme: "yellow",
          sort_order: 40,
          is_active: true,
          fields: [
            { key: "internal_number", label: "Внутренний номер", field_type: "text", sort_order: 10, is_visible: true, is_editable: true, is_system: true },
            { key: "bitrix_login", label: "Логин Bitrix", field_type: "text", sort_order: 20, is_visible: true, is_editable: true, is_system: true },
            { key: "bitrix_password", label: "Пароль Bitrix", field_type: "password", sort_order: 30, is_visible: true, is_editable: true, is_system: true },
            { key: "bitrix_id", label: "ID в Bitrix", field_type: "text", sort_order: 40, is_visible: true, is_editable: true, is_system: true },
          ],
        },
        {
          key: "verification",
          title: "Верификация",
          color_scheme: "red",
          sort_order: 50,
          is_active: true,
          fields: [
            { key: "is_verified", label: "Верификация", field_type: "checkbox", sort_order: 10, is_visible: true, is_editable: true, is_system: true },
          ],
        },
      ],
    };
  }

  function clientCardColorClass(colorScheme) {
    const value = String(colorScheme || "gray").trim().toLowerCase();
    const known = new Set(["blue", "green", "yellow", "red", "gray", "purple"]);
    return `client-card-block-${known.has(value) ? value : "gray"}`;
  }

  function isHexColor(value) {
    return /^#[0-9a-fA-F]{6}$/.test(String(value || "").trim());
  }

  function applyClientCardBlockColor(blockEl, colorScheme) {
    const color = String(colorScheme || "").trim();
    if (!isHexColor(color)) return;
    blockEl.classList.add("client-card-block-custom-color");
    blockEl.style.setProperty("--client-card-block-bg", color.toLowerCase());
  }

  function getClientFieldValue(profile, key) {
    if (!profile || !key) return null;
    if (key === "telegram_username") return profile.telegram_username || null;
    if (key === "telegram_display_name") return profile.telegram_display_name || null;
    if (key === "is_verified") return Boolean(profile.is_verified);
    if (String(key).startsWith("custom_")) {
      return (profile.custom_fields || {})[key] ?? null;
    }
    return profile[key] ?? null;
  }

  function shouldRenderClientField(ctx, field, value) {
    const key = field?.key || "";
    const fieldType = String(field?.field_type || "").toLowerCase();
    if (key === "is_verified") return true;
    if (key === "telegram_username" || key === "telegram_display_name") return true;
    if (fieldType === "checkbox") return true;
    return !ctx.isBlank(value);
  }

  function renderClientFieldValue(field, value) {
    const fieldKey = field?.key || "";
    const fieldType = String(field?.field_type || "").toLowerCase();
    const valueEl = document.createElement("div");
    valueEl.className = "client-card-field-value";

    if (fieldKey === "telegram_username") {
      const usernameRaw = String(value || "").trim();
      if (!usernameRaw) {
        valueEl.textContent = "нет";
        return valueEl;
      }
      const username = usernameRaw.replace(/^@/, "");
      const link = document.createElement("a");
      link.href = `tg://resolve?domain=${encodeURIComponent(username)}`;
      link.textContent = `@${username}`;
      valueEl.appendChild(link);
      return valueEl;
    }

    if (fieldKey === "telegram_display_name") {
      valueEl.textContent = value == null || String(value).trim() === "" ? "нет" : String(value).trim();
      return valueEl;
    }

    if (fieldKey === "is_verified") {
      valueEl.textContent = value ? "✓" : "✕";
      valueEl.classList.add(value ? "verified-ok" : "verified-no");
      return valueEl;
    }

    if (fieldType === "checkbox") {
      valueEl.textContent = value ? "✓" : "✕";
      valueEl.classList.add(value ? "verified-ok" : "verified-no");
      return valueEl;
    }

    if (fieldType === "url") {
      const urlText = String(value ?? "").trim();
      if (!urlText) return valueEl;
      const link = document.createElement("a");
      link.href = urlText;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = urlText;
      valueEl.appendChild(link);
      return valueEl;
    }

    valueEl.textContent = String(value ?? "").trim();
    if (fieldKey === "bitrix_password" || fieldType === "password") {
      valueEl.classList.add("client-card-password");
    }
    return valueEl;
  }

  function renderClientDynamicCard(ctx, profile, config) {
    const { el } = ctx;
    if (!el.clientDynamicCard) return;
    el.clientDynamicCard.innerHTML = "";
    const blocks = (config?.blocks || [])
      .filter((block) => block && block.is_active !== false)
      .sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0));

    for (const block of blocks) {
      const fields = (block.fields || [])
        .filter((field) => field && field.is_visible !== false)
        .sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0));
      const blockEl = document.createElement("div");
      blockEl.className = `client-card-block ${clientCardColorClass(block.color_scheme)}`;
      applyClientCardBlockColor(blockEl, block.color_scheme);

      let renderedFields = 0;
      for (const field of fields) {
        const value = getClientFieldValue(profile, field.key);
        if (!shouldRenderClientField(ctx, field, value)) continue;

        const rowEl = document.createElement("div");
        rowEl.className = "client-card-field";

        if (field.key === "is_verified") {
          rowEl.classList.add("client-card-field-verification");
        } else {
          const labelEl = document.createElement("div");
          labelEl.className = "client-card-field-label";
          labelEl.textContent = field.label || field.key;
          rowEl.appendChild(labelEl);
        }

        rowEl.appendChild(renderClientFieldValue(field, value));
        blockEl.appendChild(rowEl);
        renderedFields += 1;
      }

      if (renderedFields > 0 || block.key === "verification") {
        el.clientDynamicCard.appendChild(blockEl);
      }
    }
  }

  function fillClientPanel(ctx, profile) {
    const effectiveConfig = ctx.state.clientCardViewConfig || defaultClientCardViewConfig();
    renderClientDynamicCard(ctx, profile, effectiveConfig);
  }

  function syncDynamicModalFields(ctx) {
    const { el } = ctx;
    const destination = el.fDestination.value;
    const isSand = destination === "Песок";
    const isSales = destination === "Отдел Продаж";

    el.fSandOwnerWrap.classList.toggle("hidden", !isSand);
    el.fSalesGroupWrap.classList.toggle("hidden", !isSales);

    if (!isSand) el.fSandOwner.value = "";
    if (!isSales) el.fSalesGroup.value = "";

    const isRemote = el.fLocationType.value === "Удалённый";
    el.fAnydeskWrap.classList.toggle("hidden", !isRemote);
    if (!isRemote) el.fAnydesk.value = "";
  }

  function fillModal(ctx, profile) {
    const { el } = ctx;
    ctx.setControlValue(el.fContactName, profile.contact_name);
    ctx.setControlValue(el.fLastName, profile.last_name);
    ctx.setControlValue(el.fDestination, profile.destination);
    ctx.setControlValue(el.fSandOwner, profile.sand_owner);
    ctx.setControlValue(el.fSalesGroup, profile.sales_group);
    ctx.setControlValue(el.fLocationType, profile.location_type);
    ctx.setControlValue(el.fAnydesk, profile.anydesk);
    ctx.setControlValue(el.fInternalNumber, profile.internal_number);
    ctx.setControlValue(el.fBitrixLogin, profile.bitrix_login);
    ctx.setControlValue(el.fBitrixPassword, profile.bitrix_password);
    ctx.setControlValue(el.fBitrixId, profile.bitrix_id);
    el.fIsVerified.checked = Boolean(profile.is_verified);
    renderCustomFieldsEditor(ctx, profile);
    syncDynamicModalFields(ctx);
  }

  function parseCustomFieldValueForPayload(fieldType, rawValue) {
    const type = String(fieldType || "text").toLowerCase();
    if (type === "checkbox") return Boolean(rawValue);
    if (type === "number") {
      if (rawValue === "" || rawValue === null || rawValue === undefined) return null;
      const parsed = Number(rawValue);
      return Number.isFinite(parsed) ? parsed : null;
    }
    return (rawValue ?? "").toString().trim() || null;
  }

  function renderCustomFieldsEditor(ctx, profile) {
    const { el, state } = ctx;
    if (!el.customFieldsContainer || !el.customFieldsSection) return;
    el.customFieldsContainer.innerHTML = "";
    const blocks = (state.clientCardViewConfig?.blocks || [])
      .filter((block) => block && block.is_active !== false)
      .sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0));
    const editableFields = [];
    for (const block of blocks) {
      for (const field of block.fields || []) {
        if (!field || field.is_system || field.is_editable === false) continue;
        editableFields.push(field);
      }
    }
    el.customFieldsSection.classList.toggle("hidden", editableFields.length === 0);
    const customValues = profile?.custom_fields || {};
    for (const field of editableFields) {
      const wrap = document.createElement("label");
      wrap.className = "custom-field-editor";
      const title = document.createElement("span");
      title.textContent = field.label || field.key;
      wrap.appendChild(title);
      const fieldType = String(field.field_type || "text").toLowerCase();
      const currentValue = customValues[field.key];
      let inputEl = null;
      if (fieldType === "textarea") {
        inputEl = document.createElement("textarea");
        inputEl.rows = 3;
        inputEl.value = currentValue ?? "";
      } else if (fieldType === "select") {
        inputEl = document.createElement("select");
        const emptyOption = document.createElement("option");
        emptyOption.value = "";
        emptyOption.textContent = "Не выбрано";
        inputEl.appendChild(emptyOption);
        const options = Array.isArray(field.options) ? field.options : [];
        for (const optionValue of options) {
          const option = document.createElement("option");
          option.value = optionValue;
          option.textContent = optionValue;
          inputEl.appendChild(option);
        }
        inputEl.value = currentValue ?? "";
      } else if (fieldType === "checkbox") {
        inputEl = document.createElement("input");
        inputEl.type = "checkbox";
        inputEl.checked = Boolean(currentValue);
      } else {
        inputEl = document.createElement("input");
        if (fieldType === "number") inputEl.type = "number";
        else if (fieldType === "date") inputEl.type = "date";
        else if (fieldType === "url") inputEl.type = "url";
        else if (fieldType === "phone") inputEl.type = "tel";
        else if (fieldType === "password") inputEl.type = "password";
        else inputEl.type = "text";
        inputEl.value = currentValue ?? "";
      }
      inputEl.dataset.customFieldKey = field.key;
      inputEl.dataset.customFieldType = fieldType;
      wrap.appendChild(inputEl);
      el.customFieldsContainer.appendChild(wrap);
    }
  }

  function buildDepartmentMetaPayload(ctx) {
    const { el, SALES_FIRST_DEPARTMENT, SALES_SECOND_DEPARTMENT } = ctx;
    const destination = el.fDestination.value.trim() || null;
    const sandOwner = el.fSandOwner.value.trim() || null;
    const salesGroup = el.fSalesGroup.value.trim() || null;

    if (destination === "Песок") {
      return {
        department: "Стажировка",
        first_department: null,
        group_name: sandOwner,
      };
    }

    if (destination === "Отдел Продаж") {
      let firstDepartment = null;
      if (salesGroup) {
        if (SALES_FIRST_DEPARTMENT.has(salesGroup)) firstDepartment = "Первый департамент";
        else if (SALES_SECOND_DEPARTMENT.has(salesGroup)) firstDepartment = "Второй отдел";
        else if (salesGroup === "группа Москвиченко") firstDepartment = "Третий отдел";
      }

      return {
        department: "Отдел продаж",
        first_department: firstDepartment,
        group_name: salesGroup,
      };
    }

    return {
      department: destination,
      first_department: null,
      group_name: null,
    };
  }

  function getModalPayload(ctx) {
    const { el } = ctx;
    const locationType = el.fLocationType.value.trim() || null;
    const anydesk = locationType === "Удалённый" ? (el.fAnydesk.value.trim() || null) : null;
    const departmentMeta = buildDepartmentMetaPayload(ctx);

    const custom_fields = {};
    if (el.customFieldsContainer) {
      const customInputs = el.customFieldsContainer.querySelectorAll("[data-custom-field-key]");
      customInputs.forEach((input) => {
        const key = input.dataset.customFieldKey;
        const fieldType = input.dataset.customFieldType || "text";
        let rawValue = null;
        if (input.type === "checkbox") rawValue = input.checked;
        else rawValue = input.value;
        custom_fields[key] = parseCustomFieldValueForPayload(fieldType, rawValue);
      });
    }

    return {
      contact_name: el.fContactName.value.trim() || null,
      last_name: el.fLastName.value.trim() || null,
      email: null,
      phone: null,
      verification_status: null,
      is_verified: el.fIsVerified.checked,
      destination: el.fDestination.value.trim() || null,
      sand_owner: el.fSandOwner.value.trim() || null,
      sales_group: el.fSalesGroup.value.trim() || null,
      department: departmentMeta.department,
      first_department: departmentMeta.first_department,
      group_name: departmentMeta.group_name,
      employee_type: null,
      location_type: locationType,
      anydesk,
      internal_number: el.fInternalNumber.value.trim() || null,
      external_user_id: null,
      bitrix_login: el.fBitrixLogin.value.trim() || null,
      bitrix_password: el.fBitrixPassword.value.trim() || null,
      bitrix_id: el.fBitrixId.value.trim() || null,
      custom_fields,
    };
  }

  function openClientModal(ctx) {
    if (!ctx.state.card) return;
    fillModal(ctx, ctx.state.card.client_profile || {});
    ctx.el.clientModal.classList.remove("hidden");
  }

  function closeClientModal(ctx) {
    ctx.el.clientModal.classList.add("hidden");
  }

  async function saveClientProfile(ctx) {
    try {
      ctx.setStatus("Сохраняю профиль клиента...");
      const payload = getModalPayload(ctx);
      const profile = await ctx.api(`/api/v1/tickets/${ctx.state.ticketId}/client-profile`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });
      ctx.state.card.client_profile = profile;
      fillClientPanel(ctx, profile);
      closeClientModal(ctx);
      ctx.setStatus("Профиль клиента сохранён");
    } catch (e) {
      ctx.showError(e.message);
    }
  }

  global.TicketClientCard = {
    defaultClientCardViewConfig,
    renderClientDynamicCard,
    fillClientPanel,
    syncDynamicModalFields,
    fillModal,
    renderCustomFieldsEditor,
    getModalPayload,
    openClientModal,
    closeClientModal,
    saveClientProfile,
  };
})(window);
