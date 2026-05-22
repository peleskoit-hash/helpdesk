// ===== state/elements =====
const params = new URLSearchParams(window.location.search);
const ticketId = window.location.pathname.split("/").pop();
const COMPLETION_OPEN_STORAGE_KEY = "hd_completion_sidebar_open";

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

let editMessageResolve = null;
let imagePreviewScale = 1;
let imagePreviewAttachment = null;
state.lastMessagesSignature = null;
let pendingAdminAction = null;
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

function attachmentsCtx() {
  return {
    el,
    showError,
    closeOnBackdropClick,
    api,
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

function assigneeOptionsHtml(selectedId) {
  const options = [`<option value="" ${selectedId ? "" : "selected"}>Распределение</option>`];
  const hasSelectedInSupport = Boolean(
    selectedId && state.supportAgents.some((agent) => agent.id === selectedId)
  );
  if (selectedId && !hasSelectedInSupport) {
    const currentAssigneeLabel =
      state.card?.special_assignee_name ||
      state.card?.assignee_name ||
      "Текущий ответственный";
    options.push(
      `<option value="${escapeHtml(selectedId)}" selected>${escapeHtml(currentAssigneeLabel)}</option>`
    );
  }
  for (const agent of state.supportAgents) {
    options.push(
      `<option value="${escapeHtml(agent.id)}" ${agent.id === selectedId ? "selected" : ""}>${escapeHtml(agent.full_name)}</option>`
    );
  }
  return options.join("");
}

function renderTicketAssigneeSelect() {
  if (!state.card) return;
  el.ticketAssigneeSelect.innerHTML = assigneeOptionsHtml(state.card.assignee_id);
  if (state.card.assignee_id) {
    el.ticketAssigneeSelect.value = state.card.assignee_id;
  }
}

function isFinalProblemButton(item) {
  return item && item.kind !== "submenu";
}

function renderWrongProblemRows(rows, container) {
  container.innerHTML = "";
  for (const row of rows || []) {
    const rowEl = document.createElement("div");
    rowEl.className = "wrong-problem-row";
    for (const item of row.buttons || []) {
      const wrap = document.createElement("div");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `wrong-problem-item ${item.kind === "submenu" ? "submenu" : ""}`.trim();
      btn.textContent = item.kind === "submenu" ? `${item.label} ▾` : item.label;
      wrap.appendChild(btn);

      if (item.kind === "submenu") {
        const children = document.createElement("div");
        children.className = "wrong-problem-children hidden";
        renderWrongProblemRows(item.children || [], children);
        btn.addEventListener("click", () => children.classList.toggle("hidden"));
        wrap.appendChild(children);
      } else {
        btn.addEventListener("click", () => chooseWrongProblem(item));
      }

      rowEl.appendChild(wrap);
    }
    container.appendChild(rowEl);
  }
}

async function openWrongProblemModal() {
  try {
    setStatus("Загружаю меню клиента...");
    state.wrongProblemMenu = await api(`/api/v1/tickets/${state.ticketId}/problem-menu`);
    renderWrongProblemRows(state.wrongProblemMenu.rows || [], el.wrongProblemMenu);
    el.wrongProblemModal.classList.remove("hidden");
    setStatus("Выберите правильный пункт меню");
  } catch (e) {
    showError(e.message);
  }
}

function closeWrongProblemModal() {
  el.wrongProblemModal.classList.add("hidden");
}

function openSpecialConfirmModal() {
  if (!el.specialConfirmModal) return;
  el.specialConfirmModal.classList.remove("hidden");
}

function closeSpecialConfirmModal() {
  if (!el.specialConfirmModal) return;
  el.specialConfirmModal.classList.add("hidden");
}

function openAdminActionModal(kind) {
  pendingAdminAction = kind;
  const isDelete = kind === "delete";
  const isTask = (state.card?.workflow_type || "ticket") === "task";
  el.adminActionModalTitle.textContent = isDelete ? "Удаление заявки" : "Принудительное завершение";
  el.adminActionModalText.textContent = isDelete
    ? "Вы уверены, что хотите удалить заявку?"
    : (isTask
      ? "Вы уверены, что хотите принудительно завершить задачу?"
      : "Вы уверены, что хотите принудительно закрыть заявку?");
  el.confirmAdminActionBtn.classList.toggle("danger", isDelete);
  el.confirmAdminActionBtn.classList.toggle("primary", !isDelete);
  el.adminActionModal.classList.remove("hidden");
}

function closeAdminActionModal() {
  pendingAdminAction = null;
  el.adminActionModal.classList.add("hidden");
}

async function runAdminAction() {
  if (!state.card || !pendingAdminAction) return;
  const payload = { ticket_ids: [state.card.id] };
  if (pendingAdminAction === "close") {
    setStatus("Принудительно закрываю заявку...");
    const result = await apiPost("/api/v1/tickets/admin/close", payload);
    await Promise.all([loadCard(), loadTabCounts()]);
    setStatus(`Закрыто: ${result.closed}, уже закрыты: ${result.already_closed}`);
    return;
  }
  setStatus("Удаляю заявку...");
  const result = await apiPost("/api/v1/tickets/admin/delete", payload);
  if (result.file_delete_warnings?.length) {
    showError(`Заявка удалена.\nПредупреждения:\n${result.file_delete_warnings.join("\n")}`);
  }
  window.location.href = `/tickets?tab=${encodeURIComponent(state.tab)}`;
}

async function markTicketSpecial() {
  try {
    setStatus("Перевожу заявку в особые...");
    await apiPost(`/api/v1/tickets/${state.ticketId}/special`);
    closeSpecialConfirmModal();
    await Promise.all([loadCard(), loadTabCounts()]);
    setStatus("Заявка переведена в особые");
  } catch (e) {
    showError(e.message);
  }
}

async function chooseWrongProblem(item) {
  if (!isFinalProblemButton(item)) return;
  try {
    setStatus("Отправляю подсказку...");
    await apiPost(`/api/v1/tickets/${state.ticketId}/wrong-problem`, { button_key: item.key });
    closeWrongProblemModal();
    await loadCard();
    if (state.card?.workflow_type === "task") {
      setStatus("Подсказка сохранена. Клиент получит её после завершения задачи.");
    } else {
      setStatus("Подсказка отправлена клиенту");
    }
  } catch (e) {
    showError(e.message);
    if ((e.message || "").includes("уже использована")) {
      await loadCard({ silent: true });
    }
  }
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


function clientDisplayName() {
  const profile = state.card?.client_profile || {};
  const fullName = `${profile.last_name || ""} ${profile.contact_name || ""}`.trim();
  if (fullName) return fullName;
  if (profile.telegram_display_name) return profile.telegram_display_name;
  return state.card?.client_name || "Клиент";
}

function normalizeAgentLabel(name) {
  if (!name) return "Агент";
  return name;
}


function messageAuthorLabel(message) {
  const messageType = typeof message === "string" ? message : message?.message_type;
  if (typeof message === "object" && message) {
    if (messageType === "client") return clientDisplayName();
    if (messageType === "agent") return normalizeAgentLabel(message.author_name);
    if (messageType === "internal") return normalizeAgentLabel(message.author_name);
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

function messageStatusNote(message) {
  if (message.message_status === "edited") {
    return `${messageAuthorLabel(message)} исправил сообщение`;
  }
  if (message.message_status === "deleted") {
    return `${messageAuthorLabel(message)} удалил сообщение`;
  }
  return message.status_note || "";
}

function selectedFileKey(file, index) {
  return `${index}:${file?.name || "file"}:${file?.size || 0}:${file?.lastModified || 0}`;
}

// ===== attachments/stickers =====
function renderSelectedFiles() {
  if (!el.selectedFilesList) return;
  el.selectedFilesList.innerHTML = "";
  el.selectedFilesList.classList.toggle("hidden", state.selectedFiles.length === 0);

  state.selectedFiles.forEach((file, index) => {
    const fileKey = selectedFileKey(file, index);
    const progress = state.fileUploadProgress[fileKey];
    const item = document.createElement("div");
    item.className = "selected-file-item";

    const meta = document.createElement("div");
    meta.className = "selected-file-meta";
    const name = document.createElement("div");
    name.className = "selected-file-name";
    name.textContent = file.name || "Файл";
    const size = document.createElement("div");
    size.className = "selected-file-size";
    size.textContent = formatFileSize(file.size);
    if (progress) {
      const stageText = progress.stage === "sending"
        ? `Отправляется в Telegram (${progress.current || 0}/${progress.total || 0})`
        : progress.stage === "done"
          ? "Отправлено"
          : progress.stage === "queued"
            ? "Ожидает отправки"
            : progress.stage === "error"
              ? `Ошибка: ${progress.message || "не удалось отправить"}`
              : "";
      if (stageText) size.textContent = `${size.textContent} · ${stageText}`;
    }
    meta.append(name, size);
    item.append(meta);

    if (progress && progress.stage === "done") {
      item.classList.add("selected-file-item-ready");
    } else {
      item.classList.remove("selected-file-item-ready");
    }

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "selected-file-remove";
    remove.textContent = "Убрать";
    remove.addEventListener("click", () => {
      state.selectedFiles.splice(index, 1);
      state.fileUploadProgress = {};
      state.selectedFiles.forEach((nextFile, nextIndex) => {
        state.fileUploadProgress[selectedFileKey(nextFile, nextIndex)] = { stage: "queued", percent: 0 };
      });
      if (el.replyFilesInput) el.replyFilesInput.value = "";
      renderSelectedFiles();
    });

    item.append(remove);
    el.selectedFilesList.appendChild(item);
  });
}

function clearSelectedFiles() {
  state.selectedFiles = [];
  state.fileUploadProgress = {};
  if (el.replyFilesInput) el.replyFilesInput.value = "";
  renderSelectedFiles();
}

function isStickerOnlyMessage(message) {
  return TicketAttachments.isStickerOnlyMessage(message);
}

function messageRenderSignature(messages) {
  return TicketAttachments.messageRenderSignature(messages);
}

function cleanupAttachmentObjectUrls(currentAttachmentIds) {
  return TicketAttachments.cleanupAttachmentObjectUrls(currentAttachmentIds);
}

function cleanupStickerResources(currentAttachmentIds = new Set()) {
  return TicketAttachments.cleanupStickerResources(currentAttachmentIds);
}

function closeImagePreview() {
  return TicketAttachments.closeImagePreview(attachmentsCtx());
}

function renderAttachments(message, container) {
  return TicketAttachments.renderAttachments(attachmentsCtx(), message, container);
}

// ===== composer/reply/comment =====
function canEditAgentMessage(message) {
  return state.role !== "chiki_piki" && message.message_type === "agent" && message.message_status !== "deleted" && state.card?.status !== "closed";
}

function quotedMessage() {
  return (state.card?.messages || []).find((m) => m.id === state.quoteMessageId) || null;
}

function refreshComposerActions() {
  const quoted = quotedMessage();

  if (quoted) {
    el.quotePreviewAuthor.textContent = messageAuthorLabel(quoted);
    el.quotePreviewText.textContent = quoted.body || "";
    el.quotePreview.classList.remove("hidden");
  } else {
    el.quotePreview.classList.add("hidden");
    el.quotePreviewAuthor.textContent = "";
    el.quotePreviewText.textContent = "";
  }
}

function quoteMessage(message) {
  state.quoteMessageId = message.id;
  el.messageInput.focus();
  if (message.telegram_message_id) {
    setStatus(`Ответ будет отправлен в Telegram как reply на сообщение: ${messageAuthorLabel(message)}`);
  } else {
    setStatus("Цитата выбрана только в helpdesk: у сообщения нет Telegram ID для reply в Telegram");
  }
  refreshComposerActions();
}

function clearQuote() {
  state.quoteMessageId = null;
  refreshComposerActions();
}

function ensureEditMessageModal() {
  if (el.editMessageModal) return;

  const modal = document.createElement("div");
  modal.className = "modal hidden";

  const dialog = document.createElement("div");
  dialog.className = "modal-dialog edit-message-dialog";

  const head = document.createElement("div");
  head.className = "modal-head";
  const title = document.createElement("div");
  title.style.fontWeight = "700";
  title.textContent = "Редактирование сообщения";
  const cancelTop = document.createElement("button");
  cancelTop.type = "button";
  cancelTop.className = "action";
  cancelTop.textContent = "Закрыть";
  head.append(title, cancelTop);

  const body = document.createElement("div");
  body.className = "modal-body";
  const textarea = document.createElement("textarea");
  textarea.className = "edit-message-textarea";
  textarea.placeholder = "Новый текст сообщения";
  body.appendChild(textarea);

  const foot = document.createElement("div");
  foot.className = "modal-foot";
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "action";
  cancel.textContent = "Отмена";
  const save = document.createElement("button");
  save.type = "button";
  save.className = "action primary";
  save.textContent = "Сохранить";
  foot.append(cancel, save);

  dialog.append(head, body, foot);
  modal.appendChild(dialog);
  document.body.appendChild(modal);

  function close(value) {
    modal.classList.add("hidden");
    const resolve = editMessageResolve;
    editMessageResolve = null;
    if (resolve) resolve(value);
  }

  cancelTop.addEventListener("click", () => close(null));
  cancel.addEventListener("click", () => close(null));
  save.addEventListener("click", () => close(textarea.value));
  closeOnBackdropClick(modal, () => close(null));
  textarea.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      close(null);
    }
    if (event.key === "Enter" && event.ctrlKey) {
      event.preventDefault();
      close(textarea.value);
    }
  });

  el.editMessageModal = modal;
  el.editMessageTextarea = textarea;
  el.saveEditMessageBtn = save;
  el.cancelEditMessageBtn = cancel;
}

function openEditMessageModal(initialText) {
  ensureEditMessageModal();
  if (editMessageResolve) {
    editMessageResolve(null);
  }
  el.editMessageTextarea.value = initialText || "";
  el.editMessageModal.classList.remove("hidden");
  el.editMessageTextarea.focus();
  el.editMessageTextarea.setSelectionRange(0, el.editMessageTextarea.value.length);
  return new Promise((resolve) => {
    editMessageResolve = resolve;
  });
}

async function editAgentMessage(message) {
  const text = await openEditMessageModal(message.body || "");
  if (text === null) return;
  const trimmed = text.trim();
  if (!trimmed) return;
  try {
    setStatus("Редактирую сообщение...");
    await api(`/api/v1/tickets/${state.ticketId}/messages/${message.id}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text: trimmed }),
    });
    await loadCard();
    setStatus("Сообщение исправлено");
  } catch (e) {
    showError(e.message);
  }
}

async function deleteAgentMessage(message) {
  if (!window.confirm("Пометить сообщение удалённым и удалить его в Telegram, если возможно?")) return;
  try {
    setStatus("Удаляю сообщение...");
    await api(`/api/v1/tickets/${state.ticketId}/messages/${message.id}`, { method: "DELETE" });
    await loadCard();
    setStatus("Сообщение помечено удалённым");
  } catch (e) {
    showError(e.message);
  }
}

async function copyMessageText(message) {
  const text = message.body || "";
  try {
    await navigator.clipboard.writeText(text);
    setStatus("Текст сообщения скопирован");
  } catch {
    const tmp = document.createElement("textarea");
    tmp.value = text;
    tmp.style.position = "fixed";
    tmp.style.left = "-9999px";
    document.body.appendChild(tmp);
    tmp.select();
    document.execCommand("copy");
    tmp.remove();
    setStatus("Текст сообщения скопирован");
  }
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

function freezeLabel(card) {
  if (!card.is_frozen) return "нет";
  if (!card.freeze_until) return "да";
  return `до ${fmtDate(card.freeze_until)}`;
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

function hasGreetingMessage(messages) {
  return messages.some((m) => m.message_type === "agent" && /^Добр/i.test((m.body || "").trim()));
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
  const isReply = state.composeMode === "reply";
  el.modeReplyBtn.classList.toggle("primary", isReply);
  el.modeCommentBtn.classList.toggle("primary", !isReply);
  el.messageInput.classList.toggle("comment-mode", !isReply);
  el.messageInput.placeholder = isReply ? "Текст ответа клиенту" : "Внутренний комментарий";
  if (el.addFilesBtn) {
    el.addFilesBtn.disabled = !isReply || Boolean(state.card?.status === "closed");
    el.addFilesBtn.title = isReply ? "Прикрепить файлы к ответу клиенту" : "Файлы доступны только для ответа клиенту";
  }
}

function setRowValue(rowEl, valueEl, value) {
  const show = !isBlank(value);
  rowEl.classList.toggle("hidden", !show);
  if (show) {
    valueEl.textContent = String(value).trim();
  } else {
    valueEl.textContent = "";
  }
}

// ===== client card =====
function defaultClientCardViewConfig() {
  return {
    blocks: [
      {
        key: "tg",
        title: "Telegram",
        color_scheme: "blue",
        sort_order: 10,
        is_active: true,
        fields: [
          { key: "telegram_username", label: "@username", field_type: "readonly", sort_order: 10, is_visible: true, is_editable: false, is_system: true },
          { key: "telegram_display_name", label: "Имя в TG", field_type: "readonly", sort_order: 20, is_visible: true, is_editable: false, is_system: true },
        ],
      },
      {
        key: "profile",
        title: "Профиль",
        color_scheme: "green",
        sort_order: 20,
        is_active: true,
        fields: [
          { key: "last_name", label: "Фамилия", field_type: "text", sort_order: 10, is_visible: true, is_editable: true, is_system: true },
          { key: "contact_name", label: "Имя", field_type: "text", sort_order: 20, is_visible: true, is_editable: true, is_system: true },
        ],
      },
      {
        key: "department",
        title: "Отдел",
        color_scheme: "blue",
        sort_order: 30,
        is_active: true,
        fields: [
          { key: "destination", label: "Куда..?", field_type: "select", sort_order: 10, is_visible: true, is_editable: true, is_system: true },
          { key: "sand_owner", label: "Чей песок", field_type: "select", sort_order: 20, is_visible: true, is_editable: true, is_system: true },
          { key: "sales_group", label: "Группа отдела продаж", field_type: "select", sort_order: 30, is_visible: true, is_editable: true, is_system: true },
          { key: "department", label: "Отдел", field_type: "text", sort_order: 40, is_visible: true, is_editable: true, is_system: true },
          { key: "first_department", label: "Департамент", field_type: "text", sort_order: 50, is_visible: true, is_editable: true, is_system: true },
          { key: "group_name", label: "Группа", field_type: "text", sort_order: 60, is_visible: true, is_editable: true, is_system: true },
          { key: "location_type", label: "Расположение", field_type: "select", sort_order: 70, is_visible: true, is_editable: true, is_system: true },
          { key: "anydesk", label: "Anydesk", field_type: "text", sort_order: 80, is_visible: true, is_editable: true, is_system: true },
        ],
      },
      {
        key: "work",
        title: "Рабочие данные",
        color_scheme: "yellow",
        sort_order: 40,
        is_active: true,
        fields: [
          { key: "internal_number", label: "Внутренний номер", field_type: "text", sort_order: 10, is_visible: true, is_editable: true, is_system: true },
          { key: "bitrix_login", label: "Логин Bitrix", field_type: "text", sort_order: 20, is_visible: true, is_editable: true, is_system: true },
          { key: "bitrix_password", label: "Пароль Bitrix", field_type: "password", sort_order: 30, is_visible: true, is_editable: true, is_system: true },
          { key: "bitrix_id", label: "ID в Bitrix", field_type: "text", sort_order: 40, is_visible: true, is_editable: true, is_system: true },
        ],
      },
      {
        key: "verification",
        title: "Верификация",
        color_scheme: "red",
        sort_order: 50,
        is_active: true,
        fields: [
          { key: "is_verified", label: "Верификация", field_type: "checkbox", sort_order: 10, is_visible: true, is_editable: true, is_system: true },
        ],
      },
    ],
  };
}

function clientCardColorClass(colorScheme) {
  const value = String(colorScheme || "gray").trim().toLowerCase();
  const known = new Set(["blue", "green", "yellow", "red", "gray", "purple"]);
  return `client-card-block-${known.has(value) ? value : "gray"}`;
}

function getClientFieldValue(profile, key) {
  if (!profile || !key) return null;
  if (key === "telegram_username") return profile.telegram_username || null;
  if (key === "telegram_display_name") return profile.telegram_display_name || null;
  if (key === "is_verified") return Boolean(profile.is_verified);
  if (String(key).startsWith("custom_")) {
    return (profile.custom_fields || {})[key] ?? null;
  }
  return profile[key] ?? null;
}

function shouldRenderClientField(field, value) {
  const key = field?.key || "";
  const fieldType = String(field?.field_type || "").toLowerCase();
  if (key === "is_verified") return true;
  if (key === "telegram_username" || key === "telegram_display_name") return true;
  if (fieldType === "checkbox") return true;
  return !isBlank(value);
}

function renderClientFieldValue(field, value) {
  const fieldKey = field?.key || "";
  const fieldType = String(field?.field_type || "").toLowerCase();
  const valueEl = document.createElement("div");
  valueEl.className = "client-card-field-value";

  if (fieldKey === "telegram_username") {
    const usernameRaw = String(value || "").trim();
    if (!usernameRaw) {
      valueEl.textContent = "нет";
      return valueEl;
    }
    const username = usernameRaw.replace(/^@/, "");
    const link = document.createElement("a");
    link.href = `tg://resolve?domain=${encodeURIComponent(username)}`;
    link.textContent = `@${username}`;
    valueEl.appendChild(link);
    return valueEl;
  }

  if (fieldKey === "telegram_display_name") {
    valueEl.textContent = isBlank(value) ? "нет" : String(value).trim();
    return valueEl;
  }

  if (fieldKey === "is_verified") {
    valueEl.textContent = value ? "✓" : "✕";
    valueEl.classList.add(value ? "verified-ok" : "verified-no");
    return valueEl;
  }

  if (fieldType === "checkbox") {
    valueEl.textContent = value ? "✓" : "✕";
    valueEl.classList.add(value ? "verified-ok" : "verified-no");
    return valueEl;
  }

  if (fieldType === "url") {
    const urlText = String(value ?? "").trim();
    if (!urlText) return valueEl;
    const link = document.createElement("a");
    link.href = urlText;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = urlText;
    valueEl.appendChild(link);
    return valueEl;
  }

  valueEl.textContent = String(value ?? "").trim();
  if (fieldKey === "bitrix_password" || fieldType === "password") {
    valueEl.classList.add("client-card-password");
  }
  return valueEl;
}

function renderClientDynamicCard(profile, config) {
  if (!el.clientDynamicCard) return;
  el.clientDynamicCard.innerHTML = "";
  const blocks = (config?.blocks || [])
    .filter((block) => block && block.is_active !== false)
    .sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0));

  for (const block of blocks) {
    const fields = (block.fields || [])
      .filter((field) => field && field.is_visible !== false)
      .sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0));
    const blockEl = document.createElement("div");
    blockEl.className = `client-card-block ${clientCardColorClass(block.color_scheme)}`;

    let renderedFields = 0;
    for (const field of fields) {
      const value = getClientFieldValue(profile, field.key);
      if (!shouldRenderClientField(field, value)) continue;

      const rowEl = document.createElement("div");
      rowEl.className = "client-card-field";

      if (field.key === "is_verified") {
        rowEl.classList.add("client-card-field-verification");
      } else {
        const labelEl = document.createElement("div");
        labelEl.className = "client-card-field-label";
        labelEl.textContent = field.label || field.key;
        rowEl.appendChild(labelEl);
      }

      rowEl.appendChild(renderClientFieldValue(field, value));
      blockEl.appendChild(rowEl);
      renderedFields += 1;
    }

    if (renderedFields > 0 || block.key === "verification") {
      el.clientDynamicCard.appendChild(blockEl);
    }
  }
}

