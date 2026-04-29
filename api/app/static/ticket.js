const params = new URLSearchParams(window.location.search);
const ticketId = window.location.pathname.split("/").pop();

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
  role: params.get("role") || localStorage.getItem("hd_role") || "agent",
  tab: params.get("tab") || localStorage.getItem("hd_tab") || "active",
  userId: params.get("user_id") || localStorage.getItem("hd_user_id") || "00000000-0000-0000-0000-000000000001",
  agents: [],
  card: null,
  wrongProblemMenu: null,
  quoteMessageId: null,
  composeMode: "reply",
  isLoading: false,
};

const el = {
  backToListBtn: document.getElementById("backToListBtn"),
  roleSelect: document.getElementById("roleSelect"),
  agentSelect: document.getElementById("agentSelect"),
  statusText: document.getElementById("statusText"),
  ticketSubject: document.getElementById("ticketSubject"),
  ticketMeta: document.getElementById("ticketMeta"),

  clientSectionTg: document.getElementById("clientSectionTg"),
  clientSectionProfile: document.getElementById("clientSectionProfile"),
  clientSectionWork: document.getElementById("clientSectionWork"),
  clientSectionVerification: document.getElementById("clientSectionVerification"),
  clientRowUsername: document.getElementById("clientRowUsername"),
  clientUsernameText: document.getElementById("clientUsernameText"),
  clientRowTelegramName: document.getElementById("clientRowTelegramName"),
  clientTelegramNameText: document.getElementById("clientTelegramNameText"),
  clientRowFullName: document.getElementById("clientRowFullName"),
  clientFullNameText: document.getElementById("clientFullNameText"),
  clientRowDepartmentBlock: document.getElementById("clientRowDepartmentBlock"),
  clientDepartmentLine1: document.getElementById("clientDepartmentLine1"),
  clientDepartmentLine2: document.getElementById("clientDepartmentLine2"),
  clientDepartmentLine3: document.getElementById("clientDepartmentLine3"),
  clientRowLocation: document.getElementById("clientRowLocation"),
  clientLocationText: document.getElementById("clientLocationText"),
  clientRowAnydesk: document.getElementById("clientRowAnydesk"),
  clientAnydeskText: document.getElementById("clientAnydeskText"),
  clientRowInternalNumber: document.getElementById("clientRowInternalNumber"),
  clientInternalNumberText: document.getElementById("clientInternalNumberText"),
  clientRowBitrixLogin: document.getElementById("clientRowBitrixLogin"),
  clientBitrixLoginText: document.getElementById("clientBitrixLoginText"),
  clientRowBitrixPassword: document.getElementById("clientRowBitrixPassword"),
  clientBitrixPasswordText: document.getElementById("clientBitrixPasswordText"),
  clientRowBitrixId: document.getElementById("clientRowBitrixId"),
  clientBitrixIdText: document.getElementById("clientBitrixIdText"),
  clientRowVerification: document.getElementById("clientRowVerification"),
  clientVerificationText: document.getElementById("clientVerificationText"),

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

  messages: document.getElementById("messages"),
  quotePreview: document.getElementById("quotePreview"),
  quotePreviewAuthor: document.getElementById("quotePreviewAuthor"),
  quotePreviewText: document.getElementById("quotePreviewText"),
  clearQuoteBtn: document.getElementById("clearQuoteBtn"),
  messageInput: document.getElementById("messageInput"),
  sendNowBtn: document.getElementById("sendNowBtn"),
  modeReplyBtn: document.getElementById("modeReplyBtn"),
  modeCommentBtn: document.getElementById("modeCommentBtn"),
  freezeUntil: document.getElementById("freezeUntil"),
  greetingBtn: document.getElementById("greetingBtn"),
  wrongProblemBtn: document.getElementById("wrongProblemBtn"),
  wrongProblemModal: document.getElementById("wrongProblemModal"),
  wrongProblemMenu: document.getElementById("wrongProblemMenu"),
  closeWrongProblemModalBtn: document.getElementById("closeWrongProblemModalBtn"),
  freezeToggleBtn: document.getElementById("freezeToggleBtn"),
  restoreBtn: document.getElementById("restoreBtn"),
  closeBtn: document.getElementById("closeBtn"),
  ticketAssigneeBox: document.getElementById("ticketAssigneeBox"),
  ticketAssigneeSelect: document.getElementById("ticketAssigneeSelect"),
  messageContextMenu: null,
  tabLinks: [...document.querySelectorAll("[data-tab-link]")],
  statusButtons: [...document.querySelectorAll("[data-availability-status]")],
  adminOnly: [...document.querySelectorAll(".admin-only")],
  agentAvatar: document.querySelector(".agent-avatar"),
};

