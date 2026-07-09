(function () {
  const PAGE_SIZE = 50;
  const EMPTY = "\u2014";
  const SALES_FIRST_DEPARTMENT = "\u041e\u0442\u0434\u0435\u043b \u041f\u0440\u043e\u0434\u0430\u0436";
  const SALES_SECOND_DEPARTMENT = "\u041a\u043e\u043b\u043b \u0426\u0435\u043d\u0442\u0440";

  const state = {
    query: "",
    filter: "all",
    limit: PAGE_SIZE,
    offset: 0,
    total: 0,
    selectedClientId: "",
    clientCardViewConfig: null,
  };

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function formatCell(value) {
    const text = String(value ?? "").trim();
    return text || EMPTY;
  }

  function formatDate(value) {
    if (!value) return EMPTY;
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return EMPTY;
    return date.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
  }

  function buildUsersUrl(options = {}) {
    const params = new URLSearchParams();
    params.set("limit", String(options.limit || PAGE_SIZE));
    params.set("offset", String(options.offset || 0));
    const query = String(options.query || "").trim();
    if (query) params.set("q", query);
    if (options.filter === "blocked") params.set("blocked", "true");
    return `/api/v1/management/users?${params.toString()}`;
  }

  function paginationSummary(offset, limit, total, itemCount) {
    const safeTotal = Math.max(0, Number(total || 0));
    if (safeTotal === 0 || itemCount === 0) {
      return "\u041f\u043e\u043a\u0430\u0437\u0430\u043d\u043e 0\u20130 \u0438\u0437 0";
    }
    const start = Number(offset || 0) + 1;
    const end = Math.min(Number(offset || 0) + Number(itemCount || limit || 0), safeTotal);
    return `\u041f\u043e\u043a\u0430\u0437\u0430\u043d\u043e ${start}\u2013${end} \u0438\u0437 ${safeTotal}`;
  }

  function tableRowHtml(item) {
    const username = item.telegram_username ? `@${item.telegram_username.replace(/^@/, "")}` : "";
    const telegramBits = [username, item.telegram_user_id ? `TG ID: ${item.telegram_user_id}` : ""].filter(Boolean);
    const department = item.destination || item.group_name || item.sales_group || "";
    return `
      <tr class="management-user-row" data-client-id="${escapeHtml(item.client_id)}">
        <td>
          <div class="management-user-primary">${escapeHtml(formatCell(item.telegram_display_name || username || item.telegram_user_id))}</div>
          <div class="management-user-muted">${escapeHtml(telegramBits.join(" \u00b7 ") || EMPTY)}</div>
        </td>
        <td>${escapeHtml(formatCell(item.display_name))}</td>
        <td>${escapeHtml(formatCell(item.internal_number))}</td>
        <td>${escapeHtml(formatCell(department))}</td>
        <td>${item.is_verified ? "\u2713" : EMPTY}</td>
        <td>${escapeHtml(formatDate(item.last_activity_at))}</td>
      </tr>
    `;
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: {
        accept: "application/json",
        ...(options.body ? { "content-type": "application/json" } : {}),
        ...(options.headers || {}),
      },
      credentials: "include",
    });
    const raw = await response.text();
    let payload = null;
    if (raw) {
      try {
        payload = JSON.parse(raw);
      } catch {
        payload = null;
      }
    }
    if (!response.ok) {
      const message = payload?.detail?.error?.message || raw || "\u041e\u0448\u0438\u0431\u043a\u0430 \u0437\u0430\u043f\u0440\u043e\u0441\u0430";
      throw new Error(message);
    }
    return payload;
  }

  function setStatus(root, message) {
    const node = root.querySelector("#managementUsersStatus");
    if (node) node.textContent = message;
  }

  function setControlValue(control, value) {
    if (!control) return;
    control.value = value ?? "";
  }

  function isBlank(value) {
    return value === null || value === undefined || String(value).trim() === "";
  }

  function els(root) {
    return {
      tableBody: root.querySelector("#managementUsersTableBody"),
      empty: root.querySelector("#managementUsersEmpty"),
      search: root.querySelector("#managementUsersSearch"),
      searchBtn: root.querySelector("#managementUsersSearchBtn"),
      filters: [...root.querySelectorAll("[data-management-user-filter]")],
      pageSummary: root.querySelector("#managementUsersPageSummary"),
      prevBtn: root.querySelector("#managementUsersPrevBtn"),
      nextBtn: root.querySelector("#managementUsersNextBtn"),
      clientModal: root.querySelector("#clientModal"),
      closeClientModalBtn: root.querySelector("#closeClientModalBtn"),
      saveClientBtn: root.querySelector("#saveClientBtn"),
      fContactName: root.querySelector("#fContactName"),
      fLastName: root.querySelector("#fLastName"),
      fDestination: root.querySelector("#fDestination"),
      fSandOwnerWrap: root.querySelector("#fSandOwnerWrap"),
      fSandOwner: root.querySelector("#fSandOwner"),
      fSalesGroupWrap: root.querySelector("#fSalesGroupWrap"),
      fSalesGroup: root.querySelector("#fSalesGroup"),
      fLocationType: root.querySelector("#fLocationType"),
      fAnydeskWrap: root.querySelector("#fAnydeskWrap"),
      fAnydesk: root.querySelector("#fAnydesk"),
      fInternalNumber: root.querySelector("#fInternalNumber"),
      fBitrixLogin: root.querySelector("#fBitrixLogin"),
      fBitrixPassword: root.querySelector("#fBitrixPassword"),
      fBitrixId: root.querySelector("#fBitrixId"),
      fIsVerified: root.querySelector("#fIsVerified"),
      customFieldsSection: root.querySelector("#customFieldsSection"),
      customFieldsContainer: root.querySelector("#customFieldsContainer"),
    };
  }

  function clientCardCtx(root) {
    return {
      state,
      el: els(root),
      api,
      setControlValue,
      isBlank,
      SALES_FIRST_DEPARTMENT,
      SALES_SECOND_DEPARTMENT,
    };
  }

  function renderUsers(root, payload) {
    const el = els(root);
    const items = Array.isArray(payload?.items) ? payload.items : [];
    state.total = Number(payload?.total || 0);
    state.limit = Number(payload?.limit || state.limit || PAGE_SIZE);
    state.offset = Number(payload?.offset || state.offset || 0);
    el.tableBody.innerHTML = items.map(tableRowHtml).join("");
    el.empty.classList.toggle("hidden", items.length > 0);
    if (state.filter === "blocked" && payload?.blocked_supported === false) {
      el.empty.textContent = "\u0411\u043b\u043e\u043a\u0438\u0440\u043e\u0432\u043a\u0430 \u043f\u043e\u043a\u0430 \u043d\u0435 \u043f\u043e\u0434\u043a\u043b\u044e\u0447\u0435\u043d\u0430";
    } else {
      el.empty.textContent = "\u0421\u043f\u0438\u0441\u043e\u043a \u043f\u0443\u0441\u0442";
    }
    if (el.pageSummary) {
      el.pageSummary.textContent = paginationSummary(state.offset, state.limit, state.total, items.length);
    }
    if (el.prevBtn) {
      el.prevBtn.disabled = state.offset <= 0;
    }
    if (el.nextBtn) {
      el.nextBtn.disabled = state.offset + state.limit >= state.total;
    }
  }

  async function loadUsers(root) {
    setStatus(root, "\u0417\u0430\u0433\u0440\u0443\u0437\u043a\u0430...");
    const payload = await api(buildUsersUrl({ query: state.query, filter: state.filter, limit: state.limit, offset: state.offset }));
    renderUsers(root, payload);
    setStatus(root, `\u041d\u0430\u0439\u0434\u0435\u043d\u043e: ${payload?.total ?? 0}`);
  }

  function runSearch(root) {
    const el = els(root);
    state.query = el.search?.value || "";
    state.offset = 0;
    return loadUsers(root);
  }

  function closeClientModal(root) {
    const modal = els(root).clientModal;
    if (modal) modal.classList.add("hidden");
    state.selectedClientId = "";
  }

  async function openClientProfileModal(root, clientId) {
    if (!clientId || !window.TicketClientCard) return;
    state.selectedClientId = clientId;
    setStatus(root, "\u0417\u0430\u0433\u0440\u0443\u0437\u043a\u0430 \u043a\u0430\u0440\u0442\u043e\u0447\u043a\u0438...");
    const profile = await api(`/api/v1/management/users/${encodeURIComponent(clientId)}/profile`);
    window.TicketClientCard.fillModal(clientCardCtx(root), profile || {});
    els(root).clientModal.classList.remove("hidden");
    setStatus(root, "\u0413\u043e\u0442\u043e\u0432\u043e");
  }

  async function saveClientProfile(root) {
    if (!state.selectedClientId || !window.TicketClientCard) return;
    setStatus(root, "\u0421\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u0438\u0435...");
    const payload = window.TicketClientCard.getModalPayload(clientCardCtx(root));
    await api(`/api/v1/management/users/${encodeURIComponent(state.selectedClientId)}/profile`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
    closeClientModal(root);
    await loadUsers(root);
    setStatus(root, "\u0421\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u043e");
  }

  function bind(root) {
    const el = els(root);
    el.searchBtn?.addEventListener("click", () => {
      runSearch(root).catch((error) => setStatus(root, error.message));
    });
    el.search?.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      runSearch(root).catch((error) => setStatus(root, error.message));
    });
    el.filters.forEach((button) => {
      button.addEventListener("click", () => {
        state.filter = button.dataset.managementUserFilter || "all";
        state.offset = 0;
        el.filters.forEach((item) => item.classList.toggle("active", item === button));
        loadUsers(root).catch((error) => setStatus(root, error.message));
      });
    });
    el.prevBtn?.addEventListener("click", () => {
      if (state.offset <= 0) return;
      state.offset = Math.max(0, state.offset - state.limit);
      loadUsers(root).catch((error) => setStatus(root, error.message));
    });
    el.nextBtn?.addEventListener("click", () => {
      if (state.offset + state.limit >= state.total) return;
      state.offset += state.limit;
      loadUsers(root).catch((error) => setStatus(root, error.message));
    });
    el.tableBody?.addEventListener("click", (event) => {
      const row = event.target.closest("[data-client-id]");
      if (!row) return;
      openClientProfileModal(root, row.dataset.clientId).catch((error) => setStatus(root, error.message));
    });
    el.closeClientModalBtn?.addEventListener("click", () => closeClientModal(root));
    el.saveClientBtn?.addEventListener("click", () => saveClientProfile(root).catch((error) => setStatus(root, error.message)));
    el.fDestination?.addEventListener("change", () => window.TicketClientCard?.syncDynamicModalFields(clientCardCtx(root)));
    el.fLocationType?.addEventListener("change", () => window.TicketClientCard?.syncDynamicModalFields(clientCardCtx(root)));
  }

  async function initManagementUsersPage({ root } = {}) {
    if (!root) return;
    bind(root);
    try {
      const sidebar = await window.HelpdeskSidebar?.init?.();
      if (sidebar && sidebar.role !== "admin") {
        setStatus(root, "\u0414\u043e\u0441\u0442\u0443\u043f \u0442\u043e\u043b\u044c\u043a\u043e \u0434\u043b\u044f \u0430\u0434\u043c\u0438\u043d\u0430");
        return;
      }
      state.clientCardViewConfig = await api("/api/v1/management/users/client-card-view-config").catch(() => null);
      await loadUsers(root);
    } catch (error) {
      setStatus(root, error.message);
    }
  }

  window.ManagementUsersPage = {
    buildUsersUrl,
    formatCell,
    formatDate,
    paginationSummary,
    tableRowHtml,
  };

  window.HelpdeskAppShell?.registerPage?.("management-users", initManagementUsersPage);
})();
