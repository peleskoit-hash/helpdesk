(function () {
function initTicketsListPage() {
const TAB_COUNTS_STORAGE_KEY = "hd_tab_counts";
const COMPLETION_OPEN_STORAGE_KEY = "hd_completion_sidebar_open";
const DEFAULT_TAB = "active";
const TAB_KEYS = new Set(["active", "unassigned", "special", "frozen", "closed"]);

function parsePage(value) {
  const parsed = Number.parseInt(String(value || ""), 10);
  return Number.isFinite(parsed) && parsed >= 1 ? parsed : 1;
}

function readListStateFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const rawTab = params.get("tab");
  const rawPage = params.get("page");
  const rawQ = params.get("q");
  const tab = TAB_KEYS.has(rawTab) ? rawTab : null;
  const page = parsePage(rawPage);
  const q = (rawQ || "").trim();
  return { tab, page, q, hasPageParam: rawPage !== null };
}

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

const urlState = readListStateFromUrl();
let pollTimer = null;
let unsubscribeSidebar = null;
let ownsPopState = false;
let pendingLoadOptions = null;
let pageEventController = null;
const state = {
  tab: urlState.tab || localStorage.getItem("hd_tab") || DEFAULT_TAB,
  appliedSearchQuery: urlState.q,
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
  page: urlState.page,
  pages: 1,
  total: 0,
  limit: 20,
  isLoading: false,
  counts: readStoredTabCounts(),
  sessionContext: null,
  completionStats: null,
  completionOpen: false,
  hasPageFromUrl: Boolean(urlState.hasPageParam),
};

const endpointMap = {
  active: "/api/v1/tickets/active",
  unassigned: "/api/v1/tickets/unassigned",
  special: "/api/v1/tickets/special",
  frozen: "/api/v1/tickets/frozen",
  closed: "/api/v1/tickets/closed",
};

function tabKeyFromNode(node) {
  const direct = node?.dataset?.tab || "";
  if (TAB_KEYS.has(direct)) return direct;
  const href = node?.getAttribute?.("href") || "";
  if (!href) return "";
  try {
    const url = new URL(href, window.location.origin);
    const tab = url.searchParams.get("tab") || "";
    return TAB_KEYS.has(tab) ? tab : "";
  } catch {
    return "";
  }
}

function syncListUrl(mode = "replace") {
  const nextTab = TAB_KEYS.has(state.tab) ? state.tab : DEFAULT_TAB;
  const nextPage = parsePage(state.page);
  const nextQ = (state.appliedSearchQuery || "").trim();
  const url = new URL(window.location.href);
  url.searchParams.set("tab", nextTab);
  url.searchParams.set("page", String(nextPage));
  if (nextQ) url.searchParams.set("q", nextQ);
  else url.searchParams.delete("q");
  const next = `${url.pathname}?${url.searchParams.toString()}${url.hash}`;
  if (mode === "replace") {
    window.history.replaceState({ tab: nextTab, page: nextPage, q: nextQ }, "", next);
  } else {
    window.history.pushState({ tab: nextTab, page: nextPage, q: nextQ }, "", next);
  }
}

const el = {
  tabButtons: [...document.querySelectorAll(".tab-btn")],
  sortButtons: [...document.querySelectorAll(".table-sort-btn")],
  roleSelect: document.getElementById("roleSelect"),
  roleControl: document.getElementById("roleControl"),
  agentSelect: document.getElementById("agentSelect"),
  agentSelectWrap: document.getElementById("agentSelectWrap"),
  searchInput: document.getElementById("searchInput"),
  searchClearBtn: document.getElementById("searchClearBtn"),
  ticketsTableBody: document.getElementById("ticketsTableBody"),
  ticketCount: document.getElementById("ticketCount"),
  prevPageBtn: document.getElementById("prevPageBtn"),
  nextPageBtn: document.getElementById("nextPageBtn"),
  paginationPages: document.getElementById("paginationPages"),
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

function normalizeLoadOptions(options = {}) {
  return {
    silent: Boolean(options.silent),
    historyMode: options.historyMode || null,
  };
}

function mergePendingLoadOptions(current, next) {
  if (!current) return next;
  return {
    silent: current.silent && next.silent,
    historyMode: next.historyMode || current.historyMode || null,
  };
}

function updateSearchUi() {
  const draft = (el.searchInput?.value || "").trim();
  const hasDraft = draft.length > 0;
  const hasApplied = (state.appliedSearchQuery || "").trim().length > 0;
  if (el.searchClearBtn) {
    el.searchClearBtn.classList.toggle("hidden", !hasDraft);
  }
  if (el.searchInput) {
    el.searchInput.classList.toggle("search-active", hasApplied);
  }
}

async function applySearchFromInput() {
  const nextQuery = (el.searchInput.value || "").trim();
  if (nextQuery === state.appliedSearchQuery) {
    updateSearchUi();
    return;
  }
  state.appliedSearchQuery = nextQuery;
  resetToFirstPage();
  updateSearchUi();
  await loadTickets({ historyMode: "push" });
}

async function clearSearchQuery() {
  const hadDraft = Boolean((el.searchInput.value || "").trim());
  const hadApplied = Boolean(state.appliedSearchQuery);
  el.searchInput.value = "";
  state.appliedSearchQuery = "";
  updateSearchUi();
  if (hadDraft || hadApplied) {
    resetToFirstPage();
    await loadTickets({ historyMode: "push" });
  }
}

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
  if (totalDays >= 7) return `${totalDays}\u0434`;
  if (totalDays >= 1) return `${totalDays}\u0434 ${totalHours % 24}\u0447`;
  if (totalHours >= 1) return `${totalHours}\u0447 ${totalMinutes % 60}\u043c`;
  return `${totalMinutes}\u043c`;
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

  if (el.completionSummaryArrow) el.completionSummaryArrow.textContent = state.completionOpen ? "\u25B2" : "\u25BC";
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

function dateMs(value) {
  if (!value) return 0;
  const ms = new Date(value).getTime();
  return Number.isFinite(ms) ? ms : 0;
}

function sortValue(ticket, key) {
  switch (key) {
    case "subject":
      return String(ticket.subject || "").toLocaleLowerCase("ru");
    case "requester_display_name":
      return String(ticket.requester_display_name || "").toLocaleLowerCase("ru");
    case "assignee_name":
      return String(ticket.assignee_name || "").toLocaleLowerCase("ru");
    case "last_reply_at":
      return dateMs(ticket.last_reply_at);
    case "created_at":
      return dateMs(ticket.created_at);
    case "age_created":
      return dateMs(ticket.created_at);
    default:
      return "";
  }
}

function sortTickets(items) {
  // UI-sort is intentionally local for the currently loaded page.
  // Backend list endpoints remain the source for global ordering/pagination.
  const key = state.tableSortKey;
  const dir = state.tableSortDir === "asc" ? 1 : -1;
  const textKeys = new Set(["subject", "requester_display_name", "assignee_name"]);
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
    const base = btn.textContent.replace(/\s[\u25B2\u25BC?]$/, "");
    btn.textContent = base;
    btn.classList.remove("active");
    if (btn.dataset.sortKey === state.tableSortKey) {
      btn.classList.add("active");
      btn.textContent = `${base} ${state.tableSortDir === "asc" ? "\u25B2" : "\u25BC"}`;
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
  if (ticket.is_special) return " [\u041e\u0441\u043e\u0431\u0430\u044f]";
  if (ticket.workflow_type === "auto") return " [\u0430\u0432\u0442\u043e]";
  if (ticket.intake_state === "collecting") return " [\u0441\u0431\u043e\u0440 \u0434\u0430\u043d\u043d\u044b\u0445]";
  if (ticket.intake_state === "cancelled") return " [\u043e\u0442\u043c\u0435\u043d\u0435\u043d\u043e]";
  return "";
}

function assigneeOptionsHtml(selectedId, selectedName = "") {
  const options = [`<option value="" ${selectedId ? "" : "selected"}>Нераспределён</option>`];
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
    el.bulkAssignBtn.textContent = `Назначить ответственного (${selectedCount})`;
  } else {
    el.bulkAssignBtn.textContent = "Назначить ответственного";
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
      showError(`Удалено: ${result.deleted}
Предупреждения:
${result.file_delete_warnings.join("\n")}`);
    } else {
      setStatus(`Удалено: ${result.deleted}`);
    }
  }
  state.selectedTicketIds.clear();
  await Promise.all([loadTickets(), loadCounts({ silent: true })]);
}

function renderTable() {
  const filtered = sortTickets(state.tickets);
  state.visibleTicketIds = filtered.map((t) => t.id);
  el.ticketsTableBody.innerHTML = "";
  el.ticketCount.textContent = String(state.total);
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
    const currentListUrl = `${window.location.pathname}${window.location.search}`;
    const publicId = t.ticket_number || t.id;
    const ticketUrl = new URL(`/tickets/${publicId}`, window.location.origin);
    ticketUrl.searchParams.set("tab", state.tab || DEFAULT_TAB);
    ticketUrl.searchParams.set("page", String(parsePage(state.page)));
    const appliedQuery = (state.appliedSearchQuery || "").trim();
    if (appliedQuery) ticketUrl.searchParams.set("q", appliedQuery);
    ticketUrl.searchParams.set("return_url", currentListUrl);
    const ticketHref = `${ticketUrl.pathname}${ticketUrl.search}`;
    const linkedCell = (className, textValue) => (
      `<td class="${className} ticket-linked-cell"><a class="ticket-row-link" href="${escapeHtml(ticketHref)}">${escapeHtml(textValue)}</a></td>`
    );
    const selectCell = state.role === "admin"
      ? `<td class="ticket-check-cell"><input class="ticket-check" data-ticket-id="${escapeHtml(t.id)}" type="checkbox" ${state.selectedTicketIds.has(t.id) ? "checked" : ""} /></td>`
      : "";
    const assigneeCell = state.role === "admin"
      ? `<td class="ticket-assignee-col"><select class="row-assignee-select" data-ticket-id="${escapeHtml(t.id)}" data-selected-id="${escapeHtml(t.assignee_id || "")}">${assigneeOptionsHtml(t.assignee_id, t.special_assignee_name || t.assignee_name || "")}</select></td>`
      : (state.role === "chiki_piki"
        ? linkedCell("ticket-assignee-col", t.special_assignee_name || t.assignee_name || "Нераспределён")
        : "");
    tr.innerHTML = `
      ${selectCell}
      ${linkedCell("ticket-subject-col", (t.subject || "") + intakeBadge(t))}
      ${linkedCell("ticket-client-col", t.requester_display_name || t.client_name || "")}
      ${assigneeCell}
      ${linkedCell("ticket-age-col", ageText(t.created_at))}
      ${linkedCell("ticket-created-col", fmtDate(t.created_at))}
      ${linkedCell("ticket-last-reply-col", lastReplyText(t))}
    `;
    el.ticketsTableBody.appendChild(tr);
  }

  el.ticketsTableBody.querySelectorAll(".ticket-row-link").forEach((link) => {
    const persistReturnUrl = () => {
      const currentListUrl = `${window.location.pathname}${window.location.search}`;
      sessionStorage.setItem("ticketListReturnUrl", currentListUrl);
    };
    link.addEventListener("click", () => {
      persistReturnUrl();
    });
    link.addEventListener("auxclick", (event) => {
      if (event.button === 1) persistReturnUrl();
    });
  });

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
  renderPagination();
}

function buildPaginationModel(totalPages, currentPage) {
  if (totalPages <= 1) return [1];
  if (totalPages <= 7) return Array.from({ length: totalPages }, (_, index) => index + 1);
  const left = Math.max(2, currentPage - 1);
  const right = Math.min(totalPages - 1, currentPage + 1);
  const model = [1];
  if (left > 2) model.push("\u2026");
  for (let page = left; page <= right; page += 1) {
    model.push(page);
  }
  if (right < totalPages - 1) model.push("\u2026");
  model.push(totalPages);
  return model;
}

async function goToPage(page) {
  const nextPage = parsePage(page);
  if (nextPage === state.page) return;
  state.page = nextPage;
  await loadTickets({ historyMode: "push" });
}

function renderPagination() {
  if (!el.paginationPages) return;
  el.paginationPages.innerHTML = "";
  const totalPages = Math.max(1, Number(state.pages || 1));
  const currentPage = parsePage(state.page);
  const model = buildPaginationModel(totalPages, currentPage);
  for (const token of model) {
    if (token === "\u2026") {
      const span = document.createElement("span");
      span.className = "table-page-ellipsis";
      span.textContent = "\u2026";
      el.paginationPages.appendChild(span);
      continue;
    }
    const page = Number(token);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "table-page-number";
    if (page === currentPage) {
      button.classList.add("active");
      button.setAttribute("aria-current", "page");
    }
    button.textContent = String(page);
    button.disabled = page === currentPage;
    button.addEventListener("click", async () => {
      await goToPage(page);
    });
    el.paginationPages.appendChild(button);
  }
}

function applyRoleUi() {
  const isAdmin = state.role === "admin";
  const isReadonly = state.role === "chiki_piki";
  el.adminOnly.forEach((node) => {
    node.classList.toggle("hidden", !isAdmin);
  });
  el.adminAssigneeCols.forEach((node) => {
    node.classList.toggle("hidden", !(isAdmin || isReadonly));
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
  const unassignedBtn = el.tabButtons.find((btn) => tabKeyFromNode(btn) === "unassigned");
  const specialBtn = el.tabButtons.find((btn) => tabKeyFromNode(btn) === "special");
  if (unassignedBtn) {
    unassignedBtn.classList.toggle("hidden", !(isAdmin || isReadonly));
  }
  if (specialBtn) {
    specialBtn.classList.toggle("hidden", !isAdmin);
  }
  if (!(isAdmin || isReadonly) && state.tab === "unassigned") {
    state.tab = DEFAULT_TAB;
    resetToFirstPage();
    localStorage.setItem("hd_tab", state.tab);
    syncListUrl("replace");
  }
  if (!isAdmin && state.tab === "special") {
    state.tab = DEFAULT_TAB;
    resetToFirstPage();
    localStorage.setItem("hd_tab", state.tab);
    syncListUrl("replace");
  }
  el.tabButtons.forEach((btn) => {
    btn.classList.toggle("active", tabKeyFromNode(btn) === state.tab);
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
  const isInitialSidebarHydration = !prevUserId && Boolean(state.userId);
  if (isInitialSidebarHydration && state.hasPageFromUrl) {
    return;
  }
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
  const normalizedOptions = normalizeLoadOptions(options);
  if (state.isLoading) {
    pendingLoadOptions = mergePendingLoadOptions(pendingLoadOptions, normalizedOptions);
    return;
  }
  state.isLoading = true;
  const silent = normalizedOptions.silent;
  const historyMode = normalizedOptions.historyMode;
  if (!silent) setStatus("Загружаю список...");
  const base = endpointMap[state.tab];
  const queryParams = new URLSearchParams({
    sort: "created_at",
    order: "desc",
    page: String(state.page),
    limit: String(state.limit),
  });
  const searchQuery = (state.appliedSearchQuery || "").trim();
  if (searchQuery) {
    queryParams.set("query", searchQuery);
  }
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
    if (historyMode) {
      syncListUrl(historyMode);
    }

    renderTable();
    setStatus("Готово");
  } catch (e) {
    if (!silent) setStatus(`Ошибка: ${e.message}`);
  } finally {
    state.isLoading = false;
    if (pendingLoadOptions) {
      const nextOptions = pendingLoadOptions;
      pendingLoadOptions = null;
      await loadTickets(nextOptions);
    }
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

async function handleListUrlChange() {
  const next = readListStateFromUrl();
  const tabFromUrl = next.tab || DEFAULT_TAB;
  const pageFromUrl = parsePage(next.page);
  const qFromUrl = (next.q || "").trim();
  const changed = state.tab !== tabFromUrl || state.page !== pageFromUrl || state.appliedSearchQuery !== qFromUrl;
  if (!changed) return;
  state.tab = tabFromUrl;
  state.page = pageFromUrl;
  state.appliedSearchQuery = qFromUrl;
  localStorage.setItem("hd_tab", state.tab);
  el.searchInput.value = state.appliedSearchQuery;
  updateSearchUi();
  applyRoleUi();
  state.selectedTicketIds.clear();
  await Promise.all([loadTickets({ silent: true, historyMode: "replace" }), loadCounts({ silent: true })]);
}

function init() {
  pageEventController?.abort();
  pageEventController = new AbortController();
  const pageSignal = pageEventController.signal;
  if (!endpointMap[state.tab]) state.tab = DEFAULT_TAB;
  applyRoleUi();
  localStorage.setItem("hd_tab", state.tab);
  state.page = parsePage(state.page);
  syncListUrl("replace");

  el.roleSelect.value = state.role;
  el.agentSelect.value = state.userId;
  el.searchInput.value = state.appliedSearchQuery;
  updateSearchUi();
  refreshSortHeaderUi();
  renderTabCounts();

  el.tabButtons.forEach((btn) => {
    if (tabKeyFromNode(btn) === state.tab) btn.classList.add("active");
    else btn.classList.remove("active");

    btn.addEventListener("click", async (event) => {
      event.preventDefault();
      const nextTab = tabKeyFromNode(btn);
      if (!nextTab || btn.classList.contains("hidden")) return;
      el.tabButtons.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.tab = tabKeyFromNode(btn) || DEFAULT_TAB;
      localStorage.setItem("hd_tab", state.tab);
      state.selectedTicketIds.clear();
      resetToFirstPage();
      await Promise.all([loadTickets({ historyMode: "push" }), loadCounts()]);
    }, { signal: pageSignal });
  });

  el.sortButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const key = btn.dataset.sortKey;
      if (!key) return;
      if (state.tableSortKey === key) {
        state.tableSortDir = state.tableSortDir === "asc" ? "desc" : "asc";
      } else {
        state.tableSortKey = key;
        state.tableSortDir = ["subject", "requester_display_name", "assignee_name"].includes(key) ? "asc" : "desc";
      }
      localStorage.setItem("hd_table_sort_key", state.tableSortKey);
      localStorage.setItem("hd_table_sort_dir", state.tableSortDir);
      refreshSortHeaderUi();
      renderTable();
    }, { signal: pageSignal });
  });

  el.roleSelect.addEventListener("change", async () => {
    // TODO(remove-before-production): admin preview mode is temporarily disabled.
    return;
  }, { signal: pageSignal });

  el.agentSelect.addEventListener("change", async () => {
    // TODO(remove-before-production): admin preview mode is temporarily disabled.
    return;
  }, { signal: pageSignal });

  el.statusButtons.forEach((btn) => {
    btn.addEventListener("click", async () => {
      await updateAvailabilityStatus(btn.dataset.availabilityStatus);
    }, { signal: pageSignal });
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
  }, { signal: pageSignal });

  el.bulkAssignBtn.addEventListener("click", openAssignModal, { signal: pageSignal });
  if (el.bulkCloseBtn) {
    el.bulkCloseBtn.addEventListener("click", () => openBulkActionModal("close"), { signal: pageSignal });
  }
  if (el.bulkDeleteBtn) {
    el.bulkDeleteBtn.addEventListener("click", () => openBulkActionModal("delete"), { signal: pageSignal });
  }
  el.closeAssignModalBtn.addEventListener("click", closeAssignModal, { signal: pageSignal });
  closeOnBackdropClick(el.assignModal, closeAssignModal);
  el.confirmBulkAssignBtn.addEventListener("click", async () => {
    try {
      const ticketIds = [...state.selectedTicketIds];
      await assignTickets(ticketIds, el.bulkAssigneeSelect.value || null);
      closeAssignModal();
    } catch (e) {
      showError(e.message);
    }
  }, { signal: pageSignal });
  if (el.closeBulkActionModalBtn) el.closeBulkActionModalBtn.addEventListener("click", closeBulkActionModal, { signal: pageSignal });
  if (el.cancelBulkActionBtn) el.cancelBulkActionBtn.addEventListener("click", closeBulkActionModal, { signal: pageSignal });
  if (el.confirmBulkActionBtn) {
    el.confirmBulkActionBtn.addEventListener("click", async () => {
      try {
        await runBulkAction();
        closeBulkActionModal();
      } catch (e) {
        showError(e.message);
      }
    }, { signal: pageSignal });
  }
  if (el.bulkActionModal) closeOnBackdropClick(el.bulkActionModal, closeBulkActionModal);

  el.closeErrorModalBtn.addEventListener("click", closeError, { signal: pageSignal });
  el.okErrorBtn.addEventListener("click", closeError, { signal: pageSignal });
  closeOnBackdropClick(el.errorModal, closeError);
  el.copyErrorBtn.addEventListener("click", async () => {
    el.errorText.select();
    try {
      await navigator.clipboard.writeText(el.errorText.value);
    } catch {
      document.execCommand("copy");
    }
    setStatus("Текст ошибки скопирован");
  }, { signal: pageSignal });

  el.searchInput.addEventListener("input", () => {
    updateSearchUi();
  }, { signal: pageSignal });
  el.searchInput.addEventListener("keydown", async (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    await applySearchFromInput();
  }, { signal: pageSignal });
  if (el.searchClearBtn) {
    el.searchClearBtn.addEventListener("click", async () => {
      await clearSearchQuery();
    }, { signal: pageSignal });
  }

  el.prevPageBtn.addEventListener("click", async () => {
    if (state.page <= 1) return;
    state.page -= 1;
    await loadTickets({ historyMode: "push" });
  }, { signal: pageSignal });

  el.nextPageBtn.addEventListener("click", async () => {
    if (state.page >= state.pages) return;
    state.page += 1;
    await loadTickets({ historyMode: "push" });
  }, { signal: pageSignal });

  pollTimer = window.setInterval(() => {
    if (document.hidden) return;
    loadTickets({ silent: true });
    loadCounts({ silent: true });
    loadCompletionStats({ silent: true });
  }, 5000);

  if (!window.HelpdeskAppShell?.getCurrentPage) {
    ownsPopState = true;
    window.addEventListener("popstate", handleListUrlChange);
  }

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
    }, { signal: pageSignal });
  }
}

async function runTicketsListPage() {
  await window.HelpdeskSidebar.init();
  syncSidebarState(window.HelpdeskSidebar.getState());
  unsubscribeSidebar = window.HelpdeskSidebar.onChange(syncSidebarState);
  init();
  await loadMe();
  await Promise.all([loadTickets(), loadCounts(), loadCompletionStats()]);
}

runTicketsListPage().catch((error) => {
  const message = error?.message || "Ошибка загрузки списка заявок";
  setStatus(`Ошибка: ${message}`);
  showError(message);
});

return {
  destroy() {
    if (pollTimer) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
    if (ownsPopState) {
      window.removeEventListener("popstate", handleListUrlChange);
      ownsPopState = false;
    }
    if (typeof unsubscribeSidebar === "function") {
      unsubscribeSidebar();
      unsubscribeSidebar = null;
    }
    if (pageEventController) {
      pageEventController.abort();
      pageEventController = null;
    }
    pendingLoadOptions = null;
  },
  onUrlChange: handleListUrlChange,
};

}

if (window.HelpdeskAppShell?.registerPage) {
  window.HelpdeskAppShell.registerPage("tickets-list", initTicketsListPage);
} else {
  initTicketsListPage();
}
})();