function fillClientPanel(profile) {
  const effectiveConfig = state.clientCardViewConfig || defaultClientCardViewConfig();
  renderClientDynamicCard(profile, effectiveConfig);
}

function syncDynamicModalFields() {
  const destination = el.fDestination.value;
  const isSand = destination === "Песок";
  const isSales = destination === "Отдел Продаж";

  el.fSandOwnerWrap.classList.toggle("hidden", !isSand);
  el.fSalesGroupWrap.classList.toggle("hidden", !isSales);

  if (!isSand) el.fSandOwner.value = "";
  if (!isSales) el.fSalesGroup.value = "";

  const isRemote = el.fLocationType.value === "Удалённый";
  el.fAnydeskWrap.classList.toggle("hidden", !isRemote);
  if (!isRemote) el.fAnydesk.value = "";
}

function fillModal(profile) {
  setControlValue(el.fContactName, profile.contact_name);
  setControlValue(el.fLastName, profile.last_name);
  setControlValue(el.fDestination, profile.destination);
  setControlValue(el.fSandOwner, profile.sand_owner);
  setControlValue(el.fSalesGroup, profile.sales_group);
  setControlValue(el.fLocationType, profile.location_type);
  setControlValue(el.fAnydesk, profile.anydesk);
  setControlValue(el.fInternalNumber, profile.internal_number);
  setControlValue(el.fBitrixLogin, profile.bitrix_login);
  setControlValue(el.fBitrixPassword, profile.bitrix_password);
  setControlValue(el.fBitrixId, profile.bitrix_id);
  el.fIsVerified.checked = Boolean(profile.is_verified);
  renderCustomFieldsEditor(profile);
  syncDynamicModalFields();
}

