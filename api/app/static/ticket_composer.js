(function initTicketComposer(global) {
  const { formatFileSize } = global.TicketApi || {};

  function quotedMessage(ctx) {
    const { state } = ctx;
    return (state.card?.messages || []).find((m) => m.id === state.quoteMessageId) || null;
  }

  function quotedMessageIdForTelegram(ctx) {
    const quoted = quotedMessage(ctx);
    return quoted?.telegram_message_id || null;
  }

  function ensurePreloadMap(state) {
    if (!(state.filePreloads instanceof Map)) {
      state.filePreloads = new Map();
    }
    return state.filePreloads;
  }

  function getPreloadState(state, file) {
    return ensurePreloadMap(state).get(file) || null;
  }

  function setPreloadState(state, file, nextState) {
    ensurePreloadMap(state).set(file, nextState);
    return nextState;
  }

  function clearPreloadPoll(state, file) {
    const preload = getPreloadState(state, file);
    if (!preload?.pollTimer) return;
    window.clearInterval(preload.pollTimer);
    preload.pollTimer = null;
  }

  function abortPreloadRequest(state, file) {
    const preload = getPreloadState(state, file);
    if (!preload?.controller) return;
    try {
      preload.controller.abort();
    } catch {
      // noop
    }
    preload.controller = null;
  }

  function forgetPreloadState(state, file) {
    clearPreloadPoll(state, file);
    abortPreloadRequest(state, file);
    ensurePreloadMap(state).delete(file);
  }

  function cleanupPreloadRuntimeState(state) {
    for (const [file] of ensurePreloadMap(state).entries()) {
      clearPreloadPoll(state, file);
      abortPreloadRequest(state, file);
    }
  }

  function cleanupAllPreloadState(state) {
    for (const [file] of ensurePreloadMap(state).entries()) {
      forgetPreloadState(state, file);
    }
  }

  function canUseAttachmentPreload(ctx) {
    return Boolean(ctx.canUseAttachmentPreload?.());
  }

  function workflowType(ctx) {
    return String(ctx.workflowType?.() || ctx.state?.card?.workflow_type || "ticket").trim().toLowerCase() || "ticket";
  }

  function isTaskReply(ctx) {
    return ctx.state?.composeMode === "reply" && workflowType(ctx) === "task";
  }

  function nextArchiveGroupId() {
    if (global.crypto?.randomUUID) return global.crypto.randomUUID();
    return `archive-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  function ensureArchiveGroupId(state) {
    if (!state.currentArchiveGroupId) {
      state.currentArchiveGroupId = nextArchiveGroupId();
    }
    return state.currentArchiveGroupId;
  }

  function stageLabel(preload) {
    const stage = preload?.stage || "queued";
    if (stage === "browser_uploading") return "Передается в ES DESK...";
    if (stage === "uploading") return "Загружается в Telegram...";
    if (stage === "ready") return "Готов к отправке";
    if (stage === "failed") return "Не удалось подготовить";
    if (stage === "cancelled") return "Отменено";
    if (stage === "sending") return "Передается в ES DESK...";
    return "Ожидает отправки";
  }

  function shouldShowSelectedFileProgress(preload) {
    const stage = preload?.stage || "";
    return stage === "browser_uploading" || stage === "uploading" || stage === "sending";
  }

  function isRestorablePreloadStatus(status) {
    return status === "uploading" || status === "ready";
  }

  function createRestoredPreloadFile(preload) {
    return {
      name: preload?.original_filename || "Файл",
      size: Number(preload?.file_size) || 0,
      __attachmentPreloadVirtual: true,
      __attachmentPreloadId: preload?.id || null,
    };
  }

  function isVirtualPreloadFile(file) {
    return Boolean(file?.__attachmentPreloadVirtual);
  }

  function findSelectedFileByPreloadId(state, preloadId) {
    return state.selectedFiles.find((file) => getPreloadState(state, file)?.preloadId === preloadId) || null;
  }

  function createSelectedFileRow(ctx, file) {
    const { state } = ctx;
    const preload = getPreloadState(state, file);
    const item = document.createElement("div");
    item.className = "selected-file-item";

    const header = document.createElement("div");
    header.className = "selected-file-head";

    const name = document.createElement("div");
    name.className = "selected-file-name";
    name.textContent = file.name || "Файл";

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "selected-file-remove";
    remove.textContent = "Убрать";
    remove.addEventListener("click", () => {
      void removeSelectedFile(ctx, file);
    });

    header.append(name, remove);

    const footer = document.createElement("div");
    footer.className = "selected-file-foot";

    const meta = document.createElement("div");
    meta.className = "selected-file-size";
    const stageText = preload ? stageLabel(preload) : "Ожидает отправки";
    meta.textContent = `${formatFileSize(file.size)} · ${stageText}`;
    footer.append(meta);

    if (preload?.stage === "ready") {
      item.classList.add("selected-file-item-ready");
    }
    if (shouldShowSelectedFileProgress(preload)) {
      item.classList.add("selected-file-item-active");
      const progress = document.createElement("div");
      progress.className = "selected-file-progress";
      const progressFill = document.createElement("div");
      progressFill.className = "selected-file-progress-fill";
      progress.append(progressFill);
      footer.append(progress);
    }

    item.append(header, footer);
    return item;
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
    const previousScrollTop = Number(el.selectedFilesList.scrollTop) || 0;
    el.selectedFilesList.innerHTML = "";
    el.selectedFilesList.classList.toggle("hidden", state.selectedFiles.length === 0);

    state.selectedFiles.forEach((file) => {
      el.selectedFilesList.appendChild(createSelectedFileRow(ctx, file));
    });
    el.selectedFilesList.scrollTop = previousScrollTop;
  }

  function syncRestoredPreloads(ctx, serverPreloads) {
    const { state } = ctx;
    const activePreloads = Array.from(serverPreloads || []).filter((preload) => isRestorablePreloadStatus(preload?.status));
    const nextSelectedFiles = state.selectedFiles.filter((file) => {
      const preload = getPreloadState(state, file);
      return !preload?.preloadId;
    });
    const keepFiles = new Set(nextSelectedFiles);

    for (const serverPreload of activePreloads) {
      let file = findSelectedFileByPreloadId(state, serverPreload.id);
      if (!file) {
        file = createRestoredPreloadFile(serverPreload);
      }
      keepFiles.add(file);
      if (!nextSelectedFiles.includes(file)) {
        nextSelectedFiles.push(file);
      }
      const previousState = getPreloadState(state, file);
      setPreloadState(state, file, {
        stage: serverPreload.status,
        status: serverPreload.status,
        preloadId: serverPreload.id,
        controller: null,
        pollTimer: previousState?.pollTimer || null,
        error: null,
        removed: false,
        restored: true,
      });
      if (serverPreload.status === "uploading") {
        startPreloadPolling(ctx, file, serverPreload.id);
      } else {
        clearPreloadPoll(state, file);
      }
    }

    for (const [file] of ensurePreloadMap(state).entries()) {
      if (!keepFiles.has(file)) {
        forgetPreloadState(state, file);
      }
    }

    state.selectedFiles = nextSelectedFiles;
    renderSelectedFiles(ctx);
  }

  async function pollPreloadStatus(ctx, file, preloadId) {
    const { state, api } = ctx;
    const preload = getPreloadState(state, file);
    if (!preload || preload.preloadId !== preloadId) return;
    try {
      const payload = await api(`/api/v1/tickets/${state.ticketId}/attachment-preloads/${preloadId}`);
      const next = getPreloadState(state, file);
      if (!next || next.preloadId !== preloadId) return;
      next.status = payload?.status || next.status;
      next.stage = payload?.status || next.stage;
      next.error = next.stage === "failed" ? "Не удалось подготовить" : null;
      if (["ready", "failed", "cancelled"].includes(next.stage)) {
        clearPreloadPoll(state, file);
      }
      renderSelectedFiles(ctx);
    } catch (e) {
      const next = getPreloadState(state, file);
      if (!next || next.preloadId !== preloadId) return;
      next.stage = "failed";
      next.error = e.message || "Не удалось подготовить";
      clearPreloadPoll(state, file);
      renderSelectedFiles(ctx);
    }
  }

  function startPreloadPolling(ctx, file, preloadId) {
    const { state } = ctx;
    const preload = getPreloadState(state, file);
    if (!preload || preload.preloadId !== preloadId) return;
    clearPreloadPoll(state, file);
    preload.pollTimer = window.setInterval(() => {
      void pollPreloadStatus(ctx, file, preloadId);
    }, 1500);
  }

  async function startAttachmentPreload(ctx, file, archiveGroupIdOverride = null) {
    const { state, api } = ctx;
    if (!canUseAttachmentPreload(ctx)) return;
    const archiveGroupId = archiveGroupIdOverride || ensureArchiveGroupId(state);
    const controller = new AbortController();
    setPreloadState(state, file, {
      stage: "browser_uploading",
      status: null,
      preloadId: null,
      controller,
      pollTimer: null,
      error: null,
      removed: false,
    });
    renderSelectedFiles(ctx);

    const formData = new FormData();
    formData.append("file", file, file.name);
    formData.append("archive_group_id", archiveGroupId);
    try {
      const payload = await api(`/api/v1/tickets/${state.ticketId}/attachment-preloads`, {
        method: "POST",
        body: formData,
        signal: controller.signal,
      });
      const preload = getPreloadState(state, file);
      if (!preload || preload.removed) return;
      preload.preloadId = payload?.id || null;
      preload.status = payload?.status || "uploading";
      preload.stage = preload.status;
      preload.controller = null;
      preload.error = null;
      renderSelectedFiles(ctx);
      if (preload.preloadId && preload.stage === "uploading") {
        startPreloadPolling(ctx, file, preload.preloadId);
      }
    } catch (e) {
      const preload = getPreloadState(state, file);
      if (!preload) return;
      if (e?.name === "AbortError") {
        preload.stage = "cancelled";
        preload.controller = null;
        return;
      }
      preload.stage = "failed";
      preload.error = e.message || "Не удалось подготовить";
      preload.controller = null;
      clearPreloadPoll(state, file);
      renderSelectedFiles(ctx);
    }
  }

  async function removeSelectedFile(ctx, file) {
    const { state, api, el } = ctx;
    const preload = getPreloadState(state, file);
    if (preload) {
      preload.removed = true;
      abortPreloadRequest(state, file);
      clearPreloadPoll(state, file);
      if (preload.preloadId) {
        api(`/api/v1/tickets/${state.ticketId}/attachment-preloads/${preload.preloadId}`, {
          method: "DELETE",
        }).catch(() => {});
      }
    }
    state.selectedFiles = state.selectedFiles.filter((item) => item !== file);
    forgetPreloadState(state, file);
    if (state.selectedFiles.length === 0) {
      state.currentArchiveGroupId = null;
    }
    if (el.replyFilesInput) el.replyFilesInput.value = "";
    renderSelectedFiles(ctx);
  }

  function clearSelectedFiles(ctx) {
    const { state, el } = ctx;
    cleanupAllPreloadState(state);
    state.selectedFiles = [];
    state.currentArchiveGroupId = null;
    if (el.replyFilesInput) el.replyFilesInput.value = "";
    renderSelectedFiles(ctx);
  }

  function queueSelectedFiles(ctx, files) {
    const { state, el } = ctx;
    const queue = Array.from(files || []);
    if (!queue.length) return null;
    const archiveGroupId = canUseAttachmentPreload(ctx) ? ensureArchiveGroupId(state) : null;
    for (const file of queue) {
      state.selectedFiles.push(file);
      if (canUseAttachmentPreload(ctx)) {
        void startAttachmentPreload(ctx, file, archiveGroupId);
      } else {
        setPreloadState(state, file, {
          stage: "queued",
          status: null,
          preloadId: null,
          controller: null,
          pollTimer: null,
          error: null,
          removed: false,
        });
      }
    }
    if (el.replyFilesInput) el.replyFilesInput.value = "";
    renderSelectedFiles(ctx);
    return archiveGroupId;
  }

  function applyComposeModeUi(ctx) {
    const { state, el } = ctx;
    const isReply = state.composeMode === "reply";
    el.modeReplyBtn.classList.toggle("primary", isReply);
    el.modeCommentBtn.classList.toggle("primary", !isReply);
    el.messageInput.classList.toggle("comment-mode", !isReply);
    el.messageInput.placeholder = isReply ? "Текст ответа клиенту" : "Внутренний комментарий";
    if (el.addFilesBtn) {
      el.addFilesBtn.disabled = Boolean(state.card?.status === "closed");
      el.addFilesBtn.title = isReply ? "Прикрепить файлы к ответу клиенту" : "Прикрепить файлы к внутреннему комментарию";
    }
  }

  function sendHotkeyHint(state) {
    return state.sendByEnter ? "Новая строка: Shift+Enter" : "Отправка: Ctrl+Enter";
  }

  function refreshSendByEnterUi(ctx) {
    const { state, el } = ctx;
    if (el.sendByEnterCheckbox) {
      el.sendByEnterCheckbox.checked = Boolean(state.sendByEnter);
      el.sendByEnterCheckbox.disabled = Boolean(state.isSavingSendByEnterPreference);
    }
    if (el.sendHotkeyHint) {
      el.sendHotkeyHint.textContent = sendHotkeyHint(state);
    }
  }

  function resolveAttachmentTransport(ctx, file) {
    const { state } = ctx;
    const preload = getPreloadState(state, file);
    const canUsePreload = canUseAttachmentPreload(ctx);
    if (preload?.stage === "failed") {
      if (isVirtualPreloadFile(file) || state.composeMode === "comment") {
        return {
          mode: "error",
          reason: "virtual_preload_failed",
          message: "Подготовленный файл больше недоступен. Удалите его и выберите заново.",
        };
      }
      return { mode: "raw", reason: "preload_failed" };
    }
    if (preload?.stage === "cancelled") {
      if (isVirtualPreloadFile(file) || state.composeMode === "comment") {
        return {
          mode: "error",
          reason: "virtual_preload_cancelled",
          message: "Подготовленный файл больше недоступен. Удалите его и выберите заново.",
        };
      }
      return { mode: "raw", reason: "preload_cancelled" };
    }
    if (canUsePreload && preload?.stage === "browser_uploading" && !preload.preloadId) {
      if (isTaskReply(ctx) || state.composeMode === "comment") {
        return {
          mode: "error",
          reason: "task_preload_browser_uploading",
          message: "Файл ещё передаётся в ES DESK. Дождитесь готовности и отправьте сообщение снова.",
        };
      }
      abortPreloadRequest(state, file);
      return { mode: "raw", reason: "browser_uploading_without_preload_id" };
    }
    if (canUsePreload && (isTaskReply(ctx) || state.composeMode === "comment") && preload?.preloadId && preload.status === "uploading") {
      return {
        mode: "error",
        reason: "task_preload_uploading",
        message: "Файл ещё загружается в Telegram. Дождитесь готовности и отправьте сообщение снова.",
      };
    }
    if (canUsePreload && preload?.preloadId && (preload.status === "ready" || preload.status === "uploading")) {
      return {
        mode: "preloaded",
        preloadId: preload.preloadId,
        preloadStatus: preload.status,
      };
    }
    if (preload?.preloadId && preload?.status && !["ready", "uploading"].includes(preload.status)) {
      if (isVirtualPreloadFile(file) || state.composeMode === "comment") {
        return {
          mode: "error",
          reason: `virtual_preload_unusable_${preload.status}`,
          message: "Подготовленный файл больше недоступен. Удалите его и выберите заново.",
        };
      }
      return { mode: "raw", reason: `preload_unusable_${preload.status}` };
    }
    if ((isVirtualPreloadFile(file) || state.composeMode === "comment") && preload?.preloadId) {
      return {
        mode: "error",
        reason: "virtual_preload_missing_usable_status",
        message: "Подготовленный файл больше недоступен. Удалите его и выберите заново.",
      };
    }
    if (state.composeMode === "comment") {
      return {
        mode: "error",
        reason: "comment_preload_required",
        message: "Файл ещё не подготовлен. Дождитесь готовности и отправьте комментарий снова.",
      };
    }
    return { mode: "raw", reason: preload?.preloadId ? "preload_missing_usable_status" : "preload_id_missing" };
  }

  function buildReplyFormData(ctx, file, index, totalFiles, text, quoteMessageId, transport = null) {
    const selectedTransport = transport || resolveAttachmentTransport(ctx, file);
    const formData = new FormData();
    const isSingleAttachmentReply = totalFiles === 1;
    formData.append("text", index === 0 ? text : "");
    if (index === 0 && quoteMessageId) formData.append("quote_message_id", quoteMessageId);
    if (index === 0 && isSingleAttachmentReply) formData.append("single_attachment", "true");

    if (selectedTransport.mode === "preloaded") {
      formData.append("preloaded_attachment_id", selectedTransport.preloadId);
      return formData;
    }

    if (selectedTransport.mode === "error") {
      throw new Error(selectedTransport.message || "Подготовленный файл больше недоступен. Удалите его и выберите заново.");
    }

    formData.append("files", file, file.name);
    return formData;
  }

  function buildTaskReplyFormData(ctx, files, text, quoteMessageId) {
    const formData = new FormData();
    formData.append("text", text);
    if (quoteMessageId) formData.append("quote_message_id", quoteMessageId);
    const queue = Array.from(files || []);
    if (queue.length === 1) formData.append("single_attachment", "true");
    for (const file of queue) {
      const selectedTransport = resolveAttachmentTransport(ctx, file);
      if (selectedTransport.mode === "error") {
        throw new Error(selectedTransport.message || "Подготовленный файл больше недоступен. Удалите его и выберите заново.");
      }
      if (selectedTransport.mode === "preloaded") {
        formData.append("preloaded_attachment_id", selectedTransport.preloadId);
      } else {
        formData.append("files", file, file.name);
      }
    }
    return formData;
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

    const actionName = state.composeMode === "reply" ? "reply" : "comment";
    try {
      state.isSendingReply = true;
      state.pendingCloseAfterSend = false;
      ctx.setStatus("Отправка...");
      const quoteMessageId = state.quoteMessageId;
      if (hasFiles && (isTaskReply(ctx) || state.composeMode === "comment")) {
        const queue = [...state.selectedFiles];
        for (const file of queue) {
          const preload = getPreloadState(state, file);
          if (preload) preload.stage = "sending";
        }
        renderSelectedFiles(ctx);
        ctx.setStatus(state.composeMode === "comment" ? "Сохранение комментария..." : "Сохранение ответа задачи...");
        const formData = buildTaskReplyFormData(ctx, queue, text, quoteMessageId);
        await api(`/api/v1/tickets/${state.ticketId}/${actionName}`, {
          method: "POST",
          body: formData,
        });
      } else if (state.composeMode === "reply" && hasFiles) {
        const queue = [...state.selectedFiles];
        for (let index = 0; index < queue.length; index += 1) {
          const file = queue[index];
          const transport = resolveAttachmentTransport(ctx, file);
          const preload = getPreloadState(state, file);
          if (preload) {
            preload.stage = "sending";
          } else {
            setPreloadState(state, file, {
              stage: "sending",
              status: null,
              preloadId: null,
              controller: null,
              pollTimer: null,
              error: null,
              removed: false,
            });
          }
          renderSelectedFiles(ctx);
          ctx.setStatus(`Передача в ES DESK: ${index + 1}/${queue.length}`);
          const formData = buildReplyFormData(ctx, file, index, queue.length, text, quoteMessageId, transport);
          await api(`/api/v1/tickets/${state.ticketId}/reply`, {
            method: "POST",
            body: formData,
          });
          state.selectedFiles = state.selectedFiles.filter((item) => item !== file);
          forgetPreloadState(state, file);
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
        && workflowType(ctx) === "ticket"
        && ctx.isOnsitePhraseJs(text)
      ) {
        ctx.renderAvailabilityStatus("on_ticket");
      }
      if (isTaskReply(ctx)) {
        ctx.setStatus("Ответ сохранён. Клиент получит сообщение и файлы после завершения задачи.");
      } else {
        ctx.setStatus("Сообщение отправлено");
      }
    } catch (e) {
      const file = state.selectedFiles[0];
      if (file) {
        const preload = getPreloadState(state, file) || setPreloadState(state, file, {
          stage: "failed",
          status: null,
          preloadId: null,
          controller: null,
          pollTimer: null,
          error: null,
          removed: false,
        });
        preload.stage = "failed";
        preload.error = e.message || "ошибка отправки";
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
      el.replyFilesInput.click();
    });
    el.replyFilesInput.addEventListener("change", () => {
      queueSelectedFiles(ctx, el.replyFilesInput.files || []);
    });
    el.clearQuoteBtn.addEventListener("click", () => clearQuote(ctx));
    if (el.sendByEnterCheckbox) {
      el.sendByEnterCheckbox.addEventListener("change", async () => {
        await ctx.updateSendByEnterPreference?.(Boolean(el.sendByEnterCheckbox.checked));
      });
    }

    el.messageInput.addEventListener("keydown", async (e) => {
      if (e.key !== "Enter" || e.isComposing) return;
      if (state.sendByEnter) {
        if (e.shiftKey) return;
        if (e.ctrlKey || (!e.shiftKey && !e.altKey && !e.metaKey)) {
          e.preventDefault();
          await sendByMode(ctx);
        }
        return;
      }
      if (e.ctrlKey) {
        e.preventDefault();
        await sendByMode(ctx);
      }
    });
  }

  function destroyComposerState(ctx) {
    cleanupPreloadRuntimeState(ctx.state);
  }

  global.TicketComposer = {
    refreshComposerActions,
    quoteMessage,
    clearQuote,
    renderSelectedFiles,
    clearSelectedFiles,
    sendByMode,
    applyComposeModeUi,
    refreshSendByEnterUi,
    bindComposerHandlers,
    destroyComposerState,
    resolveAttachmentTransport,
    workflowType,
    isTaskReply,
    buildReplyFormData,
    buildTaskReplyFormData,
    createSelectedFileRow,
    shouldShowSelectedFileProgress,
    syncRestoredPreloads,
    ensureArchiveGroupId,
    queueSelectedFiles,
    removeSelectedFile,
  };
})(window);
