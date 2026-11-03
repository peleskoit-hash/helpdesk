(function () {
function initTicketCardPage() {
// ===== state/elements =====
const ticketId = window.location.pathname.split("/").pop();
const STATE_ACTION_NAMES = new Set(["close", "restore", "freeze", "unfreeze"]);
const RESTORE_CLOSE_COOLDOWN_MS = 10_000;

const state = {
  ticketId,
  role: localStorage.getItem("hd_user_role") || "agent",
  tab: localStorage.getItem("hd_tab") || "active",
  userId: "",
  isRealAdmin: false,
  roleUsers: [],
  supportAgents: [],
  adminAssigneeIds: JSON.parse(localStorage.getItem("hd_admin_assignee_ids") || "[]"),
  card: null,
  returnToListOnErrorClose: false,
  wrongProblemMenu: null,
  quoteMessageId: null,
  composeMode: "reply",
  sendByEnter: false,
  selectedFiles: [],
  currentArchiveGroupId: null,
  isLoading: false,
  clientCardViewConfig: null,
  counts: { unassigned: 0, active: 0, special: 0, frozen: 0, closed: 0 },
  sessionContext: null,
  isSendingReply: false,
  pendingCloseAfterSend: false,
  filePreloads: new Map(),
  isSavingSendByEnterPreference: false,
  stateActionInFlight: null,
  restoreCloseCooldownUntil: 0,
  pendingCardReloadOptions: null,
  draftDirty: false,
  draftSaveInFlight: false,
  draftSaveTimer: null,
  draftSavePromise: null,
  draftAckRevision: 0,
  draftLastSavedText: "",
  draftLastSavedMode: "reply",
  draftExitFlushSignature: "",
  initialComposerFocusApplied: false,
  quickRepliesOpen: false,
  quickReplies: [],
  quickRepliesSearchSeq: 0,
  quickRepliesHighlightedIndex: 0,
  quickRepliesSearchTimer: null,
  quickReplyUseInFlight: false,
  quickReplyUseSeq: 0,
  taskPanelEnabled: false,
  taskPanelStatus: "loading",
  taskPanelItems: [],
  taskPanelDropdownOpen: false,
};

const el = {
  backToListBtn: document.getElementById("backToListBtn"),
  roleSelect: document.getElementById("roleSelect"),
  roleControl: document.getElementById("roleControl"),
  agentSelect: document.getElementById("agentSelect"),
  agentControl: document.getElementById("agentControl"),
  ticketSubject: document.getElementById("ticketSubject"),
  ticketStatusBadges: document.getElementById("ticketStatusBadges"),
  instructionBtn: document.getElementById("instructionBtn"),
  taskPanel: document.getElementById("taskPanel"),
  taskPanelSlots: document.getElementById("taskPanelSlots"),
  taskPanelMoreBtn: document.getElementById("taskPanelMoreBtn"),
  taskPanelDropdown: document.getElementById("taskPanelDropdown"),

  clientDynamicCard: document.getElementById("clientDynamicCard"),

  editClientBtn: document.getElementById("editClientBtn"),
  clientModal: document.getElementById("clientModal"),
  closeClientModalBtn: document.getElementById("closeClientModalBtn"),
  saveClientBtn: document.getElementById("saveClientBtn"),
  errorModal: document.getElementById("errorModal"),
  errorText: document.getElementById("errorText"),
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
  messageInput: document.getElementById("ticketMessageBody"),
  replyFilesInput: document.getElementById("replyFilesInput"),
  selectedFilesList: document.getElementById("selectedFilesList"),
  addFilesBtn: document.getElementById("addFilesBtn"),
  quickRepliesBtn: document.getElementById("quickRepliesBtn"),
  quickRepliesPanel: document.getElementById("quickRepliesPanel"),
  quickRepliesSearchInput: document.getElementById("quickRepliesSearchInput"),
  quickRepliesPanelRows: document.getElementById("quickRepliesPanelRows"),
  quickRepliesEditorRow: document.getElementById("quickRepliesEditorRow"),
  sendNowBtn: document.getElementById("sendNowBtn"),
  modeReplyBtn: document.getElementById("modeReplyBtn"),
  modeCommentBtn: document.getElementById("modeCommentBtn"),
  sendByEnterCheckbox: document.getElementById("sendByEnterCheckbox"),
  sendHotkeyHint: document.getElementById("sendHotkeyHint"),
  freezeUntil: document.getElementById("freezeUntil"),
  autoCloseBox: document.getElementById("autoCloseBox"),
  autoCloseDueText: document.getElementById("autoCloseDueText"),
  autoCloseDisabledCheckbox: document.getElementById("autoCloseDisabledCheckbox"),
  greetingBtn: document.getElementById("greetingBtn"),
  clarificationBtn: document.getElementById("clarificationBtn"),
  onsiteBtn: document.getElementById("onsiteBtn"),
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
  adminSkipClientNotification: document.getElementById("adminSkipClientNotification"),
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
  imagePreviewAvatarLoading: null,
  tabLinks: [...document.querySelectorAll("[data-tab-link]")],
  statusButtons: [...document.querySelectorAll("[data-availability-status]")],
  adminOnly: [...document.querySelectorAll(".admin-only")],
  adminAgentFilter: document.getElementById("adminAgentFilter"),
  adminAgentList: document.getElementById("adminAgentList"),
  adminAgentToggleAll: document.getElementById("adminAgentToggleAll"),
  statusPanel: document.querySelector(".status-panel"),
  agentAvatar: document.querySelector(".agent-avatar"),
  tabCountNodes: [...document.querySelectorAll("[data-tab-count]")],
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
let pageHandlersBound = false;
let restoreCloseCooldownTimer = null;

function isTicketStateAction(name) {
  return STATE_ACTION_NAMES.has(String(name || ""));
}

function restoreCloseCooldownSecondsLeft() {
  const diffMs = Number(state.restoreCloseCooldownUntil || 0) - Date.now();
  if (!Number.isFinite(diffMs) || diffMs <= 0) return 0;
  return Math.ceil(diffMs / 1000);
}

function hasRestoreCloseCooldown() {
  return restoreCloseCooldownSecondsLeft() > 0;
}

function renderTicketStateControls() {
  if (destroyed || !state.card) return;
  TicketControls.renderTicketControlsView(controlsCtx());
}

function stopRestoreCloseCooldownTimer() {
  if (restoreCloseCooldownTimer !== null) {
    window.clearInterval(restoreCloseCooldownTimer);
    restoreCloseCooldownTimer = null;
  }
}

function clearRestoreCloseCooldown() {
  state.restoreCloseCooldownUntil = 0;
  stopRestoreCloseCooldownTimer();
  renderTicketStateControls();
}

function syncRestoreCloseCooldown() {
  if (!state.restoreCloseCooldownUntil) {
    stopRestoreCloseCooldownTimer();
    return false;
  }
  if (!hasRestoreCloseCooldown()) {
    clearRestoreCloseCooldown();
    return false;
  }
  renderTicketStateControls();
  return true;
}

function ensureRestoreCloseCooldownTimer() {
  if (!hasRestoreCloseCooldown()) {
    stopRestoreCloseCooldownTimer();
    return;
  }
  if (restoreCloseCooldownTimer !== null) return;
  restoreCloseCooldownTimer = window.setInterval(() => {
    if (destroyed) {
      stopRestoreCloseCooldownTimer();
      return;
    }
    syncRestoreCloseCooldown();
  }, 1000);
}

function startRestoreCloseCooldown() {
  state.restoreCloseCooldownUntil = Date.now() + RESTORE_CLOSE_COOLDOWN_MS;
  syncRestoreCloseCooldown();
  ensureRestoreCloseCooldownTimer();
}

function shellNavigateOrReload(urlLike) {
  const url = new URL(urlLike, window.location.origin);
  const href = `${url.pathname}${url.search}${url.hash}`;
  if (url.pathname === "/tickets" && window.HelpdeskAppShell?.navigate) {
    window.HelpdeskAppShell.navigate(href);
    return;
  }
  window.location.href = href;
}

function canUseTaskPanel(role = state.role) {
  return role === "agent" || role === "trainee";
}

function taskPanelTicketHref(panelTicketId) {
  return `/tickets/${panelTicketId}`;
}

function taskPanelItemHtml(item, className) {
  const unread = item?.has_unread ? '<span class="task-panel-unread" aria-label="Новые сообщения"></span>' : "";
  return `<a class="${className}${item?.has_unread ? " has-unread" : ""}" href="${escapeHtml(taskPanelTicketHref(item.id))}" title="${escapeHtml(item.subject || "")}">${unread}<span class="task-panel-subject">${escapeHtml(item.subject || "")}</span></a>`;
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
  if (!canUseTaskPanel() || !state.taskPanelEnabled) {
    state.taskPanelItems = [];
    renderTaskPanel();
    return;
  }
  renderTaskPanel();
  try {
    state.taskPanelItems = await api("/api/v1/tickets/task-panel");
    state.taskPanelStatus = "ready";
    renderTaskPanel();
  } catch (e) {
    state.taskPanelItems = [];
    state.taskPanelStatus = "error";
    renderTaskPanel();
    if (!options.silent) showError(`Не удалось загрузить панель задач.\n\n${e.message || "Неизвестная ошибка"}`);
  }
}

function cleanupTicketCardPage() {
  destroyed = true;
  TicketClientCard.destroyClientEditor(clientCardCtx());
  flushDraftKeepalive();
  stopRestoreCloseCooldownTimer();
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
    window.removeEventListener("pagehide", beforeUnloadHandler);
    beforeUnloadHandler = null;
  }
  TicketComposer.destroyComposerState?.(composerCtx());
  try {
    TicketAttachments.destroyAllAttachmentResources?.();
  } catch {
    // noop
  }
  if (el.messageContextMenu) {
    el.messageContextMenu.remove();
    el.messageContextMenu = null;
  }
  if (el.quickRepliesPanel?.parentElement === document.body) {
    el.quickRepliesPanel.remove();
  }
  if (state.quickRepliesSearchTimer !== null) {
    window.clearTimeout(state.quickRepliesSearchTimer);
    state.quickRepliesSearchTimer = null;
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
    el.imagePreviewAvatarLoading = null;
  }
  pageHandlersBound = false;
}

function attachmentsCtx() {
  return {
    el,
    setStatus,
    showError,
    closeOnBackdropClick,
    api,
  };
}

function clientCardCtx() {
  return {
    inlineClient: true,
    openClientAvatarPreview: (thumbnailUrl, fullUrl) => TicketAttachments.openClientAvatarPreview(attachmentsCtx(), thumbnailUrl, fullUrl),
    isDestroyed: () => destroyed,
    state,
    el,
    api,
    loadCard,
    setStatus,
    showError,
    closeOnBackdropClick,
    isBlank,
    setControlValue,
  };
}

function composerCtx() {
  return {
    refreshTaskUploadControls: renderTicketStateControls,
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
    updateSendByEnterPreference,
    onComposerInput: handleComposerInput,
    onComposerSelection: saveDraftSelection,
    onComposeModeChanged: handleComposeModeChanged,
    flushDraftBeforeSend,
    clearDraftAfterSuccessfulSend,
    workflowType: () => state.card?.workflow_type || "ticket",
    canUseAttachmentPreload: () => ["reply", "comment"].includes(state.composeMode) && ["ticket", "task"].includes(state.card?.workflow_type || "ticket"),
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

function currentDraftText() {
  return el.messageInput ? String(el.messageInput.value || "") : "";
}

function draftSelectionStorageKey() {
  const userKey = String(state.userId || state.sessionContext?.effective_user?.id || "unknown");
  return `hd_ticket_draft_selection:${userKey}:${state.ticketId}`;
}

function saveDraftSelection() {
  if (!el.messageInput) return;
  try {
    localStorage.setItem(draftSelectionStorageKey(), JSON.stringify({
      start: Number(el.messageInput.selectionStart) || 0,
      end: Number(el.messageInput.selectionEnd) || 0,
    }));
  } catch {
    // noop
  }
}

function restoreDraftSelection() {
  if (!el.messageInput || document.activeElement !== el.messageInput) return;
  try {
    const raw = localStorage.getItem(draftSelectionStorageKey());
    if (!raw) return;
    const parsed = JSON.parse(raw);
    const length = el.messageInput.value.length;
    const start = Math.max(0, Math.min(length, Number(parsed?.start) || 0));
    const end = Math.max(0, Math.min(length, Number(parsed?.end) || start));
    el.messageInput.setSelectionRange(start, end);
  } catch {
    // noop
  }
}

function clearDraftSelection() {
  try {
    localStorage.removeItem(draftSelectionStorageKey());
  } catch {
    // noop
  }
}

function pendingQuickReplyStorageKey() {
  return `hd_quick_reply_pending_use:${state.ticketId}`;
}

function canWriteComposer() {
  return Boolean(el.messageInput && !el.messageInput.disabled && !el.messageInput.readOnly && state.card?.status !== "closed");
}

function canUseQuickRepliesEditor() {
  return state.role === "admin" || state.role === "agent";
}

function isQuickRepliesHotkey(event) {
  return Boolean(event.ctrlKey && !event.metaKey && !event.altKey && event.code === "KeyQ");
}

function isQuickRepliesClickInside(target) {
  return Boolean(
    (el.quickRepliesBtn && el.quickRepliesBtn.contains(target))
    || (el.quickRepliesPanel && el.quickRepliesPanel.contains(target))
  );
}

function quickReplyEditorUrl() {
  return `/quick-replies?ticket_id=${encodeURIComponent(state.ticketId)}`;
}

function quickReplyRows() {
  return Array.from(el.quickRepliesPanelRows?.querySelectorAll("[data-quick-reply-id]") || []);
}

function quickReplyNavigableRows() {
  const rows = [];
  if (el.quickRepliesSearchInput) rows.push(el.quickRepliesSearchInput);
  rows.push(...quickReplyRows());
  if (el.quickRepliesEditorRow && !el.quickRepliesEditorRow.disabled) rows.push(el.quickRepliesEditorRow);
  return rows;
}

function setQuickReplyHighlight(index, { focusSearch = false } = {}) {
  const rows = quickReplyNavigableRows();
  if (!rows.length) {
    state.quickRepliesHighlightedIndex = -1;
    return;
  }
  const next = ((index % rows.length) + rows.length) % rows.length;
  state.quickRepliesHighlightedIndex = next;
  rows.forEach((row, rowIndex) => row.classList.toggle("highlighted", rowIndex === next));
  if (focusSearch && rows[next] === el.quickRepliesSearchInput) {
    el.quickRepliesSearchInput.focus();
  }
}

function renderQuickReplyPanelRows() {
  if (!el.quickRepliesPanelRows) return;
  el.quickRepliesPanelRows.innerHTML = "";
  for (const reply of state.quickReplies.slice(0, 5)) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "quick-replies-panel-row";
    row.dataset.quickReplyId = reply.id;
    row.innerHTML = `
      <span class="quick-replies-panel-title">${escapeHtml(reply.short_title || "")}</span>
      <span class="quick-replies-panel-preview">${escapeHtml(reply.body || "")}</span>
    `;
    el.quickRepliesPanelRows.appendChild(row);
  }
  if (el.quickRepliesEditorRow) {
    el.quickRepliesEditorRow.disabled = !canUseQuickRepliesEditor();
  }
  setQuickReplyHighlight(Math.max(0, state.quickRepliesHighlightedIndex), {
    focusSearch: document.activeElement === el.quickRepliesSearchInput,
  });
}

async function searchQuickReplies(query) {
  const seq = ++state.quickRepliesSearchSeq;
  const payload = await api(`/api/v1/quick-replies/search?q=${encodeURIComponent(query || "")}`);
  if (seq !== state.quickRepliesSearchSeq) return;
  state.quickReplies = Array.isArray(payload) ? payload.slice(0, 5) : [];
  state.quickRepliesHighlightedIndex = 0;
  renderQuickReplyPanelRows();
}

function scheduleQuickReplySearch() {
  if (state.quickRepliesSearchTimer !== null) {
    window.clearTimeout(state.quickRepliesSearchTimer);
  }
  state.quickRepliesSearchTimer = window.setTimeout(() => {
    state.quickRepliesSearchTimer = null;
    void searchQuickReplies(el.quickRepliesSearchInput?.value || "").catch((e) => setStatus(`Ошибка поиска быстрых ответов: ${e.message}`));
  }, 150);
}

function mountQuickRepliesPanelOverlay() {
  if (!el.quickRepliesPanel || el.quickRepliesPanel.parentElement === document.body) return;
  document.body.appendChild(el.quickRepliesPanel);
}

function positionQuickRepliesPanel() {
  if (!el.quickRepliesPanel || !el.quickRepliesBtn) return;
  const rect = el.quickRepliesBtn.getBoundingClientRect();
  const buttonCenterX = rect.left + rect.width / 2;
  const panelBottom = window.innerHeight - rect.top + 14;
  el.quickRepliesPanel.style.right = "";
  el.quickRepliesPanel.style.top = "";
  el.quickRepliesPanel.style.inset = "";
  el.quickRepliesPanel.style.left = `${buttonCenterX}px`;
  el.quickRepliesPanel.style.bottom = `${panelBottom}px`;
}

function closeQuickRepliesPanel({ focusComposer = false } = {}) {
  state.quickRepliesOpen = false;
  if (state.quickRepliesSearchTimer !== null) {
    window.clearTimeout(state.quickRepliesSearchTimer);
    state.quickRepliesSearchTimer = null;
  }
  if (el.quickRepliesPanel) el.quickRepliesPanel.classList.add("hidden");
  if (el.quickRepliesBtn) el.quickRepliesBtn.classList.remove("primary");
  if (focusComposer && el.messageInput && !el.messageInput.disabled) {
    el.messageInput.focus();
  }
}

async function openQuickRepliesPanel({ focusSearch = false } = {}) {
  if (!canWriteComposer()) return;
  saveDraftSelection();
  state.quickRepliesOpen = true;
  state.quickReplies = [];
  state.quickRepliesHighlightedIndex = 0;
  mountQuickRepliesPanelOverlay();
  if (el.quickRepliesPanel) el.quickRepliesPanel.classList.remove("hidden");
  if (el.quickRepliesBtn) el.quickRepliesBtn.classList.add("primary");
  positionQuickRepliesPanel();
  if (el.quickRepliesSearchInput) {
    el.quickRepliesSearchInput.value = "";
  }
  renderQuickReplyPanelRows();
  setQuickReplyHighlight(0, { focusSearch });
  await searchQuickReplies("");
}

async function toggleQuickRepliesPanel({ focusSearch = false } = {}) {
  if (state.quickRepliesOpen) {
    closeQuickRepliesPanel({ focusComposer: true });
    return;
  }
  await openQuickRepliesPanel({ focusSearch });
}

function insertTextIntoComposer(text) {
  if (!canWriteComposer()) return false;
  const value = String(el.messageInput.value || "");
  let start = Number(el.messageInput.selectionStart);
  let end = Number(el.messageInput.selectionEnd);
  try {
    const raw = localStorage.getItem(draftSelectionStorageKey());
    if (raw && document.activeElement !== el.messageInput) {
      const parsed = JSON.parse(raw);
      start = Number(parsed?.start);
      end = Number(parsed?.end);
    }
  } catch {
    // noop
  }
  if (!Number.isFinite(start)) start = value.length;
  if (!Number.isFinite(end)) end = start;
  start = Math.max(0, Math.min(value.length, start));
  end = Math.max(0, Math.min(value.length, end));
  if (end < start) [start, end] = [end, start];
  const inserted = String(text || "");
  el.messageInput.value = `${value.slice(0, start)}${inserted}${value.slice(end)}`;
  const cursor = start + inserted.length;
  el.messageInput.focus();
  el.messageInput.setSelectionRange(cursor, cursor);
  handleComposerInput();
  return true;
}

async function downloadQuickReplyFiles(replyId, savedFiles) {
  const files = Array.isArray(savedFiles) ? savedFiles : [];
  if (!files.length) return [];
  const downloaded = [];
  for (const savedFile of files) {
    const savedFileId = String(savedFile?.id || "").trim();
    if (!savedFileId) throw new Error("Файл быстрого ответа недоступен");
    const response = await fetch(
      `/api/v1/quick-replies/${encodeURIComponent(replyId)}/files/${encodeURIComponent(savedFileId)}/download`,
      { headers: { accept: savedFile?.mime_type || "application/octet-stream" } },
    );
    if (response.status === 401) {
      window.location.href = "/login";
      throw new Error("Требуется авторизация");
    }
    if (!response.ok) {
      throw new Error(`Не удалось загрузить файл быстрого ответа: ${savedFile.display_filename || savedFileId}`);
    }
    const blob = await response.blob();
    downloaded.push(new File(
      [blob],
      savedFile.display_filename || savedFileId,
      { type: savedFile.mime_type || blob.type || "application/octet-stream" },
    ));
  }
  return downloaded;
}

async function applyQuickReplyToComposer(payload, fallbackBody, token, cardId) {
  const replyId = payload?.id;
  const files = await downloadQuickReplyFiles(replyId, payload?.saved_files || []);
  if (destroyed || token !== state.quickReplyUseSeq || state.card?.id !== cardId || !canWriteComposer()) {
    return false;
  }
  if (files.length) {
    TicketComposer.queueSelectedFiles(composerCtx(), files);
  }
  insertTextIntoComposer(payload?.body || fallbackBody || "");
  return true;
}

async function useQuickReply(reply) {
  if (!reply?.id) return;
  if (state.quickReplyUseInFlight) return;
  if (!canWriteComposer()) {
    closeQuickRepliesPanel();
    return;
  }
  state.quickReplyUseInFlight = true;
  const token = ++state.quickReplyUseSeq;
  const cardId = state.card?.id;
  try {
    const payload = await api(`/api/v1/quick-replies/${encodeURIComponent(reply.id)}/use`, { method: "POST" });
    const applied = await applyQuickReplyToComposer(payload, reply.body || "", token, cardId);
    if (!applied) {
      closeQuickRepliesPanel();
      return;
    }
    closeQuickRepliesPanel({ focusComposer: true });
  } finally {
    state.quickReplyUseInFlight = false;
  }
}

async function openQuickRepliesEditorFromPanel() {
  if (!canUseQuickRepliesEditor()) return;
  saveDraftSelection();
  await flushDraftBeforeSend();
  window.location.href = quickReplyEditorUrl();
}

async function consumePendingQuickReplyUse() {
  let raw = "";
  try {
    raw = sessionStorage.getItem(pendingQuickReplyStorageKey()) || "";
    if (raw) sessionStorage.removeItem(pendingQuickReplyStorageKey());
  } catch {
    raw = "";
  }
  if (!raw) return;
  try {
    const payload = JSON.parse(raw);
    const token = ++state.quickReplyUseSeq;
    await applyQuickReplyToComposer(payload, "", token, state.card?.id);
  } catch (e) {
    showError(`Не удалось подготовить вложения быстрого ответа.\n\n${e.message || "Неизвестная ошибка"}`);
  }
}

function normalizeDraftMode(value) {
  return String(value || "").trim().toLowerCase() === "comment" ? "comment" : "reply";
}

function reconcileCleanDraftState() {
  if (!state.draftDirty || state.draftSaveInFlight || state.draftSavePromise) return;
  if (currentDraftText() !== state.draftLastSavedText) return;
  if (normalizeDraftMode(state.composeMode) !== state.draftLastSavedMode) return;
  state.draftDirty = false;
}

function applyServerDraft(card) {
  const revision = Number(card?.draft_revision || 0);
  if (!Number.isFinite(revision) || revision < state.draftAckRevision) return;
  const text = String(card?.draft_text || "");
  const mode = normalizeDraftMode(card?.draft_mode);
  const changed = revision > state.draftAckRevision
    || text !== state.draftLastSavedText
    || mode !== state.draftLastSavedMode;
  reconcileCleanDraftState();
  if (!changed || state.draftDirty || state.draftSaveInFlight) return;
  saveDraftSelection();
  if (el.messageInput && el.messageInput.value !== text) {
    el.messageInput.value = text;
  }
  if (state.composeMode !== mode) {
    state.composeMode = mode;
    applyComposeModeUi();
  }
  restoreDraftSelection();
  state.draftAckRevision = revision;
  state.draftLastSavedText = text;
  state.draftLastSavedMode = mode;
}

function markDraftCleanFromServer(payload) {
  const revision = Number(payload?.revision || 0);
  if (Number.isFinite(revision) && revision >= state.draftAckRevision) {
    state.draftAckRevision = revision;
  }
  state.draftLastSavedText = String(payload?.text || "");
  state.draftLastSavedMode = normalizeDraftMode(payload?.mode);
  state.draftDirty = false;
}

function draftExitSignature() {
  return JSON.stringify({
    text: currentDraftText(),
    mode: normalizeDraftMode(state.composeMode),
  });
}

async function saveDraftNow({ keepalive = false } = {}) {
  if (!el.messageInput || !state.ticketId) return null;
  if (state.draftSaveTimer !== null) {
    window.clearTimeout(state.draftSaveTimer);
    state.draftSaveTimer = null;
  }
  if (!keepalive && state.draftSavePromise) {
    return state.draftSavePromise.then(() => {
      const latestText = currentDraftText();
      const latestMode = normalizeDraftMode(state.composeMode);
      if (state.draftDirty || latestText !== state.draftLastSavedText || latestMode !== state.draftLastSavedMode) {
        return saveDraftNow();
      }
      return null;
    });
  }
  const text = currentDraftText();
  const mode = normalizeDraftMode(state.composeMode);
  if (!state.draftDirty && text === state.draftLastSavedText && mode === state.draftLastSavedMode) return null;
  const body = JSON.stringify({ text, mode });
  if (keepalive && window.fetch) {
    try {
      window.fetch(`/api/v1/tickets/${state.ticketId}/draft`, {
        method: "PATCH",
        headers: { "content-type": "application/json", accept: "application/json" },
        body,
        keepalive: true,
      });
    } catch {
      // noop
    }
    return null;
  }
  state.draftSaveInFlight = true;
  state.draftSavePromise = (async () => {
    const saved = await api(`/api/v1/tickets/${state.ticketId}/draft`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body,
    });
    if (currentDraftText() === text && normalizeDraftMode(state.composeMode) === mode) {
      markDraftCleanFromServer(saved);
    } else {
      state.draftDirty = true;
    }
    return saved;
  })();
  try {
    return await state.draftSavePromise;
  } catch (e) {
    state.draftDirty = true;
    throw e;
  } finally {
    state.draftSaveInFlight = false;
    state.draftSavePromise = null;
    if (state.draftDirty) {
      scheduleDraftSave(400);
    }
  }
}

function scheduleDraftSave(delayMs = 400) {
  if (state.draftSaveTimer !== null) {
    window.clearTimeout(state.draftSaveTimer);
  }
  state.draftSaveTimer = window.setTimeout(() => {
    void saveDraftNow().catch((e) => setStatus(`Ошибка сохранения черновика: ${e.message}`));
  }, delayMs);
}

function handleComposerInput() {
  state.draftDirty = true;
  state.draftExitFlushSignature = "";
  saveDraftSelection();
  scheduleDraftSave(400);
}

function handleComposeModeChanged() {
  state.draftDirty = true;
  state.draftExitFlushSignature = "";
  scheduleDraftSave(0);
}

async function flushDraftBeforeSend() {
  if (state.draftSaveTimer !== null) {
    window.clearTimeout(state.draftSaveTimer);
    state.draftSaveTimer = null;
  }
  while (state.draftSavePromise || state.draftDirty) {
    if (state.draftSavePromise) {
      await state.draftSavePromise;
    } else {
      await saveDraftNow();
    }
  }
}

function flushDraftKeepalive() {
  saveDraftSelection();
  if (!state.draftDirty && state.draftSaveTimer === null) return;
  const signature = draftExitSignature();
  if (state.draftExitFlushSignature === signature) return;
  state.draftExitFlushSignature = signature;
  if (state.draftSavePromise) {
    state.draftSavePromise
      .then(() => {
        const latestText = currentDraftText();
        const latestMode = normalizeDraftMode(state.composeMode);
        if (state.draftDirty || latestText !== state.draftLastSavedText || latestMode !== state.draftLastSavedMode) {
          return saveDraftNow();
        }
        return null;
      })
      .catch(() => {});
    return;
  }
  void saveDraftNow({ keepalive: true });
}

function clearDraftAfterSuccessfulSend() {
  if (state.draftSaveTimer !== null) {
    window.clearTimeout(state.draftSaveTimer);
    state.draftSaveTimer = null;
  }
  state.draftDirty = false;
  state.draftLastSavedText = "";
  state.draftLastSavedMode = "reply";
  clearDraftSelection();
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
function setStatus() {}

function showError(message) {
  el.errorText.textContent = message || "Неизвестная ошибка";
  el.errorModal.classList.remove("hidden");
}

function closeError() {
  if (el.errorModal.classList.contains("hidden")) return;
  el.errorModal.classList.add("hidden");
  if (state.returnToListOnErrorClose) shellNavigateOrReload("/tickets");
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


async function loadTabCounts(options = {}) {
  const silent = Boolean(options.silent);
  try {
    await window.HelpdeskSidebar.refreshCounts();
    const sidebarState = window.HelpdeskSidebar.getState();
    state.counts = sidebarState.counts || state.counts;
  } catch (e) {
    if (!silent) showError(`Не удалось обновить счётчики заявок.\n\n${e.message || "Неизвестная ошибка"}`);
  }
}

function messageAuthorLabel(message) {
  const messageType = typeof message === "string" ? message : message?.message_type;
  const card = state.card || {};
  const profile = card.client_profile || {};
  const fullName = `${profile.last_name || ""} ${profile.contact_name || ""}`.trim();
  const clientName = profile.display_name || fullName || profile.telegram_display_name || card.client_name || "Клиент";
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

function refreshSendByEnterUi() {
  if (typeof TicketComposer.refreshSendByEnterUi === "function") {
    TicketComposer.refreshSendByEnterUi(composerCtx());
  } else if (el.sendHotkeyHint) {
    el.sendHotkeyHint.textContent = state.sendByEnter ? "Новая строка: Shift+Enter" : "Отправка: Ctrl+Enter";
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
  if (el.addFilesBtn) el.addFilesBtn.disabled = isClosed || isReadonly;
  if (el.quickRepliesBtn) el.quickRepliesBtn.disabled = isClosed || isReadonly;
  if (isClosed || isReadonly) closeQuickRepliesPanel();

  TicketControls.renderTicketControlsView(controlsCtx());
  if (renderResult.wasNearBottom) {
    el.messages.scrollTop = el.messages.scrollHeight;
  }
}

async function loadCard(options = {}) {
  if (destroyed || state.returnToListOnErrorClose) return;
  if (state.isLoading) {
    state.pendingCardReloadOptions = {
      silent: state.pendingCardReloadOptions
        ? Boolean(state.pendingCardReloadOptions.silent) && Boolean(options.silent)
        : Boolean(options.silent),
      preserveComposerDraft: Boolean(state.pendingCardReloadOptions?.preserveComposerDraft) || Boolean(options.preserveComposerDraft),
    };
    return;
  }
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
    if (!options.preserveComposerDraft) {
      applyServerDraft(card);
    }
    TicketComposer.syncRestoredPreloads?.(composerCtx(), card.active_attachment_preloads || []);
    if (!state.clientCardInline?.open) {
      state.clientCardViewConfig = cardConfig && Array.isArray(cardConfig.blocks)
        ? cardConfig
        : defaultClientCardViewConfig();
    }
    renderCard();
    applyComposeModeUi();
    if (!state.initialComposerFocusApplied) {
      state.initialComposerFocusApplied = true;
      focusMessageInputIfAvailable();
      restoreDraftSelection();
    }
    await consumePendingQuickReplyUse();
    setStatus("Карточка загружена");
    await loadTaskPanel({ silent: true });
  } catch (e) {
    if (destroyed) return;
    console.error("loadCard failed", e);
    if (!state.card && (e.status === 403 || e.status === 404)) {
      state.returnToListOnErrorClose = true;
      showError(e.status === 404 ? "Тикет не найден" : "Нет доступа к заявке");
      return;
    }
    if (!silent) setStatus(`Ошибка: ${e.message}`);
    if (!silent) showError(e.message || "Ошибка загрузки карточки");
  } finally {
    state.isLoading = false;
    const pendingOptions = state.pendingCardReloadOptions;
    state.pendingCardReloadOptions = null;
    if (!destroyed && pendingOptions) {
      await loadCard(pendingOptions);
    }
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
  if (isTicketStateAction(name) && state.stateActionInFlight) {
    return;
  }
  try {
    if (isTicketStateAction(name)) {
      state.stateActionInFlight = name;
      renderTicketStateControls();
    }
    setStatus("Выполняю действие...");
    await apiPost(`/api/v1/tickets/${state.ticketId}/${name}`, body);
    if (name === "restore") {
      startRestoreCloseCooldown();
    }
    await loadCard();
    setStatus("Готово");
  } catch (e) {
    showError(e.message);
  } finally {
    if (isTicketStateAction(name)) {
      state.stateActionInFlight = null;
      renderTicketStateControls();
    }
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
  } catch (e) {
    showError(`Ошибка статуса: ${e.message}`);
  }
}

async function loadComposerPreferences(options = {}) {
  const silent = Boolean(options.silent);
  try {
    await TicketClientCard.waitForBlockPreferences();
    const payload = await api("/api/v1/users/me/preferences");
    state.clientCardCollapsedBlocks = payload?.client_card_collapsed_blocks || {};
    state.sendByEnter = Boolean(payload?.send_by_enter);
    state.taskPanelEnabled = payload?.task_panel_enabled === true;
    refreshSendByEnterUi();
    renderTaskPanel();
  } catch (e) {
    state.sendByEnter = false;
    state.taskPanelEnabled = false;
    refreshSendByEnterUi();
    renderTaskPanel();
    if (!silent) {
      showError(`Ошибка настройки composer: ${e.message}`);
    }
  }
}

async function updateSendByEnterPreference(nextValue) {
  const previousValue = Boolean(state.sendByEnter);
  if (state.isSavingSendByEnterPreference) return;
  state.isSavingSendByEnterPreference = true;
  state.sendByEnter = Boolean(nextValue);
  refreshSendByEnterUi();
  try {
    const payload = await apiPatch("/api/v1/users/me/preferences", {
      send_by_enter: state.sendByEnter,
    });
    state.sendByEnter = Boolean(payload?.send_by_enter);
    setStatus("Настройка composer сохранена");
  } catch (e) {
    state.sendByEnter = previousValue;
    showError(`Не удалось сохранить настройку composer: ${e.message}`);
  } finally {
    state.isSavingSendByEnterPreference = false;
    refreshSendByEnterUi();
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
  if (el.roleSelect) el.roleSelect.value = state.role;
  if (el.agentSelect) el.agentSelect.value = state.userId;
  renderAgentSelect();
  updateTabLinks();
  renderTaskPanel();
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
function bindPageHandlers() {
  if (pageHandlersBound) return;
  pageHandlersBound = true;

  el.messageContextMenu = document.createElement("div");
  el.messageContextMenu.className = "message-context-menu hidden";
  document.body.appendChild(el.messageContextMenu);

  el.roleSelect.addEventListener("change", async () => {
    // TODO(remove-before-production): admin preview mode is temporarily disabled.
    return;
  });

  el.backToListBtn.addEventListener("click", () => {
    if (window.history.length <= 1) shellNavigateOrReload("/tickets");
    else if (window.HelpdeskAppShell?.goBack) void window.HelpdeskAppShell.goBack();
    else window.history.back();
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

  mountQuickRepliesPanelOverlay();

  if (el.quickRepliesBtn) {
    el.quickRepliesBtn.addEventListener("mousedown", (event) => {
      event.preventDefault();
      saveDraftSelection();
    });
    el.quickRepliesBtn.addEventListener("click", (event) => {
      void toggleQuickRepliesPanel({ focusSearch: false }).catch((e) => showError(`Не удалось открыть быстрые ответы.\n\n${e.message || "Неизвестная ошибка"}`));
    });
  }
  if (el.quickRepliesSearchInput) {
    el.quickRepliesSearchInput.addEventListener("input", scheduleQuickReplySearch);
    el.quickRepliesSearchInput.addEventListener("keydown", (event) => {
      if (!state.quickRepliesOpen) return;
      const rows = quickReplyNavigableRows();
      if (event.key === "ArrowDown") {
        event.preventDefault();
        event.stopPropagation();
        setQuickReplyHighlight(state.quickRepliesHighlightedIndex + 1, { focusSearch: true });
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        event.stopPropagation();
        setQuickReplyHighlight(state.quickRepliesHighlightedIndex - 1, { focusSearch: true });
      } else if (event.key === "Enter") {
        event.preventDefault();
        event.stopPropagation();
        const row = rows[state.quickRepliesHighlightedIndex];
        if (row === el.quickRepliesSearchInput) return;
        if (row === el.quickRepliesEditorRow) {
          void openQuickRepliesEditorFromPanel().catch((e) => showError(`Не удалось открыть редактор быстрых ответов.\n\n${e.message || "Неизвестная ошибка"}`));
          return;
        }
        const reply = state.quickReplies.find((item) => String(item.id) === String(row?.dataset?.quickReplyId));
        if (reply) void useQuickReply(reply).catch((e) => showError(`Не удалось вставить быстрый ответ.\n\n${e.message || "Неизвестная ошибка"}`));
      } else if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        closeQuickRepliesPanel({ focusComposer: true });
      }
    });
  }
  if (el.quickRepliesPanelRows) {
    el.quickRepliesPanelRows.addEventListener("click", (event) => {
      const row = event.target?.closest?.("[data-quick-reply-id]");
      const reply = state.quickReplies.find((item) => String(item.id) === String(row?.dataset?.quickReplyId));
      if (reply) void useQuickReply(reply).catch((e) => showError(`Не удалось вставить быстрый ответ.\n\n${e.message || "Неизвестная ошибка"}`));
    });
    el.quickRepliesPanelRows.addEventListener("mouseover", (event) => {
      const row = event.target?.closest?.("[data-quick-reply-id]");
      const rows = quickReplyNavigableRows();
      const index = rows.indexOf(row);
      if (index >= 0) setQuickReplyHighlight(index);
    });
  }
  if (el.quickRepliesEditorRow) {
    el.quickRepliesEditorRow.addEventListener("click", () => {
      if (el.quickRepliesEditorRow.disabled) return;
      void openQuickRepliesEditorFromPanel().catch((e) => showError(`Не удалось открыть редактор быстрых ответов.\n\n${e.message || "Неизвестная ошибка"}`));
    });
  }

  if (typeof TicketControls.bindTicketControlHandlers === "function") {
    TicketControls.bindTicketControlHandlers(controlsCtx());
  }

  if (el.taskPanelMoreBtn) {
    el.taskPanelMoreBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      state.taskPanelDropdownOpen = !state.taskPanelDropdownOpen;
      el.taskPanelMoreBtn.classList.toggle("active", state.taskPanelDropdownOpen);
      el.taskPanelDropdown?.classList.toggle("hidden", !state.taskPanelDropdownOpen);
    });
  }

  el.okErrorBtn.addEventListener("click", closeError);
  el.copyErrorBtn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(el.errorText.textContent);
    } catch {
      const range = document.createRange();
      range.selectNodeContents(el.errorText);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      document.execCommand("copy");
    }
  });
  closeOnBackdropClick(el.errorModal, closeError);
  el.fDestination.addEventListener("change", syncDynamicModalFields);
  el.fLocationType.addEventListener("change", syncDynamicModalFields);

  keydownHandler = (e) => {
    if (isQuickRepliesHotkey(e)) {
      const tag = String(document.activeElement?.tagName || "").toLowerCase();
      const inModal = Boolean(document.activeElement?.closest?.(".modal"));
      if (!inModal && (document.activeElement === el.messageInput || tag === "body" || document.activeElement === el.quickRepliesSearchInput)) {
        e.preventDefault();
        void toggleQuickRepliesPanel({ focusSearch: true }).catch((err) => showError(`Не удалось открыть быстрые ответы.\n\n${err.message || "Неизвестная ошибка"}`));
        return;
      }
    }
    if (e.key === "Escape") {
      if (state.quickRepliesOpen) {
        closeQuickRepliesPanel({ focusComposer: true });
        return;
      }
      hideMessageContextMenu();
      closeImagePreview();
      closeTaskPanelDropdown();
    }
    if (e.key === "Escape" && !el.errorModal.classList.contains("hidden")) {
      closeError();
      return;
    }
    if (e.key === "Escape" && !el.clientModal.classList.contains("hidden")) {
      e.preventDefault();
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

  documentClickHandler = (event) => {
    if (state.quickRepliesOpen && !isQuickRepliesClickInside(event.target)) {
      closeQuickRepliesPanel();
    }
    hideMessageContextMenu();
    if (!state.taskPanelDropdownOpen) return;
    if (event.target.closest("#taskPanel")) return;
    closeTaskPanelDropdown();
  };
  resizeHandler = () => {
    hideMessageContextMenu();
    if (state.quickRepliesOpen) positionQuickRepliesPanel();
  };
  document.addEventListener("click", documentClickHandler);
  window.addEventListener("resize", resizeHandler);
  el.messages.addEventListener("scroll", hideMessageContextMenu);

  beforeUnloadHandler = (event) => {
    if (event.type === "beforeunload" && (TicketClientCard.hasInlineChanges(clientCardCtx()) || state.clientCardInline?.saving)) {
      event.preventDefault();
      event.returnValue = "";
      flushDraftKeepalive();
      return;
    }
    flushDraftKeepalive();
    if (event.type === "pagehide" && !event.persisted) {
      TicketAttachments.destroyAllAttachmentResources();
    }
  };
  window.addEventListener("beforeunload", beforeUnloadHandler);
  window.addEventListener("pagehide", beforeUnloadHandler);
}

function initPostSidebarState() {
  if (!["active", "unassigned", "special", "frozen", "closed"].includes(state.tab)) {
    state.tab = "active";
  }
  localStorage.setItem("hd_tab", state.tab);

  el.roleSelect.value = state.role;
  el.agentSelect.value = state.userId;
  updateTabLinks();
  applyComposeModeUi();
  refreshSendByEnterUi();
}

function startPolling() {
  if (pollTimer !== null || state.returnToListOnErrorClose) return;
  pollTimer = window.setInterval(() => {
    if (document.hidden) return;
    if (!el.clientModal.classList.contains("hidden")) return;
    loadCard({ silent: true });
  }, 3000);
}

(async function run() {
  bindPageHandlers();
  await window.HelpdeskSidebar.init();
  if (destroyed) return;
  syncSidebarState(window.HelpdeskSidebar.getState());
  unsubscribeSidebar = window.HelpdeskSidebar.onChange(syncSidebarState);
  initPostSidebarState();
  if (destroyed) return;
  if (state.isRealAdmin) {
    await loadRoleUsers();
  } else {
    renderAgentSelect();
  }
  try {
    await Promise.all([loadMe(), loadComposerPreferences()]);
  } catch (e) {
    console.error("loadMe failed", e);
  }
  if (destroyed) return;
  await Promise.all([loadCard(), loadTabCounts()]);
  startPolling();
})();

document.querySelectorAll("#appContent input, #appContent textarea").forEach((field) => {
  field.setAttribute("autocomplete", "off");
  field.setAttribute("autocorrect", "off");
  field.setAttribute("autocapitalize", "off");
  field.setAttribute("spellcheck", "false");
});

return {
  destroy: cleanupTicketCardPage,
  canNavigateAway: () => {
    if (state.clientCardInline?.open) return closeClientModal();
    return true;
  },
};
}

if (window.HelpdeskAppShell?.registerPage) {
  window.HelpdeskAppShell.registerPage("ticket-card", initTicketCardPage);
} else {
  initTicketCardPage();
}
})();
