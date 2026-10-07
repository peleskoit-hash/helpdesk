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
          show_in_card: true,
          show_in_editor: true,
          show_title: true,
          fields: [
            { key: "telegram_username", label: "@username", field_type: "readonly", sort_order: 10, is_visible: true, is_visible_in_editor: true, is_editable: false, is_system: true },
            { key: "telegram_display_name", label: "Имя в TG", field_type: "readonly", sort_order: 20, is_visible: true, is_visible_in_editor: true, is_editable: false, is_system: true },
          ],
        },
        {
          key: "profile",
          title: "Профиль",
          color_scheme: "green",
          sort_order: 20,
          is_active: true,
          show_in_card: true,
          show_in_editor: true,
          show_title: true,
          fields: [
            { key: "last_name", label: "Фамилия", field_type: "text", sort_order: 10, is_visible: true, is_visible_in_editor: true, is_editable: true, is_system: true },
            { key: "contact_name", label: "Имя", field_type: "text", sort_order: 20, is_visible: true, is_visible_in_editor: true, is_editable: true, is_system: true },
          ],
        },
        {
          key: "department",
          title: "Отдел",
          color_scheme: "blue",
          sort_order: 30,
          is_active: true,
          show_in_card: true,
          show_in_editor: true,
          show_title: true,
          fields: [
            { key: "destination", label: "Куда..?", field_type: "select", sort_order: 10, is_visible: true, is_visible_in_editor: true, is_editable: true, is_system: true },
            { key: "sand_owner", label: "Чей песок", field_type: "select", sort_order: 20, is_visible: true, is_visible_in_editor: false, is_editable: true, is_system: true },
            { key: "sales_group", label: "Группа отдела продаж", field_type: "select", sort_order: 30, is_visible: true, is_visible_in_editor: false, is_editable: true, is_system: true },
            { key: "department", label: "Отдел", field_type: "text", sort_order: 40, is_visible: false, is_visible_in_editor: false, is_editable: false, is_system: true },
            { key: "first_department", label: "Департамент", field_type: "text", sort_order: 50, is_visible: false, is_visible_in_editor: false, is_editable: false, is_system: true },
            { key: "group_name", label: "Группа", field_type: "text", sort_order: 60, is_visible: false, is_visible_in_editor: false, is_editable: false, is_system: true },
            { key: "location_type", label: "Расположение", field_type: "select", sort_order: 70, is_visible: true, is_visible_in_editor: true, is_editable: true, is_system: true },
            { key: "anydesk", label: "Anydesk", field_type: "text", sort_order: 80, is_visible: false, is_visible_in_editor: false, is_editable: true, is_system: true },
          ],
        },
        {
          key: "work",
          title: "Рабочие данные",
          color_scheme: "yellow",
          sort_order: 40,
          is_active: true,
          show_in_card: true,
          show_in_editor: true,
          show_title: true,
          fields: [
            { key: "internal_number", label: "Внутренний номер", field_type: "text", sort_order: 10, is_visible: true, is_visible_in_editor: true, is_editable: true, is_system: true },
            { key: "bitrix_login", label: "Логин Bitrix", field_type: "text", sort_order: 20, is_visible: true, is_visible_in_editor: true, is_editable: true, is_system: true },
            { key: "bitrix_password", label: "Пароль Bitrix", field_type: "password", sort_order: 30, is_visible: true, is_visible_in_editor: true, is_editable: true, is_system: true },
            { key: "bitrix_id", label: "ID в Bitrix", field_type: "text", sort_order: 40, is_visible: true, is_visible_in_editor: true, is_editable: true, is_system: true },
          ],
        },
        {
          key: "verification",
          title: "Верификация",
          color_scheme: "red",
          sort_order: 50,
          is_active: true,
          show_in_card: true,
          show_in_editor: true,
          show_title: true,
          fields: [
            { key: "is_verified", label: "Верификация", field_type: "checkbox", sort_order: 10, is_visible: true, is_visible_in_editor: true, is_editable: true, is_system: true },
          ],
        },
      ],
      rules: [
        {
          name: "\u041f\u043e\u043a\u0430\u0437\u044b\u0432\u0430\u0442\u044c \u0432\u043b\u0430\u0434\u0435\u043b\u044c\u0446\u0430 \u043f\u0435\u0441\u043a\u0430",
          is_active: true,
          is_system: true,
          sort_order: 10,
          condition_field_key: "destination",
          condition_operator: "equals",
          condition_value: "\u041f\u0435\u0441\u043e\u043a",
          actions: [{ target_type: "field", target_key: "sand_owner", action: "show", scope: "editor" }],
        },
        {
          name: "\u041f\u043e\u043a\u0430\u0437\u044b\u0432\u0430\u0442\u044c \u0433\u0440\u0443\u043f\u043f\u0443 \u043f\u0440\u043e\u0434\u0430\u0436",
          is_active: true,
          is_system: true,
          sort_order: 20,
          condition_field_key: "destination",
          condition_operator: "equals",
          condition_value: "\u041e\u0442\u0434\u0435\u043b \u041f\u0440\u043e\u0434\u0430\u0436",
          actions: [{ target_type: "field", target_key: "sales_group", action: "show", scope: "editor" }],
        },
        {
          name: "\u041f\u043e\u043a\u0430\u0437\u044b\u0432\u0430\u0442\u044c AnyDesk \u0434\u043b\u044f \u0443\u0434\u0430\u043b\u0451\u043d\u043d\u044b\u0445",
          is_active: true,
          is_system: true,
          sort_order: 30,
          condition_field_key: "location_type",
          condition_operator: "equals",
          condition_value: "\u0423\u0434\u0430\u043b\u0451\u043d\u043d\u044b\u0439",
          actions: [{ target_type: "field", target_key: "anydesk", action: "show", scope: "both" }],
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

  function clientCardColorHex(colorScheme) {
    const value = String(colorScheme || "").trim();
    const named = value.toLowerCase();
    const namedHex = {
      blue: "#dbeafe",
      green: "#dcfce7",
      yellow: "#fef3c7",
      red: "#ffe4e6",
      gray: "#e5e7eb",
      purple: "#ede9fe",
    };
    if (namedHex[named]) return namedHex[named];
    if (isHexColor(value)) return value.toLowerCase();
    return namedHex.gray;
  }

  function hexToRgbChannels(hex) {
    const value = clientCardColorHex(hex).slice(1);
    const r = Number.parseInt(value.slice(0, 2), 16);
    const g = Number.parseInt(value.slice(2, 4), 16);
    const b = Number.parseInt(value.slice(4, 6), 16);
    return `${r}, ${g}, ${b}`;
  }

  function hexLuminance(hex) {
    const value = clientCardColorHex(hex).slice(1);
    const channels = [0, 2, 4].map((index) => Number.parseInt(value.slice(index, index + 2), 16) / 255);
    const normalized = channels.map((channel) => (
      channel <= 0.03928 ? channel / 12.92 : Math.pow((channel + 0.055) / 1.055, 2.4)
    ));
    return (0.2126 * normalized[0]) + (0.7152 * normalized[1]) + (0.0722 * normalized[2]);
  }

  function blendHexColors(foreground, background, alpha) {
    const front = clientCardColorHex(foreground).slice(1);
    const back = clientCardColorHex(background).slice(1);
    const mixed = [0, 2, 4].map((index) => {
      const frontChannel = Number.parseInt(front.slice(index, index + 2), 16);
      const backChannel = Number.parseInt(back.slice(index, index + 2), 16);
      const channel = Math.round((frontChannel * alpha) + (backChannel * (1 - alpha)));
      return channel.toString(16).padStart(2, "0");
    }).join("");
    return `#${mixed}`;
  }

  function contrastRatio(backgroundHex, textHex) {
    const background = hexLuminance(backgroundHex);
    const text = hexLuminance(textHex);
    const lighter = Math.max(background, text);
    const darker = Math.min(background, text);
    return (lighter + 0.05) / (darker + 0.05);
  }

  function contrastTextColor(backgroundHex) {
    const dark = "#000000";
    const light = "#ffffff";
    return contrastRatio(backgroundHex, dark) >= contrastRatio(backgroundHex, light) ? dark : light;
  }

  function applyClientCardBlockColor(blockEl, colorScheme) {
    const color = String(colorScheme || "").trim();
    if (!isHexColor(color)) return;
    blockEl.classList.add("client-card-block-custom-color");
    blockEl.style.setProperty("--client-card-block-bg", color.toLowerCase());
  }

  function applyClientCardEditorSectionColor(sectionEl, colorScheme) {
    const hex = clientCardColorHex(colorScheme);
    const lightBackground = blendHexColors(hex, "#ffffff", 0.86);
    const darkBackground = blendHexColors(hex, "#0f172a", 0.58);
    const lightText = contrastTextColor(lightBackground);
    const darkText = contrastTextColor(darkBackground);
    sectionEl.style.setProperty("--client-card-editor-bg", lightBackground);
    sectionEl.style.setProperty("--client-card-editor-border", blendHexColors(hex, "#0f172a", 0.72));
    sectionEl.style.setProperty("--client-card-editor-title-text", lightText);
    sectionEl.style.setProperty("--client-card-editor-label-text", lightText);
    sectionEl.style.setProperty("--client-card-editor-readonly-text", lightText);
    sectionEl.style.setProperty("--client-card-editor-bg-dark", darkBackground);
    sectionEl.style.setProperty("--client-card-editor-border-dark", blendHexColors(hex, "#f8fafc", 0.46));
    sectionEl.style.setProperty("--client-card-editor-title-text-dark", darkText);
    sectionEl.style.setProperty("--client-card-editor-label-text-dark", darkText);
    sectionEl.style.setProperty("--client-card-editor-readonly-text-dark", darkText);
  }

  function blockShownInCard(block) {
    return block && block.is_active !== false && block.show_in_card !== false;
  }

  function blockShownInEditor(block) {
    return block && block.is_active !== false && block.show_in_editor !== false;
  }

  function blockShowsTitle(block) {
    return block?.show_title !== false;
  }

  function fieldShownInCard(field) {
    return field && field.is_visible !== false;
  }

  function fieldShownInEditor(field) {
    return field && field.is_visible_in_editor !== false;
  }

  function activeEditorRuleFieldTargets(config) {
    const targets = new Set();
    for (const rule of config?.rules || []) {
      if (!rule || rule.is_active === false) continue;
      for (const action of rule.actions || []) {
        const scope = action.scope || "editor";
        if (action.target_type === "field" && (scope === "editor" || scope === "both")) {
          targets.add(action.target_key);
        }
      }
    }
    return targets;
  }

  function activeEditorRuleBlockTargets(config) {
    const targets = new Set();
    for (const rule of config?.rules || []) {
      if (!rule || rule.is_active === false) continue;
      for (const action of rule.actions || []) {
        const scope = action.scope || "editor";
        if (action.target_type === "block" && (scope === "editor" || scope === "both")) {
          targets.add(action.target_key);
        }
      }
    }
    return targets;
  }

  function fieldShownInEditorOrRuleTarget(field, ruleTargets) {
    return fieldShownInEditor(field) || ruleTargets.has(field?.key);
  }

  function blockShownInEditorOrRuleTarget(block, ruleTargets) {
    return blockShownInEditor(block) || ruleTargets.has(block?.key);
  }

  const systemFieldControls = {
    contact_name: { control: "fContactName" },
    last_name: { control: "fLastName" },
    destination: { control: "fDestination" },
    sand_owner: { control: "fSandOwner", wrap: "fSandOwnerWrap" },
    sales_group: { control: "fSalesGroup", wrap: "fSalesGroupWrap" },
    location_type: { control: "fLocationType" },
    anydesk: { control: "fAnydesk", wrap: "fAnydeskWrap" },
    internal_number: { control: "fInternalNumber" },
    bitrix_login: { control: "fBitrixLogin" },
    bitrix_password: { control: "fBitrixPassword" },
    bitrix_id: { control: "fBitrixId" },
    is_verified: { control: "fIsVerified" },
  };

  const profileSystemFieldKeys = new Set([
    "contact_name",
    "last_name",
    "email",
    "phone",
    "verification_status",
    "is_verified",
    "destination",
    "sand_owner",
    "sales_group",
    "department",
    "first_department",
    "group_name",
    "employee_type",
    "location_type",
    "anydesk",
    "internal_number",
    "external_user_id",
    "bitrix_login",
    "bitrix_password",
    "bitrix_id",
  ]);

  function bitrixPasswordState(ctx) {
    if (!ctx.state.clientCardBitrixPassword) {
      ctx.state.clientCardBitrixPassword = { dirty: false, revealed: false, hasSaved: false, draftValue: "" };
    }
    return ctx.state.clientCardBitrixPassword;
  }

  function generateBitrixPasswordMask() {
    const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
    const bytes = new Uint8Array(10);
    if (global.crypto?.getRandomValues) {
      global.crypto.getRandomValues(bytes);
    } else {
      for (let index = 0; index < bytes.length; index += 1) {
        bytes[index] = Math.floor(Math.random() * 256);
      }
    }
    return Array.from(bytes, (byte) => alphabet[byte % alphabet.length]).join("");
  }

  function setBitrixPasswordInputHidden(ctx) {
    const { el } = ctx;
    const state = bitrixPasswordState(ctx);
    if (!el.fBitrixPassword) return;
    el.fBitrixPassword.type = "text";
    el.fBitrixPassword.autocomplete = "off";
    state.revealed = false;
    el.fBitrixPassword.classList.remove("client-card-password-revealed");
    if ((state.dirty && state.draftValue.trim()) || (!state.dirty && state.hasSaved)) {
      el.fBitrixPassword.value = generateBitrixPasswordMask();
      el.fBitrixPassword.classList.add("client-card-password");
    } else {
      el.fBitrixPassword.value = "";
      el.fBitrixPassword.classList.remove("client-card-password");
    }
  }

  function clearBitrixPasswordField(ctx) {
    const { el } = ctx;
    const state = bitrixPasswordState(ctx);
    state.dirty = false;
    state.revealed = false;
    state.hasSaved = false;
    state.draftValue = "";
    if (el.fBitrixPassword) {
      el.fBitrixPassword.value = "";
      el.fBitrixPassword.type = "text";
      el.fBitrixPassword.autocomplete = "off";
      el.fBitrixPassword.classList.remove("client-card-password", "client-card-password-revealed");
    }
  }

  function clientCardClientId(ctx) {
    return ctx.state.selectedClientId || ctx.clientId || ctx.state.card?.client_id || "";
  }

  async function revealBitrixPassword(ctx) {
    if (ctx.state.ticketId) {
      return ctx.api(`/api/v1/tickets/${encodeURIComponent(ctx.state.ticketId)}/client-profile/bitrix-password`);
    }
    const clientId = clientCardClientId(ctx);
    if (!clientId) return null;
    return ctx.api(`/api/v1/management/users/${encodeURIComponent(clientId)}/profile/bitrix-password`);
  }

  function bitrixPasswordCardRevealState(ctx) {
    if (!ctx.state.clientCardBitrixPasswordReveal) {
      ctx.state.clientCardBitrixPasswordReveal = { ownerKey: "", plaintext: "", revealedUntil: 0, timerId: null };
    }
    return ctx.state.clientCardBitrixPasswordReveal;
  }

  function clientCardRevealOwnerKey(ctx) {
    return `${ctx.state.ticketId || ""}:${clientCardClientId(ctx) || ""}`;
  }

  function currentBitrixPasswordValueEl(ctx) {
    return ctx.el.clientDynamicCard?.querySelector("[data-client-card-bitrix-password-value='1']") || null;
  }

  function showBitrixPasswordValueEl(valueEl, plaintext) {
    valueEl.textContent = plaintext;
    valueEl.classList.remove("client-card-password");
    valueEl.classList.add("client-card-password-revealed");
  }

  function clearBitrixPasswordCardReveal(ctx) {
    const state = bitrixPasswordCardRevealState(ctx);
    if (state.timerId) clearTimeout(state.timerId);
    state.ownerKey = "";
    state.plaintext = "";
    state.revealedUntil = 0;
    state.timerId = null;
  }

  function hideBitrixPasswordCardReveal(ctx, ownerKey) {
    const state = bitrixPasswordCardRevealState(ctx);
    if (ownerKey && state.ownerKey !== ownerKey) return;
    state.ownerKey = "";
    state.plaintext = "";
    state.revealedUntil = 0;
    state.timerId = null;
    const valueEl = currentBitrixPasswordValueEl(ctx);
    if (valueEl) resetBitrixPasswordValueEl(valueEl);
  }

  function activeBitrixPasswordCardReveal(ctx, ownerKey) {
    const state = bitrixPasswordCardRevealState(ctx);
    if (state.ownerKey && state.ownerKey !== ownerKey) {
      clearBitrixPasswordCardReveal(ctx);
      return null;
    }
    if (!state.plaintext || Date.now() >= state.revealedUntil) {
      clearBitrixPasswordCardReveal(ctx);
      return null;
    }
    return state;
  }

  function scheduleBitrixPasswordCardRevealHide(ctx, ownerKey) {
    const state = bitrixPasswordCardRevealState(ctx);
    if (state.timerId) clearTimeout(state.timerId);
    const delay = Math.max(0, state.revealedUntil - Date.now());
    state.timerId = setTimeout(() => {
      hideBitrixPasswordCardReveal(ctx, ownerKey);
    }, delay);
  }

  function bindBitrixPasswordField(ctx) {
    const { el } = ctx;
    if (!el.fBitrixPassword) return;
    el.fBitrixPassword.type = "text";
    el.fBitrixPassword.autocomplete = "off";
    if (!el.fBitrixPassword.dataset.clientCardBitrixPasswordBound) {
      el.fBitrixPassword.addEventListener("focus", async () => {
        const state = bitrixPasswordState(ctx);
        el.fBitrixPassword.type = "text";
        el.fBitrixPassword.classList.remove("client-card-password");
        el.fBitrixPassword.classList.add("client-card-password-revealed");
        if (state.dirty) {
          el.fBitrixPassword.value = state.draftValue;
          return;
        }
        if (!state.hasSaved || state.revealed) return;
        try {
          const payload = await revealBitrixPassword(ctx);
          if (!payload) return;
          el.fBitrixPassword.value = payload?.bitrix_password || "";
          state.dirty = false;
          state.revealed = Boolean(payload?.bitrix_password);
        } catch (error) {
          ctx.showError?.(error.message);
        }
      });
      el.fBitrixPassword.addEventListener("blur", () => {
        const state = bitrixPasswordState(ctx);
        if (state.dirty) {
          state.draftValue = el.fBitrixPassword.value;
        }
        setBitrixPasswordInputHidden(ctx);
      });
      el.fBitrixPassword.addEventListener("input", () => {
        const state = bitrixPasswordState(ctx);
        state.dirty = true;
        state.draftValue = el.fBitrixPassword.value;
        state.revealed = false;
        el.fBitrixPassword.classList.remove("client-card-password");
        el.fBitrixPassword.classList.add("client-card-password-revealed");
      });
      el.fBitrixPassword.dataset.clientCardBitrixPasswordBound = "1";
    }
  }

  function ensureRuleState(ctx) {
    if (!ctx.state.clientCardRuleState) {
      ctx.state.clientCardRuleState = { hidden: new Set(), hiddenBlocks: new Set(), readonly: new Set(), required: new Set() };
    }
    if (!ctx.state.clientCardRuleState.hiddenBlocks) ctx.state.clientCardRuleState.hiddenBlocks = new Set();
    return ctx.state.clientCardRuleState;
  }

  function fieldEditorNodes(ctx, key) {
    const spec = systemFieldControls[key] || {};
    const control = spec.control ? ctx.el[spec.control] : null;
    const dynamicWrap = control?.closest("[data-client-card-field-key]") || null;
    const wrap = dynamicWrap || (spec.wrap ? ctx.el[spec.wrap] : (control ? control.closest("label") : null));
    if (control || wrap) return { control, wrap };
    const escapedKey = selectorKey(key);
    const dynamic = ctx.el.clientModal?.querySelector(`[data-client-card-field-key="${escapedKey}"]`)
      || ctx.el.customFieldsContainer?.querySelector(`[data-client-card-field-key="${escapedKey}"]`);
    const dynamicControl = dynamic?.querySelector("[data-custom-field-key]");
    return { control: dynamicControl || null, wrap: dynamic || null };
  }

  function selectorKey(key) {
    return window.CSS?.escape ? window.CSS.escape(key) : String(key).replaceAll('"', '\\"');
  }

  function fieldEditorValue(ctx, key) {
    const { control } = fieldEditorNodes(ctx, key);
    if (control) {
      if (control.type === "checkbox") return control.checked;
      return control.value;
    }
    return fieldValueForEditor(ctx.state.clientCardRuleProfile || ctx.state.card?.client_profile || {}, { key });
  }

  function isAdminRole(ctx) {
    return ctx.state?.role === "admin";
  }

  function normalizeRuleValue(value) {
    return String(value ?? "").trim();
  }

  function ruleMatches(ctx, rule) {
    if (rule.condition_operator === "always") return true;
    const current = normalizeRuleValue(fieldEditorValue(ctx, rule.condition_field_key));
    const expected = normalizeRuleValue(rule.condition_value);
    if (rule.condition_operator === "equals") return current === expected;
    if (rule.condition_operator === "not_equals") return current !== expected;
    if (rule.condition_operator === "is_empty") return current === "";
    if (rule.condition_operator === "is_not_empty") return current !== "";
    return false;
  }

  function fieldBaseReadonly(ctx, key) {
    const { control } = fieldEditorNodes(ctx, key);
    return control?.dataset?.clientCardBaseReadonly === "1";
  }

  function setControlReadonly(control, readonly) {
    if (!control) return;
    if (control.type === "checkbox" || control.tagName === "SELECT") {
      control.disabled = Boolean(readonly);
      control.readOnly = false;
    } else {
      control.disabled = false;
      control.readOnly = Boolean(readonly);
    }
  }

  function resetClientCardRuleOverlay(ctx) {
    const state = ensureRuleState(ctx);
    state.hidden = new Set();
    state.hiddenBlocks = new Set();
    state.readonly = new Set();
    state.required = new Set();
    const keys = new Set(Object.keys(systemFieldControls));
    for (const block of ctx.state.clientCardViewConfig?.blocks || []) {
      for (const field of block.fields || []) {
        if (field?.key) keys.add(field.key);
      }
    }
    for (const key of keys) {
      const { control, wrap } = fieldEditorNodes(ctx, key);
      if (wrap) {
        if (wrap.dataset.clientCardBaseHidden === undefined) {
          wrap.dataset.clientCardBaseHidden = wrap.classList.contains("hidden") ? "1" : "0";
        }
        const baseHidden = wrap.dataset.clientCardBaseHidden === "1";
        wrap.classList.toggle("hidden", baseHidden);
        wrap.classList.remove("client-card-field-readonly-rule");
        if (baseHidden) state.hidden.add(key);
      }
      if (control) {
        const baseReadonly = control.dataset.clientCardBaseReadonly === "1";
        setControlReadonly(control, baseReadonly);
        if (wrap) wrap.classList.toggle("client-card-field-readonly-rule", baseReadonly);
        control.required = false;
      }
    }
    const blockEls = ctx.el.clientModal?.querySelectorAll("[data-client-card-generated-section][data-client-card-block-key]")
      || ctx.el.customFieldsContainer?.querySelectorAll("[data-client-card-block-key]")
      || [];
    blockEls.forEach((blockEl) => {
      if (blockEl.dataset.clientCardBaseHidden === undefined) {
        blockEl.dataset.clientCardBaseHidden = blockEl.classList.contains("hidden") ? "1" : "0";
      }
      const baseHidden = blockEl.dataset.clientCardBaseHidden === "1";
      blockEl.classList.toggle("hidden", baseHidden);
      if (baseHidden) {
        state.hiddenBlocks.add(blockEl.dataset.clientCardBlockKey);
      }
    });
  }

  function fieldBlockKey(ctx, key) {
    for (const block of ctx.state.clientCardViewConfig?.blocks || []) {
      if ((block.fields || []).some((field) => field?.key === key)) return block.key;
    }
    return null;
  }

  function syncFieldEditorVisibility(ctx, key) {
    const { wrap } = fieldEditorNodes(ctx, key);
    if (wrap) wrap.classList.toggle("hidden", isRuntimeFieldHidden(ctx, key));
  }

  function syncBlockEditorVisibility(ctx, blockKey) {
    const state = ensureRuleState(ctx);
    const block = ctx.el.clientModal?.querySelector(`[data-client-card-generated-section][data-client-card-block-key="${selectorKey(blockKey)}"]`)
      || ctx.el.customFieldsContainer?.querySelector(`[data-client-card-block-key="${selectorKey(blockKey)}"]`);
    if (block) block.classList.toggle("hidden", state.hiddenBlocks.has(blockKey));
    const configBlock = findClientCardBlock(ctx.state.clientCardViewConfig, blockKey);
    for (const field of configBlock?.fields || []) {
      syncFieldEditorVisibility(ctx, field.key);
    }
  }

  function applyClientCardRuleAction(ctx, action) {
    if (!action) return;
    const scope = action.scope || "editor";
    if (scope !== "editor" && scope !== "both") return;
    const key = action.target_key;
    const state = ensureRuleState(ctx);
    if (action.target_type === "block") {
      if (action.action !== "show" && action.action !== "hide") return;
      if (action.action === "hide") state.hiddenBlocks.add(key);
      else state.hiddenBlocks.delete(key);
      syncBlockEditorVisibility(ctx, key);
      return;
    }
    if (action.target_type !== "field") return;
    if (action.action !== "show" && action.action !== "hide" && scope !== "editor") return;
    const { control, wrap } = fieldEditorNodes(ctx, key);
    if (action.action === "show") {
      state.hidden.delete(key);
      syncFieldEditorVisibility(ctx, key);
    } else if (action.action === "hide") {
      state.hidden.add(key);
      syncFieldEditorVisibility(ctx, key);
    } else if (action.action === "readonly") {
      state.readonly.add(key);
      if (wrap) wrap.classList.add("client-card-field-readonly-rule");
      setControlReadonly(control, true);
    } else if (action.action === "editable") {
      state.readonly.delete(key);
      const baseReadonly = fieldBaseReadonly(ctx, key);
      if (wrap) wrap.classList.toggle("client-card-field-readonly-rule", baseReadonly);
      setControlReadonly(control, baseReadonly);
    } else if (action.action === "required") {
      state.required.add(key);
      if (control) control.required = true;
    } else if (action.action === "optional") {
      state.required.delete(key);
      if (control) control.required = false;
    }
  }

  function ruleConflictKey(action) {
    if (!action || !action.target_key) return "";
    const targetType = action.target_type || "field";
    const scope = action.scope || "editor";
    if (targetType === "block" && (action.action === "show" || action.action === "hide")) {
      return `${scope}:block:${action.target_key}:visibility`;
    }
    if (targetType !== "field") return "";
    if (action.action === "show" || action.action === "hide") return `${scope}:field:${action.target_key}:visibility`;
    if (action.action === "readonly" || action.action === "editable") return `${scope}:field:${action.target_key}:editability`;
    if (action.action === "required" || action.action === "optional") return `${scope}:field:${action.target_key}:requirement`;
    return "";
  }

  function compareRulePriority(a, b) {
    const sortDiff = (Number(a?.sort_order) || 0) - (Number(b?.sort_order) || 0);
    if (sortDiff) return sortDiff;
    return String(a?.id || "").localeCompare(String(b?.id || ""));
  }

  function applyClientCardRules(ctx) {
    resetClientCardRuleOverlay(ctx);
    const rules = (ctx.state.clientCardViewConfig?.rules || [])
      .filter((rule) => rule && rule.is_active !== false)
      .sort(compareRulePriority);
    const resolved = new Set();
    for (const rule of rules) {
      if (!ruleMatches(ctx, rule)) continue;
      for (const action of rule.actions || []) {
        const conflictKey = ruleConflictKey(action);
        if (conflictKey && resolved.has(conflictKey)) continue;
        applyClientCardRuleAction(ctx, action);
        if (conflictKey) resolved.add(conflictKey);
      }
    }
  }

  function bindClientCardRuleInputs(ctx) {
    const rules = ctx.state.clientCardViewConfig?.rules || [];
    const conditionKeys = new Set(rules.map((rule) => rule?.condition_field_key).filter(Boolean));
    for (const key of conditionKeys) {
      const { control } = fieldEditorNodes(ctx, key);
      if (!control || control.dataset.clientCardRuleBound === "1") continue;
      control.dataset.clientCardRuleBound = "1";
      control.addEventListener("change", () => applyClientCardRules(ctx));
      control.addEventListener("input", () => applyClientCardRules(ctx));
    }
  }

  function isRuntimeFieldHidden(ctx, key) {
    const state = ensureRuleState(ctx);
    const blockKey = fieldBlockKey(ctx, key);
    return state.hidden.has(key) || (blockKey ? state.hiddenBlocks.has(blockKey) : false);
  }

  function isRuntimeFieldReadonly(ctx, key) {
    return fieldBaseReadonly(ctx, key) || ensureRuleState(ctx).readonly.has(key);
  }

  function validateClientCardRuleRequirements(ctx) {
    const required = ensureRuleState(ctx).required;
    for (const key of required) {
      if (isRuntimeFieldHidden(ctx, key)) continue;
      const value = fieldEditorValue(ctx, key);
      if (typeof value === "boolean") continue;
      if (normalizeRuleValue(value) === "") {
        const field = findClientCardField(ctx.state.clientCardViewConfig, key);
        throw new Error(`\u0417\u0430\u043f\u043e\u043b\u043d\u0438\u0442\u0435 \u043f\u043e\u043b\u0435: ${field?.label || key}`);
      }
    }
  }

  function findClientCardField(config, key) {
    for (const block of config?.blocks || []) {
      for (const field of block.fields || []) {
        if (field?.key === key) return field;
      }
    }
    return null;
  }

  function findClientCardBlock(config, key) {
    return (config?.blocks || []).find((block) => block?.key === key) || null;
  }

  function cardRuleMatches(profile, rule) {
    if (rule.condition_operator === "always") return true;
    const value = normalizeRuleValue(getClientFieldValue(profile, rule.condition_field_key));
    const expected = normalizeRuleValue(rule.condition_value);
    if (rule.condition_operator === "equals") return value === expected;
    if (rule.condition_operator === "not_equals") return value !== expected;
    if (rule.condition_operator === "is_empty") return value === "";
    if (rule.condition_operator === "is_not_empty") return value !== "";
    return false;
  }

  function applyClientCardRulesToCardConfig(config, profile) {
    const effectiveConfig = {
      ...(config || {}),
      blocks: (config?.blocks || []).map((block) => ({
        ...block,
        fields: (block.fields || []).map((field) => ({ ...field })),
      })),
    };
    const blocksByKey = new Map(effectiveConfig.blocks.map((block) => [block.key, block]));
    const fieldsByKey = new Map();
    for (const block of effectiveConfig.blocks) {
      for (const field of block.fields || []) {
        fieldsByKey.set(field.key, field);
      }
    }
    const rules = (effectiveConfig.rules || [])
      .filter((rule) => rule && rule.is_active !== false)
      .sort(compareRulePriority);
    const resolved = new Set();
    for (const rule of rules) {
      if (!cardRuleMatches(profile, rule)) continue;
      for (const action of rule.actions || []) {
        const scope = action.scope || "editor";
        if (scope !== "card" && scope !== "both") continue;
        const conflictKey = ruleConflictKey({ ...action, scope });
        if (conflictKey && resolved.has(conflictKey)) continue;
        const shouldShow = action.action === "show";
        const shouldHide = action.action === "hide";
        if (!shouldShow && !shouldHide) continue;
        if (action.target_type === "block") {
          const block = blocksByKey.get(action.target_key);
          if (block) block.show_in_card = shouldShow;
        } else {
          const field = fieldsByKey.get(action.target_key);
          if (field) field.is_visible = shouldShow;
        }
        if (conflictKey) resolved.add(conflictKey);
      }
    }
    return effectiveConfig;
  }

  function getClientFieldValue(profile, key) {
    if (!profile || !key) return null;
    if (key === "telegram_username") return profile.telegram_username || null;
    if (key === "telegram_display_name") return profile.telegram_display_name || null;
    if (key === "is_verified") return Boolean(profile.is_verified);
    if (key === "bitrix_password") return profile.bitrix_password_set ? "********" : null;
    if (String(key).startsWith("custom_")) {
      return (profile.custom_fields || {})[key] ?? null;
    }
    return profile[key] ?? null;
  }

  function shouldRenderClientField(ctx, field, value) {
    const key = field?.key || "";
    const fieldType = String(field?.field_type || "").toLowerCase();
    if (key === "bitrix_password") return !ctx.isBlank(value);
    if (key === "telegram_username" || key === "telegram_display_name") return true;
    if (fieldType === "checkbox") return value || field.show_when_false !== false;
    return !ctx.isBlank(value);
  }

  function resetBitrixPasswordValueEl(valueEl) {
    valueEl.textContent = generateBitrixPasswordMask();
    valueEl.classList.add("client-card-password");
    valueEl.classList.remove("client-card-password-revealed");
  }

  function applyBitrixPasswordValueRevealState(ctx, valueEl) {
    valueEl.dataset.clientCardBitrixPasswordValue = "1";
    const ownerKey = clientCardRevealOwnerKey(ctx);
    const state = activeBitrixPasswordCardReveal(ctx, ownerKey);
    if (state) {
      showBitrixPasswordValueEl(valueEl, state.plaintext);
      scheduleBitrixPasswordCardRevealHide(ctx, ownerKey);
      return;
    }
    resetBitrixPasswordValueEl(valueEl);
  }

  function bindBitrixPasswordValueReveal(ctx, valueEl) {
    if (!ctx || valueEl.dataset.clientCardBitrixPasswordValueBound) return;
    valueEl.tabIndex = 0;
    valueEl.addEventListener("click", async () => {
      const ownerKey = clientCardRevealOwnerKey(ctx);
      if (activeBitrixPasswordCardReveal(ctx, ownerKey)) return;
      try {
        const payload = await revealBitrixPassword(ctx);
        if (!payload?.bitrix_password) return;
        if (clientCardRevealOwnerKey(ctx) !== ownerKey) return;
        const state = bitrixPasswordCardRevealState(ctx);
        state.ownerKey = ownerKey;
        state.plaintext = payload.bitrix_password;
        state.revealedUntil = Date.now() + 5000;
        const currentValueEl = currentBitrixPasswordValueEl(ctx);
        if (currentValueEl) {
          showBitrixPasswordValueEl(currentValueEl, state.plaintext);
        }
        scheduleBitrixPasswordCardRevealHide(ctx, ownerKey);
      } catch (error) {
        ctx.showError?.(error.message);
      }
    });
    valueEl.dataset.clientCardBitrixPasswordValueBound = "1";
  }

  function renderClientFieldValue(field, value, ctx = null) {
    const fieldKey = field?.key || "";
    const fieldType = String(field?.field_type || "").toLowerCase();
    const valueEl = document.createElement("div");
    valueEl.className = "client-card-field-value";

    if (fieldKey === "bitrix_password") {
      applyBitrixPasswordValueRevealState(ctx, valueEl);
      bindBitrixPasswordValueReveal(ctx, valueEl);
      return valueEl;
    }

    if (fieldKey === "telegram_username") {
      const usernameRaw = String(value || "").trim();
      if (!usernameRaw) {
        valueEl.textContent = "нет";
        return valueEl;
      }
      const username = usernameRaw.replace(/^@/, "");
      valueEl.textContent = `@${username}`;
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

  function clientFieldExternalLinkConfig(fieldKey) {
    if (fieldKey === "internal_number") {
      return {
        baseUrl: "http://192.168.1.2/admin/config.php?display=extensions&extdisplay=",
        label: "Открыть внутренний номер",
      };
    }
    if (fieldKey === "bitrix_id") {
      return {
        baseUrl: "https://crm.elitesochi.com/bitrix/admin/user_edit.php?lang=ru&ID=",
        label: "Открыть пользователя в Bitrix",
      };
    }
    if (fieldKey === "telegram_username") {
      return {
        label: "Открыть профиль в Telegram",
      };
    }
    return null;
  }

  function normalizedTelegramUsername(value) {
    const usernameRaw = String(value || "").trim();
    return usernameRaw ? usernameRaw.replace(/^@/, "") : "";
  }

  function renderClientFieldValueWithExternalLink(field, value, ctx) {
    const linkConfig = clientFieldExternalLinkConfig(field?.key);
    if (!linkConfig) return renderClientFieldValue(field, value, ctx);

    const valueRowEl = document.createElement("div");
    valueRowEl.className = "client-card-field-value-row";
    valueRowEl.appendChild(renderClientFieldValue(field, value, ctx));

    if (field?.key === "telegram_username") {
      const username = normalizedTelegramUsername(value);
      if (!username) {
        const disabledEl = document.createElement("span");
        disabledEl.className = "client-card-field-external-link client-card-field-external-link-disabled";
        disabledEl.setAttribute("aria-hidden", "true");
        disabledEl.textContent = "↗";
        valueRowEl.appendChild(disabledEl);
        return valueRowEl;
      }

      const telegramLinkEl = document.createElement("a");
      telegramLinkEl.className = "client-card-field-external-link";
      telegramLinkEl.href = `https://t.me/${encodeURIComponent(username)}?profile`;
      telegramLinkEl.target = "_blank";
      telegramLinkEl.rel = "noopener noreferrer";
      telegramLinkEl.title = linkConfig.label;
      telegramLinkEl.setAttribute("aria-label", linkConfig.label);
      telegramLinkEl.textContent = "↗";
      valueRowEl.appendChild(telegramLinkEl);
      return valueRowEl;
    }

    const linkEl = document.createElement("a");
    linkEl.className = "client-card-field-external-link";
    linkEl.href = `${linkConfig.baseUrl}${encodeURIComponent(String(value).trim())}`;
    linkEl.target = "_blank";
    linkEl.rel = "noopener noreferrer";
    linkEl.title = linkConfig.label;
    linkEl.setAttribute("aria-label", linkConfig.label);
    linkEl.textContent = "↗";
    valueRowEl.appendChild(linkEl);

    return valueRowEl;
  }

  function renderClientDynamicCard(ctx, profile, config) {
    const { el } = ctx;
    if (!el.clientDynamicCard) return;
    el.clientDynamicCard.innerHTML = "";
    if (!profile?.bitrix_password_set) clearBitrixPasswordCardReveal(ctx);
    const effectiveConfig = applyClientCardRulesToCardConfig(config, profile);
    const blocks = (effectiveConfig?.blocks || [])
      .filter(blockShownInCard)
      .sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0));

    for (const block of blocks) {
      const fields = (block.fields || [])
        .filter(fieldShownInCard)
        .sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0));
      const blockEl = document.createElement("div");
      blockEl.className = `client-card-block ${clientCardColorClass(block.color_scheme)}`;
      applyClientCardBlockColor(blockEl, block.color_scheme);
      if (blockShowsTitle(block)) {
        const titleEl = document.createElement("div");
        titleEl.className = "client-card-block-title";
        titleEl.textContent = block.title || block.key || "";
        blockEl.appendChild(titleEl);
      }

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

        rowEl.appendChild(renderClientFieldValueWithExternalLink(field, value, ctx));
        blockEl.appendChild(rowEl);
        renderedFields += 1;
      }

      if (renderedFields > 0) {
        el.clientDynamicCard.appendChild(blockEl);
      }
    }
  }

  function fillClientPanel(ctx, profile) {
    const effectiveConfig = ctx.state.clientCardViewConfig || defaultClientCardViewConfig();
    renderClientDynamicCard(ctx, profile, effectiveConfig);
  }

  function syncDynamicModalFields(ctx) {
    applyClientCardRules(ctx);
  }

  function fillModal(ctx, profile) {
    const { el } = ctx;
    ctx.state.clientCardRuleProfile = profile || {};
    clearBitrixPasswordField(ctx);
    const bitrixState = bitrixPasswordState(ctx);
    bitrixState.hasSaved = Boolean(profile.bitrix_password_set);
    ctx.setControlValue(el.fContactName, profile.contact_name);
    ctx.setControlValue(el.fLastName, profile.last_name);
    ctx.setControlValue(el.fDestination, profile.destination);
    ctx.setControlValue(el.fSandOwner, profile.sand_owner);
    ctx.setControlValue(el.fSalesGroup, profile.sales_group);
    ctx.setControlValue(el.fLocationType, profile.location_type);
    ctx.setControlValue(el.fAnydesk, profile.anydesk);
    ctx.setControlValue(el.fInternalNumber, profile.internal_number);
    ctx.setControlValue(el.fBitrixLogin, profile.bitrix_login);
    ctx.setControlValue(el.fBitrixPassword, null);
    setBitrixPasswordInputHidden(ctx);
    ctx.setControlValue(el.fBitrixId, profile.bitrix_id);
    el.fIsVerified.checked = Boolean(profile.is_verified);
    renderCustomFieldsEditor(ctx, profile);
    bindBitrixPasswordField(ctx);
    bindClientCardRuleInputs(ctx);
    applyClientCardRules(ctx);
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

  function getModalPayload(ctx) {
    const { el } = ctx;
    validateClientCardRuleRequirements(ctx);
    const locationType = el.fLocationType.value.trim() || null;
    const anydesk = el.fAnydesk.value.trim() || null;

    const custom_fields = {};
    if (el.clientModal || el.customFieldsContainer) {
      const customInputs = (el.clientModal || el.customFieldsContainer).querySelectorAll("[data-custom-field-key]");
      customInputs.forEach((input) => {
        const key = input.dataset.customFieldKey;
        if (isRuntimeFieldHidden(ctx, key) || isRuntimeFieldReadonly(ctx, key)) return;
        const fieldType = input.dataset.customFieldType || "text";
        let rawValue = null;
        if (input.type === "checkbox") rawValue = input.checked;
        else rawValue = input.value;
        custom_fields[key] = parseCustomFieldValueForPayload(fieldType, rawValue);
      });
    }

    const payload = {
      contact_name: el.fContactName.value.trim() || null,
      last_name: el.fLastName.value.trim() || null,
      is_verified: el.fIsVerified.checked,
      destination: el.fDestination.value.trim() || null,
      sand_owner: el.fSandOwner.value.trim() || null,
      sales_group: el.fSalesGroup.value.trim() || null,
      location_type: locationType,
      anydesk,
      internal_number: el.fInternalNumber.value.trim() || null,
      bitrix_login: el.fBitrixLogin.value.trim() || null,
      bitrix_id: el.fBitrixId.value.trim() || null,
      custom_fields,
    };
    if (bitrixPasswordState(ctx).dirty) {
      payload.bitrix_password = bitrixPasswordState(ctx).draftValue.trim() || null;
    }
    for (const key of Object.keys(payload)) {
      if (isRuntimeFieldHidden(ctx, key) || isRuntimeFieldReadonly(ctx, key)) {
        delete payload[key];
      }
    }
    return payload;
  }

  async function openClientModal(ctx) {
    if (!ctx.state.card) return;
    try {
      ctx.setStatus("Загружаю текущий профиль клиента...");
      const profile = await ctx.api(`/api/v1/tickets/${ctx.state.ticketId}/client-profile`);
      ctx.state.currentClientProfile = profile || {};
      fillModal(ctx, ctx.state.currentClientProfile);
      ctx.el.clientModal.classList.remove("hidden");
      ctx.setStatus("Текущий профиль клиента загружен");
    } catch (e) {
      ctx.showError(e.message);
    }
  }

  function closeClientModal(ctx) {
    clearBitrixPasswordField(ctx);
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
      ctx.state.currentClientProfile = profile || {};
      await ctx.loadCard({ silent: true });
      closeClientModal(ctx);
      ctx.setStatus("Профиль клиента сохранён");
    } catch (e) {
      ctx.showError(e.message);
    }
  }

  function fieldValueForEditor(profile, field) {
    if (!field) return null;
    if (String(field.key || "").startsWith("custom_")) {
      return (profile?.custom_fields || {})[field.key] ?? null;
    }
    return getClientFieldValue(profile, field.key);
  }

  function renderReadonlyEditorField(field, value) {
    const wrap = document.createElement("div");
    wrap.className = "custom-field-editor readonly-copyable";
    wrap.dataset.clientCardFieldKey = field.key;
    const title = document.createElement("span");
    title.textContent = field.label || field.key;
    wrap.appendChild(title);
    const valueRow = document.createElement("div");
    valueRow.className = "readonly-copyable-row";
    const valueEl = renderClientFieldValue(field, value);
    if (valueEl.textContent.trim() === "") valueEl.textContent = "-";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "action";
    button.textContent = "\u0421\u043a\u043e\u043f\u0438\u0440\u043e\u0432\u0430\u0442\u044c";
    button.addEventListener("click", async () => {
      const text = String(value ?? "").trim();
      if (!text) return;
      try {
        await navigator.clipboard.writeText(text);
      } catch {
        const textarea = document.createElement("textarea");
        textarea.value = text;
        textarea.style.position = "fixed";
        textarea.style.opacity = "0";
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand("copy");
        textarea.remove();
      }
    });
    valueRow.appendChild(valueEl);
    valueRow.appendChild(button);
    wrap.appendChild(valueRow);
    return wrap;
  }

  function renderEditableCustomField(ctx, field, currentValue) {
    const wrap = document.createElement("label");
    wrap.className = "custom-field-editor";
    wrap.dataset.clientCardFieldKey = field.key;
    const title = document.createElement("span");
    title.textContent = field.label || field.key;
    wrap.appendChild(title);
    const fieldType = String(field.field_type || "text").toLowerCase();
    let inputEl = null;
    if (fieldType === "textarea") {
      inputEl = document.createElement("textarea");
      inputEl.rows = 3;
      inputEl.value = currentValue ?? "";
    } else if (fieldType === "select") {
      inputEl = document.createElement("select");
      const emptyOption = document.createElement("option");
      emptyOption.value = "";
      emptyOption.textContent = "\u041d\u0435 \u0432\u044b\u0431\u0440\u0430\u043d\u043e";
      inputEl.appendChild(emptyOption);
      const options = Array.isArray(field.options) ? field.options : [];
      const adminOnlyValues = new Set(Array.isArray(field.admin_only_values) ? field.admin_only_values : []);
      for (const optionValue of options) {
        const option = document.createElement("option");
        option.value = optionValue;
        option.textContent = optionValue;
        if (!isAdminRole(ctx) && adminOnlyValues.has(optionValue)) option.disabled = true;
        inputEl.appendChild(option);
      }
      inputEl.value = currentValue ?? "";
      if (!isAdminRole(ctx) && adminOnlyValues.has(inputEl.value)) inputEl.disabled = true;
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
    const baseReadonly = field.is_editable === false
      || (!isAdminRole(ctx) && field.admin_only_edit)
      || (!isAdminRole(ctx) && fieldType === "select" && new Set(Array.isArray(field.admin_only_values) ? field.admin_only_values : []).has(String(currentValue ?? "")));
    inputEl.dataset.clientCardBaseReadonly = baseReadonly ? "1" : "0";
    if (inputEl.dataset.clientCardBaseReadonly === "1") {
      setControlReadonly(inputEl, true);
      wrap.classList.add("client-card-field-readonly-rule");
    }
    wrap.appendChild(inputEl);
    return wrap;
  }

  function renderConfiguredSystemEditorField(ctx, field, currentValue) {
    const { control } = fieldEditorNodes(ctx, field.key);
    if (!control) return renderReadonlyEditorField(field, currentValue);
    const wrap = document.createElement("label");
    wrap.className = "custom-field-editor";
    wrap.dataset.clientCardFieldKey = field.key;
    const title = document.createElement("span");
    title.textContent = field.label || field.key;
    wrap.appendChild(title);
    wrap.appendChild(control);
    const fieldType = String(field.field_type || "text").toLowerCase();
    const adminOnlyValues = new Set(Array.isArray(field.admin_only_values) ? field.admin_only_values : []);
    if (control.tagName === "SELECT") {
      Array.from(control.options || []).forEach((option) => {
        option.disabled = !isAdminRole(ctx) && adminOnlyValues.has(option.value);
      });
    }
    const adminOnlySelectCurrent = !isAdminRole(ctx) && fieldType === "select" && adminOnlyValues.has(String(currentValue ?? control.value ?? ""));
    control.dataset.clientCardBaseReadonly = field.is_editable === false || (!isAdminRole(ctx) && field.admin_only_edit) || adminOnlySelectCurrent ? "1" : "0";
    if (control.dataset.clientCardBaseReadonly === "1") {
      setControlReadonly(control, true);
      wrap.classList.add("client-card-field-readonly-rule");
    }
    return wrap;
  }

  function renderCustomFieldsEditor(ctx, profile) {
    const { el, state } = ctx;
    if (!el.customFieldsSection) return;
    const sectionParent = el.customFieldsSection.parentElement;
    if (!sectionParent) return;
    if (el.customFieldsContainer) el.customFieldsContainer.innerHTML = "";
    Array.from(sectionParent.children).forEach((node) => {
      if (node.dataset?.clientCardGeneratedSection === "1") node.remove();
    });
    el.clientModal?.querySelectorAll(".modal-field-section-profile, .modal-field-section-department, .modal-field-section-work, .modal-field-section-verification")
      .forEach((section) => section.classList.add("hidden"));
    el.customFieldsSection.classList.add("hidden");
    el.customFieldsSection.querySelector(".status-panel-title")?.classList.add("hidden");
    const blockRuleTargets = activeEditorRuleBlockTargets(state.clientCardViewConfig);
    const blocks = (state.clientCardViewConfig?.blocks || [])
      .filter((block) => blockShownInEditorOrRuleTarget(block, blockRuleTargets))
      .sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0));
    const ruleTargets = activeEditorRuleFieldTargets(state.clientCardViewConfig);
    let renderedFields = 0;
    for (const block of blocks) {
      const fields = (block.fields || [])
        .filter((field) => fieldShownInEditorOrRuleTarget(field, ruleTargets))
        .sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0));
      if (!fields.length) continue;
      const blockWrap = document.createElement("section");
      blockWrap.className = "modal-field-section custom-field-editor-block";
      if (block.id) blockWrap.dataset.blockId = block.id;
      blockWrap.dataset.blockKey = block.key;
      blockWrap.dataset.clientCardBlockKey = block.key;
      blockWrap.dataset.clientCardGeneratedSection = "1";
      blockWrap.dataset.blockColorScheme = String(block.color_scheme || "");
      applyClientCardEditorSectionColor(blockWrap, block.color_scheme);
      if (!blockShownInEditor(block)) blockWrap.classList.add("hidden");
      if (blockShowsTitle(block)) {
        const title = document.createElement("div");
        title.className = "client-card-block-title";
        title.textContent = block.title || block.key || "";
        blockWrap.appendChild(title);
      }
      for (const field of fields) {
        const currentValue = fieldValueForEditor(profile, field);
        const baseHidden = !fieldShownInEditor(field);
        let fieldEl;
        if (field.is_system) {
          fieldEl = renderConfiguredSystemEditorField(ctx, field, currentValue);
        } else if (field.is_editable !== false) {
          fieldEl = renderEditableCustomField(ctx, field, currentValue);
        } else {
          fieldEl = renderReadonlyEditorField(field, currentValue);
        }
        if (baseHidden) fieldEl.classList.add("hidden");
        blockWrap.appendChild(fieldEl);
        renderedFields += 1;
      }
      sectionParent.insertBefore(blockWrap, el.customFieldsSection);
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
