(function () {
  async function api(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: { accept: "application/json", ...(options.headers || {}) },
    });
    if (response.status === 401) {
      window.location.href = "/login";
      throw new Error("Требуется авторизация");
    }
    const raw = await response.text();
    const payload = raw ? JSON.parse(raw) : null;
    if (!response.ok) {
      throw new Error(payload?.detail?.error?.message || raw || "Ошибка запроса");
    }
    return payload;
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  async function initQuickRepliesPage() {
    await window.HelpdeskSidebar?.init?.();
    const params = new URLSearchParams(window.location.search);
    const ticketId = (params.get("ticket_id") || "").trim();
    const ticketMode = Boolean(ticketId);
    const state = {
      replies: [],
      session: null,
      editing: null,
      draftFiles: [],
      initialDraft: null,
      uploadInFlight: false,
      modalToken: 0,
      useInFlight: false,
      deletingIds: new Set(),
    };
    const el = {
      status: document.getElementById("statusText"),
      create: document.getElementById("createQuickReplyBtn"),
      list: document.getElementById("quickRepliesList"),
      modal: document.getElementById("quickReplyModal"),
      modalTitle: document.getElementById("quickReplyModalTitle"),
      closeModal: document.getElementById("closeQuickReplyModalBtn"),
      cancel: document.getElementById("cancelQuickReplyBtn"),
      save: document.getElementById("saveQuickReplyBtn"),
      titleInput: document.getElementById("quickReplyTitleInput"),
      bodyInput: document.getElementById("quickReplyBodyInput"),
      authorFooter: document.getElementById("quickReplyAuthorFooter"),
      fileInput: document.getElementById("quickReplyFileInput"),
      attachFile: document.getElementById("attachQuickReplyFileBtn"),
      fileUploadStatus: document.getElementById("quickReplyFileUploadStatus"),
      filesBlock: document.getElementById("quickReplyFilesBlock"),
      filesTitle: document.getElementById("quickReplyFilesTitle"),
      filesList: document.getElementById("quickReplyFilesList"),
    };

    function setStatus(text) {
      if (el.status) el.status.textContent = text;
    }

    function canCreate() {
      const role = state.session?.effective_user?.role;
      return role === "admin" || role === "agent";
    }

    function isOwnAdminReply(reply) {
      const user = state.session?.effective_user || {};
      return user.role === "admin" && reply.author_user_id === user.id;
    }

    function canDelete() {
      return state.session?.effective_user?.role === "admin";
    }

    function canEditCurrentReply() {
      return state.editing ? Boolean(state.editing.can_edit) : canCreate();
    }

    function pendingQuickReplyStorageKey() {
      return `hd_quick_reply_pending_use:${ticketId}`;
    }

    function ticketUrl() {
      return `/tickets/${encodeURIComponent(ticketId)}`;
    }

    function renderReplies() {
      el.list.innerHTML = "";
      for (const reply of state.replies) {
        const row = document.createElement("div");
        row.className = "quick-reply-row";

        const card = document.createElement("div");
        card.tabIndex = 0;
        card.setAttribute("role", "button");
        card.className = `quick-reply-card${isOwnAdminReply(reply) ? " ticket-type-request" : ""}`;
        card.dataset.replyId = reply.id;
        card.innerHTML = `
          <span class="quick-reply-card-title">${escapeHtml(reply.short_title)}</span>
          ${ticketMode ? `<span class="quick-reply-card-actions"><button type="button" class="action" data-use="${escapeHtml(reply.id)}">Использовать</button></span>` : ""}
        `;
        row.appendChild(card);

        if (canDelete()) {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "action danger quick-reply-delete";
          button.dataset.delete = reply.id;
          button.textContent = "Удалить";
          button.disabled = state.deletingIds.has(String(reply.id));
          row.appendChild(button);
        }

        el.list.appendChild(row);
      }
    }

    function fileName(file) {
      return file?.display_filename || file?.filename || file?.id || "Файл";
    }

    function renderDraftFiles() {
      const canEdit = canEditCurrentReply();
      const count = state.draftFiles.length;
      el.filesBlock.classList.toggle("hidden", count === 0);
      el.filesTitle.textContent = `Прикреплённые файлы (${count}/5):`;
      el.filesList.innerHTML = "";
      for (const file of state.draftFiles) {
        const row = document.createElement("div");
        row.className = "quick-reply-file-row";

        const name = document.createElement("span");
        name.className = "quick-reply-file-name";
        name.textContent = fileName(file);
        row.appendChild(name);

        if (canEdit) {
          const remove = document.createElement("button");
          remove.type = "button";
          remove.className = "action danger quick-reply-file-remove";
          remove.dataset.fileId = file.id;
          remove.title = "Отвязать файл";
          remove.setAttribute("aria-label", `Отвязать файл ${fileName(file)}`);
          remove.textContent = "🗑";
          row.appendChild(remove);
        }

        el.filesList.appendChild(row);
      }
      el.attachFile.disabled = !canEdit || state.uploadInFlight || count >= 5;
      el.fileUploadStatus.classList.toggle("hidden", !state.uploadInFlight);
      el.fileUploadStatus.textContent = state.uploadInFlight ? "Загружаю файл..." : "";
      el.save.disabled = !canEdit || state.uploadInFlight;
    }

    function setDraftFiles(files) {
      state.draftFiles = Array.isArray(files) ? files.filter((file) => file?.id) : [];
      renderDraftFiles();
    }

    function draftFileIds() {
      return state.draftFiles.map((file) => String(file.id));
    }

    function rememberInitialDraft() {
      state.initialDraft = {
        shortTitle: el.titleInput.value,
        body: el.bodyInput.value,
        fileIds: draftFileIds(),
      };
    }

    function draftChanged() {
      if (state.uploadInFlight) return true;
      if (!state.initialDraft) return false;
      const fileIds = draftFileIds();
      return el.titleInput.value !== state.initialDraft.shortTitle
        || el.bodyInput.value !== state.initialDraft.body
        || fileIds.length !== state.initialDraft.fileIds.length
        || fileIds.some((id, index) => id !== state.initialDraft.fileIds[index]);
    }

    function closeModal() {
      state.modalToken += 1;
      state.editing = null;
      state.draftFiles = [];
      state.initialDraft = null;
      state.uploadInFlight = false;
      el.fileInput.value = "";
      el.modal.classList.add("hidden");
    }

    function requestCloseModal() {
      if (el.modal.classList.contains("hidden")) return;
      if (draftChanged() && !window.confirm("Есть несохранённые изменения. Закрыть без сохранения?")) return;
      closeModal();
    }

    function openCreate() {
      state.modalToken += 1;
      state.editing = null;
      el.modalTitle.textContent = "Создать быстрый ответ";
      el.titleInput.value = "";
      el.bodyInput.value = "";
      el.titleInput.readOnly = false;
      el.bodyInput.readOnly = false;
      el.authorFooter.textContent = "";
      el.authorFooter.classList.add("hidden");
      setDraftFiles([]);
      rememberInitialDraft();
      el.modal.classList.remove("hidden");
      el.titleInput.focus();
    }

    function openEdit(reply) {
      state.modalToken += 1;
      state.editing = reply;
      el.modalTitle.textContent = "Быстрый ответ";
      el.titleInput.value = reply.short_title || "";
      el.bodyInput.value = reply.body || "";
      el.titleInput.readOnly = !reply.can_edit;
      el.bodyInput.readOnly = !reply.can_edit;
      el.authorFooter.textContent = `Создал: ${reply.author_name || reply.author_user_id || "-"}`;
      el.authorFooter.classList.remove("hidden");
      setDraftFiles(reply.saved_files || []);
      rememberInitialDraft();
      el.modal.classList.remove("hidden");
      el.titleInput.focus();
    }

    async function load() {
      setStatus("Загружаю...");
      state.session = await api("/api/v1/auth/session");
      state.replies = await api("/api/v1/quick-replies");
      el.create.classList.toggle("hidden", !canCreate());
      el.create.disabled = !canCreate();
      renderReplies();
      setStatus(ticketMode ? `Контекст заявки: ${ticketId}` : "Готово");
    }

    async function save() {
      if (state.editing && !state.editing.can_edit) return;
      const body = {
        short_title: el.titleInput.value,
        body: el.bodyInput.value,
        saved_file_ids: state.draftFiles.map((file) => file.id),
      };
      const path = state.editing ? `/api/v1/quick-replies/${encodeURIComponent(state.editing.id)}` : "/api/v1/quick-replies";
      const method = state.editing ? "PATCH" : "POST";
      try {
        el.save.disabled = true;
        await api(path, {
          method,
          headers: { "content-type": "application/json" },
          body: JSON.stringify(body),
        });
        closeModal();
        await load();
      } catch (e) {
        setStatus(e.message);
        renderDraftFiles();
      }
    }

    async function uploadDraftFile(file) {
      if (!file || !canEditCurrentReply() || state.uploadInFlight || state.draftFiles.length >= 5) return;
      const token = state.modalToken;
      const formData = new FormData();
      formData.append("file", file);
      state.uploadInFlight = true;
      renderDraftFiles();
      try {
        const savedFile = await api("/api/v1/quick-replies/files", {
          method: "POST",
          body: formData,
        });
        if (token !== state.modalToken) return;
        if (!savedFile?.id) throw new Error("Сервер не вернул файл");
        state.draftFiles = [...state.draftFiles.filter((item) => item.id !== savedFile.id), savedFile];
        setStatus("Файл прикреплён");
      } catch (e) {
        if (token === state.modalToken) setStatus(e.message);
      } finally {
        if (token === state.modalToken) {
          state.uploadInFlight = false;
          el.fileInput.value = "";
          renderDraftFiles();
        }
      }
    }

    function detachDraftFile(fileId) {
      if (!canEditCurrentReply()) return;
      const file = state.draftFiles.find((item) => String(item.id) === String(fileId));
      if (!file) return;
      if (!window.confirm(`Отвязать файл «${fileName(file)}» от быстрого ответа?`)) return;
      state.draftFiles = state.draftFiles.filter((item) => String(item.id) !== String(fileId));
      renderDraftFiles();
    }

    async function useReply(reply) {
      if (!ticketMode || !reply?.id) return;
      if (state.useInFlight) return;
      state.useInFlight = true;
      try {
        const payload = await api(`/api/v1/quick-replies/${encodeURIComponent(reply.id)}/use`, { method: "POST" });
        try {
          sessionStorage.setItem(pendingQuickReplyStorageKey(), JSON.stringify({
            id: payload?.id || reply.id,
            body: payload?.body || reply.body || "",
            saved_files: payload?.saved_files || reply.saved_files || [],
          }));
        } catch {
          // noop
        }
        window.location.href = ticketUrl();
      } finally {
        state.useInFlight = false;
      }
    }

    async function deleteReply(reply) {
      if (!canDelete() || !reply?.id) return;
      const replyId = String(reply.id);
      if (state.deletingIds.has(replyId)) return;
      if (!window.confirm(`Удалить быстрый ответ «${reply.short_title || reply.id}»?`)) return;
      state.deletingIds.add(replyId);
      renderReplies();
      try {
        await api(`/api/v1/quick-replies/${encodeURIComponent(reply.id)}`, { method: "DELETE" });
        await load();
      } catch (e) {
        state.deletingIds.delete(replyId);
        renderReplies();
        setStatus(e.message);
      }
    }

    el.create.addEventListener("click", openCreate);
    el.closeModal.addEventListener("click", requestCloseModal);
    el.cancel.addEventListener("click", requestCloseModal);
    el.save.addEventListener("click", save);
    el.attachFile.addEventListener("click", () => {
      if (!el.attachFile.disabled) el.fileInput.click();
    });
    el.fileInput.addEventListener("change", () => {
      const file = el.fileInput.files?.[0];
      if (file) void uploadDraftFile(file);
      else el.fileInput.value = "";
    });
    el.filesList.addEventListener("click", (event) => {
      const fileId = event.target?.closest?.("[data-file-id]")?.dataset?.fileId;
      if (fileId) detachDraftFile(fileId);
    });
    el.list.addEventListener("click", (event) => {
      const deleteId = event.target?.dataset?.delete;
      if (deleteId) {
        event.stopPropagation();
        const reply = state.replies.find((item) => String(item.id) === String(deleteId));
        if (reply) void deleteReply(reply).catch((e) => setStatus(e.message));
        return;
      }
      const useId = event.target?.dataset?.use;
      if (useId) {
        event.stopPropagation();
        const reply = state.replies.find((item) => String(item.id) === String(useId));
        if (reply) void useReply(reply).catch((e) => setStatus(e.message));
        return;
      }
      const card = event.target?.closest?.("[data-reply-id]");
      const replyId = card?.dataset?.replyId;
      if (!replyId) return;
      const reply = state.replies.find((item) => String(item.id) === String(replyId));
      if (reply) openEdit(reply);
    });
    el.list.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      if (event.target?.closest?.("[data-use]")) return;
      const card = event.target?.closest?.("[data-reply-id]");
      const replyId = card?.dataset?.replyId;
      if (!replyId) return;
      event.preventDefault();
      const reply = state.replies.find((item) => String(item.id) === String(replyId));
      if (reply) openEdit(reply);
    });
    el.modal.addEventListener("click", (event) => {
      if (event.target === el.modal) requestCloseModal();
    });
    const keydownHandler = (event) => {
      if (event.key === "Escape") requestCloseModal();
    };
    document.addEventListener("keydown", keydownHandler);

    await load();
    return {
      destroy() {
        document.removeEventListener("keydown", keydownHandler);
      },
    };
  }

  window.HelpdeskQuickReplies = { initQuickRepliesPage };
  window.HelpdeskAppShell?.registerPage?.("quick-replies", initQuickRepliesPage);
})();
