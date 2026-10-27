(function () {
function initTicketsListPage() {
const TAB_COUNTS_STORAGE_KEY = "hd_tab_counts";
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
let colorLegendHideTimer = null;
let columnAutoResizeObserver = null;
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
  hasPageFromUrl: Boolean(urlState.hasPageParam),
  colorEnabled: true,
  taskPanelEnabled: false,
  taskPanelStatus: "loading",
  taskPanelItems: [],
  taskPanelDropdownOpen: false,
  ticketListColumns: [],
  ticketListAutoWidth: false,
  draftTicketListAutoWidth: false,
  columnResizeAutoWidth: false,
  columnAutoCalculation: null,
  draftTicketListColumns: null,
  columnDraftPreviewActive: false,
  columnResizeActive: false,
  columnResizeColumns: null,
  columnResizePreviousDraft: null,
  columnResizeDrag: null,
  columnReorderDrag: null,
};

const endpointMap = {
  active: "/api/v1/tickets/active",
  unassigned: "/api/v1/tickets/unassigned",
  special: "/api/v1/tickets/special",
  frozen: "/api/v1/tickets/frozen",
  closed: "/api/v1/tickets/closed",
};

const DEFAULT_TICKET_COLUMNS = [
  { key: "subject", width: 0 },
  { key: "requester", width: 150 },
  { key: "assignee", width: 170 },
  { key: "age", width: 120 },
  { key: "created_at", width: 140 },
  { key: "last_reply", width: 150 },
];

const TICKET_COLUMN_DEFS = {
  subject: { title: "Тема", className: "ticket-subject-col", sortKey: "subject", mandatory: true, defaultWidth: 0 },
  requester: { title: "Заявка от", className: "ticket-client-col", sortKey: "requester_display_name", defaultWidth: 150 },
  assignee: { title: "Исполнитель", className: "ticket-assignee-col", sortKey: "assignee_name", defaultWidth: 170 },
  department: { title: "Подразделение", className: "ticket-department-col", sortKey: "requester_department", defaultWidth: 160 },
  type: { title: "Тип", className: "ticket-type-col", sortKey: "ticket_type", defaultWidth: 130 },
  location: { title: "Расположение", className: "ticket-location-col", sortKey: "requester_location_type", defaultWidth: 130 },
  age: { title: "С создания", className: "ticket-age-col", sortKey: "age_created", defaultWidth: 120 },
  created_at: { title: "Дата создания", className: "ticket-created-col", sortKey: "created_at", defaultWidth: 140 },
  last_reply: { title: "Последний ответ", className: "ticket-last-reply-col", sortKey: "last_reply_at", defaultWidth: 150 },
};

const TICKET_COLUMN_KEYS = Object.keys(TICKET_COLUMN_DEFS);

function ticketRowColorClass(t, tab = "") {
  if (t?.status === "closed" || tab === "closed") return "ticket-color-closed";
  if (t?.is_frozen || tab === "frozen") return "ticket-color-frozen";
  if (t?.workflow_type === "ticket") return "ticket-color-request";
  if (t?.task_kind === "group_parent" || t?.task_kind === "group_child") return "ticket-color-group-task";
  if (t?.is_internal_task) return "ticket-color-internal-task";
  return "ticket-color-task";
}

function ticketTypeInfo(t, tab = "") {
  if (t?.status === "closed" || tab === "closed") return { label: "Закрытая", className: "closed" };
  if (t?.is_frozen || tab === "frozen") return { label: "Замороженная", className: "frozen" };
  if (t?.workflow_type === "ticket") return { label: "Заявка", className: "request" };
  if (t?.task_kind === "group_parent" || t?.task_kind === "group_child") return { label: "Увольнение", className: "group-task" };
  if (t?.is_internal_task) return { label: "Внутренняя", className: "internal-task" };
  return { label: "Задача", className: "task" };
}

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

function collectElements() {
  const root = document.getElementById("appContent");
  const byId = (id) => root?.querySelector(`#${id}`) || null;
  return {
    tabButtons: [...document.querySelectorAll(".tab-btn")],
    sortButtons: [...(root?.querySelectorAll(".table-sort-btn") || [])],
    roleSelect: byId("roleSelect"),
    roleControl: byId("roleControl"),
    agentSelect: byId("agentSelect"),
    agentSelectWrap: byId("agentSelectWrap"),
    searchInput: byId("searchInput"),
    searchClearBtn: byId("searchClearBtn"),
    colorEnabledInput: byId("colorEnabledInput"),
    colorToggleLabel: byId("colorToggleLabel"),
    colorToggleText: byId("colorToggleText"),
    colorLegend: byId("colorLegend"),
    taskPanel: byId("taskPanel"),
    taskPanelSlots: byId("taskPanelSlots"),
    taskPanelMoreBtn: byId("taskPanelMoreBtn"),
    taskPanelDropdown: byId("taskPanelDropdown"),
    taskPanelToggle: root?.querySelector(".columns-task-panel-toggle") || null,
    columnAutoWidthControl: byId("columnAutoWidthControl"),
    columnAutoWidthInput: byId("columnAutoWidthInput"),
    ticketsHeaderRow: byId("ticketsHeaderRow"),
    ticketsTable: byId("ticketsTable"),
    ticketsTableBody: byId("ticketsTableBody"),
    ticketCount: byId("ticketCount"),
    prevPageBtn: byId("prevPageBtn"),
    nextPageBtn: byId("nextPageBtn"),
    paginationPages: byId("paginationPages"),
    emptyText: byId("emptyText"),
    bulkAdminActions: byId("bulkAdminActions"),
    bulkSkipClientNotification: byId("bulkSkipClientNotification"),
    bulkAssignBtn: byId("bulkAssignBtn"),
    bulkCloseBtn: byId("bulkCloseBtn"),
    bulkFreezeBtn: byId("bulkFreezeBtn"),
    bulkUnfreezeBtn: byId("bulkUnfreezeBtn"),
    bulkDeleteBtn: byId("bulkDeleteBtn"),
    selectAllTicketsBtn: byId("selectAllTicketsBtn"),
    assignModal: byId("assignModal"),
    closeAssignModalBtn: byId("closeAssignModalBtn"),
    confirmBulkAssignBtn: byId("confirmBulkAssignBtn"),
    bulkAssigneeSelect: byId("bulkAssigneeSelect"),
    bulkActionModal: byId("bulkActionModal"),
    bulkActionModalTitle: byId("bulkActionModalTitle"),
    bulkActionModalText: byId("bulkActionModalText"),
    closeBulkActionModalBtn: byId("closeBulkActionModalBtn"),
    cancelBulkActionBtn: byId("cancelBulkActionBtn"),
    confirmBulkActionBtn: byId("confirmBulkActionBtn"),
    columnsModal: byId("columnsModal"),
    closeColumnsModalBtn: byId("closeColumnsModalBtn"),
    columnAddSelect: byId("columnAddSelect"),
    columnsDraftList: byId("columnsDraftList"),
    taskPanelEnabledInput: byId("taskPanelEnabledInput"),
    editColumnsWidthBtn: byId("editColumnsWidthBtn"),
    resizeColumnsActions: byId("resizeColumnsActions"),
    saveResizeColumnsBtn: byId("saveResizeColumnsBtn"),
    cancelResizeColumnsBtn: byId("cancelResizeColumnsBtn"),
    saveColumnsBtn: byId("saveColumnsBtn"),
    errorModal: byId("errorModal"),
    errorText: byId("errorText"),
    closeErrorModalBtn: byId("closeErrorModalBtn"),
    copyErrorBtn: byId("copyErrorBtn"),
    okErrorBtn: byId("okErrorBtn"),
    statusButtons: [...document.querySelectorAll("[data-availability-status]")],
    adminOnly: [...document.querySelectorAll(".admin-only")],
    statusPanel: document.querySelector(".status-panel"),
    adminAgentFilter: document.getElementById("adminAgentFilter"),
    adminAgentList: document.getElementById("adminAgentList"),
    adminAgentToggleAll: document.getElementById("adminAgentToggleAll"),
    adminAssigneeCols: [...(root?.querySelectorAll(".admin-assignee-col") || [])],
    adminSelectCols: [...(root?.querySelectorAll(".admin-select-col") || [])],
    agentAvatar: document.querySelector(".agent-avatar"),
    tabCountNodes: [...document.querySelectorAll("[data-tab-count]")],
  };
}

const REQUIRED_LIST_ELEMENT_KEYS = [
  "roleSelect",
  "agentSelect",
  "searchInput",
  "bulkAssignBtn",
  "assignModal",
  "closeAssignModalBtn",
  "confirmBulkAssignBtn",
  "bulkAssigneeSelect",
  "errorModal",
  "errorText",
  "closeErrorModalBtn",
  "copyErrorBtn",
  "okErrorBtn",
  "ticketsHeaderRow",
  "ticketsTableBody",
  "ticketCount",
  "prevPageBtn",
  "nextPageBtn",
  "paginationPages",
  "emptyText",
];

function requireListElements() {
  const missing = REQUIRED_LIST_ELEMENT_KEYS.filter((key) => !el[key]);
  if (missing.length) {
    throw new Error(`Tickets list DOM is incomplete: ${missing.join(", ")}`);
  }
}

let el = collectElements();
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

function setStatus() {}

function showError(message) {
  el.errorText.value = message || "Неизвестная ошибка";
  el.errorModal.classList.remove("hidden");
}

function closeError() {
  el.errorModal.classList.add("hidden");
}

function hideColorLegend() {
  window.clearTimeout(colorLegendHideTimer);
  colorLegendHideTimer = null;
  if (el.colorLegend?.matches(":popover-open")) el.colorLegend.hidePopover();
  el.colorLegend?.setAttribute("aria-hidden", "true");
}

function positionColorLegend() {
  const legend = el.colorLegend;
  if (!legend?.matches(":popover-open")) return;
  const anchor = el.colorToggleText.getBoundingClientRect();
  const toolbar = el.colorToggleText.closest(".toolbar").getBoundingClientRect();
  if (anchor.right <= Math.max(0, toolbar.left) || anchor.left >= Math.min(window.innerWidth, toolbar.right)
      || anchor.bottom <= 0 || anchor.top >= window.innerHeight) {
    hideColorLegend();
    return;
  }
  const { width, height } = legend.getBoundingClientRect();
  const left = Math.max(8, Math.min(anchor.right - width, window.innerWidth - width - 8));
  const below = anchor.bottom + 8;
  const top = below + height <= window.innerHeight - 8 ? below : Math.max(8, anchor.top - height - 8);
  legend.style.left = `${left}px`;
  legend.style.top = `${top}px`;
}

function showColorLegend() {
  window.clearTimeout(colorLegendHideTimer);
  colorLegendHideTimer = null;
  if (!state.colorEnabled || !el.colorLegend) return;
  if (!el.colorLegend.matches(":popover-open")) el.colorLegend.showPopover();
  el.colorLegend.setAttribute("aria-hidden", "false");
  positionColorLegend();
}

function scheduleColorLegendHide() {
  window.clearTimeout(colorLegendHideTimer);
  colorLegendHideTimer = window.setTimeout(() => {
    if (el.colorToggleText?.matches(":hover, :focus-visible")
        || el.colorEnabledInput?.matches(":focus-visible") || el.colorLegend?.matches(":hover")) return;
    hideColorLegend();
  }, 120);
}

function applyColorPreference() {
  if (el.colorEnabledInput) {
    el.colorEnabledInput.checked = Boolean(state.colorEnabled);
  }
  document.body.classList.toggle("ticket-colors-disabled", !state.colorEnabled);
  if (el.colorToggleLabel) {
    el.colorToggleLabel.classList.toggle("color-toggle-disabled", !state.colorEnabled);
  }
  if (!state.colorEnabled) hideColorLegend();
}

function applyTaskPanelPreference() {
  const canUse = canUseTaskPanel();
  if (el.taskPanelToggle) {
    el.taskPanelToggle.classList.toggle("hidden", !canUse);
  }
  if (el.taskPanelEnabledInput) {
    el.taskPanelEnabledInput.checked = Boolean(state.taskPanelEnabled);
  }
  renderTaskPanel();
}

async function loadPreferences() {
  const preferences = await api("/api/v1/users/me/preferences");
  state.ticketListAutoWidth = preferences?.ticket_list_auto_width === true;
  state.colorEnabled = preferences?.color_enabled !== false;
  state.taskPanelEnabled = preferences?.task_panel_enabled === true;
  state.ticketListColumns = normalizeTicketColumns(preferences?.ticket_list_columns || DEFAULT_TICKET_COLUMNS);
  applyColorPreference();
  applyTaskPanelPreference();
  renderTicketHeader();
}

async function updateColorPreference(enabled) {
  const previous = state.colorEnabled;
  state.colorEnabled = Boolean(enabled);
  applyColorPreference();
  try {
    const preferences = await apiWrite("/api/v1/users/me/preferences", { color_enabled: state.colorEnabled });
    state.ticketListAutoWidth = preferences?.ticket_list_auto_width === true;
  state.colorEnabled = preferences?.color_enabled !== false;
    applyColorPreference();
    setStatus("Настройка цвета сохранена");
  } catch (e) {
    state.colorEnabled = previous;
    applyColorPreference();
    showError(`Ошибка настройки цвета: ${e.message}`);
  }
}

function ticketHref(ticketId) {
  const currentListUrl = `${window.location.pathname}${window.location.search}`;
  const ticketUrl = new URL(`/tickets/${ticketId}`, window.location.origin);
  ticketUrl.searchParams.set("tab", state.tab || DEFAULT_TAB);
  ticketUrl.searchParams.set("page", String(parsePage(state.page)));
  const appliedQuery = (state.appliedSearchQuery || "").trim();
  if (appliedQuery) ticketUrl.searchParams.set("q", appliedQuery);
  ticketUrl.searchParams.set("return_url", currentListUrl);
  return `${ticketUrl.pathname}${ticketUrl.search}`;
}

function taskPanelItemHtml(item, className) {
  const unread = item?.has_unread ? '<span class="task-panel-unread" aria-label="Новые сообщения"></span>' : "";
  return `<a class="${className}${item?.has_unread ? " has-unread" : ""}" href="${escapeHtml(ticketHref(item.id))}" title="${escapeHtml(item.subject || "")}">${unread}<span class="task-panel-subject">${escapeHtml(item.subject || "")}</span></a>`;
}

function closeTaskPanelDropdown() {
  state.taskPanelDropdownOpen = false;
  if (el.taskPanelDropdown) el.taskPanelDropdown.classList.add("hidden");
  if (el.taskPanelMoreBtn) el.taskPanelMoreBtn.classList.remove("active");
}

function renderTaskPanel() {
  if (!el.taskPanel || !el.taskPanelSlots || !el.taskPanelMoreBtn || !el.taskPanelDropdown) return;
  const canUse = canUseTaskPanel();
  const items = canUse && state.taskPanelEnabled ? (state.taskPanelItems || []) : [];
  const shouldShow = canUse && state.taskPanelEnabled;
  const visibleItems = items.slice(0, 8);
  const overflowItems = items.slice(8);
  const slotsHtml = !shouldShow ? "" : items.length
    ? visibleItems.map((item) => taskPanelItemHtml(item, "task-panel-chip")).join("")
    : `<span class="task-panel-chip"><span class="task-panel-subject">${state.taskPanelStatus === "ready" ? "Нет заявок" : state.taskPanelStatus === "error" ? "Не удалось загрузить панель задач" : "Загрузка…"}</span></span>`;
  const dropdownHtml = overflowItems.map((item) => taskPanelItemHtml(item, "task-panel-menu-item")).join("");
  const renderKey = JSON.stringify([shouldShow, slotsHtml, dropdownHtml]);
  if (el.taskPanel._taskPanelRenderKey === renderKey) return;
  el.taskPanel._taskPanelRenderKey = renderKey;
  el.taskPanel.classList.toggle("hidden", !shouldShow);
  if (!shouldShow) {
    el.taskPanelSlots.innerHTML = "";
    el.taskPanelMoreBtn.classList.add("hidden");
    el.taskPanelDropdown.innerHTML = "";
    closeTaskPanelDropdown();
    return;
  }
  el.taskPanel.classList.toggle("has-overflow", overflowItems.length > 0);
  el.taskPanelSlots.innerHTML = slotsHtml;
  el.taskPanelMoreBtn.classList.toggle("hidden", overflowItems.length === 0);
  el.taskPanelDropdown.innerHTML = dropdownHtml;
  if (overflowItems.length === 0) closeTaskPanelDropdown();
}

async function loadTaskPanel(options = {}) {
  await fetchTaskPanelItems(options);
  renderTaskPanel();
}

async function fetchTaskPanelItems(options = {}) {
  if (!canUseTaskPanel() || !state.taskPanelEnabled) {
    state.taskPanelItems = [];
    return;
  }
  renderTaskPanel();
  try {
    state.taskPanelItems = await api("/api/v1/tickets/task-panel");
    state.taskPanelStatus = "ready";
  } catch (e) {
    state.taskPanelItems = [];
    state.taskPanelStatus = "error";
    if (!options.silent) showError(`Не удалось загрузить панель задач.\n\n${e.message || "Неизвестная ошибка"}`);
  }
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
    case "requester_department":
      return String(ticket.requester_department || "").toLocaleLowerCase("ru");
    case "requester_location_type":
      return String(ticket.requester_location_type || "").toLocaleLowerCase("ru");
    case "assignee_name":
      return String(ticket.assignee_name || "").toLocaleLowerCase("ru");
    case "ticket_type":
      return ticketTypeInfo(ticket, state.tab).label.toLocaleLowerCase("ru");
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
  const textKeys = new Set(["subject", "requester_display_name", "assignee_name", "requester_department", "requester_location_type", "ticket_type"]);
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
    if (!state.columnResizeActive && btn.dataset.sortKey === state.tableSortKey) {
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

function canShowAssigneeColumn(role = state.role) {
  return role === "admin" || role === "chiki_piki";
}

function canUseTaskPanel(role = state.role) {
  return role === "agent" || role === "trainee";
}

function allowedTicketColumnKeys(role = state.role) {
  return TICKET_COLUMN_KEYS.filter((key) => key !== "assignee" || canShowAssigneeColumn(role));
}

function cloneColumns(columns) {
  return (columns || []).map((column) => ({ key: column.key, width: Number(column.width || 0) }));
}

function normalizeColumnWidth(value, fallback = 0) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  if (!Number.isFinite(parsed)) return fallback;
  if (parsed <= 0) return 0;
  return Math.max(60, parsed);
}

function normalizeTicketColumns(columns, role = state.role) {
  const allowed = new Set(allowedTicketColumnKeys(role));
  const source = Array.isArray(columns) && columns.length ? columns : DEFAULT_TICKET_COLUMNS;
  const normalized = [];
  const seen = new Set();
  source.forEach((column) => {
    const key = column?.key || "";
    if (!allowed.has(key) || seen.has(key) || !TICKET_COLUMN_DEFS[key]) return;
    seen.add(key);
    normalized.push({ key, width: normalizeColumnWidth(column.width, TICKET_COLUMN_DEFS[key].defaultWidth) });
  });
  if (!seen.has("subject")) {
    normalized.unshift({ key: "subject", width: 0 });
  }
  return normalized;
}

function columnStyle(width) {
  const normalized = normalizeColumnWidth(width);
  if (normalized === 0) return "";
  return `width:${normalized}px;min-width:${normalized}px;max-width:${normalized}px;`;
}

function minResizeWidthForColumn(key) {
  if (key === "subject") return 160;
  if (key === "age") return 90;
  return 100;
}

function clampResizeWidth(key, width) {
  const parsed = Number.parseInt(String(width || ""), 10);
  const min = minResizeWidthForColumn(key);
  return Math.max(min, Number.isFinite(parsed) ? parsed : min);
}

function activeTicketAutoWidth() {
  return state.columnResizeActive ? state.columnResizeAutoWidth
    : state.columnDraftPreviewActive ? state.draftTicketListAutoWidth : state.ticketListAutoWidth;
}

function activeTicketColumns() {
  const columns = normalizeTicketColumns(state.columnResizeActive && state.columnResizeColumns
    ? state.columnResizeColumns : state.columnDraftPreviewActive && state.draftTicketListColumns
      ? state.draftTicketListColumns : state.ticketListColumns);
  if (!activeTicketAutoWidth() || !el.ticketsTable) return columns;
  const scroll = el.ticketsTable.closest(".table-scroll");
  const style = getComputedStyle(scroll);
  const available = scroll.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
  return window.HelpdeskTicketColumnAutoWidth.fill(columns, available, 54 + (state.role === "admin" ? 34 : 0));
}

function refreshAutoColumnLayout() {
  if (!activeTicketAutoWidth() || state.columnReorderDrag || !el.ticketsTable) return;
  const columns = activeTicketColumns();
  const subject = columns.find((column) => column.key === "subject");
  el.ticketsTable.style.width = `${columns.reduce((sum, column) => sum + column.width, 54 + (state.role === "admin" ? 34 : 0))}px`;
  el.ticketsTable.querySelectorAll('[data-column-key="subject"]').forEach((cell) => {
    cell.style.cssText = columnStyle(subject.width);
  });
}

function fixedTableWidth(columns) {
  const systemWidth = 36 + (state.role === "admin" ? 34 : 0);
  return columns.reduce((sum, column) => sum + normalizeColumnWidth(column.width), systemWidth);
}

function ticketChannel(ticket) {
  if (ticket?.is_internal_task) return "internal";
  if (ticket?.channel) return ticket.channel;
  if (ticket?.workflow_type === "ticket") return "telegram";
  return "telegram";
}

function channelIconSvg(channel) {
  if (channel === "telegram") {
    return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 4.6 17.9 19c-.2 1-.8 1.2-1.6.8l-4.5-3.3-2.2 2.1c-.2.2-.4.4-.9.4l.3-4.6 8.4-7.6c.4-.3-.1-.5-.6-.2L6.5 13.1 2 11.7c-1-.3-1-1 0-1.4L19.6 3.5c.8-.3 1.5.2 1.4 1.1Z"/></svg>';
  }
  return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7.1 7.7A7 7 0 0 1 18.7 10h-2.2l3.6 3.6L23.7 10h-2A10 10 0 0 0 4.9 5.6l2.2 2.1Zm9.8 8.6A7 7 0 0 1 5.3 14h2.2l-3.6-3.6L.3 14h2a10 10 0 0 0 16.8 4.4l-2.2-2.1Z"/></svg>';
}

function renderChannelIcon(ticket) {
  const channel = ticketChannel(ticket);
  const title = channel === "telegram" ? "Telegram" : (channel === "internal" ? "Внутренняя" : "Другой канал");
  const iconClass = channel === "telegram" ? "ticket-channel-telegram" : "ticket-channel-sync";
  return `<span class="ticket-channel-icon ${iconClass}" title="${escapeHtml(title)}" aria-label="${escapeHtml(title)}">${channelIconSvg(channel)}</span>`;
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
  el.bulkAssignBtn.classList.toggle("hidden", !isAdmin);
  el.bulkAssignBtn.disabled = selectedCount === 0;
  if (el.bulkAdminActions) el.bulkAdminActions.classList.toggle("hidden", !isAdmin);
  if (el.bulkCloseBtn) {
    el.bulkCloseBtn.classList.toggle("hidden", !isAdmin);
    el.bulkCloseBtn.disabled = selectedCount === 0;
  }
  if (el.bulkFreezeBtn) {
    el.bulkFreezeBtn.classList.toggle("hidden", !isAdmin || state.tab === "frozen");
    el.bulkFreezeBtn.disabled = selectedCount === 0 || state.tab !== "active";
  }
  if (el.bulkUnfreezeBtn) {
    el.bulkUnfreezeBtn.classList.toggle("hidden", !isAdmin || state.tab !== "frozen");
    el.bulkUnfreezeBtn.disabled = selectedCount === 0;
  }
  if (el.bulkDeleteBtn) {
    el.bulkDeleteBtn.classList.toggle("hidden", !isAdmin);
    el.bulkDeleteBtn.disabled = selectedCount === 0;
  }
  el.bulkAssignBtn.textContent = `Назначить ответственного (${selectedCount})`;

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
  const isFreeze = kind === "freeze";
  const isUnfreeze = kind === "unfreeze";
  el.bulkActionModalTitle.textContent = isDelete ? "Удаление заявок" : isFreeze ? "Заморозка заявок" : isUnfreeze ? "Разморозка заявок" : "Завершение заявок";
  el.bulkActionModalText.textContent = isDelete
    ? (isSingle ? "Вы уверены, что хотите удалить заявку?" : "Вы уверены, что хотите удалить заявки?")
    : isFreeze
      ? (isSingle ? "Заморозить выбранную заявку?" : `Заморозить выбранные заявки (${selected})?`)
      : isUnfreeze
        ? (isSingle ? "Разморозить выбранную заявку?" : `Разморозить выбранные заявки (${selected})?`)
      : (isSingle ? "Вы уверены, что хотите закрыть заявку?" : "Вы уверены, что хотите закрыть заявки?");
  el.confirmBulkActionBtn.textContent = isDelete ? "Удалить" : isFreeze ? "Заморозить" : isUnfreeze ? "Разморозить" : "Завершить";
  el.confirmBulkActionBtn.classList.toggle("danger", isDelete);
  el.confirmBulkActionBtn.classList.toggle("primary", !isDelete);
  el.bulkActionModal.classList.remove("hidden");
}
function closeBulkActionModal() {
  if (["close", "delete"].includes(pendingBulkAction) && el.bulkSkipClientNotification) el.bulkSkipClientNotification.checked = false;
  pendingBulkAction = null;
  if (el.confirmBulkActionBtn) {
    el.confirmBulkActionBtn.disabled = false;
    el.confirmBulkActionBtn.textContent = "Подтвердить";
  }
  el.bulkActionModal.classList.add("hidden");
}

async function runBulkAction() {
  const ticketIds = [...state.selectedTicketIds];
  if (!ticketIds.length || !pendingBulkAction || state.role !== "admin") return;
  const skipClientNotification = ["close", "delete"].includes(pendingBulkAction) && Boolean(el.bulkSkipClientNotification?.checked);
  const resetClientNotification = ["close", "delete"].includes(pendingBulkAction);
  if (resetClientNotification && el.bulkSkipClientNotification) el.bulkSkipClientNotification.checked = false;
  try {
    if (pendingBulkAction === "close") {
      setStatus("Принудительно закрываю заявки...");
      const result = await apiPost("/api/v1/tickets/admin/close", { ticket_ids: ticketIds, skip_client_notification: skipClientNotification });
      setStatus(`Закрыто: ${result.closed}, уже закрыты: ${result.already_closed}`);
    } else if (pendingBulkAction === "freeze") {
      setStatus("Замораживаю заявки...");
      const result = await apiPost("/api/v1/tickets/admin/freeze", { ticket_ids: ticketIds });
      const parts = [
        `Заморожено: ${Number(result.frozen || 0)}`,
        `уже заморожены: ${Number(result.already_frozen || 0)}`,
        `не подходят: ${Number(result.invalid || 0)}`,
        `закрыты: ${Number(result.closed || 0)}`,
      ];
      if (result.not_found?.length) {
        parts.push(`не найдены: ${result.not_found.length}`);
      }
      const message = parts.join(", ");
      const skipped = Number(result.already_frozen || 0) + Number(result.invalid || 0) + Number(result.closed || 0) + Number(result.not_found?.length || 0);
      if (skipped) {
        console.info("Bulk freeze skipped items", result);
      } else {
        setStatus(message);
      }
    } else if (pendingBulkAction === "unfreeze") {
      const result = await apiPost("/api/v1/tickets/admin/unfreeze", { ticket_ids: ticketIds });
      const skipped = Number(result.already_unfrozen || 0) + Number(result.closed || 0) + Number(result.not_found?.length || 0);
      if (skipped) {
        console.info("Bulk unfreeze skipped items", result);
      }
    } else {
      setStatus("Удаляю заявки...");
      const result = await apiPost("/api/v1/tickets/admin/delete", { ticket_ids: ticketIds, skip_client_notification: skipClientNotification });
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
  } finally {
    if (resetClientNotification && el.bulkSkipClientNotification) el.bulkSkipClientNotification.checked = false;
  }
}

function toggleAllVisibleTickets() {
  if (state.columnResizeActive) return;
  const allVisibleSelected = state.visibleTicketIds.length > 0
    && state.visibleTicketIds.every((id) => state.selectedTicketIds.has(id));
  for (const ticketId of state.visibleTicketIds) {
    if (allVisibleSelected) state.selectedTicketIds.delete(ticketId);
    else state.selectedTicketIds.add(ticketId);
  }
  renderTable();
}

function handleSortClick(btn) {
  if (state.columnResizeActive) return;
  const key = btn.dataset.sortKey;
  if (!key) return;
  if (state.tableSortKey === key) {
    state.tableSortDir = state.tableSortDir === "asc" ? "desc" : "asc";
  } else {
    state.tableSortKey = key;
    state.tableSortDir = ["subject", "requester_display_name", "assignee_name", "requester_department", "requester_location_type", "ticket_type"].includes(key) ? "asc" : "desc";
  }
  localStorage.setItem("hd_table_sort_key", state.tableSortKey);
  localStorage.setItem("hd_table_sort_dir", state.tableSortDir);
  refreshSortHeaderUi();
  renderTable();
}

function renderTicketHeader() {
  if (!el.ticketsHeaderRow) return;
  const columns = activeTicketColumns();
  const cells = [];
  if (state.role === "admin") {
    cells.push('<th class="admin-select-col"><button id="selectAllTicketsBtn" class="mini-check-btn" type="button">✓</button></th>');
  }
  cells.push('<th class="ticket-channel-col"><button id="columnsSettingsBtn" class="mini-icon-btn" type="button" title="Настройка списка" aria-label="Настройка списка">⚙️</button></th>');
  columns.forEach((column) => {
    const def = TICKET_COLUMN_DEFS[column.key];
    const sort = def.sortKey ? ` data-sort-key="${escapeHtml(def.sortKey)}"` : "";
    const handle = state.columnResizeActive && !state.columnResizeAutoWidth
      ? `<button class="column-resize-handle" type="button" data-resize-column="${escapeHtml(column.key)}" aria-label="Изменить ширину ${escapeHtml(def.title)}"><span class="resize-handle-icon" aria-hidden="true"></span></button>`
      : "";
    cells.push(`<th class="${def.className}" data-column-key="${escapeHtml(column.key)}" style="${columnStyle(column.width)}"><button type="button" class="table-sort-btn"${sort}>${escapeHtml(def.title)}</button>${handle}</th>`);
  });
  el.ticketsHeaderRow.innerHTML = cells.join("");
  el.sortButtons = [...document.querySelectorAll(".table-sort-btn")];
  el.selectAllTicketsBtn = document.getElementById("selectAllTicketsBtn");
  el.sortButtons.forEach((btn) => {
    btn.addEventListener("click", () => handleSortClick(btn));
  });
  if (el.selectAllTicketsBtn) {
    el.selectAllTicketsBtn.addEventListener("click", toggleAllVisibleTickets);
  }
  const settingsBtn = document.getElementById("columnsSettingsBtn");
  if (settingsBtn) {
    settingsBtn.addEventListener("click", () => {
      if (!state.columnResizeActive) openColumnsModal();
    });
  }
  document.querySelectorAll(".column-resize-handle").forEach((handle) => {
    handle.addEventListener("pointerdown", startColumnResizeDrag);
  });
  if (state.columnResizeActive) {
    el.ticketsHeaderRow.querySelectorAll('th[data-column-key]').forEach((header) => {
      header.addEventListener("pointerdown", startColumnReorderDrag);
    });
  }
  refreshSortHeaderUi();
}

function measureRenderedColumnWidths() {
  return normalizeTicketColumns(state.columnResizeColumns || state.draftTicketListColumns || state.ticketListColumns)
    .map((column) => {
      const node = el.ticketsHeaderRow?.querySelector(`[data-column-key="${CSS.escape(column.key)}"]`);
      const measured = node ? Math.round(node.getBoundingClientRect().width) : column.width;
      return { key: column.key, width: clampResizeWidth(column.key, measured) };
    });
}

function enterColumnResizeMode() {
  state.columnResizePreviousDraft = cloneColumns(normalizeTicketColumns(state.draftTicketListColumns || state.ticketListColumns));
  state.columnResizeColumns = cloneColumns(state.columnResizePreviousDraft);
  state.columnResizeAutoWidth = state.draftTicketListAutoWidth;
  state.columnResizeActive = true;
  el.columnsModal?.classList.add("columns-resize-mode");
  el.resizeColumnsActions?.classList.remove("hidden");
  renderTable();
  if (!state.columnResizeAutoWidth) state.columnResizeColumns = measureRenderedColumnWidths();
  renderTable();
}

function leaveColumnResizeMode() {
  stopColumnReorderDrag();
  state.columnAutoCalculation = null;
  if (el.saveResizeColumnsBtn) el.saveResizeColumnsBtn.disabled = false;
  state.columnResizeActive = false;
  state.columnResizeColumns = null;
  state.columnResizeDrag = null;
  el.columnsModal?.classList.remove("columns-resize-mode");
  el.resizeColumnsActions?.classList.add("hidden");
  renderTable();
}

function saveColumnResizeDraft() {
  if (state.columnAutoCalculation) return;
  state.draftTicketListAutoWidth = state.columnResizeAutoWidth;
  state.draftTicketListColumns = cloneColumns(activeTicketColumns());
  state.columnDraftPreviewActive = true;
  state.columnResizePreviousDraft = null;
  leaveColumnResizeMode();
  renderColumnsDraft();
}

function cancelColumnResizeDraft() {
  state.draftTicketListColumns = cloneColumns(normalizeTicketColumns(state.columnResizePreviousDraft || state.draftTicketListColumns));
  state.columnResizePreviousDraft = null;
  leaveColumnResizeMode();
  renderColumnsDraft();
}

function startColumnResizeDrag(event) {
  if (!state.columnResizeActive || state.columnResizeAutoWidth) return;
  event.preventDefault();
  event.stopPropagation();
  const key = event.currentTarget?.dataset?.resizeColumn || "";
  const column = normalizeTicketColumns(state.columnResizeColumns).find((item) => item.key === key);
  if (!column) return;
  const headerCell = el.ticketsHeaderRow?.querySelector(`[data-column-key="${CSS.escape(key)}"]`);
  const renderedWidth = headerCell ? Math.round(headerCell.getBoundingClientRect().width) : column.width;
  state.columnResizeDrag = {
    key,
    startX: event.clientX,
    startWidth: clampResizeWidth(key, renderedWidth),
    pointerId: event.pointerId,
  };
  event.currentTarget.setPointerCapture?.(event.pointerId);
}

function updateColumnResizeDrag(event) {
  const drag = state.columnResizeDrag;
  if (!drag) return;
  const nextWidth = clampResizeWidth(drag.key, drag.startWidth + event.clientX - drag.startX);
  state.columnResizeColumns = normalizeTicketColumns(state.columnResizeColumns).map((column) => (
    column.key === drag.key ? { ...column, width: nextWidth } : column
  ));
  renderTable();
}

function stopColumnResizeDrag() {
  state.columnResizeDrag = null;
}

async function toggleAutoColumnWidth() {
  if (!state.columnResizeActive) return;
  const columns = cloneColumns(activeTicketColumns());
  const samples = [...el.ticketsTableBody.rows].map((row) => row.cloneNode(true));
  state.columnResizeColumns = columns;
  state.columnResizeAutoWidth = el.columnAutoWidthInput.checked;
  state.columnAutoCalculation = null;
  el.saveResizeColumnsBtn.disabled = false;
  renderTable();
  if (!state.columnResizeAutoWidth) return;
  const calculation = {};
  state.columnAutoCalculation = calculation;
  el.saveResizeColumnsBtn.disabled = true;
  try {
    if (!samples.length) {
      try {
        const payload = await api(`${endpointMap.closed}?sort=created_at&order=desc&page=1&limit=${state.limit}`);
        const linkedCell = (className, text, width, isHtml = false) =>
          `<td class="${className} ticket-linked-cell"><a class="ticket-row-link">${isHtml ? text : escapeHtml(text)}</a></td>`;
        for (const ticket of payload.items || []) {
          const row = document.createElement("tr");
          row.className = "ticket-row";
          row.innerHTML = columns.map((column) => ticketCellHtml(column, ticket, "", linkedCell)).join("");
          [...row.cells].forEach((cell, index) => { cell.dataset.columnKey = columns[index].key; });
          row.querySelectorAll(".row-assignee-select").forEach((select) => { select.value = select.dataset.selectedId || ""; });
          samples.push(row);
        }
      } catch (_) { /* Empty samples use header minimums. */ }
    }
    await document.fonts.ready;
    if (state.columnAutoCalculation !== calculation) return;
    const measured = window.HelpdeskTicketColumnAutoWidth.measure(el.ticketsTable, columns, samples);
    state.columnResizeColumns = state.columnResizeColumns.map((column) => ({ ...column, width: measured.find((item) => item.key === column.key).width }));
    renderTable();
  } finally {
    if (state.columnAutoCalculation === calculation) {
      state.columnAutoCalculation = null;
      el.saveResizeColumnsBtn.disabled = false;
    }
  }
}

function renderColumnsDraft() {
  if (!el.columnsDraftList || !el.columnAddSelect) return;
  const draft = normalizeTicketColumns(state.draftTicketListColumns || state.ticketListColumns);
  state.draftTicketListColumns = draft;
  const existing = new Set(draft.map((column) => column.key));
  const addable = allowedTicketColumnKeys().filter((key) => !existing.has(key));
  el.columnAddSelect.innerHTML = `${addable.map((key) => `<option value="${escapeHtml(key)}">${escapeHtml(TICKET_COLUMN_DEFS[key].title)}</option>`).join("")}`;
  el.columnAddSelect.selectedIndex = -1;
  el.columnAddSelect.disabled = addable.length === 0;
  el.columnsDraftList.innerHTML = draft.map((column, index) => {
    const def = TICKET_COLUMN_DEFS[column.key];
    const deleteButton = def.mandatory
      ? '<span class="columns-delete-spacer" aria-hidden="true"></span>'
      : `<button class="columns-delete-btn" type="button" data-column-delete="${escapeHtml(column.key)}" title="Удалить" aria-label="Удалить ${escapeHtml(def.title)}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3h6l1 2h4v2H4V5h4l1-2Zm-2 6h10l-.7 11H7.7L7 9Zm3 2 .3 7h1.5l-.2-7H10Zm4 0-.2 7h1.5l.3-7H14Z"/></svg></button>`;
    return `
      <div class="columns-draft-row" data-column-key="${escapeHtml(column.key)}" data-column-index="${index}">
        <span class="columns-drag-handle" title="Перетащить">☰</span>
        <span class="columns-draft-title">${escapeHtml(def.title)}</span>
        ${deleteButton}
      </div>
    `;
  }).join("");
}

function openColumnsModal() {
  state.draftTicketListAutoWidth = state.ticketListAutoWidth;
  state.columnDraftPreviewActive = false;
  state.draftTicketListColumns = cloneColumns(normalizeTicketColumns(state.ticketListColumns));
  applyTaskPanelPreference();
  if (el.taskPanelEnabledInput) el.taskPanelEnabledInput.checked = Boolean(state.taskPanelEnabled);
  renderColumnsDraft();
  el.columnsModal?.classList.remove("hidden");
}

function closeColumnsModal() {
  stopColumnReorderDrag();
  if (state.columnResizeActive) {
    cancelColumnResizeDraft();
  }
  const hadPreview = state.columnDraftPreviewActive;
  state.columnDraftPreviewActive = false;
  state.draftTicketListColumns = null;
  el.columnsModal?.classList.add("hidden");
  if (hadPreview) renderTable();
}

async function confirmDeleteDraftColumn(key) {
  const def = TICKET_COLUMN_DEFS[key];
  const draft = state.draftTicketListColumns;
  if (!def || def.mandatory || !draft) return;
  const confirmed = await window.HelpdeskDialogs.confirm(
    `Вы уверены, что хотите удалить столбец „${def.title}“ из списка?`,
    { title: "Удаление столбца", acceptLabel: "Удалить" },
  );
  if (!confirmed || !pageEventController || pageEventController.signal.aborted || state.draftTicketListColumns !== draft) return;
  deleteDraftColumn(key);
  el.editColumnsWidthBtn?.focus();
}

function deleteDraftColumn(key) {
  if (TICKET_COLUMN_DEFS[key]?.mandatory) return;
  state.draftTicketListColumns = normalizeTicketColumns(state.draftTicketListColumns).filter((column) => column.key !== key);
  renderColumnsDraft();
}

function addDraftColumn(key) {
  if (!key || !allowedTicketColumnKeys().includes(key)) return;
  const draft = normalizeTicketColumns(state.draftTicketListColumns);
  if (draft.some((column) => column.key === key)) return;
  draft.push({ key, width: TICKET_COLUMN_DEFS[key].defaultWidth });
  state.draftTicketListColumns = draft;
  renderColumnsDraft();
}

function moveDraftColumn(fromIndex, toIndex) {
  const field = state.columnResizeActive ? "columnResizeColumns" : "draftTicketListColumns";
  const draft = normalizeTicketColumns(state[field]);
  if (fromIndex === toIndex || fromIndex < 0 || toIndex < 0 || fromIndex >= draft.length || toIndex >= draft.length) return;
  const [moved] = draft.splice(fromIndex, 1);
  draft.splice(toIndex, 0, moved);
  state[field] = draft;
  const rows = state.columnResizeActive
    ? el.ticketsTable.querySelectorAll("tr") : [el.columnsDraftList];
  rows.forEach((row) => {
    const nodes = [...row.querySelectorAll(":scope > [data-column-key]")];
    const before = new Map(nodes.map((node) => [node, node.getBoundingClientRect()]));
    nodes.forEach((node) => node.getAnimations().forEach((animation) => animation.cancel()));
    draft.forEach((column, index) => {
      const node = nodes.find((item) => item.dataset.columnKey === column.key);
      if (!node) return;
      row.appendChild(node);
      if (!state.columnResizeActive) node.dataset.columnIndex = index;
    });
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    nodes.forEach((node) => {
      const previous = before.get(node), next = node.getBoundingClientRect();
      if (previous.x === next.x && previous.y === next.y) return;
      node.animate([
        { transform: `translate(${previous.x - next.x}px, ${previous.y - next.y}px)` },
        { transform: "translate(0, 0)" },
      ], { duration: 160, easing: "ease-out" });
    });
  });
}

function startColumnReorderDrag(event) {
  if (event.button !== 0 || state.columnResizeDrag || state.columnReorderDrag) return;
  const header = event.target.closest("th[data-column-key]");
  const handle = event.target.closest(".columns-drag-handle");
  if (header ? (!state.columnResizeActive || event.target.closest(".column-resize-handle")) : !handle) return;
  const node = header || handle.closest(".columns-draft-row");
  event.preventDefault();
  const captureNode = header ? el.ticketsHeaderRow : el.columnsDraftList;
  captureNode.setPointerCapture(event.pointerId);
  state.columnReorderDrag = {
    key: node.dataset.columnKey, node, captureNode, table: Boolean(header),
    pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, moving: false,
  };
}

function updateColumnReorderDrag(event) {
  const drag = state.columnReorderDrag;
  if (!drag || event.pointerId !== drag.pointerId) return;
  if (!drag.moving && Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) < 4) return;
  drag.moving = true;
  drag.node.classList.add("column-reorder-dragging");
  document.body.classList.add("column-reordering");
  const parent = drag.table ? el.ticketsHeaderRow : el.columnsDraftList;
  const nodes = [...parent.querySelectorAll(":scope > [data-column-key]")];
  const from = nodes.findIndex((node) => node.dataset.columnKey === drag.key);
  let to = from;
  nodes.forEach((node, index) => {
    if (index === from) return;
    const box = node.getBoundingClientRect();
    const transform = new DOMMatrix(getComputedStyle(node).transform);
    const position = drag.table ? event.clientX : event.clientY;
    const middle = drag.table ? box.x + box.width / 2 - transform.m41 : box.y + box.height / 2 - transform.m42;
    if (index < from && position <= middle) to = Math.min(to, index);
    if (index > from && position >= middle) to = Math.max(to, index);
  });
  moveDraftColumn(from, to);
}

function stopColumnReorderDrag() {
  const drag = state.columnReorderDrag;
  if (!drag) return;
  state.columnReorderDrag = null;
  drag.node.classList.remove("column-reorder-dragging");
  if (drag.captureNode.hasPointerCapture(drag.pointerId)) drag.captureNode.releasePointerCapture(drag.pointerId);
  document.body.classList.remove("column-reordering");
  if (drag.table) renderTable();
}

async function saveColumnsModal() {
  const payloadColumns = normalizeTicketColumns(state.draftTicketListColumns).map((column) => ({ key: column.key, width: column.width }));
  const nextTaskPanelEnabled = Boolean(el.taskPanelEnabledInput?.checked);
  const payload = { ticket_list_columns: payloadColumns, ticket_list_auto_width: state.draftTicketListAutoWidth };
  if (canUseTaskPanel()) {
    payload.task_panel_enabled = nextTaskPanelEnabled;
  }
  try {
    const preferences = await apiWrite("/api/v1/users/me/preferences", payload);
    state.ticketListAutoWidth = preferences?.ticket_list_auto_width ?? state.draftTicketListAutoWidth;
    state.ticketListColumns = normalizeTicketColumns(preferences?.ticket_list_columns || payloadColumns);
    state.taskPanelEnabled = preferences?.task_panel_enabled === true;
    state.columnDraftPreviewActive = false;
    closeColumnsModal();
    renderTable();
    await loadTaskPanel();
    setStatus("Настройка списка сохранена");
    window.HelpdeskAppShell?.showSaved?.(el.ticketsTable?.closest(".panel"));
  } catch (e) {
    showError(`Ошибка настройки списка: ${e.message}`);
  }
}

function ticketCellHtml(column, ticket, ticketHref, linkedCell) {
  const key = column.key;
  if (key === "subject") {
    const subject = (ticket.subject || "") + intakeBadge(ticket);
    if (ticket.task_kind === "group_parent") {
      return linkedCell("ticket-subject-col", `<span class="ticket-main-badge">MAIN</span><span class="ticket-main-subject">${escapeHtml(subject)}</span>`, column.width, true);
    }
    return linkedCell("ticket-subject-col", subject, column.width);
  }
  if (key === "requester") return linkedCell("ticket-client-col", ticket.requester_display_name || ticket.client_name || "", column.width);
  if (key === "department") return linkedCell("ticket-department-col", ticket.is_internal_task ? "Система" : (ticket.requester_department || "—"), column.width);
  if (key === "location") return linkedCell("ticket-location-col", ticket.is_internal_task ? "" : (ticket.requester_location_type || ""), column.width);
  if (key === "type") {
    const typeInfo = ticketTypeInfo(ticket, state.tab);
    const badge = `<span class="ticket-type-badge ticket-type-${escapeHtml(typeInfo.className)}">${escapeHtml(typeInfo.label)}</span>`;
    return linkedCell("ticket-type-col", badge, column.width, true);
  }
  if (key === "assignee") {
    if (state.role === "admin") {
      return `<td class="ticket-assignee-col" data-column-key="assignee" style="${columnStyle(column.width)}"><select class="row-assignee-select" data-ticket-id="${escapeHtml(ticket.id)}" data-selected-id="${escapeHtml(ticket.assignee_id || "")}" ${state.columnResizeActive ? "disabled" : ""}>${assigneeOptionsHtml(ticket.assignee_id, ticket.special_assignee_name || ticket.assignee_name || "")}</select></td>`;
    }
    if (state.role === "chiki_piki") {
      return linkedCell("ticket-assignee-col", ticket.special_assignee_name || ticket.assignee_name || "Нераспределён", column.width);
    }
    return "";
  }
  if (key === "age") return linkedCell("ticket-age-col", ageText(ticket.created_at), column.width);
  if (key === "created_at") return linkedCell("ticket-created-col", fmtDate(ticket.created_at), column.width);
  if (key === "last_reply") return linkedCell("ticket-last-reply-col", lastReplyText(ticket), column.width);
  return "";
}

function renderTable() {
  if (state.columnReorderDrag) return;
  const filtered = sortTickets(state.tickets);
  const activeColumns = activeTicketColumns();
  state.visibleTicketIds = filtered.map((t) => t.id);
  state.ticketListColumns = normalizeTicketColumns(state.ticketListColumns);
  document.body.classList.toggle("ticket-column-resize-active", state.columnResizeActive);
  document.body.classList.toggle("ticket-columns-auto", activeTicketAutoWidth());
  el.columnAutoWidthControl?.classList.toggle("hidden", !state.columnResizeActive);
  if (el.columnAutoWidthInput) el.columnAutoWidthInput.checked = state.columnResizeAutoWidth;
  const hasFixedColumns = activeColumns.every((column) => column.width > 0);
  document.body.classList.toggle("ticket-columns-fixed", hasFixedColumns);
  if (el.ticketsTable) {
    el.ticketsTable.style.width = hasFixedColumns ? `${activeTicketAutoWidth() ? activeColumns.reduce((sum, column) => sum + column.width, 54 + (state.role === "admin" ? 34 : 0)) : fixedTableWidth(activeColumns)}px` : "";
  }
  renderTicketHeader();
  el.ticketsTableBody.innerHTML = "";
  el.ticketCount.textContent = String(state.total);
  el.prevPageBtn.disabled = state.page <= 1;
  el.nextPageBtn.disabled = state.page >= state.pages;
  el.emptyText.style.display = filtered.length ? "none" : "block";

  for (const t of filtered) {
    const tr = document.createElement("tr");
    const rowClasses = ["row-click", "ticket-row"];
    rowClasses.push(ticketRowColorClass(t, state.tab));
    if (t.seen_by_agent && !(t.is_frozen || state.tab === "frozen") && !(t.status === "closed" || state.tab === "closed")) {
      rowClasses.push("ticket-row-seen");
    }
    if ((state.role === "agent" || state.role === "trainee") && t.seen_by_agent === false) {
      rowClasses.push("ticket-row-unseen");
    }
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
    const linkedCell = (className, textValue, width = 0, isHtml = false) => (
      `<td class="${className} ticket-linked-cell" data-column-key="${escapeHtml(Object.keys(TICKET_COLUMN_DEFS).find((key) => TICKET_COLUMN_DEFS[key].className === className) || "")}" style="${columnStyle(width)}"><a class="ticket-row-link" href="${escapeHtml(ticketHref)}">${isHtml ? textValue : escapeHtml(textValue)}</a></td>`
    );
    const selectCell = state.role === "admin"
      ? `<td class="ticket-check-cell"><input class="ticket-check" data-ticket-id="${escapeHtml(t.id)}" type="checkbox" ${state.selectedTicketIds.has(t.id) ? "checked" : ""} /></td>`
      : "";
    const channelCell = `<td class="ticket-channel-col">${renderChannelIcon(t)}</td>`;
    const dataCells = activeTicketColumns()
      .map((column) => ticketCellHtml(column, t, ticketHref, linkedCell))
      .join("");
    tr.innerHTML = `
      ${selectCell}
      ${channelCell}
      ${dataCells}
    `;
    el.ticketsTableBody.appendChild(tr);
  }

  el.ticketsTableBody.querySelectorAll(".ticket-row-link").forEach((link) => {
    const persistReturnUrl = () => {
      const currentListUrl = `${window.location.pathname}${window.location.search}`;
      sessionStorage.setItem("ticketListReturnUrl", currentListUrl);
    };
    link.addEventListener("click", (event) => {
      if (state.columnResizeActive) {
        event.preventDefault();
        return;
      }
      persistReturnUrl();
    });
    link.addEventListener("auxclick", (event) => {
      if (state.columnResizeActive) {
    event.preventDefault();
    return;
  }
  if (event.button === 1) persistReturnUrl();
  });
});
  el.ticketsTableBody.querySelectorAll(".ticket-check").forEach((checkbox) => {
    checkbox.addEventListener("click", (event) => {
      event.stopPropagation();
      if (state.columnResizeActive) {
        event.preventDefault();
        return;
      }
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
  applyTaskPanelPreference();
  state.ticketListColumns = normalizeTicketColumns(state.ticketListColumns);
  renderTicketHeader();
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
  if (el.roleSelect) el.roleSelect.value = state.role;
  renderAgentSelect();
  applyRoleUi();
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
    const [payload] = await Promise.all([
      api(`${base}?${queryParams.toString()}`),
      fetchTaskPanelItems({ silent: true }),
    ]);

    state.tickets = payload.items || [];
    state.total = payload.total || 0;
    state.pages = payload.pages || 1;
    state.page = payload.page || 1;
    if (historyMode) {
      syncListUrl(historyMode);
    }

    renderTaskPanel();
    renderTable();
    setStatus("Готово");
  } catch (e) {
    if (!silent) showError(`Не удалось загрузить список заявок.\n\n${e.message || "Неизвестная ошибка"}`);
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
    if (!silent) showError(`Не удалось обновить счётчики заявок.\n\n${e.message || "Неизвестная ошибка"}`);
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
  el = collectElements();
  requireListElements();
  if (el.bulkSkipClientNotification) el.bulkSkipClientNotification.checked = false;
  const pageSignal = pageEventController.signal;
  columnAutoResizeObserver?.disconnect();
  columnAutoResizeObserver = new ResizeObserver(refreshAutoColumnLayout);
  if (el.ticketsTable) columnAutoResizeObserver.observe(el.ticketsTable.closest(".table-scroll"));
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

  renderTicketHeader();

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

  el.bulkAssignBtn.addEventListener("click", openAssignModal, { signal: pageSignal });
  if (el.bulkCloseBtn) {
    el.bulkCloseBtn.addEventListener("click", () => openBulkActionModal("close"), { signal: pageSignal });
  }
  if (el.bulkFreezeBtn) {
    el.bulkFreezeBtn.addEventListener("click", () => openBulkActionModal("freeze"), { signal: pageSignal });
  }
  if (el.bulkUnfreezeBtn) {
    el.bulkUnfreezeBtn.addEventListener("click", () => openBulkActionModal("unfreeze"), { signal: pageSignal });
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
        el.confirmBulkActionBtn.disabled = true;
        await runBulkAction();
        closeBulkActionModal();
      } catch (e) {
        el.confirmBulkActionBtn.disabled = false;
        showError(e.message);
      }
    }, { signal: pageSignal });
  }
  if (el.bulkActionModal) closeOnBackdropClick(el.bulkActionModal, closeBulkActionModal);
  if (el.closeColumnsModalBtn) el.closeColumnsModalBtn.addEventListener("click", closeColumnsModal, { signal: pageSignal });
  if (el.saveColumnsBtn) el.saveColumnsBtn.addEventListener("click", saveColumnsModal, { signal: pageSignal });
  el.columnAutoWidthInput?.addEventListener("change", toggleAutoColumnWidth, { signal: pageSignal });
  if (el.editColumnsWidthBtn) el.editColumnsWidthBtn.addEventListener("click", enterColumnResizeMode, { signal: pageSignal });
  if (el.saveResizeColumnsBtn) el.saveResizeColumnsBtn.addEventListener("click", saveColumnResizeDraft, { signal: pageSignal });
  if (el.cancelResizeColumnsBtn) el.cancelResizeColumnsBtn.addEventListener("click", cancelColumnResizeDraft, { signal: pageSignal });
  document.addEventListener("pointermove", updateColumnReorderDrag, { signal: pageSignal });
  document.addEventListener("pointerup", stopColumnReorderDrag, { signal: pageSignal });
  document.addEventListener("pointercancel", stopColumnReorderDrag, { signal: pageSignal });
  document.addEventListener("pointermove", updateColumnResizeDrag, { signal: pageSignal });
  document.addEventListener("pointerup", stopColumnResizeDrag, { signal: pageSignal });
  document.addEventListener("pointercancel", stopColumnResizeDrag, { signal: pageSignal });
  if (el.columnsModal) {
    closeOnBackdropClick(el.columnsModal, closeColumnsModal);
    el.columnsModal.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeColumnsModal();
    }, { signal: pageSignal });
  }
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && el.columnsModal && !el.columnsModal.classList.contains("hidden")) {
      closeColumnsModal();
    }
  }, { signal: pageSignal });
  if (el.columnAddSelect) {
    el.columnAddSelect.addEventListener("change", () => {
      addDraftColumn(el.columnAddSelect.value);
    }, { signal: pageSignal });
  }
  if (el.columnsDraftList) {
    el.columnsDraftList.addEventListener("click", async (event) => {
      const deleteButton = event.target.closest("[data-column-delete]");
      if (deleteButton) {
        await confirmDeleteDraftColumn(deleteButton.dataset.columnDelete || "");
        return;
      }
    }, { signal: pageSignal });
    el.columnsDraftList.addEventListener("pointerdown", startColumnReorderDrag, { signal: pageSignal });
  }

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
  if (el.colorEnabledInput) {
    el.colorEnabledInput.addEventListener("change", async () => {
      await updateColorPreference(el.colorEnabledInput.checked);
    }, { signal: pageSignal });
  }
  if (el.colorToggleText && el.colorLegend) {
    el.colorToggleText.addEventListener("mouseenter", showColorLegend, { signal: pageSignal });
    el.colorToggleText.addEventListener("mouseleave", scheduleColorLegendHide, { signal: pageSignal });
    for (const target of [el.colorToggleText, el.colorEnabledInput]) {
      target?.addEventListener("focus", () => {
        if (target.matches(":focus-visible")) showColorLegend();
      }, { signal: pageSignal });
      target?.addEventListener("blur", scheduleColorLegendHide, { signal: pageSignal });
    }
    el.colorLegend.addEventListener("mouseenter", showColorLegend, { signal: pageSignal });
    el.colorLegend.addEventListener("mouseleave", scheduleColorLegendHide, { signal: pageSignal });
    window.addEventListener("resize", positionColorLegend, { signal: pageSignal });
    document.addEventListener("scroll", positionColorLegend, { capture: true, signal: pageSignal });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") hideColorLegend();
    }, { signal: pageSignal });
  }
  if (el.taskPanelMoreBtn) {
    el.taskPanelMoreBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      state.taskPanelDropdownOpen = !state.taskPanelDropdownOpen;
      el.taskPanelMoreBtn.classList.toggle("active", state.taskPanelDropdownOpen);
      el.taskPanelDropdown?.classList.toggle("hidden", !state.taskPanelDropdownOpen);
    }, { signal: pageSignal });
  }
  document.addEventListener("click", (event) => {
    if (!state.taskPanelDropdownOpen) return;
    if (event.target.closest("#taskPanel")) return;
    closeTaskPanelDropdown();
  }, { signal: pageSignal });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeTaskPanelDropdown();
  }, { signal: pageSignal });

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
  }, 5000);

  if (!window.HelpdeskAppShell?.getCurrentPage) {
    ownsPopState = true;
    window.addEventListener("popstate", handleListUrlChange);
  }

}

async function runTicketsListPage() {
  await window.HelpdeskSidebar.init();
  syncSidebarState(window.HelpdeskSidebar.getState());
  unsubscribeSidebar = window.HelpdeskSidebar.onChange(syncSidebarState);
  init();
  await Promise.all([loadMe(), loadPreferences()]);
  await Promise.all([loadTickets(), loadCounts()]);
}

runTicketsListPage().catch((error) => {
  const message = error?.message || "Ошибка загрузки списка заявок";
  showError(message);
});

return {
  destroy() {
    columnAutoResizeObserver?.disconnect();
    state.columnAutoCalculation = null;
    stopColumnReorderDrag();
    hideColorLegend();
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
