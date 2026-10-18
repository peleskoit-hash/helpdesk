(function (global) {
  "use strict";
  const LOGIN = "custom_web_login", PASSWORD = "custom_web_password";
  function isWebField(key) { return key === LOGIN || key === PASSWORD; }
  function canEdit(ctx) { return ["admin", "agent", "trainee"].includes(ctx.state.role); }
  function owner(ctx) { return String(ctx.state.ticketId || ctx.state.selectedClientId || ""); }
  function presentation() { return global.TicketClientCard.passwordPresentation; }
  function endpoint(ctx, suffix) {
    if (ctx.state.ticketId) return `/api/v1/tickets/${encodeURIComponent(ctx.state.ticketId)}/client-profile/${suffix}`;
    return `/api/v1/management/users/${encodeURIComponent(ctx.state.selectedClientId)}/profile/${suffix}`;
  }
  function controls(ctx) {
    const root = ctx.el.clientModal || ctx.el.customFieldsContainer;
    return [LOGIN, PASSWORD].map(key => root?.querySelector(`[data-custom-field-key="${key}"]`));
  }
  function value(profile, key) {
    if (key === LOGIN) return profile?.web_login || "";
    if (key === PASSWORD) return profile?.web_password_set ? "********" : "";
    return undefined;
  }
  function passwordValue(ctx) { return ctx.state.webPasswordDraft || ""; }
  function setPasswordDraft(ctx, input, value) {
    ctx.state.webPasswordDraft = value;
    input.dataset.webPasswordDirty = "1";
    presentation().clear(ctx, PASSWORD);
    presentation().show(input, value);
  }
  function hasPassword(ctx) {
    const password = controls(ctx)[1];
    return password?.dataset.webPasswordDirty === "1" ? Boolean(passwordValue(ctx)) : Boolean(ctx.state.currentClientProfile?.web_password_set);
  }
  function error(ctx, failure) {
    const message = failure.message || "Не удалось получить WEB-реквизиты. Повторите попытку.";
    if (ctx.showError) ctx.showError(message);
    else ctx.setStatus?.(message);
  }
  function maskInput(ctx) {
    const password = controls(ctx)[1];
    if (!password) return;
    const present = hasPassword(ctx);
    if (!present) {
      presentation().clear(ctx, PASSWORD);
      delete ctx.state.clientCardWebPasswordMask;
    }
    password.value = present ? presentation().mask(ctx, PASSWORD) : "";
    password.classList.remove("client-card-password-revealed");
    password.classList.toggle("client-card-password", present);
  }
  function protectValue(ctx, key, node, current) {
    if (key !== PASSWORD) return false;
    node.textContent = current ? "********" : "";
    if (!current || !ctx) return true;
    presentation().render(ctx, node, PASSWORD);
    presentation().bind(ctx, node, PASSWORD);
    return true;
  }
  function protectInput(ctx, field, input) {
    if (field.key !== PASSWORD) return;
    ctx.state.webCredentialsConfirm?.(false);
    const shared = presentation();
    shared.clear(ctx, PASSWORD);
    ctx.state.webCredentialsRevision = (ctx.state.webCredentialsRevision || 0) + 1;
    ctx.state.webPasswordDraft = "";
    if (!ctx.state.currentClientProfile?.web_password_set) delete ctx.state.clientCardWebPasswordMask;
    input.dataset.webPasswordDirty = "0";
    input.autocomplete = "off";
    input.spellcheck = false;
    input.setAttribute("autocapitalize", "off");
    input.setAttribute("data-lpignore", "true");
    input.setAttribute("data-1p-ignore", "true");
    input.setAttribute("data-bwignore", "true");
    // The input is not in the document until its editor block has been assembled.
    input.value = ctx.state.currentClientProfile?.web_password_set ? shared.mask(ctx, PASSWORD) : "";
    input.classList.toggle("client-card-password", Boolean(input.value));
    let loading = false, editIntent = false, manualInput = false;
    const show = async () => {
      if (!hasPassword(ctx)) { maskInput(ctx); return; }
      if (!canEdit(ctx) || loading) return;
      const current = owner(ctx), revision = ctx.state.webCredentialsRevision;
      const originalDraft = passwordValue(ctx), originalDirty = input.dataset.webPasswordDirty;
      const reveal = shared.active(ctx, shared.owner(ctx), PASSWORD);
      if (reveal) { shared.show(input, reveal.plaintext); return; }
      loading = true;
      try {
        const plaintext = originalDirty === "1" ? originalDraft : (await shared.read(ctx, PASSWORD))?.web_password;
        if (!canEdit(ctx) || current !== owner(ctx) || revision !== ctx.state.webCredentialsRevision || !input.isConnected
            || originalDirty !== input.dataset.webPasswordDirty || originalDraft !== passwordValue(ctx)) return;
        if (!plaintext) return;
        const state = shared.state(ctx, PASSWORD);
        state.ownerKey = shared.owner(ctx);
        state.plaintext = plaintext;
        state.revealedUntil = Date.now() + shared.duration;
        shared.show(input, plaintext);
        shared.schedule(ctx, state.ownerKey, PASSWORD);
      } catch (failure) {
        if (current === owner(ctx) && revision === ctx.state.webCredentialsRevision) error(ctx, failure);
      } finally { loading = false; }
    };
    input.addEventListener("focus", show);
    input.addEventListener("click", show);
    input.addEventListener("blur", () => { editIntent = manualInput = false; maskInput(ctx); });
    input.addEventListener("keydown", event => {
      editIntent = event.isTrusted && (event.key.length === 1 || ["Backspace", "Delete"].includes(event.key));
    });
    for (const type of ["paste", "cut", "drop"]) input.addEventListener(type, event => { editIntent = event.isTrusted; });
    input.addEventListener("beforeinput", event => {
      if (["insertLineBreak", "insertParagraph"].includes(event.inputType)) event.preventDefault();
      manualInput = event.isTrusted && (editIntent || event.isComposing) && event.inputType !== "insertReplacementText";
      editIntent = false;
    });
    input.addEventListener("input", event => {
      const intended = manualInput && event.isTrusted && canEdit(ctx);
      manualInput = editIntent = false;
      // Autofill/extensions must never turn display contents into a password change.
      if (!intended) { maskInput(ctx); return; }
      setPasswordDraft(ctx, input, input.value.replace(/[\r\n]/g, ""));
    });
  }
  function sourceValues(ctx) {
    const values = {}, root = ctx.el.clientModal;
    const profile = ctx.state.currentClientProfile || ctx.state.card?.client_profile || {};
    for (const [key, id] of [["bitrix_login", "fBitrixLogin"], ["contact_name", "fContactName"], ["last_name", "fLastName"]]) {
      const live = root?.querySelector(`#${id}`);
      const control = live || (ctx.el[id]?.isConnected !== false ? ctx.el[id] : null);
      if (control) values[key] = control.value || null;
      else if (Object.hasOwn(profile, key)) values[key] = profile[key];
      // Omit unavailable values, so they cannot overwrite the server's stored sources with null.
    }
    return values;
  }
  function confirmGeneration(ctx, loginSet, passwordSet) {
    ctx.state.webCredentialsConfirm?.(false);
    return new Promise(resolve => {
      const modal = document.createElement("div");
      modal.className = "modal";
      modal.id = "clientWebGenerateConfirm";
      modal.setAttribute("role", "dialog");
      modal.setAttribute("aria-modal", "true");
      modal.setAttribute("aria-labelledby", "clientWebGenerateTitle");
      modal.innerHTML = `
        <div class="modal-dialog">
          <div class="modal-head">
            <h3 id="clientWebGenerateTitle">Подтверждение генерации WEB</h3>
            <button type="button" class="action" data-web-confirm-close>Закрыть</button>
          </div>
          <div class="modal-body"></div>
          <div class="modal-foot">
            <button type="button" class="action" data-web-confirm-no>Нет</button>
            <button type="button" class="action primary" data-web-confirm-yes>Да</button>
          </div>
        </div>`;
      const body = modal.querySelector(".modal-body");
      const text = loginSet && passwordSet
        ? "Внимание! Существующие WEB-логин и пароль будут заменены новыми после сохранения карточки"
        : loginSet ? "Будет создан новый WEB-пароль. Существующий логин останется без изменений"
        : passwordSet ? "Будет создан WEB-логин. Существующий пароль останется без изменений"
        : "Будут созданы новый WEB-логин и пароль";
      const messages = [text];
      if (!loginSet || passwordSet) messages.push("Логин сначала берётся из Bitrix. Если он занят или отсутствует, создаётся из имени и фамилии латиницей. При совпадении подбирается свободный вариант");
      messages.push("Изменения вступят в силу только после сохранения карточки. Продолжить генерацию?");
      for (const message of messages) {
        const paragraph = document.createElement("p"); paragraph.textContent = message; body.appendChild(paragraph);
      }
      const editor = ctx.el.clientModal;
      const focus = document.activeElement;
      const finish = accepted => {
        if (ctx.state.webCredentialsConfirm !== finish) return;
        ctx.state.webCredentialsConfirm = null;
        modal.remove();
        editor?.removeEventListener("input", invalidate, true);
        editor?.removeEventListener("change", invalidate, true);
        document.removeEventListener("keydown", keydown, true);
        if (focus?.isConnected && !editor?.classList.contains("hidden")) focus.focus();
        resolve(accepted);
      };
      const invalidate = () => finish(false);
      const keydown = event => {
        if (event.key === "Escape") { event.preventDefault(); event.stopImmediatePropagation(); finish(false); }
      };
      ctx.state.webCredentialsConfirm = finish;
      modal.querySelector("[data-web-confirm-close]").addEventListener("click", () => finish(false));
      modal.querySelector("[data-web-confirm-no]").addEventListener("click", () => finish(false));
      modal.querySelector("[data-web-confirm-yes]").addEventListener("click", () => finish(true));
      modal.addEventListener("click", event => { if (event.target === modal) finish(false); });
      editor?.addEventListener("input", invalidate, true);
      editor?.addEventListener("change", invalidate, true);
      document.addEventListener("keydown", keydown, true);
      document.body.appendChild(modal);
      modal.querySelector("[data-web-confirm-no]").focus();
    });
  }
  function addGenerate(ctx) {
    const [login, password] = controls(ctx);
    if (!login || !password) return;
    const button = document.createElement("button");
    button.type = "button"; button.className = "action"; button.textContent = "Генерировать WEB";
    button.style.gridColumn = "1 / -1";
    button.style.justifySelf = "start";
    const readonly = () => !canEdit(ctx) || [login, password].some(input => input.disabled || input.readOnly || input.dataset.clientCardBaseReadonly === "1");
    button.disabled = readonly();
    button.addEventListener("click", async event => {
      event.preventDefault();
      if (readonly() || button.disabled) return;
      const current = owner(ctx), revision = ctx.state.webCredentialsRevision;
      const originalLogin = login.value, originalPassword = passwordValue(ctx), originalDirty = password.dataset.webPasswordDirty;
      const passwordSet = hasPassword(ctx), both = Boolean(login.value.trim() && passwordSet);
      const sources = sourceValues(ctx), passwordOwner = presentation().owner(ctx), cardStatus = ctx.state.card?.status;
      const fresh = () => owner(ctx) === current && revision === ctx.state.webCredentialsRevision
        && login.isConnected && password.isConnected && !readonly()
        && presentation().owner(ctx) === passwordOwner && ctx.state.card?.status === cardStatus
        && login.value === originalLogin && password.dataset.webPasswordDirty === originalDirty
        && passwordValue(ctx) === originalPassword && hasPassword(ctx) === passwordSet
        && JSON.stringify(sourceValues(ctx)) === JSON.stringify(sources);
      button.disabled = true;
      try {
        if (!await confirmGeneration(ctx, Boolean(login.value.trim()), passwordSet) || !fresh()) return;
        const draft = await ctx.api(endpoint(ctx, "web-credentials/generate"), { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({
          web_login: login.value.trim() || null, web_password_set: passwordSet, regenerate: both, ...sources,
        }) });
        if (!fresh()) return;
        const replacePassword = !passwordSet || both;
        if (!draft || typeof draft.web_login !== "string" || !draft.web_login.trim()
            || (replacePassword && (typeof draft.web_password !== "string" || !draft.web_password))) {
          throw new Error("Сервер не вернул WEB-реквизиты. Поля не изменены.");
        }
        // Validate the complete response before changing either field.
        if (!originalLogin.trim() || both) {
          login.value = draft.web_login;
          login.dispatchEvent(new Event("input", { bubbles: true }));
        }
        if (replacePassword) {
          setPasswordDraft(ctx, password, draft.web_password);
          password.dispatchEvent(new Event("input", { bubbles: true }));
          maskInput(ctx);
        }
      } catch (failure) {
        if (owner(ctx) === current && revision === ctx.state.webCredentialsRevision) error(ctx, failure);
      } finally { button.disabled = readonly(); }
    });
    const loginField = login.parentElement, passwordField = password.parentElement, block = loginField.parentElement;
    if (passwordField.parentElement === block) block.insertBefore(passwordField, loginField.nextSibling);
    block.insertBefore(button, loginField);
  }
  function hasChanges(ctx) { return controls(ctx)[1]?.dataset.webPasswordDirty === "1"; }
  function clear(ctx) {
    ctx.state.webCredentialsConfirm?.(false);
    ctx.state.webCredentialsRevision = (ctx.state.webCredentialsRevision || 0) + 1;
    ctx.state.webPasswordDraft = "";
    presentation().clear(ctx, PASSWORD);
    if (!ctx.state.currentClientProfile?.web_password_set) delete ctx.state.clientCardWebPasswordMask;
    const password = controls(ctx)[1];
    if (password) {
      password.value = ""; password.dataset.webPasswordDirty = "0";
      password.classList.remove("client-card-password", "client-card-password-revealed");
    }
  }
  global.ClientWebCredentials = { isWebField, canEdit, value, protectValue, protectInput, addGenerate, passwordValue, maskInput, hasChanges, clear };
})(window);
