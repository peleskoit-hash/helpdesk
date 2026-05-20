const params = new URLSearchParams(window.location.search);
const tabFromQuery = params.get("tab");
const TAB_COUNTS_STORAGE_KEY = "hd_tab_counts";
const COMPLETION_OPEN_STORAGE_KEY = "hd_completion_sidebar_open";

function readStoredTabCounts() {
  try {
    const raw = localStorage.getItem(TAB_COUNTS_STORAGE_KEY);
    if (!raw) return { unassigned: 0, active: 0, special: 0, frozen: 0, closed: 0 };
    const parsed = JSON.parse(raw);
    return {
      unassigned: Number(parsed?.unassigned || 0),
      active: Number(parsed?.active || 0),
      special: Number(parsed?.special || 0),
      frozen: Number(parsed?.frozen || 0),
      closed: Number(parsed?.closed || 0),
    };
  } catch {
    return { unassigned: 0, active: 0, special: 0, frozen: 0, closed: 0 };
  }
}

const state = {
  tab: tabFromQuery || localStorage.getItem("hd_tab") || "active",
  tableSortKey: localStorage.getItem("hd_table_sort_key") || "created_at",
  tableSortDir: localStorage.getItem("hd_table_sort_dir") || "desc",
  role: localStorage.getItem("hd_user_role") || "agent",
  userId: "",
  isRealAdmin: false,
  roleUsers: [],
  supportAgents: [],
  adminAssigneeIds: JSON.parse(localStorage.getItem("hd_admin_assignee_ids") || "[]"),
  selectedTicketIds: new Set(),
  visibleTicketIds: [],
  tickets: [],
  page: 1,
  pages: 1,
  total: 0,
  limit: 20,
  isLoading: false,
  counts: readStoredTabCounts(),
  sessionContext: null,
  completionStats: null,
  completionOpen: false,
};

const endpointMap = {
  active: "/api/v1/tickets/active",
  unassigned: "/api/v1/tickets/unassigned",
  special: "/api/v1/tickets/special",
  frozen: "/api/v1/tickets/frozen",
  closed: "/api/v1/tickets/closed",
};

const el = {
  tabButtons: [...document.querySelectorAll(".tab-btn")],
  sortButtons: [...document.querySelectorAll(".table-sort-btn")],
  roleSelect: document.getElementById("roleSelect"),
  roleControl: document.getElementById("roleControl"),
  agentSelect: document.getElementById("agentSelect"),
  agentSelectWrap: document.getElementById("agentSelectWrap"),
  searchInput: document.getElementById("searchInput"),
  ticketsTableBody: document.getElementById("ticketsTableBody"),
  ticketCount: document.getElementById("ticketCount"),
  pageCurrent: document.getElementById("pageCurrent"),
  pageTotal: document.getElementById("pageTotal"),
  prevPageBtn: document.getElementById("prevPageBtn"),
  nextPageBtn: document.getElementById("nextPageBtn"),
  emptyText: document.getElementById("emptyText"),
  statusText: document.getElementById("statusText"),
  bulkAssignBtn: document.getElementById("bulkAssignBtn"),
  bulkCloseBtn: document.getElementById("bulkCloseBtn"),
  bulkDeleteBtn: document.getElementById("bulkDeleteBtn"),
  selectAllTicketsBtn: document.getElementById("selectAllTicketsBtn"),
  assignModal: document.getElementById("assignModal"),
  closeAssignModalBtn: document.getElementById("closeAssignModalBtn"),
  confirmBulkAssignBtn: document.getElementById("confirmBulkAssignBtn"),
  bulkAssigneeSelect: document.getElementById("bulkAssigneeSelect"),
  bulkActionModal: document.getElementById("bulkActionModal"),
  bulkActionModalTitle: document.getElementById("bulkActionModalTitle"),
  bulkActionModalText: document.getElementById("bulkActionModalText"),
  closeBulkActionModalBtn: document.getElementById("closeBulkActionModalBtn"),
  cancelBulkActionBtn: document.getElementById("cancelBulkActionBtn"),
  confirmBulkActionBtn: document.getElementById("confirmBulkActionBtn"),
  errorModal: document.getElementById("errorModal"),
  errorText: document.getElementById("errorText"),
  closeErrorModalBtn: document.getElementById("closeErrorModalBtn"),
  copyErrorBtn: document.getElementById("copyErrorBtn"),
  okErrorBtn: document.getElementById("okErrorBtn"),
  statusButtons: [...document.querySelectorAll("[data-availability-status]")],
  adminOnly: [...document.querySelectorAll(".admin-only")],
  statusPanel: document.querySelector(".status-panel"),
  adminAgentFilter: document.getElementById("adminAgentFilter"),
  adminAgentList: document.getElementById("adminAgentList"),
  adminAgentToggleAll: document.getElementById("adminAgentToggleAll"),
  adminAssigneeCols: [...document.querySelectorAll(".admin-assignee-col")],
  adminSelectCols: [...document.querySelectorAll(".admin-select-col")],
  agentAvatar: document.querySelector(".agent-avatar"),
  tabCountNodes: [...document.querySelectorAll("[data-tab-count]")],
  sidebarUserDisplayName: document.getElementById("sidebarUserDisplayName"),
  completionToggleBtn: document.getElementById("completionToggleBtn"),
  completionSummaryArrow: document.getElementById("completionSummaryArrow"),
  completionDetails: document.getElementById("completionDetails"),
  completionYearTickets: document.getElementById("completionYearTickets"),
  completionYearTasks: document.getElementById("completionYearTasks"),
  completionYearTotal: document.getElementById("completionYearTotal"),
  completionMonthTickets: document.getElementById("completionMonthTickets"),
  completionMonthTasks: document.getElementById("completionMonthTasks"),
  completionMonthTotal: document.getElementById("completionMonthTotal"),
  completionWeekTickets: document.getElementById("completionWeekTickets"),
  completionWeekTasks: document.getElementById("completionWeekTasks"),
  completionWeekTotal: document.getElementById("completionWeekTotal"),
  completionDayTickets: document.getElementById("completionDayTickets"),
  completionDayTasks: document.getElementById("completionDayTasks"),
  completionDayTotal: document.getElementById("completionDayTotal"),
  completionAllTimeTickets: document.getElementById("completionAllTimeTickets"),
  completionAllTimeTasks: document.getElementById("completionAllTimeTasks"),
  completionAllTimeTotal: document.getElementById("completionAllTimeTotal"),
  completionAllTimeTotalRow: document.getElementById("completionAllTimeTotalRow"),
  completionLegacy: document.getElementById("completionLegacy"),
};
let pendingBulkAction = null;

