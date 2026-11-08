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
    if (["agent", "internal"].includes(message.message_type)
        && ["edited", "deleted"].includes(message.message_status)
        && (message.status_note || "").trim()) {
      return message.status_note;
    }
    if (message.message_status === "edited") {
      return `${messageAuthorLabel(state, card, message)} исправил сообщение`;
    }
    if (message.message_status === "deleted") {
      return `${messageAuthorLabel(state, card, message)} удалил сообщение`;
    }
    return message.status_note || "";
  }

  function canEditAgentMessage(state, message) {
    const canEdit = state.role === "admin" || (["agent", "trainee"].includes(state.role)
      && Boolean(message.author_user_id) && message.author_user_id === state.userId);
    return canEdit && ["agent", "internal"].includes(message.message_type) && message.message_status !== "deleted" && state.card?.status !== "closed";
  }

  function renderQuote(author, content, message, authorLabel) {
    author.textContent = "";
    content.textContent = "";
    global.TicketAttachments?.cleanupStickerResources?.(null);
    if (!message || message.message_type === "internal" || message.message_status === "deleted") {
      content.textContent = "Исходное сообщение недоступно";
      return;
    }
    author.textContent = authorLabel(message);
    const body = (message.body || "").trim();
    if (body) {
      const characters = Array.from(body);
      content.textContent = characters.slice(0, 100).join("") + (characters.length > 100 ? "…" : "");
      return;
    }
    const attachments = message.attachments || [];
    const first = attachments[0];
    if (!first) {
      content.textContent = "Сообщение без текста";
      return;
    }
    const sticker = /sticker|webp|webm/i.test(first.mime_type || "") || /\.(webp|webm|tgs)$/i.test(first.original_filename || "");
    if (first.is_image || sticker) {
      const thumbnail = document.createElement("span");
      thumbnail.className = "quote-thumbnail-host";
      content.appendChild(thumbnail);
      global.TicketAttachments.renderQuoteThumbnail(first, thumbnail);
      if (!sticker) {
        const more = attachments.filter((attachment) => attachment.is_image).length - 1;
        if (more > 0) {
          const count = document.createElement("span");
          count.className = "quote-photo-count";
          count.textContent = `+${more}`;
          content.appendChild(count);
        }
      }
    } else {
      content.textContent = first.original_filename || "Файл";
    }
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
      global.HelpdeskAppShell?.showSaved?.(ctx.el.messages?.closest(".panel.ticket-card"));
    } catch (e) {
      showError(e.message);
    }
  }

  async function deleteAgentMessage(ctx, message) {
    const { state, api, setStatus, loadCard, showError } = ctx;
    const confirmation = message.message_type === "internal"
      ? "Удалить внутренний комментарий?"
      : "Пометить сообщение удалённым и удалить его в Telegram, если возможно?";
    if (!await window.HelpdeskDialogs.confirm(confirmation)) return;
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
    if (state.role !== "chiki_piki" && message.message_type !== "internal") {
      addContextMenuItem(ctx, menu, "Цитировать", () => quoteMessage(message));
    }

    if (canEditAgentMessage(state, message)) {
      addContextMenuItem(ctx, menu, "Редактировать", () => editAgentMessage(ctx, message));
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

    const visibleMessages = (card.messages || []).filter((message) =>
      message.message_type !== "internal" || message.message_status !== "deleted");
    const scrollTop = el.messages.scrollTop;
    const top = el.messages.getBoundingClientRect().top;
    const anchor = Array.from(el.messages.children).find(node => node.getBoundingClientRect().bottom > top);
    const anchorOffset = anchor ? anchor.getBoundingClientRect().top - top : 0;
    const anchorId = anchor?.dataset.messageId;
    const anchorPart = anchor ? Array.from(el.messages.children).filter(node => node.dataset.messageId === anchorId).indexOf(anchor) : 0;
    const currentSignature = messageRenderSignature(visibleMessages);
    if (currentSignature !== lastSignature) {
      const currentAttachmentIds = new Set();
      for (const message of visibleMessages) {
        for (const attachment of message.attachments || []) {
          if (attachment?.id) currentAttachmentIds.add(attachment.id);
        }
      }
      cleanupAttachmentObjectUrls(currentAttachmentIds);
      cleanupStickerResources(currentAttachmentIds);

      el.messages.innerHTML = "";
      let orderedMessages = visibleMessages;
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
        div.dataset.messageId = m.id;
        const cls = m.message_type || "";
        const stickerOnly = isStickerOnlyMessage(m);
        div.className = `message ${cls} ${stickerOnly ? "message-sticker-only" : ""} ${m.message_status && m.message_status !== "normal" ? `message-${m.message_status}` : ""}`.trim();
        if (["client", "agent", "internal"].includes(m.message_type)) {
          div.addEventListener("contextmenu", (event) => showMessageContextMenu(ctx, event, m.sourceMessage || m));
        }
        if (m.quote_message_id) {
          const quote = document.createElement("div");
          quote.className = "quote-preview message-quote";
          const author = document.createElement("div");
          author.className = "quote-preview-author";
          const content = document.createElement("div");
          content.className = "quote-preview-text";
          quote.append(author, content);
          div.appendChild(quote);
          renderQuote(author, content, (card.messages || []).find((source) => source.id === m.quote_message_id),
            (source) => messageAuthorLabel(state, card, source));
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
      el.messages.scrollTop = scrollTop;
      const restored = Array.from(el.messages.children).filter(node => node.dataset.messageId === anchorId)[anchorPart];
      if (restored) el.messages.scrollTop += restored.getBoundingClientRect().top - el.messages.getBoundingClientRect().top - anchorOffset;
    }

    const quoteMissing = Boolean(quoteMessageId && !visibleMessages.some((m) => m.id === quoteMessageId && m.message_type !== "internal"));
    return {
      signature: currentSignature,
      quoteMissing,
    };
  }

  function createReadingController(container, confirmRead) {
    let ticketId = null, card = null, canRead = false, timer = null, inFlight = false, destroyed = false;
    let seenParts = new Set(), acknowledged = new Set();
    let pendingInitialMedia = false, initialScrollTop = 0;

    function mediaPending(node) {
      return Array.from(node.querySelectorAll("img, video")).some(media =>
        media.tagName === "IMG" ? !media.getAttribute("src") || !media.complete : media.readyState < 1);
    }

    function placeInitial(forceBottom = false) {
      const unreadIds = new Set(card.unread_message_ids || []);
      const first = canRead && !forceBottom
        ? Array.from(container.children).find(node => unreadIds.has(node.dataset.messageId)) : null;
      const firstTop = first ? first.getBoundingClientRect().top - container.getBoundingClientRect().top - container.clientTop + container.scrollTop : 0;
      container.scrollTop = first && container.scrollHeight - firstTop > container.clientHeight
        ? firstTop : container.scrollHeight;
      initialScrollTop = container.scrollTop;
    }

    function onScroll() {
      if (container.scrollTop !== initialScrollTop) pendingInitialMedia = false;
      observe();
    }

    function onMediaReady() {
      if (pendingInitialMedia) {
        placeInitial();
        pendingInitialMedia = mediaPending(container);
      }
      observe();
    }

    function messageParts() {
      const groups = new Map();
      for (const node of container.children) {
        const id = node.dataset.messageId;
        if (!groups.has(id)) groups.set(id, []);
        groups.get(id).push(node);
      }
      return groups;
    }

    function observe() {
      if (destroyed || !canRead || document.hidden || !container.clientHeight) return;
      const box = container.getBoundingClientRect();
      const top = box.top + container.clientTop, bottom = top + container.clientHeight;
      const groups = messageParts();
      for (const id of card.unread_message_ids || []) {
        (groups.get(id) || []).forEach((node, index) => {
          const rect = node.getBoundingClientRect();
          // A tall message is read on reaching its bottom, not merely its beginning.
          if (!mediaPending(node) && rect.height > 0 && rect.bottom > top && rect.bottom <= bottom + 1 && rect.top < bottom) {
            seenParts.add(`${id}:${index}`);
          }
        });
      }
      clearTimeout(timer);
      timer = setTimeout(flush, 180);
    }

    async function flush() {
      timer = null;
      if (destroyed || inFlight || !canRead || document.hidden) return;
      const messages = new Map((card.messages || []).map(message => [message.id, message]));
      const ids = (card.unread_message_ids || []).filter(id => !acknowledged.has(id));
      const groups = messageParts();
      const shown = [];
      let boundary = [];
      for (let index = 0; index < ids.length; index++) {
        const id = ids[index], nodes = groups.get(id) || [];
        if (!nodes.length || !nodes.every((_, part) => seenParts.has(`${id}:${part}`))) break;
        shown.push(id);
        // One timestamp boundary covers all messages at that timestamp, including split task parts.
        if (!ids[index + 1] || messages.get(id)?.created_at !== messages.get(ids[index + 1])?.created_at) {
          boundary = shown.slice();
        }
      }
      if (!boundary.length) return;
      const currentTicket = ticketId;
      inFlight = true;
      try {
        await confirmRead({ message_id: boundary[boundary.length - 1], message_ids: boundary });
        if (!destroyed && currentTicket === ticketId) boundary.forEach(id => acknowledged.add(id));
      } catch (error) {
        // Keep the boundary unchanged; the next real view/scroll can confirm it again.
        console.warn("Could not confirm visible ticket messages", error);
      } finally {
        inFlight = false;
      }
    }

    container.addEventListener("scroll", onScroll);
    container.addEventListener("load", onMediaReady, true);
    container.addEventListener("loadedmetadata", onMediaReady, true);
    container.addEventListener("error", onMediaReady, true);
    document.addEventListener("visibilitychange", observe);
    return {
      update(nextCard, options = {}) {
        if (destroyed) return;
        const initial = ticketId !== nextCard.id;
        if (initial) {
          clearTimeout(timer);
          ticketId = nextCard.id;
          seenParts = new Set();
          acknowledged = new Set();
        }
        card = nextCard;
        canRead = Boolean(options.canRead);
        if (initial || options.forceBottom) {
          placeInitial(options.forceBottom);
          pendingInitialMedia = initial && canRead && !options.forceBottom && mediaPending(container);
        }
        observe();
      },
      destroy() {
        destroyed = true;
        clearTimeout(timer);
        container.removeEventListener("scroll", onScroll);
        container.removeEventListener("load", onMediaReady, true);
        container.removeEventListener("loadedmetadata", onMediaReady, true);
        container.removeEventListener("error", onMediaReady, true);
        document.removeEventListener("visibilitychange", observe);
      },
    };
  }

  global.TicketMessageView = {
    createReadingController,
    renderQuote,
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