let editMessageResolve = null;

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

function headers(extra = {}) {
  return {
    "x-user-role": state.role,
    "x-user-id": state.userId,
    accept: "application/json",
    ...extra,
  };
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: headers(options.headers || {}),
  });
  const payload = await response.json();
  if (!response.ok) {
    const detail = payload?.detail?.error?.message || "Ошибка запроса";
    throw new Error(detail);
  }
  return payload;
}

async function apiPatch(path, body) {
  return api(path, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

async function apiPost(path, body = null) {
  const options = { method: "POST" };
  if (body !== null) {
    options.headers = { "content-type": "application/json" };
    options.body = JSON.stringify(body);
  }
  return api(path, options);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderAgentSelect() {
  el.agentSelect.innerHTML = "";
  for (const agent of state.agents) {
    const option = document.createElement("option");
    option.value = agent.id;
    option.textContent = agent.full_name;
    el.agentSelect.appendChild(option);
  }
  if (!state.agents.some((agent) => agent.id === state.userId) && state.agents[0]) {
    state.userId = state.agents[0].id;
    localStorage.setItem("hd_user_id", state.userId);
  }
  el.agentSelect.value = state.userId;
}

function assigneeOptionsHtml(selectedId) {
  const options = [`<option value="" ${selectedId ? "" : "selected"}>нераспределён</option>`];
  for (const agent of state.agents) {
    options.push(
      `<option value="${escapeHtml(agent.id)}" ${agent.id === selectedId ? "selected" : ""}>${escapeHtml(agent.full_name)}</option>`
    );
  }
  return options.join("");
}

function renderTicketAssigneeSelect() {
  if (!state.card) return;
  el.ticketAssigneeSelect.innerHTML = assigneeOptionsHtml(state.card.assignee_id);
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
  }
}

async function loadAgents() {
  const agents = await api("/api/v1/users/agents");
  state.agents = agents || [];
  renderAgentSelect();
}

function fmtDate(dateIso) {
  const d = new Date(dateIso);
  return d.toLocaleString("ru-RU", { hour12: false });
}

function fmtMessageStamp(dateIso) {
  const d = new Date(dateIso);
  const time = d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  const date = d.toLocaleDateString("ru-RU");
  return `${time} ${date}`;
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
    if (messageType === "system") return "system";
  }

  const labels = {
    client: "Клиент",
    agent: "Агент",
    internal: "Комментарий",
    system: "system",
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

function canEditAgentMessage(message) {
  return message.message_type === "agent" && message.message_status !== "deleted" && state.card?.status !== "closed";
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
  modal.addEventListener("click", (event) => {
    if (event.target === modal) close(null);
  });
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
  element.style.display = isVisible ? "inline-flex" : "none";
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

function refreshSectionVisibility(sectionEl) {
  if (!sectionEl) return;
  const rows = [...sectionEl.querySelectorAll(".client-row")];
  const hasVisibleRows = rows.some((r) => !r.classList.contains("hidden"));
  sectionEl.classList.toggle("hidden", !hasVisibleRows);
}

function computeDepartmentLines(profile) {
  const destination = (profile.destination || "").trim();
  const sandOwner = (profile.sand_owner || "").trim();
  const salesGroup = (profile.sales_group || "").trim();

  if (destination === "Песок" && sandOwner) {
    return ["Стажировка", sandOwner, ""];
  }

  if (destination === "Отдел Продаж" && salesGroup) {
    if (SALES_FIRST_DEPARTMENT.has(salesGroup)) {
      return ["Отдел продаж", "Первый департамент", salesGroup];
    }
    if (SALES_SECOND_DEPARTMENT.has(salesGroup)) {
      return ["Отдел продаж", "Второй отдел", salesGroup];
    }
    if (salesGroup === "группа Москвиченко") {
      return ["Отдел продаж", "Третий отдел", salesGroup];
    }
    return ["Отдел продаж", "", salesGroup];
  }

  if (!isBlank(destination)) {
    return [destination, "", ""];
  }

  return ["", "", ""];
}

function fillClientPanel(profile) {
  el.clientRowUsername.classList.remove("hidden");
  el.clientUsernameText.innerHTML = "";
  if (!isBlank(profile.telegram_username)) {
    const username = String(profile.telegram_username).trim().replace(/^@/, "");
    const link = document.createElement("a");
    link.href = `tg://resolve?domain=${encodeURIComponent(username)}`;
    link.textContent = `@${username}`;
    el.clientUsernameText.appendChild(link);
  } else {
    el.clientUsernameText.textContent = "нет";
  }

  el.clientRowTelegramName.classList.remove("hidden");
  el.clientTelegramNameText.textContent = isBlank(profile.telegram_display_name)
    ? "нет"
    : String(profile.telegram_display_name).trim();

  const fullName = `${profile.last_name || ""} ${profile.contact_name || ""}`.trim();
  setRowValue(el.clientRowFullName, el.clientFullNameText, fullName);

  const [line1, line2, line3] = computeDepartmentLines(profile);
  const hasDepartmentBlock = !isBlank(line1) || !isBlank(line2) || !isBlank(line3);
  el.clientRowDepartmentBlock.classList.toggle("hidden", !hasDepartmentBlock);
  el.clientDepartmentLine1.textContent = line1 || "";
  el.clientDepartmentLine2.textContent = line2 || "";
  el.clientDepartmentLine3.textContent = line3 || "";
  el.clientDepartmentLine1.classList.toggle("hidden", isBlank(line1));
  el.clientDepartmentLine2.classList.toggle("hidden", isBlank(line2));
  el.clientDepartmentLine3.classList.toggle("hidden", isBlank(line3));

  setRowValue(el.clientRowLocation, el.clientLocationText, profile.location_type);
  setRowValue(el.clientRowAnydesk, el.clientAnydeskText, profile.anydesk);
  setRowValue(el.clientRowInternalNumber, el.clientInternalNumberText, profile.internal_number);
  setRowValue(el.clientRowBitrixLogin, el.clientBitrixLoginText, profile.bitrix_login);
  setRowValue(el.clientRowBitrixPassword, el.clientBitrixPasswordText, profile.bitrix_password);
  setRowValue(el.clientRowBitrixId, el.clientBitrixIdText, profile.bitrix_id);
  el.clientRowVerification.classList.remove("hidden");
  el.clientVerificationText.textContent = profile.is_verified ? "✓" : "✕";
  el.clientVerificationText.classList.toggle("verified-ok", Boolean(profile.is_verified));
  el.clientVerificationText.classList.toggle("verified-no", !profile.is_verified);

  refreshSectionVisibility(el.clientSectionTg);
  refreshSectionVisibility(el.clientSectionProfile);
  refreshSectionVisibility(el.clientSectionWork);
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
  syncDynamicModalFields();
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
  addContextMenuItem(menu, "Цитировать", () => quoteMessage(message));
  addContextMenuItem(menu, "Копировать", () => copyMessageText(message));

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

function renderCard() {
  const card = state.card;
  const isTask = card.workflow_type === "task";
  const typeLabel = card.workflow_type === "auto" ? "Автоматическая" : (isTask ? "Задача" : "Заявка");
  el.ticketSubject.textContent = card.subject;
  el.ticketMeta.textContent = `Тип: ${typeLabel} | Сбор: ${intakeLabel(card.intake_state)} | Статус: ${statusLabel(card.status)} | Заморозка: ${freezeLabel(card)} | Ответственный: ${card.assignee_name || "нераспределён"}`;
  fillClientPanel(card.client_profile || {});

  const wasNearBottom = el.messages.scrollHeight - el.messages.scrollTop - el.messages.clientHeight < 80;
  el.messages.innerHTML = "";
  for (const m of card.messages) {
    const div = document.createElement("div");
    const cls = m.message_type || "";
    div.className = `message ${cls} ${m.message_status && m.message_status !== "normal" ? `message-${m.message_status}` : ""}`.trim();
    if (["client", "agent", "internal"].includes(m.message_type)) {
      div.addEventListener("contextmenu", (event) => showMessageContextMenu(event, m));
    }
    const headerEl = document.createElement("div");
    headerEl.className = "message-header";
    const strong = document.createElement("strong");
    strong.textContent = messageAuthorLabel(m);
    const dateEl = document.createElement("div");
    dateEl.className = "message-date";
    dateEl.textContent = fmtMessageStamp(m.created_at);
    headerEl.append(strong, dateEl);
    const bodyEl = document.createElement("div");
    bodyEl.className = "message-body";
    bodyEl.textContent = m.body || "";
    div.append(headerEl, bodyEl);
    const note = messageStatusNote(m);
    if (note) {
      const noteEl = document.createElement("div");
      noteEl.className = "message-status-note";
      noteEl.textContent = note;
      div.appendChild(noteEl);
    }
    el.messages.appendChild(div);
  }
  if (state.quoteMessageId && !card.messages.some((m) => m.id === state.quoteMessageId)) {
    state.quoteMessageId = null;
  }
  refreshComposerActions();

  const isClosed = card.status === "closed";
  const hasAgentMessage = card.messages.some((m) => m.message_type === "agent");
  const hasAnyMessage = card.messages.length > 0;
  const greeted = hasGreetingMessage(card.messages);
  const isFirstConnection = card.subject === "Первое подключение к боту";
  const firstConnectionVerified = Boolean(card.client_profile?.is_verified);

  el.modeReplyBtn.disabled = isClosed;
  el.modeCommentBtn.disabled = isClosed;
  el.messageInput.disabled = isClosed;
  el.sendNowBtn.disabled = isClosed;

  const showGreeting = !isClosed && greetingAllowed() && !greeted;
  const showClose = !isClosed && (!isFirstConnection || firstConnectionVerified) && (state.role === "admin" || (hasAnyMessage && hasAgentMessage));
  const showFreezeToggle = !isClosed;
  const showRestore = isClosed;
  const showWrongProblem = !isClosed;

  setVisible(el.greetingBtn, showGreeting);
  setVisible(el.wrongProblemBtn, showWrongProblem);
  setVisible(el.closeBtn, showClose);
  setVisible(el.freezeToggleBtn, showFreezeToggle);
  setVisible(el.restoreBtn, showRestore);
  el.ticketAssigneeBox.classList.toggle("hidden", state.role !== "admin");
  renderTicketAssigneeSelect();

  el.closeBtn.textContent = isTask ? "Завершить задачу" : "Закрыть заявку";
  el.freezeToggleBtn.textContent = card.is_frozen ? "Разморозить" : "Заморозить";
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
    state.card = await api(`/api/v1/tickets/${state.ticketId}`);
    renderCard();
    applyComposeModeUi();
    setStatus("Карточка загружена");
  } catch (e) {
    if (!silent) setStatus(`Ошибка: ${e.message}`);
  } finally {
    state.isLoading = false;
  }
}

async function sendByMode() {
  const text = el.messageInput.value.trim();
  if (!text) return;

  const actionName = state.composeMode === "reply" ? "reply" : "comment";
  try {
    setStatus("Отправка...");
    const quoteMessageId = state.quoteMessageId;
    await api(`/api/v1/tickets/${state.ticketId}/${actionName}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text, quote_message_id: quoteMessageId }),
    });
    el.messageInput.value = "";
    state.quoteMessageId = null;
    await loadCard();
    if (state.card?.workflow_type === "task" && state.composeMode === "reply") {
      setStatus("Ответ сохранён. Клиент получит последнее сообщение после завершения задачи.");
    } else {
      setStatus("Сообщение отправлено");
    }
  } catch (e) {
    showError(e.message);
  }
}

async function action(name, body = null) {
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
      ticket_ids: [state.ticketId],
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
  el.adminOnly.forEach((node) => {
    node.classList.toggle("hidden", !isAdmin);
  });
  if (!isAdmin && state.tab === "unassigned") {
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

function init() {
  el.messageContextMenu = document.createElement("div");
  el.messageContextMenu.className = "message-context-menu hidden";
  document.body.appendChild(el.messageContextMenu);

  if (!["active", "unassigned", "frozen", "closed"].includes(state.tab)) {
    state.tab = "active";
  }
  localStorage.setItem("hd_tab", state.tab);

  el.roleSelect.value = state.role;
  el.agentSelect.value = state.userId;
  updateTabLinks();
  applyComposeModeUi();

  el.roleSelect.addEventListener("change", async () => {
    state.role = el.roleSelect.value;
    localStorage.setItem("hd_role", state.role);
    updateTabLinks();
    await loadMe();
    await loadCard();
  });

  el.backToListBtn.addEventListener("click", () => {
    window.location.href = `/tickets?tab=${encodeURIComponent(state.tab)}`;
  });

  el.agentSelect.addEventListener("change", async () => {
    state.userId = el.agentSelect.value;
    localStorage.setItem("hd_user_id", state.userId);
    await loadMe();
    await loadCard();
  });

  el.statusButtons.forEach((btn) => {
    btn.addEventListener("click", async () => {
      await updateAvailabilityStatus(btn.dataset.availabilityStatus);
    });
  });

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
  el.restoreBtn.addEventListener("click", () => action("restore"));
  el.wrongProblemBtn.addEventListener("click", openWrongProblemModal);

  el.ticketAssigneeSelect.addEventListener("change", async () => {
    await assignCurrentTicket(el.ticketAssigneeSelect.value || null);
  });

  el.editClientBtn.addEventListener("click", openClientModal);
  el.closeClientModalBtn.addEventListener("click", closeClientModal);
  el.closeWrongProblemModalBtn.addEventListener("click", closeWrongProblemModal);
  el.saveClientBtn.addEventListener("click", saveClientProfile);
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
  el.clientModal.addEventListener("click", (e) => {
    if (e.target === el.clientModal) closeClientModal();
  });
  el.wrongProblemModal.addEventListener("click", (e) => {
    if (e.target === el.wrongProblemModal) closeWrongProblemModal();
  });
  el.errorModal.addEventListener("click", (e) => {
    if (e.target === el.errorModal) closeError();
  });

  el.fDestination.addEventListener("change", syncDynamicModalFields);
  el.fLocationType.addEventListener("change", syncDynamicModalFields);

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      hideMessageContextMenu();
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
  });

  document.addEventListener("click", hideMessageContextMenu);
  window.addEventListener("resize", hideMessageContextMenu);
  el.messages.addEventListener("scroll", hideMessageContextMenu);

  window.setInterval(() => {
    if (document.hidden) return;
    if (!el.clientModal.classList.contains("hidden")) return;
    loadCard({ silent: true });
  }, 3000);
}

(async function run() {
  init();
  await loadAgents();
  await loadMe();
  await loadCard();
})();

document.querySelectorAll("input, textarea").forEach((field) => {
  field.setAttribute("autocomplete", "off");
  field.setAttribute("autocorrect", "off");
  field.setAttribute("autocapitalize", "off");
  field.setAttribute("spellcheck", "false");
});