function setStatus(text) { el.statusText.textContent = text; }

function showError(message) {
  el.errorText.value = message || "Неизвестная ошибка";
  el.errorModal.classList.remove("hidden");
}

function closeError() {
  el.errorModal.classList.add("hidden");
}

function closeOnBackdropClick(modal, closeHandler) {
  let startedOnBackdrop = false;
  modal.addEventListener("pointerdown", (event) => {
    startedOnBackdrop = event.target === modal;
  });
  modal.addEventListener("click", (event) => {
    if (startedOnBackdrop && event.target === modal) {
      closeHandler();
    }
    startedOnBackdrop = false;
  });
}

function headers() {
  return { accept: "application/json" };
}

async function api(path) {
  const response = await fetch(path, { headers: headers() });
  if (response.status === 401) {
    window.location.href = "/login";
    throw new Error("Требуется авторизация");
  }
  const payload = await response.json();
  if (!response.ok) {
    const detail = payload?.detail?.error?.message || "Ошибка запроса";
    throw new Error(detail);
  }
  return payload;
}

async function apiWrite(path, body) {
  const response = await fetch(path, {
    method: "PATCH",
    headers: {
      ...headers(),
      "content-type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (response.status === 401) {
    window.location.href = "/login";
    throw new Error("Требуется авторизация");
  }
  const payload = await response.json();
  if (!response.ok) {
    const detail = payload?.detail?.error?.message || "Ошибка запроса";
    throw new Error(detail);
  }
  return payload;
}

async function apiPost(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: {
      ...headers(),
      "content-type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (response.status === 401) {
    window.location.href = "/login";
    throw new Error("Требуется авторизация");
  }
  const payload = await response.json();
  if (!response.ok) {
    const detail = payload?.detail?.error?.message || "Ошибка запроса";
    throw new Error(detail);
  }
  return payload;
}

function fmtDate(dateIso) {
  if (!dateIso) return "-";
  const d = new Date(dateIso);
  return d.toLocaleString("ru-RU", { hour12: false });
}

function ageText(dateIso) {
  if (!dateIso) return "-";
  const diffMs = Date.now() - new Date(dateIso).getTime();
  if (!Number.isFinite(diffMs)) return "-";
  const totalMinutes = Math.max(0, Math.floor(diffMs / 60000));
  const totalHours = Math.floor(totalMinutes / 60);
  const totalDays = Math.floor(totalHours / 24);
  if (totalDays >= 7) return `${totalDays}д`;
  if (totalDays >= 1) return `${totalDays}д ${totalHours % 24}ч`;
  if (totalHours >= 1) return `${totalHours}ч ${totalMinutes % 60}м`;
  return `${totalMinutes}м`;
}

function renderTabCounts() {
  // Sidebar counters are rendered by shared sidebar.js
}

function renderCompletionStats() {
  if (!el.sidebarUserDisplayName) return;
  const session = state.sessionContext || {};
  const effectiveName = session?.effective_user?.full_name || session?.real_user?.full_name || "";
  const fallbackLogin = session?.effective_user?.login || session?.real_user?.login || "";
  el.sidebarUserDisplayName.textContent = (effectiveName || fallbackLogin || "Пользователь").trim();

  const role = session?.effective_user?.role || state.role;
  const showExpanded = role === "agent" || role === "trainee";
  if (el.completionToggleBtn) {
    el.completionToggleBtn.classList.toggle("hidden", !showExpanded);
  }
  if (!showExpanded) {
    if (el.completionDetails) el.completionDetails.classList.add("hidden");
    return;
  }

  const stats = state.completionStats || {};
  const total = stats.total || { total: 0, tasks: 0, tickets: 0 };
  const calendar = stats.calendar || {};
  const legacy = Number(stats.legacy || 0);
  const year = calendar.year || { total: 0, tasks: 0, tickets: 0 };
  const month = calendar.month || { total: 0, tasks: 0, tickets: 0 };
  const week = calendar.week || { total: 0, tasks: 0, tickets: 0 };
  const day = calendar.day || { total: 0, tasks: 0, tickets: 0 };

  if (el.completionYearTickets) el.completionYearTickets.textContent = String(Number(year.tickets || 0));
  if (el.completionYearTasks) el.completionYearTasks.textContent = String(Number(year.tasks || 0));
  if (el.completionYearTotal) el.completionYearTotal.textContent = String(Number(year.total || 0));
  if (el.completionMonthTickets) el.completionMonthTickets.textContent = String(Number(month.tickets || 0));
  if (el.completionMonthTasks) el.completionMonthTasks.textContent = String(Number(month.tasks || 0));
  if (el.completionMonthTotal) el.completionMonthTotal.textContent = String(Number(month.total || 0));
  if (el.completionWeekTickets) el.completionWeekTickets.textContent = String(Number(week.tickets || 0));
  if (el.completionWeekTasks) el.completionWeekTasks.textContent = String(Number(week.tasks || 0));
  if (el.completionWeekTotal) el.completionWeekTotal.textContent = String(Number(week.total || 0));
  if (el.completionDayTickets) el.completionDayTickets.textContent = String(Number(day.tickets || 0));
  if (el.completionDayTasks) el.completionDayTasks.textContent = String(Number(day.tasks || 0));
  if (el.completionDayTotal) el.completionDayTotal.textContent = String(Number(day.total || 0));
  if (el.completionAllTimeTickets) el.completionAllTimeTickets.textContent = String(Number(total.tickets || 0));
  if (el.completionAllTimeTasks) el.completionAllTimeTasks.textContent = String(Number(total.tasks || 0));
  if (el.completionAllTimeTotal) el.completionAllTimeTotal.textContent = String(Number(total.total || 0));
  if (el.completionAllTimeTotalRow) el.completionAllTimeTotalRow.textContent = String(Number(total.total || 0));
  if (el.completionLegacy) el.completionLegacy.textContent = String(legacy);

  if (el.completionSummaryArrow) el.completionSummaryArrow.textContent = state.completionOpen ? "▲" : "▼";
  if (el.completionDetails) el.completionDetails.classList.toggle("hidden", !state.completionOpen);
}

async function loadCompletionStats(options = {}) {
  if (!(state.role === "agent" || state.role === "trainee")) {
    state.completionStats = null;
    renderCompletionStats();
    return;
  }
  try {
    state.completionStats = await api("/api/v1/users/me/completion-stats");
  } catch (e) {
    state.completionStats = {
      total: { total: 0, tasks: 0, tickets: 0 },
      calendar: {
        year: { total: 0, tasks: 0, tickets: 0 },
        month: { total: 0, tasks: 0, tickets: 0 },
        week: { total: 0, tasks: 0, tickets: 0 },
        day: { total: 0, tasks: 0, tickets: 0 },
      },
      legacy: 0,
    };
    if (!options.silent) {
      console.error("loadCompletionStats failed", e);
    }
  }
  renderCompletionStats();
}

function applySearch(items) {
  const q = (el.searchInput.value || "").trim().toLowerCase();
  if (!q) return items;
  return items.filter((t) =>
    (t.subject || "").toLowerCase().includes(q) ||
    (t.client_name || "").toLowerCase().includes(q)
  );
}

function dateMs(value) {
  if (!value) return 0;
  const ms = new Date(value).getTime();
  return Number.isFinite(ms) ? ms : 0;
}

function sortValue(ticket, key) {
  switch (key) {
    case "subject":
      return String(ticket.subject || "").toLocaleLowerCase("ru");
    case "client_name":
      return String(ticket.client_name || "").toLocaleLowerCase("ru");
    case "assignee_name":
      return String(ticket.assignee_name || "").toLocaleLowerCase("ru");
    case "last_reply_at":
      return dateMs(ticket.last_reply_at);
    case "created_at":
      return dateMs(ticket.created_at);
    case "age_created":
      return dateMs(ticket.created_at);
    case "updated_at":
      return dateMs(ticket.updated_at);
    default:
      return "";
  }
}

function sortTickets(items) {
  // UI-sort is intentionally local for the currently loaded page.
  // Backend list endpoints remain the source for global ordering/pagination.
  const key = state.tableSortKey;
  const dir = state.tableSortDir === "asc" ? 1 : -1;
  const textKeys = new Set(["subject", "client_name", "assignee_name"]);
  const sorted = [...items];
  sorted.sort((a, b) => {
    const av = sortValue(a, key);
    const bv = sortValue(b, key);
    if (textKeys.has(key)) {
      return String(av).localeCompare(String(bv), "ru", { sensitivity: "base" }) * dir;
    }
    return ((av || 0) - (bv || 0)) * dir;
  });
  return sorted;
}

function refreshSortHeaderUi() {
  el.sortButtons.forEach((btn) => {
    const base = btn.textContent.replace(/\s[▲▼]$/, "");
    btn.textContent = base;
    btn.classList.remove("active");
    if (btn.dataset.sortKey === state.tableSortKey) {
      btn.classList.add("active");
      btn.textContent = `${base} ${state.tableSortDir === "asc" ? "▲" : "▼"}`;
    }
  });
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function lastReplyText(ticket) {
  if (!ticket.last_reply_at) return "-";
  const who = ticket.last_reply_by || "-";
  return `${who} (${fmtDate(ticket.last_reply_at)})`;
}

function intakeBadge(ticket) {
  if (ticket.is_special) return " [Особая]";
  if (ticket.workflow_type === "auto") return " [авто]";
  if (ticket.intake_state === "collecting") return " [сбор данных]";
  if (ticket.intake_state === "cancelled") return " [отменено]";
  return "";
}

function assigneeOptionsHtml(selectedId, selectedName = "") {
  const options = [`<option value="" ${selectedId ? "" : "selected"}>Распределение</option>`];
  const hasSelectedInSupport = Boolean(
    selectedId && state.supportAgents.some((agent) => agent.id === selectedId)
  );
  if (selectedId && !hasSelectedInSupport) {
    const fallbackName = selectedName || "Текущий ответственный";
    options.push(
      `<option value="${escapeHtml(selectedId)}" selected>${escapeHtml(fallbackName)}</option>`
    );
  }
  for (const agent of state.supportAgents) {
    options.push(
      `<option value="${escapeHtml(agent.id)}" ${agent.id === selectedId ? "selected" : ""}>${escapeHtml(agent.full_name)}</option>`
    );
  }
  return options.join("");
}

function renderBulkAssigneeOptions() {
  el.bulkAssigneeSelect.innerHTML = assigneeOptionsHtml("");
}

function updateBulkAssignUi() {
  const isAdmin = state.role === "admin";
  const selectedCount = state.selectedTicketIds.size;
  el.bulkAssignBtn.classList.toggle("hidden", !isAdmin || selectedCount === 0);
  if (el.bulkCloseBtn) {
    el.bulkCloseBtn.classList.toggle("hidden", !isAdmin);
    el.bulkCloseBtn.disabled = selectedCount === 0;
  }
  if (el.bulkDeleteBtn) {
    el.bulkDeleteBtn.classList.toggle("hidden", !isAdmin);
    el.bulkDeleteBtn.disabled = selectedCount === 0;
  }
  if (selectedCount > 0) {
    el.bulkAssignBtn.textContent = `Назначить ответственно (${selectedCount})`;
  } else {
    el.bulkAssignBtn.textContent = "Назначить ответственно";
  }

  if (el.selectAllTicketsBtn) {
    const allVisibleSelected = state.visibleTicketIds.length > 0
      && state.visibleTicketIds.every((id) => state.selectedTicketIds.has(id));
    el.selectAllTicketsBtn.classList.toggle("active", allVisibleSelected);
  }
}

async function assignTickets(ticketIds, assigneeId) {
  if (!ticketIds.length) return;
  setStatus("Назначаю ответственного...");
  await apiPost("/api/v1/tickets/assign", {
    ticket_ids: ticketIds,
    assignee_id: assigneeId || null,
  });
  for (const ticketId of ticketIds) {
    state.selectedTicketIds.delete(ticketId);
  }
  await Promise.all([loadTickets(), loadCounts({ silent: true })]);
  setStatus("Ответственный назначен");
}

function openAssignModal() {
  renderBulkAssigneeOptions();
  el.assignModal.classList.remove("hidden");
}

function closeAssignModal() {
  el.assignModal.classList.add("hidden");
}

function openBulkActionModal(kind) {
  const selected = state.selectedTicketIds.size;
  if (!selected) {
    showError("Выберите заявки");
    return;
  }
  pendingBulkAction = kind;
  const isSingle = selected === 1;
  const isDelete = kind === "delete";
  el.bulkActionModalTitle.textContent = isDelete ? "Удаление заявок" : "Завершение заявок";
  el.bulkActionModalText.textContent = isDelete
    ? (isSingle ? "Вы уверены, что хотите удалить заявку?" : "Вы уверены, что хотите удалить заявки?")
    : (isSingle ? "Вы уверены, что хотите закрыть заявку?" : "Вы уверены, что хотите закрыть заявки?");
  el.confirmBulkActionBtn.classList.toggle("danger", isDelete);
  el.confirmBulkActionBtn.classList.toggle("primary", !isDelete);
  el.bulkActionModal.classList.remove("hidden");
}

function closeBulkActionModal() {
  pendingBulkAction = null;
  el.bulkActionModal.classList.add("hidden");
}

async function runBulkAction() {
  const ticketIds = [...state.selectedTicketIds];
  if (!ticketIds.length || !pendingBulkAction) return;
  if (pendingBulkAction === "close") {
    setStatus("Принудительно закрываю заявки...");
    const result = await apiPost("/api/v1/tickets/admin/close", { ticket_ids: ticketIds });
    setStatus(`Закрыто: ${result.closed}, уже закрыты: ${result.already_closed}`);
  } else {
    setStatus("Удаляю заявки...");
    const result = await apiPost("/api/v1/tickets/admin/delete", { ticket_ids: ticketIds });
    if (result.file_delete_warnings?.length) {
      showError(`Удалено: ${result.deleted}\nПредупреждения:\n${result.file_delete_warnings.join("\n")}`);
    } else {
      setStatus(`Удалено: ${result.deleted}`);
    }
  }
  state.selectedTicketIds.clear();
  await Promise.all([loadTickets(), loadCounts({ silent: true })]);
}

function renderTable() {
  const filtered = sortTickets(applySearch(state.tickets));
  state.visibleTicketIds = filtered.map((t) => t.id);
  el.ticketsTableBody.innerHTML = "";
  el.ticketCount.textContent = String(state.total);
  el.pageCurrent.textContent = String(state.page);
  el.pageTotal.textContent = String(state.pages);
  el.prevPageBtn.disabled = state.page <= 1;
  el.nextPageBtn.disabled = state.page >= state.pages;
  el.emptyText.style.display = filtered.length ? "none" : "block";

  for (const t of filtered) {
    const tr = document.createElement("tr");
    const rowClasses = ["row-click", "ticket-row"];
    if (state.tab === "active" || t.status === "new") rowClasses.push("ticket-row-new");
    if (state.tab === "unassigned" || t.status === "unassigned") rowClasses.push("ticket-row-unassigned");
    if (state.tab === "active" && !t.seen_by_agent) rowClasses.push("ticket-row-fresh");
    if (t.is_frozen || state.tab === "frozen") rowClasses.push("ticket-row-frozen");
    if (t.status === "closed" || state.tab === "closed") rowClasses.push("ticket-row-closed");
    tr.className = rowClasses.join(" ");
    const selectCell = state.role === "admin"
      ? `<td class="ticket-check-cell"><input class="ticket-check" data-ticket-id="${escapeHtml(t.id)}" type="checkbox" ${state.selectedTicketIds.has(t.id) ? "checked" : ""} /></td>`
      : "";
    const assigneeCell = state.role === "admin"
      ? `<td><select class="row-assignee-select" data-ticket-id="${escapeHtml(t.id)}" data-selected-id="${escapeHtml(t.assignee_id || "")}">${assigneeOptionsHtml(t.assignee_id, t.special_assignee_name || t.assignee_name || "")}</select></td>`
      : "";
    tr.innerHTML = `
      ${selectCell}
      <td>${escapeHtml((t.subject || "") + intakeBadge(t))}</td>
      <td>${escapeHtml(t.client_name || "")}</td>
      ${assigneeCell}
      <td>${lastReplyText(t)}</td>
      <td>${fmtDate(t.created_at)}</td>
      <td>${ageText(t.created_at)}</td>
      <td>${fmtDate(t.updated_at)}</td>
    `;
    tr.addEventListener("click", () => {
      const publicId = t.ticket_number || t.id;
      window.location.href = `/tickets/${publicId}`;
    });
    el.ticketsTableBody.appendChild(tr);
  }

  el.ticketsTableBody.querySelectorAll(".ticket-check").forEach((checkbox) => {
    checkbox.addEventListener("click", (event) => {
      event.stopPropagation();
      const ticketId = checkbox.dataset.ticketId;
      if (checkbox.checked) {
        state.selectedTicketIds.add(ticketId);
      } else {
        state.selectedTicketIds.delete(ticketId);
      }
      updateBulkAssignUi();
    });
  });

  el.ticketsTableBody.querySelectorAll(".row-assignee-select").forEach((select) => {
    const selectedId = select.getAttribute("data-selected-id") || "";
    if (selectedId) {
      select.value = selectedId;
    }
    select.addEventListener("click", (event) => event.stopPropagation());
    select.addEventListener("change", async (event) => {
      event.stopPropagation();
      try {
        await assignTickets([select.dataset.ticketId], select.value || null);
      } catch (e) {
        showError(e.message);
        await Promise.all([loadTickets(), loadCounts({ silent: true })]);
      }
    });
  });
  updateBulkAssignUi();
}

function applyRoleUi() {
  const isAdmin = state.role === "admin";
  const isReadonly = state.role === "chiki_piki";
  el.adminOnly.forEach((node) => {
    node.classList.toggle("hidden", !isAdmin);
  });
  el.adminAssigneeCols.forEach((node) => {
    node.classList.toggle("hidden", !isAdmin);
  });
  el.adminSelectCols.forEach((node) => {
    node.classList.toggle("hidden", !isAdmin);
  });
  if (el.agentSelectWrap) {
    el.agentSelectWrap.classList.add("hidden");
  }
  if (el.roleControl) {
    el.roleControl.classList.add("hidden");
  }
  if (el.statusPanel) {
    el.statusPanel.classList.toggle("hidden", isReadonly || isAdmin);
  }
  if (el.adminAgentFilter) {
    el.adminAgentFilter.classList.toggle("hidden", !isAdmin);
  }
  const unassignedBtn = el.tabButtons.find((btn) => btn.dataset.tab === "unassigned");
  const specialBtn = el.tabButtons.find((btn) => btn.dataset.tab === "special");
  if (unassignedBtn) {
    unassignedBtn.classList.toggle("hidden", !(isAdmin || isReadonly));
  }
  if (specialBtn) {
    specialBtn.classList.toggle("hidden", !isAdmin);
  }
  if (!(isAdmin || isReadonly) && state.tab === "unassigned") {
    state.tab = "active";
    localStorage.setItem("hd_tab", state.tab);
  }
  if (!isAdmin && state.tab === "special") {
    state.tab = "active";
    localStorage.setItem("hd_tab", state.tab);
  }
  el.tabButtons.forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === state.tab);
  });
  if (!isAdmin) {
    state.selectedTicketIds.clear();
  }
  updateBulkAssignUi();
}

