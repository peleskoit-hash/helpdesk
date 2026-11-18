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
};

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
    throw err;
  }
  return p;
}

function showError(m) { el.errorText.value = m || "Неизвестная ошибка"; el.errorModal.classList.remove("hidden"); }
function closeError() { el.errorModal.classList.add("hidden"); }

function closeOnBackdropClick(modal, closeHandler) {
  let down = false;
  modal.addEventListener("pointerdown", (e) => { down = e.target === modal; });
  modal.addEventListener("click", (e) => {
    if (down && e.target === modal) closeHandler();
    down = false;
  });
}

function fillModal(user = null) {
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
  if (state.deletionModal) closeDeletionModal();
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
  document.removeEventListener("keydown", handlePageKeydown);
};
}

if (window.HelpdeskAppShell?.registerPage) {
  window.HelpdeskAppShell.registerPage("settings-users", initSettingsUsersPage);
} else {
  initSettingsUsersPage();
}
})();
