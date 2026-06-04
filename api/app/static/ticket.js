(function () {
function initTicketCardPage() {
// ===== state/elements =====
const params = new URLSearchParams(window.location.search);
const ticketId = window.location.pathname.split("/").pop();
const COMPLETION_OPEN_STORAGE_KEY = "hd_completion_sidebar_open";

function parseReturnPage(value) {
  const parsed = Number.parseInt(String(value || ""), 10);
  return Number.isFinite(parsed) && parsed >= 1 ? parsed : 1;
}

function normalizeTicketsReturnUrl(rawUrl) {
  const value = String(rawUrl || "").trim();
  if (!value) return "";
  try {
    const parsed = new URL(value, window.location.origin);
    if (parsed.origin !== window.location.origin) return "";
    if (!parsed.pathname.startsWith("/tickets")) return "";
    return `${parsed.pathname}${parsed.search}`;
  } catch {
    return "";
  }
}

const SALES_FIRST_DEPARTMENT = new Set([
  "группа Власова",
  "группа Шабурова",
  "группа Сайдаралиева",
  "группа Макаренко",
]);
const SALES_SECOND_DEPARTMENT = new Set([
  "группа Шахалиевой",
  "группа Кочуры",
]);

const state = {
  ticketId,
  role: localStorage.getItem("hd_user_role") || "agent",
  tab: params.get("tab") || localStorage.getItem("hd_tab") || "active",
  returnPage: parseReturnPage(params.get("page")),
  returnQuery: (params.get("q") || "").trim(),
  userId: "",
  isRealAdmin: false,
  roleUsers: [],
  supportAgents: [],
  adminAssigneeIds: JSON.parse(localStorage.getItem("hd_admin_assignee_ids") || "[]"),
  card: null,
  wrongProblemMenu: null,
  quoteMessageId: null,
  composeMode: "reply",
  selectedFiles: [],
  isLoading: false,
  clientCardViewConfig: null,
  counts: { unassigned: 0, active: 0, special: 0, frozen: 0, closed: 0 },
  sessionContext: null,
  completionStats: null,
  completionOpen: false,
  isSendingReply: false,
  pendingCloseAfterSend: false,
  fileUploadProgress: {},
};

const el = {
  backToListBtn: document.getElementById("backToListBtn"),
  roleSelect: document.getElementById("roleSelect"),
  roleControl: document.getElementById("roleControl"),
  agentSelect: document.getElementById("agentSelect"),
  agentControl: document.getElementById("agentControl"),
  statusText: document.getElementById("statusText"),
  ticketSubject: document.getElementById("ticketSubject"),
  ticketStatusBadges: document.getElementById("ticketStatusBadges"),
  instructionBtn: document.getElementById("instructionBtn"),

  clientDynamicCard: document.getElementById("clientDynamicCard"),

  editClientBtn: document.getElementById("editClientBtn"),
  clientModal: document.getElementById("clientModal"),
  closeClientModalBtn: document.getElementById("closeClientModalBtn"),
  saveClientBtn: document.getElementById("saveClientBtn"),
  errorModal: document.getElementById("errorModal"),
  errorText: document.getElementById("errorText"),
  closeErrorModalBtn: document.getElementById("closeErrorModalBtn"),
  copyErrorBtn: document.getElementById("copyErrorBtn"),
  okErrorBtn: document.getElementById("okErrorBtn"),
  editMessageModal: null,
  editMessageTextarea: null,
  saveEditMessageBtn: null,
  cancelEditMessageBtn: null,

  fContactName: document.getElementById("fContactName"),
  fLastName: document.getElementById("fLastName"),
  fDestination: document.getElementById("fDestination"),
  fSandOwnerWrap: document.getElementById("fSandOwnerWrap"),
  fSandOwner: document.getElementById("fSandOwner"),
  fSalesGroupWrap: document.getElementById("fSalesGroupWrap"),
  fSalesGroup: document.getElementById("fSalesGroup"),
  fLocationType: document.getElementById("fLocationType"),
  fAnydeskWrap: document.getElementById("fAnydeskWrap"),
  fAnydesk: document.getElementById("fAnydesk"),
  fInternalNumber: document.getElementById("fInternalNumber"),
  fBitrixLogin: document.getElementById("fBitrixLogin"),
  fBitrixPassword: document.getElementById("fBitrixPassword"),
  fBitrixId: document.getElementById("fBitrixId"),
  fIsVerified: document.getElementById("fIsVerified"),
  customFieldsSection: document.getElementById("customFieldsSection"),
  customFieldsContainer: document.getElementById("customFieldsContainer"),

  messages: document.getElementById("messages"),
  quotePreview: document.getElementById("quotePreview"),
  quotePreviewAuthor: document.getElementById("quotePreviewAuthor"),
  quotePreviewText: document.getElementById("quotePreviewText"),
  clearQuoteBtn: document.getElementById("clearQuoteBtn"),
  messageInput: document.getElementById("messageInput"),
  replyFilesInput: document.getElementById("replyFilesInput"),
  selectedFilesList: document.getElementById("selectedFilesList"),
  addFilesBtn: document.getElementById("addFilesBtn"),
  sendNowBtn: document.getElementById("sendNowBtn"),
  modeReplyBtn: document.getElementById("modeReplyBtn"),
  modeCommentBtn: document.getElementById("modeCommentBtn"),
  freezeUntil: document.getElementById("freezeUntil"),
  autoCloseBox: document.getElementById("autoCloseBox"),
  autoCloseDueText: document.getElementById("autoCloseDueText"),
  autoCloseDisabledCheckbox: document.getElementById("autoCloseDisabledCheckbox"),
  greetingBtn: document.getElementById("greetingBtn"),
  wrongProblemBtn: document.getElementById("wrongProblemBtn"),
  markSpecialBtn: document.getElementById("markSpecialBtn"),
  specialConfirmModal: document.getElementById("specialConfirmModal"),
  closeSpecialConfirmBtn: document.getElementById("closeSpecialConfirmBtn"),
  confirmSpecialBtn: document.getElementById("confirmSpecialBtn"),
  wrongProblemModal: document.getElementById("wrongProblemModal"),
  wrongProblemMenu: document.getElementById("wrongProblemMenu"),
  closeWrongProblemModalBtn: document.getElementById("closeWrongProblemModalBtn"),
  freezeToggleBtn: document.getElementById("freezeToggleBtn"),
  restoreBtn: document.getElementById("restoreBtn"),
  closeBtn: document.getElementById("closeBtn"),
  adminForceCloseBtn: document.getElementById("adminForceCloseBtn"),
  adminDeleteBtn: document.getElementById("adminDeleteBtn"),
  adminActionModal: document.getElementById("adminActionModal"),
  adminActionModalTitle: document.getElementById("adminActionModalTitle"),
  adminActionModalText: document.getElementById("adminActionModalText"),
  closeAdminActionModalBtn: document.getElementById("closeAdminActionModalBtn"),
  cancelAdminActionBtn: document.getElementById("cancelAdminActionBtn"),
  confirmAdminActionBtn: document.getElementById("confirmAdminActionBtn"),
  instructionModal: document.getElementById("instructionModal"),
  instructionTitle: document.getElementById("instructionTitle"),
  instructionBody: document.getElementById("instructionBody"),
  closeInstructionModalBtn: document.getElementById("closeInstructionModalBtn"),
  ticketAssigneeBox: document.getElementById("ticketAssigneeBox"),
  ticketAssigneeSelect: document.getElementById("ticketAssigneeSelect"),
  messageContextMenu: null,
  imagePreviewModal: null,
  imagePreviewImg: null,
  imagePreviewClose: null,
  imagePreviewDownload: null,
  tabLinks: [...document.querySelectorAll("[data-tab-link]")],
  statusButtons: [...document.querySelectorAll("[data-availability-status]")],
  adminOnly: [...document.querySelectorAll(".admin-only")],
  adminAgentFilter: document.getElementById("adminAgentFilter"),
  adminAgentList: document.getElementById("adminAgentList"),
  adminAgentToggleAll: document.getElementById("adminAgentToggleAll"),
  statusPanel: document.querySelector(".status-panel"),
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

state.lastMessagesSignature = null;
const {
  api,
  apiPost,
  apiPatch,
  headers,
  escapeHtml,
  fmtDate,
  fmtMessageStamp,
  formatFileSize,
  linkifyText,
  renderTextWithLinks,
  minutesLeftToDue,
} = window.TicketApi || {};
const TicketAttachments = window.TicketAttachments || {};
const TicketClientCard = window.TicketClientCard || {};
const TicketComposer = window.TicketComposer || {};
const TicketControls = window.TicketControls || {};
const TicketMessageView = window.TicketMessageView || {};

let pollTimer = null;
let unsubscribeSidebar = null;
let keydownHandler = null;
let documentClickHandler = null;
let resizeHandler = null;
let beforeUnloadHandler = null;
let destroyed = false;

function shellNavigateOrReload(urlLike) {
  const url = new URL(urlLike, window.location.origin);
  const href = `${url.pathname}${url.search}${url.hash}`;
  if (url.pathname === "/tickets" && window.HelpdeskAppShell?.navigate) {
    window.HelpdeskAppShell.navigate(href);
    return;
  }
  window.location.href = href;
}

function cleanupTicketCardPage() {
  destroyed = true;
  if (pollTimer !== null) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
  if (typeof unsubscribeSidebar === "function") {
    unsubscribeSidebar();
    unsubscribeSidebar = null;
  }
  if (keydownHandler) {
    document.removeEventListener("keydown", keydownHandler);
    keydownHandler = null;
  }
  if (documentClickHandler) {
    document.removeEventListener("click", documentClickHandler);
    documentClickHandler = null;
  }
  if (resizeHandler) {
    window.removeEventListener("resize", resizeHandler);
    resizeHandler = null;
  }
  if (el.messages) {
    el.messages.removeEventListener("scroll", hideMessageContextMenu);
  }
  if (beforeUnloadHandler) {
    window.removeEventListener("beforeunload", beforeUnloadHandler);
    beforeUnloadHandler = null;
  }
  try {
    TicketAttachments.destroyAllAttachmentResources?.();
  } catch {
    // noop
  }
  if (el.messageContextMenu) {
    el.messageContextMenu.remove();
    el.messageContextMenu = null;
  }
  if (typeof TicketMessageView.cleanupEditMessageModal === "function") {
    TicketMessageView.cleanupEditMessageModal(messageViewCtx());
  } else if (el.editMessageModal) {
    el.editMessageModal.remove();
    el.editMessageModal = null;
    el.editMessageTextarea = null;
    el.saveEditMessageBtn = null;
    el.cancelEditMessageBtn = null;
  }
  if (el.imagePreviewModal) {
    el.imagePreviewModal.remove();
    el.imagePreviewModal = null;
    el.imagePreviewImg = null;
    el.imagePreviewClose = null;
    el.imagePreviewDownload = null;
  }
}

function attachmentsCtx() {
  return {
    el,
    showError,
    closeOnBackdropClick,
    api,
  };
}

function clientCardCtx() {
  return {
    state,
    el,
    api,
    setStatus,
    showError,
    isBlank,
    setControlValue,
    SALES_FIRST_DEPARTMENT,
    SALES_SECOND_DEPARTMENT,
  };
}

function composerCtx() {
  return {
    state,
    el,
    api,
    showError,
    setStatus,
    loadCard,
    action,
    renderAvailabilityStatus,
    messageAuthorLabel,
    isOnsitePhraseJs,
  };
}

function controlsCtx() {
  return {
    state,
    el,
    api,
    apiPost,
    apiPatch,
    escapeHtml,
    minutesLeftToDue,
    setStatus,
    showError,
    setVisible,
    loadCard,
    loadTabCounts,
    action,
    greetingAllowed,
    closeOnBackdropClick,
    openInstructionModal,
    closeInstructionModal,
    assignCurrentTicket,
    openClientModal,
    closeClientModal,
    saveClientProfile,
  };
}

function messageViewCtx() {
  return {
    state,
    el,
    api,
    setStatus,
    showError,
    loadCard,
    closeOnBackdropClick,
    fmtMessageStamp,
    linkifyText,
    quoteMessage,
    isStickerOnlyMessage: TicketAttachments.isStickerOnlyMessage,
    messageRenderSignature: TicketAttachments.messageRenderSignature,
    cleanupAttachmentObjectUrls: TicketAttachments.cleanupAttachmentObjectUrls,
    cleanupStickerResources: TicketAttachments.cleanupStickerResources,
    renderAttachments: (message, container) => TicketAttachments.renderAttachments(attachmentsCtx(), message, container),
  };
}

// ===== modals =====
function setStatus(text) {
  el.statusText.textContent = text;
}

function showError(message) {
  el.errorText.value = message || "Неизвестная ошибка";
  el.errorModal.classList.remove("hidden");
}

function closeError() {
  el.errorModal.classList.add("hidden");
}

function openInstructionModal() {
  const card = state.card;
  if (!card || !card.instruction_text || state.role === "chiki_piki") return;
  if (el.instructionTitle) {
    if (card.instruction_title) {
      el.instructionTitle.textContent = card.instruction_title;
      el.instructionTitle.classList.remove("hidden");
    } else {
      el.instructionTitle.textContent = "";
      el.instructionTitle.classList.add("hidden");
    }
  }
  if (el.instructionBody) renderTextWithLinks(el.instructionBody, card.instruction_text);
  if (el.instructionModal) {
    el.instructionModal.classList.remove("hidden");
  }
}

function closeInstructionModal() {
  if (el.instructionModal) {
    el.instructionModal.classList.add("hidden");
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

function focusMessageInputIfAvailable() {
  if (!el.messageInput) return;
  if (el.messageInput.disabled) return;
  if (document.hidden) return;
  if (el.clientModal && !el.clientModal.classList.contains("hidden")) return;
  if (el.errorModal && !el.errorModal.classList.contains("hidden")) return;
  if (el.wrongProblemModal && !el.wrongProblemModal.classList.contains("hidden")) return;
  if (el.specialConfirmModal && !el.specialConfirmModal.classList.contains("hidden")) return;
  if (el.adminActionModal && !el.adminActionModal.classList.contains("hidden")) return;
  if (el.instructionModal && !el.instructionModal.classList.contains("hidden")) return;
  requestAnimationFrame(() => {
    try {
      el.messageInput.focus({ preventScroll: true });
    } catch {
      el.messageInput.focus();
    }
  });
}

function isOnsitePhraseJs(text) {
  let normalized = String(text || "").trim().toLowerCase();
  if (normalized.endsWith(".")) normalized = normalized.slice(0, -1).trim();
  return normalized === "иду к вам";
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
  }
  el.agentSelect.value = state.userId;
}

function renderTicketAssigneeSelect() {
  return TicketControls.renderTicketAssigneeSelect(controlsCtx());
}

async function openWrongProblemModal() {
  return TicketControls.openWrongProblemModal(controlsCtx());
}

function closeWrongProblemModal() {
  return TicketControls.closeWrongProblemModal(controlsCtx());
}

function openSpecialConfirmModal() {
  return TicketControls.openSpecialConfirmModal(controlsCtx());
}

function closeSpecialConfirmModal() {
  return TicketControls.closeSpecialConfirmModal(controlsCtx());
}

function openAdminActionModal(kind) {
  return TicketControls.openAdminActionModal(controlsCtx(), kind);
}

function closeAdminActionModal() {
  return TicketControls.closeAdminActionModal(controlsCtx());
}

async function runAdminAction() {
  return TicketControls.runAdminAction(controlsCtx());
}

async function markTicketSpecial() {
  return TicketControls.markTicketSpecial(controlsCtx());
}

async function chooseWrongProblem(item) {
  return TicketControls.chooseWrongProblem(controlsCtx(), item);
}

async function loadRoleUsers() {
  if (!state.isRealAdmin || state.role !== "admin") {
    state.roleUsers = [];
    renderAgentSelect();
    return;
  }
  try {
    const users = await api(`/api/v1/users/by-role?role=${encodeURIComponent(state.role)}`);
    state.roleUsers = users || [];
    renderAgentSelect();
  } catch (e) {
    state.roleUsers = [];
    renderAgentSelect();
    console.error("loadRoleUsers failed", e);
  }
}

async function loadSupportAgents() {
  // Sidebar agents are managed by shared sidebar.js
}

function renderAdminAgentFilter() {
  // Sidebar filter is managed by shared sidebar.js
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

async function loadTabCounts(options = {}) {
  const silent = Boolean(options.silent);
  try {
    await window.HelpdeskSidebar.refreshCounts();
    const sidebarState = window.HelpdeskSidebar.getState();
    state.counts = sidebarState.counts || state.counts;
  } catch (e) {
    if (!silent) setStatus(`Ошибка счётчиков: ${e.message}`);
  }
}


function selectedFileKey(file, index) {
  return TicketComposer.selectedFileKey(file, index);
}

function messageAuthorLabel(message) {
  const messageType = typeof message === "string" ? message : message?.message_type;
  const card = state.card || {};
  const profile = card.client_profile || {};
  const fullName = `${profile.last_name || ""} ${profile.contact_name || ""}`.trim();
  const clientName = fullName || profile.telegram_display_name || card.client_name || "Клиент";
  if (typeof message === "object" && message) {
    if (messageType === "client") return clientName;
    if (messageType === "agent" || messageType === "internal") return message.author_name || "Агент";
    if (messageType === "system") return "ES DESK";
  }
  const labels = {
    client: "Клиент",
    agent: "Агент",
    internal: "Комментарий",
    system: "ES DESK",
  };
  return labels[messageType] || messageType || "";
}

// ===== attachments/stickers =====
function renderSelectedFiles() {
  return TicketComposer.renderSelectedFiles(composerCtx());
}

function clearSelectedFiles() {
  return TicketComposer.clearSelectedFiles(composerCtx());
}

function closeImagePreview() {
  return TicketAttachments.closeImagePreview(attachmentsCtx());
}

// ===== composer/reply/comment =====
function refreshComposerActions() {
  return TicketComposer.refreshComposerActions(composerCtx());
}

function quoteMessage(message) {
  return TicketComposer.quoteMessage(composerCtx(), message);
}

function clearQuote() {
  return TicketComposer.clearQuote(composerCtx());
}

// ===== ticket controls =====
function statusLabel(status) {
  const labels = {
    unassigned: "Нераспределённая",
    new: "Новая",
    pending: "В работе",
    closed: "Закрыта",
  };
  return labels[status] || status || "-";
}

function buildTicketHeaderBadges(card) {
  const badges = [];
  const workflowType = (card.workflow_type || "ticket");
  if (workflowType === "ticket") {
    badges.push({ text: "Заявка", cls: "ticket-badge-type-ticket" });
  } else {
    badges.push({ text: "Задача", cls: "ticket-badge-type-task" });
  }

  if (card.is_special) {
    badges.push({ text: "Админская", cls: "ticket-badge-status-special" });
  } else if ((card.status || "") === "unassigned") {
    badges.push({ text: "Нераспределена", cls: "ticket-badge-status-unassigned" });
  } else if ((card.status || "") === "closed") {
    badges.push({ text: "Закрыта", cls: "ticket-badge-status-closed" });
  } else {
    badges.push({ text: "В работе", cls: "ticket-badge-status-active" });
  }

  if (card.is_frozen) {
    badges.push({ text: "Заморожена", cls: "ticket-badge-frozen" });
  }
  return badges;
}

function intakeLabel(stateValue) {
  const labels = {
    collecting: "сбор данных",
    ready: "готово",
    cancelled: "отменено",
  };
  return labels[stateValue] || stateValue || "готово";
}

function greetingAllowed() {
  return true;
}

function setVisible(element, isVisible) {
  if (!element) return;
  element.classList.toggle("hidden", !isVisible);
}

function isBlank(v) {
  return v === null || v === undefined || String(v).trim() === "";
}

function setControlValue(control, value) {
  const normalized = value || "";
  if (!control) return;

  if (control.tagName === "SELECT") {
    const hasValue = [...control.options].some((o) => o.value === normalized);
    if (!hasValue && normalized) {
      const custom = document.createElement("option");
      custom.value = normalized;
      custom.textContent = `${normalized} (из карточки)`;
      control.appendChild(custom);
    }
  }
  control.value = normalized;
}

function applyComposeModeUi() {
  if (typeof TicketComposer.applyComposeModeUi === "function") {
    return TicketComposer.applyComposeModeUi(composerCtx());
  }
}

function defaultClientCardViewConfig() {
  return TicketClientCard.defaultClientCardViewConfig();
}

function renderClientDynamicCard(profile, config) {
  return TicketClientCard.renderClientDynamicCard(clientCardCtx(), profile, config);
}

function fillClientPanel(profile) {
  return TicketClientCard.fillClientPanel(clientCardCtx(), profile);
}

function syncDynamicModalFields() {
  return TicketClientCard.syncDynamicModalFields(clientCardCtx());
}

function fillModal(profile) {
  return TicketClientCard.fillModal(clientCardCtx(), profile);
}

function renderCustomFieldsEditor(profile) {
  return TicketClientCard.renderCustomFieldsEditor(clientCardCtx(), profile);
}

function getModalPayload() {
  return TicketClientCard.getModalPayload(clientCardCtx());
}

function openClientModal() {
  return TicketClientCard.openClientModal(clientCardCtx());
}

function closeClientModal() {
  return TicketClientCard.closeClientModal(clientCardCtx());
}

async function saveClientProfile() {
  return TicketClientCard.saveClientProfile(clientCardCtx());
}

function hideMessageContextMenu() {
  if (typeof TicketMessageView.hideMessageContextMenu === "function") {
    return TicketMessageView.hideMessageContextMenu(messageViewCtx());
  }
  if (!el.messageContextMenu) return;
  el.messageContextMenu.classList.add("hidden");
  el.messageContextMenu.innerHTML = "";
}

function renderMessagesFallback(card) {
  el.messages.innerHTML = "";
  for (const m of card.messages || []) {
    const div = document.createElement("div");
    div.className = `message ${m.message_type || ""}`.trim();
    const headerEl = document.createElement("div");
    headerEl.className = "message-header";
    const strong = document.createElement("strong");
    strong.textContent = m.author_name || (m.message_type === "system" ? "ES DESK" : "Сообщение");
    const dateEl = document.createElement("div");
    dateEl.className = "message-date";
    dateEl.textContent = fmtMessageStamp(m.created_at);
    headerEl.append(strong, dateEl);
    div.appendChild(headerEl);
    if ((m.body || "").trim()) {
      const bodyEl = document.createElement("div");
      bodyEl.className = "message-body";
      bodyEl.innerHTML = linkifyText(m.body || "");
      div.appendChild(bodyEl);
    }
    el.messages.appendChild(div);
  }
  return {
    signature: JSON.stringify((card.messages || []).map((m) => [m.id, m.body, m.created_at, m.message_type])),
    quoteMissing: false,
    wasNearBottom: true,
  };
}

// ===== messages rendering =====
function renderCard() {
  const card = state.card;
  if (!card || typeof card !== "object") {
    throw new Error("Карточка заявки не загружена");
  }
  if (!Array.isArray(card.messages)) {
    card.messages = [];
  }
  const isTask = card.workflow_type === "task";
  el.ticketSubject.textContent = card.subject;
  if (el.ticketStatusBadges) {
    el.ticketStatusBadges.innerHTML = "";
    for (const badge of buildTicketHeaderBadges(card)) {
      const node = document.createElement("span");
      node.className = `ticket-status-badge ${badge.cls}`;
      node.textContent = badge.text;
      el.ticketStatusBadges.appendChild(node);
    }
  }
  fillClientPanel(card.client_profile || {});

  let renderResult;
  try {
    if (typeof TicketMessageView.renderMessages === "function") {
      renderResult = TicketMessageView.renderMessages(messageViewCtx(), card, state.lastMessagesSignature, state.quoteMessageId);
    } else {
      renderResult = renderMessagesFallback(card);
    }
  } catch (e) {
    console.error("renderMessages failed", e);
    renderResult = renderMessagesFallback(card);
  }
  state.lastMessagesSignature = renderResult.signature;
  if (renderResult.quoteMissing) {
    state.quoteMessageId = null;
  }
  refreshComposerActions();

  const isClosed = card.status === "closed";
  const isReadonly = state.role === "chiki_piki";

  el.modeReplyBtn.disabled = isClosed || isReadonly;
  el.modeCommentBtn.disabled = isClosed || isReadonly;
  el.messageInput.disabled = isClosed || isReadonly;
  el.messageInput.placeholder = isReadonly ? "Режим только для чтения" : "Текст ответа/комментария";
  el.sendNowBtn.disabled = isClosed || isReadonly;
  if (el.addFilesBtn) el.addFilesBtn.disabled = isClosed || isReadonly || state.composeMode !== "reply";

  TicketControls.renderTicketControlsView(controlsCtx());
  if (renderResult.wasNearBottom) {
    el.messages.scrollTop = el.messages.scrollHeight;
  }
}

async function loadCard(options = {}) {
  if (destroyed || state.isLoading) return;
  state.isLoading = true;
  const silent = Boolean(options.silent);
  if (!silent) setStatus("Загружаю карточку...");

  try {
    const [card, cardConfig] = await Promise.all([
      api(`/api/v1/tickets/${state.ticketId}`),
      api(`/api/v1/tickets/${state.ticketId}/client-card-view-config`).catch(() => null),
    ]);
    if (!card || typeof card !== "object") {
      throw new Error("API вернул пустую карточку заявки");
    }
    if (!Array.isArray(card.messages)) {
      card.messages = [];
    }
    if (!card.client_profile || typeof card.client_profile !== "object") {
      card.client_profile = {};
    }
    if (destroyed) return;
    state.card = card;
    state.clientCardViewConfig = cardConfig && Array.isArray(cardConfig.blocks)
      ? cardConfig
      : defaultClientCardViewConfig();
    renderCard();
    applyComposeModeUi();
    focusMessageInputIfAvailable();
    setStatus("Карточка загружена");
  } catch (e) {
    console.error("loadCard failed", e);
    if (!silent) setStatus(`Ошибка: ${e.message}`);
    if (!silent) showError(e.message || "Ошибка загрузки карточки");
  } finally {
    state.isLoading = false;
  }
}

async function sendByMode() {
  return TicketComposer.sendByMode(composerCtx());
}

async function action(name, body = null) {
  if (name === "close" && state.isSendingReply) {
    state.pendingCloseAfterSend = true;
    setStatus("Сначала завершается отправка файлов, затем задача будет закрыта.");
    return;
  }
  try {
    setStatus("Выполняю действие...");
    await apiPost(`/api/v1/tickets/${state.ticketId}/${name}`, body);
    await loadCard();
    setStatus("Готово");
  } catch (e) {
    showError(e.message);
  }
}

async function assignCurrentTicket(assigneeId) {
  try {
    setStatus("Меняю ответственного...");
    await apiPost("/api/v1/tickets/assign", {
      ticket_ids: [state.card?.id || state.ticketId],
      assignee_id: assigneeId || null,
    });
    await loadCard();
    setStatus("Ответственный изменён");
  } catch (e) {
    showError(e.message);
    await loadCard();
  }
}

function updateTabLinks() {
  const isAdmin = state.role === "admin";
  const isReadonly = state.role === "chiki_piki";
  el.adminOnly.forEach((node) => {
    node.classList.toggle("hidden", !isAdmin);
  });
  if (el.statusPanel) {
    el.statusPanel.classList.toggle("hidden", isAdmin || isReadonly);
  }
  if (el.adminAgentFilter) {
    el.adminAgentFilter.classList.toggle("hidden", !isAdmin);
  }
  if (el.roleControl) el.roleControl.classList.add("hidden");
  if (el.agentControl) el.agentControl.classList.add("hidden");
  const unassignedLink = el.tabLinks.find((link) => link.dataset.tabLink === "unassigned");
  const specialLink = el.tabLinks.find((link) => link.dataset.tabLink === "special");
  const frozenLink = el.tabLinks.find((link) => link.dataset.tabLink === "frozen");
  if (unassignedLink) {
    unassignedLink.classList.toggle("hidden", !(isAdmin || isReadonly));
  }
  if (specialLink) {
    specialLink.classList.toggle("hidden", !isAdmin);
  }
  if (!(isAdmin || isReadonly) && state.tab === "unassigned") {
    state.tab = "active";
    localStorage.setItem("hd_tab", state.tab);
  }
  if (!isAdmin && state.tab === "special") {
    state.tab = "active";
    localStorage.setItem("hd_tab", state.tab);
  }
  el.tabLinks.forEach((link) => {
    const tab = link.dataset.tabLink;
    link.classList.toggle("active", tab === state.tab);
    link.href = `/tickets?tab=${encodeURIComponent(tab)}`;
  });
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

function syncSidebarState(sidebarState) {
  if (destroyed || !sidebarState) return;
  const prevRole = state.role;
  const prevUserId = state.userId;
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
  if (el.agentSelect) el.agentSelect.value = state.userId;
  renderAgentSelect();
  updateTabLinks();
  renderCompletionStats();
  if ((prevRole !== state.role || prevUserId !== state.userId) && state.card) {
    loadCard({ silent: true });
  }
}

async function updateAvailabilityStatus(status) {
  try {
    renderAvailabilityStatus(status);
    const me = await apiPatch("/api/v1/users/me/status", { availability_status: status });
    renderAvailabilityStatus(me.availability_status);
    setStatus("Статус обновлён");
  } catch (e) {
    showError(`Ошибка статуса: ${e.message}`);
    await loadMe();
  }
}

// ===== init/events =====
function init() {
  el.messageContextMenu = document.createElement("div");
  el.messageContextMenu.className = "message-context-menu hidden";
  document.body.appendChild(el.messageContextMenu);

  if (!["active", "unassigned", "special", "frozen", "closed"].includes(state.tab)) {
    state.tab = "active";
  }
  localStorage.setItem("hd_tab", state.tab);

  el.roleSelect.value = state.role;
  el.agentSelect.value = state.userId;
  updateTabLinks();
  applyComposeModeUi();

  el.roleSelect.addEventListener("change", async () => {
    // TODO(remove-before-production): admin preview mode is temporarily disabled.
    return;
  });

  el.backToListBtn.addEventListener("click", () => {
    const queryReturnUrl = normalizeTicketsReturnUrl(params.get("return_url"));
    if (queryReturnUrl) {
      shellNavigateOrReload(queryReturnUrl);
      return;
    }
    const savedReturnUrl = normalizeTicketsReturnUrl(sessionStorage.getItem("ticketListReturnUrl"));
    if (savedReturnUrl) {
      shellNavigateOrReload(savedReturnUrl);
      return;
    }
    const listUrl = new URL("/tickets", window.location.origin);
    listUrl.searchParams.set("tab", state.tab || "active");
    listUrl.searchParams.set("page", String(state.returnPage || 1));
    if (state.returnQuery) listUrl.searchParams.set("q", state.returnQuery);
    shellNavigateOrReload(listUrl.toString());
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

  if (typeof TicketComposer.bindComposerHandlers === "function") {
    TicketComposer.bindComposerHandlers(composerCtx());
  }

  if (typeof TicketControls.bindTicketControlHandlers === "function") {
    TicketControls.bindTicketControlHandlers(controlsCtx());
  }

  el.closeErrorModalBtn.addEventListener("click", closeError);
  el.okErrorBtn.addEventListener("click", closeError);
  el.copyErrorBtn.addEventListener("click", async () => {
    el.errorText.select();
    try {
      await navigator.clipboard.writeText(el.errorText.value);
      setStatus("Текст ошибки скопирован");
    } catch {
      document.execCommand("copy");
      setStatus("Текст ошибки скопирован");
    }
  });
  closeOnBackdropClick(el.errorModal, closeError);

  el.fDestination.addEventListener("change", syncDynamicModalFields);
  el.fLocationType.addEventListener("change", syncDynamicModalFields);

  keydownHandler = (e) => {
    if (e.key === "Escape") {
      hideMessageContextMenu();
      closeImagePreview();
    }
    if (e.key === "Escape" && !el.errorModal.classList.contains("hidden")) {
      closeError();
      return;
    }
    if (e.key === "Escape" && !el.clientModal.classList.contains("hidden")) {
      closeClientModal();
      return;
    }
    if (e.key === "Escape" && !el.wrongProblemModal.classList.contains("hidden")) {
      closeWrongProblemModal();
    }
    if (e.key === "Escape" && el.adminActionModal && !el.adminActionModal.classList.contains("hidden")) {
      closeAdminActionModal();
    }
    if (e.key === "Escape" && el.instructionModal && !el.instructionModal.classList.contains("hidden")) {
      closeInstructionModal();
    }
  };
  document.addEventListener("keydown", keydownHandler);

  documentClickHandler = hideMessageContextMenu;
  resizeHandler = hideMessageContextMenu;
  document.addEventListener("click", documentClickHandler);
  window.addEventListener("resize", resizeHandler);
  el.messages.addEventListener("scroll", hideMessageContextMenu);

  pollTimer = window.setInterval(() => {
    if (document.hidden) return;
    if (!el.clientModal.classList.contains("hidden")) return;
    loadCard({ silent: true });
    loadTabCounts({ silent: true });
    loadCompletionStats({ silent: true });
  }, 3000);

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

  beforeUnloadHandler = () => {
    TicketAttachments.destroyAllAttachmentResources();
  };
  window.addEventListener("beforeunload", beforeUnloadHandler);
}

(async function run() {
  await window.HelpdeskSidebar.init();
  if (destroyed) return;
  syncSidebarState(window.HelpdeskSidebar.getState());
  unsubscribeSidebar = window.HelpdeskSidebar.onChange(syncSidebarState);
  init();
  if (destroyed) return;
  if (state.isRealAdmin) {
    await loadRoleUsers();
  } else {
    renderAgentSelect();
  }
  try {
    await loadMe();
  } catch (e) {
    console.error("loadMe failed", e);
  }
  if (destroyed) return;
  await Promise.all([loadCard(), loadTabCounts(), loadCompletionStats()]);
})();

document.querySelectorAll("#appContent input, #appContent textarea").forEach((field) => {
  field.setAttribute("autocomplete", "off");
  field.setAttribute("autocorrect", "off");
  field.setAttribute("autocapitalize", "off");
  field.setAttribute("spellcheck", "false");
});

return cleanupTicketCardPage;
}

if (window.HelpdeskAppShell?.registerPage) {
  window.HelpdeskAppShell.registerPage("ticket-card", initTicketCardPage);
} else {
  initTicketCardPage();
}
})();