function renderAgentSelect() {
  el.agentSelect.innerHTML = "";
  const fallbackName = state.sessionContext?.real_user?.full_name || "Текущий пользователь";
  const option = document.createElement("option");
  option.value = state.userId || "";
  option.textContent = fallbackName;
  el.agentSelect.appendChild(option);
  el.agentSelect.value = option.value;
  if (!state.isRealAdmin || state.role !== "admin") return;
  el.agentSelect.innerHTML = "";
  for (const agent of state.roleUsers) {
    const option = document.createElement("option");
    option.value = agent.id;
    option.textContent = agent.full_name;
    el.agentSelect.appendChild(option);
  }
  if (!state.roleUsers.some((agent) => agent.id === state.userId) && state.roleUsers[0]) {
    state.userId = state.roleUsers[0].id;
    localStorage.setItem("hd_user_id", state.userId);
  }
  el.agentSelect.value = state.userId;
}

function renderAdminAgentFilter() {
  // Sidebar agent filter is rendered and managed by shared sidebar.js
}

async function loadRoleUsers() {
  if (!state.isRealAdmin || state.role !== "admin") {
    state.roleUsers = [];
    renderAgentSelect();
    return;
  }
  const users = await api(`/api/v1/users/by-role?role=${encodeURIComponent(state.role)}`);
  state.roleUsers = users || [];
  renderAgentSelect();
}