function _parseCustomFieldValueForPayload(fieldType, rawValue) {
  const type = String(fieldType || "text").toLowerCase();
  if (type === "checkbox") return Boolean(rawValue);
  if (type === "number") {
    if (rawValue === "" || rawValue === null || rawValue === undefined) return null;
    const parsed = Number(rawValue);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return (rawValue ?? "").toString().trim() || null;
}

function renderCustomFieldsEditor(profile) {
  if (!el.customFieldsContainer || !el.customFieldsSection) return;
  el.customFieldsContainer.innerHTML = "";
  const blocks = (state.clientCardViewConfig?.blocks || [])
    .filter((block) => block && block.is_active !== false)
    .sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0));
  const editableFields = [];
  for (const block of blocks) {
    for (const field of block.fields || []) {
      if (!field || field.is_system || field.is_editable === false) continue;
      editableFields.push(field);
    }
  }
  el.customFieldsSection.classList.toggle("hidden", editableFields.length === 0);
  const customValues = profile?.custom_fields || {};
  for (const field of editableFields) {
    const wrap = document.createElement("label");
    wrap.className = "custom-field-editor";
    const title = document.createElement("span");
    title.textContent = field.label || field.key;
    wrap.appendChild(title);
    const fieldType = String(field.field_type || "text").toLowerCase();
    const currentValue = customValues[field.key];
    let inputEl = null;
    if (fieldType === "textarea") {
      inputEl = document.createElement("textarea");
      inputEl.rows = 3;
      inputEl.value = currentValue ?? "";
    } else if (fieldType === "select") {
      inputEl = document.createElement("select");
      const emptyOption = document.createElement("option");
      emptyOption.value = "";
      emptyOption.textContent = "Не выбрано";
      inputEl.appendChild(emptyOption);
      const options = Array.isArray(field.options) ? field.options : [];
      for (const optionValue of options) {
        const option = document.createElement("option");
        option.value = optionValue;
        option.textContent = optionValue;
        inputEl.appendChild(option);
      }
      inputEl.value = currentValue ?? "";
    } else if (fieldType === "checkbox") {
      inputEl = document.createElement("input");
      inputEl.type = "checkbox";
      inputEl.checked = Boolean(currentValue);
    } else {
      inputEl = document.createElement("input");
      if (fieldType === "number") inputEl.type = "number";
      else if (fieldType === "date") inputEl.type = "date";
      else if (fieldType === "url") inputEl.type = "url";
      else if (fieldType === "phone") inputEl.type = "tel";
      else if (fieldType === "password") inputEl.type = "password";
      else inputEl.type = "text";
      inputEl.value = currentValue ?? "";
    }
    inputEl.dataset.customFieldKey = field.key;
    inputEl.dataset.customFieldType = fieldType;
    wrap.appendChild(inputEl);
    el.customFieldsContainer.appendChild(wrap);
  }
}

