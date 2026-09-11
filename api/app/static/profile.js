(function () {
function initProfilePage() {
const roleLabels = { admin: "Администратор", agent: "Сотрудник", trainee: "Стажёр", chiki_piki: "Чики-пики" };
const RELOGIN_DELAY_MS = 2000;
let profile = null;
let reloginTimer = null;

const el = {
  statusText: document.getElementById("statusText"),
  backToTicketsBtn: document.getElementById("backToTicketsBtn"),
  loading: document.getElementById("profileLoading"),
  error: document.getElementById("profileError"),
  successMessage: document.getElementById("profileSuccessMessage"),
  root: document.getElementById("profileRoot"),
  avatar: document.getElementById("profileAvatar"),
  fullNameHero: document.getElementById("profileFullNameHero"),
  loginHero: document.getElementById("profileLoginHero"),
  fullName: document.getElementById("profileFullName"),
  login: document.getElementById("profileLogin"),
  role: document.getElementById("profileRole"),
  legacyCount: document.getElementById("profileLegacyCount"),
  telegram: document.getElementById("profileTelegram"),
  editLoginBtn: document.getElementById("editLoginBtn"),
  editPasswordBtn: document.getElementById("editPasswordBtn"),
  loginModal: document.getElementById("loginModal"),
  closeLoginModalBtn: document.getElementById("closeLoginModalBtn"),
  cancelLoginBtn: document.getElementById("cancelLoginBtn"),
  saveLoginBtn: document.getElementById("saveLoginBtn"),
  loginInput: document.getElementById("loginInput"),
  loginModalError: document.getElementById("loginModalError"),
  passwordModal: document.getElementById("passwordModal"),
  closePasswordModalBtn: document.getElementById("closePasswordModalBtn"),
  cancelPasswordBtn: document.getElementById("cancelPasswordBtn"),
  savePasswordBtn: document.getElementById("savePasswordBtn"),
  currentPasswordInput: document.getElementById("currentPasswordInput"),
  newPasswordInput: document.getElementById("newPasswordInput"),
  newPasswordConfirmationInput: document.getElementById("newPasswordConfirmationInput"),
  passwordModalError: document.getElementById("passwordModalError"),
};

function setStatus(text) {
  if (el.statusText) el.statusText.textContent = text || "";
}

function setText(node, value) {
  if (node) node.textContent = String(value ?? "");
}

function initials(fullName) {
  const parts = String(fullName || "")
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2);
  const value = parts.map((part) => part[0]).join("").toUpperCase();
  return value || "A";
}

function errorCode(error) {
  return error?.payload?.detail?.error?.code || "";
}

function errorMessage(error) {
  return error?.payload?.detail?.error?.message || error?.message || "Ошибка запроса";
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    credentials: "include",
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
    const err = new Error(payload?.detail?.error?.message || raw || "Ошибка запроса");
    err.status = response.status;
    err.payload = payload;
    throw err;
  }
  return payload;
}

function showModalError(node, message) {
  if (!node) return;
  node.textContent = message || "";
  node.classList.toggle("hidden", !message);
}

function showSuccessMessage(message) {
  setText(el.successMessage, message || "Данные изменены. Войдите снова.");
  el.successMessage?.classList.remove("hidden");
}

function closeOnBackdropClick(modal, closeHandler) {
  if (!modal) return;
  let down = false;
  modal.addEventListener("pointerdown", (event) => { down = event.target === modal; });
  modal.addEventListener("click", (event) => {
    if (down && event.target === modal) closeHandler();
    down = false;
  });
}

function renderProfile(data) {
  profile = data || {};
  const name = profile.full_name || "Пользователь";
  const login = profile.login || "";
  const telegram = profile.telegram_notification || {};
  setText(el.avatar, initials(name));
  if (el.avatar) el.avatar.title = name;
  setText(el.fullNameHero, name);
  setText(el.loginHero, login || "Login не указан");
  setText(el.fullName, name);
  setText(el.login, login || "—");
  setText(el.role, roleLabels[profile.role] || profile.role || "—");
  setText(el.legacyCount, Number(profile.legacy_ticket_count || 0));
  if (telegram.bound) {
    setText(el.telegram, telegram.display ? `Привязан — ${telegram.display}` : "Привязан");
  } else {
    setText(el.telegram, "Не привязан");
  }
}

async function loadProfile() {
  el.loading?.classList.remove("hidden");
  el.error?.classList.add("hidden");
  el.successMessage?.classList.add("hidden");
  el.root?.classList.add("hidden");
  setStatus("Загрузка...");
  try {
    const data = await api("/api/v1/users/me/profile");
    renderProfile(data);
    el.root?.classList.remove("hidden");
    setStatus("Готово");
  } catch (error) {
    setText(el.error, `Не удалось загрузить личные данные: ${errorMessage(error)}`);
    el.error?.classList.remove("hidden");
    setStatus("Ошибка загрузки");
  } finally {
    el.loading?.classList.add("hidden");
  }
}

function openLoginModal() {
  showModalError(el.loginModalError, "");
  if (el.loginInput) el.loginInput.value = profile?.login || "";
  el.loginModal?.classList.remove("hidden");
  el.loginInput?.focus();
}

function closeLoginModal() {
  el.loginModal?.classList.add("hidden");
}

function openPasswordModal() {
  showModalError(el.passwordModalError, "");
  if (el.currentPasswordInput) el.currentPasswordInput.value = "";
  if (el.newPasswordInput) el.newPasswordInput.value = "";
  if (el.newPasswordConfirmationInput) el.newPasswordConfirmationInput.value = "";
  el.passwordModal?.classList.remove("hidden");
  el.currentPasswordInput?.focus();
}

function closePasswordModal() {
  el.passwordModal?.classList.add("hidden");
}

async function logoutAfterDelay(message) {
  closeLoginModal();
  closePasswordModal();
  const successText = message || "Данные изменены. Войдите снова.";
  showSuccessMessage(successText);
  setStatus(successText);
  reloginTimer = window.setTimeout(async () => {
    try {
      await fetch("/logout", {
        method: "POST",
        credentials: "include",
        headers: { accept: "text/html" },
      });
    } finally {
      window.location.href = "/login";
    }
  }, RELOGIN_DELAY_MS);
}

async function saveLogin() {
  const login = (el.loginInput?.value || "").trim();
  showModalError(el.loginModalError, "");
  if (!login) {
    showModalError(el.loginModalError, "Введите новый логин");
    return;
  }
  el.saveLoginBtn.disabled = true;
  try {
    const result = await api("/api/v1/users/me/login", {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ login }),
    });
    await logoutAfterDelay(result?.message);
  } catch (error) {
    const code = errorCode(error);
    showModalError(
      el.loginModalError,
      code === "login_already_used" ? "Этот логин уже используется" : errorMessage(error),
    );
  } finally {
    el.saveLoginBtn.disabled = false;
  }
}

