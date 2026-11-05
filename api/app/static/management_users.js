(function () {
  const PAGE_SIZE = 50;
  const EMPTY = "\u2014";
  const SALES_FIRST_DEPARTMENT = "\u041e\u0442\u0434\u0435\u043b \u041f\u0440\u043e\u0434\u0430\u0436";
  const SALES_SECOND_DEPARTMENT = "\u041a\u043e\u043b\u043b \u0426\u0435\u043d\u0442\u0440";
  const BLOCKED_VIEW = "blocked";
  const URL_VIEW_PARAM = "view";

  const state = {
    query: "",
    filter: "all",
    limit: PAGE_SIZE,
    offset: 0,
    total: 0,
    selectedClientId: "",
    selectedClientBlocked: false,
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

  function filterFromPageUrl(urlLike) {
    const url = urlLike instanceof URL ? urlLike : new URL(String(urlLike || window.location.href), window.location.origin);
    return url.searchParams.get(URL_VIEW_PARAM) === BLOCKED_VIEW ? BLOCKED_VIEW : "all";
  }

  function buildPageUrlForFilter(filter, urlLike) {
    const url = urlLike instanceof URL ? new URL(urlLike.toString()) : new URL(String(urlLike || window.location.href), window.location.origin);
    if (filter === BLOCKED_VIEW) url.searchParams.set(URL_VIEW_PARAM, BLOCKED_VIEW);
    else url.searchParams.delete(URL_VIEW_PARAM);
    return `${url.pathname}${url.search}${url.hash}`;
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
      <tr class="management-user-row ${item.is_blocked ? "management-user-row-blocked" : ""}" data-client-id="${escapeHtml(item.client_id)}" data-is-blocked="${item.is_blocked ? "1" : "0"}">
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
      const error = new Error(message);
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  function setStatus(root, message) {
    const node = root.querySelector("#managementUsersStatus");
    if (node) {
      node.textContent = message;
      node.hidden = !message || message === "Готово";
    }
  }

  function showError(root, message) {
    const el = els(root);
    if (!el.errorModal || !el.errorText) return;
    el.errorText.value = message || "\u041d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u0430\u044f \u043e\u0448\u0438\u0431\u043a\u0430";
    el.errorModal.classList.remove("hidden");
  }

  function closeError(root) {
    const el = els(root);
    if (el.errorModal) el.errorModal.classList.add("hidden");
  }

  function closeOnBackdropClick(modal, closeHandler) {
    let down = false;
    modal.addEventListener("pointerdown", (event) => {
      down = event.target === modal;
    });
    modal.addEventListener("click", (event) => {
      if (down && event.target === modal) closeHandler();
      down = false;
    });
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
      resetClientDataBtn: root.querySelector("#resetClientDataBtn"),
      saveClientBtn: root.querySelector("#saveClientBtn"),
      errorModal: root.querySelector("#errorModal"),
      errorText: root.querySelector("#errorText"),
      closeErrorModalBtn: root.querySelector("#closeErrorModalBtn"),
      copyErrorBtn: root.querySelector("#copyErrorBtn"),
      okErrorBtn: root.querySelector("#okErrorBtn"),
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
      clientBlockedState: root.querySelector("#clientBlockedState"),
      toggleClientBlockBtn: root.querySelector("#toggleClientBlockBtn"),
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
      showError: (message) => showError(root, message),
      clientId: state.selectedClientId,
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
    el.empty.textContent = "\u0421\u043f\u0438\u0441\u043e\u043a \u043f\u0443\u0441\u0442";
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

  function syncFilterButtons(root) {
    const el = els(root);
    el.filters.forEach((item) => {
      item.classList.toggle("active", (item.dataset.managementUserFilter || "all") === state.filter);
    });
  }

  function applyFilterState(root, filter, { resetOffset = true } = {}) {
    state.filter = filter === BLOCKED_VIEW ? BLOCKED_VIEW : "all";
    if (resetOffset) state.offset = 0;
    syncFilterButtons(root);
  }

  function updatePageUrlForFilter(filter, { replace = false } = {}) {
    const nextUrl = buildPageUrlForFilter(filter);
    if (replace) {
      window.history.replaceState({ shell: true, url: nextUrl }, "", nextUrl);
    } else {
      window.history.pushState({ shell: true, url: nextUrl }, "", nextUrl);
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

  async function closeClientModal(root, { force = false } = {}) {
    if (!force && !await window.TicketClientCard?.confirmInlineExit?.(clientCardCtx(root))) return false;
    await window.TicketClientCard?.closeClientModal?.(clientCardCtx(root), { force: true });
    const modal = els(root).clientModal;
    if (modal) modal.classList.add("hidden");
    if (state.clientCardInline) state.clientCardInline.open = false;
    state.selectedClientId = "";
    state.selectedClientBlocked = false;
  }

  function syncClientBlockControls(root) {
    const el = els(root);
    if (!el.clientBlockedState || !el.toggleClientBlockBtn) return;
    el.clientBlockedState.classList.toggle("hidden", !state.selectedClientId);
    el.clientBlockedState.classList.toggle("is-blocked", state.selectedClientBlocked);
    el.clientBlockedState.textContent = state.selectedClientBlocked
      ? "\u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c \u0437\u0430\u0431\u043b\u043e\u043a\u0438\u0440\u043e\u0432\u0430\u043d"
      : "\u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c \u043d\u0435 \u0437\u0430\u0431\u043b\u043e\u043a\u0438\u0440\u043e\u0432\u0430\u043d";
    el.toggleClientBlockBtn.textContent = state.selectedClientBlocked
      ? "\u0420\u0430\u0437\u0431\u043b\u043e\u043a\u0438\u0440\u043e\u0432\u0430\u0442\u044c"
      : "\u0417\u0430\u0431\u043b\u043e\u043a\u0438\u0440\u043e\u0432\u0430\u0442\u044c";
    el.toggleClientBlockBtn.classList.toggle("danger", !state.selectedClientBlocked);
  }

  function syncClientModalToggles(root) {
    els(root).clientModal.querySelectorAll('input[type="checkbox"]').forEach(input => input.classList.add("desk-toggle"));
  }

  async function openClientProfileModal(root, clientId, item = null) {
    if (!clientId || !window.TicketClientCard) return;
    state.selectedClientId = clientId;
    state.selectedClientBlocked = Boolean(item?.is_blocked || item?.blocked);
    syncClientBlockControls(root);
    setStatus(root, "\u0417\u0430\u0433\u0440\u0443\u0437\u043a\u0430 \u043a\u0430\u0440\u0442\u043e\u0447\u043a\u0438...");
    const [profile, config] = await Promise.all([
      api(`/api/v1/management/users/${encodeURIComponent(clientId)}/profile`, { cache: "no-store" }),
      api("/api/v1/management/users/client-card-view-config", { cache: "no-store" }),
    ]);
    if (!config || !Array.isArray(config.blocks)) throw new Error("Не удалось загрузить настройки карточки клиента");
    state.clientCardViewConfig = config;
    window.TicketClientCard.fillModal(clientCardCtx(root), profile || {});
    syncClientModalToggles(root);
    window.TicketClientCard.trackModalChanges(clientCardCtx(root));
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
    closeClientModal(root, { force: true });
    await loadUsers(root);
    setStatus(root, "\u0421\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u043e");
    window.HelpdeskAppShell?.showSaved?.(root.querySelector(".panel.management-users-table"));
  }

  async function toggleClientBlock(root) {
    if (!state.selectedClientId) return;
    const nextBlocked = !state.selectedClientBlocked;
    if (nextBlocked) {
      const ok = await window.HelpdeskDialogs.confirm("\u0417\u0430\u0431\u043b\u043e\u043a\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044f? \u041e\u043d \u0431\u043e\u043b\u044c\u0448\u0435 \u043d\u0435 \u0441\u043c\u043e\u0436\u0435\u0442 \u0432\u0437\u0430\u0438\u043c\u043e\u0434\u0435\u0439\u0441\u0442\u0432\u043e\u0432\u0430\u0442\u044c \u0441 Telegram-\u0431\u043e\u0442\u043e\u043c \u0434\u043e \u0440\u0430\u0437\u0431\u043b\u043e\u043a\u0438\u0440\u043e\u0432\u043a\u0438.");
      if (!ok) return;
    }
    const action = nextBlocked ? "block" : "unblock";
    setStatus(root, nextBlocked ? "\u0411\u043b\u043e\u043a\u0438\u0440\u043e\u0432\u043a\u0430..." : "\u0420\u0430\u0437\u0431\u043b\u043e\u043a\u0438\u0440\u043e\u0432\u043a\u0430...");
    const payload = await api(`/api/v1/management/users/${encodeURIComponent(state.selectedClientId)}/${action}`, {
      method: "POST",
    });
    state.selectedClientBlocked = Boolean(payload?.is_blocked);
    syncClientBlockControls(root);
    await loadUsers(root);
    setStatus(root, state.selectedClientBlocked ? "\u0417\u0430\u0431\u043b\u043e\u043a\u0438\u0440\u043e\u0432\u0430\u043d\u043e" : "\u0420\u0430\u0437\u0431\u043b\u043e\u043a\u0438\u0440\u043e\u0432\u0430\u043d\u043e");
  }

  async function resetClientData(root) {
    if (!state.selectedClientId || !window.TicketClientCard) return;
    const ok = await window.HelpdeskDialogs.confirm(
      "\u0421\u0442\u0435\u0440\u0435\u0442\u044c \u0432\u0441\u0435 \u0434\u0430\u043d\u043d\u044b\u0435 \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044f?\n\n" +
      "\u0414\u0430\u043d\u043d\u044b\u0435 \u043f\u0440\u043e\u0444\u0438\u043b\u044f \u0438 \u0432\u0435\u0440\u0438\u0444\u0438\u043a\u0430\u0446\u0438\u044f \u0431\u0443\u0434\u0443\u0442 \u0443\u0434\u0430\u043b\u0435\u043d\u044b. \u041f\u0440\u0438 \u0441\u043b\u0435\u0434\u0443\u044e\u0449\u0435\u043c \u043e\u0431\u0440\u0430\u0449\u0435\u043d\u0438\u0438 \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c \u043f\u0440\u043e\u0439\u0434\u0451\u0442 \u0441\u0446\u0435\u043d\u0430\u0440\u0438\u0439 \u043f\u0435\u0440\u0432\u043e\u0433\u043e \u043f\u043e\u0434\u043a\u043b\u044e\u0447\u0435\u043d\u0438\u044f \u0437\u0430\u043d\u043e\u0432\u043e.\n\n" +
      "\u0418\u0441\u0442\u043e\u0440\u0438\u044f \u0437\u0430\u044f\u0432\u043e\u043a \u0441\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u0441\u044f.\n" +
      "\u0421\u0442\u0430\u0442\u0443\u0441 \u0431\u043b\u043e\u043a\u0438\u0440\u043e\u0432\u043a\u0438 \u043d\u0435 \u0438\u0437\u043c\u0435\u043d\u0438\u0442\u0441\u044f."
    );
    if (!ok) return;
    setStatus(root, "\u041e\u0447\u0438\u0441\u0442\u043a\u0430 \u0434\u0430\u043d\u043d\u044b\u0445...");
    const payload = await api(`/api/v1/management/users/${encodeURIComponent(state.selectedClientId)}/reset-data`, {
      method: "POST",
    });
    state.selectedClientBlocked = Boolean(payload?.is_blocked);
    syncClientBlockControls(root);
    window.TicketClientCard.fillModal(clientCardCtx(root), payload?.profile || {});
    syncClientModalToggles(root);
    window.TicketClientCard.trackModalChanges(clientCardCtx(root));
    await loadUsers(root);
    setStatus(root, "\u0414\u0430\u043d\u043d\u044b\u0435 \u043a\u043b\u0438\u0435\u043d\u0442\u0430 \u043e\u0447\u0438\u0449\u0435\u043d\u044b");
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
        const nextFilter = button.dataset.managementUserFilter || "all";
        applyFilterState(root, nextFilter, { resetOffset: true });
        updatePageUrlForFilter(state.filter);
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
      const item = { is_blocked: row.dataset.isBlocked === "1" };
      openClientProfileModal(root, row.dataset.clientId, item).catch((error) => setStatus(root, error.message));
    });
    el.closeClientModalBtn?.addEventListener("click", () => closeClientModal(root));
    if (el.clientModal) closeOnBackdropClick(el.clientModal, () => closeClientModal(root));
    el.closeErrorModalBtn?.addEventListener("click", () => closeError(root));
    el.okErrorBtn?.addEventListener("click", () => closeError(root));
    el.copyErrorBtn?.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(el.errorText?.value || "");
        setStatus(root, "\u0422\u0435\u043a\u0441\u0442 \u043e\u0448\u0438\u0431\u043a\u0438 \u0441\u043a\u043e\u043f\u0438\u0440\u043e\u0432\u0430\u043d");
      } catch {
        setStatus(root, "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0441\u043a\u043e\u043f\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u0442\u0435\u043a\u0441\u0442 \u043e\u0448\u0438\u0431\u043a\u0438");
      }
    });
    if (el.errorModal) closeOnBackdropClick(el.errorModal, () => closeError(root));
    el.resetClientDataBtn?.addEventListener("click", () => resetClientData(root).catch((error) => {
      setStatus(root, `\u041e\u0448\u0438\u0431\u043a\u0430: ${error.message}`);
      showError(root, error.message);
    }));
    el.saveClientBtn?.addEventListener("click", () => saveClientProfile(root).catch((error) => setStatus(root, error.message)));
    el.toggleClientBlockBtn?.addEventListener("click", () => toggleClientBlock(root).catch((error) => setStatus(root, error.message)));
    el.fDestination?.addEventListener("change", () => window.TicketClientCard?.syncDynamicModalFields(clientCardCtx(root)));
    el.fLocationType?.addEventListener("change", () => window.TicketClientCard?.syncDynamicModalFields(clientCardCtx(root)));
  }

  async function handleManagementUsersUrlChange(root, urlLike) {
    const nextFilter = filterFromPageUrl(urlLike);
    const changed = nextFilter !== state.filter;
    applyFilterState(root, nextFilter, { resetOffset: changed });
    if (changed) {
      await loadUsers(root);
    }
  }

  function initManagementUsersPage({ root, url } = {}) {
    if (!root) return;
    applyFilterState(root, filterFromPageUrl(url || window.location.href), { resetOffset: false });
    bind(root);
    const handlePageKeydown = (event) => {
      if (event.key === "Escape" && !els(root).clientModal?.classList.contains("hidden")) { event.preventDefault(); closeClientModal(root); }
    };
    document.addEventListener("keydown", handlePageKeydown);
    const destroy = () => document.removeEventListener("keydown", handlePageKeydown);
    (async () => {
      try {
        const sidebar = await window.HelpdeskSidebar?.init?.();
        if (sidebar && sidebar.role !== "admin") {
          setStatus(root, "\u0414\u043e\u0441\u0442\u0443\u043f \u0442\u043e\u043b\u044c\u043a\u043e \u0434\u043b\u044f \u0430\u0434\u043c\u0438\u043d\u0430");
          return;
        }
        state.role = sidebar?.role || "";
        state.clientCardViewConfig = await api("/api/v1/management/users/client-card-view-config").catch(() => null);
        await loadUsers(root);
      } catch (error) {
        setStatus(root, error.message);
      }
    })();
    return {
      destroy,
      onUrlChange(nextUrl) {
        return handleManagementUsersUrlChange(root, nextUrl);
      },
    };
  }

  window.ManagementUsersPage = {
    buildUsersUrl,
    buildPageUrlForFilter,
    filterFromPageUrl,
    formatCell,
    formatDate,
    paginationSummary,
    tableRowHtml,
  };

  window.HelpdeskAppShell?.registerPage?.("management-users", initManagementUsersPage);
})();