function syncSidebarState(sidebarState) {
  if (!sidebarState) return;
  const prevRole = state.role;
  const prevUserId = state.userId;
  const prevAssigneeKey = (state.adminAssigneeIds || []).slice().sort().join(",");
  state.role = sidebarState.role || state.role;
  state.userId = sidebarState.userId || state.userId;
  state.isRealAdmin = Boolean(sidebarState.isRealAdmin);
  state.supportAgents = Array.isArray(sidebarState.agents) ? sidebarState.agents : state.supportAgents;
  state.adminAssigneeIds = Array.isArray(sidebarState.adminAssigneeIds) ? sidebarState.adminAssigneeIds : state.adminAssigneeIds;
  state.counts = sidebarState.counts || state.counts;
  const fullName = (sidebarState.fullName || "").trim();
  const login = (sidebarState.login || "").trim();
  state.sessionContext = {
    real_user: { role: state.role, id: state.userId, full_name: fullName, login },
    effective_user: { role: state.role, id: state.userId, full_name: fullName, login },
    session_login_ts: Number(sidebarState.sessionLoginTs || 0),
  };
  const storedOpen = localStorage.getItem(COMPLETION_OPEN_STORAGE_KEY);
  state.completionOpen = storedOpen === String(state.sessionContext.session_login_ts || 0);
  if (el.roleSelect) el.roleSelect.value = state.role;
  renderAgentSelect();
  applyRoleUi();
  renderCompletionStats();
  const nextAssigneeKey = (state.adminAssigneeIds || []).slice().sort().join(",");
  if ((prevRole !== state.role || prevUserId !== state.userId || prevAssigneeKey !== nextAssigneeKey) && !state.isLoading) {
    resetToFirstPage();
    loadTickets({ silent: true });
  }
}

