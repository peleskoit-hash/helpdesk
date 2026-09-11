(function () {
function initProfilePage() {
const roleLabels = { admin: "Администратор", agent: "Сотрудник", trainee: "Стажёр", chiki_piki: "Чики-пики" };
const RELOGIN_DELAY_MS = 2000;
const AVATAR_MAX_BYTES = 5 * 1024 * 1024;
const AVATAR_OUTPUT_SIZE = 512;
const avatarTypes = new Map([
  ["image/jpeg", { extension: "jpg", outputType: "image/jpeg" }],
  ["image/jpg", { extension: "jpg", outputType: "image/jpeg" }],
  ["image/png", { extension: "png", outputType: "image/png" }],
  ["image/webp", { extension: "webp", outputType: "image/webp" }],
]);
let profile = null;
let reloginTimer = null;
let avatarObjectUrl = "";
let cropState = null;
let activePointerId = null;

const el = {
  statusText: document.getElementById("statusText"),
  backToTicketsBtn: document.getElementById("backToTicketsBtn"),
  loading: document.getElementById("profileLoading"),
  error: document.getElementById("profileError"),
  successMessage: document.getElementById("profileSuccessMessage"),
  root: document.getElementById("profileRoot"),
  avatar: document.getElementById("profileAvatar"),
  avatarImage: document.getElementById("profileAvatarImage"),
  avatarInitials: document.getElementById("profileAvatarInitials"),
  avatarFileInput: document.getElementById("avatarFileInput"),
  changeAvatarBtn: document.getElementById("changeAvatarBtn"),
  deleteAvatarBtn: document.getElementById("deleteAvatarBtn"),
  avatarActionMessage: document.getElementById("avatarActionMessage"),
  avatarCropModal: document.getElementById("avatarCropModal"),
  closeAvatarCropBtn: document.getElementById("closeAvatarCropBtn"),
  cancelAvatarCropBtn: document.getElementById("cancelAvatarCropBtn"),
  saveAvatarCropBtn: document.getElementById("saveAvatarCropBtn"),
  avatarCropFrame: document.getElementById("avatarCropFrame"),
  avatarCropCanvas: document.getElementById("avatarCropCanvas"),
  avatarZoomInput: document.getElementById("avatarZoomInput"),
  avatarCropError: document.getElementById("avatarCropError"),
  deleteAvatarModal: document.getElementById("deleteAvatarModal"),
  closeDeleteAvatarBtn: document.getElementById("closeDeleteAvatarBtn"),
  cancelDeleteAvatarBtn: document.getElementById("cancelDeleteAvatarBtn"),
  confirmDeleteAvatarBtn: document.getElementById("confirmDeleteAvatarBtn"),
  deleteAvatarError: document.getElementById("deleteAvatarError"),
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

function avatarErrorMessage(error) {
  const code = typeof error === "string" ? error : errorCode(error);
  const mapped = {
    avatar_too_large: "Размер файла не должен превышать 5 МБ",
    avatar_invalid_image: "Не удалось распознать изображение",
    avatar_unsupported_type: "Поддерживаются только JPG, PNG и WEBP",
    avatar_not_found: "",
  };
  return mapped[code] ?? errorMessage(error);
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

function showAvatarMessage(message) {
  if (!el.avatarActionMessage) return;
  el.avatarActionMessage.textContent = message || "";
  el.avatarActionMessage.classList.toggle("hidden", !message);
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

function avatarCacheBustedUrl(url) {
  if (!url) return "";
  return `${url}${url.includes("?") ? "&" : "?"}v=${Date.now()}`;
}

function avatarExtensionForType(type, fallback = "jpg") {
  return avatarTypes.get(String(type || "").toLowerCase())?.extension || fallback;
}

function renderAvatar(profileData, bustedUrl = "") {
  const name = profileData?.full_name || "Пользователь";
  const url = bustedUrl || (profileData?.avatar_present && profileData?.avatar_url ? profileData.avatar_url : "");
  setText(el.avatarInitials, initials(name));
  if (el.avatar) el.avatar.title = name;
  if (el.avatarImage) {
    if (url) {
      el.avatarImage.classList.add("hidden");
      el.avatarInitials?.classList.remove("hidden");
      el.avatarImage.src = url;
      if (el.avatarImage.complete && el.avatarImage.naturalWidth > 0) {
        handleAvatarImageLoad();
      }
    } else {
      el.avatarImage.removeAttribute("src");
      el.avatarImage.classList.add("hidden");
      el.avatarInitials?.classList.remove("hidden");
    }
  }
  el.deleteAvatarBtn?.classList.toggle("hidden", !url);
}

function handleAvatarImageLoad() {
  el.avatarImage?.classList.remove("hidden");
  el.avatarInitials?.classList.add("hidden");
}

function handleAvatarImageError() {
  if (el.avatarImage) {
    el.avatarImage.removeAttribute("src");
    el.avatarImage.classList.add("hidden");
  }
  el.avatarInitials?.classList.remove("hidden");
}

function syncSidebarAvatar(nextProfile, bustedUrl = "") {
  window.HelpdeskSidebar?.setCurrentUserAvatar?.({
    avatar_present: Boolean(nextProfile?.avatar_present),
    avatar_url: nextProfile?.avatar_url || null,
    avatar_display_url: bustedUrl || null,
  });
}

function renderProfile(data) {
  profile = data || {};
  const name = profile.full_name || "Пользователь";
  const login = profile.login || "";
  const telegram = profile.telegram_notification || {};
  renderAvatar(profile);
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

function resetAvatarInput() {
  if (el.avatarFileInput) el.avatarFileInput.value = "";
}

function closeAvatarCropModal() {
  el.avatarCropModal?.classList.add("hidden");
  showModalError(el.avatarCropError, "");
  if (avatarObjectUrl) {
    URL.revokeObjectURL(avatarObjectUrl);
    avatarObjectUrl = "";
  }
  cropState = null;
  activePointerId = null;
  el.avatarCropFrame?.classList.remove("dragging");
  resetAvatarInput();
}

function openDeleteAvatarModal() {
  showModalError(el.deleteAvatarError, "");
  el.deleteAvatarModal?.classList.remove("hidden");
  el.confirmDeleteAvatarBtn?.focus();
}

function closeDeleteAvatarModal() {
  el.deleteAvatarModal?.classList.add("hidden");
  showModalError(el.deleteAvatarError, "");
}

function cropCanvasSize() {
  const rect = el.avatarCropFrame?.getBoundingClientRect?.();
  const size = Math.max(1, Math.round(rect?.width || 420));
  return size;
}

function minimumCoverScale(image, cropSize) {
  const width = Math.max(1, Number(image?.naturalWidth || image?.width || 1));
  const height = Math.max(1, Number(image?.naturalHeight || image?.height || 1));
  return Math.max(cropSize / width, cropSize / height);
}

function clampCropPosition() {
  if (!cropState) return;
  const scaledWidth = cropState.imageWidth * cropState.scale;
  const scaledHeight = cropState.imageHeight * cropState.scale;
  const maxX = Math.max(0, (scaledWidth - cropState.cropSize) / 2);
  const maxY = Math.max(0, (scaledHeight - cropState.cropSize) / 2);
  cropState.offsetX = Math.min(maxX, Math.max(-maxX, Number(cropState.offsetX) || 0));
  cropState.offsetY = Math.min(maxY, Math.max(-maxY, Number(cropState.offsetY) || 0));
}

function drawCropPreview() {
  if (!cropState || !el.avatarCropCanvas) return;
  const canvas = el.avatarCropCanvas;
  const size = cropState.cropSize;
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.clearRect(0, 0, size, size);
  ctx.fillStyle = "#0f172a";
  ctx.fillRect(0, 0, size, size);
  const scaledWidth = cropState.imageWidth * cropState.scale;
  const scaledHeight = cropState.imageHeight * cropState.scale;
  const x = (size - scaledWidth) / 2 + cropState.offsetX;
  const y = (size - scaledHeight) / 2 + cropState.offsetY;
  ctx.drawImage(cropState.image, x, y, scaledWidth, scaledHeight);
}

function setZoom(nextZoom) {
  if (!cropState) return;
  const zoom = Math.min(cropState.maxZoom, Math.max(1, Number(nextZoom) || 1));
  cropState.zoom = zoom;
  cropState.scale = cropState.minScale * zoom;
  clampCropPosition();
  drawCropPreview();
  if (el.avatarZoomInput) el.avatarZoomInput.value = String(zoom);
}

function initCropGeometry(image, outputType, extension) {
  const cropSize = cropCanvasSize();
  const imageWidth = Math.max(1, Number(image.naturalWidth || image.width || 1));
  const imageHeight = Math.max(1, Number(image.naturalHeight || image.height || 1));
  const minScale = minimumCoverScale({ width: imageWidth, height: imageHeight }, cropSize);
  cropState = {
    image,
    imageWidth,
    imageHeight,
    outputType,
    extension,
    cropSize,
    minScale: Number.isFinite(minScale) && minScale > 0 ? minScale : 1,
    maxZoom: 4,
    zoom: 1,
    scale: Number.isFinite(minScale) && minScale > 0 ? minScale : 1,
    offsetX: 0,
    offsetY: 0,
    dragStartX: 0,
    dragStartY: 0,
    startOffsetX: 0,
    startOffsetY: 0,
  };
  if (el.avatarZoomInput) {
    el.avatarZoomInput.min = "1";
    el.avatarZoomInput.max = String(cropState.maxZoom);
    el.avatarZoomInput.step = "0.01";
    el.avatarZoomInput.value = "1";
  }
  clampCropPosition();
  drawCropPreview();
}

function openCropModalForFile(file) {
  showModalError(el.avatarCropError, "");
  showAvatarMessage("");
  if (avatarObjectUrl) URL.revokeObjectURL(avatarObjectUrl);
  avatarObjectUrl = URL.createObjectURL(file);
  const image = new Image();
  image.onload = () => {
    initCropGeometry(image, avatarTypes.get(file.type.toLowerCase()).outputType, avatarTypes.get(file.type.toLowerCase()).extension);
    el.avatarCropModal?.classList.remove("hidden");
  };
  image.onerror = () => {
    closeAvatarCropModal();
    showAvatarMessage("Не удалось распознать изображение");
  };
  image.src = avatarObjectUrl;
}

function handleAvatarFileChange() {
  const file = el.avatarFileInput?.files?.[0];
  if (!file) return;
  const normalizedType = String(file.type || "").toLowerCase();
  if (!avatarTypes.has(normalizedType)) {
    showAvatarMessage("Поддерживаются только JPG, PNG и WEBP");
    resetAvatarInput();
    return;
  }
  if (file.size > AVATAR_MAX_BYTES) {
    showAvatarMessage("Размер файла не должен превышать 5 МБ");
    resetAvatarInput();
    return;
  }
  openCropModalForFile(file);
}

function croppedAvatarBlob() {
  return new Promise((resolve, reject) => {
    if (!cropState) {
      reject(new Error("Изображение не выбрано"));
      return;
    }
    const output = document.createElement("canvas");
    output.width = AVATAR_OUTPUT_SIZE;
    output.height = AVATAR_OUTPUT_SIZE;
    const ctx = output.getContext("2d");
    if (!ctx) {
      reject(new Error("Canvas недоступен"));
      return;
    }
    const factor = AVATAR_OUTPUT_SIZE / cropState.cropSize;
    const scaledWidth = cropState.imageWidth * cropState.scale * factor;
    const scaledHeight = cropState.imageHeight * cropState.scale * factor;
    const x = (AVATAR_OUTPUT_SIZE - scaledWidth) / 2 + cropState.offsetX * factor;
    const y = (AVATAR_OUTPUT_SIZE - scaledHeight) / 2 + cropState.offsetY * factor;
    ctx.clearRect(0, 0, AVATAR_OUTPUT_SIZE, AVATAR_OUTPUT_SIZE);
    ctx.drawImage(cropState.image, x, y, scaledWidth, scaledHeight);
    const preferredType = cropState.outputType || "image/jpeg";
    output.toBlob((blob) => {
      if (blob) {
        resolve({ blob, type: blob.type || preferredType, extension: avatarExtensionForType(blob.type || preferredType, cropState.extension || "jpg") });
        return;
      }
      output.toBlob((fallbackBlob) => {
        if (fallbackBlob) {
          resolve({ blob: fallbackBlob, type: fallbackBlob.type || "image/jpeg", extension: "jpg" });
        } else {
          reject(new Error("Не удалось подготовить изображение"));
        }
      }, "image/jpeg", 0.9);
    }, preferredType, preferredType === "image/jpeg" ? 0.9 : undefined);
  });
}

async function saveAvatarCrop() {
  showModalError(el.avatarCropError, "");
  if (!cropState) return;
  el.saveAvatarCropBtn.disabled = true;
  try {
    const { blob, extension } = await croppedAvatarBlob();
    const formData = new FormData();
    formData.append("avatar", blob, `avatar.${extension}`);
    const result = await api("/api/v1/users/me/avatar", {
      method: "PUT",
      body: formData,
    });
    const bustedUrl = avatarCacheBustedUrl(result?.avatar_url);
    profile = { ...(profile || {}), avatar_present: true, avatar_url: result?.avatar_url || null };
    closeAvatarCropModal();
    renderAvatar(profile, bustedUrl);
    syncSidebarAvatar(profile, bustedUrl);
    showAvatarMessage("");
    setStatus("Аватар обновлён");
  } catch (error) {
    showModalError(el.avatarCropError, avatarErrorMessage(error));
  } finally {
    el.saveAvatarCropBtn.disabled = false;
  }
}

async function deleteAvatar() {
  showModalError(el.deleteAvatarError, "");
  el.confirmDeleteAvatarBtn.disabled = true;
  try {
    const result = await api("/api/v1/users/me/avatar", { method: "DELETE" });
    profile = { ...(profile || {}), avatar_present: Boolean(result?.avatar_present), avatar_url: result?.avatar_url || null };
    closeDeleteAvatarModal();
    renderAvatar(profile);
    syncSidebarAvatar(profile);
    setStatus("Аватар удалён");
  } catch (error) {
    showModalError(el.deleteAvatarError, avatarErrorMessage(error));
  } finally {
    el.confirmDeleteAvatarBtn.disabled = false;
  }
}

function startCropDrag(event) {
  if (!cropState || event.button > 0) return;
  activePointerId = event.pointerId;
  cropState.dragStartX = event.clientX;
  cropState.dragStartY = event.clientY;
  cropState.startOffsetX = cropState.offsetX;
  cropState.startOffsetY = cropState.offsetY;
  el.avatarCropFrame?.setPointerCapture?.(event.pointerId);
  el.avatarCropFrame?.classList.add("dragging");
}

function moveCropDrag(event) {
  if (!cropState || activePointerId !== event.pointerId) return;
  event.preventDefault();
  cropState.offsetX = cropState.startOffsetX + event.clientX - cropState.dragStartX;
  cropState.offsetY = cropState.startOffsetY + event.clientY - cropState.dragStartY;
  clampCropPosition();
  drawCropPreview();
}

function endCropDrag(event) {
  if (activePointerId !== event.pointerId) return;
  el.avatarCropFrame?.releasePointerCapture?.(event.pointerId);
  el.avatarCropFrame?.classList.remove("dragging");
  activePointerId = null;
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
  closeAvatarCropModal();
  closeDeleteAvatarModal();
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
  el.changeAvatarBtn?.addEventListener("click", () => el.avatarFileInput?.click());
  el.avatarImage?.addEventListener("load", handleAvatarImageLoad);
  el.avatarImage?.addEventListener("error", handleAvatarImageError);
  el.avatarFileInput?.addEventListener("change", handleAvatarFileChange);
  el.deleteAvatarBtn?.addEventListener("click", openDeleteAvatarModal);
  el.closeAvatarCropBtn?.addEventListener("click", closeAvatarCropModal);
  el.cancelAvatarCropBtn?.addEventListener("click", closeAvatarCropModal);
  el.saveAvatarCropBtn?.addEventListener("click", saveAvatarCrop);
  el.avatarZoomInput?.addEventListener("input", () => setZoom(el.avatarZoomInput.value));
  el.avatarCropFrame?.addEventListener("pointerdown", startCropDrag);
  el.avatarCropFrame?.addEventListener("pointermove", moveCropDrag);
  el.avatarCropFrame?.addEventListener("pointerup", endCropDrag);
  el.avatarCropFrame?.addEventListener("pointercancel", endCropDrag);
  el.closeDeleteAvatarBtn?.addEventListener("click", closeDeleteAvatarModal);
  el.cancelDeleteAvatarBtn?.addEventListener("click", closeDeleteAvatarModal);
  el.confirmDeleteAvatarBtn?.addEventListener("click", deleteAvatar);
  el.closeLoginModalBtn?.addEventListener("click", closeLoginModal);
  el.cancelLoginBtn?.addEventListener("click", closeLoginModal);
  el.saveLoginBtn?.addEventListener("click", saveLogin);
  el.closePasswordModalBtn?.addEventListener("click", closePasswordModal);
  el.cancelPasswordBtn?.addEventListener("click", closePasswordModal);
  el.savePasswordBtn?.addEventListener("click", savePassword);
  closeOnBackdropClick(el.loginModal, closeLoginModal);
  closeOnBackdropClick(el.passwordModal, closePasswordModal);
  closeOnBackdropClick(el.avatarCropModal, closeAvatarCropModal);
  closeOnBackdropClick(el.deleteAvatarModal, closeDeleteAvatarModal);
  document.addEventListener("keydown", handleKeydown);
}

bind();
window.HelpdeskSidebar?.init?.({ onError: (error) => setStatus(`Ошибка: ${error.message}`) }).catch(() => {});
loadProfile();

return () => {
  document.removeEventListener("keydown", handleKeydown);
  if (reloginTimer) window.clearTimeout(reloginTimer);
  closeAvatarCropModal();
};
}

if (window.HelpdeskAppShell?.registerPage) {
  window.HelpdeskAppShell.registerPage("profile", initProfilePage);
} else {
  initProfilePage();
}
})();
