(function () {
function initMassNotificationsPage() {
const state = {
  selectedFiles: [],
  recipients: [],
  filterField: "",
  branchField: "",
};

const BRANCH_OPTIONS_BY_DESTINATION = {
  "Песок": ["песок Власова", "песок Шабурова", "песок Сайдаралиева", "песок Макаренко", "песок Шахалиевой", "песок Кочуры", "песок Москвиченко"],
  "Отдел Продаж": ["группа Власова", "группа Шабурова", "группа Сайдаралиева", "группа Макаренко", "группа Шахалиевой", "группа Кочуры", "группа Москвиченко"],
};
const LOCATION_OPTIONS = ["Офисный", "Удалённый"];

const el = {
  filterFieldSelect: document.getElementById("filterFieldSelect"),
  filterValueSelect: document.getElementById("filterValueSelect"),
  locationTypeSelect: document.getElementById("locationTypeSelect"),
  recipientCount: document.getElementById("recipientCount"),
  broadcastText: document.getElementById("broadcastText"),
  sendBroadcastBtn: document.getElementById("sendBroadcastBtn"),
  addBroadcastFilesBtn: document.getElementById("addBroadcastFilesBtn"),
  broadcastFilesInput: document.getElementById("broadcastFilesInput"),
  broadcastFilesList: document.getElementById("broadcastFilesList"),
  resultBox: document.getElementById("resultBox"),
  errorModal: document.getElementById("errorModal"),
  errorText: document.getElementById("errorText"),
  closeErrorModalBtn: document.getElementById("closeErrorModalBtn"),
  copyErrorBtn: document.getElementById("copyErrorBtn"),
  okErrorBtn: document.getElementById("okErrorBtn"),
  backToManagementBtn: document.getElementById("backToManagementBtn"),
};

function setStatus() {}
function showError(message) { el.errorText.value = message || "Неизвестная ошибка"; el.errorModal.classList.remove("hidden"); }
function closeError() { el.errorModal.classList.add("hidden"); }
function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function closeOnBackdropClick(modal, closeHandler) {
  let startedOnBackdrop = false;
  modal.addEventListener("pointerdown", (event) => {
    startedOnBackdrop = event.target === modal;
  });
  modal.addEventListener("click", (event) => {
    if (startedOnBackdrop && event.target === modal) closeHandler();
    startedOnBackdrop = false;
  });
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { accept: "application/json", ...(options.headers || {}) },
  });
  const raw = await response.text();
  let payload = null;
  if (raw) {
    try { payload = JSON.parse(raw); } catch { payload = null; }
  }
  if (!response.ok) {
    const detail = payload?.detail?.error?.message || raw || "Ошибка запроса";
    const err = new Error(detail);
    err.status = response.status;
    throw err;
  }
  return payload;
}

function renderFiles() {
  if (!state.selectedFiles.length) {
    el.broadcastFilesList.classList.add("hidden");
    el.broadcastFilesList.innerHTML = "";
    return;
  }
  el.broadcastFilesList.classList.remove("hidden");
  el.broadcastFilesList.innerHTML = state.selectedFiles.map((file, idx) => `
    <div class="selected-file-item">
      <span>${escapeHtml(file.name)}</span>
      <button type="button" class="action" data-remove-file="${idx}">Удалить</button>
    </div>
  `).join("");
  el.broadcastFilesList.querySelectorAll("[data-remove-file]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const idx = Number(btn.dataset.removeFile);
      state.selectedFiles.splice(idx, 1);
      renderFiles();
    });
  });
}

function setBranchOptions(destination) {
  const options = BRANCH_OPTIONS_BY_DESTINATION[destination] || [];
  el.filterValueSelect.innerHTML = `<option value="" selected>Не выбрано</option>` + options.map((item) => `<option value="${escapeHtml(item)}">${escapeHtml(item)}</option>`).join("");
  el.filterValueSelect.disabled = !options.length;
}