function renderAvailabilityStatus(status) {
  el.statusButtons.forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.availabilityStatus === status);
  });
}

async function loadMe() {
  try {
    const me = await api("/api/v1/users/me");
    renderAvailabilityStatus(me.availability_status);
    if (el.agentAvatar) {
      const name = me.full_name || "A";
      el.agentAvatar.textContent = name.trim().slice(0, 1).toUpperCase();
      el.agentAvatar.title = name;
    }
    if (el.sidebarUserDisplayName) {
      const displayName = (me.full_name || "").trim() || me.login || "";
      el.sidebarUserDisplayName.textContent = displayName || "Пользователь";
    }
  } catch (e) {
    showError(`Ошибка статуса: ${e.message}`);
  }
}

async function updateAvailabilityStatus(status) {
  try {
    renderAvailabilityStatus(status);
    const me = await apiWrite("/api/v1/users/me/status", { availability_status: status });
    renderAvailabilityStatus(me.availability_status);
    setStatus("Статус обновлён");
  } catch (e) {
    showError(`Ошибка статуса: ${e.message}`);
    await loadMe();
  }
}

async function loadTickets(options = {}) {
  if (state.isLoading) return;
  state.isLoading = true;
  const silent = Boolean(options.silent);
  if (!silent) setStatus("Загружаю список...");
  const base = endpointMap[state.tab];
  const queryParams = new URLSearchParams({
    sort: "created_at",
    order: "desc",
    page: String(state.page),
    limit: String(state.limit),
  });
  if (state.role === "admin" && state.tab !== "unassigned") {
    if (state.tab === "special") {
      // Special queue is global for admins and does not depend on selected agents.
    } else {
      state.adminAssigneeIds.forEach((id) => queryParams.append("assignee_ids", id));
    }
  }
  try {
    const payload = await api(`${base}?${queryParams.toString()}`);

    state.tickets = payload.items || [];
    state.total = payload.total || 0;
    state.pages = payload.pages || 1;
    state.page = payload.page || 1;

    renderTable();
    setStatus("Готово");
  } catch (e) {
    if (!silent) setStatus(`Ошибка: ${e.message}`);
  } finally {
    state.isLoading = false;
  }
}

