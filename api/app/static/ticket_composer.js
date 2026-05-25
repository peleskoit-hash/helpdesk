(function initTicketComposer(global) {
  const { formatFileSize } = global.TicketApi || {};

  function selectedFileKey(file, index) {
    return `${index}:${file?.name || "file"}:${file?.size || 0}:${file?.lastModified || 0}`;
  }

  function quotedMessage(ctx) {
    const { state } = ctx;
    return (state.card?.messages || []).find((m) => m.id === state.quoteMessageId) || null;
  }

  function refreshComposerActions(ctx) {
    const { el } = ctx;
    const quoted = quotedMessage(ctx);
    if (quoted) {
      el.quotePreviewAuthor.textContent = ctx.messageAuthorLabel(quoted);
      el.quotePreviewText.textContent = quoted.body || "";
      el.quotePreview.classList.remove("hidden");
    } else {
      el.quotePreview.classList.add("hidden");
      el.quotePreviewAuthor.textContent = "";
      el.quotePreviewText.textContent = "";
    }
  }

  function quoteMessage(ctx, message) {
    const { state, el } = ctx;
    state.quoteMessageId = message.id;
    el.messageInput.focus();
    if (message.telegram_message_id) {
      ctx.setStatus(`Ответ будет отправлен в Telegram как reply на сообщение: ${ctx.messageAuthorLabel(message)}`);
    } else {
      ctx.setStatus("Цитата выбрана только в helpdesk: у сообщения нет Telegram ID для reply в Telegram");
    }
    refreshComposerActions(ctx);
  }

  function clearQuote(ctx) {
    ctx.state.quoteMessageId = null;
    refreshComposerActions(ctx);
  }

  function renderSelectedFiles(ctx) {
    const { state, el } = ctx;
    if (!el.selectedFilesList) return;
    el.selectedFilesList.innerHTML = "";
    el.selectedFilesList.classList.toggle("hidden", state.selectedFiles.length === 0);

    state.selectedFiles.forEach((file, index) => {
      const fileKey = selectedFileKey(file, index);
      const progress = state.fileUploadProgress[fileKey];
      const item = document.createElement("div");
      item.className = "selected-file-item";

      const meta = document.createElement("div");
      meta.className = "selected-file-meta";
      const name = document.createElement("div");
      name.className = "selected-file-name";
      name.textContent = file.name || "Файл";
      const size = document.createElement("div");
      size.className = "selected-file-size";
      size.textContent = formatFileSize(file.size);
      if (progress) {
        const stageText = progress.stage === "sending"
          ? `Отправляется в Telegram (${progress.current || 0}/${progress.total || 0})`
          : progress.stage === "done"
            ? "Отправлено"
            : progress.stage === "queued"
              ? "Ожидает отправки"
              : progress.stage === "error"
                ? `Ошибка: ${progress.message || "не удалось отправить"}`
                : "";
        if (stageText) size.textContent = `${size.textContent} В· ${stageText}`;
      }
      meta.append(name, size);
      item.append(meta);

      if (progress && progress.stage === "done") {
        item.classList.add("selected-file-item-ready");
      } else {
        item.classList.remove("selected-file-item-ready");
      }

      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "selected-file-remove";
      remove.textContent = "Убрать";
      remove.addEventListener("click", () => {
        state.selectedFiles.splice(index, 1);
        state.fileUploadProgress = {};
        state.selectedFiles.forEach((nextFile, nextIndex) => {
          state.fileUploadProgress[selectedFileKey(nextFile, nextIndex)] = { stage: "queued", percent: 0 };
        });
        if (el.replyFilesInput) el.replyFilesInput.value = "";
        renderSelectedFiles(ctx);
      });

      item.append(remove);
      el.selectedFilesList.appendChild(item);
    });
  }

  function clearSelectedFiles(ctx) {
    const { state, el } = ctx;
    state.selectedFiles = [];
    state.fileUploadProgress = {};
    if (el.replyFilesInput) el.replyFilesInput.value = "";
    renderSelectedFiles(ctx);
  }

  function applyComposeModeUi(ctx) {
    const { state, el } = ctx;
    const isReply = state.composeMode === "reply";
    el.modeReplyBtn.classList.toggle("primary", isReply);
    el.modeCommentBtn.classList.toggle("primary", !isReply);
    el.messageInput.classList.toggle("comment-mode", !isReply);
    el.messageInput.placeholder = isReply ? "Текст ответа клиенту" : "Внутренний комментарий";
    if (el.addFilesBtn) {
      el.addFilesBtn.disabled = !isReply || Boolean(state.card?.status === "closed");
      el.addFilesBtn.title = isReply ? "Прикрепить файлы к ответу клиенту" : "Файлы доступны только для ответа клиенту";
    }
  }

  async function sendByMode(ctx) {
    const { state, el, api } = ctx;
    if (state.isSendingReply) return;
    if (state.role === "chiki_piki") {
      ctx.showError("Роль Чики-пики работает только в режиме просмотра");
      return;
    }
    const text = el.messageInput.value.trim();
    const hasFiles = state.selectedFiles.length > 0;
    if (!text && !hasFiles) return;
    if (state.composeMode === "comment" && hasFiles) {
      ctx.showError("Файлы можно отправлять только в ответе клиенту. Внутренние комментарии пока поддерживают только текст.");
      return;
    }

    const actionName = state.composeMode === "reply" ? "reply" : "comment";
    try {
      state.isSendingReply = true;
      state.pendingCloseAfterSend = false;
      ctx.setStatus("Отправка...");
      const quoteMessageId = state.quoteMessageId;
      if (state.composeMode === "reply" && hasFiles) {
        const queue = [...state.selectedFiles];
        for (let index = 0; index < queue.length; index += 1) {
          const file = queue[index];
          const fileKey = selectedFileKey(file, index);
          state.fileUploadProgress[fileKey] = { stage: "sending", current: index + 1, total: queue.length };
          renderSelectedFiles(ctx);
          ctx.setStatus(`Отправка в Telegram: ${index + 1}/${queue.length}`);
          const formData = new FormData();
          formData.append("text", index === 0 ? text : "");
          if (index === 0 && quoteMessageId) formData.append("quote_message_id", quoteMessageId);
          formData.append("files", file, file.name);
          await api(`/api/v1/tickets/${state.ticketId}/reply`, {
            method: "POST",
            body: formData,
          });
          state.fileUploadProgress[fileKey] = { stage: "done" };
          renderSelectedFiles(ctx);
          const idxInState = state.selectedFiles.indexOf(file);
          if (idxInState >= 0) state.selectedFiles.splice(idxInState, 1);
          delete state.fileUploadProgress[fileKey];
          renderSelectedFiles(ctx);
          await ctx.loadCard(true);
        }
      } else {
        await api(`/api/v1/tickets/${state.ticketId}/${actionName}`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ text, quote_message_id: quoteMessageId }),
        });
      }
      el.messageInput.value = "";
      state.quoteMessageId = null;
      clearSelectedFiles(ctx);
      await ctx.loadCard();
      if (
        state.role === "agent"
        && state.composeMode === "reply"
        && (state.card?.workflow_type || "ticket") === "ticket"
        && ctx.isOnsitePhraseJs(text)
      ) {
        ctx.renderAvailabilityStatus("on_ticket");
      }
      if (state.card?.workflow_type === "task" && state.composeMode === "reply") {
        ctx.setStatus("Ответ сохранён. Клиент получит сообщение и файлы после завершения задачи.");
      } else {
        ctx.setStatus("Сообщение отправлено");
      }
    } catch (e) {
      if (state.selectedFiles.length) {
        const file = state.selectedFiles[0];
        const fileKey = selectedFileKey(file, 0);
        state.fileUploadProgress[fileKey] = { stage: "error", message: e.message || "ошибка отправки" };
        renderSelectedFiles(ctx);
      }
      ctx.showError(e.message);
    } finally {
      state.isSendingReply = false;
      if (state.pendingCloseAfterSend) {
        state.pendingCloseAfterSend = false;
        await ctx.action("close");
      }
    }
  }

  function bindComposerHandlers(ctx) {
    const { state, el } = ctx;
    el.modeReplyBtn.addEventListener("click", () => {
      state.composeMode = "reply";
      applyComposeModeUi(ctx);
    });

    el.modeCommentBtn.addEventListener("click", () => {
      state.composeMode = "comment";
      applyComposeModeUi(ctx);
    });

    el.sendNowBtn.addEventListener("click", async () => {
      await sendByMode(ctx);
    });
    el.addFilesBtn.addEventListener("click", () => {
      if (state.composeMode !== "reply") {
        ctx.showError("Файлы можно прикреплять только к ответу клиенту");
        return;
      }
      el.replyFilesInput.click();
    });
    el.replyFilesInput.addEventListener("change", () => {
      state.selectedFiles.push(...Array.from(el.replyFilesInput.files || []));
      state.fileUploadProgress = {};
      state.selectedFiles.forEach((file, index) => {
        state.fileUploadProgress[selectedFileKey(file, index)] = { stage: "queued", percent: 0 };
      });
      el.replyFilesInput.value = "";
      renderSelectedFiles(ctx);
    });
    el.clearQuoteBtn.addEventListener("click", () => clearQuote(ctx));

    el.messageInput.addEventListener("keydown", async (e) => {
      if (e.key === "Enter" && e.ctrlKey) {
        e.preventDefault();
        await sendByMode(ctx);
      }
    });
  }

  global.TicketComposer = {
    refreshComposerActions,
    quoteMessage,
    clearQuote,
    renderSelectedFiles,
    clearSelectedFiles,
    sendByMode,
    applyComposeModeUi,
    bindComposerHandlers,
    selectedFileKey,
  };
})(window);
