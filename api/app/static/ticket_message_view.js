(function initTicketMessageView(global) {
  let editMessageResolve = null;

  function clientDisplayName(state, card) {
    const profile = card?.client_profile || {};
    if (profile.display_name) return profile.display_name;
    const fullName = `${profile.last_name || ""} ${profile.contact_name || ""}`.trim();
    if (fullName) return fullName;
    if (profile.telegram_display_name) return profile.telegram_display_name;
    return card?.client_name || "Клиент";
  }

  function normalizeAgentLabel(name) {
    if (!name) return "Агент";
    return name;
  }

  function messageAuthorLabel(state, card, message) {
    const messageType = typeof message === "string" ? message : message?.message_type;
    if (typeof message === "object" && message) {
      if (messageType === "client") return clientDisplayName(state, card);
      if (messageType === "agent") return normalizeAgentLabel(message.author_name);
      if (messageType === "internal") return normalizeAgentLabel(message.author_name);
      if (messageType === "system") return "ES DESK";
    }

    const labels = {
      client: "Клиент",
      agent: "Агент",
      internal: "Комментарий",
      system: "ES DESK",
    };
    return labels[messageType] || messageType || "";
  }

  function messageStatusNote(state, card, message) {
    if (message.message_status === "edited") {
      return `${messageAuthorLabel(state, card, message)} исправил сообщение`;
    }
    if (message.message_status === "deleted") {
      return `${messageAuthorLabel(state, card, message)} удалил сообщение`;
    }
    return message.status_note || "";
  }

  function canEditAgentMessage(state, message) {
    return state.role !== "chiki_piki" && message.message_type === "agent" && message.message_status !== "deleted" && state.card?.status !== "closed";
  }

  function hideMessageContextMenu(ctx) {
    const { el } = ctx;
    if (!el.messageContextMenu) return;
    el.messageContextMenu.classList.add("hidden");
    el.messageContextMenu.innerHTML = "";
  }

  function addContextMenuItem(ctx, menu, label, onClick, options = {}) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "message-menu-item";
    button.textContent = label;
    button.disabled = Boolean(options.disabled);
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      hideMessageContextMenu(ctx);
      onClick();
    });
    menu.appendChild(button);
  }

  function ensureEditMessageModal(ctx) {
    const { el, closeOnBackdropClick } = ctx;
    if (el.editMessageModal) return;

    const modal = document.createElement("div");
    modal.className = "modal hidden";

    const dialog = document.createElement("div");
    dialog.className = "modal-dialog edit-message-dialog";

    const head = document.createElement("div");
    head.className = "modal-head";
    const title = document.createElement("div");
    title.style.fontWeight = "700";
    title.textContent = "Редактирование сообщения";
    const cancelTop = document.createElement("button");
    cancelTop.type = "button";
    cancelTop.className = "action";
    cancelTop.textContent = "Закрыть";
    head.append(title, cancelTop);

    const body = document.createElement("div");
    body.className = "modal-body";
    const textarea = document.createElement("textarea");
    textarea.className = "edit-message-textarea";
    textarea.placeholder = "Новый текст сообщения";
    body.appendChild(textarea);

    const foot = document.createElement("div");
    foot.className = "modal-foot";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "action";
    cancel.textContent = "Отмена";
    const save = document.createElement("button");
    save.type = "button";
    save.className = "action primary";
    save.textContent = "Сохранить";
    foot.append(cancel, save);

    dialog.append(head, body, foot);
    modal.appendChild(dialog);
    document.body.appendChild(modal);

    function close(value) {
      modal.classList.add("hidden");
      const resolve = editMessageResolve;
      editMessageResolve = null;
      if (resolve) resolve(value);
    }

    cancelTop.addEventListener("click", () => close(null));
    cancel.addEventListener("click", () => close(null));
    save.addEventListener("click", () => close(textarea.value));
    closeOnBackdropClick(modal, () => close(null));
    textarea.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close(null);
      }
      if (event.key === "Enter" && event.ctrlKey) {
        event.preventDefault();
        close(textarea.value);
      }
    });

    el.editMessageModal = modal;
    el.editMessageTextarea = textarea;
    el.saveEditMessageBtn = save;
    el.cancelEditMessageBtn = cancel;
  }

  function cleanupEditMessageModal(ctx) {
    const { el } = ctx;
    if (editMessageResolve) {
      editMessageResolve(null);
      editMessageResolve = null;
    }
    if (el.editMessageModal) {
      el.editMessageModal.remove();
      el.editMessageModal = null;
      el.editMessageTextarea = null;
      el.saveEditMessageBtn = null;
      el.cancelEditMessageBtn = null;
    }
  }

  function openEditMessageModal(ctx, initialText) {
    const { el } = ctx;
    ensureEditMessageModal(ctx);
    if (editMessageResolve) {
      editMessageResolve(null);
    }
    el.editMessageTextarea.value = initialText || "";
    el.editMessageModal.classList.remove("hidden");
    el.editMessageTextarea.focus();
    el.editMessageTextarea.setSelectionRange(0, el.editMessageTextarea.value.length);
    return new Promise((resolve) => {
      editMessageResolve = resolve;
    });
  }

  async function editAgentMessage(ctx, message) {
    const { state, api, setStatus, loadCard, showError } = ctx;
    const text = await openEditMessageModal(ctx, message.body || "");
    if (text === null) return;
    const trimmed = text.trim();
    if (!trimmed) return;
    try {
      setStatus("Редактирую сообщение...");
      await api(`/api/v1/tickets/${state.ticketId}/messages/${message.id}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ text: trimmed }),
      });
      await loadCard();
      setStatus("Сообщение исправлено");
    } catch (e) {
      showError(e.message);
    }
  }

  async function deleteAgentMessage(ctx, message) {
    const { state, api, setStatus, loadCard, showError } = ctx;
    if (!window.confirm("Пометить сообщение удалённым и удалить его в Telegram, если возможно?")) return;
    try {
      setStatus("Удаляю сообщение...");
      await api(`/api/v1/tickets/${state.ticketId}/messages/${message.id}`, { method: "DELETE" });
      await loadCard();
      setStatus("Сообщение помечено удалённым");
    } catch (e) {
      showError(e.message);
    }
  }

  async function copyMessageText(ctx, message) {
    const { setStatus } = ctx;
    const text = message.body || "";
    try {
      await navigator.clipboard.writeText(text);
      setStatus("Текст сообщения скопирован");
    } catch {
      const tmp = document.createElement("textarea");
      tmp.value = text;
      tmp.style.position = "fixed";
      tmp.style.left = "-9999px";
      document.body.appendChild(tmp);
      tmp.select();
      document.execCommand("copy");
      tmp.remove();
      setStatus("Текст сообщения скопирован");
    }
  }

  function showMessageContextMenu(ctx, event, message) {
    const { state, el, quoteMessage } = ctx;
    if (!["client", "agent", "internal"].includes(message.message_type)) return;
    event.preventDefault();
    event.stopPropagation();

    const menu = el.messageContextMenu;
    menu.innerHTML = "";
    addContextMenuItem(ctx, menu, "Копировать", () => copyMessageText(ctx, message));
    if (state.role !== "chiki_piki") {
      addContextMenuItem(ctx, menu, "Цитировать", () => quoteMessage(message));
    }

    if (canEditAgentMessage(state, message)) {
      addContextMenuItem(ctx, menu, "Исправить", () => editAgentMessage(ctx, message));
      addContextMenuItem(ctx, menu, "Удалить", () => deleteAgentMessage(ctx, message));
    }

    menu.classList.remove("hidden");
    const rect = menu.getBoundingClientRect();
    const left = Math.min(event.clientX, window.innerWidth - rect.width - 8);
    const top = Math.min(event.clientY, window.innerHeight - rect.height - 8);
    menu.style.left = `${Math.max(8, left)}px`;
    menu.style.top = `${Math.max(8, top)}px`;
  }

  function renderMessages(ctx, card, lastSignature, quoteMessageId) {
    const { state, el, fmtMessageStamp, linkifyText, isStickerOnlyMessage, messageRenderSignature, cleanupAttachmentObjectUrls, cleanupStickerResources, renderAttachments } = ctx;

    const wasNearBottom = el.messages.scrollHeight - el.messages.scrollTop - el.messages.clientHeight < 80;
    const currentSignature = messageRenderSignature(card.messages || []);
    if (currentSignature !== lastSignature) {
      const currentAttachmentIds = new Set();
      for (const message of card.messages || []) {
        for (const attachment of message.attachments || []) {
          if (attachment?.id) currentAttachmentIds.add(attachment.id);
        }
      }
      cleanupAttachmentObjectUrls(currentAttachmentIds);
      cleanupStickerResources(currentAttachmentIds);

      el.messages.innerHTML = "";
      let orderedMessages = card.messages || [];
      const initialMessages = orderedMessages.filter((message) => message.is_initial_task_message && message.message_type === "client");
      if (initialMessages.some((message) => (message.attachments || []).length)) {
        const assignment = orderedMessages.find((message) => message.is_initial_task_assignment && message.message_type === "system");
        const initialIds = new Set(initialMessages.map((message) => message.id));
        if (assignment) initialIds.add(assignment.id);
        const initialParts = [
          ...initialMessages.flatMap((message) => (message.attachments || []).map((attachment) => ({
            ...message, body: "", attachments: [attachment], sourceMessage: message,
          }))),
          ...initialMessages.filter((message) => message.is_initial_task_description && (message.body || "").trim())
            .map((message) => ({ ...message, attachments: [], sourceMessage: message })),
          ...(assignment ? [assignment] : []),
        ];
        let inserted = false;
        orderedMessages = orderedMessages.flatMap((message) => {
          if (!initialIds.has(message.id)) return [message];
          if (inserted) return [];
          inserted = true;
          return initialParts;
        });
      }
      const displayMessages = orderedMessages.flatMap((message) => {
        if (!message.is_initial_task_description || message.message_type !== "client"
            || !(message.body || "").trim() || !(message.attachments || []).length) return [message];
        return [
          { ...message, attachments: [], sourceMessage: message },
          ...message.attachments.map((attachment) => ({ ...message, body: "", attachments: [attachment], sourceMessage: message })),
        ];
      });
      for (const m of displayMessages) {
        const div = document.createElement("div");
        const cls = m.message_type || "";
        const stickerOnly = isStickerOnlyMessage(m);
        div.className = `message ${cls} ${stickerOnly ? "message-sticker-only" : ""} ${m.message_status && m.message_status !== "normal" ? `message-${m.message_status}` : ""}`.trim();
        if (["client", "agent", "internal"].includes(m.message_type)) {
          div.addEventListener("contextmenu", (event) => showMessageContextMenu(ctx, event, m.sourceMessage || m));
        }
        if (!stickerOnly) {
          const headerEl = document.createElement("div");
          headerEl.className = "message-header";
          const strong = document.createElement("strong");
          strong.textContent = messageAuthorLabel(state, card, m);
          const dateEl = document.createElement("div");
          dateEl.className = "message-date";
          dateEl.textContent = fmtMessageStamp(m.created_at);
          headerEl.append(strong, dateEl);
          div.appendChild(headerEl);
        }
        if ((m.body || "").trim()) {
          const bodyEl = document.createElement("div");
          bodyEl.className = "message-body";
          bodyEl.innerHTML = linkifyText(m.body || "");
          div.appendChild(bodyEl);
        }
        renderAttachments(m, div);
        const note = messageStatusNote(state, card, m);
        if (note) {
          const noteEl = document.createElement("div");
          noteEl.className = "message-status-note";
          noteEl.textContent = note;
          div.appendChild(noteEl);
        }
        if (stickerOnly) {
          const ts = document.createElement("div");
          ts.className = "sticker-time-hover";
          ts.textContent = fmtMessageStamp(m.created_at);
          div.appendChild(ts);
        }
        el.messages.appendChild(div);
      }
      cleanupStickerResources(currentAttachmentIds);
    }

    const quoteMissing = Boolean(quoteMessageId && !(card.messages || []).some((m) => m.id === quoteMessageId));
    return {
      signature: currentSignature,
      quoteMissing,
      wasNearBottom,
    };
  }

  global.TicketMessageView = {
    renderMessages,
    showMessageContextMenu,
    hideMessageContextMenu,
    editAgentMessage,
    deleteAgentMessage,
    copyMessageText,
    ensureEditMessageModal,
    cleanupEditMessageModal,
  };
})(window);