async function loadCounts(options = {}) {
  const silent = Boolean(options.silent);
  try {
    await window.HelpdeskSidebar.refreshCounts();
    const sidebarState = window.HelpdeskSidebar.getState();
    state.counts = sidebarState.counts || state.counts;
  } catch (e) {
    if (!silent) setStatus(`Ошибка счётчиков: ${e.message}`);
  }
}

function resetToFirstPage() {
  state.page = 1;
}

function init() {
  if (!endpointMap[state.tab]) state.tab = "active";
  applyRoleUi();
  localStorage.setItem("hd_tab", state.tab);

  el.roleSelect.value = state.role;
  el.agentSelect.value = state.userId;
  refreshSortHeaderUi();
  renderTabCounts();

  el.tabButtons.forEach((btn) => {
    if (btn.dataset.tab === state.tab) btn.classList.add("active");
    else btn.classList.remove("active");

    btn.addEventListener("click", async () => {
      if (btn.classList.contains("hidden")) return;
      el.tabButtons.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.tab = btn.dataset.tab;
      localStorage.setItem("hd_tab", state.tab);
      state.selectedTicketIds.clear();
      resetToFirstPage();
      await Promise.all([loadTickets(), loadCounts()]);
    });
  });

  el.sortButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const key = btn.dataset.sortKey;
      if (!key) return;
      if (state.tableSortKey === key) {
        state.tableSortDir = state.tableSortDir === "asc" ? "desc" : "asc";
      } else {
        state.tableSortKey = key;
        state.tableSortDir = ["subject", "client_name", "assignee_name"].includes(key) ? "asc" : "desc";
      }
      localStorage.setItem("hd_table_sort_key", state.tableSortKey);
      localStorage.setItem("hd_table_sort_dir", state.tableSortDir);
      refreshSortHeaderUi();
      renderTable();
    });
  });

  el.roleSelect.addEventListener("change", async () => {
    // TODO(remove-before-production): admin preview mode is temporarily disabled.
    return;
  });

  el.agentSelect.addEventListener("change", async () => {
    // TODO(remove-before-production): admin preview mode is temporarily disabled.
    return;
  });

  el.statusButtons.forEach((btn) => {
    btn.addEventListener("click", async () => {
      await updateAvailabilityStatus(btn.dataset.availabilityStatus);
    });
  });

  // Admin agent filter toggle is handled by shared sidebar.js

  el.selectAllTicketsBtn.addEventListener("click", () => {
    const allVisibleSelected = state.visibleTicketIds.length > 0
      && state.visibleTicketIds.every((id) => state.selectedTicketIds.has(id));
    for (const ticketId of state.visibleTicketIds) {
      if (allVisibleSelected) state.selectedTicketIds.delete(ticketId);
      else state.selectedTicketIds.add(ticketId);
    }
    renderTable();
  });

  el.bulkAssignBtn.addEventListener("click", openAssignModal);
  if (el.bulkCloseBtn) {
    el.bulkCloseBtn.addEventListener("click", () => openBulkActionModal("close"));
  }
  if (el.bulkDeleteBtn) {
    el.bulkDeleteBtn.addEventListener("click", () => openBulkActionModal("delete"));
  }
  el.closeAssignModalBtn.addEventListener("click", closeAssignModal);
  closeOnBackdropClick(el.assignModal, closeAssignModal);
  el.confirmBulkAssignBtn.addEventListener("click", async () => {
    try {
      const ticketIds = [...state.selectedTicketIds];
      await assignTickets(ticketIds, el.bulkAssigneeSelect.value || null);
      closeAssignModal();
    } catch (e) {
      showError(e.message);
    }
  });
  if (el.closeBulkActionModalBtn) el.closeBulkActionModalBtn.addEventListener("click", closeBulkActionModal);
  if (el.cancelBulkActionBtn) el.cancelBulkActionBtn.addEventListener("click", closeBulkActionModal);
  if (el.confirmBulkActionBtn) {
    el.confirmBulkActionBtn.addEventListener("click", async () => {
      try {
        await runBulkAction();
        closeBulkActionModal();
      } catch (e) {
        showError(e.message);
      }
    });
  }
  if (el.bulkActionModal) closeOnBackdropClick(el.bulkActionModal, closeBulkActionModal);

  el.closeErrorModalBtn.addEventListener("click", closeError);
  el.okErrorBtn.addEventListener("click", closeError);
  closeOnBackdropClick(el.errorModal, closeError);
  el.copyErrorBtn.addEventListener("click", async () => {
    el.errorText.select();
    try {
      await navigator.clipboard.writeText(el.errorText.value);
    } catch {
      document.execCommand("copy");
    }
    setStatus("Текст ошибки скопирован");
  });

  el.searchInput.addEventListener("input", renderTable);

  el.prevPageBtn.addEventListener("click", async () => {
    if (state.page <= 1) return;
    state.page -= 1;
    await loadTickets();
  });

  el.nextPageBtn.addEventListener("click", async () => {
    if (state.page >= state.pages) return;
    state.page += 1;
    await loadTickets();
  });

  window.setInterval(() => {
    if (document.hidden) return;
    loadTickets({ silent: true });
    loadCounts({ silent: true });
    loadCompletionStats({ silent: true });
  }, 5000);

  if (el.completionToggleBtn) {
    el.completionToggleBtn.addEventListener("click", () => {
      state.completionOpen = !state.completionOpen;
      const loginTs = Number(state.sessionContext?.session_login_ts || 0);
      if (state.completionOpen && loginTs) {
        localStorage.setItem(COMPLETION_OPEN_STORAGE_KEY, String(loginTs));
      } else {
        localStorage.removeItem(COMPLETION_OPEN_STORAGE_KEY);
      }
      renderCompletionStats();
    });
  }
}

(async function run() {
  await window.HelpdeskSidebar.init();
  syncSidebarState(window.HelpdeskSidebar.getState());
  window.HelpdeskSidebar.onChange(syncSidebarState);
  init();
  await loadMe();
  await Promise.all([loadTickets(), loadCounts(), loadCompletionStats()]);
})();