function setLocationOptions() {
  el.locationTypeSelect.innerHTML = `<option value="" selected>Не выбрано</option>` + LOCATION_OPTIONS.map((item) => `<option value="${escapeHtml(item)}">${escapeHtml(item)}</option>`).join("");
}

function buildRecipientQuery() {
  const query = new URLSearchParams();
  if (state.filterField && state.filterField !== "__all__") {
    query.set("destination", state.filterField);
  }
  if (state.branchField) {
    query.set("branch", state.branchField);
  }
  const location = el.locationTypeSelect.value;
  if (location) {
    query.set("location_type", location);
  }
  return query.toString() ? `?${query.toString()}` : "";
}

async function loadRecipients() {
  const suffix = buildRecipientQuery();
  const recipients = await api(`/api/v1/mass-notifications/recipients${suffix}`);
  state.recipients = Array.isArray(recipients) ? recipients : [];
  el.recipientCount.textContent = String(state.recipients.length);
}

async function sendBroadcast() {
  const text = el.broadcastText.value.trim();
  if (!text) {
    showError("Введите текст оповещения");
    return;
  }
  try {
    setStatus("Отправка оповещения...");
    const formData = new FormData();
    formData.append("text", text);
    formData.append("destination", state.filterField || "");
    formData.append("branch", state.branchField || "");
    formData.append("location_type", el.locationTypeSelect.value || "");
    state.selectedFiles.forEach((file) => formData.append("files", file));

    const response = await fetch("/api/v1/mass-notifications/send", {
      method: "POST",
      body: formData,
      headers: { accept: "application/json" },
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload?.detail?.error?.message || "Ошибка отправки");
    }

    el.resultBox.textContent = `Отправлено: ${payload.sent || 0}, Ошибок: ${payload.failed || 0}`;
    setStatus("Оповещение отправлено");
  } catch (error) {
    showError(error.message);
    setStatus("Ошибка отправки");
  }
}

(function init() {
  setLocationOptions();
  el.filterFieldSelect.addEventListener("change", async () => {
    state.filterField = el.filterFieldSelect.value;
    state.branchField = "";
    setBranchOptions(state.filterField);
    await loadRecipients();
  });
  el.filterValueSelect.addEventListener("change", async () => {
    state.branchField = el.filterValueSelect.value;
    await loadRecipients();
  });
  el.locationTypeSelect.addEventListener("change", loadRecipients);

  el.addBroadcastFilesBtn.addEventListener("click", () => el.broadcastFilesInput.click());
  el.broadcastFilesInput.addEventListener("change", () => {
    const files = [...(el.broadcastFilesInput.files || [])];
    state.selectedFiles.push(...files);
    el.broadcastFilesInput.value = "";
    renderFiles();
  });
  el.sendBroadcastBtn.addEventListener("click", sendBroadcast);
  el.backToManagementBtn?.addEventListener("click", () => {
    if (window.HelpdeskAppShell?.goBack) {
      window.HelpdeskAppShell.goBack();
      return;
    }
    window.history.back();
  });

  el.closeErrorModalBtn.addEventListener("click", closeError);
  el.okErrorBtn.addEventListener("click", closeError);
  closeOnBackdropClick(el.errorModal, closeError);
  el.copyErrorBtn.addEventListener("click", async () => {
    el.errorText.select();
    try { await navigator.clipboard.writeText(el.errorText.value); }
    catch { document.execCommand("copy"); }
  });
  window.HelpdeskSidebar.init({ onError: (e) => showError(e.message) })
    .then(() => loadRecipients())
    .catch((error) => {
      if (error?.status !== 401) showError(error.message);
    });
})();
return () => {
  // Page-local handlers are attached to DOM nodes that are replaced on navigation.
};
}

if (window.HelpdeskAppShell?.registerPage) {
  window.HelpdeskAppShell.registerPage("mass-notifications", initMassNotificationsPage);
} else {
  initMassNotificationsPage();
}
})();
