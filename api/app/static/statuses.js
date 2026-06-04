(function () {
function initStatusesPage() {
const state = {
  users: [],
  inFlight: new Set(),
  pollTimer: null,
};

const statusOrder = ["offline", "online", "on_ticket", "waiting", "lunch"];
const statusLabels = {
  offline: "Не в сети",
  online: "В сети",
  on_ticket: "На заявке",
  waiting: "В ожидании",
  lunch: "Обед",
};

const el = {
  statusText: document.getElementById("statusText"),
  statusBoardGrid: document.getElementById("statusBoardGrid"),
  statusBoardEmpty: document.getElementById("statusBoardEmpty"),
  errorModal: document.getElementById("errorModal"),
  errorText: document.getElementById("errorText"),
  closeErrorModalBtn: document.getElementById("closeErrorModalBtn"),
  copyErrorBtn: document.getElementById("copyErrorBtn"),
  okErrorBtn: document.getElementById("okErrorBtn"),
};

function setStatus(text) { el.statusText.textContent = text; }
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
    headers: {
      accept: "application/json",
      ...(options.headers || {}),
    },
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

function formatStatusDuration(updatedAt) {
  if (!updatedAt) return "0 мин";
  const raw = String(updatedAt).trim();
  if (!raw) return "0 мин";
  const normalized = /z$|[+-]\d\d:\d\d$/i.test(raw) ? raw : `${raw}Z`;
  const started = new Date(normalized);
  if (Number.isNaN(started.getTime())) return "0 мин";
  const diffMs = Math.max(0, Date.now() - started.getTime());
  const totalMinutes = Math.floor(diffMs / 60000);
  const totalHours = Math.floor(totalMinutes / 60);
  const totalDays = Math.floor(totalHours / 24);
  if (totalDays >= 7) return `${totalDays} д`;
  if (totalDays >= 1) return `${totalDays} д ${totalHours % 24} ч`;
  if (totalHours >= 1) return `${totalHours} ч ${totalMinutes % 60} мин`;
  return `${totalMinutes} мин`;
}

function renderStatusBoard() {
  const users = state.users || [];
  el.statusBoardGrid.innerHTML = "";
  el.statusBoardEmpty.classList.toggle("hidden", users.length > 0);
  if (!users.length) return;

  users.forEach((user) => {
    const active = user.availability_status || "offline";
    const updating = state.inFlight.has(user.id);
    const card = document.createElement("div");
    card.className = "status-user-column";
    card.innerHTML = `
      <div class="status-user-name">${escapeHtml(user.full_name)}</div>
      ${statusOrder.map((status) => {
        const isActive = status === active;
        const duration = isActive ? `<span class="status-board-btn-time">${escapeHtml(formatStatusDuration(user.updated_at))}</span>` : "";
        return `<button class="status-board-btn status-${status.replace("_", "-")} ${isActive ? "active" : ""}" data-user-id="${escapeHtml(user.id)}" data-status="${status}" ${updating ? "disabled" : ""}>${escapeHtml(statusLabels[status] || status)}${duration}</button>`;
      }).join("")}
    `;
    el.statusBoardGrid.appendChild(card);
  });

  el.statusBoardGrid.querySelectorAll(".status-board-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const userId = btn.dataset.userId;
      const nextStatus = btn.dataset.status;
      if (!userId || !nextStatus) return;
      await updateUserStatus(userId, nextStatus);
    });
  });
}

async function loadStatusBoard() {
  const users = await api("/api/v1/users/status-board");
  state.users = Array.isArray(users) ? users : [];
  renderStatusBoard();
}

async function updateUserStatus(userId, availabilityStatus) {
  if (state.inFlight.has(userId)) return;
  state.inFlight.add(userId);
  renderStatusBoard();
  try {
    await api(`/api/v1/users/${userId}/status`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ availability_status: availabilityStatus }),
    });
    setStatus("Статус обновлён");
    await loadStatusBoard();
  } catch (error) {
    showError(error.message);
  } finally {
    state.inFlight.delete(userId);
    renderStatusBoard();
  }
}

function startPolling() {
  stopPolling();
  state.pollTimer = setInterval(() => {
    loadStatusBoard().catch(() => {});
  }, 5000);
}

function stopPolling() {
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

(function init() {
  el.closeErrorModalBtn.addEventListener("click", closeError);
  el.okErrorBtn.addEventListener("click", closeError);
  closeOnBackdropClick(el.errorModal, closeError);
  el.copyErrorBtn.addEventListener("click", async () => {
    el.errorText.select();
    try { await navigator.clipboard.writeText(el.errorText.value); }
    catch { document.execCommand("copy"); }
    setStatus("Текст ошибки скопирован");
  });

  setStatus("Загрузка...");
  window.HelpdeskSidebar.init({ onError: (e) => setStatus(`Ошибка: ${e.message}`) })
    .then(() => loadStatusBoard())
    .then(() => {
      setStatus("Готово");
      startPolling();
    })
    .catch((error) => {
      if (error?.status !== 401) {
        showError(error.message);
      }
      setStatus("Ошибка загрузки");
    });

  window.addEventListener("beforeunload", stopPolling);
})();
return () => {
  stopPolling();
  window.removeEventListener("beforeunload", stopPolling);
};
}

if (window.HelpdeskAppShell?.registerPage) {
  window.HelpdeskAppShell.registerPage("statuses", initStatusesPage);
} else {
  initStatusesPage();
}
})();