function buildDepartmentMetaPayload() {
  const destination = el.fDestination.value.trim() || null;
  const sandOwner = el.fSandOwner.value.trim() || null;
  const salesGroup = el.fSalesGroup.value.trim() || null;

  if (destination === "Песок") {
    return {
      department: "Стажировка",
      first_department: null,
      group_name: sandOwner,
    };
  }

  if (destination === "Отдел Продаж") {
    let firstDepartment = null;
    if (salesGroup) {
      if (SALES_FIRST_DEPARTMENT.has(salesGroup)) firstDepartment = "Первый департамент";
      else if (SALES_SECOND_DEPARTMENT.has(salesGroup)) firstDepartment = "Второй отдел";
      else if (salesGroup === "группа Москвиченко") firstDepartment = "Третий отдел";
    }

    return {
      department: "Отдел продаж",
      first_department: firstDepartment,
      group_name: salesGroup,
    };
  }

  return {
    department: destination,
    first_department: null,
    group_name: null,
  };
}

function getModalPayload() {
  const locationType = el.fLocationType.value.trim() || null;
  const anydesk = locationType === "Удалённый" ? (el.fAnydesk.value.trim() || null) : null;
  const departmentMeta = buildDepartmentMetaPayload();

  const custom_fields = {};
  if (el.customFieldsContainer) {
    const customInputs = el.customFieldsContainer.querySelectorAll("[data-custom-field-key]");
    customInputs.forEach((input) => {
      const key = input.dataset.customFieldKey;
      const fieldType = input.dataset.customFieldType || "text";
      let rawValue = null;
      if (input.type === "checkbox") rawValue = input.checked;
      else rawValue = input.value;
      custom_fields[key] = _parseCustomFieldValueForPayload(fieldType, rawValue);
    });
  }

  return {
    contact_name: el.fContactName.value.trim() || null,
    last_name: el.fLastName.value.trim() || null,
    email: null,
    phone: null,
    verification_status: null,
    is_verified: el.fIsVerified.checked,
    destination: el.fDestination.value.trim() || null,
    sand_owner: el.fSandOwner.value.trim() || null,
    sales_group: el.fSalesGroup.value.trim() || null,
    department: departmentMeta.department,
    first_department: departmentMeta.first_department,
    group_name: departmentMeta.group_name,
    employee_type: null,
    location_type: locationType,
    anydesk,
    internal_number: el.fInternalNumber.value.trim() || null,
    external_user_id: null,
    bitrix_login: el.fBitrixLogin.value.trim() || null,
    bitrix_password: el.fBitrixPassword.value.trim() || null,
    bitrix_id: el.fBitrixId.value.trim() || null,
    custom_fields,
  };
}