async function savePassword() {
  const currentPassword = el.currentPasswordInput?.value || "";
  const newPassword = el.newPasswordInput?.value || "";
  const confirmation = el.newPasswordConfirmationInput?.value || "";
  showModalError(el.passwordModalError, "");
  if (!currentPassword || !newPassword || !confirmation) {
    showModalError(el.passwordModalError, "Заполните все поля");
    return;
  }
  if (newPassword !== confirmation) {
    showModalError(el.passwordModalError, "Новый пароль и подтверждение не совпадают");
    return;
  }
  el.savePasswordBtn.disabled = true;
  try {
    const result = await api("/api/v1/users/me/password", {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword,
        new_password_confirmation: confirmation,
      }),
    });
    await logoutAfterDelay(result?.message);
  } catch (error) {
    const code = errorCode(error);
    const mapped = {
      current_password_invalid: "Старый пароль указан неверно",
      password_confirmation_mismatch: "Новый пароль и подтверждение не совпадают",
    };
    showModalError(el.passwordModalError, mapped[code] || errorMessage(error));
  } finally {
    el.savePasswordBtn.disabled = false;
  }
}

const handleKeydown = (event) => {
  if (event.key !== "Escape") return;
  closeLoginModal();
  closePasswordModal();
};

function bind() {
  el.backToTicketsBtn?.addEventListener("click", () => {
    if (window.HelpdeskAppShell?.navigate) {
      window.HelpdeskAppShell.navigate("/tickets");
    } else {
      window.location.href = "/tickets";
    }
  });
  el.editLoginBtn?.addEventListener("click", openLoginModal);
  el.editPasswordBtn?.addEventListener("click", openPasswordModal);
  el.closeLoginModalBtn?.addEventListener("click", closeLoginModal);
  el.cancelLoginBtn?.addEventListener("click", closeLoginModal);
  el.saveLoginBtn?.addEventListener("click", saveLogin);
  el.closePasswordModalBtn?.addEventListener("click", closePasswordModal);
  el.cancelPasswordBtn?.addEventListener("click", closePasswordModal);
  el.savePasswordBtn?.addEventListener("click", savePassword);
  closeOnBackdropClick(el.loginModal, closeLoginModal);
  closeOnBackdropClick(el.passwordModal, closePasswordModal);
  document.addEventListener("keydown", handleKeydown);
}

bind();
window.HelpdeskSidebar?.init?.({ onError: (error) => setStatus(`Ошибка: ${error.message}`) }).catch(() => {});
loadProfile();

return () => {
  document.removeEventListener("keydown", handleKeydown);
  if (reloginTimer) window.clearTimeout(reloginTimer);
};
}

if (window.HelpdeskAppShell?.registerPage) {
  window.HelpdeskAppShell.registerPage("profile", initProfilePage);
} else {
  initProfilePage();
}
})();
