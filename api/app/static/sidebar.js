(function () {
  const TAB_COUNTS_STORAGE_KEY = "hd_tab_counts";
  const AGENTS_CACHE_STORAGE_KEY = "hd_agents_cache";
  const ASSIGNEES_STORAGE_KEY = "hd_admin_assignee_ids";
  const ASSIGNEES_LOGIN_TS_STORAGE_KEY = "hd_admin_assignee_login_ts";
  const USER_ROLE_STORAGE_KEY = "hd_user_role";
  const STORED_ASSIGNEES_RAW = localStorage.getItem(ASSIGNEES_STORAGE_KEY);
  const rootBody = document.body;

  if (rootBody) {
    rootBody.dataset.sidebarReady = "0";
  }

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

  function readByAssignee(raw) {
    if (!raw || typeof raw !== "object") return {};
    return Object.fromEntries(
      Object.entries(raw)
        .filter(([id, value]) => id && value && typeof value === "object")
        .map(([id, value]) => [
          String(id),
          {
            active: Number(value.active || 0),
            frozen: Number(value.frozen || 0),
          },
        ]),
    );
  }

  function readStoredAgents() {
    const parsed = safeParse(localStorage.getItem(AGENTS_CACHE_STORAGE_KEY) || "", []);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((item) => item && item.id && item.full_name)
      .map((item) => ({ id: String(item.id), full_name: String(item.full_name) }));
  }

  const state = {
    role: null,
    userId: "",
    isRealAdmin: false,
    sessionLoginTs: 0,
    appTimezone: "",
    availabilityStatus: "",
    login: "",
    avatarPresent: false,
    avatarUrl: null,
    avatarDisplayUrl: null,
    avatarResolvedUrl: null,
    avatarFailedUrl: null,
    avatarPendingUrl: null,
    counts: readStoredCounts(),
    byAssigneeCounts: {},
    agents: readStoredAgents(),
    adminAssigneeIds: safeParse(STORED_ASSIGNEES_RAW || "[]", []),
    hasStoredAssignees: STORED_ASSIGNEES_RAW !== null,
  };
  state.countsLoaded = false;

  const listeners = new Set();
  let initialized = false;
  let initPromise = null;
  let userMenuCleanup = null;
  let avatarLoadToken = 0;

  function setSidebarReady(value) {
    if (!rootBody) return;
    rootBody.dataset.sidebarReady = value ? "1" : "0";
  }

  function notify() {
    const snapshot = {
      role: state.role,
      userId: state.userId,
      isRealAdmin: state.isRealAdmin,
      fullName: state.fullName || "",
      login: state.login || "",
      avatarPresent: Boolean(state.avatarPresent),
      avatarUrl: state.avatarUrl || null,
      sessionLoginTs: Number(state.sessionLoginTs || 0),
      appTimezone: state.appTimezone,
      availabilityStatus: state.availabilityStatus || "",
      counts: { ...state.counts },
      byAssigneeCounts: { ...state.byAssigneeCounts },
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

  function ensureManagementSection() {
    if (qs("#sidebarManagementLink")) return;
    const massLink = qs('a.settings-link[href="/mass-notifications"]');
    if (!massLink || !massLink.parentNode) return;

    massLink.id = "sidebarManagementLink";
    massLink.href = "/management";
    massLink.innerHTML = "<span>\u0423\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u0435</span>";
    massLink.classList.toggle(
      "active",
      window.location.pathname === "/mass-notifications" || window.location.pathname.startsWith("/management"),
    );
  }

  function currentDisplayName() {
    const nameNode = qs("#sidebarUserDisplayName");
    const legacyText = (nameNode?.textContent || "").trim();
    const legacyName = legacyText && legacyText !== "—" ? legacyText : "";
    return (state.fullName || legacyName || "\u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c").trim();
  }
  function renderUserDisplay() {
    const displayName = currentDisplayName();
    const nameNode = qs("#sidebarUserDisplayName");
    if (nameNode) {
      nameNode.textContent = displayName;
    }
    const visibleNameNode = qs("[data-sidebar-user-full-name]");
    if (visibleNameNode) {
      visibleNameNode.textContent = displayName;
    }
  }

  function positionUserStatsByRole() {
    const stats = qs("#sidebarUserStats");
    if (!stats) return;
    stats.classList.remove("sidebar-user-stats-bottom");
  }

  function renderAvatar() {
    const avatar = qs(".sidebar-user-avatar-button") || qs(".agent-avatar");
    if (!avatar) return;
    let sourceName = currentDisplayName();
    if (!sourceName || sourceName === "\u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c") {
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
    const avatarUrl = state.avatarDisplayUrl || state.avatarUrl || "";
    if (avatarUrl && state.avatarPresent && state.avatarResolvedUrl === avatarUrl) {
      avatar.textContent = "";
      avatar.dataset.sidebarAvatarImage = "1";
      if (avatar.style) {
        avatar.style.backgroundImage = `url("${avatarUrl}")`;
        avatar.style.backgroundSize = "cover";
        avatar.style.backgroundPosition = "center";
      }
    } else {
      avatar.textContent = initials || "A";
      delete avatar.dataset.sidebarAvatarImage;
      if (avatar.style) {
        avatar.style.backgroundImage = "";
        avatar.style.backgroundSize = "";
        avatar.style.backgroundPosition = "";
      }
      if (avatarUrl && state.avatarPresent && state.avatarFailedUrl !== avatarUrl && state.avatarPendingUrl !== avatarUrl) {
        verifySidebarAvatarUrl(avatarUrl);
      }
    }
    avatar.title = sourceName || "\u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c";
  }

  function verifySidebarAvatarUrl(avatarUrl) {
    const token = ++avatarLoadToken;
    state.avatarPendingUrl = avatarUrl;
    const image = new Image();
    image.onload = () => {
      if (token !== avatarLoadToken) return;
      state.avatarPendingUrl = null;
      state.avatarResolvedUrl = avatarUrl;
      state.avatarFailedUrl = null;
      renderAvatar();
    };
    image.onerror = () => {
      if (token !== avatarLoadToken) return;
      state.avatarPendingUrl = null;
      state.avatarPresent = false;
      state.avatarResolvedUrl = null;
      state.avatarFailedUrl = avatarUrl;
      renderAvatar();
      notify();
    };
    image.src = avatarUrl;
  }

  function ensureUserMenuStructure() {
    const sidebarBottom = qs(".sidebar-bottom");
    if (!sidebarBottom) return null;
    let panel = qs("[data-sidebar-user-panel]") || qs(".sidebar-user-actions");
    if (!panel) {
      panel = document.createElement("div");
      sidebarBottom.appendChild(panel);
    }
    panel.classList.add("sidebar-user-panel");
    panel.dataset.sidebarUserPanel = "1";

    let toggle = panel.querySelector("[data-sidebar-user-menu-toggle]");
    if (!toggle) {
      const existingAvatar = panel.querySelector(".agent-avatar") || qs(".agent-avatar");
      if (existingAvatar && existingAvatar.tagName === "BUTTON") {
        toggle = existingAvatar;
      } else {
        toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "sidebar-user-avatar-button agent-avatar";
        toggle.textContent = existingAvatar?.textContent || "A";
        toggle.title = existingAvatar?.getAttribute("title") || "";
        if (existingAvatar?.parentNode) {
          existingAvatar.replaceWith(toggle);
        } else {
          panel.insertBefore(toggle, panel.firstChild);
        }
      }
    }
    toggle.classList.add("sidebar-user-avatar-button");
    toggle.setAttribute("aria-label", "\u041e\u0442\u043a\u0440\u044b\u0442\u044c \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c\u0441\u043a\u043e\u0435 \u043c\u0435\u043d\u044e");
    toggle.setAttribute("aria-haspopup", "menu");
    toggle.setAttribute("aria-expanded", "false");
    toggle.dataset.sidebarUserMenuToggle = "1";

    let visibleNameNode = panel.querySelector("[data-sidebar-user-full-name]");
    if (!visibleNameNode) {
      visibleNameNode = document.createElement("div");
      visibleNameNode.className = "sidebar-user-name";
      visibleNameNode.dataset.sidebarUserFullName = "1";
      visibleNameNode.textContent = "\u2014";
      panel.insertBefore(visibleNameNode, toggle.nextSibling);
    }

    let legacyNameNode = qs("#sidebarUserDisplayName");
    if (!legacyNameNode) {
      legacyNameNode = document.createElement("div");
      legacyNameNode.id = "sidebarUserDisplayName";
      panel.appendChild(legacyNameNode);
    }
    legacyNameNode.className = "sidebar-user-legacy-name";
    legacyNameNode.hidden = true;
    legacyNameNode.setAttribute("aria-hidden", "true");

    const stats = qs("#sidebarUserStats");
    if (stats) {
      const hasCompletionControls = Boolean(stats.querySelector("#completionToggleBtn") || stats.querySelector("#completionDetails"));
      if (!hasCompletionControls) {
        stats.hidden = true;
      }
    }
    const logoutForm = qs("[data-sidebar-logout-form]") || qs("form[action='/logout']");
    if (!logoutForm) return null;
    if (logoutForm.parentNode !== panel) {
      panel.appendChild(logoutForm);
    }
    logoutForm.hidden = true;
    logoutForm.dataset.sidebarLogoutForm = "1";
    let menu = panel.querySelector("[data-sidebar-user-menu]");
    if (!menu) {
      menu = document.createElement("div");
      menu.className = "sidebar-user-menu";
      menu.hidden = true;
      menu.dataset.sidebarUserMenu = "1";
      [
        ["profile", "\u041b\u0438\u0447\u043d\u044b\u0435 \u0434\u0430\u043d\u043d\u044b\u0435"],
        ["bot-notifications", "\u0423\u0432\u0435\u0434\u043e\u043c\u043b\u0435\u043d\u0438\u044f \u0432 \u0431\u043e\u0442\u0435"],
        ["feedback", "\u041e\u0442\u0437\u044b\u0432\u044b"],
        ["logout", "\u0412\u044b\u0445\u043e\u0434"],
      ].forEach(([key, label]) => {
        const item = document.createElement("button");
        item.type = "button";
        item.className = "sidebar-user-menu-item";
        if (key === "logout") {
          item.classList.add("sidebar-user-menu-item-danger");
        }
        item.dataset.sidebarUserMenuItem = key;
        item.textContent = label;
        menu.appendChild(item);
      });
      panel.appendChild(menu);
    }
    return { panel, toggle, menu, logoutForm };
  }

  function renderUserMenuVisibility(menu) {
    const reviewItem = menu?.querySelector("[data-sidebar-user-menu-item='feedback']");
    if (reviewItem) {
      reviewItem.hidden = state.role !== "agent" && state.role !== "trainee";
    }
    const notificationItem = menu?.querySelector("[data-sidebar-user-menu-item='bot-notifications']");
    if (notificationItem) {
      notificationItem.hidden = false;
    }
  }

  function ensureAvailabilityStatusPanel() {
    const sidebarBottom = qs(".sidebar-bottom");
    if (!sidebarBottom) return null;
    let panel = sidebarBottom.querySelector(".status-panel") || qs(".status-panel");
    if (!panel) {
      panel = document.createElement("div");
      panel.className = "status-panel hidden";
    }
    panel.classList.add("status-panel");
    panel.classList.add("hidden");

    if (!panel.querySelector(".status-panel-title")) {
      const title = document.createElement("div");
      title.className = "status-panel-title";
      title.textContent = "\u0421\u0442\u0430\u0442\u0443\u0441";
      panel.insertBefore(title, panel.firstChild);
    }

    [
      ["offline", "status-offline", "\u041d\u0435 \u0432 \u0441\u0435\u0442\u0438"],
      ["online", "status-online", "\u0412 \u0441\u0435\u0442\u0438"],
      ["on_ticket", "status-on-ticket", "\u041d\u0430 \u0437\u0430\u044f\u0432\u043a\u0435"],
      ["waiting", "status-waiting", "\u0412 \u043e\u0436\u0438\u0434\u0430\u043d\u0438\u0438"],
      ["lunch", "status-lunch", "\u041e\u0431\u0435\u0434"],
    ].forEach(([status, className, label]) => {
      if (panel.querySelector(`[data-availability-status='${status}']`)) return;
      const button = document.createElement("button");
      button.type = "button";
      button.className = `agent-status-btn ${className}`;
      button.dataset.availabilityStatus = status;
      button.setAttribute("data-availability-status", status);
      button.textContent = label;
      panel.appendChild(button);
    });

    const userPanel = sidebarBottom.querySelector("[data-sidebar-user-panel]") || sidebarBottom.querySelector(".sidebar-user-actions");
    if (panel.parentNode !== sidebarBottom) {
      sidebarBottom.insertBefore(panel, userPanel || sidebarBottom.firstChild);
    } else if (userPanel && panel.nextSibling !== userPanel) {
      sidebarBottom.insertBefore(panel, userPanel);
    }
    return panel;
  }

  function renderAvailabilityVisibility() {
    const panel = ensureAvailabilityStatusPanel();
    if (!panel) return;
    panel.classList.toggle("hidden", state.role !== "agent" && state.role !== "trainee");
  }

  function bindUserMenu() {
    const structure = ensureUserMenuStructure();
    if (!structure) return;
    const { panel, toggle, menu, logoutForm } = structure;
    renderUserMenuVisibility(menu);
    if (toggle.dataset.sidebarUserMenuBound === "1") return;
    if (typeof userMenuCleanup === "function") {
      userMenuCleanup();
      userMenuCleanup = null;
    }
    let isOpen = false;
    const menuItems = [...menu.querySelectorAll("[data-sidebar-user-menu-item]")];
    const setOpen = (nextOpen) => {
      isOpen = Boolean(nextOpen);
      panel.classList.toggle("menu-open", isOpen);
      toggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
      menu.hidden = !isOpen;
    };
    const closeMenu = () => setOpen(false);
    const toggleMenu = () => setOpen(!isOpen);
    toggle.addEventListener("click", (event) => {
      event.preventDefault();
      toggleMenu();
    });
    toggle.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        toggleMenu();
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        closeMenu();
      }
    });
    menu.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      closeMenu();
      toggle.focus();
    });
    menuItems.forEach((item) => {
      item.addEventListener("click", () => {
        const action = item.dataset.sidebarUserMenuItem;
        closeMenu();
        if (action === "logout") {
          if (typeof logoutForm.requestSubmit === "function") {
            logoutForm.requestSubmit();
          } else if (typeof logoutForm.submit === "function") {
            logoutForm.submit();
          } else {
            console.error("[sidebar-user-menu] Logout form cannot be submitted");
          }
          return;
        }
        if (action === "feedback") {
          if (state.role !== "agent" && state.role !== "trainee") return;
          if (window.HelpdeskAppShell?.navigate) {
            window.HelpdeskAppShell.navigate("/reviews");
          } else {
            window.location.href = "/reviews";
          }
          return;
        }
        if (action === "bot-notifications") {
          if (window.HelpdeskAppShell?.navigate) {
            window.HelpdeskAppShell.navigate("/profile/notification-bot");
          } else {
            window.location.href = "/profile/notification-bot";
          }
          return;
        }
        if (action === "profile") {
          if (window.HelpdeskAppShell?.navigate) {
            window.HelpdeskAppShell.navigate("/profile");
          } else {
            window.location.href = "/profile";
          }
          return;
        }
        console.info(`[sidebar-user-menu] Placeholder action: ${action || "unknown"}`);
      });
    });
    const handlePointerDown = (event) => {
      if (!isOpen) return;
      if (panel.contains(event.target)) return;
      closeMenu();
    };
    const handleDocumentKeydown = (event) => {
      if (!isOpen || event.key !== "Escape") return;
      event.preventDefault();
      closeMenu();
      toggle.focus();
    };
    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleDocumentKeydown);
    setOpen(false);
    toggle.dataset.sidebarUserMenuBound = "1";
    userMenuCleanup = () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleDocumentKeydown);
      setOpen(false);
    };
  }

  function selectedAgentIds() {
    const validIds = new Set((state.agents || []).map((agent) => agent.id));
    return [...new Set(state.adminAssigneeIds || [])].filter((id) => validIds.has(id));
  }

  function isAllAgentsSelected() {
    const selected = new Set(selectedAgentIds());
    return state.agents.length > 0 && state.agents.every((agent) => selected.has(agent.id));
  }

  function persistAssignees() {
    try {
      localStorage.setItem(ASSIGNEES_STORAGE_KEY, JSON.stringify(state.adminAssigneeIds));
      state.hasStoredAssignees = true;
    } catch {
      // noop
    }
  }

  function selectAssignees(nextIds) {
    const validIds = new Set((state.agents || []).map((agent) => agent.id));
    let normalized = [...new Set(nextIds || [])].filter((id) => validIds.has(id));
    if (!normalized.length && state.agents.length) {
      normalized = [state.agents[0].id];
    }
    state.adminAssigneeIds = normalized;
    persistAssignees();
  }

  function selectAllAssignees() {
    selectAssignees(state.agents.map((agent) => agent.id));
  }

  function formatAssigneeCount(value) {
    const count = Number(value || 0);
    return count > 0 ? String(count) : "";
  }

  function renderAvailabilityStatus(status) {
    if (status) {
      state.availabilityStatus = status;
    }
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
    state.availabilityStatus = me?.availability_status || status;
    renderAvailabilityStatus(state.availabilityStatus);
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
    const allSelected = isAllAgentsSelected();
    toggle.checked = allSelected;
    const header = toggle.closest ? toggle.closest(".admin-agent-header") : qs(".admin-agent-header");
    if (header) {
      header.classList.toggle("pressed", allSelected);
      header.setAttribute("aria-pressed", allSelected ? "true" : "false");
    }
  }

  function renderAgentFilter() {
    const listEl = qs("#adminAgentList");
    const toggleAll = qs("#adminAgentToggleAll");
    if (!listEl) return;
    if (state.role !== "admin") {
      listEl.innerHTML = "";
      listEl.dataset.signature = "";
      return;
    }

    const selectedSet = new Set(state.adminAssigneeIds || []);
    const allSelected = isAllAgentsSelected();
    const countsSignature = Object.entries(state.byAssigneeCounts || {})
      .map(([id, counts]) => `${id}:${counts.active || 0}:${counts.frozen || 0}`)
      .join("|");
    const signature = `${(state.agents || []).map((a) => `${a.id}:${a.full_name}`).join("|")}::${countsSignature}`;
    if (listEl.dataset.signature === signature) {
      listEl.querySelectorAll(".admin-agent-item").forEach((item) => {
        const id = item.dataset.agentId;
        const isSelected = selectedSet.has(id);
        item.classList.toggle("active", isSelected);
        item.setAttribute("aria-pressed", isSelected ? "true" : "false");
        const counts = state.byAssigneeCounts?.[id] || {};
        const activeNode = item.querySelector("[data-agent-count-active]");
        const frozenNode = item.querySelector("[data-agent-count-frozen]");
        if (activeNode) activeNode.textContent = formatAssigneeCount(counts.active);
        if (frozenNode) frozenNode.textContent = formatAssigneeCount(counts.frozen);
      });
      syncToggleAll(toggleAll, listEl);
      return;
    }

    listEl.dataset.signature = signature;
    listEl.innerHTML = "";
    state.agents.forEach((agent) => {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "admin-agent-item";
      item.dataset.agentId = agent.id;
      const name = document.createElement("span");
      name.className = "admin-agent-name";
      name.textContent = agent.full_name;
      const counts = state.byAssigneeCounts?.[agent.id] || {};
      const countsNode = document.createElement("span");
      countsNode.className = "admin-agent-counts";
      countsNode.setAttribute("aria-label", "\u0410\u043a\u0442\u0438\u0432\u043d\u044b\u0435 \u0438 \u0437\u0430\u043c\u043e\u0440\u043e\u0436\u0435\u043d\u043d\u044b\u0435");
      const activeCount = document.createElement("span");
      activeCount.className = "admin-agent-count admin-agent-count-active";
      activeCount.dataset.agentCountActive = "1";
      activeCount.textContent = formatAssigneeCount(counts.active);
      const dividerOne = document.createElement("span");
      dividerOne.className = "admin-agent-count-divider";
      dividerOne.textContent = "|";
      const dividerTwo = document.createElement("span");
      dividerTwo.className = "admin-agent-count-divider admin-agent-count-divider-frozen";
      dividerTwo.textContent = "|";
      const frozenCount = document.createElement("span");
      frozenCount.className = "admin-agent-count admin-agent-count-frozen";
      frozenCount.dataset.agentCountFrozen = "1";
      frozenCount.textContent = formatAssigneeCount(counts.frozen);
      countsNode.appendChild(activeCount);
      countsNode.appendChild(dividerOne);
      countsNode.appendChild(dividerTwo);
      countsNode.appendChild(frozenCount);
      item.appendChild(name);
      item.appendChild(countsNode);
      item.addEventListener("click", (event) => {
        event.preventDefault();
        const currentSet = new Set(state.adminAssigneeIds || []);
        if (event.ctrlKey && !isAllAgentsSelected()) {
          if (currentSet.has(agent.id)) {
            if (currentSet.size <= 1) return;
            currentSet.delete(agent.id);
          } else {
            currentSet.add(agent.id);
          }
          selectAssignees([...currentSet]);
        } else {
          selectAssignees([agent.id]);
        }
        renderAgentFilter();
        loadCounts();
        notify();
      });
      listEl.appendChild(item);
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
    state.appTimezone = payload.app_timezone;
    state.availabilityStatus = payload?.real_user?.availability_status || payload?.effective_user?.availability_status || state.availabilityStatus || "";
    state.fullName = (realUser.full_name || "").trim() || "Пользователь";
    try {
      localStorage.setItem("hd_user_id", state.userId);
      localStorage.setItem(USER_ROLE_STORAGE_KEY, state.role);
    } catch {
      // noop
    }
  }

  async function loadCurrentUserProfileAvatar() {
    try {
      const profile = await api("/api/v1/users/me/profile");
      state.avatarPresent = Boolean(profile?.avatar_present && profile?.avatar_url);
      state.avatarUrl = profile?.avatar_url || null;
      state.avatarDisplayUrl = null;
      state.avatarResolvedUrl = null;
      state.avatarFailedUrl = null;
      state.avatarPendingUrl = null;
      state.fullName = (profile?.full_name || state.fullName || "").trim() || state.fullName;
      state.login = (profile?.login || state.login || "").trim() || state.login;
    } catch {
      state.avatarPresent = false;
      state.avatarUrl = null;
      state.avatarDisplayUrl = null;
      state.avatarResolvedUrl = null;
      state.avatarFailedUrl = null;
      state.avatarPendingUrl = null;
    }
  }

  async function loadAgents() {
    const agents = await api("/api/v1/users/agents");
    state.agents = Array.isArray(agents)
      ? agents.filter((item) => item && item.id && item.full_name).map((item) => ({ id: String(item.id), full_name: String(item.full_name) }))
      : [];
    if (state.role === "admin") {
      const loginTs = String(state.sessionLoginTs || "");
      const storedLoginTs = localStorage.getItem(ASSIGNEES_LOGIN_TS_STORAGE_KEY);
      const validStoredIds = selectedAgentIds();
      if (storedLoginTs === null && state.hasStoredAssignees && validStoredIds.length) {
        state.adminAssigneeIds = validStoredIds;
      } else if (storedLoginTs !== loginTs) {
        state.adminAssigneeIds = state.agents[0] ? [state.agents[0].id] : [];
        persistAssignees();
      } else {
        state.adminAssigneeIds = validStoredIds.length || !state.agents.length ? validStoredIds : [state.agents[0].id];
        persistAssignees();
      }
      try {
        localStorage.setItem(ASSIGNEES_LOGIN_TS_STORAGE_KEY, loginTs);
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
    state.byAssigneeCounts = readByAssignee(counts?.by_assignee);
    state.countsLoaded = true;
    renderCounts();
    renderAgentFilter();
  }

  function bindToggleAll() {
    const toggleAll = qs("#adminAgentToggleAll");
    if (!toggleAll || toggleAll.dataset.bound === "1") return;
    toggleAll.dataset.bound = "1";
    const header = toggleAll.closest ? toggleAll.closest(".admin-agent-header") : qs(".admin-agent-header");
    if (header) {
      header.setAttribute("role", "button");
      header.setAttribute("tabindex", "0");
      header.setAttribute("aria-label", "\u0412\u0441\u0435");
    }
    const label = header?.querySelector("span");
    if (label) label.textContent = "\u0412\u0441\u0435";
    const chooseAll = (event) => {
      event?.preventDefault?.();
      selectAllAssignees();
      renderAgentFilter();
      loadCounts();
      notify();
    };
    toggleAll.tabIndex = -1;
	toggleAll.setAttribute("aria-hidden", "true");
    header?.addEventListener("click", chooseAll);
    header?.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      chooseAll(event);
    });
  }

  function renderFromCurrentState() {
    ensureManagementSection();
    ensureAvailabilityStatusPanel();
    positionUserStatsByRole();
    renderAdminVisibility();
    renderAvailabilityVisibility();
    renderCounts();
    renderUserDisplay();
    renderAvatar();
    bindUserMenu();
    renderAgentFilter();
    bindToggleAll();
    bindSettingsStatusButtons();
    renderAvailabilityStatus(state.availabilityStatus);
  }

  async function init(options = {}) {
    if (initialized) {
      renderFromCurrentState();
      setSidebarReady(true);
      return { ...state };
    }
    if (initPromise) return initPromise;
    setSidebarReady(false);
    renderFromCurrentState();
    initPromise = (async () => {
      try {
        await loadSession();
        await loadCurrentUserProfileAvatar();
        await loadAgents();
        await loadCounts();
        renderFromCurrentState();
        setSidebarReady(true);
        initialized = true;
        notify();
        initPromise = null;
        return { ...state };
      } catch (error) {
        initPromise = null;
        if (error?.status === 401) {
          window.location.href = "/login";
        }
        if (typeof options.onError === "function") {
          options.onError(error);
        }
        throw error;
      }
    })();
    return initPromise;
  }

  window.HelpdeskSidebar = {
    init,
    onChange,
    getState: () => ({ ...state, counts: { ...state.counts }, byAssigneeCounts: { ...state.byAssigneeCounts }, agents: [...state.agents], adminAssigneeIds: [...state.adminAssigneeIds] }),
    refreshCounts: () => loadCounts().then(() => { renderFromCurrentState(); notify(); }),
    setCurrentUserAvatar: (avatarState = {}) => {
      const nextUrl = avatarState.avatar_display_url || avatarState.avatar_url || null;
      state.avatarPresent = Boolean(avatarState.avatar_present && avatarState.avatar_url);
      state.avatarUrl = avatarState.avatar_url || null;
      state.avatarDisplayUrl = avatarState.avatar_display_url || null;
      state.avatarResolvedUrl = state.avatarResolvedUrl === nextUrl ? state.avatarResolvedUrl : null;
      state.avatarFailedUrl = state.avatarFailedUrl === nextUrl ? state.avatarFailedUrl : null;
      state.avatarPendingUrl = state.avatarPendingUrl === nextUrl ? state.avatarPendingUrl : null;
      renderAvatar();
      notify();
    },
  };
  function bootstrapUserPanel() {
    ensureUserMenuStructure();
    renderUserDisplay();
    renderAvatar();
    bindUserMenu();
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootstrapUserPanel, { once: true });
  } else {
    bootstrapUserPanel();
  }
})();