function openClientModal() {
  if (!state.card) return;
  fillModal(state.card.client_profile || {});
  el.clientModal.classList.remove("hidden");
}

function closeClientModal() {
  el.clientModal.classList.add("hidden");
}

async function saveClientProfile() {
  try {
    setStatus("Сохраняю профиль клиента...");
    const payload = getModalPayload();
    const profile = await api(`/api/v1/tickets/${state.ticketId}/client-profile`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
    state.card.client_profile = profile;
    fillClientPanel(profile);
    closeClientModal();
    setStatus("Профиль клиента сохранён");
  } catch (e) {
    showError(e.message);
  }
}

function hideMessageContextMenu() {
  if (!el.messageContextMenu) return;
  el.messageContextMenu.classList.add("hidden");
  el.messageContextMenu.innerHTML = "";
}

function addContextMenuItem(menu, label, onClick, options = {}) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "message-menu-item";
  button.textContent = label;
  button.disabled = Boolean(options.disabled);
  button.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    hideMessageContextMenu();
    onClick();
  });
  menu.appendChild(button);
}

function showMessageContextMenu(event, message) {
  if (!["client", "agent", "internal"].includes(message.message_type)) return;
  event.preventDefault();
  event.stopPropagation();

  const menu = el.messageContextMenu;
  menu.innerHTML = "";
  addContextMenuItem(menu, "Копировать", () => copyMessageText(message));
  if (state.role !== "chiki_piki") {
    addContextMenuItem(menu, "Цитировать", () => quoteMessage(message));
  }

  if (canEditAgentMessage(message)) {
    addContextMenuItem(menu, "Исправить", () => editAgentMessage(message));
    addContextMenuItem(menu, "Удалить", () => deleteAgentMessage(message));
  }

  menu.classList.remove("hidden");
  const rect = menu.getBoundingClientRect();
  const left = Math.min(event.clientX, window.innerWidth - rect.width - 8);
  const top = Math.min(event.clientY, window.innerHeight - rect.height - 8);
  menu.style.left = `${Math.max(8, left)}px`;
  menu.style.top = `${Math.max(8, top)}px`;
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

  const wasNearBottom = el.messages.scrollHeight - el.messages.scrollTop - el.messages.clientHeight < 80;
  const currentSignature = messageRenderSignature(card.messages);
  if (currentSignature !== state.lastMessagesSignature) {
    const currentAttachmentIds = new Set();
    for (const message of card.messages || []) {
      for (const attachment of message.attachments || []) {
        if (attachment?.id) currentAttachmentIds.add(attachment.id);
      }
    }
    cleanupAttachmentObjectUrls(currentAttachmentIds);
    cleanupStickerResources(currentAttachmentIds);

    el.messages.innerHTML = "";
    for (const m of card.messages) {
      const div = document.createElement("div");
      const cls = m.message_type || "";
      const stickerOnly = isStickerOnlyMessage(m);
      div.className = `message ${cls} ${stickerOnly ? "message-sticker-only" : ""} ${m.message_status && m.message_status !== "normal" ? `message-${m.message_status}` : ""}`.trim();
      if (["client", "agent", "internal"].includes(m.message_type)) {
        div.addEventListener("contextmenu", (event) => showMessageContextMenu(event, m));
      }
      if (!stickerOnly) {
        const headerEl = document.createElement("div");
        headerEl.className = "message-header";
        const strong = document.createElement("strong");
        strong.textContent = messageAuthorLabel(m);
        const dateEl = document.createElement("div");
        dateEl.className = "message-date";
        dateEl.textContent = fmtMessageStamp(m.created_at);
        headerEl.append(strong, dateEl);
        div.appendChild(headerEl);
      }
      if ((m.body || "").trim()) {
        const bodyEl = document.createElement("div");
        bodyEl.className = "message-body";
        bodyEl.innerHTML = linkifyText(m.body || "");
        div.appendChild(bodyEl);
      }
      renderAttachments(m, div);
      const note = messageStatusNote(m);
      if (note) {
        const noteEl = document.createElement("div");
        noteEl.className = "message-status-note";
        noteEl.textContent = note;
        div.appendChild(noteEl);
      }
      if (stickerOnly) {
        const ts = document.createElement("div");
        ts.className = "sticker-time-hover";
        ts.textContent = fmtMessageStamp(m.created_at);
        div.appendChild(ts);
      }
      el.messages.appendChild(div);
    }
    cleanupStickerResources(currentAttachmentIds);
    state.lastMessagesSignature = currentSignature;
  }
  if (state.quoteMessageId && !card.messages.some((m) => m.id === state.quoteMessageId)) {
    state.quoteMessageId = null;
  }
  refreshComposerActions();

  const isClosed = card.status === "closed";
  const isReadonly = state.role === "chiki_piki";
  const hasAgentMessage = card.messages.some((m) => m.message_type === "agent");
  const hasAnyMessage = card.messages.length > 0;
  const greeted = hasGreetingMessage(card.messages);
  const isFirstConnection = card.subject === "Первое подключение к боту";
  const firstConnectionVerified = Boolean(card.client_profile?.is_verified);

  el.modeReplyBtn.disabled = isClosed || isReadonly;
  el.modeCommentBtn.disabled = isClosed || isReadonly;
  el.messageInput.disabled = isClosed || isReadonly;
  el.messageInput.placeholder = isReadonly ? "Режим только для чтения" : "Текст ответа/комментария";
  el.sendNowBtn.disabled = isClosed || isReadonly;
  if (el.addFilesBtn) el.addFilesBtn.disabled = isClosed || isReadonly || state.composeMode !== "reply";

  const workflowType = card.workflow_type || "ticket";
  const isGreetingFlow = workflowType !== "task" && workflowType !== "auto";
  const showGreeting = !isReadonly && !isClosed && isGreetingFlow && greetingAllowed() && !greeted;
  const showClose = !isReadonly && !isClosed && (!isFirstConnection || firstConnectionVerified) && (state.role === "admin" || (hasAnyMessage && hasAgentMessage));
  const showFreezeToggle = !isReadonly && !isClosed;
  const canRestore = state.role === "admin" || (card.closed_by_user_id && card.closed_by_user_id === state.userId);
  const canRestoreFlow = workflowType !== "task" && workflowType !== "auto";
  const showRestore = !isReadonly && isClosed && canRestoreFlow && canRestore;
  const isGroupChildTask = (card.task_kind || "") === "group_child";
  const showWrongProblem = !isReadonly && !isClosed && !card.wrong_problem_used && !isGroupChildTask;
  const showMarkSpecial = state.role === "admin" && !isClosed && !card.is_special && workflowType === "ticket";
  const showAdminForceClose = state.role === "admin" && !isClosed;
  const showAdminDelete = state.role === "admin";
  const isCollecting = (card.intake_state || "ready") === "collecting";
  const showAutoCloseBox = !isReadonly && !isClosed && !card.is_special && workflowType === "ticket" && !isCollecting;

  setVisible(el.greetingBtn, showGreeting);
  setVisible(el.wrongProblemBtn, showWrongProblem);
  setVisible(el.closeBtn, showClose);
  if (el.adminForceCloseBtn) setVisible(el.adminForceCloseBtn, showAdminForceClose);
  if (el.adminDeleteBtn) setVisible(el.adminDeleteBtn, showAdminDelete);
  setVisible(el.freezeToggleBtn, showFreezeToggle);
  setVisible(el.restoreBtn, showRestore);
  if (el.markSpecialBtn) setVisible(el.markSpecialBtn, showMarkSpecial);
  if (el.autoCloseBox) setVisible(el.autoCloseBox, showAutoCloseBox);
  const canSeeInstruction = state.role !== "chiki_piki" && Boolean(card.instruction_text);
  if (el.instructionBtn) setVisible(el.instructionBtn, canSeeInstruction);
  setVisible(el.editClientBtn, !isReadonly);
  el.ticketAssigneeBox.classList.toggle("hidden", state.role !== "admin");
  renderTicketAssigneeSelect();

  const isOnsiteTicket = workflowType === "ticket" && card.onsite_triggered;
  el.closeBtn.textContent = isTask ? "Завершить задачу" : (isOnsiteTicket ? "Решил на месте" : "Закрыть заявку");
  if (el.adminForceCloseBtn) {
    el.adminForceCloseBtn.textContent = "Принудительно завершить";
  }
  el.freezeToggleBtn.textContent = card.is_frozen ? "Разморозить" : "Заморозить";
  if (el.autoCloseDisabledCheckbox) {
    el.autoCloseDisabledCheckbox.checked = Boolean(card.auto_close_disabled);
  }
  if (el.autoCloseDueText) {
    const minutesLeft = minutesLeftToDue(card.auto_close_due_at);
    if (!showAutoCloseBox || card.auto_close_disabled || minutesLeft === null) {
      el.autoCloseDueText.textContent = "";
    } else {
      el.autoCloseDueText.textContent = `До закрытия: ${minutesLeft} м.`;
    }
  }
  if (wasNearBottom) {
    el.messages.scrollTop = el.messages.scrollHeight;
  }
}

