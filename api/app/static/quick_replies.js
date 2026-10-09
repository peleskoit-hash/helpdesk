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
    const state = { replies: [], session: null, editing: null, useInFlight: false };
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

    function pendingQuickReplyStorageKey() {
      return `hd_quick_reply_pending_use:${ticketId}`;
    }

    function ticketUrl() {
      return `/tickets/${encodeURIComponent(ticketId)}`;
    }

    function renderReplies() {
      el.list.innerHTML = "";
      for (const reply of state.replies) {
        const card = document.createElement("div");
        card.tabIndex = 0;
        card.setAttribute("role", "button");
        card.className = `quick-reply-card${isOwnAdminReply(reply) ? " ticket-type-request" : ""}`;
        card.dataset.replyId = reply.id;
        card.innerHTML = `
          <span class="quick-reply-card-title">${escapeHtml(reply.short_title)}</span>
          ${ticketMode ? `<span class="quick-reply-card-actions"><button type="button" class="action" data-use="${escapeHtml(reply.id)}">Использовать</button></span>` : ""}
        `;
        el.list.appendChild(card);
      }
    }

    function closeModal() {
      state.editing = null;
      el.modal.classList.add("hidden");
    }

    function openCreate() {
      state.editing = null;
      el.modalTitle.textContent = "Создать быстрый ответ";
      el.titleInput.value = "";
      el.bodyInput.value = "";
      el.titleInput.readOnly = false;
      el.bodyInput.readOnly = false;
      el.authorFooter.textContent = "";
      el.authorFooter.classList.add("hidden");
      el.save.disabled = false;
      el.modal.classList.remove("hidden");
      el.titleInput.focus();
    }

    function openEdit(reply) {
      state.editing = reply;
      el.modalTitle.textContent = "Быстрый ответ";
      el.titleInput.value = reply.short_title || "";
      el.bodyInput.value = reply.body || "";
      el.titleInput.readOnly = !reply.can_edit;
      el.bodyInput.readOnly = !reply.can_edit;
      el.authorFooter.textContent = `Создал: ${reply.author_name || reply.author_user_id || "-"}`;
      el.authorFooter.classList.remove("hidden");
      el.save.disabled = !reply.can_edit;
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
        el.save.disabled = Boolean(state.editing && !state.editing.can_edit);
      }
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
          }));
        } catch {
          // noop
        }
        window.location.href = ticketUrl();
      } finally {
        state.useInFlight = false;
      }
    }

    el.create.addEventListener("click", openCreate);
    el.closeModal.addEventListener("click", closeModal);
    el.cancel.addEventListener("click", closeModal);
    el.save.addEventListener("click", save);
    el.list.addEventListener("click", (event) => {
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

    await load();
  }

  window.HelpdeskQuickReplies = { initQuickRepliesPage };
  window.HelpdeskAppShell?.registerPage?.("quick-replies", initQuickRepliesPage);
})();
