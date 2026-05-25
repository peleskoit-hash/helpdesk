(function () {
  const TAB_COUNTS_STORAGE_KEY = "hd_tab_counts";
  const AGENTS_CACHE_STORAGE_KEY = "hd_agents_cache";
  const ASSIGNEES_STORAGE_KEY = "hd_admin_assignee_ids";
  const USER_ROLE_STORAGE_KEY = "hd_user_role";
  const STORED_ASSIGNEES_RAW = localStorage.getItem(ASSIGNEES_STORAGE_KEY);

  function safeParse(raw, fallback) {
    try {
      return JSON.parse(raw);
    } catch {
      return fallback;
    }
  }

  function readStoredCounts() {
    const parsed = safeParse(localStorage.getItem(TAB_COUNTS_STORAGE_KEY) || "", null);
    if (!parsed || typeof parsed !== "object") {
      return { unassigned: 0, active: 0, special: 0, frozen: 0, closed: 0 };
    }
    return {
      unassigned: Number(parsed.unassigned || 0),
      active: Number(parsed.active || 0),
      special: Number(parsed.special || 0),
      frozen: Number(parsed.frozen || 0),
      closed: Number(parsed.closed || 0),
    };
  }

  function readStoredAgents() {
    const parsed = safeParse(localStorage.getItem(AGENTS_CACHE_STORAGE_KEY) || "", []);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((item) => item && item.id && item.full_name)
      .map((item) => ({ id: String(item.id), full_name: String(item.full_name) }));
  }

  const state = {
    role: localStorage.getItem(USER_ROLE_STORAGE_KEY) || "agent",
    userId: localStorage.getItem("hd_user_id") || "",
    isRealAdmin: false,
    sessionLoginTs: 0,
    login: "",
    counts: readStoredCounts(),
    agents: readStoredAgents(),
    adminAssigneeIds: safeParse(STORED_ASSIGNEES_RAW || "[]", []),
    hasStoredAssignees: STORED_ASSIGNEES_RAW !== null,
  };
  state.countsLoaded = false;

  const listeners = new Set();
  let statsAnchor = null;

  function ensureStatsAnchor() {
    const stats = qs("#sidebarUserStats");
    if (!stats || statsAnchor) return;
    statsAnchor = document.createComment("sidebar-user-stats-anchor");
    const parent = stats.parentNode;
    if (parent) {
      parent.insertBefore(statsAnchor, stats);
    }
  }

  function notify() {
    const snapshot = {
      role: state.role,
      userId: state.userId,
      isRealAdmin: state.isRealAdmin,
      fullName: state.fullName || "",
      login: state.login || "",
      sessionLoginTs: Number(state.sessionLoginTs || 0),
      counts: { ...state.counts },
      agents: [...state.agents],
      adminAssigneeIds: [...state.adminAssigneeIds],
    };
    listeners.forEach((fn) => {
      try {
        fn(snapshot);
      } catch {
        // noop
      }
    });
  }

  function onChange(fn) {
    if (typeof fn !== "function") return () => {};
    listeners.add(fn);
    return () => listeners.delete(fn);
  }

  function qs(selector) {
    return document.querySelector(selector);
  }

  function qsa(selector) {
    return [...document.querySelectorAll(selector)];
  }

  function escapeHtml(v) {
    return String(v ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: {
        accept: "application/json",
        ...(options.headers || {}),
      },
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
      const message = payload?.detail?.error?.message || raw || "Ошибка запроса";
      const err = new Error(message);
      err.status = response.status;
      throw err;
    }
    return payload;
  }

  function renderCounts() {
    const map = {
      unassigned: state.counts.unassigned || 0,
      active: state.counts.active || 0,
      special: state.counts.special || 0,
      frozen: state.counts.frozen || 0,
      closed: state.counts.closed || 0,
    };
    qsa("[data-tab-count]").forEach((node) => {
      const key = node.dataset.tabCount;
      if (!(key in map)) return;
      node.textContent = String(map[key]);
    });
    if (state.countsLoaded) {
      try {
        localStorage.setItem(TAB_COUNTS_STORAGE_KEY, JSON.stringify(map));
      } catch {
        // noop
      }
    }
  }

  function renderAdminVisibility() {
    qsa(".admin-only").forEach((node) => {
      node.classList.toggle("hidden", state.role !== "admin");
    });
  }

  function renderUserDisplay() {
    const nameNode = qs("#sidebarUserDisplayName");
    if (nameNode && state.fullName) {
      nameNode.textContent = state.fullName;
    }
  }

  function positionUserStatsByRole() {
    const stats = qs("#sidebarUserStats");
    if (!stats) return;
    ensureStatsAnchor();
    const sidebarBottom = qs(".sidebar-bottom");
    const userActions = qs(".sidebar-user-actions");
    const role = state.role;
    const placeBottom = role === "admin" || role === "chiki_piki";

    if (placeBottom && sidebarBottom) {
      if (userActions && userActions.parentNode === sidebarBottom) {
        sidebarBottom.insertBefore(stats, userActions);
      } else if (stats.parentNode !== sidebarBottom) {
        sidebarBottom.appendChild(stats);
      }
      stats.classList.add("sidebar-user-stats-bottom");
      return;
    }

    if (statsAnchor && statsAnchor.parentNode) {
      statsAnchor.parentNode.insertBefore(stats, statsAnchor.nextSibling);
    }
    stats.classList.remove("sidebar-user-stats-bottom");
  }

  function renderAvatar() {
    const avatar = qs(".agent-avatar");
    if (!avatar) return;
    let sourceName = state.fullName || "Пользователь";
    if (!sourceName || sourceName === "Пользователь") {
      const me = state.agents.find((a) => a.id === state.userId);
      sourceName = me?.full_name || sourceName;
    }
    const initials = (sourceName || "A")
      .split(" ")
      .filter(Boolean)
      .slice(0, 2)
      .map((chunk) => chunk[0])
      .join("")
      .toUpperCase();
    avatar.textContent = initials || "A";
    avatar.title = sourceName || "Пользователь";
  }

  function renderAvailabilityStatus(status) {
    qsa("[data-availability-status]").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.availabilityStatus === status);
    });
  }

  async function updateMyAvailabilityStatus(status) {
    const me = await api("/api/v1/users/me/status", {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ availability_status: status }),
    });
    renderAvailabilityStatus(me?.availability_status || status);
    // Refresh counts after potential auto-assignment trigger on backend.
    await loadCounts();
    notify();
  }

  function bindSettingsStatusButtons() {
    // Bind globally so status switch works from sidebar on any page.
    qsa("[data-availability-status]").forEach((btn) => {
      if (btn.dataset.boundStatusSidebar === "1") return;
      btn.dataset.boundStatusSidebar = "1";
      btn.addEventListener("click", async () => {
        const status = btn.dataset.availabilityStatus;
        if (!status) return;
        try {
          renderAvailabilityStatus(status);
          await updateMyAvailabilityStatus(status);
        } catch {
          // noop; page-level error handling is absent on settings, keep silent.
        }
      });
    });
  }

  function syncToggleAll(toggle, listEl) {
    if (!toggle || !listEl) return;
    const selected = new Set(state.adminAssigneeIds || []);
    const allSelected = state.agents.length > 0 && state.agents.every((a) => selected.has(a.id));
    toggle.checked = allSelected;
  }

  function renderAgentFilter() {
    const listEl = qs("#adminAgentList");
    const toggleAll = qs("#adminAgentToggleAll");
    if (!listEl) return;

    const selectedSet = new Set(state.adminAssigneeIds || []);
    const signature = (state.agents || []).map((a) => `${a.id}:${a.full_name}`).join("|");
    if (listEl.dataset.signature === signature) {
      listEl.querySelectorAll("label.admin-agent-item").forEach((label) => {
        const input = label.querySelector("input[type='checkbox']");
        if (!input) return;
        const isSelected = selectedSet.has(input.value);
        input.checked = isSelected;
        label.classList.toggle("active", isSelected);
      });
      syncToggleAll(toggleAll, listEl);
      return;
    }

    listEl.dataset.signature = signature;
    listEl.innerHTML = "";
    state.agents.forEach((agent) => {
      const label = document.createElement("label");
      label.className = "admin-agent-item";
      label.innerHTML = `<input type="checkbox" value="${escapeHtml(agent.id)}" /><span>${escapeHtml(agent.full_name)}</span>`;
      label.addEventListener("click", (event) => {
        event.preventDefault();
        const currentSet = new Set(state.adminAssigneeIds || []);
        if (currentSet.has(agent.id)) currentSet.delete(agent.id);
        else currentSet.add(agent.id);
        state.adminAssigneeIds = [...currentSet];
        try {
          localStorage.setItem(ASSIGNEES_STORAGE_KEY, JSON.stringify(state.adminAssigneeIds));
          state.hasStoredAssignees = true;
        } catch {
          // noop
        }
        renderAgentFilter();
        loadCounts();
        notify();
      });
      listEl.appendChild(label);
    });
    renderAgentFilter();
  }

  async function loadSession() {
    const payload = await api("/api/v1/auth/session");
    const realUser = payload?.real_user;
    if (!realUser?.id || !realUser?.role) {
      throw new Error("Сессия не инициализирована");
    }
    state.userId = String(realUser.id);
    state.role = String(realUser.role);
    state.login = String(realUser.login || "");
    state.isRealAdmin = Boolean(payload?.is_real_admin);
    state.sessionLoginTs = Number(payload?.session_login_ts || 0);
    state.fullName = (realUser.full_name || "").trim() || "Пользователь";
    try {
      localStorage.setItem("hd_user_id", state.userId);
      localStorage.setItem(USER_ROLE_STORAGE_KEY, state.role);
    } catch {
      // noop
    }
  }

  async function loadAgents() {
    const agents = await api("/api/v1/users/agents");
    state.agents = Array.isArray(agents)
      ? agents.filter((item) => item && item.id && item.full_name).map((item) => ({ id: String(item.id), full_name: String(item.full_name) }))
      : [];
    if (state.role === "admin" && !state.hasStoredAssignees) {
      state.adminAssigneeIds = state.agents.map((agent) => agent.id);
      state.hasStoredAssignees = true;
      try {
        localStorage.setItem(ASSIGNEES_STORAGE_KEY, JSON.stringify(state.adminAssigneeIds));
      } catch {
        // noop
      }
    }
    try {
      localStorage.setItem(AGENTS_CACHE_STORAGE_KEY, JSON.stringify(state.agents));
    } catch {
      // noop
    }
  }

  async function loadCounts() {
    if (state.role === "admin" && (!state.agents.length || !Array.isArray(state.adminAssigneeIds))) {
      return;
    }
    const query = new URLSearchParams();
    if (state.role === "admin") {
      (state.adminAssigneeIds || []).forEach((id) => query.append("assignee_ids", id));
    }
    const suffix = query.toString() ? `?${query.toString()}` : "";
    const counts = await api(`/api/v1/tickets/counts${suffix}`);
    state.counts = {
      unassigned: Number(counts?.unassigned || 0),
      active: Number(counts?.active || 0),
      special: Number(counts?.special || 0),
      frozen: Number(counts?.frozen || 0),
      closed: Number(counts?.closed || 0),
    };
    state.countsLoaded = true;
    renderCounts();
  }

  function bindToggleAll() {
    const toggleAll = qs("#adminAgentToggleAll");
    if (!toggleAll || toggleAll.dataset.bound === "1") return;
    toggleAll.dataset.bound = "1";
    toggleAll.addEventListener("change", () => {
      state.adminAssigneeIds = toggleAll.checked ? state.agents.map((a) => a.id) : [];
      try {
        localStorage.setItem(ASSIGNEES_STORAGE_KEY, JSON.stringify(state.adminAssigneeIds));
        state.hasStoredAssignees = true;
      } catch {
        // noop
      }
      renderAgentFilter();
      loadCounts();
      notify();
    });
  }

  function renderFromCurrentState() {
    positionUserStatsByRole();
    renderAdminVisibility();
    renderCounts();
    renderUserDisplay();
    renderAvatar();
    renderAgentFilter();
    bindToggleAll();
    bindSettingsStatusButtons();
  }

  async function init(options = {}) {
    renderFromCurrentState();
    try {
      await loadSession();
      await loadAgents();
      await loadCounts();
      renderFromCurrentState();
      notify();
      return { ...state };
    } catch (error) {
      if (error?.status === 401) {
        window.location.href = "/login";
      }
      if (typeof options.onError === "function") {
        options.onError(error);
      }
      throw error;
    }
  }

  window.HelpdeskSidebar = {
    init,
    onChange,
    getState: () => ({ ...state, counts: { ...state.counts }, agents: [...state.agents], adminAssigneeIds: [...state.adminAssigneeIds] }),
    refreshCounts: () => loadCounts().then(() => { renderFromCurrentState(); notify(); }),
  };
})();