async function loadCard(options = {}) {
  if (state.isLoading) return;
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
    state.card = card;
    state.clientCardViewConfig = cardConfig && Array.isArray(cardConfig.blocks)
      ? cardConfig
      : defaultClientCardViewConfig();
    renderCard();
    applyComposeModeUi();
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
  if (state.isSendingReply) return;
  if (state.role === "chiki_piki") {
    showError("Роль Чики-пики работает только в режиме просмотра");
    return;
  }
  const text = el.messageInput.value.trim();
  const hasFiles = state.selectedFiles.length > 0;
  if (!text && !hasFiles) return;
  if (state.composeMode === "comment" && hasFiles) {
    showError("Файлы можно отправлять только в ответе клиенту. Внутренние комментарии пока поддерживают только текст.");
    return;
  }

  const actionName = state.composeMode === "reply" ? "reply" : "comment";
  try {
    state.isSendingReply = true;
    state.pendingCloseAfterSend = false;
    setStatus("Отправка...");
    const quoteMessageId = state.quoteMessageId;
    if (state.composeMode === "reply" && hasFiles) {
      const queue = [...state.selectedFiles];
      for (let index = 0; index < queue.length; index += 1) {
        const file = queue[index];
        const fileKey = selectedFileKey(file, index);
        state.fileUploadProgress[fileKey] = { stage: "sending", current: index + 1, total: queue.length };
        renderSelectedFiles();
        setStatus(`Отправка в Telegram: ${index + 1}/${queue.length}`);
        const formData = new FormData();
        formData.append("text", index === 0 ? text : "");
        if (index === 0 && quoteMessageId) formData.append("quote_message_id", quoteMessageId);
        formData.append("files", file, file.name);
        await api(`/api/v1/tickets/${state.ticketId}/reply`, {
          method: "POST",
          body: formData,
        });
        state.fileUploadProgress[fileKey] = { stage: "done" };
        renderSelectedFiles();
        const idxInState = state.selectedFiles.indexOf(file);
        if (idxInState >= 0) state.selectedFiles.splice(idxInState, 1);
        delete state.fileUploadProgress[fileKey];
        renderSelectedFiles();
        await loadCard(true);
      }
    } else {
      await api(`/api/v1/tickets/${state.ticketId}/${actionName}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ text, quote_message_id: quoteMessageId }),
      });
    }
    el.messageInput.value = "";
    state.quoteMessageId = null;
    clearSelectedFiles();
    await loadCard();
    if (
      state.role === "agent"
      && state.composeMode === "reply"
      && (state.card?.workflow_type || "ticket") === "ticket"
      && isOnsitePhraseJs(text)
    ) {
      renderAvailabilityStatus("on_ticket");
    }
    if (state.card?.workflow_type === "task" && state.composeMode === "reply") {
      setStatus("Ответ сохранён. Клиент получит сообщение и файлы после завершения задачи.");
    } else {
      setStatus("Сообщение отправлено");
    }
  } catch (e) {
    if (state.selectedFiles.length) {
      const file = state.selectedFiles[0];
      const fileKey = selectedFileKey(file, 0);
      state.fileUploadProgress[fileKey] = { stage: "error", message: e.message || "ошибка отправки" };
      renderSelectedFiles();
    }
    showError(e.message);
  } finally {
    state.isSendingReply = false;
    if (state.pendingCloseAfterSend) {
      state.pendingCloseAfterSend = false;
      await action("close");
    }
  }
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
  if (!sidebarState) return;
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
    window.location.href = `/tickets?tab=${encodeURIComponent(state.tab)}`;
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

  el.modeReplyBtn.addEventListener("click", () => {
    state.composeMode = "reply";
    applyComposeModeUi();
  });

  el.modeCommentBtn.addEventListener("click", () => {
    state.composeMode = "comment";
    applyComposeModeUi();
  });

  el.sendNowBtn.addEventListener("click", async () => {
    await sendByMode();
  });
  el.addFilesBtn.addEventListener("click", () => {
    if (state.composeMode !== "reply") {
      showError("Файлы можно прикреплять только к ответу клиенту");
      return;
    }
    el.replyFilesInput.click();
  });
  el.replyFilesInput.addEventListener("change", () => {
    state.selectedFiles.push(...Array.from(el.replyFilesInput.files || []));
    state.fileUploadProgress = {};
    state.selectedFiles.forEach((file, index) => {
      state.fileUploadProgress[selectedFileKey(file, index)] = { stage: "queued", percent: 0 };
    });
    el.replyFilesInput.value = "";
    renderSelectedFiles();
  });
  el.clearQuoteBtn.addEventListener("click", clearQuote);

  el.messageInput.addEventListener("keydown", async (e) => {
    if (e.key === "Enter" && e.ctrlKey) {
      e.preventDefault();
      await sendByMode();
    }
  });

  el.greetingBtn.addEventListener("click", (event) => {
    const profile = state.card?.client_profile || {};
    const name = (profile.contact_name || "").trim();
    if (!name) {
      event.preventDefault();
      showError("Поле 'Имя' в карточке клиента не заполнено");
      return;
    }
    action("greeting");
  });

  el.freezeToggleBtn.addEventListener("click", () => {
    const card = state.card;
    if (!card) return;

    if (card.is_frozen) {
      action("unfreeze");
      return;
    }

    const raw = el.freezeUntil.value;
    const freezeUntil = raw ? new Date(raw).toISOString() : null;
    action("freeze", { freeze_until: freezeUntil });
  });

  el.closeBtn.addEventListener("click", () => action("close"));
  if (el.adminForceCloseBtn) {
    el.adminForceCloseBtn.addEventListener("click", () => openAdminActionModal("close"));
  }
  if (el.adminDeleteBtn) {
    el.adminDeleteBtn.addEventListener("click", () => openAdminActionModal("delete"));
  }
  el.restoreBtn.addEventListener("click", () => action("restore"));
  if (el.autoCloseDisabledCheckbox) {
    el.autoCloseDisabledCheckbox.addEventListener("change", async () => {
      try {
        await apiPatch(`/api/v1/tickets/${state.ticketId}/auto-close`, {
          auto_close_disabled: Boolean(el.autoCloseDisabledCheckbox.checked),
        });
        await loadCard();
      } catch (e) {
        showError(e.message);
        el.autoCloseDisabledCheckbox.checked = Boolean(state.card?.auto_close_disabled);
      }
    });
  }
  el.wrongProblemBtn.addEventListener("click", openWrongProblemModal);
  if (el.instructionBtn) el.instructionBtn.addEventListener("click", openInstructionModal);
  if (el.closeInstructionModalBtn) el.closeInstructionModalBtn.addEventListener("click", closeInstructionModal);

  el.ticketAssigneeSelect.addEventListener("change", async () => {
    await assignCurrentTicket(el.ticketAssigneeSelect.value || null);
  });

  el.editClientBtn.addEventListener("click", openClientModal);
  el.closeClientModalBtn.addEventListener("click", closeClientModal);
  el.closeWrongProblemModalBtn.addEventListener("click", closeWrongProblemModal);
  if (el.markSpecialBtn) el.markSpecialBtn.addEventListener("click", openSpecialConfirmModal);
  if (el.closeSpecialConfirmBtn) el.closeSpecialConfirmBtn.addEventListener("click", closeSpecialConfirmModal);
  if (el.confirmSpecialBtn) {
    el.confirmSpecialBtn.addEventListener("click", async () => {
      await markTicketSpecial();
    });
  }
  el.saveClientBtn.addEventListener("click", saveClientProfile);
  if (el.closeAdminActionModalBtn) el.closeAdminActionModalBtn.addEventListener("click", closeAdminActionModal);
  if (el.cancelAdminActionBtn) el.cancelAdminActionBtn.addEventListener("click", closeAdminActionModal);
  if (el.confirmAdminActionBtn) {
    el.confirmAdminActionBtn.addEventListener("click", async () => {
      try {
        await runAdminAction();
        closeAdminActionModal();
      } catch (e) {
        showError(e.message);
      }
    });
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
  closeOnBackdropClick(el.clientModal, closeClientModal);
  closeOnBackdropClick(el.wrongProblemModal, closeWrongProblemModal);
  if (el.specialConfirmModal) closeOnBackdropClick(el.specialConfirmModal, closeSpecialConfirmModal);
  if (el.adminActionModal) closeOnBackdropClick(el.adminActionModal, closeAdminActionModal);
  closeOnBackdropClick(el.errorModal, closeError);
  if (el.instructionModal) closeOnBackdropClick(el.instructionModal, closeInstructionModal);

  el.fDestination.addEventListener("change", syncDynamicModalFields);
  el.fLocationType.addEventListener("change", syncDynamicModalFields);

  document.addEventListener("keydown", (e) => {
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
  });

  document.addEventListener("click", hideMessageContextMenu);
  window.addEventListener("resize", hideMessageContextMenu);
  el.messages.addEventListener("scroll", hideMessageContextMenu);

  window.setInterval(() => {
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

  window.addEventListener("beforeunload", () => {
    TicketAttachments.destroyAllAttachmentResources();
  });
}

(async function run() {
  await window.HelpdeskSidebar.init();
  syncSidebarState(window.HelpdeskSidebar.getState());
  window.HelpdeskSidebar.onChange(syncSidebarState);
  init();
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
  await Promise.all([loadCard(), loadTabCounts(), loadCompletionStats()]);
})();

document.querySelectorAll("input, textarea").forEach((field) => {
  field.setAttribute("autocomplete", "off");
  field.setAttribute("autocorrect", "off");
  field.setAttribute("autocapitalize", "off");
  field.setAttribute("spellcheck", "false");
});
