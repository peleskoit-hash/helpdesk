(function () {
function initSettingsUsersPage() {
const state = {
  users: [],
  editingUserId: null,
  deletionTarget: null,
  deletionModal: null,
  deletionPending: false,
  disposed: false,
  currentTelegramNotification: null,
  sparkTicket: 0, sparkBinding: null, sparkSync: null, sparkBusy: false, sparkEmployees: [],
  sparkUnlinkContext: null,
};

const spark = Object.fromEntries(["sparkBlock", "sparkSummary", "openSparkBtn", "sparkModal", "closeSparkBtn",
  "sparkLocalName", "sparkBindingStatus", "sparkIdentity", "sparkShift", "sparkSyncStatus", "sparkSyncTimes",
  "sparkFeedback", "sparkReconnectBtn", "sparkChooseBtn", "sparkRefreshBtn", "sparkDisableBtn", "sparkUnlinkBtn", "sparkPicker",
  "sparkSearch", "sparkEmployees", "sparkListInfo", "sparkConfirm", "sparkConfirmText", "sparkBindBtn",
  "confirmSparkUnlinkModal", "sparkUnlinkIdentity", "cancelSparkUnlinkBtn", "confirmSparkUnlinkBtn"]
  .map(id => [id, document.getElementById(id)]));

const roleLabels = { admin: "Администратор", agent: "Тех. поддержка", chiki_piki: "Call Center Observer" };
const statusLabels = { offline: "Не в сети", online: "В сети", on_ticket: "На заявке", waiting: "В ожидании", lunch: "Обед" };

const el = {
  backToSettingsBtn: document.getElementById("backToSettingsBtn"),
  createUserBtn: document.getElementById("createUserBtn"),
  usersTableBody: document.getElementById("usersTableBody"),
  emptyUsersText: document.getElementById("emptyUsersText"),
  userModal: document.getElementById("userModal"),
  userModalTitle: document.getElementById("userModalTitle"),
  closeUserModalBtn: document.getElementById("closeUserModalBtn"),
  saveUserBtn: document.getElementById("saveUserBtn"),
  deleteUserBtn: document.getElementById("deleteUserBtn"),
  confirmDeleteUserModal: document.getElementById("confirmDeleteUserModal"),
  confirmPermanentDeletionModal: document.getElementById("confirmPermanentDeletionModal"),
  userDeletionProgressModal: document.getElementById("userDeletionProgressModal"),
  continueDeleteUserBtn: document.getElementById("continueDeleteUserBtn"),
  confirmPermanentDeletionBtn: document.getElementById("confirmPermanentDeletionBtn"),
  startUserDeletionBtn: document.getElementById("startUserDeletionBtn"),
  closeUserDeletionBtn: document.getElementById("closeUserDeletionBtn"),
  userDeletionName: document.getElementById("userDeletionName"),
  userDeletionDescription: document.getElementById("userDeletionDescription"),
  userDeletionStages: document.querySelectorAll("#userDeletionStages [data-stage]"),
  userDeletionStatus: document.getElementById("userDeletionStatus"),
  userDeletionProgress: document.getElementById("userDeletionProgress"),
  fFullName: document.getElementById("fFullName"),
  fLogin: document.getElementById("fLogin"),
  fPassword: document.getElementById("fPassword"),
  fRole: document.getElementById("fRole"),
  fIsActive: document.getElementById("fIsActive"),
  fLegacyTicketCount: document.getElementById("fLegacyTicketCount"),
  telegramNotificationBlock: document.getElementById("telegramNotificationBlock"),
  telegramNotificationStatus: document.getElementById("telegramNotificationStatus"),
  telegramNotificationDisplay: document.getElementById("telegramNotificationDisplay"),
  errorModal: document.getElementById("errorModal"),
  errorText: document.getElementById("errorText"),
  closeErrorModalBtn: document.getElementById("closeErrorModalBtn"),
  copyErrorBtn: document.getElementById("copyErrorBtn"),
  okErrorBtn: document.getElementById("okErrorBtn"),
};

function setStatus() {}
function escapeHtml(v) { return String(v ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;"); }
function fmtLastLogin(value) {
  if (!value) return "—";
  const normalized = /(?:Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : value + "Z";
  return Number.isNaN(new Date(normalized).getTime()) ? "—" : fmtDate(normalized);
}
function fmtDate(d) { return d ? new Date(d).toLocaleString("ru-RU", { hour12: false }) : "-"; }

async function api(path, options = {}) {
  const r = await fetch(path, {
    ...options,
    credentials: "include",
    headers: { accept: "application/json", ...(options.headers || {}) },
  });
  const t = await r.text();
  let p = null;
  if (t) {
    try { p = JSON.parse(t); } catch { p = null; }
  }
  if (!r.ok) {
    const msg = p?.detail?.error?.message || t || "Ошибка запроса";
    const err = new Error(msg);
    err.status = r.status;
    err.code = p?.detail?.error?.code;
    throw err;
  }
  return p;
}

function showError(m) { el.errorText.value = m || "Неизвестная ошибка"; el.errorModal.classList.remove("hidden"); }
function closeError() { el.errorModal.classList.add("hidden"); }
function sparkApi(path, options = {}) {
  // Live list/binding can require two requests, each bounded by the server's
  // existing maximum of 30 seconds. Snapshot/status reads remain short.
  const timeout = path.includes("/employee-schedule/employees") || options.method === "PATCH" ? 65000 : 15000;
  return api(path, {...options, signal: AbortSignal.timeout(timeout)});
}

function closeOnBackdropClick(modal, closeHandler) {
  let down = false;
  modal.addEventListener("pointerdown", (e) => { down = e.target === modal; });
  modal.addEventListener("click", (e) => {
    if (down && e.target === modal) closeHandler();
    down = false;
  });
}

function fillModal(user = null) {
  state.sparkUnlinkContext = null;
  spark.confirmSparkUnlinkModal?.classList.add("hidden");
  state.sparkTicket++;
  state.sparkBinding = null;
  state.editingUserId = user?.id || null;
  state.currentTelegramNotification = user ? {
    bound: Boolean(user.telegram_notification_bound),
    display: user.telegram_notification_display || null,
  } : {
    bound: false,
    display: null,
  };
  el.userModalTitle.textContent = user ? "Редактирование пользователя" : "Создание пользователя";
  el.fFullName.value = user?.full_name || "";
  el.fLogin.value = user?.login || "";
  el.fPassword.value = "";
  el.fPassword.placeholder = user ? "Оставьте пустым, чтобы не менять" : "";
  el.fRole.value = user?.role || "agent";
  el.fIsActive.checked = user ? Boolean(user.is_active) : true;
  if (el.fLegacyTicketCount) el.fLegacyTicketCount.value = String(Math.max(0, Number(user?.legacy_ticket_count || 0)));
  syncTelegramNotificationBlock();
  syncSparkBlock();
  if (user && shouldShowTelegramNotification(user.role)) {
    const id = user.id;
    const ticket = state.sparkTicket;
    sparkApi(`/api/v1/settings/users/${encodeURIComponent(id)}/schedule`).then(binding => {
      if (!state.disposed && state.editingUserId === id && state.sparkTicket === ticket) {
        state.sparkBinding = binding;
        syncSparkBlock();
      }
    }).catch(() => {
      if (!state.disposed && state.editingUserId === id && state.sparkTicket === ticket) spark.sparkSummary.textContent = "Состояние неизвестно";
    });
  }
}

function syncSparkBlock() {
  if (!spark.sparkBlock) return;
  const user = state.users.find(user => user.id === state.editingUserId);
  const visible = user && shouldShowTelegramNotification(user.role) && shouldShowTelegramNotification(el.fRole.value);
  spark.sparkBlock.classList.toggle("hidden", !visible);
  spark.sparkSummary.textContent = state.sparkBinding ? (state.sparkBinding.enabled ? "Подключено" : "Не подключено") : "Состояние загружается…";
}

function sparkActive(ticket) {
  return !state.disposed && state.sparkTicket === ticket && !spark.sparkModal.classList.contains("hidden");
}
function sparkDate(value, timeOnly = false) {
  if (!value) return "—";
  const normalized = /(?:Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : value + "Z";
  return new Date(normalized).toLocaleString("ru-RU", {timeZone: "Europe/Moscow", ...(timeOnly ? {hour: "2-digit", minute: "2-digit"} : {} )});
}
function sparkError(error) {
  if (error.name === "TimeoutError" || error.name === "AbortError") return "Истекло время ожидания ответа. Новое обновление не подтверждено; повторите проверку позже.";
  const messages = {
    external_id_required: "Выберите сотрудника из Spark для подключения.",
    schedule_unavailable: "Нет опубликованных данных. Обновите данные из Spark.",
    employee_missing: "Сохранённый или выбранный ID отсутствует в актуальном ответе Spark. Выберите сотрудника вручную.",
    external_id_in_use: "Этот сотрудник Spark уже привязан к другому пользователю.",
    binding_conflict: "Выбранный сотрудник уже занят. Обновите список и повторите выбор.",
    user_inactive: "Подключение доступно только для активного пользователя ES DESK.",
    inconsistent_employee: "Spark вернул противоречивые ФИО для этого ID. Требуется проверка данных Spark.",
    invalid_snapshot: "Сохранённые данные расписания некорректны. Обновите их из Spark.",
    disabled: "Общая синхронизация Spark выключена. Обратитесь к администратору сервера.",
    migration_required: "Хранилище расписания ещё не подготовлено.",
    spark_network_error: "Spark недоступен. Поиск и привязка требуют актуального ответа; повторите позже.",
    spark_not_configured: "Подключение к API Spark не настроено на сервере.",
    spark_invalid_payload: "Spark вернул некорректные данные. Привязка не изменена.",
  };
  return messages[error.code] || error.message || "Не удалось выполнить операцию Spark";
}
function renderSpark() {
  const b = state.sparkBinding, s = state.sparkSync;
  spark.sparkBindingStatus.textContent = b ? (b.enabled ? "Подключено" : "Не подключено") : "Состояние привязки неизвестно";
  spark.sparkIdentity.textContent = b?.external_employee_id
    ? `Spark: ${b.external_full_name || "—"}\nОтдел: ${b.external_department_name || "—"} · ID отдела: ${b.external_department_id || "—"}\nID Spark: ${b.external_employee_id}` : "Сотрудник Spark не выбран";
  const shift = b?.schedule;
  spark.sparkShift.textContent = shift?.status === "non_working" ? `Сегодня, ${shift.date}: выходной по МСК`
    : shift?.start_at && shift?.end_at ? `Сегодня, ${shift.date}: ${sparkDate(shift.start_at, true)}–${sparkDate(shift.end_at, true)} МСК`
    : "Сегодняшнее расписание неизвестно.";
  const serverFailed = s && (s.status === "error" || s.status === "interrupted");
  const failed = serverFailed || state.sparkRefreshError;
  const running = s?.status === "running" || state.sparkBusy && state.sparkPolling;
  const known = shift && shift.status !== "unknown";
  spark.sparkSyncStatus.style.color = failed ? "var(--desk-danger)" : s?.status === "success" && known && !running ? "var(--desk-success)" : "var(--desk-muted)";
  spark.sparkSyncStatus.textContent = running ? "Обновление Spark выполняется…" : failed
    ? `Новое обновление не подтверждено (${state.sparkRefreshError || s.last_error_code || s.status}). Показываются ранее сохранённые данные, если они есть.`
    : s?.status === "success" ? `Синхронизация Spark подтверждена.${known ? "" : " Расписание неизвестно."}`
    : "Успешная синхронизация Spark не подтверждена.";
  spark.sparkSyncTimes.textContent = `Последнее успешное обновление: ${sparkDate(s?.last_success_at)} МСК`
    + (serverFailed ? `\nОшибочная попытка: ${sparkDate(s.last_attempt_at)} МСК` : "");
  spark.sparkReconnectBtn.classList.toggle("hidden", Boolean(b?.enabled) || !b?.external_employee_id);
  spark.sparkDisableBtn.classList.toggle("hidden", !b?.enabled);
  spark.sparkUnlinkBtn.classList.toggle("hidden", !b?.external_employee_id);
  spark.sparkModal.querySelectorAll("[data-spark-action]").forEach(button => { button.disabled = state.sparkBusy; });
  spark.sparkReconnectBtn.disabled ||= b?.local_active === false;
  spark.sparkChooseBtn.disabled ||= b?.local_active === false;
  const selected = state.sparkEmployees.find(employee => employee.external_employee_id === spark.sparkEmployees.value);
  spark.sparkBindBtn.disabled = state.sparkBusy || !selected?.available || !spark.sparkConfirm.checked;
  syncSparkBlock();
}
async function loadSpark(ticket) {
  const id = state.editingUserId;
  const [binding, sync] = await Promise.all([
    sparkApi(`/api/v1/settings/users/${encodeURIComponent(id)}/schedule`), sparkApi("/api/v1/settings/employee-schedule"),
  ]);
  if (!sparkActive(ticket)) return;
  state.sparkBinding = binding; state.sparkSync = sync;
  renderSpark();
}
async function openSpark() {
  const ticket = ++state.sparkTicket;
  el.userModal.classList.add("hidden");
  spark.sparkModal.classList.remove("hidden");
  spark.sparkModal.querySelector(".modal-body").scrollTop = 0;
  spark.closeSparkBtn.focus();
  spark.sparkLocalName.textContent = `ES DESK: ${state.users.find(user => user.id === state.editingUserId)?.full_name || "—"}`;
  spark.sparkPicker.classList.add("hidden"); spark.sparkFeedback.textContent = "";
  state.sparkBusy = true; state.sparkPolling = true; state.sparkSync = null; state.sparkRefreshError = null;
  renderSpark();
  try {
    await loadSpark(ticket);
    if (sparkActive(ticket)) await requestSparkRefresh(ticket, state.sparkSync);
  } catch (error) {
    if (sparkActive(ticket)) { state.sparkRefreshError = sparkError(error); spark.sparkFeedback.textContent = sparkError(error); }
  }
  finally { if (sparkActive(ticket)) { state.sparkBusy = false; state.sparkPolling = false; renderSpark(); } }
}
function closeSpark() {
  state.sparkTicket++;
  spark.sparkModal.classList.add("hidden");
  el.userModal.classList.remove("hidden");
  spark.openSparkBtn.focus({preventScroll: true});
}
async function changeSpark(payload, method = "PATCH") {
  const ticket = state.sparkTicket;
  if (state.sparkBusy) return;
  state.sparkBusy = true; spark.sparkFeedback.textContent = ""; renderSpark();
  try {
    const binding = await sparkApi(`/api/v1/settings/users/${encodeURIComponent(state.editingUserId)}/schedule`, {
      ...(method === "DELETE" ? {method} : {method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)}),
    });
    if (!sparkActive(ticket)) return;
    state.sparkBinding = binding;
    spark.sparkPicker.classList.add("hidden");
  } catch (error) { if (sparkActive(ticket)) spark.sparkFeedback.textContent = sparkError(error); }
  finally { if (sparkActive(ticket)) { state.sparkBusy = false; renderSpark(); } }
}
function unlinkSpark() {
  const binding = state.sparkBinding;
  if (state.sparkBusy || state.sparkUnlinkContext || !sparkActive(state.sparkTicket) || !binding?.external_employee_id) return;
  const name = state.users.find(user => user.id === state.editingUserId)?.full_name || "Пользователь";
  state.sparkUnlinkContext = {ticket: state.sparkTicket, userId: state.editingUserId, externalId: binding.external_employee_id};
  spark.sparkUnlinkIdentity.textContent = `${name} — ${binding.external_full_name || "Сотрудник Spark"} (ID Spark: ${binding.external_employee_id})`;
  spark.sparkModal.classList.add("hidden");
  spark.confirmSparkUnlinkModal.classList.remove("hidden");
  spark.cancelSparkUnlinkBtn.focus();
}
function closeSparkUnlink() {
  const context = state.sparkUnlinkContext;
  state.sparkUnlinkContext = null;
  spark.confirmSparkUnlinkModal.classList.add("hidden");
  if (!state.disposed && context?.ticket === state.sparkTicket && context.userId === state.editingUserId) {
    spark.sparkModal.classList.remove("hidden");
    spark.sparkUnlinkBtn.focus();
  }
}
function confirmSparkUnlink() {
  const context = state.sparkUnlinkContext;
  if (!context || state.disposed || state.sparkBusy || context.ticket !== state.sparkTicket
      || context.userId !== state.editingUserId || context.externalId !== state.sparkBinding?.external_employee_id) return;
  closeSparkUnlink();
  changeSpark(null, "DELETE");
}
function filterSparkEmployees() {
  const query = spark.sparkSearch.value.normalize("NFKC").toLocaleLowerCase("ru").replaceAll("ё", "е").trim();
  spark.sparkEmployees.replaceChildren();
  for (const employee of state.sparkEmployees) {
    const label = `${employee.full_name} · ${employee.department_name} · ID ${employee.external_employee_id}`;
    if (!label.normalize("NFKC").toLocaleLowerCase("ru").replaceAll("ё", "е").includes(query)) continue;
    const option = new Option(label, employee.external_employee_id);
    spark.sparkEmployees.add(option);
  }
  spark.sparkEmployees.selectedIndex = -1;
  spark.sparkConfirm.checked = false;
  spark.sparkListInfo.textContent = spark.sparkEmployees.options.length ? "Показаны сотрудники без привязки к ES DESK." : "Свободных сотрудников по запросу не найдено";
  renderSpark();
}
async function chooseSpark() {
  const ticket = state.sparkTicket;
  state.sparkBusy = true; spark.sparkFeedback.textContent = ""; renderSpark();
  try {
    const employees = await sparkApi(`/api/v1/settings/employee-schedule/employees?user_id=${encodeURIComponent(state.editingUserId)}`);
    if (!sparkActive(ticket)) return;
    state.sparkEmployees = employees; spark.sparkSearch.value = "";
    spark.sparkPicker.classList.remove("hidden"); filterSparkEmployees(); spark.sparkSearch.focus();
  } catch (error) { if (sparkActive(ticket)) spark.sparkFeedback.textContent = sparkError(error); }
  finally { if (sparkActive(ticket)) { state.sparkBusy = false; renderSpark(); } }
}
async function monitorSpark(ticket, baseline, existing = false) {
  state.sparkPolling = true; renderSpark();
  let attempt = existing ? baseline : null;
  const deadline = Date.now() + 120000;
  for (let i = 0; i < 120 && Date.now() < deadline && sparkActive(ticket); i++) {
    const sync = await sparkApi("/api/v1/settings/employee-schedule");
    if (!sparkActive(ticket)) return;
    state.sparkSync = sync; renderSpark();
    if (!attempt && sync.last_attempt_at && sync.last_attempt_at !== baseline) attempt = sync.last_attempt_at;
    if (attempt && sync.last_attempt_at !== attempt) throw new Error("Началась другая попытка синхронизации. Откройте окно заново для проверки состояния.");
    if (attempt && sync.status !== "running") {
      await loadSpark(ticket);
      if (sync.status !== "success") throw new Error(`Обновление Spark не завершилось успешно: ${sync.last_error_code || sync.status}`);
      return;
    }
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  if (sparkActive(ticket)) throw new Error("Обновление ещё не подтверждено. Проверьте состояние позже; повторный запуск не выполнялся.");
}
async function refreshSpark() {
  if (state.sparkBusy) return;
  const ticket = state.sparkTicket;
  state.sparkBusy = true; state.sparkPolling = true; state.sparkRefreshError = null;
  spark.sparkFeedback.textContent = ""; renderSpark();
  try {
    const before = await sparkApi("/api/v1/settings/employee-schedule");
    if (!sparkActive(ticket)) return;
    await requestSparkRefresh(ticket, before);
  } catch (error) {
    if (sparkActive(ticket)) { state.sparkRefreshError = sparkError(error); spark.sparkFeedback.textContent = sparkError(error); }
  }
  finally { if (sparkActive(ticket)) { state.sparkBusy = false; state.sparkPolling = false; renderSpark(); } }
}
async function requestSparkRefresh(ticket, before) {
    state.sparkSync = before;
    if (before.status === "running") await monitorSpark(ticket, before.last_attempt_at, true);
    else {
      try { await sparkApi("/api/v1/settings/employee-schedule/sync", {method: "POST"}); }
      catch (error) {
        if (error.code !== "running") throw error;
        const current = await sparkApi("/api/v1/settings/employee-schedule");
        if (sparkActive(ticket)) await monitorSpark(ticket, current.last_attempt_at, true);
        return;
      }
      if (sparkActive(ticket)) await monitorSpark(ticket, before.last_attempt_at);
    }
}

function shouldShowTelegramNotification(role) {
  return role === "admin" || role === "agent";
}

function syncTelegramNotificationBlock() {
  const role = el.fRole.value || "agent";
  const visible = shouldShowTelegramNotification(role);
  el.telegramNotificationBlock.classList.toggle("hidden", !visible);
  if (!visible) return;

  const bound = Boolean(state.currentTelegramNotification?.bound);
  el.telegramNotificationStatus.textContent = `Бот уведомлений: ${bound ? "привязан" : "не привязан"}`;
  el.telegramNotificationDisplay.textContent = state.currentTelegramNotification?.display || "";
  el.telegramNotificationDisplay.classList.toggle("hidden", !(bound && state.currentTelegramNotification?.display));
}

function openUserModal(user = null) {
  fillModal(user);
  el.deleteUserBtn?.classList.toggle("hidden", !user);
  el.userModal.classList.remove("hidden");
  el.userModal.querySelector(".modal-body").scrollTop = 0;
  el.fFullName.focus({ preventScroll: true });
}

function closeUserModal() {
  el.userModal.classList.add("hidden");
}

function renderUsers() {
  el.usersTableBody.innerHTML = "";
  el.emptyUsersText.style.display = state.users.length ? "none" : "block";
  el.emptyUsersText.textContent = state.users.length ? "" : "Пользователей пока нет";

  state.users.forEach((user) => {
    const tr = document.createElement("tr");
    tr.className = "settings-user-row";
    tr.dataset.userId = user.id;
    tr.tabIndex = 0;
    tr.setAttribute("aria-label", `Редактировать пользователя: ${user.full_name}`);
    tr.innerHTML = `
      <td>${escapeHtml(user.full_name)}</td>
      <td>${escapeHtml(user.login)}</td>
      <td>${escapeHtml(user.role_label || roleLabels[user.role] || user.role)}</td>
      <td>${user.is_active ? "✓" : "✕"}</td>
      <td>${escapeHtml(statusLabels[user.availability_status] || user.availability_status || "-")}</td>
      <td>${Number(user.legacy_ticket_count || 0)}</td>
      <td>${fmtDate(user.created_at)}</td>
      <td>${fmtDate(user.updated_at)}</td>
      <td>${escapeHtml(fmtLastLogin(user.last_login_at))}</td>
    `;
    el.usersTableBody.appendChild(tr);
  });

  el.usersTableBody.querySelectorAll(".settings-user-row").forEach((row) => {
    const edit = () => {
      const user = state.users.find((item) => item.id === row.dataset.userId);
      if (user) openUserModal(user);
    };
    row.addEventListener("click", edit);
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); edit(); }
    });
  });
}

async function loadUsers() {
  el.emptyUsersText.style.display = "block";
  el.emptyUsersText.textContent = "Загрузка пользователей...";
  state.users = await api("/api/v1/settings/users");
  renderUsers();
}

function modalPayload() {
  const payload = {
    full_name: el.fFullName.value.trim(),
    login: el.fLogin.value.trim(),
    role: el.fRole.value,
    is_active: el.fIsActive.checked,
    legacy_ticket_count: Math.max(0, Number(el.fLegacyTicketCount?.value || 0) || 0),
  };
  const password = el.fPassword.value;
  if (!state.editingUserId || password.trim()) payload.password = password;
  return payload;
}

async function saveUser() {
  try {
    const payload = modalPayload();
    if (!payload.full_name || !payload.login) throw new Error("ФИО и login обязательны");
    if (!state.editingUserId && !payload.password) throw new Error("При создании пароль обязателен");
    setStatus("Сохраняю пользователя...");

    if (state.editingUserId) {
      await api(`/api/v1/settings/users/${state.editingUserId}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });
    } else {
      await api("/api/v1/settings/users", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });
    }

    closeUserModal();
    await loadUsers();
    setStatus("Пользователь сохранён");
    window.HelpdeskAppShell?.showSaved?.(el.usersTableBody?.closest(".panel"));
  } catch (e) {
    showError(e.message);
  }
}

function showDeletionModal(modal) {
  state.deletionModal?.classList.add("hidden");
  state.deletionModal = modal;
  modal?.classList.remove("hidden");
  modal?.querySelector("button")?.focus();
}

function closeDeletionModal() {
  if (state.deletionPending) return;
  showDeletionModal(null);
  if (!el.userModal.classList.contains("hidden")) el.deleteUserBtn.focus();
}

function renderDeletion(operation) {
  const stages = [...el.userDeletionStages];
  const current = stages.findIndex((row) => row.dataset.stage === operation.stage);
  const running = operation.status === "running";
  stages.forEach((row, index) => {
    const done = operation.status === "succeeded" || (running && index < current);
    row.querySelector("span").textContent = done ? "✓" : index === current && running ? "— выполняется" : "";
    if (index === current && running) row.setAttribute("aria-current", "step");
    else row.removeAttribute("aria-current");
  });
  el.userDeletionProgress.classList.toggle("hidden", !running && operation.status !== "queued");
  if (operation.status === "succeeded") {
    el.userDeletionDescription.textContent = "Операция завершена";
    el.startUserDeletionBtn.classList.add("hidden");
  }
}

function prepareDeletion() {
  el.userDeletionName.textContent = state.deletionTarget.full_name;
  el.userDeletionStatus.textContent = "";
  el.userDeletionDescription.textContent = "Операция выполняется в фоне.";
  el.startUserDeletionBtn.classList.remove("hidden");
  el.startUserDeletionBtn.disabled = false;
  el.closeUserDeletionBtn.textContent = "Отмена";
  renderDeletion({});
  showDeletionModal(el.userDeletionProgressModal);
}

async function startDeletion() {
  if (state.deletionPending || el.startUserDeletionBtn.disabled) return;
  state.deletionPending = true;
  el.startUserDeletionBtn.disabled = true;
  el.closeUserDeletionBtn.disabled = true;
  el.userDeletionStatus.textContent = "Запуск удаления…";
  try {
    const target = state.deletionTarget;
    let operation = await api(`/api/v1/settings/users/${encodeURIComponent(target.id)}/deletion`, { method: "POST" });
    closeUserModal();
    while (!state.disposed) {
      renderDeletion(operation);
      if (operation.status === "failed") {
        el.userDeletionStatus.textContent = operation.error_message || "Не удалось удалить сотрудника.";
        break;
      }
      if (operation.status === "succeeded") {
        el.userDeletionStatus.textContent = "Сотрудник удалён";
        state.users = state.users.filter((user) => user.id !== target.id);
        renderUsers();
        loadUsers().catch((error) => showError(error.message));
        break;
      }
      el.userDeletionStatus.textContent = operation.status === "queued" ? "Ожидание запуска…" : "Выполняется удаление…";
      await new Promise((resolve) => setTimeout(resolve, 1250));
      if (state.disposed) return;
      operation = await api(`/api/v1/settings/user-deletion-operations/${encodeURIComponent(operation.operation_id)}`);
    }
  } catch (error) {
    renderDeletion({});
    el.userDeletionStatus.textContent = error.message;
  } finally {
    state.deletionPending = false;
    el.closeUserDeletionBtn.disabled = false;
    el.closeUserDeletionBtn.textContent = "Закрыть";
  }
}

const handlePageKeydown = (event) => {
  if (event.key !== "Escape") return;
  if (state.sparkUnlinkContext) closeSparkUnlink();
  else if (spark.sparkModal && !spark.sparkModal.classList.contains("hidden")) closeSpark();
  else if (state.deletionModal) closeDeletionModal();
  else { closeUserModal(); closeError(); }
};

(function init() {
  el.backToSettingsBtn.addEventListener("click", () => {
    if (window.HelpdeskAppShell?.goBack) {
      window.HelpdeskAppShell.goBack();
      return;
    }
    window.history.back();
  });
  el.createUserBtn.addEventListener("click", () => openUserModal());
  el.closeUserModalBtn.addEventListener("click", closeUserModal);
  el.saveUserBtn.addEventListener("click", saveUser);
  el.deleteUserBtn?.addEventListener("click", () => {
    state.deletionTarget = state.users.find((user) => user.id === state.editingUserId);
    if (state.deletionTarget) showDeletionModal(el.confirmDeleteUserModal);
  });
  el.continueDeleteUserBtn?.addEventListener("click", () => showDeletionModal(el.confirmPermanentDeletionModal));
  el.confirmPermanentDeletionBtn?.addEventListener("click", prepareDeletion);
  el.startUserDeletionBtn?.addEventListener("click", startDeletion);
  document.querySelectorAll("[data-cancel-user-deletion]").forEach((button) => {
    button.addEventListener("click", closeDeletionModal);
  });
  el.fRole.addEventListener("change", syncTelegramNotificationBlock);
  el.fRole.addEventListener("change", syncSparkBlock);
  spark.openSparkBtn?.addEventListener("click", openSpark);
  spark.closeSparkBtn?.addEventListener("click", closeSpark);
  if (spark.sparkModal) closeOnBackdropClick(spark.sparkModal, closeSpark);
  spark.sparkReconnectBtn?.addEventListener("click", () => changeSpark({enabled: true}));
  spark.sparkDisableBtn?.addEventListener("click", () => changeSpark({enabled: false}));
  spark.sparkUnlinkBtn?.addEventListener("click", unlinkSpark);
  spark.cancelSparkUnlinkBtn?.addEventListener("click", closeSparkUnlink);
  spark.confirmSparkUnlinkBtn?.addEventListener("click", confirmSparkUnlink);
  if (spark.confirmSparkUnlinkModal) closeOnBackdropClick(spark.confirmSparkUnlinkModal, closeSparkUnlink);
  spark.sparkRefreshBtn?.addEventListener("click", refreshSpark);
  spark.sparkChooseBtn?.addEventListener("click", chooseSpark);
  spark.sparkSearch?.addEventListener("input", filterSparkEmployees);
  spark.sparkEmployees?.addEventListener("change", () => {
    spark.sparkConfirm.checked = false;
    const selected = state.sparkEmployees.find(employee => employee.external_employee_id === spark.sparkEmployees.value);
    const replacement = state.sparkBinding?.external_employee_id && state.sparkBinding.external_employee_id !== selected?.external_employee_id;
    spark.sparkConfirmText.textContent = replacement
      ? `Подтверждаю замену ${state.sparkBinding.external_full_name} (ID ${state.sparkBinding.external_employee_id}) на ${selected?.full_name} (ID ${selected?.external_employee_id})`
      : `Подтверждаю выбор ${selected?.full_name || "сотрудника"} (ID ${selected?.external_employee_id || "—"})`;
    renderSpark();
  });
  spark.sparkConfirm?.addEventListener("change", renderSpark);
  spark.sparkBindBtn?.addEventListener("click", () => {
    const selected = state.sparkEmployees.find(employee => employee.external_employee_id === spark.sparkEmployees.value);
    if (selected?.available && spark.sparkConfirm.checked) changeSpark({enabled: true, external_employee_id: selected.external_employee_id});
  });
  closeOnBackdropClick(el.userModal, closeUserModal);
  el.closeErrorModalBtn.addEventListener("click", closeError);
  el.okErrorBtn.addEventListener("click", closeError);
  closeOnBackdropClick(el.errorModal, closeError);
  el.copyErrorBtn.addEventListener("click", async () => {
    el.errorText.select();
    try { await navigator.clipboard.writeText(el.errorText.value); }
    catch { document.execCommand("copy"); }
  });
  document.addEventListener("keydown", handlePageKeydown);

  setStatus("Загружаю настройки...");
  window.HelpdeskSidebar.init({ onError: (e) => showError(e.message) })
    .then(() => loadUsers())
    .catch((e) => {
      if (e?.status !== 401) showError(e.message);
    });
})();
return () => {
  state.disposed = true;
  state.sparkTicket++;
  state.sparkUnlinkContext = null;
  document.removeEventListener("keydown", handlePageKeydown);
};
}

if (window.HelpdeskAppShell?.registerPage) {
  window.HelpdeskAppShell.registerPage("settings-users", initSettingsUsersPage);
} else {
  initSettingsUsersPage();
}
})();
